"""P7 local manual rehearsal runs and immutable result recording."""
from __future__ import annotations

import uuid
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
    is_manager,
    is_operator,
    reason_allowed,
    result_actions,
)
from .plans import CHECK_COLUMNS, RUN_COLUMNS, plan_row, run_out


RESULT_COLUMNS = (
    "id, enterprise_id, run_id, check_id, check_key, category, label, "
    "sequence_no, required, status, reason_code, evidence_sha256, "
    "recorded_by_user_id, recorded_at, created_at"
)


async def run_row(
    session: AsyncSession, run_id: uuid.UUID, *, lock: bool = False
) -> Mapping[str, Any]:
    suffix = " FOR UPDATE" if lock else ""
    row = (
        await session.execute(
            text(
                f"SELECT {RUN_COLUMNS} FROM f1.rehearsal_run "
                "WHERE id = :run_id" + suffix
            ),
            {"run_id": run_id},
        )
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="REHEARSAL_RUN_NOT_FOUND")
    return row


async def result_row(
    session: AsyncSession, result_id: uuid.UUID, *, lock: bool = False
) -> Mapping[str, Any]:
    suffix = " FOR UPDATE" if lock else ""
    row = (
        await session.execute(
            text(
                f"SELECT {RESULT_COLUMNS} FROM f1.rehearsal_check_result "
                "WHERE id = :result_id" + suffix
            ),
            {"result_id": result_id},
        )
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="REHEARSAL_RESULT_NOT_FOUND")
    return row


def result_out(
    row: Mapping[str, Any], tenant: Tenant, *, run_status: str
) -> dict[str, Any]:
    output = row_dict(row)
    output["allowed_actions"] = result_actions(
        tenant.role, run_status, str(row["status"])
    )
    output["boundaries"] = list(LOCAL_REHEARSAL_BOUNDARIES)
    return output


def _actual_counts(results: list[Mapping[str, Any]]) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "blocked": 0, "pending": 0}
    for result in results:
        counts[str(result["status"])] += 1
    return counts


async def create_run(tenant: Tenant, plan_id: uuid.UUID) -> dict[str, Any]:
    if not is_manager(tenant.role):
        raise HTTPException(status_code=403, detail="REHEARSAL_MANAGER_REQUIRED")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        plan = await plan_row(session, plan_id, lock=True)
        if plan["status"] != "active":
            raise HTTPException(status_code=409, detail="REHEARSAL_PLAN_NOT_ACTIVE")
        checks = (
            await session.execute(
                text(
                    f"SELECT {CHECK_COLUMNS} FROM f1.rehearsal_check "
                    "WHERE plan_id = :plan_id AND enabled IS TRUE "
                    "ORDER BY sequence_no, id FOR UPDATE"
                ),
                {"plan_id": plan_id},
            )
        ).mappings().all()
        if not checks:
            raise HTTPException(status_code=409, detail="REHEARSAL_NO_ENABLED_CHECKS")
        run_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO f1.rehearsal_run ("
                "id, enterprise_id, plan_id, status, total_count, passed_count, "
                "failed_count, blocked_count, pending_count, rollback_required, "
                "created_by_user_id) VALUES ("
                ":id, :enterprise_id, :plan_id, 'planned', 0, 0, 0, 0, 0, "
                "false, :actor_id)"
            ),
            {
                "id": run_id,
                "enterprise_id": tenant.enterprise_id,
                "plan_id": plan_id,
                "actor_id": actor_id,
            },
        )
        for check in checks:
            result_id = uuid.uuid4()
            await session.execute(
                text(
                    "INSERT INTO f1.rehearsal_check_result ("
                    "id, enterprise_id, run_id, check_id, check_key, category, "
                    "label, sequence_no, required, status) VALUES ("
                    ":id, :enterprise_id, :run_id, :check_id, :check_key, "
                    ":category, :label, :sequence_no, :required, 'pending')"
                ),
                {
                    "id": result_id,
                    "enterprise_id": tenant.enterprise_id,
                    "run_id": run_id,
                    "check_id": check["id"],
                    "check_key": check["check_key"],
                    "category": check["category"],
                    "label": check["label"],
                    "sequence_no": check["sequence_no"],
                    "required": check["required"],
                },
            )
        changed = await session.execute(
            text(
                "UPDATE f1.rehearsal_run SET status = 'running', "
                "started_at = statement_timestamp() "
                "WHERE id = :run_id AND status = 'planned'"
            ),
            {"run_id": run_id},
        )
        if changed.rowcount != 1:
            raise HTTPException(status_code=409, detail="REHEARSAL_RUN_START_CONFLICT")
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "rehearsal.run.started",
            "rehearsal_run",
            str(run_id),
        )
        await session.commit()
    return await get_run(tenant, run_id)


async def get_run(tenant: Tenant, run_id: uuid.UUID) -> dict[str, Any]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await current_actor_id(session, tenant)
        run = await run_row(session, run_id)
        results = (
            await session.execute(
                text(
                    f"SELECT {RESULT_COLUMNS} FROM f1.rehearsal_check_result "
                    "WHERE run_id = :run_id ORDER BY sequence_no, id"
                ),
                {"run_id": run_id},
            )
        ).mappings().all()
    counts = _actual_counts(list(results))
    current = row_dict(run)
    current.update(
        {
            "total_count": len(results),
            "passed_count": counts["passed"],
            "failed_count": counts["failed"],
            "blocked_count": counts["blocked"],
            "pending_count": counts["pending"],
        }
    )
    output = run_out(current, tenant)
    output["results"] = [
        result_out(row, tenant, run_status=str(run["status"])) for row in results
    ]
    return output


