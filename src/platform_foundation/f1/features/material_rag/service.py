"""Server-derived material contexts and PostgreSQL evidence verification."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import re
import uuid
from collections.abc import Mapping

from sqlalchemy import text

from platform_foundation.f0j1.ragflow_client import RagFlowClient, RagFlowProbeError

from ...auth import Tenant
from ...config import ragflow_base_url
from ...database import session_scope
from ...ragflow_provision import RagflowProvisionError, ragflow_token
from .contracts import (
    MaterialExtractiveAnswer,
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
from .local_extractive import (
    LocalExtractiveIntegrityError,
    MAX_LOCAL_CANDIDATES,
    rank_local_evidence,
)
from .ports import ReleasedUnitRecord, ScopeBinding
from .ragflow_adapter import RemoteCandidate
from .repository import load_dataset_binding
from .security import (
    AUTHORIZED_DEMO_SOURCE_SHA256,
    assert_external_text_safe,
    dataset_ref_aad,
    decrypt_text,
    unit_aad_for_identity,
)
_TRANSPORT_FAILURES = (
    RagFlowProbeError,
    RagflowProvisionError,
    OSError,
    TimeoutError,
    asyncio.TimeoutError,
)
_DATASET_REF_RE = re.compile(r"^[0-9a-f]{32}$")
LOCAL_EXTRACTIVE_FLAG = "F1_MATERIAL_QA_LOCAL_EXTRACTIVE"
LOCAL_ENGINEERING_FLAG = "F1_LOCAL_ENGINEERING"


def local_extractive_enabled() -> bool:
    """Require two explicit local flags; every other runtime stays on RAGFlow."""
    return (
        os.environ.get(LOCAL_EXTRACTIVE_FLAG) == "1"
        and os.environ.get(LOCAL_ENGINEERING_FLAG) == "1"
    )


def _decode_local_released_unit(
    row: Mapping[str, object], *, aad_enterprise_id: uuid.UUID
) -> ReleasedUnitRecord:
    """Authenticate and decrypt one DB-authorized canonical unit or fail closed."""
    try:
        aad = unit_aad_for_identity(
            enterprise_id=aad_enterprise_id,
            knowledge_scope_id=row["knowledge_scope_id"],  # type: ignore[arg-type]
            unit_id=row["canonical_unit_id"],  # type: ignore[arg-type]
            document_record_id=row["document_record_id"],  # type: ignore[arg-type]
            document_version_id=row["document_version_id"],  # type: ignore[arg-type]
            source_sha256=str(row["source_sha256"]),
            page_number=int(row["page_number"]),  # type: ignore[arg-type]
            ordinal=int(row["ordinal"]),  # type: ignore[arg-type]
            parser_version=str(row["parser_version"]),
            body_sha256=str(row["body_sha256"]),
        )
        body = decrypt_text(
            bytes(row["body_ciphertext"]),  # type: ignore[arg-type]
            aad,
            str(row["body_aad_sha256"]),
        )
    except Exception:
        raise MaterialRagUnavailable("MATERIAL_LOCAL_UNIT_INTEGRITY_INVALID") from None
    body_sha256 = str(row["body_sha256"])
    actual = hashlib.sha256(body.encode("utf-8")).hexdigest()
    scope_kind = str(row["scope_kind"])
    if (
        not hmac.compare_digest(actual, body_sha256)
        or scope_kind not in {"service_provider", "client"}
    ):
        raise MaterialRagUnavailable("MATERIAL_LOCAL_UNIT_INTEGRITY_INVALID")
    return ReleasedUnitRecord(
        canonical_unit_id=row["canonical_unit_id"],  # type: ignore[arg-type]
        knowledge_scope_id=row["knowledge_scope_id"],  # type: ignore[arg-type]
        document_record_id=row["document_record_id"],  # type: ignore[arg-type]
        document_version_id=row["document_version_id"],  # type: ignore[arg-type]
        source_sha256=str(row["source_sha256"]),
        page_number=int(row["page_number"]),  # type: ignore[arg-type]
        body_sha256=body_sha256,
        body=body,
        scope_kind=scope_kind,  # type: ignore[arg-type]
        document_name=str(row["document_name"]),
        version_number=int(row["version_number"]),  # type: ignore[arg-type]
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
                        "AND record.status='active' "
                        "AND version.version_no=record.latest_version_no "
                        "AND task.pipeline_kind='controlled_ingestion' "
                        "AND task.status='done' AND task.processing_stage='ready' "
                        "AND task.object_state='ready' AND task.scan_verdict='clean' "
                        "AND task.preview_status='ready' "
                        "AND task.quarantine_status='released' "
                        "AND task.released_at IS NOT NULL "
                        "AND task.rejected_at IS NULL "
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

    async def load_local_released_units(
        self,
        tenant: Tenant,
        context: RetrievalContext,
        *,
        candidate_limit: int = MAX_LOCAL_CANDIDATES,
    ) -> tuple[ReleasedUnitRecord, ...]:
        if not 1 <= candidate_limit <= MAX_LOCAL_CANDIDATES:
            raise ValueError("MATERIAL_LOCAL_CANDIDATE_LIMIT")
        async with session_scope(
            role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
        ) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT unit.id AS canonical_unit_id,"
                        "unit.knowledge_scope_id,unit.document_record_id,"
                        "unit.document_version_id,unit.source_sha256,"
                        "unit.page_number,unit.ordinal,unit.parser_version,"
                        "unit.body_ciphertext,unit.body_sha256,"
                        "unit.body_aad_sha256,scope.scope_kind,"
                        "record.title AS document_name,"
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
                        "AND record.status='active' "
                        "AND version.version_no=record.latest_version_no "
                        "AND task.pipeline_kind='controlled_ingestion' "
                        "AND task.status='done' AND task.processing_stage='ready' "
                        "AND task.object_state='ready' AND task.scan_verdict='clean' "
                        "AND task.preview_status='ready' "
                        "AND task.quarantine_status='released' "
                        "AND task.released_at IS NOT NULL "
                        "AND task.rejected_at IS NULL "
                        "AND task.content_sha256=unit.source_sha256 "
                        "ORDER BY unit.id LIMIT :candidate_limit"
                    ),
                    {
                        "enterprise_id": tenant.enterprise_id,
                        "scope_ids": list(context._scope_ids),
                        "candidate_limit": candidate_limit,
                    },
                )
            ).mappings().all()
        expected_scope_kinds = {context._scope_ids[0]: "service_provider"}
        if context.kind == "client":
            expected_scope_kinds[context._scope_ids[1]] = "client"
        if len(rows) > candidate_limit:
            raise MaterialRagUnavailable("MATERIAL_LOCAL_SCOPE_INVALID")
        records: list[ReleasedUnitRecord] = []
        seen: set[uuid.UUID] = set()
        for row in rows:
            unit_id = row["canonical_unit_id"]
            scope_id = row["knowledge_scope_id"]
            if (
                not isinstance(unit_id, uuid.UUID)
                or unit_id in seen
                or scope_id not in expected_scope_kinds
                or str(row["scope_kind"]) != expected_scope_kinds[scope_id]
            ):
                raise MaterialRagUnavailable("MATERIAL_LOCAL_SCOPE_INVALID")
            records.append(
                _decode_local_released_unit(
                    row,
                    aad_enterprise_id=tenant.enterprise_id,
                )
            )
            seen.add(unit_id)
        return tuple(records)


class AudiencePostgresMaterialRagRepository:
    """Narrow SECURITY DEFINER adapter for one active client audience.

    The SQL functions expose only two authorized dataset bindings and only
    caller-selected canonical unit ids.  Provider ids never cross the HTTP
    response boundary.
    """

    async def resolve_audience(self, tenant: Tenant) -> Mapping[str, object] | None:
        async with session_scope(
            role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
        ) as session:
            rows = (
                await session.execute(
                    text("SELECT * FROM f1.aeco_client_material_context()")
                )
            ).mappings().all()
        if len(rows) != 1:
            return None
        return rows[0]

    async def load_provider_scope_id(self, tenant: Tenant) -> uuid.UUID | None:
        row = await self.resolve_audience(tenant)
        return row["provider_scope_id"] if row is not None else None  # type: ignore[return-value]

    async def load_client_scope_id(
        self, tenant: Tenant, client_account_id: uuid.UUID
    ) -> uuid.UUID | None:
        row = await self.resolve_audience(tenant)
        if row is None or row["client_account_id"] != client_account_id:
            return None
        return row["client_scope_id"]  # type: ignore[return-value]

    async def load_ready_bindings(
        self, tenant: Tenant, context: RetrievalContext
    ) -> tuple[ScopeBinding, ...]:
        resolved = await self.resolve_audience(tenant)
        if resolved is None:
            return ()
        provider_id = resolved["provider_enterprise_id"]
        if not isinstance(provider_id, uuid.UUID):
            return ()
        async with session_scope(
            role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
        ) as session:
            rows = (
                await session.execute(
                    text("SELECT * FROM f1.aeco_client_material_bindings()")
                )
            ).mappings().all()
        bindings: list[ScopeBinding] = []
        for row in rows:
            scope_id = row["knowledge_scope_id"]
            if (
                row["provider_enterprise_id"] != provider_id
                or scope_id not in context._scope_ids
            ):
                continue
            try:
                aad = dataset_ref_aad(
                    enterprise_id=provider_id,
                    knowledge_scope_id=scope_id,
                    binding_id=row["binding_id"],
                )
                dataset_ref = decrypt_text(
                    bytes(row["dataset_ref_ciphertext"]),
                    aad,
                    str(row["dataset_ref_aad_sha256"]),
                )
            except (TypeError, ValueError):
                raise MaterialRagUnavailable("MATERIAL_RAG_BINDING_INVALID") from None
            digest = hashlib.sha256(dataset_ref.encode("utf-8")).hexdigest()
            if not _DATASET_REF_RE.fullmatch(dataset_ref) or not hmac.compare_digest(
                digest, str(row["dataset_ref_sha256"])
            ):
                raise MaterialRagUnavailable("MATERIAL_RAG_BINDING_INVALID")
            bindings.append(ScopeBinding(scope_id, dataset_ref))
        return tuple(bindings)

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
                        "SELECT * FROM f1.aeco_client_material_units("
                        "CAST(:unit_ids AS uuid[]))"
                    ),
                    {"unit_ids": list(unit_ids)},
                )
            ).mappings().all()
        records: list[ReleasedUnitRecord] = []
        for row in rows:
            if row["knowledge_scope_id"] not in context._scope_ids:
                continue
            try:
                aad = unit_aad_for_identity(
                    enterprise_id=row["provider_enterprise_id"],
                    knowledge_scope_id=row["knowledge_scope_id"],
                    unit_id=row["canonical_unit_id"],
                    document_record_id=row["document_record_id"],
                    document_version_id=row["document_version_id"],
                    source_sha256=str(row["source_sha256"]),
                    page_number=int(row["page_number"]),
                    ordinal=int(row["ordinal"]),
                    parser_version=str(row["parser_version"]),
                    body_sha256=str(row["body_sha256"]),
                )
                body = decrypt_text(
                    bytes(row["body_ciphertext"]),
                    aad,
                    str(row["body_aad_sha256"]),
                )
            except (TypeError, ValueError):
                continue
            records.append(
                ReleasedUnitRecord(
                    canonical_unit_id=row["canonical_unit_id"],
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

    async def load_local_released_units(
        self,
        tenant: Tenant,
        context: RetrievalContext,
        *,
        candidate_limit: int = MAX_LOCAL_CANDIDATES,
    ) -> tuple[ReleasedUnitRecord, ...]:
        if not 1 <= candidate_limit <= MAX_LOCAL_CANDIDATES:
            raise ValueError("MATERIAL_LOCAL_CANDIDATE_LIMIT")
        resolved = await self.resolve_audience(tenant)
        if resolved is None:
            raise MaterialRagUnavailable("MATERIAL_LOCAL_AUDIENCE_INVALID")
        provider_id = resolved["provider_enterprise_id"]
        expected_scopes = {
            resolved["provider_scope_id"],
            resolved["client_scope_id"],
        }
        expected_scope_kinds = {
            resolved["provider_scope_id"]: "service_provider",
            resolved["client_scope_id"]: "client",
        }
        if (
            not isinstance(provider_id, uuid.UUID)
            or expected_scopes != set(context._scope_ids)
        ):
            raise MaterialRagUnavailable("MATERIAL_LOCAL_AUDIENCE_INVALID")
        async with session_scope(
            role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
        ) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM f1.aeco_client_material_local_units("
                        "CAST(:candidate_limit AS integer))"
                    ),
                    {"candidate_limit": candidate_limit},
                )
            ).mappings().all()
        records: list[ReleasedUnitRecord] = []
        seen: set[uuid.UUID] = set()
        if len(rows) > candidate_limit:
            raise MaterialRagUnavailable("MATERIAL_LOCAL_SCOPE_INVALID")
        for row in rows:
            unit_id = row["canonical_unit_id"]
            scope_id = row["knowledge_scope_id"]
            if (
                row["provider_enterprise_id"] != provider_id
                or scope_id not in expected_scopes
                or str(row["scope_kind"]) != expected_scope_kinds[scope_id]
                or not isinstance(unit_id, uuid.UUID)
                or unit_id in seen
            ):
                raise MaterialRagUnavailable("MATERIAL_LOCAL_SCOPE_INVALID")
            records.append(
                _decode_local_released_unit(row, aad_enterprise_id=provider_id)
            )
            seen.add(unit_id)
        return tuple(records)


class RagflowMaterialRagTransport:
    """Scope-pinned RAGFlow retrieval; it never invokes a generator."""

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

    def __init__(self, repository, transport, *, local_extractive: bool = False) -> None:
        self._repository = repository
        self._transport = transport
        self._local_extractive = local_extractive is True
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

    async def retrieve(
        self,
        query: str,
        tenant: Tenant,
        context: RetrievalContext,
        *,
        request_id: uuid.UUID | None = None,
        limit: int = 6,
    ) -> MaterialRetrievalResult:
        _validate_retrieval_request(query, tenant, context, limit)
        query = query.strip()
        try:
            assert_external_text_safe(query)
        except ValueError:
            return MaterialRetrievalResult((), REFUSE_REJECTED)
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

    async def retrieve_registered(
        self,
        query: str,
        tenant: Tenant,
        context: RetrievalContext,
        *,
        request_id: uuid.UUID | None = None,
        limit: int = 6,
    ) -> MaterialRetrievalResult:
        """Compatibility entry point; retrieval is no longer query-allowlisted."""
        return await self.retrieve(
            query,
            tenant,
            context,
            request_id=request_id,
            limit=limit,
        )

    async def extractive_answer(
        self,
        question: str,
        tenant: Tenant,
        context: RetrievalContext,
        *,
        limit: int = 6,
    ) -> MaterialExtractiveAnswer:
        """Copy a bounded answer from evidence verified by this service."""
        retrieval = await self.retrieve(question, tenant, context, limit=limit)
        if retrieval.refusal_reason is not None:
            return MaterialExtractiveAnswer(None, (), retrieval.refusal_reason)
        cited = retrieval.evidence[:3]
        answer = "\n\n".join(item.snippet for item in cited)
        return MaterialExtractiveAnswer(answer, cited)

    async def _retrieve_authorized(
        self,
        query: str,
        tenant: Tenant,
        context: RetrievalContext,
        limit: int,
    ) -> MaterialRetrievalResult:
        if self._local_extractive:
            return await self._retrieve_local_authorized(
                query, tenant, context, limit
            )
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

    async def _retrieve_local_authorized(
        self,
        query: str,
        tenant: Tenant,
        context: RetrievalContext,
        limit: int,
    ) -> MaterialRetrievalResult:
        records = await self._repository.load_local_released_units(
            tenant,
            context,
            candidate_limit=MAX_LOCAL_CANDIDATES,
        )
        try:
            evidence = rank_local_evidence(query, records, limit=limit)
        except LocalExtractiveIntegrityError:
            raise MaterialRagUnavailable(
                "MATERIAL_LOCAL_UNIT_INTEGRITY_INVALID"
            ) from None
        if not evidence:
            return MaterialRetrievalResult((), REFUSE_NO_HITS)
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
        local_extractive=local_extractive_enabled(),
    )


def _audience_service() -> MaterialRetrievalService:
    return MaterialRetrievalService(
        AudiencePostgresMaterialRagRepository(),
        RagflowMaterialRagTransport(),
        local_extractive=local_extractive_enabled(),
    )


def _validate_retrieval_request(
    query: object,
    tenant: Tenant,
    context: object,
    limit: int,
) -> None:
    if (
        not isinstance(context, RetrievalContext)
        or context.enterprise_id != tenant.enterprise_id
        or not isinstance(query, str)
        or not query.strip()
        or len(query) > 2_000
        or not 1 <= limit <= 20
    ):
        raise ValueError("MATERIAL_CONTEXT_INVALID")


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


async def derive_audience_retrieval_context(tenant: Tenant) -> RetrievalContext:
    """Resolve exactly one active provider/client pair from the client session."""
    repository = AudiencePostgresMaterialRagRepository()
    row = await repository.resolve_audience(tenant)
    if row is None:
        raise MaterialRagContextNotFound("MATERIAL_CONTEXT_NOT_FOUND")
    return RetrievalContext(
        enterprise_id=tenant.enterprise_id,
        kind="client",
        client_account_id=row["client_account_id"],  # type: ignore[arg-type]
        scope_ids=(
            row["provider_scope_id"],  # type: ignore[arg-type]
            row["client_scope_id"],  # type: ignore[arg-type]
        ),
        audience_bound=True,
    )


async def run_verified_retrieval(
    question: str,
    tenant: Tenant,
    context: RetrievalContext,
    *,
    limit: int = 6,
) -> MaterialRetrievalResult:
    """Retrieve arbitrary PII-safe text inside the server-derived scope only."""
    service = (
        _audience_service()
        if getattr(context, "_audience_bound", False)
        else _production_service()
    )
    return await service.retrieve(
        question,
        tenant,
        context,
        limit=limit,
    )


async def run_extractive_answer(
    question: str,
    tenant: Tenant,
    context: RetrievalContext,
    *,
    limit: int = 6,
) -> MaterialExtractiveAnswer:
    """Return only text copied from verified evidence; never call an LLM."""
    service = (
        _audience_service()
        if getattr(context, "_audience_bound", False)
        else _production_service()
    )
    return await service.extractive_answer(
        question,
        tenant,
        context,
        limit=limit,
    )


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
    """Compatibility wrapper for historical verifier probes."""

    return await _production_service().retrieve_registered(
        query, tenant, context, limit=limit
    )


async def _retrieve_exact_authorized(
    query: str,
    datasets: tuple[tuple[uuid.UUID, str], ...],
    limit: int,
) -> tuple[RemoteCandidate, ...]:
    """Network boundary for PII-safe text and server-derived scope datasets."""

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
    service = (
        _audience_service()
        if getattr(context, "_audience_bound", False)
        else _production_service()
    )
    return await service.verify_remote_candidates(
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
    "derive_audience_retrieval_context",
    "derive_retrieval_context",
    "run_extractive_answer",
    "run_verified_retrieval",
    "retrieve_authorized_demo_fragment",
    "retrieve_registered_verifier_query",
    "verify_remote_candidates",
)
