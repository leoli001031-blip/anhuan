"""Shared P7 database helpers."""
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
                "SELECT member.user_id FROM f1.enterprise_user AS member "
                "JOIN f1.user_profile AS profile ON profile.id = member.user_id "
                "WHERE member.enterprise_id = :enterprise_id "
                "AND profile.keycloak_sub = :sub"
            ),
            {"enterprise_id": tenant.enterprise_id, "sub": tenant.sub},
        )
    ).scalar_one_or_none()
    if actor_id is None:
        raise HTTPException(status_code=404, detail="P7_MEMBERSHIP_NOT_FOUND")
    return actor_id


def row_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in row.items()}


__all__ = ("current_actor_id", "row_dict")
