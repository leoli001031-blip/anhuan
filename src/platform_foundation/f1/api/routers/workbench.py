"""P2 role workbench, calendar, and body-free in-app notifications."""
from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...audit import add_event
from ...auth import Tenant, tenant_from_header
from ...business_workbench import (
    case_allowed_actions,
    finding_allowed_actions,
    site_visit_allowed_actions,
)
from ...database import session_scope

router = APIRouter()

_NOTIFICATION_COLUMNS = (
    "notification.id, timeline.event_type, timeline.subject_type, "
    "timeline.subject_id, timeline.service_case_id, "
    "notification.created_at, notification.read_at"
)
_NOTIFICATION_FROM = (
    "FROM f1.in_app_notification AS notification "
    "JOIN f1.business_timeline AS timeline "
    "ON timeline.enterprise_id = notification.enterprise_id "
    "AND timeline.id = notification.timeline_event_id"
)


class WorkbenchServiceCaseOut(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    planned_start_at: datetime | None
    planned_end_at: datetime | None
    allowed_actions: list[str]


class WorkbenchVisitOut(BaseModel):
    id: uuid.UUID
    service_case_id: uuid.UUID
    title: str
    status: str
    planned_start_at: datetime | None
    planned_end_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    allowed_actions: list[str]


class WorkbenchFindingOut(BaseModel):
    id: uuid.UUID
    service_case_id: uuid.UUID
    title: str
    severity: str
    status: str
    due_at: datetime
    allowed_actions: list[str]


class WorkbenchOverviewOut(BaseModel):
    view: Literal["admin", "enterprise", "executor"]
    metrics: dict[str, int]
    service_cases: list[WorkbenchServiceCaseOut]
    findings: list[WorkbenchFindingOut]
    upcoming_visits: list[WorkbenchVisitOut]
    reviews: list[WorkbenchFindingOut]


class CalendarItemOut(BaseModel):
    id: uuid.UUID
    item_type: Literal["case", "visit", "finding_deadline"]
    title: str
    start_at: datetime
    end_at: datetime | None
    status: str
    service_case_id: uuid.UUID | None
    finding_id: uuid.UUID | None


class CalendarOut(BaseModel):
    items: list[CalendarItemOut]


class NotificationOut(BaseModel):
    id: uuid.UUID
    event_type: str
    subject_type: str
    subject_id: uuid.UUID
    service_case_id: uuid.UUID
    created_at: datetime
    read_at: datetime | None
    allowed_actions: list[str]


class NotificationListOut(BaseModel):
    items: list[NotificationOut]


class UnreadCountOut(BaseModel):
    unread_count: int


def _workbench_view(role: str | None) -> Literal["admin", "enterprise", "executor"]:
    if role == "super_admin":
        return "admin"
    if role == "enterprise_admin":
        return "enterprise"
    return "executor"


async def _current_user_id(session: AsyncSession, tenant: Tenant) -> uuid.UUID:
    actor_id = (
        await session.execute(
            text(
                "SELECT membership.user_id "
                "FROM f1.enterprise_user AS membership "
                "JOIN f1.user_profile AS profile ON profile.id = membership.user_id "
                "WHERE membership.enterprise_id = :enterprise_id "
                "AND profile.keycloak_sub = :sub"
            ),
            {"enterprise_id": tenant.enterprise_id, "sub": tenant.sub},
        )
    ).scalar_one_or_none()
    if actor_id is None:
        raise HTTPException(status_code=404, detail="P2_MEMBERSHIP_NOT_FOUND")
    return actor_id


async def _capacity_map(
    session: AsyncSession,
    actor_id: uuid.UUID,
) -> dict[uuid.UUID, tuple[str, ...]]:
    rows = (
        await session.execute(
            text(
                "SELECT service_case_id, capacity FROM f1.service_assignment "
                "WHERE assignee_user_id = :actor_id AND status = 'accepted'"
            ),
            {"actor_id": actor_id},
        )
    ).all()
    grouped: dict[uuid.UUID, list[str]] = defaultdict(list)
    for case_id, capacity in rows:
        grouped[case_id].append(str(capacity))
    return {case_id: tuple(values) for case_id, values in grouped.items()}


def _notification_out(row: Mapping[str, Any]) -> NotificationOut:
    read_at = row["read_at"]
    actions = ["view"] if read_at is not None else ["mark_read", "view"]
    return NotificationOut(**dict(row), allowed_actions=actions)


@router.get("/overview", response_model=WorkbenchOverviewOut)
async def get_workbench_overview(
    tenant: Tenant = Depends(tenant_from_header),
) -> WorkbenchOverviewOut:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await _current_user_id(session, tenant)
        capacities_by_case = await _capacity_map(session, actor_id)
        case_rows = (
            await session.execute(
                text(
                    "SELECT id, title, status, planned_start_at, planned_end_at "
                    "FROM f1.service_case "
                    "WHERE status NOT IN ('closed','cancelled') "
                    "ORDER BY planned_start_at NULLS LAST, updated_at DESC, id"
                )
            )
        ).mappings().all()
        visit_rows = (
            await session.execute(
                text(
                    "SELECT visit.id, visit.service_case_id, parent.title, "
                    "visit.status, visit.planned_start_at, visit.planned_end_at, "
                    "visit.started_at, visit.completed_at "
                    "FROM f1.site_visit AS visit JOIN f1.service_case AS parent "
                    "ON parent.enterprise_id = visit.enterprise_id "
                    "AND parent.id = visit.service_case_id "
                    "WHERE visit.status IN ('planned','in_progress') "
                    "ORDER BY visit.planned_start_at NULLS LAST, "
                    "visit.created_at, visit.id"
                )
            )
        ).mappings().all()
        finding_rows = (
            await session.execute(
                text(
                    "SELECT finding.id, COALESCE(finding.service_case_id, "
                    "visit.service_case_id) AS access_case_id, finding.title, "
                    "finding.severity, finding.status, finding.due_at "
                    "FROM f1.finding AS finding "
                    "LEFT JOIN f1.site_visit AS visit "
                    "ON visit.enterprise_id = finding.enterprise_id "
                    "AND visit.id = finding.site_visit_id "
                    "WHERE finding.status <> 'closed' "
                    "ORDER BY finding.due_at, finding.created_at, finding.id"
                )
            )
        ).mappings().all()
        unread_count = int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM f1.in_app_notification "
                        "WHERE read_at IS NULL"
                    )
                )
            ).scalar_one()
        )

    service_cases = [
        WorkbenchServiceCaseOut(
            **dict(row),
            allowed_actions=case_allowed_actions(tenant.role, str(row["status"])),
        )
        for row in case_rows
    ]
    upcoming_visits = [
        WorkbenchVisitOut(
            **dict(row),
            allowed_actions=site_visit_allowed_actions(
                tenant.role,
                str(row["status"]),
                capacities_by_case.get(row["service_case_id"], ()),
            ),
        )
        for row in visit_rows
    ]
    findings: list[WorkbenchFindingOut] = []
    reviews: list[WorkbenchFindingOut] = []
    for row in finding_rows:
        case_id = row["access_case_id"]
        actions = finding_allowed_actions(
            tenant.role,
            str(row["status"]),
            capacities_by_case.get(case_id, ()),
        )
        item = WorkbenchFindingOut(
            id=row["id"],
            service_case_id=case_id,
            title=row["title"],
            severity=row["severity"],
            status=row["status"],
            due_at=row["due_at"],
            allowed_actions=actions,
        )
        findings.append(item)
        if any(action in actions for action in ("start_review", "pass", "reject")):
            reviews.append(item)
    return WorkbenchOverviewOut(
        view=_workbench_view(tenant.role),
        metrics={
            "service_cases": len(service_cases),
            "active_service_cases": sum(
                item.status in ("planned", "in_progress") for item in service_cases
            ),
            "upcoming_visits": len(upcoming_visits),
            "open_findings": len(findings),
            "pending_reviews": len(reviews),
            "unread_notifications": unread_count,
        },
        service_cases=service_cases,
        findings=findings,
        upcoming_visits=upcoming_visits,
        reviews=reviews,
    )


