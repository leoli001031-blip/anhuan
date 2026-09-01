"""Persistence and read service for vendor-neutral material analysis."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Mapping
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import Tenant
from ...database import session_scope
from ..material_rag.security import decrypt_text, encrypt_text
from .contracts import (
    MATERIAL_ANALYSIS_VERSION,
    MATERIAL_INTAKE_BOUNDARIES,
    MaterialAnalysisOut,
    MaterialAnalysisResult,
    material_allowed_actions,
)
from .ocr import (
    MAX_OCR_CHECKPOINT_TEXT_BYTES,
    MAX_OCR_PAGE_TEXT_CHARACTERS,
    OCR_PARSER_BACKEND,
    OCR_PARSER_BACKENDS,
    OcrPageResult,
    ocr_checkpoint_aad,
)


_MANAGER_ROLES = frozenset(("super_admin", "enterprise_admin", "plant_admin"))
_MAX_OCR_CHECKPOINT_TOTAL_BYTES = 32 * 1024 * 1024
_ANALYSIS_COLUMNS_BASE = (
    "analysis.id,analysis.document_version_id,analysis.source_sha256,"
    "analysis.analysis_version,{revision_columns}analysis.parser_backend,analysis.status,"
    "analysis.document_profile,analysis.shadow_status,analysis.reason_code,"
    "analysis.suggested_kind,analysis.suggested_kind_confidence_ppm,"
    "analysis.resolved_kind,analysis.classification_source,"
    "analysis.classification_by_user_id,analysis.classification_at,"
    "analysis.page_count,analysis.candidate_count,analysis.policy_source_id,"
    "analysis.policy_version_id,analysis.confirmed_at,analysis.created_at,"
    "analysis.updated_at"
)
_PAGE_COLUMNS = (
    "page_number,primary_kind,ocr_required,table_candidate,two_column_candidate,"
    "text_character_count,text_confidence_ppm,scan_confidence_ppm,"
    "table_confidence_ppm,two_column_confidence_ppm,reason_codes"
)
_CANDIDATE_COLUMNS = (
    "id,field_name,candidate_value,page_number,evidence_snippet,confidence_ppm,"
    "confidence_basis,calibrated,producer"
)


def _require_viewer(tenant: Tenant) -> None:
    if tenant.role not in _MANAGER_ROLES:
        raise HTTPException(status_code=403, detail="MATERIAL_MANAGER_REQUIRED")


def material_analysis_recovery_enabled() -> bool:
    """The f1_0022 schema is exclusive to the local automatic candidate."""
    return os.environ.get("F1_MATERIAL_AUTO_PIPELINE_LOCAL") == "1"


def _analysis_columns() -> str:
    revision_columns = (
        "analysis.analysis_revision,analysis.supersedes_analysis_id,"
        if material_analysis_recovery_enabled()
        else ""
    )
    return _ANALYSIS_COLUMNS_BASE.format(revision_columns=revision_columns)


async def _register_auto_pipeline_delivery_if_enabled(
    session: AsyncSession,
    tenant: Tenant,
    document_version_id: uuid.UUID,
) -> None:
    """Atomically hand a ready analysis to the local automatic pipeline."""
    if tenant.role not in {"super_admin", "enterprise_admin"}:
        return
    # Lazy imports keep the material-analysis module independent when the
    # local engineering pipeline is disabled (the production-safe default).
    from ..material_pipeline.coordinator import auto_pipeline_enabled

    if not auto_pipeline_enabled():
        return
    from ..material_pipeline.repository import register_delivery_in_session

    await register_delivery_in_session(
        session,
        tenant,
        document_version_id,
        rearm_terminal=False,
    )


def _checkpoint_body(result: OcrPageResult) -> tuple[str, bytes, str]:
    if (
        not result.ocr_applied
        or result.status != "applied"
        or result.reason_code != "OCR_APPLIED"
        or result.parser_backend not in OCR_PARSER_BACKENDS
        or result.source_unit_id is None
        or not 1 <= result.page_number <= 128
        or not 40 <= result.character_count <= MAX_OCR_PAGE_TEXT_CHARACTERS
        or sum(not character.isspace() for character in result.text)
        != result.character_count
        or len(result.text) > MAX_OCR_PAGE_TEXT_CHARACTERS
        or (
            result.confidence_mean_ppm is not None
            and (
                type(result.confidence_mean_ppm) is not int
                or not 0 <= result.confidence_mean_ppm <= 1_000_000
            )
        )
        or type(result.table_candidate) is not bool
        or type(result.two_column_candidate) is not bool
    ):
        raise RuntimeError("MATERIAL_OCR_CHECKPOINT_INVALID")
    body = result.text.encode("utf-8")
    if not 1 <= len(body) <= MAX_OCR_CHECKPOINT_TEXT_BYTES:
        raise RuntimeError("MATERIAL_OCR_CHECKPOINT_SIZE_LIMIT")
    return result.text, body, hashlib.sha256(body).hexdigest()


async def _purge_expired_ocr_checkpoints(
    session: AsyncSession, tenant: Tenant
) -> None:
    """Opportunistically delete at most 256 expired rows visible to this actor."""

    await session.execute(
        text(
            "DELETE FROM f1.material_ocr_checkpoint AS checkpoint USING ("
            "SELECT candidate.id FROM f1.material_ocr_checkpoint AS candidate "
            "WHERE candidate.enterprise_id=:enterprise_id "
            "AND candidate.expires_at<=statement_timestamp() "
            "ORDER BY candidate.expires_at,candidate.id LIMIT 256"
            ") AS expired WHERE checkpoint.enterprise_id=:enterprise_id "
            "AND checkpoint.id=expired.id"
        ),
        {"enterprise_id": tenant.enterprise_id},
    )


async def load_ocr_checkpoints(
    tenant: Tenant,
    *,
    document_version_id: uuid.UUID,
    source_sha256: str,
    expected_page_count: int,
    parser_backend: str = OCR_PARSER_BACKEND,
) -> tuple[OcrPageResult, ...]:
    """Load unexpired, identity-bound OCR page bodies and purge stale rows."""

    _require_viewer(tenant)
    if (
        parser_backend not in OCR_PARSER_BACKENDS
        or not 1 <= expected_page_count <= 128
        or len(source_sha256) != 64
        or any(character not in "0123456789abcdef" for character in source_sha256)
    ):
        raise RuntimeError("MATERIAL_OCR_CHECKPOINT_IDENTITY_INVALID")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        source = (
            await session.execute(
                text(
                    "SELECT task.id FROM f1.document_version AS version "
                    "JOIN f1.upload_task AS task ON "
                    "task.enterprise_id=version.enterprise_id "
                    "AND task.id=version.upload_task_id "
                    "JOIN f1.document AS document ON "
                    "document.enterprise_id=version.enterprise_id "
                    "AND document.id=version.source_document_id "
                    "WHERE version.enterprise_id=:enterprise_id "
                    "AND version.id=:version_id "
                    "AND document.content_type='application/pdf' "
                    "AND task.pipeline_kind='controlled_ingestion' "
                    "AND task.content_sha256=:source_sha256 "
                    "AND task.preview_unit_count=:page_count "
                    "AND task.status='done' AND task.processing_stage='ready' "
                    "AND task.object_state='ready' AND task.scan_verdict='clean' "
                    "AND task.preview_status='ready'"
                ),
                {
                    "enterprise_id": tenant.enterprise_id,
                    "version_id": document_version_id,
                    "source_sha256": source_sha256,
                    "page_count": expected_page_count,
                },
            )
        ).first()
        if source is None:
            raise RuntimeError("MATERIAL_ANALYSIS_SOURCE_NOT_READY")
        await _purge_expired_ocr_checkpoints(session, tenant)
        await session.execute(
            text(
                "DELETE FROM f1.material_ocr_checkpoint "
                "WHERE enterprise_id=:enterprise_id "
                "AND document_version_id=:version_id AND ("
                "expires_at<=statement_timestamp() "
                "OR source_sha256<>:source_sha256 "
                "OR expected_page_count<>:page_count "
                "OR parser_backend<>:parser_backend)"
            ),
            {
                "enterprise_id": tenant.enterprise_id,
                "version_id": document_version_id,
                "source_sha256": source_sha256,
                "page_count": expected_page_count,
                "parser_backend": parser_backend,
            },
        )
        rows = (
            await session.execute(
                text(
                    "SELECT page_number,source_unit_id,body_ciphertext,"
                    "body_sha256,body_aad_sha256,character_count,"
                    "confidence_mean_ppm,table_candidate,two_column_candidate "
                    "FROM f1.material_ocr_checkpoint "
                    "WHERE enterprise_id=:enterprise_id "
                    "AND document_version_id=:version_id "
                    "AND source_sha256=:source_sha256 "
                    "AND expected_page_count=:page_count "
                    "AND parser_backend=:parser_backend "
                    "AND expires_at>statement_timestamp() "
                    "ORDER BY page_number"
                ),
                {
                    "enterprise_id": tenant.enterprise_id,
                    "version_id": document_version_id,
                    "source_sha256": source_sha256,
                    "page_count": expected_page_count,
                    "parser_backend": parser_backend,
                },
            )
        ).mappings().all()
        if (
            len(rows) > expected_page_count
            or sum(len(bytes(row["body_ciphertext"])) for row in rows)
            > _MAX_OCR_CHECKPOINT_TOTAL_BYTES
        ):
            raise RuntimeError("MATERIAL_OCR_CHECKPOINT_SIZE_LIMIT")
        results: list[OcrPageResult] = []
        for row in rows:
            page_number = int(row["page_number"])
            source_unit_id = str(row["source_unit_id"])
            body_sha256 = str(row["body_sha256"])
            stored_character_count = int(row["character_count"])
            stored_confidence = (
                int(row["confidence_mean_ppm"])
                if row["confidence_mean_ppm"] is not None
                else None
            )
            table_candidate = bool(row["table_candidate"])
            two_column_candidate = bool(row["two_column_candidate"])
            aad = ocr_checkpoint_aad(
                enterprise_id=tenant.enterprise_id,
                document_version_id=document_version_id,
                source_sha256=source_sha256,
                expected_page_count=expected_page_count,
                page_number=page_number,
                parser_backend=parser_backend,
                source_unit_id=source_unit_id,
                body_sha256=body_sha256,
                character_count=stored_character_count,
                confidence_mean_ppm=stored_confidence,
                table_candidate=table_candidate,
                two_column_candidate=two_column_candidate,
            )
            try:
                body = decrypt_text(
                    bytes(row["body_ciphertext"]),
                    aad,
                    str(row["body_aad_sha256"]),
                )
                encoded = body.encode("utf-8")
            except Exception:
                raise RuntimeError("MATERIAL_OCR_CHECKPOINT_INVALID") from None
            character_count = sum(not character.isspace() for character in body)
            if (
                not 1 <= len(encoded) <= MAX_OCR_CHECKPOINT_TEXT_BYTES
                or len(body) > MAX_OCR_PAGE_TEXT_CHARACTERS
                or hashlib.sha256(encoded).hexdigest() != body_sha256
                or character_count != stored_character_count
                or not 40 <= character_count <= MAX_OCR_PAGE_TEXT_CHARACTERS
            ):
                raise RuntimeError("MATERIAL_OCR_CHECKPOINT_INVALID")
            results.append(
                OcrPageResult(
                    page_number=page_number,
                    text=body,
                    status="applied",
                    reason_code="OCR_APPLIED",
                    ocr_applied=True,
                    parser_backend=parser_backend,
                    character_count=character_count,
                    confidence_mean_ppm=stored_confidence,
                    table_candidate=table_candidate,
                    two_column_candidate=two_column_candidate,
                    source_unit_id=source_unit_id,
                )
            )
        await session.commit()
    return tuple(results)


async def persist_ocr_checkpoint(
    tenant: Tenant,
    *,
    document_version_id: uuid.UUID,
    source_sha256: str,
    expected_page_count: int,
    result: OcrPageResult,
) -> Literal["created", "unchanged"]:
    """Encrypt and append one successful OCR page; existing rows are immutable."""

    _require_viewer(tenant)
    value, encoded, body_sha256 = _checkpoint_body(result)
    source_unit_id = str(result.source_unit_id)
    parser_backend = result.parser_backend
    aad = ocr_checkpoint_aad(
        enterprise_id=tenant.enterprise_id,
        document_version_id=document_version_id,
        source_sha256=source_sha256,
        expected_page_count=expected_page_count,
        page_number=result.page_number,
        parser_backend=parser_backend,
        source_unit_id=source_unit_id,
        body_sha256=body_sha256,
        character_count=result.character_count,
        confidence_mean_ppm=result.confidence_mean_ppm,
        table_candidate=result.table_candidate,
        two_column_candidate=result.two_column_candidate,
    )
    ciphertext, aad_sha256 = encrypt_text(value, aad)
    if len(ciphertext) > MAX_OCR_CHECKPOINT_TEXT_BYTES + 33:
        raise RuntimeError("MATERIAL_OCR_CHECKPOINT_SIZE_LIMIT")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        source = (
            await session.execute(
                text(
                    "SELECT task.id FROM f1.document_version AS version "
                    "JOIN f1.upload_task AS task ON "
                    "task.enterprise_id=version.enterprise_id "
                    "AND task.id=version.upload_task_id "
                    "JOIN f1.document AS document ON "
                    "document.enterprise_id=version.enterprise_id "
                    "AND document.id=version.source_document_id "
                    "WHERE version.enterprise_id=:enterprise_id "
                    "AND version.id=:version_id "
                    "AND document.content_type='application/pdf' "
                    "AND task.pipeline_kind='controlled_ingestion' "
                    "AND task.content_sha256=:source_sha256 "
                    "AND task.preview_unit_count=:page_count "
                    "AND task.status='done' AND task.processing_stage='ready' "
                    "AND task.object_state='ready' AND task.scan_verdict='clean' "
                    "AND task.preview_status='ready' FOR UPDATE OF task"
                ),
                {
                    "enterprise_id": tenant.enterprise_id,
                    "version_id": document_version_id,
                    "source_sha256": source_sha256,
                    "page_count": expected_page_count,
                },
            )
        ).first()
        if source is None:
            raise RuntimeError("MATERIAL_ANALYSIS_SOURCE_NOT_READY")
        await _purge_expired_ocr_checkpoints(session, tenant)
        await session.execute(
            text(
                "DELETE FROM f1.material_ocr_checkpoint "
                "WHERE enterprise_id=:enterprise_id "
                "AND document_version_id=:version_id AND ("
                "expires_at<=statement_timestamp() "
                "OR source_sha256<>:source_sha256 "
                "OR expected_page_count<>:page_count "
                "OR parser_backend<>:parser_backend)"
            ),
            {
                "enterprise_id": tenant.enterprise_id,
                "version_id": document_version_id,
                "source_sha256": source_sha256,
                "page_count": expected_page_count,
                "parser_backend": parser_backend,
            },
        )
        existing = (
            await session.execute(
                text(
                    "SELECT source_unit_id,body_sha256,body_aad_sha256,"
                    "character_count,confidence_mean_ppm,table_candidate,"
                    "two_column_candidate FROM f1.material_ocr_checkpoint "
                    "WHERE enterprise_id=:enterprise_id "
                    "AND document_version_id=:version_id "
                    "AND source_sha256=:source_sha256 "
                    "AND expected_page_count=:page_count "
                    "AND page_number=:page_number "
                    "AND parser_backend=:parser_backend"
                ),
                {
                    "enterprise_id": tenant.enterprise_id,
                    "version_id": document_version_id,
                    "source_sha256": source_sha256,
                    "page_count": expected_page_count,
                    "page_number": result.page_number,
                    "parser_backend": parser_backend,
                },
            )
        ).mappings().one_or_none()
        if existing is not None:
            if (
                str(existing["source_unit_id"]) != source_unit_id
                or str(existing["body_sha256"]) != body_sha256
                or str(existing["body_aad_sha256"]) != aad_sha256
                or int(existing["character_count"]) != result.character_count
                or existing["confidence_mean_ppm"] != result.confidence_mean_ppm
                or bool(existing["table_candidate"]) != result.table_candidate
                or bool(existing["two_column_candidate"])
                != result.two_column_candidate
            ):
                raise RuntimeError("MATERIAL_OCR_CHECKPOINT_CONFLICT")
            await session.commit()
            return "unchanged"
        total = int(
            (
                await session.execute(
                    text(
                        "SELECT COALESCE(sum(octet_length(body_ciphertext)),0) "
                        "FROM f1.material_ocr_checkpoint "
                        "WHERE enterprise_id=:enterprise_id "
                        "AND document_version_id=:version_id "
                        "AND source_sha256=:source_sha256 "
                        "AND expected_page_count=:page_count "
                        "AND parser_backend=:parser_backend"
                    ),
                    {
                        "enterprise_id": tenant.enterprise_id,
                        "version_id": document_version_id,
                        "source_sha256": source_sha256,
                        "page_count": expected_page_count,
                        "parser_backend": parser_backend,
                    },
                )
            ).scalar_one()
        )
        if total + len(ciphertext) > _MAX_OCR_CHECKPOINT_TOTAL_BYTES:
            raise RuntimeError("MATERIAL_OCR_CHECKPOINT_SIZE_LIMIT")
        inserted = (
            await session.execute(
                text(
                    "INSERT INTO f1.material_ocr_checkpoint ("
                    "id,enterprise_id,document_version_id,source_sha256,"
                    "expected_page_count,page_number,parser_backend,source_unit_id,"
                    "body_ciphertext,body_sha256,body_aad_sha256,character_count,"
                    "confidence_mean_ppm,table_candidate,two_column_candidate,"
                    "expires_at) VALUES ("
                    ":id,:enterprise_id,:version_id,:source_sha256,:page_count,"
                    ":page_number,:parser_backend,:source_unit_id,:ciphertext,"
                    ":body_sha256,:aad_sha256,:character_count,:confidence,"
                    ":table_candidate,:two_column_candidate,"
                    "statement_timestamp()+interval '24 hours') "
                    "ON CONFLICT (enterprise_id,document_version_id,source_sha256,"
                    "page_number,parser_backend) DO NOTHING RETURNING id"
                ),
                {
                    "id": uuid.uuid4(),
                    "enterprise_id": tenant.enterprise_id,
                    "version_id": document_version_id,
                    "source_sha256": source_sha256,
                    "page_count": expected_page_count,
                    "page_number": result.page_number,
                    "parser_backend": parser_backend,
                    "source_unit_id": source_unit_id,
                    "ciphertext": ciphertext,
                    "body_sha256": body_sha256,
                    "aad_sha256": aad_sha256,
                    "character_count": result.character_count,
                    "confidence": result.confidence_mean_ppm,
                    "table_candidate": result.table_candidate,
                    "two_column_candidate": result.two_column_candidate,
                },
            )
        ).first()
        if inserted is None:
            raise RuntimeError("MATERIAL_OCR_CHECKPOINT_CONFLICT")
        await session.commit()
    # Drop the extra immutable plaintext byte copy before returning.  Python
    # strings cannot be reliably zeroed, so callers must retain body-free logs.
    del encoded
    return "created"


async def clear_ocr_checkpoints(
    tenant: Tenant,
    *,
    document_version_id: uuid.UUID,
    source_sha256: str,
) -> None:
    """Remove temporary page bodies after their analysis snapshot commits."""

    _require_viewer(tenant)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await session.execute(
            text(
                "DELETE FROM f1.material_ocr_checkpoint "
                "WHERE enterprise_id=:enterprise_id "
                "AND document_version_id=:version_id "
                "AND source_sha256=:source_sha256"
            ),
            {
                "enterprise_id": tenant.enterprise_id,
                "version_id": document_version_id,
                "source_sha256": source_sha256,
            },
        )
        await session.commit()


async def persist_material_analysis(
    tenant: Tenant,
    *,
    document_version_id: uuid.UUID,
    source_sha256: str,
    page_count: int,
    result: MaterialAnalysisResult | None,
    reason_code: str | None = None,
) -> Literal["created", "superseded", "unchanged"]:
    """Insert an immutable snapshot or an allowed recovery successor."""
    _require_viewer(tenant)
    if result is None and not reason_code:
        raise ValueError("MATERIAL_ANALYSIS_OUTCOME_REQUIRED")
    if result is not None and reason_code is not None:
        raise ValueError("MATERIAL_ANALYSIS_OUTCOME_CONFLICT")
    analysis_id = uuid.uuid4()
    try:
        async with session_scope(
            role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
        ) as session:
            source = (
                await session.execute(
                    text(
                        "SELECT task.content_sha256,source.content_type,"
                        "task.object_state,task.scan_verdict,task.preview_status "
                        ",record.declared_material_kind,"
                        "record.created_by_user_id AS document_creator_id,"
                        "record.created_at AS document_created_at "
                        "FROM f1.document_version AS version "
                        "JOIN f1.document_record AS record ON "
                        "record.enterprise_id=version.enterprise_id "
                        "AND record.id=version.document_record_id "
                        "JOIN f1.upload_task AS task ON "
                        "task.enterprise_id=version.enterprise_id "
                        "AND task.id=version.upload_task_id "
                        "JOIN f1.document AS source ON "
                        "source.enterprise_id=version.enterprise_id "
                        "AND source.id=version.source_document_id "
                        "WHERE version.enterprise_id=:enterprise_id "
                        "AND version.id=:version_id "
                        "AND task.pipeline_kind='controlled_ingestion' "
                        "FOR UPDATE OF task"
                    ),
                    {
                        "enterprise_id": tenant.enterprise_id,
                        "version_id": document_version_id,
                    },
                )
            ).mappings().one_or_none()
            if source is None:
                raise RuntimeError("MATERIAL_ANALYSIS_SOURCE_NOT_READY")
            if (
                str(source["content_type"]) != "application/pdf"
                or str(source["content_sha256"]) != source_sha256
                or str(source["object_state"]) != "ready"
                or str(source["scan_verdict"]) != "clean"
                or str(source["preview_status"]) != "ready"
            ):
                raise RuntimeError("MATERIAL_ANALYSIS_SOURCE_NOT_READY")
            revisions_enabled = material_analysis_recovery_enabled()
            revision_select = (
                ",analysis.analysis_revision" if revisions_enabled else ""
            )
            revision_order = (
                " ORDER BY analysis.analysis_revision DESC,analysis.id DESC LIMIT 1"
                if revisions_enabled
                else ""
            )
            existing = (
                await session.execute(
                    text(
                        "SELECT analysis.id,analysis.source_sha256,"
                        "analysis.status,analysis.parser_backend,"
                        "EXISTS (SELECT 1 "
                        "FROM f1.material_page_classification AS page "
                        "WHERE page.enterprise_id=analysis.enterprise_id "
                        "AND page.analysis_id=analysis.id "
                        "AND page.ocr_required IS TRUE) AS ocr_retry_required"
                        + revision_select
                        + " FROM f1.material_analysis AS analysis "
                        "WHERE analysis.enterprise_id=:enterprise_id "
                        "AND analysis.document_version_id=:version_id "
                        "AND analysis.analysis_version=:analysis_version "
                        + revision_order
                        + " "
                        "FOR UPDATE"
                    ),
                    {
                        "enterprise_id": tenant.enterprise_id,
                        "version_id": document_version_id,
                        "analysis_version": MATERIAL_ANALYSIS_VERSION,
                    },
                )
            ).mappings().first()
            analysis_revision = 1
            supersedes_analysis_id = None
            outcome: Literal["created", "superseded"] = "created"
            if existing is not None:
                if (
                    str(existing["source_sha256"]) != source_sha256
                    or str(existing["parser_backend"]) != "pypdf_heuristic"
                ):
                    raise RuntimeError("MATERIAL_ANALYSIS_SOURCE_IDENTITY_MISMATCH")
                existing_status = str(existing["status"])
                ocr_retry_required = bool(existing["ocr_retry_required"])
                if existing_status == "confirmed" and ocr_retry_required:
                    raise RuntimeError(
                        "MATERIAL_ANALYSIS_CONFIRMED_OCR_REVIEW_REQUIRED"
                    )
                if existing_status in {"ready", "confirmed"} and not (
                    revisions_enabled
                    and existing_status == "ready"
                    and ocr_retry_required
                ):
                    await _register_auto_pipeline_delivery_if_enabled(
                        session,
                        tenant,
                        document_version_id,
                    )
                    await session.commit()
                    return "unchanged"
                if not revisions_enabled:
                    # The legacy f1_0014 schema has one immutable row per
                    # version and intentionally cannot append a successor.
                    return "unchanged"
                if existing_status not in {"failed", "ready"}:
                    raise RuntimeError("MATERIAL_ANALYSIS_REVISION_INVALID")
                analysis_revision = int(existing["analysis_revision"]) + 1
                if analysis_revision > 100:
                    raise RuntimeError("MATERIAL_ANALYSIS_REVISION_LIMIT")
                supersedes_analysis_id = existing["id"]
                outcome = "superseded"

            status = "ready" if result is not None else "failed"
            profile = result.document_profile if result is not None else "unknown"
            candidate_count = len(result.candidates) if result is not None else 0
            suggested_kind = result.suggested_kind if result is not None else "unknown"
            suggested_kind_confidence_ppm = (
                result.suggested_kind_confidence_ppm if result is not None else 0
            )
            declared_kind = str(source["declared_material_kind"])
            if declared_kind in {"policy", "report"}:
                resolved_kind = declared_kind
                classification_source = "upload_selection"
                classification_by_user_id = source["document_creator_id"]
                classification_at = source["document_created_at"]
            else:
                resolved_kind = "unknown"
                classification_source = "machine_pending"
                classification_by_user_id = None
                classification_at = None
            values = {
                "id": analysis_id,
                "enterprise_id": tenant.enterprise_id,
                "document_version_id": document_version_id,
                "source_sha256": source_sha256,
                "analysis_version": MATERIAL_ANALYSIS_VERSION,
                "analysis_revision": analysis_revision,
                "supersedes_analysis_id": supersedes_analysis_id,
                "status": status,
                "profile": profile,
                "reason_code": reason_code,
                "suggested_kind": suggested_kind,
                "suggested_kind_confidence_ppm": suggested_kind_confidence_ppm,
                "resolved_kind": resolved_kind,
                "classification_source": classification_source,
                "classification_by_user_id": classification_by_user_id,
                "classification_at": classification_at,
                "page_count": page_count,
                "candidate_count": candidate_count,
            }
            revision_insert_columns = (
                "analysis_revision,supersedes_analysis_id,"
                if revisions_enabled
                else ""
            )
            revision_insert_values = (
                ":analysis_revision,:supersedes_analysis_id,"
                if revisions_enabled
                else ""
            )
            await session.execute(
                text(
                    "INSERT INTO f1.material_analysis ("
                    "id,enterprise_id,document_version_id,source_sha256,"
                    "analysis_version,"
                    + revision_insert_columns
                    + "parser_backend,status,document_profile,"
                    "shadow_status,reason_code,suggested_kind,"
                    "suggested_kind_confidence_ppm,resolved_kind,"
                    "classification_source,classification_by_user_id,"
                    "classification_at,page_count,candidate_count) VALUES ("
                    ":id,:enterprise_id,:document_version_id,:source_sha256,"
                    ":analysis_version,"
                    + revision_insert_values
                    + "'pypdf_heuristic',:status,:profile,"
                    "'disabled',:reason_code,:suggested_kind,"
                    ":suggested_kind_confidence_ppm,:resolved_kind,"
                    ":classification_source,:classification_by_user_id,"
                    ":classification_at,:page_count,:candidate_count)"
                ),
                values,
            )
            if result is not None:
                for page in result.pages:
                    await session.execute(
                        text(
                            "INSERT INTO f1.material_page_classification ("
                            "id,enterprise_id,analysis_id,page_number,primary_kind,"
                            "ocr_required,table_candidate,two_column_candidate,"
                            "text_character_count,text_confidence_ppm,"
                            "scan_confidence_ppm,table_confidence_ppm,"
                            "two_column_confidence_ppm,reason_codes) VALUES ("
                            ":id,:enterprise_id,:analysis_id,:page_number,:primary_kind,"
                            ":ocr_required,:table_candidate,:two_column_candidate,"
                            ":text_character_count,:text_confidence_ppm,"
                            ":scan_confidence_ppm,:table_confidence_ppm,"
                            ":two_column_confidence_ppm,CAST(:reason_codes AS jsonb))"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "enterprise_id": tenant.enterprise_id,
                            "analysis_id": analysis_id,
                            "page_number": page.page_number,
                            "primary_kind": page.primary_kind,
                            "ocr_required": page.ocr_required,
                            "table_candidate": page.table_candidate,
                            "two_column_candidate": page.two_column_candidate,
                            "text_character_count": page.text_character_count,
                            "text_confidence_ppm": page.text_confidence_ppm,
                            "scan_confidence_ppm": page.scan_confidence_ppm,
                            "table_confidence_ppm": page.table_confidence_ppm,
                            "two_column_confidence_ppm": page.two_column_confidence_ppm,
                            "reason_codes": json.dumps(
                                list(page.reason_codes), separators=(",", ":")
                            ),
                        },
                    )
                for candidate in result.candidates:
                    await session.execute(
                        text(
                            "INSERT INTO f1.material_field_candidate ("
                            "id,enterprise_id,analysis_id,field_name,candidate_value,"
                            "page_number,evidence_snippet,confidence_ppm,"
                            "confidence_basis,calibrated,producer) VALUES ("
                            ":id,:enterprise_id,:analysis_id,:field_name,"
                            ":candidate_value,:page_number,:evidence_snippet,"
                            ":confidence_ppm,:confidence_basis,false,:producer)"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "enterprise_id": tenant.enterprise_id,
                            "analysis_id": analysis_id,
                            "field_name": candidate.field_name,
                            "candidate_value": candidate.candidate_value,
                            "page_number": candidate.page_number,
                            "evidence_snippet": candidate.evidence_snippet,
                            "confidence_ppm": candidate.confidence_ppm,
                            "confidence_basis": candidate.confidence_basis,
                            "producer": candidate.producer,
                        },
                    )
            await session.execute(
                text(
                    "INSERT INTO f1.audit_log "
                    "(id,enterprise_id,user_sub,action,resource_type,resource_id,result) "
                    "VALUES (:id,:enterprise_id,:sub,:action,"
                    "'material_analysis',:resource_id,:result)"
                ),
                {
                    "id": uuid.uuid4(),
                    "enterprise_id": tenant.enterprise_id,
                    "sub": tenant.sub,
                    "resource_id": str(analysis_id),
                    "result": status,
                    "action": (
                        "material.analysis.retried"
                        if outcome == "superseded"
                        else "material.analysis.created"
                    ),
                },
            )
            if result is not None:
                await _register_auto_pipeline_delivery_if_enabled(
                    session,
                    tenant,
                    document_version_id,
                )
            await session.commit()
            return outcome
    except IntegrityError:
        # The upload-task row lock serializes same-version writes.  Any
        # remaining integrity failure is not a successful idempotent replay
        # and must stay observable to the caller.
        raise RuntimeError("MATERIAL_ANALYSIS_PERSIST_CONFLICT") from None


async def _analysis_row(
    session: AsyncSession,
    *,
    analysis_id: uuid.UUID | None = None,
    document_version_id: uuid.UUID | None = None,
    lock: bool = False,
) -> Mapping[str, Any]:
    if (analysis_id is None) == (document_version_id is None):
        raise ValueError("MATERIAL_ANALYSIS_LOOKUP_INVALID")
    predicate = "analysis.id=:lookup_id" if analysis_id else "analysis.document_version_id=:lookup_id"
    order = (
        " ORDER BY analysis.analysis_revision DESC,analysis.id DESC LIMIT 1"
        if document_version_id is not None and material_analysis_recovery_enabled()
        else ""
    )
    suffix = " FOR UPDATE OF analysis" if lock else ""
    row = (
        await session.execute(
            text(
                f"SELECT {_analysis_columns()},"
                "task.quarantine_status,task.object_state,task.scan_verdict,"
                "task.preview_status,task.content_sha256 AS current_source_sha256 "
                ",scope.id AS knowledge_scope_id,"
                "scope.scope_kind AS knowledge_scope_kind,"
                "scope.client_account_id,account.display_name AS client_display_name "
                "FROM f1.material_analysis AS analysis "
                "JOIN f1.document_version AS version ON "
                "version.enterprise_id=analysis.enterprise_id "
                "AND version.id=analysis.document_version_id "
                "JOIN f1.document_record AS record ON "
                "record.enterprise_id=version.enterprise_id "
                "AND record.id=version.document_record_id "
                "JOIN f1.material_knowledge_scope AS scope ON "
                "scope.enterprise_id=record.enterprise_id "
                "AND scope.id=record.knowledge_scope_id "
                "LEFT JOIN f1.crm_account AS account ON "
                "account.enterprise_id=scope.enterprise_id "
                "AND account.id=scope.client_account_id "
                "JOIN f1.upload_task AS task ON "
                "task.enterprise_id=version.enterprise_id "
                "AND task.id=version.upload_task_id "
                f"WHERE {predicate} AND analysis.analysis_version=:analysis_version"
                + order
                + suffix
            ),
            {
                "lookup_id": analysis_id or document_version_id,
                "analysis_version": MATERIAL_ANALYSIS_VERSION,
            },
        )
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="MATERIAL_ANALYSIS_NOT_FOUND")
    return row


async def material_analysis_payload(
    session: AsyncSession,
    tenant: Tenant,
    row: Mapping[str, Any],
) -> dict[str, Any]:
    pages = (
        await session.execute(
            text(
                f"SELECT {_PAGE_COLUMNS} FROM f1.material_page_classification "
                "WHERE analysis_id=:analysis_id ORDER BY page_number"
            ),
            {"analysis_id": row["id"]},
        )
    ).mappings().all()
    candidates = (
        await session.execute(
            text(
                f"SELECT {_CANDIDATE_COLUMNS} FROM f1.material_field_candidate "
                "WHERE analysis_id=:analysis_id ORDER BY field_name,page_number,id"
            ),
            {"analysis_id": row["id"]},
        )
    ).mappings().all()
    released = (
        str(row["quarantine_status"]) == "released"
        and str(row["object_state"]) == "ready"
        and str(row["scan_verdict"]) == "clean"
        and str(row["preview_status"]) == "ready"
    )
    payload = {
        key: row[key]
        for key in (
            "id",
            "document_version_id",
            "source_sha256",
            "analysis_version",
            "parser_backend",
            "document_profile",
            "status",
            "reason_code",
            "shadow_status",
            "suggested_kind",
            "suggested_kind_confidence_ppm",
            "resolved_kind",
            "classification_source",
            "classification_by_user_id",
            "classification_at",
            "page_count",
            "candidate_count",
            "policy_source_id",
            "policy_version_id",
            "confirmed_at",
            "created_at",
            "updated_at",
        )
    }
    payload["pages"] = [dict(item) for item in pages]
    payload["candidates"] = [dict(item) for item in candidates]
    payload["knowledge_scope"] = {
        "id": row["knowledge_scope_id"],
        "kind": str(row["knowledge_scope_kind"]),
        "client_account_id": row.get("client_account_id"),
        "client_display_name": row.get("client_display_name"),
    }
    payload["allowed_actions"] = material_allowed_actions(
        role=tenant.role,
        status=str(row["status"]),
        document_released=released,
        source_id=row.get("policy_source_id"),
        version_id=row.get("policy_version_id"),
        resolved_kind=str(row["resolved_kind"]),
        classification_source=str(row["classification_source"]),
        classification_by_user_id=row.get("classification_by_user_id"),
        classification_at=row.get("classification_at"),
        knowledge_scope_kind=str(row["knowledge_scope_kind"]),
    )
    payload["boundaries"] = list(MATERIAL_INTAKE_BOUNDARIES)
    return payload


async def get_material_analysis(
    tenant: Tenant, document_version_id: uuid.UUID
) -> MaterialAnalysisOut:
    _require_viewer(tenant)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        row = await _analysis_row(
            session, document_version_id=document_version_id
        )
        payload = await material_analysis_payload(session, tenant, row)
    return MaterialAnalysisOut.model_validate(payload)


async def set_material_kind(
    tenant: Tenant,
    analysis_id: uuid.UUID,
    *,
    kind: str,
) -> MaterialAnalysisOut:
    """Persist an explicit manager choice without creating a business record."""
    _require_viewer(tenant)
    if kind not in {"policy", "report", "unknown"}:
        raise HTTPException(status_code=422, detail="MATERIAL_KIND_INVALID")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        row = await _analysis_row(session, analysis_id=analysis_id, lock=True)
        if (
            str(row["status"]) != "ready"
            or row.get("policy_source_id") is not None
            or row.get("policy_version_id") is not None
        ):
            raise HTTPException(
                status_code=409, detail="MATERIAL_CLASSIFICATION_NOT_EDITABLE"
            )
        actor_id = (
            await session.execute(
                text(
                    "SELECT membership.user_id FROM f1.enterprise_user AS membership "
                    "JOIN f1.user_profile AS profile "
                    "ON profile.id=membership.user_id "
                    "WHERE membership.enterprise_id=:enterprise_id "
                    "AND profile.keycloak_sub=:sub"
                ),
                {"enterprise_id": tenant.enterprise_id, "sub": tenant.sub},
            )
        ).scalar_one_or_none()
        if actor_id is None:
            raise HTTPException(status_code=404, detail="MATERIAL_ACTOR_NOT_FOUND")
        updated = (
            await session.execute(
                text(
                    "UPDATE f1.material_analysis SET resolved_kind=:kind,"
                    "classification_source='human_review',"
                    "classification_by_user_id=:actor_id,"
                    "classification_at=statement_timestamp(),"
                    "updated_at=statement_timestamp() "
                    "WHERE id=:analysis_id AND status='ready' "
                    "AND policy_source_id IS NULL AND policy_version_id IS NULL "
                    "RETURNING id"
                ),
                {
                    "kind": kind,
                    "actor_id": actor_id,
                    "analysis_id": row["id"],
                },
            )
        ).first()
        if updated is None:
            raise HTTPException(
                status_code=409, detail="MATERIAL_CLASSIFICATION_NOT_EDITABLE"
            )
        await session.execute(
            text(
                "INSERT INTO f1.audit_log "
                "(id,enterprise_id,user_sub,action,resource_type,resource_id,result) "
                "VALUES (:id,:enterprise_id,:sub,'material.classification.updated',"
                "'material_analysis',:resource_id,'updated')"
            ),
            {
                "id": uuid.uuid4(),
                "enterprise_id": tenant.enterprise_id,
                "sub": tenant.sub,
                "resource_id": str(row["id"]),
            },
        )
        await session.commit()

    return await get_material_analysis(tenant, row["document_version_id"])


__all__ = (
    "_analysis_row",
    "clear_ocr_checkpoints",
    "get_material_analysis",
    "load_ocr_checkpoints",
    "material_analysis_payload",
    "material_analysis_recovery_enabled",
    "persist_material_analysis",
    "persist_ocr_checkpoint",
    "set_material_kind",
)
