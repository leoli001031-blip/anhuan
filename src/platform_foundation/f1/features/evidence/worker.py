"""Extract verified native bytes outside the short, fenced SQL transaction."""
from __future__ import annotations

import asyncio
import hashlib
import uuid

from . import repository
from .docx_native import DocxExtractionError, PARSER_VERSION, SUPPORT_PROFILE, extract_docx
from .envelopes import EXTRACTION_CONTRACT, build_docx_payload, build_native_payload
from .formats import CONTRACTS
from .xlsx_native import extract_xlsx, XlsxExtractionError
from .jpeg_native import extract_jpeg, JpegExtractionError


class NativeSourceError(RuntimeError):
    def __init__(self, reason: str, *, retryable: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


def _extract_payload(claim: repository.NativeClaim) -> dict:
    from ... import storage, source_gateway

    if (CONTRACTS.get(claim.source_format) != (claim.parser_version, claim.support_profile)
            or claim.extraction_contract != EXTRACTION_CONTRACT):
        raise NativeSourceError("NATIVE_PARSER_UNSUPPORTED")
    try:
        if source_gateway.gateway_enabled():
            raw = source_gateway.fetch_source("native", claim.job_id, claim.lease_token, claim.source_sha256, claim.source_size)
        else:
            raw = storage.read_released_native_source(
                claim.object_key, claim.source_sha256, claim.source_size,
            )
    except storage.StorageError as error:
        temporary = str(error) in {"SOURCE_OBJECT_STAT_FAILED", "SOURCE_OBJECT_READ_FAILED", "SOURCE_OBJECT_MISSING"}
        raise NativeSourceError("NATIVE_SOURCE_UNAVAILABLE" if temporary else "NATIVE_SOURCE_INVALID",
                                retryable=temporary) from None
    # Use these exact verified bytes for parsing; no second object fetch.
    if len(raw) != claim.source_size or hashlib.sha256(raw).hexdigest() != claim.source_sha256:
        raise NativeSourceError("NATIVE_SOURCE_INVALID")
    try:
        parser = {"docx": extract_docx, "xlsx": extract_xlsx, "jpeg": extract_jpeg}[claim.source_format]
        from ...ocr_cache import task_scope
        with task_scope('native', claim.job_id, claim.lease_token, claim.enterprise_id, claim.document_version_id):
            extraction = parser(raw, expected_sha256=claim.source_sha256)
    except (DocxExtractionError, XlsxExtractionError, JpegExtractionError):
        raise NativeSourceError("NATIVE_PARSE_REJECTED") from None
    if claim.source_format == "jpeg" and extraction.debts:
        reason = extraction.debts[0].reason_code
        if reason != "OCR_OUTPUT_INSUFFICIENT":
            raise NativeSourceError("NATIVE_OCR_UNAVAILABLE", retryable=extraction.retryable)
    try:
        builder = build_docx_payload if claim.source_format == "docx" else build_native_payload
        extra = {} if claim.source_format == "docx" else {"source_format": claim.source_format}
        return builder(extraction, **extra, enterprise_id=claim.enterprise_id,
            knowledge_scope_id=claim.knowledge_scope_id, document_record_id=claim.document_record_id,
            document_version_id=claim.document_version_id, revision_id=claim.revision_id)
    except ValueError as error:
        if str(error) == "NATIVE_EXTRACTION_PAYLOAD_LIMIT":
            raise NativeSourceError("NATIVE_PARSE_REJECTED") from None
        raise


async def _heartbeat(claim: repository.NativeClaim, stop: asyncio.Event,
                     lost: asyncio.Event, *, interval: float = 30) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except TimeoutError:
            pass
        try:
            live = await repository.renew_lease(claim.job_id, claim.lease_token)
        except Exception:
            live = False
        if not live:
            lost.set()
            return


async def run_job(job_id: uuid.UUID, token: uuid.UUID) -> str:
    if not repository.native_extraction_enabled():
        return "DISABLED"
    claim = await repository.read_claim(job_id, token)
    if claim is None:
        return "STALE"
    stop, lost = asyncio.Event(), asyncio.Event()
    heartbeat = asyncio.create_task(_heartbeat(claim, stop, lost))
    try:
        payload = await asyncio.to_thread(_extract_payload, claim)
        if lost.is_set() or not await repository.renew_lease(job_id, token):
            return "STALE"
        result = await repository.finalize(claim, payload)
        return "DONE" if result is not None else "STALE"
    except NativeSourceError as error:
        if not lost.is_set():
            await repository.finish_failure(job_id, token, reason=error.reason,
                retry_seconds=30 if error.retryable else None)
        return "RETRY" if error.retryable else "BLOCKED"
    except Exception:
        # An uncertain commit must never overwrite a result. This function's
        # DB CAS can only affect the same still-running token; done stays done.
        if not lost.is_set():
            await repository.finish_failure(job_id, token,
                reason="NATIVE_EXTRACTION_FAILED", retry_seconds=30)
        return "RETRY"
    finally:
        stop.set()
        await heartbeat


def run_native_extraction(job_id: str, token: str) -> None:
    asyncio.run(run_job(uuid.UUID(job_id), uuid.UUID(token)))
