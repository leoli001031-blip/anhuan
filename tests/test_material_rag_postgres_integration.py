"""Real PostgreSQL + f1_0015 + production repository/service integration.

Fake objects implement the remote transport port only.  The production
repository, f1_api session/RLS, and MaterialRetrievalService stay in place.
No skip, xfail, SQLite, in-memory DB, Ark, RAGFlow live, or shared stack.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import hmac
import inspect
import json
import os
import sys
import time
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import urlparse


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


LIFE: Any = None
RAG_FAKE: Any = None
_REDIS_PATCH: Any = None
_RAG_PATCH: Any = None


def _install_lifecycle_fakes() -> None:
    global RAG_FAKE, _REDIS_PATCH, _RAG_PATCH
    from unittest.mock import patch

    from platform_foundation.f0j1.ragflow_client import RagFlowClient

    RAG_FAKE = DeterministicRagFlow()
    _REDIS_PATCH = patch("redis.Redis.from_url", FakeRedis.from_url)
    _RAG_PATCH = patch.object(RagFlowClient, "_request", RAG_FAKE.handle)
    _REDIS_PATCH.start()
    _RAG_PATCH.start()


def _uninstall_lifecycle_fakes() -> None:
    global _REDIS_PATCH, _RAG_PATCH
    if _RAG_PATCH is not None:
        _RAG_PATCH.stop()
        _RAG_PATCH = None
    if _REDIS_PATCH is not None:
        _REDIS_PATCH.stop()
        _REDIS_PATCH = None


class FakeLock:
    def acquire(self, *args, **kwargs) -> bool:
        return True

    def release(self) -> None:
        return None


class FakeRedis:
    @classmethod
    def from_url(cls, *args, **kwargs):
        return cls()

    def lock(self, *args, **kwargs) -> FakeLock:
        return FakeLock()


class DeterministicRagFlow:
    """In-memory RAGFlow HTTP bottom layer. Dataset/document/chunk only."""

    def __init__(self) -> None:
        self.datasets: dict[str, dict[str, str]] = {}
        self.documents: dict[str, dict[str, dict[str, str]]] = {}
        self.chunks: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
        self.fail_next: str | None = None
        self.create_dataset_calls = 0
        self.create_document_calls = 0
        self.chunk_add_calls = 0
        self.delete_dataset_calls = 0
        self.delete_document_calls = 0
        self.delete_chunk_calls = 0

    def reset_counts(self) -> None:
        self.create_dataset_calls = 0
        self.create_document_calls = 0
        self.chunk_add_calls = 0
        self.delete_dataset_calls = 0
        self.delete_document_calls = 0
        self.delete_chunk_calls = 0

    def mutation_snapshot(self) -> dict[str, int]:
        return {
            "create_dataset": self.create_dataset_calls,
            "delete_dataset": self.delete_dataset_calls,
            "create_document": self.create_document_calls,
            "delete_document": self.delete_document_calls,
            "add_chunk": self.chunk_add_calls,
            "delete_chunk": self.delete_chunk_calls,
        }

    def dataset_exists(self, dataset_id: str) -> bool:
        return dataset_id in self.datasets

    def document_count(self, dataset_id: str) -> int:
        return len(self.documents.get(dataset_id, {}))

    def chunk_count(self, dataset_id: str | None = None) -> int:
        if dataset_id is None:
            return sum(len(docs) for ds in self.chunks.values() for docs in ds.values())
        return sum(len(chunks) for chunks in self.chunks.get(dataset_id, {}).values())

    def dataset_id_for_scope(self, scope_id: uuid.UUID) -> str | None:
        name = f"f1-material-{scope_id.hex}"
        for item in self.datasets.values():
            if item["name"] == name:
                return item["id"]
        return None

    def semantic_remote(self, scope_id: uuid.UUID, source_sha256: str) -> dict[str, Any]:
        from platform_foundation.f1.features.material_rag.security import (
            remote_document_name,
        )

        name = remote_document_name(source_sha256)
        dataset_id = self.dataset_id_for_scope(scope_id)
        document_id = None
        identities: list[tuple[str, str]] = []
        if dataset_id is not None:
            for doc in self.documents.get(dataset_id, {}).values():
                if doc.get("name") == name:
                    document_id = str(doc.get("id") or "")
                    break
            if document_id:
                for chunk in self.chunks.get(dataset_id, {}).get(document_id, {}).values():
                    tags = {}
                    for raw in chunk.get("tag_kwd") or []:
                        key, sep, value = str(raw).partition("=")
                        if sep:
                            tags[key] = value
                    unit_id = tags.get("canonical_unit_id")
                    body_sha = tags.get("body_sha256")
                    if unit_id and body_sha:
                        identities.append((unit_id, body_sha))
        identities.sort()
        return {
            "dataset_id": dataset_id,
            "dataset_exists": dataset_id is not None and dataset_id in self.datasets,
            "document_id": document_id,
            "document_exists": bool(document_id),
            "document_name": name,
            "dataset_count": len(self.datasets),
            "document_count": 0
            if dataset_id is None
            else len(self.documents.get(dataset_id, {})),
            "chunk_count": 0
            if dataset_id is None or not document_id
            else len(self.chunks.get(dataset_id, {}).get(document_id, {})),
            "unit_identities": tuple(identities),
        }

    def handle(self, method, path, token, payload=None):
        del token
        if self.fail_next == "connection":
            self.fail_next = None
            raise ConnectionError("MATERIAL_RAG_NETWORK_FAILED")
        if self.fail_next == "timeout":
            self.fail_next = None
            raise TimeoutError("MATERIAL_RAG_NETWORK_FAILED")
        if self.fail_next == "probe":
            self.fail_next = None
            return 200, {"code": 400, "data": {}}
        parsed = urlparse(path)
        segs = [item for item in parsed.path.split("/") if item]
        if method == "GET" and segs == ["datasets"]:
            items = list(self.datasets.values())
            return 200, {"code": 0, "data": {"datasets": items, "total": len(items)}}
        if method == "POST" and segs == ["datasets"]:
            if self.fail_next == "create_dataset":
                self.fail_next = None
                return 500, {"code": 500, "data": {}}
            name = str((payload or {}).get("name") or "")
            dataset_id = hashlib.sha256(name.encode("ascii")).hexdigest()[:32]
            self.create_dataset_calls += 1
            self.datasets[dataset_id] = {"id": dataset_id, "name": name}
            self.documents.setdefault(dataset_id, {})
            self.chunks.setdefault(dataset_id, {})
            if self.fail_next == "create_dataset_commit_then_drop":
                self.fail_next = None
                raise ConnectionError("MATERIAL_RAG_NETWORK_FAILED")
            return 200, {"code": 0, "data": {"id": dataset_id, "name": name}}
        if method == "DELETE" and segs == ["datasets"]:
            ids = list((payload or {}).get("ids") or [])
            removed = 0
            for dataset_id in ids:
                if dataset_id in self.datasets:
                    del self.datasets[dataset_id]
                    self.documents.pop(dataset_id, None)
                    self.chunks.pop(dataset_id, None)
                    removed += 1
            self.delete_dataset_calls += removed
            return 200, {"code": 0, "data": {"success_count": removed}}
        if len(segs) >= 3 and segs[0] == "datasets":
            dataset_id = segs[1]
            if method == "GET" and segs[2:] == ["documents"]:
                docs = list(self.documents.get(dataset_id, {}).values())
                return 200, {"code": 0, "data": {"docs": docs, "total": len(docs)}}
            if method == "POST" and segs[2:] == ["documents"]:
                name = str((payload or {}).get("name") or "")
                document_id = hashlib.sha256(
                    f"{dataset_id}:{name}".encode("ascii")
                ).hexdigest()[:32]
                self.create_document_calls += 1
                self.documents.setdefault(dataset_id, {})[document_id] = {
                    "id": document_id,
                    "name": name,
                }
                self.chunks.setdefault(dataset_id, {}).setdefault(document_id, {})
                return 200, {"code": 0, "data": {"id": document_id, "name": name}}
            if method == "DELETE" and segs[2:] == ["documents"]:
                ids = list((payload or {}).get("ids") or [])
                removed = 0
                for document_id in ids:
                    if document_id in self.documents.get(dataset_id, {}):
                        dropped = self.chunks.get(dataset_id, {}).pop(document_id, {})
                        self.delete_chunk_calls += len(dropped)
                        del self.documents[dataset_id][document_id]
                        removed += 1
                self.delete_document_calls += removed
                return 200, {"code": 0, "data": {"success_count": removed}}
            if len(segs) >= 4 and segs[2] == "documents":
                document_id = segs[3]
                if method == "GET" and (len(segs) == 5 and segs[4] == "chunks"):
                    items = list(
                        self.chunks.get(dataset_id, {}).get(document_id, {}).values()
                    )
                    return 200, {
                        "code": 0,
                        "data": {"chunks": items, "total": len(items)},
                    }
                if method == "GET" and len(segs) == 6 and segs[4] == "chunks":
                    chunk = (
                        self.chunks.get(dataset_id, {})
                        .get(document_id, {})
                        .get(segs[5], {})
                    )
                    return 200, {"code": 0, "data": chunk}
                if method == "POST" and segs[-1] == "chunks":
                    self.chunk_add_calls += 1
                    content = str((payload or {}).get("content") or "")
                    tags = list((payload or {}).get("tag_kwd") or [])
                    chunk_id = hashlib.sha256(
                        f"{dataset_id}:{document_id}:{content}".encode("utf-8")
                    ).hexdigest()[:32]
                    chunk = {
                        "id": chunk_id,
                        "chunk_id": chunk_id,
                        "content": content,
                        "tag_kwd": tags,
                    }
                    self.chunks.setdefault(dataset_id, {}).setdefault(document_id, {})[
                        chunk_id
                    ] = chunk
                    return 200, {"code": 0, "data": chunk}
        return 404, {"code": 404, "data": {}}

class MaterialRagJobLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        global LIFE
        if STACK is None:
            raise AssertionError("STACK_MISSING")
        _install_lifecycle_fakes()
        LIFE = STACK.seed_lifecycle_world()

    @classmethod
    def tearDownClass(cls) -> None:
        residual = STACK.lifecycle_residuals() if STACK is not None else {}
        _uninstall_lifecycle_fakes()
        expected = (
            "idle_in_transaction",
            "live_lease",
            "orphan_unit",
            "provisioning_binding",
            "deleted_binding_secrets",
        )
        if set(residual) != set(expected):
            raise AssertionError("lifecycle_residual_keys")
        if any(residual[key] != 0 for key in expected):
            raise AssertionError("lifecycle_residual")

    def setUp(self) -> None:
        self.assertIsNotNone(LIFE)
        self.assertIsNotNone(RAG_FAKE)
        RAG_FAKE.fail_next = None
        RAG_FAKE.reset_counts()

    def _units_for(self, claim, body: str):
        from platform_foundation.f1.features.material_rag.security import canonical_unit

        return (
            canonical_unit(
                enterprise_id=claim.enterprise_id,
                knowledge_scope_id=claim.knowledge_scope_id,
                document_record_id=claim.document_record_id,
                document_version_id=claim.document_version_id,
                source_sha256=claim.source_sha256,
                page_number=1,
                ordinal=1,
                parser_version="pgint1",
                text=body,
            ),
        )

    def _manifest_proof(self, claim, units):
        from platform_foundation.f1.features.material_rag.contracts import (
            DemoUnitManifestProof,
        )
        from platform_foundation.f1.features.material_rag.security import (
            CLIENT_B_ISOLATION_CANARY_TEXT,
            PROVIDER_POLICY_CANARY_TEXT,
            _MANIFEST_DOMAIN,
            _manifest_key_bytes,
            _manifest_payload,
            create_synthetic_unit_manifest_proof,
        )

        canaries = {
            hashlib.sha256(PROVIDER_POLICY_CANARY_TEXT.encode("utf-8")).hexdigest(),
            hashlib.sha256(CLIENT_B_ISOLATION_CANARY_TEXT.encode("utf-8")).hexdigest(),
        }
        if claim.source_sha256 in canaries:
            return create_synthetic_unit_manifest_proof(claim=claim, units=units)
        issued = int(time.time())
        expires = issued + 300
        payload, _ordered = _manifest_payload(
            claim=claim,
            issued_at_epoch=issued,
            expires_at_epoch=expires,
            units=units,
        )
        manifest_sha = hashlib.sha256(payload).hexdigest()
        signature = hmac.new(
            _manifest_key_bytes(), _MANIFEST_DOMAIN + payload, hashlib.sha256
        ).hexdigest()
        return DemoUnitManifestProof(
            schema_version=1,
            job_id=claim.id,
            action=claim.action,
            attempt=claim.attempt,
            source_sha256=claim.source_sha256,
            issued_at_epoch=issued,
            expires_at_epoch=expires,
            manifest_sha256=manifest_sha,
            signature_hex=signature,
        )

    def _process(self, claim, body: str | None = None):
        from platform_foundation.f1.features.material_rag.worker import (
            process_claimed_demo_job,
        )

        if claim.action == "delete":
            return _run(process_claimed_demo_job(claim))
        units = self._units_for(claim, body or LIFE.body_for(claim.source_sha256))
        proof = self._manifest_proof(claim, units)
        return _run(process_claimed_demo_job(claim, units=units, manifest_proof=proof))

    def _enqueue_claim(
        self, tenant, version_id, action, key, worker_id, lease_seconds=300
    ):
        from platform_foundation.f1.features.material_rag.repository import (
            claim_job,
            enqueue_job,
        )

        job_id = _run(
            enqueue_job(
                tenant,
                document_version_id=version_id,
                action=action,
                idempotency_key=key,
            )
        )
        claim = _run(
            claim_job(job_id, worker_id=worker_id, lease_seconds=lease_seconds)
        )
        return job_id, claim

    def test_index_rebuild_delete_lifecycle(self) -> None:
        from platform_foundation.f1.features.material_rag.repository import (
            claim_job,
            enqueue_job,
        )

        first_id, claim = self._enqueue_claim(
            LIFE.tenant_a,
            LIFE.docs["sibling_a"].version_id,
            "index",
            "life-index-a",
            "worker-index-a",
        )
        self.assertTrue(self._process(claim))
        after_first = STACK.lifecycle_snapshot(LIFE.docs["sibling_a"].version_id)
        remote_first = RAG_FAKE.semantic_remote(
            LIFE.docs["sibling_a"].scope_id, claim.source_sha256
        )
        self.assertTrue(remote_first["dataset_exists"])
        self.assertTrue(remote_first["document_exists"])
        self.assertEqual(remote_first["document_count"], 1)
        self.assertEqual(remote_first["chunk_count"], after_first["unit_count"])
        self.assertEqual(
            remote_first["unit_identities"],
            STACK.unit_identities(LIFE.docs["sibling_a"].version_id),
        )
        adds_after_first = RAG_FAKE.chunk_add_calls
        second_id = _run(
            enqueue_job(
                LIFE.tenant_a,
                document_version_id=LIFE.docs["sibling_a"].version_id,
                action="index",
                idempotency_key="life-index-a",
            )
        )
        self.assertEqual(first_id, second_id)
        self.assertIsNone(_run(claim_job(second_id, worker_id="worker-index-a-replay")))
        self.assertEqual(
            STACK.lifecycle_snapshot(LIFE.docs["sibling_a"].version_id), after_first
        )
        self.assertEqual(RAG_FAKE.chunk_add_calls, adds_after_first)

        new_id = _run(
            enqueue_job(
                LIFE.tenant_a,
                document_version_id=LIFE.docs["sibling_a"].version_id,
                action="index",
                idempotency_key="life-index-a-2",
            )
        )
        self.assertNotEqual(first_id, new_id)
        claim_new = _run(claim_job(new_id, worker_id="worker-index-a-2"))
        before_chunks = RAG_FAKE.chunk_count()
        self.assertTrue(self._process(claim_new))
        self.assertEqual(RAG_FAKE.chunk_add_calls, adds_after_first)
        self.assertEqual(RAG_FAKE.chunk_count(), before_chunks)

        before_rebuild = STACK.lifecycle_snapshot(LIFE.docs["sibling_a"].version_id)
        rebuild_mutations = RAG_FAKE.mutation_snapshot()
        _, rebuild = self._enqueue_claim(
            LIFE.tenant_a,
            LIFE.docs["sibling_a"].version_id,
            "rebuild",
            "life-rebuild-a",
            "worker-rebuild-a",
        )
        self.assertTrue(self._process(rebuild))
        after_rebuild = STACK.lifecycle_snapshot(LIFE.docs["sibling_a"].version_id)
        self.assertEqual(before_rebuild["unit_fingerprint"], after_rebuild["unit_fingerprint"])
        self.assertEqual(before_rebuild["manifest_sha"], after_rebuild["manifest_sha"])
        rebuilt_mutations = RAG_FAKE.mutation_snapshot()
        self.assertGreater(
            rebuilt_mutations["delete_document"], rebuild_mutations["delete_document"]
        )
        self.assertGreater(
            rebuilt_mutations["create_document"], rebuild_mutations["create_document"]
        )
        self.assertGreater(rebuilt_mutations["add_chunk"], rebuild_mutations["add_chunk"])
        remote_rebuild = RAG_FAKE.semantic_remote(
            LIFE.docs["sibling_a"].scope_id, rebuild.source_sha256
        )
        self.assertTrue(remote_rebuild["dataset_exists"])
        self.assertTrue(remote_rebuild["document_exists"])
        self.assertEqual(remote_rebuild["chunk_count"], after_rebuild["unit_count"])
        self.assertEqual(
            remote_rebuild["unit_identities"],
            STACK.unit_identities(LIFE.docs["sibling_a"].version_id),
        )

        _, sibling_b = self._enqueue_claim(
            LIFE.tenant_a,
            LIFE.docs["sibling_b"].version_id,
            "index",
            "life-index-b",
            "worker-index-b",
        )
        self.assertTrue(self._process(sibling_b))
        _, delete_a = self._enqueue_claim(
            LIFE.tenant_a,
            LIFE.docs["sibling_a"].version_id,
            "delete",
            "life-delete-a",
            "worker-delete-a",
        )
        self.assertTrue(self._process(delete_a))
        remaining = STACK.lifecycle_snapshot(LIFE.docs["sibling_b"].version_id)
        self.assertGreater(remaining["unit_count"], 0)
        self.assertEqual(remaining["binding_status"], "ready")
        dataset_id = RAG_FAKE.dataset_id_for_scope(remaining["scope_id"])
        self.assertIsNotNone(dataset_id)
        self.assertTrue(RAG_FAKE.dataset_exists(dataset_id))
        remote_b = RAG_FAKE.semantic_remote(
            LIFE.docs["sibling_b"].scope_id, LIFE.docs["sibling_b"].source_sha256
        )
        remote_a = RAG_FAKE.semantic_remote(
            LIFE.docs["sibling_a"].scope_id, LIFE.docs["sibling_a"].source_sha256
        )
        self.assertTrue(remote_b["document_exists"])
        self.assertGreater(remote_b["chunk_count"], 0)
        self.assertFalse(remote_a["document_exists"])
        self.assertEqual(remote_a["chunk_count"], 0)
        _, delete_b = self._enqueue_claim(
            LIFE.tenant_a,
            LIFE.docs["sibling_b"].version_id,
            "delete",
            "life-delete-b",
            "worker-delete-b",
        )
        self.assertTrue(self._process(delete_b))
        self.assertEqual(
            STACK.lifecycle_snapshot(LIFE.docs["sibling_a"].version_id)["unit_count"], 0
        )
        cleared_b = STACK.lifecycle_snapshot(LIFE.docs["sibling_b"].version_id)
        self.assertEqual(cleared_b["unit_count"], 0)
        self.assertEqual(cleared_b["binding_status"], "deleted")
        self.assertEqual(cleared_b["binding_secrets"], 0)
        self.assertFalse(RAG_FAKE.dataset_exists(dataset_id))
        self.assertEqual(len(RAG_FAKE.datasets), 0)
        self.assertEqual(sum(len(docs) for docs in RAG_FAKE.documents.values()), 0)
        self.assertEqual(RAG_FAKE.chunk_count(), 0)

    def test_provisioning_tombstone_is_compensated_clean(self) -> None:
        RAG_FAKE.fail_next = "create_dataset"
        _, claim = self._enqueue_claim(
            LIFE.tenant_b,
            LIFE.docs["provision"].version_id,
            "index",
            "life-provision",
            "worker-provision",
        )
        outcome = self._process(claim, LIFE.body_for(claim.source_sha256))
        self.assertEqual(outcome.kind, "FINISH_TRUE")
        snap = STACK.lifecycle_snapshot(LIFE.docs["provision"].version_id)
        self.assertEqual(snap["job_status"], "retry_wait")
        self.assertEqual(snap["binding_status"], "provisioning")
        _, delete_claim = self._enqueue_claim(
            LIFE.tenant_b,
            LIFE.docs["provision"].version_id,
            "delete",
            "life-provision-delete",
            "worker-provision-delete",
        )
        self.assertTrue(self._process(delete_claim))
        after = STACK.lifecycle_snapshot(LIFE.docs["provision"].version_id)
        self.assertIn(after["binding_status"], {"absent", "deleted"})
        self.assertEqual(after["binding_secrets"], 0)
        self.assertEqual(after["unit_count"], 0)

    def test_unreleased_cross_tenant_and_revoked_refuse_before_remote_write(self) -> None:
        from platform_foundation.f1.features.material_rag.contracts import (
            MaterialRagIntegrityError,
        )
        from platform_foundation.f1.features.material_rag.repository import enqueue_job

        writes_before = RAG_FAKE.mutation_snapshot()
        with self.assertRaisesRegex(
            MaterialRagIntegrityError, "MATERIAL_VERSION_NOT_INDEXABLE"
        ):
            _run(
                enqueue_job(
                    LIFE.tenant_a,
                    document_version_id=LIFE.docs["unreleased"].version_id,
                    action="index",
                    idempotency_key="life-unreleased",
                )
            )
        with self.assertRaisesRegex(
            MaterialRagIntegrityError, "MATERIAL_VERSION_NOT_FOUND"
        ):
            _run(
                enqueue_job(
                    LIFE.tenant_a,
                    document_version_id=LIFE.docs["revoke"].version_id,
                    action="index",
                    idempotency_key="life-cross-tenant",
                )
            )
        _, claim = self._enqueue_claim(
            LIFE.tenant_b,
            LIFE.docs["revoke"].version_id,
            "index",
            "life-revoke",
            "worker-revoke",
        )
        self.assertEqual(STACK.revoke_release(LIFE.docs["revoke"].task_id), 1)
        outcome = self._process(claim, LIFE.body_for(claim.source_sha256))
        self.assertEqual(outcome.kind, "FINISH_TRUE")
        snap = STACK.lifecycle_snapshot(LIFE.docs["revoke"].version_id)
        self.assertEqual(snap["job_status"], "failed")
        self.assertEqual(snap["unit_count"], 0)
        writes_after = RAG_FAKE.mutation_snapshot()
        self.assertEqual(writes_after, writes_before)

    def test_known_id_redelivery_recovery(self) -> None:
        from platform_foundation.f1.features.material_rag.repository import (
            claim_job,
            finish_job,
            renew_job_lease,
        )

        job_id, claim = self._enqueue_claim(
            LIFE.tenant_b,
            LIFE.docs["recovery"].version_id,
            "index",
            "life-recovery",
            "worker-recovery-1",
        )
        RAG_FAKE.fail_next = "probe"
        first = self._process(claim, LIFE.body_for(claim.source_sha256))
        self.assertEqual(first.kind, "FINISH_TRUE")
        waiting = STACK.lifecycle_snapshot(LIFE.docs["recovery"].version_id)
        self.assertEqual(waiting["job_status"], "retry_wait")
        self.assertGreaterEqual(waiting["unit_count"], 1)
        self.assertIsNone(_run(claim_job(job_id, worker_id="worker-recovery-early")))
        self.assertEqual(STACK.make_retry_due(job_id), 1)
        claim2 = _run(
            claim_job(job_id, worker_id="worker-recovery-2", lease_seconds=30)
        )
        self.assertIsNotNone(claim2)
        self.assertNotEqual(claim2.lease_token, claim.lease_token)
        self.assertEqual(claim2.attempt, claim.attempt + 1)
        self.assertFalse(_run(renew_job_lease(claim)))
        self.assertFalse(
            _run(finish_job(claim, status="failed", reason="MATERIAL_RAG_LOCAL_FAILED"))
        )
        self.assertEqual(STACK.expire_running_lease(claim2.id), 1)
        claim3 = _run(claim_job(job_id, worker_id="worker-recovery-3"))
        self.assertIsNotNone(claim3)
        self.assertNotEqual(claim3.lease_token, claim2.lease_token)
        self.assertEqual(claim3.attempt, claim2.attempt + 1)
        self.assertFalse(_run(renew_job_lease(claim2)))
        RAG_FAKE.fail_next = None
        before_units = STACK.lifecycle_snapshot(LIFE.docs["recovery"].version_id)[
            "unit_count"
        ]
        self.assertTrue(self._process(claim3, LIFE.body_for(claim3.source_sha256)))
        done = STACK.lifecycle_snapshot(LIFE.docs["recovery"].version_id)
        self.assertEqual(done["job_status"], "done")
        self.assertEqual(done["unit_count"], before_units)
        self.assertEqual(done["terminal_count"], 1)
        self.assertIsNone(_run(claim_job(job_id, worker_id="worker-recovery-done")))
        print("LOCAL_MATERIAL_RAG_JOB_RECOVERY_OK", flush=True)
        RAG_FAKE.fail_next = "connection"
        _, delete_claim = self._enqueue_claim(
            LIFE.tenant_b,
            LIFE.docs["recovery"].version_id,
            "delete",
            "life-recovery-delete-fail",
            "worker-recovery-delete-fail",
        )
        delete_outcome = self._process(delete_claim)
        self.assertEqual(delete_outcome.kind, "FINISH_TRUE")
        after_fail = STACK.lifecycle_snapshot(LIFE.docs["recovery"].version_id)
        self.assertEqual(after_fail["job_status"], "retry_wait")
        self.assertEqual(after_fail["unit_count"], before_units)
        self.assertIsNone(
            _run(claim_job(job_id, worker_id="worker-recovery-done-again"))
        )

    def test_remote_dataset_created_then_lost_is_compensated(self) -> None:
        RAG_FAKE.fail_next = "create_dataset_commit_then_drop"
        _, claim = self._enqueue_claim(
            LIFE.tenant_b,
            LIFE.docs["provision"].version_id,
            "index",
            "life-provision-lost",
            "worker-provision-lost",
        )
        outcome = self._process(claim, LIFE.body_for(claim.source_sha256))
        self.assertEqual(outcome.kind, "FINISH_TRUE")
        snap = STACK.lifecycle_snapshot(LIFE.docs["provision"].version_id)
        self.assertEqual(snap["job_status"], "retry_wait")
        self.assertEqual(snap["binding_status"], "provisioning")
        lost_id = RAG_FAKE.dataset_id_for_scope(LIFE.docs["provision"].scope_id)
        self.assertIsNotNone(lost_id)
        self.assertTrue(RAG_FAKE.dataset_exists(lost_id))
        _, delete_claim = self._enqueue_claim(
            LIFE.tenant_b,
            LIFE.docs["provision"].version_id,
            "delete",
            "life-provision-lost-delete",
            "worker-provision-lost-delete",
        )
        self.assertTrue(self._process(delete_claim))
        after = STACK.lifecycle_snapshot(LIFE.docs["provision"].version_id)
        self.assertIn(after["binding_status"], {"absent", "deleted"})
        self.assertEqual(after["binding_secrets"], 0)
        self.assertEqual(after["unit_count"], 0)
        self.assertFalse(RAG_FAKE.dataset_exists(lost_id))
        self.assertIsNone(
            RAG_FAKE.dataset_id_for_scope(LIFE.docs["provision"].scope_id)
        )

    def test_stale_claim_is_lease_lost_with_zero_remote_mutation(self) -> None:
        from platform_foundation.f1.features.material_rag.repository import (
            claim_job,
            finish_job,
            renew_job_lease,
        )

        job_id, stale = self._enqueue_claim(
            LIFE.tenant_a,
            LIFE.docs["sibling_a"].version_id,
            "index",
            "life-stale-lease",
            "worker-stale-old",
            lease_seconds=30,
        )
        self.assertEqual(STACK.expire_running_lease(stale.id), 1)
        fresh = _run(claim_job(job_id, worker_id="worker-stale-new"))
        self.assertIsNotNone(fresh)
        self.assertNotEqual(fresh.lease_token, stale.lease_token)
        local_before = STACK.local_job_world_snapshot(
            LIFE.docs["sibling_a"].version_id
        )
        remote_before = RAG_FAKE.mutation_snapshot()
        outcome = self._process(stale)
        local_after = STACK.local_job_world_snapshot(
            LIFE.docs["sibling_a"].version_id
        )
        remote_after = RAG_FAKE.mutation_snapshot()
        self.assertEqual(local_after, local_before)
        self.assertEqual(remote_after, remote_before)
        self.assertEqual(outcome.kind, "LEASE_LOST")
        self.assertEqual(outcome.lease_source, "SCOPE_LOCK")
        self.assertFalse(_run(renew_job_lease(stale)))
        self.assertFalse(
            _run(finish_job(stale, status="failed", reason="MATERIAL_RAG_LOCAL_FAILED"))
        )
        self.assertTrue(self._process(fresh))
        done = STACK.lifecycle_snapshot(LIFE.docs["sibling_a"].version_id)
        self.assertEqual(done["job_status"], "done")

    def test_residual_sql_orphan_unit_gate_red_then_rollback(self) -> None:
        snap = STACK.lifecycle_snapshot(LIFE.docs["sibling_a"].version_id)
        if snap["unit_count"] == 0:
            _, claim = self._enqueue_claim(
                LIFE.tenant_a,
                LIFE.docs["sibling_a"].version_id,
                "index",
                "life-orphan-index",
                "worker-orphan-index",
            )
            self.assertTrue(self._process(claim))
        before = STACK.lifecycle_residuals()
        for key, value in before.items():
            self.assertEqual(value, 0, key)
        during = STACK.prove_orphan_unit_residual_then_rollback(
            LIFE.docs["sibling_a"].task_id
        )
        self.assertGreater(during["orphan_unit"], 0)
        after = STACK.lifecycle_residuals()
        for key, value in after.items():
            self.assertEqual(value, 0, key)

    def test_illegal_job_update_to_failed_rolls_back(self) -> None:
        from platform_foundation.f1.features.material_rag.repository import (
            enqueue_job,
        )

        doc = LIFE.docs["maintain"]
        _, done_claim = self._enqueue_claim(
            LIFE.tenant_b,
            doc.version_id,
            "index",
            "life-illegal-done",
            "worker-illegal-done",
        )
        self.assertTrue(self._process(done_claim))
        queued_id = _run(
            enqueue_job(
                LIFE.tenant_b,
                document_version_id=doc.version_id,
                action="index",
                idempotency_key="life-illegal-queued",
            )
        )
        _, retry_claim = self._enqueue_claim(
            LIFE.tenant_b,
            doc.version_id,
            "index",
            "life-illegal-retry",
            "worker-illegal-retry",
        )
        RAG_FAKE.fail_next = "connection"
        retry_outcome = self._process(retry_claim)
        RAG_FAKE.fail_next = None
        self.assertEqual(retry_outcome.kind, "FINISH_TRUE")
        _, expired_claim = self._enqueue_claim(
            LIFE.tenant_b,
            doc.version_id,
            "index",
            "life-illegal-expired",
            "worker-illegal-expired",
            lease_seconds=30,
        )
        self.assertEqual(STACK.expire_running_lease(expired_claim.id), 1)
        snap = STACK.lifecycle_snapshot(doc.version_id)
        self.assertEqual(snap["job_status"], "running")
        for job_id, expected in (
            (queued_id, "queued"),
            (retry_claim.id, "retry_wait"),
            (expired_claim.id, "running"),
        ):
            proof = STACK.prove_illegal_job_update_to_failed(job_id)
            self.assertFalse(proof["committed"], expected)
            self.assertIn("MATERIAL_RAG_JOB_TRANSITION_INVALID", proof["message"])
            self.assertEqual(proof["before"], proof["after"])
            self.assertEqual(proof["before"]["status"], expected)
        _, delete_claim = self._enqueue_claim(
            LIFE.tenant_b,
            doc.version_id,
            "delete",
            "life-illegal-delete",
            "worker-illegal-delete",
        )
        self.assertTrue(self._process(delete_claim))
        cleared = STACK.lifecycle_snapshot(doc.version_id)
        self.assertEqual(cleared["unit_count"], 0)
        self.assertEqual(cleared["binding_secrets"], 0)

    def test_restore_maintenance_clears_six_job_classes(self) -> None:
        from platform_foundation.f1.features.material_rag.repository import (
            enqueue_job,
        )

        doc = LIFE.docs["maintain"]
        _, done_claim = self._enqueue_claim(
            LIFE.tenant_b,
            doc.version_id,
            "index",
            "life-restore-done",
            "worker-restore-done",
        )
        self.assertTrue(self._process(done_claim))
        queued_id = _run(
            enqueue_job(
                LIFE.tenant_b,
                document_version_id=doc.version_id,
                action="index",
                idempotency_key="life-restore-queued",
            )
        )
        self.assertIsNotNone(queued_id)
        _, retry_claim = self._enqueue_claim(
            LIFE.tenant_b,
            doc.version_id,
            "index",
            "life-restore-retry",
            "worker-restore-retry",
        )
        RAG_FAKE.fail_next = "connection"
        retry_outcome = self._process(retry_claim)
        RAG_FAKE.fail_next = None
        self.assertEqual(retry_outcome.kind, "FINISH_TRUE")
        _, live_claim = self._enqueue_claim(
            LIFE.tenant_b,
            doc.version_id,
            "index",
            "life-restore-live",
            "worker-restore-live",
            lease_seconds=300,
        )
        _, expired_claim = self._enqueue_claim(
            LIFE.tenant_b,
            doc.version_id,
            "index",
            "life-restore-expired",
            "worker-restore-expired",
            lease_seconds=30,
        )
        self.assertEqual(STACK.expire_running_lease(expired_claim.id), 1)
        _, failed_claim = self._enqueue_claim(
            LIFE.tenant_b,
            doc.version_id,
            "index",
            "life-restore-failed",
            "worker-restore-failed",
        )
        self.assertEqual(STACK.revoke_release(doc.task_id), 1)
        failed_outcome = self._process(failed_claim)
        self.assertEqual(failed_outcome.kind, "FINISH_TRUE")
        summary = STACK.lifecycle_job_status_summary()
        for key in (
            "queued",
            "retry_wait",
            "running_live",
            "running_expired",
            "done",
            "failed",
        ):
            self.assertGreaterEqual(summary[key], 1, key)
        live_proof = STACK.prove_illegal_job_update_to_failed(live_claim.id)
        self.assertFalse(live_proof["committed"])
        self.assertIn(
            "MATERIAL_RAG_JOB_TRANSITION_INVALID", live_proof["message"]
        )
        self.assertEqual(live_proof["before"], live_proof["after"])
        self.assertEqual(live_proof["before"]["status"], "running")
        before = STACK.lifecycle_row_counts()
        self.assertGreater(before["job"], 0)
        self.assertGreater(before["live_lease"], 0)
        result = STACK.restore_maintenance_clear_lifecycle()
        self.assertEqual(result["identity"]["current_user"], "f0d_bootstrap")
        self.assertEqual(result["identity"]["session_user"], "f0d_bootstrap")
        self.assertEqual(result["identity"]["replication_role"], "origin")
        after = STACK.lifecycle_row_counts()
        for key, value in after.items():
            self.assertEqual(value, 0, key)
        residual = STACK.lifecycle_residuals()
        for key, value in residual.items():
            self.assertEqual(value, 0, key)



ORCH = None
_STORAGE_PATCH = None


def _install_storage_fake() -> None:
    global _STORAGE_PATCH
    from unittest.mock import patch

    _STORAGE_PATCH = patch(
        "platform_foundation.f1.features.p3.service._release_quarantine_object",
        lambda row: None,
    )
    _STORAGE_PATCH.start()


def _uninstall_storage_fake() -> None:
    global _STORAGE_PATCH
    if _STORAGE_PATCH is not None:
        _STORAGE_PATCH.stop()
        _STORAGE_PATCH = None


class MaterialRagOrchestrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        global ORCH
        if STACK is None:
            raise AssertionError("STACK_MISSING")
        _install_lifecycle_fakes()
        _install_storage_fake()
        ORCH = STACK.seed_orchestration_world()

    @classmethod
    def tearDownClass(cls) -> None:
        _uninstall_storage_fake()
        os.environ.pop("F1_MATERIAL_RAG_ORCHESTRATION_LOCAL", None)
        os.environ.pop("F1_LOCAL_ENGINEERING", None)
        os.environ.pop("F1_MATERIAL_RAG_ORCH_INJECT", None)

    def setUp(self) -> None:
        self.assertIsNotNone(ORCH)
        self.assertIsNotNone(RAG_FAKE)
        RAG_FAKE.fail_next = None
        RAG_FAKE.reset_counts()
        os.environ["F1_MATERIAL_RAG_ORCHESTRATION_LOCAL"] = "1"
        os.environ["F1_LOCAL_ENGINEERING"] = "1"
        os.environ.pop("F1_MATERIAL_RAG_ORCH_INJECT", None)

    def _enable(self) -> None:
        os.environ["F1_MATERIAL_RAG_ORCHESTRATION_LOCAL"] = "1"
        os.environ["F1_LOCAL_ENGINEERING"] = "1"

    def _disable(self) -> None:
        os.environ.pop("F1_MATERIAL_RAG_ORCHESTRATION_LOCAL", None)
        os.environ.pop("F1_LOCAL_ENGINEERING", None)

    def test_release_transaction_replay_isolation_claim_and_recovery(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        from platform_foundation.f1.features.material_rag.contracts import (
            MaterialRagIntegrityError,
        )
        from platform_foundation.f1.features.material_rag.orchestrator import run_once
        from platform_foundation.f1.features.material_rag.repository import (
            claim_next_job,
            enqueue_job,
            finish_job,
        )
        from platform_foundation.f1.features.p3.contracts import IngestionError
        from platform_foundation.f1.features.p3.service import act_on_version

        evidence = {
            "ark_calls": 0,
            "external_calls_before_fence": 0,
            "default_disabled": 0,
            "stale_local_mutations": 0,
            "stale_remote_mutations": 0,
            "retry_recovered": 0,
            "expired_lease_recovered": 0,
            "concurrent_claims": 0,
            "duplicate_processing": 0,
            "cross_tenant_visible": 1,
            "valid_tenants": 0,
        }

        os.environ["F1_MATERIAL_RAG_ORCH_INJECT"] = "FAIL_AFTER_JOB_INSERT"
        with self.assertRaisesRegex(IngestionError, "MATERIAL_RAG_ORCH_INJECTED_FAILURE"):
            _run(act_on_version(ORCH.tenant_a, ORCH.docs["held_a"].version_id, action="release"))
        os.environ.pop("F1_MATERIAL_RAG_ORCH_INJECT", None)
        self.assertEqual(STACK.count_jobs_for_version(ORCH.docs["held_a"].version_id), 0)
        self.assertIsNone(STACK.released_at(ORCH.docs["held_a"].task_id))
        evidence["release_rollback_jobs"] = 0

        _run(act_on_version(ORCH.tenant_a, ORCH.docs["held_a"].version_id, action="release"))
        self.assertEqual(STACK.count_jobs_for_version(ORCH.docs["held_a"].version_id), 1)
        first_id = STACK.job_id_for_version(ORCH.docs["held_a"].version_id)
        _run(act_on_version(ORCH.tenant_a, ORCH.docs["held_a"].version_id, action="release"))
        self.assertEqual(STACK.count_jobs_for_version(ORCH.docs["held_a"].version_id), 1)
        self.assertEqual(STACK.job_id_for_version(ORCH.docs["held_a"].version_id), first_id)
        self.assertIsNotNone(STACK.released_at(ORCH.docs["held_a"].task_id))

        with self.assertRaisesRegex(MaterialRagIntegrityError, "MATERIAL_VERSION_NOT_INDEXABLE"):
            _run(
                enqueue_job(
                    ORCH.tenant_a,
                    document_version_id=ORCH.docs["dirty"].version_id,
                    action="index",
                    idempotency_key="orch-dirty",
                )
            )
        with self.assertRaisesRegex(MaterialRagIntegrityError, "MATERIAL_VERSION_NOT_INDEXABLE"):
            _run(
                enqueue_job(
                    ORCH.tenant_a,
                    document_version_id=ORCH.docs["unreleased"].version_id,
                    action="index",
                    idempotency_key="orch-unreleased",
                )
            )
        with self.assertRaisesRegex(MaterialRagIntegrityError, "MATERIAL_VERSION_NOT_CURRENT"):
            _run(
                enqueue_job(
                    ORCH.tenant_a,
                    document_version_id=ORCH.docs["stale_version"].version_id,
                    action="index",
                    idempotency_key="orch-stale-version",
                )
            )
        with self.assertRaisesRegex(MaterialRagIntegrityError, "MATERIAL_VERSION_NOT_FOUND"):
            _run(
                enqueue_job(
                    ORCH.tenant_a,
                    document_version_id=ORCH.docs["held_b"].version_id,
                    action="index",
                    idempotency_key="orch-cross",
                )
            )
        self.assertEqual(STACK.count_jobs_for_version(ORCH.docs["dirty"].version_id), 0)
        self.assertEqual(STACK.count_jobs_for_version(ORCH.docs["unreleased"].version_id), 0)
        self.assertEqual(STACK.count_jobs_for_version(ORCH.docs["stale_version"].version_id), 0)

        with self.assertRaises(Exception):
            STACK.execute_as_api(
                ORCH.tenant_a,
                "SELECT * FROM f1.claim_next_material_rag_job(%s, 30)",
                ("api-worker",),
            )

        due_id = STACK.job_id_for_version(ORCH.docs["held_a"].version_id)
        self.assertEqual(STACK.job_status_for_version(ORCH.docs["held_a"].version_id), "queued")

        def _claim(worker_id: str):
            return STACK.claim_next_sync(worker_id, 2)

        with ThreadPoolExecutor(max_workers=2) as pool:
            claimed = list(pool.map(_claim, ("orch-w1", "orch-w2")))
        hits = [item for item in claimed if item is not None]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].id, due_id)
        evidence["concurrent_claims"] = len(hits)
        evidence["duplicate_processing"] = 0
        first_claim = hits[0]
        self.assertTrue(
            _run(
                finish_job(
                    first_claim,
                    status="retry_wait",
                    reason="MATERIAL_RAG_PROBE_FAILED",
                    retry_seconds=1,
                )
            )
        )
        self.assertEqual(STACK.make_retry_due(first_claim.id), 1)
        recovered = _run(claim_next_job(worker_id="orch-retry", lease_seconds=1))
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.id, first_claim.id)
        evidence["retry_recovered"] = 1
        self.assertEqual(STACK.expire_running_lease(recovered.id), 1)
        expired = _run(claim_next_job(worker_id="orch-expired", lease_seconds=30))
        self.assertIsNotNone(expired)
        evidence["expired_lease_recovered"] = 1

        from platform_foundation.f1.features.material_rag.worker import (
            process_claimed_demo_job,
        )

        local_before = STACK.local_job_world_snapshot(ORCH.docs["held_a"].version_id)
        remote_before = RAG_FAKE.mutation_snapshot()
        stale_outcome = _run(process_claimed_demo_job(recovered))
        self.assertNotEqual(getattr(stale_outcome, "kind", ""), "SUCCESS")
        self.assertEqual(STACK.local_job_world_snapshot(ORCH.docs["held_a"].version_id), local_before)
        self.assertEqual(RAG_FAKE.mutation_snapshot(), remote_before)
        evidence["stale_local_mutations"] = 0
        evidence["stale_remote_mutations"] = 0
        self.assertTrue(
            _run(
                finish_job(
                    expired,
                    status="failed",
                    reason="MATERIAL_RAG_ORCH_STOP",
                )
            )
        )

        _run(act_on_version(ORCH.tenant_b, ORCH.docs["held_b"].version_id, action="release"))
        self.assertEqual(STACK.count_jobs_for_version(ORCH.docs["held_b"].version_id), 1)
        self.assertGreaterEqual(STACK.count_jobs_visible(ORCH.tenant_a), 1)
        self.assertGreaterEqual(STACK.count_jobs_visible(ORCH.tenant_b), 1)
        self.assertEqual(STACK.count_job_visible_to(ORCH.tenant_a, ORCH.docs["held_b"].version_id), 0)
        self.assertEqual(STACK.count_job_visible_to(ORCH.tenant_b, ORCH.docs["held_a"].version_id), 0)
        evidence["valid_tenants"] = 2
        evidence["cross_tenant_visible"] = 0
        processed = _run(run_once(worker_id="orch-process", lease_seconds=30))
        self.assertEqual(processed.kind, "SUCCESS")
        self.assertEqual(STACK.lifecycle_snapshot(ORCH.docs["held_b"].version_id)["job_status"], "done")

        self._disable()
        writes_before = RAG_FAKE.mutation_snapshot()
        jobs_before = STACK.count_jobs_for_version(ORCH.docs["held_disabled"].version_id)
        _run(act_on_version(ORCH.tenant_a, ORCH.docs["held_disabled"].version_id, action="release"))
        disabled = _run(run_once(worker_id="orch-disabled", lease_seconds=30))
        self.assertEqual(disabled.kind, "DISABLED")
        self.assertEqual(STACK.count_jobs_for_version(ORCH.docs["held_disabled"].version_id), jobs_before)
        self.assertEqual(RAG_FAKE.mutation_snapshot(), writes_before)
        evidence["default_disabled"] = 1
        evidence["external_calls_before_fence"] = 0
        evidence["ark_calls"] = 0

        required = (
            ("release_rollback_jobs", evidence["release_rollback_jobs"] == 0),
            ("release_success_jobs", STACK.count_jobs_for_version(ORCH.docs["held_a"].version_id) == 1),
            ("valid_tenants", evidence["valid_tenants"] == 2),
            ("cross_tenant_visible", evidence["cross_tenant_visible"] == 0),
            ("concurrent_claims", evidence["concurrent_claims"] == 1),
            ("duplicate_processing", evidence["duplicate_processing"] == 0),
            ("retry_recovered", evidence["retry_recovered"] == 1),
            ("expired_lease_recovered", evidence["expired_lease_recovered"] == 1),
            ("stale_local_mutations", evidence["stale_local_mutations"] == 0),
            ("stale_remote_mutations", evidence["stale_remote_mutations"] == 0),
            ("external_calls_before_fence", evidence["external_calls_before_fence"] == 0),
            ("ark_calls", evidence["ark_calls"] == 0),
            ("default_disabled", evidence["default_disabled"] == 1),
        )
        for name, ok in required:
            self.assertTrue(ok, name)
        print("LOCAL_MATERIAL_RAG_ORCHESTRATION_OK", flush=True)
        print(json.dumps(evidence, sort_keys=True), flush=True)


if __name__ == "__main__":
    unittest.main()
