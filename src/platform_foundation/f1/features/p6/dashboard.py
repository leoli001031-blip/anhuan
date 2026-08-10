"""P6 synthetic-quality aggregate dashboard."""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from ...auth import Tenant
from ...database import session_scope
from .common import current_actor_id
from .contracts import AUTOMATED_QUALITY_BOUNDARIES, suite_collection_actions
from .suites import RUN_COLUMNS, run_out


async def get_dashboard(tenant: Tenant) -> dict[str, Any]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await current_actor_id(session, tenant)
        counts = (
            await session.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM f1.quality_suite) AS suites_total, "
                    "(SELECT count(*) FROM f1.quality_suite "
                    " WHERE status = 'active') AS suites_active, "
                    "(SELECT count(*) FROM f1.quality_suite "
                    " WHERE status = 'archived') AS suites_archived, "
                    "(SELECT count(*) FROM f1.quality_scenario) AS scenarios_total, "
                    "(SELECT count(*) FROM f1.quality_scenario "
                    " WHERE enabled IS TRUE) AS scenarios_enabled, "
                    "(SELECT count(*) FROM f1.quality_scenario "
                    " WHERE enabled IS FALSE) AS scenarios_disabled, "
                    "(SELECT count(*) FROM f1.quality_run) AS runs_total, "
                    "(SELECT count(*) FROM f1.quality_run "
                    " WHERE status = 'queued') AS runs_queued, "
                    "(SELECT count(*) FROM f1.quality_run "
                    " WHERE status = 'running') AS runs_running, "
                    "(SELECT count(*) FROM f1.quality_run "
                    " WHERE status = 'passed') AS runs_passed, "
                    "(SELECT count(*) FROM f1.quality_run "
                    " WHERE status = 'failed') AS runs_failed, "
                    "(SELECT count(*) FROM f1.quality_run "
                    " WHERE status = 'cancelled') AS runs_cancelled, "
                    "(SELECT count(*) FROM f1.quality_result) AS results_total, "
                    "(SELECT count(*) FROM f1.quality_result "
                    " WHERE status = 'passed') AS results_passed, "
                    "(SELECT count(*) FROM f1.quality_result "
                    " WHERE status = 'failed') AS results_failed, "
                    "(SELECT count(*) FROM f1.quality_result "
                    " WHERE status = 'error') AS results_error, "
                    "(SELECT count(*) FROM f1.quality_disagreement) "
                    " AS disagreements_total, "
                    "(SELECT count(*) FROM f1.quality_disagreement "
                    " WHERE review_status = 'open') AS disagreements_open, "
                    "(SELECT count(*) FROM f1.quality_disagreement "
                    " WHERE review_status = 'acknowledged') "
                    " AS disagreements_acknowledged, "
                    "(SELECT count(*) FROM f1.quality_disagreement "
                    " WHERE review_status = 'waived') AS disagreements_waived"
                )
            )
        ).mappings().one()
        recent_runs = (
            await session.execute(
                text(
                    f"SELECT {RUN_COLUMNS} FROM f1.quality_run "
                    "ORDER BY created_at DESC, id DESC LIMIT 10"
                )
            )
        ).mappings().all()
    return {
        "synthetic_label": "合成场景",
        "suite_counts": {
            "total": counts["suites_total"],
            "active": counts["suites_active"],
            "archived": counts["suites_archived"],
        },
        "scenario_counts": {
            "total": counts["scenarios_total"],
            "enabled": counts["scenarios_enabled"],
            "disabled": counts["scenarios_disabled"],
        },
        "run_counts": {
            "total": counts["runs_total"],
            "queued": counts["runs_queued"],
            "running": counts["runs_running"],
            "passed": counts["runs_passed"],
            "failed": counts["runs_failed"],
            "cancelled": counts["runs_cancelled"],
        },
        "result_counts": {
            "total": counts["results_total"],
            "passed": counts["results_passed"],
            "failed": counts["results_failed"],
            "error": counts["results_error"],
        },
        "disagreement_counts": {
            "total": counts["disagreements_total"],
            "open": counts["disagreements_open"],
            "acknowledged": counts["disagreements_acknowledged"],
            "waived": counts["disagreements_waived"],
        },
        "recent_runs": [run_out(row) for row in recent_runs],
        "allowed_actions": suite_collection_actions(tenant.role),
        "boundaries": list(AUTOMATED_QUALITY_BOUNDARIES),
    }


__all__ = ("get_dashboard",)
