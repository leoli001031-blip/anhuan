"""Server-derived material contexts and PostgreSQL evidence verification."""
from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Mapping

from sqlalchemy import text

from platform_foundation.f0j1.ragflow_client import RagFlowClient, RagFlowProbeError

from ...auth import Tenant
from ...config import ragflow_base_url
from ...database import session_scope
from ...ragflow_provision import RagflowProvisionError, ragflow_token
from .contracts import (
    MaterialEvidence,
    MaterialRagContextNotFound,
    MaterialRagRequestConflict,
    MaterialRagUnavailable,
    MaterialRetrievalResult,
    REFUSE_NO_HITS,
    REFUSE_NOT_CONFIGURED,
    REFUSE_REJECTED,
    REFUSE_UNAVAILABLE,
    RetrievalContext,
)
from .ports import ReleasedUnitRecord, ScopeBinding
from .ragflow_adapter import RemoteCandidate
from .repository import load_dataset_binding
from .security import (
    AUTHORIZED_DEMO_SOURCE_SHA256,
    CLIENT_A_RETRIEVAL_QUERY_TEXT,
    CLIENT_B_RETRIEVAL_QUERY_TEXT,
    PROVIDER_RETRIEVAL_QUERY_TEXT,
    assert_external_text_safe,
    decrypt_text,
    unit_aad_for_identity,
)

_REGISTERED_VERIFIER_QUERIES = frozenset(
    (
        PROVIDER_RETRIEVAL_QUERY_TEXT,
        CLIENT_A_RETRIEVAL_QUERY_TEXT,
        CLIENT_B_RETRIEVAL_QUERY_TEXT,
    )
)
_TRANSPORT_FAILURES = (
    RagFlowProbeError,
    RagflowProvisionError,
    OSError,
    TimeoutError,
    asyncio.TimeoutError,
)


