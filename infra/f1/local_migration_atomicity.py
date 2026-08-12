"""Prove F0D + F1 migration rollback in one exact scratch database.

The command is intentionally database-local and body-free.  It accepts only a
fully migrated ``anhuan_closeout_*`` source database, creates one random
scratch database in that same PostgreSQL cluster, injects the in-process
``after_f1_upgrade`` failure, proves that no F0/F1 schema objects survived,
then performs and verifies a normal migration.  The exact scratch database is
always dropped before a success tag can be emitted.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

SOURCE_DATABASE_RE = re.compile(r"anhuan_closeout_([0-9a-f]{24})\Z")
SCRATCH_DATABASE_RE = re.compile(
    r"anhuan_atomicity_([0-9a-f]{12})_([0-9a-f]{16})\Z"
)
SECRET_NAMES = (
    "f0d_migration_dsn",
    "f1_bootstrap_dsn",
    "f1_migration_dsn",
    "f1_api_password",
    "f1_worker_password",
)

FAILURE_REASONS = frozenset(
    {
        "LOCAL_ATOMICITY_SOURCE_INVALID",
        "LOCAL_ATOMICITY_SOURCE_NOT_READY",
        "LOCAL_ATOMICITY_SCRATCH_CREATE_FAILED",
        "LOCAL_ATOMICITY_SCRATCH_IDENTITY_MISMATCH",
        "LOCAL_ATOMICITY_FAILURE_MIGRATION_FAILED",
        "LOCAL_ATOMICITY_FAILPOINT_NOT_OBSERVED",
        "LOCAL_ATOMICITY_ROLLBACK_MISMATCH",
        "LOCAL_ATOMICITY_NORMAL_MIGRATION_FAILED",
        "LOCAL_ATOMICITY_HEAD_MISMATCH",
        "LOCAL_ATOMICITY_CLEANUP_FAILED",
        "LOCAL_ATOMICITY_INTERNAL_ERROR",
    }
)


class AtomicityError(RuntimeError):
    """A fixed, non-sensitive atomicity failure."""

    def __init__(self, reason: str) -> None:
        safe_reason = (
            reason if reason in FAILURE_REASONS else "LOCAL_ATOMICITY_INTERNAL_ERROR"
        )
        super().__init__(safe_reason)
        self.reason = safe_reason


class _InjectedFailure(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SourceConfiguration:
    database: str
    bootstrap_dsn: str
    f0_migration_dsn: str
    f1_migration_dsn: str
    f1_api_password: str
    f1_worker_password: str
    protected_roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RollbackObservation:
    f0_schema_count: int
    f1_schema_count: int
    version_table_count: int
    business_relation_count: int
    business_routine_count: int


@dataclass(frozen=True, slots=True)
class AtomicityCounts:
    rollback_f0_schema_count: int = 0
    rollback_f1_schema_count: int = 0
    rollback_version_table_count: int = 0
    rollback_business_relation_count: int = 0
    rollback_business_routine_count: int = 0
    normal_migration_head_count: int = 2
    scratch_database_residual_count: int = 0


def scratch_database_name(source_database: str, nonce: str) -> str:
    """Return a project-bound scratch name from caller-supplied hex entropy."""
    source_match = SOURCE_DATABASE_RE.fullmatch(source_database)
    if source_match is None or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise AtomicityError("LOCAL_ATOMICITY_SOURCE_INVALID")
    name = (
        f"anhuan_atomicity_{source_match.group(1)[:12]}_{nonce[:16]}"
    )
    if SCRATCH_DATABASE_RE.fullmatch(name) is None:
        raise AtomicityError("LOCAL_ATOMICITY_SOURCE_INVALID")
    return name


def rewrite_dsn_database(
    dsn: str,
    *,
    database: str,
    expected_user: str,
) -> str:
    """Rewrite only the database component of an already bounded DSN."""
    from sqlalchemy.engine import make_url

    if SCRATCH_DATABASE_RE.fullmatch(database) is None:
        raise AtomicityError("LOCAL_ATOMICITY_SOURCE_INVALID")
    try:
        value = make_url(dsn)
    except (TypeError, ValueError):
        raise AtomicityError("LOCAL_ATOMICITY_SOURCE_INVALID") from None
    if (
        value.drivername not in {"postgresql", "postgresql+psycopg"}
        or value.username != expected_user
        or not value.password
        or not value.host
        or value.port is None
        or not value.database
        or bool(value.query)
    ):
        raise AtomicityError("LOCAL_ATOMICITY_SOURCE_INVALID")
    return value.set(
        drivername="postgresql",
        database=database,
    ).render_as_string(hide_password=False)


def verify_rollback_observation(observation: RollbackObservation) -> None:
    if observation != RollbackObservation(0, 0, 0, 0, 0):
        raise AtomicityError("LOCAL_ATOMICITY_ROLLBACK_MISMATCH")


def verify_normal_heads(
    f0_heads: tuple[str, ...],
    f1_heads: tuple[str, ...],
) -> None:
    if f0_heads != ("f0d_0006",) or f1_heads != ("f1_0011",):
        raise AtomicityError("LOCAL_ATOMICITY_HEAD_MISMATCH")


def render_success(counts: AtomicityCounts) -> tuple[str, str]:
    metrics = json.dumps(asdict(counts), separators=(",", ":"), sort_keys=True)
    return metrics, "LOCAL_MIGRATION_ATOMICITY_OK"


def _load_source_configuration() -> SourceConfiguration:
    try:
        from sqlalchemy.engine import make_url

        from infra.f1 import local_migrate, migrate_f1
        from platform_foundation.f1.config import pg_database

        database = pg_database()
        if SOURCE_DATABASE_RE.fullmatch(database) is None:
            raise AtomicityError("LOCAL_ATOMICITY_SOURCE_INVALID")
        bootstrap_dsn = migrate_f1._bootstrap_dsn()
        f0_url = local_migrate._root_migration_url().set(drivername="postgresql")
        f1_url = make_url(migrate_f1._migration_dsn()).set(
            drivername="postgresql"
        )
        protected_roles = tuple(
            sorted((*migrate_f1.DEFINER_ROLES, *migrate_f1.ROLES))
        )
        return SourceConfiguration(
            database=database,
            bootstrap_dsn=bootstrap_dsn,
            f0_migration_dsn=f0_url.render_as_string(hide_password=False),
            f1_migration_dsn=f1_url.render_as_string(hide_password=False),
            f1_api_password=migrate_f1._read_secret("f1_api_password"),
            f1_worker_password=migrate_f1._read_secret("f1_worker_password"),
            protected_roles=protected_roles,
        )
    except AtomicityError:
        raise
    except BaseException:
        raise AtomicityError("LOCAL_ATOMICITY_SOURCE_INVALID") from None


def _assert_source_ready(configuration: SourceConfiguration) -> None:
    import psycopg

    try:
        with psycopg.connect(
            configuration.bootstrap_dsn,
            autocommit=True,
        ) as connection:
            identity = connection.execute(
                "SELECT current_user,session_user,current_database()"
            ).fetchone()
            if identity is None or tuple(identity) != (
                "f0d_bootstrap",
                "f0d_bootstrap",
                configuration.database,
            ):
                raise AtomicityError("LOCAL_ATOMICITY_SOURCE_NOT_READY")
            f0_heads = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT version_num FROM f0d.alembic_version "
                    "ORDER BY version_num"
                ).fetchall()
            )
            f1_heads = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT version_num FROM f1.alembic_version "
                    "ORDER BY version_num"
                ).fetchall()
            )
            verify_normal_heads(f0_heads, f1_heads)
            roles = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT rolname FROM pg_roles WHERE rolname=ANY(%s) "
                    "ORDER BY rolname",
                    (list(configuration.protected_roles),),
                ).fetchall()
            )
            if roles != configuration.protected_roles:
                raise AtomicityError("LOCAL_ATOMICITY_SOURCE_NOT_READY")
    except AtomicityError:
        raise
    except BaseException:
        raise AtomicityError("LOCAL_ATOMICITY_SOURCE_NOT_READY") from None


def _write_secret(path: Path, value: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    raw = value.encode("ascii")
    descriptor = os.open(path, flags, 0o600)
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written < 1:
                raise AtomicityError("LOCAL_ATOMICITY_INTERNAL_ERROR")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.geteuid()
        or info.st_size != len(raw)
    ):
        raise AtomicityError("LOCAL_ATOMICITY_INTERNAL_ERROR")


def _scratch_secret_directory(
    configuration: SourceConfiguration,
    scratch_database: str,
) -> tuple[Path, str]:
    directory = Path(tempfile.mkdtemp(prefix="anhuan-atomicity-secrets-"))
    os.chmod(directory, 0o700)
    try:
        scratch_bootstrap = rewrite_dsn_database(
            configuration.bootstrap_dsn,
            database=scratch_database,
            expected_user="f0d_bootstrap",
        )
        values = {
            "f0d_migration_dsn": rewrite_dsn_database(
                configuration.f0_migration_dsn,
                database=scratch_database,
                expected_user="f0d_migration",
            ),
            "f1_bootstrap_dsn": scratch_bootstrap,
            "f1_migration_dsn": rewrite_dsn_database(
                configuration.f1_migration_dsn,
                database=scratch_database,
                expected_user="f0d_migration",
            ),
            "f1_api_password": configuration.f1_api_password,
            "f1_worker_password": configuration.f1_worker_password,
        }
        for name in SECRET_NAMES:
            _write_secret(directory / name, values[name])
        return directory, scratch_bootstrap
    except BaseException as original_error:
        try:
            shutil.rmtree(directory)
            if directory.exists():
                raise OSError
        except BaseException:
            raise AtomicityError("LOCAL_ATOMICITY_CLEANUP_FAILED") from None
        raise original_error


def _remove_secret_directory(directory: Path) -> None:
    try:
        info = directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.geteuid()
            or {entry.name for entry in directory.iterdir()} != set(SECRET_NAMES)
        ):
            raise AtomicityError("LOCAL_ATOMICITY_CLEANUP_FAILED")
        for name in SECRET_NAMES:
            file_info = (directory / name).lstat()
            if (
                not stat.S_ISREG(file_info.st_mode)
                or stat.S_ISLNK(file_info.st_mode)
                or file_info.st_nlink != 1
                or stat.S_IMODE(file_info.st_mode) != 0o600
                or file_info.st_uid != os.geteuid()
            ):
                raise AtomicityError("LOCAL_ATOMICITY_CLEANUP_FAILED")
        shutil.rmtree(directory)
        if directory.exists():
            raise AtomicityError("LOCAL_ATOMICITY_CLEANUP_FAILED")
    except AtomicityError:
        raise
    except BaseException:
        raise AtomicityError("LOCAL_ATOMICITY_CLEANUP_FAILED") from None


@contextlib.contextmanager
def _temporary_scratch_environment(
    *,
    scratch_database: str,
    secret_directory: Path,
    f0_migration_dsn: str,
) -> Iterator[None]:
    changes = {
        "F1_PG_DATABASE": scratch_database,
        "F1_SECRETS_DIR": str(secret_directory),
        "F0D_MIGRATION_DSN": f0_migration_dsn,
    }
    previous = {name: os.environ.get(name) for name in changes}
    os.environ.update(changes)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _create_scratch_database(
    configuration: SourceConfiguration,
    scratch_database: str,
) -> None:
    import psycopg
    from psycopg import sql

    try:
        with psycopg.connect(
            configuration.bootstrap_dsn,
            autocommit=True,
        ) as connection:
            exists = connection.execute(
                "SELECT count(*) FROM pg_database WHERE datname=%s",
                (scratch_database,),
            ).fetchone()
            if exists is None or int(exists[0]) != 0:
                raise AtomicityError("LOCAL_ATOMICITY_SCRATCH_CREATE_FAILED")
            connection.execute(
                sql.SQL(
                    "CREATE DATABASE {} OWNER f0d_migration TEMPLATE template0"
                ).format(sql.Identifier(scratch_database))
            )
    except AtomicityError:
        raise
    except BaseException:
        raise AtomicityError("LOCAL_ATOMICITY_SCRATCH_CREATE_FAILED") from None


def _harden_scratch_database(
    scratch_bootstrap_dsn: str,
    scratch_database: str,
) -> None:
    import psycopg
    from psycopg import sql

    try:
        with psycopg.connect(scratch_bootstrap_dsn, autocommit=True) as connection:
            identity = connection.execute(
                "SELECT current_user,session_user,current_database()"
            ).fetchone()
            if identity is None or tuple(identity) != (
                "f0d_bootstrap",
                "f0d_bootstrap",
                scratch_database,
            ):
                raise AtomicityError(
                    "LOCAL_ATOMICITY_SCRATCH_IDENTITY_MISMATCH"
                )
            connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            connection.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                    sql.Identifier(scratch_database)
                )
            )
    except AtomicityError:
        raise
    except BaseException:
        raise AtomicityError(
            "LOCAL_ATOMICITY_SCRATCH_IDENTITY_MISMATCH"
        ) from None


def _raise_injected_failure() -> None:
    raise _InjectedFailure("LOCAL_ATOMICITY_INJECTED")


@contextlib.contextmanager
def _scratch_f0_schema_bootstrap(local_migrate: object) -> Iterator[None]:
    """Put the init-script F0 schema bootstrap inside migrate's transaction.

    The normal local database receives ``f0d`` from ``00_roles.sql`` before
    Alembic starts.  A template0 scratch database deliberately does not.  This
    wrapper performs that same bootstrap through local_migrate's existing
    SQLAlchemy connection, after its outer transaction and migration-role
    switch have begun, so the injected failure must roll the schema back too.
    """
    original = getattr(local_migrate, "_upgrade_f0")

    def upgrade_with_schema(connection: object) -> None:
        connection.exec_driver_sql(
            "CREATE SCHEMA f0d AUTHORIZATION f0d_migration"
        )
        connection.exec_driver_sql("REVOKE ALL ON SCHEMA f0d FROM PUBLIC")
        original(connection)

    setattr(local_migrate, "_upgrade_f0", upgrade_with_schema)
    try:
        yield
    finally:
        if getattr(local_migrate, "_upgrade_f0") is not upgrade_with_schema:
            raise AtomicityError("LOCAL_ATOMICITY_INTERNAL_ERROR")
        setattr(local_migrate, "_upgrade_f0", original)


def _observe_rollback(scratch_bootstrap_dsn: str) -> RollbackObservation:
    import psycopg

    try:
        with psycopg.connect(scratch_bootstrap_dsn, autocommit=True) as connection:
            schemas = connection.execute(
                "SELECT count(*) FILTER (WHERE nspname='f0d'),"
                "count(*) FILTER (WHERE nspname='f1') "
                "FROM pg_namespace"
            ).fetchone()
            versions = connection.execute(
                "SELECT (to_regclass('f0d.alembic_version') IS NOT NULL)::int + "
                "(to_regclass('f1.alembic_version') IS NOT NULL)::int"
            ).fetchone()
            relations = connection.execute(
                "SELECT count(*) FROM pg_class AS c "
                "JOIN pg_namespace AS n ON n.oid=c.relnamespace "
                "WHERE n.nspname IN ('f0d','f1')"
            ).fetchone()
            routines = connection.execute(
                "SELECT count(*) FROM pg_proc AS p "
                "JOIN pg_namespace AS n ON n.oid=p.pronamespace "
                "WHERE n.nspname IN ('f0d','f1')"
            ).fetchone()
        if schemas is None or versions is None or relations is None or routines is None:
            raise AtomicityError("LOCAL_ATOMICITY_ROLLBACK_MISMATCH")
        return RollbackObservation(
            int(schemas[0]),
            int(schemas[1]),
            int(versions[0]),
            int(relations[0]),
            int(routines[0]),
        )
    except AtomicityError:
        raise
    except BaseException:
        raise AtomicityError("LOCAL_ATOMICITY_ROLLBACK_MISMATCH") from None


def _observe_heads(
    scratch_bootstrap_dsn: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    import psycopg

    try:
        with psycopg.connect(scratch_bootstrap_dsn, autocommit=True) as connection:
            f0_heads = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT version_num FROM f0d.alembic_version "
                    "ORDER BY version_num"
                ).fetchall()
            )
            f1_heads = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT version_num FROM f1.alembic_version "
                    "ORDER BY version_num"
                ).fetchall()
            )
        return f0_heads, f1_heads
    except BaseException:
        raise AtomicityError("LOCAL_ATOMICITY_HEAD_MISMATCH") from None


def _execute_probe(
    *,
    scratch_database: str,
    secret_directory: Path,
    scratch_bootstrap_dsn: str,
    scratch_f0_migration_dsn: str,
) -> None:
    from infra.f1 import local_migrate

    with _temporary_scratch_environment(
        scratch_database=scratch_database,
        secret_directory=secret_directory,
        f0_migration_dsn=scratch_f0_migration_dsn,
    ):
        with _scratch_f0_schema_bootstrap(local_migrate):
            try:
                local_migrate.migrate(after_f1_upgrade=_raise_injected_failure)
            except _InjectedFailure:
                pass
            except BaseException:
                raise AtomicityError(
                    "LOCAL_ATOMICITY_FAILURE_MIGRATION_FAILED"
                ) from None
            else:
                raise AtomicityError("LOCAL_ATOMICITY_FAILPOINT_NOT_OBSERVED")

            verify_rollback_observation(_observe_rollback(scratch_bootstrap_dsn))
            try:
                local_migrate.migrate()
            except BaseException:
                raise AtomicityError(
                    "LOCAL_ATOMICITY_NORMAL_MIGRATION_FAILED"
                ) from None
            verify_normal_heads(*_observe_heads(scratch_bootstrap_dsn))


def _drop_scratch_database(
    configuration: SourceConfiguration,
    scratch_database: str,
) -> None:
    import psycopg
    from psycopg import sql

    if SCRATCH_DATABASE_RE.fullmatch(scratch_database) is None:
        raise AtomicityError("LOCAL_ATOMICITY_CLEANUP_FAILED")
    try:
        with psycopg.connect(
            configuration.bootstrap_dsn,
            autocommit=True,
        ) as connection:
            row = connection.execute(
                "SELECT d.datname,r.rolname FROM pg_database AS d "
                "JOIN pg_roles AS r ON r.oid=d.datdba WHERE d.datname=%s",
                (scratch_database,),
            ).fetchone()
            if row is None or tuple(row) != (
                scratch_database,
                "f0d_migration",
            ):
                raise AtomicityError("LOCAL_ATOMICITY_CLEANUP_FAILED")
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(scratch_database)
                )
            )
            residual = connection.execute(
                "SELECT count(*) FROM pg_database WHERE datname=%s",
                (scratch_database,),
            ).fetchone()
            if residual is None or int(residual[0]) != 0:
                raise AtomicityError("LOCAL_ATOMICITY_CLEANUP_FAILED")
    except AtomicityError:
        raise
    except BaseException:
        raise AtomicityError("LOCAL_ATOMICITY_CLEANUP_FAILED") from None


def run() -> AtomicityCounts:
    configuration: SourceConfiguration | None = None
    scratch_database: str | None = None
    secret_directory: Path | None = None
    scratch_created = False
    pending_error: AtomicityError | None = None

    try:
        configuration = _load_source_configuration()
        _assert_source_ready(configuration)
        scratch_database = scratch_database_name(
            configuration.database,
            uuid.uuid4().hex,
        )
        _create_scratch_database(configuration, scratch_database)
        scratch_created = True
        secret_directory, scratch_bootstrap_dsn = _scratch_secret_directory(
            configuration,
            scratch_database,
        )
        scratch_f0_migration_dsn = rewrite_dsn_database(
            configuration.f0_migration_dsn,
            database=scratch_database,
            expected_user="f0d_migration",
        )
        _harden_scratch_database(scratch_bootstrap_dsn, scratch_database)
        _execute_probe(
            scratch_database=scratch_database,
            secret_directory=secret_directory,
            scratch_bootstrap_dsn=scratch_bootstrap_dsn,
            scratch_f0_migration_dsn=scratch_f0_migration_dsn,
        )
    except AtomicityError as error:
        pending_error = error
    except BaseException:
        pending_error = AtomicityError("LOCAL_ATOMICITY_INTERNAL_ERROR")

    cleanup_failed = False
    if scratch_created and configuration is not None and scratch_database is not None:
        try:
            _drop_scratch_database(configuration, scratch_database)
        except BaseException:
            cleanup_failed = True
    if secret_directory is not None:
        try:
            _remove_secret_directory(secret_directory)
        except BaseException:
            cleanup_failed = True

    if cleanup_failed:
        raise AtomicityError("LOCAL_ATOMICITY_CLEANUP_FAILED")
    if pending_error is not None:
        raise pending_error
    return AtomicityCounts()


def main() -> int:
    try:
        counts = run()
    except BaseException as error:
        reason = (
            error.reason
            if isinstance(error, AtomicityError)
            else "LOCAL_ATOMICITY_INTERNAL_ERROR"
        )
        print(reason, file=sys.stderr)
        return 1
    for line in render_success(counts):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
