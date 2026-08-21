"""Independent Alembic environment for the F1 schema (f1.*).

The F0 root env.py is frozen and pins ``version_table_schema='f0d'``, so an
F1 branch there would clobber the frozen ``f0d_0006`` head.  This env is a
separate Alembic: it owns only the ``f1`` schema and keeps its own version
table (``f1.alembic_version``).  The migration role is the existing
``f0d_migration`` (owner of the f1 schema); the API/worker never use it.
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text
from sqlalchemy.engine import Connection, make_url


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def _migration_url() -> str:
    value = os.environ.get("F1_MIGRATION_DSN", "")
    if not value:
        raise RuntimeError("F1_MIGRATION_DSN_REQUIRED")
    try:
        url = make_url(value)
    except (TypeError, ValueError):
        raise RuntimeError("F1_MIGRATION_DSN_INVALID") from None
    if (
        url.drivername != "postgresql+psycopg"
        or url.username != "f0d_migration"
        or not url.password
        or not url.host
        or url.port is None
        or not url.database
        or bool(url.query)
    ):
        raise RuntimeError("F1_MIGRATION_ROLE_REQUIRED")
    return value


def run_migrations_offline() -> None:
    context.configure(
        url=_migration_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        transactional_ddl=True,
        include_schemas=True,
        version_table_schema="f1",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied = config.attributes.get("connection")
    if supplied is not None:
        if not isinstance(supplied, Connection):
            raise RuntimeError("F1_EXTERNAL_CONNECTION_INVALID")
        identity = supplied.execute(
            text("SELECT current_user, session_user")
        ).one()
        if tuple(identity) != ("f0d_migration", "f0d_bootstrap"):
            raise RuntimeError("F1_EXTERNAL_CONNECTION_IDENTITY_MISMATCH")
        context.configure(
            connection=supplied,
            target_metadata=target_metadata,
            transactional_ddl=True,
            include_schemas=True,
            version_table_schema="f1",
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _migration_url().replace("%", "%%")
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            transactional_ddl=True,
            include_schemas=True,
            version_table_schema="f1",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