class PostgresMaterialRagRepository:
    """Production adapter: existing PostgreSQL lookups, unchanged SQL."""

    async def load_provider_scope_id(self, tenant: Tenant) -> uuid.UUID | None:
        async with session_scope(
            role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
        ) as session:
            return (
                await session.execute(
                    text(
                        "SELECT id FROM f1.material_knowledge_scope "
                        "WHERE enterprise_id=:enterprise_id "
                        "AND scope_kind='service_provider'"
                    ),
                    {"enterprise_id": tenant.enterprise_id},
                )
            ).scalar_one_or_none()

    async def load_client_scope_id(
        self, tenant: Tenant, client_account_id: uuid.UUID
    ) -> uuid.UUID | None:
        async with session_scope(
            role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
        ) as session:
            return (
                await session.execute(
                    text(
                        "SELECT id FROM f1.material_knowledge_scope "
                        "WHERE enterprise_id=:enterprise_id AND scope_kind='client' "
                        "AND client_account_id=:client_account_id"
                    ),
                    {
                        "enterprise_id": tenant.enterprise_id,
                        "client_account_id": client_account_id,
                    },
                )
            ).scalar_one_or_none()

    async def load_ready_bindings(
        self, tenant: Tenant, context: RetrievalContext
    ) -> tuple[ScopeBinding, ...]:
        datasets: list[ScopeBinding] = []
        async with session_scope(
            role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
        ) as session:
            for scope_id in context._scope_ids:
                binding = await load_dataset_binding(
                    session,
                    enterprise_id=tenant.enterprise_id,
                    knowledge_scope_id=scope_id,
                )
                if binding is not None:
                    datasets.append(ScopeBinding(scope_id, binding.dataset_ref))
        return tuple(datasets)

    async def load_released_units(
        self,
        tenant: Tenant,
        context: RetrievalContext,
        unit_ids: tuple[uuid.UUID, ...],
    ) -> tuple[ReleasedUnitRecord, ...]:
        if not unit_ids:
            return ()
        async with session_scope(
            role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
        ) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT unit.id,unit.knowledge_scope_id,unit.document_record_id,"
                        "unit.document_version_id,unit.source_sha256,unit.page_number,"
                        "unit.ordinal,unit.parser_version,unit.body_ciphertext,"
                        "unit.body_sha256,unit.body_aad_sha256,"
                        "scope.scope_kind,record.title AS document_name,"
                        "version.version_no AS version_number "
                        "FROM f1.material_rag_unit AS unit "
                        "JOIN f1.material_knowledge_scope AS scope ON "
                        "scope.enterprise_id=unit.enterprise_id "
                        "AND scope.id=unit.knowledge_scope_id "
                        "JOIN f1.document_record AS record ON "
                        "record.enterprise_id=unit.enterprise_id "
                        "AND record.id=unit.document_record_id "
                        "AND record.knowledge_scope_id=unit.knowledge_scope_id "
                        "JOIN f1.document_version AS version ON "
                        "version.enterprise_id=unit.enterprise_id "
                        "AND version.id=unit.document_version_id "
                        "AND version.document_record_id=unit.document_record_id "
                        "JOIN f1.upload_task AS task ON "
                        "task.enterprise_id=version.enterprise_id "
                        "AND task.id=version.upload_task_id "
                        "WHERE unit.enterprise_id=:enterprise_id "
                        "AND unit.knowledge_scope_id=ANY(CAST(:scope_ids AS uuid[])) "
                        "AND unit.id=ANY(CAST(:unit_ids AS uuid[])) "
                        "AND task.pipeline_kind='controlled_ingestion' "
                        "AND task.status='done' AND task.processing_stage='ready' "
                        "AND task.object_state='ready' AND task.scan_verdict='clean' "
                        "AND task.preview_status='ready' "
                        "AND task.quarantine_status='released' "
                        "AND task.released_at IS NOT NULL "
                        "AND task.content_sha256=unit.source_sha256"
                    ),
                    {
                        "enterprise_id": tenant.enterprise_id,
                        "scope_ids": list(context._scope_ids),
                        "unit_ids": list(unit_ids),
                    },
                )
            ).mappings().all()
        records: list[ReleasedUnitRecord] = []
        for row in rows:
            try:
                aad = unit_aad_for_identity(
                    enterprise_id=tenant.enterprise_id,
                    knowledge_scope_id=row["knowledge_scope_id"],
                    unit_id=row["id"],
                    document_record_id=row["document_record_id"],
                    document_version_id=row["document_version_id"],
                    source_sha256=str(row["source_sha256"]),
                    page_number=int(row["page_number"]),
                    ordinal=int(row["ordinal"]),
                    parser_version=str(row["parser_version"]),
                    body_sha256=str(row["body_sha256"]),
                )
                body = decrypt_text(
                    bytes(row["body_ciphertext"]), aad, str(row["body_aad_sha256"])
                )
            except (TypeError, ValueError):
                continue
            records.append(
                ReleasedUnitRecord(
                    canonical_unit_id=row["id"],
                    knowledge_scope_id=row["knowledge_scope_id"],
                    document_record_id=row["document_record_id"],
                    document_version_id=row["document_version_id"],
                    source_sha256=str(row["source_sha256"]),
                    page_number=int(row["page_number"]),
                    body_sha256=str(row["body_sha256"]),
                    body=body,
                    scope_kind=str(row["scope_kind"]),  # type: ignore[arg-type]
                    document_name=str(row["document_name"]),
                    version_number=int(row["version_number"]),
                )
            )
        return tuple(records)


class RagflowMaterialRagTransport:
    """Production adapter: existing exact-authorized retrieval."""

    async def retrieve_candidates(
        self,
        query: str,
        datasets: tuple[ScopeBinding, ...],
        limit: int,
    ) -> tuple[RemoteCandidate, ...]:
        packed = tuple(
            (item.knowledge_scope_id, item.dataset_ref) for item in datasets
        )
        return await _retrieve_exact_authorized(query, packed, limit)


