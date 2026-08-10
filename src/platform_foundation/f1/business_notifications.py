"""Body-free P2 in-app notification helpers for caller-owned transactions."""
from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


async def recipient_ids_for_roles(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    roles: Iterable[str],
) -> set[uuid.UUID]:
    selected = sorted(set(roles))
    if not selected:
        return set()
    return set(
        (
            await session.execute(
                text(
                    "SELECT user_id FROM f1.enterprise_user "
                    "WHERE enterprise_id = :enterprise_id "
                    "AND role = ANY(CAST(:roles AS text[]))"
                ),
                {"enterprise_id": enterprise_id, "roles": selected},
            )
        ).scalars()
    )


async def accepted_assignee_ids(
    session: AsyncSession,
    *,
    service_case_id: uuid.UUID,
    capacities: Iterable[str],
) -> set[uuid.UUID]:
    selected = sorted(set(capacities))
    if not selected:
        return set()
    return set(
        (
            await session.execute(
                text(
                    "SELECT assignee_user_id FROM f1.service_assignment "
                    "WHERE service_case_id = :case_id AND status = 'accepted' "
                    "AND capacity = ANY(CAST(:capacities AS text[]))"
                ),
                {"case_id": service_case_id, "capacities": selected},
            )
        ).scalars()
    )


async def add_notifications(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    timeline_event_id: uuid.UUID,
    recipient_user_ids: Iterable[uuid.UUID],
    actor_user_id: uuid.UUID,
) -> tuple[uuid.UUID, ...]:
    """Deduplicate recipients and insert notification references without commit."""
    inserted: list[uuid.UUID] = []
    recipients = sorted(
        set(recipient_user_ids) - {actor_user_id}, key=lambda value: value.hex
    )
    for recipient_user_id in recipients:
        notification_id = uuid.uuid4()
        try:
            async with session.begin_nested():
                await session.execute(
                    text(
                        "INSERT INTO f1.in_app_notification ("
                        "id, enterprise_id, recipient_user_id, timeline_event_id"
                        ") VALUES ("
                        ":id, :enterprise_id, :recipient_user_id, :timeline_event_id"
                        ")"
                    ),
                    {
                        "id": notification_id,
                        "enterprise_id": enterprise_id,
                        "recipient_user_id": recipient_user_id,
                        "timeline_event_id": timeline_event_id,
                    },
                )
        except IntegrityError as error:
            diagnostic = getattr(error.orig, "diag", None)
            if (
                getattr(diagnostic, "constraint_name", None)
                != "in_app_notification_recipient_event_uq"
            ):
                raise
        else:
            inserted.append(notification_id)
    return tuple(inserted)


__all__ = (
    "add_notifications",
    "accepted_assignee_ids",
    "recipient_ids_for_roles",
)
