"""Local-only due-queue orchestrator for released material PDFs.

Enabled exclusively when ``F1_MATERIAL_RAG_ORCHESTRATION_LOCAL=1`` and
``F1_LOCAL_ENGINEERING=1``.  Default API, default compose, and default
migrate stay closed.  Public ``run_once`` accepts only a worker id and
lease; it never takes a manifest key, arbitrary body, or physical ids.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass

from pypdf import PdfReader
from sqlalchemy import text as sql_text

from .contracts import (
    CanonicalUnit,
    MaterialRagIntegrityError,
    MaterialRagJobClaim,
)
from .repository import claim_next_job, finish_job, renew_job_lease
from .security import (
    CLIENT_B_ISOLATION_CANARY_TEXT,
    PROVIDER_POLICY_CANARY_TEXT,
    canonical_page_units,
    canonical_unit,
    create_released_unit_manifest_proof,
    create_synthetic_unit_manifest_proof,
)
from .worker import claimed_session, process_claimed_demo_job


ORCH_FLAG = "F1_MATERIAL_RAG_ORCHESTRATION_LOCAL"
ENGINEERING_FLAG = "F1_LOCAL_ENGINEERING"
HOLD_AFTER_CLAIM_MS_FLAG = "F1_MATERIAL_RAG_WORKER_HOLD_AFTER_CLAIM_MS"
PARSER_VERSION = "pgint1"
PDF_PARSER_VERSION = "pypdf-6.14.2"
MAX_PDF_PAGES = 128
MAX_PAGE_TEXT_CHARACTERS = 100_000
MAX_DOCUMENT_TEXT_CHARACTERS = 2_000_000
_CANARY_BODY = {
    hashlib.sha256(PROVIDER_POLICY_CANARY_TEXT.encode("utf-8")).hexdigest():
        PROVIDER_POLICY_CANARY_TEXT,
    hashlib.sha256(CLIENT_B_ISOLATION_CANARY_TEXT.encode("utf-8")).hexdigest():
        CLIENT_B_ISOLATION_CANARY_TEXT,
}


@dataclass(frozen=True, slots=True)
class OrchestratorOutcome:
    kind: str


@dataclass(frozen=True, slots=True)
class _ReleasedPdf:
    object_key: str
    source_size: int
    source_etag: str


class _SourceFailure(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def orchestration_enabled() -> bool:
    return os.environ.get(ORCH_FLAG) == "1" and os.environ.get(ENGINEERING_FLAG) == "1"


async def _released_pdf(claim: MaterialRagJobClaim) -> _ReleasedPdf:
    """Resolve only the exact clean/released source behind the live job lease."""
    async with claimed_session(claim) as session:
        row = (
            await session.execute(
                sql_text(
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
        raise _SourceFailure("MATERIAL_VERSION_NOT_INDEXABLE")
    object_key = str(row["object_key"] or "")
    source_etag = str(row["source_etag"] or "")
    source_size = int(row["source_size"] or 0)
    if (
        not object_key.endswith(".pdf")
        or not source_etag
        or not 1 <= source_size <= 50 * 1024 * 1024
    ):
        raise _SourceFailure("MATERIAL_RAG_PDF_SOURCE_INVALID")
    return _ReleasedPdf(object_key, source_size, source_etag)


def _parse_released_pdf(
    claim: MaterialRagJobClaim, source: _ReleasedPdf
) -> tuple[CanonicalUnit, ...]:
    """Read the hash-pinned P3 object and produce bounded, redacted text units."""
    from ... import storage

    try:
        source_file = storage.open_quarantine_source(
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
        raise _SourceFailure(
            (
                "MATERIAL_RAG_SOURCE_UNAVAILABLE"
                if retryable
                else "MATERIAL_RAG_SOURCE_INVALID"
            ),
            retryable=retryable,
        ) from error

    try:
        if source_file.read(5) != b"%PDF-":
            raise ValueError
        source_file.seek(0)
        reader = PdfReader(
            source_file,
            strict=True,
            password=None,
            root_object_recovery_limit=10_000,
        )
        if reader.is_encrypted or not 1 <= len(reader.pages) <= MAX_PDF_PAGES:
            raise ValueError
        units: list[CanonicalUnit] = []
        total_characters = 0
        for page_number, page in enumerate(reader.pages, start=1):
            page_text = page.extract_text(extraction_mode="plain") or ""
            if len(page_text) > MAX_PAGE_TEXT_CHARACTERS:
                raise ValueError
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
                    page_number=page_number,
                    parser_version=PDF_PARSER_VERSION,
                    text=page_text,
                )
            )
        if not units:
            raise ValueError
        return tuple(units)
    except _SourceFailure:
        raise
    except Exception as error:
        raise _SourceFailure("MATERIAL_RAG_PDF_TEXT_INVALID") from error
    finally:
        source_file.close()


async def _finish_source_failure(
    claim: MaterialRagJobClaim, failure: _SourceFailure
) -> OrchestratorOutcome:
    kwargs = {
        "status": "retry_wait" if failure.retryable else "failed",
        "reason": failure.code,
    }
    if failure.retryable:
        kwargs["retry_seconds"] = min(300, 15 * max(1, claim.attempt))
    finished = await finish_job(claim, **kwargs)
    return OrchestratorOutcome(kind="FINISH_TRUE" if finished else "FINISH_FALSE")


async def _process_fenced_claim(claim: MaterialRagJobClaim):
    if claim.action == "delete":
        return await process_claimed_demo_job(claim)
    text = _CANARY_BODY.get(claim.source_sha256)
    if text is not None:
        unit = canonical_unit(
            enterprise_id=claim.enterprise_id,
            knowledge_scope_id=claim.knowledge_scope_id,
            document_record_id=claim.document_record_id,
            document_version_id=claim.document_version_id,
            source_sha256=claim.source_sha256,
            page_number=1,
            ordinal=1,
            parser_version=PARSER_VERSION,
            text=text,
        )
        units = (unit,)
        proof = create_synthetic_unit_manifest_proof(claim=claim, units=units)
    else:
        try:
            if not await renew_job_lease(claim, lease_seconds=300):
                return OrchestratorOutcome(kind="LEASE_LOST")
            source = await _released_pdf(claim)
            units = await asyncio.to_thread(_parse_released_pdf, claim, source)
            proof = create_released_unit_manifest_proof(claim=claim, units=units)
        except _SourceFailure as failure:
            return await _finish_source_failure(claim, failure)
        except (MaterialRagIntegrityError, RuntimeError, ValueError):
            return await _finish_source_failure(
                claim, _SourceFailure("MATERIAL_RAG_MANIFEST_INVALID")
            )
    return await process_claimed_demo_job(
        claim, units=units, manifest_proof=proof
    )


async def _hold_after_claim() -> None:
    raw = os.environ.get(HOLD_AFTER_CLAIM_MS_FLAG, "")
    if raw == "":
        return
    if not raw.isdigit():
        raise RuntimeError("MATERIAL_RAG_WORKER_HOLD_INVALID")
    value = int(raw)
    if value < 1 or value > 5000:
        raise RuntimeError("MATERIAL_RAG_WORKER_HOLD_INVALID")
    await asyncio.sleep(value / 1000.0)


async def run_once(*, worker_id: str, lease_seconds: int = 30):
    if not orchestration_enabled():
        return OrchestratorOutcome(kind="DISABLED")
    claim = await claim_next_job(worker_id=worker_id, lease_seconds=lease_seconds)
    if claim is None:
        return OrchestratorOutcome(kind="EMPTY")
    await _hold_after_claim()
    return await _process_fenced_claim(claim)


__all__ = (
    "ENGINEERING_FLAG",
    "ORCH_FLAG",
    "OrchestratorOutcome",
    "orchestration_enabled",
    "run_once",
)
