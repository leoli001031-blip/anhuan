"""P5 manual impact candidates and internal follow-through tasks."""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...audit import add_event
from ...auth import Tenant
from ...database import session_scope
from .catalog import version_row
from .common import current_actor_id, ensure_member, row_dict
from .contracts import (
    POLICY_WORKFLOW_BOUNDARIES,
    impact_actions,
    impact_collection_actions,
    impact_task_actions,
    is_manager,
    is_reviewer,
)


IMPACT_COLUMNS = (
    "id, enterprise_id, policy_version_id, domain, scope_note, priority, status, "
    "created_by_user_id, created_at, updated_at"
)
TASK_COLUMNS = (
    "id, enterprise_id, impact_candidate_id, title, owner_user_id, due_at, "
    "status, created_by_user_id, created_at, updated_at"
)


async def impact_row(
    session: AsyncSession, impact_id: uuid.UUID, *, lock: bool = False
) -> Mapping[str, Any]:
    suffix = " FOR UPDATE" if lock else ""
    row = (
        await session.execute(
            text(
                f"SELECT {IMPACT_COLUMNS} FROM f1.policy_impact_candidate "
                "WHERE id = :impact_id" + suffix
            ),
            {"impact_id": impact_id},
        )
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="POLICY_IMPACT_NOT_FOUND")
    return row


async def task_row(
    session: AsyncSession, task_id: uuid.UUID, *, lock: bool = False
) -> Mapping[str, Any]:
    suffix = " FOR UPDATE" if lock else ""
    row = (
        await session.execute(
            text(
                f"SELECT {TASK_COLUMNS} FROM f1.policy_impact_task "
                "WHERE id = :task_id" + suffix
            ),
            {"task_id": task_id},
        )
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="POLICY_IMPACT_TASK_NOT_FOUND")
    return row


def impact_out(row: Mapping[str, Any], tenant: Tenant) -> dict[str, Any]:
    output = row_dict(row)
    output["allowed_actions"] = impact_actions(
        tenant.role, str(row["status"])
    )
    output["boundaries"] = list(POLICY_WORKFLOW_BOUNDARIES)
    return output


def task_out(
    row: Mapping[str, Any], tenant: Tenant, actor_id: uuid.UUID
) -> dict[str, Any]:
    output = row_dict(row)
    output["allowed_actions"] = impact_task_actions(
        tenant.role,
        str(row["status"]),
        is_owner=row["owner_user_id"] == actor_id,
    )
    return output


async def list_impacts(tenant: Tenant) -> dict[str, Any]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await current_actor_id(session, tenant)
        rows = (
            await session.execute(
                text(
                    f"SELECT {IMPACT_COLUMNS} FROM f1.policy_impact_candidate "
                    "ORDER BY priority DESC, updated_at DESC, id"
                )
            )
        ).mappings().all()
    return {
        "items": [impact_out(row, tenant) for row in rows],
        "allowed_actions": impact_collection_actions(tenant.role),
        "boundaries": list(POLICY_WORKFLOW_BOUNDARIES),
    }


async def get_impact(tenant: Tenant, impact_id: uuid.UUID) -> dict[str, Any]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        impact = await impact_row(session, impact_id)
        tasks = (
            await session.execute(
                text(
                    f"SELECT {TASK_COLUMNS} FROM f1.policy_impact_task "
                    "WHERE impact_candidate_id = :impact_id "
                    "ORDER BY due_at NULLS LAST, created_at, id"
                ),
                {"impact_id": impact_id},
            )
        ).mappings().all()
    output = impact_out(impact, tenant)
    output["tasks"] = [task_out(row, tenant, actor_id) for row in tasks]
    return output


