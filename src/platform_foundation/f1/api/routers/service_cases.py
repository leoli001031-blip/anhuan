"""P2 service-case and personnel-assignment API."""
from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...audit import add_event
from ...auth import Tenant, tenant_from_header
from ...business_notifications import add_notifications
from ...business_timeline import add_timeline_event
from ...business_workbench import (
    allowed_capacities,
    assignment_allowed_actions,
    capacity_is_allowed,
    case_allowed_actions,
    is_manager,
    list_allowed_actions,
    next_assignment_status,
    site_visit_allowed_actions,
)
from ...database import session_scope

router = APIRouter()

_BASE_CASE_COLUMNS = (
    "id, enterprise_id, plant_id, {client_account_id}, title, description, "
    "service_type, status, planned_start_at, planned_end_at, "
    "created_by_user_id, created_at, updated_at"
)
_ASSIGNMENT_COLUMNS = (
    "id, enterprise_id, service_case_id, assignee_user_id, assigned_by_user_id, "
    "capacity, status, assigned_at, responded_at, revoked_at"
)


class ServiceCaseCreate(BaseModel):
    plant_id: uuid.UUID | None = None
    client_account_id: uuid.UUID | None = None
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    service_type: str = Field(min_length=1, max_length=64)
    status: Literal["planned"] = "planned"
    planned_start_at: datetime | None = None
    planned_end_at: datetime | None = None


class ServiceCaseUpdate(BaseModel):
    plant_id: uuid.UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    service_type: str | None = Field(default=None, min_length=1, max_length=64)
    planned_start_at: datetime | None = None
    planned_end_at: datetime | None = None


class ServiceAssignmentCreate(BaseModel):
    assignee_user_id: uuid.UUID
    capacity: Literal["employee", "consultant", "partner"]


class ServiceAssignmentOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    service_case_id: uuid.UUID
    assignee_user_id: uuid.UUID
    assigned_by_user_id: uuid.UUID
    capacity: str
    status: str
    assigned_at: datetime
    responded_at: datetime | None
    revoked_at: datetime | None
    allowed_actions: list[str]


class ServiceCaseOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    plant_id: uuid.UUID | None
    client_account_id: uuid.UUID | None
    title: str
    description: str | None
    service_type: str
    status: str
    planned_start_at: datetime | None
    planned_end_at: datetime | None
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[str]


class ServiceCaseListOut(BaseModel):
    items: list[ServiceCaseOut]
    allowed_actions: list[str]


class ClientServiceCaseOut(BaseModel):
    id: uuid.UUID
    title: str
    service_type: str
    status: str
    planned_start_at: datetime | None
    planned_end_at: datetime | None
    assigned: bool
    updated_at: datetime


class ClientServiceCaseListOut(BaseModel):
    items: list[ClientServiceCaseOut]
    allowed_actions: list[str] = []


class AssignmentCandidateOut(BaseModel):
    user_id: uuid.UUID
    membership_role: str
    allowed_capacities: list[str]


class SiteVisitCompactOut(BaseModel):
    id: uuid.UUID
    status: str
    planned_start_at: datetime | None
    planned_end_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    allowed_actions: list[str]


class FindingCompactOut(BaseModel):
    id: uuid.UUID
    title: str
    severity: str
    status: str
    due_at: datetime


class BusinessTimelineOut(BaseModel):
    id: uuid.UUID
    event_type: str
    subject_type: str
    subject_id: uuid.UUID
    status: str | None
    actor_user_id: uuid.UUID
    occurred_at: datetime


class ServiceCaseDetailOut(ServiceCaseOut):
    assignments: list[ServiceAssignmentOut]
    site_visits: list[SiteVisitCompactOut]
    findings: list[FindingCompactOut]
    finding_summary: dict[str, int]
    timeline: list[BusinessTimelineOut]


def _require_manager(tenant: Tenant) -> None:
    if not is_manager(tenant.role):
        raise HTTPException(status_code=403, detail="P2_MANAGER_REQUIRED")


