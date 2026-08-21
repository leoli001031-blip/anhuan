from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import importlib.machinery
import importlib.util
import io
import ipaddress
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import get_type_hints
from unittest.mock import AsyncMock, MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _ensure_p3_namespace() -> None:
    import types

    for name, path in (
        ("platform_foundation", ROOT / "src" / "platform_foundation"),
        ("platform_foundation.f1", ROOT / "src" / "platform_foundation" / "f1"),
        (
            "platform_foundation.f1.features",
            ROOT / "src" / "platform_foundation" / "f1" / "features",
        ),
        (
            "platform_foundation.f1.features.p3",
            ROOT / "src" / "platform_foundation" / "f1" / "features" / "p3",
        ),
    ):
        existing = sys.modules.get(name)
        if existing is not None and getattr(existing, "__file__", None):
            continue
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(path)]
            module.__package__ = name
            sys.modules[name] = module


class MaterialRagValueContractTests(unittest.TestCase):
    def test_sensitive_text_never_renders_body(self) -> None:
        from platform_foundation.f1.features.material_rag.contracts import SensitiveText

        secret = "body-that-must-not-render"
        value = SensitiveText(secret)
        self.assertEqual(value.reveal(), secret)
        self.assertNotIn(secret, repr(value))
        self.assertNotIn(secret, str(value))

    def test_canonical_unit_is_deterministic_and_filtered(self) -> None:
        from platform_foundation.f1.features.material_rag.security import canonical_unit

        identity = {
            "enterprise_id": uuid.UUID("10000000-0000-4000-8000-000000000001"),
            "knowledge_scope_id": uuid.UUID("10000000-0000-4000-8000-000000000002"),
            "document_record_id": uuid.UUID("10000000-0000-4000-8000-000000000003"),
            "document_version_id": uuid.UUID("10000000-0000-4000-8000-000000000004"),
            "source_sha256": (
                "e64cb41465eaf3fc550dbc881c06d687275a8d2b6850f34c703c111a4a3cfc46"
            ),
            "page_number": 7,
            "ordinal": 1,
            "parser_version": "pypdf-6.14.2",
            "text": "联系人：张三\n电话：13800138000\n邮箱：demo@example.invalid\n正文证据。",
        }
        first = canonical_unit(**identity)
        second = canonical_unit(**identity)
        self.assertEqual((first.id, first.body_sha256), (second.id, second.body_sha256))
        filtered = first.body.reveal()
        self.assertIn("[REDACTED]", filtered)
        self.assertNotIn("13800138000", filtered)
        self.assertNotIn("demo@example.invalid", filtered)
        self.assertEqual(
            first.body_sha256,
            hashlib.sha256(filtered.encode("utf-8")).hexdigest(),
        )

    def test_retrieval_context_cardinality_and_repr(self) -> None:
        from platform_foundation.f1.features.material_rag.contracts import (
            RetrievalContext,
        )

        enterprise = uuid.UUID("20000000-0000-4000-8000-000000000001")
        provider = uuid.UUID("20000000-0000-4000-8000-000000000002")
        client_scope = uuid.UUID("20000000-0000-4000-8000-000000000003")
        account = uuid.UUID("20000000-0000-4000-8000-000000000004")
        context = RetrievalContext(
            enterprise_id=enterprise,
            kind="client",
            client_account_id=account,
            scope_ids=(provider, client_scope),
        )
        rendered = repr(context)
        self.assertNotIn(str(provider), rendered)
        self.assertNotIn(str(client_scope), rendered)
        self.assertRegex(context.context_sha256, r"^[0-9a-f]{64}$")
        with self.assertRaisesRegex(ValueError, "MATERIAL_CONTEXT_INVALID"):
            RetrievalContext(
                enterprise_id=enterprise,
                kind="client",
                client_account_id=account,
                scope_ids=(provider,),
            )

    def test_public_freeform_query_fails_before_database_or_network(self) -> None:
        from platform_foundation.f1.auth import Tenant
        from platform_foundation.f1.features.material_rag.contracts import (
            MaterialRagUnavailable,
            RetrievalContext,
        )
        from platform_foundation.f1.features.material_rag.service import (
            run_verified_retrieval,
        )

        enterprise = uuid.UUID("30000000-0000-4000-8000-000000000001")
        context = RetrievalContext(
            enterprise_id=enterprise,
            kind="service_provider",
            client_account_id=None,
            scope_ids=(uuid.UUID("30000000-0000-4000-8000-000000000002"),),
        )
        tenant = Tenant(enterprise_id=enterprise, sub="contract-user", roles=())
        with self.assertRaisesRegex(
            MaterialRagUnavailable,
            "MATERIAL_QUERY_EXTERNAL_PROCESSING_NOT_AUTHORIZED",
        ):
            asyncio.run(run_verified_retrieval("arbitrary question", tenant, context))

    def test_registered_verifier_queries_are_closed_and_pii_free(self) -> None:
        from platform_foundation.f1.auth import Tenant
        from platform_foundation.f1.features.material_rag.contracts import (
            MaterialRagUnavailable,
            RetrievalContext,
        )
        from platform_foundation.f1.features.material_rag.security import (
            SYNTHETIC_AUTHORIZED_EMBEDDING_TEXTS,
            SYNTHETIC_AUTHORIZED_SOURCE_SHA256,
            assert_external_text_safe,
        )
        from platform_foundation.f1.features.material_rag.service import (
            retrieve_registered_verifier_query,
        )

        # Two canary bodies, three fixed queries, and six opaque document
        # aliases are the complete fixed surface outside canonical Demo bodies.
        self.assertEqual(len(SYNTHETIC_AUTHORIZED_EMBEDDING_TEXTS), 11)
        self.assertEqual(len(set(SYNTHETIC_AUTHORIZED_EMBEDDING_TEXTS)), 11)
        self.assertEqual(len(SYNTHETIC_AUTHORIZED_SOURCE_SHA256), 2)
        for value in SYNTHETIC_AUTHORIZED_EMBEDDING_TEXTS:
            self.assertLessEqual(len(value), 1_600)
            assert_external_text_safe(value)
        enterprise = uuid.UUID("30000000-0000-4000-8000-000000000011")
        tenant = Tenant(enterprise_id=enterprise, sub="contract-user", roles=())
        context = RetrievalContext(
            enterprise_id=enterprise,
            kind="service_provider",
            client_account_id=None,
            scope_ids=(uuid.UUID("30000000-0000-4000-8000-000000000012"),),
        )
        with self.assertRaisesRegex(
            MaterialRagUnavailable,
            "MATERIAL_QUERY_EXTERNAL_PROCESSING_NOT_AUTHORIZED",
        ):
            asyncio.run(
                retrieve_registered_verifier_query(
                    "unregistered synthetic query", tenant, context
                )
            )

    def test_material_evidence_has_no_physical_adapter_identity(self) -> None:
        from platform_foundation.f1.features.material_rag.contracts import (
            MaterialEvidence,
        )

        names = {field.name for field in dataclasses.fields(MaterialEvidence)}
        self.assertFalse(
            names
            & {
                "dataset_id",
                "dataset_ref",
                "document_id",
                "chunk_id",
                "ragflow_dataset_id",
            }
        )
        self.assertTrue(
            {
                "document_record_id",
                "document_version_id",
                "document_name",
                "version_number",
                "page_number",
                "body_sha256",
                "snippet",
            }.issubset(names)
        )

    def test_query_context_changes_encryption_identity(self) -> None:
        from platform_foundation.f1.qa_service import _aad

        request_id = uuid.UUID("31000000-0000-4000-8000-000000000001")
        enterprise_id = uuid.UUID("31000000-0000-4000-8000-000000000002")
        question_sha = "1" * 64
        client_a = "2" * 64
        client_b = "3" * 64
        self.assertNotEqual(
            _aad(request_id, enterprise_id, question_sha, client_a),
            _aad(request_id, enterprise_id, question_sha, client_b),
        )

    def test_public_material_claim_persists_context_bound_refusal(self) -> None:
        from platform_foundation.f1 import qa_service
        from platform_foundation.f1.auth import Tenant
        from platform_foundation.f1.features.material_rag.contracts import (
            RetrievalContext,
        )
        from platform_foundation.f1.features.material_rag import service as rag_service

        enterprise = uuid.UUID("31100000-0000-4000-8000-000000000001")
        request_id = uuid.UUID("31100000-0000-4000-8000-000000000002")
        owner_token = uuid.UUID("31100000-0000-4000-8000-000000000003")
        tenant = Tenant(enterprise_id=enterprise, sub="contract-user", roles=())
        context = RetrievalContext(
            enterprise_id=enterprise,
            kind="service_provider",
            client_account_id=None,
            scope_ids=(uuid.UUID("31100000-0000-4000-8000-000000000004"),),
        )
        reservation = qa_service.QaReservation(
            qa_service.ReservationState.CLAIMED,
            request_id,
            owner_token=owner_token,
            attempt=1,
        )
        reserve = AsyncMock(return_value=reservation)
        complete = AsyncMock()
        retrieval = AsyncMock()
        question = "公开自由问题不得外发"
        with (
            patch.object(qa_service, "reserve_request", reserve),
            patch.object(qa_service, "complete_request", complete),
            patch.object(rag_service, "run_verified_retrieval", retrieval),
        ):
            outcome = asyncio.run(
                qa_service.ask_material_question(
                    question, request_id, tenant, context
                )
            )

        self.assertIsNone(outcome.answer)
        self.assertEqual(outcome.citations, [])
        self.assertEqual(
            outcome.refusal_reason,
            "MATERIAL_QUERY_EXTERNAL_PROCESSING_NOT_AUTHORIZED",
        )
        reserve.assert_awaited_once_with(
            request_id,
            tenant,
            question,
            query_context_sha256=context.context_sha256,
        )
        complete.assert_awaited_once_with(
            request_id,
            tenant,
            question,
            owner_token,
            outcome,
            query_context_sha256=context.context_sha256,
        )
        retrieval.assert_not_awaited()

    def test_public_material_replay_and_nonterminal_states(self) -> None:
        from platform_foundation.f1 import qa_service
        from platform_foundation.f1.auth import Tenant
        from platform_foundation.f1.features.material_rag.contracts import (
            RetrievalContext,
        )

        enterprise = uuid.UUID("31200000-0000-4000-8000-000000000001")
        request_id = uuid.UUID("31200000-0000-4000-8000-000000000002")
        tenant = Tenant(enterprise_id=enterprise, sub="contract-user", roles=())
        context = RetrievalContext(
            enterprise_id=enterprise,
            kind="client",
            client_account_id=uuid.UUID(
                "31200000-0000-4000-8000-000000000003"
            ),
            scope_ids=(
                uuid.UUID("31200000-0000-4000-8000-000000000004"),
                uuid.UUID("31200000-0000-4000-8000-000000000005"),
            ),
        )
        refusal = qa_service.QaResult(
            None,
            [],
            "MATERIAL_QUERY_EXTERNAL_PROCESSING_NOT_AUTHORIZED",
            str(request_id),
        )
        question = "相同问题"

        replay = qa_service.QaReservation(
            qa_service.ReservationState.REPLAY,
            request_id,
            result=refusal,
            attempt=1,
        )
        with (
            patch.object(
                qa_service,
                "reserve_request",
                AsyncMock(return_value=replay),
            ),
            patch.object(qa_service, "complete_request", AsyncMock()) as complete,
        ):
            self.assertIs(
                asyncio.run(
                    qa_service.ask_material_question(
                        question, request_id, tenant, context
                    )
                ),
                refusal,
            )
            complete.assert_not_awaited()

        for state, error in (
            (qa_service.ReservationState.CONFLICT, qa_service.RequestIdConflict),
            (qa_service.ReservationState.IN_PROGRESS, qa_service.RequestInProgress),
        ):
            with self.subTest(state=state), patch.object(
                qa_service,
                "reserve_request",
                AsyncMock(
                    return_value=qa_service.QaReservation(state, request_id)
                ),
            ):
                with self.assertRaises(error):
                    asyncio.run(
                        qa_service.ask_material_question(
                            question, request_id, tenant, context
                        )
                    )

    def test_material_citation_matches_verified_evidence_identity(self) -> None:
        from platform_foundation.f1.api.routers.material_qa import MaterialCitation

        self.assertEqual(
            set(MaterialCitation.model_fields),
            {
                "canonical_unit_id",
                "document_record_id",
                "document_version_id",
                "document_name",
                "version_number",
                "source_sha256",
                "page_number",
                "body_sha256",
                "snippet",
            },
        )

    def test_public_request_forbids_adapter_selected_scope_fields(self) -> None:
        from pydantic import ValidationError
        from platform_foundation.f1.api.routers.material_qa import MaterialQaRequest

        base = {
            "question": "内部材料证据查询",
            "request_id": "32000000-0000-4000-8000-000000000001",
        }
        for forbidden in ("dataset_id", "knowledge_scope_id", "scope_ids"):
            with self.subTest(forbidden=forbidden), self.assertRaises(ValidationError):
                MaterialQaRequest.model_validate({**base, forbidden: str(uuid.uuid4())})


class MaterialRagDemoAttestationTests(unittest.TestCase):
    def _claim_and_unit(self):
        from platform_foundation.f1.features.material_rag.contracts import (
            MaterialRagJobClaim,
        )
        from platform_foundation.f1.features.material_rag.security import canonical_unit

        enterprise = uuid.UUID("40000000-0000-4000-8000-000000000001")
        scope = uuid.UUID("40000000-0000-4000-8000-000000000002")
        record = uuid.UUID("40000000-0000-4000-8000-000000000003")
        version = uuid.UUID("40000000-0000-4000-8000-000000000004")
        source_sha = (
            "ab242c22f92e73d519c5e5485df7027ad33812e96324943b6591171d0e41fc07"
        )
        claim = MaterialRagJobClaim(
            id=uuid.UUID("40000000-0000-4000-8000-000000000005"),
            enterprise_id=enterprise,
            knowledge_scope_id=scope,
            document_record_id=record,
            document_version_id=version,
            upload_task_id=uuid.UUID("40000000-0000-4000-8000-000000000007"),
            source_sha256=source_sha,
            action="index",
            lease_token=uuid.UUID("40000000-0000-4000-8000-000000000006"),
            attempt=1,
        )
        unit = canonical_unit(
            enterprise_id=enterprise,
            knowledge_scope_id=scope,
            document_record_id=record,
            document_version_id=version,
            source_sha256=source_sha,
            page_number=1,
            ordinal=1,
            parser_version="f0h-ppocrv6-3.9.2",
            text="A locally filtered canonical fragment for attestation.",
            ocr_applied=True,
        )
        return claim, unit

    def test_attestation_rejects_non_allowlisted_file_bytes(self) -> None:
        from platform_foundation.f1.features.material_rag.contracts import (
            MaterialRagIntegrityError,
        )
        from platform_foundation.f1.features.material_rag.security import (
            create_demo_unit_manifest_proof,
        )

        claim, unit = self._claim_and_unit()
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "opaque.pdf"
            source.write_bytes(b"%PDF-1.7\nnot-an-authorized-demo\n")
            source.chmod(0o600)
            with self.assertRaisesRegex(
                MaterialRagIntegrityError, "MATERIAL_RAG_SOURCE_NOT_AUTHORIZED"
            ):
                create_demo_unit_manifest_proof(
                    source_path=source.resolve(), claim=claim, units=(unit,)
                )

    def test_attestation_rejects_symlink_source(self) -> None:
        from platform_foundation.f1.features.material_rag.contracts import (
            MaterialRagIntegrityError,
        )
        from platform_foundation.f1.features.material_rag.security import (
            create_demo_unit_manifest_proof,
        )

        claim, unit = self._claim_and_unit()
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.pdf"
            source.write_bytes(b"%PDF-1.7\nnot-an-authorized-demo\n")
            source.chmod(0o600)
            link = Path(temporary) / "link.pdf"
            os.symlink(source, link)
            with self.assertRaisesRegex(
                MaterialRagIntegrityError, "MATERIAL_RAG_SOURCE_OPEN_FAILED"
            ):
                create_demo_unit_manifest_proof(
                    source_path=link.absolute(), claim=claim, units=(unit,)
                )


