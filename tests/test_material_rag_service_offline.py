"""Offline production-service contract for the material-RAG trust boundary.

Fake objects implement repository/transport ports only.  They do not replace
the production service.  No skip, xfail, live Ark, RAGFlow, or Docker.
"""
from __future__ import annotations

import asyncio
import ast
import dataclasses
import hashlib
import json
import sys
import unittest
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PUBLIC_QA_ROUTER = ROOT / "src/platform_foundation/f1/api/routers/material_qa.py"
QA_SERVICE = ROOT / "src/platform_foundation/f1/qa_service.py"

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
    "ds_must_not_leak",
    "chunk_must_not_leak",
    "scope_must_not_leak",
    "SELECT ",
    "FROM f1.",
    "Traceback",
    "/datasets/",
    "ragflow_token",
)
DATASET_A = "ds_must_not_leak0123456789abcdef"
DATASET_B = "chunk_must_not_leak0123456789abcd"
DATASET_C = "scope_must_not_leak0123456789abcde"
DATASET_OTHER = "ffffffffffffffffffffffffffffffff"


def _sha(label: str) -> str:
    return hashlib.sha256(f"offline-trust|{label}".encode("utf-8")).hexdigest()


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


@dataclass
class FakeUnit:
    enterprise_id: uuid.UUID
    canonical_unit_id: uuid.UUID
    knowledge_scope_id: uuid.UUID
    document_record_id: uuid.UUID
    document_version_id: uuid.UUID
    source_sha256: str
    page_number: int
    body_sha256: str
    body: str
    scope_kind: str
    document_name: str
    version_number: int
    scan_verdict: str = "clean"
    preview_status: str = "ready"
    object_state: str = "ready"
    processing_stage: str = "ready"
    pipeline_kind: str = "controlled_ingestion"
    status: str = "done"
    quarantine_status: str = "released"
    released: bool = True
    stale: bool = False
    revoked: bool = False


@dataclass(frozen=True)
class FakeBinding:
    knowledge_scope_id: uuid.UUID
    dataset_ref: str


class FakeMaterialRagRepository:
    def __init__(self) -> None:
        self.calls = {
            "load_provider_scope_id": 0,
            "load_client_scope_id": 0,
            "load_ready_bindings": 0,
            "load_released_units": 0,
            "prove_exact_authorized_fragment": 0,
        }
        self.provider_scopes: dict[uuid.UUID, uuid.UUID] = {}
        self.client_scopes: dict[tuple[uuid.UUID, uuid.UUID], uuid.UUID] = {}
        self.bindings: dict[uuid.UUID, str] = {}
        self.units: list[FakeUnit] = []

    def io_count(self) -> int:
        return sum(self.calls.values())

    async def load_provider_scope_id(self, tenant) -> uuid.UUID | None:
        self.calls["load_provider_scope_id"] += 1
        return self.provider_scopes.get(tenant.enterprise_id)

    async def load_client_scope_id(
        self, tenant, client_account_id: uuid.UUID
    ) -> uuid.UUID | None:
        self.calls["load_client_scope_id"] += 1
        return self.client_scopes.get((tenant.enterprise_id, client_account_id))

    async def load_ready_bindings(self, tenant, context) -> tuple[FakeBinding, ...]:
        self.calls["load_ready_bindings"] += 1
        result: list[FakeBinding] = []
        for scope_id in context._scope_ids:
            dataset_ref = self.bindings.get(scope_id)
            if dataset_ref is None:
                continue
            if scope_id == self.provider_scopes.get(tenant.enterprise_id):
                result.append(FakeBinding(scope_id, dataset_ref))
                continue
            owner = None
            for (enterprise_id, _client_id), client_scope in self.client_scopes.items():
                if enterprise_id == tenant.enterprise_id and client_scope == scope_id:
                    owner = enterprise_id
                    break
            if owner == tenant.enterprise_id:
                result.append(FakeBinding(scope_id, dataset_ref))
        return tuple(result)

    async def load_released_units(
        self, tenant, context, unit_ids: tuple[uuid.UUID, ...]
    ) -> tuple[FakeUnit, ...]:
        self.calls["load_released_units"] += 1
        allowed = set(context._scope_ids)
        wanted = set(unit_ids)
        result: list[FakeUnit] = []
        for unit in self.units:
            if unit.canonical_unit_id not in wanted:
                continue
            if unit.enterprise_id != tenant.enterprise_id:
                continue
            if unit.knowledge_scope_id not in allowed:
                continue
            if not _unit_is_indexable(unit):
                continue
            result.append(unit)
        return tuple(result)

    async def prove_exact_authorized_fragment(self, *args: object, **kwargs: object) -> bool:
        self.calls["prove_exact_authorized_fragment"] += 1
        return False


