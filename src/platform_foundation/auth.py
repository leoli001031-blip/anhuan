"""Opaque local-fixture session authentication."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field

from .database import DatabaseConfig, role_transaction


class AuthenticationError(PermissionError):
    def __init__(self, code: str = "LOCAL_SESSION_INVALID") -> None:
        self.code = code
        super().__init__(code)

    def to_dict(self) -> dict[str, str]:
        return {"error": "AUTHENTICATION_DENIED", "reason_code": self.code}


@dataclass(frozen=True, slots=True)
class SessionContext:
    enterprise_id: uuid.UUID
    actor_id: uuid.UUID
    session_token_sha256: str = field(repr=False)


def authenticate_local_session(
    config: DatabaseConfig, token: str
) -> SessionContext:
    if not isinstance(token, str) or not 16 <= len(token) <= 256:
        raise AuthenticationError()
    token_sha256 = hashlib.sha256(token.encode("utf-8")).hexdigest()
    try:
        with role_transaction(config, "f0d_runtime") as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT enterprise_id, actor_id "
                    "FROM f0d.authenticate_local_fixture_session(%s)",
                    (token_sha256,),
                )
                record = cursor.fetchone()
    except Exception:
        raise AuthenticationError("LOCAL_SESSION_UNAVAILABLE") from None
    if record is None:
        raise AuthenticationError()
    enterprise_id = record.get("enterprise_id")
    actor_id = record.get("actor_id")
    if not isinstance(enterprise_id, uuid.UUID) or not isinstance(actor_id, uuid.UUID):
        raise AuthenticationError("LOCAL_SESSION_UNAVAILABLE")
    return SessionContext(
        enterprise_id=enterprise_id,
        actor_id=actor_id,
        session_token_sha256=token_sha256,
    )


__all__ = (
    "AuthenticationError",
    "SessionContext",
    "authenticate_local_session",
)
