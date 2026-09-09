"""Same-organization membership administration; the DB rechecks every write."""
from __future__ import annotations
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from ...auth import Tenant, tenant_from_header
from ...database import session_scope

router = APIRouter()


class MembershipAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: uuid.UUID


class MembershipRole(MembershipAction):
    role: str


def _manager(tenant: Tenant) -> None:
    if tenant.role != 'enterprise_admin' or tenant.business_kind not in ('service_provider', 'client'):
        raise HTTPException(404, detail='MEMBERSHIP_NOT_FOUND')


def _error(error: SQLAlchemyError) -> HTTPException:
    message = str(getattr(error, 'orig', error))
    for code in ('MEMBERSHIP_NOT_FOUND', 'MEMBERSHIP_LAST_ADMIN', 'MEMBERSHIP_REQUEST_CONFLICT',
                 'MEMBERSHIP_ROLE_INVALID', 'MEMBERSHIP_TECHNICAL_ADMIN_PROTECTED'):
        if code in message:
            return HTTPException(404 if code.endswith('NOT_FOUND') else 409, detail=code)
    if getattr(getattr(error, 'orig', None), 'sqlstate', None) == '23505':
        return HTTPException(409, detail='MEMBERSHIP_REQUEST_CONFLICT')
    return HTTPException(503, detail='MEMBERSHIP_UNAVAILABLE')


@router.get('')
async def read(tenant: Tenant = Depends(tenant_from_header)) -> dict:
    _manager(tenant)
    try:
        async with session_scope(role='f1_api', enterprise_id=tenant.enterprise_id, sub=tenant.sub) as session:
            return (await session.execute(text('SELECT f1.read_memberships()'))).scalar_one()
    except SQLAlchemyError as error:
        raise _error(error) from None


async def _change(member_id: uuid.UUID, body: MembershipAction, action: str, tenant: Tenant) -> dict:
    _manager(tenant)
    try:
        async with session_scope(role='f1_api', enterprise_id=tenant.enterprise_id, sub=tenant.sub) as session:
            result = (await session.execute(text('SELECT f1.manage_membership(:member,:request,:action,:role)'),
                {'member': member_id, 'request': body.request_id, 'action': action,
                 'role': body.role if isinstance(body, MembershipRole) else None})).scalar_one()
            await session.commit()
            return result
    except SQLAlchemyError as error:
        raise _error(error) from None


@router.post('/{member_id}/role')
async def change_role(member_id: uuid.UUID, body: MembershipRole, tenant: Tenant = Depends(tenant_from_header)) -> dict:
    return await _change(member_id, body, 'role', tenant)


@router.post('/{member_id}/revoke')
async def revoke(member_id: uuid.UUID, body: MembershipAction, tenant: Tenant = Depends(tenant_from_header)) -> dict:
    return await _change(member_id, body, 'revoke', tenant)


@router.post('/{member_id}/restore')
async def restore(member_id: uuid.UUID, body: MembershipAction, tenant: Tenant = Depends(tenant_from_header)) -> dict:
    return await _change(member_id, body, 'restore', tenant)