class FakeMaterialRagTransport:
    def __init__(self) -> None:
        self.calls = 0
        self.queries: list[tuple[str, tuple[FakeBinding, ...], int]] = []
        self.candidates: tuple[Any, ...] = ()
        self.error: BaseException | None = None

    async def retrieve_candidates(
        self, query: str, datasets: tuple[FakeBinding, ...], limit: int
    ):
        self.calls += 1
        self.queries.append((query, datasets, limit))
        if self.error is not None:
            raise self.error
        return self.candidates


def _unit_is_indexable(unit: FakeUnit) -> bool:
    return (
        not unit.stale
        and not unit.revoked
        and unit.released
        and unit.scan_verdict == "clean"
        and unit.preview_status == "ready"
        and unit.object_state == "ready"
        and unit.processing_stage == "ready"
        and unit.pipeline_kind == "controlled_ingestion"
        and unit.status == "done"
        and unit.quarantine_status == "released"
    )


def _candidate_for(unit: FakeUnit, **overrides: object):
    from platform_foundation.f1.features.material_rag.ragflow_adapter import (
        RemoteCandidate,
    )

    payload = {
        "canonical_unit_id": unit.canonical_unit_id,
        "knowledge_scope_id": unit.knowledge_scope_id,
        "document_record_id": unit.document_record_id,
        "document_version_id": unit.document_version_id,
        "source_sha256": unit.source_sha256,
        "page_number": unit.page_number,
        "body_sha256": unit.body_sha256,
    }
    payload.update(overrides)
    return RemoteCandidate(**payload)  # type: ignore[arg-type]


def _ordinary_extractive_answer():
    from platform_foundation.f1.features.material_rag.contracts import (
        MaterialEvidence,
        MaterialExtractiveAnswer,
    )

    snippet = "普通发布材料要求作业前复核应急职责。"
    evidence = MaterialEvidence(
        canonical_unit_id=uuid.UUID("62000000-0000-4000-8000-000000000005"),
        document_record_id=uuid.UUID("62000000-0000-4000-8000-000000000006"),
        document_version_id=uuid.UUID("62000000-0000-4000-8000-000000000007"),
        document_name="普通发布材料.pdf",
        version_number=1,
        source_sha256=_sha("ordinary-released-pdf"),
        page_number=2,
        body_sha256=hashlib.sha256(snippet.encode("utf-8")).hexdigest(),
        snippet=snippet,
        scope_kind="service_provider",
    )
    return MaterialExtractiveAnswer(snippet, (evidence,))


