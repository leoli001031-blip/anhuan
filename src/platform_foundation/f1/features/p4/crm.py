"""Internal-only, manual P4 CRM operations."""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...audit import add_event
from ...auth import Tenant
from ...database import session_scope
from .common import current_actor_id, ensure_enterprise_member, row_dict
from .contracts import (
    crm_account_allowed_actions,
    crm_collection_allowed_actions,
    is_manager,
)


_ACCOUNT_COLUMNS = (
    "id, enterprise_id, display_name, stage, owner_user_id, industry_note, "
    "region_note, next_follow_up_at, created_by_user_id, created_at, updated_at"
)
_CONTACT_COLUMNS = (
    "id, enterprise_id, account_id, display_name, role_title, email, phone, "
    "status, created_by_user_id, created_at, updated_at"
)
_FOLLOW_UP_COLUMNS = (
    "id, enterprise_id, account_id, channel, summary, next_action, "
    "next_due_at, occurred_at, actor_user_id, created_at"
)


def _account_out(
    row: Mapping[str, Any], tenant: Tenant, actor_id: uuid.UUID
) -> dict[str, Any]:
    output = row_dict(row)
    output["allowed_actions"] = crm_account_allowed_actions(
        tenant.role, is_owner=row["owner_user_id"] == actor_id
    )
    return output


def _can_manage_account(
    tenant: Tenant, row: Mapping[str, Any], actor_id: uuid.UUID
) -> bool:
    return is_manager(tenant.role) or row["owner_user_id"] == actor_id


async def _account_row(
    session: AsyncSession, account_id: uuid.UUID, *, lock: bool = False
) -> Mapping[str, Any]:
    suffix = " FOR UPDATE" if lock else ""
    row = (
        await session.execute(
            text(
                f"SELECT {_ACCOUNT_COLUMNS} FROM f1.crm_account "
                "WHERE id = :account_id" + suffix
            ),
            {"account_id": account_id},
        )
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="CRM_ACCOUNT_NOT_FOUND")
    return row


async def list_accounts(tenant: Tenant) -> dict[str, Any]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        rows = (
            await session.execute(
                text(
                    f"SELECT {_ACCOUNT_COLUMNS} FROM f1.crm_account "
                    "ORDER BY next_follow_up_at NULLS LAST, updated_at DESC, id"
                )
            )
        ).mappings().all()
    return {
        "items": [_account_out(row, tenant, actor_id) for row in rows],
        "allowed_actions": crm_collection_allowed_actions(tenant.role),
    }


async def get_account(tenant: Tenant, account_id: uuid.UUID) -> dict[str, Any]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        account = await _account_row(session, account_id)
        contacts = (
            await session.execute(
                text(
                    f"SELECT {_CONTACT_COLUMNS} FROM f1.crm_contact "
                    "WHERE account_id = :account_id "
                    "ORDER BY status, display_name, id"
                ),
                {"account_id": account_id},
            )
        ).mappings().all()
        follow_ups = (
            await session.execute(
                text(
                    f"SELECT {_FOLLOW_UP_COLUMNS} FROM f1.crm_follow_up "
                    "WHERE account_id = :account_id "
                    "ORDER BY occurred_at DESC, created_at DESC, id DESC"
                ),
                {"account_id": account_id},
            )
        ).mappings().all()
    output = _account_out(account, tenant, actor_id)
    can_manage = _can_manage_account(tenant, account, actor_id)
    output["contacts"] = [
        {**row_dict(row), "allowed_actions": ["edit"] if can_manage else []}
        for row in contacts
    ]
    output["follow_ups"] = [
        {**row_dict(row), "allowed_actions": ["view"]} for row in follow_ups
    ]
    return output


