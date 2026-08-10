"""P2 finding, corrective-action, and review workflow API."""
from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...audit import add_event
from ...auth import Tenant, tenant_from_header
from ...business_notifications import (
    accepted_assignee_ids,
    add_notifications,
    recipient_ids_for_roles,
)
from ...business_timeline import add_timeline_event, maybe_complete_service_case
from ...business_workbench import (
    FINDING_SCOPE_STATUSES,
    can_register_finding,
    finding_allowed_actions,
    finding_collection_allowed_actions,
    next_finding_status,
)
from ...database import session_scope

router = APIRouter()

_FINDING_COLUMNS = (
    "finding.id, finding.enterprise_id, finding.service_case_id, "
    "finding.site_visit_id, finding.title, finding.description, "
    "finding.severity, finding.responsible_user_id, finding.due_at, "
    "finding.status, finding.created_by_user_id, finding.created_at, "
    "finding.updated_at, "
    "COALESCE(finding.service_case_id, visit.service_case_id) AS access_case_id"
)
_FINDING_FROM = (
    "FROM f1.finding AS finding "
    "LEFT JOIN f1.site_visit AS visit "
    "ON visit.enterprise_id = finding.enterprise_id "
    "AND visit.id = finding.site_visit_id"
)
_CORRECTIVE_COLUMNS = "id, revision, description, submitted_at"
_REVIEW_COLUMNS = "id, decision, comment, created_at"


class FindingCreate(BaseModel):
    service_case_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=8000)
    severity: Literal["low", "medium", "high", "critical"]
    responsible_user_id: uuid.UUID | None = None
    due_at: datetime


class FindingUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=8000)
    severity: Literal["low", "medium", "high", "critical"] | None = None
    responsible_user_id: uuid.UUID | None = None
    due_at: datetime | None = None


class CorrectiveActionCreate(BaseModel):
    description: str = Field(min_length=1, max_length=8000)


class FindingReviewCreate(BaseModel):
    decision: Literal["passed", "rejected"]
    comment: str = Field(max_length=4000)


class CorrectiveActionOut(BaseModel):
    id: uuid.UUID
    revision: int
    description: str
    submitted_at: datetime


class FindingReviewOut(BaseModel):
    id: uuid.UUID
    decision: str
    comment: str
    created_at: datetime


class FindingOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    service_case_id: uuid.UUID | None
    site_visit_id: uuid.UUID | None
    title: str
    description: str
    severity: str
    responsible_user_id: uuid.UUID | None
    due_at: datetime
    status: str
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[str]


class FindingDetailOut(FindingOut):
    corrective_actions: list[CorrectiveActionOut]
    reviews: list[FindingReviewOut]


class FindingListOut(BaseModel):
    items: list[FindingOut]
    allowed_actions: list[str]


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


async def _finding_row(
    session: AsyncSession,
    finding_id: uuid.UUID,
) -> Mapping[str, Any] | None:
    return (
        await session.execute(
            text(
                f"SELECT {_FINDING_COLUMNS} {_FINDING_FROM} "
                "WHERE finding.id = :finding_id"
            ),
            {"finding_id": finding_id},
        )
    ).mappings().first()


async def _lock_finding(session: AsyncSession, finding_id: uuid.UUID) -> None:
    found = (
        await session.execute(
            text("SELECT id FROM f1.finding WHERE id = :finding_id FOR UPDATE"),
            {"finding_id": finding_id},
        )
    ).scalar_one_or_none()
    if found is None:
        raise HTTPException(status_code=404, detail="FINDING_NOT_FOUND")


def _capacities_for(
    row: Mapping[str, Any],
    capacities_by_case: Mapping[uuid.UUID, tuple[str, ...]],
) -> tuple[str, ...]:
    access_case_id = row.get("access_case_id")
    if not isinstance(access_case_id, uuid.UUID):
        return ()
    return capacities_by_case.get(access_case_id, ())