class OfflineWorld:
    def __init__(self) -> None:
        from platform_foundation.f1.auth import Tenant
        from platform_foundation.f1.features.material_rag.contracts import (
            RetrievalContext,
        )
        from platform_foundation.f1.features.material_rag.service import (
            MaterialRetrievalService,
        )

        self.enterprise_a = uuid.UUID("61000000-0000-4000-8000-00000000000a")
        self.enterprise_b = uuid.UUID("61000000-0000-4000-8000-00000000000b")
        self.provider_a = uuid.UUID("61000000-0000-4000-8000-000000000010")
        self.client_a_scope = uuid.UUID("61000000-0000-4000-8000-000000000011")
        self.client_b_scope = uuid.UUID("61000000-0000-4000-8000-000000000012")
        self.provider_b = uuid.UUID("61000000-0000-4000-8000-000000000013")
        self.client_a_id = uuid.UUID("61000000-0000-4000-8000-000000000021")
        self.client_b_id = uuid.UUID("61000000-0000-4000-8000-000000000022")
        self.unknown_client = uuid.UUID("61000000-0000-4000-8000-0000000000ff")
        self.tenant_a = Tenant(
            enterprise_id=self.enterprise_a, sub="offline-a", roles=("enterprise_admin",)
        )
        self.tenant_b = Tenant(
            enterprise_id=self.enterprise_b, sub="offline-b", roles=("enterprise_admin",)
        )
        self.repo = FakeMaterialRagRepository()
        self.transport = FakeMaterialRagTransport()
        self.repo.provider_scopes[self.enterprise_a] = self.provider_a
        self.repo.provider_scopes[self.enterprise_b] = self.provider_b
        self.repo.client_scopes[(self.enterprise_a, self.client_a_id)] = self.client_a_scope
        self.repo.client_scopes[(self.enterprise_a, self.client_b_id)] = self.client_b_scope
        self.repo.bindings[self.provider_a] = DATASET_A
        self.repo.bindings[self.client_a_scope] = DATASET_B
        self.repo.bindings[self.client_b_scope] = DATASET_C
        self.repo.bindings[self.provider_b] = DATASET_OTHER
        self.provider_unit = self._unit(
            self.enterprise_a,
            self.provider_a,
            "service_provider",
            "Provider Policy",
            "共享政策要求：作业前复核应急职责。",
            page_number=1,
            code="11",
        )
        self.client_a_unit = self._unit(
            self.enterprise_a,
            self.client_a_scope,
            "client",
            "Client A Manual",
            "客户甲现场复核要求：作业许可。",
            page_number=2,
            code="12",
        )
        self.client_b_unit = self._unit(
            self.enterprise_a,
            self.client_b_scope,
            "client",
            "Client B Manual",
            "客户乙隔离要求：复工前确认。",
            page_number=3,
            code="13",
        )
        self.foreign_unit = self._unit(
            self.enterprise_b,
            self.provider_b,
            "service_provider",
            "Foreign Policy",
            "外租户材料不得出现。",
            page_number=4,
            code="14",
        )
        self.repo.units = [
            self.provider_unit,
            self.client_a_unit,
            self.client_b_unit,
            self.foreign_unit,
        ]
        self.provider_context = RetrievalContext(
            enterprise_id=self.enterprise_a,
            kind="service_provider",
            client_account_id=None,
            scope_ids=(self.provider_a,),
        )
        self.client_a_context = RetrievalContext(
            enterprise_id=self.enterprise_a,
            kind="client",
            client_account_id=self.client_a_id,
            scope_ids=(self.provider_a, self.client_a_scope),
        )
        self.client_b_context = RetrievalContext(
            enterprise_id=self.enterprise_a,
            kind="client",
            client_account_id=self.client_b_id,
            scope_ids=(self.provider_a, self.client_b_scope),
        )
        self.service = MaterialRetrievalService(
            repository=self.repo, transport=self.transport
        )

    def _unit(
        self,
        enterprise_id: uuid.UUID,
        scope_id: uuid.UUID,
        scope_kind: str,
        document_name: str,
        body: str,
        *,
        page_number: int,
        code: str,
    ) -> FakeUnit:
        return FakeUnit(
            enterprise_id=enterprise_id,
            canonical_unit_id=uuid.UUID(f"61000000-0000-4000-8000-0000000000{code}"),
            knowledge_scope_id=scope_id,
            document_record_id=uuid.UUID(
                f"61000000-0000-4000-8000-0000000001{code}"
            ),
            document_version_id=uuid.UUID(
                f"61000000-0000-4000-8000-0000000002{code}"
            ),
            source_sha256=_sha(f"source-{code}"),
            page_number=page_number,
            body_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            body=body,
            scope_kind=scope_kind,
            document_name=document_name,
            version_number=1,
        )

    def io_count(self) -> int:
        return self.repo.io_count() + self.transport.calls

    def snapshot_io(self) -> tuple[int, dict[str, int], int]:
        return self.io_count(), dict(self.repo.calls), self.transport.calls


