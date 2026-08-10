"""P2 site-visit planning and execution API."""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...audit import add_event
from ...auth import Tenant, tenant_from_header
from ...business_timeline import add_timeline_event, maybe_complete_service_case
from ...business_workbench import (
    is_manager,
    next_site_visit_status,
    site_visit_allowed_actions,
)
from ...database import session_scope

router = APIRouter()

_VISIT_COLUMNS = (
    "id, enterprise_id, service_case_id, status, planned_start_at, "
    "planned_end_at, started_at, completed_at, created_by_user_id, "
    "created_at, updated_at"
)


class SiteVisitCreate(BaseModel):
    planned_start_at: datetime | None = None
    planned_end_at: datetime | None = None


class SiteVisitUpdate(BaseModel):
    planned_start_at: datetime | None = None
    planned_end_at: datetime | None = None


class SiteVisitOut(BaseModel):
    id: uuid.UUID
    status: str
    planned_start_at: datetime | None
    planned_end_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    allowed_actions: list[str]


class SiteVisitListOut(BaseModel):
    items: list[SiteVisitOut]
    allowed_actions: list[str]


def _validate_window(start: datetime | None, end: datetime | None) -> None:
    if start is not None and end is not None and end < start:
        raise HTTPException(status_code=422, detail="SITE_VISIT_WINDOW_INVALID")


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


