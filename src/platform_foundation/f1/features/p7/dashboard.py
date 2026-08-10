"""P7 local manual rehearsal dashboard."""
from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import text

from ...auth import Tenant
from ...database import session_scope
from .common import current_actor_id, row_dict
from .contracts import LOCAL_REHEARSAL_BOUNDARIES, plan_collection_actions
from .plans import PLAN_COLUMNS, RUN_COLUMNS, plan_out, run_out


async def get_dashboard(tenant: Tenant) -> dict[str, Any]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await current_actor_id(session, tenant)
        plan_rows = (
            await session.execute(
                text(
                    f"SELECT {PLAN_COLUMNS} FROM f1.rehearsal_plan "
                    "ORDER BY updated_at DESC, id"
                )
            )
        ).mappings().all()
        run_rows = (
            await session.execute(
                text(
                    f"SELECT {RUN_COLUMNS} FROM f1.rehearsal_run "
                    "ORDER BY created_at DESC, id DESC"
                )
            )
        ).mappings().all()
        result_rows = (
            await session.execute(
                text("SELECT run_id, status FROM f1.rehearsal_check_result")
            )
        ).mappings().all()

    plan_counts = Counter(str(row["status"]) for row in plan_rows)
    run_counts = Counter(str(row["status"]) for row in run_rows)
    result_counts = Counter(str(row["status"]) for row in result_rows)
    counts_by_run: dict[object, Counter[str]] = {}
    for result in result_rows:
        counts_by_run.setdefault(result["run_id"], Counter())[str(result["status"])] += 1
    pending_plans = [row for row in plan_rows if row["status"] == "active"][:10]
    recent_runs: list[dict[str, Any]] = []
    for run in run_rows[:10]:
        current = row_dict(run)
        current_counts = counts_by_run.get(run["id"], Counter())
        current.update(
            {
                "total_count": sum(current_counts.values()),
                "passed_count": current_counts["passed"],
                "failed_count": current_counts["failed"],
                "blocked_count": current_counts["blocked"],
                "pending_count": current_counts["pending"],
            }
        )
        recent_runs.append(run_out(current, tenant))
    return {
        "rehearsal_label": "本地人工演练",
        "plan_counts": {
            "total": len(plan_rows),
            "draft": plan_counts["draft"],
            "active": plan_counts["active"],
            "archived": plan_counts["archived"],
        },
        "run_counts": {
            "total": len(run_rows),
            "planned": run_counts["planned"],
            "running": run_counts["running"],
            "passed": run_counts["passed"],
            "failed": run_counts["failed"],
            "cancelled": run_counts["cancelled"],
        },
        "result_counts": {
            "total": len(result_rows),
            "pending": result_counts["pending"],
            "passed": result_counts["passed"],
            "failed": result_counts["failed"],
            "blocked": result_counts["blocked"],
        },
        "rollback_required_count": sum(
            bool(row["rollback_required"]) for row in run_rows
        ),
        "pending_plans": [plan_out(row, tenant) for row in pending_plans],
        "recent_runs": recent_runs,
        "allowed_actions": plan_collection_actions(tenant.role),
        "boundaries": list(LOCAL_REHEARSAL_BOUNDARIES),
    }


__all__ = ("get_dashboard",)
