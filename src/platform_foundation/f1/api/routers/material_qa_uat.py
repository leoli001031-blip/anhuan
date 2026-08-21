"""Local-only closed-query material QA. Mounted solely when both UAT flags are on.

Requires exact ``F1_MATERIAL_RAG_UAT_LOCAL=1`` and ``F1_LOCAL_ENGINEERING=1``.
Uses ``tenant_from_header`` plus ``require_role``; no actor header.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from ...auth import Tenant, require_role, tenant_from_header
from ...features.material_rag.uat_local import (
    LocalUatFault,
    ask as uat_ask,
    bind_client_account,
    catalog_enterprise_for_tenant,
    delete_scope as uat_delete_scope,
    local_uat_enabled,
    open_citation as uat_open_citation,
    rebuild as uat_rebuild,
)

router = APIRouter()
_UAT_ADMIN = Depends(
    require_role("super_admin", "enterprise_admin", "plant_admin")
)


class LocalUatAskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str = Field(min_length=1, max_length=64)
    request_id: uuid.UUID
    client_account_id: uuid.UUID | None = None


class LocalUatScopeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_account_id: uuid.UUID | None = None


class LocalUatCitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_record_id: uuid.UUID
    document_version_id: uuid.UUID


def mount_if_enabled(app: FastAPI) -> bool:
    if not local_uat_enabled():
        return False
    app.include_router(
        router,
        prefix="/api/v1/local-uat/material-qa",
        tags=["local-uat-material-qa"],
    )
    return True


def _http(fault: LocalUatFault) -> HTTPException:
    return HTTPException(status_code=fault.http_status, detail=fault.code)


async def _catalog_context(
    tenant: Tenant, client_account_id: uuid.UUID | None
) -> tuple[uuid.UUID, uuid.UUID | None]:
    try:
        catalog = catalog_enterprise_for_tenant(tenant)
        synthetic_client = await bind_client_account(tenant, client_account_id)
    except LocalUatFault as fault:
        raise _http(fault) from None
    return catalog, synthetic_client


@router.post("")
async def ask_fixed_query(
    body: LocalUatAskRequest,
    response: Response,
    tenant: Tenant = Depends(tenant_from_header),
    _user: dict = _UAT_ADMIN,
) -> dict:
    enterprise_id, client_account_id = await _catalog_context(
        tenant, body.client_account_id
    )
    try:
        result = uat_ask(
            query_id=body.query_id,
            request_id=body.request_id,
            enterprise_id=enterprise_id,
            client_account_id=client_account_id,
        )
    except LocalUatFault as fault:
        raise _http(fault) from None
    response.status_code = result.http_status
    return result.to_public_dict()


@router.post("/rebuild")
async def rebuild_scope(
    body: LocalUatScopeRequest,
    tenant: Tenant = Depends(tenant_from_header),
    _user: dict = _UAT_ADMIN,
) -> dict:
    enterprise_id, client_account_id = await _catalog_context(
        tenant, body.client_account_id
    )
    try:
        return uat_rebuild(
            enterprise_id=enterprise_id,
            client_account_id=client_account_id,
        )
    except LocalUatFault as fault:
        raise _http(fault) from None


@router.post("/delete")
async def delete_scope(
    body: LocalUatScopeRequest,
    tenant: Tenant = Depends(tenant_from_header),
    _user: dict = _UAT_ADMIN,
) -> dict:
    enterprise_id, client_account_id = await _catalog_context(
        tenant, body.client_account_id
    )
    try:
        return uat_delete_scope(
            enterprise_id=enterprise_id,
            client_account_id=client_account_id,
        )
    except LocalUatFault as fault:
        raise _http(fault) from None


@router.post("/citation")
async def open_citation(
    body: LocalUatCitationRequest,
    tenant: Tenant = Depends(tenant_from_header),
    _user: dict = _UAT_ADMIN,
) -> dict:
    try:
        return uat_open_citation(
            enterprise_id=catalog_enterprise_for_tenant(tenant),
            document_record_id=body.document_record_id,
            document_version_id=body.document_version_id,
        )
    except LocalUatFault as fault:
        raise _http(fault) from None


__all__ = ("local_uat_enabled", "mount_if_enabled", "router")