async def create_account(
    tenant: Tenant,
    *,
    display_name: str,
    stage: str,
    owner_user_id: uuid.UUID | None,
    industry_note: str | None,
    region_note: str | None,
    next_follow_up_at: datetime | None,
) -> dict[str, Any]:
    if not is_manager(tenant.role):
        raise HTTPException(status_code=403, detail="CRM_MANAGER_REQUIRED")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        if owner_user_id is not None:
            await ensure_enterprise_member(
                session,
                enterprise_id=tenant.enterprise_id,
                user_id=owner_user_id,
            )
        account_id = uuid.uuid4()
        row = (
            await session.execute(
                text(
                    "INSERT INTO f1.crm_account ("
                    "id, enterprise_id, display_name, stage, owner_user_id, "
                    "industry_note, region_note, next_follow_up_at, "
                    "created_by_user_id) VALUES ("
                    ":id, :enterprise_id, :display_name, :stage, :owner_user_id, "
                    ":industry_note, :region_note, :next_follow_up_at, :actor_id) "
                    f"RETURNING {_ACCOUNT_COLUMNS}"
                ),
                {
                    "id": account_id,
                    "enterprise_id": tenant.enterprise_id,
                    "display_name": display_name,
                    "stage": stage,
                    "owner_user_id": owner_user_id,
                    "industry_note": industry_note,
                    "region_note": region_note,
                    "next_follow_up_at": next_follow_up_at,
                    "actor_id": actor_id,
                },
            )
        ).mappings().one()
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "crm.account.created",
            "crm_account",
            str(account_id),
        )
        await session.commit()
    return _account_out(row, tenant, actor_id)


async def update_account(
    tenant: Tenant,
    account_id: uuid.UUID,
    changes: dict[str, Any],
) -> dict[str, Any]:
    if not changes:
        raise HTTPException(status_code=422, detail="CRM_ACCOUNT_NO_CHANGES")
    allowed = {
        "display_name",
        "stage",
        "owner_user_id",
        "industry_note",
        "region_note",
        "next_follow_up_at",
    }
    if not set(changes).issubset(allowed):
        raise HTTPException(status_code=422, detail="CRM_ACCOUNT_FIELD_INVALID")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        current = await _account_row(session, account_id, lock=True)
        if not _can_manage_account(tenant, current, actor_id):
            raise HTTPException(status_code=403, detail="CRM_ACCOUNT_EDIT_FORBIDDEN")
        if "owner_user_id" in changes:
            if not is_manager(tenant.role):
                raise HTTPException(
                    status_code=403, detail="CRM_ACCOUNT_OWNER_CHANGE_FORBIDDEN"
                )
            if changes["owner_user_id"] is not None:
                await ensure_enterprise_member(
                    session,
                    enterprise_id=tenant.enterprise_id,
                    user_id=changes["owner_user_id"],
                )
        assignments = ", ".join(f"{name} = :{name}" for name in changes)
        parameters = {**changes, "account_id": account_id}
        row = (
            await session.execute(
                text(
                    f"UPDATE f1.crm_account SET {assignments}, "
                    "updated_at = statement_timestamp() "
                    "WHERE id = :account_id "
                    f"RETURNING {_ACCOUNT_COLUMNS}"
                ),
                parameters,
            )
        ).mappings().one_or_none()
        if row is None:
            raise HTTPException(status_code=409, detail="CRM_ACCOUNT_CONFLICT")
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "crm.account.updated",
            "crm_account",
            str(account_id),
        )
        await session.commit()
    return _account_out(row, tenant, actor_id)


async def create_contact(
    tenant: Tenant,
    account_id: uuid.UUID,
    *,
    display_name: str,
    role_title: str | None,
    email: str | None,
    phone: str | None,
    status: str,
) -> dict[str, Any]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        account = await _account_row(session, account_id, lock=True)
        if not _can_manage_account(tenant, account, actor_id):
            raise HTTPException(status_code=403, detail="CRM_CONTACT_CREATE_FORBIDDEN")
        contact_id = uuid.uuid4()
        row = (
            await session.execute(
                text(
                    "INSERT INTO f1.crm_contact ("
                    "id, enterprise_id, account_id, display_name, role_title, "
                    "email, phone, status, created_by_user_id) VALUES ("
                    ":id, :enterprise_id, :account_id, :display_name, "
                    ":role_title, :email, :phone, :status, :actor_id) "
                    f"RETURNING {_CONTACT_COLUMNS}"
                ),
                {
                    "id": contact_id,
                    "enterprise_id": tenant.enterprise_id,
                    "account_id": account_id,
                    "display_name": display_name,
                    "role_title": role_title,
                    "email": email,
                    "phone": phone,
                    "status": status,
                    "actor_id": actor_id,
                },
            )
        ).mappings().one()
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "crm.contact.created",
            "crm_contact",
            str(contact_id),
        )
        await session.commit()
    output = row_dict(row)
    output["allowed_actions"] = ["edit"]
    return output


