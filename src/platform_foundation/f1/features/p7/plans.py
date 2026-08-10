"""P7 local rehearsal plans and future-run checklists."""
from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...audit import add_event
from ...auth import Tenant
from ...database import session_scope
from .common import current_actor_id, row_dict
from .contracts import (
    LOCAL_REHEARSAL_BOUNDARIES,
    check_actions,
    is_manager,
    plan_actions,
    plan_collection_actions,
    run_actions,
)


PLAN_COLUMNS = (
    "id, enterprise_id, name, status, execution_mode, created_by_user_id, "
    "created_at, updated_at"
)
CHECK_COLUMNS = (
    "id, enterprise_id, plan_id, check_key, category, label, sequence_no, "
    "required, enabled, created_by_user_id, created_at, updated_at"
)
RUN_COLUMNS = (
    "id, enterprise_id, plan_id, status, total_count, passed_count, "
    "failed_count, blocked_count, pending_count, rollback_required, "
    "created_by_user_id, created_at, started_at, completed_at"
)


async def plan_row(
    session: AsyncSession, plan_id: uuid.UUID, *, lock: bool = False
) -> Mapping[str, Any]:
    suffix = " FOR UPDATE" if lock else ""
    row = (
        await session.execute(
            text(
                f"SELECT {PLAN_COLUMNS} FROM f1.rehearsal_plan "
                "WHERE id = :plan_id" + suffix
            ),
            {"plan_id": plan_id},
        )
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="REHEARSAL_PLAN_NOT_FOUND")
    return row


async def check_row(
    session: AsyncSession, check_id: uuid.UUID, *, lock: bool = False
) -> Mapping[str, Any]:
    suffix = " FOR UPDATE" if lock else ""
    row = (
        await session.execute(
            text(
                f"SELECT {CHECK_COLUMNS} FROM f1.rehearsal_check "
                "WHERE id = :check_id" + suffix
            ),
            {"check_id": check_id},
        )
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="REHEARSAL_CHECK_NOT_FOUND")
    return row


def plan_out(
    row: Mapping[str, Any],
    tenant: Tenant,
    *,
    has_enabled_checks: bool = True,
) -> dict[str, Any]:
    output = row_dict(row)
    output["allowed_actions"] = plan_actions(tenant.role, str(row["status"]))
    if not has_enabled_checks and "start_run" in output["allowed_actions"]:
        output["allowed_actions"].remove("start_run")
    output["boundaries"] = list(LOCAL_REHEARSAL_BOUNDARIES)
    return output


def check_out(
    row: Mapping[str, Any], tenant: Tenant, *, plan_status: str = "active"
) -> dict[str, Any]:
    output = row_dict(row)
    output["allowed_actions"] = check_actions(tenant.role, plan_status)
    output["boundaries"] = list(LOCAL_REHEARSAL_BOUNDARIES)
    return output


def run_out(row: Mapping[str, Any], tenant: Tenant) -> dict[str, Any]:
    output = row_dict(row)
    output["allowed_actions"] = run_actions(
        tenant.role, str(row["status"]), int(row["pending_count"])
    )
    output["boundaries"] = list(LOCAL_REHEARSAL_BOUNDARIES)
    return output


async def list_plans(tenant: Tenant) -> dict[str, Any]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await current_actor_id(session, tenant)
        rows = (
            await session.execute(
                text(
                    f"SELECT {PLAN_COLUMNS} FROM f1.rehearsal_plan "
                    "ORDER BY updated_at DESC, id"
                )
            )
        ).mappings().all()
    return {
        "items": [plan_out(row, tenant) for row in rows],
        "allowed_actions": plan_collection_actions(tenant.role),
        "boundaries": list(LOCAL_REHEARSAL_BOUNDARIES),
    }


async def get_plan(tenant: Tenant, plan_id: uuid.UUID) -> dict[str, Any]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await current_actor_id(session, tenant)
        plan = await plan_row(session, plan_id)
        checks = (
            await session.execute(
                text(
                    f"SELECT {CHECK_COLUMNS} FROM f1.rehearsal_check "
                    "WHERE plan_id = :plan_id ORDER BY sequence_no, id"
                ),
                {"plan_id": plan_id},
            )
        ).mappings().all()
        recent_runs = (
            await session.execute(
                text(
                    f"SELECT {RUN_COLUMNS} FROM f1.rehearsal_run "
                    "WHERE plan_id = :plan_id "
                    "ORDER BY created_at DESC, id DESC LIMIT 20"
                ),
                {"plan_id": plan_id},
            )
        ).mappings().all()
        result_rows = (
            await session.execute(
                text(
                    "SELECT run_id, status FROM f1.rehearsal_check_result "
                    "WHERE run_id IN (SELECT id FROM f1.rehearsal_run "
                    "WHERE plan_id = :plan_id)"
                ),
                {"plan_id": plan_id},
            )
        ).mappings().all()
    output = plan_out(
        plan,
        tenant,
        has_enabled_checks=any(bool(row["enabled"]) for row in checks),
    )
    output["checks"] = [
        check_out(row, tenant, plan_status=str(plan["status"])) for row in checks
    ]
    counts_by_run: dict[object, Counter[str]] = {}
    for result in result_rows:
        counts_by_run.setdefault(result["run_id"], Counter())[str(result["status"])] += 1
    effective_runs: list[dict[str, Any]] = []
    for run in recent_runs:
        current = row_dict(run)
        counts = counts_by_run.get(run["id"], Counter())
        current.update(
            {
                "total_count": sum(counts.values()),
                "passed_count": counts["passed"],
                "failed_count": counts["failed"],
                "blocked_count": counts["blocked"],
                "pending_count": counts["pending"],
            }
        )
        effective_runs.append(run_out(current, tenant))
    output["recent_runs"] = effective_runs
    return output


