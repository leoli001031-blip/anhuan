"""P7 local, manual production-rehearsal API."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ...auth import Tenant, tenant_from_header
from ...features.p7 import dashboard, plans, runs


router = APIRouter()

CheckCategory = Literal[
    "service", "dependency", "backup", "restore", "security", "rollback"
]


class RehearsalPlanCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class RehearsalPlanOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    name: str
    status: str
    execution_mode: str
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[str]
    boundaries: list[str]


class RehearsalPlanListOut(BaseModel):
    items: list[RehearsalPlanOut]
    allowed_actions: list[str]
    boundaries: list[str]


class RehearsalCheckCreate(BaseModel):
    check_key: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    category: CheckCategory
    label: str = Field(min_length=1, max_length=200)
    sequence_no: int = Field(ge=1, le=10000)
    required: bool = True
    enabled: bool = True


class RehearsalCheckUpdate(BaseModel):
    category: CheckCategory | None = None
    label: str | None = Field(default=None, min_length=1, max_length=200)
    sequence_no: int | None = Field(default=None, ge=1, le=10000)
    required: bool | None = None
    enabled: bool | None = None


class RehearsalCheckOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    plan_id: uuid.UUID
    check_key: str
    category: str
    label: str
    sequence_no: int
    required: bool
    enabled: bool
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[str]
    boundaries: list[str]


class RehearsalRunOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    plan_id: uuid.UUID
    status: str
    total_count: int
    passed_count: int
    failed_count: int
    blocked_count: int
    pending_count: int
    rollback_required: bool
    created_by_user_id: uuid.UUID
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    allowed_actions: list[str]
    boundaries: list[str]


class RehearsalPlanDetailOut(RehearsalPlanOut):
    checks: list[RehearsalCheckOut]
    recent_runs: list[RehearsalRunOut]


class RehearsalResultOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    run_id: uuid.UUID
    check_id: uuid.UUID
    check_key: str
    category: str
    label: str
    sequence_no: int
    required: bool
    status: str
    reason_code: str | None
    evidence_sha256: str | None
    recorded_by_user_id: uuid.UUID | None
    recorded_at: datetime | None
    created_at: datetime
    allowed_actions: list[str]
    boundaries: list[str]


class RehearsalRunDetailOut(RehearsalRunOut):
    results: list[RehearsalResultOut]


class RehearsalResultRecord(BaseModel):
    status: Literal["passed", "failed", "blocked"]
    reason_code: Literal[
        "MANUAL_CHECK_PASSED",
        "MANUAL_CHECK_FAILED",
        "MANUAL_CHECK_BLOCKED",
    ]
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PlanCounts(BaseModel):
    total: int
    draft: int
    active: int
    archived: int


class RunCounts(BaseModel):
    total: int
    planned: int
    running: int
    passed: int
    failed: int
    cancelled: int


class ResultCounts(BaseModel):
    total: int
    pending: int
    passed: int
    failed: int
    blocked: int


class RehearsalDashboardOut(BaseModel):
    rehearsal_label: str
    plan_counts: PlanCounts
    run_counts: RunCounts
    result_counts: ResultCounts
    rollback_required_count: int
    pending_plans: list[RehearsalPlanOut]
    recent_runs: list[RehearsalRunOut]
    allowed_actions: list[str]
    boundaries: list[str]


@router.get("/plans", response_model=RehearsalPlanListOut)
async def list_rehearsal_plans(
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await plans.list_plans(tenant)


@router.post("/plans", response_model=RehearsalPlanDetailOut, status_code=201)
async def create_rehearsal_plan(
    body: RehearsalPlanCreate,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await plans.create_plan(tenant, **body.model_dump())


@router.get("/plans/{plan_id}", response_model=RehearsalPlanDetailOut)
async def get_rehearsal_plan(
    plan_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await plans.get_plan(tenant, plan_id)


@router.post(
    "/plans/{plan_id}/checks",
    response_model=RehearsalCheckOut,
    status_code=201,
)
async def create_rehearsal_check(
    plan_id: uuid.UUID,
    body: RehearsalCheckCreate,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await plans.create_check(tenant, plan_id, **body.model_dump())


@router.patch("/checks/{check_id}", response_model=RehearsalCheckOut)
async def update_rehearsal_check(
    check_id: uuid.UUID,
    body: RehearsalCheckUpdate,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await plans.update_check(
        tenant, check_id, body.model_dump(exclude_unset=True)
    )


@router.post(
    "/plans/{plan_id}/runs",
    response_model=RehearsalRunDetailOut,
    status_code=201,
)
async def create_rehearsal_run(
    plan_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await runs.create_run(tenant, plan_id)


@router.get("/runs/{run_id}", response_model=RehearsalRunDetailOut)
async def get_rehearsal_run(
    run_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await runs.get_run(tenant, run_id)


@router.patch(
    "/runs/{run_id}/checks/{result_id}",
    response_model=RehearsalRunDetailOut,
)
async def record_rehearsal_result(
    run_id: uuid.UUID,
    result_id: uuid.UUID,
    body: RehearsalResultRecord,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await runs.record_result(
        tenant, run_id, result_id, **body.model_dump()
    )


@router.post(
    "/runs/{run_id}/complete", response_model=RehearsalRunDetailOut
)
async def complete_rehearsal_run(
    run_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await runs.complete_run(tenant, run_id)


@router.post(
    "/runs/{run_id}/cancel", response_model=RehearsalRunDetailOut
)
async def cancel_rehearsal_run(
    run_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await runs.cancel_run(tenant, run_id)


@router.get("/dashboard", response_model=RehearsalDashboardOut)
async def get_rehearsal_dashboard(
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await dashboard.get_dashboard(tenant)


__all__ = ("router",)
