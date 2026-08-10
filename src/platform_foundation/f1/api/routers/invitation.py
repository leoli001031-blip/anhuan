"""Invitation endpoints: create (tenant admin) + consume (single-use)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...auth import Tenant, current_user, tenant_from_header
from ...invitation import InvitationError, consume_invite, create_invite

router = APIRouter()

_INVITABLE_BY_LOCAL_ROLE = {
    "super_admin": frozenset(("enterprise_admin", "plant_admin", "partner", "auditor")),
    "enterprise_admin": frozenset(("plant_admin", "partner", "auditor")),
    "plant_admin": frozenset(("partner", "auditor")),
}


class InviteCreate(BaseModel):
    email: str
    role: str


class InviteOut(BaseModel):
    email: str
    role: str
    token: str


class InviteConsume(BaseModel):
    token: str
    # ``keycloak_sub`` / ``email`` are accepted for client compatibility but
    # deliberately IGNORED: consume binds only the authenticated OIDC identity.
    keycloak_sub: str | None = None
    email: str | None = None


@router.post("", response_model=InviteOut, status_code=201)
async def create(
    body: InviteCreate,
    tenant: Tenant = Depends(tenant_from_header),
) -> InviteOut:
    if body.role not in _INVITABLE_BY_LOCAL_ROLE.get(tenant.role or "", frozenset()):
        raise HTTPException(status_code=403, detail="INVITE_ROLE_ESCALATION")
    try:
        invite = await create_invite(
            tenant.enterprise_id, body.email, body.role, user_sub=tenant.sub
        )
    except InvitationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return InviteOut(email=body.email, role=body.role, token=invite.token)


@router.post("/consume", response_model=InviteOut)
async def consume(
    body: InviteConsume,
    user: dict = Depends(current_user),
) -> InviteOut:
    # Only the OIDC identity is used; a client-supplied ``keycloak_sub`` is
    # ignored.  The JTI+profile+membership+audit all commit in one transaction
    # inside ``f1.consume_invite``.
    oidc_email = user.get("email")
    if not isinstance(oidc_email, str) or not oidc_email.strip():
        raise HTTPException(status_code=409, detail="OIDC_EMAIL_REQUIRED")
    try:
        invite = await consume_invite(
            body.token, user_sub=user["sub"], oidc_email=oidc_email
        )
    except InvitationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    return InviteOut(email=invite.email, role=invite.role, token=invite.token)


__all__ = ("router", "InviteCreate", "InviteConsume", "InviteOut")