def _finding_out(
    row: Mapping[str, Any],
    tenant: Tenant,
    capacities_by_case: Mapping[uuid.UUID, tuple[str, ...]],
) -> FindingOut:
    values = {key: value for key, value in row.items() if key != "access_case_id"}
    return FindingOut(
        **values,
        allowed_actions=finding_allowed_actions(
            tenant.role,
            str(row["status"]),
            _capacities_for(row, capacities_by_case),
        ),
    )


async def _detail_out(
    session: AsyncSession,
    row: Mapping[str, Any],
    tenant: Tenant,
    capacities_by_case: Mapping[uuid.UUID, tuple[str, ...]],
) -> FindingDetailOut:
    action_rows = (
        await session.execute(
            text(
                f"SELECT {_CORRECTIVE_COLUMNS} FROM f1.corrective_action "
                "WHERE finding_id = :finding_id ORDER BY revision"
            ),
            {"finding_id": row["id"]},
        )
    ).mappings().all()
    review_rows = (
        await session.execute(
            text(
                f"SELECT {_REVIEW_COLUMNS} FROM f1.finding_review "
                "WHERE finding_id = :finding_id ORDER BY created_at, id"
            ),
            {"finding_id": row["id"]},
        )
    ).mappings().all()
    finding = _finding_out(row, tenant, capacities_by_case)
    return FindingDetailOut(
        **finding.model_dump(),
        corrective_actions=[CorrectiveActionOut(**item) for item in action_rows],
        reviews=[FindingReviewOut(**item) for item in review_rows],
    )


async def _ensure_responsible_user(
    session: AsyncSession,
    tenant: Tenant,
    user_id: uuid.UUID | None,
) -> None:
    if user_id is None:
        return
    found = (
        await session.execute(
            text(
                "SELECT user_id FROM f1.enterprise_user "
                "WHERE enterprise_id = :enterprise_id AND user_id = :user_id"
            ),
            {"enterprise_id": tenant.enterprise_id, "user_id": user_id},
        )
    ).scalar_one_or_none()
    if found is None:
        raise HTTPException(status_code=404, detail="RESPONSIBLE_USER_NOT_FOUND")


@router.get("", response_model=FindingListOut)
async def list_findings(
    scope: Literal["all", "rectification", "review"] = Query(default="all"),
    service_case_id: uuid.UUID | None = Query(default=None),
    tenant: Tenant = Depends(tenant_from_header),
) -> FindingListOut:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await _current_user_id(session, tenant)
        capacities_by_case = await _capacity_map(session, actor_id)
        filters: list[str] = []
        parameters: dict[str, Any] = {}
        if scope != "all":
            filters.append("finding.status = ANY(:statuses)")
            parameters["statuses"] = list(FINDING_SCOPE_STATUSES[scope])
        if service_case_id is not None:
            filters.append(
                "COALESCE(finding.service_case_id, visit.service_case_id) = :case_id"
            )
            parameters["case_id"] = service_case_id
        where = " WHERE " + " AND ".join(filters) if filters else ""
        rows = (
            await session.execute(
                text(
                    f"SELECT {_FINDING_COLUMNS} {_FINDING_FROM}{where} "
                    "ORDER BY finding.due_at, finding.created_at, finding.id"
                ),
                parameters,
            )
        ).mappings().all()
    all_capacities = tuple(
        capacity
        for capacities in capacities_by_case.values()
        for capacity in capacities
    )
    return FindingListOut(
        items=[
            _finding_out(row, tenant, capacities_by_case)
            for row in rows
        ],
        allowed_actions=finding_collection_allowed_actions(
            tenant.role, all_capacities
        ),
    )