class MaterialRagServiceOfflineTests(unittest.TestCase):
    def test_provider_query_returns_only_provider_evidence(self) -> None:
        from platform_foundation.f1.features.material_rag.contracts import (
            MaterialEvidence,
        )
        from platform_foundation.f1.features.material_rag.security import (
            PROVIDER_RETRIEVAL_QUERY_TEXT,
        )

        world = OfflineWorld()
        world.transport.candidates = (
            _candidate_for(world.client_a_unit),
            _candidate_for(world.provider_unit),
            _candidate_for(world.client_b_unit),
        )
        result = _run(
            world.service.retrieve_registered(
                PROVIDER_RETRIEVAL_QUERY_TEXT,
                world.tenant_a,
                world.provider_context,
                request_id=uuid.UUID("61000000-0000-4000-8000-000000000801"),
            )
        )
        self.assertIsNone(result.refusal_reason)
        self.assertEqual(len(result.evidence), 1)
        item = result.evidence[0]
        self.assertIsInstance(item, MaterialEvidence)
        self.assertEqual(item.scope_kind, "service_provider")
        self.assertEqual(item.canonical_unit_id, world.provider_unit.canonical_unit_id)
        self.assertEqual(item.document_name, "Provider Policy")
        self.assertNotEqual(item.scope_kind, "client")
        datasets = world.transport.queries[0][1]
        self.assertEqual([binding.dataset_ref for binding in datasets], [DATASET_A])

    def test_client_query_allows_provider_and_current_client_only(self) -> None:
        from platform_foundation.f1.features.material_rag.contracts import (
            MaterialRagContextNotFound,
        )
        from platform_foundation.f1.features.material_rag.security import (
            CLIENT_A_RETRIEVAL_QUERY_TEXT,
        )

        world = OfflineWorld()
        world.transport.candidates = (
            _candidate_for(world.client_b_unit),
            _candidate_for(world.provider_unit),
            _candidate_for(world.client_a_unit),
            _candidate_for(world.foreign_unit),
        )
        result = _run(
            world.service.retrieve_registered(
                CLIENT_A_RETRIEVAL_QUERY_TEXT,
                world.tenant_a,
                world.client_a_context,
                request_id=uuid.UUID("61000000-0000-4000-8000-000000000802"),
            )
        )
        self.assertIsNone(result.refusal_reason)
        ids = [item.canonical_unit_id for item in result.evidence]
        self.assertEqual(
            ids,
            [
                world.provider_unit.canonical_unit_id,
                world.client_a_unit.canonical_unit_id,
            ],
        )
        kinds = {item.scope_kind for item in result.evidence}
        self.assertEqual(kinds, {"service_provider", "client"})
        self.assertNotIn(world.client_b_unit.canonical_unit_id, ids)
        self.assertNotIn(world.foreign_unit.canonical_unit_id, ids)
        dataset_refs = [binding.dataset_ref for binding in world.transport.queries[0][1]]
        self.assertEqual(dataset_refs, [DATASET_A, DATASET_B])
        self.assertNotIn(DATASET_C, dataset_refs)

        before = world.snapshot_io()
        provider_only = _run(
            world.service.derive_retrieval_context(world.tenant_a, None)
        )
        self.assertEqual(provider_only.kind, "service_provider")
        self.assertIsNone(provider_only.client_account_id)
        self.assertEqual(world.repo.calls["load_client_scope_id"], 0)

        with self.assertRaisesRegex(MaterialRagContextNotFound, "MATERIAL_CONTEXT_NOT_FOUND"):
            _run(
                world.service.derive_retrieval_context(
                    world.tenant_a, world.unknown_client
                )
            )
        self.assertEqual(world.transport.calls, before[2])
        self.assertEqual(world.repo.calls["load_ready_bindings"], before[1]["load_ready_bindings"])
        self.assertEqual(
            world.repo.calls["load_released_units"], before[1]["load_released_units"]
        )

    def test_safe_freeform_query_retrieves_and_invalid_context_rejects_before_io(
        self,
    ) -> None:
        from platform_foundation.f1.features.material_rag.contracts import (
            RetrievalContext,
        )

        world = OfflineWorld()
        world.transport.candidates = (_candidate_for(world.provider_unit),)
        request_id = uuid.UUID("61000000-0000-4000-8000-000000000803")
        query = "普通材料有哪些作业前要求？"
        result = _run(
            world.service.retrieve(
                query,
                world.tenant_a,
                world.provider_context,
                request_id=request_id,
            )
        )
        self.assertIsNone(result.refusal_reason)
        self.assertEqual(
            [item.canonical_unit_id for item in result.evidence],
            [world.provider_unit.canonical_unit_id],
        )
        self.assertEqual(
            result.evidence[0].source_sha256, world.provider_unit.source_sha256
        )
        self.assertEqual(world.transport.queries[0][0], query)

        cases = [
            ("", world.provider_context, world.tenant_a),
            (query, world.provider_context, world.tenant_b),
        ]
        for invalid_query, context, tenant in cases:
            with self.subTest(query=invalid_query, tenant=tenant.sub):
                before = world.snapshot_io()
                with self.assertRaisesRegex(
                    ValueError,
                    "MATERIAL_CONTEXT_INVALID",
                ):
                    _run(
                        world.service.retrieve(
                            invalid_query, tenant, context, request_id=request_id
                        )
                    )
                self.assertEqual(world.snapshot_io(), before)

        invalid = RetrievalContext(
            enterprise_id=world.enterprise_b,
            kind="service_provider",
            client_account_id=None,
            scope_ids=(world.provider_b,),
        )
        before = world.snapshot_io()
        with self.assertRaisesRegex(
            ValueError,
            "MATERIAL_CONTEXT_INVALID",
        ):
            _run(
                world.service.retrieve(
                    query,
                    world.tenant_a,
                    invalid,
                    request_id=request_id,
                )
            )
        self.assertEqual(world.snapshot_io(), before)
        before = world.snapshot_io()
        with self.assertRaisesRegex(
            ValueError,
            "MATERIAL_CONTEXT_INVALID",
        ):
            _run(
                world.service.retrieve(
                    query,
                    world.tenant_a,
                    object(),  # type: ignore[arg-type]
                    request_id=request_id,
                )
            )
        self.assertEqual(world.snapshot_io(), before)
        self.assertEqual(world.repo.calls["prove_exact_authorized_fragment"], 0)

    def test_untrusted_remote_candidates_become_zero_evidence(self) -> None:
        from platform_foundation.f1.features.material_rag.contracts import (
            REFUSE_REJECTED,
        )
        from platform_foundation.f1.features.material_rag.security import (
            CLIENT_A_RETRIEVAL_QUERY_TEXT,
            PROVIDER_RETRIEVAL_QUERY_TEXT,
        )

        def refuse(world: OfflineWorld, candidates, context, query, suffix: str):
            world.transport.candidates = candidates
            world.transport.error = None
            result = _run(
                world.service.retrieve_registered(
                    query,
                    world.tenant_a,
                    context,
                    request_id=uuid.UUID(f"61000000-0000-4000-8000-00000000081{suffix}"),
                )
            )
            self.assertEqual(result.evidence, ())
            self.assertEqual(result.refusal_reason, REFUSE_REJECTED)

        world = OfflineWorld()
        forged_sha = _candidate_for(
            world.provider_unit, body_sha256="0" * 64
        )
        forged_tag = _candidate_for(
            world.provider_unit,
            knowledge_scope_id=world.client_a_scope,
        )
        refuse(
            world,
            (_candidate_for(world.foreign_unit),),
            world.provider_context,
            PROVIDER_RETRIEVAL_QUERY_TEXT,
            "1",
        )
        refuse(
            world,
            (_candidate_for(world.client_a_unit),),
            world.provider_context,
            PROVIDER_RETRIEVAL_QUERY_TEXT,
            "2",
        )
        world.client_a_unit.stale = True
        refuse(
            world,
            (_candidate_for(world.client_a_unit),),
            world.client_a_context,
            CLIENT_A_RETRIEVAL_QUERY_TEXT,
            "3",
        )
        world.client_a_unit.stale = False
        world.client_a_unit.revoked = True
        world.client_a_unit.quarantine_status = "held"
        refuse(
            world,
            (_candidate_for(world.client_a_unit),),
            world.client_a_context,
            CLIENT_A_RETRIEVAL_QUERY_TEXT,
            "4",
        )
        world.client_a_unit.revoked = False
        world.client_a_unit.quarantine_status = "released"
        world.client_a_unit.scan_verdict = "infected"
        refuse(
            world,
            (_candidate_for(world.client_a_unit),),
            world.client_a_context,
            CLIENT_A_RETRIEVAL_QUERY_TEXT,
            "5",
        )
        world.client_a_unit.scan_verdict = "clean"
        world.client_a_unit.preview_status = "pending"
        refuse(
            world,
            (_candidate_for(world.client_a_unit),),
            world.client_a_context,
            CLIENT_A_RETRIEVAL_QUERY_TEXT,
            "6",
        )
        world.client_a_unit.preview_status = "ready"
        refuse(
            world,
            (forged_sha, forged_tag),
            world.provider_context,
            PROVIDER_RETRIEVAL_QUERY_TEXT,
            "7",
        )

    def test_duplicate_and_shuffled_candidates_are_stable(self) -> None:
        from platform_foundation.f1.features.material_rag.security import (
            CLIENT_A_RETRIEVAL_QUERY_TEXT,
        )

        world = OfflineWorld()
        world.transport.candidates = (
            _candidate_for(world.client_a_unit),
            _candidate_for(world.provider_unit),
            _candidate_for(world.client_a_unit),
            _candidate_for(world.provider_unit),
        )
        first = _run(
            world.service.retrieve_registered(
                CLIENT_A_RETRIEVAL_QUERY_TEXT,
                world.tenant_a,
                world.client_a_context,
                request_id=uuid.UUID("61000000-0000-4000-8000-000000000804"),
            )
        )
        self.assertEqual(
            [item.canonical_unit_id for item in first.evidence],
            [
                world.client_a_unit.canonical_unit_id,
                world.provider_unit.canonical_unit_id,
            ],
        )
        world.transport.candidates = (
            _candidate_for(world.provider_unit),
            _candidate_for(world.client_a_unit),
            _candidate_for(world.provider_unit),
        )
        second = _run(
            world.service.retrieve_registered(
                CLIENT_A_RETRIEVAL_QUERY_TEXT,
                world.tenant_a,
                world.client_a_context,
                request_id=uuid.UUID("61000000-0000-4000-8000-000000000805"),
            )
        )
        self.assertEqual(
            [item.canonical_unit_id for item in second.evidence],
            [
                world.provider_unit.canonical_unit_id,
                world.client_a_unit.canonical_unit_id,
            ],
        )

    def test_legal_candidates_emit_product_evidence_without_physical_ids(self) -> None:
        from platform_foundation.f1.features.material_rag.contracts import (
            MaterialEvidence,
        )
        from platform_foundation.f1.features.material_rag.security import (
            PROVIDER_RETRIEVAL_QUERY_TEXT,
        )

        world = OfflineWorld()
        world.transport.candidates = (_candidate_for(world.provider_unit),)
        result = _run(
            world.service.retrieve_registered(
                PROVIDER_RETRIEVAL_QUERY_TEXT,
                world.tenant_a,
                world.provider_context,
                request_id=uuid.UUID("61000000-0000-4000-8000-000000000806"),
            )
        )
        self.assertEqual(len(result.evidence), 1)
        item = result.evidence[0]
        self.assertIsInstance(item, MaterialEvidence)
        payload = dataclasses.asdict(item)
        names = set(payload)
        self.assertTrue(
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
                "scope_kind",
            }.issubset(names)
        )
        self.assertTrue(names.isdisjoint(FORBIDDEN_PHYSICAL_KEYS))
        _walk_forbidden(payload)
        rendered = json.dumps(payload, default=str)
        for token in (DATASET_A, DATASET_B, DATASET_C, str(world.provider_a)):
            self.assertNotIn(token, rendered)
        self.assertNotIn(str(world.provider_a), repr(item))

    def test_same_request_is_idempotent_and_context_switch_conflicts(self) -> None:
        from platform_foundation.f1.features.material_rag.contracts import (
            RetrievalContext,
        )
        from platform_foundation.f1.features.material_rag.security import (
            CLIENT_A_RETRIEVAL_QUERY_TEXT,
            PROVIDER_RETRIEVAL_QUERY_TEXT,
        )

        world = OfflineWorld()
        world.transport.candidates = (_candidate_for(world.provider_unit),)
        request_id = uuid.UUID("61000000-0000-4000-8000-000000000807")
        first = _run(
            world.service.retrieve_registered(
                PROVIDER_RETRIEVAL_QUERY_TEXT,
                world.tenant_a,
                world.provider_context,
                request_id=request_id,
            )
        )
        after_first = world.snapshot_io()
        self.assertGreater(after_first[2], 0)
        second = _run(
            world.service.retrieve_registered(
                PROVIDER_RETRIEVAL_QUERY_TEXT,
                world.tenant_a,
                world.provider_context,
                request_id=request_id,
            )
        )
        self.assertEqual(world.snapshot_io(), after_first)
        self.assertEqual(
            [item.canonical_unit_id for item in second.evidence],
            [item.canonical_unit_id for item in first.evidence],
        )
        tenant_b_context = RetrievalContext(
            enterprise_id=world.enterprise_b,
            kind="service_provider",
            client_account_id=None,
            scope_ids=(world.provider_b,),
        )
        conflicts = [
            (
                CLIENT_A_RETRIEVAL_QUERY_TEXT,
                world.tenant_a,
                world.provider_context,
            ),
            (
                PROVIDER_RETRIEVAL_QUERY_TEXT,
                world.tenant_b,
                tenant_b_context,
            ),
            (
                PROVIDER_RETRIEVAL_QUERY_TEXT,
                world.tenant_a,
                world.client_a_context,
            ),
        ]
        for query, tenant, context in conflicts:
            with self.subTest(query=query, tenant=tenant.sub, kind=context.kind):
                with self.assertRaisesRegex(Exception, "REQUEST_ID_CONFLICT"):
                    _run(
                        world.service.retrieve_registered(
                            query, tenant, context, request_id=request_id
                        )
                    )
                self.assertEqual(world.snapshot_io(), after_first)

    def test_transport_failure_does_not_return_old_or_expanded_evidence(self) -> None:
        from platform_foundation.f0j1.ragflow_client import RagFlowProbeError
        from platform_foundation.f1.features.material_rag.contracts import (
            MaterialRagIntegrityError,
            REFUSE_UNAVAILABLE,
        )
        from platform_foundation.f1.features.material_rag.security import (
            PROVIDER_RETRIEVAL_QUERY_TEXT,
        )

        world = OfflineWorld()
        world.transport.candidates = (
            _candidate_for(world.provider_unit),
            _candidate_for(world.client_a_unit),
        )
        ok = _run(
            world.service.retrieve_registered(
                PROVIDER_RETRIEVAL_QUERY_TEXT,
                world.tenant_a,
                world.provider_context,
                request_id=uuid.UUID("61000000-0000-4000-8000-000000000808"),
            )
        )
        self.assertEqual(len(ok.evidence), 1)

        world.transport.error = TimeoutError("timed out contacting adapter")
        failed = _run(
            world.service.retrieve_registered(
                PROVIDER_RETRIEVAL_QUERY_TEXT,
                world.tenant_a,
                world.provider_context,
                request_id=uuid.UUID("61000000-0000-4000-8000-000000000809"),
            )
        )
        self.assertEqual(failed.evidence, ())
        self.assertEqual(failed.refusal_reason, REFUSE_UNAVAILABLE)
        self.assertNotEqual(failed.evidence, ok.evidence)

        world.transport.error = ConnectionError("adapter down")
        failed_conn = _run(
            world.service.retrieve_registered(
                PROVIDER_RETRIEVAL_QUERY_TEXT,
                world.tenant_a,
                world.provider_context,
                request_id=uuid.UUID("61000000-0000-4000-8000-00000000080a"),
            )
        )
        self.assertEqual(failed_conn.evidence, ())
        self.assertEqual(failed_conn.refusal_reason, REFUSE_UNAVAILABLE)

        world.transport.error = RagFlowProbeError("RETRIEVAL_FAILED", status=503)
        failed_probe = _run(
            world.service.retrieve_registered(
                PROVIDER_RETRIEVAL_QUERY_TEXT,
                world.tenant_a,
                world.provider_context,
                request_id=uuid.UUID("61000000-0000-4000-8000-00000000080b"),
            )
        )
        self.assertEqual(failed_probe.evidence, ())
        self.assertEqual(failed_probe.refusal_reason, REFUSE_UNAVAILABLE)

        world.transport.error = MaterialRagIntegrityError(
            "MATERIAL_RAG_DATASET_BINDING_INVALID"
        )
        with self.assertRaisesRegex(
            MaterialRagIntegrityError, "MATERIAL_RAG_DATASET_BINDING_INVALID"
        ):
            _run(
                world.service.retrieve_registered(
                    PROVIDER_RETRIEVAL_QUERY_TEXT,
                    world.tenant_a,
                    world.provider_context,
                    request_id=uuid.UUID("61000000-0000-4000-8000-00000000080c"),
                )
            )

        world.transport.error = None
        world.transport.candidates = (
            _candidate_for(world.provider_unit, body_sha256="1" * 64),
            _candidate_for(world.provider_unit),
        )
        mixed = _run(
            world.service.retrieve_registered(
                PROVIDER_RETRIEVAL_QUERY_TEXT,
                world.tenant_a,
                world.provider_context,
                request_id=uuid.UUID("61000000-0000-4000-8000-00000000080d"),
            )
        )
        self.assertEqual(
            [item.canonical_unit_id for item in mixed.evidence],
            [world.provider_unit.canonical_unit_id],
        )