async def record_result(
    tenant: Tenant,
    run_id: uuid.UUID,
    result_id: uuid.UUID,
    *,
    status: str,
    reason_code: str,
    evidence_sha256: str,
) -> dict[str, Any]:
    if not is_operator(tenant.role):
        raise HTTPException(status_code=403, detail="REHEARSAL_OPERATOR_REQUIRED")
    if not reason_allowed(status, reason_code):
        raise HTTPException(status_code=422, detail="REHEARSAL_REASON_CODE_INVALID")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        run = await run_row(session, run_id, lock=True)
        if run["status"] != "running":
            raise HTTPException(status_code=409, detail="REHEARSAL_RUN_NOT_RUNNING")
        result = await result_row(session, result_id, lock=True)
        if result["run_id"] != run_id:
            raise HTTPException(status_code=404, detail="REHEARSAL_RESULT_NOT_FOUND")
        if result["status"] != "pending":
            raise HTTPException(status_code=409, detail="REHEARSAL_RESULT_TERMINAL")
        row = (
            await session.execute(
                text(
                    "UPDATE f1.rehearsal_check_result SET status = :status, "
                    "reason_code = :reason_code, evidence_sha256 = :evidence_sha256, "
                    "recorded_by_user_id = :actor_id, "
                    "recorded_at = statement_timestamp() "
                    "WHERE id = :result_id AND run_id = :run_id "
                    "AND status = 'pending' "
                    f"RETURNING {RESULT_COLUMNS}"
                ),
                {
                    "result_id": result_id,
                    "run_id": run_id,
                    "status": status,
                    "reason_code": reason_code,
                    "evidence_sha256": evidence_sha256,
                    "actor_id": actor_id,
                },
            )
        ).mappings().one_or_none()
        if row is None:
            raise HTTPException(status_code=409, detail="REHEARSAL_RESULT_CONFLICT")
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "rehearsal.result.recorded",
            "rehearsal_check_result",
            str(result_id),
        )
        await session.commit()
    return await get_run(tenant, run_id)


async def complete_run(tenant: Tenant, run_id: uuid.UUID) -> dict[str, Any]:
    if not is_manager(tenant.role):
        raise HTTPException(status_code=403, detail="REHEARSAL_MANAGER_REQUIRED")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await current_actor_id(session, tenant)
        run = await run_row(session, run_id, lock=True)
        if run["status"] != "running":
            raise HTTPException(status_code=409, detail="REHEARSAL_RUN_NOT_RUNNING")
        results = (
            await session.execute(
                text(
                    f"SELECT {RESULT_COLUMNS} FROM f1.rehearsal_check_result "
                    "WHERE run_id = :run_id ORDER BY sequence_no, id FOR UPDATE"
                ),
                {"run_id": run_id},
            )
        ).mappings().all()
        counts = _actual_counts(list(results))
        if counts["pending"] != 0:
            raise HTTPException(status_code=409, detail="REHEARSAL_RESULTS_PENDING")
        terminal_status = (
            "passed"
            if counts["failed"] == 0 and counts["blocked"] == 0
            else "failed"
        )
        changed = await session.execute(
            text(
                "UPDATE f1.rehearsal_run SET status = :terminal_status, "
                "completed_at = statement_timestamp() "
                "WHERE id = :run_id AND status = 'running'"
            ),
            {"run_id": run_id, "terminal_status": terminal_status},
        )
        if changed.rowcount != 1:
            raise HTTPException(status_code=409, detail="REHEARSAL_RUN_CONFLICT")
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            f"rehearsal.run.{terminal_status}",
            "rehearsal_run",
            str(run_id),
        )
        await session.commit()
    return await get_run(tenant, run_id)


async def cancel_run(tenant: Tenant, run_id: uuid.UUID) -> dict[str, Any]:
    if not is_manager(tenant.role):
        raise HTTPException(status_code=403, detail="REHEARSAL_MANAGER_REQUIRED")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await current_actor_id(session, tenant)
        run = await run_row(session, run_id, lock=True)
        if run["status"] != "running":
            raise HTTPException(status_code=409, detail="REHEARSAL_RUN_NOT_RUNNING")
        await session.execute(
            text(
                f"SELECT {RESULT_COLUMNS} FROM f1.rehearsal_check_result "
                "WHERE run_id = :run_id ORDER BY sequence_no, id FOR UPDATE"
            ),
            {"run_id": run_id},
        )
        changed = await session.execute(
            text(
                "UPDATE f1.rehearsal_run SET status = 'cancelled', "
                "completed_at = statement_timestamp() "
                "WHERE id = :run_id AND status = 'running'"
            ),
            {"run_id": run_id},
        )
        if changed.rowcount != 1:
            raise HTTPException(status_code=409, detail="REHEARSAL_RUN_CONFLICT")
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "rehearsal.run.cancelled",
            "rehearsal_run",
            str(run_id),
        )
        await session.commit()
    return await get_run(tenant, run_id)


__all__ = (
    "RESULT_COLUMNS",
    "cancel_run",
    "complete_run",
    "create_run",
    "get_run",
    "record_result",
    "result_out",
    "result_row",
    "run_row",
)