@router.post("", response_model=FindingDetailOut, status_code=201)
async def create_finding(
    body: FindingCreate,
    tenant: Tenant = Depends(tenant_from_header),
) -> FindingDetailOut:
    title = body.title.strip()
    description = body.description.strip()
    if not title or not description:
        raise HTTPException(status_code=422, detail="FINDING_TEXT_REQUIRED")
    finding_id = uuid.uuid4()
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await _current_user_id(session, tenant)
        case = (
            await session.execute(
                text(
                    "SELECT id, status FROM f1.service_case WHERE id = :case_id"
                ),
                {"case_id": body.service_case_id},
            )
        ).mappings().first()
        if case is None:
            raise HTTPException(status_code=404, detail="SERVICE_CASE_NOT_FOUND")
        if str(case["status"]) not in ("planned", "in_progress"):
            raise HTTPException(status_code=409, detail="FINDING_CASE_CLOSED")
        capacities_by_case = await _capacity_map(session, actor_id)
        capacities = capacities_by_case.get(body.service_case_id, ())
        if not can_register_finding(tenant.role, capacities):
            raise HTTPException(status_code=403, detail="FINDING_CREATE_FORBIDDEN")
        await _ensure_responsible_user(session, tenant, body.responsible_user_id)
        await session.execute(
            text(
                "INSERT INTO f1.finding ("
                "id, enterprise_id, service_case_id, site_visit_id, title, "
                "description, severity, responsible_user_id, due_at, status, "
                "created_by_user_id) VALUES ("
                ":id, :enterprise_id, :service_case_id, NULL, :title, "
                ":description, :severity, :responsible_user_id, :due_at, "
                "'open', :created_by_user_id)"
            ),
            {
                "id": finding_id,
                "enterprise_id": tenant.enterprise_id,
                "service_case_id": body.service_case_id,
                "title": title,
                "description": description,
                "severity": body.severity,
                "responsible_user_id": body.responsible_user_id,
                "due_at": body.due_at,
                "created_by_user_id": actor_id,
            },
        )
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "finding.create",
            "finding",
            str(finding_id),
        )
        timeline_event_id = await add_timeline_event(
            session,
            enterprise_id=tenant.enterprise_id,
            service_case_id=body.service_case_id,
            actor_user_id=actor_id,
            event_type="finding.created",
            subject_type="finding",
            subject_id=finding_id,
            status="open",
        )
        recipients = await recipient_ids_for_roles(
            session,
            enterprise_id=tenant.enterprise_id,
            roles=("enterprise_admin",),
        )
        if body.responsible_user_id is not None:
            recipients.add(body.responsible_user_id)
        await add_notifications(
            session,
            enterprise_id=tenant.enterprise_id,
            timeline_event_id=timeline_event_id,
            recipient_user_ids=recipients,
            actor_user_id=actor_id,
        )
        row = await _finding_row(session, finding_id)
        if row is None:
            raise HTTPException(status_code=404, detail="FINDING_NOT_FOUND")
        result = await _detail_out(session, row, tenant, capacities_by_case)
        await session.commit()
    return result