class PublicMaterialQaBackendGateTests(unittest.TestCase):
    def test_safe_question_persists_extractive_answer_and_citations(self) -> None:
        from platform_foundation.f1 import qa_service
        from platform_foundation.f1.auth import Tenant
        from platform_foundation.f1.features.material_rag.contracts import (
            RetrievalContext,
        )
        from platform_foundation.f1.features import material_rag

        enterprise = uuid.UUID("62000000-0000-4000-8000-000000000001")
        request_id = uuid.UUID("62000000-0000-4000-8000-000000000002")
        owner_token = uuid.UUID("62000000-0000-4000-8000-000000000003")
        tenant = Tenant(enterprise_id=enterprise, sub="qa-offline", roles=())
        context = RetrievalContext(
            enterprise_id=enterprise,
            kind="service_provider",
            client_account_id=None,
            scope_ids=(uuid.UUID("62000000-0000-4000-8000-000000000004"),),
        )
        reservation = qa_service.QaReservation(
            qa_service.ReservationState.CLAIMED,
            request_id,
            owner_token=owner_token,
            attempt=1,
        )
        extracted = _ordinary_extractive_answer()
        extractive = AsyncMock(return_value=extracted)
        question = "普通材料有哪些作业前要求？"
        with (
            patch.object(
                qa_service, "reserve_request", AsyncMock(return_value=reservation)
            ) as reserve,
            patch.object(qa_service, "complete_request", AsyncMock()) as complete,
            patch.object(material_rag, "run_extractive_answer", extractive),
        ):
            outcome = _run(
                qa_service.ask_material_question(
                    question, request_id, tenant, context
                )
            )
        self.assertEqual(outcome.answer, extracted.answer)
        self.assertEqual(outcome.citations, extracted.citation_dicts())
        self.assertIsNone(outcome.refusal_reason)
        _walk_forbidden(outcome.to_dict())
        extractive.assert_awaited_once_with(question, tenant, context)
        reserve.assert_awaited_once()
        complete.assert_awaited_once()
        self.assertEqual(
            reserve.await_args.kwargs["query_context_sha256"], context.context_sha256
        )

    def test_replay_binds_original_context_and_switch_conflicts(self) -> None:
        from platform_foundation.f1 import qa_service
        from platform_foundation.f1.auth import Tenant
        from platform_foundation.f1.features.material_rag.contracts import (
            RetrievalContext,
        )

        enterprise = uuid.UUID("62100000-0000-4000-8000-000000000001")
        request_id = uuid.UUID("62100000-0000-4000-8000-000000000002")
        tenant = Tenant(enterprise_id=enterprise, sub="qa-replay", roles=())
        provider = RetrievalContext(
            enterprise_id=enterprise,
            kind="service_provider",
            client_account_id=None,
            scope_ids=(uuid.UUID("62100000-0000-4000-8000-000000000010"),),
        )
        client = RetrievalContext(
            enterprise_id=enterprise,
            kind="client",
            client_account_id=uuid.UUID("62100000-0000-4000-8000-000000000021"),
            scope_ids=(
                uuid.UUID("62100000-0000-4000-8000-000000000010"),
                uuid.UUID("62100000-0000-4000-8000-000000000011"),
            ),
        )
        store = _FakeQaClaimStore()
        question = "同一问题换客户必须冲突"
        extracted = _ordinary_extractive_answer()
        extractive = AsyncMock(return_value=extracted)
        from platform_foundation.f1.features import material_rag

        with (
            patch.object(qa_service, "reserve_request", store.reserve),
            patch.object(qa_service, "complete_request", store.complete),
            patch.object(material_rag, "run_extractive_answer", extractive),
        ):
            first = _run(
                qa_service.ask_material_question(
                    question, request_id, tenant, provider
                )
            )
            replay = _run(
                qa_service.ask_material_question(
                    question, request_id, tenant, provider
                )
            )
            with self.assertRaises(qa_service.RequestIdConflict):
                _run(
                    qa_service.ask_material_question(
                        question, request_id, tenant, client
                    )
                )
        self.assertEqual(first.answer, extracted.answer)
        self.assertEqual(first.citations, extracted.citation_dicts())
        self.assertIsNone(first.refusal_reason)
        self.assertEqual(first.to_dict(), replay.to_dict())
        self.assertEqual(store.complete_calls, 1)
        self.assertEqual(store.reserve_calls, 3)
        extractive.assert_awaited_once_with(question, tenant, provider)
        _walk_forbidden(replay.to_dict())

    def test_public_request_forbids_physical_and_actor_supplied_scope(self) -> None:
        from pydantic import ValidationError
        from platform_foundation.f1.api.routers.material_qa import (
            MaterialCitation,
            MaterialQaRequest,
        )

        base = {
            "question": "内部材料证据查询",
            "request_id": "62200000-0000-4000-8000-000000000001",
        }
        for forbidden in (
            "dataset_id",
            "knowledge_scope_id",
            "scope_ids",
            "chunk_id",
            "tenant_id",
            "roles",
            "enterprise_id",
        ):
            with self.subTest(forbidden=forbidden), self.assertRaises(ValidationError):
                MaterialQaRequest.model_validate({**base, forbidden: str(uuid.uuid4())})
        self.assertTrue(
            set(MaterialCitation.model_fields).isdisjoint(FORBIDDEN_PHYSICAL_KEYS)
        )
        source = PUBLIC_QA_ROUTER.read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertIn("tenant_from_header", source)
        self.assertNotIn("require_role", source)
        self.assertIn('tenant.role in {"super_admin", "enterprise_admin"}', source)
        self.assertIn("derive_audience_retrieval_context", source)
        self.assertIn("derive_retrieval_context", source)
        self.assertNotIn("RagFlow", source)
        self.assertNotIn("retrieve_registered", source)
        self.assertNotIn("str(exc)", source)
        details: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name != "HTTPException":
                continue
            for keyword in node.keywords:
                if keyword.arg == "detail" and isinstance(keyword.value, ast.Constant):
                    if isinstance(keyword.value.value, str):
                        details.append(keyword.value.value)
        self.assertTrue(details)
        for detail in details:
            self.assertRegex(detail, r"^[A-Z0-9_]+$")
            self.assertNotIn("/", detail)
        qa_source = QA_SERVICE.read_text(encoding="utf-8")
        self.assertIn("run_extractive_answer(", qa_source)
        self.assertNotIn("run_verified_retrieval(", qa_source)


