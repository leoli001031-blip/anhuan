"""P6 disagreement queue and human disposition."""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...audit import add_event
from ...auth import Tenant
from ...database import session_scope
from .common import current_actor_id, row_dict
from .contracts import (
    AUTOMATED_QUALITY_BOUNDARIES,
    disagreement_actions,
    is_manager,
    is_reviewer,
)
from .runs import DISAGREEMENT_COLUMNS


async def disagreement_row(
    session: AsyncSession,
    disagreement_id: uuid.UUID,
    *,
    lock: bool = False,
) -> Mapping[str, Any]:
    suffix = " FOR UPDATE" if lock else ""
    row = (
        await session.execute(
            text(
                f"SELECT {DISAGREEMENT_COLUMNS} FROM f1.quality_disagreement "
                "WHERE id = :disagreement_id" + suffix
            ),
            {"disagreement_id": disagreement_id},
        )
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="QUALITY_DISAGREEMENT_NOT_FOUND")
    return row


def disagreement_out(
    row: Mapping[str, Any], tenant: Tenant
) -> dict[str, Any]:
    output = row_dict(row)
    output["allowed_actions"] = disagreement_actions(
        tenant.role, str(row["review_status"])
    )
    output["boundaries"] = list(AUTOMATED_QUALITY_BOUNDARIES)
    return output


async def list_disagreements(tenant: Tenant) -> dict[str, Any]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await current_actor_id(session, tenant)
        rows = (
            await session.execute(
                text(
                    f"SELECT {DISAGREEMENT_COLUMNS} "
                    "FROM f1.quality_disagreement "
                    "ORDER BY (review_status = 'open') DESC, created_at DESC, id"
                )
            )
        ).mappings().all()
    return {
        "items": [disagreement_out(row, tenant) for row in rows],
        "count": len(rows),
        "open_count": sum(row["review_status"] == "open" for row in rows),
        "boundaries": list(AUTOMATED_QUALITY_BOUNDARIES),
    }


async def review_disagreement(
    tenant: Tenant,
    disagreement_id: uuid.UUID,
    *,
    review_status: str,
    review_note: str,
) -> dict[str, Any]:
    if not (is_manager(tenant.role) or is_reviewer(tenant.role)):
        raise HTTPException(status_code=403, detail="QUALITY_REVIEW_FORBIDDEN")
    if review_status not in ("acknowledged", "waived"):
        raise HTTPException(status_code=422, detail="QUALITY_REVIEW_STATUS_INVALID")
    if not review_note.strip():
        raise HTTPException(status_code=422, detail="QUALITY_REVIEW_NOTE_REQUIRED")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        current = await disagreement_row(session, disagreement_id, lock=True)
        if current["review_status"] != "open":
            raise HTTPException(status_code=409, detail="QUALITY_REVIEW_TERMINAL")
        row = (
            await session.execute(
                text(
                    "UPDATE f1.quality_disagreement SET "
                    "review_status = :review_status, review_note = :review_note, "
                    "reviewed_by_user_id = :actor_id, "
                    "reviewed_at = statement_timestamp() "
                    "WHERE id = :disagreement_id AND review_status = 'open' "
                    f"RETURNING {DISAGREEMENT_COLUMNS}"
                ),
                {
                    "disagreement_id": disagreement_id,
                    "review_status": review_status,
                    "review_note": review_note.strip(),
                    "actor_id": actor_id,
                },
            )
        ).mappings().one_or_none()
        if row is None:
            raise HTTPException(status_code=409, detail="QUALITY_REVIEW_CONFLICT")
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            f"quality.disagreement.{review_status}",
            "quality_disagreement",
            str(disagreement_id),
        )
        await session.commit()
    return disagreement_out(row, tenant)


__all__ = (
    "disagreement_out",
    "disagreement_row",
    "list_disagreements",
    "review_disagreement",
)
