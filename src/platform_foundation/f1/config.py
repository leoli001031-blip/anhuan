"""F1 runtime config for host and isolated clean-checkout operation.

The API/worker run as Docker services reach the shared fixture services via
``host.docker.internal``; public loopback endpoints keep safe local defaults.
The database identity is always explicit.  No secrets or DSNs are printed.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit


_DATABASE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_$-]{0,62}\Z")


def _port(name: str, default: str) -> str:
    raw = os.environ.get(name, default).strip()
    try:
        value = int(raw, 10)
    except ValueError:
        raise RuntimeError(f"{name}_INVALID") from None
    if value < 1 or value > 65535:
        raise RuntimeError(f"{name}_INVALID")
    return str(value)


def _public_url(name: str, default: str) -> str:
    raw = os.environ.get(name, default).strip().rstrip("/")
    try:
        parsed = urlsplit(raw)
        parsed.port
    except ValueError:
        raise RuntimeError(f"{name}_INVALID") from None
    if (
        not raw.isascii()
        or any(character.isspace() for character in raw)
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(f"{name}_INVALID")
    return raw


def _origin_url(name: str, default: str = "") -> str:
    raw = os.environ.get(name, default).strip().rstrip("/")
    if not raw:
        raise RuntimeError(f"{name}_REQUIRED")
    value = _public_url(name, default)
    parsed = urlsplit(value)
    if parsed.path:
        raise RuntimeError(f"{name}_INVALID")
    return value


def pg_host() -> str:
    value = os.environ.get("F1_PG_HOST", "127.0.0.1").strip()
    if not value or any(character.isspace() for character in value):
        raise RuntimeError("F1_PG_HOST_INVALID")
    return value


def pg_port() -> str:
    return _port("F1_PG_PORT", "55432")


def pg_database() -> str:
    """Return the explicitly named fresh F1 database.

    There is deliberately no developer-machine default: a clean runner must
    name its own database instead of silently attaching to an acceptance DB.
    """
    value = os.environ.get("F1_PG_DATABASE", "").strip()
    if not value:
        raise RuntimeError("F1_PG_DATABASE_REQUIRED")
    if not _DATABASE_NAME.fullmatch(value):
        raise RuntimeError("F1_PG_DATABASE_INVALID")
    return value


def api_base_url() -> str:
    return _public_url("F1_API_BASE_URL", "http://127.0.0.1:8001")


def keycloak_url() -> str:
    return _origin_url("KEYCLOAK_URL", "http://127.0.0.1:8080")


def keycloak_realm() -> str:
    value = os.environ.get("F1_KEYCLOAK_REALM", "anhuan").strip()
    if (
        not value
        or "/" in value
        or any(character.isspace() for character in value)
    ):
        raise RuntimeError("F1_KEYCLOAK_REALM_INVALID")
    return value


def keycloak_client_id() -> str:
    value = os.environ.get("F1_KEYCLOAK_CLIENT_ID", "anhuan-web").strip()
    if not value or any(character.isspace() for character in value):
        raise RuntimeError("F1_KEYCLOAK_CLIENT_ID_INVALID")
    return value


def keycloak_issuer_url() -> str:
    """Return the explicit external issuer for the configured realm.

    The API may fetch JWKS through the internal ``KEYCLOAK_URL`` service
    origin, but a token is accepted only when its issuer is the independently
    configured public realm URL.
    """
    name = "F1_KEYCLOAK_ISSUER_URL"
    raw = os.environ.get(name, "").strip().rstrip("/")
    if not raw:
        raise RuntimeError(f"{name}_REQUIRED")
    value = _public_url(name, "")
    parsed = urlsplit(value)
    if parsed.path != f"/realms/{keycloak_realm()}":
        raise RuntimeError(f"{name}_INVALID")
    return value


def minio_endpoint() -> str:
    return os.environ.get("MINIO_ENDPOINT", "127.0.0.1:9000")


def redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")


def ragflow_base_url() -> str:
    return os.environ.get("RAGFLOW_BASE_URL", "http://127.0.0.1:80")


__all__ = (
    "api_base_url",
    "keycloak_client_id",
    "keycloak_issuer_url",
    "keycloak_realm",
    "pg_host",
    "pg_port",
    "pg_database",
    "keycloak_url",
    "minio_endpoint",
    "redis_url",
    "ragflow_base_url",
)