async def update_contact(
    tenant: Tenant,
    contact_id: uuid.UUID,
    changes: dict[str, Any],
) -> dict[str, Any]:
    if not changes:
        raise HTTPException(status_code=422, detail="CRM_CONTACT_NO_CHANGES")
    allowed = {"display_name", "role_title", "email", "phone", "status"}
    if not set(changes).issubset(allowed):
        raise HTTPException(status_code=422, detail="CRM_CONTACT_FIELD_INVALID")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        current = (
            await session.execute(
                text(
                    f"SELECT contact.{_CONTACT_COLUMNS.replace(', ', ', contact.')}"
                    ", account.owner_user_id FROM f1.crm_contact AS contact "
                    "JOIN f1.crm_account AS account "
                    "ON account.enterprise_id = contact.enterprise_id "
                    "AND account.id = contact.account_id "
                    "WHERE contact.id = :contact_id FOR UPDATE OF contact"
                ),
                {"contact_id": contact_id},
            )
        ).mappings().one_or_none()
        if current is None:
            raise HTTPException(status_code=404, detail="CRM_CONTACT_NOT_FOUND")
        if not (
            is_manager(tenant.role) or current["owner_user_id"] == actor_id
        ):
            raise HTTPException(status_code=403, detail="CRM_CONTACT_EDIT_FORBIDDEN")
        assignments = ", ".join(f"{name} = :{name}" for name in changes)
        row = (
            await session.execute(
                text(
                    f"UPDATE f1.crm_contact SET {assignments}, "
                    "updated_at = statement_timestamp() "
                    "WHERE id = :contact_id "
                    f"RETURNING {_CONTACT_COLUMNS}"
                ),
                {**changes, "contact_id": contact_id},
            )
        ).mappings().one_or_none()
        if row is None:
            raise HTTPException(status_code=409, detail="CRM_CONTACT_CONFLICT")
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "crm.contact.updated",
            "crm_contact",
            str(contact_id),
        )
        await session.commit()
    output = row_dict(row)
    output["allowed_actions"] = ["edit"]
    return output


async def create_follow_up(
    tenant: Tenant,
    account_id: uuid.UUID,
    *,
    channel: str,
    summary: str,
    next_action: str | None,
    next_due_at: datetime | None,
    occurred_at: datetime | None,
) -> dict[str, Any]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        account = await _account_row(session, account_id, lock=True)
        if not _can_manage_account(tenant, account, actor_id):
            raise HTTPException(status_code=403, detail="CRM_FOLLOW_UP_FORBIDDEN")
        follow_up_id = uuid.uuid4()
        row = (
            await session.execute(
                text(
                    "INSERT INTO f1.crm_follow_up ("
                    "id, enterprise_id, account_id, channel, summary, next_action, "
                    "next_due_at, occurred_at, actor_user_id) VALUES ("
                    ":id, :enterprise_id, :account_id, :channel, :summary, "
                    ":next_action, :next_due_at, "
                    "COALESCE(:occurred_at, statement_timestamp()), :actor_id) "
                    f"RETURNING {_FOLLOW_UP_COLUMNS}"
                ),
                {
                    "id": follow_up_id,
                    "enterprise_id": tenant.enterprise_id,
                    "account_id": account_id,
                    "channel": channel,
                    "summary": summary,
                    "next_action": next_action,
                    "next_due_at": next_due_at,
                    "occurred_at": occurred_at,
                    "actor_id": actor_id,
                },
            )
        ).mappings().one()
        if next_due_at is not None:
            await session.execute(
                text(
                    "UPDATE f1.crm_account SET next_follow_up_at = :next_due_at, "
                    "updated_at = statement_timestamp() WHERE id = :account_id"
                ),
                {"next_due_at": next_due_at, "account_id": account_id},
            )
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "crm.follow_up.created",
            "crm_follow_up",
            str(follow_up_id),
        )
        await session.commit()
    output = row_dict(row)
    output["allowed_actions"] = ["view"]
    return output


__all__ = (
    "create_account",
    "create_contact",
    "create_follow_up",
    "get_account",
    "list_accounts",
    "update_account",
    "update_contact",
)