async def create_impact(
    tenant: Tenant,
    *,
    policy_version_id: uuid.UUID,
    domain: str,
    scope_note: str,
    priority: str,
) -> dict[str, Any]:
    if not (is_manager(tenant.role) or is_reviewer(tenant.role)):
        raise HTTPException(status_code=403, detail="POLICY_IMPACT_MANAGER_REQUIRED")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        version = await version_row(session, policy_version_id)
        if version["workflow_status"] not in ("approved", "published"):
            raise HTTPException(status_code=409, detail="POLICY_IMPACT_VERSION_INVALID")
        impact_id = uuid.uuid4()
        row = (
            await session.execute(
                text(
                    "INSERT INTO f1.policy_impact_candidate ("
                    "id, enterprise_id, policy_version_id, domain, scope_note, "
                    "priority, status, created_by_user_id) VALUES ("
                    ":id, :enterprise_id, :policy_version_id, :domain, "
                    ":scope_note, :priority, 'open', :actor_id) "
                    f"RETURNING {IMPACT_COLUMNS}"
                ),
                {
                    "id": impact_id,
                    "enterprise_id": tenant.enterprise_id,
                    "policy_version_id": policy_version_id,
                    "domain": domain,
                    "scope_note": scope_note,
                    "priority": priority,
                    "actor_id": actor_id,
                },
            )
        ).mappings().one()
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "policy.impact.created",
            "policy_impact_candidate",
            str(impact_id),
        )
        await session.commit()
    return impact_out(row, tenant)


async def update_impact(
    tenant: Tenant, impact_id: uuid.UUID, changes: dict[str, Any]
) -> dict[str, Any]:
    if not (is_manager(tenant.role) or is_reviewer(tenant.role)):
        raise HTTPException(status_code=403, detail="POLICY_IMPACT_MANAGER_REQUIRED")
    if not changes:
        raise HTTPException(status_code=422, detail="POLICY_IMPACT_NO_CHANGES")
    allowed = {"scope_note", "priority", "status"}
    if not set(changes).issubset(allowed):
        raise HTTPException(status_code=422, detail="POLICY_IMPACT_FIELD_INVALID")
    if any(value is None for value in changes.values()):
        raise HTTPException(status_code=422, detail="POLICY_IMPACT_FIELD_INVALID")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await current_actor_id(session, tenant)
        current = await impact_row(session, impact_id, lock=True)
        if current["status"] != "open":
            raise HTTPException(status_code=409, detail="POLICY_IMPACT_TERMINAL")
        target = changes.get("status", "open")
        if target not in ("open", "accepted", "dismissed"):
            raise HTTPException(status_code=409, detail="POLICY_IMPACT_STATE_CONFLICT")
        assignments = ", ".join(f"{name} = :{name}" for name in changes)
        row = (
            await session.execute(
                text(
                    f"UPDATE f1.policy_impact_candidate SET {assignments}, "
                    "updated_at = statement_timestamp() "
                    "WHERE id = :impact_id AND status = 'open' "
                    f"RETURNING {IMPACT_COLUMNS}"
                ),
                {**changes, "impact_id": impact_id},
            )
        ).mappings().one_or_none()
        if row is None:
            raise HTTPException(status_code=409, detail="POLICY_IMPACT_CONFLICT")
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "policy.impact.updated",
            "policy_impact_candidate",
            str(impact_id),
        )
        await session.commit()
    return impact_out(row, tenant)


