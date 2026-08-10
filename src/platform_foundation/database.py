"""Small PostgreSQL connection boundary with transaction-local tenant scope."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Literal, Protocol

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row


RoleName = Literal["f0d_migration", "f0d_runtime", "f0d_worker"]


class TenantContext(Protocol):
    enterprise_id: uuid.UUID
    actor_id: uuid.UUID
    session_token_sha256: str


class DatabaseError(RuntimeError):
    """A redacted database error with a fixed reason code."""

    _CODES = frozenset(
        {
            "DATABASE_UNAVAILABLE",
            "DATABASE_ROLE_MISMATCH",
            "DATABASE_TRANSACTION_FAILED",
            "DATABASE_RLS_CONTEXT_INVALID",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            code = "DATABASE_TRANSACTION_FAILED"
        self.code = code
        super().__init__(code)

    def to_dict(self) -> dict[str, str]:
        return {"error": "DATABASE_ERROR", "reason_code": self.code}


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    migration_dsn: str
    runtime_dsn: str
    worker_dsn: str

    def dsn_for(self, role: RoleName) -> str:
        if role == "f0d_migration":
            return self.migration_dsn
        if role == "f0d_runtime":
            return self.runtime_dsn
        return self.worker_dsn


def _connect(config: DatabaseConfig, role: RoleName) -> Connection[dict[str, object]]:
    try:
        connection = psycopg.connect(
            config.dsn_for(role),
            autocommit=False,
            row_factory=dict_row,
            connect_timeout=5,
            application_name="anhuan-f0d-local",
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_user AS role")
            record = cursor.fetchone()
        if record is None or record.get("role") != role:
            connection.close()
            raise DatabaseError("DATABASE_ROLE_MISMATCH")
        connection.rollback()
        return connection
    except DatabaseError:
        raise
    except psycopg.Error:
        raise DatabaseError("DATABASE_UNAVAILABLE") from None


@contextmanager
def role_transaction(
    config: DatabaseConfig, role: RoleName
) -> Iterator[Connection[dict[str, object]]]:
    connection = _connect(config, role)
    try:
        with connection.transaction():
            yield connection
    except DatabaseError:
        raise
    except psycopg.Error:
        raise DatabaseError("DATABASE_TRANSACTION_FAILED") from None
    finally:
        connection.close()


@contextmanager
def tenant_transaction(
    config: DatabaseConfig,
    role: Literal["f0d_runtime", "f0d_worker"],
    context: TenantContext,
) -> Iterator[Connection[dict[str, object]]]:
    connection = _connect(config, role)
    try:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('f0d.enterprise_id', %s, true) AS enterprise_id, "
                    "set_config('f0d.actor_id', %s, true) AS actor_id, "
                    "set_config('f0d.session_token_sha256', %s, true) AS token_sha256",
                    (
                        str(context.enterprise_id),
                        str(context.actor_id),
                        context.session_token_sha256,
                    ),
                )
                record = cursor.fetchone()
                if record is None or record.get("enterprise_id") != str(
                    context.enterprise_id
                ) or record.get("actor_id") != str(
                    context.actor_id
                ) or record.get("token_sha256") != context.session_token_sha256:
                    raise DatabaseError("DATABASE_RLS_CONTEXT_INVALID")
                cursor.execute(
                    "SELECT f0d.context_session_authorized(%s) AS authorized",
                    (context.enterprise_id,),
                )
                authorization = cursor.fetchone()
                if authorization is None or not authorization.get("authorized"):
                    raise DatabaseError("DATABASE_RLS_CONTEXT_INVALID")
            yield connection
    except DatabaseError:
        raise
    except psycopg.Error:
        raise DatabaseError("DATABASE_TRANSACTION_FAILED") from None
    finally:
        connection.close()


def database_health(config: DatabaseConfig) -> dict[str, object]:
    try:
        connection = _connect(config, "f0d_runtime")
        try:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT current_setting('server_version_num')::integer AS version_num"
                    )
                    record = cursor.fetchone()
            version_num = int(record["version_num"]) if record else 0
        finally:
            connection.close()
    except (DatabaseError, KeyError, TypeError, ValueError):
        return {
            "status": "DEGRADED",
            "database": "UNAVAILABLE",
            "postgresql_major": None,
        }
    return {
        "status": "OK" if version_num // 10_000 == 18 else "DEGRADED",
        "database": "AVAILABLE",
        "postgresql_major": version_num // 10_000,
    }


__all__ = (
    "DatabaseConfig",
    "DatabaseError",
    "database_health",
    "role_transaction",
    "tenant_transaction",
)