def _aeco_client_ops_enabled() -> bool:
    return (
        os.environ.get("F1_LOCAL_ENGINEERING") == "1"
        and os.environ.get("F1_MATERIAL_ANALYSIS_REPORT_LOCAL") == "1"
    )


def _case_columns() -> str:
    # f1_0020 is deliberately exclusive to the analysis-report candidate;
    # the default f1_0014 runtime still receives the legacy nullable shape.
    client = (
        "client_account_id"
        if _aeco_client_ops_enabled()
        else "NULL::uuid AS client_account_id"
    )
    return _BASE_CASE_COLUMNS.format(client_account_id=client)


def _validate_window(start: datetime | None, end: datetime | None) -> None:
    if start is not None and end is not None and end < start:
        raise HTTPException(status_code=422, detail="SERVICE_WINDOW_INVALID")


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


async def _ensure_plant(
    session: AsyncSession,
    tenant: Tenant,
    plant_id: uuid.UUID | None,
) -> None:
    if plant_id is None:
        return
    found = (
        await session.execute(
            text(
                "SELECT id FROM f1.plant "
                "WHERE enterprise_id = :enterprise_id AND id = :plant_id"
            ),
            {"enterprise_id": tenant.enterprise_id, "plant_id": plant_id},
        )
    ).scalar_one_or_none()
    if found is None:
        raise HTTPException(status_code=404, detail="PLANT_NOT_FOUND")


async def _ensure_client_account(
    session: AsyncSession,
    tenant: Tenant,
    client_account_id: uuid.UUID | None,
) -> None:
    if client_account_id is None:
        return
    if not _aeco_client_ops_enabled():
        raise HTTPException(status_code=409, detail="CLIENT_ACCOUNT_BINDING_UNAVAILABLE")
    found = (
        await session.execute(
            text(
                "SELECT 1 FROM f1.crm_account "
                "WHERE enterprise_id = :enterprise_id "
                "AND id = :client_account_id "
                "AND stage IN ('lead','active','dormant')"
            ),
            {
                "enterprise_id": tenant.enterprise_id,
                "client_account_id": client_account_id,
            },
        )
    ).first()
    if found is None:
        raise HTTPException(status_code=404, detail="CLIENT_ACCOUNT_NOT_FOUND")


async def _case_row(
    session: AsyncSession,
    case_id: uuid.UUID,
) -> Mapping[str, Any] | None:
    return (
        await session.execute(
            text(
                f"SELECT {_case_columns()} FROM f1.service_case WHERE id = :case_id"
            ),
            {"case_id": case_id},
        )
    ).mappings().first()


async def _assignment_rows(
    session: AsyncSession,
    case_id: uuid.UUID,
) -> list[Mapping[str, Any]]:
    return list(
        (
            await session.execute(
                text(
                    f"SELECT {_ASSIGNMENT_COLUMNS} FROM f1.service_assignment "
                    "WHERE service_case_id = :case_id "
                    "ORDER BY assigned_at, id"
                ),
                {"case_id": case_id},
            )
        ).mappings().all()
    )


def _case_out(row: Mapping[str, Any], tenant: Tenant) -> ServiceCaseOut:
    return ServiceCaseOut(
        **dict(row),
        allowed_actions=case_allowed_actions(tenant.role, str(row["status"])),
    )


def _assignment_out(
    row: Mapping[str, Any],
    tenant: Tenant,
    actor_id: uuid.UUID,
) -> ServiceAssignmentOut:
    return ServiceAssignmentOut(
        **dict(row),
        allowed_actions=assignment_allowed_actions(
            tenant.role,
            str(row["status"]),
            is_assignee=row["assignee_user_id"] == actor_id,
        ),
    )


