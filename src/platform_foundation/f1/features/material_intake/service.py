"""Persistence and read service for vendor-neutral material analysis."""
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import Tenant
from ...database import session_scope
from .contracts import (
    MATERIAL_ANALYSIS_VERSION,
    MATERIAL_INTAKE_BOUNDARIES,
    MaterialAnalysisOut,
    MaterialAnalysisResult,
    material_allowed_actions,
)


_MANAGER_ROLES = frozenset(("super_admin", "enterprise_admin", "plant_admin"))
_ANALYSIS_COLUMNS = (
    "analysis.id,analysis.document_version_id,analysis.source_sha256,"
    "analysis.analysis_version,analysis.parser_backend,analysis.status,"
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


async def persist_material_analysis(
    tenant: Tenant,
    *,
    document_version_id: uuid.UUID,
    source_sha256: str,
    page_count: int,
    result: MaterialAnalysisResult | None,
    reason_code: str | None = None,
) -> None:
    """Insert one immutable analysis snapshot; exact retries are no-ops."""
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
                        "AND task.pipeline_kind='controlled_ingestion'"
                    ),
                    {
                        "enterprise_id": tenant.enterprise_id,
                        "version_id": document_version_id,
                    },
                )
            ).mappings().one_or_none()
            if source is None:
                return
            if (
                str(source["content_type"]) != "application/pdf"
                or str(source["content_sha256"]) != source_sha256
                or str(source["object_state"]) != "ready"
                or str(source["scan_verdict"]) != "clean"
                or str(source["preview_status"]) != "ready"
            ):
                return
            existing = (
                await session.execute(
                    text(
                        "SELECT id,source_sha256 FROM f1.material_analysis "
                        "WHERE document_version_id=:version_id "
                        "AND analysis_version=:analysis_version"
                    ),
                    {
                        "version_id": document_version_id,
                        "analysis_version": MATERIAL_ANALYSIS_VERSION,
                    },
                )
            ).first()
            if existing is not None:
                # The unique version snapshot is immutable; a source mismatch is
                # intentionally not overwritten by a later request.
                return

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
            await session.execute(
                text(
                    "INSERT INTO f1.material_analysis ("
                    "id,enterprise_id,document_version_id,source_sha256,"
                    "analysis_version,parser_backend,status,document_profile,"
                    "shadow_status,reason_code,suggested_kind,"
                    "suggested_kind_confidence_ppm,resolved_kind,"
                    "classification_source,classification_by_user_id,"
                    "classification_at,page_count,candidate_count) VALUES ("
                    ":id,:enterprise_id,:document_version_id,:source_sha256,"
                    ":analysis_version,'pypdf_heuristic',:status,:profile,"
                    "'disabled',:reason_code,:suggested_kind,"
                    ":suggested_kind_confidence_ppm,:resolved_kind,"
                    ":classification_source,:classification_by_user_id,"
                    ":classification_at,:page_count,:candidate_count)"
                ),
                {
                    "id": analysis_id,
                    "enterprise_id": tenant.enterprise_id,
                    "document_version_id": document_version_id,
                    "source_sha256": source_sha256,
                    "analysis_version": MATERIAL_ANALYSIS_VERSION,
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
                },
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
                    "VALUES (:id,:enterprise_id,:sub,'material.analysis.created',"
                    "'material_analysis',:resource_id,:result)"
                ),
                {
                    "id": uuid.uuid4(),
                    "enterprise_id": tenant.enterprise_id,
                    "sub": tenant.sub,
                    "resource_id": str(analysis_id),
                    "result": status,
                },
            )
            await session.commit()
    except IntegrityError:
        # A concurrent exact process may have inserted the immutable snapshot.
        return


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
    suffix = " FOR UPDATE OF analysis" if lock else ""
    row = (
        await session.execute(
            text(
                f"SELECT {_ANALYSIS_COLUMNS},"
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
    "get_material_analysis",
    "material_analysis_payload",
    "persist_material_analysis",
    "set_material_kind",
)
