"""P5 source, internal review, impact, task, and structured-search API."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field

from ...auth import Tenant, tenant_from_header
from ...features.p5 import catalog, impacts, search, workflow
from ...features.material_intake.contracts import (
    ConfirmPolicyDraftIn,
    MaterialAnalysisOut,
)
from ...features.material_intake.policy_draft import confirm_policy_draft


router = APIRouter()

PolicyDomain = Literal[
    "safety", "health", "environment", "fire", "chemical", "general"
]
EffectStatus = Literal["unknown", "not_effective", "effective", "expired"]
WorkflowStatus = Literal[
    "draft", "in_review", "approved", "rejected", "published", "superseded"
]


class PolicySourceCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    publisher: str = Field(min_length=1, max_length=200)
    source_type: Literal["law", "regulation", "standard", "guidance", "internal"]
    jurisdiction: str = Field(min_length=1, max_length=120)
    source_reference: str = Field(min_length=1, max_length=500)


class PolicySourceUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    publisher: str | None = Field(default=None, min_length=1, max_length=200)
    source_type: Literal[
        "law", "regulation", "standard", "guidance", "internal"
    ] | None = None
    jurisdiction: str | None = Field(default=None, min_length=1, max_length=120)
    source_reference: str | None = Field(default=None, min_length=1, max_length=500)
    status: Literal["active", "archived"] | None = None


class PolicyVersionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    domain: PolicyDomain
    effect_status: EffectStatus = "unknown"
    issued_on: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    summary: str = Field(min_length=1, max_length=4000)
    document_version_id: uuid.UUID | None = None


class PolicySourceOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    title: str
    publisher: str
    source_type: str
    jurisdiction: str
    source_reference: str
    status: str
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[str]


class PolicyReviewEventOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    policy_version_id: uuid.UUID
    action: str
    comment: str | None
    actor_user_id: uuid.UUID
    occurred_at: datetime


class PolicyVersionOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    source_id: uuid.UUID
    version_number: int
    title: str
    domain: str
    effect_status: str
    issued_on: date | None
    effective_from: date | None
    effective_to: date | None
    summary: str
    document_version_id: uuid.UUID | None
    document_sha256: str | None
    workflow_status: str
    submitted_by_user_id: uuid.UUID | None
    submitted_at: datetime | None
    approved_by_user_id: uuid.UUID | None
    approved_at: datetime | None
    published_by_user_id: uuid.UUID | None
    published_at: datetime | None
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[str]
    boundaries: list[str]


class PolicyVersionDetailOut(PolicyVersionOut):
    review_events: list[PolicyReviewEventOut]


class PolicySourceDetailOut(PolicySourceOut):
    versions: list[PolicyVersionOut]
    boundaries: list[str]


class ConfirmPolicyDraftOut(BaseModel):
    analysis: MaterialAnalysisOut
    source: PolicySourceOut
    version: PolicyVersionOut


class PolicySourceListOut(BaseModel):
    items: list[PolicySourceOut]
    allowed_actions: list[str]
    boundaries: list[str]


class PolicyReviewAction(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


class PolicyImpactCreate(BaseModel):
    policy_version_id: uuid.UUID
    domain: PolicyDomain
    scope_note: str = Field(min_length=1, max_length=4000)
    priority: Literal["low", "medium", "high", "critical"]


class PolicyImpactUpdate(BaseModel):
    scope_note: str | None = Field(default=None, min_length=1, max_length=4000)
    priority: Literal["low", "medium", "high", "critical"] | None = None
    status: Literal["open", "accepted", "dismissed"] | None = None


class PolicyImpactTaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    owner_user_id: uuid.UUID
    due_at: datetime


class PolicyImpactTaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    owner_user_id: uuid.UUID | None = None
    due_at: datetime | None = None
    status: Literal["open", "in_progress", "completed", "dismissed"] | None = None


class PolicyImpactTaskOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    impact_candidate_id: uuid.UUID
    title: str
    owner_user_id: uuid.UUID
    due_at: datetime | None
    status: str
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[str]


class PolicyImpactOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    policy_version_id: uuid.UUID
    domain: str
    scope_note: str
    priority: str
    status: str
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[str]
    boundaries: list[str]


class PolicyImpactDetailOut(PolicyImpactOut):
    tasks: list[PolicyImpactTaskOut]


class PolicyImpactListOut(BaseModel):
    items: list[PolicyImpactOut]
    allowed_actions: list[str]
    boundaries: list[str]


class PolicySearchItemOut(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID
    version_number: int
    title: str
    domain: str
    effect_status: str
    issued_on: date | None
    effective_from: date | None
    effective_to: date | None
    summary: str
    workflow_status: str
    source_title: str
    publisher: str
    source_type: str
    jurisdiction: str
    source_reference: str
    allowed_actions: list[str]


class PolicySearchOut(BaseModel):
    items: list[PolicySearchItemOut]
    count: int
    boundaries: list[str]


@router.get("/sources", response_model=PolicySourceListOut)
async def list_policy_sources(
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await catalog.list_sources(tenant)


@router.post("/sources", response_model=PolicySourceOut, status_code=201)
async def create_policy_source(
    body: PolicySourceCreate,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await catalog.create_source(tenant, **body.model_dump())


@router.get("/sources/{source_id}", response_model=PolicySourceDetailOut)
async def get_policy_source(
    source_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await catalog.get_source(tenant, source_id)


@router.patch("/sources/{source_id}", response_model=PolicySourceOut)
async def update_policy_source(
    source_id: uuid.UUID,
    body: PolicySourceUpdate,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await catalog.update_source(
        tenant, source_id, body.model_dump(exclude_unset=True)
    )


@router.post(
    "/sources/{source_id}/versions",
    response_model=PolicyVersionOut,
    status_code=201,
)
async def create_policy_version(
    source_id: uuid.UUID,
    body: PolicyVersionCreate,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await catalog.create_version(
        tenant, source_id, **body.model_dump()
    )


@router.get("/versions/{version_id}", response_model=PolicyVersionDetailOut)
async def get_policy_version(
    version_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await catalog.get_version(tenant, version_id)


@router.post(
    "/material-analyses/{analysis_id}/confirm",
    response_model=ConfirmPolicyDraftOut,
    status_code=201,
)
async def confirm_material_policy_draft(
    analysis_id: uuid.UUID,
    body: ConfirmPolicyDraftIn,
    idempotency_key: Annotated[
        str | None, Header(alias="Idempotency-Key")
    ] = None,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await confirm_policy_draft(
        tenant,
        analysis_id,
        body=body,
        idempotency_key=idempotency_key,
    )


@router.post("/versions/{version_id}/submit", response_model=PolicyVersionDetailOut)
async def submit_policy_version(
    version_id: uuid.UUID,
    body: PolicyReviewAction,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await workflow.transition_version(
        tenant, version_id, action="submit", comment=body.comment
    )


@router.post("/versions/{version_id}/approve", response_model=PolicyVersionDetailOut)
async def approve_policy_version(
    version_id: uuid.UUID,
    body: PolicyReviewAction,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await workflow.transition_version(
        tenant, version_id, action="approve", comment=body.comment
    )


@router.post("/versions/{version_id}/reject", response_model=PolicyVersionDetailOut)
async def reject_policy_version(
    version_id: uuid.UUID,
    body: PolicyReviewAction,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await workflow.transition_version(
        tenant, version_id, action="reject", comment=body.comment
    )


@router.post("/versions/{version_id}/publish", response_model=PolicyVersionDetailOut)
async def publish_policy_version(
    version_id: uuid.UUID,
    body: PolicyReviewAction,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await workflow.transition_version(
        tenant, version_id, action="publish", comment=body.comment
    )


@router.get("/impacts", response_model=PolicyImpactListOut)
async def list_policy_impacts(
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await impacts.list_impacts(tenant)


@router.post("/impacts", response_model=PolicyImpactOut, status_code=201)
async def create_policy_impact(
    body: PolicyImpactCreate,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await impacts.create_impact(tenant, **body.model_dump())


@router.get("/impacts/{impact_id}", response_model=PolicyImpactDetailOut)
async def get_policy_impact(
    impact_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await impacts.get_impact(tenant, impact_id)


@router.patch("/impacts/{impact_id}", response_model=PolicyImpactOut)
async def update_policy_impact(
    impact_id: uuid.UUID,
    body: PolicyImpactUpdate,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await impacts.update_impact(
        tenant, impact_id, body.model_dump(exclude_unset=True)
    )


@router.post(
    "/impacts/{impact_id}/tasks",
    response_model=PolicyImpactTaskOut,
    status_code=201,
)
async def create_policy_impact_task(
    impact_id: uuid.UUID,
    body: PolicyImpactTaskCreate,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await impacts.create_task(
        tenant, impact_id, **body.model_dump()
    )


@router.patch("/impact-tasks/{task_id}", response_model=PolicyImpactTaskOut)
async def update_policy_impact_task(
    task_id: uuid.UUID,
    body: PolicyImpactTaskUpdate,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await impacts.update_task(
        tenant, task_id, body.model_dump(exclude_unset=True)
    )


@router.get("/search", response_model=PolicySearchOut)
async def search_policy_versions(
    q: str | None = Query(default=None, max_length=200),
    domain: PolicyDomain | None = Query(default=None),
    effect_status: EffectStatus | None = Query(default=None),
    workflow_status: WorkflowStatus | None = Query(default=None),
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await search.search_policies(
        tenant,
        query=q,
        domain=domain,
        effect_status=effect_status,
        workflow_status=workflow_status,
    )


__all__ = ("router",)