@router.get("/{finding_id}", response_model=FindingDetailOut)
async def get_finding(
    finding_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> FindingDetailOut:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await _current_user_id(session, tenant)
        row = await _finding_row(session, finding_id)
        if row is None:
            raise HTTPException(status_code=404, detail="FINDING_NOT_FOUND")
        capacities_by_case = await _capacity_map(session, actor_id)
        return await _detail_out(session, row, tenant, capacities_by_case)


@router.patch("/{finding_id}", response_model=FindingDetailOut)
async def update_finding(
    finding_id: uuid.UUID,
    body: FindingUpdate,
    tenant: Tenant = Depends(tenant_from_header),
) -> FindingDetailOut:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await _current_user_id(session, tenant)
        current = await _finding_row(session, finding_id)
        if current is None:
            raise HTTPException(status_code=404, detail="FINDING_NOT_FOUND")
        capacities_by_case = await _capacity_map(session, actor_id)
        allowed = finding_allowed_actions(
            tenant.role,
            str(current["status"]),
            _capacities_for(current, capacities_by_case),
        )
        if "edit" not in allowed:
            if current["status"] != "open":
                raise HTTPException(status_code=409, detail="FINDING_NOT_EDITABLE")
            raise HTTPException(status_code=403, detail="FINDING_EDIT_FORBIDDEN")
        updates = body.model_dump(exclude_unset=True)
        for key in ("title", "description"):
            if key in updates:
                value = updates[key]
                if value is None or not value.strip():
                    raise HTTPException(status_code=422, detail="FINDING_TEXT_REQUIRED")
                updates[key] = value.strip()
        if "severity" in updates and updates["severity"] is None:
            raise HTTPException(status_code=422, detail="FINDING_SEVERITY_REQUIRED")
        if "due_at" in updates and updates["due_at"] is None:
            raise HTTPException(status_code=422, detail="FINDING_DUE_AT_REQUIRED")
        if "responsible_user_id" in updates:
            await _ensure_responsible_user(
                session, tenant, updates["responsible_user_id"]
            )
        if updates:
            setters = ", ".join(f"{column} = :{column}" for column in updates)
            updates["finding_id"] = finding_id
            await session.execute(
                text(
                    f"UPDATE f1.finding SET {setters} WHERE id = :finding_id"
                ),
                updates,
            )
            await add_event(
                session,
                tenant.enterprise_id,
                tenant.sub,
                "finding.update",
                "finding",
                str(finding_id),
            )
            await add_timeline_event(
                session,
                enterprise_id=tenant.enterprise_id,
                service_case_id=current["access_case_id"],
                actor_user_id=actor_id,
                event_type="finding.updated",
                subject_type="finding",
                subject_id=finding_id,
                status=str(current["status"]),
            )
        row = await _finding_row(session, finding_id)
        if row is None:
            raise HTTPException(status_code=404, detail="FINDING_NOT_FOUND")
        result = await _detail_out(session, row, tenant, capacities_by_case)
        await session.commit()
    return result


async def _transition_finding(
    finding_id: uuid.UUID,
    action: str,
    tenant: Tenant,
) -> FindingDetailOut:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await _current_user_id(session, tenant)
        await _lock_finding(session, finding_id)
        current = await _finding_row(session, finding_id)
        if current is None:
            raise HTTPException(status_code=404, detail="FINDING_NOT_FOUND")
        capacities_by_case = await _capacity_map(session, actor_id)
        next_status = next_finding_status(str(current["status"]), action)
        if next_status is None:
            raise HTTPException(status_code=409, detail="FINDING_STATE_CONFLICT")
        allowed = finding_allowed_actions(
            tenant.role,
            str(current["status"]),
            _capacities_for(current, capacities_by_case),
        )
        if action not in allowed:
            raise HTTPException(status_code=403, detail="FINDING_ACTION_FORBIDDEN")
        changed = await session.execute(
            text(
                "UPDATE f1.finding SET status = :next_status "
                "WHERE id = :finding_id AND status = :current_status"
            ),
            {
                "next_status": next_status,
                "finding_id": finding_id,
                "current_status": current["status"],
            },
        )
        if changed.rowcount != 1:
            raise HTTPException(status_code=409, detail="FINDING_STATE_CONFLICT")
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            f"finding.{action}",
            "finding",
            str(finding_id),
        )
        await add_timeline_event(
            session,
            enterprise_id=tenant.enterprise_id,
            service_case_id=current["access_case_id"],
            actor_user_id=actor_id,
            event_type=f"finding.{action}",
            subject_type="finding",
            subject_id=finding_id,
            status=next_status,
        )
        if action == "close":
            await maybe_complete_service_case(
                session,
                enterprise_id=tenant.enterprise_id,
                service_case_id=current["access_case_id"],
                actor_user_id=actor_id,
                actor_sub=tenant.sub,
            )
        row = await _finding_row(session, finding_id)
        if row is None:
            raise HTTPException(status_code=404, detail="FINDING_NOT_FOUND")
        result = await _detail_out(session, row, tenant, capacities_by_case)
        await session.commit()
    return result