async def _detail_out(
    session: AsyncSession,
    row: Mapping[str, Any],
    tenant: Tenant,
    actor_id: uuid.UUID,
) -> ServiceCaseDetailOut:
    assignment_rows = await _assignment_rows(session, row["id"])
    assignments = [
        _assignment_out(item, tenant, actor_id) for item in assignment_rows
    ]
    actor_capacities = tuple(
        str(item["capacity"])
        for item in assignment_rows
        if item["assignee_user_id"] == actor_id and item["status"] == "accepted"
    )
    visit_rows = (
        await session.execute(
            text(
                "SELECT id, status, planned_start_at, planned_end_at, "
                "started_at, completed_at FROM f1.site_visit "
                "WHERE service_case_id = :case_id "
                "ORDER BY planned_start_at NULLS LAST, created_at, id"
            ),
            {"case_id": row["id"]},
        )
    ).mappings().all()
    finding_rows = (
        await session.execute(
            text(
                "SELECT finding.id, finding.title, finding.severity, "
                "finding.status, finding.due_at FROM f1.finding AS finding "
                "LEFT JOIN f1.site_visit AS visit "
                "ON visit.enterprise_id = finding.enterprise_id "
                "AND visit.id = finding.site_visit_id "
                "WHERE COALESCE(finding.service_case_id, visit.service_case_id) "
                "= :case_id ORDER BY finding.due_at, finding.created_at, finding.id"
            ),
            {"case_id": row["id"]},
        )
    ).mappings().all()
    timeline_rows = (
        await session.execute(
            text(
                "SELECT id, event_type, subject_type, subject_id, status, "
                "actor_user_id, occurred_at FROM f1.business_timeline "
                "WHERE service_case_id = :case_id ORDER BY occurred_at, id"
            ),
            {"case_id": row["id"]},
        )
    ).mappings().all()
    finding_summary = {
        status: 0
        for status in (
            "open",
            "rectifying",
            "submitted",
            "reviewing",
            "passed",
            "rejected",
            "closed",
        )
    }
    for finding in finding_rows:
        finding_summary[str(finding["status"])] += 1
    finding_summary["total"] = len(finding_rows)
    case = _case_out(row, tenant)
    return ServiceCaseDetailOut(
        **case.model_dump(),
        assignments=assignments,
        site_visits=[
            SiteVisitCompactOut(
                **dict(item),
                allowed_actions=site_visit_allowed_actions(
                    tenant.role, str(item["status"]), actor_capacities
                ),
            )
            for item in visit_rows
        ],
        findings=[FindingCompactOut(**item) for item in finding_rows],
        finding_summary=finding_summary,
        timeline=[BusinessTimelineOut(**item) for item in timeline_rows],
    )


async def _list_cases(
    tenant: Tenant,
    *,
    mine: bool,
    client_account_id: uuid.UUID | None = None,
) -> ServiceCaseListOut:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await _current_user_id(session, tenant)
        clauses: list[str] = []
        parameters: dict[str, Any] = {}
        if mine:
            clauses.append(
                "EXISTS ("
                "SELECT 1 FROM f1.service_assignment AS assignment "
                "WHERE assignment.service_case_id = service_case.id "
                "AND assignment.assignee_user_id = :actor_id "
                "AND assignment.status IN ('pending','accepted'))"
            )
            parameters["actor_id"] = actor_id
        if client_account_id is not None:
            _require_manager(tenant)
            await _ensure_client_account(session, tenant, client_account_id)
            clauses.append("client_account_id = :client_account_id")
            parameters["client_account_id"] = client_account_id
        where_clause = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = (
            await session.execute(
                text(
                    f"SELECT {_case_columns()} FROM f1.service_case "
                    f"{where_clause} ORDER BY updated_at DESC, id"
                ),
                parameters,
            )
        ).mappings().all()
    return ServiceCaseListOut(
        items=[_case_out(row, tenant) for row in rows],
        allowed_actions=[] if mine else list_allowed_actions(tenant.role),
    )


@router.get("", response_model=ServiceCaseListOut)
async def list_service_cases(
    client_account_id: uuid.UUID | None = Query(default=None),
    tenant: Tenant = Depends(tenant_from_header),
) -> ServiceCaseListOut:
    return await _list_cases(
        tenant,
        mine=False,
        client_account_id=client_account_id,
    )


