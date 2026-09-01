"""Fail-closed P3 quarantine processor.

The product API invokes this pipeline after a successful upload and also keeps
the explicit process/retry recovery seams.  It never creates an outbox event
or calls the legacy upload/indexing worker.  The source stays quarantined until
local scanning and bounded preview generation both succeed; even then it
remains held until a separate release.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from contextlib import closing
from dataclasses import dataclass

from sqlalchemy import text

from ...auth import Tenant
from ...database import session_scope
from ..material_intake.analyzer import MaterialAnalysisFailure, analyze_pdf
from ..material_intake.cloud_ocr import resolve_ocr_engine
from ..material_intake.contracts import (
    MATERIAL_ANALYSIS_VERSION,
    MaterialAnalysisResult,
)
from ..material_intake.ocr import LocalOcrError, RETRYABLE_OCR_REASON_CODES
from ..material_intake.service import (
    clear_ocr_checkpoints,
    load_ocr_checkpoints,
    material_analysis_recovery_enabled,
    persist_material_analysis,
    persist_ocr_checkpoint,
)
from .contracts import (
    ALLOWED_FORMATS,
    IngestionError,
    MAX_ATTEMPTS,
    PREVIEW_TIMEOUT_SECONDS,
)
from .preview import PreviewFailure, PreviewResult, build_preview
from .scanner import ScanFailure, ScanResult, scan_stream


P3_PIPELINE_KIND = "controlled_ingestion"
_MANAGER_ROLES = frozenset(("super_admin", "enterprise_admin", "plant_admin"))
_CONTENT_KIND = {item.content_type: item.kind for item in ALLOWED_FORMATS.values()}
_SCANNER_UNAVAILABLE = frozenset(
    {
        "P3_SCANNER_UNAVAILABLE",
        "P3_SCANNER_DNS_FAILED",
        "P3_SCANNER_REFUSED",
        "P3_SCANNER_TIMEOUT",
        "P3_SCANNER_CONNECT_REFUSED",
        "P3_SCANNER_CONNECT_RESET",
        "P3_SCANNER_CONNECT_PIPE",
        "P3_SCANNER_VERSION_REFUSED",
        "P3_SCANNER_VERSION_RESET",
        "P3_SCANNER_VERSION_PIPE",
        "P3_SCANNER_STREAM_REFUSED",
        "P3_SCANNER_STREAM_RESET",
        "P3_SCANNER_STREAM_PIPE",
        "P3_SCAN_ENGINE_ERROR",
        "P3_SCAN_PROTOCOL_ERROR",
    }
)
_RETRYABLE_ANALYSIS_REASONS = frozenset(
    {
        *RETRYABLE_OCR_REASON_CODES,
        "MATERIAL_ANALYSIS_FAILED",
        "MATERIAL_SOURCE_READ_FAILED",
        "MATERIAL_ANALYSIS_PERSIST_FAILED",
    }
)
_ANALYSIS_TASK_MARKERS = _RETRYABLE_ANALYSIS_REASONS


@dataclass(frozen=True, slots=True)
class _ProcessClaim:
    version_id: uuid.UUID
    task_id: uuid.UUID
    document_id: uuid.UUID
    object_key: str
    content_sha256: str
    source_size: int
    source_etag: str
    content_type: str
    kind: str
    token: uuid.UUID
    attempt: int


@dataclass(frozen=True, slots=True)
class _ReadyPdfSource:
    version_id: uuid.UUID
    task_id: uuid.UUID
    object_key: str
    content_sha256: str
    source_size: int
    source_etag: str
    page_count: int
    analysis_status: str | None
    analysis_source_sha256: str | None
    analysis_ocr_required_count: int


class _ProcessOutcome(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        scan_verdict: str,
        preview_status: str,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.scan_verdict = scan_verdict
        self.preview_status = preview_status


def _require_manager(tenant: Tenant) -> None:
    if tenant.role not in _MANAGER_ROLES:
        raise IngestionError("P3_MANAGER_REQUIRED", http_status=403)


async def _claim_process(
    tenant: Tenant, version_id: uuid.UUID
) -> _ProcessClaim:
    _require_manager(tenant)
    token = uuid.uuid4()
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        row = (
            await session.execute(
                text(
                    "SELECT task.id AS task_id,task.document_id,task.object_key,"
                    "task.content_sha256,task.source_size,task.source_etag,"
                    "task.processing_stage,task.object_state,task.quarantine_status,"
                    "task.attempt,task.lease_until,source.content_type "
                    "FROM f1.document_version AS version "
                    "JOIN f1.upload_task AS task "
                    "ON task.enterprise_id=version.enterprise_id "
                    "AND task.id=version.upload_task_id "
                    "JOIN f1.document AS source "
                    "ON source.enterprise_id=task.enterprise_id "
                    "AND source.id=task.document_id "
                    "WHERE version.enterprise_id=:enterprise_id "
                    "AND version.id=:version_id "
                    "AND task.pipeline_kind=:pipeline_kind FOR UPDATE OF task"
                ),
                {
                    "enterprise_id": tenant.enterprise_id,
                    "version_id": version_id,
                    "pipeline_kind": P3_PIPELINE_KIND,
                },
            )
        ).mappings().first()
        if row is None:
            raise IngestionError("P3_DOCUMENT_NOT_FOUND", http_status=404)
        content_type = str(row["content_type"])
        kind = _CONTENT_KIND.get(content_type)
        if kind is None or not row.get("source_etag") or not row.get("source_size"):
            raise IngestionError("P3_SOURCE_IDENTITY_MISMATCH", http_status=409)
        if (
            str(row["object_state"]) != "quarantined"
            or str(row["quarantine_status"]) != "held"
            or int(row["attempt"]) >= MAX_ATTEMPTS
        ):
            raise IngestionError("P3_ILLEGAL_STATE_TRANSITION", http_status=409)
        stage = str(row["processing_stage"])
        if stage not in {"received", "retry_wait", "scanning", "validating", "previewing"}:
            raise IngestionError("P3_ILLEGAL_STATE_TRANSITION", http_status=409)
        active_lease = row.get("lease_until") is not None
        if stage in {"scanning", "validating", "previewing"} and active_lease:
            busy = (
                await session.execute(
                    text(
                        "SELECT lease_until > statement_timestamp() FROM f1.upload_task "
                        "WHERE id=:task_id AND enterprise_id=:enterprise_id"
                    ),
                    {
                        "task_id": row["task_id"],
                        "enterprise_id": tenant.enterprise_id,
                    },
                )
            ).scalar_one()
            if bool(busy):
                raise IngestionError("P3_PROCESSING_IN_PROGRESS", http_status=409)
        claimed = (
            await session.execute(
                text(
                    "UPDATE f1.upload_task SET status='scanning',"
                    "processing_stage='scanning',scan_verdict='scanning',"
                    "preview_status='blocked',error_reason=NULL,attempt=attempt+1,"
                    "lease_token=:token,lease_owner='p3-api',"
                    "lease_acquired_at=statement_timestamp(),"
                    "lease_until=statement_timestamp()+interval '180 seconds',"
                    "updated_at=statement_timestamp() WHERE id=:task_id "
                    "AND enterprise_id=:enterprise_id "
                    "AND pipeline_kind=:pipeline_kind "
                    "AND object_state='quarantined' AND quarantine_status='held' "
                    "RETURNING attempt"
                ),
                {
                    "token": token,
                    "task_id": row["task_id"],
                    "enterprise_id": tenant.enterprise_id,
                    "pipeline_kind": P3_PIPELINE_KIND,
                },
            )
        ).first()
        if claimed is None:
            raise IngestionError("P3_PROCESSING_IN_PROGRESS", http_status=409)
        await session.execute(
            text(
                "INSERT INTO f1.audit_log "
                "(id,enterprise_id,user_sub,action,resource_type,resource_id,result) "
                "VALUES (:id,:enterprise_id,:sub,'document.version.process',"
                "'document_version',:resource_id,'scanning')"
            ),
            {
                "id": uuid.uuid4(),
                "enterprise_id": tenant.enterprise_id,
                "sub": tenant.sub,
                "resource_id": str(version_id),
            },
        )
        await session.commit()
    return _ProcessClaim(
        version_id=version_id,
        task_id=row["task_id"],
        document_id=row["document_id"],
        object_key=str(row["object_key"]),
        content_sha256=str(row["content_sha256"]),
        source_size=int(row["source_size"]),
        source_etag=str(row["source_etag"]),
        content_type=content_type,
        kind=kind,
        token=token,
        attempt=int(claimed[0]),
    )


def _open_source(claim: _ProcessClaim):
    from ... import storage

    return storage.open_quarantine_source(
        claim.object_key,
        claim.content_sha256,
        claim.source_size,
        claim.source_etag,
    )


def _material_analysis_reason(result: MaterialAnalysisResult) -> str | None:
    """Return a fixed fail-closed reason for incomplete OCR, if any."""

    incomplete = tuple(page for page in result.pages if page.ocr_required)
    if not incomplete:
        return None
    for preferred in (
        "OCR_UNAVAILABLE",
        "OCR_DISABLED",
        "OCR_PAGE_LIMIT",
        "OCR_OUTPUT_INSUFFICIENT",
    ):
        if any(preferred in page.reason_codes for page in incomplete):
            return preferred
    return "OCR_REQUIRED"


async def _ready_pdf_source(
    tenant: Tenant, version_id: uuid.UUID
) -> _ReadyPdfSource | None:
    """Resolve an exact ready PDF and its retryable analysis state under RLS."""

    _require_manager(tenant)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        analysis_order = (
            " ORDER BY candidate.analysis_revision DESC,candidate.id DESC"
            if material_analysis_recovery_enabled()
            else ""
        )
        row = (
            await session.execute(
                text(
                    "SELECT task.id AS task_id,task.object_key,"
                    "task.content_sha256,task.source_size,task.source_etag,"
                    "task.object_state,task.processing_stage,task.scan_verdict,"
                    "task.preview_status,task.preview_unit_count,source.content_type,"
                    "analysis.status AS analysis_status,"
                    "analysis.source_sha256 AS analysis_source_sha256,"
                    "analysis.ocr_required_count "
                    "FROM f1.document_version AS version "
                    "JOIN f1.upload_task AS task ON "
                    "task.enterprise_id=version.enterprise_id "
                    "AND task.id=version.upload_task_id "
                    "JOIN f1.document AS source ON "
                    "source.enterprise_id=version.enterprise_id "
                    "AND source.id=version.source_document_id "
                    "LEFT JOIN LATERAL ("
                    "SELECT candidate.status,candidate.source_sha256,"
                    "COALESCE((SELECT count(*) "
                    "FROM f1.material_page_classification AS page "
                    "WHERE page.enterprise_id=candidate.enterprise_id "
                    "AND page.analysis_id=candidate.id "
                    "AND page.ocr_required IS TRUE),0) AS ocr_required_count "
                    "FROM f1.material_analysis AS candidate "
                    "WHERE candidate.enterprise_id=version.enterprise_id "
                    "AND candidate.document_version_id=version.id "
                    "AND candidate.analysis_version=:analysis_version "
                    + analysis_order
                    + " LIMIT 1) AS analysis ON TRUE "
                    "WHERE version.enterprise_id=:enterprise_id "
                    "AND version.id=:version_id "
                    "AND task.pipeline_kind=:pipeline_kind"
                ),
                {
                    "analysis_version": MATERIAL_ANALYSIS_VERSION,
                    "enterprise_id": tenant.enterprise_id,
                    "version_id": version_id,
                    "pipeline_kind": P3_PIPELINE_KIND,
                },
            )
        ).mappings().one_or_none()
    if row is None:
        raise IngestionError("P3_DOCUMENT_NOT_FOUND", http_status=404)
    if (
        str(row["object_state"]) != "ready"
        or str(row["processing_stage"]) != "ready"
        or str(row["scan_verdict"]) != "clean"
        or str(row["preview_status"]) != "ready"
        or not row.get("source_etag")
        or not row.get("source_size")
        or not 1 <= int(row["preview_unit_count"]) <= 128
    ):
        raise IngestionError("MATERIAL_ANALYSIS_SOURCE_NOT_READY", http_status=409)
    if str(row["content_type"]) != "application/pdf":
        return None
    return _ReadyPdfSource(
        version_id=version_id,
        task_id=row["task_id"],
        object_key=str(row["object_key"]),
        content_sha256=str(row["content_sha256"]),
        source_size=int(row["source_size"]),
        source_etag=str(row["source_etag"]),
        page_count=int(row["preview_unit_count"]),
        analysis_status=(
            str(row["analysis_status"])
            if row.get("analysis_status") is not None
            else None
        ),
        analysis_source_sha256=(
            str(row["analysis_source_sha256"])
            if row.get("analysis_source_sha256") is not None
            else None
        ),
        analysis_ocr_required_count=int(row.get("ocr_required_count") or 0),
    )


def _open_ready_pdf(source: _ReadyPdfSource):
    from ... import storage

    return storage.open_quarantine_source(
        source.object_key,
        source.content_sha256,
        source.source_size,
        source.source_etag,
    )


async def _set_analysis_task_marker(
    tenant: Tenant, source: _ReadyPdfSource, reason: str | None
) -> None:
    if reason is not None and reason not in _ANALYSIS_TASK_MARKERS:
        raise ValueError("MATERIAL_ANALYSIS_REASON_INVALID")
    setting = reason is not None
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        updated = (
            await session.execute(
                text(
                    "UPDATE f1.upload_task SET error_reason=:reason,"
                    "updated_at=statement_timestamp() WHERE id=:task_id "
                    "AND enterprise_id=:enterprise_id "
                    "AND pipeline_kind=:pipeline_kind "
                    "AND object_state='ready' AND processing_stage='ready' "
                    "AND scan_verdict='clean' AND preview_status='ready' "
                    "AND (error_reason IS NULL OR error_reason IN "
                    "('OCR_DISABLED','OCR_UNAVAILABLE',"
                    "'OCR_PAGE_LIMIT','OCR_OUTPUT_INSUFFICIENT','OCR_REQUIRED',"
                    "'MATERIAL_ANALYSIS_FAILED','MATERIAL_SOURCE_READ_FAILED',"
                    "'MATERIAL_ANALYSIS_PERSIST_FAILED')) "
                    "RETURNING id"
                ),
                {
                    "reason": reason,
                    "task_id": source.task_id,
                    "enterprise_id": tenant.enterprise_id,
                    "pipeline_kind": P3_PIPELINE_KIND,
                },
            )
        ).first()
        if updated is None:
            if reason is None:
                # Clearing is idempotent and must not erase or block on a
                # marker owned by another subsystem.
                return
            raise IngestionError("MATERIAL_ANALYSIS_SOURCE_NOT_READY", http_status=409)
        if setting:
            await session.execute(
                text(
                    "INSERT INTO f1.audit_log "
                    "(id,enterprise_id,user_sub,action,resource_type,resource_id,result) "
                    "VALUES (:id,:enterprise_id,:sub,:action,"
                    "'document_version',:resource_id,:result)"
                ),
                {
                    "id": uuid.uuid4(),
                    "enterprise_id": tenant.enterprise_id,
                    "sub": tenant.sub,
                    "action": (
                        "material.analysis.persist"
                        if reason == "MATERIAL_ANALYSIS_PERSIST_FAILED"
                        else "material.analysis.deferred"
                    ),
                    "resource_id": str(source.version_id),
                    "result": (
                        "failed"
                        if reason == "MATERIAL_ANALYSIS_PERSIST_FAILED"
                        else "retry_wait"
                    ),
                },
            )
        await session.commit()


async def retry_ready_material_analysis(
    tenant: Tenant, version_id: uuid.UUID
) -> None:
    """Retry a missing, deferred, or failed analysis for one exact ready PDF."""

    recovery_enabled = material_analysis_recovery_enabled()
    source = await _ready_pdf_source(tenant, version_id)
    if source is None:
        return
    if source.analysis_status in {"ready", "confirmed"}:
        if source.analysis_source_sha256 != source.content_sha256:
            raise IngestionError(
                "MATERIAL_ANALYSIS_SOURCE_IDENTITY_MISMATCH", http_status=409
            )
        if (
            source.analysis_status == "confirmed"
            and source.analysis_ocr_required_count > 0
        ):
            raise IngestionError(
                "MATERIAL_ANALYSIS_CONFIRMED_OCR_REVIEW_REQUIRED",
                http_status=409,
            )
        if (
            source.analysis_status == "ready"
            and recovery_enabled
            and source.analysis_ocr_required_count > 0
        ):
            # f1_0023 permits one immutable successor to a machine-ready
            # snapshot only when its persisted pages still carry OCR debt.
            # Keep the predecessor and any usable encrypted checkpoints.
            pass
        else:
            if recovery_enabled:
                await clear_ocr_checkpoints(
                    tenant,
                    document_version_id=source.version_id,
                    source_sha256=source.content_sha256,
                )
            await _set_analysis_task_marker(tenant, source, None)
            return
    if source.analysis_status == "failed":
        if source.analysis_source_sha256 != source.content_sha256:
            raise IngestionError(
                "MATERIAL_ANALYSIS_SOURCE_IDENTITY_MISMATCH", http_status=409
            )
        if not recovery_enabled:
            # f1_0014 keeps one immutable snapshot and has no successor chain.
            return
        # Recovery appends a new immutable analysis revision.  The historical
        # failure remains readable and is never updated in place.

    result: MaterialAnalysisResult | None = None
    reason: str | None = None
    page_count = source.page_count
    checkpoints = ()
    # One engine per run: checkpoint resume identity and fresh OCR must come
    # from the same provider, and providers are never mixed mid-process.
    engine = resolve_ocr_engine()
    if recovery_enabled:
        try:
            checkpoints = await load_ocr_checkpoints(
                tenant,
                document_version_id=source.version_id,
                source_sha256=source.content_sha256,
                expected_page_count=source.page_count,
                parser_backend=engine.backend,
            )
        except Exception:
            await _set_analysis_task_marker(
                tenant, source, "MATERIAL_ANALYSIS_PERSIST_FAILED"
            )
            raise IngestionError(
                "MATERIAL_ANALYSIS_PERSIST_FAILED", http_status=503
            ) from None
    loop = asyncio.get_running_loop()

    def _checkpoint_page(page) -> None:
        future = asyncio.run_coroutine_threadsafe(
            persist_ocr_checkpoint(
                tenant,
                document_version_id=source.version_id,
                source_sha256=source.content_sha256,
                expected_page_count=source.page_count,
                result=page,
            ),
            loop,
        )
        try:
            future.result(timeout=30.0)
        except Exception:
            future.cancel()
            raise LocalOcrError("MATERIAL_ANALYSIS_PERSIST_FAILED") from None

    try:
        opened = await asyncio.to_thread(_open_ready_pdf, source)
        with closing(opened) as source_file:
            try:
                result = await asyncio.to_thread(
                    analyze_pdf,
                    source_file,
                    expected_sha256=source.content_sha256,
                    ocr_checkpoints=checkpoints,
                    ocr_checkpoint_callback=(
                        _checkpoint_page
                        if recovery_enabled
                        else None
                    ),
                    ocr_pages=engine.pages,
                    ocr_parser_backend=engine.backend,
                )
                page_count = len(result.pages)
                reason = _material_analysis_reason(result)
            except MaterialAnalysisFailure as error:
                reason = error.code
                page_count = error.page_count or source.page_count
            except Exception:
                reason = "MATERIAL_ANALYSIS_FAILED"
    except Exception:
        reason = "MATERIAL_SOURCE_READ_FAILED"

    if reason in _RETRYABLE_ANALYSIS_REASONS:
        await _set_analysis_task_marker(tenant, source, reason)
        return

    try:
        await persist_material_analysis(
            tenant,
            document_version_id=source.version_id,
            source_sha256=source.content_sha256,
            page_count=page_count,
            result=result,
            reason_code=reason,
        )
        if result is not None and recovery_enabled:
            await clear_ocr_checkpoints(
                tenant,
                document_version_id=source.version_id,
                source_sha256=source.content_sha256,
            )
        await _set_analysis_task_marker(tenant, source, None)
    except IngestionError:
        raise
    except RuntimeError as error:
        if str(error) == "MATERIAL_ANALYSIS_CONFIRMED_OCR_REVIEW_REQUIRED":
            # Confirmation may race the earlier source snapshot.  Preserve
            # the explicit terminal boundary instead of masking it as a
            # generic persistence outage.
            raise IngestionError(str(error), http_status=409) from None
        try:
            await _set_analysis_task_marker(
                tenant, source, "MATERIAL_ANALYSIS_PERSIST_FAILED"
            )
        except Exception:
            pass
        raise IngestionError(
            "MATERIAL_ANALYSIS_PERSIST_FAILED", http_status=503
        ) from None
    except Exception:
        try:
            await _set_analysis_task_marker(
                tenant, source, "MATERIAL_ANALYSIS_PERSIST_FAILED"
            )
        except Exception:
            pass
        raise IngestionError(
            "MATERIAL_ANALYSIS_PERSIST_FAILED", http_status=503
        ) from None


async def resume_controlled_ingestion(
    tenant: Tenant,
    version_id: uuid.UUID,
    *,
    scanner_host: str = "clamd",
    scanner_port: int = 3310,
) -> None:
    """Process a held source, or retry only analysis once P3 is already ready."""

    try:
        ready = await _ready_pdf_source(tenant, version_id)
    except IngestionError as error:
        if error.code != "MATERIAL_ANALYSIS_SOURCE_NOT_READY":
            raise
        await process_controlled_ingestion(
            tenant,
            version_id,
            scanner_host=scanner_host,
            scanner_port=scanner_port,
        )
        return
    if ready is not None:
        await retry_ready_material_analysis(tenant, version_id)
        return


async def _advance_after_scan(
    tenant: Tenant, claim: _ProcessClaim, result: ScanResult
) -> None:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        updated = (
            await session.execute(
                text(
                    "UPDATE f1.upload_task SET processing_stage='validating',"
                    "scan_verdict='clean',scanner_engine=:engine,"
                    "scanner_version=:engine_version,signature_version=:signature_version,"
                    "lease_until=statement_timestamp()+interval '180 seconds',"
                    "updated_at=statement_timestamp() WHERE id=:task_id "
                    "AND enterprise_id=:enterprise_id "
                    "AND object_state='quarantined' AND quarantine_status='held' "
                    "AND processing_stage='scanning' AND lease_token=:token "
                    "RETURNING id"
                ),
                {
                    "engine": result.engine,
                    "engine_version": result.engine_version,
                    "signature_version": result.signature_version,
                    "task_id": claim.task_id,
                    "enterprise_id": tenant.enterprise_id,
                    "token": claim.token,
                },
            )
        ).first()
        if updated is None:
            raise IngestionError("P3_PROCESS_OWNERSHIP_LOST", http_status=409)
        await session.commit()


async def _advance_to_previewing(tenant: Tenant, claim: _ProcessClaim) -> None:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        updated = (
            await session.execute(
                text(
                    "UPDATE f1.upload_task SET processing_stage='previewing',"
                    "preview_status='generating',"
                    "lease_until=statement_timestamp()+interval '180 seconds',"
                    "updated_at=statement_timestamp() WHERE id=:task_id "
                    "AND enterprise_id=:enterprise_id "
                    "AND object_state='quarantined' AND quarantine_status='held' "
                    "AND processing_stage='validating' AND scan_verdict='clean' "
                    "AND lease_token=:token RETURNING id"
                ),
                {
                    "task_id": claim.task_id,
                    "enterprise_id": tenant.enterprise_id,
                    "token": claim.token,
                },
            )
        ).first()
        if updated is None:
            raise IngestionError("P3_PROCESS_OWNERSHIP_LOST", http_status=409)
        await session.commit()


def _store_preview(claim: _ProcessClaim, result: PreviewResult) -> None:
    from ... import storage

    for artifact in result.units:
        stored = storage.store_ingestion_preview_unit(
            task_id=claim.task_id,
            unit_id=artifact.id,
            content=artifact.content,
            content_type=artifact.content_type,
        )
        if int(stored.size) != len(artifact.content):
            raise RuntimeError("P3_PREVIEW_IDENTITY_INVALID")
        loaded = storage.read_ingestion_preview_artifact(
            task_id=claim.task_id,
            unit_id=artifact.id,
            content_type=artifact.content_type,
            expected_sha256=artifact.sha256,
            expected_size=len(artifact.content),
        )
        if loaded != artifact.content:
            raise RuntimeError("P3_PREVIEW_IDENTITY_INVALID")
    manifest = json.dumps(
        result.payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if hashlib.sha256(manifest).hexdigest() != result.sha256:
        raise RuntimeError("P3_PREVIEW_IDENTITY_INVALID")
    storage.store_ingestion_preview_unit(
        task_id=claim.task_id,
        unit_id="manifest",
        content=manifest,
        content_type="application/json",
    )
    loaded_manifest = storage.read_ingestion_preview_manifest(
        task_id=claim.task_id, expected_sha256=result.sha256
    )
    if loaded_manifest != manifest:
        raise RuntimeError("P3_PREVIEW_IDENTITY_INVALID")


async def _publish_ready(
    tenant: Tenant, claim: _ProcessClaim, result: PreviewResult
) -> None:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        updated = (
            await session.execute(
                text(
                    "UPDATE f1.upload_task SET status='done',object_state='ready',"
                    "processing_stage='ready',scan_verdict='clean',"
                    "preview_status='ready',preview_kind=:preview_kind,"
                    "preview_sha256=:preview_sha256,preview_unit_count=:unit_count,"
                    "error_reason=NULL,next_attempt_at=NULL,lease_token=NULL,"
                    "lease_owner=NULL,lease_acquired_at=NULL,lease_until=NULL,"
                    "updated_at=statement_timestamp() WHERE id=:task_id "
                    "AND enterprise_id=:enterprise_id "
                    "AND pipeline_kind=:pipeline_kind "
                    "AND object_state='quarantined' AND quarantine_status='held' "
                    "AND processing_stage='previewing' AND scan_verdict='clean' "
                    "AND preview_status='generating' AND lease_token=:token "
                    "AND lease_until > statement_timestamp() RETURNING id"
                ),
                {
                    "preview_kind": result.kind,
                    "preview_sha256": result.sha256,
                    "unit_count": result.unit_count,
                    "task_id": claim.task_id,
                    "enterprise_id": tenant.enterprise_id,
                    "pipeline_kind": P3_PIPELINE_KIND,
                    "token": claim.token,
                },
            )
        ).first()
        if updated is None:
            raise IngestionError("P3_PROCESS_OWNERSHIP_LOST", http_status=409)
        await session.execute(
            text(
                "UPDATE f1.document SET status='scanning' "
                "WHERE id=:document_id AND enterprise_id=:enterprise_id"
            ),
            {
                "document_id": claim.document_id,
                "enterprise_id": tenant.enterprise_id,
            },
        )
        await session.execute(
            text(
                "INSERT INTO f1.audit_log "
                "(id,enterprise_id,user_sub,action,resource_type,resource_id,result) "
                "VALUES (:id,:enterprise_id,:sub,'document.version.process',"
                "'document_version',:resource_id,'ready')"
            ),
            {
                "id": uuid.uuid4(),
                "enterprise_id": tenant.enterprise_id,
                "sub": tenant.sub,
                "resource_id": str(claim.version_id),
            },
        )
        await session.commit()


async def _finish_failure(
    tenant: Tenant,
    claim: _ProcessClaim,
    *,
    code: str,
    retryable: bool,
    scan_verdict: str,
    preview_status: str,
) -> None:
    retryable = bool(retryable and claim.attempt < MAX_ATTEMPTS)
    stage = "retry_wait" if retryable else "failed"
    quarantine_status = "held" if retryable else "blocked"
    document_status = "pending" if retryable else "failed"
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        updated = (
            await session.execute(
                text(
                    "UPDATE f1.upload_task SET status='failed',object_state='quarantined',"
                    "processing_stage=:stage,quarantine_status=:quarantine_status,"
                    "scan_verdict=:scan_verdict,preview_status=:preview_status,"
                    "error_reason=:error_reason,next_attempt_at=NULL,"
                    "lease_token=NULL,lease_owner=NULL,lease_acquired_at=NULL,"
                    "lease_until=NULL,updated_at=statement_timestamp() "
                    "WHERE id=:task_id AND enterprise_id=:enterprise_id "
                    "AND pipeline_kind=:pipeline_kind "
                    "AND lease_token=:token RETURNING id"
                ),
                {
                    "stage": stage,
                    "quarantine_status": quarantine_status,
                    "scan_verdict": scan_verdict,
                    "preview_status": preview_status,
                    "error_reason": code,
                    "task_id": claim.task_id,
                    "enterprise_id": tenant.enterprise_id,
                    "pipeline_kind": P3_PIPELINE_KIND,
                    "token": claim.token,
                },
            )
        ).first()
        if updated is None:
            raise IngestionError("P3_PROCESS_OWNERSHIP_LOST", http_status=409)
        await session.execute(
            text(
                "UPDATE f1.document SET status=:status "
                "WHERE id=:document_id AND enterprise_id=:enterprise_id"
            ),
            {
                "status": document_status,
                "document_id": claim.document_id,
                "enterprise_id": tenant.enterprise_id,
            },
        )
        await session.execute(
            text(
                "INSERT INTO f1.audit_log "
                "(id,enterprise_id,user_sub,action,resource_type,resource_id,result) "
                "VALUES (:id,:enterprise_id,:sub,'document.version.process',"
                "'document_version',:resource_id,:result)"
            ),
            {
                "id": uuid.uuid4(),
                "enterprise_id": tenant.enterprise_id,
                "sub": tenant.sub,
                "resource_id": str(claim.version_id),
                "result": stage,
            },
        )
        await session.commit()


async def process_controlled_ingestion(
    tenant: Tenant,
    version_id: uuid.UUID,
    *,
    scanner_host: str = "clamd",
    scanner_port: int = 3310,
) -> None:
    """Process one held version; failures become durable, body-free states."""
    if scanner_host == "clamd":
        configured = os.environ.get("F1_CLAMD_HOST")
        if configured:
            scanner_host = configured
    claim = await _claim_process(tenant, version_id)
    phase = "source"
    try:
        source = await asyncio.to_thread(_open_source, claim)
        with closing(source) as source_file:
            phase = "scan"
            scan_result = await asyncio.to_thread(
                scan_stream,
                source_file,
                expected_size=claim.source_size,
                expected_sha256=claim.content_sha256,
                host=scanner_host,
                port=scanner_port,
            )
            if scan_result.verdict == "infected":
                raise _ProcessOutcome(
                    "P3_MALWARE_DETECTED",
                    retryable=False,
                    scan_verdict="infected",
                    preview_status="blocked",
                )
            if scan_result.verdict != "clean":
                raise _ProcessOutcome(
                    "P3_SCAN_PROTOCOL_ERROR",
                    retryable=True,
                    scan_verdict="error",
                    preview_status="blocked",
                )
            await _advance_after_scan(tenant, claim, scan_result)
            await _advance_to_previewing(tenant, claim)
            phase = "preview"
            started_at = time.monotonic()
            preview = await asyncio.to_thread(build_preview, claim.kind, source_file)
            if time.monotonic() - started_at > PREVIEW_TIMEOUT_SECONDS:
                raise PreviewFailure("P3_PREVIEW_TIMEOUT", retryable=True)
            await asyncio.to_thread(_store_preview, claim, preview)
        phase = "publish"
        await _publish_ready(tenant, claim, preview)
        if claim.kind == "pdf":
            # OCR/analysis runs only after the scanner + preview lease has
            # safely published the immutable source.  It can now outlive the
            # 180s P3 lease and be retried without rescanning or rewriting the
            # preview.
            phase = "analysis"
            await retry_ready_material_analysis(tenant, claim.version_id)
    except ScanFailure as error:
        await _finish_failure(
            tenant,
            claim,
            code=error.code,
            retryable=error.retryable,
            scan_verdict="unavailable" if error.code in _SCANNER_UNAVAILABLE else "error",
            preview_status="blocked",
        )
    except PreviewFailure as error:
        await _finish_failure(
            tenant,
            claim,
            code=error.code,
            retryable=error.retryable,
            scan_verdict="clean",
            preview_status="failed",
        )
    except _ProcessOutcome as error:
        await _finish_failure(
            tenant,
            claim,
            code=error.code,
            retryable=error.retryable,
            scan_verdict=error.scan_verdict,
            preview_status=error.preview_status,
        )
    except IngestionError:
        raise
    except Exception:
        if phase == "analysis":
            # The P3 claim was already safely completed.  Never try to roll a
            # ready source back through the expired scan/preview lease.
            raise IngestionError(
                "MATERIAL_ANALYSIS_FAILED", http_status=503
            ) from None
        preview_phase = phase in {"preview", "publish"}
        await _finish_failure(
            tenant,
            claim,
            code=(
                "P3_PREVIEW_TEMPORARY_FAILURE"
                if preview_phase
                else "P3_SOURCE_READ_FAILED"
            ),
            retryable=True,
            scan_verdict="clean" if preview_phase else "error",
            preview_status="failed" if preview_phase else "blocked",
        )


__all__ = (
    "process_controlled_ingestion",
    "resume_controlled_ingestion",
    "retry_ready_material_analysis",
)
