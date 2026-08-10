"""Exact local-only database identities for F0-I.

The credentials are inherited in memory from the already frozen F0-G local
Fixture configuration.  This module never discovers a host, port, service or
passfile and never renders a DSN in an exception.
"""

from __future__ import annotations

import os
import re

from psycopg.conninfo import conninfo_to_dict

from ..database import DatabaseConfig
from ..f0g.__main__ import _config as _f0g_source_config
from ..f0_isolation import FrozenF0IsolationError, load_frozen_f0_isolation
from .contracts import F0IError


_FROZEN_F0_ISOLATION = load_frozen_f0_isolation()
ACCEPTANCE_DATABASE = (
    _FROZEN_F0_ISOLATION.f0i_template_database
    if _FROZEN_F0_ISOLATION is not None
    else "f0i_acceptance_v01"
)
SOURCE_DATABASE = (
    _FROZEN_F0_ISOLATION.f0g_template_database
    if _FROZEN_F0_ISOLATION is not None
    else "f0g_acceptance_v01"
)
_DSN_FIELDS = (
    ("migration_dsn", "f0d_migration", "F0I_MIGRATION_DSN"),
    ("runtime_dsn", "f0d_runtime", "F0I_RUNTIME_DSN"),
    ("worker_dsn", "f0d_worker", "F0I_WORKER_DSN"),
)
_DSN_KEYS = frozenset({"user", "password", "host", "port", "dbname"})
_LOCAL_HOSTS = frozenset({"127.0.0.1", "::1"})
_DATABASE_NAME = re.compile(
    r"(?:f0i_acceptance_v01|f0i_(?:test|verify)_[0-9a-f]{16})"
)


def _isolated_database_names() -> tuple[str, ...]:
    if _FROZEN_F0_ISOLATION is None:
        return ()
    return (
        _FROZEN_F0_ISOLATION.f0i_template_database,
        _FROZEN_F0_ISOLATION.database_name("f0i-migration"),
        _FROZEN_F0_ISOLATION.database_name("f0i-persistence"),
    )


def validate_local_database_config(config: DatabaseConfig) -> DatabaseConfig:
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
            raise F0IError("DATABASE_CONFIGURATION_INVALID") from None
    if not isinstance(config, DatabaseConfig):
        raise F0IError("DATABASE_CONFIGURATION_INVALID")
    names: set[str] = set()
    for field_name, expected_user, _ in _DSN_FIELDS:
        dsn = getattr(config, field_name, None)
        if (
            not isinstance(dsn, str)
            or not dsn.startswith("postgresql://")
            or "?" in dsn
            or "#" in dsn
            or any(character.isspace() for character in dsn)
        ):
            raise F0IError("DATABASE_CONFIGURATION_INVALID")
        try:
            parsed = conninfo_to_dict(dsn)
        except Exception:
            raise F0IError("DATABASE_CONFIGURATION_INVALID") from None
        if frozenset(parsed) != _DSN_KEYS:
            raise F0IError("DATABASE_CONFIGURATION_INVALID")
        values = {key: parsed.get(key) for key in _DSN_KEYS}
        if (
            any(not isinstance(value, str) or not value for value in values.values())
            or values["user"] != expected_user
            or values["host"] not in _LOCAL_HOSTS
            or values["port"] != "55432"
            or _DATABASE_NAME.fullmatch(str(values["dbname"])) is None
        ):
            raise F0IError("DATABASE_CONFIGURATION_INVALID")
        names.add(str(values["dbname"]))
    if len(names) != 1:
        raise F0IError("DATABASE_CONFIGURATION_INVALID")
    return config


def database_config(database_name: str = ACCEPTANCE_DATABASE) -> DatabaseConfig:
    if _FROZEN_F0_ISOLATION is not None:
        try:
            if database_name not in _isolated_database_names():
                raise FrozenF0IsolationError()
            return _FROZEN_F0_ISOLATION.database_config(database_name)
        except FrozenF0IsolationError:
            raise F0IError("DATABASE_CONFIGURATION_INVALID") from None
    if not isinstance(database_name, str) or _DATABASE_NAME.fullmatch(database_name) is None:
        raise F0IError("DATABASE_CONFIGURATION_INVALID")
    source = _f0g_source_config()
    values: dict[str, str] = {}
    for field_name, _, environment_name in _DSN_FIELDS:
        source_dsn = getattr(source, field_name)
        prefix, separator, current_name = source_dsn.rpartition("/")
        if not separator or current_name != SOURCE_DATABASE:
            raise F0IError("DATABASE_CONFIGURATION_INVALID")
        default = prefix + "/" + database_name
        values[field_name] = os.environ.get(environment_name, default)
    return validate_local_database_config(DatabaseConfig(**values))


__all__ = (
    "ACCEPTANCE_DATABASE",
    "SOURCE_DATABASE",
    "database_config",
    "validate_local_database_config",
)