class MaterialRagVerifierContractTests(unittest.TestCase):
    @staticmethod
    def _page(number: int, *, ocr: bool) -> dict[str, object]:
        return {
            "page_id": hashlib.sha256(f"page-{number}".encode()).hexdigest(),
            "page_no": number,
            "media_box": {
                "left": "0.000",
                "bottom": "0.000",
                "right": "612.000",
                "top": "792.000",
            },
            "crop_box": {
                "left": "0.000",
                "bottom": "0.000",
                "right": "612.000",
                "top": "792.000",
            },
            "rotation": 0,
            "native_characters": 0 if ocr else 80,
            "bad_character_ppm": 0,
            "native_text_sha256": hashlib.sha256(
                f"native-{number}".encode()
            ).hexdigest(),
            "decision": "FULL_PAGE_OCR_REQUIRED" if ocr else "NATIVE_CANDIDATE",
            "reason_codes": ["LOW_NATIVE_TEXT"]
            if ocr
            else ["NATIVE_TEXT_THRESHOLD_MET"],
        }

    def _entries(self) -> list[dict[str, object]]:
        from infra.f1.local_material_rag_verify import FIXTURES

        return [
            {
                "group": "core",
                "line": spec.line,
                "type": "PDF",
                "route": "PDF_NATIVE_OR_OCR_PROBE",
                "parse_status": "NATIVE_PROBE_COMPLETE",
                "document_id": hashlib.sha256(
                    f"document-{spec.line}".encode()
                ).hexdigest(),
                "page_count": spec.page_count,
                "pages": [
                    self._page(number, ocr=number in spec.ocr_pages)
                    for number in range(1, spec.page_count + 1)
                ],
            }
            for spec in FIXTURES
        ]

    def test_fixture_set_is_exactly_four_client_a_documents(self) -> None:
        from infra.f1.local_material_rag_verify import FIXTURES

        self.assertEqual(tuple(spec.line for spec in FIXTURES), (1, 2, 19, 21))
        self.assertEqual(sum(spec.page_count for spec in FIXTURES), 136)
        self.assertEqual(sum(len(spec.ocr_pages) for spec in FIXTURES), 6)
        self.assertEqual(
            sum(spec.page_count - len(spec.ocr_pages) for spec in FIXTURES),
            130,
        )
        self.assertEqual(
            len({spec.source_sha256 for spec in FIXTURES}),
            4,
        )

    def test_authorization_manifest_uses_worker_canonical_body_hashes(self) -> None:
        from infra.f1.local_material_rag_verify import (
            ARK_AUTHORIZATION_SCHEMA,
            FIXTURES,
            FixtureDocument,
            ParsedPage,
            _authorization_body_sha256,
            _authorization_payload,
        )
        from platform_foundation.f1.features.material_rag.security import (
            SYNTHETIC_AUTHORIZED_EMBEDDING_TEXTS,
            canonical_page_units,
        )

        fixtures = tuple(
            FixtureDocument(
                spec=spec,
                body=b"private-demo-body",
                pages=tuple(
                    ParsedPage(
                        page_number=page_number,
                        parser_version="f0h-ppocrv6-3.9.2"
                        if page_number in spec.ocr_pages
                        else "pypdf-6.14.2",
                        text=f"same body on page {page_number}",
                        ocr_applied=page_number in spec.ocr_pages,
                        table_candidate=page_number % 2 == 0,
                        two_column_candidate=page_number % 3 == 0,
                    )
                    for page_number in range(1, spec.page_count + 1)
                ),
            )
            for spec in FIXTURES
        )
        hashes = _authorization_body_sha256(fixtures)
        expected = {
            unit.body_sha256
            for fixture in fixtures
            for page in fixture.pages
            for unit in canonical_page_units(
                enterprise_id=uuid.uuid4(),
                knowledge_scope_id=uuid.uuid4(),
                document_record_id=uuid.uuid4(),
                document_version_id=uuid.uuid4(),
                source_sha256=fixture.spec.source_sha256,
                page_number=page.page_number,
                parser_version=page.parser_version,
                text=page.text,
                ocr_applied=page.ocr_applied,
                table_candidate=page.table_candidate,
                two_column_candidate=page.two_column_candidate,
            )
        }
        expected.update(
            hashlib.sha256(value.encode("utf-8")).hexdigest()
            for value in SYNTHETIC_AUTHORIZED_EMBEDDING_TEXTS
        )
        self.assertEqual(hashes, tuple(sorted(expected)))
        self.assertTrue(hashes)
        self.assertEqual(len(hashes), len(set(hashes)))
        payload = json.loads(_authorization_payload(hashes).decode("ascii"))
        self.assertEqual(
            payload,
            {
                "body_sha256": list(hashes),
                "schema": ARK_AUTHORIZATION_SCHEMA,
            },
        )
        self.assertEqual(
            _authorization_payload(hashes),
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii"),
        )
        self.assertFalse(
            {"text", "source_sha256", "filename", "object_key"} & set(payload)
        )

    def test_authorization_writer_contract_is_private_and_atomic(self) -> None:
        source = (ROOT / "infra/f1/local_material_rag_verify.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("F1_MATERIAL_RAG_ARK_AUTHORIZATION_FILE", source)
        self.assertIn("anhuan-material-rag-body-authorization-v1", source)
        self.assertIn("os.O_NOFOLLOW", source)
        self.assertNotIn("os.fchown(", source)
        self.assertIn("ARK_AUTHORIZATION_OWNER = 65532", source)
        self.assertIn("stat.S_IMODE(directory_info.st_mode) != 0o700", source)
        self.assertIn("stat.S_IMODE(before.st_mode) != 0o600", source)
        self.assertIn("before.st_nlink != 1", source)
        self.assertIn("os.replace(", source)
        self.assertIn("LOCAL_MATERIAL_RAG_AUTHORIZATION_OK", source)

    def test_selected_plan_requires_each_core_line_once(self) -> None:
        from infra.f1.local_material_rag_verify import (
            MaterialRagVerifyError,
            _validate_selected_plan_entries,
        )

        entries = self._entries()
        negative_same_lines = [
            {"group": "negative", "line": line, "pages": None}
            for line in (1, 2)
        ]
        self.assertEqual(
            set(_validate_selected_plan_entries(entries + negative_same_lines)),
            {1, 2, 19, 21},
        )
        with self.assertRaisesRegex(
            MaterialRagVerifyError, "LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED"
        ):
            _validate_selected_plan_entries(entries + [dict(entries[0])])
        with self.assertRaisesRegex(
            MaterialRagVerifyError, "LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED"
        ):
            _validate_selected_plan_entries(entries[:-1])

    def test_metrics_are_integer_only_and_cover_negative_gates(self) -> None:
        from infra.f1.local_material_rag_verify import (
            MaterialRagVerificationCounts,
        )

        names = {field.name for field in dataclasses.fields(MaterialRagVerificationCounts)}
        self.assertEqual(set(get_type_hints(MaterialRagVerificationCounts).values()), {int})
        self.assertTrue(
            {
                "held_enqueue_rejection_count",
                "pre_release_remote_zero_snapshot_count",
                "premature_index_count",
                "manual_report_classification_preserved_count",
                "duplicate_unit_count",
                "unit_identity_conflict_rejection_count",
                "index_replay_job_count",
                "synthetic_index_job_count",
                "synthetic_canary_version_count",
                "synthetic_canonical_unit_count",
                "synthetic_scope_unauthorized_rls_visible_count",
                "provider_indexed_remote_document_count",
                "client_b_indexed_remote_document_count",
                "provider_retrieval_hit_count",
                "client_a_scoped_retrieval_hit_count",
                "client_b_retrieval_hit_count",
                "cross_scope_sibling_delete_proof_count",
                "sibling_scope_delete_leak_count",
                "pre_index_provider_empty_scope_refusal_count",
                "pre_index_client_b_no_hit_count",
                "pre_index_empty_scope_egress_count",
                "freeform_query_rejection_count",
                "context_idempotency_conflict_count",
                "wrong_context_aad_rejection_count",
                "forged_candidate_rejection_count",
                "unauthorized_rls_visible_count",
                "rebuild_mismatch_count",
                "stale_candidate_leak_count",
                "remote_dataset_residual_count",
                "ready_binding_residual_count",
                "binding_secret_residual_count",
                "external_llm_call_count",
                "egress_rejected_request_count",
                "object_residual_count",
                "bucket_residual_count",
            }.issubset(names)
        )
        self.assertNotIn("sibling_delete_leak_count", names)
        localctl = _load_localctl()
        self.assertTrue(hasattr(localctl, "_material_rag_verifier_metrics"))
        metrics = dict(localctl.EXPECTED_MATERIAL_RAG_FIXED_METRICS)
        metrics["canonical_unit_count"] = 136
        metrics["citation_count"] = 7
        metrics["client_a_indexed_remote_chunk_count"] = 136
        metrics["egress_forwarded_embedding_request_count"] = 1
        payload = json.dumps(metrics, sort_keys=True, separators=(",", ":"))
        ok = payload + "\nLOCAL_MATERIAL_RAG_VERIFY_OK\n"
        parsed = localctl._material_rag_verifier_metrics(ok)
        self.assertEqual(parsed["rebuild_job_count"], 4)
        enveloped = (
            "\x1b[0mAttaching to material-rag-verifier\n"
            + "material-rag-verifier exited with code 0\n"
            + "material-rag-verifier-1 exited with code 0\n"
            + ok
            + "container anhuan-material-rag-deadbeef12-material-rag-verifier-1 exited with code 0\n"
            + "container anhuan-material-rag-deadbeef12-material-rag-verifier-1 exited (0)\n"
            + "Container anhuan-material-rag-deadbeef12-material-rag-postgres-1  Exited (0) Less than a second ago\n"
        )
        self.assertEqual(
            localctl._material_rag_verifier_metrics(enveloped)["rebuild_job_count"],
            4,
        )
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(localctl.LocalError) as raised:
                localctl._material_rag_verifier_metrics(ok + "unexpected-line\n")
            self.assertEqual(str(raised.exception), "LOCAL_MATERIAL_RAG_OUTPUT_INVALID")
            extra_json = ok + payload + "\n"
            with self.assertRaises(localctl.LocalError):
                localctl._material_rag_verifier_metrics(extra_json)
            extra_ok = ok + "LOCAL_MATERIAL_RAG_VERIFY_OK\n"
            with self.assertRaises(localctl.LocalError):
                localctl._material_rag_verifier_metrics(extra_ok)
            long_tail = (
                ok
                + "Container anhuan-material-rag-deadbeef12-material-rag-verifier-1  "
                "Exited (0) 1 second ago with extra-unbounded-tail\n"
            )
            with self.assertRaises(localctl.LocalError):
                localctl._material_rag_verifier_metrics(long_tail)
        bad = dict(metrics)
        bad["rebuild_job_count"] = 3
        bad_payload = json.dumps(bad, sort_keys=True, separators=(",", ":"))
        printed = io.StringIO()
        with contextlib.redirect_stderr(printed):
            with self.assertRaises(localctl.LocalError):
                localctl._material_rag_verifier_metrics(
                    bad_payload + "\nLOCAL_MATERIAL_RAG_VERIFY_OK\n"
                )
        evidence_lines = [
            line
            for line in printed.getvalue().splitlines()
            if line.startswith("LOCAL_MATERIAL_RAG_OUTPUT_EVIDENCE ")
        ]
        self.assertEqual(len(evidence_lines), 1)
        evidence = json.loads(
            evidence_lines[0][len("LOCAL_MATERIAL_RAG_OUTPUT_EVIDENCE ") :]
        )
        self.assertEqual(evidence["mismatch"], "VALUE")
        self.assertEqual(evidence["mismatch_key"], "rebuild_job_count")
        self.assertEqual(evidence["other_kind"], "NONE")
        self.assertNotIn("unexpected-line", printed.getvalue())


    def test_remote_snapshot_semantics_ignore_only_physical_ids(self) -> None:
        from infra.f1.local_material_rag_verify import (
            RemoteChunkSnapshot,
            RemoteDocumentSnapshot,
            RemoteScopeSnapshot,
        )

        unit_id = uuid.UUID("51000000-0000-4000-8000-000000000001")
        identity = {
            "canonical_unit_id": unit_id,
            "knowledge_scope_id": uuid.UUID(
                "51000000-0000-4000-8000-000000000002"
            ),
            "document_record_id": uuid.UUID(
                "51000000-0000-4000-8000-000000000003"
            ),
            "document_version_id": uuid.UUID(
                "51000000-0000-4000-8000-000000000004"
            ),
            "source_sha256": "1" * 64,
            "page_number": 7,
            "body_sha256": "2" * 64,
            "content_sha256": "2" * 64,
        }

        def snapshot(remote_document_id: str, remote_chunk_id: str, **overrides):
            chunk = RemoteChunkSnapshot(
                remote_chunk_id=remote_chunk_id,
                **{**identity, **overrides},
            )
            document = RemoteDocumentSnapshot(
                remote_document_id=remote_document_id,
                document_name="MATERIAL_RAG_TEST_DOCUMENT",
                chunks=(chunk,),
            )
            return RemoteScopeSnapshot(
                dataset_ref="3" * 32,
                dataset_name="f1-material-test",
                documents=(document,),
            )

        before = snapshot("a" * 32, "b" * 32)
        rebuilt = snapshot("c" * 32, "d" * 32)
        wrong_body = snapshot(
            "c" * 32,
            "d" * 32,
            body_sha256="4" * 64,
            content_sha256="4" * 64,
        )
        self.assertNotEqual(before, rebuilt)
        self.assertEqual(
            before.semantic_fingerprint(), rebuilt.semantic_fingerprint()
        )
        self.assertNotEqual(
            before.semantic_fingerprint(), wrong_body.semantic_fingerprint()
        )

    def test_sensitive_verifier_setup_repr_is_body_free(self) -> None:
        from infra.f1.local_material_rag_verify import (
            FIXTURES,
            ProductSetup,
            UploadedDocument,
        )

        opaque_key = "opaque-key-that-must-not-render.pdf"
        upload = UploadedDocument(
            spec=FIXTURES[0],
            document_record_id=uuid.uuid4(),
            document_version_id=uuid.uuid4(),
            upload_task_id=uuid.uuid4(),
            knowledge_scope_id=uuid.uuid4(),
            object_key=opaque_key,
        )
        setup = ProductSetup(
            provider_scope_id=uuid.uuid4(),
            client_a_account_id=uuid.uuid4(),
            client_a_scope_id=uuid.uuid4(),
            client_b_account_id=uuid.uuid4(),
            client_b_scope_id=uuid.uuid4(),
            uploads=(upload,),
            synthetic_documents=(),
            held_enqueue_rejection_count=1,
            pre_release_remote_zero_snapshot_count=1,
            premature_index_count=0,
            manual_report_classification_preserved_count=1,
            cross_tenant_api_visible_count=0,
        )
        self.assertNotIn(opaque_key, repr(upload))
        self.assertNotIn(opaque_key, repr(setup))

    def test_verifier_uploads_demo_only_to_client_scope(self) -> None:
        source = (ROOT / "infra/f1/local_material_rag_verify.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"knowledge_scope_kind": "client"', source)
        self.assertNotIn('"knowledge_scope_kind": "service_provider"', source)
        self.assertIn("client.list_all_datasets(token)", source)
        self.assertIn("client.list_all_documents(token, dataset_id)", source)
        self.assertIn("client.list_chunks(token, dataset_id, document_id)", source)
        self.assertIn("material-rag-index-replay-v1-", source)
        self.assertIn("persist_canonical_units(session, (conflict,))", source)
        self.assertIn('declared_kind = "report"', source)
        self.assertNotIn("sibling_delete_leak_count", source)
        self.assertIn("MATERIAL_QUERY_EXTERNAL_PROCESSING_NOT_AUTHORIZED", source)
        self.assertIn("anhuan-material-rag-provider-attestation-v2", source)
        self.assertIn("anhuan-material-rag-ark-relay-audit-v2", source)
        self.assertIn("forwarded_non_embedding_request_count", source)
        self.assertIn("external_llm_call_count", source)
        self.assertIn("external_ocr_call_count", source)
        self.assertIn('"model_types": ["embedding"]', source)
        self.assertIn("stat.S_IMODE(before.st_mode) != 0o600", source)
        self.assertIn("LOCAL_MATERIAL_RAG_VERIFY_OK", source)
        self.assertIn("LOCAL_MATERIAL_RAG_P3_PREVIEW_EVIDENCE", source)
        self.assertIn("_print_p3_preview_evidence", source)
        self.assertIn("F1_CLAMD_SOCKET", (
            ROOT / "src/platform_foundation/f1/features/p3/scanner.py"
        ).read_text(encoding="utf-8"))
        preflight = source.split("def _preflight_scanner(", 1)[1].split(
            "class _DiscardText", 1
        )[0]
        self.assertIn('"P3_SCAN_PROTOCOL_ERROR"', preflight)
        self.assertIn("while time.monotonic() - started < 60:", preflight)
        for reason in (
            "LOCAL_MATERIAL_RAG_P3_CRM_FAILED",
            "LOCAL_MATERIAL_RAG_P3_UPLOAD_FAILED",
            "LOCAL_MATERIAL_RAG_P3_UPLOAD_HTTP_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_CONNECT_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_CONNECT_PIPE_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_CONNECT_REFUSED_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_CONNECT_RESET_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_DNS_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_ENGINE_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_ERROR_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_INCOMPLETE_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_INFECTED_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_PROTOCOL_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_REFUSED_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_STREAM_PIPE_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_STREAM_REFUSED_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_STREAM_RESET_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_TARGET_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_TIMEOUT_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_UNAVAILABLE_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_VERSION_PIPE_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_VERSION_REFUSED_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_VERSION_RESET_FAILED",
            "LOCAL_MATERIAL_RAG_P3_PREVIEW_FAILED",
            "LOCAL_MATERIAL_RAG_P3_RELEASE_FAILED",
            "LOCAL_MATERIAL_RAG_SEED_FAILED",
            "LOCAL_MATERIAL_RAG_STORAGE_FAILED",
        ):
            self.assertIn(reason, source)
            self.assertIn(reason, (ROOT / "scripts/localctl").read_text(encoding="utf-8"))


class MaterialRagStaticBoundaryTests(unittest.TestCase):
    def test_authorizer_is_one_shot_networkless_and_verifier_is_read_only(self) -> None:
        compose = (
            ROOT / "infra/f1/docker-compose.material-rag.yml"
        ).read_text(encoding="utf-8")
        localctl = (ROOT / "scripts/localctl").read_text(encoding="utf-8")
        authorizer = compose.split(
            "  material-rag-authorizer:", 1
        )[1].split("\n  material-rag-provider-provisioner:", 1)[0]
        verifier = compose.split(
            "  material-rag-verifier:", 1
        )[1].split("\nnetworks:", 1)[0]
        authorization_call = localctl.split(
            "authorization_result = _material_rag_compose_stage(", 1
        )[1].split("provision_result = _material_rag_compose_stage(", 1)[0]
        material_rag_compose_base = localctl.split(
            "def _material_rag_compose_base(", 1
        )[1].split("def _material_rag_compose(", 1)[0]

        self.assertIn("network_mode: none", authorizer)
        self.assertIn('user: "65532:65532"', authorizer)
        self.assertIn("--write-authorization", authorizer)
        self.assertIn(
            '"--progress",\n        "quiet",', material_rag_compose_base
        )
        self.assertIn(
            "material_rag_authorization:/run/material-rag-authorization\n",
            authorizer,
        )
        self.assertIn("material_rag_ocr_socket:/run/material-rag-ocr", authorizer)
        for volume_alias in (
            "*material-rag-core-manifest-volume",
            "*material-rag-native-plan-volume",
            "*material-rag-demo-e64cb414-volume",
            "*material-rag-demo-ab242c22-volume",
            "*material-rag-demo-12f20a5a-volume",
            "*material-rag-demo-973e6ac9-volume",
        ):
            self.assertIn(volume_alias, authorizer)
        for forbidden in (
            "networks:",
            "material_rag_verifier_secrets",
            "material_rag_control",
            "material_rag_egress",
            "material-rag-postgres",
            "material-rag-ragflow",
            "F1_ARK_API_KEY_FILE",
            "ARK_API_KEY",
            "/run/secrets",
        ):
            self.assertNotIn(forbidden, authorizer)

        self.assertIn(
            "material_rag_authorization:/run/material-rag-authorization:ro",
            verifier,
        )
        self.assertNotIn("--write-authorization", verifier)
        self.assertIn('"material-rag-authorizer"', authorization_call)
        self.assertNotIn('"material-rag-verifier"', authorization_call)
        self.assertIn("authorization_result.stderr", authorization_call)

    def test_provider_bootstrap_uses_pinned_internal_model_transaction(self) -> None:
        provision = (
            ROOT / "infra/f1/material-rag/provider_provision.py"
        ).read_text(encoding="utf-8")
        compose = (
            ROOT / "infra/f1/docker-compose.material-rag.yml"
        ).read_text(encoding="utf-8")

        self.assertIn('EXPECTED_RAGFLOW_VERSION = "v0.26.4"', provision)
        self.assertIn(
            'EXPECTED_RAGFLOW_BASE_URL = "http://material-rag-ragflow:80"',
            provision,
        )
        self.assertIn(
            'EXPECTED_RAGFLOW_LOOPBACK_URL = "http://127.0.0.1:80"',
            provision,
        )
        self.assertIn("{EXPECTED_RAGFLOW_BASE_URL, EXPECTED_RAGFLOW_LOOPBACK_URL}", provision)
        self.assertIn('"/api/v1/users"', provision)
        self.assertIn('"/api/v1/auth/login"', provision)
        self.assertIn('"/api/v1/system/tokens"', provision)
        self.assertIn("from api.db.db_models import (", provision)
        self.assertIn('sys.path.insert(0, str(RAGFLOW_ROOT))', provision)
        self.assertIn("TenantModelProvider.select()", provision)
        self.assertIn("TenantModelInstance.select()", provision)
        self.assertIn("TenantModel.select()", provision)
        self.assertIn("with DB.connection_context():", provision)
        self.assertIn("with DB.lock(lock_name, 60):", provision)
        self.assertIn("with DB.atomic():", provision)
        self.assertIn('MODEL_TYPE = "embedding"', provision)
        self.assertIn("INTERNAL_PASSWORD_RE.fullmatch(value)", provision)
        self.assertNotIn("verify_api_key", provision)
        self.assertNotIn("/{PROVIDER}/instances", provision)
        self.assertNotIn("execute_sql", provision)
        self.assertNotIn("Test if the api key is available", provision)
        for failure_reason in (
            "LOCAL_MATERIAL_RAG_PROVIDER_PREFLIGHT_FAILED",
            "LOCAL_MATERIAL_RAG_PROVIDER_IDENTITY_FAILED",
            "LOCAL_MATERIAL_RAG_PROVIDER_READY_FAILED",
            "LOCAL_MATERIAL_RAG_PROVIDER_UNAVAILABLE_FAILED",
            "LOCAL_MATERIAL_RAG_PROVIDER_REQUEST_FAILED",
            "LOCAL_MATERIAL_RAG_PROVIDER_RESPONSE_INVALID",
            "LOCAL_MATERIAL_RAG_PROVIDER_REGISTER_DISABLED",
            "LOCAL_MATERIAL_RAG_PROVIDER_REGISTER_FAILED",
            "LOCAL_MATERIAL_RAG_PROVIDER_LOGIN_FAILED",
            "LOCAL_MATERIAL_RAG_PROVIDER_TOKEN_FAILED",
            "LOCAL_MATERIAL_RAG_PROVIDER_RUNTIME_CONFIG_FAILED",
            "LOCAL_MATERIAL_RAG_PROVIDER_STATE_FAILED",
            "LOCAL_MATERIAL_RAG_PROVIDER_CONTROL_WRITE_FAILED",
            "LOCAL_MATERIAL_RAG_PROVIDER_INTERNAL_ERROR",
        ):
            self.assertIn(failure_reason, provision)
            self.assertIn(failure_reason, (
                ROOT / "scripts/localctl"
            ).read_text(encoding="utf-8"))
        self.assertIn('"/api/v1/system/config"', provision)
        self.assertIn('"curl"', provision)
        self.assertIn("--path-as-is", provision)
        self.assertIn("HTTPCookieProcessor", provision)
        self.assertIn("ProxyHandler({})", provision)
        self.assertIn('os.environ.pop(key, None)', provision)
        self.assertIn("redirect_stderr", provision)
        self.assertNotIn("str(error)", provision)
        provision_call = (
            ROOT / "scripts/localctl"
        ).read_text(encoding="utf-8").split(
            "provision_result = _material_rag_compose_stage(", 1
        )[1].split("if (", 1)[0]
        self.assertIn('"-T"', provision_call)

        provider_section = compose.split(
            "  material-rag-provider-provisioner:", 1
        )[1].split("\n  material-rag-unit:", 1)[0]
        self.assertIn(
            "infiniflow/ragflow@sha256:36c22d70e32494395c0cd5fa8fd65b6ff4aa1302a82ebca1d38d9f3d52d000b8",
            provider_section,
        )
        self.assertIn("MYSQL_HOST: material-rag-mysql", provider_section)
        self.assertIn("MYSQL_DBNAME: rag_flow", provider_section)
        self.assertIn("DB_TYPE: mysql", provider_section)
        self.assertIn('ALL_PROXY: ""', provider_section)
        self.assertIn('all_proxy: ""', provider_section)
        self.assertIn('network_mode: "service:material-rag-ragflow"', provider_section)
        self.assertIn("RAGFLOW_BASE_URL: http://127.0.0.1:80", provider_section)
        self.assertNotIn("networks:", provider_section)
        self.assertNotIn("material_rag_egress", provider_section)

    def test_migration_forces_rls_and_has_no_bypass_grants(self) -> None:
        migration = (
            ROOT / "infra/f1/alembic/versions/f1_0015_material_rag.py"
        ).read_text(encoding="utf-8")
        for table in (
            "material_rag_scope_binding",
            "material_rag_unit",
            "material_rag_job",
        ):
            self.assertIn(f"f1.{table}", migration)
        self.assertIn("FORCE ROW LEVEL SECURITY", migration)
        self.assertIn("MATERIAL_RAG_JOB_SOURCE_NOT_RELEASED", migration)
        self.assertIn("prepare_empty_material_rag_scope", migration)
        self.assertIn("finalize_empty_material_rag_scope", migration)
        self.assertIn("material_rag_unit_scope_delete_worker_select", migration)
        self.assertNotIn("BYPASSRLS", migration)

    def test_dedicated_migrate_requests_closed_f1_0016_not_head(self) -> None:
        migrator = (
            ROOT / "infra/f1/material-rag/migrate.py"
        ).read_text(encoding="utf-8")
        self.assertIn("F1_MATERIAL_RAG_MIGRATE_TARGET", migrator)
        self.assertIn("target=migrate_f1.F1_MATERIAL_RAG_MIGRATE_TARGET", migrator)
        self.assertIn('"f1_0016"', migrator)
        self.assertNotIn("command.upgrade", migrator)
        self.assertNotIn('"head"', migrator)
        self.assertNotIn("os.environ", migrator)
        self.assertNotIn("sys.argv", migrator)
        self.assertIn('"f1_0016"', migrator.split("def _verify_catalog", 1)[1])

    def test_worker_requires_claim_bound_manifest_and_live_release(self) -> None:
        worker = (
            ROOT / "src/platform_foundation/f1/features/material_rag/worker.py"
        ).read_text(encoding="utf-8")
        self.assertIn("verify_demo_unit_manifest_proof", worker)
        self.assertIn("AUTHORIZED_MATERIAL_RAG_SOURCE_SHA256", worker)
        self.assertIn("_released_sync", worker)
        self.assertIn("prepare(claim)", worker)
        self.assertIn("MATERIAL_RAG_MANIFEST_REQUIRED", worker)
        self.assertIn("MATERIAL_VERSION_NOT_INDEXABLE", worker)
        self.assertIn("delete_empty_scope_dataset", worker)
        self.assertIn("finalize_empty_scope_dataset_delete", worker)

    def test_compose_has_no_host_ports_and_only_proxy_has_egress(self) -> None:
        compose = (ROOT / "infra/f1/docker-compose.material-rag.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("\n    ports:", compose)
        self.assertIn("network_mode: none", compose)
        ragflow = compose.split("  material-rag-ragflow:", 1)[1].split(
            "\n  material-rag-migrator:", 1
        )[0]
        self.assertIn(
            "curl -sf http://127.0.0.1:80/api/v1/system/config >/dev/null",
            ragflow,
        )
        self.assertNotIn(
            "curl -sf http://127.0.0.1:9380/api/v1/system/config >/dev/null",
            ragflow,
        )
        self.assertNotIn("curl -sf http://127.0.0.1:80 >/dev/null", ragflow)
        self.assertIn("start_period: 90s", ragflow)
        self.assertIn("http://material-rag-ragflow:80", compose)
        self.assertNotIn("http://material-rag-ragflow:9380", compose)
        self.assertIn('command: ["--disable-datasync"]', ragflow)
        es = compose.split("  material-rag-es:", 1)[1].split(
            "\n  material-rag-objectstore:", 1
        )[0]
        self.assertIn("ES_JAVA_OPTS: -Xms512m -Xmx512m", es)
        self.assertIn("mem_limit: 1073741824", es)
        localctl = (ROOT / "scripts/localctl").read_text(encoding="utf-8")
        stack_start = localctl.split(
            'failure_reason="LOCAL_MATERIAL_RAG_STACK_START_FAILED"', 1
        )[0].rsplit("_material_rag_compose_stage(", 1)[1]
        self.assertIn('"material-rag-ocr"', stack_start)
        self.assertIn('"material-rag-clamd"', stack_start)
        self.assertNotIn('"material-rag-ragflow"', stack_start)
        self.assertIn('"material-rag-unit"', localctl)
        self.assertIn('"--abort-on-container-exit"', localctl)
        self.assertIn('"--exit-code-from"', localctl)
        self.assertNotIn('"--use-aliases"', localctl)
        self.assertIn('network_mode: "service:material-rag-ragflow"', compose)
        self.assertNotIn('network_mode: "service:material-rag-clamd"', compose)
        self.assertIn("F1_CLAMD_HOST: material-rag-clamd", compose)
        self.assertIn("TCPAddr 0.0.0.0", compose)
        self.assertIn("00000000:0CEE", compose)
        self.assertIn("exec /init", compose)
        self.assertIn('s|exec tail -f \\"/dev/null\\"|wait|', compose)
        self.assertIn(
            'failure_reason="LOCAL_MATERIAL_RAG_RAGFLOW_READY_FAILED"',
            localctl,
        )
        self.assertIn(
            'failure_reason="LOCAL_MATERIAL_RAG_OCR_STOP_FAILED"',
            localctl,
        )
        self.assertIn(
            'failure_reason="LOCAL_MATERIAL_RAG_RAGFLOW_STOP_FAILED"',
            localctl,
        )
        self.assertIn("LOCAL_MATERIAL_RAG_STACK_OOM_FAILED", localctl)
        self.assertIn("LOCAL_MATERIAL_RAG_FAILURE_EVIDENCE", localctl)
        self.assertIn("_emit_material_rag_failure_evidence", localctl)
        self.assertIn("LOCAL_MATERIAL_RAG_P3_PREVIEW_EVIDENCE", localctl)
        self.assertIn("_emit_material_rag_preview_evidence", localctl)
        self.assertIn("LOCAL_MATERIAL_RAG_VERIFIER_REASON", localctl)
        self.assertIn("_emit_material_rag_verifier_diagnostics", localctl)
        self.assertIn("_emit_material_rag_unreachable_verifier_diagnostics", localctl)
        self.assertIn("LOCAL_MATERIAL_RAG_VERIFIER_NOT_REACHED", localctl)
        self.assertIn("_contract_verifier_reason", localctl)
        remove_control = localctl.split(
            "def _remove_material_rag_control(", 1
        )[1].split("def _write_compose_env(", 1)[0]
        self.assertIn("MATERIAL_RAG_CONTROL_TMP_DIR", remove_control)
        self.assertIn("stat.S_ISSOCK(info.st_mode)", remove_control)
        self.assertIn('raise verification_error from error', localctl)
        self.assertIn("ark.cn-beijing.volces.com:443", (
            ROOT / "infra/f1/material-rag/ark_connect_proxy.py"
        ).read_text(encoding="utf-8"))
        self.assertIn("CLAMD_CONF_TCPSocket: \"3310\"", compose)
        self.assertIn("CLAMD_CONF_TCPAddr: \"0.0.0.0\"", compose)
        clamd = compose.split("  material-rag-clamd:", 1)[1].split(
            "\n  material-rag-mysql:", 1
        )[0]
        self.assertIn("echo PING | nc -w 2 127.0.0.1 3310 | grep -qx PONG", clamd)
        self.assertIn("00000000:0CEE", clamd)
        self.assertNotIn("hostname -I", clamd)
        self.assertNotIn("nc -z 127.0.0.1 3310", clamd)
        self.assertNotIn("/dev/tcp/127.0.0.1/3310", clamd)
        self.assertIn("start_period: 180s", clamd)
        self.assertIn("retries: 60", clamd)
        self.assertNotIn("retries: 3", clamd)
        self.assertIn('s|exec tail -f \\"/dev/null\\"|wait|', clamd)
        self.assertIn("aliases: [clamd]", clamd)
        self.assertNotIn("hostname: clamd", clamd)
        self.assertIn("F1_CLAMD_HOST: material-rag-clamd", compose)
        self.assertNotIn("material-rag-clamd:clamd", compose)
        self.assertIn("NO_PROXY: clamd,", compose)
        scanner_source = (
            ROOT / "src/platform_foundation/f1/features/p3/scanner.py"
        ).read_text(encoding="utf-8")
        self.assertIn("material-rag-clamd", scanner_source)
        self.assertIn("F1_CLAMD_HOST", scanner_source)
        self.assertIn("is_link_local", scanner_source)
        self.assertIn("LOOPBACK_SCANNER_HOSTS", scanner_source)
        self.assertIn("socket.AF_INET", scanner_source)
        self.assertIn("P3_SCANNER_DNS_FAILED", scanner_source)
        self.assertIn("P3_SCANNER_CONNECT_REFUSED", scanner_source)
        self.assertIn("P3_SCANNER_VERSION_RESET", scanner_source)
        self.assertIn("P3_SCANNER_STREAM_PIPE", scanner_source)
        self.assertIn("diagnose_scanner_preflight", scanner_source)
        self.assertNotIn("ECONNREFUSED, errno.ECONNRESET, errno.EPIPE", scanner_source)
        self.assertIn("F1_MATERIAL_RAG_CORE_MANIFEST_FILE", compose)
        self.assertIn("F1_MATERIAL_RAG_NATIVE_PLAN_FILE", compose)
        for digest in (
            "e64cb41465eaf3fc550dbc881c06d687275a8d2b6850f34c703c111a4a3cfc46",
            "ab242c22f92e73d519c5e5485df7027ad33812e96324943b6591171d0e41fc07",
            "12f20a5a1edf14eb18a77553740b8ab18e49dd7b2c95dcfc3ce22954ea206860",
            "973e6ac91e95489a6b8311a9ca61a1a734b6f3ef08f3b3b6d4713d4b04c4dd0e",
        ):
            self.assertIn(f"/demo/{digest}.pdf", compose)

        proxy = compose.split("  material-rag-egress-proxy:", 1)[1].split(
            "\n  material-rag-ocr:", 1
        )[0]
        self.assertIn('user: "65532:65532"', proxy)
        self.assertNotIn('user: "0:0"', proxy)
        self.assertIn(
            "networks: [material_rag_proxy, material_rag_egress]", proxy
        )
        authorizer = compose.split("  material-rag-authorizer:", 1)[1].split(
            "\n  material-rag-provider-provisioner:", 1
        )[0]
        self.assertIn('user: "65532:65532"', authorizer)
        self.assertNotIn('user: "0:0"', authorizer)
        self.assertNotIn("material_rag_egress", authorizer)
        egress_network_lines = [
            line
            for line in compose.splitlines()
            if line.startswith("    networks:") and "material_rag_egress" in line
        ]
        self.assertEqual(
            egress_network_lines,
            ["    networks: [material_rag_proxy, material_rag_egress]"],
        )
        dockerfile = (ROOT / "infra/f1/local.Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            "python:3.11-slim@sha256:"
            "90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff",
            dockerfile,
        )
        self.assertIn("--require-hashes", dockerfile)
        self.assertIn("--mount=type=cache,target=/root/.cache/pip", dockerfile)
        self.assertIn("--timeout 60", dockerfile)
        self.assertIn("--retries 5", dockerfile)
        self.assertNotIn("chmod 777", dockerfile)
        last_copy = dockerfile.find("COPY scripts/localctl /app/scripts/localctl")
        self.assertNotEqual(last_copy, -1)
        after_copy = dockerfile[last_copy:]
        self.assertIn(
            "chmod -R a+rX /app/src /app/migrations /app/infra /app/scripts",
            after_copy,
        )
        self.assertIn("chmod a+r /app/alembic.ini", after_copy)
        self.assertGreater(
            dockerfile.find(
                "chmod -R a+rX /app/src /app/migrations /app/infra /app/scripts"
            ),
            last_copy,
        )

    def test_scanner_ignores_link_local_and_prefers_private_ipv4(self) -> None:
        _ensure_p3_namespace()
        from platform_foundation.f1.features.p3 import scanner

        mixed = [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1", 3310, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.18.0.8", 3310)),
        ]
        with patch.object(scanner.socket, "getaddrinfo", return_value=mixed):
            self.assertEqual(
                scanner._resolve_target("material-rag-clamd", 3310, 5),
                "172.18.0.8",
            )
        public = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 3310)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.18.0.8", 3310)),
        ]
        with patch.object(scanner.socket, "getaddrinfo", return_value=public):
            with self.assertRaises(scanner.ScanFailure) as raised:
                scanner._resolve_target("material-rag-clamd", 3310, 5)
        self.assertEqual(raised.exception.code, "P3_SCANNER_TARGET_INVALID")
        loopback_and_remote = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 3310)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.18.0.8", 3310)),
        ]
        with patch.object(
            scanner.socket, "getaddrinfo", return_value=loopback_and_remote
        ):
            self.assertEqual(
                scanner._resolve_target("material-rag-clamd", 3310, 5),
                "172.18.0.8",
            )
            self.assertEqual(
                scanner._resolve_target("localhost", 3310, 5),
                "127.0.0.1",
            )
        loopback_only = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 3310)),
        ]
        with patch.object(scanner.socket, "getaddrinfo", return_value=loopback_only):
            with self.assertRaises(scanner.ScanFailure) as raised:
                scanner._resolve_target("material-rag-clamd", 3310, 5)
            self.assertEqual(raised.exception.code, "P3_SCANNER_TARGET_INVALID")
            self.assertEqual(
                scanner._resolve_target("localhost", 3310, 5),
                "127.0.0.1",
            )

    def test_scanner_os_errors_are_split_by_errno(self) -> None:
        import errno

        _ensure_p3_namespace()
        from platform_foundation.f1.features.p3 import scanner

        refused = OSError(errno.ECONNREFUSED, "refused")
        with self.assertRaises(scanner.ScanFailure) as raised:
            scanner._raise_scanner_os_error(refused, phase="connect")
        self.assertEqual(raised.exception.code, "P3_SCANNER_CONNECT_REFUSED")
        reset = OSError(errno.ECONNRESET, "reset")
        with self.assertRaises(scanner.ScanFailure) as raised:
            scanner._raise_scanner_os_error(reset, phase="version")
        self.assertEqual(raised.exception.code, "P3_SCANNER_VERSION_RESET")
        pipe = OSError(errno.EPIPE, "pipe")
        with self.assertRaises(scanner.ScanFailure) as raised:
            scanner._raise_scanner_os_error(pipe, phase="stream")
        self.assertEqual(raised.exception.code, "P3_SCANNER_STREAM_PIPE")
        dns = socket.gaierror(socket.EAI_NONAME, "name")
        with self.assertRaises(scanner.ScanFailure) as raised:
            scanner._raise_scanner_os_error(dns, phase="connect")
        self.assertEqual(raised.exception.code, "P3_SCANNER_DNS_FAILED")
        with patch.object(
            scanner.socket,
            "getaddrinfo",
            side_effect=socket.gaierror(socket.EAI_NONAME, "name"),
        ):
            with self.assertRaises(scanner.ScanFailure) as raised:
                scanner._resolve_target("clamd", 3310, 5)
        self.assertEqual(raised.exception.code, "P3_SCANNER_DNS_FAILED")
        with patch.dict(os.environ, {"F1_CLAMD_SOCKET": "/tmp/other.sock"}):
            with self.assertRaises(scanner.ScanFailure) as raised:
                scanner.scanner_version(timeout_seconds=5)
        self.assertEqual(raised.exception.code, "P3_SCANNER_TARGET_INVALID")
        with patch.object(
            scanner.socket,
            "getaddrinfo",
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.18.0.8", 3310)),
            ],
        ):
            with patch.object(
                scanner.socket,
                "create_connection",
                side_effect=OSError(errno.ECONNREFUSED, "refused"),
            ):
                report = scanner.diagnose_scanner_preflight(timeout_seconds=5)
        self.assertEqual(report["SCAN_CODE"], "P3_SCANNER_CONNECT_REFUSED")
        self.assertEqual(report["CONNECT_ERRNO"], "ECONNREFUSED")
        self.assertEqual(report["ADDR_CLASS"], "PRIVATE_IPV4")
        self.assertNotIn("172.18.0.8", "".join(report.values()))

        captured: list[dict[str, object]] = []

        def sink(payload: object) -> None:
            captured.append(dict(payload))  # type: ignore[arg-type]

        silent_out = io.StringIO()
        silent_err = io.StringIO()
        with contextlib.redirect_stdout(silent_out), contextlib.redirect_stderr(
            silent_err
        ):
            with self.assertRaises(scanner.ScanFailure) as raised:
                scanner.parse_clamd_version(b"")
        self.assertEqual(raised.exception.code, "P3_SCAN_PROTOCOL_ERROR")
        self.assertEqual(silent_out.getvalue(), "")
        self.assertEqual(silent_err.getvalue(), "")

        with scanner.scanner_evidence_sink(sink):
            with self.assertRaises(scanner.ScanFailure) as raised:
                scanner.parse_clamd_version(b"")
            self.assertEqual(raised.exception.code, "P3_SCAN_PROTOCOL_ERROR")
            with self.assertRaises(scanner.ScanFailure) as raised:
                scanner.parse_clamd_version(b"x" * (scanner.MAX_RESPONSE_BYTES + 1))
            self.assertEqual(raised.exception.code, "P3_SCAN_PROTOCOL_ERROR")
            with self.assertRaises(scanner.ScanFailure) as raised:
                scanner.parse_clamd_version(b"not-a-clam-version\n")
            self.assertEqual(raised.exception.code, "P3_SCAN_PROTOCOL_ERROR")
            with self.assertRaises(scanner.ScanFailure) as raised:
                scanner.parse_clamd_response(b"")
            self.assertEqual(raised.exception.code, "P3_SCAN_PROTOCOL_ERROR")
            with self.assertRaises(scanner.ScanFailure) as raised:
                scanner.parse_clamd_response(b"x" * (scanner.MAX_RESPONSE_BYTES + 1))
            self.assertEqual(raised.exception.code, "P3_SCAN_PROTOCOL_ERROR")
            with self.assertRaises(scanner.ScanFailure) as raised:
                scanner.parse_clamd_response(b"stream: unexpected\n")
            self.assertEqual(raised.exception.code, "P3_SCAN_PROTOCOL_ERROR")
            with self.assertRaises(scanner.ScanFailure) as raised:
                scanner.parse_clamd_response(b"stream: engine ERROR\n")
            self.assertEqual(raised.exception.code, "P3_SCAN_ENGINE_ERROR")
            class _OversizeSocket:
                def recv(self, size: int) -> bytes:
                    return b"a" * size

            with self.assertRaises(scanner.ScanFailure) as raised:
                scanner._receive_response(_OversizeSocket())  # type: ignore[arg-type]
            self.assertEqual(raised.exception.code, "P3_SCAN_PROTOCOL_ERROR")

        expected = (
            ("VERSION", "PARSE", "EMPTY", "P3_SCAN_PROTOCOL_ERROR"),
            ("VERSION", "PARSE", "OVERSIZE", "P3_SCAN_PROTOCOL_ERROR"),
            ("VERSION", "PARSE", "FORMAT_MISMATCH", "P3_SCAN_PROTOCOL_ERROR"),
            ("INSTREAM", "PARSE", "EMPTY", "P3_SCAN_PROTOCOL_ERROR"),
            ("INSTREAM", "PARSE", "OVERSIZE", "P3_SCAN_PROTOCOL_ERROR"),
            ("INSTREAM", "PARSE", "FORMAT_MISMATCH", "P3_SCAN_PROTOCOL_ERROR"),
            ("INSTREAM", "PARSE", "ENGINE_ERROR", "P3_SCAN_ENGINE_ERROR"),
            ("VERSION", "RECV", "OVERSIZE", "P3_SCAN_PROTOCOL_ERROR"),
        )
        self.assertEqual(len(captured), len(expected))
        for payload, (operation, phase, response_class, scan_code) in zip(
            captured, expected
        ):
            self.assertEqual(
                payload,
                {
                    "attempt_count": 1,
                    "operation": operation,
                    "phase": phase,
                    "response_class": response_class,
                    "scan_code": scan_code,
                },
            )
            dumped = json.dumps(payload)
            self.assertNotIn("ClamAV", dumped)
            self.assertNotIn("stream:", dumped)

        localctl = _load_localctl()
        valid = {
            "attempt_count": 2,
            "operation": "VERSION",
            "phase": "PARSE",
            "response_class": "EMPTY",
            "scan_code": "P3_SCAN_PROTOCOL_ERROR",
        }
        valid_line = (
            "LOCAL_MATERIAL_RAG_SCANNER_EVIDENCE "
            + json.dumps(valid, separators=(",", ":"), sort_keys=True)
        )
        self.assertLessEqual(len(valid_line.encode("utf-8")), 1024)
        secret = "leak-token-must-not-print"
        address = "172.18.0.8"
        version_body = "ClamAV 1.4.6/27632/Wed Aug 13"
        stream_body = "stream: OK"
        url = "http://evil.example/scan"
        header = "Authorization: Bearer secret"
        path = "/run/material-rag-clamd/clamd.sock"
        padding = "PAD" * 3000
        stderr_text = (
            padding
            + "\n"
            + valid_line
            + "\n"
            + secret
            + "\n"
            + address
            + "\n"
            + version_body
            + "\n"
        )
        stdout_text = "LOCAL_MATERIAL_RAG_P3_SCAN_PROTOCOL_FAILED\n"
        self.assertGreater(len(stderr_text.encode("utf-8")), 8192)
        reprinted = io.StringIO()
        with contextlib.redirect_stderr(reprinted):
            reason = localctl._emit_material_rag_verifier_diagnostics(
                stdout_text,
                stderr_text,
                fallback_reason="LOCAL_COMMAND_FAILED",
            )
        printed = reprinted.getvalue()
        self.assertEqual(reason, "LOCAL_MATERIAL_RAG_P3_SCAN_PROTOCOL_FAILED")
        self.assertIn(
            "LOCAL_MATERIAL_RAG_VERIFIER_REASON "
            "LOCAL_MATERIAL_RAG_P3_SCAN_PROTOCOL_FAILED",
            printed.splitlines(),
        )
        self.assertIn(valid_line, printed.splitlines())
        for bait in (secret, address, version_body, stream_body, url, header, path, "PAD"):
            self.assertNotIn(bait, printed)

        other = dict(valid)
        other["attempt_count"] = 4
        other_line = (
            "LOCAL_MATERIAL_RAG_SCANNER_EVIDENCE "
            + json.dumps(other, separators=(",", ":"), sort_keys=True)
        )
        duplicate_err = io.StringIO()
        with contextlib.redirect_stderr(duplicate_err):
            duplicate_reason = localctl._emit_material_rag_verifier_diagnostics(
                "LOCAL_MATERIAL_RAG_P3_SCAN_PROTOCOL_FAILED\n",
                valid_line + "\n" + other_line + "\n",
                fallback_reason="LOCAL_COMMAND_FAILED",
            )
        malformed = (
            "LOCAL_MATERIAL_RAG_SCANNER_EVIDENCE {"
            + secret
            + ","
            + address
            + ","
            + version_body
            + ","
            + stream_body
            + ","
            + url
            + ","
            + header
            + ","
            + path
            + "}"
        )
        malformed_err = io.StringIO()
        with contextlib.redirect_stderr(malformed_err):
            malformed_reason = localctl._emit_material_rag_verifier_diagnostics(
                "LOCAL_MATERIAL_RAG_P3_SCAN_PROTOCOL_FAILED\n",
                malformed + "\n",
                fallback_reason="LOCAL_COMMAND_FAILED",
            )
        self.assertEqual(duplicate_reason, "LOCAL_MATERIAL_RAG_P3_SCAN_PROTOCOL_FAILED")
        self.assertEqual(malformed_reason, "LOCAL_MATERIAL_RAG_P3_SCAN_PROTOCOL_FAILED")
        self.assertIn(
            "LOCAL_MATERIAL_RAG_SCANNER_EVIDENCE_DEGRADED DUPLICATE",
            duplicate_err.getvalue().splitlines(),
        )
        self.assertIn(
            "LOCAL_MATERIAL_RAG_SCANNER_EVIDENCE_DEGRADED MALFORMED",
            malformed_err.getvalue().splitlines(),
        )
        self.assertNotIn(valid_line, duplicate_err.getvalue())
        for bait in (secret, address, version_body, stream_body, url, header, path):
            self.assertNotIn(bait, malformed_err.getvalue())
            self.assertNotIn(bait, duplicate_err.getvalue())

        verify = _load_material_rag_verify()
        self.assertEqual(
            scanner.SCANNER_EVIDENCE_CODE_TO_REASON,
            verify._SCANNER_EVIDENCE_CODE_TO_REASON,
        )
        self.assertEqual(
            scanner.SCANNER_EVIDENCE_CODE_TO_REASON,
            localctl._MATERIAL_RAG_SCANNER_EVIDENCE_CODE_TO_REASON,
        )
        self.assertEqual(
            frozenset(scanner.SCANNER_EVIDENCE_CODE_TO_REASON),
            localctl._MATERIAL_RAG_SCANNER_EVIDENCE_SCAN_CODES,
        )
        self.assertEqual(
            frozenset(scanner.SCANNER_EVIDENCE_CODE_TO_REASON.values()),
            localctl._MATERIAL_RAG_SCANNER_EVIDENCE_REASONS,
        )
        self.assertEqual(
            frozenset(scanner.SCANNER_EVIDENCE_CODE_TO_REASON.values()),
            verify._P3_SCAN_EVIDENCE_REASONS,
        )
        for status_reason in (
            "LOCAL_MATERIAL_RAG_P3_SCAN_INFECTED_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_INCOMPLETE_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_ERROR_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_UNAVAILABLE_FAILED",
        ):
            self.assertNotIn(status_reason, verify._P3_SCAN_EVIDENCE_REASONS)
            self.assertNotIn(
                status_reason, localctl._MATERIAL_RAG_SCANNER_EVIDENCE_REASONS
            )
        for status_code in (
            "P3_SCAN_SIZE_INVALID",
            "P3_SCAN_TIMEOUT_INVALID",
            "P3_SCANNER_REFUSED",
            "P3_SOURCE_IDENTITY_MISMATCH",
            "P3_SOURCE_READ_FAILED",
        ):
            self.assertNotIn(status_code, scanner.SCANNER_EVIDENCE_CODE_TO_REASON)

        buffer = verify._ScannerEvidenceBuffer()
        with scanner.scanner_evidence_sink(buffer):
            with self.assertRaises(scanner.ScanFailure) as raised:
                scanner.parse_clamd_version(b"")
            self.assertEqual(raised.exception.code, "P3_SCAN_PROTOCOL_ERROR")
            self.assertEqual(buffer._group[-1]["operation"], "VERSION")
            parsed = scanner.parse_clamd_version(b"ClamAV 1.4.6/1")
            self.assertEqual(parsed.engine_version, "1.4.6")
            self.assertEqual(buffer._group, [])
            with self.assertRaises(scanner.ScanFailure) as raised:
                scanner.parse_clamd_response(b"stream: unexpected\n")
            self.assertEqual(raised.exception.code, "P3_SCAN_PROTOCOL_ERROR")
            self.assertEqual(buffer._group[-1]["operation"], "INSTREAM")
        recovered = io.StringIO()
        with contextlib.redirect_stderr(recovered):
            buffer.emit_for_reason("LOCAL_MATERIAL_RAG_P3_SCAN_PROTOCOL_FAILED")
        recovered_lines = [
            line
            for line in recovered.getvalue().splitlines()
            if line.startswith("LOCAL_MATERIAL_RAG_SCANNER_EVIDENCE ")
        ]
        self.assertEqual(len(recovered_lines), 1)
        recovered_payload = json.loads(
            recovered_lines[0][len("LOCAL_MATERIAL_RAG_SCANNER_EVIDENCE ") :]
        )
        self.assertEqual(recovered_payload["operation"], "INSTREAM")
        self.assertEqual(recovered_payload["phase"], "PARSE")
        self.assertEqual(recovered_payload["response_class"], "FORMAT_MISMATCH")
        self.assertEqual(recovered_payload["scan_code"], "P3_SCAN_PROTOCOL_ERROR")
        self.assertEqual(recovered_payload["attempt_count"], 1)
        self.assertNotEqual(recovered_payload["operation"], "VERSION")
        self.assertNotIn("VERSION", recovered.getvalue())

        mismatch = {
            "attempt_count": 1,
            "operation": "INSTREAM",
            "phase": "PARSE",
            "response_class": "ENGINE_ERROR",
            "scan_code": "P3_SCAN_ENGINE_ERROR",
        }
        mismatch_line = (
            "LOCAL_MATERIAL_RAG_SCANNER_EVIDENCE "
            + json.dumps(mismatch, separators=(",", ":"), sort_keys=True)
        )
        mismatch_err = io.StringIO()
        with contextlib.redirect_stderr(mismatch_err):
            mismatch_reason = localctl._emit_material_rag_verifier_diagnostics(
                "LOCAL_MATERIAL_RAG_P3_SCAN_PROTOCOL_FAILED\n",
                mismatch_line + "\n",
                fallback_reason="LOCAL_COMMAND_FAILED",
            )
        mismatch_printed = mismatch_err.getvalue()
        self.assertEqual(
            mismatch_reason, "LOCAL_MATERIAL_RAG_P3_SCAN_PROTOCOL_FAILED"
        )
        self.assertIn(
            "LOCAL_MATERIAL_RAG_SCANNER_EVIDENCE_DEGRADED MALFORMED",
            mismatch_printed.splitlines(),
        )
        self.assertNotIn(mismatch_line, mismatch_printed.splitlines())
        for bait in (secret, address, version_body, stream_body, url, header, path):
            self.assertNotIn(bait, mismatch_printed)

        stale_version = valid_line
        for status_reason in (
            "LOCAL_MATERIAL_RAG_P3_SCAN_INFECTED_FAILED",
            "LOCAL_MATERIAL_RAG_P3_SCAN_INCOMPLETE_FAILED",
        ):
            status_err = io.StringIO()
            with contextlib.redirect_stderr(status_err):
                status_emitted = localctl._emit_material_rag_verifier_diagnostics(
                    status_reason + "\n",
                    stale_version + "\n",
                    fallback_reason="LOCAL_COMMAND_FAILED",
                )
            status_printed = status_err.getvalue()
            self.assertEqual(status_emitted, status_reason)
            self.assertNotIn(stale_version, status_printed.splitlines())
            self.assertFalse(
                any(
                    line.startswith("LOCAL_MATERIAL_RAG_SCANNER_EVIDENCE")
                    for line in status_printed.splitlines()
                )
            )
            for bait in (secret, address, version_body, stream_body, url, header, path):
                self.assertNotIn(bait, status_printed)

        original_import = __import__

        def _fail_scanner_import(
            name: str,
            globals: object | None = None,
            locals: object | None = None,
            fromlist: tuple[str, ...] = (),
            level: int = 0,
        ) -> object:
            if name == "platform_foundation.f1.features.p3.scanner":
                raise ImportError("simulated-scanner-import")
            return original_import(name, globals, locals, fromlist, level)

        import_out = io.StringIO()
        import_err = io.StringIO()
        with patch("builtins.__import__", side_effect=_fail_scanner_import):
            with contextlib.redirect_stdout(import_out), contextlib.redirect_stderr(
                import_err
            ):
                import_rc = verify.main()
        self.assertEqual(import_rc, 1)
        self.assertEqual(import_out.getvalue(), "")
        self.assertEqual(
            import_err.getvalue(),
            "LOCAL_MATERIAL_RAG_INTERNAL_ERROR\n"
            "LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE "
            '{"db_token":"NONE","error_class":"IMPORT_ERROR",'
            '"operation":"UNKNOWN","phase":"IMPORT_SCANNER",'
            '"primary_preserved":true,"sqlstate":"NONE"}\n',
        )
        self.assertNotIn("Traceback", import_err.getvalue())
        self.assertNotIn("ImportError", import_err.getvalue())
        self.assertNotIn("simulated-scanner-import", import_err.getvalue())
        self.assertNotIn("simulated-scanner-import", import_out.getvalue())

        reach_state = {"project_id": "10000000-0000-4000-8000-000000000001"}
        project, project_id, _database, _probe = localctl._material_rag_identity(
            reach_state
        )

        def _inspect_payload(service: str, status: str) -> str:
            return json.dumps(
                [
                    {
                        "Config": {
                            "Labels": {
                                "com.docker.compose.project": project,
                                "com.docker.compose.service": service,
                                "io.anhuan.project-id": project_id,
                                "io.anhuan.parent-project-id": reach_state[
                                    "project_id"
                                ],
                                "io.anhuan.scope": "material-rag-verification",
                            }
                        },
                        "State": {"Status": status},
                    }
                ],
                separators=(",", ":"),
                sort_keys=True,
            )

        def _docker_result(returncode: int, stdout: str) -> MagicMock:
            result = MagicMock()
            result.returncode = returncode
            result.stdout = stdout
            return result

        def _partial_inspect_run(arguments: list[str], **_kwargs: object) -> MagicMock:
            if "ps" in arguments and any(
                item.endswith("=material-rag-verifier") for item in arguments
            ):
                return _docker_result(1, "")
            if arguments[1:3] == ["inspect", "cid-clamd"]:
                return _docker_result(
                    0, _inspect_payload("material-rag-clamd", "running")
                )
            if arguments[1:3] == ["inspect", "cid-partial"]:
                raise localctl.LocalError("LOCAL_COMMAND_FAILED")
            raise AssertionError(arguments)

        with patch.object(localctl, "_docker", return_value="docker"), patch.object(
            localctl, "_resource_ids", return_value=["cid-clamd", "cid-partial"]
        ), patch.object(localctl, "_run", side_effect=_partial_inspect_run):
            self.assertIsNone(
                localctl._material_rag_complete_container_snapshot(reach_state)
            )
            self.assertEqual(
                localctl._material_rag_verifier_reach(reach_state), "unknown"
            )
            partial_err = io.StringIO()
            with contextlib.redirect_stderr(partial_err):
                localctl._emit_material_rag_scanner_reach_evidence(reach_state)
        partial_printed = partial_err.getvalue().splitlines()
        self.assertIn(
            "LOCAL_MATERIAL_RAG_SCANNER_EVIDENCE_DEGRADED MISSING",
            partial_printed,
        )
        self.assertNotIn(
            "LOCAL_MATERIAL_RAG_SCANNER_EVIDENCE_NOT_REACHED",
            partial_printed,
        )

        def _reached_run(arguments: list[str], **_kwargs: object) -> MagicMock:
            if "ps" in arguments and any(
                item.endswith("=material-rag-verifier") for item in arguments
            ):
                return _docker_result(0, "cid-verifier\n")
            if arguments[1:3] == ["inspect", "cid-verifier"]:
                return _docker_result(
                    0, _inspect_payload("material-rag-verifier", "exited")
                )
            raise AssertionError(arguments)

        with patch.object(localctl, "_docker", return_value="docker"), patch.object(
            localctl, "_run", side_effect=_reached_run
        ):
            self.assertEqual(
                localctl._material_rag_verifier_reach(reach_state), "reached"
            )
            reached_err = io.StringIO()
            with contextlib.redirect_stderr(reached_err):
                localctl._emit_material_rag_scanner_reach_evidence(reach_state)
        reached_printed = reached_err.getvalue().splitlines()
        self.assertIn(
            "LOCAL_MATERIAL_RAG_SCANNER_EVIDENCE_DEGRADED MISSING",
            reached_printed,
        )
        self.assertNotIn(
            "LOCAL_MATERIAL_RAG_SCANNER_EVIDENCE_NOT_REACHED",
            reached_printed,
        )

        dockerfile = (ROOT / "infra/f1/local.Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            "python:3.11-slim@sha256:"
            "90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff",
            dockerfile,
        )
        self.assertIn("--require-hashes", dockerfile)
        self.assertIn("--mount=type=cache,target=/root/.cache/pip", dockerfile)
        self.assertIn("--disable-pip-version-check", dockerfile)
        self.assertIn("--timeout 60", dockerfile)
        self.assertIn("--retries 5", dockerfile)
        self.assertNotIn("--index-url", dockerfile)
        self.assertNotIn("trusted-host", dockerfile)
        self.assertNotIn("PIP_TRUSTED_HOST", dockerfile)

        build_secret = "leak-token-must-not-print"
        build_url = "https://files.pythonhosted.org/packages/cryptography.whl"
        build_wheel = "cryptography-46.0.5-cp311-abi3-manylinux.whl"
        build_path = "/root/.cache/pip/http-v2/deadbeef"
        build_header = "Authorization: Bearer secret"
        build_stderr = (
            "ERROR: Could not install packages due to an OSError: "
            "HTTPSConnectionPool(host='files.pythonhosted.org', port=443): "
            "Read timed out.\n"
            + build_secret
            + "\n"
            + build_url
            + "\n"
            + build_wheel
            + "\n"
            + build_path
            + "\n"
            + build_header
            + "\n"
        )
        build_result = MagicMock()
        build_result.returncode = 1
        build_result.stdout = ""
        build_result.stderr = build_stderr
        build_err = io.StringIO()
        with patch.object(
            localctl, "_material_rag_compose", return_value=build_result
        ):
            with contextlib.redirect_stderr(build_err):
                with self.assertRaises(localctl.LocalError) as raised:
                    localctl._material_rag_compose_stage(
                        {"project_id": "10000000-0000-4000-8000-000000000001"},
                        ROOT / "infra/f1/docker-compose.material-rag.yml",
                        "build",
                        "material-rag-migrator",
                        timeout=1800,
                        failure_reason="LOCAL_MATERIAL_RAG_BUILD_FAILED",
                    )
        self.assertEqual(str(raised.exception), "LOCAL_MATERIAL_RAG_BUILD_FAILED")
        build_printed = build_err.getvalue()
        build_lines = [
            line
            for line in build_printed.splitlines()
            if line.startswith("LOCAL_MATERIAL_RAG_BUILD_EVIDENCE ")
        ]
        self.assertEqual(len(build_lines), 1)
        build_payload = json.loads(
            build_lines[0][len("LOCAL_MATERIAL_RAG_BUILD_EVIDENCE ") :]
        )
        self.assertEqual(
            set(build_payload),
            {"detail_class", "exit_class", "exit_code", "phase"},
        )
        self.assertEqual(build_payload["exit_class"], "TIMEOUT")
        self.assertEqual(build_payload["phase"], "DEPENDENCY_INSTALL")
        self.assertEqual(build_payload["detail_class"], "NETWORK_TIMEOUT")
        self.assertEqual(build_payload["exit_code"], 1)
        for bait in (
            build_secret,
            build_url,
            build_wheel,
            build_path,
            build_header,
            "files.pythonhosted.org",
            "Read timed out",
            "cryptography",
        ):
            self.assertNotIn(bait, build_printed)

        expected_internal_phases = frozenset(
            {
                "ASSERT_RUNTIME",
                "DISPOSE_ENGINES",
                "FINAL_AUDIT",
                "IMPORT_SCANNER",
                "LOAD_FIXTURES",
                "PJ_CONTEXT_GUARDS",
                "PJ_DELETE",
                "PJ_FINAL_AUDIT",
                "PJ_IMPORT_INIT",
                "PJ_INDEX_REPLAY",
                "PJ_PRIMARY_ATTEST",
                "PJ_PRIMARY_INDEX",
                "PJ_REBUILD",
                "PJ_SCOPED_RETRIEVAL",
                "PJ_SCOPE_ISOLATION",
                "PJ_SYNTHETIC_INDEX",
                "PROVIDER_ATTESTATION",
                "SEED_DATABASE",
                "SETUP_UPLOAD",
                "STORAGE_ACTIVATE",
                "STORAGE_CLEANUP",
                "UNKNOWN",
            }
        )
        expected_internal_error_classes = frozenset(
            {
                "ASSERTION_ERROR",
                "ATTRIBUTE_ERROR",
                "CANCELLED_ERROR",
                "DB_DATA",
                "DB_INTEGRITY",
                "DB_INTERFACE",
                "DB_INTERNAL",
                "DB_INVALID_REQUEST",
                "DB_MISSING_GREENLET",
                "DB_NOT_SUPPORTED",
                "DB_OPERATIONAL",
                "DB_OTHER",
                "DB_PENDING_ROLLBACK",
                "DB_PROGRAMMING",
                "DB_STATEMENT",
                "EXCEPTION_GROUP",
                "IMPORT_ERROR",
                "INDEX_ERROR",
                "KEY_ERROR",
                "OS_ERROR",
                "OTHER",
                "RUNTIME_ERROR",
                "TIMEOUT",
                "TYPE_ERROR",
                "UNKNOWN",
                "VALUE_ERROR",
            }
        )
        expected_internal_db_tokens = frozenset(
            {
                "MATERIAL_RAG_BINDING_IDENTITY_IMMUTABLE",
                "MATERIAL_RAG_DOWNGRADE_DATA_PRESENT",
                "MATERIAL_RAG_JOB_CLAIM_INVALID",
                "MATERIAL_RAG_JOB_IDENTITY_IMMUTABLE",
                "MATERIAL_RAG_JOB_OUTCOME_INVALID",
                "MATERIAL_RAG_JOB_SOURCE_IDENTITY_INVALID",
                "MATERIAL_RAG_JOB_SOURCE_NOT_RELEASED",
                "MATERIAL_RAG_JOB_TRANSITION_INVALID",
                "MATERIAL_RAG_UNIT_IMMUTABLE",
                "MATERIAL_RAG_UNIT_SOURCE_NOT_RELEASED",
                "QA_CLAIM_INVALID",
                "QA_COMPLETE_INVALID",
                "QA_OUTCOME_STATE_INVALID",
                "TEXT_NUL",
            }
        )
        expected_internal_operations = frozenset(
            {
                "CANDIDATE_VERIFY",
                "CLAIM_JOB",
                "CLAIMED_SESSION",
                "CONTEXT_DERIVE",
                "CRYPTO_PROBE",
                "DB_SNAPSHOT_EXIT",
                "DB_SNAPSHOT_LOAD",
                "DB_SNAPSHOT_OPEN",
                "EGRESS_AUDIT",
                "ENQUEUE_JOB",
                "FINAL_RESIDUE",
                "IMPORTS",
                "JOB_ROW",
                "LOAD_UNITS",
                "MUTATION_FENCE",
                "PERSIST_UNITS",
                "PROCESS_DEMO_JOB",
                "QA_COMPLETE",
                "QA_RESERVE",
                "REMOTE_SNAPSHOT",
                "RETRIEVAL",
                "RLS_CHECK",
                "SCOPE_LOCK",
                "UNIT_COUNTS",
                "UNKNOWN",
            }
        )
        self.assertEqual(verify._INTERNAL_EVIDENCE_PHASES, expected_internal_phases)
        self.assertEqual(
            localctl._MATERIAL_RAG_INTERNAL_EVIDENCE_PHASES, expected_internal_phases
        )
        self.assertEqual(
            verify._INTERNAL_EVIDENCE_ERROR_CLASSES, expected_internal_error_classes
        )
        self.assertEqual(
            localctl._MATERIAL_RAG_INTERNAL_EVIDENCE_ERROR_CLASSES,
            expected_internal_error_classes,
        )
        self.assertEqual(
            verify._INTERNAL_EVIDENCE_OPERATIONS, expected_internal_operations
        )
        self.assertEqual(
            localctl._MATERIAL_RAG_INTERNAL_EVIDENCE_OPERATIONS,
            expected_internal_operations,
        )
        self.assertEqual(
            verify._INTERNAL_EVIDENCE_DB_TOKENS, expected_internal_db_tokens
        )
        self.assertEqual(
            localctl._MATERIAL_RAG_INTERNAL_EVIDENCE_DB_TOKENS,
            expected_internal_db_tokens,
        )
        self.assertEqual(
            localctl._MATERIAL_RAG_INTERNAL_EVIDENCE_KEYS,
            frozenset(
                {
                    "db_token",
                    "error_class",
                    "operation",
                    "phase",
                    "primary_preserved",
                    "sqlstate",
                }
            ),
        )
        self.assertNotIn("PROCESS_JOBS", verify._INTERNAL_EVIDENCE_PHASES)
        verify_source = (
            ROOT / "infra/f1/local_material_rag_verify.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn('_internal_phase("PROCESS_JOBS")', verify_source)
        for process_jobs_phase in (
            "PJ_IMPORT_INIT",
            "PJ_PRIMARY_INDEX",
            "PJ_PRIMARY_ATTEST",
            "PJ_INDEX_REPLAY",
            "PJ_CONTEXT_GUARDS",
            "PJ_SYNTHETIC_INDEX",
            "PJ_SCOPE_ISOLATION",
            "PJ_SCOPED_RETRIEVAL",
            "PJ_REBUILD",
            "PJ_DELETE",
            "PJ_FINAL_AUDIT",
        ):
            self.assertIn(
                f'_enter_internal_phase("{process_jobs_phase}")',
                verify_source,
            )
        for internal_operation in (
            "CANDIDATE_VERIFY",
            "CONTEXT_DERIVE",
            "CRYPTO_PROBE",
            "DB_SNAPSHOT_EXIT",
            "DB_SNAPSHOT_LOAD",
            "DB_SNAPSHOT_OPEN",
            "EGRESS_AUDIT",
            "ENQUEUE_JOB",
            "FINAL_RESIDUE",
            "IMPORTS",
            "JOB_ROW",
            "LOAD_UNITS",
            "CLAIM_JOB",
            "CLAIMED_SESSION",
            "MUTATION_FENCE",
            "PERSIST_UNITS",
            "PROCESS_DEMO_JOB",
            "QA_COMPLETE",
            "QA_RESERVE",
            "REMOTE_SNAPSHOT",
            "RETRIEVAL",
            "RLS_CHECK",
            "SCOPE_LOCK",
            "UNIT_COUNTS",
        ):
            self.assertIn(
                f'_enter_internal_operation("{internal_operation}")',
                verify_source,
            )
        run_async_source = verify_source.split("async def _run_async")[1].split(
            "def _assert_runtime_authorization"
        )[0]
        self.assertIn("_INTERNAL_EVIDENCE.record(", run_async_source)
        self.assertIn("primary = error", run_async_source)
        internal_payload = {
            "db_token": "NONE",
            "error_class": "IMPORT_ERROR",
            "operation": "UNKNOWN",
            "phase": "IMPORT_SCANNER",
            "primary_preserved": True,
            "sqlstate": "NONE",
        }
        internal_line = (
            "LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE "
            + json.dumps(internal_payload, separators=(",", ":"), sort_keys=True)
        )
        internal_secret = "internal-leak-token-must-not-print"
        internal_url = "https://ark.cn-beijing.volces.com/api/plan/v3"
        internal_path = "/app/infra/f1/local_material_rag_verify.py"
        internal_trace = 'Traceback (most recent call last): File "<stdin>"'
        internal_repr = "TypeError('boom')"
        padded_internal = (
            ("PAD" * 3000)
            + "\n"
            + internal_line
            + "\n"
            + internal_secret
            + "\n"
            + internal_url
            + "\n"
            + internal_path
            + "\n"
            + internal_trace
            + "\n"
            + internal_repr
            + "\n"
        )
        self.assertGreater(len(padded_internal.encode("utf-8")), 8192)
        internal_err = io.StringIO()
        with contextlib.redirect_stderr(internal_err):
            internal_reason = localctl._emit_material_rag_verifier_diagnostics(
                "LOCAL_MATERIAL_RAG_INTERNAL_ERROR\n",
                padded_internal,
                fallback_reason="LOCAL_COMMAND_FAILED",
            )
        internal_printed = internal_err.getvalue()
        self.assertEqual(internal_reason, "LOCAL_MATERIAL_RAG_INTERNAL_ERROR")
        self.assertIn(internal_line, internal_printed.splitlines())
        for bait in (
            internal_secret,
            internal_url,
            internal_path,
            internal_trace,
            internal_repr,
            "PAD",
        ):
            self.assertNotIn(bait, internal_printed)

        other_internal = dict(internal_payload)
        other_internal["phase"] = "UNKNOWN"
        other_internal_line = (
            "LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE "
            + json.dumps(other_internal, separators=(",", ":"), sort_keys=True)
        )
        duplicate_internal = io.StringIO()
        with contextlib.redirect_stderr(duplicate_internal):
            duplicate_internal_reason = localctl._emit_material_rag_verifier_diagnostics(
                "LOCAL_MATERIAL_RAG_INTERNAL_ERROR\n",
                internal_line + "\n" + other_internal_line + "\n",
                fallback_reason="LOCAL_COMMAND_FAILED",
            )
        malformed_internal = (
            "LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE {"
            + internal_secret
            + ","
            + internal_url
            + ","
            + internal_path
            + "}"
        )
        malformed_internal_err = io.StringIO()
        with contextlib.redirect_stderr(malformed_internal_err):
            malformed_internal_reason = localctl._emit_material_rag_verifier_diagnostics(
                "LOCAL_MATERIAL_RAG_INTERNAL_ERROR\n",
                malformed_internal + "\n",
                fallback_reason="LOCAL_COMMAND_FAILED",
            )
        self.assertEqual(
            duplicate_internal_reason, "LOCAL_MATERIAL_RAG_INTERNAL_ERROR"
        )
        self.assertEqual(
            malformed_internal_reason, "LOCAL_MATERIAL_RAG_INTERNAL_ERROR"
        )
        self.assertIn(
            "LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE_DEGRADED DUPLICATE",
            duplicate_internal.getvalue().splitlines(),
        )
        self.assertIn(
            "LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE_DEGRADED MALFORMED",
            malformed_internal_err.getvalue().splitlines(),
        )
        self.assertNotIn(internal_line, duplicate_internal.getvalue())
        for bait in (internal_secret, internal_url, internal_path):
            self.assertNotIn(bait, malformed_internal_err.getvalue())
            self.assertNotIn(bait, duplicate_internal.getvalue())

        oversized_internal = "LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE " + ("A" * 1100)
        self.assertGreater(len(oversized_internal.encode("utf-8")), 1024)
        oversized_internal_err = io.StringIO()
        with contextlib.redirect_stderr(oversized_internal_err):
            oversized_internal_reason = localctl._emit_material_rag_verifier_diagnostics(
                "LOCAL_MATERIAL_RAG_INTERNAL_ERROR\n",
                oversized_internal + "\n" + internal_line + "\n",
                fallback_reason="LOCAL_COMMAND_FAILED",
            )
        oversized_printed = oversized_internal_err.getvalue()
        self.assertEqual(
            oversized_internal_reason, "LOCAL_MATERIAL_RAG_INTERNAL_ERROR"
        )
        self.assertIn(
            "LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE_DEGRADED MALFORMED",
            oversized_printed.splitlines(),
        )
        self.assertNotIn(internal_line, oversized_printed)
        self.assertNotIn("A" * 32, oversized_printed)

        primary = verify.MaterialRagVerifyError(
            "LOCAL_MATERIAL_RAG_P3_SCAN_PROTOCOL_FAILED"
        )
        preserved = verify._preserve_primary_error(primary, RuntimeError("dispose-boom"))
        self.assertIs(preserved, primary)
        self.assertEqual(
            preserved.reason, "LOCAL_MATERIAL_RAG_P3_SCAN_PROTOCOL_FAILED"
        )
        verify._INTERNAL_EVIDENCE.clear()
        overlay = verify._preserve_primary_error(None, TypeError("dispose-only"))
        self.assertEqual(overlay.reason, "LOCAL_MATERIAL_RAG_INTERNAL_ERROR")
        dispose_err = io.StringIO()
        with contextlib.redirect_stderr(dispose_err):
            verify._INTERNAL_EVIDENCE.emit_for_reason(
                "LOCAL_MATERIAL_RAG_INTERNAL_ERROR"
            )
        dispose_lines = [
            line
            for line in dispose_err.getvalue().splitlines()
            if line.startswith("LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE ")
        ]
        self.assertEqual(len(dispose_lines), 1)
        dispose_payload = json.loads(
            dispose_lines[0][len("LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE ") :]
        )
        self.assertEqual(dispose_payload["phase"], "DISPOSE_ENGINES")
        self.assertEqual(dispose_payload["operation"], "UNKNOWN")
        self.assertEqual(dispose_payload["error_class"], "TYPE_ERROR")
        self.assertEqual(dispose_payload["db_token"], "NONE")
        self.assertEqual(dispose_payload["sqlstate"], "NONE")
        self.assertIs(dispose_payload["primary_preserved"], False)
        self.assertNotIn("dispose-only", dispose_err.getvalue())
        self.assertNotIn("dispose-boom", dispose_err.getvalue())

        class OperationalError(Exception):
            pass

        OperationalError.__module__ = "sqlalchemy.exc"
        db_error = OperationalError("SELECT 1 FROM secret_table")
        self.assertEqual(verify._classify_internal_error(db_error), "DB_OPERATIONAL")

        class InvalidRequestError(Exception):
            pass

        InvalidRequestError.__module__ = "sqlalchemy.exc"
        self.assertEqual(
            verify._classify_internal_error(InvalidRequestError("invalid-bait")),
            "DB_INVALID_REQUEST",
        )

        class PendingRollbackError(InvalidRequestError):
            pass

        PendingRollbackError.__module__ = "sqlalchemy.exc"
        self.assertEqual(
            verify._classify_internal_error(PendingRollbackError("rollback-bait")),
            "DB_PENDING_ROLLBACK",
        )

        class MissingGreenlet(Exception):
            pass

        MissingGreenlet.__module__ = "sqlalchemy.exc"
        self.assertEqual(
            verify._classify_internal_error(MissingGreenlet("greenlet-bait")),
            "DB_MISSING_GREENLET",
        )

        class DataError(Exception):
            pass

        DataError.__module__ = "sqlalchemy.exc"
        self.assertEqual(
            verify._classify_internal_error(DataError("data-bait")),
            "DB_DATA",
        )
        self.assertEqual(
            verify._classify_internal_error(AssertionError("assert-bait")),
            "ASSERTION_ERROR",
        )
        self.assertEqual(
            verify._classify_internal_error(IndexError("idx-bait")),
            "INDEX_ERROR",
        )
        self.assertEqual(
            verify._classify_internal_error(asyncio.CancelledError()),
            "CANCELLED_ERROR",
        )
        grouped = ExceptionGroup("group-bait", [RuntimeError("group-runtime")])
        self.assertEqual(
            verify._classify_internal_error(grouped), "EXCEPTION_GROUP"
        )
        verify._INTERNAL_EVIDENCE.clear()
        verify._INTERNAL_EVIDENCE.record(
            verify._classify_internal_error(db_error),
            "PJ_PRIMARY_INDEX",
            True,
        )
        db_err = io.StringIO()
        with contextlib.redirect_stderr(db_err):
            verify._INTERNAL_EVIDENCE.emit_for_reason(
                "LOCAL_MATERIAL_RAG_INTERNAL_ERROR"
            )
        db_printed = db_err.getvalue()
        db_lines = [
            line
            for line in db_printed.splitlines()
            if line.startswith("LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE ")
        ]
        self.assertEqual(len(db_lines), 1)
        db_payload = json.loads(
            db_lines[0][len("LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE ") :]
        )
        self.assertEqual(db_payload["error_class"], "DB_OPERATIONAL")
        self.assertEqual(db_payload["phase"], "PJ_PRIMARY_INDEX")
        self.assertEqual(db_payload["operation"], "UNKNOWN")
        self.assertEqual(db_payload["db_token"], "NONE")
        self.assertEqual(db_payload["sqlstate"], "NONE")
        for bait in (
            "OperationalError",
            "sqlalchemy",
            "SELECT",
            "secret_table",
            "assert-bait",
            "idx-bait",
            "group-bait",
            "group-runtime",
            "ExceptionGroup",
            "CancelledError",
            "invalid-bait",
            "rollback-bait",
            "greenlet-bait",
            "data-bait",
        ):
            self.assertNotIn(bait, db_printed)

        class _RaiseDiag:
            sqlstate = "P0001"
            message_primary = "MATERIAL_RAG_UNIT_SOURCE_NOT_RELEASED"

        class RaiseException(Exception):
            sqlstate = "P0001"
            diag = _RaiseDiag()

        RaiseException.__module__ = "psycopg.errors"
        raise_error = RaiseException("SELECT leak from secret_table")
        self.assertEqual(verify._classify_internal_error(raise_error), "DB_OTHER")
        self.assertEqual(verify._safe_sqlstate(raise_error), "P0001")
        self.assertEqual(
            verify._safe_db_token(raise_error),
            "MATERIAL_RAG_UNIT_SOURCE_NOT_RELEASED",
        )
        verify._INTERNAL_EVIDENCE.clear()
        verify._enter_internal_operation("PROCESS_DEMO_JOB")
        verify._INTERNAL_EVIDENCE.record(
            verify._classify_internal_error(raise_error),
            "PJ_PRIMARY_INDEX",
            True,
            source=raise_error,
        )
        raise_err = io.StringIO()
        with contextlib.redirect_stderr(raise_err):
            verify._INTERNAL_EVIDENCE.emit_for_reason(
                "LOCAL_MATERIAL_RAG_INTERNAL_ERROR"
            )
        raise_printed = raise_err.getvalue()
        raise_lines = [
            line
            for line in raise_printed.splitlines()
            if line.startswith("LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE ")
        ]
        self.assertEqual(len(raise_lines), 1)
        raise_payload = json.loads(
            raise_lines[0][len("LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE ") :]
        )
        self.assertEqual(raise_payload["error_class"], "DB_OTHER")
        self.assertEqual(raise_payload["operation"], "PROCESS_DEMO_JOB")
        self.assertEqual(raise_payload["phase"], "PJ_PRIMARY_INDEX")
        self.assertEqual(raise_payload["sqlstate"], "P0001")
        self.assertEqual(
            raise_payload["db_token"], "MATERIAL_RAG_UNIT_SOURCE_NOT_RELEASED"
        )
        for bait in (
            "SELECT leak",
            "secret_table",
            "RaiseException",
            "psycopg",
            "message_primary",
        ):
            self.assertNotIn(bait, raise_printed)

        class _SqlDiag:
            sqlstate = "p0001"
            message_primary = "SELECT 1 FROM secret_table"

        class SqlBaitError(Exception):
            sqlstate = "p0001"
            diag = _SqlDiag()

        SqlBaitError.__module__ = "psycopg.errors"
        sql_bait = SqlBaitError("SELECT 1 FROM secret_table")
        self.assertEqual(verify._safe_sqlstate(sql_bait), "NONE")
        self.assertEqual(verify._safe_db_token(sql_bait), "NONE")
        verify._INTERNAL_EVIDENCE.clear()
        verify._INTERNAL_EVIDENCE.record(
            verify._classify_internal_error(sql_bait),
            "PJ_PRIMARY_INDEX",
            True,
            source=sql_bait,
        )
        sql_bait_err = io.StringIO()
        with contextlib.redirect_stderr(sql_bait_err):
            verify._INTERNAL_EVIDENCE.emit_for_reason(
                "LOCAL_MATERIAL_RAG_INTERNAL_ERROR"
            )
        sql_bait_printed = sql_bait_err.getvalue()
        sql_bait_payload = json.loads(
            [
                line
                for line in sql_bait_printed.splitlines()
                if line.startswith("LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE ")
            ][0][len("LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE ") :]
        )
        self.assertEqual(sql_bait_payload["sqlstate"], "NONE")
        self.assertEqual(sql_bait_payload["db_token"], "NONE")
        self.assertNotIn("SELECT", sql_bait_printed)
        self.assertNotIn("secret_table", sql_bait_printed)
        self.assertNotIn("p0001", sql_bait_printed)
        verify._enter_internal_operation("UNKNOWN")

        nul_message = "PostgreSQL text fields cannot contain NUL (0x00) bytes"

        class DataError(Exception):
            pass

        DataError.__module__ = "psycopg.errors"
        nul_error = DataError(nul_message)
        self.assertEqual(verify._classify_internal_error(nul_error), "DB_DATA")
        self.assertEqual(verify._safe_sqlstate(nul_error), "NONE")
        self.assertEqual(verify._safe_db_token(nul_error), "TEXT_NUL")
        verify._INTERNAL_EVIDENCE.clear()
        verify._enter_internal_operation("PERSIST_UNITS")
        verify._INTERNAL_EVIDENCE.record(
            verify._classify_internal_error(nul_error),
            "PJ_PRIMARY_INDEX",
            True,
            source=nul_error,
        )
        nul_err = io.StringIO()
        with contextlib.redirect_stderr(nul_err):
            verify._INTERNAL_EVIDENCE.emit_for_reason(
                "LOCAL_MATERIAL_RAG_INTERNAL_ERROR"
            )
        nul_printed = nul_err.getvalue()
        nul_payload = json.loads(
            [
                line
                for line in nul_printed.splitlines()
                if line.startswith("LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE ")
            ][0][len("LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE ") :]
        )
        self.assertEqual(nul_payload["error_class"], "DB_DATA")
        self.assertEqual(nul_payload["operation"], "PERSIST_UNITS")
        self.assertEqual(nul_payload["db_token"], "TEXT_NUL")
        self.assertEqual(nul_payload["sqlstate"], "NONE")
        self.assertNotIn(nul_message, nul_printed)
        self.assertNotIn("0x00", nul_printed)
        self.assertNotIn("PostgreSQL text fields", nul_printed)
        verify._enter_internal_operation("UNKNOWN")

        async def _fake_setup(_fixtures: object) -> object:
            return object()

        async def _fake_jobs_ok(_fixtures: object, _setup: object) -> object:
            return object()

        async def _fake_jobs_scan(_fixtures: object, _setup: object) -> object:
            raise verify.MaterialRagVerifyError(
                "LOCAL_MATERIAL_RAG_P3_SCAN_PROTOCOL_FAILED"
            )

        class DatabaseError(Exception):
            pass

        DatabaseError.__module__ = "sqlalchemy.engine"

        async def _fake_jobs_db(_fixtures: object, _setup: object) -> object:
            verify._enter_internal_phase("PJ_PRIMARY_INDEX")
            verify._enter_internal_operation("ENQUEUE_JOB")
            raise DatabaseError("SELECT leak from secret_table")

        async def _dispose_type_error() -> None:
            raise TypeError("dispose-only")

        async def _dispose_runtime_error() -> None:
            raise RuntimeError("dispose-boom")

        verify._INTERNAL_EVIDENCE.clear()
        with (
            patch.object(verify, "_setup_and_upload", _fake_setup),
            patch.object(verify, "_process_jobs", _fake_jobs_scan),
            patch.object(verify, "_dispose_engines", _dispose_runtime_error),
        ):
            with self.assertRaises(verify.MaterialRagVerifyError) as raised:
                asyncio.run(verify._run_async(()))
        self.assertEqual(
            raised.exception.reason, "LOCAL_MATERIAL_RAG_P3_SCAN_PROTOCOL_FAILED"
        )
        scan_dispose_err = io.StringIO()
        with contextlib.redirect_stderr(scan_dispose_err):
            verify._INTERNAL_EVIDENCE.emit_for_reason(
                "LOCAL_MATERIAL_RAG_INTERNAL_ERROR"
            )
        self.assertEqual(scan_dispose_err.getvalue(), "")
        self.assertNotIn("dispose-boom", scan_dispose_err.getvalue())

        verify._INTERNAL_EVIDENCE.clear()
        with (
            patch.object(verify, "_setup_and_upload", _fake_setup),
            patch.object(verify, "_process_jobs", _fake_jobs_ok),
            patch.object(verify, "_dispose_engines", _dispose_type_error),
        ):
            with self.assertRaises(verify.MaterialRagVerifyError) as raised:
                asyncio.run(verify._run_async(()))
        self.assertEqual(raised.exception.reason, "LOCAL_MATERIAL_RAG_INTERNAL_ERROR")
        run_dispose_err = io.StringIO()
        with contextlib.redirect_stderr(run_dispose_err):
            verify._INTERNAL_EVIDENCE.emit_for_reason(
                "LOCAL_MATERIAL_RAG_INTERNAL_ERROR"
            )
        run_dispose_lines = [
            line
            for line in run_dispose_err.getvalue().splitlines()
            if line.startswith("LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE ")
        ]
        self.assertEqual(len(run_dispose_lines), 1)
        run_dispose_payload = json.loads(
            run_dispose_lines[0][len("LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE ") :]
        )
        self.assertEqual(run_dispose_payload["phase"], "DISPOSE_ENGINES")
        self.assertEqual(run_dispose_payload["operation"], "UNKNOWN")
        self.assertEqual(run_dispose_payload["error_class"], "TYPE_ERROR")
        self.assertEqual(run_dispose_payload["db_token"], "NONE")
        self.assertEqual(run_dispose_payload["sqlstate"], "NONE")
        self.assertIs(run_dispose_payload["primary_preserved"], False)
        self.assertNotIn("dispose-only", run_dispose_err.getvalue())

        verify._INTERNAL_EVIDENCE.clear()
        with (
            patch.object(verify, "_setup_and_upload", _fake_setup),
            patch.object(verify, "_process_jobs", _fake_jobs_db),
            patch.object(verify, "_dispose_engines", _dispose_type_error),
        ):
            with self.assertRaises(DatabaseError):
                asyncio.run(verify._run_async(()))
        primary_dispose_err = io.StringIO()
        with contextlib.redirect_stderr(primary_dispose_err):
            verify._INTERNAL_EVIDENCE.emit_for_reason(
                "LOCAL_MATERIAL_RAG_INTERNAL_ERROR"
            )
        primary_dispose_lines = [
            line
            for line in primary_dispose_err.getvalue().splitlines()
            if line.startswith("LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE ")
        ]
        self.assertEqual(len(primary_dispose_lines), 1)
        primary_dispose_payload = json.loads(
            primary_dispose_lines[0][len("LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE ") :]
        )
        self.assertEqual(primary_dispose_payload["phase"], "PJ_PRIMARY_INDEX")
        self.assertEqual(primary_dispose_payload["operation"], "ENQUEUE_JOB")
        self.assertEqual(primary_dispose_payload["error_class"], "DB_OTHER")
        self.assertEqual(primary_dispose_payload["db_token"], "NONE")
        self.assertEqual(primary_dispose_payload["sqlstate"], "NONE")
        self.assertIs(primary_dispose_payload["primary_preserved"], True)
        for bait in (
            "idx-boom",
            "dispose-only",
            "SELECT leak",
            "secret_table",
            "DatabaseError",
            "sqlalchemy",
        ):
            self.assertNotIn(bait, primary_dispose_err.getvalue())

        persist_source = (
            ROOT / "src/platform_foundation/f1/features/material_rag/repository.py"
        ).read_text(encoding="utf-8").split(
            "async def persist_canonical_units", 1
        )[1].split("async def load_dataset_binding", 1)[0]
        self.assertIn("bindparam", persist_source)
        self.assertIn("LargeBinary", persist_source)
        self.assertIn('bindparam("body_ciphertext"', persist_source)
        self.assertIn("type_=LargeBinary()", persist_source)
        lock_source = (
            ROOT / "src/platform_foundation/f1/features/material_rag/repository.py"
        ).read_text(encoding="utf-8").split(
            "def live_scope_job_lock", 1
        )[1].split("async def persist_canonical_units", 1)[0]
        self.assertIn("hashbyteaextended", lock_source)
        self.assertIn(".encode(", lock_source)
        self.assertNotIn("hashtextextended", lock_source)
        fence_source = (
            ROOT / "src/platform_foundation/f1/features/material_rag/repository.py"
        ).read_text(encoding="utf-8").split(
            "def live_source_mutation_fence", 1
        )[1].split("def live_scope_job_lock", 1)[0]
        self.assertIn("FOR SHARE OF active_job, task", fence_source)
        self.assertNotIn("FOR SHARE OF active_job, version", fence_source)
        self.assertNotIn("FOR SHARE OF record", fence_source)
        self.assertNotIn(
            "FOR SHARE OF active_job, version, record, task", fence_source
        )
        self.assertIn("JOIN f1.document_version AS version", fence_source)
        self.assertIn("JOIN f1.document_record AS record", fence_source)
        self.assertIn("JOIN f1.upload_task AS task", fence_source)
        expected_index_job_statuses = frozenset(
            {"done", "failed", "queued", "retry_wait", "running"}
        )
        expected_index_reason_tokens = frozenset(
            {
                "MATERIAL_RAG_BINDING_MISSING",
                "MATERIAL_RAG_DATASET_BINDING_CONFLICT",
                "MATERIAL_RAG_DATASET_BINDING_DELETING",
                "MATERIAL_RAG_DATASET_BINDING_INVALID",
                "MATERIAL_RAG_DATASET_FINALIZE_FAILED",
                "MATERIAL_RAG_DELETE_UNITS_FORBIDDEN",
                "MATERIAL_RAG_IDEMPOTENCY_CONFLICT",
                "MATERIAL_RAG_INTEGRITY_FAILED",
                "MATERIAL_RAG_JOB_ACTION_INVALID",
                "MATERIAL_RAG_LOCAL_FAILED",
                "MATERIAL_RAG_MANIFEST_INVALID",
                "MATERIAL_RAG_MANIFEST_REQUIRED",
                "MATERIAL_RAG_RELEASE_FENCE_FORBIDDEN",
                "MATERIAL_RAG_REMOTE_DATASET_DELETE_MISMATCH",
                "MATERIAL_RAG_REMOTE_DATASET_IDENTITY_INVALID",
                "MATERIAL_RAG_REMOTE_DATASET_NOT_EMPTY",
                "MATERIAL_RAG_SOURCE_NOT_AUTHORIZED",
                "MATERIAL_RAG_STORED_MANIFEST_MISMATCH",
                "MATERIAL_RAG_NETWORK_FAILED",
                "MATERIAL_RAG_PROBE_FAILED",
                "MATERIAL_RAG_PROVISION_FAILED",
                "MATERIAL_RAG_UNAVAILABLE",
                "MATERIAL_RAG_UNITS_MISSING",
                "MATERIAL_RAG_UNIT_JOB_MISMATCH",
                "MATERIAL_UNIT_IDENTITY_CONFLICT",
                "MATERIAL_VERSION_NOT_FOUND",
                "MATERIAL_VERSION_NOT_INDEXABLE",
            }
        )
        expected_index_checkpoints = frozenset(
            {
                "CANONICAL_UNITS_EMPTY",
                "CONFLICT_ACCEPTED",
                "CONFLICT_IDENTITY",
                "CONFLICT_MUTATED",
                "CONFLICT_PERSIST",
                "JOB_ROW_MISSING",
                "NONE",
                "PRIMARY_ATTEST_COUNTS",
                "PRIMARY_ATTEST_REMOTE",
                "PRIMARY_FINGERPRINT",
                "PRIMARY_JOB",
                "PRIMARY_PROCESS",
                "REMOTE_SNAPSHOT",
                "REMOTE_TAGS",
                "SNAPSHOT_EXIT",
                "SNAPSHOT_LOAD",
                "SNAPSHOT_OPEN",
                "REPLAY_COUNTS",
                "REPLAY_JOB",
                "REPLAY_PROCESS",
                "REPLAY_REMOTE",
                "SYNTHETIC_COUNTS",
                "SYNTHETIC_JOB",
                "SYNTHETIC_PROCESS",
                "SYNTHETIC_REMOTE",
                "SYNTHETIC_SCOPES",
                "UNKNOWN",
            }
        )
        self.assertEqual(verify._INDEX_JOB_STATUSES, expected_index_job_statuses)
        self.assertEqual(
            verify._INDEX_REASON_TOKENS,
            expected_index_reason_tokens
            | verify._INDEX_PROBE_STATUS_TOKENS
            | verify._INDEX_CHUNK_ADD_CODE_TOKENS,
        )
        self.assertEqual(verify._INDEX_EVIDENCE_CHECKPOINTS, expected_index_checkpoints)
        self.assertEqual(
            localctl._MATERIAL_RAG_INDEX_JOB_STATUSES,
            expected_index_job_statuses | {"NONE"},
        )
        self.assertEqual(
            localctl._MATERIAL_RAG_INDEX_REASON_TOKENS,
            expected_index_reason_tokens
            | {"NONE"}
            | localctl._MATERIAL_RAG_INDEX_PROBE_STATUS_TOKENS
            | localctl._MATERIAL_RAG_INDEX_CHUNK_ADD_CODE_TOKENS,
        )
        self.assertEqual(
            verify._INDEX_PROBE_STATUS_TOKENS,
            localctl._MATERIAL_RAG_INDEX_PROBE_STATUS_TOKENS,
        )
        self.assertEqual(
            verify._INDEX_CHUNK_ADD_CODE_TOKENS,
            localctl._MATERIAL_RAG_INDEX_CHUNK_ADD_CODE_TOKENS,
        )
        self.assertEqual(
            localctl._MATERIAL_RAG_INDEX_EVIDENCE_CHECKPOINTS,
            expected_index_checkpoints,
        )
        self.assertEqual(
            localctl._MATERIAL_RAG_INDEX_EVIDENCE_KEYS,
            frozenset(
                {
                    "checkpoint",
                    "finish_sqlstate",
                    "job_status",
                    "lease_live",
                    "lease_present",
                    "lease_source",
                    "operation",
                    "outcome",
                    "phase",
                    "reason_token",
                    "token_match",
                }
            ),
        )
        expected_index_outcomes = frozenset(
            {
                "CLAIM_NONE",
                "FINISH_EXCEPTION",
                "FINISH_FALSE",
                "FINISH_TRUE",
                "LEASE_LOST",
                "NONE",
            }
        )
        expected_index_lease_sources = frozenset(
            {
                "ADAPTER",
                "FINISH_DONE",
                "MUTATION_FENCE",
                "NONE",
                "RENEW",
                "SCOPE_LOCK",
                "UNKNOWN",
            }
        )
        self.assertEqual(verify._INDEX_OUTCOMES, expected_index_outcomes)
        self.assertEqual(
            localctl._MATERIAL_RAG_INDEX_OUTCOMES, expected_index_outcomes
        )
        self.assertEqual(verify._INDEX_LEASE_SOURCES, expected_index_lease_sources)
        self.assertEqual(
            localctl._MATERIAL_RAG_INDEX_LEASE_SOURCES,
            expected_index_lease_sources,
        )
        main_source = verify_source.split("def main()", 1)[1].split(
            'if __name__ == "__main__":', 1
        )[0]
        self.assertIn("_INDEX_EVIDENCE.clear()", main_source)
        self.assertIn("_INDEX_EVIDENCE.emit_for_reason(reason)", main_source)
        self.assertNotIn("_emit_index_failure_evidence", verify_source)
        self.assertIn('await _raise_index_failed(job_id, "PRIMARY_PROCESS")', verify_source)
        self.assertIn("_fail_index(", verify_source)
        record_source = verify_source.split("class _IndexEvidenceBuffer", 1)[1].split(
            "def emit_for_reason", 1
        )[0]
        self.assertNotIn("print(", record_source)
        index_payload = {
            "checkpoint": "PRIMARY_PROCESS",
            "finish_sqlstate": "NONE",
            "job_status": "failed",
            "lease_live": False,
            "lease_present": True,
            "lease_source": "MUTATION_FENCE",
            "operation": "MUTATION_FENCE",
            "outcome": "LEASE_LOST",
            "phase": "PJ_PRIMARY_INDEX",
            "reason_token": "MATERIAL_VERSION_NOT_INDEXABLE",
            "token_match": False,
        }
        index_line = (
            "LOCAL_MATERIAL_RAG_INDEX_EVIDENCE "
            + json.dumps(index_payload, separators=(",", ":"), sort_keys=True)
        )
        index_secret = "index-leak-token-must-not-print"
        index_url = "https://ark.cn-beijing.volces.com/api/plan/v3"
        index_path = "/app/infra/f1/local_material_rag_verify.py"
        index_sql = "SELECT leak from secret_table"
        index_err = io.StringIO()
        with contextlib.redirect_stderr(index_err):
            index_reason = localctl._emit_material_rag_verifier_diagnostics(
                "LOCAL_MATERIAL_RAG_INDEX_FAILED\n",
                index_line
                + "\n"
                + index_secret
                + "\n"
                + index_url
                + "\n"
                + index_path
                + "\n"
                + index_sql
                + "\n",
                fallback_reason="LOCAL_COMMAND_FAILED",
            )
        index_printed = index_err.getvalue()
        self.assertEqual(index_reason, "LOCAL_MATERIAL_RAG_INDEX_FAILED")
        self.assertIn(index_line, index_printed.splitlines())
        for bait in (index_secret, index_url, index_path, "SELECT leak", "secret_table"):
            self.assertNotIn(bait, index_printed)

        other_index = dict(index_payload)
        other_index["checkpoint"] = "REPLAY_PROCESS"
        other_index_line = (
            "LOCAL_MATERIAL_RAG_INDEX_EVIDENCE "
            + json.dumps(other_index, separators=(",", ":"), sort_keys=True)
        )
        duplicate_index = io.StringIO()
        with contextlib.redirect_stderr(duplicate_index):
            duplicate_index_reason = localctl._emit_material_rag_verifier_diagnostics(
                "LOCAL_MATERIAL_RAG_INDEX_FAILED\n",
                index_line + "\n" + other_index_line + "\n",
                fallback_reason="LOCAL_COMMAND_FAILED",
            )
        malformed_index = (
            "LOCAL_MATERIAL_RAG_INDEX_EVIDENCE {"
            + index_secret
            + ","
            + index_url
            + ","
            + index_path
            + "}"
        )
        malformed_index_err = io.StringIO()
        with contextlib.redirect_stderr(malformed_index_err):
            malformed_index_reason = localctl._emit_material_rag_verifier_diagnostics(
                "LOCAL_MATERIAL_RAG_INDEX_FAILED\n",
                malformed_index + "\n",
                fallback_reason="LOCAL_COMMAND_FAILED",
            )
        self.assertEqual(duplicate_index_reason, "LOCAL_MATERIAL_RAG_INDEX_FAILED")
        self.assertEqual(malformed_index_reason, "LOCAL_MATERIAL_RAG_INDEX_FAILED")
        self.assertIn(
            "LOCAL_MATERIAL_RAG_INDEX_EVIDENCE_DEGRADED DUPLICATE",
            duplicate_index.getvalue().splitlines(),
        )
        self.assertIn(
            "LOCAL_MATERIAL_RAG_INDEX_EVIDENCE_DEGRADED MALFORMED",
            malformed_index_err.getvalue().splitlines(),
        )
        self.assertNotIn(index_line, duplicate_index.getvalue())
        for bait in (index_secret, index_url, index_path):
            self.assertNotIn(bait, malformed_index_err.getvalue())
            self.assertNotIn(bait, duplicate_index.getvalue())

        oversized_index = "LOCAL_MATERIAL_RAG_INDEX_EVIDENCE " + ("A" * 1100)
        self.assertGreater(len(oversized_index.encode("utf-8")), 1024)
        oversized_index_err = io.StringIO()
        with contextlib.redirect_stderr(oversized_index_err):
            oversized_index_reason = localctl._emit_material_rag_verifier_diagnostics(
                "LOCAL_MATERIAL_RAG_INDEX_FAILED\n",
                oversized_index + "\n" + index_line + "\n",
                fallback_reason="LOCAL_COMMAND_FAILED",
            )
        oversized_index_printed = oversized_index_err.getvalue()
        self.assertEqual(oversized_index_reason, "LOCAL_MATERIAL_RAG_INDEX_FAILED")
        self.assertIn(
            "LOCAL_MATERIAL_RAG_INDEX_EVIDENCE_DEGRADED MALFORMED",
            oversized_index_printed.splitlines(),
        )
        self.assertNotIn(index_line, oversized_index_printed)
        self.assertNotIn("A" * 32, oversized_index_printed)

        def _index_run_recorded() -> object:
            print(index_sql, file=sys.stderr)
            print(index_url, file=sys.stderr)
            print(index_path, file=sys.stderr)
            verify._enter_internal_operation("MUTATION_FENCE")
            verify._enter_internal_phase("PJ_PRIMARY_INDEX")
            verify._INDEX_EVIDENCE.record(
                "PRIMARY_PROCESS",
                job_status="failed",
                reason_token="MATERIAL_VERSION_NOT_INDEXABLE",
            )
            raise verify.MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")

        verify._INDEX_EVIDENCE.clear()
        verify._INTERNAL_EVIDENCE.clear()
        index_main_out = io.StringIO()
        index_main_err = io.StringIO()
        with patch.object(verify, "run", _index_run_recorded):
            with contextlib.redirect_stdout(index_main_out), contextlib.redirect_stderr(
                index_main_err
            ):
                index_main_rc = verify.main()
        self.assertEqual(index_main_rc, 1)
        self.assertEqual(index_main_out.getvalue(), "")
        index_main_printed = index_main_err.getvalue()
        self.assertIn("LOCAL_MATERIAL_RAG_INDEX_FAILED", index_main_printed.splitlines())
        index_main_lines = [
            line
            for line in index_main_printed.splitlines()
            if line.startswith("LOCAL_MATERIAL_RAG_INDEX_EVIDENCE ")
        ]
        self.assertEqual(len(index_main_lines), 1)
        index_main_payload = json.loads(
            index_main_lines[0][len("LOCAL_MATERIAL_RAG_INDEX_EVIDENCE ") :]
        )
        self.assertEqual(index_main_payload["checkpoint"], "PRIMARY_PROCESS")
        self.assertEqual(index_main_payload["job_status"], "failed")
        self.assertEqual(index_main_payload["operation"], "MUTATION_FENCE")
        self.assertEqual(index_main_payload["phase"], "PJ_PRIMARY_INDEX")
        self.assertEqual(
            index_main_payload["reason_token"], "MATERIAL_VERSION_NOT_INDEXABLE"
        )
        self.assertEqual(index_main_payload["outcome"], "NONE")
        self.assertEqual(index_main_payload["lease_source"], "NONE")
        self.assertIs(index_main_payload["lease_present"], False)
        self.assertIs(index_main_payload["lease_live"], False)
        self.assertIs(index_main_payload["token_match"], False)
        self.assertEqual(index_main_payload["finish_sqlstate"], "NONE")
        for bait in (index_sql, "SELECT leak", "secret_table", index_url, index_path):
            self.assertNotIn(bait, index_main_printed)

        def _index_run_empty() -> object:
            verify._enter_internal_operation("UNKNOWN")
            verify._enter_internal_phase("UNKNOWN")
            raise verify.MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")

        verify._INDEX_EVIDENCE.clear()
        verify._INTERNAL_EVIDENCE.clear()
        fallback_out = io.StringIO()
        fallback_err = io.StringIO()
        with patch.object(verify, "run", _index_run_empty):
            with contextlib.redirect_stdout(fallback_out), contextlib.redirect_stderr(
                fallback_err
            ):
                fallback_rc = verify.main()
        self.assertEqual(fallback_rc, 1)
        self.assertEqual(fallback_out.getvalue(), "")
        fallback_printed = fallback_err.getvalue()
        fallback_lines = [
            line
            for line in fallback_printed.splitlines()
            if line.startswith("LOCAL_MATERIAL_RAG_INDEX_EVIDENCE ")
        ]
        self.assertEqual(len(fallback_lines), 1)
        fallback_payload = json.loads(
            fallback_lines[0][len("LOCAL_MATERIAL_RAG_INDEX_EVIDENCE ") :]
        )
        self.assertEqual(fallback_payload["checkpoint"], "NONE")
        self.assertEqual(fallback_payload["job_status"], "NONE")
        self.assertEqual(fallback_payload["operation"], "UNKNOWN")
        self.assertEqual(fallback_payload["phase"], "UNKNOWN")
        self.assertEqual(fallback_payload["reason_token"], "NONE")
        self.assertEqual(fallback_payload["outcome"], "NONE")
        self.assertEqual(fallback_payload["lease_source"], "NONE")
        self.assertIs(fallback_payload["lease_present"], False)
        self.assertIs(fallback_payload["lease_live"], False)
        self.assertIs(fallback_payload["token_match"], False)
        self.assertEqual(fallback_payload["finish_sqlstate"], "NONE")
        record_err = io.StringIO()
        verify._INDEX_EVIDENCE.clear()
        with contextlib.redirect_stderr(record_err):
            verify._INDEX_EVIDENCE.record("PRIMARY_PROCESS")
            verify._INDEX_EVIDENCE.record("REPLAY_PROCESS")
        self.assertEqual(record_err.getvalue(), "")
        first_wins_err = io.StringIO()
        with contextlib.redirect_stderr(first_wins_err):
            verify._INDEX_EVIDENCE.emit_for_reason("LOCAL_MATERIAL_RAG_INDEX_FAILED")
        first_wins_lines = [
            line
            for line in first_wins_err.getvalue().splitlines()
            if line.startswith("LOCAL_MATERIAL_RAG_INDEX_EVIDENCE ")
        ]
        self.assertEqual(len(first_wins_lines), 1)
        first_wins_payload = json.loads(
            first_wins_lines[0][len("LOCAL_MATERIAL_RAG_INDEX_EVIDENCE ") :]
        )
        self.assertEqual(first_wins_payload["checkpoint"], "PRIMARY_PROCESS")
        self.assertEqual(first_wins_payload["outcome"], "NONE")
        self.assertIs(first_wins_payload["lease_present"], False)

        from platform_foundation.f1.features.material_rag import worker as rag_worker
        from platform_foundation.f1.features.material_rag.contracts import (
            MaterialRagIntegrityError,
            MaterialRagJobClaim,
            MaterialRagLeaseLost,
        )

        worker_source = (
            ROOT / "src/platform_foundation/f1/features/material_rag/worker.py"
        ).read_text(encoding="utf-8")
        locked_source = worker_source.split(
            "async def _process_claimed_demo_job_locked", 1
        )[1].split("async def process_claimed_demo_job", 1)[0]
        self.assertNotIn("except MaterialRagLeaseLost:\n        return False", locked_source)
        self.assertNotIn("UPDATE f1.document_record", worker_source)
        claimed_source = worker_source.split(
            "async def process_claimed_demo_job", 1
        )[1].split("async def process_demo_job", 1)[0]
        self.assertNotIn("except MaterialRagLeaseLost:\n        return False", claimed_source)
        self.assertIn("ProcessOutcome", worker_source)
        self.assertEqual(
            rag_worker.PROCESS_OUTCOME_KINDS,
            frozenset(
                {
                    "CLAIM_NONE",
                    "FINISH_EXCEPTION",
                    "FINISH_FALSE",
                    "FINISH_TRUE",
                    "LEASE_LOST",
                    "SUCCESS",
                }
            ),
        )

        claim = MaterialRagJobClaim(
            id=uuid.UUID("10000000-0000-4000-8000-000000000011"),
            enterprise_id=uuid.UUID("10000000-0000-4000-8000-000000000001"),
            knowledge_scope_id=uuid.UUID("10000000-0000-4000-8000-000000000002"),
            document_record_id=uuid.UUID("10000000-0000-4000-8000-000000000003"),
            document_version_id=uuid.UUID("10000000-0000-4000-8000-000000000004"),
            upload_task_id=uuid.UUID("10000000-0000-4000-8000-000000000005"),
            source_sha256=(
                "e64cb41465eaf3fc550dbc881c06d687275a8d2b6850f34c703c111a4a3cfc46"
            ),
            action="index",
            lease_token=uuid.UUID("10000000-0000-4000-8000-000000000006"),
            attempt=1,
        )

        async def _claim_none(job_id: uuid.UUID, *, worker_id: str):
            return None

        with patch.object(rag_worker, "claim_demo_job", _claim_none):
            none_outcome = asyncio.run(
                rag_worker.process_demo_job(
                    uuid.uuid4(), worker_id="material-rag-verifier"
                )
            )
        self.assertEqual(none_outcome.kind, "CLAIM_NONE")
        self.assertFalse(none_outcome)
        self.assertEqual(none_outcome.lease_source, "NONE")
        self.assertIs(none_outcome.lease_present, False)
        self.assertIs(none_outcome.lease_live, False)
        self.assertIs(none_outcome.token_match, False)

        finish_calls: list[dict[str, object]] = []

        async def _finish_track(*args: object, **kwargs: object):
            finish_calls.append(dict(kwargs))
            return True

        def _scope_lock_lost(claim_obj: object):
            raise rag_worker.lease_lost("SCOPE_LOCK")

        with (
            patch.object(rag_worker, "live_scope_job_lock", _scope_lock_lost),
            patch.object(rag_worker, "finish_job", _finish_track),
        ):
            lost_outcome = asyncio.run(rag_worker.process_claimed_demo_job(claim))
        self.assertEqual(lost_outcome.kind, "LEASE_LOST")
        self.assertEqual(lost_outcome.lease_source, "SCOPE_LOCK")
        self.assertFalse(lost_outcome)
        self.assertEqual(finish_calls, [])

        async def _integrity_locked(claim_obj: object, **kwargs: object):
            raise MaterialRagIntegrityError("MATERIAL_VERSION_NOT_INDEXABLE")

        async def _finish_false(*args: object, **kwargs: object):
            return False

        with (
            patch.object(
                rag_worker, "live_scope_job_lock", lambda claim_obj: contextlib.nullcontext()
            ),
            patch.object(
                rag_worker, "_process_claimed_demo_job_locked", _integrity_locked
            ),
            patch.object(rag_worker, "finish_job", _finish_false),
        ):
            finish_false_outcome = asyncio.run(
                rag_worker.process_claimed_demo_job(claim)
            )
        self.assertEqual(finish_false_outcome.kind, "FINISH_FALSE")
        self.assertEqual(finish_false_outcome.finish_sqlstate, "NONE")

        class ProgrammingError(Exception):
            sqlstate = "42501"

        ProgrammingError.__module__ = "psycopg.errors"
        finish_bait = "SELECT leak from secret_table"

        async def _finish_exception(*args: object, **kwargs: object):
            raise ProgrammingError(finish_bait)

        with (
            patch.object(
                rag_worker, "live_scope_job_lock", lambda claim_obj: contextlib.nullcontext()
            ),
            patch.object(
                rag_worker, "_process_claimed_demo_job_locked", _integrity_locked
            ),
            patch.object(rag_worker, "finish_job", _finish_exception),
        ):
            finish_exc_outcome = asyncio.run(
                rag_worker.process_claimed_demo_job(claim)
            )
        self.assertEqual(finish_exc_outcome.kind, "FINISH_EXCEPTION")
        self.assertEqual(finish_exc_outcome.finish_sqlstate, "42501")
        self.assertNotIn(finish_bait, repr(finish_exc_outcome))
        self.assertNotIn(finish_bait, str(finish_exc_outcome))

        verify._INDEX_EVIDENCE.clear()
        verify._enter_internal_operation("MUTATION_FENCE")
        verify._enter_internal_phase("PJ_PRIMARY_INDEX")
        verify._INDEX_EVIDENCE.record(
            "PRIMARY_PROCESS",
            job_status="running",
            reason_token="NONE",
            outcome="LEASE_LOST",
            lease_source="MUTATION_FENCE",
            lease_present=True,
            lease_live=True,
            token_match=False,
        )
        injected_err = io.StringIO()
        with contextlib.redirect_stderr(injected_err):
            verify._INDEX_EVIDENCE.emit_for_reason("LOCAL_MATERIAL_RAG_INDEX_FAILED")
        injected_printed = injected_err.getvalue()
        self.assertNotIn(finish_bait, injected_printed)
        self.assertNotIn(str(claim.lease_token), injected_printed)
        self.assertNotIn(str(claim.id), injected_printed)
        injected_lines = [
            line
            for line in injected_printed.splitlines()
            if line.startswith("LOCAL_MATERIAL_RAG_INDEX_EVIDENCE ")
        ]
        self.assertEqual(len(injected_lines), 1)
        injected_payload = json.loads(
            injected_lines[0][len("LOCAL_MATERIAL_RAG_INDEX_EVIDENCE ") :]
        )
        self.assertEqual(injected_payload["outcome"], "LEASE_LOST")
        self.assertEqual(injected_payload["lease_source"], "MUTATION_FENCE")
        self.assertIs(injected_payload["lease_present"], True)
        self.assertIs(injected_payload["lease_live"], True)
        self.assertIs(injected_payload["token_match"], False)

        malformed_outcome = dict(index_payload)
        malformed_outcome["outcome"] = finish_bait
        malformed_outcome_line = (
            "LOCAL_MATERIAL_RAG_INDEX_EVIDENCE "
            + json.dumps(malformed_outcome, separators=(",", ":"), sort_keys=True)
        )
        malformed_outcome_err = io.StringIO()
        with contextlib.redirect_stderr(malformed_outcome_err):
            localctl._emit_material_rag_verifier_diagnostics(
                "LOCAL_MATERIAL_RAG_INDEX_FAILED\n",
                malformed_outcome_line + "\n",
                fallback_reason="LOCAL_COMMAND_FAILED",
            )
        self.assertIn(
            "LOCAL_MATERIAL_RAG_INDEX_EVIDENCE_DEGRADED MALFORMED",
            malformed_outcome_err.getvalue().splitlines(),
        )
        self.assertNotIn(finish_bait, malformed_outcome_err.getvalue())

        scope_source = (
            ROOT / "src/platform_foundation/f1/features/p3/service.py"
        ).read_text(encoding="utf-8").split(
            "async def set_document_knowledge_scope", 1
        )[1].split("async def get_version", 1)[0]
        self.assertIn("FOR UPDATE OF task", scope_source)
        self.assertIn("ORDER BY task.id", scope_source)
        self.assertLess(
            scope_source.find("FOR UPDATE OF task"),
            scope_source.find("UPDATE f1.document_record"),
        )
        self.assertLess(
            scope_source.find("FOR UPDATE OF task"),
            scope_source.find("quarantine_status='released'"),
        )
        release_source = (
            ROOT / "src/platform_foundation/f1/features/p3/service.py"
        ).read_text(encoding="utf-8").split(
            'if action == "release":', 1
        )[1].split('elif action == "reject":', 1)[0]
        self.assertIn("FOR UPDATE OF task",
            (
                ROOT / "src/platform_foundation/f1/features/p3/service.py"
            ).read_text(encoding="utf-8").split("async def act_on_version", 1)[1].split(
                'if action == "release":', 1
            )[0]
        )
        self.assertIn("quarantine_status='released'", release_source)
        migration_source = (
            ROOT / "infra/f1/alembic/versions/f1_0015_material_rag.py"
        ).read_text(encoding="utf-8")
        self.assertIn("guard_document_record_scope", migration_source)
        self.assertIn("P3_KNOWLEDGE_SCOPE_LOCKED", migration_source)
        self.assertIn(
            "record.knowledge_scope_id = active_job.knowledge_scope_id",
            fence_source,
        )

    def test_material_rag_finish_policy_allows_terminal_without_broadening(self) -> None:
        migration = (
            ROOT / "infra/f1/alembic/versions/f1_0015_material_rag.py"
        ).read_text(encoding="utf-8")
        repository = (
            ROOT
            / "src/platform_foundation/f1/features/material_rag/repository.py"
        ).read_text(encoding="utf-8")
        rls_source = migration.split("def _rls_and_grants()", 1)[1].split(
            "def downgrade()", 1
        )[0]
        self.assertIn("job_target = ", rls_source)
        self.assertIn("job_after_update = ", rls_source)
        self.assertIn(
            'job_select_target = "(" + job_target + ") OR (" + job_after_update + ")"',
            rls_source,
        )
        self.assertIn(
            '"FOR SELECT TO f1_worker USING (" + job_select_target + ")"',
            rls_source,
        )
        self.assertNotIn(
            '"FOR SELECT TO f1_worker USING (" + job_target + ")"',
            rls_source,
        )
        self.assertIn(
            '"FOR UPDATE TO f1_worker USING (" + job_target + ") WITH CHECK ("',
            rls_source,
        )
        self.assertIn('+ job_after_update + ")"', rls_source)
        self.assertNotIn("USING (true)", rls_source)
        self.assertNotIn("USING(true)", rls_source)
        self.assertNotIn("USING (true)", rls_source.replace(" ", ""))
        self.assertIn("status IN ('done','retry_wait','failed')", rls_source)
        finish_fn = migration.split(
            "CREATE FUNCTION f1.finish_material_rag_job", 1
        )[1].split("CREATE FUNCTION", 1)[0]
        self.assertIn("SECURITY INVOKER", finish_fn)
        self.assertNotIn("SECURITY DEFINER", finish_fn)
        self.assertIn("set_config('f1.material_rag_job_id'", finish_fn)
        self.assertIn("set_config(\n            'f1.material_rag_lease_token'", finish_fn)
        self.assertNotIn("BYPASSRLS", migration)
        self.assertIn(
            "GRANT SELECT, UPDATE ON f1.material_rag_job TO f1_worker",
            migration,
        )
        self.assertNotIn(
            "GRANT SELECT, INSERT, UPDATE ON f1.material_rag_job",
            migration,
        )
        self.assertNotIn("GRANT UPDATE ON f1.document_record TO f1_worker", migration)
        self.assertNotIn("GRANT UPDATE ON f1.upload_task TO f1_worker", migration)
        worker_execute = [
            line
            for line in migration.splitlines()
            if "GRANT EXECUTE ON FUNCTION" in line and "f1_worker" in line
        ]
        self.assertIn(
            '        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO f1_worker")',
            migration,
        )
        self.assertTrue(
            any("f1_api, f1_worker" in line or "f1_worker" in line for line in worker_execute)
            or 'GRANT EXECUTE ON FUNCTION {signature} TO f1_worker' in migration
        )
        finish_job = repository.split("async def finish_job", 1)[1].split(
            "__all__", 1
        )[0]
        self.assertIn("finish_material_rag_job", finish_job)
        self.assertNotIn("set_config('f1.material_rag_job_id'", finish_job)
        self.assertNotIn("set_config('f1.material_rag_lease_token'", finish_job)
        fence = repository.split("def live_source_mutation_fence", 1)[1].split(
            "def live_scope_job_lock", 1
        )[0]
        self.assertIn("FOR SHARE OF active_job, task", fence)
        self.assertNotIn("set_config('f1.enterprise_id'", fence)
        self.assertIn("material_rag_source_upload_worker_update", rls_source)
        self.assertIn(
            '"f1.upload_task FOR UPDATE TO f1_worker USING ("',
            rls_source,
        )
        upload_update = rls_source.split(
            "material_rag_source_upload_worker_update", 1
        )[1].split("binding_worker", 1)[0]
        self.assertIn("+ source_upload_worker", upload_update)
        self.assertIn("WITH CHECK (", upload_update)
        self.assertNotIn("USING (true)", upload_update)
        self.assertNotIn("USING(true)", upload_update)

    def test_index_failure_transfers_egress_audit_without_text(self) -> None:
        verify = _load_material_rag_verify()
        localctl = _load_localctl()
        verify_source = (
            ROOT / "infra/f1/local_material_rag_verify.py"
        ).read_text(encoding="utf-8")
        localctl_source = (ROOT / "scripts/localctl").read_text(encoding="utf-8")
        main_source = verify_source.split("def main()", 1)[1].split(
            'if __name__ == "__main__":', 1
        )[0]
        self.assertIn("redirect_stderr(_DiscardText())", main_source)
        self.assertIn("_EGRESS_EVIDENCE.clear()", main_source)
        self.assertIn("_EGRESS_EVIDENCE.emit_for_reason(reason)", main_source)
        self.assertLess(
            main_source.find("redirect_stderr(_DiscardText())"),
            main_source.find("_EGRESS_EVIDENCE.emit_for_reason(reason)"),
        )
        diagnostics = localctl_source.split(
            "def _emit_material_rag_verifier_diagnostics", 1
        )[1].split("def _emit_material_rag_unreachable", 1)[0]
        self.assertIn(
            "_emit_material_rag_egress_evidence(stdout, stderr, reason)",
            diagnostics,
        )
        self.assertIn(
            "_emit_material_rag_retrieval_evidence(stdout, stderr, reason)",
            diagnostics,
        )
        self.assertIn("_RETRIEVAL_EVIDENCE.clear()", main_source)
        self.assertIn("_RETRIEVAL_EVIDENCE.emit_for_reason(reason)", main_source)
        expected_retrieval_keys = frozenset(
            {
                "checkpoint",
                "citation_mismatch_count",
                "client_a_hit_count",
                "client_a_overlap",
                "client_a_refusal",
                "client_a_refusal_token",
                "client_b_hit_count",
                "client_b_refusal",
                "client_b_refusal_token",
                "cross_ab",
                "cross_ba",
                "fragment_hit_count",
                "provider_hit_count",
                "provider_refusal",
                "provider_refusal_token",
            }
        )
        expected_retrieval_checkpoints = frozenset(
            {
                "CITATION_MATCH",
                "DEMO_FRAGMENT",
                "EXPECTED_COUNT",
                "NONE",
                "SCOPED_SET",
                "UNKNOWN",
            }
        )
        self.assertEqual(verify._RETRIEVAL_EVIDENCE_KEYS, expected_retrieval_keys)
        self.assertEqual(
            verify._RETRIEVAL_EVIDENCE_CHECKPOINTS, expected_retrieval_checkpoints
        )
        self.assertEqual(
            localctl._MATERIAL_RAG_RETRIEVAL_EVIDENCE_KEYS, expected_retrieval_keys
        )
        self.assertEqual(
            localctl._MATERIAL_RAG_RETRIEVAL_EVIDENCE_CHECKPOINTS,
            expected_retrieval_checkpoints,
        )
        expected_retrieval_refusal_tokens = frozenset(
            {
                "NONE",
                "NO_HITS",
                "NOT_CONFIGURED",
                "REJECTED",
                "UNAVAILABLE",
                "UNKNOWN",
            }
        )
        self.assertEqual(
            verify._RETRIEVAL_REFUSAL_TOKENS, expected_retrieval_refusal_tokens
        )
        self.assertEqual(
            localctl._MATERIAL_RAG_RETRIEVAL_REFUSAL_TOKENS,
            expected_retrieval_refusal_tokens,
        )
        self.assertIn("LOCAL_MATERIAL_RAG_RETRIEVAL_EVIDENCE ", verify_source)
        self.assertIn("_fail_retrieval(", verify_source)
        self.assertIn(
            "_emit_material_rag_rebuild_evidence(stdout, stderr, reason)",
            diagnostics,
        )
        self.assertIn("_REBUILD_EVIDENCE.clear()", main_source)
        self.assertIn("_REBUILD_EVIDENCE.emit_for_reason(reason)", main_source)
        expected_rebuild_keys = frozenset(
            {
                "checkpoint",
                "chunk_count",
                "document_count",
                "fingerprint_match",
                "job_status",
                "manifest_match",
                "outcome",
                "reason_token",
                "unit_count_match",
            }
        )
        expected_rebuild_checkpoints = frozenset(
            {
                "FINGERPRINT",
                "JOB_ROW",
                "NONE",
                "PROCESS",
                "REMOTE_SNAPSHOT",
                "REMOTE_TAGS",
                "UNKNOWN",
            }
        )
        expected_rebuild_outcomes = frozenset(
            {
                "CLAIM_NONE",
                "FINISH_EXCEPTION",
                "FINISH_FALSE",
                "FINISH_TRUE",
                "LEASE_LOST",
                "NONE",
                "SUCCESS",
            }
        )
        expected_rebuild_extra_reasons = frozenset(
            {
                "MATERIAL_RAG_RELEASE_FENCE_REQUIRED",
                "MATERIAL_RAG_REMOTE_BODY_MISMATCH",
                "MATERIAL_RAG_REMOTE_CHUNK_INVALID",
                "MATERIAL_RAG_REMOTE_COUNT_MISMATCH",
                "MATERIAL_RAG_REMOTE_DELETE_MISMATCH",
                "MATERIAL_RAG_REMOTE_DOCUMENT_AMBIGUOUS",
                "MATERIAL_RAG_REMOTE_DOCUMENT_INVALID",
                "MATERIAL_RAG_REMOTE_EXTRA_UNIT",
                "MATERIAL_RAG_REMOTE_IDENTITY_INVALID",
                "MATERIAL_RAG_UNIT_DUPLICATE",
                "MATERIAL_RAG_UNIT_SCOPE_MISMATCH",
            }
        )
        self.assertEqual(verify._REBUILD_EVIDENCE_KEYS, expected_rebuild_keys)
        self.assertEqual(
            verify._REBUILD_EVIDENCE_CHECKPOINTS, expected_rebuild_checkpoints
        )
        self.assertEqual(verify._REBUILD_OUTCOMES, expected_rebuild_outcomes)
        self.assertEqual(
            localctl._MATERIAL_RAG_REBUILD_EVIDENCE_KEYS, expected_rebuild_keys
        )
        self.assertEqual(
            localctl._MATERIAL_RAG_REBUILD_EVIDENCE_CHECKPOINTS,
            expected_rebuild_checkpoints,
        )
        self.assertEqual(
            localctl._MATERIAL_RAG_REBUILD_OUTCOMES, expected_rebuild_outcomes
        )
        self.assertTrue(
            expected_rebuild_extra_reasons.issubset(verify._REBUILD_REASON_TOKENS)
        )
        self.assertTrue(
            expected_rebuild_extra_reasons.issubset(
                localctl._MATERIAL_RAG_REBUILD_REASON_TOKENS
            )
        )
        self.assertIn("LOCAL_MATERIAL_RAG_REBUILD_EVIDENCE ", verify_source)
        self.assertIn("_fail_rebuild(", verify_source)
        rebuild_source = verify_source.split(
            '_enter_internal_phase("PJ_REBUILD")', 1
        )[1].split('_enter_internal_phase("PJ_DELETE")', 1)[0]
        self.assertIn("_fail_rebuild(", rebuild_source)
        self.assertIn('processed = await process_demo_job(', rebuild_source)
        self.assertIn('_fail_rebuild("PROCESS"', rebuild_source)
        self.assertIn('_fail_rebuild("JOB_ROW"', rebuild_source)
        self.assertIn('_fail_rebuild("FINGERPRINT"', rebuild_source)
        self.assertIn(
            "knowledge_scope_id=setup.client_a_scope_id",
            rebuild_source.split("await _remote_snapshot(", 1)[1].split(")", 1)[0],
        )
        rebuild_payload = {
            "checkpoint": "PROCESS",
            "chunk_count": 0,
            "document_count": 0,
            "fingerprint_match": 0,
            "job_status": "failed",
            "manifest_match": 0,
            "outcome": "FINISH_TRUE",
            "reason_token": "MATERIAL_RAG_REMOTE_IDENTITY_INVALID",
            "unit_count_match": 0,
        }
        rebuild_line = (
            "LOCAL_MATERIAL_RAG_REBUILD_EVIDENCE "
            + json.dumps(rebuild_payload, separators=(",", ":"), sort_keys=True)
        )
        rebuild_secret = "rebuild-leak-token-must-not-print"
        rebuild_err = io.StringIO()
        with contextlib.redirect_stderr(rebuild_err):
            rebuild_reason = localctl._emit_material_rag_verifier_diagnostics(
                "LOCAL_MATERIAL_RAG_REBUILD_FAILED\n",
                rebuild_line + "\n" + rebuild_secret + "\n",
                fallback_reason="LOCAL_COMMAND_FAILED",
            )
        self.assertEqual(rebuild_reason, "LOCAL_MATERIAL_RAG_REBUILD_FAILED")
        self.assertIn(rebuild_line, rebuild_err.getvalue().splitlines())
        self.assertNotIn(rebuild_secret, rebuild_err.getvalue())
        expected_keys = frozenset(
            {
                "audit_status",
                "authorized_embedding_request_count",
                "forwarded_embedding_request_count",
                "rejected_json_count",
                "rejected_model_count",
                "rejected_non_text_input_count",
                "rejected_path_count",
                "rejected_request_count",
                "rejected_unauthorized_text_count",
                "upstream_2xx_count",
                "upstream_4xx_count",
                "upstream_5xx_count",
            }
        )
        expected_statuses = frozenset(
            {"INVALID", "MISSING", "READY", "UNAVAILABLE"}
        )
        self.assertEqual(verify._EGRESS_EVIDENCE_KEYS, expected_keys)
        self.assertEqual(verify._EGRESS_AUDIT_STATUSES, expected_statuses)
        self.assertEqual(localctl._MATERIAL_RAG_EGRESS_EVIDENCE_KEYS, expected_keys)
        self.assertEqual(
            localctl._MATERIAL_RAG_EGRESS_AUDIT_STATUSES, expected_statuses
        )
        self.assertIn("LOCAL_MATERIAL_RAG_EGRESS_EVIDENCE ", verify_source)
        self.assertNotIn("body_sha256", verify_source.split("def _index_failure_egress_payload", 1)[1].split("class ", 1)[0])
        payload = {
            "audit_status": "READY",
            "authorized_embedding_request_count": 1,
            "forwarded_embedding_request_count": 1,
            "rejected_json_count": 0,
            "rejected_model_count": 0,
            "rejected_non_text_input_count": 0,
            "rejected_path_count": 0,
            "rejected_request_count": 2,
            "rejected_unauthorized_text_count": 2,
            "upstream_2xx_count": 0,
            "upstream_4xx_count": 0,
            "upstream_5xx_count": 0,
        }
        line = (
            "LOCAL_MATERIAL_RAG_EGRESS_EVIDENCE "
            + json.dumps(payload, separators=(",", ":"), sort_keys=True)
        )
        secret = "egress-leak-token-must-not-print"
        url = "https://ark.cn-beijing.volces.com/api/plan/v3"
        path = "/run/material-rag-egress/audit.json"
        body = '{"input":[{"type":"text","text":"secret-demo-body"}]}'
        stderr_text = (
            ("PAD" * 3000)
            + "\n"
            + line
            + "\n"
            + secret
            + "\n"
            + url
            + "\n"
            + path
            + "\n"
            + body
            + "\n"
        )
        self.assertGreater(len(stderr_text.encode("utf-8")), 8192)
        reprinted = io.StringIO()
        with contextlib.redirect_stderr(reprinted):
            reason = localctl._emit_material_rag_verifier_diagnostics(
                "LOCAL_MATERIAL_RAG_INDEX_FAILED\n",
                stderr_text,
                fallback_reason="LOCAL_COMMAND_FAILED",
            )
        printed = reprinted.getvalue()
        self.assertEqual(reason, "LOCAL_MATERIAL_RAG_INDEX_FAILED")
        self.assertIn(line, printed.splitlines())
        for bait in (secret, url, path, "secret-demo-body", "PAD"):
            self.assertNotIn(bait, printed)
        malformed = io.StringIO()
        with contextlib.redirect_stderr(malformed):
            localctl._emit_material_rag_verifier_diagnostics(
                "LOCAL_MATERIAL_RAG_INDEX_FAILED\n",
                "LOCAL_MATERIAL_RAG_EGRESS_EVIDENCE {" + secret + "," + url + "}\n",
                fallback_reason="LOCAL_COMMAND_FAILED",
            )
        self.assertIn(
            "LOCAL_MATERIAL_RAG_EGRESS_EVIDENCE_DEGRADED MALFORMED",
            malformed.getvalue().splitlines(),
        )
        self.assertNotIn(secret, malformed.getvalue())
        self.assertNotIn(url, malformed.getvalue())
        audit = {
            "aborted_embedding_request_count": 0,
            "allowed_method": "POST",
            "allowed_model": "doubao-embedding-vision",
            "allowed_path": "/api/plan/v3/embeddings/multimodal",
            "allowed_upstream_authority": "ark.cn-beijing.volces.com:443",
            "authorized_embedding_request_count": 3,
            "external_llm_call_count": 0,
            "external_ocr_call_count": 0,
            "forwarded_embedding_request_count": 1,
            "forwarded_non_embedding_request_count": 0,
            "inflight_embedding_request_count": 0,
            "input_text_count": 3,
            "process_start_count": 1,
            "rejected_content_type_count": 0,
            "rejected_json_count": 0,
            "rejected_method_count": 0,
            "rejected_model_count": 0,
            "rejected_non_text_input_count": 1,
            "rejected_path_count": 0,
            "rejected_request_count": 2,
            "rejected_unauthorized_text_count": 1,
            "schema": "anhuan-material-rag-ark-relay-audit-v2",
            "upstream_2xx_count": 0,
            "upstream_4xx_count": 1,
            "upstream_5xx_count": 0,
            "upstream_request_byte_count": 99,
            "upstream_response_byte_count": 0,
        }
        with tempfile.TemporaryDirectory() as raw_dir:
            audit_path = Path(raw_dir) / "audit.json"
            audit_path.write_text(
                json.dumps(audit, separators=(",", ":"), sort_keys=True) + "\n",
                encoding="ascii",
            )
            os.chmod(audit_path, 0o600)
            emit_err = io.StringIO()
            with patch.dict(
                os.environ,
                {"F1_MATERIAL_RAG_EGRESS_AUDIT_FILE": str(audit_path)},
            ):
                with contextlib.redirect_stderr(emit_err):
                    verify._EGRESS_EVIDENCE.emit_for_reason(
                        "LOCAL_MATERIAL_RAG_INDEX_FAILED"
                    )
            emit_lines = [
                item
                for item in emit_err.getvalue().splitlines()
                if item.startswith("LOCAL_MATERIAL_RAG_EGRESS_EVIDENCE ")
            ]
            self.assertEqual(len(emit_lines), 1)
            emitted = json.loads(
                emit_lines[0][len("LOCAL_MATERIAL_RAG_EGRESS_EVIDENCE ") :]
            )
            self.assertEqual(set(emitted), expected_keys)
            self.assertEqual(emitted["audit_status"], "READY")
            self.assertEqual(emitted["authorized_embedding_request_count"], 3)
            self.assertEqual(emitted["rejected_unauthorized_text_count"], 1)
            self.assertEqual(emitted["rejected_json_count"], 0)
            self.assertEqual(emitted["rejected_non_text_input_count"], 1)
            self.assertEqual(emitted["upstream_4xx_count"], 1)
            self.assertNotIn("secret-demo-body", emit_err.getvalue())
            self.assertNotIn("body_sha256", emit_err.getvalue())
            self.assertNotIn("/api/plan/v3", emit_err.getvalue())
            missing_err = io.StringIO()
            with patch.dict(
                os.environ,
                {"F1_MATERIAL_RAG_EGRESS_AUDIT_FILE": str(Path(raw_dir) / "missing.json")},
            ):
                with contextlib.redirect_stderr(missing_err):
                    verify._EGRESS_EVIDENCE.emit_for_reason(
                        "LOCAL_MATERIAL_RAG_INDEX_FAILED"
                    )
            missing_lines = [
                item
                for item in missing_err.getvalue().splitlines()
                if item.startswith("LOCAL_MATERIAL_RAG_EGRESS_EVIDENCE ")
            ]
            self.assertEqual(len(missing_lines), 1)
            missing_payload = json.loads(
                missing_lines[0][len("LOCAL_MATERIAL_RAG_EGRESS_EVIDENCE ") :]
            )
            self.assertEqual(missing_payload["audit_status"], "MISSING")
            self.assertEqual(missing_payload["authorized_embedding_request_count"], 0)
            self.assertNotIn("No such file", missing_err.getvalue())

    def test_ark_relay_allows_benchmark_fake_ip_without_opening_rfc1918(self) -> None:
        source = (
            ROOT / "infra/f1/material-rag/ark_connect_proxy.py"
        ).read_text(encoding="utf-8")
        self.assertIn('ipaddress.ip_network("198.18.0.0/15")', source)
        self.assertIn("def _allowed_upstream_ip(", source)
        self.assertIn("ARK_EGRESS_DNS_REJECTED", source)
        self.assertIn("if not _allowed_upstream_ip(value):", source)
        namespace: dict[str, object] = {}
        exec(
            compile(
                source.replace("COUNTERS = _Counters()", "COUNTERS = None", 1),
                str(ROOT / "infra/f1/material-rag/ark_connect_proxy.py"),
                "exec",
            ),
            namespace,
        )
        allowed = namespace["_allowed_upstream_ip"]
        assert callable(allowed)
        self.assertTrue(allowed(ipaddress.ip_address("8.8.8.8")))
        self.assertTrue(allowed(ipaddress.ip_address("198.18.0.1")))
        self.assertTrue(allowed(ipaddress.ip_address("198.19.255.255")))
        for blocked in (
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.1",
            "127.0.0.1",
            "169.254.169.254",
            "0.0.0.0",
        ):
            self.assertFalse(allowed(ipaddress.ip_address(blocked)), blocked)

    def test_remote_snapshot_reads_pinned_get_chunk_content_field(self) -> None:
        verify = _load_material_rag_verify()
        source = (
            ROOT / "infra/f1/local_material_rag_verify.py"
        ).read_text(encoding="utf-8")
        snapshot_source = source.split("async def _remote_snapshot", 1)[1].split(
            "async def _final_scope_residue", 1
        )[0]
        self.assertIn("_chunk_detail_content(", snapshot_source)
        self.assertIn("_chunk_detail_tags(", snapshot_source)
        helper_source = source.split("def _chunk_detail_content", 1)[1].split(
            "async def _remote_snapshot", 1
        )[0]
        self.assertIn("content_with_weight", helper_source)
        self.assertNotIn("print(", helper_source)
        mapped = verify._chunk_detail_content(
            {"content_with_weight": "canonical-body", "tag_kwd": ["canonical_unit_id=x"]}
        )
        self.assertEqual(mapped, "canonical-body")
        preferred = verify._chunk_detail_content(
            {
                "content": "listed-body",
                "content_with_weight": "canonical-body",
            }
        )
        self.assertEqual(preferred, "listed-body")
        nested = verify._chunk_detail_content(
            {"chunk": {"content_with_weight": "nested-body"}}
        )
        self.assertEqual(nested, "nested-body")
        self.assertIsNone(verify._chunk_detail_content({"id": "chunk-1"}))
        adapter_source = (
            ROOT
            / "src/platform_foundation/f1/features/material_rag/ragflow_adapter.py"
        ).read_text(encoding="utf-8")
        self.assertIn("def _normalize_pinned_chunk(", adapter_source)
        self.assertIn("content_with_weight", adapter_source)
        self.assertIn("RagFlowClient.get_chunk =", adapter_source)
        self.assertIn("RagFlowClient.retrieval =", adapter_source)
        from platform_foundation.f1.features.material_rag import ragflow_adapter

        self.assertEqual(
            ragflow_adapter._pinned_chunk_content(
                {"content_with_weight": "canonical-body"}
            ),
            "canonical-body",
        )
        nested_chunk = ragflow_adapter._normalize_pinned_chunk(
            {"chunk": {"content_with_weight": "nested-body", "tag_kwd": ["a=b"]}}
        )
        self.assertEqual(nested_chunk.get("content"), "nested-body")
        preserved = ragflow_adapter._normalize_pinned_chunk(
            {
                "tag_kwd": None,
                "chunk": {
                    "content_with_weight": "nested-body",
                    "tag_kwd": ["canonical_unit_id=x"],
                },
            }
        )
        self.assertEqual(preserved.get("content"), "nested-body")
        self.assertEqual(preserved.get("tag_kwd"), ["canonical_unit_id=x"])

    def test_scope_unit_db_snapshot_uses_working_api_select_session(self) -> None:
        source = (
            ROOT / "infra/f1/local_material_rag_verify.py"
        ).read_text(encoding="utf-8")
        snapshot_source = source.split("async def _scope_unit_db_snapshot", 1)[1].split(
            "async def _action_job_count", 1
        )[0]
        conflict_source = source.split(
            "async def _prove_unit_identity_conflict_rejected", 1
        )[1].split("def _egress_audit", 1)[0]
        process_jobs_source = source.split("async def _process_jobs", 1)[1].split(
            "def main(", 1
        )[0]
        counts_source = source.split("async def _unit_counts", 1)[1].split(
            "async def _load_version_units", 1
        )[0]
        load_source = source.split("async def _load_version_units", 1)[1].split(
            "def _unit_fingerprint", 1
        )[0]
        signature = snapshot_source.split(":\n", 1)[0]
        self.assertIn("version_ids", signature)
        self.assertIn("tuple(sorted(set(version_ids)))", snapshot_source)
        self.assertIn("load_units_for_version", snapshot_source)
        self.assertNotIn("SELECT DISTINCT", snapshot_source)
        self.assertNotIn("FROM f1.material_rag_unit", snapshot_source)
        self.assertNotIn("document_version_id FROM", snapshot_source)
        self.assertNotIn("EMPLOYEE_SUB", snapshot_source)
        self.assertNotIn("body_ciphertext", snapshot_source)
        self.assertIn("local_seed.ADMIN_SUB", snapshot_source)
        self.assertIn('role="f1_api"', snapshot_source)
        self.assertIn('_enter_internal_operation("DB_SNAPSHOT_OPEN")', snapshot_source)
        self.assertIn('_enter_internal_operation("DB_SNAPSHOT_LOAD")', snapshot_source)
        self.assertIn('_enter_internal_operation("DB_SNAPSHOT_EXIT")', snapshot_source)
        self.assertIn(
            'operation_token = _enter_internal_operation("DB_SNAPSHOT_OPEN")',
            snapshot_source,
        )
        self.assertIn("_INTERNAL_OPERATION.reset(operation_token)", snapshot_source)
        self.assertIn("finally:", snapshot_source)
        op_source = source.split("def _enter_internal_operation", 1)[1].split(
            "\n\n", 1
        )[0]
        self.assertIn("return _INTERNAL_OPERATION.set(", op_source)
        self.assertIn('_fail_index("SNAPSHOT_OPEN")', snapshot_source)
        self.assertIn('_fail_index("SNAPSHOT_LOAD")', snapshot_source)
        self.assertIn('_fail_index("SNAPSHOT_EXIT")', snapshot_source)
        conflict_signature = conflict_source.split(")", 1)[0]
        self.assertIn("claim", conflict_signature)
        self.assertIn("version_ids", conflict_signature)
        self.assertNotIn("scope_id: uuid.UUID", conflict_signature)
        self.assertIn(
            "_scope_unit_db_snapshot(scope_id, version_ids)",
            conflict_source,
        )
        self.assertIn('_enter_internal_operation("PERSIST_UNITS")', conflict_source)
        self.assertIn("live_scope_job_lock", conflict_source)
        self.assertIn("live_source_mutation_fence", conflict_source)
        self.assertIn("claimed_session", conflict_source)
        self.assertIn("MATERIAL_UNIT_IDENTITY_CONFLICT", conflict_source)
        self.assertIn("await session.rollback()", conflict_source)
        self.assertNotIn('role="f1_api"', conflict_source)
        self.assertNotIn("session_scope", conflict_source)
        self.assertNotIn("EMPLOYEE_SUB", conflict_source)
        self.assertIn("tuple(sorted(persisted_by_version))", process_jobs_source)
        self.assertGreaterEqual(
            process_jobs_source.count("_scope_unit_db_snapshot("),
            2,
        )
        self.assertIn(
            "_scope_unit_db_snapshot(setup.client_a_scope_id, known_versions)",
            process_jobs_source,
        )
        replay_source = process_jobs_source.split("PJ_INDEX_REPLAY", 1)[1].split(
            "PJ_CONTEXT_GUARDS", 1
        )[0]
        self.assertIn("claim_demo_job(", replay_source)
        self.assertIn("process_claimed_demo_job(", replay_source)
        self.assertNotIn("process_demo_job(", replay_source)
        self.assertEqual(replay_source.count("enqueue_job("), 1)
        self.assertIn("index_replay_job_count == 0", replay_source)
        probe_at = replay_source.find("_prove_unit_identity_conflict_rejected(")
        claimed_at = replay_source.find("process_claimed_demo_job(")
        self.assertGreater(probe_at, 0)
        self.assertGreater(claimed_at, probe_at)
        after_counts = replay_source.split('REPLAY_COUNTS', 1)[1]
        self.assertNotIn("_prove_unit_identity_conflict_rejected(", after_counts)
        self.assertIn("count(*)", counts_source)
        self.assertIn("count(DISTINCT id)", counts_source)
        self.assertIn("PRIMARY_ATTEST_COUNTS", process_jobs_source)
        self.assertIn("REPLAY_COUNTS", process_jobs_source)
        self.assertIn("local_seed.ADMIN_SUB", load_source)
        loop_src = snapshot_source.split("for version_id in known:", 1)[1]
        before_loop = snapshot_source.split("for version_id in known:", 1)[0]
        self.assertIn("async with session_scope(", loop_src)
        self.assertNotIn("async with session_scope(", before_loop)
        self.assertIn("await session.rollback()", loop_src)
        self.assertIn("_unwrap_internal_errors(", snapshot_source)
        self.assertIn("_classify_db_error(", snapshot_source)
        localctl = (ROOT / "scripts/localctl").read_text(encoding="utf-8")
        verify_source = localctl.split("def _material_rag_verify", 1)[1]
        build_at = verify_source.find('"material-rag-migrator"')
        prove_at = verify_source.find("_prove_material_rag_image_source(")
        secret_at = verify_source.find("material-rag-secret-init")
        self.assertTrue(0 < build_at < prove_at < secret_at)
        image_source = localctl.split(
            "def _prove_material_rag_image_source", 1
        )[1].split("\ndef ", 1)[0]
        self.assertIn("LOCAL_MATERIAL_RAG_IMAGE_SOURCE_EVIDENCE", image_source)
        self.assertIn("network", image_source)
        self.assertIn("none", image_source)
        self.assertIn("65532", image_source)
        self.assertIn("read-only", image_source)
        self.assertIn('"MATCH"', image_source)
        self.assertIn("MISMATCH", image_source)

    def test_clamd_diag_compose_stays_offline_and_matches_runtime_clamd(self) -> None:
        compose = (
            ROOT / "infra/f1/docker-compose.material-rag.yml"
        ).read_text(encoding="utf-8")
        diag = (
            ROOT / "infra/f1/docker-compose.material-rag-clamd-diag.yml"
        ).read_text(encoding="utf-8")
        localctl = (ROOT / "scripts/localctl").read_text(encoding="utf-8")
        self.assertIn("material-rag-diagnose-clamd", localctl)
        self.assertIn("docker-compose.material-rag-clamd-diag.yml", localctl)
        self.assertIn("material-rag-clamd-probe", localctl)
        clamd_image = (
            "clamav/clamav:1.4.6-debian13-slim@sha256:"
            "aaf6efb85740dc60872e2c13e5b7778c2d57b05b960f854a2461eaf729250d18"
        )
        self.assertIn(clamd_image, compose)
        self.assertIn(clamd_image, diag)
        self.assertIn("mem_limit: 1073741824", diag)
        self.assertIn("00000000:0CEE", diag)
        self.assertIn("retries: 3", diag)
        self.assertIn('s|exec tail -f \\"/dev/null\\"|wait|', diag)
        self.assertIn("F1_CLAMD_HOST: material-rag-clamd", diag)
        self.assertIn("material-rag-clamd-probe", diag)
        for forbidden in (
            "material-rag-ragflow",
            "material-rag-provider",
            "ARK",
            "ark.",
            "demo/",
            "LOCAL_MATERIAL_RAG_DEMO",
        ):
            self.assertNotIn(forbidden, diag)
        self.assertNotIn("material-rag-verify", diag)

    def test_adapter_never_falls_back_to_enterprise_dataset(self) -> None:
        provision = (
            ROOT / "src/platform_foundation/f1/ragflow_provision.py"
        ).read_text(encoding="utf-8")
        worker = (
            ROOT / "src/platform_foundation/f1/features/material_rag/worker.py"
        ).read_text(encoding="utf-8")
        self.assertIn("dataset_for_material_scope", worker)
        self.assertNotIn("dataset_for_enterprise", worker)
        material_function = provision.split("def dataset_for_material_scope", 1)[1]
        self.assertNotIn("dataset_for_enterprise(", material_function)


def _load_provider_provision():
    for name in ("Cryptodome", "Cryptodome.Cipher", "Cryptodome.PublicKey"):
        sys.modules.setdefault(name, MagicMock())
    path = ROOT / "infra/f1/material-rag/provider_provision.py"
    spec = importlib.util.spec_from_file_location(
        "material_rag_provider_provision", path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class MaterialRagProviderIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.provision = _load_provider_provision()

    def test_login_identity_does_not_swallow_ragflow_unavailable(self) -> None:
        calls: list[str] = []

        def fake_request(method: str, path: str, **kwargs: object):
            calls.append(path)
            raise self.provision.ProvisionError("MATERIAL_RAG_RAGFLOW_UNAVAILABLE")

        with patch.object(self.provision, "_request", side_effect=fake_request):
            with self.assertRaisesRegex(
                self.provision.ProvisionError, "MATERIAL_RAG_RAGFLOW_UNAVAILABLE"
            ):
                self.provision._register_user("encrypted")
        self.assertEqual(calls, ["/api/v1/users"])

    def test_register_user_does_not_swallow_request_failure(self) -> None:
        calls: list[str] = []

        def fake_request(method: str, path: str, **kwargs: object):
            calls.append(path)
            raise self.provision.ProvisionError("MATERIAL_RAG_RAGFLOW_REQUEST_FAILED")

        with patch.object(self.provision, "_request", side_effect=fake_request):
            with self.assertRaisesRegex(
                self.provision.ProvisionError, "MATERIAL_RAG_RAGFLOW_REQUEST_FAILED"
            ):
                self.provision._register_user("encrypted")
        self.assertEqual(calls, ["/api/v1/users"])

    def test_login_identity_reuses_session_when_authorization_header_absent(
        self,
    ) -> None:
        user_id = "a" * 32
        api_token = "ragflow-" + ("B" * 32)
        login_hits = {"count": 0}
        token_hits = {"cookie": ""}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                if self.path == "/api/v1/users":
                    body = json.dumps(
                        {
                            "code": 0,
                            "message": "welcome",
                            "data": {"id": user_id, "email": "x@invalid.local"},
                        }
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path != "/api/v1/auth/login":
                    self.send_error(404)
                    return
                login_hits["count"] += 1
                body = json.dumps(
                    {
                        "code": 0,
                        "message": "Welcome back!",
                        "data": {"id": user_id, "email": "x@invalid.local"},
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", "session=material-rag-session; Path=/")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path != "/api/v1/system/tokens":
                    self.send_error(404)
                    return
                token_hits["cookie"] = self.headers.get("Cookie") or ""
                if "material-rag-session" not in token_hits["cookie"]:
                    body = json.dumps({"code": 401, "message": "Unauthorized", "data": None}).encode()
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                payload = {
                    "code": 0,
                    "data": [
                        {
                            "token": api_token,
                            "tenant_id": user_id,
                            "dialog_id": None,
                            "source": None,
                        }
                    ],
                }
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{port}"
        original_base = self.provision.BASE_URL
        original_expected = self.provision.EXPECTED_RAGFLOW_BASE_URL
        try:
            self.provision.BASE_URL = base
            self.provision.EXPECTED_RAGFLOW_BASE_URL = base
            if hasattr(self.provision, "_reset_http_client"):
                self.provision._reset_http_client()
            self.provision._register_user("encrypted")
            login_token, returned_id = self.provision._login_identity("encrypted")
            token, tenant_id = self.provision._api_identity(login_token, returned_id)
        finally:
            self.provision.BASE_URL = original_base
            self.provision.EXPECTED_RAGFLOW_BASE_URL = original_expected
            if hasattr(self.provision, "_reset_http_client"):
                self.provision._reset_http_client()
            server.shutdown()
            server.server_close()
        self.assertEqual(login_hits["count"], 1)
        self.assertIn("material-rag-session", token_hits["cookie"])
        self.assertEqual((token, tenant_id), (api_token, user_id))

    def test_wait_for_ragflow_api_retries_until_register_enabled(self) -> None:
        calls: list[str] = []

        def fake_config() -> dict[str, object]:
            calls.append("/api/v1/system/config")
            if len(calls) < 3:
                raise self.provision.ProvisionError("MATERIAL_RAG_RAGFLOW_UNAVAILABLE")
            return {
                "code": 0,
                "data": {
                    "registerEnabled": 1,
                    "disablePasswordLogin": False,
                },
            }

        with patch.object(self.provision, "_system_config", side_effect=fake_config):
            with patch.object(self.provision.time, "sleep"):
                with patch.object(
                    self.provision.time, "monotonic", side_effect=[0.0, 1.0, 2.0, 3.0]
                ):
                    self.provision._wait_for_ragflow_api()
        self.assertEqual(calls, ["/api/v1/system/config"] * 3)

    def test_wait_for_ragflow_api_fails_fast_when_registration_disabled(
        self,
    ) -> None:
        calls: list[str] = []

        def fake_config() -> dict[str, object]:
            calls.append("/api/v1/system/config")
            return {
                "code": 0,
                "data": {
                    "registerEnabled": 0,
                    "disablePasswordLogin": True,
                },
            }

        with patch.object(self.provision, "_system_config", side_effect=fake_config):
            with self.assertRaisesRegex(
                self.provision.ProvisionError, "MATERIAL_RAG_REGISTER_DISABLED"
            ):
                self.provision._wait_for_ragflow_api()
        self.assertEqual(calls, ["/api/v1/system/config"])

    def test_scanner_ignores_link_local_and_prefers_private_ipv4(self) -> None:
        _ensure_p3_namespace()
        from platform_foundation.f1.features.p3 import scanner

        fake = [
            (0, 0, 0, "", ("fe80::1", 3310)),
            (0, 0, 0, "", ("172.18.0.8", 3310)),
        ]
        with patch.object(scanner.socket, "getaddrinfo", return_value=fake):
            self.assertEqual(
                scanner._resolve_target("material-rag-clamd", 3310, 5),
                "172.18.0.8",
            )
        public = [
            (0, 0, 0, "", ("8.8.8.8", 3310)),
            (0, 0, 0, "", ("172.18.0.8", 3310)),
        ]
        with patch.object(scanner.socket, "getaddrinfo", return_value=public):
            with self.assertRaisesRegex(scanner.ScanFailure, "P3_SCANNER_TARGET_INVALID"):
                scanner._resolve_target("material-rag-clamd", 3310, 5)


def _load_localctl():
    path = ROOT / "scripts/localctl"
    loader = importlib.machinery.SourceFileLoader(
        "anhuan_material_rag_localctl", str(path)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_material_rag_verify():
    path = ROOT / "infra/f1/local_material_rag_verify.py"
    name = "anhuan_material_rag_verify_static"
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class MaterialRagProviderReadyDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.localctl = _load_localctl()
        cls.provision = _load_provider_provision()

    def test_normal_provider_evidence_is_reprinted_from_stderr(self) -> None:
        evidence = {
            "attempt_count": 3,
            "curl_code": 22,
            "curl_exit_class": "HTTP",
            "elapsed_class": "S5_30",
            "endpoint": "SYSTEM_CONFIG",
            "phase": "READY",
            "response_size_class": "EMPTY",
        }
        line = (
            "LOCAL_MATERIAL_RAG_PROVIDER_EVIDENCE "
            + json.dumps(evidence, separators=(",", ":"), sort_keys=True)
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            reason = self.localctl._emit_material_rag_provider_diagnostics(
                "",
                line + "\nLOCAL_MATERIAL_RAG_PROVIDER_REQUEST_FAILED\n",
                fallback_reason="LOCAL_MATERIAL_RAG_PROVIDER_PROVISION_FAILED",
            )
        printed = stderr.getvalue()
        self.assertEqual(reason, "LOCAL_MATERIAL_RAG_PROVIDER_REQUEST_FAILED")
        self.assertIn(line, printed.splitlines())
        self.assertNotIn("http://", printed)
        self.assertNotIn("Authorization", printed)

    def test_reason_on_stdout_survives_stderr_over_8192_bytes(self) -> None:
        evidence = {
            "attempt_count": 1,
            "curl_code": 7,
            "curl_exit_class": "CONNECT",
            "elapsed_class": "LT5S",
            "endpoint": "SYSTEM_CONFIG",
            "phase": "READY",
            "response_size_class": "EMPTY",
        }
        evidence_line = (
            "LOCAL_MATERIAL_RAG_PROVIDER_EVIDENCE "
            + json.dumps(evidence, separators=(",", ":"), sort_keys=True)
        )
        padding = "PAD" * 3000
        stderr_text = padding + "\n" + evidence_line + "\n"
        stdout_text = "LOCAL_MATERIAL_RAG_PROVIDER_REQUEST_FAILED\n"
        self.assertGreater(len(stderr_text.encode("utf-8")), 8192)
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            reason = self.localctl._emit_material_rag_provider_diagnostics(
                stdout_text,
                stderr_text,
                fallback_reason="LOCAL_MATERIAL_RAG_PROVIDER_PROVISION_FAILED",
            )
        printed = captured.getvalue()
        self.assertEqual(reason, "LOCAL_MATERIAL_RAG_PROVIDER_REQUEST_FAILED")
        self.assertIn(evidence_line, printed.splitlines())
        self.assertNotIn("PAD", printed)

    def test_duplicate_and_malformed_provider_evidence_degrade_without_leak(self) -> None:
        valid = {
            "attempt_count": 2,
            "curl_code": 22,
            "curl_exit_class": "HTTP",
            "elapsed_class": "LT5S",
            "endpoint": "SYSTEM_CONFIG",
            "phase": "READY",
            "response_size_class": "SMALL",
        }
        valid_line = (
            "LOCAL_MATERIAL_RAG_PROVIDER_EVIDENCE "
            + json.dumps(valid, separators=(",", ":"), sort_keys=True)
        )
        other = dict(valid)
        other["attempt_count"] = 4
        other_line = (
            "LOCAL_MATERIAL_RAG_PROVIDER_EVIDENCE "
            + json.dumps(other, separators=(",", ":"), sort_keys=True)
        )
        secret = "leak-token-must-not-print"
        malformed = "LOCAL_MATERIAL_RAG_PROVIDER_EVIDENCE {" + secret + "}"
        duplicate_stderr = io.StringIO()
        with contextlib.redirect_stderr(duplicate_stderr):
            duplicate_reason = self.localctl._emit_material_rag_provider_diagnostics(
                "",
                valid_line + "\n" + other_line + "\nLOCAL_MATERIAL_RAG_PROVIDER_REQUEST_FAILED\n",
                fallback_reason="LOCAL_MATERIAL_RAG_PROVIDER_PROVISION_FAILED",
            )
        malformed_stderr = io.StringIO()
        with contextlib.redirect_stderr(malformed_stderr):
            malformed_reason = self.localctl._emit_material_rag_provider_diagnostics(
                "",
                malformed + "\nLOCAL_MATERIAL_RAG_PROVIDER_REQUEST_FAILED\n",
                fallback_reason="LOCAL_MATERIAL_RAG_PROVIDER_PROVISION_FAILED",
            )
        self.assertEqual(duplicate_reason, "LOCAL_MATERIAL_RAG_PROVIDER_REQUEST_FAILED")
        self.assertEqual(malformed_reason, "LOCAL_MATERIAL_RAG_PROVIDER_REQUEST_FAILED")
        self.assertEqual(
            duplicate_stderr.getvalue().splitlines(),
            ["LOCAL_MATERIAL_RAG_PROVIDER_EVIDENCE_DEGRADED DUPLICATE"],
        )
        self.assertEqual(
            malformed_stderr.getvalue().splitlines(),
            ["LOCAL_MATERIAL_RAG_PROVIDER_EVIDENCE_DEGRADED MALFORMED"],
        )
        self.assertNotIn(secret, malformed_stderr.getvalue())
        self.assertNotIn(valid_line, duplicate_stderr.getvalue())

    def test_system_config_http_not_ok_classifies_curl_22(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["curl"], returncode=22, stdout=b"", stderr=b""
        )
        with patch.object(self.provision, "BASE_URL", "http://127.0.0.1:80"):
            with patch.object(self.provision.subprocess, "run", return_value=completed):
                document, evidence, error = self.provision._system_config_attempt(
                    attempt_count=1, elapsed_s=1.0
                )
        self.assertIsNone(document)
        self.assertEqual(error, "MATERIAL_RAG_RAGFLOW_REQUEST_FAILED")
        self.assertEqual(evidence["phase"], "READY")
        self.assertEqual(evidence["endpoint"], "SYSTEM_CONFIG")
        self.assertEqual(evidence["curl_code"], 22)
        self.assertEqual(evidence["curl_exit_class"], "HTTP")
        self.assertEqual(evidence["response_size_class"], "EMPTY")


class MaterialRagOrchestrationContractTests(unittest.TestCase):
    def test_f1_0016_claim_next_is_invoker_skip_locked_and_not_broad(self) -> None:
        path = ROOT / "infra/f1/alembic/versions/f1_0016_material_rag_orchestration.py"
        source = path.read_text(encoding="utf-8")
        self.assertIn('revision: str = "f1_0016"', source)
        self.assertIn('down_revision: str | None = "f1_0015"', source)
        fn = source.split("CREATE FUNCTION f1.claim_next_material_rag_job", 1)[1].split(
            "CREATE POLICY", 1
        )[0]
        self.assertIn("SECURITY INVOKER", fn)
        self.assertNotIn("SECURITY DEFINER", fn)
        self.assertIn("SET search_path = pg_catalog", fn)
        self.assertIn("FOR UPDATE OF job SKIP LOCKED", fn)
        self.assertIn("session_user <> 'f1_worker'", fn)
        self.assertIn("LIMIT 1", fn)
        self.assertNotIn("BYPASSRLS", source)
        self.assertNotIn("USING (true)", source)
        self.assertNotIn("USING(true)", source.replace(" ", ""))
        self.assertNotIn("session_replication_role", source)
        self.assertIn(
            "GRANT EXECUTE ON FUNCTION f1.claim_next_material_rag_job(text,integer) TO f1_worker",
            source,
        )
        self.assertIn(
            "REVOKE ALL ON FUNCTION f1.claim_next_material_rag_job(text,integer) FROM PUBLIC",
            source,
        )
        self.assertNotIn("TO f1_api", source)
        self.assertIn("material_rag_job_worker_due_select", source)
        self.assertIn("material_rag_job_worker_due_update", source)
        self.assertIn("FOR UPDATE TO f1_worker", source)
        self.assertNotIn("CREATE TABLE", source)

    def test_migrate_closed_set_keeps_default_0014_and_rag_0016(self) -> None:
        migrate = (ROOT / "infra/f1/migrate_f1.py").read_text(encoding="utf-8")
        self.assertIn('F1_DEFAULT_MIGRATE_TARGET = "f1_0014"', migrate)
        self.assertIn('F1_MATERIAL_RAG_MIGRATE_TARGET = "f1_0016"', migrate)
        self.assertIn('"f1_0014"', migrate)
        self.assertIn('"f1_0015"', migrate)
        self.assertIn('"f1_0016"', migrate)
        self.assertIn("type(target) is not str", migrate)

    def test_p3_release_enqueues_in_same_session_only_under_exact_dual_switch(self) -> None:
        p3 = (ROOT / "src/platform_foundation/f1/features/p3/service.py").read_text(
            encoding="utf-8"
        )
        release = p3.split("async def act_on_version", 1)[1]
        self.assertIn("enqueue_job_in_session", release)
        self.assertLess(
            release.index("enqueue_job_in_session"),
            release.index("await session.commit()"),
        )
        self.assertIn('os.environ.get("F1_MATERIAL_RAG_ORCHESTRATION_LOCAL") == "1"', p3)
        self.assertIn('os.environ.get("F1_LOCAL_ENGINEERING") == "1"', p3)
        self.assertNotIn('os.environ.get("F1_MATERIAL_RAG_ORCHESTRATION_LOCAL") in', p3)

    def test_orchestrator_run_once_has_no_public_manifest_or_physical_ids(self) -> None:
        import inspect

        from platform_foundation.f1.features.material_rag.orchestrator import run_once

        names = tuple(inspect.signature(run_once).parameters)
        self.assertEqual(names, ("worker_id", "lease_seconds"))
        source = inspect.getsource(run_once)
        for forbidden in (
            "manifest_key",
            "dataset_id",
            "chunk_id",
            "units=",
            "manifest_proof=",
            "body=",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("claim_next_job", source)
        orch = (
            ROOT / "src/platform_foundation/f1/features/material_rag/orchestrator.py"
        ).read_text(encoding="utf-8")
        self.assertIn("process_claimed_demo_job", orch)
        self.assertIn("def orchestration_enabled", orch)
        self.assertIn('os.environ.get(ORCH_FLAG) == "1"', orch)
        self.assertNotIn("ark", orch.lower())

    def test_enqueue_requires_current_released_version(self) -> None:
        repository = (
            ROOT / "src/platform_foundation/f1/features/material_rag/repository.py"
        ).read_text(encoding="utf-8")
        fn = repository.split("async def enqueue_job_in_session", 1)[1].split(
            "async def enqueue_job(", 1
        )[0]
        self.assertIn("version.version_no = record.latest_version_no", fn)
        self.assertIn("MATERIAL_VERSION_NOT_CURRENT", fn)
        self.assertIn("MATERIAL_VERSION_NOT_INDEXABLE", fn)
        self.assertNotIn("await session.commit()", fn)


if __name__ == "__main__":
    unittest.main()