@router.get("/calendar", response_model=CalendarOut)
async def get_workbench_calendar(
    start_at: datetime = Query(),
    end_at: datetime = Query(),
    tenant: Tenant = Depends(tenant_from_header),
) -> CalendarOut:
    if end_at <= start_at:
        raise HTTPException(status_code=422, detail="CALENDAR_RANGE_INVALID")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        parameters = {"start_at": start_at, "end_at": end_at}
        case_rows = (
            await session.execute(
                text(
                    "SELECT id, title, status, planned_start_at, planned_end_at "
                    "FROM f1.service_case WHERE planned_start_at >= :start_at "
                    "AND planned_start_at < :end_at ORDER BY planned_start_at, id"
                ),
                parameters,
            )
        ).mappings().all()
        visit_rows = (
            await session.execute(
                text(
                    "SELECT visit.id, visit.service_case_id, parent.title, "
                    "visit.status, visit.planned_start_at, visit.planned_end_at "
                    "FROM f1.site_visit AS visit "
                    "JOIN f1.service_case AS parent ON parent.enterprise_id = "
                    "visit.enterprise_id AND parent.id = visit.service_case_id "
                    "WHERE visit.planned_start_at >= :start_at "
                    "AND visit.planned_start_at < :end_at "
                    "ORDER BY visit.planned_start_at, visit.id"
                ),
                parameters,
            )
        ).mappings().all()
        finding_rows = (
            await session.execute(
                text(
                    "SELECT finding.id, COALESCE(finding.service_case_id, "
                    "visit.service_case_id) AS service_case_id, finding.title, "
                    "finding.status, finding.due_at FROM f1.finding AS finding "
                    "LEFT JOIN f1.site_visit AS visit "
                    "ON visit.enterprise_id = finding.enterprise_id "
                    "AND visit.id = finding.site_visit_id "
                    "WHERE finding.due_at >= :start_at AND finding.due_at < :end_at "
                    "ORDER BY finding.due_at, finding.id"
                ),
                parameters,
            )
        ).mappings().all()
    items: list[CalendarItemOut] = [
        CalendarItemOut(
            id=row["id"],
            item_type="case",
            title=row["title"],
            start_at=row["planned_start_at"],
            end_at=row["planned_end_at"],
            status=row["status"],
            service_case_id=row["id"],
            finding_id=None,
        )
        for row in case_rows
    ]
    items.extend(
        CalendarItemOut(
            id=row["id"],
            item_type="visit",
            title=row["title"],
            start_at=row["planned_start_at"],
            end_at=row["planned_end_at"],
            status=row["status"],
            service_case_id=row["service_case_id"],
            finding_id=None,
        )
        for row in visit_rows
    )
    items.extend(
        CalendarItemOut(
            id=row["id"],
            item_type="finding_deadline",
            title=row["title"],
            start_at=row["due_at"],
            end_at=None,
            status=row["status"],
            service_case_id=row["service_case_id"],
            finding_id=row["id"],
        )
        for row in finding_rows
    )
    items.sort(key=lambda item: (item.start_at, item.item_type, item.id.hex))
    return CalendarOut(items=items)


