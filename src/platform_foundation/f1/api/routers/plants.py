"""Plant CRUD (tenant-scoped)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from ...audit import log_event
from ...auth import (
    Tenant,
    current_tenant,
    require_role,
    tenant_from_header,
)
from ...database import session_scope

router = APIRouter()


class PlantCreate(BaseModel):
    name: str
    address: str | None = None


class PlantOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    name: str
    address: str | None


@router.get("", response_model=list[PlantOut])
async def list_plants(
    tenant: Tenant = Depends(tenant_from_header),
) -> list[PlantOut]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, enterprise_id, name, address "
                    "FROM f1.plant ORDER BY created_at"
                )
            )
        ).fetchall()
    return [
        PlantOut(id=r[0], enterprise_id=r[1], name=r[2], address=r[3])
        for r in rows
    ]


@router.post("", response_model=PlantOut, status_code=201)
async def create_plant(
    body: PlantCreate,
    tenant: Tenant = Depends(tenant_from_header),
) -> PlantOut:
    if "super_admin" not in tenant.roles and tenant.role not in (
        "enterprise_admin",
        "plant_admin",
    ):
        raise HTTPException(status_code=403, detail="insufficient role")
    new_id = uuid.uuid4()
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await session.execute(
            text(
                "INSERT INTO f1.plant (id, enterprise_id, name, address) "
                "VALUES (:id, :eid, :name, :address)"
            ),
            {
                "id": new_id,
                "eid": tenant.enterprise_id,
                "name": body.name,
                "address": body.address,
            },
        )
        await session.commit()
    await log_event(
        tenant.enterprise_id, tenant.sub, "plant.create", "plant",
        str(new_id), "success",
    )
    return PlantOut(
        id=new_id, enterprise_id=tenant.enterprise_id,
        name=body.name, address=body.address,
    )


__all__ = ("router", "PlantOut")