class MaterialRetrievalService:
    """Production retrieval logic with injectable repository/transport ports."""

    def __init__(self, repository, transport) -> None:
        self._repository = repository
        self._transport = transport
        self._requests: dict[
            uuid.UUID, tuple[tuple[object, ...], MaterialRetrievalResult]
        ] = {}

    async def derive_retrieval_context(
        self, tenant: Tenant, client_account_id: uuid.UUID | None
    ) -> RetrievalContext:
        provider = await self._repository.load_provider_scope_id(tenant)
        if provider is None:
            raise MaterialRagContextNotFound("MATERIAL_CONTEXT_NOT_FOUND")
        if client_account_id is None:
            return RetrievalContext(
                enterprise_id=tenant.enterprise_id,
                kind="service_provider",
                client_account_id=None,
                scope_ids=(provider,),
            )
        client = await self._repository.load_client_scope_id(
            tenant, client_account_id
        )
        if client is None:
            # Missing and unauthorized are deliberately indistinguishable.
            raise MaterialRagContextNotFound("MATERIAL_CONTEXT_NOT_FOUND")
        return RetrievalContext(
            enterprise_id=tenant.enterprise_id,
            kind="client",
            client_account_id=client_account_id,
            scope_ids=(provider, client),
        )

    async def retrieve_registered(
        self,
        query: str,
        tenant: Tenant,
        context: RetrievalContext,
        *,
        request_id: uuid.UUID | None = None,
        limit: int = 6,
    ) -> MaterialRetrievalResult:
        _reject_unregistered(query, tenant, context, limit)
        assert_external_text_safe(query)
        identity = (tenant.enterprise_id, context.context_sha256, query)
        if request_id is not None:
            cached = self._requests.get(request_id)
            if cached is not None:
                if cached[0] != identity:
                    raise MaterialRagRequestConflict("REQUEST_ID_CONFLICT")
                return cached[1]
        result = await self._retrieve_authorized(query, tenant, context, limit)
        if request_id is not None:
            self._requests[request_id] = (identity, result)
        return result

    async def _retrieve_authorized(
        self,
        query: str,
        tenant: Tenant,
        context: RetrievalContext,
        limit: int,
    ) -> MaterialRetrievalResult:
        allowed_scopes = set(context._scope_ids)
        bindings = tuple(
            binding
            for binding in await self._repository.load_ready_bindings(tenant, context)
            if binding.knowledge_scope_id in allowed_scopes
        )
        if not bindings:
            reason = (
                REFUSE_NOT_CONFIGURED
                if context.kind == "service_provider"
                else REFUSE_NO_HITS
            )
            return MaterialRetrievalResult((), reason)
        try:
            candidates = await self._transport.retrieve_candidates(
                query, bindings, limit
            )
        except _TRANSPORT_FAILURES:
            return MaterialRetrievalResult((), REFUSE_UNAVAILABLE)
        if not candidates:
            return MaterialRetrievalResult((), REFUSE_NO_HITS)
        evidence = await self.verify_remote_candidates(candidates, tenant, context)
        if not evidence:
            return MaterialRetrievalResult((), REFUSE_REJECTED)
        return MaterialRetrievalResult(evidence, None)

    async def verify_remote_candidates(
        self,
        candidates: tuple[RemoteCandidate, ...],
        tenant: Tenant,
        context: RetrievalContext,
    ) -> tuple[MaterialEvidence, ...]:
        if (
            not isinstance(context, RetrievalContext)
            or context.enterprise_id != tenant.enterprise_id
        ):
            raise ValueError("MATERIAL_CONTEXT_INVALID")
        if not candidates:
            return ()
        records = await self._repository.load_released_units(
            tenant,
            context,
            tuple(candidate.canonical_unit_id for candidate in candidates),
        )
        by_id = {_row_get(row, "canonical_unit_id"): row for row in records}
        allowed_scopes = set(context._scope_ids)
        evidence: list[MaterialEvidence] = []
        seen: set[uuid.UUID] = set()
        for candidate in candidates:
            if candidate.canonical_unit_id in seen:
                continue
            row = by_id.get(candidate.canonical_unit_id)
            if row is None:
                continue
            scope_id = _row_get(row, "knowledge_scope_id")
            scope_kind = str(_row_get(row, "scope_kind"))
            if scope_id not in allowed_scopes:
                continue
            if context.kind == "service_provider" and scope_kind != "service_provider":
                continue
            if not _candidate_matches(candidate, row):
                continue
            body = str(_row_get(row, "body"))
            if hashlib.sha256(body.encode("utf-8")).hexdigest() != _row_get(
                row, "body_sha256"
            ):
                continue
            snippet = " ".join(body.split())[:320]
            if not snippet:
                continue
            evidence.append(
                MaterialEvidence(
                    canonical_unit_id=_row_get(row, "canonical_unit_id"),  # type: ignore[arg-type]
                    document_record_id=_row_get(row, "document_record_id"),  # type: ignore[arg-type]
                    document_version_id=_row_get(row, "document_version_id"),  # type: ignore[arg-type]
                    document_name=str(_row_get(row, "document_name")),
                    version_number=int(_row_get(row, "version_number")),  # type: ignore[arg-type]
                    source_sha256=str(_row_get(row, "source_sha256")),
                    page_number=int(_row_get(row, "page_number")),  # type: ignore[arg-type]
                    body_sha256=str(_row_get(row, "body_sha256")),
                    snippet=snippet,
                    scope_kind=scope_kind,  # type: ignore[arg-type]
                )
            )
            seen.add(candidate.canonical_unit_id)
        return tuple(evidence)


