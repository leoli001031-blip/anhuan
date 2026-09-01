"""Idempotent local coordinator from safe PDF release to report draft.

The coordinator is an explicit local-engineering feature.  It reuses the
existing durable material-index and report-generation jobs, derives request
identities from tenant/client/source fingerprints, and leaves report review
and publication as manual gates.  No RAGFlow, Ark, embedding, or other remote
provider is called by this module.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import text

from ...auth import Tenant
from ...config import redis_url
from ...database import session_scope
from ..analysis_reports import repository as report_repository
from ..analysis_reports.contracts import (
    ENGINEERING_FLAG,
    LOCAL_FLAG as REPORT_LOCAL_FLAG,
    PROVIDER_MEMBER_ROLES,
    ReportNotFound,
    ReportTransitionInvalid,
)
from ..analysis_reports.service import create_report, generate_report
from ..material_rag.repository import enqueue_job
from ..p3.contracts import (
    AutoPipelineOut,
    AutoPipelineStageOut,
    IngestionError,
    public_reason_code,
)
from ..p3.service import act_on_version, require_manager
from . import repository as delivery_repository


AUTO_PIPELINE_FLAG = "F1_MATERIAL_AUTO_PIPELINE_LOCAL"
LOCAL_INDEX_FLAG = "F1_MATERIAL_RAG_LOCAL_INDEX"
_PHYSICAL_ORCHESTRATION_FLAG = "F1_MATERIAL_RAG_ORCHESTRATION_LOCAL"
_AUTO_NAMESPACE = uuid.UUID("a9122792-7000-4b8a-a5d9-64389d475552")
_LOCAL_REDIS_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "redis"})
_ANALYSIS_RETRY_MARKERS = frozenset(
    {
        "OCR_DISABLED",
        "OCR_UNAVAILABLE",
        "OCR_PAGE_LIMIT",
        "OCR_OUTPUT_INSUFFICIENT",
        "OCR_REQUIRED",
        "MATERIAL_ANALYSIS_FAILED",
        "MATERIAL_SOURCE_READ_FAILED",
        "MATERIAL_ANALYSIS_PERSIST_FAILED",
    }
)


@dataclass(frozen=True, slots=True)
class _PipelineContext:
    version_id: uuid.UUID
    content_type: str
    scope_kind: str
    client_account_id: uuid.UUID | None
    processing_stage: str
    object_state: str
    quarantine_status: str
    scan_verdict: str
    preview_status: str
    ingestion_reason: str | None
    ingestion_delivery_state: str | None
    ingestion_delivery_reason: str | None
    released: bool
    analysis_status: str | None
    analysis_reason: str | None
    ocr_required_count: int
    index_job_id: uuid.UUID | None
    index_status: str | None
    index_reason: str | None
    delivery_state: str | None
    delivery_reason: str | None


def _local_redis_enabled() -> bool:
    try:
        parsed = urlsplit(redis_url())
        return (
            parsed.scheme in {"redis", "rediss"}
            and parsed.hostname in _LOCAL_REDIS_HOSTS
            and parsed.username is None
            and parsed.password is None
        )
    except (RuntimeError, ValueError):
        return False


def auto_pipeline_enabled() -> bool:
    """Require every local-only capability; defaults remain fail-closed."""
    return (
        os.environ.get(AUTO_PIPELINE_FLAG) == "1"
        and os.environ.get(ENGINEERING_FLAG) == "1"
        and os.environ.get(LOCAL_INDEX_FLAG) == "1"
        and os.environ.get(REPORT_LOCAL_FLAG) == "1"
        and _local_index_mode_enabled()
        and _local_redis_enabled()
    )


def _local_index_mode_enabled() -> bool:
    return (
        os.environ.get(ENGINEERING_FLAG) == "1"
        and os.environ.get(LOCAL_INDEX_FLAG) == "1"
        and os.environ.get(_PHYSICAL_ORCHESTRATION_FLAG) != "1"
    )


def _request_id(kind: str, *parts: object) -> uuid.UUID:
    identity = "\x00".join((kind, *(str(part) for part in parts)))
    return uuid.uuid5(_AUTO_NAMESPACE, identity)


def _stage(status: str, reason: str | None = None) -> AutoPipelineStageOut:
    return AutoPipelineStageOut(status=status, reason_code=reason)  # type: ignore[arg-type]


async def _load_context(
    tenant: Tenant,
    version_id: uuid.UUID,
    *,
    expected_client_account_id: uuid.UUID | None = None,
) -> _PipelineContext:
    require_manager(tenant)
    delivery_enabled = auto_pipeline_enabled()
    from ..p3.delivery_repository import delivery_enabled as ingestion_delivery_enabled

    ingestion_delivery_active = ingestion_delivery_enabled()
    delivery_projection = (
        "delivery.state AS delivery_state,"
        "delivery.reason_code AS delivery_reason "
        if delivery_enabled
        else "NULL::text AS delivery_state,NULL::text AS delivery_reason "
    )
    delivery_join = (
        "LEFT JOIN f1.material_pipeline_delivery AS delivery ON "
        "delivery.enterprise_id=version.enterprise_id "
        "AND delivery.document_version_id=version.id "
        "AND delivery.delivery_kind='advance' "
        if delivery_enabled
        else ""
    )
    if ingestion_delivery_active:
        ingestion_delivery_projection = (
            "ingestion_delivery.state AS ingestion_delivery_state,"
            "ingestion_delivery.reason_code AS ingestion_delivery_reason,"
        )
    else:
        ingestion_delivery_projection = (
            "NULL::text AS ingestion_delivery_state,"
            "NULL::text AS ingestion_delivery_reason,"
        )
    ingestion_delivery_join = (
        "LEFT JOIN f1.material_ingestion_delivery AS ingestion_delivery ON "
        "ingestion_delivery.enterprise_id=version.enterprise_id "
        "AND ingestion_delivery.document_version_id=version.id "
        "AND ingestion_delivery.delivery_kind='resume' "
        if ingestion_delivery_active
        else ""
    )
    if delivery_enabled:
        analysis_order = "ORDER BY current_analysis.analysis_revision DESC,"
    else:
        analysis_order = "ORDER BY current_analysis.created_at DESC,"
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        row = (
            await session.execute(
                text(
                    "SELECT version.id AS version_id,source.content_type,"
                    "scope.scope_kind,scope.client_account_id,"
                    "task.processing_stage,task.object_state,"
                    "task.quarantine_status,task.scan_verdict,task.preview_status,"
                    "task.error_reason AS ingestion_reason,"
                    + ingestion_delivery_projection
                    +
                    "(task.released_at IS NOT NULL) AS released,"
                    "analysis.status AS analysis_status,"
                    "analysis.reason_code AS analysis_reason,"
                    "COALESCE((SELECT count(*) FROM f1.material_page_classification AS page "
                    "WHERE page.enterprise_id=analysis.enterprise_id "
                    "AND page.analysis_id=analysis.id AND page.ocr_required IS TRUE),0) "
                    "AS ocr_required_count,"
                    "rag_job.id AS index_job_id,rag_job.status AS index_status,"
                    "rag_job.error_reason AS index_reason,"
                    + delivery_projection
                    + "FROM f1.document_version AS version "
                    "JOIN f1.document AS source ON "
                    "source.enterprise_id=version.enterprise_id "
                    "AND source.id=version.source_document_id "
                    "JOIN f1.document_record AS record ON "
                    "record.enterprise_id=version.enterprise_id "
                    "AND record.id=version.document_record_id "
                    "JOIN f1.material_knowledge_scope AS scope ON "
                    "scope.enterprise_id=record.enterprise_id "
                    "AND scope.id=record.knowledge_scope_id "
                    "JOIN f1.upload_task AS task ON "
                    "task.enterprise_id=version.enterprise_id "
                    "AND task.id=version.upload_task_id "
                    "LEFT JOIN LATERAL ("
                    "SELECT current_analysis.id,current_analysis.enterprise_id,"
                    "current_analysis.status,current_analysis.reason_code "
                    "FROM f1.material_analysis AS current_analysis "
                    "WHERE current_analysis.enterprise_id=version.enterprise_id "
                    "AND current_analysis.document_version_id=version.id "
                    "AND current_analysis.analysis_version='material-v1' "
                    + analysis_order
                    +
                    "current_analysis.created_at DESC,current_analysis.id DESC LIMIT 1"
                    ") AS analysis ON TRUE "
                    "LEFT JOIN LATERAL ("
                    "SELECT job.id,job.status,job.error_reason "
                    "FROM f1.material_rag_job AS job "
                    "WHERE job.enterprise_id=version.enterprise_id "
                    "AND job.document_version_id=version.id "
                    "AND job.action IN ('index','rebuild') "
                    "ORDER BY job.created_at DESC,job.id DESC LIMIT 1"
                    ") AS rag_job ON TRUE "
                    + delivery_join
                    + ingestion_delivery_join
                    + "WHERE version.enterprise_id=:enterprise_id "
                    "AND version.id=:version_id "
                    "AND task.pipeline_kind='controlled_ingestion'"
                ),
                {
                    "enterprise_id": tenant.enterprise_id,
                    "version_id": version_id,
                },
            )
        ).mappings().one_or_none()
    if row is None:
        raise IngestionError("P3_DOCUMENT_NOT_FOUND", http_status=404)
    if expected_client_account_id is not None and (
        str(row["scope_kind"]) != "client"
        or row["client_account_id"] != expected_client_account_id
    ):
        # Same-enterprise cross-client guesses remain indistinguishable from a
        # missing version when a client route binds the expected account.
        raise IngestionError("P3_DOCUMENT_NOT_FOUND", http_status=404)
    return _PipelineContext(
        version_id=row["version_id"],
        content_type=str(row["content_type"]),
        scope_kind=str(row["scope_kind"]),
        client_account_id=row["client_account_id"],
        processing_stage=str(row["processing_stage"]),
        object_state=str(row["object_state"]),
        quarantine_status=str(row["quarantine_status"]),
        scan_verdict=str(row["scan_verdict"] or "queued"),
        preview_status=str(row["preview_status"] or "blocked"),
        ingestion_reason=(
            str(row["ingestion_reason"]) if row["ingestion_reason"] else None
        ),
        ingestion_delivery_state=(
            str(row["ingestion_delivery_state"])
            if row["ingestion_delivery_state"]
            else None
        ),
        ingestion_delivery_reason=(
            str(row["ingestion_delivery_reason"])
            if row["ingestion_delivery_reason"]
            else None
        ),
        released=bool(row["released"]),
        analysis_status=(
            str(row["analysis_status"]) if row["analysis_status"] else None
        ),
        analysis_reason=(
            str(row["analysis_reason"]) if row["analysis_reason"] else None
        ),
        ocr_required_count=int(row["ocr_required_count"] or 0),
        index_job_id=row["index_job_id"],
        index_status=(str(row["index_status"]) if row["index_status"] else None),
        index_reason=(str(row["index_reason"]) if row["index_reason"] else None),
        delivery_state=(
            str(row["delivery_state"]) if row["delivery_state"] else None
        ),
        delivery_reason=(
            str(row["delivery_reason"]) if row["delivery_reason"] else None
        ),
    )


async def _load_disabled_context(
    tenant: Tenant,
    version_id: uuid.UUID,
    *,
    expected_client_account_id: uuid.UUID | None = None,
) -> _PipelineContext:
    """Read only f1_0014-era tables before the auto feature is enabled."""
    require_manager(tenant)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        row = (
            await session.execute(
                text(
                    "SELECT version.id AS version_id,source.content_type,"
                    "scope.scope_kind,scope.client_account_id,"
                    "task.processing_stage,task.object_state,"
                    "task.quarantine_status,task.scan_verdict,task.preview_status,"
                    "task.error_reason AS ingestion_reason,"
                    "(task.released_at IS NOT NULL) AS released "
                    "FROM f1.document_version AS version "
                    "JOIN f1.document AS source ON "
                    "source.enterprise_id=version.enterprise_id "
                    "AND source.id=version.source_document_id "
                    "JOIN f1.document_record AS record ON "
                    "record.enterprise_id=version.enterprise_id "
                    "AND record.id=version.document_record_id "
                    "JOIN f1.material_knowledge_scope AS scope ON "
                    "scope.enterprise_id=record.enterprise_id "
                    "AND scope.id=record.knowledge_scope_id "
                    "JOIN f1.upload_task AS task ON "
                    "task.enterprise_id=version.enterprise_id "
                    "AND task.id=version.upload_task_id "
                    "WHERE version.enterprise_id=:enterprise_id "
                    "AND version.id=:version_id "
                    "AND task.pipeline_kind='controlled_ingestion'"
                ),
                {
                    "enterprise_id": tenant.enterprise_id,
                    "version_id": version_id,
                },
            )
        ).mappings().one_or_none()
    if row is None:
        raise IngestionError("P3_DOCUMENT_NOT_FOUND", http_status=404)
    if expected_client_account_id is not None and (
        str(row["scope_kind"]) != "client"
        or row["client_account_id"] != expected_client_account_id
    ):
        raise IngestionError("P3_DOCUMENT_NOT_FOUND", http_status=404)
    return _PipelineContext(
        version_id=row["version_id"],
        content_type=str(row["content_type"]),
        scope_kind=str(row["scope_kind"]),
        client_account_id=row["client_account_id"],
        processing_stage=str(row["processing_stage"]),
        object_state=str(row["object_state"]),
        quarantine_status=str(row["quarantine_status"]),
        scan_verdict=str(row["scan_verdict"] or "queued"),
        preview_status=str(row["preview_status"] or "blocked"),
        ingestion_reason=(
            str(row["ingestion_reason"]) if row["ingestion_reason"] else None
        ),
        ingestion_delivery_state=None,
        ingestion_delivery_reason=None,
        released=bool(row["released"]),
        analysis_status=None,
        analysis_reason=None,
        ocr_required_count=0,
        index_job_id=None,
        index_status=None,
        index_reason=None,
        delivery_state=None,
        delivery_reason=None,
    )


def _ingestion_stage(context: _PipelineContext) -> AutoPipelineStageOut:
    if context.ingestion_delivery_state == "blocked":
        return _stage(
            "failed",
            context.ingestion_delivery_reason
            or "MATERIAL_INGESTION_DELIVERY_FAILED",
        )
    if context.ingestion_delivery_state == "retry_wait":
        return _stage(
            "pending",
            context.ingestion_delivery_reason
            or "MATERIAL_INGESTION_DELIVERY_FAILED",
        )
    if (
        context.processing_stage == "ready"
        and context.object_state == "ready"
        and context.scan_verdict == "clean"
        and context.preview_status == "ready"
    ):
        return _stage("ready")
    if context.processing_stage in {"failed", "rejected"}:
        return _stage(
            "failed",
            public_reason_code(context.ingestion_reason) or "INGESTION_UNAVAILABLE",
        )
    if context.processing_stage == "retry_wait":
        return _stage(
            "pending",
            public_reason_code(context.ingestion_reason) or "INGESTION_RETRY_WAIT",
        )
    return _stage("running", "INGESTION_PROCESSING")


def _analysis_stage(context: _PipelineContext) -> AutoPipelineStageOut:
    if context.content_type != "application/pdf":
        return _stage("skipped", "AUTO_PIPELINE_PDF_ONLY")
    if context.ingestion_reason in _ANALYSIS_RETRY_MARKERS:
        return _stage(
            "failed",
            public_reason_code(context.ingestion_reason)
            or "MATERIAL_ANALYSIS_RETRY_REQUIRED",
        )
    if context.analysis_status == "failed":
        return _stage(
            "failed", context.analysis_reason or "MATERIAL_ANALYSIS_FAILED"
        )
    if context.analysis_status not in {"ready", "confirmed"}:
        return _stage("pending", "MATERIAL_ANALYSIS_PENDING")
    if context.ocr_required_count:
        return _stage("failed", "OCR_REQUIRED")
    return _stage("ready")


def _index_stage(context: _PipelineContext) -> AutoPipelineStageOut:
    if context.content_type != "application/pdf":
        return _stage("skipped", "AUTO_PIPELINE_PDF_ONLY")
    if context.index_status == "done":
        return _stage("ready")
    if context.index_status in {"running", "queued", "retry_wait"}:
        if context.index_job_id is not None:
            from .queue import index_dispatch_failure_reason

            try:
                dispatch_failure = index_dispatch_failure_reason(
                    context.index_job_id
                )
            except Exception:
                return _stage("failed", "MATERIAL_INDEX_STATUS_UNAVAILABLE")
            if dispatch_failure is not None:
                return _stage("failed", dispatch_failure)
    if context.index_status == "running":
        return _stage("running", "MATERIAL_INDEX_RUNNING")
    if context.index_status in {"queued", "retry_wait"}:
        return _stage(
            "pending", context.index_reason or "MATERIAL_INDEX_PENDING"
        )
    if context.index_status == "failed":
        return _stage(
            "failed", context.index_reason or "MATERIAL_INDEX_FAILED"
        )
    if not _local_index_mode_enabled():
        return _stage("disabled", "LOCAL_INDEX_DISABLED")
    return _stage("pending", "MATERIAL_INDEX_PENDING")


async def _current_report_source(
    tenant: Tenant, client_account_id: uuid.UUID
) -> tuple[str | None, str | None]:
    """Return current source fingerprint and a body-free prerequisite reason."""
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        if not await report_repository.active_binding_for_provider(
            session, tenant.enterprise_id, client_account_id
        ):
            return None, "REPORT_CLIENT_BINDING_REQUIRED"
        sources = await report_repository.load_eligible_sources(
            session, tenant.enterprise_id, client_account_id
        )
    kinds = {source.scope_kind for source in sources}
    if "service_provider" not in kinds:
        return None, "REPORT_PROVIDER_SOURCES_MISSING"
    if "client" not in kinds:
        return None, "REPORT_CLIENT_SOURCES_EMPTY"
    return (
        report_repository.fingerprint_for(
            tenant.enterprise_id, client_account_id, sources
        ),
        None,
    )


async def _report_stage(
    tenant: Tenant,
    context: _PipelineContext,
    index: AutoPipelineStageOut,
) -> AutoPipelineStageOut:
    if context.content_type != "application/pdf":
        return _stage("skipped", "AUTO_PIPELINE_PDF_ONLY")
    if context.scope_kind != "client" or context.client_account_id is None:
        return _stage("skipped", "REPORT_CLIENT_SCOPE_REQUIRED")
    if tenant.role not in PROVIDER_MEMBER_ROLES:
        return _stage("failed", "REPORT_PROVIDER_REQUIRED")
    if context.delivery_state == "blocked":
        return _stage(
            "failed",
            context.delivery_reason or "MATERIAL_PIPELINE_DELIVERY_BLOCKED",
        )
    if index.status != "ready":
        return _stage("pending", "REPORT_WAITING_FOR_INDEX")
    if (
        os.environ.get(REPORT_LOCAL_FLAG) != "1"
        or os.environ.get(ENGINEERING_FLAG) != "1"
        or not _local_redis_enabled()
    ):
        return _stage("disabled", "REPORT_GENERATION_DISABLED")
    from .queue import pipeline_dispatch_failure_reason

    try:
        dispatch_failure = pipeline_dispatch_failure_reason(context.version_id)
    except Exception:
        return _stage("failed", "REPORT_QUEUE_STATUS_UNAVAILABLE")
    if dispatch_failure is not None:
        return _stage("failed", dispatch_failure)
    fingerprint, reason = await _current_report_source(
        tenant, context.client_account_id
    )
    if fingerprint is None:
        return _stage("pending", reason or "REPORT_SOURCES_INCOMPLETE")
    generation_request_id = _request_id(
        "generate",
        tenant.enterprise_id,
        context.client_account_id,
        fingerprint,
    )
    create_request_id = _request_id(
        "create", tenant.enterprise_id, context.client_account_id
    )
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        job = await report_repository.get_job_by_request(
            session, tenant.enterprise_id, generation_request_id
        )
        report = await report_repository.get_report_by_create_request(
            session, tenant.enterprise_id, create_request_id
        )
        current = None
        if report is not None:
            current = await report_repository.get_report(
                session, tenant.enterprise_id, report["id"]
            )
    if job is not None:
        if job["status"] == "draft":
            return _stage("ready")
        if job["status"] == "generating":
            return _stage("running", "REPORT_GENERATING")
        if job["status"] == "queued":
            return _stage("pending", "REPORT_GENERATION_QUEUED")
        if job["status"] == "failed":
            return _stage(
                "failed", job.get("error_reason") or "REPORT_GENERATION_FAILED"
            )
    if current is not None and current.get("current_status") not in {
        None,
        "changes_requested",
        "failed",
        "superseded",
        "withdrawn",
    }:
        return _stage("pending", "REPORT_REVIEW_REQUIRED")
    return _stage("pending", "REPORT_GENERATION_PENDING")


async def auto_pipeline_status(
    tenant: Tenant,
    version_id: uuid.UUID,
    *,
    client_account_id: uuid.UUID | None = None,
) -> AutoPipelineOut:
    if not auto_pipeline_enabled():
        context = await _load_disabled_context(
            tenant,
            version_id,
            expected_client_account_id=client_account_id,
        )
        disabled = _stage("disabled", "MATERIAL_PIPELINE_DISABLED")
        return AutoPipelineOut(
            version_id=context.version_id,
            enabled=False,
            scope_kind=context.scope_kind,  # type: ignore[arg-type]
            ingestion=_ingestion_stage(context),
            analysis=disabled,
            index=disabled,
            report=disabled,
        )
    context = await _load_context(
        tenant,
        version_id,
        expected_client_account_id=client_account_id,
    )
    ingestion = _ingestion_stage(context)
    analysis = _analysis_stage(context)
    index = _index_stage(context)
    report = await _report_stage(tenant, context, index)
    return AutoPipelineOut(
        version_id=version_id,
        enabled=auto_pipeline_enabled(),
        scope_kind=context.scope_kind,  # type: ignore[arg-type]
        ingestion=ingestion,
        analysis=analysis,
        index=index,
        report=report,
    )


async def _dispatch_report_for_client(
    tenant: Tenant, client_account_id: uuid.UUID
) -> None:
    fingerprint, _reason = await _current_report_source(
        tenant, client_account_id
    )
    if fingerprint is None:
        return
    create_request_id = _request_id(
        "create", tenant.enterprise_id, client_account_id
    )
    report = await create_report(tenant, client_account_id, create_request_id)
    generation_request_id = _request_id(
        "generate",
        tenant.enterprise_id,
        client_account_id,
        fingerprint,
    )
    try:
        await generate_report(
            tenant,
            client_account_id,
            uuid.UUID(str(report["report_id"])),
            generation_request_id,
        )
    except ReportTransitionInvalid:
        # A generated draft/review version is intentionally not overwritten.
        # Status exposes REPORT_REVIEW_REQUIRED and a later replay can resume
        # after the existing review/publish lifecycle advances.
        return
    except ReportNotFound:
        # Missing active audience/source prerequisites remain fail-closed and
        # visible through the derived status endpoint.
        return


async def _dispatch_report(
    tenant: Tenant,
    context: _PipelineContext,
) -> None:
    if context.scope_kind != "client" or context.client_account_id is None:
        return
    await _dispatch_report_for_client(tenant, context.client_account_id)


async def _eligible_client_versions(tenant: Tenant) -> tuple[uuid.UUID, ...]:
    """Return one indexed current version per active bound client."""
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT DISTINCT ON (scope.client_account_id) version.id "
                    "FROM f1.document_version AS version "
                    "JOIN f1.document_record AS record ON "
                    "record.enterprise_id=version.enterprise_id "
                    "AND record.id=version.document_record_id "
                    "JOIN f1.upload_task AS task ON "
                    "task.enterprise_id=version.enterprise_id "
                    "AND task.id=version.upload_task_id "
                    "JOIN f1.material_knowledge_scope AS scope ON "
                    "scope.enterprise_id=record.enterprise_id "
                    "AND scope.id=record.knowledge_scope_id "
                    "JOIN f1.analysis_report_client_audience AS binding ON "
                    "binding.enterprise_id=scope.enterprise_id "
                    "AND binding.client_account_id=scope.client_account_id "
                    "AND binding.status='active' "
                    "JOIN f1.material_rag_unit AS unit ON "
                    "unit.enterprise_id=version.enterprise_id "
                    "AND unit.document_version_id=version.id "
                    "WHERE version.enterprise_id=:enterprise_id "
                    "AND scope.scope_kind='client' "
                    "AND scope.client_account_id IS NOT NULL "
                    "AND record.status='active' "
                    "AND version.version_no=record.latest_version_no "
                    "AND task.pipeline_kind='controlled_ingestion' "
                    "AND task.status='done' AND task.processing_stage='ready' "
                    "AND task.object_state='ready' AND task.scan_verdict='clean' "
                    "AND task.preview_status='ready' "
                    "AND task.quarantine_status='released' "
                    "AND task.released_at IS NOT NULL "
                    "ORDER BY scope.client_account_id,version.version_no DESC,version.id "
                    "LIMIT 200"
                ),
                {"enterprise_id": tenant.enterprise_id},
            )
        ).all()
    return tuple(row[0] for row in rows)


async def _sweep_version_ids(tenant: Tenant) -> tuple[uuid.UUID, ...]:
    """Bounded DB-truth sweep; terminal failures require explicit replay."""
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT version.id FROM f1.document_version AS version "
                    "JOIN f1.document_record AS record ON "
                    "record.enterprise_id=version.enterprise_id "
                    "AND record.id=version.document_record_id "
                    "JOIN f1.document AS source ON "
                    "source.enterprise_id=version.enterprise_id "
                    "AND source.id=version.source_document_id "
                    "JOIN f1.upload_task AS task ON "
                    "task.enterprise_id=version.enterprise_id "
                    "AND task.id=version.upload_task_id "
                    "JOIN LATERAL ("
                    "SELECT current_analysis.id,current_analysis.enterprise_id,"
                    "current_analysis.status "
                    "FROM f1.material_analysis AS current_analysis "
                    "WHERE current_analysis.enterprise_id=version.enterprise_id "
                    "AND current_analysis.document_version_id=version.id "
                    "AND current_analysis.analysis_version='material-v1' "
                    "ORDER BY current_analysis.analysis_revision DESC,"
                    "current_analysis.created_at DESC,current_analysis.id DESC LIMIT 1"
                    ") AS analysis ON TRUE "
                    "LEFT JOIN LATERAL (SELECT job.status FROM f1.material_rag_job AS job "
                    "WHERE job.enterprise_id=version.enterprise_id "
                    "AND job.document_version_id=version.id "
                    "AND job.action IN ('index','rebuild') "
                    "ORDER BY job.created_at DESC,job.id DESC LIMIT 1) AS latest ON TRUE "
                    "WHERE version.enterprise_id=:enterprise_id "
                    "AND record.status='active' "
                    "AND version.version_no=record.latest_version_no "
                    "AND source.content_type='application/pdf' "
                    "AND task.pipeline_kind='controlled_ingestion' "
                    "AND task.status='done' AND task.processing_stage='ready' "
                    "AND task.object_state='ready' AND task.scan_verdict='clean' "
                    "AND task.preview_status='ready' "
                    "AND analysis.status IN ('ready','confirmed') "
                    "AND NOT EXISTS (SELECT 1 FROM f1.material_page_classification AS page "
                    "WHERE page.enterprise_id=analysis.enterprise_id "
                    "AND page.analysis_id=analysis.id AND page.ocr_required IS TRUE) "
                    "AND (latest.status IS NULL OR latest.status<>'failed') "
                    "ORDER BY task.updated_at DESC,version.id LIMIT 100"
                ),
                {"enterprise_id": tenant.enterprise_id},
            )
        ).all()
    return tuple(row[0] for row in rows)


async def sweep_auto_pipeline(tenant: Tenant) -> int:
    """Replay bounded non-terminal work from PostgreSQL durable truth."""
    require_manager(tenant)
    if tenant.role not in PROVIDER_MEMBER_ROLES or not auto_pipeline_enabled():
        return 0
    version_ids = await _sweep_version_ids(tenant)
    for version_id in version_ids:
        await advance_auto_pipeline(
            tenant, version_id, _delivery_rearm=False
        )
    return len(version_ids)


async def dispatch_report_after_index(
    tenant: Tenant, version_id: uuid.UUID
) -> AutoPipelineOut:
    """API-credential report-worker continuation; generation stops at draft."""
    if not auto_pipeline_enabled():
        return await auto_pipeline_status(tenant, version_id)
    context = await _load_context(tenant, version_id)
    if _index_stage(context).status != "ready":
        raise RuntimeError("MATERIAL_LOCAL_INDEX_NOT_DONE")
    if context.scope_kind == "service_provider":
        from .queue import enqueue_report_stage

        for client_version_id in await _eligible_client_versions(tenant):
            # A new provider source changes every bound client's report
            # fingerprint.  Re-arm the stable client delivery before the
            # best-effort RQ continuation so Redis loss cannot erase fanout.
            await delivery_repository.register_delivery(
                tenant,
                client_version_id,
                rearm_terminal=True,
            )
            enqueue_report_stage(
                enterprise_id=tenant.enterprise_id,
                provider_sub=tenant.sub,
                version_id=client_version_id,
            )
        return await auto_pipeline_status(tenant, version_id)
    await _dispatch_report(tenant, context)
    return await auto_pipeline_status(tenant, version_id)


async def advance_auto_pipeline(
    tenant: Tenant,
    version_id: uuid.UUID,
    *,
    _delivery_rearm: bool | None = True,
) -> AutoPipelineOut:
    """Advance only safe, analyzed PDFs; exact replays resume idempotently."""
    if not auto_pipeline_enabled():
        return await auto_pipeline_status(tenant, version_id)
    context = await _load_context(tenant, version_id)
    if tenant.role not in PROVIDER_MEMBER_ROLES:
        return await auto_pipeline_status(tenant, version_id)
    if _ingestion_stage(context).status != "ready":
        return await auto_pipeline_status(tenant, version_id)
    if _analysis_stage(context).status != "ready":
        return await auto_pipeline_status(tenant, version_id)

    if _delivery_rearm is not None:
        # This commit is the durable hand-off.  Queue dispatch below is only
        # a latency optimization; the independent DB dispatcher reconstructs
        # it after Redis loss or any commit-to-enqueue process crash.  Manual
        # /process uses the default True to take over blocked/done work.
        await delivery_repository.register_delivery(
            tenant,
            version_id,
            rearm_terminal=_delivery_rearm,
        )

    from .queue import (
        enqueue_local_index_stage,
        enqueue_reconcile_stage,
        enqueue_recovery_sweep,
        enqueue_report_stage,
    )

    # Keep the existing direct replay for low latency.  PostgreSQL delivery,
    # registered above, is the recovery authority rather than this RQ call.
    enqueue_reconcile_stage(
        enterprise_id=tenant.enterprise_id,
        provider_sub=tenant.sub,
        version_id=version_id,
    )

    # The pre-committed replay may have already advanced this exact version.
    # Reload durable truth before acting so the foreground request and the
    # recovery delivery converge instead of racing a duplicate release.
    context = await _load_context(tenant, version_id)

    if not context.released:
        await act_on_version(tenant, version_id, action="release")
    if context.index_job_id is not None and context.index_status != "failed":
        job_id = context.index_job_id
    else:
        idempotency_key = f"auto-local-index:{version_id}"
        if context.index_job_id is not None:
            idempotency_key = (
                f"auto-local-index-retry:{version_id}:{context.index_job_id}"
            )
        job_id = await enqueue_job(
            tenant,
            document_version_id=version_id,
            action="index",
            idempotency_key=idempotency_key,
        )
    context = await _load_context(tenant, version_id)
    # Privilege split: the API only releases and enqueues.  The existing
    # generic worker owns f1_worker credentials for canonical-unit writes;
    # after success it dispatches the report continuation to report-worker,
    # which owns f1_api credentials.  The API never imports or calls worker
    # persistence code.
    if _index_stage(context).status == "ready":
        enqueue_recovery_sweep(
            enterprise_id=tenant.enterprise_id,
            provider_sub=tenant.sub,
        )
        enqueue_report_stage(
            enterprise_id=tenant.enterprise_id,
            provider_sub=tenant.sub,
            version_id=version_id,
        )
    else:
        enqueue_local_index_stage(
            index_job_id=job_id,
            enterprise_id=tenant.enterprise_id,
            provider_sub=tenant.sub,
            version_id=version_id,
        )
    return await auto_pipeline_status(tenant, version_id)


__all__ = (
    "AUTO_PIPELINE_FLAG",
    "LOCAL_INDEX_FLAG",
    "advance_auto_pipeline",
    "auto_pipeline_enabled",
    "auto_pipeline_status",
    "dispatch_report_after_index",
    "sweep_auto_pipeline",
)
