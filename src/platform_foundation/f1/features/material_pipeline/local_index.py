"""Lease-fenced PostgreSQL-only indexing for released PDFs.

This path intentionally stops at encrypted ``material_rag_unit`` rows.  It
never provisions a RAGFlow dataset and never calls an embedding or model
provider.  The physical RAGFlow worker remains a separate, mutually exclusive
runtime mode.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, BinaryIO

from sqlalchemy import text
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from ...database import session_scope
from ..material_intake.cloud_ocr import resolve_ocr_engine
from ..material_intake.ocr import (
    LocalOcrError,
    OcrPageResult,
    RETRYABLE_OCR_REASON_CODES,
    extract_pdf_text_pages,
)
from ..material_rag.contracts import (
    CanonicalUnit,
    MaterialRagIntegrityError,
    MaterialRagJobClaim,
    MaterialRagLeaseLost,
)
from ..material_rag.repository import (
    claim_job,
    finish_job,
    load_units_for_version,
    live_source_mutation_fence,
    persist_canonical_units,
    renew_job_lease,
)
from ..material_rag.security import canonical_page_units


ENGINEERING_FLAG = "F1_LOCAL_ENGINEERING"
AUTO_PIPELINE_FLAG = "F1_MATERIAL_AUTO_PIPELINE_LOCAL"
LOCAL_INDEX_FLAG = "F1_MATERIAL_RAG_LOCAL_INDEX"
PHYSICAL_ORCHESTRATION_FLAG = "F1_MATERIAL_RAG_ORCHESTRATION_LOCAL"
PDF_PARSER_VERSION = "pypdf-6.14.2"
MAX_PDF_PAGES = 128
MAX_PAGE_TEXT_CHARACTERS = 100_000
MAX_DOCUMENT_TEXT_CHARACTERS = 2_000_000


@dataclass(frozen=True, slots=True)
class LocalIndexOutcome:
    kind: str
    job_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class _ReleasedPdf:
    object_key: str
    source_size: int
    source_etag: str


class _LocalIndexFailure(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def local_index_enabled() -> bool:
    """Use the DB-only mode only under explicit local engineering flags."""
    return (
        os.environ.get(AUTO_PIPELINE_FLAG) == "1"
        and os.environ.get(ENGINEERING_FLAG) == "1"
        and os.environ.get(LOCAL_INDEX_FLAG) == "1"
        # Prevent one job from racing the physical RAGFlow worker mode.
        and os.environ.get(PHYSICAL_ORCHESTRATION_FLAG) != "1"
    )


@asynccontextmanager
async def _claimed_session(
    claim: MaterialRagJobClaim,
) -> AsyncIterator[AsyncSession]:
    async with session_scope(role="f1_worker") as session:
        await session.execute(
            text("SELECT set_config('f1.material_rag_job_id',:id,true)"),
            {"id": str(claim.id)},
        )
        await session.execute(
            text("SELECT set_config('f1.material_rag_lease_token',:token,true)"),
            {"token": str(claim.lease_token)},
        )
        yield session


async def _released_pdf(claim: MaterialRagJobClaim) -> _ReleasedPdf:
    async with _claimed_session(claim) as session:
        row = (
            await session.execute(
                text(
                    "SELECT task.object_key,task.source_size,task.source_etag "
                    "FROM f1.document_record AS record "
                    "JOIN f1.document_version AS version ON "
                    "version.enterprise_id=record.enterprise_id "
                    "AND version.document_record_id=record.id "
                    "JOIN f1.upload_task AS task ON "
                    "task.enterprise_id=version.enterprise_id "
                    "AND task.id=version.upload_task_id "
                    "WHERE record.enterprise_id=:enterprise_id "
                    "AND record.id=:record_id "
                    "AND record.knowledge_scope_id=:scope_id "
                    "AND version.id=:version_id "
                    "AND task.id=:task_id "
                    "AND task.content_sha256=:source_sha "
                    "AND task.pipeline_kind='controlled_ingestion' "
                    "AND task.status='done' AND task.processing_stage='ready' "
                    "AND task.object_state='ready' AND task.scan_verdict='clean' "
                    "AND task.preview_kind='page_text' "
                    "AND task.preview_status='ready' "
                    "AND task.quarantine_status='released' "
                    "AND task.released_at IS NOT NULL"
                ),
                {
                    "enterprise_id": claim.enterprise_id,
                    "record_id": claim.document_record_id,
                    "scope_id": claim.knowledge_scope_id,
                    "version_id": claim.document_version_id,
                    "task_id": claim.upload_task_id,
                    "source_sha": claim.source_sha256,
                },
            )
        ).mappings().one_or_none()
    if row is None:
        raise _LocalIndexFailure("MATERIAL_VERSION_NOT_INDEXABLE")
    object_key = str(row["object_key"] or "")
    source_etag = str(row["source_etag"] or "")
    source_size = int(row["source_size"] or 0)
    if (
        not object_key.endswith(".pdf")
        or not source_etag
        or not 1 <= source_size <= 50 * 1024 * 1024
    ):
        raise _LocalIndexFailure("MATERIAL_RAG_PDF_SOURCE_INVALID")
    return _ReleasedPdf(object_key, source_size, source_etag)


def _open_source(claim: MaterialRagJobClaim, source: _ReleasedPdf) -> BinaryIO:
    from ... import storage

    try:
        return storage.open_quarantine_source(
            source.object_key,
            claim.source_sha256,
            source.source_size,
            source.source_etag,
        )
    except Exception as error:
        code = str(error)
        retryable = code in {
            "SOURCE_OBJECT_STAT_FAILED",
            "SOURCE_OBJECT_READ_FAILED",
        }
        raise _LocalIndexFailure(
            "MATERIAL_RAG_SOURCE_UNAVAILABLE"
            if retryable
            else "MATERIAL_RAG_SOURCE_INVALID",
            retryable=retryable,
        ) from error


def _parse_pdf(
    claim: MaterialRagJobClaim,
    source: _ReleasedPdf,
    *,
    ocr_pages: Callable[..., tuple[OcrPageResult, ...]] | None = None,
) -> tuple[CanonicalUnit, ...]:
    source_file = _open_source(claim, source)
    try:
        if source_file.read(5) != b"%PDF-":
            raise ValueError
        source_file.seek(0)
        raw = source_file.read()
        if (
            len(raw) != source.source_size
            or hashlib.sha256(raw).hexdigest() != claim.source_sha256
        ):
            raise _LocalIndexFailure("MATERIAL_RAG_SOURCE_INVALID")
        try:
            pages = extract_pdf_text_pages(
                raw,
                expected_sha256=claim.source_sha256,
                ocr_threshold_characters=40,
                ocr_pages=ocr_pages,
            )
        except LocalOcrError as error:
            raise _LocalIndexFailure(
                error.code,
                retryable=error.code in RETRYABLE_OCR_REASON_CODES,
            ) from error
        if not 1 <= len(pages) <= MAX_PDF_PAGES:
            raise ValueError
        units: list[CanonicalUnit] = []
        total_characters = 0
        for page in pages:
            if page.ocr_required:
                reason = next(
                    (
                        value
                        for value in page.reason_codes
                        if value != "OCR_REQUIRED"
                    ),
                    "OCR_REQUIRED",
                )
                raise _LocalIndexFailure(
                    reason,
                    retryable=reason in RETRYABLE_OCR_REASON_CODES,
                )
            page_text = page.text
            if len(page_text) > MAX_PAGE_TEXT_CHARACTERS:
                raise _LocalIndexFailure("OCR_OUTPUT_LIMIT")
            total_characters += len(page_text)
            if total_characters > MAX_DOCUMENT_TEXT_CHARACTERS:
                raise ValueError
            if not page_text.strip():
                continue
            units.extend(
                canonical_page_units(
                    enterprise_id=claim.enterprise_id,
                    knowledge_scope_id=claim.knowledge_scope_id,
                    document_record_id=claim.document_record_id,
                    document_version_id=claim.document_version_id,
                    source_sha256=claim.source_sha256,
                    page_number=page.page_number,
                    parser_version=(
                        page.parser_backend
                        if page.ocr_applied
                        else PDF_PARSER_VERSION
                    ),
                    text=page_text,
                    ocr_applied=page.ocr_applied,
                    table_candidate=page.table_candidate,
                    two_column_candidate=page.two_column_candidate,
                )
            )
        if not units:
            raise ValueError
        return tuple(units)
    except _LocalIndexFailure:
        raise
    except Exception as error:
        raise _LocalIndexFailure("MATERIAL_RAG_PDF_TEXT_INVALID") from error
    finally:
        source_file.close()


def _validate_units(
    claim: MaterialRagJobClaim, units: tuple[CanonicalUnit, ...]
) -> None:
    if not units or any(
        unit.enterprise_id != claim.enterprise_id
        or unit.knowledge_scope_id != claim.knowledge_scope_id
        or unit.document_record_id != claim.document_record_id
        or unit.document_version_id != claim.document_version_id
        or unit.source_sha256 != claim.source_sha256
        for unit in units
    ):
        raise MaterialRagIntegrityError("MATERIAL_RAG_UNIT_JOB_MISMATCH")


def _unit_fingerprint(unit: CanonicalUnit) -> tuple[object, ...]:
    return (
        unit.id,
        unit.enterprise_id,
        unit.knowledge_scope_id,
        unit.document_record_id,
        unit.document_version_id,
        unit.source_sha256,
        unit.page_number,
        unit.ordinal,
        unit.parser_version,
        unit.body_sha256,
        unit.ocr_applied,
        unit.table_candidate,
        unit.two_column_candidate,
    )


def _manifest_sha256(
    claim: MaterialRagJobClaim, units: tuple[CanonicalUnit, ...]
) -> str:
    payload = json.dumps(
        {
            "document_version_id": str(claim.document_version_id),
            "source_sha256": claim.source_sha256,
            "units": [
                [str(unit.id), unit.page_number, unit.ordinal, unit.body_sha256]
                for unit in units
            ],
            "version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


async def _finish_failure(
    claim: MaterialRagJobClaim, failure: _LocalIndexFailure
) -> LocalIndexOutcome:
    status = "retry_wait" if failure.retryable else "failed"
    kwargs: dict[str, object] = {"status": status, "reason": failure.code}
    if failure.retryable:
        kwargs["retry_seconds"] = min(300, 15 * max(1, claim.attempt))
    finished = await finish_job(claim, **kwargs)
    if not finished:
        kind = "LEASE_LOST"
    elif failure.retryable:
        kind = "RETRY_WAIT"
    else:
        kind = "FAILED"
    return LocalIndexOutcome(
        kind=kind,
        job_id=claim.id,
    )


async def run_local_index_job(
    job_id: uuid.UUID,
    *,
    worker_id: str,
) -> LocalIndexOutcome:
    """Claim and finish one exact DB-only material index job."""
    if not local_index_enabled():
        return LocalIndexOutcome(kind="DISABLED", job_id=job_id)
    claim = await claim_job(job_id, worker_id=worker_id, lease_seconds=900)
    if claim is None:
        return LocalIndexOutcome(kind="CLAIM_NONE", job_id=job_id)
    if claim.action not in {"index", "rebuild"}:
        return await _finish_failure(
            claim, _LocalIndexFailure("MATERIAL_RAG_LOCAL_ACTION_INVALID")
        )
    try:
        if not await renew_job_lease(claim, lease_seconds=900):
            raise MaterialRagLeaseLost("MATERIAL_RAG_LEASE_LOST")
        source = await _released_pdf(claim)
        engine = resolve_ocr_engine()
        units = await asyncio.to_thread(_parse_pdf, claim, source, ocr_pages=engine.pages)
        _validate_units(claim, units)
        # Re-prove the exact released source under this live job lease while
        # writing encrypted canonical units.  No remote mutation occurs.
        with live_source_mutation_fence(claim, lease_seconds=900):
            async with _claimed_session(claim) as session:
                await persist_canonical_units(session, units)
                await session.commit()
        async with _claimed_session(claim) as session:
            stored = await load_units_for_version(
                session,
                enterprise_id=claim.enterprise_id,
                knowledge_scope_id=claim.knowledge_scope_id,
                document_version_id=claim.document_version_id,
            )
        _validate_units(claim, stored)
        if tuple(_unit_fingerprint(item) for item in stored) != tuple(
            _unit_fingerprint(item) for item in units
        ):
            raise MaterialRagIntegrityError(
                "MATERIAL_RAG_STORED_MANIFEST_MISMATCH"
            )
        finished = await finish_job(
            claim,
            status="done",
            result_manifest_sha256=_manifest_sha256(claim, stored),
            indexed_unit_count=len(stored),
        )
        return LocalIndexOutcome(
            kind="DONE" if finished else "LEASE_LOST", job_id=claim.id
        )
    except _LocalIndexFailure as failure:
        return await _finish_failure(claim, failure)
    except MaterialRagLeaseLost:
        return LocalIndexOutcome(kind="LEASE_LOST", job_id=claim.id)
    except MaterialRagIntegrityError as error:
        reason = str(error)
        if not reason or len(reason) > 80:
            reason = "MATERIAL_RAG_INTEGRITY_FAILED"
        return await _finish_failure(claim, _LocalIndexFailure(reason))
    except (RuntimeError, ValueError):
        return await _finish_failure(
            claim, _LocalIndexFailure("MATERIAL_RAG_LOCAL_FAILED")
        )


__all__ = (
    "AUTO_PIPELINE_FLAG",
    "ENGINEERING_FLAG",
    "LOCAL_INDEX_FLAG",
    "LocalIndexOutcome",
    "local_index_enabled",
    "run_local_index_job",
)