def _production_service() -> MaterialRetrievalService:
    return MaterialRetrievalService(
        PostgresMaterialRagRepository(),
        RagflowMaterialRagTransport(),
    )


def _reject_unregistered(
    query: object,
    tenant: Tenant,
    context: object,
    limit: int,
) -> None:
    if (
        not isinstance(context, RetrievalContext)
        or context.enterprise_id != tenant.enterprise_id
        or not isinstance(query, str)
        or query not in _REGISTERED_VERIFIER_QUERIES
        or not 1 <= limit <= 20
    ):
        raise MaterialRagUnavailable(
            "MATERIAL_QUERY_EXTERNAL_PROCESSING_NOT_AUTHORIZED"
        )


def _row_get(row: object, key: str) -> object:
    if isinstance(row, Mapping):
        if key == "canonical_unit_id":
            return row["id"] if "id" in row else row["canonical_unit_id"]
        return row[key]
    if key == "canonical_unit_id":
        return getattr(row, "canonical_unit_id", getattr(row, "id", None))
    return getattr(row, key)


async def derive_retrieval_context(
    tenant: Tenant, client_account_id: uuid.UUID | None
) -> RetrievalContext:
    """Resolve provider-only or provider+one-client scope under RLS."""
    return await _production_service().derive_retrieval_context(
        tenant, client_account_id
    )


async def run_verified_retrieval(
    question: str,
    tenant: Tenant,
    context: RetrievalContext,
    *,
    limit: int = 6,
) -> MaterialRetrievalResult:
    """Refuse until outbound query-vector processing is explicitly allowed.

    The verifier has a closed manifest of four filtered Demo documents plus
    fixed synthetic canaries and queries.  Public user questions remain
    outside that manifest, so opening this product route would silently admit
    arbitrary text.  The internal registered-query path below is separate.
    """
    if (
        not isinstance(context, RetrievalContext)
        or context.enterprise_id != tenant.enterprise_id
        or not isinstance(question, str)
        or not question.strip()
        or not 1 <= limit <= 20
    ):
        raise ValueError("MATERIAL_CONTEXT_INVALID")
    raise MaterialRagUnavailable("MATERIAL_QUERY_EXTERNAL_PROCESSING_NOT_AUTHORIZED")


