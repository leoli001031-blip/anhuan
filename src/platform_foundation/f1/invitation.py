"""F1 invitation flow: one-time JWT invite links (24h) with jti tracking.

An invite is minted with a ``jti`` and persisted to ``f1.invite_jti`` (the
single-use ledger).  Consumption atomically marks the jti consumed; a second
consume of the same invite is rejected.  No plaintext email/role is logged.
"""
from __future__ import annotations

import os
import stat
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jose import jwt, JWTError
from sqlalchemy import text

from .database import session_scope
INVITE_TTL_HOURS = 24
INVITE_KEY_ENV = "F1_INVITE_KEY_FILE"
ALGORITHM = "HS256"

ROLE_WHITELIST = {
    "enterprise_admin",
    "plant_admin",
    "partner",
    "auditor",
}


class InvitationError(RuntimeError):
    pass


def _load_invite_key() -> bytes:
    """Load a local signing key from one regular 0600 file, or fail closed."""
    raw_path = os.environ.get(INVITE_KEY_ENV, "").strip()
    if not raw_path:
        raw_dir = os.environ.get("F1_SECRETS_DIR", "").strip()
        if raw_dir:
            raw_path = str(Path(raw_dir) / "invite_signing_key")
    if not raw_path or not os.path.isabs(raw_path):
        raise InvitationError("INVITE_KEY_UNAVAILABLE")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(raw_path, flags)
    except OSError:
        raise InvitationError("INVITE_KEY_UNAVAILABLE") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise InvitationError("INVITE_KEY_UNAVAILABLE")
        if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid():
            raise InvitationError("INVITE_KEY_PERMISSIONS")
        if info.st_size < 32 or info.st_size > 4096:
            raise InvitationError("INVITE_KEY_INVALID")
        value = os.read(fd, info.st_size + 1)
    finally:
        os.close(fd)
    value = value.strip()
    if len(value) < 32:
        raise InvitationError("INVITE_KEY_INVALID")
    return value


@dataclass(frozen=True, slots=True)
class Invite:
    token: str
    jti: str
    email: str
    enterprise_id: uuid.UUID
    role: str
    expires_at: datetime


def _issue(token: str, claims: dict) -> Invite:
    return Invite(
        token=token,
        jti=str(claims["jti"]),
        email=str(claims["email"]),
        enterprise_id=uuid.UUID(str(claims["enterprise_id"])),
        role=str(claims["role"]),
        expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc),
    )


def _token_to_claims(token: str) -> dict:
    try:
        claims = jwt.decode(token, _load_invite_key(), algorithms=[ALGORITHM])
    except InvitationError:
        raise
    except JWTError:
        raise InvitationError("INVALID_INVITE") from None
    if claims.get("sub") != "invite":
        raise InvitationError("INVALID_INVITE")
    if claims.get("role") not in ROLE_WHITELIST:
        raise InvitationError("INVALID_INVITE")
    for required in ("jti", "email", "enterprise_id", "exp"):
        if not claims.get(required):
            raise InvitationError("INVALID_INVITE")
    try:
        uuid.UUID(str(claims["enterprise_id"]))
    except ValueError:
        raise InvitationError("INVALID_INVITE") from None
    return claims


def validate_invite(token: str) -> dict:
    """Validate the invite JWT; returns its claims or raises InvitationError."""
    return _token_to_claims(token)