async def create_plan(tenant: Tenant, *, name: str) -> dict[str, Any]:
    if not is_manager(tenant.role):
        raise HTTPException(status_code=403, detail="REHEARSAL_MANAGER_REQUIRED")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        plan_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO f1.rehearsal_plan ("
                "id, enterprise_id, name, status, execution_mode, "
                "created_by_user_id) VALUES ("
                ":id, :enterprise_id, :name, 'active', 'local_manual', :actor_id)"
            ),
            {
                "id": plan_id,
                "enterprise_id": tenant.enterprise_id,
                "name": name,
                "actor_id": actor_id,
            },
        )
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "rehearsal.plan.created",
            "rehearsal_plan",
            str(plan_id),
        )
        await session.commit()
    return await get_plan(tenant, plan_id)


async def create_check(
    tenant: Tenant,
    plan_id: uuid.UUID,
    *,
    check_key: str,
    category: str,
    label: str,
    sequence_no: int,
    required: bool,
    enabled: bool,
) -> dict[str, Any]:
    if not is_manager(tenant.role):
        raise HTTPException(status_code=403, detail="REHEARSAL_MANAGER_REQUIRED")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        plan = await plan_row(session, plan_id, lock=True)
        if plan["status"] not in ("draft", "active"):
            raise HTTPException(status_code=409, detail="REHEARSAL_PLAN_ARCHIVED")
        duplicate = (
            await session.execute(
                text(
                    "SELECT 1 FROM f1.rehearsal_check WHERE plan_id = :plan_id "
                    "AND (check_key = :check_key OR sequence_no = :sequence_no)"
                ),
                {
                    "plan_id": plan_id,
                    "check_key": check_key,
                    "sequence_no": sequence_no,
                },
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="REHEARSAL_CHECK_SLOT_EXISTS")
        check_id = uuid.uuid4()
        row = (
            await session.execute(
                text(
                    "INSERT INTO f1.rehearsal_check ("
                    "id, enterprise_id, plan_id, check_key, category, label, "
                    "sequence_no, required, enabled, created_by_user_id) VALUES ("
                    ":id, :enterprise_id, :plan_id, :check_key, :category, "
                    ":label, :sequence_no, :required, :enabled, :actor_id) "
                    f"RETURNING {CHECK_COLUMNS}"
                ),
                {
                    "id": check_id,
                    "enterprise_id": tenant.enterprise_id,
                    "plan_id": plan_id,
                    "check_key": check_key,
                    "category": category,
                    "label": label,
                    "sequence_no": sequence_no,
                    "required": required,
                    "enabled": enabled,
                    "actor_id": actor_id,
                },
            )
        ).mappings().one()
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "rehearsal.check.created",
            "rehearsal_check",
            str(check_id),
        )
        await session.commit()
    return check_out(row, tenant, plan_status=str(plan["status"]))


async def update_check(
    tenant: Tenant, check_id: uuid.UUID, changes: dict[str, Any]
) -> dict[str, Any]:
    if not is_manager(tenant.role):
        raise HTTPException(status_code=403, detail="REHEARSAL_MANAGER_REQUIRED")
    if not changes:
        raise HTTPException(status_code=422, detail="REHEARSAL_CHECK_NO_CHANGES")
    allowed = {"category", "label", "sequence_no", "required", "enabled"}
    if not set(changes).issubset(allowed) or any(
        value is None for value in changes.values()
    ):
        raise HTTPException(status_code=422, detail="REHEARSAL_CHECK_FIELD_INVALID")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await current_actor_id(session, tenant)
        visible = await check_row(session, check_id)
        plan = await plan_row(session, visible["plan_id"], lock=True)
        if plan["status"] not in ("draft", "active"):
            raise HTTPException(status_code=409, detail="REHEARSAL_PLAN_ARCHIVED")
        current = await check_row(session, check_id, lock=True)
        if "sequence_no" in changes:
            duplicate = (
                await session.execute(
                    text(
                        "SELECT 1 FROM f1.rehearsal_check "
                        "WHERE plan_id = :plan_id AND sequence_no = :sequence_no "
                        "AND id <> :check_id"
                    ),
                    {
                        "plan_id": current["plan_id"],
                        "sequence_no": changes["sequence_no"],
                        "check_id": check_id,
                    },
                )
            ).scalar_one_or_none()
            if duplicate is not None:
                raise HTTPException(
                    status_code=409, detail="REHEARSAL_CHECK_SLOT_EXISTS"
                )
        assignments = ", ".join(f"{name} = :{name}" for name in changes)
        row = (
            await session.execute(
                text(
                    f"UPDATE f1.rehearsal_check SET {assignments}, "
                    "updated_at = statement_timestamp() WHERE id = :check_id "
                    f"RETURNING {CHECK_COLUMNS}"
                ),
                {**changes, "check_id": check_id},
            )
        ).mappings().one_or_none()
        if row is None:
            raise HTTPException(status_code=409, detail="REHEARSAL_CHECK_CONFLICT")
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "rehearsal.check.updated",
            "rehearsal_check",
            str(check_id),
        )
        await session.commit()
    return check_out(row, tenant, plan_status=str(plan["status"]))


__all__ = (
    "CHECK_COLUMNS",
    "PLAN_COLUMNS",
    "RUN_COLUMNS",
    "check_out",
    "check_row",
    "create_check",
    "create_plan",
    "get_plan",
    "list_plans",
    "plan_out",
    "plan_row",
    "run_out",
    "update_check",
)