@router.get("/notifications", response_model=NotificationListOut)
async def list_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=100),
    tenant: Tenant = Depends(tenant_from_header),
) -> NotificationListOut:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        where = " WHERE notification.read_at IS NULL" if unread_only else ""
        rows = (
            await session.execute(
                text(
                    f"SELECT {_NOTIFICATION_COLUMNS} {_NOTIFICATION_FROM}{where} "
                    "ORDER BY notification.created_at DESC, notification.id DESC "
                    "LIMIT :limit"
                ),
                {"limit": limit},
            )
        ).mappings().all()
    return NotificationListOut(items=[_notification_out(row) for row in rows])


@router.get("/notifications/unread-count", response_model=UnreadCountOut)
async def get_unread_notification_count(
    tenant: Tenant = Depends(tenant_from_header),
) -> UnreadCountOut:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        count = int(
            (
                await session.execute(
                    text(
                        "SELECT count(*) FROM f1.in_app_notification "
                        "WHERE read_at IS NULL"
                    )
                )
            ).scalar_one()
        )
    return UnreadCountOut(unread_count=count)


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
async def mark_notification_read(
    notification_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> NotificationOut:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await _current_user_id(session, tenant)
        current = (
            await session.execute(
                text(
                    f"SELECT {_NOTIFICATION_COLUMNS} {_NOTIFICATION_FROM} "
                    "WHERE notification.id = :notification_id "
                    "AND notification.recipient_user_id = :actor_id"
                ),
                {"notification_id": notification_id, "actor_id": actor_id},
            )
        ).mappings().first()
        if current is None:
            raise HTTPException(status_code=404, detail="NOTIFICATION_NOT_FOUND")
        if current["read_at"] is None:
            changed = await session.execute(
                text(
                    "UPDATE f1.in_app_notification SET read_at = statement_timestamp() "
                    "WHERE id = :notification_id AND recipient_user_id = :actor_id "
                    "AND read_at IS NULL"
                ),
                {"notification_id": notification_id, "actor_id": actor_id},
            )
            if changed.rowcount != 1:
                raise HTTPException(status_code=409, detail="NOTIFICATION_STATE_CONFLICT")
            await add_event(
                session,
                tenant.enterprise_id,
                tenant.sub,
                "notification.read",
                "in_app_notification",
                str(notification_id),
            )
            current = (
                await session.execute(
                    text(
                        f"SELECT {_NOTIFICATION_COLUMNS} {_NOTIFICATION_FROM} "
                        "WHERE notification.id = :notification_id "
                        "AND notification.recipient_user_id = :actor_id"
                    ),
                    {"notification_id": notification_id, "actor_id": actor_id},
                )
            ).mappings().first()
            if current is None:
                raise HTTPException(status_code=404, detail="NOTIFICATION_NOT_FOUND")
            await session.commit()
        return _notification_out(current)


__all__ = (
    "router",
    "WorkbenchOverviewOut",
    "CalendarOut",
    "CalendarItemOut",
    "NotificationOut",
    "NotificationListOut",
    "UnreadCountOut",
)
