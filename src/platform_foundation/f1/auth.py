"""F1 identity: Keycloak OIDC validation (RS256 via JWKS) + RBAC + tenant scope.

Every API token is verified against the Keycloak realm's JWKS with strict
issuer and azp checks.  The ``realm_access.roles`` claim is the role source.
For public-client tokens (``anhuan-web``) Keycloak emits no ``aud``; when an
``aud`` IS present it must be an accepted audience.  No client_secret is
involved (public/bearer-only clients).

Tenant scope: the authenticated ``sub`` resolves to its enterprise
memberships via the zero-argument security-definer
``f1.resolve_current_enterprises``; each
request runs under a transaction-local ``f1.enterprise_id`` (RLS boundary).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt
from sqlalchemy import text

from .database import session_scope

from .config import keycloak_issuer_url as _keycloak_issuer_url
from .config import keycloak_url as _keycloak_url

KEYCLOAK_URL = _keycloak_url()
REALM = "anhuan"
JWKS_URL = f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/certs"
# JWKS is fetched through the container-internal service origin, while the
# signed token must carry the explicit external issuer used by the browser.
ISSUER = _keycloak_issuer_url()
ACCEPTED_AZP = ("anhuan-web", "anhuan-api")
ACCEPTED_AUD = ("account", "anhuan-web", "anhuan-api")

_security = HTTPBearer(auto_error=False)

_jwks_cache: dict[str, Any] = {"keys": [], "fetched_at": 0.0}
_JWKS_TTL = 300.0


def _fetch_jwks() -> dict[str, Any]:
    now = time.monotonic()
    if _jwks_cache["keys"] and now - _jwks_cache["fetched_at"] < _JWKS_TTL:
        return _jwks_cache
    with urllib.request.urlopen(JWKS_URL, timeout=10) as response:
        data = json.loads(response.read())
    _jwks_cache["keys"] = data.get("keys", [])
    _jwks_cache["fetched_at"] = now
    return _jwks_cache


def _extract_roles(claims: dict[str, Any]) -> list[str]:
    realm_access = claims.get("realm_access") or {}
    roles = realm_access.get("roles") or []
    return [str(role) for role in roles if isinstance(role, str)]


def _valid_issuer(value: str | None) -> bool:
    return value == ISSUER


def _valid_azp(value: str | None) -> bool:
    return value in ACCEPTED_AZP


def _valid_audience(value: Any) -> bool:
    if value is None:
        return True  # public-client tokens omit aud
    if isinstance(value, str):
        audiences = [value]
    elif isinstance(value, (list, tuple)) and all(
        isinstance(item, str) for item in value
    ):
        audiences = list(value)
    else:
        return False
    return bool(audiences) and all(aud in ACCEPTED_AUD for aud in audiences)


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
) -> dict[str, Any]:
    """Verify the Bearer token (RS256, realm anhuan) and return claims."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing token"
        )
    token = credentials.credentials
    try:
        jwks = _fetch_jwks()
        header = jwt.get_unverified_header(token)
        key = next(
            (k for k in jwks["keys"] if k.get("kid") == header.get("kid")), None
        )
        if key is None:
            raise ValueError("kid not found")
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=ISSUER,
            options={"verify_aud": False},  # public-client tokens may omit aud
        )
    except Exception:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token"
        ) from None
    if not _valid_issuer(claims.get("iss")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid issuer"
        )
    if not _valid_azp(claims.get("azp")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid azp"
        )
    if not _valid_audience(claims.get("aud")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid audience"
        )
    claims["roles"] = _extract_roles(claims)
    return claims


def require_role(*roles: str):
    """Dependency factory: require at least one of the given realm roles."""

    async def checker(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if not any(role in user.get("roles", []) for role in roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="insufficient role"
            )
        return user

    return checker


@dataclass(frozen=True, slots=True)
class Tenant:
    enterprise_id: uuid.UUID
    sub: str
    roles: tuple[str, ...]
    role: str | None = None


async def memberships_for_sub(sub: str) -> list[dict[str, str]]:
    """Resolve the enterprise memberships of an OIDC ``sub`` (RLS-bypassing)."""
    if not isinstance(sub, str) or not sub:
        return []
    # The caller identity is transaction-local context.  The definer has no
    # caller-supplied sub argument, so it cannot be used as an identity
    # enumeration primitive.
    async with session_scope(role="f1_api", sub=sub) as session:
        result = await session.execute(
            text(
                "SELECT enterprise_id, name, role "
                "FROM f1.resolve_current_enterprises()"
            )
        )
        return [
            {
                "enterprise_id": str(row[0]),
                "name": str(row[1]),
                "role": str(row[2]),
            }
            for row in result.fetchall()
        ]


async def current_tenant(
    user: dict[str, Any] = Depends(current_user),
    enterprise_id: uuid.UUID | None = None,
) -> Tenant:
    """Resolve the request tenant from the authenticated sub.

    Cross-tenant access (a user requesting an enterprise they do not belong
    to) is deliberately a 404, never a 403, so existence of other tenants is
    not disclosed.
    """
    memberships = await memberships_for_sub(user["sub"])
    if not memberships:
        raise HTTPException(status_code=404, detail="no enterprise")
    if enterprise_id is not None:
        matches = [m for m in memberships if uuid.UUID(m["enterprise_id"]) == enterprise_id]
        if not matches:
            raise HTTPException(status_code=404, detail="enterprise not found")
        selected = matches[0]
    else:
        if len(memberships) > 1:
            raise HTTPException(status_code=400, detail="enterprise selection required")
        selected = memberships[0]
    return Tenant(
        enterprise_id=uuid.UUID(selected["enterprise_id"]),
        sub=user["sub"],
        roles=tuple(user.get("roles", [])),
        role=selected.get("role"),
    )


async def tenant_from_header(
    user: dict[str, Any] = Depends(current_user),
    x_enterprise_id: str | None = Header(default=None),
) -> Tenant:
    """Resolve the tenant from the ``X-Enterprise-Id`` request header."""
    enterprise_id: uuid.UUID | None = None
    if x_enterprise_id:
        try:
            enterprise_id = uuid.UUID(x_enterprise_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="invalid enterprise id"
            ) from None
    return await current_tenant(user, enterprise_id)


__all__ = (
    "KEYCLOAK_URL",
    "REALM",
    "JWKS_URL",
    "ISSUER",
    "current_user",
    "require_role",
    "Tenant",
    "memberships_for_sub",
    "current_tenant",
    "tenant_from_header",
)
