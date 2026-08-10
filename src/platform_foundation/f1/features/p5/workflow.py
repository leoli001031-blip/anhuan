"""P5 internal review and publication-state transitions."""
from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import text

from ...audit import add_event
from ...auth import Tenant
from ...database import session_scope
from .catalog import VERSION_COLUMNS, get_version, version_row
from .common import current_actor_id
from .contracts import is_manager, is_reviewer


WorkflowAction = Literal["submit", "approve", "reject", "publish"]


async def transition_version(
    tenant: Tenant,
    version_id: uuid.UUID,
    *,
    action: WorkflowAction,
    comment: str | None,
) -> dict[str, Any]:
    if action == "reject" and not (comment or "").strip():
        raise HTTPException(status_code=422, detail="POLICY_REJECTION_COMMENT_REQUIRED")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        current = await version_row(session, version_id, lock=True)
        state = str(current["workflow_status"])

        if action == "submit":
            if not is_manager(tenant.role):
                raise HTTPException(status_code=403, detail="POLICY_SUBMIT_FORBIDDEN")
            if state not in ("draft", "rejected"):
                raise HTTPException(status_code=409, detail="POLICY_STATE_CONFLICT")
            update_sql = (
                "workflow_status = 'in_review', "
                "submitted_by_user_id = :actor_id, "
                "submitted_at = statement_timestamp(), "
                "approved_by_user_id = NULL, approved_at = NULL, "
                "published_by_user_id = NULL, published_at = NULL"
            )
            next_state = "in_review"
        elif action == "approve":
            if not is_reviewer(tenant.role):
                raise HTTPException(status_code=403, detail="POLICY_REVIEW_FORBIDDEN")
            if state != "in_review":
                raise HTTPException(status_code=409, detail="POLICY_STATE_CONFLICT")
            if current["submitted_by_user_id"] == actor_id:
                raise HTTPException(
                    status_code=403, detail="POLICY_SELF_APPROVAL_FORBIDDEN"
                )
            update_sql = (
                "workflow_status = 'approved', "
                "approved_by_user_id = :actor_id, "
                "approved_at = statement_timestamp()"
            )
            next_state = "approved"
        elif action == "reject":
            if not is_reviewer(tenant.role):
                raise HTTPException(status_code=403, detail="POLICY_REVIEW_FORBIDDEN")
            if state != "in_review":
                raise HTTPException(status_code=409, detail="POLICY_STATE_CONFLICT")
            if current["submitted_by_user_id"] == actor_id:
                raise HTTPException(
                    status_code=403, detail="POLICY_SELF_REVIEW_FORBIDDEN"
                )
            update_sql = (
                "workflow_status = 'rejected', "
                "approved_by_user_id = NULL, approved_at = NULL"
            )
            next_state = "rejected"
        else:
            if not is_manager(tenant.role):
                raise HTTPException(status_code=403, detail="POLICY_PUBLISH_FORBIDDEN")
            if state != "approved":
                raise HTTPException(status_code=409, detail="POLICY_STATE_CONFLICT")
            await session.execute(
                text(
                    "UPDATE f1.policy_version SET workflow_status = 'superseded', "
                    "updated_at = statement_timestamp() "
                    "WHERE source_id = :source_id AND id <> :version_id "
                    "AND workflow_status = 'published'"
                ),
                {"source_id": current["source_id"], "version_id": version_id},
            )
            update_sql = (
                "workflow_status = 'published', "
                "published_by_user_id = :actor_id, "
                "published_at = statement_timestamp()"
            )
            next_state = "published"

        changed = (
            await session.execute(
                text(
                    f"UPDATE f1.policy_version SET {update_sql}, "
                    "updated_at = statement_timestamp() "
                    "WHERE id = :version_id AND workflow_status = :current_state "
                    f"RETURNING {VERSION_COLUMNS}"
                ),
                {
                    "actor_id": actor_id,
                    "version_id": version_id,
                    "current_state": state,
                },
            )
        ).mappings().one_or_none()
        if changed is None:
            raise HTTPException(status_code=409, detail="POLICY_STATE_CONFLICT")

        event_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO f1.policy_review_event ("
                "id, enterprise_id, policy_version_id, action, comment, "
                "actor_user_id) VALUES ("
                ":id, :enterprise_id, :version_id, :action, :comment, :actor_id)"
            ),
            {
                "id": event_id,
                "enterprise_id": tenant.enterprise_id,
                "version_id": version_id,
                "action": (
                    "submitted" if action == "submit" else
                    "approved" if action == "approve" else
                    "rejected" if action == "reject" else "published"
                ),
                "comment": comment,
                "actor_id": actor_id,
            },
        )
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            f"policy.version.{next_state}",
            "policy_version",
            str(version_id),
        )
        await session.commit()
    return await get_version(tenant, version_id)


__all__ = ("transition_version",)