class _FakeQaClaimStore:
    def __init__(self) -> None:
        self.bound: dict[uuid.UUID, dict[str, Any]] = {}
        self.reserve_calls = 0
        self.complete_calls = 0

    async def reserve(self, request_id, tenant, question, *, query_context_sha256, **kwargs):
        from platform_foundation.f1 import qa_service

        self.reserve_calls += 1
        identity = (
            tenant.enterprise_id,
            hashlib.sha256(question.encode("utf-8")).hexdigest(),
            query_context_sha256,
        )
        existing = self.bound.get(request_id)
        if existing is None:
            token = uuid.uuid4()
            self.bound[request_id] = {
                "identity": identity,
                "token": token,
                "result": None,
            }
            return qa_service.QaReservation(
                qa_service.ReservationState.CLAIMED,
                request_id,
                owner_token=token,
                attempt=1,
            )
        if existing["identity"] != identity:
            return qa_service.QaReservation(
                qa_service.ReservationState.CONFLICT, request_id
            )
        if existing["result"] is not None:
            return qa_service.QaReservation(
                qa_service.ReservationState.REPLAY,
                request_id,
                result=existing["result"],
                attempt=1,
            )
        return qa_service.QaReservation(
            qa_service.ReservationState.IN_PROGRESS, request_id
        )

    async def complete(
        self, request_id, tenant, question, owner_token, outcome, *, query_context_sha256, **kwargs
    ):
        self.complete_calls += 1
        self.bound[request_id]["result"] = outcome


if __name__ == "__main__":
    unittest.main()
