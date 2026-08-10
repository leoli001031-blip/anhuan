"""Shared P4 database helpers."""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import Tenant


async def current_actor_id(
    session: AsyncSession, tenant: Tenant
) -> uuid.UUID:
    actor_id = (
        await session.execute(
            text(
                "SELECT membership.user_id "
                "FROM f1.enterprise_user AS membership "
                "JOIN f1.user_profile AS profile "
                "ON profile.id = membership.user_id "
                "WHERE membership.enterprise_id = :enterprise_id "
                "AND profile.keycloak_sub = :sub"
            ),
            {"enterprise_id": tenant.enterprise_id, "sub": tenant.sub},
        )
    ).scalar_one_or_none()
    if actor_id is None:
        raise HTTPException(status_code=404, detail="P4_MEMBERSHIP_NOT_FOUND")
    return actor_id


async def accepted_capacities(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID,
    service_case_id: uuid.UUID,
) -> tuple[str, ...]:
    rows = (
        await session.execute(
            text(
                "SELECT capacity FROM f1.service_assignment "
                "WHERE service_case_id = :service_case_id "
                "AND assignee_user_id = :actor_id AND status = 'accepted' "
                "ORDER BY capacity"
            ),
            {"service_case_id": service_case_id, "actor_id": actor_id},
        )
    ).scalars()
    return tuple(str(value) for value in rows)


async def ensure_enterprise_member(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    exists = (
        await session.execute(
            text(
                "SELECT user_id FROM f1.enterprise_user "
                "WHERE enterprise_id = :enterprise_id AND user_id = :user_id"
            ),
            {"enterprise_id": enterprise_id, "user_id": user_id},
        )
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="P4_OWNER_NOT_FOUND")


def row_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in row.items()}


__all__ = (
    "accepted_capacities",
    "current_actor_id",
    "ensure_enterprise_member",
    "row_dict",
)