async def _case_status(
    session: AsyncSession,
    case_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> str | None:
    suffix = " FOR UPDATE" if for_update else ""
    value = (
        await session.execute(
            text(
                "SELECT status FROM f1.service_case WHERE id = :case_id" + suffix
            ),
            {"case_id": case_id},
        )
    ).scalar_one_or_none()
    return None if value is None else str(value)


async def _accepted_capacities(
    session: AsyncSession,
    case_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> tuple[str, ...]:
    return tuple(
        str(capacity)
        for capacity in (
            await session.execute(
                text(
                    "SELECT capacity FROM f1.service_assignment "
                    "WHERE service_case_id = :case_id "
                    "AND assignee_user_id = :actor_id AND status = 'accepted' "
                    "ORDER BY capacity"
                ),
                {"case_id": case_id, "actor_id": actor_id},
            )
        ).scalars()
    )


async def _visit_row(
    session: AsyncSession,
    case_id: uuid.UUID,
    visit_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Mapping[str, Any] | None:
    suffix = " FOR UPDATE" if for_update else ""
    return (
        await session.execute(
            text(
                f"SELECT {_VISIT_COLUMNS} FROM f1.site_visit "
                "WHERE id = :visit_id AND service_case_id = :case_id" + suffix
            ),
            {"case_id": case_id, "visit_id": visit_id},
        )
    ).mappings().first()


def _visit_out(
    row: Mapping[str, Any],
    tenant: Tenant,
    capacities: tuple[str, ...],
) -> SiteVisitOut:
    return SiteVisitOut(
        id=row["id"],
        status=str(row["status"]),
        planned_start_at=row["planned_start_at"],
        planned_end_at=row["planned_end_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        allowed_actions=site_visit_allowed_actions(
            tenant.role, str(row["status"]), capacities
        ),
    )


@router.get("/{case_id}/site-visits", response_model=SiteVisitListOut)
async def list_site_visits(
    case_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> SiteVisitListOut:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await _current_user_id(session, tenant)
        status = await _case_status(session, case_id)
        if status is None:
            raise HTTPException(status_code=404, detail="SERVICE_CASE_NOT_FOUND")
        capacities = await _accepted_capacities(session, case_id, actor_id)
        rows = (
            await session.execute(
                text(
                    f"SELECT {_VISIT_COLUMNS} FROM f1.site_visit "
                    "WHERE service_case_id = :case_id "
                    "ORDER BY planned_start_at NULLS LAST, created_at, id"
                ),
                {"case_id": case_id},
            )
        ).mappings().all()
        return SiteVisitListOut(
            items=[_visit_out(row, tenant, capacities) for row in rows],
            allowed_actions=(
                ["create"]
                if is_manager(tenant.role) and status in ("planned", "in_progress")
                else []
            ),
        )


@router.post("/{case_id}/site-visits", response_model=SiteVisitOut, status_code=201)
async def create_site_visit(
    case_id: uuid.UUID,
    body: SiteVisitCreate,
    tenant: Tenant = Depends(tenant_from_header),
) -> SiteVisitOut:
    if not is_manager(tenant.role):
        raise HTTPException(status_code=403, detail="SITE_VISIT_PLAN_FORBIDDEN")
    _validate_window(body.planned_start_at, body.planned_end_at)
    visit_id = uuid.uuid4()
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await _current_user_id(session, tenant)
        case_status = await _case_status(session, case_id, for_update=True)
        if case_status is None:
            raise HTTPException(status_code=404, detail="SERVICE_CASE_NOT_FOUND")
        if case_status not in ("planned", "in_progress"):
            raise HTTPException(status_code=409, detail="SITE_VISIT_CASE_CLOSED")
        row = (
            await session.execute(
                text(
                    "INSERT INTO f1.site_visit ("
                    "id, enterprise_id, service_case_id, status, planned_start_at, "
                    "planned_end_at, created_by_user_id) VALUES ("
                    ":id, :enterprise_id, :case_id, 'planned', :planned_start_at, "
                    ":planned_end_at, :actor_id) "
                    f"RETURNING {_VISIT_COLUMNS}"
                ),
                {
                    "id": visit_id,
                    "enterprise_id": tenant.enterprise_id,
                    "case_id": case_id,
                    "planned_start_at": body.planned_start_at,
                    "planned_end_at": body.planned_end_at,
                    "actor_id": actor_id,
                },
            )
        ).mappings().one()
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "site_visit.create",
            "site_visit",
            str(visit_id),
        )
        await add_timeline_event(
            session,
            enterprise_id=tenant.enterprise_id,
            service_case_id=case_id,
            actor_user_id=actor_id,
            event_type="site_visit.planned",
            subject_type="site_visit",
            subject_id=visit_id,
            status="planned",
        )
        result = _visit_out(row, tenant, ())
        await session.commit()
    return result


@router.patch(
    "/{case_id}/site-visits/{visit_id}", response_model=SiteVisitOut
)
async def update_site_visit(
    case_id: uuid.UUID,
    visit_id: uuid.UUID,
    body: SiteVisitUpdate,
    tenant: Tenant = Depends(tenant_from_header),
) -> SiteVisitOut:
    if not is_manager(tenant.role):
        raise HTTPException(status_code=403, detail="SITE_VISIT_EDIT_FORBIDDEN")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await _current_user_id(session, tenant)
        case_status = await _case_status(session, case_id)
        if case_status is None:
            raise HTTPException(status_code=404, detail="SERVICE_CASE_NOT_FOUND")
        if case_status not in ("planned", "in_progress"):
            raise HTTPException(status_code=409, detail="SITE_VISIT_CASE_CLOSED")
        current = await _visit_row(session, case_id, visit_id, for_update=True)
        if current is None:
            raise HTTPException(status_code=404, detail="SITE_VISIT_NOT_FOUND")
        if str(current["status"]) != "planned":
            raise HTTPException(status_code=409, detail="SITE_VISIT_NOT_EDITABLE")
        updates = body.model_dump(exclude_unset=True)
        start = updates.get("planned_start_at", current["planned_start_at"])
        end = updates.get("planned_end_at", current["planned_end_at"])
        _validate_window(start, end)
        if updates:
            setters = ", ".join(f"{key} = :{key}" for key in updates)
            updates.update({"case_id": case_id, "visit_id": visit_id})
            row = (
                await session.execute(
                    text(
                        f"UPDATE f1.site_visit SET {setters} "
                        "WHERE id = :visit_id AND service_case_id = :case_id "
                        f"RETURNING {_VISIT_COLUMNS}"
                    ),
                    updates,
                )
            ).mappings().first()
            if row is None:
                raise HTTPException(status_code=404, detail="SITE_VISIT_NOT_FOUND")
            await add_event(
                session,
                tenant.enterprise_id,
                tenant.sub,
                "site_visit.update",
                "site_visit",
                str(visit_id),
            )
            await add_timeline_event(
                session,
                enterprise_id=tenant.enterprise_id,
                service_case_id=case_id,
                actor_user_id=actor_id,
                event_type="site_visit.rescheduled",
                subject_type="site_visit",
                subject_id=visit_id,
                status="planned",
            )
        else:
            row = current
        result = _visit_out(row, tenant, ())
        await session.commit()
    return result


async def _transition_site_visit(
    case_id: uuid.UUID,
    visit_id: uuid.UUID,
    action: Literal["start", "complete"],
    tenant: Tenant,
) -> SiteVisitOut:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await _current_user_id(session, tenant)
        case_status = await _case_status(session, case_id, for_update=True)
        if case_status is None:
            raise HTTPException(status_code=404, detail="SERVICE_CASE_NOT_FOUND")
        if case_status not in ("planned", "in_progress"):
            raise HTTPException(status_code=409, detail="SITE_VISIT_CASE_CLOSED")
        current = await _visit_row(session, case_id, visit_id, for_update=True)
        if current is None:
            raise HTTPException(status_code=404, detail="SITE_VISIT_NOT_FOUND")
        capacities = await _accepted_capacities(session, case_id, actor_id)
        allowed = site_visit_allowed_actions(
            tenant.role, str(current["status"]), capacities
        )
        public_action = f"{action}_visit"
        if public_action not in allowed:
            if next_site_visit_status(str(current["status"]), action) is None:
                raise HTTPException(status_code=409, detail="SITE_VISIT_STATE_CONFLICT")
            raise HTTPException(status_code=403, detail="SITE_VISIT_ACTION_FORBIDDEN")
        target = next_site_visit_status(str(current["status"]), action)
        if target is None:
            raise HTTPException(status_code=409, detail="SITE_VISIT_STATE_CONFLICT")
        event_at = (
            await session.execute(text("SELECT statement_timestamp()"))
        ).scalar_one()
        time_column = "started_at" if action == "start" else "completed_at"
        row = (
            await session.execute(
                text(
                    f"UPDATE f1.site_visit SET status = :target, "
                    f"{time_column} = :event_at "
                    "WHERE id = :visit_id AND service_case_id = :case_id "
                    "AND status = :current_status "
                    f"RETURNING {_VISIT_COLUMNS}"
                ),
                {
                    "target": target,
                    "event_at": event_at,
                    "visit_id": visit_id,
                    "case_id": case_id,
                    "current_status": current["status"],
                },
            )
        ).mappings().first()
        if row is None:
            raise HTTPException(status_code=409, detail="SITE_VISIT_STATE_CONFLICT")
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            f"site_visit.{action}",
            "site_visit",
            str(visit_id),
        )
        await add_timeline_event(
            session,
            enterprise_id=tenant.enterprise_id,
            service_case_id=case_id,
            actor_user_id=actor_id,
            event_type=(
                "site_visit.started" if action == "start" else "site_visit.completed"
            ),
            subject_type="site_visit",
            subject_id=visit_id,
            status=target,
            occurred_at=event_at,
        )
        if action == "start" and case_status == "planned":
            changed = await session.execute(
                text(
                    "UPDATE f1.service_case SET status = 'in_progress' "
                    "WHERE id = :case_id AND status = 'planned'"
                ),
                {"case_id": case_id},
            )
            if changed.rowcount != 1:
                raise HTTPException(status_code=409, detail="SERVICE_CASE_STATE_CONFLICT")
            await add_event(
                session,
                tenant.enterprise_id,
                tenant.sub,
                "service_case.auto_start",
                "service_case",
                str(case_id),
            )
            await add_timeline_event(
                session,
                enterprise_id=tenant.enterprise_id,
                service_case_id=case_id,
                actor_user_id=actor_id,
                event_type="service_case.started",
                subject_type="service_case",
                subject_id=case_id,
                status="in_progress",
                occurred_at=event_at,
            )
        if action == "complete":
            await maybe_complete_service_case(
                session,
                enterprise_id=tenant.enterprise_id,
                service_case_id=case_id,
                actor_user_id=actor_id,
                actor_sub=tenant.sub,
            )
        result = _visit_out(row, tenant, capacities)
        await session.commit()
    return result


@router.post(
    "/{case_id}/site-visits/{visit_id}/start", response_model=SiteVisitOut
)
async def start_site_visit(
    case_id: uuid.UUID,
    visit_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> SiteVisitOut:
    return await _transition_site_visit(case_id, visit_id, "start", tenant)


@router.post(
    "/{case_id}/site-visits/{visit_id}/complete", response_model=SiteVisitOut
)
async def complete_site_visit(
    case_id: uuid.UUID,
    visit_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> SiteVisitOut:
    return await _transition_site_visit(case_id, visit_id, "complete", tenant)


__all__ = ("router", "SiteVisitOut", "SiteVisitListOut")
