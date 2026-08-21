"""Atomic human confirmation into a P5 source and draft version."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text

from ...audit import add_event
from ...auth import Tenant
from ...database import session_scope
from ..p5 import catalog
from ..p5.common import current_actor_id
from .contracts import ConfirmPolicyDraftIn
from .service import _analysis_row, material_analysis_payload


_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def confirmation_key_sha256(value: str | None) -> str:
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail="MATERIAL_IDEMPOTENCY_KEY_REQUIRED")
    normalized = value.strip()
    if not 8 <= len(normalized) <= 128 or _CONTROL_RE.search(normalized):
        raise HTTPException(status_code=400, detail="MATERIAL_IDEMPOTENCY_KEY_INVALID")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def confirmation_payload_sha256(body: ConfirmPolicyDraftIn) -> str:
    encoded = json.dumps(
        body.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def confirm_policy_draft(
    tenant: Tenant,
    analysis_id: uuid.UUID,
    *,
    body: ConfirmPolicyDraftIn,
    idempotency_key: str | None,
) -> dict[str, Any]:
    if tenant.role not in {"super_admin", "enterprise_admin"}:
        raise HTTPException(status_code=403, detail="POLICY_MANAGER_REQUIRED")
    if (
        body.version.effective_from is not None
        and body.version.effective_to is not None
        and body.version.effective_to < body.version.effective_from
    ):
        raise HTTPException(status_code=422, detail="POLICY_EFFECTIVE_RANGE_INVALID")
    key_sha256 = confirmation_key_sha256(idempotency_key)
    payload_sha256 = confirmation_payload_sha256(body)

    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        analysis = await _analysis_row(session, analysis_id=analysis_id, lock=True)
        if str(analysis["current_source_sha256"]) != str(analysis["source_sha256"]):
            raise HTTPException(
                status_code=409, detail="MATERIAL_SOURCE_IDENTITY_MISMATCH"
            )
        if str(analysis["status"]) == "confirmed":
            stored = (
                await session.execute(
                    text(
                        "SELECT confirmation_key_sha256,confirmation_payload_sha256 "
                        "FROM f1.material_analysis WHERE id=:analysis_id"
                    ),
                    {"analysis_id": analysis_id},
                )
            ).first()
            if stored is None or stored[0] != key_sha256 or stored[1] != payload_sha256:
                raise HTTPException(status_code=409, detail="MATERIAL_CONFIRMATION_CONFLICT")
            source = await catalog.source_row(session, analysis["policy_source_id"])
            version = await catalog.version_row(session, analysis["policy_version_id"])
            material = await material_analysis_payload(session, tenant, analysis)
            return {
                "analysis": material,
                "source": catalog.source_out(source, tenant),
                "version": catalog.version_out(version, tenant, actor_id),
            }
        if str(analysis["status"]) != "ready":
            raise HTTPException(status_code=409, detail="MATERIAL_ANALYSIS_NOT_CONFIRMABLE")
        if (
            str(analysis["resolved_kind"]) != "policy"
            or str(analysis["knowledge_scope_kind"]) != "service_provider"
            or str(analysis["classification_source"])
            not in {"upload_selection", "human_review"}
            or analysis.get("classification_by_user_id") is None
            or analysis.get("classification_at") is None
        ):
            raise HTTPException(
                status_code=409, detail="MATERIAL_POLICY_CLASSIFICATION_REQUIRED"
            )
        if (
            str(analysis["current_source_sha256"])
            != str(analysis["source_sha256"])
            or str(analysis["quarantine_status"]) != "released"
            or str(analysis["object_state"]) != "ready"
            or str(analysis["scan_verdict"]) != "clean"
            or str(analysis["preview_status"]) != "ready"
        ):
            raise HTTPException(status_code=409, detail="MATERIAL_DOCUMENT_NOT_RELEASED")

        source = await catalog.insert_source_in_session(
            session,
            tenant,
            actor_id=actor_id,
            **body.source.model_dump(),
        )
        version = await catalog.insert_version_in_session(
            session,
            tenant,
            actor_id=actor_id,
            source_id=source["id"],
            document_version_id=analysis["document_version_id"],
            **body.version.model_dump(),
        )
        updated = (
            await session.execute(
                text(
                    "UPDATE f1.material_analysis SET status='confirmed',"
                    "confirmed_by_user_id=:actor_id,confirmed_at=statement_timestamp(),"
                    "policy_source_id=:source_id,policy_version_id=:version_id,"
                    "confirmation_key_sha256=:key_sha256,"
                    "confirmation_payload_sha256=:payload_sha256 "
                    "WHERE id=:analysis_id AND status='ready' "
                    "RETURNING id"
                ),
                {
                    "actor_id": actor_id,
                    "source_id": source["id"],
                    "version_id": version["id"],
                    "key_sha256": key_sha256,
                    "payload_sha256": payload_sha256,
                    "analysis_id": analysis_id,
                },
            )
        ).first()
        if updated is None:
            raise HTTPException(status_code=409, detail="MATERIAL_CONFIRMATION_CONFLICT")
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "policy.source.created",
            "policy_source",
            str(source["id"]),
        )
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "policy.version.created",
            "policy_version",
            str(version["id"]),
            "draft",
        )
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "material.analysis.confirmed",
            "material_analysis",
            str(analysis_id),
        )
        await session.commit()

    # Re-read through normal RLS after commit so the response reflects the
    # trigger-generated timestamp and server-derived allowed_actions.
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        analysis = await _analysis_row(session, analysis_id=analysis_id)
        source = await catalog.source_row(session, analysis["policy_source_id"])
        version = await catalog.version_row(session, analysis["policy_version_id"])
        material = await material_analysis_payload(session, tenant, analysis)
    return {
        "analysis": material,
        "source": catalog.source_out(source, tenant),
        "version": catalog.version_out(version, tenant, actor_id),
    }


__all__ = ("confirm_policy_draft",)