@router.post("/{finding_id}/start-rectification", response_model=FindingDetailOut)
async def start_rectification(
    finding_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> FindingDetailOut:
    return await _transition_finding(finding_id, "start_rectification", tenant)


@router.post(
    "/{finding_id}/corrective-actions",
    response_model=CorrectiveActionOut,
    status_code=201,
)
async def submit_corrective_action(
    finding_id: uuid.UUID,
    body: CorrectiveActionCreate,
    tenant: Tenant = Depends(tenant_from_header),
) -> CorrectiveActionOut:
    description = body.description.strip()
    if not description:
        raise HTTPException(status_code=422, detail="CORRECTIVE_ACTION_REQUIRED")
    action_id = uuid.uuid4()
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await _current_user_id(session, tenant)
        await _lock_finding(session, finding_id)
        current = await _finding_row(session, finding_id)
        if current is None:
            raise HTTPException(status_code=404, detail="FINDING_NOT_FOUND")
        capacities_by_case = await _capacity_map(session, actor_id)
        if next_finding_status(str(current["status"]), "submit_correction") is None:
            raise HTTPException(status_code=409, detail="FINDING_STATE_CONFLICT")
        allowed = finding_allowed_actions(
            tenant.role,
            str(current["status"]),
            _capacities_for(current, capacities_by_case),
        )
        if "submit_correction" not in allowed:
            raise HTTPException(status_code=403, detail="CORRECTION_SUBMIT_FORBIDDEN")
        revision = int(
            (
                await session.execute(
                    text(
                        "SELECT COALESCE(max(revision), 0) + 1 "
                        "FROM f1.corrective_action WHERE finding_id = :finding_id"
                    ),
                    {"finding_id": finding_id},
                )
            ).scalar_one()
        )
        action_row = (
            await session.execute(
                text(
                    "INSERT INTO f1.corrective_action ("
                    "id, enterprise_id, finding_id, revision, description, "
                    "submitted_by_user_id) VALUES ("
                    ":id, :enterprise_id, :finding_id, :revision, :description, "
                    f":submitted_by_user_id) RETURNING {_CORRECTIVE_COLUMNS}"
                ),
                {
                    "id": action_id,
                    "enterprise_id": tenant.enterprise_id,
                    "finding_id": finding_id,
                    "revision": revision,
                    "description": description,
                    "submitted_by_user_id": actor_id,
                },
            )
        ).mappings().one()
        changed = await session.execute(
            text(
                "UPDATE f1.finding SET status = 'submitted' "
                "WHERE id = :finding_id AND status = 'rectifying'"
            ),
            {"finding_id": finding_id},
        )
        if changed.rowcount != 1:
            raise HTTPException(status_code=409, detail="FINDING_STATE_CONFLICT")
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "finding.submit_correction",
            "finding",
            str(finding_id),
        )
        timeline_event_id = await add_timeline_event(
            session,
            enterprise_id=tenant.enterprise_id,
            service_case_id=current["access_case_id"],
            actor_user_id=actor_id,
            event_type="corrective_action.submitted",
            subject_type="corrective_action",
            subject_id=action_id,
            status="submitted",
        )
        recipients = await accepted_assignee_ids(
            session,
            service_case_id=current["access_case_id"],
            capacities=("consultant",),
        )
        recipients.update(
            await recipient_ids_for_roles(
                session,
                enterprise_id=tenant.enterprise_id,
                roles=("super_admin",),
            )
        )
        await add_notifications(
            session,
            enterprise_id=tenant.enterprise_id,
            timeline_event_id=timeline_event_id,
            recipient_user_ids=recipients,
            actor_user_id=actor_id,
        )
        result = CorrectiveActionOut(**action_row)
        await session.commit()
    return result