async def retrieve_authorized_demo_fragment(
    query: str,
    tenant: Tenant,
    context: RetrievalContext,
    *,
    query_source_sha256: str,
    limit: int = 6,
) -> MaterialRetrievalResult:
    """Retrieve using only an exact, persisted fragment from an allowed Demo.

    Before any network call PostgreSQL proves the query is byte-for-byte the
    already-filtered canonical unit body of one of the four explicitly
    authorized PDF hashes in an actor-visible scope.  Arbitrary user text can
    never use this internal verification path.
    """
    if (
        not isinstance(context, RetrievalContext)
        or context.enterprise_id != tenant.enterprise_id
        or query_source_sha256 not in AUTHORIZED_DEMO_SOURCE_SHA256
        or not isinstance(query, str)
        or not query
        or not 1 <= limit <= 20
    ):
        raise MaterialRagUnavailable("MATERIAL_QUERY_EXTERNAL_PROCESSING_NOT_AUTHORIZED")
    query_sha = hashlib.sha256(query.encode("utf-8")).hexdigest()
    datasets: list[tuple[uuid.UUID, str]] = []
    exact_match = False
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        for scope_id in context._scope_ids:
            binding = await load_dataset_binding(
                session,
                enterprise_id=tenant.enterprise_id,
                knowledge_scope_id=scope_id,
            )
            if binding is not None:
                datasets.append((scope_id, binding.dataset_ref))

        # A missing binding is an empty logical scope, never permission to
        # fall back to the historical enterprise dataset.  Return before the
        # query-proof lookup when a client context has no physical datasets;
        # this is the verifier's no-egress client-B isolation path.
        if not datasets:
            reason = (
                REFUSE_NOT_CONFIGURED
                if context.kind == "service_provider"
                else REFUSE_NO_HITS
            )
            return MaterialRetrievalResult((), reason)

        rows = (
            await session.execute(
                text(
                    "SELECT unit.id,unit.knowledge_scope_id,"
                    "unit.document_record_id,unit.document_version_id,"
                    "unit.source_sha256,unit.page_number,unit.ordinal,"
                    "unit.parser_version,unit.body_ciphertext,unit.body_sha256,"
                    "unit.body_aad_sha256 "
                    "FROM f1.material_rag_unit AS unit "
                    "JOIN f1.document_version AS version ON "
                    "version.enterprise_id=unit.enterprise_id "
                    "AND version.id=unit.document_version_id "
                    "AND version.document_record_id=unit.document_record_id "
                    "JOIN f1.document_record AS record ON "
                    "record.enterprise_id=unit.enterprise_id "
                    "AND record.id=unit.document_record_id "
                    "AND record.knowledge_scope_id=unit.knowledge_scope_id "
                    "JOIN f1.upload_task AS task ON "
                    "task.enterprise_id=version.enterprise_id "
                    "AND task.id=version.upload_task_id "
                    "WHERE unit.enterprise_id=:enterprise_id "
                    "AND unit.knowledge_scope_id=ANY(CAST(:scope_ids AS uuid[])) "
                    "AND unit.source_sha256=:source_sha "
                    "AND unit.body_sha256=:body_sha "
                    "AND task.pipeline_kind='controlled_ingestion' "
                    "AND task.status='done' AND task.processing_stage='ready' "
                    "AND task.object_state='ready' AND task.scan_verdict='clean' "
                    "AND task.preview_status='ready' "
                    "AND task.quarantine_status='released' "
                    "AND task.released_at IS NOT NULL "
                    "AND task.content_sha256=unit.source_sha256"
                ),
                {
                    "enterprise_id": tenant.enterprise_id,
                    "scope_ids": list(context._scope_ids),
                    "source_sha": query_source_sha256,
                    "body_sha": query_sha,
                },
            )
        ).mappings().all()
        for row in rows:
            try:
                aad = unit_aad_for_identity(
                    enterprise_id=tenant.enterprise_id,
                    knowledge_scope_id=row["knowledge_scope_id"],
                    unit_id=row["id"],
                    document_record_id=row["document_record_id"],
                    document_version_id=row["document_version_id"],
                    source_sha256=str(row["source_sha256"]),
                    page_number=int(row["page_number"]),
                    ordinal=int(row["ordinal"]),
                    parser_version=str(row["parser_version"]),
                    body_sha256=str(row["body_sha256"]),
                )
                stored = decrypt_text(
                    bytes(row["body_ciphertext"]),
                    aad,
                    str(row["body_aad_sha256"]),
                )
            except (TypeError, ValueError):
                continue
            if stored == query:
                exact_match = True
                break
        if not exact_match:
            raise MaterialRagUnavailable(
                "MATERIAL_QUERY_EXTERNAL_PROCESSING_NOT_AUTHORIZED"
            )
        assert_external_text_safe(query)

    try:
        candidates = await _retrieve_exact_authorized(query, tuple(datasets), limit)
    except (RagFlowProbeError, RagflowProvisionError, OSError, ConnectionError):
        return MaterialRetrievalResult((), REFUSE_UNAVAILABLE)
    if not candidates:
        return MaterialRetrievalResult((), REFUSE_NO_HITS)
    evidence = await verify_remote_candidates(candidates, tenant, context)
    if not evidence:
        return MaterialRetrievalResult((), REFUSE_REJECTED)
    return MaterialRetrievalResult(evidence, None)