async def create_task(
    tenant: Tenant,
    impact_id: uuid.UUID,
    *,
    title: str,
    owner_user_id: uuid.UUID,
    due_at: datetime,
) -> dict[str, Any]:
    if not (is_manager(tenant.role) or is_reviewer(tenant.role)):
        raise HTTPException(status_code=403, detail="POLICY_IMPACT_MANAGER_REQUIRED")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        impact = await impact_row(session, impact_id, lock=True)
        if impact["status"] != "accepted":
            raise HTTPException(status_code=409, detail="POLICY_IMPACT_NOT_ACCEPTED")
        version = await version_row(session, impact["policy_version_id"])
        if version["workflow_status"] not in ("approved", "published"):
            raise HTTPException(status_code=409, detail="POLICY_IMPACT_VERSION_INVALID")
        await ensure_member(
            session,
            enterprise_id=tenant.enterprise_id,
            user_id=owner_user_id,
        )
        task_id = uuid.uuid4()
        row = (
            await session.execute(
                text(
                    "INSERT INTO f1.policy_impact_task ("
                    "id, enterprise_id, impact_candidate_id, title, owner_user_id, "
                    "due_at, status, created_by_user_id) VALUES ("
                    ":id, :enterprise_id, :impact_id, :title, :owner_user_id, "
                    ":due_at, 'open', :actor_id) "
                    f"RETURNING {TASK_COLUMNS}"
                ),
                {
                    "id": task_id,
                    "enterprise_id": tenant.enterprise_id,
                    "impact_id": impact_id,
                    "title": title,
                    "owner_user_id": owner_user_id,
                    "due_at": due_at,
                    "actor_id": actor_id,
                },
            )
        ).mappings().one()
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "policy.impact_task.created",
            "policy_impact_task",
            str(task_id),
        )
        await session.commit()
    return task_out(row, tenant, actor_id)


async def update_task(
    tenant: Tenant, task_id: uuid.UUID, changes: dict[str, Any]
) -> dict[str, Any]:
    if not changes:
        raise HTTPException(status_code=422, detail="POLICY_TASK_NO_CHANGES")
    allowed = {"title", "owner_user_id", "due_at", "status"}
    if not set(changes).issubset(allowed):
        raise HTTPException(status_code=422, detail="POLICY_TASK_FIELD_INVALID")
    if any(
        changes.get(name) is None
        for name in ("title", "owner_user_id", "status")
        if name in changes
    ):
        raise HTTPException(status_code=422, detail="POLICY_TASK_FIELD_INVALID")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        current = await task_row(session, task_id, lock=True)
        manager = is_manager(tenant.role) or is_reviewer(tenant.role)
        owner = current["owner_user_id"] == actor_id
        if not manager and not owner:
            raise HTTPException(status_code=403, detail="POLICY_TASK_EDIT_FORBIDDEN")
        if current["status"] in ("completed", "dismissed"):
            raise HTTPException(status_code=409, detail="POLICY_TASK_TERMINAL")
        if not manager and set(changes) - {"status"}:
            raise HTTPException(status_code=403, detail="POLICY_TASK_EDIT_FORBIDDEN")
        if "owner_user_id" in changes:
            await ensure_member(
                session,
                enterprise_id=tenant.enterprise_id,
                user_id=changes["owner_user_id"],
            )
        current_status = str(current["status"])
        target_status = str(changes.get("status", current_status))
        transitions = {
            "open": {"open", "in_progress", "completed", "dismissed"},
            "in_progress": {"in_progress", "completed", "dismissed"},
        }
        if target_status not in transitions[current_status]:
            raise HTTPException(status_code=409, detail="POLICY_TASK_STATE_CONFLICT")
        if not manager and target_status == "dismissed":
            raise HTTPException(status_code=403, detail="POLICY_TASK_DISMISS_FORBIDDEN")
        assignments = ", ".join(f"{name} = :{name}" for name in changes)
        row = (
            await session.execute(
                text(
                    f"UPDATE f1.policy_impact_task SET {assignments}, "
                    "updated_at = statement_timestamp() "
                    "WHERE id = :task_id AND status = :current_status "
                    f"RETURNING {TASK_COLUMNS}"
                ),
                {**changes, "task_id": task_id, "current_status": current_status},
            )
        ).mappings().one_or_none()
        if row is None:
            raise HTTPException(status_code=409, detail="POLICY_TASK_CONFLICT")
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "policy.impact_task.updated",
            "policy_impact_task",
            str(task_id),
        )
        await session.commit()
    return task_out(row, tenant, actor_id)


__all__ = (
    "IMPACT_COLUMNS",
    "TASK_COLUMNS",
    "create_impact",
    "create_task",
    "get_impact",
    "list_impacts",
    "update_impact",
    "update_task",
)
