"""P4 dashboard, internal CRM, and unsigned report snapshot API."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ...auth import Tenant, tenant_from_header
from ...features.p4 import crm as crm_service
from ...features.p4 import dashboard as dashboard_service
from ...features.p4 import reports as report_service


router = APIRouter()


class DashboardQueueItem(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    due_at: datetime | None
    related_id: uuid.UUID | None


class DashboardQueues(BaseModel):
    service_cases: list[DashboardQueueItem]
    site_visits: list[DashboardQueueItem]
    findings: list[DashboardQueueItem]
    reports: list[DashboardQueueItem]
    crm_follow_ups: list[DashboardQueueItem]


class DashboardOut(BaseModel):
    view: Literal["admin", "consultant", "partner", "enterprise"]
    as_of: datetime
    metrics: dict[str, int]
    queues: DashboardQueues
    allowed_actions: list[str]
    boundaries: list[str]


class CrmAccountCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    stage: Literal["lead", "active", "dormant", "closed"] = "lead"
    owner_user_id: uuid.UUID | None = None
    industry_note: str | None = Field(default=None, max_length=2000)
    region_note: str | None = Field(default=None, max_length=2000)
    next_follow_up_at: datetime | None = None


class CrmAccountUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    stage: Literal["lead", "active", "dormant", "closed"] | None = None
    owner_user_id: uuid.UUID | None = None
    industry_note: str | None = Field(default=None, max_length=2000)
    region_note: str | None = Field(default=None, max_length=2000)
    next_follow_up_at: datetime | None = None


class CrmContactCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    role_title: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=64)
    status: Literal["active", "inactive"] = "active"


class CrmContactUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    role_title: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=64)
    status: Literal["active", "inactive"] | None = None


class CrmFollowUpCreate(BaseModel):
    channel: Literal["onsite", "meeting", "phone", "internal_note"]
    summary: str = Field(min_length=1, max_length=4000)
    next_action: str | None = Field(default=None, max_length=2000)
    next_due_at: datetime | None = None
    occurred_at: datetime | None = None


class CrmContactOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    account_id: uuid.UUID
    display_name: str
    role_title: str | None
    email: str | None
    phone: str | None
    status: str
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[str]


class CrmFollowUpOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    account_id: uuid.UUID
    channel: str
    summary: str
    next_action: str | None
    next_due_at: datetime | None
    occurred_at: datetime
    actor_user_id: uuid.UUID
    created_at: datetime
    allowed_actions: list[str]


class CrmAccountOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    display_name: str
    stage: str
    owner_user_id: uuid.UUID | None
    industry_note: str | None
    region_note: str | None
    next_follow_up_at: datetime | None
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[str]


class CrmAccountDetailOut(CrmAccountOut):
    contacts: list[CrmContactOut]
    follow_ups: list[CrmFollowUpOut]


class CrmAccountListOut(BaseModel):
    items: list[CrmAccountOut]
    allowed_actions: list[str]


class ReportCreate(BaseModel):
    service_case_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)


class ReportVersionCreate(BaseModel):
    change_note: str | None = Field(default=None, max_length=2000)
    document_version_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)


class ReportArtifactOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    report_version_id: uuid.UUID
    artifact_kind: str
    storage_kind: str
    content_type: str
    status: str
    sha256: str
    size_bytes: int
    created_at: datetime


class ReportVersionOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    report_id: uuid.UUID
    version_number: int
    lifecycle: str
    change_note: str | None
    canonical_snapshot: dict[str, Any]
    snapshot_sha256: str
    snapshot_size_bytes: int
    source_counts: dict[str, int]
    created_by_user_id: uuid.UUID
    captured_at: datetime
    artifact: ReportArtifactOut | None
    allowed_actions: list[str]
    boundaries: list[str]


class ReportOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    service_case_id: uuid.UUID
    title: str
    status: str
    current_version_no: int
    created_by_user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[str]
    boundaries: list[str] = Field(default_factory=list)


class ReportDetailOut(ReportOut):
    versions: list[ReportVersionOut]


class ReportListOut(BaseModel):
    items: list[ReportOut]
    allowed_actions: list[str]
    boundaries: list[str]


@router.get("/dashboard", response_model=DashboardOut)
async def get_dashboard(
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, object]:
    return await dashboard_service.dashboard_overview(tenant)


@router.get("/crm/accounts", response_model=CrmAccountListOut)
async def list_crm_accounts(
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await crm_service.list_accounts(tenant)


@router.post("/crm/accounts", response_model=CrmAccountOut, status_code=201)
async def create_crm_account(
    body: CrmAccountCreate,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await crm_service.create_account(tenant, **body.model_dump())


@router.get("/crm/accounts/{account_id}", response_model=CrmAccountDetailOut)
async def get_crm_account(
    account_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await crm_service.get_account(tenant, account_id)


@router.patch("/crm/accounts/{account_id}", response_model=CrmAccountOut)
async def update_crm_account(
    account_id: uuid.UUID,
    body: CrmAccountUpdate,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await crm_service.update_account(
        tenant, account_id, body.model_dump(exclude_unset=True)
    )


@router.post(
    "/crm/accounts/{account_id}/contacts",
    response_model=CrmContactOut,
    status_code=201,
)
async def create_crm_contact(
    account_id: uuid.UUID,
    body: CrmContactCreate,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await crm_service.create_contact(
        tenant, account_id, **body.model_dump()
    )


@router.patch("/crm/contacts/{contact_id}", response_model=CrmContactOut)
async def update_crm_contact(
    contact_id: uuid.UUID,
    body: CrmContactUpdate,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await crm_service.update_contact(
        tenant, contact_id, body.model_dump(exclude_unset=True)
    )


@router.post(
    "/crm/accounts/{account_id}/follow-ups",
    response_model=CrmFollowUpOut,
    status_code=201,
)
async def create_crm_follow_up(
    account_id: uuid.UUID,
    body: CrmFollowUpCreate,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await crm_service.create_follow_up(
        tenant, account_id, **body.model_dump()
    )


@router.get("/reports", response_model=ReportListOut)
async def list_business_reports(
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await report_service.list_reports(tenant)


@router.post("/reports", response_model=ReportOut, status_code=201)
async def create_business_report(
    body: ReportCreate,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await report_service.create_report(tenant, **body.model_dump())


@router.get("/reports/{report_id}", response_model=ReportDetailOut)
async def get_business_report(
    report_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await report_service.get_report(tenant, report_id)


@router.post(
    "/reports/{report_id}/versions",
    response_model=ReportVersionOut,
    status_code=201,
)
async def create_business_report_version(
    report_id: uuid.UUID,
    body: ReportVersionCreate,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await report_service.create_report_version(
        tenant,
        report_id,
        change_note=body.change_note,
        document_version_ids=tuple(body.document_version_ids),
    )


@router.get("/report-versions/{version_id}", response_model=ReportVersionOut)
async def get_business_report_version(
    version_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await report_service.get_report_version(tenant, version_id)


@router.post("/reports/{report_id}/archive", response_model=ReportOut)
async def archive_business_report(
    report_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict[str, Any]:
    return await report_service.archive_report(tenant, report_id)


__all__ = ("router",)
