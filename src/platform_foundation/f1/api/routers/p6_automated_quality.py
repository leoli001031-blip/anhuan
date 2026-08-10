"""P6 synthetic automated-quality API."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ...auth import Tenant, tenant_from_header
from ...features.p6 import dashboard, disagreements, runs, suites


router = APIRouter()

SuiteCategory = Literal["ingestion", "retrieval", "qa", "authorization", "injection"]
ScenarioType = Literal[
    "exact_match",
    "threshold",
    "refusal_required",
    "isolation_required",
    "injection_blocked",
    "disagreement_max",
]
Severity = Literal["low", "medium", "high", "critical"]


class QualitySuiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category: SuiteCategory


class QualitySuiteOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    name: str
    category: str
    status: str
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[str]
    boundaries: list[str]


class QualitySuiteListOut(BaseModel):
    items: list[QualitySuiteOut]
    allowed_actions: list[str]
    boundaries: list[str]


class QualityScenarioCreate(BaseModel):
    scenario_key: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    scenario_type: ScenarioType
    severity: Severity
    oracle_config: dict[str, Any]
    synthetic_observation: dict[str, Any]
    enabled: bool = True


class QualityScenarioUpdate(BaseModel):
    severity: Severity | None = None
    oracle_config: dict[str, Any] | None = None
    synthetic_observation: dict[str, Any] | None = None
    enabled: bool | None = None


class QualityScenarioOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    suite_id: uuid.UUID
    scenario_key: str
    scenario_type: str
    severity: str
    oracle_config: dict[str, Any]
    synthetic_observation: dict[str, Any]
    scenario_sha256: str
    enabled: bool
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[str]
    boundaries: list[str]


class QualityRunOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    suite_id: uuid.UUID
    status: str
    trigger_kind: str
    total_count: int
    passed_count: int
    failed_count: int
    error_count: int
    created_by_user_id: uuid.UUID
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    allowed_actions: list[str]
    boundaries: list[str]


class QualitySuiteDetailOut(QualitySuiteOut):
    scenarios: list[QualityScenarioOut]
    runs: list[QualityRunOut]


class QualityDisagreementOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    result_id: uuid.UUID
    kind: str
    left_digest: str
    right_digest: str
    score: float
    review_status: str
    review_note: str | None
    reviewed_by_user_id: uuid.UUID | None
    reviewed_at: datetime | None
    created_at: datetime
    allowed_actions: list[str] = Field(default_factory=lambda: ["view"])
    boundaries: list[str] = Field(default_factory=list)


class QualityResultOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    run_id: uuid.UUID
    scenario_id: uuid.UUID
    status: str
    reason_code: str
    observed_metrics: dict[str, Any]
    evidence_sha256: str
    created_at: datetime
    disagreements: list[QualityDisagreementOut]
    allowed_actions: list[str]
    boundaries: list[str]


class QualityRunDetailOut(QualityRunOut):
    results: list[QualityResultOut]


class QualityDisagreementListOut(BaseModel):
    items: list[QualityDisagreementOut]
    count: int
    open_count: int
    boundaries: list[str]


class QualityDisagreementReview(BaseModel):
    review_status: Literal["acknowledged", "waived"]
    review_note: str = Field(min_length=1, max_length=2000)


class SuiteCounts(BaseModel):
    total: int
    active: int
    archived: int


class ScenarioCounts(BaseModel):
    total: int
    enabled: int
    disabled: int


class RunCounts(BaseModel):
    total: int
    queued: int
    running: int
    passed: int
    failed: int
    cancelled: int


class ResultCounts(BaseModel):
    total: int
    passed: int
    failed: int
    error: int


class DisagreementCounts(BaseModel):
    total: int
    open: int
    acknowledged: int
    waived: int


class QualityDashboardOut(BaseModel):
    synthetic_label: str
    suite_counts: SuiteCounts
    scenario_counts: ScenarioCounts
    run_counts: RunCounts
    result_counts: ResultCounts
    disagreement_counts: DisagreementCounts
    recent_runs: list[QualityRunOut]
    allowed_actions: list[str]
    boundaries: list[str]


@router.get("/suites", response_model=QualitySuiteListOut)
async def list_quality_suites(
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await suites.list_suites(tenant)


@router.post("/suites", response_model=QualitySuiteOut, status_code=201)
async def create_quality_suite(
    body: QualitySuiteCreate,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await suites.create_suite(tenant, **body.model_dump())


@router.get("/suites/{suite_id}", response_model=QualitySuiteDetailOut)
async def get_quality_suite(
    suite_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await suites.get_suite(tenant, suite_id)


@router.post(
    "/suites/{suite_id}/scenarios",
    response_model=QualityScenarioOut,
    status_code=201,
)
async def create_quality_scenario(
    suite_id: uuid.UUID,
    body: QualityScenarioCreate,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await suites.create_scenario(
        tenant, suite_id, **body.model_dump()
    )


@router.patch("/scenarios/{scenario_id}", response_model=QualityScenarioOut)
async def update_quality_scenario(
    scenario_id: uuid.UUID,
    body: QualityScenarioUpdate,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await suites.update_scenario(
        tenant, scenario_id, body.model_dump(exclude_unset=True)
    )


@router.post(
    "/suites/{suite_id}/runs",
    response_model=QualityRunDetailOut,
    status_code=201,
)
async def trigger_quality_run(
    suite_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await runs.trigger_run(tenant, suite_id)


@router.get("/runs/{run_id}", response_model=QualityRunDetailOut)
async def get_quality_run(
    run_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await runs.get_run(tenant, run_id)


@router.get("/disagreements", response_model=QualityDisagreementListOut)
async def list_quality_disagreements(
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await disagreements.list_disagreements(tenant)


@router.patch(
    "/disagreements/{disagreement_id}", response_model=QualityDisagreementOut
)
async def review_quality_disagreement(
    disagreement_id: uuid.UUID,
    body: QualityDisagreementReview,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await disagreements.review_disagreement(
        tenant, disagreement_id, **body.model_dump()
    )


@router.get("/dashboard", response_model=QualityDashboardOut)
async def get_quality_dashboard(
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await dashboard.get_dashboard(tenant)


__all__ = ("router",)