async def retrieve_registered_verifier_query(
    query: str,
    tenant: Tenant,
    context: RetrievalContext,
    *,
    limit: int = 6,
) -> MaterialRetrievalResult:
    """Run one fixed verifier query; never accepts public or free-form text."""

    return await _production_service().retrieve_registered(
        query, tenant, context, limit=limit
    )


async def _retrieve_exact_authorized(
    query: str,
    datasets: tuple[tuple[uuid.UUID, str], ...],
    limit: int,
) -> tuple[RemoteCandidate, ...]:
    """Network boundary; caller has already proven exact authorized text."""

    def run() -> tuple[RemoteCandidate, ...]:
        dataset_scope = {dataset_id: scope_id for scope_id, dataset_id in datasets}
        if len(dataset_scope) != len(datasets) or not dataset_scope:
            return ()
        client = RagFlowClient(base_url=ragflow_base_url())
        token = ragflow_token()
        hits = client.retrieval(token, list(dataset_scope), query, page_size=limit)
        candidates: list[RemoteCandidate] = []
        seen: set[uuid.UUID] = set()
        for hit in hits:
            dataset_id = str(hit.get("dataset_id") or hit.get("kb_id") or "")
            document_id = str(hit.get("document_id") or hit.get("doc_id") or "")
            chunk_id = str(hit.get("chunk_id") or hit.get("id") or "")
            if dataset_id not in dataset_scope or not document_id or not chunk_id:
                continue
            detail = client.get_chunk(token, dataset_id, document_id, chunk_id)
            tags = _tag_map(detail.get("tag_kwd") or hit.get("tag_kwd"))
            content = detail.get("content")
            if not isinstance(content, str):
                continue
            actual_body_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if actual_body_sha != tags.get("body_sha256"):
                continue
            try:
                candidate = RemoteCandidate(
                    canonical_unit_id=uuid.UUID(tags["canonical_unit_id"]),
                    knowledge_scope_id=uuid.UUID(tags["knowledge_scope_id"]),
                    document_record_id=uuid.UUID(tags["document_record_id"]),
                    document_version_id=uuid.UUID(tags["document_version_id"]),
                    source_sha256=tags["source_sha256"],
                    page_number=int(tags["page_number"]),
                    body_sha256=tags["body_sha256"],
                )
            except (KeyError, TypeError, ValueError):
                continue
            if (
                candidate.knowledge_scope_id != dataset_scope[dataset_id]
                or candidate.canonical_unit_id in seen
            ):
                continue
            seen.add(candidate.canonical_unit_id)
            candidates.append(candidate)
        return tuple(candidates)

    return await asyncio.to_thread(run)


def _tag_map(value: object) -> dict[str, str]:
    tags = value if isinstance(value, list) else [value] if value else []
    result: dict[str, str] = {}
    for raw in tags:
        key, separator, item = str(raw).partition("=")
        if separator and key not in result:
            result[key] = item
    return result


async def verify_remote_candidates(
    candidates: tuple[RemoteCandidate, ...],
    tenant: Tenant,
    context: RetrievalContext,
) -> tuple[MaterialEvidence, ...]:
    """Intersect adapter candidates with current released PostgreSQL rows."""
    return await _production_service().verify_remote_candidates(
        candidates, tenant, context
    )


def _candidate_matches(
    candidate: RemoteCandidate, row: object
) -> bool:
    return (
        candidate.knowledge_scope_id == _row_get(row, "knowledge_scope_id")
        and candidate.document_record_id == _row_get(row, "document_record_id")
        and candidate.document_version_id == _row_get(row, "document_version_id")
        and candidate.source_sha256 == _row_get(row, "source_sha256")
        and candidate.page_number == _row_get(row, "page_number")
        and candidate.body_sha256 == _row_get(row, "body_sha256")
    )


__all__ = (
    "MaterialRetrievalService",
    "derive_retrieval_context",
    "run_verified_retrieval",
    "retrieve_authorized_demo_fragment",
    "retrieve_registered_verifier_query",
    "verify_remote_candidates",
)
