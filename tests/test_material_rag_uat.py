"""Offline machine gate for local closed-query material-RAG UAT.

No Ark, RAGFlow, PostgreSQL, shared stack, or free-text egress.  Public
``/material-qa`` remains fail-closed.  The local UAT surface exists only when
``F1_MATERIAL_RAG_UAT_LOCAL=1``.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PUBLIC_QA_ROUTER = (
    ROOT / "src/platform_foundation/f1/api/routers/material_qa.py"
)
MAIN_APP = ROOT / "src/platform_foundation/f1/api/main.py"
UAT_BROWSER_GATE = (
    ROOT / "src/web/src/features/material-rag/uatBrowserGate.mjs"
)
FORBIDDEN_PUBLIC_KEYS = frozenset(
    {
        "dataset_id",
        "chunk_id",
        "knowledge_scope_id",
        "scope_ids",
        "ragflow_id",
        "ragflow_dataset_id",
        "ragflow_chunk_id",
    }
)
LEAK_TOKENS = (
    "ds_must_not_leak",
    "chunk_must_not_leak",
    "scope_must_not_leak",
)


def _sha(label: str) -> str:
    return hashlib.sha256(f"UAT_SYNTH|{label}".encode("utf-8")).hexdigest()


def _walk_forbidden(payload: object) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).lower() in FORBIDDEN_PUBLIC_KEYS:
                raise AssertionError(f"physical_id_key:{key}")
            _walk_forbidden(value)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            _walk_forbidden(item)
    elif isinstance(payload, str):
        lowered = payload.lower()
        for token in LEAK_TOKENS:
            if token in lowered:
                raise AssertionError(f"physical_id_token:{token}")


class PublicFreeformStillClosedTests(unittest.TestCase):
    def test_public_freeform_query_fails_before_database_or_network(self) -> None:
        import asyncio

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
            asyncio.run(
                run_verified_retrieval("arbitrary question", tenant, context)
            )

    def test_public_claim_still_refuses_without_retrieval(self) -> None:
        import asyncio

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
        retrieval = AsyncMock()
        with (
            patch.object(
                qa_service, "reserve_request", AsyncMock(return_value=reservation)
            ),
            patch.object(qa_service, "complete_request", AsyncMock()),
            patch.object(rag_service, "run_verified_retrieval", retrieval),
        ):
            outcome = asyncio.run(
                qa_service.ask_material_question(
                    "公开自由问题不得外发", request_id, tenant, context
                )
            )
        self.assertIsNone(outcome.answer)
        self.assertEqual(outcome.citations, [])
        self.assertEqual(
            outcome.refusal_reason,
            "MATERIAL_QUERY_EXTERNAL_PROCESSING_NOT_AUTHORIZED",
        )
        retrieval.assert_not_awaited()

    def test_public_router_still_requires_question_and_forbids_query_id(self) -> None:
        from pydantic import ValidationError
        from platform_foundation.f1.api.routers.material_qa import MaterialQaRequest

        with self.assertRaises(ValidationError):
            MaterialQaRequest.model_validate(
                {
                    "query_id": "provider.shared",
                    "request_id": "32000000-0000-4000-8000-000000000001",
                }
            )
        with self.assertRaises(ValidationError):
            MaterialQaRequest.model_validate(
                {
                    "question": "x",
                    "request_id": "32000000-0000-4000-8000-000000000001",
                    "query_id": "provider.shared",
                }
            )
        source = PUBLIC_QA_ROUTER.read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertIn("question", source)
        self.assertNotIn("query_id", source)
        self.assertNotIn("F1_MATERIAL_RAG_UAT_LOCAL", source)
        self.assertTrue(
            any(
                isinstance(node, ast.AsyncFunctionDef)
                and node.name == "ask_material_question"
                for node in ast.walk(tree)
            )
        )

    def test_main_calls_single_mount_helper_and_requires_both_flags(self) -> None:
        source = MAIN_APP.read_text(encoding="utf-8")
        self.assertIn("mount_if_enabled(app)", source)
        self.assertEqual(source.count("mount_if_enabled"), 1)
        self.assertNotIn("material_qa_uat.router", source)
        router = (
            ROOT / "src/platform_foundation/f1/api/routers/material_qa_uat.py"
        ).read_text(encoding="utf-8")
        self.assertIn("tenant_from_header", router)
        self.assertIn("require_role", router)
        self.assertIn("F1_LOCAL_ENGINEERING", router)
        self.assertNotIn("x_uat_actor", router)
        self.assertNotIn("X-Uat-Actor", router)
        self.assertNotIn("CLOSED_ACTORS", router)

    def test_frontend_does_not_send_uat_actor_and_crm_button_is_gated(self) -> None:
        api = (ROOT / "src/web/src/features/material-rag/api.ts").read_text(
            encoding="utf-8"
        )
        panel = (
            ROOT / "src/web/src/features/material-rag/MaterialQaPanel.tsx"
        ).read_text(encoding="utf-8")
        crm = (
            ROOT / "src/web/src/features/p4/pages/CrmAccountDetailPage.tsx"
        ).read_text(encoding="utf-8")
        dockerfile = (ROOT / "infra/f1/web.Dockerfile").read_text(encoding="utf-8")
        overlay = (
            ROOT / "infra/f1/docker-compose.material-rag-uat.yml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("X-Uat-Actor", api)
        self.assertNotIn("x-uat-actor", api.lower())
        self.assertNotIn("X-Uat-Actor", panel)
        self.assertIn("localUatRuntimeEnabled", crm)
        self.assertIn("本地合成", crm)
        self.assertIn("ARG VITE_MATERIAL_RAG_UAT_LOCAL", dockerfile)
        self.assertIn("F1_MATERIAL_RAG_UAT_LOCAL", overlay)
        self.assertIn("F1_LOCAL_ENGINEERING", overlay)
        self.assertIn("VITE_MATERIAL_RAG_UAT_LOCAL", overlay)
        self.assertIn('F1_EXTERNAL_PIPELINES_ENABLED: "false"', overlay)
        self.assertNotIn("ark", overlay.lower())
        self.assertIn("io.anhuan.scope: material-rag-uat", overlay)
        self.assertIn("LOCAL_PROJECT_ID:?LOCAL_PROJECT_ID_REQUIRED", overlay)
        for name in (
            "secret-init",
            "postgres",
            "keycloak",
            "minio",
            "redis",
            "clamd",
            "api",
            "worker",
            "dispatcher",
            "web",
            "postgres_data",
            "localnet",
        ):
            self.assertIn(f"{name}:", overlay)
        localctl = (ROOT / "scripts/localctl").read_text(encoding="utf-8")
        self.assertIn('subparsers.add_parser("material-rag-uat-start")', localctl)
        self.assertIn('subparsers.add_parser("material-rag-uat-check")', localctl)
        self.assertIn('subparsers.add_parser("material-rag-uat-stop")', localctl)
        self.assertIn('subparsers.add_parser("material-rag-uat-open")', localctl)
        self.assertIn(
            'BROWSER_STAGES = frozenset(\n    {"all", "business", "faults", "pwa-update", "pwa-os"}\n)',
            localctl,
        )
        start_fn = localctl.split("def _material_rag_uat_start", 1)[1].split(
            "def _material_rag_uat_stop", 1
        )[0]
        stop_fn = localctl.split("def _material_rag_uat_stop", 1)[1].split(
            "def _material_rag_uat_read_secret", 1
        )[0]
        command_fn = localctl.split("def _material_rag_uat_command", 1)[1].split(
            "def _parser", 1
        )[0]
        self.assertIn("_material_rag_uat_assert_mutation_safe", start_fn)
        self.assertIn("_material_rag_uat_assert_mutation_safe", stop_fn)
        self.assertNotIn("check=False", stop_fn)
        self.assertNotIn("rmi", stop_fn)
        self.assertIn("finally", command_fn)
        self.assertIn("HUMAN_UAT_URL", localctl)
        self.assertIn("human_uat_url_ready", localctl)
        self.assertIn("resource_identity_verified", localctl)
        self.assertIn("shared_identity_unchanged", localctl)
        self.assertIn("valid_tenant_count", localctl)
        browser_fn = localctl.split("def _material_rag_uat_browser(", 1)[1].split(
            "def _material_rag_uat_print_url", 1
        )[0]
        self.assertIn('line.startswith("LOCAL_BROWSER_VERIFY_FAILED ")', browser_fn)
        evidence_fn = localctl.split("_UAT_BROWSER_EVIDENCE_KEYS", 1)[1].split(
            "def _material_rag_uat_browser(", 1
        )[0]
        self.assertIn('"journey"', evidence_fn)
        self.assertIn('"expected_phase"', evidence_fn)
        self.assertIn('"actual_phase"', evidence_fn)
        self.assertIn('"request_seen"', evidence_fn)
        self.assertIn('"http_status"', evidence_fn)
        self.assertIn('"action_stage"', evidence_fn)
        self.assertIn("LOCAL_MATERIAL_RAG_UAT_BROWSER_EVIDENCE_INVALID", evidence_fn)
        remove_fn = localctl.split(
            "def _material_rag_uat_remove_control_dir", 1
        )[1].split("def _material_rag_uat_stop", 1)[0]
        self.assertIn(
            "_secure_file(MATERIAL_RAG_UAT_LOCK_FILE, minimum=0, maximum=65536)",
            remove_fn,
        )


class LocalUatDisabledTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("F1_MATERIAL_RAG_UAT_LOCAL", None)
        os.environ.pop("F1_LOCAL_ENGINEERING", None)

    def test_local_adapter_refuses_before_store_without_flag(self) -> None:
        from platform_foundation.f1.features.material_rag.uat_local import (
            ENTERPRISE_A,
            ask,
            local_uat_enabled,
            reset_store,
            store_mutation_count,
        )

        reset_store()
        self.assertFalse(local_uat_enabled())
        with self.assertRaisesRegex(RuntimeError, "MATERIAL_RAG_UAT_LOCAL_DISABLED"):
            ask(
                query_id="provider.shared",
                request_id=uuid.uuid4(),
                enterprise_id=ENTERPRISE_A,
                client_account_id=None,
            )
        self.assertEqual(store_mutation_count(), 0)

    def test_http_surface_is_404_without_flag(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from platform_foundation.f1.api.routers import material_qa_uat

        app = FastAPI()
        material_qa_uat.mount_if_enabled(app)
        client = TestClient(app)
        response = client.post(
            "/api/v1/local-uat/material-qa",
            json={
                "query_id": "provider.shared",
                "request_id": str(uuid.uuid4()),
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_uat_flag_alone_does_not_mount(self) -> None:
        os.environ["F1_MATERIAL_RAG_UAT_LOCAL"] = "1"
        os.environ.pop("F1_LOCAL_ENGINEERING", None)
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from platform_foundation.f1.api.routers import material_qa_uat

        app = FastAPI()
        self.assertFalse(material_qa_uat.mount_if_enabled(app))
        client = TestClient(app)
        response = client.post(
            "/api/v1/local-uat/material-qa",
            json={
                "query_id": "provider.shared",
                "request_id": str(uuid.uuid4()),
            },
        )
        self.assertEqual(response.status_code, 404)
        os.environ.pop("F1_MATERIAL_RAG_UAT_LOCAL", None)


class LocalUatJourneyTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["F1_MATERIAL_RAG_UAT_LOCAL"] = "1"
        os.environ["F1_LOCAL_ENGINEERING"] = "1"
        import platform_foundation.f1.features.material_rag.uat_local as uat_mod

        uat_mod.reset_store()
        self.uat = uat_mod

    def tearDown(self) -> None:
        self.uat.reset_store()
        os.environ.pop("F1_MATERIAL_RAG_UAT_LOCAL", None)
        os.environ.pop("F1_LOCAL_ENGINEERING", None)

    def test_unknown_query_id_fails_closed_before_store(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "QUERY_ID_NOT_AUTHORIZED"):
            self.uat.ask(
                query_id="freeform please retrieve this",
                request_id=uuid.uuid4(),
                enterprise_id=self.uat.ENTERPRISE_A,
                client_account_id=None,
            )
        self.assertEqual(self.uat.store_mutation_count(), 0)

    def test_journey_provider_shared_hits_only_provider(self) -> None:
        result = self.uat.ask(
            query_id="provider.shared",
            request_id=uuid.UUID("51000000-0000-4000-8000-000000000001"),
            enterprise_id=self.uat.ENTERPRISE_A,
            client_account_id=None,
        )
        payload = result.to_public_dict()
        _walk_forbidden(payload)
        self.assertEqual(result.http_status, 200)
        self.assertEqual(result.refusal_reason, None)
        kinds = {item.scope_kind for item in result.citations}
        self.assertEqual(kinds, {"service_provider"})
        self.assertEqual(
            [item.document_record_id for item in result.citations],
            [self.uat.PROVIDER_DOCUMENT_RECORD_ID],
        )
        self.assertIn("SYNTH_PROVIDER", result.answer or "")

    def test_journey_client_current_stays_in_client_a(self) -> None:
        result = self.uat.ask(
            query_id="client.current",
            request_id=uuid.UUID("51000000-0000-4000-8000-000000000002"),
            enterprise_id=self.uat.ENTERPRISE_A,
            client_account_id=self.uat.CLIENT_A,
        )
        _walk_forbidden(result.to_public_dict())
        self.assertEqual(result.http_status, 200)
        kinds = {item.scope_kind for item in result.citations}
        self.assertEqual(kinds, {"client"})
        self.assertEqual(
            [item.document_record_id for item in result.citations],
            [self.uat.CLIENT_A_DOCUMENT_RECORD_ID],
        )

    def test_journey_combo_matches_provider_plus_client_a_and_b_does_not_fallback(self) -> None:
        combo_a = self.uat.ask(
            query_id="combo.provider_client",
            request_id=uuid.UUID("51000000-0000-4000-8000-000000000003"),
            enterprise_id=self.uat.ENTERPRISE_A,
            client_account_id=self.uat.CLIENT_A,
        )
        combo_b = self.uat.ask(
            query_id="combo.provider_client",
            request_id=uuid.UUID("51000000-0000-4000-8000-000000000004"),
            enterprise_id=self.uat.ENTERPRISE_A,
            client_account_id=self.uat.CLIENT_B,
        )
        _walk_forbidden(combo_a.to_public_dict())
        _walk_forbidden(combo_b.to_public_dict())
        records_a = {item.document_record_id for item in combo_a.citations}
        records_b = {item.document_record_id for item in combo_b.citations}
        self.assertEqual(
            records_a,
            {
                self.uat.PROVIDER_DOCUMENT_RECORD_ID,
                self.uat.CLIENT_A_DOCUMENT_RECORD_ID,
            },
        )
        self.assertEqual(records_b, {self.uat.PROVIDER_DOCUMENT_RECORD_ID})
        self.assertNotIn(self.uat.CLIENT_A_DOCUMENT_RECORD_ID, records_b)

    def test_journey_client_b_empty_has_no_client_hits(self) -> None:
        result = self.uat.ask(
            query_id="client.current",
            request_id=uuid.UUID("51000000-0000-4000-8000-000000000005"),
            enterprise_id=self.uat.ENTERPRISE_A,
            client_account_id=self.uat.CLIENT_B,
        )
        self.assertEqual(result.http_status, 200)
        self.assertIsNone(result.answer)
        self.assertEqual(result.citations, ())
        self.assertEqual(result.refusal_reason, "NO_HITS")

    def test_journey_cross_tenant_and_unknown_client_are_404(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "MATERIAL_CONTEXT_NOT_FOUND"):
            self.uat.ask(
                query_id="client.current",
                request_id=uuid.uuid4(),
                enterprise_id=self.uat.ENTERPRISE_B,
                client_account_id=self.uat.CLIENT_A,
            )
        with self.assertRaisesRegex(RuntimeError, "MATERIAL_CONTEXT_NOT_FOUND"):
            self.uat.ask(
                query_id="cross.denied",
                request_id=uuid.uuid4(),
                enterprise_id=self.uat.ENTERPRISE_A,
                client_account_id=uuid.uuid4(),
            )

    def test_unauthorized_citation_is_404_without_physical_ids(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "MATERIAL_CITATION_NOT_FOUND") as raised:
            self.uat.open_citation(
                enterprise_id=self.uat.ENTERPRISE_A,
                document_record_id=self.uat.ENTERPRISE_B_DOCUMENT_RECORD_ID,
                document_version_id=self.uat.ENTERPRISE_B_DOCUMENT_VERSION_ID,
            )
        self.assertNotIn("ds_must_not_leak", str(raised.exception))
        allowed = self.uat.open_citation(
            enterprise_id=self.uat.ENTERPRISE_A,
            document_record_id=self.uat.PROVIDER_DOCUMENT_RECORD_ID,
            document_version_id=self.uat.PROVIDER_DOCUMENT_VERSION_ID,
        )
        _walk_forbidden(allowed)

    def test_idempotent_replay_and_switch_conflict(self) -> None:
        request_id = uuid.UUID("51000000-0000-4000-8000-000000000006")
        first = self.uat.ask(
            query_id="provider.shared",
            request_id=request_id,
            enterprise_id=self.uat.ENTERPRISE_A,
            client_account_id=None,
        )
        replay = self.uat.ask(
            query_id="provider.shared",
            request_id=request_id,
            enterprise_id=self.uat.ENTERPRISE_A,
            client_account_id=None,
        )
        self.assertEqual(first.to_public_dict(), replay.to_public_dict())
        with self.assertRaisesRegex(RuntimeError, "REQUEST_ID_CONFLICT"):
            self.uat.ask(
                query_id="provider.shared",
                request_id=request_id,
                enterprise_id=self.uat.ENTERPRISE_A,
                client_account_id=self.uat.CLIENT_A,
            )
        with self.assertRaisesRegex(RuntimeError, "REQUEST_ID_CONFLICT"):
            self.uat.ask(
                query_id="client.current",
                request_id=request_id,
                enterprise_id=self.uat.ENTERPRISE_A,
                client_account_id=None,
            )

    def test_rebuild_delete_residual_zero(self) -> None:
        rebuilt = self.uat.rebuild(
            enterprise_id=self.uat.ENTERPRISE_A,
            client_account_id=self.uat.CLIENT_A,
        )
        self.assertGreater(rebuilt["residual_count"], 0)
        deleted = self.uat.delete_scope(
            enterprise_id=self.uat.ENTERPRISE_A,
            client_account_id=self.uat.CLIENT_A,
        )
        self.assertEqual(deleted["residual_count"], 0)
        after = self.uat.ask(
            query_id="client.current",
            request_id=uuid.UUID("51000000-0000-4000-8000-000000000007"),
            enterprise_id=self.uat.ENTERPRISE_A,
            client_account_id=self.uat.CLIENT_A,
        )
        self.assertEqual(after.refusal_reason, "NO_HITS")
        self.assertEqual(after.citations, ())

    def test_fail_clear_empties_previous_result(self) -> None:
        prior = self.uat.ask(
            query_id="provider.shared",
            request_id=uuid.UUID("51000000-0000-4000-8000-000000000008"),
            enterprise_id=self.uat.ENTERPRISE_A,
            client_account_id=None,
        )
        self.assertTrue(prior.citations)
        with self.assertRaisesRegex(RuntimeError, "MATERIAL_RAG_UNAVAILABLE"):
            self.uat.ask(
                query_id="fail.clear",
                request_id=uuid.UUID("51000000-0000-4000-8000-000000000009"),
                enterprise_id=self.uat.ENTERPRISE_A,
                client_account_id=None,
            )
        visible = self.uat.visible_result(self.uat.ENTERPRISE_A)
        self.assertIsNone(visible["answer"])
        self.assertEqual(visible["citations"], [])

    def test_in_progress_is_202(self) -> None:
        result = self.uat.ask(
            query_id="progress.wait",
            request_id=uuid.UUID("51000000-0000-4000-8000-00000000000a"),
            enterprise_id=self.uat.ENTERPRISE_A,
            client_account_id=None,
        )
        self.assertEqual(result.http_status, 202)
        self.assertEqual(result.refusal_reason, "REQUEST_IN_PROGRESS")
        self.assertEqual(result.citations, ())
        self.assertIsNone(result.answer)

    def test_source_and_body_sha_are_synthetic(self) -> None:
        result = self.uat.ask(
            query_id="provider.shared",
            request_id=uuid.UUID("51000000-0000-4000-8000-00000000000b"),
            enterprise_id=self.uat.ENTERPRISE_A,
            client_account_id=None,
        )
        citation = result.citations[0]
        self.assertEqual(citation.source_sha256, _sha("provider-source"))
        self.assertEqual(citation.body_sha256, _sha("provider-body"))


class LocalUatHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["F1_MATERIAL_RAG_UAT_LOCAL"] = "1"
        os.environ["F1_LOCAL_ENGINEERING"] = "1"
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from platform_foundation.f1.auth import Tenant, current_user, tenant_from_header
        from platform_foundation.f1.api.routers import material_qa_uat
        import platform_foundation.f1.features.material_rag.uat_local as uat_mod

        uat_mod.reset_store()
        self.uat = uat_mod
        self.uat.set_test_client_binder(self._bind_client)
        tenant = Tenant(
            enterprise_id=uat_mod.ENTERPRISE_A,
            sub="uat-admin",
            roles=("enterprise_admin",),
            role="enterprise_admin",
        )

        async def fake_user() -> dict:
            return {"sub": tenant.sub, "roles": ["enterprise_admin"]}

        async def fake_tenant() -> Tenant:
            return tenant

        app = FastAPI()
        material_qa_uat.mount_if_enabled(app)
        app.dependency_overrides[current_user] = fake_user
        app.dependency_overrides[tenant_from_header] = fake_tenant
        self.client = TestClient(app)
        self.app = app

    def tearDown(self) -> None:
        self.uat.set_test_client_binder(None)
        self.uat.reset_store()
        os.environ.pop("F1_MATERIAL_RAG_UAT_LOCAL", None)
        os.environ.pop("F1_LOCAL_ENGINEERING", None)

    def _bind_client(self, _tenant: object, client_account_id: uuid.UUID | None) -> uuid.UUID | None:
        if client_account_id is None:
            return None
        if client_account_id in {self.uat.CLIENT_A, self.uat.CLIENT_B}:
            return client_account_id
        raise self.uat.LocalUatFault("MATERIAL_CONTEXT_NOT_FOUND", 404)

    def _headers(self, enterprise: uuid.UUID) -> dict[str, str]:
        return {"X-Enterprise-Id": str(enterprise)}

    def test_ask_forbids_question_and_physical_ids(self) -> None:
        response = self.client.post(
            "/api/v1/local-uat/material-qa",
            headers=self._headers(self.uat.ENTERPRISE_A),
            json={
                "question": "任意文本不得外发",
                "request_id": str(uuid.uuid4()),
            },
        )
        self.assertEqual(response.status_code, 422)
        response = self.client.post(
            "/api/v1/local-uat/material-qa",
            headers=self._headers(self.uat.ENTERPRISE_A),
            json={
                "query_id": "provider.shared",
                "request_id": str(uuid.uuid4()),
                "dataset_id": "ds_must_not_leak",
            },
        )
        self.assertEqual(response.status_code, 422)

    def test_http_journeys_and_conflict(self) -> None:
        request_id = str(uuid.UUID("52000000-0000-4000-8000-000000000001"))
        first = self.client.post(
            "/api/v1/local-uat/material-qa",
            headers=self._headers(self.uat.ENTERPRISE_A),
            json={"query_id": "provider.shared", "request_id": request_id},
        )
        self.assertEqual(first.status_code, 200)
        _walk_forbidden(first.json())
        replay = self.client.post(
            "/api/v1/local-uat/material-qa",
            headers=self._headers(self.uat.ENTERPRISE_A),
            json={"query_id": "provider.shared", "request_id": request_id},
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(first.json(), replay.json())
        conflict = self.client.post(
            "/api/v1/local-uat/material-qa",
            headers=self._headers(self.uat.ENTERPRISE_A),
            json={
                "query_id": "client.current",
                "request_id": request_id,
                "client_account_id": str(self.uat.CLIENT_A),
            },
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["detail"], "REQUEST_ID_CONFLICT")
        foreign_client = uuid.UUID("52000000-0000-4000-8000-00000000ffff")
        denied = self.client.post(
            "/api/v1/local-uat/material-qa",
            headers=self._headers(self.uat.ENTERPRISE_A),
            json={
                "query_id": "client.current",
                "request_id": str(uuid.uuid4()),
                "client_account_id": str(foreign_client),
            },
        )
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(denied.json()["detail"], "MATERIAL_CONTEXT_NOT_FOUND")
        _walk_forbidden(denied.json())
        failed = self.client.post(
            "/api/v1/local-uat/material-qa",
            headers=self._headers(self.uat.ENTERPRISE_A),
            json={
                "query_id": "fail.clear",
                "request_id": str(uuid.UUID("52000000-0000-4000-8000-000000000002")),
            },
        )
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(failed.json()["detail"], "MATERIAL_RAG_UNAVAILABLE")
        deleted = self.client.post(
            "/api/v1/local-uat/material-qa/delete",
            headers=self._headers(self.uat.ENTERPRISE_A),
            json={"client_account_id": str(self.uat.CLIENT_A)},
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()["residual_count"], 0)
        citation = self.client.post(
            "/api/v1/local-uat/material-qa/citation",
            headers=self._headers(self.uat.ENTERPRISE_A),
            json={
                "document_record_id": str(self.uat.ENTERPRISE_B_DOCUMENT_RECORD_ID),
                "document_version_id": str(self.uat.ENTERPRISE_B_DOCUMENT_VERSION_ID),
            },
        )
        self.assertEqual(citation.status_code, 404)
        self.assertEqual(citation.json()["detail"], "MATERIAL_CITATION_NOT_FOUND")
        _walk_forbidden(citation.json())

    def test_missing_token_is_401(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from platform_foundation.f1.api.routers import material_qa_uat

        app = FastAPI()
        material_qa_uat.mount_if_enabled(app)
        client = TestClient(app)
        response = client.post(
            "/api/v1/local-uat/material-qa",
            headers={"X-Enterprise-Id": str(self.uat.ENTERPRISE_A)},
            json={
                "query_id": "provider.shared",
                "request_id": str(uuid.uuid4()),
            },
        )
        self.assertEqual(response.status_code, 401)

    def test_wrong_role_is_403(self) -> None:
        from platform_foundation.f1.auth import Tenant, current_user, tenant_from_header

        async def fake_user() -> dict:
            return {"sub": "uat-employee", "roles": ["employee"]}

        async def fake_tenant() -> Tenant:
            return Tenant(
                enterprise_id=self.uat.ENTERPRISE_A,
                sub="uat-employee",
                roles=("employee",),
                role="employee",
            )

        self.app.dependency_overrides[current_user] = fake_user
        self.app.dependency_overrides[tenant_from_header] = fake_tenant
        response = self.client.post(
            "/api/v1/local-uat/material-qa",
            headers=self._headers(self.uat.ENTERPRISE_A),
            json={
                "query_id": "provider.shared",
                "request_id": str(uuid.uuid4()),
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_request_rejects_uat_actor_header_field(self) -> None:
        response = self.client.post(
            "/api/v1/local-uat/material-qa",
            headers={
                **self._headers(self.uat.ENTERPRISE_A),
                "X-Uat-Actor": "uat.provider",
            },
            json={
                "query_id": "provider.shared",
                "request_id": str(uuid.uuid4()),
            },
        )
        self.assertIn(response.status_code, {200, 202})
        self.assertNotIn("X-Uat-Actor", response.request.headers.get("x-uat-actor", "") or "")


class DualLegalTenantIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["F1_MATERIAL_RAG_UAT_LOCAL"] = "1"
        os.environ["F1_LOCAL_ENGINEERING"] = "1"
        from platform_foundation.f1.auth import Tenant
        import platform_foundation.f1.features.material_rag.uat_local as uat_mod

        uat_mod.reset_store()
        uat_mod.set_test_client_binder(None)
        self.uat = uat_mod
        self.Tenant = Tenant

    def tearDown(self) -> None:
        self.uat.set_test_client_binder(None)
        self.uat.reset_store()
        os.environ.pop("F1_MATERIAL_RAG_UAT_LOCAL", None)
        os.environ.pop("F1_LOCAL_ENGINEERING", None)

    def _tenant(self, enterprise: uuid.UUID) -> object:
        return self.Tenant(
            enterprise_id=enterprise,
            sub="uat-admin",
            roles=("super_admin",),
            role="super_admin",
        )

    def test_seed_tenants_map_to_distinct_catalogs_and_unknown_is_404(self) -> None:
        self.assertEqual(
            str(self.uat.ENTERPRISE_A), "20000000-0000-4000-8000-00000000000a"
        )
        self.assertEqual(
            str(self.uat.ENTERPRISE_B), "20000000-0000-4000-8000-00000000000b"
        )
        mapped_a = self.uat.catalog_enterprise_for_tenant(
            self._tenant(self.uat.ENTERPRISE_A)
        )
        mapped_b = self.uat.catalog_enterprise_for_tenant(
            self._tenant(self.uat.ENTERPRISE_B)
        )
        self.assertEqual(mapped_a, self.uat.ENTERPRISE_A)
        self.assertEqual(mapped_b, self.uat.ENTERPRISE_B)
        self.assertNotEqual(mapped_a, mapped_b)
        with self.assertRaisesRegex(RuntimeError, "MATERIAL_CONTEXT_NOT_FOUND"):
            self.uat.catalog_enterprise_for_tenant(self._tenant(uuid.uuid4()))

    def test_request_id_state_delete_and_citations_are_tenant_isolated(self) -> None:
        request_id = uuid.UUID("53000000-0000-4000-8000-000000000001")
        first_a = self.uat.ask(
            query_id="provider.shared",
            request_id=request_id,
            enterprise_id=self.uat.ENTERPRISE_A,
            client_account_id=None,
        )
        first_b = self.uat.ask(
            query_id="provider.shared",
            request_id=request_id,
            enterprise_id=self.uat.ENTERPRISE_B,
            client_account_id=None,
        )
        self.assertEqual(first_a.http_status, 200)
        self.assertEqual(first_b.http_status, 200)
        self.assertNotEqual(
            first_a.citations[0].document_record_id,
            first_b.citations[0].document_record_id,
        )
        visible_b_before = self.uat.visible_result(self.uat.ENTERPRISE_B)
        self.uat.delete_scope(
            enterprise_id=self.uat.ENTERPRISE_A,
            client_account_id=self.uat.CLIENT_A,
        )
        visible_b_after = self.uat.visible_result(self.uat.ENTERPRISE_B)
        self.assertEqual(visible_b_before, visible_b_after)
        still_b = self.uat.ask(
            query_id="provider.shared",
            request_id=request_id,
            enterprise_id=self.uat.ENTERPRISE_B,
            client_account_id=None,
        )
        self.assertEqual(still_b.to_public_dict(), first_b.to_public_dict())
        with self.assertRaisesRegex(RuntimeError, "MATERIAL_CITATION_NOT_FOUND"):
            self.uat.open_citation(
                enterprise_id=self.uat.ENTERPRISE_B,
                document_record_id=self.uat.PROVIDER_DOCUMENT_RECORD_ID,
                document_version_id=self.uat.PROVIDER_DOCUMENT_VERSION_ID,
            )
        with self.assertRaisesRegex(RuntimeError, "MATERIAL_CITATION_NOT_FOUND"):
            self.uat.open_citation(
                enterprise_id=self.uat.ENTERPRISE_A,
                document_record_id=self.uat.ENTERPRISE_B_DOCUMENT_RECORD_ID,
                document_version_id=self.uat.ENTERPRISE_B_DOCUMENT_VERSION_ID,
            )
        allowed_b = self.uat.open_citation(
            enterprise_id=self.uat.ENTERPRISE_B,
            document_record_id=self.uat.ENTERPRISE_B_DOCUMENT_RECORD_ID,
            document_version_id=self.uat.ENTERPRISE_B_DOCUMENT_VERSION_ID,
        )
        self.assertEqual(
            allowed_b["document_record_id"],
            str(self.uat.ENTERPRISE_B_DOCUMENT_RECORD_ID),
        )

    def test_crm_fixture_binds_closed_names_not_uuid_rank(self) -> None:
        import asyncio
        import inspect

        from platform_foundation.f1.features.p4 import crm as crm_service

        source = inspect.getsource(self.uat.bind_client_account)
        module_source = inspect.getsource(self.uat)
        self.assertNotIn("sorted(items", source)
        self.assertNotIn("ordered = sorted", source)
        self.assertIn("CLOSED_CLIENT_FIXTURE_NAMES.get(display_name)", source)
        self.assertIn("UAT-SYNTH-CLIENT-A", module_source)
        self.assertIn("UAT-SYNTH-CLIENT-B", module_source)
        ranked_first = uuid.UUID("00000000-0000-4000-8000-0000000000aa")
        ranked_second = uuid.UUID("ffffffff-0000-4000-8000-0000000000bb")

        async def fake_get_account(_tenant: object, account_id: uuid.UUID) -> dict:
            if account_id == ranked_first:
                return {"id": str(ranked_first), "display_name": "UAT-SYNTH-CLIENT-B"}
            if account_id == ranked_second:
                return {"id": str(ranked_second), "display_name": "UAT-SYNTH-CLIENT-A"}
            raise AssertionError("unexpected account")

        with patch.object(crm_service, "get_account", fake_get_account):
            bound_first = asyncio.run(
                self.uat.bind_client_account(
                    self._tenant(self.uat.ENTERPRISE_A), ranked_first
                )
            )
            bound_second = asyncio.run(
                self.uat.bind_client_account(
                    self._tenant(self.uat.ENTERPRISE_A), ranked_second
                )
            )
        self.assertEqual(bound_first, self.uat.CLIENT_B)
        self.assertEqual(bound_second, self.uat.CLIENT_A)


class DualLegalTenantHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["F1_MATERIAL_RAG_UAT_LOCAL"] = "1"
        os.environ["F1_LOCAL_ENGINEERING"] = "1"
        from fastapi import FastAPI, Header, HTTPException
        from fastapi.testclient import TestClient
        from platform_foundation.f1.auth import Tenant, current_user, tenant_from_header
        from platform_foundation.f1.api.routers import material_qa_uat
        import platform_foundation.f1.features.material_rag.uat_local as uat_mod

        uat_mod.reset_store()
        uat_mod.set_test_client_binder(self._bind_client)
        self.uat = uat_mod

        async def fake_user() -> dict:
            return {"sub": "uat-admin", "roles": ["super_admin"]}

        async def fake_tenant(
            x_enterprise_id: str | None = Header(default=None),
        ) -> Tenant:
            if not x_enterprise_id:
                raise HTTPException(status_code=400, detail="invalid enterprise id")
            return Tenant(
                enterprise_id=uuid.UUID(x_enterprise_id),
                sub="uat-admin",
                roles=("super_admin",),
                role="super_admin",
            )

        app = FastAPI()
        material_qa_uat.mount_if_enabled(app)
        app.dependency_overrides[current_user] = fake_user
        app.dependency_overrides[tenant_from_header] = fake_tenant
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.uat.set_test_client_binder(None)
        self.uat.reset_store()
        os.environ.pop("F1_MATERIAL_RAG_UAT_LOCAL", None)
        os.environ.pop("F1_LOCAL_ENGINEERING", None)

    def _bind_client(self, _tenant: object, client_account_id: uuid.UUID | None) -> uuid.UUID | None:
        if client_account_id is None:
            return None
        if client_account_id in {self.uat.CLIENT_A, self.uat.CLIENT_B}:
            return client_account_id
        raise self.uat.LocalUatFault("MATERIAL_CONTEXT_NOT_FOUND", 404)

    def test_http_isolates_two_legal_tenants_and_404s_unknown(self) -> None:
        request_id = str(uuid.UUID("54000000-0000-4000-8000-000000000001"))
        asked_a = self.client.post(
            "/api/v1/local-uat/material-qa",
            headers={"X-Enterprise-Id": str(self.uat.ENTERPRISE_A)},
            json={"query_id": "provider.shared", "request_id": request_id},
        )
        asked_b = self.client.post(
            "/api/v1/local-uat/material-qa",
            headers={"X-Enterprise-Id": str(self.uat.ENTERPRISE_B)},
            json={"query_id": "provider.shared", "request_id": request_id},
        )
        self.assertEqual(asked_a.status_code, 200)
        self.assertEqual(asked_b.status_code, 200)
        record_a = asked_a.json()["citations"][0]["document_record_id"]
        record_b = asked_b.json()["citations"][0]["document_record_id"]
        self.assertNotEqual(record_a, record_b)
        unknown = self.client.post(
            "/api/v1/local-uat/material-qa",
            headers={"X-Enterprise-Id": str(uuid.uuid4())},
            json={"query_id": "provider.shared", "request_id": str(uuid.uuid4())},
        )
        self.assertEqual(unknown.status_code, 404)
        self.assertEqual(unknown.json()["detail"], "MATERIAL_CONTEXT_NOT_FOUND")
        b_opens_a = self.client.post(
            "/api/v1/local-uat/material-qa/citation",
            headers={"X-Enterprise-Id": str(self.uat.ENTERPRISE_B)},
            json={
                "document_record_id": str(self.uat.PROVIDER_DOCUMENT_RECORD_ID),
                "document_version_id": str(self.uat.PROVIDER_DOCUMENT_VERSION_ID),
            },
        )
        a_opens_b = self.client.post(
            "/api/v1/local-uat/material-qa/citation",
            headers={"X-Enterprise-Id": str(self.uat.ENTERPRISE_A)},
            json={
                "document_record_id": str(self.uat.ENTERPRISE_B_DOCUMENT_RECORD_ID),
                "document_version_id": str(self.uat.ENTERPRISE_B_DOCUMENT_VERSION_ID),
            },
        )
        self.assertEqual(b_opens_a.status_code, 404)
        self.assertEqual(a_opens_b.status_code, 404)
        deleted_a = self.client.post(
            "/api/v1/local-uat/material-qa/delete",
            headers={"X-Enterprise-Id": str(self.uat.ENTERPRISE_A)},
            json={"client_account_id": str(self.uat.CLIENT_A)},
        )
        self.assertEqual(deleted_a.status_code, 200)
        replay_b = self.client.post(
            "/api/v1/local-uat/material-qa",
            headers={"X-Enterprise-Id": str(self.uat.ENTERPRISE_B)},
            json={"query_id": "provider.shared", "request_id": request_id},
        )
        self.assertEqual(replay_b.status_code, 200)
        self.assertEqual(replay_b.json(), asked_b.json())


class ResourceIdentityAndHandoffTests(unittest.TestCase):
    def test_same_name_wrong_label_is_refused_and_not_deleted(self) -> None:
        import importlib.machinery
        import importlib.util

        loader = importlib.machinery.SourceFileLoader(
            "anhuan_localctl_uat", str(ROOT / "scripts/localctl")
        )
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        self.assertTrue(hasattr(module, "_material_rag_uat_assert_mutation_safe"))
        identity = module._material_rag_uat_identity()
        state = {**identity, "web_port": 18080}
        name = f"{identity['compose_project']}-api-1"
        marker = f"uat-foreign-{uuid.uuid4().hex[:12]}"
        created = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--label",
                "io.anhuan.scope=foreign-sentinel",
                "--label",
                f"io.anhuan.project-id={uuid.uuid4()}",
                "--label",
                f"io.anhuan.reverse-sentinel={marker}",
                "redis:7-alpine",
                "sleep",
                "60",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        try:
            with self.assertRaises(module.LocalError) as raised:
                module._material_rag_uat_assert_mutation_safe(state, action="up")
            self.assertIn("FOREIGN_RESOURCE", str(raised.exception))
            still = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Status}}", name],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(still.returncode, 0)
            self.assertIn(still.stdout.strip(), {"created", "running"})
            labels = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "-f",
                    "{{index .Config.Labels \"io.anhuan.reverse-sentinel\"}}",
                    name,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(labels.stdout.strip(), marker)
        finally:
            subprocess.run(
                ["docker", "rm", "-f", name],
                capture_output=True,
                text=True,
                check=False,
            )


class BrowserEvidenceAllowlistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import importlib.machinery
        import importlib.util

        loader = importlib.machinery.SourceFileLoader(
            "anhuan_localctl_evidence", str(ROOT / "scripts/localctl")
        )
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        cls.module = module

    def test_rejects_missing_or_extra_evidence_keys(self) -> None:
        parse = self.module._parse_material_rag_uat_browser_evidence
        good = {
            "journey": "J6_FAIL_CLEAR",
            "expected_phase": "unavailable",
            "actual_phase": None,
            "request_seen": 0,
            "http_status": None,
            "action_stage": "select",
        }
        accepted = parse(json.dumps(good, separators=(",", ":")))
        self.assertEqual(len(accepted), 1)
        missing = dict(good)
        missing.pop("action_stage")
        with self.assertRaises(self.module.LocalError) as raised_missing:
            parse(json.dumps(missing))
        self.assertIn("EVIDENCE_INVALID", str(raised_missing.exception))
        extra = dict(good)
        extra["extra"] = 1
        with self.assertRaises(self.module.LocalError) as raised_extra:
            parse(json.dumps(extra))
        self.assertIn("EVIDENCE_INVALID", str(raised_extra.exception))


class LocalUatBrowserGateTests(unittest.TestCase):
    def test_browser_gate_covers_six_journeys(self) -> None:
        self.assertTrue(UAT_BROWSER_GATE.is_file())
        completed = subprocess.run(
            ["node", str(UAT_BROWSER_GATE)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout.strip().splitlines()[-1],
            "LOCAL_MATERIAL_RAG_UAT_BROWSER_GATE_OK",
        )
        payload = json.loads(completed.stdout.strip().splitlines()[0])
        self.assertEqual(payload["journeys_passed"], 6)
        self.assertEqual(payload["cleared_on_failure"], True)
        _walk_forbidden(payload)


if __name__ == "__main__":
    unittest.main()
