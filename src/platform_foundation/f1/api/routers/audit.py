"""Audit log endpoints (tenant-scoped; auditor / super_admin)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from ...auth import Tenant, current_tenant, current_user
from ...database import session_scope

router = APIRouter()


class AuditOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID | None
    user_sub: str
    action: str
    resource_type: str
    resource_id: str | None
    result: str
    created_at: str


async def _audit_tenant(
    user: dict = Depends(current_user),
    x_enterprise_id: str | None = Header(default=None),
) -> Tenant:
    """Resolve the tenant for an auditor / super_admin read."""
    enterprise_id: uuid.UUID | None = None
    if x_enterprise_id:
        try:
            enterprise_id = uuid.UUID(x_enterprise_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid enterprise id") from None
    tenant = await current_tenant(user, enterprise_id)
    # Realm roles are global token claims and cannot grant a tenant-local audit
    # read.  The membership role returned by the database is authoritative.
    if tenant.role not in ("super_admin", "auditor"):
        raise HTTPException(status_code=403, detail="insufficient role")
    return tenant


@router.get("", response_model=list[AuditOut])
async def list_audit(
    tenant: Tenant = Depends(_audit_tenant),
) -> list[AuditOut]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, enterprise_id, user_sub, action, resource_type, "
                    "resource_id, result, created_at "
                    "FROM f1.audit_log ORDER BY created_at DESC LIMIT 200"
                )
            )
        ).fetchall()
    return [
        AuditOut(
            id=r[0], enterprise_id=r[1], user_sub=r[2], action=r[3],
            resource_type=r[4], resource_id=r[5], result=r[6],
            created_at=r[7].isoformat() if r[7] else "",
        )
        for r in rows
    ]


__all__ = ("router", "AuditOut")
