"""Fail-closed local database configuration validation for F0-G."""

from __future__ import annotations

import re

from psycopg.conninfo import conninfo_to_dict

from ..database import DatabaseConfig
from ..f0_isolation import FrozenF0IsolationError, load_frozen_f0_isolation


_DSN_FIELDS = (
    ("migration_dsn", "f0d_migration"),
    ("runtime_dsn", "f0d_runtime"),
    ("worker_dsn", "f0d_worker"),
)
_DSN_KEYS = frozenset({"user", "password", "host", "port", "dbname"})
_LOCAL_HOSTS = frozenset({"127.0.0.1", "::1"})
_DATABASE_NAME = re.compile(
    r"(?:f0g_acceptance_v01|f0g_verify_[0-9a-f]{16}|"
    r"f0g_test_(?:[0-9a-f]{16}|(?:base|case)_[0-9a-f]{16}))"
)
_FROZEN_F0_ISOLATION = load_frozen_f0_isolation()


def _isolated_database_names() -> tuple[str, ...]:
    if _FROZEN_F0_ISOLATION is None:
        return ()
    return (
        _FROZEN_F0_ISOLATION.f0g_template_database,
        _FROZEN_F0_ISOLATION.database_name("f0g-base"),
        _FROZEN_F0_ISOLATION.database_name("f0g-case"),
    )


def validate_local_database_config(config: DatabaseConfig) -> DatabaseConfig:
    """Return *config* only when all three DSNs stay in the F0-G sandbox.

    The parsed libpq surface is deliberately exact: connection options,
    services, passfiles, hostaddr, multi-host targets and alternate URI schemes
    are not part of this local-only contract.
    """

    if _FROZEN_F0_ISOLATION is not None:
        try:
            validated = _FROZEN_F0_ISOLATION.validate_database_config(config)
            if not any(
                validated == _FROZEN_F0_ISOLATION.database_config(database)
                for database in _isolated_database_names()
            ):
                raise FrozenF0IsolationError()
            return validated
        except FrozenF0IsolationError:
            raise RuntimeError("DATABASE_CONFIGURATION_INVALID") from None
    if not isinstance(config, DatabaseConfig):
        raise RuntimeError("DATABASE_CONFIGURATION_INVALID")
    database_names: set[str] = set()
    for field_name, expected_user in _DSN_FIELDS:
        dsn = getattr(config, field_name, None)
        if (
            not isinstance(dsn, str)
            or not dsn.startswith("postgresql://")
            or "?" in dsn
            or "#" in dsn
            or any(character.isspace() for character in dsn)
        ):
            raise RuntimeError("DATABASE_CONFIGURATION_INVALID")
        try:
            parsed = conninfo_to_dict(dsn)
        except Exception:
            raise RuntimeError("DATABASE_CONFIGURATION_INVALID") from None
        if frozenset(parsed) != _DSN_KEYS:
            raise RuntimeError("DATABASE_CONFIGURATION_INVALID")
        values = {key: parsed.get(key) for key in _DSN_KEYS}
        if (
            any(not isinstance(value, str) or not value for value in values.values())
            or values["user"] != expected_user
            or values["host"] not in _LOCAL_HOSTS
            or values["port"] != "55432"
            or _DATABASE_NAME.fullmatch(values["dbname"]) is None
        ):
            raise RuntimeError("DATABASE_CONFIGURATION_INVALID")
        database_names.add(values["dbname"])
    if len(database_names) != 1:
        raise RuntimeError("DATABASE_CONFIGURATION_INVALID")
    return config


__all__ = ("validate_local_database_config",)
