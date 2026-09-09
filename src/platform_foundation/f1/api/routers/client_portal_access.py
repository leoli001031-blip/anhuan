"""Provider-managed customer access without cross-tenant memberships."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException
from jose import jwt
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from ...auth import Tenant, tenant_from_header
from ...database import session_scope
from ...features.analysis_reports.service import product_role_for
from ...invitation import ALGORITHM, InvitationError, _load_invite_key

router = APIRouter()


class PortalAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: uuid.UUID


class PortalOpen(PortalAction):
    license_no: str = Field(min_length=1, max_length=64)


class PortalInvitation(PortalAction):
    email: str = Field(min_length=3, max_length=320)


def _require_manager(tenant: Tenant) -> None:
    if product_role_for(tenant) != "provider_admin":
        raise HTTPException(404, detail="CLIENT_PORTAL_NOT_FOUND")


def _error(error: SQLAlchemyError) -> HTTPException:
    message = str(getattr(error, "orig", error))
    for code in ("CLIENT_PORTAL_NOT_FOUND", "CLIENT_PORTAL_REQUEST_CONFLICT",
                 "CLIENT_PORTAL_RESTORE_REQUIRED", "CLIENT_PORTAL_LICENSE_REQUIRED",
                 "CLIENT_PORTAL_ORGANIZATION_UNCONFIGURED", "CLIENT_PORTAL_INPUT_INVALID",
                 "CLIENT_PORTAL_INVITE_FINISHED"):
        if code in message:
            return HTTPException(404 if code.endswith("NOT_FOUND") else 409, detail=code)
    if getattr(getattr(error, "orig", None), "sqlstate", None) == "23505":
        return HTTPException(409, detail="CLIENT_PORTAL_REQUEST_CONFLICT")
    return HTTPException(503, detail="CLIENT_PORTAL_UNAVAILABLE")


@router.get("/{client_id}/portal-access")
async def read_access(client_id: uuid.UUID, tenant: Tenant = Depends(tenant_from_header)) -> dict:
    _require_manager(tenant)
    try:
        async with session_scope(role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub) as session:
            return (await session.execute(text("SELECT f1.read_client_portal(:client)"), {"client":client_id})).scalar_one()
    except SQLAlchemyError as error:
        raise _error(error) from None


async def _change(client_id: uuid.UUID, body: PortalAction, action: str, tenant: Tenant) -> dict:
    _require_manager(tenant)
    try:
        async with session_scope(role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub) as session:
            await session.execute(text("SELECT f1.manage_client_portal(:client,:request,:action,:license)"),
                {"client":client_id,"request":body.request_id,"action":action,
                 "license":body.license_no if isinstance(body,PortalOpen) else None})
            receipt = (await session.execute(text("SELECT f1.read_client_portal(:client)"), {"client":client_id})).scalar_one()
            await session.commit()
            return receipt
    except SQLAlchemyError as error:
        raise _error(error) from None


@router.post("/{client_id}/portal-access")
async def open_access(client_id: uuid.UUID, body: PortalOpen, tenant: Tenant = Depends(tenant_from_header)) -> dict:
    return await _change(client_id,body,"open",tenant)


@router.post("/{client_id}/portal-access/revoke")
async def revoke_access(client_id: uuid.UUID, body: PortalAction, tenant: Tenant = Depends(tenant_from_header)) -> dict:
    return await _change(client_id,body,"revoke",tenant)


@router.post("/{client_id}/portal-access/restore")
async def restore_access(client_id: uuid.UUID, body: PortalAction, tenant: Tenant = Depends(tenant_from_header)) -> dict:
    return await _change(client_id,body,"restore",tenant)


@router.post("/{client_id}/portal-invitations")
async def invite_owner(client_id: uuid.UUID, body: PortalInvitation, tenant: Tenant = Depends(tenant_from_header)) -> dict:
    _require_manager(tenant)
    email=body.email.strip().lower()
    if email.count("@")!=1 or any(char.isspace() for char in email) or email.startswith("@") or email.endswith("@"):
        raise HTTPException(422,detail="INVALID_EMAIL")
    try:
        key = _load_invite_key()
        expiry = datetime.fromtimestamp(int((datetime.now(timezone.utc)+timedelta(hours=24)).timestamp()),tz=timezone.utc)
        async with session_scope(role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub) as session:
            claims = (await session.execute(text("SELECT f1.issue_client_portal_invite(:client,:jti,:email,:expiry)"),
                {"client":client_id,"jti":body.request_id.hex,"email":email,"expiry":expiry})).scalar_one()
            token = jwt.encode({"sub":"invite",**claims},key,algorithm=ALGORITHM)
            await session.commit()
            return {"token":token,"email":claims["email"],"role":claims["role"],"expires_at":claims["exp"]}
    except InvitationError as error:
        raise HTTPException(503,detail=str(error)) from None
    except SQLAlchemyError as error:
        raise _error(error) from None
