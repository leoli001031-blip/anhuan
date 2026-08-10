"""F1 audit logging: append-only, tenant-scoped log_event for all writes."""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .database import session_scope


async def add_event(
    session: AsyncSession,
    enterprise_id: uuid.UUID | None,
    user_sub: str,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    result: str = "success",
) -> None:
    """Append an audit row to the caller's transaction without committing."""
    await session.execute(
        text(
            "INSERT INTO f1.audit_log ("
            "id, enterprise_id, user_sub, action, resource_type, resource_id, result"
            ") VALUES ("
            ":id, :enterprise_id, :user_sub, :action, :resource_type, "
            ":resource_id, :result)"
        ),
        {
            "id": uuid.uuid4(),
            "enterprise_id": enterprise_id,
            "user_sub": user_sub,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "result": result,
        },
    )


async def log_event(
    enterprise_id: uuid.UUID | None,
    user_sub: str,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    result: str = "success",
    *,
    role: str = "f1_api",
) -> None:
    """Persist a standalone audit event.

    Business mutations should call :func:`add_event` using their own session
    and commit once.  This wrapper remains for events that have no associated
    business write.
    """
    async with session_scope(
        role=role, enterprise_id=enterprise_id, sub=user_sub
    ) as session:
        await add_event(
            session,
            enterprise_id,
            user_sub,
            action,
            resource_type,
            resource_id,
            result,
        )
        await session.commit()


__all__ = ("add_event", "log_event")