@router.get("/portal", response_model=ClientServiceCaseListOut)
async def list_client_service_cases(
    tenant: Tenant = Depends(tenant_from_header),
) -> ClientServiceCaseListOut:
    """Return only the audience-bound, client-safe service summary contract."""
    if (
        not _aeco_client_ops_enabled()
        or tenant.role in {"super_admin", "enterprise_admin"}
    ):
        raise HTTPException(status_code=404, detail="SERVICE_CASES_NOT_FOUND")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id,title,service_type,status,planned_start_at,"
                    "planned_end_at,assigned,updated_at "
                    "FROM f1.aeco_client_service_cases()"
                )
            )
        ).mappings().all()
    return ClientServiceCaseListOut(
        items=[ClientServiceCaseOut(**dict(row)) for row in rows],
        allowed_actions=[],
    )


@router.post("", response_model=ServiceCaseDetailOut, status_code=201)
async def create_service_case(
    body: ServiceCaseCreate,
    tenant: Tenant = Depends(tenant_from_header),
) -> ServiceCaseDetailOut:
    _require_manager(tenant)
    title = body.title.strip()
    service_type = body.service_type.strip()
    if not title or not service_type:
        raise HTTPException(status_code=422, detail="SERVICE_CASE_TEXT_REQUIRED")
    _validate_window(body.planned_start_at, body.planned_end_at)
    case_id = uuid.uuid4()
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await _current_user_id(session, tenant)
        await _ensure_plant(session, tenant, body.plant_id)
        await _ensure_client_account(session, tenant, body.client_account_id)
        row = (
            await session.execute(
                text(
                    "INSERT INTO f1.service_case ("
                    "id, enterprise_id, plant_id, "
                    + ("client_account_id, " if _aeco_client_ops_enabled() else "")
                    + "title, "
                    "description, service_type, "
                    "status, planned_start_at, planned_end_at, created_by_user_id"
                    ") VALUES ("
                    ":id, :enterprise_id, :plant_id, "
                    + (":client_account_id, " if _aeco_client_ops_enabled() else "")
                    + ":title, :description, :service_type, "
                    ":status, :planned_start_at, :planned_end_at, :created_by_user_id"
                    f") RETURNING {_case_columns()}"
                ),
                {
                    "id": case_id,
                    "enterprise_id": tenant.enterprise_id,
                    "plant_id": body.plant_id,
                    "client_account_id": body.client_account_id,
                    "title": title,
                    "description": body.description,
                    "service_type": service_type,
                    "status": body.status,
                    "planned_start_at": body.planned_start_at,
                    "planned_end_at": body.planned_end_at,
                    "created_by_user_id": actor_id,
                },
            )
        ).mappings().one()
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "service_case.create",
            "service_case",
            str(case_id),
        )
        await add_timeline_event(
            session,
            enterprise_id=tenant.enterprise_id,
            service_case_id=case_id,
            actor_user_id=actor_id,
            event_type="service_case.created",
            subject_type="service_case",
            subject_id=case_id,
            status="planned",
        )
        result = await _detail_out(session, row, tenant, actor_id)
        await session.commit()
    return result


@router.get("/mine", response_model=ServiceCaseListOut)
async def list_my_service_cases(
    tenant: Tenant = Depends(tenant_from_header),
) -> ServiceCaseListOut:
    return await _list_cases(tenant, mine=True)


@router.get(
    "/assignment-candidates",
    response_model=list[AssignmentCandidateOut],
)
async def list_assignment_candidates(
    tenant: Tenant = Depends(tenant_from_header),
) -> list[AssignmentCandidateOut]:
    _require_manager(tenant)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT user_id, role FROM f1.enterprise_user "
                    "WHERE enterprise_id = :enterprise_id "
                    "AND role IN ('plant_admin','auditor','partner') "
                    "ORDER BY role, user_id"
                ),
                {"enterprise_id": tenant.enterprise_id},
            )
        ).mappings().all()
    return [
        AssignmentCandidateOut(
            user_id=row["user_id"],
            membership_role=row["role"],
            allowed_capacities=allowed_capacities(str(row["role"])),
        )
        for row in rows
    ]


