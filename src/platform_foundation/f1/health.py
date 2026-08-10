"""Body-free dependency readiness checks for the local engineering stack."""
from __future__ import annotations

import asyncio
import json
import urllib.request
from typing import Callable

from redis import Redis
from sqlalchemy import text

from .config import keycloak_url, pg_database, redis_url
from .database import session_scope
from .features.p3.scanner import scanner_version
from .storage import _client as minio_client


async def _database_ready() -> bool:
    try:
        async with session_scope(role="f1_api") as session:
            row = (
                await session.execute(
                    text(
                        "SELECT current_user,current_database(),"
                        "to_regclass('f1.rehearsal_run') IS NOT NULL"
                    )
                )
            ).one()
        return tuple(row) == ("f1_api", pg_database(), True)
    except Exception:  # noqa: BLE001 - dependency details stay out of responses
        return False


def _redis_ready() -> bool:
    try:
        return bool(Redis.from_url(redis_url(), socket_timeout=3).ping())
    except Exception:  # noqa: BLE001
        return False


def _minio_ready() -> bool:
    try:
        minio_client().list_buckets()
        return True
    except Exception:  # noqa: BLE001
        return False


def _clamd_ready() -> bool:
    try:
        version = scanner_version(timeout_seconds=3)
        return bool(version.engine_version and version.signature_version)
    except Exception:  # noqa: BLE001
        return False


def _oidc_ready() -> bool:
    endpoint = (
        keycloak_url()
        + "/realms/anhuan/protocol/openid-connect/certs"
    )
    try:
        with urllib.request.urlopen(endpoint, timeout=3) as response:
            payload = json.loads(response.read(262145))
        return response.status == 200 and bool(payload.get("keys"))
    except Exception:  # noqa: BLE001
        return False


async def readiness() -> dict[str, bool]:
    threaded: tuple[tuple[str, Callable[[], bool]], ...] = (
        ("clamd", _clamd_ready),
        ("minio", _minio_ready),
        ("oidc", _oidc_ready),
        ("redis", _redis_ready),
    )
    database, *values = await asyncio.gather(
        _database_ready(),
        *(asyncio.to_thread(check) for _name, check in threaded),
    )
    return {
        "database": bool(database),
        **{
            name: bool(value)
            for (name, _check), value in zip(threaded, values, strict=True)
        },
    }


__all__ = ("readiness",)
