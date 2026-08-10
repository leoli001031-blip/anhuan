"""F1 async SQLAlchemy engine/session (PostgreSQL f1.* schema).

Connections use the low-privilege ``f1_api`` / ``f1_worker`` roles (never
the f0d_migration role and never BYPASSRLS).  Every tenant-scoped session
sets a transaction-local tenant context (``f1.enterprise_id`` /
``f1.sub``) derived from the authenticated OIDC ``sub``; the context dies
with the transaction so pooled connections never leak tenant scope.

The runtime role DSNs are built from the F1 host/port/db-name config plus
the role's own secret file — the migration DSN (``f0d_migration``) is never
parsed or used by the API/worker.  Only ``f1_api`` and ``f1_worker`` are
accepted as session roles.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator
from urllib.parse import quote

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import pg_database, pg_host, pg_port
from .secret_files import read_f1_secret_text

ROLE_PASSWORDS = {"f1_api": "f1_api_password", "f1_worker": "f1_worker_password"}
ROLE_PASSWORD_FILE_ENVS = {
    "f1_api": "F1_API_PASSWORD_FILE",
    "f1_worker": "F1_WORKER_PASSWORD_FILE",
}
_ALLOWED_ROLES = frozenset(ROLE_PASSWORDS)


class Base(DeclarativeBase):
    pass


def _role_dsn(role: str) -> str:
    if role not in ROLE_PASSWORDS:
        raise ValueError("F1_ROLE_INVALID")
    password = read_f1_secret_text(
        ROLE_PASSWORDS[role], file_env=ROLE_PASSWORD_FILE_ENVS[role]
    )
    return (
        f"postgresql+psycopg://{role}:{quote(password, safe='')}"
        f"@{pg_host()}:{pg_port()}/{pg_database()}"
    )


def _api_dsn() -> str:
    return _role_dsn("f1_api")


def _worker_dsn() -> str:
    return _role_dsn("f1_worker")


_engines: dict[str, object] = {}
_factories: dict[str, object] = {}


def _get_factory(role: str) -> async_sessionmaker[AsyncSession]:
    if role not in _ALLOWED_ROLES:
        raise ValueError("F1_ROLE_INVALID")
    if role not in _factories:
        dsn = _api_dsn() if role == "f1_api" else _worker_dsn()
        engine = create_async_engine(dsn, pool_pre_ping=True)
        _engines[role] = engine
        _factories[role] = async_sessionmaker(engine, expire_on_commit=False)
    return _factories[role]  # type: ignore[return-value]


async def _set_context(
    session: AsyncSession,
    enterprise_id: uuid.UUID | None,
    sub: str | None,
    task_id: uuid.UUID | None,
    lease_token: uuid.UUID | None,
) -> None:
    if enterprise_id is not None:
        await session.execute(
            text("SELECT set_config('f1.enterprise_id', :eid, true)"),
            {"eid": str(enterprise_id)},
        )
    if sub:
        await session.execute(
            text("SELECT set_config('f1.sub', :sub, true)"),
            {"sub": sub},
        )
    if task_id is not None:
        await session.execute(
            text("SELECT set_config('f1.task_id', :task_id, true)"),
            {"task_id": str(task_id)},
        )
    if lease_token is not None:
        await session.execute(
            text("SELECT set_config('f1.lease_token', :lease_token, true)"),
            {"lease_token": str(lease_token)},
        )


@asynccontextmanager
async def session_scope(
    *,
    role: str = "f1_api",
    enterprise_id: uuid.UUID | None = None,
    sub: str | None = None,
    task_id: uuid.UUID | None = None,
    lease_token: uuid.UUID | None = None,
) -> AsyncIterator[AsyncSession]:
    """Yield a session scoped to an optional tenant context.

    The tenant context is transaction-local (SET LOCAL), so a connection
    returned to the pool never carries another request's enterprise.
    """
    if role == "f1_api" and (task_id is not None or lease_token is not None):
        raise ValueError("F1_API_TASK_CONTEXT_FORBIDDEN")
    if role == "f1_worker" and enterprise_id is not None:
        if task_id is None or lease_token is None:
            raise ValueError("F1_WORKER_LEASE_CONTEXT_REQUIRED")
    if (task_id is None) != (lease_token is None):
        raise ValueError("F1_LEASE_CONTEXT_INCOMPLETE")
    factory = _get_factory(role)
    async with factory() as session:  # type: ignore[union-attr]
        try:
            if enterprise_id is not None or sub or task_id is not None:
                await _set_context(
                    session, enterprise_id, sub, task_id, lease_token
                )
            yield session
        finally:
            # Never leave an open transaction on a pooled connection.
            if session.is_active and session.get_transaction() is not None:
                await session.rollback()


__all__ = ("Base", "session_scope", "_api_dsn", "_worker_dsn")