@router.get("/{case_id}", response_model=ServiceCaseDetailOut)
async def get_service_case(
    case_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> ServiceCaseDetailOut:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await _current_user_id(session, tenant)
        row = await _case_row(session, case_id)
        if row is None:
            raise HTTPException(status_code=404, detail="SERVICE_CASE_NOT_FOUND")
        return await _detail_out(session, row, tenant, actor_id)


@router.patch("/{case_id}", response_model=ServiceCaseDetailOut)
async def update_service_case(
    case_id: uuid.UUID,
    body: ServiceCaseUpdate,
    tenant: Tenant = Depends(tenant_from_header),
) -> ServiceCaseDetailOut:
    _require_manager(tenant)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await _current_user_id(session, tenant)
        current = await _case_row(session, case_id)
        if current is None:
            raise HTTPException(status_code=404, detail="SERVICE_CASE_NOT_FOUND")
        if "edit" not in case_allowed_actions(tenant.role, str(current["status"])):
            raise HTTPException(status_code=409, detail="SERVICE_CASE_NOT_EDITABLE")
        updates = body.model_dump(exclude_unset=True)
        if "title" in updates:
            if updates["title"] is None or not updates["title"].strip():
                raise HTTPException(status_code=422, detail="SERVICE_CASE_TEXT_REQUIRED")
            updates["title"] = updates["title"].strip()
        if "service_type" in updates:
            if updates["service_type"] is None or not updates["service_type"].strip():
                raise HTTPException(status_code=422, detail="SERVICE_CASE_TEXT_REQUIRED")
            updates["service_type"] = updates["service_type"].strip()
        if "plant_id" in updates:
            await _ensure_plant(session, tenant, updates["plant_id"])
        start = updates.get("planned_start_at", current["planned_start_at"])
        end = updates.get("planned_end_at", current["planned_end_at"])
        _validate_window(start, end)
        if updates:
            setters = ", ".join(f"{column} = :{column}" for column in updates)
            updates["case_id"] = case_id
            row = (
                await session.execute(
                    text(
                        f"UPDATE f1.service_case SET {setters} "
                        f"WHERE id = :case_id RETURNING {_case_columns()}"
                    ),
                    updates,
                )
            ).mappings().first()
            if row is None:
                raise HTTPException(status_code=404, detail="SERVICE_CASE_NOT_FOUND")
            await add_event(
                session,
                tenant.enterprise_id,
                tenant.sub,
                "service_case.update",
                "service_case",
                str(case_id),
            )
            await add_timeline_event(
                session,
                enterprise_id=tenant.enterprise_id,
                service_case_id=case_id,
                actor_user_id=actor_id,
                event_type="service_case.updated",
                subject_type="service_case",
                subject_id=case_id,
                status=str(row["status"]),
            )
        else:
            row = current
        result = await _detail_out(session, row, tenant, actor_id)
        await session.commit()
    return result


@router.post("/{case_id}/close", response_model=ServiceCaseDetailOut)
async def close_service_case(
    case_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> ServiceCaseDetailOut:
    _require_manager(tenant)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await _current_user_id(session, tenant)
        current = (
            await session.execute(
                text(
                    f"SELECT {_case_columns()} FROM f1.service_case "
                    "WHERE id = :case_id"
                ),
                {"case_id": case_id},
            )
        ).mappings().first()
        if current is None:
            raise HTTPException(status_code=404, detail="SERVICE_CASE_NOT_FOUND")
        if "close" not in case_allowed_actions(tenant.role, str(current["status"])):
            raise HTTPException(status_code=409, detail="SERVICE_CASE_NOT_CLOSABLE")
        row = (
            await session.execute(
                text(
                    "UPDATE f1.service_case SET status = 'closed' "
                    "WHERE id = :case_id AND status = 'completed' "
                    f"RETURNING {_case_columns()}"
                ),
                {"case_id": case_id},
            )
        ).mappings().first()
        if row is None:
            raise HTTPException(status_code=409, detail="SERVICE_CASE_STATE_CONFLICT")
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "service_case.close",
            "service_case",
            str(case_id),
        )
        await add_timeline_event(
            session,
            enterprise_id=tenant.enterprise_id,
            service_case_id=case_id,
            actor_user_id=actor_id,
            event_type="service_case.closed",
            subject_type="service_case",
            subject_id=case_id,
            status="closed",
        )
        result = await _detail_out(session, row, tenant, actor_id)
        await session.commit()
    return result


@router.post(
    "/{case_id}/assignments",
    response_model=ServiceAssignmentOut,
    status_code=201,
)
async def create_service_assignment(
    case_id: uuid.UUID,
    body: ServiceAssignmentCreate,
    tenant: Tenant = Depends(tenant_from_header),
) -> ServiceAssignmentOut:
    _require_manager(tenant)
    assignment_id = uuid.uuid4()
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await _current_user_id(session, tenant)
        case = await _case_row(session, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="SERVICE_CASE_NOT_FOUND")
        if "assign" not in case_allowed_actions(tenant.role, str(case["status"])):
            raise HTTPException(status_code=409, detail="SERVICE_CASE_NOT_ASSIGNABLE")
        membership_role = (
            await session.execute(
                text(
                    "SELECT role FROM f1.enterprise_user "
                    "WHERE enterprise_id = :enterprise_id AND user_id = :user_id"
                ),
                {
                    "enterprise_id": tenant.enterprise_id,
                    "user_id": body.assignee_user_id,
                },
            )
        ).scalar_one_or_none()
        if membership_role is None:
            raise HTTPException(status_code=404, detail="ASSIGNEE_NOT_FOUND")
        if not capacity_is_allowed(str(membership_role), body.capacity):
            raise HTTPException(status_code=422, detail="ASSIGNMENT_CAPACITY_INVALID")
        active = (
            await session.execute(
                text(
                    "SELECT id FROM f1.service_assignment "
                    "WHERE service_case_id = :case_id "
                    "AND assignee_user_id = :assignee_user_id "
                    "AND capacity = :capacity "
                    "AND status IN ('pending','accepted')"
                ),
                {
                    "case_id": case_id,
                    "assignee_user_id": body.assignee_user_id,
                    "capacity": body.capacity,
                },
            )
        ).scalar_one_or_none()
        if active is not None:
            raise HTTPException(status_code=409, detail="ACTIVE_ASSIGNMENT_EXISTS")
        try:
            row = (
                await session.execute(
                    text(
                        "INSERT INTO f1.service_assignment ("
                        "id, enterprise_id, service_case_id, assignee_user_id, "
                        "assigned_by_user_id, capacity, status"
                        ") VALUES ("
                        ":id, :enterprise_id, :service_case_id, :assignee_user_id, "
                        ":assigned_by_user_id, :capacity, 'pending'"
                        f") RETURNING {_ASSIGNMENT_COLUMNS}"
                    ),
                    {
                        "id": assignment_id,
                        "enterprise_id": tenant.enterprise_id,
                        "service_case_id": case_id,
                        "assignee_user_id": body.assignee_user_id,
                        "assigned_by_user_id": actor_id,
                        "capacity": body.capacity,
                    },
                )
            ).mappings().one()
        except IntegrityError:
            raise HTTPException(status_code=409, detail="ASSIGNMENT_CONFLICT") from None
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "service_assignment.create",
            "service_assignment",
            str(assignment_id),
        )
        timeline_event_id = await add_timeline_event(
            session,
            enterprise_id=tenant.enterprise_id,
            service_case_id=case_id,
            actor_user_id=actor_id,
            event_type="service_assignment.created",
            subject_type="service_assignment",
            subject_id=assignment_id,
            status="pending",
        )
        await add_notifications(
            session,
            enterprise_id=tenant.enterprise_id,
            timeline_event_id=timeline_event_id,
            recipient_user_ids=(body.assignee_user_id,),
            actor_user_id=actor_id,
        )
        result = _assignment_out(row, tenant, actor_id)
        await session.commit()
    return result


