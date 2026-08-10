"""Fresh F0-I database bootstrap from the frozen read-only F0-G template."""

from __future__ import annotations

import os
from pathlib import Path
import re

from alembic import command
from alembic.config import Config
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from ..f0_isolation import FrozenF0IsolationError, load_frozen_f0_isolation
from .config import ACCEPTANCE_DATABASE, SOURCE_DATABASE, database_config
from .contracts import F0IError


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_COMPOSE_FILE = _PROJECT_ROOT / "infra/f0d/compose.yaml"
_SAFE_DATABASE = re.compile(r"f0i_(?:acceptance_v01|(?:test|verify)_[0-9a-f]{16})")
_FROZEN_F0_ISOLATION = load_frozen_f0_isolation()


def _database_allowed(database_name: object, *, drop: bool = False) -> bool:
    if _FROZEN_F0_ISOLATION is None:
        if not isinstance(database_name, str):
            return False
        pattern = (
            r"f0i_(?:test|verify)_[0-9a-f]{16}"
            if drop
            else r"f0i_(?:acceptance_v01|(?:test|verify)_[0-9a-f]{16})"
        )
        return re.fullmatch(pattern, database_name) is not None
    if not isinstance(database_name, str):
        return False
    scratch = {
        _FROZEN_F0_ISOLATION.database_name("f0i-migration"),
        _FROZEN_F0_ISOLATION.database_name("f0i-persistence"),
    }
    if drop:
        return database_name in scratch
    return database_name == _FROZEN_F0_ISOLATION.f0i_template_database or (
        database_name in scratch
    )


def ensure_database(database_name: str = ACCEPTANCE_DATABASE) -> bool:
    """Ensure exactly revision 0006 exists; return True only after a fresh clone."""

    if not _database_allowed(database_name):
        raise F0IError("DATABASE_CONFIGURATION_INVALID")
    bootstrap_dsn = _bootstrap_dsn()
    created = False
    try:
        with psycopg.connect(bootstrap_dsn, autocommit=True) as connection:
            rows = connection.execute(
                "SELECT datname FROM pg_database WHERE datname=ANY(%s) ORDER BY datname",
                ([SOURCE_DATABASE, database_name],),
            ).fetchall()
            present = {str(row[0]) for row in rows}
            if SOURCE_DATABASE not in present:
                raise F0IError("DATABASE_OPERATION_FAILED")
            if database_name not in present:
                active = connection.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname=%s",
                    (SOURCE_DATABASE,),
                ).fetchone()
                if active is None or int(active[0]) != 0:
                    raise F0IError("DATABASE_OPERATION_FAILED")
                connection.execute(
                    sql.SQL("CREATE DATABASE {} OWNER f0d_migration TEMPLATE {}").format(
                        sql.Identifier(database_name), sql.Identifier(SOURCE_DATABASE)
                    )
                )
                created = True
        if created:
            _upgrade(database_name)
        _verify_revision(database_name)
        return created
    except F0IError:
        raise
    except Exception:
        raise F0IError("DATABASE_OPERATION_FAILED") from None


def drop_scratch_database(database_name: str) -> None:
    if not _database_allowed(database_name, drop=True):
        raise F0IError("DATABASE_CONFIGURATION_INVALID")
    try:
        with psycopg.connect(_bootstrap_dsn(), autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(database_name)
                )
            )
    except Exception:
        raise F0IError("DATABASE_OPERATION_FAILED") from None


def _upgrade(database_name: str) -> None:
    config = database_config(database_name)
    previous = os.environ.get("F0D_MIGRATION_DSN")
    os.environ["F0D_MIGRATION_DSN"] = config.migration_dsn.replace(
        "postgresql://", "postgresql+psycopg://", 1
    )
    try:
        command.upgrade(Config(str(_PROJECT_ROOT / "alembic.ini")), "f0d_0006")
    finally:
        if previous is None:
            os.environ.pop("F0D_MIGRATION_DSN", None)
        else:
            os.environ["F0D_MIGRATION_DSN"] = previous


def _verify_revision(database_name: str) -> None:
    config = database_config(database_name)
    try:
        with psycopg.connect(config.migration_dsn, autocommit=True) as connection:
            rows = connection.execute(
                "SELECT version_num FROM f0d.alembic_version"
            ).fetchall()
        if [str(row[0]) for row in rows] != ["f0d_0006"]:
            raise F0IError("DATABASE_OPERATION_FAILED")
    except F0IError:
        raise
    except Exception:
        raise F0IError("DATABASE_OPERATION_FAILED") from None


def _bootstrap_dsn() -> str:
    if _FROZEN_F0_ISOLATION is not None:
        try:
            return _FROZEN_F0_ISOLATION.dsn_for("f0d_bootstrap", "postgres")
        except FrozenF0IsolationError:
            raise F0IError("DATABASE_CONFIGURATION_INVALID") from None
    try:
        metadata = _COMPOSE_FILE.lstat()
        if not _COMPOSE_FILE.is_file() or _COMPOSE_FILE.is_symlink() or metadata.st_size > 64 * 1024:
            raise F0IError("DATABASE_CONFIGURATION_INVALID")
        text = _COMPOSE_FILE.read_text(encoding="utf-8", errors="strict")
        values: dict[str, str] = {}
        for name in ("POSTGRES_USER", "POSTGRES_DB"):
            match = re.search(rf"^\s*{name}:\s*([^\s#]+)\s*$", text, re.MULTILINE)
            if match is None:
                raise F0IError("DATABASE_CONFIGURATION_INVALID")
            values[name] = match.group(1)
        if (
            values["POSTGRES_USER"] != "f0d_bootstrap"
            or values["POSTGRES_DB"] != "f0d"
        ):
            raise F0IError("DATABASE_CONFIGURATION_INVALID")
        password = (
            values["POSTGRES_USER"].removesuffix("_bootstrap").replace("_", "-")
            + "-bootstrap-local-v01"
        )
        return make_conninfo(
            user=values["POSTGRES_USER"],
            password=password,
            host="127.0.0.1",
            port="55432",
            dbname="postgres",
        )
    except F0IError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise F0IError("DATABASE_CONFIGURATION_INVALID") from None


__all__ = ("drop_scratch_database", "ensure_database")
