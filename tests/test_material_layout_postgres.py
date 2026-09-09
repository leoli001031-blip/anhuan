"""Layout contract upgrade on real PostgreSQL, tenant RLS and worker leases.

Only immutable object storage and a deliberate persistence fault are injected.
The random dedicated harness never touches shared service objects.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import unittest
import uuid
from unittest.mock import patch

os.environ.setdefault("F1_KEYCLOAK_ISSUER_URL", "http://material-rag.invalid/realms/anhuan")

from sqlalchemy import text

from infra.f1.analysis_report_postgres_integration import PostgresIntegrationStack, _stable
from platform_foundation.f1.database import session_scope
from platform_foundation.f1.features.analysis_reports import repository as reports, service as report_service
from platform_foundation.f1.features.analysis_reports.contracts import GenerationFailed
from platform_foundation.f1.features.material_intake import service as intake
from platform_foundation.f1.features.material_intake.analyzer import analyze_pdf
from platform_foundation.f1.features.material_intake.ocr import PDF_TEXT_PARSER_VERSION, MATERIAL_EXTRACTION_CONTRACT
from platform_foundation.f1.features.material_intake.contracts import ConfirmPolicyDraftIn
from platform_foundation.f1.features.material_intake.policy_draft import confirm_policy_draft
from platform_foundation.f1.features.p3.contracts import IngestionError
from platform_foundation.f1.features.p3.processor import retry_ready_material_analysis
from platform_foundation.f1.features.material_pipeline import coordinator, local_index
from platform_foundation.f1.features.material_rag import repository as rag
from platform_foundation.f1.features.material_rag.security import canonical_unit, encrypt_text, unit_aad
from tests.test_material_layout_regressions import _pdf


STACK = None
WORLD = None


def setUpModule():
    global STACK, WORLD
    STACK = PostgresIntegrationStack()
    print("MATERIAL_LAYOUT_PG_PROJECT=" + STACK.project_name, flush=True)
    try:
        STACK.start()
        WORLD = STACK.seed_world()
    except BaseException:
        STACK.dispose_runtime()
        STACK.stop()
        raise


def tearDownModule():
    if STACK is not None:
        STACK.dispose_runtime()
        STACK.stop()
        if STACK.cleanup_status != "CLEAN" or STACK.dedicated_after != (0, 0, 0):
            raise AssertionError("MATERIAL_LAYOUT_CLEANUP_FAILED")
        print("MATERIAL_LAYOUT_PG_CLEANUP=" + json.dumps({"project": STACK.project_name,
            "status": STACK.cleanup_status, "residual": STACK.dedicated_after,
            "shared_match": STACK.shared_match}), flush=True)


class MaterialLayoutPostgresTests(unittest.TestCase):
    def _seed_source(self, label):
        source = _pdf(pages=1, header=True, draw=False)
        sha = hashlib.sha256(source).hexdigest()
        version_id = _stable("version", label)
        scope_id = WORLD.provider_scope_id if label == "provider" else _stable("scope", label)
        unit = canonical_unit(
            enterprise_id=WORLD.enterprise_a, knowledge_scope_id=scope_id,
            document_record_id=_stable("record", label), document_version_id=version_id,
            source_sha256=sha, page_number=1, ordinal=1,
            parser_version=PDF_TEXT_PARSER_VERSION,
            text="Frozen historical monitoring report: COD 42 mg/L, recorded within limit.",
        )
        with STACK._bootstrap() as connection:
            connection.execute("SELECT set_config('session_replication_role','replica',false)")
            connection.execute(
                "UPDATE f1.upload_task SET content_sha256=%s,object_key=%s,source_size=%s,"
                "source_etag='synthetic-etag',preview_unit_count=1 WHERE id=%s",
                (sha, f"layout/{label}.pdf", len(source), _stable("task", label)),
            )
            connection.execute("DELETE FROM f1.material_rag_unit WHERE document_version_id=%s", (version_id,))
            self._insert_unit(connection, unit)
        return source, unit

    @staticmethod
    def _insert_unit(connection, unit):
        ciphertext, aad_sha = encrypt_text(unit.body.reveal(), unit_aad(unit))
        connection.execute(
            "INSERT INTO f1.material_rag_unit (id,enterprise_id,knowledge_scope_id,document_record_id,"
            "document_version_id,source_sha256,page_number,ordinal,parser_version,body_ciphertext,"
            "body_sha256,body_aad_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (unit.id, unit.enterprise_id, unit.knowledge_scope_id, unit.document_record_id,
             unit.document_version_id, unit.source_sha256, unit.page_number, unit.ordinal,
             unit.parser_version, ciphertext, unit.body_sha256, aad_sha),
        )

    async def _units(self, unit):
        async with session_scope(role="f1_api", enterprise_id=WORLD.enterprise_a, sub=WORLD.provider_a.sub) as session:
            return await rag.load_units_for_version(session, enterprise_id=unit.enterprise_id,
                knowledge_scope_id=unit.knowledge_scope_id, document_version_id=unit.document_version_id)

    async def _is_current(self, unit):
        async with session_scope(role="f1_api", enterprise_id=WORLD.enterprise_a, sub=WORLD.provider_a.sub) as session:
            return await rag.index_is_current(session, enterprise_id=unit.enterprise_id,
                knowledge_scope_id=unit.knowledge_scope_id, document_version_id=unit.document_version_id)

    def _report_snapshot(self):
        with STACK._bootstrap() as connection:
            return tuple(
                connection.execute(f"SELECT row_to_json(item)::text FROM f1.{table} AS item ORDER BY id").fetchall()
                for table in ("analysis_report", "analysis_report_version", "analysis_report_section", "analysis_report_citation")
            )

    async def _publish_bound_report(self):
        from platform_foundation.f1.features.analysis_reports import delivery_repository, worker
        created = await report_service.create_report(WORLD.provider_a, WORLD.bound_client_id, uuid.uuid4())
        queued = await report_service.generate_report(WORLD.provider_a, WORLD.bound_client_id, uuid.UUID(created["report_id"]), uuid.uuid4())
        delivery_id = delivery_repository.delivery_id_for(uuid.UUID(queued["job_id"]))
        claims = await delivery_repository.claim_due_deliveries()
        claim = next(item for item in claims if item.id == delivery_id)
        await worker._process_generation_delivery(claim.id, claim.dispatch_token)
        self.assertEqual((await report_service.job_status(WORLD.provider_a, uuid.UUID(queued["job_id"])))["status"], "draft")
        version_id = uuid.UUID(queued["version_id"])
        await report_service.apply_transition(WORLD.provider_a, version_id, "submit")
        await report_service.apply_transition(WORLD.provider_a, version_id, "approve",
            checklist={"citation_traceable": True, "risks_complete": True, "usage_boundary": True})
        await report_service.apply_transition(WORLD.provider_a, version_id, "publish")

    def test_rebuild_rollback_no_mixed_manifest_and_published_snapshot_unchanged(self):
        async def exercise():
            source, unit = self._seed_source("bound")
            await self._publish_bound_report()
            frozen = self._report_snapshot()
            old = canonical_unit(enterprise_id=unit.enterprise_id, knowledge_scope_id=unit.knowledge_scope_id,
                document_record_id=unit.document_record_id, document_version_id=unit.document_version_id,
                source_sha256=unit.source_sha256, page_number=1, ordinal=1, parser_version="pypdf-6.14.2",
                text="Legacy header-only evidence must remain until a complete replacement commits.")
            with STACK._bootstrap() as connection:
                connection.execute("SELECT set_config('session_replication_role','replica',false)")
                connection.execute("DELETE FROM f1.material_rag_unit WHERE document_version_id=%s", (unit.document_version_id,))
                self._insert_unit(connection, old)
            self.assertFalse(await self._is_current(unit))
            async with session_scope(role="f1_api", enterprise_id=WORLD.enterprise_a, sub=WORLD.provider_a.sub) as session:
                with self.assertRaises(GenerationFailed) as raised:
                    await reports.load_eligible_sources(session, WORLD.enterprise_a, WORLD.bound_client_id)
                self.assertEqual(raised.exception.reason, "REPORT_SOURCE_INDEX_OUTDATED")

            real_persist = local_index.persist_canonical_units
            async def fault_after_insert(session, units):
                await real_persist(session, units)
                # A fresh tenant connection must still see the committed old
                # manifest while DELETE + new INSERT are uncommitted.
                visible = await self._units(unit)
                self.assertEqual(tuple(item.id for item in visible), (old.id,))
                raise RuntimeError("INJECTED_INDEX_PERSIST_FAILURE")

            flags = {"F1_LOCAL_ENGINEERING": "1", "F1_MATERIAL_AUTO_PIPELINE_LOCAL": "1",
                     "F1_MATERIAL_RAG_LOCAL_INDEX": "1", "F1_MATERIAL_RAG_ORCHESTRATION_LOCAL": "0"}
            with patch.dict(os.environ, flags), patch.object(local_index, "_open_source", side_effect=lambda *_: io.BytesIO(source)):
                legacy_job = await rag.enqueue_job(WORLD.provider_a, document_version_id=unit.document_version_id,
                    action="index", idempotency_key="layout-legacy-done-" + str(uuid.uuid4()))
                legacy_claim = await rag.claim_job(legacy_job, worker_id="layout-regression", lease_seconds=900)
                self.assertIsNotNone(legacy_claim)
                self.assertTrue(await rag.finish_job(legacy_claim, status="done", result_manifest_sha256="d" * 64, indexed_unit_count=1))
                context = await coordinator._load_context(WORLD.provider_a, unit.document_version_id)
                self.assertEqual(context.index_status, "done")
                self.assertFalse(context.index_current)
                self.assertEqual(coordinator._index_stage(context).reason_code, "MATERIAL_INDEX_REPROCESS_REQUIRED")
                first = await rag.enqueue_job(WORLD.provider_a, document_version_id=unit.document_version_id,
                    action="rebuild", idempotency_key="layout-rollback-" + str(uuid.uuid4()))
                with patch.object(local_index, "persist_canonical_units", side_effect=fault_after_insert):
                    outcome = await local_index.run_local_index_job(first, worker_id="layout-regression")
                self.assertEqual(outcome.kind, "FAILED")
                self.assertEqual(tuple(item.id for item in await self._units(unit)), (old.id,))
                self.assertFalse(await self._is_current(unit))
                second = await rag.enqueue_job(WORLD.provider_a, document_version_id=unit.document_version_id,
                    action="rebuild", idempotency_key="layout-success-" + str(uuid.uuid4()))
                outcome = await local_index.run_local_index_job(second, worker_id="layout-regression")
                self.assertEqual(outcome.kind, "DONE")
                context = await coordinator._load_context(WORLD.provider_a, unit.document_version_id)
                self.assertTrue(context.index_current)
                self.assertEqual(coordinator._index_stage(context).status, "ready")
            current = await self._units(unit)
            self.assertTrue(await self._is_current(unit))
            self.assertEqual(len(current), 1)
            self.assertEqual(current[0].parser_version, PDF_TEXT_PARSER_VERSION)
            self.assertNotEqual(current[0].id, old.id)
            self.assertEqual(self._report_snapshot(), frozen)
        asyncio.run(exercise())

    def test_legacy_ready_analysis_appends_new_contract_and_preserves_predecessor(self):
        async def exercise():
            source, unit = self._seed_source("race")
            predecessor_id = uuid.uuid4()
            with STACK._bootstrap() as connection:
                connection.execute("SELECT set_config('session_replication_role','replica',false)")
                connection.execute(
                    "INSERT INTO f1.material_analysis (id,enterprise_id,document_version_id,source_sha256,"
                    "status,document_profile,page_count) VALUES (%s,%s,%s,%s,'ready','text',1)",
                    (predecessor_id, unit.enterprise_id, unit.document_version_id, unit.source_sha256),
                )
                before = connection.execute("SELECT row_to_json(item)::text FROM f1.material_analysis AS item WHERE id=%s", (predecessor_id,)).fetchone()
                self.assertEqual(connection.execute("SELECT extraction_contract FROM f1.material_analysis WHERE id=%s", (predecessor_id,)).fetchone(), (1,))
            result = analyze_pdf(io.BytesIO(source), expected_sha256=unit.source_sha256)
            with patch.dict(os.environ, {"F1_MATERIAL_AUTO_PIPELINE_LOCAL": "1", "F1_MATERIAL_RAG_LOCAL_INDEX": "1"}):
                context = await coordinator._load_context(WORLD.provider_a, unit.document_version_id)
                self.assertFalse(context.analysis_current)
                self.assertEqual(coordinator._analysis_stage(context).reason_code, "MATERIAL_ANALYSIS_REPROCESS_REQUIRED")
                outcome = await intake.persist_material_analysis(WORLD.provider_a, document_version_id=unit.document_version_id,
                    source_sha256=unit.source_sha256, page_count=1, result=result)
                self.assertEqual(outcome, "superseded")
                again = await intake.persist_material_analysis(WORLD.provider_a, document_version_id=unit.document_version_id,
                    source_sha256=unit.source_sha256, page_count=1, result=result)
                self.assertEqual(again, "unchanged")
                context = await coordinator._load_context(WORLD.provider_a, unit.document_version_id)
                self.assertTrue(context.analysis_current)
                self.assertEqual(coordinator._analysis_stage(context).status, "ready")
            with STACK._bootstrap() as connection:
                self.assertEqual(connection.execute("SELECT row_to_json(item)::text FROM f1.material_analysis AS item WHERE id=%s", (predecessor_id,)).fetchone(), before)
                rows = connection.execute("SELECT analysis_revision,extraction_contract,supersedes_analysis_id FROM f1.material_analysis WHERE document_version_id=%s ORDER BY analysis_revision", (unit.document_version_id,)).fetchall()
                self.assertEqual(rows, [(1, 1, None), (2, MATERIAL_EXTRACTION_CONTRACT, predecessor_id)])
            async with session_scope(role="f1_api", enterprise_id=WORLD.enterprise_a, sub=WORLD.provider_a.sub) as session:
                # Version metadata is internal and immutable even for an API
                # caller with otherwise legitimate classification permissions.
                with self.assertRaises(Exception):
                    await session.execute(text("UPDATE f1.material_analysis SET extraction_contract=2 WHERE id=:id"), {"id": predecessor_id})
        asyncio.run(exercise())

    def test_legacy_confirmed_analysis_is_not_automatically_overwritten(self):
        async def exercise():
            source, unit = self._seed_source("provider")
            result = analyze_pdf(io.BytesIO(source), expected_sha256=unit.source_sha256)
            with patch.dict(os.environ, {"F1_MATERIAL_AUTO_PIPELINE_LOCAL": "1"}):
                await intake.persist_material_analysis(WORLD.provider_a, document_version_id=unit.document_version_id,
                    source_sha256=unit.source_sha256, page_count=1, result=result)
                material = await intake.get_material_analysis(WORLD.provider_a, unit.document_version_id)
                await intake.set_material_kind(WORLD.provider_a, material.id, kind="policy")
                await confirm_policy_draft(WORLD.provider_a, material.id,
                    body=ConfirmPolicyDraftIn.model_validate({
                        "source": {"title": "Synthetic historical policy", "publisher": "Fixture",
                            "source_type": "internal", "jurisdiction": "Fixture", "source_reference": "fixture-only"},
                        "version": {"title": "Confirmed fixture", "domain": "environment", "summary": "Synthetic historical confirmation"},
                    }), idempotency_key="layout-confirm-" + str(uuid.uuid4()))
                # Model a pre-upgrade confirmed row without claiming a fresh
                # confirmation happened under the obsolete extraction engine.
                with STACK._bootstrap() as connection:
                    connection.execute("SELECT set_config('session_replication_role','replica',false)")
                    connection.execute("UPDATE f1.material_analysis SET extraction_contract=1 WHERE id=%s", (material.id,))
                    before = connection.execute("SELECT row_to_json(item)::text FROM f1.material_analysis AS item WHERE id=%s", (material.id,)).fetchone()
                with self.assertRaises(RuntimeError) as raised:
                    await intake.persist_material_analysis(WORLD.provider_a, document_version_id=unit.document_version_id,
                        source_sha256=unit.source_sha256, page_count=1, result=result)
                self.assertEqual(str(raised.exception), "MATERIAL_ANALYSIS_CONFIRMED_OCR_REVIEW_REQUIRED")
                with self.assertRaises(IngestionError) as raised:
                    await retry_ready_material_analysis(WORLD.provider_a, unit.document_version_id)
                self.assertEqual(raised.exception.code, "MATERIAL_ANALYSIS_CONFIRMED_OCR_REVIEW_REQUIRED")
                async with session_scope(role="f1_api", enterprise_id=WORLD.enterprise_a, sub=WORLD.provider_a.sub) as session:
                    with self.assertRaises(Exception) as rejected:
                        await session.execute(text(
                            "INSERT INTO f1.material_analysis (id,enterprise_id,document_version_id,source_sha256,"
                            "status,document_profile,page_count,analysis_revision,supersedes_analysis_id,extraction_contract) "
                            "VALUES (:id,:enterprise_id,:version_id,:sha,'ready','text',1,2,:prior,2)"),
                            {"id": uuid.uuid4(), "enterprise_id": unit.enterprise_id, "version_id": unit.document_version_id,
                             "sha": unit.source_sha256, "prior": material.id})
                    self.assertIn("MATERIAL_ANALYSIS_SUPERSESSION_INVALID", str(rejected.exception))
                with STACK._bootstrap() as connection:
                    self.assertEqual(connection.execute("SELECT row_to_json(item)::text FROM f1.material_analysis AS item WHERE id=%s", (material.id,)).fetchone(), before)
                    self.assertEqual(connection.execute("SELECT count(*) FROM f1.material_analysis WHERE document_version_id=%s", (unit.document_version_id,)).fetchone(), (1,))
        asyncio.run(exercise())