async def _change_assignment(
    case_id: uuid.UUID,
    assignment_id: uuid.UUID,
    action: Literal["accept", "reject", "revoke"],
    tenant: Tenant,
) -> ServiceAssignmentOut:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await _current_user_id(session, tenant)
        case = await _case_row(session, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="SERVICE_CASE_NOT_FOUND")
        row = (
            await session.execute(
                text(
                    f"SELECT {_ASSIGNMENT_COLUMNS} FROM f1.service_assignment "
                    "WHERE id = :assignment_id AND service_case_id = :case_id"
                ),
                {"assignment_id": assignment_id, "case_id": case_id},
            )
        ).mappings().first()
        if row is None:
            raise HTTPException(status_code=404, detail="SERVICE_ASSIGNMENT_NOT_FOUND")
        is_assignee = row["assignee_user_id"] == actor_id
        if action in ("accept", "reject"):
            if not is_assignee:
                raise HTTPException(
                    status_code=403, detail="ASSIGNMENT_RESPONSE_FORBIDDEN"
                )
            timestamp_column = "responded_at"
        else:
            if not is_manager(tenant.role):
                raise HTTPException(status_code=403, detail="ASSIGNMENT_REVOKE_FORBIDDEN")
            timestamp_column = "revoked_at"
        next_status = next_assignment_status(str(row["status"]), action)
        if next_status is None:
            raise HTTPException(status_code=409, detail="ASSIGNMENT_STATE_CONFLICT")
        event_at = (
            await session.execute(text("SELECT statement_timestamp()"))
        ).scalar_one()
        await add_timeline_event(
            session,
            enterprise_id=tenant.enterprise_id,
            service_case_id=case_id,
            actor_user_id=actor_id,
            event_type=f"service_assignment.{action}",
            subject_type="service_assignment",
            subject_id=assignment_id,
            status=next_status,
            occurred_at=event_at,
        )
        changed = await session.execute(
            text(
                f"UPDATE f1.service_assignment SET status = :next_status, "
                f"{timestamp_column} = :event_at "
                "WHERE id = :assignment_id AND service_case_id = :case_id "
                "AND status = :previous_status"
            ),
            {
                "next_status": next_status,
                "event_at": event_at,
                "assignment_id": assignment_id,
                "case_id": case_id,
                "previous_status": row["status"],
            },
        )
        if changed.rowcount != 1:
            raise HTTPException(status_code=409, detail="ASSIGNMENT_STATE_CONFLICT")
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            f"service_assignment.{action}",
            "service_assignment",
            str(assignment_id),
        )
        updated = dict(row)
        updated["status"] = next_status
        updated[timestamp_column] = event_at
        result = _assignment_out(updated, tenant, actor_id)
        await session.commit()
    return result


@router.post(
    "/{case_id}/assignments/{assignment_id}/accept",
    response_model=ServiceAssignmentOut,
)
async def accept_service_assignment(
    case_id: uuid.UUID,
    assignment_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> ServiceAssignmentOut:
    return await _change_assignment(case_id, assignment_id, "accept", tenant)


@router.post(
    "/{case_id}/assignments/{assignment_id}/reject",
    response_model=ServiceAssignmentOut,
)
async def reject_service_assignment(
    case_id: uuid.UUID,
    assignment_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> ServiceAssignmentOut:
    return await _change_assignment(case_id, assignment_id, "reject", tenant)


@router.post(
    "/{case_id}/assignments/{assignment_id}/revoke",
    response_model=ServiceAssignmentOut,
)
async def revoke_service_assignment(
    case_id: uuid.UUID,
    assignment_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> ServiceAssignmentOut:
    return await _change_assignment(case_id, assignment_id, "revoke", tenant)


__all__ = (
    "router",
    "ServiceCaseListOut",
    "ServiceCaseOut",
    "ServiceCaseDetailOut",
    "ServiceAssignmentOut",
    "AssignmentCandidateOut",
    "SiteVisitCompactOut",
    "FindingCompactOut",
    "BusinessTimelineOut",
)