@router.post("/{finding_id}/start-review", response_model=FindingDetailOut)
async def start_review(
    finding_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> FindingDetailOut:
    return await _transition_finding(finding_id, "start_review", tenant)


@router.post(
    "/{finding_id}/reviews",
    response_model=FindingReviewOut,
    status_code=201,
)
async def review_finding(
    finding_id: uuid.UUID,
    body: FindingReviewCreate,
    tenant: Tenant = Depends(tenant_from_header),
) -> FindingReviewOut:
    comment = body.comment.strip()
    if body.decision == "rejected" and not comment:
        raise HTTPException(status_code=422, detail="REVIEW_COMMENT_REQUIRED")
    review_id = uuid.uuid4()
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await _current_user_id(session, tenant)
        await _lock_finding(session, finding_id)
        current = await _finding_row(session, finding_id)
        if current is None:
            raise HTTPException(status_code=404, detail="FINDING_NOT_FOUND")
        capacities_by_case = await _capacity_map(session, actor_id)
        review_action = "pass" if body.decision == "passed" else "reject"
        next_status = next_finding_status(str(current["status"]), review_action)
        if next_status is None:
            raise HTTPException(status_code=409, detail="FINDING_STATE_CONFLICT")
        allowed = finding_allowed_actions(
            tenant.role,
            str(current["status"]),
            _capacities_for(current, capacities_by_case),
        )
        if review_action not in allowed:
            raise HTTPException(status_code=403, detail="FINDING_REVIEW_FORBIDDEN")
        review_row = (
            await session.execute(
                text(
                    "INSERT INTO f1.finding_review ("
                    "id, enterprise_id, finding_id, decision, comment, reviewer_user_id"
                    ") VALUES ("
                    ":id, :enterprise_id, :finding_id, :decision, :comment, "
                    f":reviewer_user_id) RETURNING {_REVIEW_COLUMNS}"
                ),
                {
                    "id": review_id,
                    "enterprise_id": tenant.enterprise_id,
                    "finding_id": finding_id,
                    "decision": next_status,
                    "comment": comment,
                    "reviewer_user_id": actor_id,
                },
            )
        ).mappings().one()
        changed = await session.execute(
            text(
                "UPDATE f1.finding SET status = :next_status "
                "WHERE id = :finding_id AND status = 'reviewing'"
            ),
            {"next_status": next_status, "finding_id": finding_id},
        )
        if changed.rowcount != 1:
            raise HTTPException(status_code=409, detail="FINDING_STATE_CONFLICT")
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            f"finding.review_{review_action}",
            "finding",
            str(finding_id),
        )
        timeline_event_id = await add_timeline_event(
            session,
            enterprise_id=tenant.enterprise_id,
            service_case_id=current["access_case_id"],
            actor_user_id=actor_id,
            event_type=f"finding.review_{review_action}",
            subject_type="finding_review",
            subject_id=review_id,
            status=next_status,
        )
        recipients = await recipient_ids_for_roles(
            session,
            enterprise_id=tenant.enterprise_id,
            roles=("enterprise_admin",),
        )
        if current["responsible_user_id"] is not None:
            recipients.add(current["responsible_user_id"])
        await add_notifications(
            session,
            enterprise_id=tenant.enterprise_id,
            timeline_event_id=timeline_event_id,
            recipient_user_ids=recipients,
            actor_user_id=actor_id,
        )
        result = FindingReviewOut(**review_row)
        await session.commit()
    return result


@router.post("/{finding_id}/close", response_model=FindingDetailOut)
async def close_finding(
    finding_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> FindingDetailOut:
    return await _transition_finding(finding_id, "close", tenant)


__all__ = (
    "router",
    "FindingOut",
    "FindingDetailOut",
    "FindingListOut",
    "CorrectiveActionOut",
    "FindingReviewOut",
)
