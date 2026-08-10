"""Append-only P2 business timeline and service-case aggregation helpers."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .audit import add_event
from .business_notifications import add_notifications, recipient_ids_for_roles
from .business_workbench import case_aggregate_target


async def add_timeline_event(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    service_case_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    event_type: str,
    subject_type: str,
    subject_id: uuid.UUID,
    status: str | None,
    occurred_at: datetime | None = None,
) -> uuid.UUID:
    """Insert one body-free timeline row without committing the transaction."""
    event_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO f1.business_timeline ("
            "id, enterprise_id, service_case_id, event_type, subject_type, "
            "subject_id, status, actor_user_id, occurred_at"
            ") VALUES ("
            ":id, :enterprise_id, :service_case_id, :event_type, :subject_type, "
            ":subject_id, :status, :actor_user_id, "
            "COALESCE(:occurred_at, statement_timestamp()))"
        ),
        {
            "id": event_id,
            "enterprise_id": enterprise_id,
            "service_case_id": service_case_id,
            "event_type": event_type,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "status": status,
            "actor_user_id": actor_user_id,
            "occurred_at": occurred_at,
        },
    )
    return event_id


async def maybe_complete_service_case(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    service_case_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    actor_sub: str,
) -> bool:
    """Aggregate a visible in-progress case to completed, without committing."""
    case_status = (
        await session.execute(
            text(
                "SELECT status FROM f1.service_case "
                "WHERE id = :case_id FOR UPDATE"
            ),
            {"case_id": service_case_id},
        )
    ).scalar_one_or_none()
    if case_status is None or str(case_status) != "in_progress":
        return False
    visit_statuses = tuple(
        str(status)
        for status in (
            await session.execute(
                text(
                    "SELECT status FROM f1.site_visit "
                    "WHERE service_case_id = :case_id ORDER BY id"
                ),
                {"case_id": service_case_id},
            )
        ).scalars()
    )
    finding_statuses = tuple(
        str(status)
        for status in (
            await session.execute(
                text(
                    "SELECT finding.status FROM f1.finding AS finding "
                    "LEFT JOIN f1.site_visit AS visit "
                    "ON visit.enterprise_id = finding.enterprise_id "
                    "AND visit.id = finding.site_visit_id "
                    "WHERE COALESCE(finding.service_case_id, visit.service_case_id) "
                    "= :case_id ORDER BY finding.id"
                ),
                {"case_id": service_case_id},
            )
        ).scalars()
    )
    target = case_aggregate_target(
        str(case_status), visit_statuses, finding_statuses
    )
    if target is None:
        return False
    changed = await session.execute(
        text(
            "UPDATE f1.service_case SET status = :target "
            "WHERE id = :case_id AND status = 'in_progress'"
        ),
        {"target": target, "case_id": service_case_id},
    )
    if changed.rowcount != 1:
        return False
    await add_event(
        session,
        enterprise_id,
        actor_sub,
        "service_case.auto_complete",
        "service_case",
        str(service_case_id),
    )
    timeline_event_id = await add_timeline_event(
        session,
        enterprise_id=enterprise_id,
        service_case_id=service_case_id,
        actor_user_id=actor_user_id,
        event_type="service_case.auto_completed",
        subject_type="service_case",
        subject_id=service_case_id,
        status=target,
    )
    manager_ids = await recipient_ids_for_roles(
        session,
        enterprise_id=enterprise_id,
        roles=("super_admin", "enterprise_admin"),
    )
    await add_notifications(
        session,
        enterprise_id=enterprise_id,
        timeline_event_id=timeline_event_id,
        recipient_user_ids=manager_ids,
        actor_user_id=actor_user_id,
    )
    return True


__all__ = ("add_timeline_event", "maybe_complete_service_case")