async def create_invite(
    enterprise_id: uuid.UUID,
    email: str,
    role: str,
    *,
    user_sub: str,
    expires_at: datetime | None = None,
) -> Invite:
    """Mint and persist a single-use invite under the tenant scope."""
    if role not in ROLE_WHITELIST:
        raise InvitationError("INVALID_ROLE")
    jti = uuid.uuid4().hex
    raw_exp = expires_at or (datetime.now(timezone.utc) + timedelta(hours=INVITE_TTL_HOURS))
    # Normalize to whole seconds so the ledger row exactly equals the token's
    # ``exp`` claim (avoids microsecond drift breaking field-by-field checks).
    exp = datetime.fromtimestamp(int(raw_exp.timestamp()), tz=timezone.utc)
    payload = {
        "sub": "invite",
        "jti": jti,
        "email": email,
        "enterprise_id": str(enterprise_id),
        "role": role,
        "exp": int(exp.timestamp()),
    }
    normalized_email = email.strip().lower()
    if not normalized_email or len(normalized_email) > 320:
        raise InvitationError("INVALID_EMAIL")
    payload["email"] = normalized_email
    token = jwt.encode(payload, _load_invite_key(), algorithm=ALGORITHM)
    from sqlalchemy.exc import SQLAlchemyError

    try:
        async with session_scope(
            role="f1_api", enterprise_id=enterprise_id, sub=user_sub
        ) as session:
            result = await session.execute(
                text(
                    "SELECT f1.create_invite_for_current_sub"
                    "(:jti, :email, :role, :expires_at)"
                ),
                {
                    "jti": jti,
                    "email": normalized_email,
                    "role": role,
                    "expires_at": exp,
                },
            )
            if result.scalar_one_or_none() is not True:
                await session.rollback()
                raise InvitationError("INVITE_CREATE_FAILED")
            await session.commit()
    except InvitationError:
        raise
    except SQLAlchemyError as error:
        raise InvitationError(_map_consume_error(error)) from None
    return _issue(token, payload)


async def consume_invite(
    token: str,
    *,
    user_sub: str,
    oidc_email: str,
) -> Invite:
    """Validate and atomically consume a single-use invite.

    Runs entirely inside the SECURITY DEFINER ``f1.consume_invite`` which
    verifies every claim field-by-field against the ``invite_jti`` ledger,
    derives the sub from transaction-local OIDC context, verifies the OIDC
    email against the ledger, never overrides an existing membership role,
    and writes the audit row in the same transaction.  A second consume is
    rejected with zero partial side effects.
    """
    if not isinstance(user_sub, str) or not user_sub:
        raise InvitationError("OIDC_IDENTITY_REQUIRED")
    if not isinstance(oidc_email, str) or not oidc_email.strip():
        raise InvitationError("INVITE_IDENTITY_MISMATCH")
    claims = _token_to_claims(token)
    from datetime import datetime, timezone

    expires_at = datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
    from sqlalchemy.exc import SQLAlchemyError

    try:
        async with session_scope(role="f1_api", sub=user_sub) as session:
            result = await session.execute(
                text(
                    "SELECT out_jti, out_enterprise_id, out_email, out_role "
                    "FROM f1.consume_invite(:jti, :email, :role, :eid, :exp, "
                    ":oidc_email)"
                ),
                {
                    "jti": claims["jti"],
                    "email": claims["email"],
                    "role": claims["role"],
                    "eid": claims["enterprise_id"],
                    "exp": expires_at,
                    "oidc_email": oidc_email.strip().lower(),
                },
            )
            row = result.fetchone()
            await session.commit()
    except SQLAlchemyError as error:
        raise InvitationError(_map_consume_error(error)) from None
    if row is None:
        raise InvitationError("INVITE_CONSUME_FAILED")
    return _issue(token, claims)


def _map_consume_error(error) -> str:
    """Extract the ledger rejection reason from a raised consume error."""
    message = str(getattr(error, "orig", error))
    for code in (
        "INVITE_NOT_FOUND",
        "INVITE_ALREADY_USED",
        "INVITE_CLAIMS_MISMATCH",
        "INVITE_EXPIRED",
        "OIDC_IDENTITY_REQUIRED",
        "INVITE_IDENTITY_MISMATCH",
        "MEMBERSHIP_ALREADY_EXISTS",
        "INVITE_FORBIDDEN",
        "INVITE_ROLE_ESCALATION",
    ):
        if code in message:
            return code
    return "INVITE_CONSUME_FAILED"


__all__ = (
    "Invite",
    "InvitationError",
    "create_invite",
    "consume_invite",
    "validate_invite",
    "INVITE_TTL_HOURS",
    "INVITE_KEY_ENV",
)
