"""RLS-backed P4 operating dashboard."""
from __future__ import annotations

from sqlalchemy import text

from ...auth import Tenant
from ...database import session_scope
from .common import current_actor_id, row_dict
from .contracts import (
    BUSINESS_SNAPSHOT_BOUNDARIES,
    dashboard_allowed_actions,
    dashboard_view,
)


async def dashboard_overview(tenant: Tenant) -> dict[str, object]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await current_actor_id(session, tenant)
        as_of = (
            await session.execute(text("SELECT statement_timestamp()"))
        ).scalar_one()

        metric_sql = {
            "active_service_cases": (
                "SELECT count(*) FROM f1.service_case "
                "WHERE status IN ('planned','in_progress')"
            ),
            "upcoming_site_visits": (
                "SELECT count(*) FROM f1.site_visit "
                "WHERE status = 'planned' "
                "AND planned_start_at >= statement_timestamp()"
            ),
            "open_findings": (
                "SELECT count(*) FROM f1.finding WHERE status <> 'closed'"
            ),
            "overdue_findings": (
                "SELECT count(*) FROM f1.finding WHERE status <> 'closed' "
                "AND due_at < statement_timestamp()"
            ),
            "pending_reviews": (
                "SELECT count(*) FROM f1.finding "
                "WHERE status IN ('submitted','reviewing')"
            ),
            "controlled_documents_ready": (
                "SELECT count(*) FROM f1.upload_task "
                "WHERE pipeline_kind = 'controlled_ingestion' "
                "AND object_state = 'ready' AND quarantine_status = 'released' "
                "AND scan_verdict = 'clean' AND preview_status = 'ready'"
            ),
            "controlled_documents_blocked": (
                "SELECT count(*) FROM f1.upload_task "
                "WHERE pipeline_kind = 'controlled_ingestion' AND ("
                "quarantine_status = 'blocked' OR processing_stage IN "
                "('retry_wait','failed','rejected'))"
            ),
            "business_reports": "SELECT count(*) FROM f1.business_report",
            "crm_follow_ups_due": (
                "SELECT count(*) FROM f1.crm_account "
                "WHERE stage <> 'closed' AND next_follow_up_at IS NOT NULL "
                "AND next_follow_up_at <= statement_timestamp()"
            ),
        }
        metrics: dict[str, int] = {}
        for name, sql in metric_sql.items():
            metrics[name] = int(
                (await session.execute(text(sql))).scalar_one()
            )

        service_rows = (
            await session.execute(
                text(
                    "SELECT id, status, planned_start_at AS due_at, "
                    "NULL::uuid AS related_id FROM f1.service_case "
                    "WHERE status IN ('planned','in_progress') "
                    "ORDER BY planned_start_at NULLS LAST, id LIMIT 10"
                )
            )
        ).mappings().all()
        visit_rows = (
            await session.execute(
                text(
                    "SELECT id, status, planned_start_at AS due_at, "
                    "service_case_id AS related_id FROM f1.site_visit "
                    "WHERE status IN ('planned','in_progress') "
                    "ORDER BY planned_start_at NULLS LAST, id LIMIT 10"
                )
            )
        ).mappings().all()
        finding_rows = (
            await session.execute(
                text(
                    "SELECT finding.id, finding.status, finding.due_at, "
                    "COALESCE(finding.service_case_id, visit.service_case_id) "
                    "AS related_id FROM f1.finding AS finding "
                    "LEFT JOIN f1.site_visit AS visit "
                    "ON visit.enterprise_id = finding.enterprise_id "
                    "AND visit.id = finding.site_visit_id "
                    "WHERE finding.status <> 'closed' "
                    "ORDER BY finding.due_at, finding.id LIMIT 10"
                )
            )
        ).mappings().all()
        report_rows = (
            await session.execute(
                text(
                    "SELECT id, status, updated_at AS due_at, "
                    "service_case_id AS related_id FROM f1.business_report "
                    "ORDER BY updated_at DESC, id LIMIT 10"
                )
            )
        ).mappings().all()
        crm_rows = (
            await session.execute(
                text(
                    "SELECT id, stage AS status, next_follow_up_at AS due_at, "
                    "NULL::uuid AS related_id FROM f1.crm_account "
                    "WHERE stage <> 'closed' "
                    "ORDER BY next_follow_up_at NULLS LAST, id LIMIT 10"
                )
            )
        ).mappings().all()

    def items(kind: str, rows: list[object]) -> list[dict[str, object]]:
        return [{"kind": kind, **row_dict(row)} for row in rows]  # type: ignore[arg-type]

    return {
        "view": dashboard_view(tenant.role),
        "as_of": as_of,
        "metrics": metrics,
        "queues": {
            "service_cases": items("service_case", service_rows),
            "site_visits": items("site_visit", visit_rows),
            "findings": items("finding", finding_rows),
            "reports": items("business_report", report_rows),
            "crm_follow_ups": items("crm_account", crm_rows),
        },
        "allowed_actions": dashboard_allowed_actions(tenant.role),
        "boundaries": list(BUSINESS_SNAPSHOT_BOUNDARIES),
    }


__all__ = ("dashboard_overview",)
