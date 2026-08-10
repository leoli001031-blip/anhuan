"""Enterprise endpoints (tenant-scoped)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from ...auth import (
    Tenant,
    current_tenant,
    require_role,
    tenant_from_header,
)
from ...database import session_scope

router = APIRouter()


class EnterpriseCreate(BaseModel):
    name: str
    license_no: str


class EnterpriseOut(BaseModel):
    id: uuid.UUID
    name: str
    license_no: str


@router.get("", response_model=list[EnterpriseOut])
async def list_enterprises(
    tenant: Tenant = Depends(tenant_from_header),
) -> list[EnterpriseOut]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        rows = (
            await session.execute(
                text("SELECT id, name, license_no FROM f1.enterprise")
            )
        ).fetchall()
    return [
        EnterpriseOut(id=r[0], name=r[1], license_no=r[2]) for r in rows
    ]


@router.post("", response_model=EnterpriseOut, status_code=201)
async def create_enterprise(
    body: EnterpriseCreate,
    user: dict = Depends(require_role("super_admin")),
) -> EnterpriseOut:
    # Direct enterprise/membership inserts are revoked from f1_api.  The
    # definer verifies that the current OIDC sub already has an authoritative
    # platform super-admin membership, then creates enterprise, membership and
    # audit row atomically.
    new_id = uuid.uuid4()
    async with session_scope(role="f1_api", sub=user["sub"]) as session:
        created = await session.execute(
            text(
                "SELECT f1.create_enterprise_for_current_sub"
                "(:id, :name, :license_no, :email)"
            ),
            {
                "id": new_id,
                "name": body.name,
                "license_no": body.license_no,
                "email": str(user.get("email") or ""),
            },
        )
        if created.scalar_one_or_none() != new_id:
            await session.rollback()
            raise HTTPException(status_code=409, detail="enterprise create failed")
        await session.commit()
    return EnterpriseOut(id=new_id, name=body.name, license_no=body.license_no)


@router.get("/{enterprise_id}", response_model=EnterpriseOut)
async def get_enterprise(
    enterprise_id: uuid.UUID,
    user: dict = Depends(require_role("super_admin", "enterprise_admin", "plant_admin", "partner", "auditor")),
) -> EnterpriseOut:
    tenant = await current_tenant(user, enterprise_id)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        row = (
            await session.execute(
                text(
                    "SELECT id, name, license_no FROM f1.enterprise WHERE id = :id"
                ),
                {"id": enterprise_id},
            )
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="enterprise not found")
    return EnterpriseOut(id=row[0], name=row[1], license_no=row[2])


__all__ = ("router", "EnterpriseOut")
