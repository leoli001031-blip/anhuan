"""User profile endpoints (bound to Keycloak sub)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from ...auth import current_user, memberships_for_sub, require_role
from ...database import session_scope
from ...models import UserProfile

router = APIRouter()


class UserOut(BaseModel):
    id: uuid.UUID
    keycloak_sub: str
    email: str


class MembershipOut(BaseModel):
    enterprise_id: uuid.UUID
    name: str
    role: str


async def _ensure_profile(sub: str, email: str) -> None:
    """Upsert the current user's profile under its own RLS scope."""
    async with session_scope(role="f1_api", sub=sub) as session:
        row = (
            await session.execute(
                select(UserProfile).where(UserProfile.keycloak_sub == sub)
            )
        ).scalars().first()
        if row is None:
            session.add(UserProfile(id=uuid.uuid4(), keycloak_sub=sub, email=email))
            await session.commit()


@router.get("/me", response_model=UserOut)
async def me(user: dict = Depends(current_user)) -> UserOut:
    sub = user["sub"]
    await _ensure_profile(sub, user.get("email", ""))
    async with session_scope(role="f1_api", sub=sub) as session:
        row = (
            await session.execute(
                select(UserProfile).where(UserProfile.keycloak_sub == sub)
            )
        ).scalars().first()
        return UserOut(id=row.id, keycloak_sub=row.keycloak_sub, email=row.email)


@router.get("/me/enterprises", response_model=list[MembershipOut])
async def my_enterprises(user: dict = Depends(current_user)) -> list[MembershipOut]:
    """The enterprises the authenticated user belongs to (for selection)."""
    memberships = await memberships_for_sub(user["sub"])
    return [
        MembershipOut(
            enterprise_id=uuid.UUID(m["enterprise_id"]),
            name=m["name"],
            role=m["role"],
        )
        for m in memberships
    ]


@router.get("", response_model=list[UserOut])
async def list_users(user: dict = Depends(require_role("super_admin"))) -> list[UserOut]:
    async with session_scope(role="f1_api") as session:
        rows = (await session.execute(UserProfile.__table__.select())).scalars().all()
    return [
        UserOut(id=r.id, keycloak_sub=r.keycloak_sub, email=r.email) for r in rows
    ]


__all__ = ("router", "UserOut", "MembershipOut", "_ensure_profile")
