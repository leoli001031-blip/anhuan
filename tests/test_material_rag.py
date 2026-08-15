from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import importlib.machinery
import importlib.util
import io
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


if __name__ == "__main__":
    unittest.main()
