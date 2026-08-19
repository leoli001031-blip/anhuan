"""Real PostgreSQL + f1_0015 + production repository/service integration.

Fake objects implement the remote transport port only.  The production
repository, f1_api session/RLS, and MaterialRetrievalService stay in place.
No skip, xfail, SQLite, in-memory DB, Ark, RAGFlow live, or shared stack.
"""
from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import sys
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from infra.f1.material_rag_postgres_integration import (  # noqa: E402
    PostgresIntegrationStack,
)

FORBIDDEN_PHYSICAL_KEYS = frozenset(
    {
        "dataset_id",
        "dataset_ref",
        "document_id",
        "chunk_id",
        "knowledge_scope_id",
        "scope_ids",
        "ragflow_id",
        "ragflow_dataset_id",
        "ragflow_chunk_id",
        "kb_id",
        "doc_id",
    }
)
LEAK_TOKENS = (
    "SELECT ",
    "FROM f1.",
    "Traceback",
    "/datasets/",
    "ragflow_token",
)

STACK: PostgresIntegrationStack | None = None
WORLD: Any = None


def _run(coro):
    return asyncio.run(coro)


def _walk_forbidden(payload: object) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).lower() in FORBIDDEN_PHYSICAL_KEYS:
                raise AssertionError(f"physical_id_key:{key}")
            _walk_forbidden(value)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            _walk_forbidden(item)
    elif isinstance(payload, uuid.UUID):
        return
    elif isinstance(payload, str):
        lowered = payload.lower()
        for token in LEAK_TOKENS:
            if token.lower() in lowered:
                raise AssertionError(f"physical_id_token:{token}")


def _payload(result: object) -> dict[str, Any]:
    evidence = []
    for item in result.evidence:
        evidence.append(
            {
                name: getattr(item, name)
                for name in (
                    "canonical_unit_id",
                    "document_record_id",
                    "document_version_id",
                    "document_name",
                    "version_number",
                    "source_sha256",
                    "page_number",
                    "body_sha256",
                    "snippet",
                    "scope_kind",
                )
            }
        )
    return {
        "evidence": evidence,
        "refusal_reason": result.refusal_reason,
        "repr": repr(result),
    }


def setUpModule() -> None:
    global STACK, WORLD
    STACK = PostgresIntegrationStack()
    STACK.start()
    WORLD = STACK.seed_world()


def tearDownModule() -> None:
    global STACK
    if STACK is None:
        return
    try:
        STACK.dispose_runtime()
    finally:
        STACK.stop()
        STACK = None


class CountingRepository:
    def __init__(self, inner: object) -> None:
        self.inner = inner
        self.calls = {
            "load_provider_scope_id": 0,
            "load_client_scope_id": 0,
            "load_ready_bindings": 0,
            "load_released_units": 0,
        }

    def io_count(self) -> int:
        return sum(self.calls.values())

    async def load_provider_scope_id(self, tenant):
        self.calls["load_provider_scope_id"] += 1
        return await self.inner.load_provider_scope_id(tenant)

    async def load_client_scope_id(self, tenant, client_account_id):
        self.calls["load_client_scope_id"] += 1
        return await self.inner.load_client_scope_id(tenant, client_account_id)

    async def load_ready_bindings(self, tenant, context):
        self.calls["load_ready_bindings"] += 1
        return await self.inner.load_ready_bindings(tenant, context)

    async def load_released_units(self, tenant, context, unit_ids):
        self.calls["load_released_units"] += 1
        return await self.inner.load_released_units(tenant, context, unit_ids)


class FakeTransport:
    def __init__(self) -> None:
        self.calls = 0
        self.error: BaseException | None = None
        self.candidates: tuple[object, ...] = ()

    async def retrieve_candidates(self, query, datasets, limit):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.candidates


class MaterialRagPostgresIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        from platform_foundation.f1.features.material_rag.service import (
            MaterialRetrievalService,
            PostgresMaterialRagRepository,
        )

        self.assertIsNotNone(STACK)
        self.assertIsNotNone(WORLD)
        self.repo = CountingRepository(PostgresMaterialRagRepository())
        self.transport = FakeTransport()
        self.service = MaterialRetrievalService(self.repo, self.transport)
        self.assertIsInstance(self.repo.inner, PostgresMaterialRagRepository)
        self.assertNotIsInstance(self.repo.inner, MagicMock)

    def _candidate(self, spec, **overrides):
        from platform_foundation.f1.features.material_rag.ragflow_adapter import (
            RemoteCandidate,
        )

        payload = dataclasses.asdict(spec)
        payload.update(overrides)
        return RemoteCandidate(
            canonical_unit_id=payload["canonical_unit_id"],
            knowledge_scope_id=payload["knowledge_scope_id"],
            document_record_id=payload["document_record_id"],
            document_version_id=payload["document_version_id"],
            source_sha256=payload["source_sha256"],
            page_number=payload["page_number"],
            body_sha256=payload["body_sha256"],
        )

    def _ids(self, result) -> list[uuid.UUID]:
        return [item.canonical_unit_id for item in result.evidence]

    def test_session_scope_and_repository_are_production(self) -> None:
        from platform_foundation.f1 import database
        from platform_foundation.f1.features.material_rag.service import (
            PostgresMaterialRagRepository,
        )

        self.assertNotIsInstance(database.session_scope, MagicMock)
        source = inspect.getsource(PostgresMaterialRagRepository)
        self.assertIn('role="f1_api"', source)
        self.assertIn("session_scope", source)
        self.assertNotIn("sqlite", source.lower())

    def test_public_freeform_is_refused_before_ports(self) -> None:
        from platform_foundation.f1.features.material_rag.contracts import (
            MaterialRagUnavailable,
        )

        before = (self.repo.io_count(), self.transport.calls)
        with self.assertRaisesRegex(
            MaterialRagUnavailable, "MATERIAL_QUERY_EXTERNAL_PROCESSING_NOT_AUTHORIZED"
        ):
            _run(
                self.service.retrieve_registered(
                    "任意自由文本不得进入检索端口",
                    WORLD.tenant_a,
                    WORLD.provider_context,
                )
            )
        self.assertEqual((self.repo.io_count(), self.transport.calls), before)

        from platform_foundation.f1.features.material_rag.service import (
            run_verified_retrieval,
        )

        with self.assertRaisesRegex(
            MaterialRagUnavailable, "MATERIAL_QUERY_EXTERNAL_PROCESSING_NOT_AUTHORIZED"
        ):
            _run(
                run_verified_retrieval(
                    "任意自由文本不得进入检索端口",
                    WORLD.tenant_a,
                    WORLD.provider_context,
                )
            )

    def test_tenants_are_isolated_and_provider_is_provider_only(self) -> None:
        from platform_foundation.f1.features.material_rag.security import (
            PROVIDER_RETRIEVAL_QUERY_TEXT,
        )

        provider_a = _run(
            self.service.derive_retrieval_context(WORLD.tenant_a, None)
        )
        provider_b = _run(
            self.service.derive_retrieval_context(WORLD.tenant_b, None)
        )
        self.assertEqual(provider_a.kind, "service_provider")
        self.assertIsNone(provider_a.client_account_id)
        self.assertEqual(provider_b.kind, "service_provider")
        self.assertNotEqual(provider_a.context_sha256, provider_b.context_sha256)

        self.transport.candidates = (
            self._candidate(WORLD.units["provider_a"]),
            self._candidate(WORLD.units["client_a"]),
            self._candidate(WORLD.units["provider_b"]),
        )
        result = _run(
            self.service.retrieve_registered(
                PROVIDER_RETRIEVAL_QUERY_TEXT,
                WORLD.tenant_a,
                provider_a,
            )
        )
        self.assertEqual(self._ids(result), [WORLD.units["provider_a"].canonical_unit_id])
        self.assertEqual({item.scope_kind for item in result.evidence}, {"service_provider"})
        _walk_forbidden(_payload(result))

        other = _run(
            self.service.retrieve_registered(
                PROVIDER_RETRIEVAL_QUERY_TEXT,
                WORLD.tenant_b,
                provider_b,
            )
        )
        self.assertEqual(self._ids(other), [WORLD.units["provider_b"].canonical_unit_id])
        self.assertNotIn(WORLD.units["provider_a"].canonical_unit_id, self._ids(other))

    def test_client_scope_is_provider_plus_named_client_without_fallback(self) -> None:
        from platform_foundation.f1.features.material_rag.contracts import (
            MaterialRagContextNotFound,
        )
        from platform_foundation.f1.features.material_rag.security import (
            CLIENT_A_RETRIEVAL_QUERY_TEXT,
            CLIENT_B_RETRIEVAL_QUERY_TEXT,
        )

        context_a = _run(
            self.service.derive_retrieval_context(WORLD.tenant_a, WORLD.client_a_id)
        )
        context_b = _run(
            self.service.derive_retrieval_context(WORLD.tenant_a, WORLD.client_b_id)
        )
        self.assertEqual(context_a.kind, "client")
        self.assertEqual(context_a.client_account_id, WORLD.client_a_id)
        self.assertEqual(context_b.client_account_id, WORLD.client_b_id)

        mixed = (
            self._candidate(WORLD.units["client_a"]),
            self._candidate(WORLD.units["provider_a"]),
            self._candidate(WORLD.units["client_b"]),
            self._candidate(WORLD.units["client_a"]),
            self._candidate(WORLD.units["dirty"]),
        )
        self.transport.candidates = mixed
        result_a = _run(
            self.service.retrieve_registered(
                CLIENT_A_RETRIEVAL_QUERY_TEXT,
                WORLD.tenant_a,
                context_a,
            )
        )
        self.assertEqual(
            self._ids(result_a),
            [
                WORLD.units["client_a"].canonical_unit_id,
                WORLD.units["provider_a"].canonical_unit_id,
            ],
        )
        self.assertNotIn(WORLD.units["client_b"].canonical_unit_id, self._ids(result_a))
        _walk_forbidden(_payload(result_a))

        self.transport.candidates = mixed
        result_b = _run(
            self.service.retrieve_registered(
                CLIENT_B_RETRIEVAL_QUERY_TEXT,
                WORLD.tenant_a,
                context_b,
            )
        )
        self.assertEqual(
            self._ids(result_b),
            [
                WORLD.units["provider_a"].canonical_unit_id,
                WORLD.units["client_b"].canonical_unit_id,
            ],
        )
        self.assertNotIn(WORLD.units["client_a"].canonical_unit_id, self._ids(result_b))

        missing = [
            uuid.UUID(int=0),
            uuid.uuid4(),
            WORLD.client_b_id,
            WORLD.empty_client_id,
            WORLD.foreign_client_id,
        ]
        for client_id in missing:
            with self.subTest(client=str(client_id)):
                tenant = WORLD.tenant_a
                if client_id == WORLD.client_b_id:
                    # Named client B is valid for tenant A; use B's client on
                    # tenant B's foreign account and the empty/unknown ids here.
                    continue
                with self.assertRaisesRegex(
                    MaterialRagContextNotFound, "MATERIAL_CONTEXT_NOT_FOUND"
                ):
                    _run(self.service.derive_retrieval_context(tenant, client_id))

        with self.assertRaisesRegex(
            MaterialRagContextNotFound, "MATERIAL_CONTEXT_NOT_FOUND"
        ):
            _run(
                self.service.derive_retrieval_context(
                    WORLD.tenant_a, WORLD.foreign_client_id
                )
            )
        with self.assertRaisesRegex(
            MaterialRagContextNotFound, "MATERIAL_CONTEXT_NOT_FOUND"
        ):
            _run(
                self.service.derive_retrieval_context(
                    WORLD.tenant_a, WORLD.empty_client_id
                )
            )
        with self.assertRaisesRegex(
            MaterialRagContextNotFound, "MATERIAL_CONTEXT_NOT_FOUND"
        ):
            _run(
                self.service.derive_retrieval_context(
                    WORLD.tenant_b, WORLD.client_a_id
                )
            )

    def test_released_current_rows_visible_and_disqualified_rows_are_zero_evidence(
        self,
    ) -> None:
        from platform_foundation.f1.features.material_rag.security import (
            PROVIDER_RETRIEVAL_QUERY_TEXT,
        )

        hidden = (
            "dirty",
            "stale",
            "revoked",
            "preview_not_ready",
            "forged_aad",
        )
        self.transport.candidates = (
            self._candidate(WORLD.units["provider_a"]),
            *(self._candidate(WORLD.units[name]) for name in hidden),
            self._candidate(
                WORLD.units["provider_a"],
                body_sha256="1" * 64,
            ),
            self._candidate(
                WORLD.units["provider_a"],
                knowledge_scope_id=WORLD.units["client_a"].knowledge_scope_id,
            ),
            self._candidate(WORLD.units["provider_b"]),
        )
        result = _run(
            self.service.retrieve_registered(
                PROVIDER_RETRIEVAL_QUERY_TEXT,
                WORLD.tenant_a,
                WORLD.provider_context,
            )
        )
        self.assertEqual(self._ids(result), [WORLD.units["provider_a"].canonical_unit_id])
        hidden_ids = {WORLD.units[name].canonical_unit_id for name in hidden}
        self.assertTrue(hidden_ids.isdisjoint(self._ids(result)))
        self.assertNotIn(WORLD.units["provider_b"].canonical_unit_id, self._ids(result))
        _walk_forbidden(_payload(result))

    def test_duplicate_and_out_of_order_candidates_dedupe_deterministically(self) -> None:
        from platform_foundation.f1.features.material_rag.security import (
            CLIENT_A_RETRIEVAL_QUERY_TEXT,
        )

        first = self._candidate(WORLD.units["client_a"])
        second = self._candidate(WORLD.units["provider_a"])
        self.transport.candidates = (first, second, first, second)
        context = _run(
            self.service.derive_retrieval_context(WORLD.tenant_a, WORLD.client_a_id)
        )
        result = _run(
            self.service.retrieve_registered(
                CLIENT_A_RETRIEVAL_QUERY_TEXT,
                WORLD.tenant_a,
                context,
            )
        )
        self.assertEqual(
            self._ids(result),
            [
                WORLD.units["client_a"].canonical_unit_id,
                WORLD.units["provider_a"].canonical_unit_id,
            ],
        )
        rendered = json.dumps(_payload(result), default=str)
        for token in WORLD.leak_tokens:
            self.assertNotIn(token, rendered)
            self.assertNotIn(token, repr(result))

    def test_request_replay_is_idempotent_and_conflicts_have_no_side_effects(self) -> None:
        from platform_foundation.f1.features.material_rag.contracts import (
            MaterialRagRequestConflict,
        )
        from platform_foundation.f1.features.material_rag.security import (
            CLIENT_A_RETRIEVAL_QUERY_TEXT,
            PROVIDER_RETRIEVAL_QUERY_TEXT,
        )

        self.transport.candidates = (self._candidate(WORLD.units["provider_a"]),)
        client_context = _run(
            self.service.derive_retrieval_context(WORLD.tenant_a, WORLD.client_a_id)
        )
        request_id = uuid.UUID("71000000-0000-4000-8000-000000000001")
        first = _run(
            self.service.retrieve_registered(
                PROVIDER_RETRIEVAL_QUERY_TEXT,
                WORLD.tenant_a,
                WORLD.provider_context,
                request_id=request_id,
            )
        )
        after_first = (self.repo.io_count(), self.transport.calls)
        self.assertGreater(after_first[1], 0)
        second = _run(
            self.service.retrieve_registered(
                PROVIDER_RETRIEVAL_QUERY_TEXT,
                WORLD.tenant_a,
                WORLD.provider_context,
                request_id=request_id,
            )
        )
        self.assertEqual((self.repo.io_count(), self.transport.calls), after_first)
        self.assertEqual(self._ids(second), self._ids(first))

        conflicts = [
            (
                CLIENT_A_RETRIEVAL_QUERY_TEXT,
                WORLD.tenant_a,
                WORLD.provider_context,
            ),
            (
                PROVIDER_RETRIEVAL_QUERY_TEXT,
                WORLD.tenant_b,
                WORLD.provider_b_context,
            ),
            (
                PROVIDER_RETRIEVAL_QUERY_TEXT,
                WORLD.tenant_a,
                client_context,
            ),
        ]
        for query, tenant, context in conflicts:
            with self.subTest(kind=context.kind, sub=tenant.sub):
                with self.assertRaisesRegex(
                    MaterialRagRequestConflict, "REQUEST_ID_CONFLICT"
                ):
                    _run(
                        self.service.retrieve_registered(
                            query, tenant, context, request_id=request_id
                        )
                    )
                self.assertEqual(
                    (self.repo.io_count(), self.transport.calls), after_first
                )

    def test_transport_failures_are_unavailable_and_integrity_is_not_swallowed(
        self,
    ) -> None:
        from platform_foundation.f0j1.ragflow_client import RagFlowProbeError
        from platform_foundation.f1.features.material_rag.contracts import (
            MaterialRagIntegrityError,
            REFUSE_UNAVAILABLE,
        )
        from platform_foundation.f1.features.material_rag.security import (
            PROVIDER_RETRIEVAL_QUERY_TEXT,
        )

        self.transport.candidates = (self._candidate(WORLD.units["provider_a"]),)
        ok = _run(
            self.service.retrieve_registered(
                PROVIDER_RETRIEVAL_QUERY_TEXT,
                WORLD.tenant_a,
                WORLD.provider_context,
                request_id=uuid.UUID("71000000-0000-4000-8000-000000000010"),
            )
        )
        self.assertEqual(len(ok.evidence), 1)

        failures = [
            TimeoutError("adapter timeout"),
            ConnectionError("adapter connection"),
            RagFlowProbeError("RETRIEVAL_FAILED", status=503),
        ]
        for index, error in enumerate(failures, start=1):
            with self.subTest(error=type(error).__name__):
                self.transport.error = error
                failed = _run(
                    self.service.retrieve_registered(
                        PROVIDER_RETRIEVAL_QUERY_TEXT,
                        WORLD.tenant_a,
                        WORLD.provider_context,
                        request_id=uuid.UUID(f"71000000-0000-4000-8000-00000000001{index}"),
                    )
                )
                self.assertEqual(failed.evidence, ())
                self.assertEqual(failed.refusal_reason, REFUSE_UNAVAILABLE)
                self.assertNotEqual(failed.evidence, ok.evidence)

        self.transport.error = MaterialRagIntegrityError(
            "MATERIAL_RAG_DATASET_BINDING_INVALID"
        )
        with self.assertRaisesRegex(
            MaterialRagIntegrityError, "MATERIAL_RAG_DATASET_BINDING_INVALID"
        ) as raised:
            _run(
                self.service.retrieve_registered(
                    PROVIDER_RETRIEVAL_QUERY_TEXT,
                    WORLD.tenant_a,
                    WORLD.provider_context,
                    request_id=uuid.UUID("71000000-0000-4000-8000-000000000019"),
                )
            )
        self.assertIs(type(raised.exception), MaterialRagIntegrityError)

        self.transport.error = None
        recovered = _run(
            self.service.retrieve_registered(
                PROVIDER_RETRIEVAL_QUERY_TEXT,
                WORLD.tenant_a,
                WORLD.provider_context,
                request_id=uuid.UUID("71000000-0000-4000-8000-00000000001a"),
            )
        )
        self.assertEqual(self._ids(recovered), [WORLD.units["provider_a"].canonical_unit_id])
        self.assertEqual(STACK.idle_in_transaction_count(), 0)


if __name__ == "__main__":
    unittest.main()
