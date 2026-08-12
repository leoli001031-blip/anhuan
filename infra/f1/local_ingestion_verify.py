"""One-shot P3 ingestion verifier for the isolated local engineering stack.

The verifier reuses the already-running ``postgres``, ``minio`` and ``clamd``
services.  It creates one project-bound random database inside the dedicated
PostgreSQL cluster and three project-bound random MinIO buckets.  The product
router, low-privilege database roles, RLS policies, object IO, ClamAV INSTREAM
protocol, preview builders and release copy are all real.  Only OIDC token
verification is replaced at the ASGI dependency boundary.

Every created object and bucket is removed by exact identity before the exact
scratch database is dropped.  Output contains fixed aggregate integers and a
fixed status tag only; response bodies, filenames, object names, database
names, credentials and tracebacks are never rendered.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import io
import json
import os
import re
import secrets
import shutil
import socket
import stat
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))


SOURCE_DATABASE_RE = re.compile(r"anhuan_closeout_([0-9a-f]{24})\Z")
SCRATCH_DATABASE_RE = re.compile(
    r"anhuan_ingest_([0-9a-f]{12})_([0-9a-f]{16})\Z"
)
BUCKET_RE = re.compile(
    r"anhuan-ingest-([0-9a-f]{12})-([0-9a-f]{16})-([qpr])\Z"
)
DOCUMENT_OBJECT_RE = re.compile(r"[0-9a-f]{32}\.pdf\Z")
PREVIEW_OBJECT_RE = re.compile(r"[0-9a-f]{32}/[0-9a-f]{32}\.json\Z")

EXPECTED_POSTGRES_HOST = "postgres"
EXPECTED_POSTGRES_PORT = "5432"
EXPECTED_MINIO_ENDPOINT = "minio:9000"
EXPECTED_CLAMD_HOST = "clamd"
EXPECTED_CLAMD_PORT = 3310

STANDARD_BUCKETS = (
    "anhuan-f1-quarantine",
    "anhuan-f1-previews",
    "anhuan-f1-documents",
)

ENTERPRISE_A = uuid.UUID("20000000-0000-4000-8000-00000000000a")
ENTERPRISE_B = uuid.UUID("20000000-0000-4000-8000-00000000000b")
ADMIN_SUB = "d561ffe2-3be8-40cc-a87e-598dd7d84758"
TENANT_B_SUB = "ddc4e27e-ccde-4c89-958f-798fc8f30175"

SECRET_NAMES = (
    "f0d_migration_dsn",
    "f1_bootstrap_dsn",
    "f1_migration_dsn",
    "f1_api_password",
    "f1_worker_password",
    "minio_root_user",
    "minio_root_password",
)

EXPECTED_AUDIT_ACTIONS = frozenset(
    {
        "document.version.create",
        "document.quarantine",
        "document.version.process",
        "document.version.retry",
        "document.version.release",
    }
)

FAILURE_REASONS = frozenset(
    {
        "LOCAL_INGESTION_SOURCE_INVALID",
        "LOCAL_INGESTION_SOURCE_NOT_READY",
        "LOCAL_INGESTION_ENDPOINT_MISMATCH",
        "LOCAL_INGESTION_SCRATCH_CREATE_FAILED",
        "LOCAL_INGESTION_SCRATCH_IDENTITY_MISMATCH",
        "LOCAL_INGESTION_MIGRATION_FAILED",
        "LOCAL_INGESTION_SEED_FAILED",
        "LOCAL_INGESTION_RESOURCE_NAMESPACE_FAILED",
        "LOCAL_INGESTION_UPLOAD_FAILED",
        "LOCAL_INGESTION_PROCESS_FAILED",
        "LOCAL_INGESTION_PREVIEW_FAILED",
        "LOCAL_INGESTION_RELEASE_FAILED",
        "LOCAL_INGESTION_CROSS_TENANT_LEAK",
        "LOCAL_INGESTION_RLS_LEAK",
        "LOCAL_INGESTION_DATA_IDENTITY_MISMATCH",
        "LOCAL_INGESTION_OBJECT_IDENTITY_MISMATCH",
        "LOCAL_INGESTION_SOURCE_MUTATION",
        "LOCAL_INGESTION_OBJECT_CLEANUP_FAILED",
        "LOCAL_INGESTION_SCRATCH_CLEANUP_FAILED",
        "LOCAL_INGESTION_INTERNAL_ERROR",
    }
)


class IngestionVerifyError(RuntimeError):
    """A fixed, non-sensitive verifier failure."""

    def __init__(self, reason: str) -> None:
        safe_reason = (
            reason
            if reason in FAILURE_REASONS
            else "LOCAL_INGESTION_INTERNAL_ERROR"
        )
        super().__init__(safe_reason)
        self.reason = safe_reason


class _DiscardText:
    """A non-buffering sink for nested library diagnostics."""

    def write(self, value: str) -> int:
        return len(value)

    def flush(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class SourceConfiguration:
    database: str
    bootstrap_dsn: str
    f0_migration_dsn: str
    f1_migration_dsn: str
    f1_api_password: str
    f1_worker_password: str
    minio_root_user: str
    minio_root_password: str


@dataclass(frozen=True, slots=True)
class ResourceNames:
    quarantine_bucket: str
    preview_bucket: str
    released_bucket: str

    @property
    def buckets(self) -> tuple[str, str, str]:
        return (
            self.quarantine_bucket,
            self.preview_bucket,
            self.released_bucket,
        )


@dataclass(frozen=True, slots=True)
class RunIdentifiers:
    document_id: uuid.UUID
    version_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class TaskObjectIdentity:
    task_id: uuid.UUID
    object_key: str


@dataclass(frozen=True, slots=True)
class DataObservation:
    task_id: uuid.UUID
    object_key: str
    content_sha256: str
    source_size: int
    source_etag: str
    status: str
    object_state: str
    processing_stage: str
    quarantine_status: str
    scan_verdict: str
    scanner_engine: str
    scanner_version: str
    signature_version: str
    preview_status: str
    preview_kind: str
    preview_sha256: str
    preview_unit_count: int
    released: bool
    audit_actions: frozenset[str]


@dataclass(frozen=True, slots=True)
class IngestionVerificationCounts:
    source_database_mutation_count: int = 0
    scratch_migration_head_count: int = 2
    uploaded_version_count: int = 1
    minio_write_failure_count: int = 1
    idempotent_upload_recovery_count: int = 1
    scanner_unavailable_count: int = 1
    scanner_retry_recovery_count: int = 1
    clean_scan_count: int = 1
    preview_unit_count: int = 1
    released_object_count: int = 1
    cross_tenant_api_visible_count: int = 0
    cross_tenant_rls_visible_count: int = 0
    audit_action_count: int = 5
    object_sha_mismatch_count: int = 0
    object_residual_count: int = 0
    bucket_residual_count: int = 0
    scratch_database_residual_count: int = 0


def scratch_database_name(source_database: str, nonce: str) -> str:
    """Derive a unique scratch name bound to the closeout project database."""
    match = SOURCE_DATABASE_RE.fullmatch(source_database)
    if match is None or re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        raise IngestionVerifyError("LOCAL_INGESTION_SOURCE_INVALID")
    name = f"anhuan_ingest_{match.group(1)[:12]}_{nonce[:16]}"
    if SCRATCH_DATABASE_RE.fullmatch(name) is None:
        raise IngestionVerifyError("LOCAL_INGESTION_SOURCE_INVALID")
    return name


def resource_names(source_database: str, nonce: str) -> ResourceNames:
    """Derive three unique S3 bucket names from project and run identities."""
    match = SOURCE_DATABASE_RE.fullmatch(source_database)
    if match is None or re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        raise IngestionVerifyError("LOCAL_INGESTION_SOURCE_INVALID")
    stem = f"anhuan-ingest-{match.group(1)[:12]}-{nonce[:16]}"
    resources = ResourceNames(
        quarantine_bucket=f"{stem}-q",
        preview_bucket=f"{stem}-p",
        released_bucket=f"{stem}-r",
    )
    if (
        len(set(resources.buckets)) != 3
        or any(BUCKET_RE.fullmatch(name) is None for name in resources.buckets)
    ):
        raise IngestionVerifyError("LOCAL_INGESTION_SOURCE_INVALID")
    return resources


def rewrite_dsn_database(
    dsn: str,
    *,
    database: str,
    expected_user: str,
) -> str:
    """Change only the database of a bounded project-local PostgreSQL DSN."""
    from sqlalchemy.engine import make_url

    if SCRATCH_DATABASE_RE.fullmatch(database) is None:
        raise IngestionVerifyError("LOCAL_INGESTION_SOURCE_INVALID")
    try:
        value = make_url(dsn)
    except (TypeError, ValueError):
        raise IngestionVerifyError("LOCAL_INGESTION_SOURCE_INVALID") from None
    if (
        value.drivername not in {"postgresql", "postgresql+psycopg"}
        or value.username != expected_user
        or not value.password
        or value.host != EXPECTED_POSTGRES_HOST
        or value.port != int(EXPECTED_POSTGRES_PORT)
        or SOURCE_DATABASE_RE.fullmatch(str(value.database or "")) is None
        or bool(value.query)
    ):
        raise IngestionVerifyError("LOCAL_INGESTION_SOURCE_INVALID")
    return value.set(
        drivername="postgresql",
        database=database,
    ).render_as_string(hide_password=False)


def verify_data_observation(
    observation: DataObservation,
    *,
    expected_sha256: str,
    expected_size: int,
) -> None:
    """Validate the final P3 row and its body-free audit contract."""
    if (
        not DOCUMENT_OBJECT_RE.fullmatch(observation.object_key)
        or observation.content_sha256 != expected_sha256
        or observation.source_size != expected_size
        or not observation.source_etag
        or observation.status != "done"
        or observation.object_state != "ready"
        or observation.processing_stage != "ready"
        or observation.quarantine_status != "released"
        or observation.scan_verdict != "clean"
        or observation.scanner_engine != "clamav"
        or not observation.scanner_version
        or not observation.signature_version
        or observation.preview_status != "ready"
        or observation.preview_kind != "page_text"
        or re.fullmatch(r"[0-9a-f]{64}", observation.preview_sha256) is None
        or observation.preview_unit_count != 1
        or not observation.released
        or observation.audit_actions != EXPECTED_AUDIT_ACTIONS
    ):
        raise IngestionVerifyError(
            "LOCAL_INGESTION_DATA_IDENTITY_MISMATCH"
        )


def render_success(
    counts: IngestionVerificationCounts,
) -> tuple[str, str]:
    metrics = json.dumps(asdict(counts), separators=(",", ":"), sort_keys=True)
    return metrics, "LOCAL_INGESTION_VERIFY_OK"


def _load_source_configuration() -> SourceConfiguration:
    try:
        from sqlalchemy.engine import make_url

        from infra.f1 import local_migrate, migrate_f1
        from platform_foundation.f1.config import (
            minio_endpoint,
            pg_database,
            pg_host,
            pg_port,
        )
        from platform_foundation.f1.secret_files import read_f1_secret_text

        database = pg_database()
        if SOURCE_DATABASE_RE.fullmatch(database) is None:
            raise IngestionVerifyError("LOCAL_INGESTION_SOURCE_INVALID")
        if (
            pg_host() != EXPECTED_POSTGRES_HOST
            or pg_port() != EXPECTED_POSTGRES_PORT
            or minio_endpoint() != EXPECTED_MINIO_ENDPOINT
        ):
            raise IngestionVerifyError("LOCAL_INGESTION_ENDPOINT_MISMATCH")

        bootstrap_dsn = migrate_f1._bootstrap_dsn()
        f0_url = local_migrate._root_migration_url().set(drivername="postgresql")
        f1_url = make_url(migrate_f1._migration_dsn()).set(
            drivername="postgresql"
        )
        return SourceConfiguration(
            database=database,
            bootstrap_dsn=bootstrap_dsn,
            f0_migration_dsn=f0_url.render_as_string(hide_password=False),
            f1_migration_dsn=f1_url.render_as_string(hide_password=False),
            f1_api_password=migrate_f1._read_secret("f1_api_password"),
            f1_worker_password=migrate_f1._read_secret("f1_worker_password"),
            minio_root_user=read_f1_secret_text(
                "minio_root_user", file_env="F1_MINIO_ROOT_USER_FILE"
            ),
            minio_root_password=read_f1_secret_text(
                "minio_root_password",
                file_env="F1_MINIO_ROOT_PASSWORD_FILE",
            ),
        )
    except IngestionVerifyError:
        raise
    except BaseException:
        raise IngestionVerifyError("LOCAL_INGESTION_SOURCE_INVALID") from None


def _source_run_row_count(
    configuration: SourceConfiguration,
    idempotency_key_sha256: str,
) -> int:
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
                raise IngestionVerifyError(
                    "LOCAL_INGESTION_SOURCE_NOT_READY"
                )
            heads = connection.execute(
                "SELECT "
                "(SELECT string_agg(version_num,',' ORDER BY version_num) "
                "FROM f0d.alembic_version),"
                "(SELECT string_agg(version_num,',' ORDER BY version_num) "
                "FROM f1.alembic_version)"
            ).fetchone()
            if heads is None or tuple(heads) != ("f0d_0006", "f1_0011"):
                raise IngestionVerifyError(
                    "LOCAL_INGESTION_SOURCE_NOT_READY"
                )
            row = connection.execute(
                "SELECT count(*) FROM f1.document_version "
                "WHERE idempotency_key_sha256=%s",
                (idempotency_key_sha256,),
            ).fetchone()
            if row is None:
                raise IngestionVerifyError(
                    "LOCAL_INGESTION_SOURCE_NOT_READY"
                )
            return int(row[0])
    except IngestionVerifyError:
        raise
    except BaseException:
        raise IngestionVerifyError("LOCAL_INGESTION_SOURCE_NOT_READY") from None


def _create_scratch_database(
    configuration: SourceConfiguration,
    scratch_database: str,
) -> None:
    import psycopg
    from psycopg import sql

    if SCRATCH_DATABASE_RE.fullmatch(scratch_database) is None:
        raise IngestionVerifyError("LOCAL_INGESTION_SCRATCH_CREATE_FAILED")
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
                raise IngestionVerifyError(
                    "LOCAL_INGESTION_SCRATCH_CREATE_FAILED"
                )
            connection.execute(
                sql.SQL(
                    "CREATE DATABASE {} OWNER f0d_migration TEMPLATE template0"
                ).format(sql.Identifier(scratch_database))
            )
    except IngestionVerifyError:
        raise
    except BaseException:
        raise IngestionVerifyError(
            "LOCAL_INGESTION_SCRATCH_CREATE_FAILED"
        ) from None


def _harden_scratch_database(
    scratch_bootstrap_dsn: str,
    scratch_database: str,
) -> None:
    import psycopg
    from psycopg import sql

    try:
        with psycopg.connect(
            scratch_bootstrap_dsn,
            autocommit=True,
        ) as connection:
            identity = connection.execute(
                "SELECT current_user,session_user,current_database()"
            ).fetchone()
            if identity is None or tuple(identity) != (
                "f0d_bootstrap",
                "f0d_bootstrap",
                scratch_database,
            ):
                raise IngestionVerifyError(
                    "LOCAL_INGESTION_SCRATCH_IDENTITY_MISMATCH"
                )
            connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            connection.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                    sql.Identifier(scratch_database)
                )
            )
    except IngestionVerifyError:
        raise
    except BaseException:
        raise IngestionVerifyError(
            "LOCAL_INGESTION_SCRATCH_IDENTITY_MISMATCH"
        ) from None


def _drop_scratch_database(
    configuration: SourceConfiguration,
    scratch_database: str,
) -> None:
    import psycopg
    from psycopg import sql

    if SCRATCH_DATABASE_RE.fullmatch(scratch_database) is None:
        raise IngestionVerifyError(
            "LOCAL_INGESTION_SCRATCH_CLEANUP_FAILED"
        )
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
                raise IngestionVerifyError(
                    "LOCAL_INGESTION_SCRATCH_CLEANUP_FAILED"
                )
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
                raise IngestionVerifyError(
                    "LOCAL_INGESTION_SCRATCH_CLEANUP_FAILED"
                )
    except IngestionVerifyError:
        raise
    except BaseException:
        raise IngestionVerifyError(
            "LOCAL_INGESTION_SCRATCH_CLEANUP_FAILED"
        ) from None


def _write_secret(path: Path, value: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    raw = value.encode("ascii")
    descriptor = os.open(path, flags, 0o600)
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written < 1:
                raise IngestionVerifyError("LOCAL_INGESTION_INTERNAL_ERROR")
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
        raise IngestionVerifyError("LOCAL_INGESTION_INTERNAL_ERROR")


def _scratch_secret_directory(
    configuration: SourceConfiguration,
    scratch_database: str,
) -> tuple[Path, str, str]:
    directory = Path(tempfile.mkdtemp(prefix="anhuan-ingestion-secrets-"))
    os.chmod(directory, 0o700)
    try:
        scratch_bootstrap = rewrite_dsn_database(
            configuration.bootstrap_dsn,
            database=scratch_database,
            expected_user="f0d_bootstrap",
        )
        scratch_f0_migration = rewrite_dsn_database(
            configuration.f0_migration_dsn,
            database=scratch_database,
            expected_user="f0d_migration",
        )
        values = {
            "f0d_migration_dsn": scratch_f0_migration,
            "f1_bootstrap_dsn": scratch_bootstrap,
            "f1_migration_dsn": rewrite_dsn_database(
                configuration.f1_migration_dsn,
                database=scratch_database,
                expected_user="f0d_migration",
            ),
            "f1_api_password": configuration.f1_api_password,
            "f1_worker_password": configuration.f1_worker_password,
            "minio_root_user": configuration.minio_root_user,
            "minio_root_password": configuration.minio_root_password,
        }
        for name in SECRET_NAMES:
            _write_secret(directory / name, values[name])
        return directory, scratch_bootstrap, scratch_f0_migration
    except BaseException as original_error:
        try:
            shutil.rmtree(directory)
            if directory.exists():
                raise OSError
        except BaseException:
            raise IngestionVerifyError(
                "LOCAL_INGESTION_SCRATCH_CLEANUP_FAILED"
            ) from None
        raise original_error


def _remove_secret_directory(directory: Path) -> None:
    try:
        info = directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.geteuid()
            or {entry.name for entry in directory.iterdir()}
            != set(SECRET_NAMES)
        ):
            raise IngestionVerifyError(
                "LOCAL_INGESTION_SCRATCH_CLEANUP_FAILED"
            )
        for name in SECRET_NAMES:
            file_info = (directory / name).lstat()
            if (
                not stat.S_ISREG(file_info.st_mode)
                or stat.S_ISLNK(file_info.st_mode)
                or file_info.st_nlink != 1
                or stat.S_IMODE(file_info.st_mode) != 0o600
                or file_info.st_uid != os.geteuid()
            ):
                raise IngestionVerifyError(
                    "LOCAL_INGESTION_SCRATCH_CLEANUP_FAILED"
                )
        shutil.rmtree(directory)
        if directory.exists():
            raise IngestionVerifyError(
                "LOCAL_INGESTION_SCRATCH_CLEANUP_FAILED"
            )
    except IngestionVerifyError:
        raise
    except BaseException:
        raise IngestionVerifyError(
            "LOCAL_INGESTION_SCRATCH_CLEANUP_FAILED"
        ) from None


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
        "F1_API_PASSWORD_FILE": str(secret_directory / "f1_api_password"),
        "F1_WORKER_PASSWORD_FILE": str(secret_directory / "f1_worker_password"),
        "F1_MINIO_ROOT_USER_FILE": str(secret_directory / "minio_root_user"),
        "F1_MINIO_ROOT_PASSWORD_FILE": str(
            secret_directory / "minio_root_password"
        ),
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


@contextlib.contextmanager
def _scratch_f0_schema_bootstrap(local_migrate: object) -> Iterator[None]:
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
            raise IngestionVerifyError("LOCAL_INGESTION_INTERNAL_ERROR")
        setattr(local_migrate, "_upgrade_f0", original)


def _migrate_scratch() -> None:
    try:
        from infra.f1 import local_migrate

        with _scratch_f0_schema_bootstrap(local_migrate):
            local_migrate.migrate()
    except IngestionVerifyError:
        raise
    except BaseException:
        raise IngestionVerifyError("LOCAL_INGESTION_MIGRATION_FAILED") from None


def _seed_scratch(scratch_bootstrap_dsn: str) -> None:
    import psycopg

    try:
        from infra.f1 import local_seed

        with psycopg.connect(
            scratch_bootstrap_dsn,
            autocommit=False,
        ) as connection:
            head = connection.execute(
                "SELECT string_agg(version_num,',' ORDER BY version_num) "
                "FROM f1.alembic_version"
            ).fetchone()
            if head is None or head[0] != "f1_0011":
                raise IngestionVerifyError("LOCAL_INGESTION_SEED_FAILED")
            local_seed._ensure_enterprise(
                connection,
                local_seed.ENTERPRISE_A,
                "Local Enterprise A",
                "LOCAL-A",
            )
            local_seed._ensure_enterprise(
                connection,
                local_seed.ENTERPRISE_B,
                "Local Enterprise B",
                "LOCAL-B",
            )
            for binding in local_seed.BINDINGS:
                local_seed._ensure_binding(connection, binding)
            connection.commit()
    except IngestionVerifyError:
        raise
    except BaseException:
        raise IngestionVerifyError("LOCAL_INGESTION_SEED_FAILED") from None


def _assert_scratch_heads(scratch_bootstrap_dsn: str) -> None:
    import psycopg

    try:
        with psycopg.connect(
            scratch_bootstrap_dsn,
            autocommit=True,
        ) as connection:
            heads = connection.execute(
                "SELECT "
                "(SELECT string_agg(version_num,',' ORDER BY version_num) "
                "FROM f0d.alembic_version),"
                "(SELECT string_agg(version_num,',' ORDER BY version_num) "
                "FROM f1.alembic_version)"
            ).fetchone()
        if heads is None or tuple(heads) != ("f0d_0006", "f1_0011"):
            raise IngestionVerifyError("LOCAL_INGESTION_MIGRATION_FAILED")
    except IngestionVerifyError:
        raise
    except BaseException:
        raise IngestionVerifyError("LOCAL_INGESTION_MIGRATION_FAILED") from None


def _blank_pdf() -> bytes:
    from pypdf import PdfWriter

    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


@contextlib.contextmanager
def _temporary_wrong_minio_password(secret_directory: Path) -> Iterator[None]:
    """Point one request at a private, deliberately invalid MinIO secret."""
    path = secret_directory / "minio_fault_password"
    previous = os.environ.get("F1_MINIO_ROOT_PASSWORD_FILE")
    _write_secret(path, f"invalid-{secrets.token_hex(24)}")
    os.environ["F1_MINIO_ROOT_PASSWORD_FILE"] = str(path)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("F1_MINIO_ROOT_PASSWORD_FILE", None)
        else:
            os.environ["F1_MINIO_ROOT_PASSWORD_FILE"] = previous
        try:
            info = path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.geteuid()
            ):
                raise OSError
            path.unlink()
            if path.exists():
                raise OSError
        except BaseException:
            raise IngestionVerifyError(
                "LOCAL_INGESTION_OBJECT_CLEANUP_FAILED"
            ) from None


@contextlib.contextmanager
def _bound_unlistened_loopback() -> Iterator[int]:
    """Reserve a random loopback port without listening on it."""
    reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        reservation.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        reservation.bind(("127.0.0.1", 0))
        port = int(reservation.getsockname()[1])
        if not 1 <= port <= 65535:
            raise IngestionVerifyError("LOCAL_INGESTION_INTERNAL_ERROR")
        yield port
    finally:
        reservation.close()


def _json_object(response: object, reason: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except BaseException:
        raise IngestionVerifyError(reason) from None
    if not isinstance(payload, dict):
        raise IngestionVerifyError(reason)
    return payload


def _uuid_field(
    payload: dict[str, Any],
    field: str,
    reason: str,
) -> uuid.UUID:
    try:
        return uuid.UUID(str(payload[field]))
    except (KeyError, TypeError, ValueError):
        raise IngestionVerifyError(reason) from None


def _assert_upload_write_failure(
    scratch_bootstrap_dsn: str,
    *,
    version_id: uuid.UUID,
    idempotency_key_sha256: str,
) -> None:
    import psycopg

    try:
        with psycopg.connect(
            scratch_bootstrap_dsn,
            autocommit=True,
        ) as connection:
            row = connection.execute(
                "SELECT task.object_state,task.status,task.processing_stage,"
                "task.quarantine_status,(task.source_etag IS NULL),"
                "(task.released_at IS NULL),"
                "(SELECT count(*) FROM f1.document_version AS own_version "
                "WHERE own_version.enterprise_id=version.enterprise_id "
                "AND own_version.idempotency_key_sha256=%s) "
                "FROM f1.document_version AS version "
                "JOIN f1.upload_task AS task "
                "ON task.enterprise_id=version.enterprise_id "
                "AND task.id=version.upload_task_id WHERE version.id=%s",
                (idempotency_key_sha256, version_id),
            ).fetchone()
        if row is None or tuple(row) != (
            "write_failed",
            "failed",
            "failed",
            "blocked",
            True,
            True,
            1,
        ):
            raise IngestionVerifyError("LOCAL_INGESTION_UPLOAD_FAILED")
    except IngestionVerifyError:
        raise
    except BaseException:
        raise IngestionVerifyError("LOCAL_INGESTION_UPLOAD_FAILED") from None


def _assert_scanner_retry_wait(
    scratch_bootstrap_dsn: str,
    *,
    version_id: uuid.UUID,
) -> None:
    import psycopg

    try:
        with psycopg.connect(
            scratch_bootstrap_dsn,
            autocommit=True,
        ) as connection:
            row = connection.execute(
                "SELECT task.object_state,task.status,task.processing_stage,"
                "task.quarantine_status,task.scan_verdict,task.error_reason,"
                "task.attempt,(task.lease_token IS NULL),"
                "(task.lease_until IS NULL),(task.released_at IS NULL) "
                "FROM f1.document_version AS version "
                "JOIN f1.upload_task AS task "
                "ON task.enterprise_id=version.enterprise_id "
                "AND task.id=version.upload_task_id WHERE version.id=%s",
                (version_id,),
            ).fetchone()
        if row is None or tuple(row) != (
            "quarantined",
            "failed",
            "retry_wait",
            "held",
            "unavailable",
            "P3_SCANNER_UNAVAILABLE",
            1,
            True,
            True,
            True,
        ):
            raise IngestionVerifyError("LOCAL_INGESTION_PROCESS_FAILED")
    except IngestionVerifyError:
        raise
    except BaseException:
        raise IngestionVerifyError("LOCAL_INGESTION_PROCESS_FAILED") from None


async def _api_smoke(
    *,
    idempotency_key: str,
    idempotency_key_sha256: str,
    pdf_body: bytes,
    secret_directory: Path,
    scratch_bootstrap_dsn: str,
) -> RunIdentifiers:
    import httpx
    from fastapi import FastAPI, Header, HTTPException

    from platform_foundation.f1 import auth
    from platform_foundation.f1.api.routers import p3_controlled_ingestion

    actors = {
        "admin": {"sub": ADMIN_SUB, "enterprise_id": ENTERPRISE_A},
        "tenant_b": {"sub": TENANT_B_SUB, "enterprise_id": ENTERPRISE_B},
    }

    app = FastAPI()
    app.include_router(
        p3_controlled_ingestion.router,
        prefix="/api/v1/ingestion",
    )

    async def synthetic_user(
        x_local_ingestion_actor: str | None = Header(default=None),
    ) -> dict[str, Any]:
        actor = actors.get(x_local_ingestion_actor or "")
        if actor is None:
            raise HTTPException(
                status_code=401,
                detail="LOCAL_INGESTION_IDENTITY_REQUIRED",
            )
        return {"sub": actor["sub"], "roles": ()}

    app.dependency_overrides[auth.current_user] = synthetic_user
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://local-ingestion.invalid",
    ) as client:
        async def request(
            actor_name: str,
            method: str,
            path: str,
            expected_status: int,
            *,
            reason: str,
            data: dict[str, str] | None = None,
            files: dict[str, tuple[str, bytes, str]] | None = None,
            extra_headers: dict[str, str] | None = None,
        ) -> httpx.Response:
            actor = actors[actor_name]
            headers = {
                "X-Local-Ingestion-Actor": actor_name,
                "X-Enterprise-Id": str(actor["enterprise_id"]),
            }
            if extra_headers:
                headers.update(extra_headers)
            try:
                response = await client.request(
                    method,
                    path,
                    headers=headers,
                    data=data,
                    files=files,
                )
            except BaseException:
                raise IngestionVerifyError(reason) from None
            if response.status_code != expected_status:
                raise IngestionVerifyError(reason)
            return response

        upload_arguments = {
            "reason": "LOCAL_INGESTION_UPLOAD_FAILED",
            "data": {"display_name": "LOCAL_INGESTION_SYNTHETIC"},
            "files": {
                "file": (
                    "local-ingestion.pdf",
                    pdf_body,
                    "application/pdf",
                )
            },
            "extra_headers": {"Idempotency-Key": idempotency_key},
        }
        with _temporary_wrong_minio_password(secret_directory):
            failed_upload_response = await request(
                "admin",
                "POST",
                "/api/v1/ingestion/documents",
                503,
                **upload_arguments,
            )
        failed_upload = _json_object(
            failed_upload_response,
            "LOCAL_INGESTION_UPLOAD_FAILED",
        )
        failed_detail = failed_upload.get("detail")
        if (
            not isinstance(failed_detail, dict)
            or failed_detail.get("code") != "SOURCE_OBJECT_STAT_FAILED"
            or failed_detail.get("retryable") is not True
        ):
            raise IngestionVerifyError("LOCAL_INGESTION_UPLOAD_FAILED")

        failed_library_response = await request(
            "admin",
            "GET",
            "/api/v1/ingestion/documents",
            200,
            reason="LOCAL_INGESTION_UPLOAD_FAILED",
        )
        failed_library = _json_object(
            failed_library_response,
            "LOCAL_INGESTION_UPLOAD_FAILED",
        )
        failed_items = failed_library.get("items")
        if (
            not isinstance(failed_items, list)
            or len(failed_items) != 1
            or not isinstance(failed_items[0], dict)
            or not isinstance(failed_items[0].get("latest_version"), dict)
        ):
            raise IngestionVerifyError("LOCAL_INGESTION_UPLOAD_FAILED")
        failed_version = failed_items[0]["latest_version"]
        if (
            failed_items[0].get("status") == "ready"
            or failed_version.get("workflow_status") != "failed"
            or failed_version.get("quarantine_status") != "blocked"
            or failed_version.get("preview_status") != "blocked"
        ):
            raise IngestionVerifyError("LOCAL_INGESTION_UPLOAD_FAILED")
        failed_document_id = _uuid_field(
            failed_items[0],
            "id",
            "LOCAL_INGESTION_UPLOAD_FAILED",
        )
        failed_version_id = _uuid_field(
            failed_version,
            "id",
            "LOCAL_INGESTION_UPLOAD_FAILED",
        )
        _assert_upload_write_failure(
            scratch_bootstrap_dsn,
            version_id=failed_version_id,
            idempotency_key_sha256=idempotency_key_sha256,
        )

        from platform_foundation.f1 import storage

        correct_client = storage._client()
        if any(
            correct_client.bucket_exists(bucket)
            for bucket in (
                storage.QUARANTINE_BUCKET,
                storage.PREVIEW_BUCKET,
                storage.BUCKET,
            )
        ):
            raise IngestionVerifyError("LOCAL_INGESTION_UPLOAD_FAILED")

        uploaded_response = await request(
            "admin",
            "POST",
            "/api/v1/ingestion/documents",
            202,
            **upload_arguments,
        )
        uploaded = _json_object(
            uploaded_response,
            "LOCAL_INGESTION_UPLOAD_FAILED",
        )
        document_id = _uuid_field(
            uploaded,
            "id",
            "LOCAL_INGESTION_UPLOAD_FAILED",
        )
        versions = uploaded.get("versions")
        if not isinstance(versions, list) or len(versions) != 1:
            raise IngestionVerifyError("LOCAL_INGESTION_UPLOAD_FAILED")
        version = versions[0]
        if not isinstance(version, dict):
            raise IngestionVerifyError("LOCAL_INGESTION_UPLOAD_FAILED")
        version_id = _uuid_field(
            version,
            "id",
            "LOCAL_INGESTION_UPLOAD_FAILED",
        )
        if (
            document_id != failed_document_id
            or version_id != failed_version_id
            or version.get("workflow_status") != "received"
            or version.get("quarantine_status") != "held"
            or version.get("scan_status") != "queued"
            or version.get("preview_status") != "blocked"
        ):
            raise IngestionVerifyError("LOCAL_INGESTION_UPLOAD_FAILED")

        # A held source is never releasable before a real clean scan and a
        # completed preview, even though its quarantine write already exists.
        await request(
            "admin",
            "POST",
            f"/api/v1/ingestion/versions/{version_id}/release",
            409,
            reason="LOCAL_INGESTION_RELEASE_FAILED",
        )

        await request(
            "tenant_b",
            "GET",
            f"/api/v1/ingestion/documents/{document_id}",
            404,
            reason="LOCAL_INGESTION_CROSS_TENANT_LEAK",
        )

        from platform_foundation.f1.auth import Tenant
        from platform_foundation.f1.features.p3.processor import (
            process_controlled_ingestion,
        )

        with _bound_unlistened_loopback() as unavailable_port:
            await process_controlled_ingestion(
                Tenant(
                    enterprise_id=ENTERPRISE_A,
                    sub=ADMIN_SUB,
                    roles=(),
                    role="enterprise_admin",
                ),
                version_id,
                scanner_host="127.0.0.1",
                scanner_port=unavailable_port,
            )
        _assert_scanner_retry_wait(
            scratch_bootstrap_dsn,
            version_id=version_id,
        )
        unavailable_response = await request(
            "admin",
            "GET",
            f"/api/v1/ingestion/versions/{version_id}",
            200,
            reason="LOCAL_INGESTION_PROCESS_FAILED",
        )
        unavailable = _json_object(
            unavailable_response,
            "LOCAL_INGESTION_PROCESS_FAILED",
        )
        if (
            unavailable.get("workflow_status") != "blocked"
            or unavailable.get("quarantine_status") != "held"
            or unavailable.get("scan_status") != "unavailable"
            or unavailable.get("preview_status") != "blocked"
            or unavailable.get("retryable") is not True
            or unavailable.get("allowed_actions") != ["retry"]
        ):
            raise IngestionVerifyError("LOCAL_INGESTION_PROCESS_FAILED")

        processed_response = await request(
            "admin",
            "POST",
            f"/api/v1/ingestion/versions/{version_id}/retry",
            200,
            reason="LOCAL_INGESTION_PROCESS_FAILED",
        )
        processed = _json_object(
            processed_response,
            "LOCAL_INGESTION_PROCESS_FAILED",
        )
        if (
            processed.get("workflow_status") != "ready"
            or processed.get("quarantine_status") != "held"
            or processed.get("scan_status") != "clean"
            or processed.get("preview_status") != "ready"
        ):
            raise IngestionVerifyError("LOCAL_INGESTION_PROCESS_FAILED")

        preview_response = await request(
            "admin",
            "GET",
            f"/api/v1/ingestion/versions/{version_id}/preview",
            200,
            reason="LOCAL_INGESTION_PREVIEW_FAILED",
        )
        preview = _json_object(
            preview_response,
            "LOCAL_INGESTION_PREVIEW_FAILED",
        )
        units = preview.get("units")
        if (
            preview.get("status") != "ready"
            or preview.get("kind") != "page_text"
            or not isinstance(units, list)
            or len(units) != 1
            or not isinstance(units[0], dict)
            or units[0].get("kind") != "page_text"
        ):
            raise IngestionVerifyError("LOCAL_INGESTION_PREVIEW_FAILED")
        unit_id = str(units[0].get("id") or "")
        if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", unit_id) is None:
            raise IngestionVerifyError("LOCAL_INGESTION_PREVIEW_FAILED")
        unit_response = await request(
            "admin",
            "GET",
            f"/api/v1/ingestion/versions/{version_id}/preview/units/"
            f"{unit_id}/content",
            200,
            reason="LOCAL_INGESTION_PREVIEW_FAILED",
        )
        unit = _json_object(
            unit_response,
            "LOCAL_INGESTION_PREVIEW_FAILED",
        )
        if not isinstance(unit.get("lines"), list) or not isinstance(
            unit.get("truncated"), bool
        ):
            raise IngestionVerifyError("LOCAL_INGESTION_PREVIEW_FAILED")

        released_response = await request(
            "admin",
            "POST",
            f"/api/v1/ingestion/versions/{version_id}/release",
            200,
            reason="LOCAL_INGESTION_RELEASE_FAILED",
        )
        released = _json_object(
            released_response,
            "LOCAL_INGESTION_RELEASE_FAILED",
        )
        if (
            released.get("workflow_status") != "ready"
            or released.get("quarantine_status") != "released"
            or released.get("scan_status") != "clean"
            or released.get("preview_status") != "ready"
        ):
            raise IngestionVerifyError("LOCAL_INGESTION_RELEASE_FAILED")

    return RunIdentifiers(document_id=document_id, version_id=version_id)


def _observe_data(
    scratch_bootstrap_dsn: str,
    identifiers: RunIdentifiers,
) -> DataObservation:
    import psycopg

    try:
        with psycopg.connect(
            scratch_bootstrap_dsn,
            autocommit=True,
        ) as connection:
            row = connection.execute(
                "SELECT task.id,task.object_key,task.content_sha256,"
                "task.source_size,task.source_etag,task.status,"
                "task.object_state,task.processing_stage,"
                "task.quarantine_status,task.scan_verdict,"
                "task.scanner_engine,task.scanner_version,"
                "task.signature_version,task.preview_status,"
                "task.preview_kind,task.preview_sha256,"
                "task.preview_unit_count,(task.released_at IS NOT NULL) "
                "FROM f1.document_version AS version "
                "JOIN f1.upload_task AS task "
                "ON task.enterprise_id=version.enterprise_id "
                "AND task.id=version.upload_task_id "
                "WHERE version.id=%s AND version.document_record_id=%s",
                (identifiers.version_id, identifiers.document_id),
            ).fetchone()
            audit_actions = frozenset(
                str(item[0])
                for item in connection.execute(
                    "SELECT DISTINCT action FROM f1.audit_log "
                    "WHERE enterprise_id=%s AND resource_id=%s",
                    (ENTERPRISE_A, str(identifiers.version_id)),
                ).fetchall()
            )
        if row is None or len(row) != 18:
            raise IngestionVerifyError(
                "LOCAL_INGESTION_DATA_IDENTITY_MISMATCH"
            )
        return DataObservation(
            task_id=row[0],
            object_key=str(row[1]),
            content_sha256=str(row[2]),
            source_size=int(row[3]),
            source_etag=str(row[4] or ""),
            status=str(row[5]),
            object_state=str(row[6]),
            processing_stage=str(row[7]),
            quarantine_status=str(row[8]),
            scan_verdict=str(row[9]),
            scanner_engine=str(row[10] or ""),
            scanner_version=str(row[11] or ""),
            signature_version=str(row[12] or ""),
            preview_status=str(row[13]),
            preview_kind=str(row[14] or ""),
            preview_sha256=str(row[15] or ""),
            preview_unit_count=int(row[16]),
            released=bool(row[17]),
            audit_actions=audit_actions,
        )
    except IngestionVerifyError:
        raise
    except BaseException:
        raise IngestionVerifyError(
            "LOCAL_INGESTION_DATA_IDENTITY_MISMATCH"
        ) from None


async def _verify_cross_tenant_rls(
    identifiers: RunIdentifiers,
) -> None:
    from sqlalchemy import text

    from platform_foundation.f1.database import session_scope

    try:
        async with session_scope(
            role="f1_api",
            enterprise_id=ENTERPRISE_A,
            sub=ADMIN_SUB,
        ) as session:
            own_counts = (
                await session.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM f1.document_record WHERE id=:doc),"
                        "(SELECT count(*) FROM f1.document_version WHERE id=:ver)"
                    ),
                    {"doc": identifiers.document_id, "ver": identifiers.version_id},
                )
            ).one()
        async with session_scope(
            role="f1_api",
            enterprise_id=ENTERPRISE_B,
            sub=TENANT_B_SUB,
        ) as session:
            foreign_counts = (
                await session.execute(
                    text(
                        "SELECT "
                        "(SELECT count(*) FROM f1.document_record WHERE id=:doc),"
                        "(SELECT count(*) FROM f1.document_version WHERE id=:ver)"
                    ),
                    {"doc": identifiers.document_id, "ver": identifiers.version_id},
                )
            ).one()
        if tuple(own_counts) != (1, 1) or tuple(foreign_counts) != (0, 0):
            raise IngestionVerifyError("LOCAL_INGESTION_RLS_LEAK")
    except IngestionVerifyError:
        raise
    except BaseException:
        raise IngestionVerifyError("LOCAL_INGESTION_RLS_LEAK") from None


def _verify_object_identity(storage: object, observation: DataObservation) -> None:
    try:
        quarantined = storage.verify_quarantine_object(
            observation.object_key,
            expected_sha256=observation.content_sha256,
            expected_size=observation.source_size,
            expected_etag=observation.source_etag,
        )
        released = storage.verify_stored_object(
            observation.object_key,
            expected_sha256=observation.content_sha256,
            expected_size=observation.source_size,
        )
        manifest = storage.read_ingestion_preview_manifest(
            task_id=observation.task_id,
            expected_sha256=observation.preview_sha256,
        )
        if (
            quarantined.sha256 != observation.content_sha256
            or released.sha256 != observation.content_sha256
            or quarantined.size != released.size
            or hashlib.sha256(manifest).hexdigest()
            != observation.preview_sha256
        ):
            raise IngestionVerifyError(
                "LOCAL_INGESTION_OBJECT_IDENTITY_MISMATCH"
            )
    except IngestionVerifyError:
        raise
    except BaseException:
        raise IngestionVerifyError(
            "LOCAL_INGESTION_OBJECT_IDENTITY_MISMATCH"
        ) from None


def _collect_task_objects(
    scratch_bootstrap_dsn: str,
    idempotency_key_sha256: str,
) -> tuple[TaskObjectIdentity, ...]:
    import psycopg

    try:
        with psycopg.connect(
            scratch_bootstrap_dsn,
            autocommit=True,
        ) as connection:
            rows = connection.execute(
                "SELECT task.id,task.object_key "
                "FROM f1.document_version AS version "
                "JOIN f1.upload_task AS task "
                "ON task.enterprise_id=version.enterprise_id "
                "AND task.id=version.upload_task_id "
                "WHERE version.idempotency_key_sha256=%s "
                "AND task.pipeline_kind='controlled_ingestion'",
                (idempotency_key_sha256,),
            ).fetchall()
        identities = tuple(
            TaskObjectIdentity(task_id=row[0], object_key=str(row[1]))
            for row in rows
        )
        if len(identities) > 1 or any(
            not isinstance(item.task_id, uuid.UUID)
            or DOCUMENT_OBJECT_RE.fullmatch(item.object_key) is None
            for item in identities
        ):
            raise IngestionVerifyError(
                "LOCAL_INGESTION_OBJECT_CLEANUP_FAILED"
            )
        return identities
    except IngestionVerifyError:
        raise
    except BaseException:
        raise IngestionVerifyError(
            "LOCAL_INGESTION_OBJECT_CLEANUP_FAILED"
        ) from None


def _validate_bucket_object(
    bucket: str,
    object_name: str,
    resources: ResourceNames,
    identities: tuple[TaskObjectIdentity, ...],
) -> None:
    source_keys = {item.object_key for item in identities}
    preview_prefixes = {f"{item.task_id.hex}/" for item in identities}
    valid = False
    if bucket in {resources.quarantine_bucket, resources.released_bucket}:
        valid = object_name in source_keys
    elif bucket == resources.preview_bucket:
        valid = (
            PREVIEW_OBJECT_RE.fullmatch(object_name) is not None
            and any(object_name.startswith(prefix) for prefix in preview_prefixes)
        )
    if not valid:
        raise IngestionVerifyError(
            "LOCAL_INGESTION_OBJECT_CLEANUP_FAILED"
        )


def _assert_buckets_absent(client: object, resources: ResourceNames) -> None:
    try:
        if any(client.bucket_exists(bucket) for bucket in resources.buckets):
            raise IngestionVerifyError(
                "LOCAL_INGESTION_RESOURCE_NAMESPACE_FAILED"
            )
    except IngestionVerifyError:
        raise
    except BaseException:
        raise IngestionVerifyError(
            "LOCAL_INGESTION_RESOURCE_NAMESPACE_FAILED"
        ) from None


def _cleanup_buckets(
    client: object,
    resources: ResourceNames,
    identities: tuple[TaskObjectIdentity, ...],
) -> None:
    try:
        for bucket in resources.buckets:
            if not client.bucket_exists(bucket):
                continue
            object_names = tuple(
                str(item.object_name)
                for item in client.list_objects(bucket, recursive=True)
            )
            for object_name in object_names:
                _validate_bucket_object(
                    bucket,
                    object_name,
                    resources,
                    identities,
                )
            for object_name in sorted(object_names):
                client.remove_object(bucket, object_name)
            if tuple(client.list_objects(bucket, recursive=True)):
                raise IngestionVerifyError(
                    "LOCAL_INGESTION_OBJECT_CLEANUP_FAILED"
                )
            client.remove_bucket(bucket)
            if client.bucket_exists(bucket):
                raise IngestionVerifyError(
                    "LOCAL_INGESTION_OBJECT_CLEANUP_FAILED"
                )
    except IngestionVerifyError:
        raise
    except BaseException:
        raise IngestionVerifyError(
            "LOCAL_INGESTION_OBJECT_CLEANUP_FAILED"
        ) from None


def _activate_unique_buckets(
    storage: object,
    resources: ResourceNames,
) -> tuple[str, str, str]:
    original = (
        str(storage.QUARANTINE_BUCKET),
        str(storage.PREVIEW_BUCKET),
        str(storage.BUCKET),
    )
    if original != STANDARD_BUCKETS:
        raise IngestionVerifyError(
            "LOCAL_INGESTION_RESOURCE_NAMESPACE_FAILED"
        )
    storage.QUARANTINE_BUCKET = resources.quarantine_bucket
    storage.PREVIEW_BUCKET = resources.preview_bucket
    storage.BUCKET = resources.released_bucket
    return original


def _restore_buckets(storage: object, original: tuple[str, str, str]) -> None:
    storage.QUARANTINE_BUCKET = original[0]
    storage.PREVIEW_BUCKET = original[1]
    storage.BUCKET = original[2]


async def _dispose_database_engines() -> None:
    try:
        from platform_foundation.f1 import database
    except ImportError:
        return
    for engine in tuple(database._engines.values()):
        await engine.dispose()
    database._engines.clear()
    database._factories.clear()


def _execute_scratch_probe(
    *,
    configuration: SourceConfiguration,
    scratch_database: str,
    scratch_bootstrap_dsn: str,
    secret_directory: Path,
    scratch_f0_migration_dsn: str,
    resources: ResourceNames,
    idempotency_key: str,
    idempotency_key_sha256: str,
) -> None:
    pending_error: IngestionVerifyError | None = None
    storage: object | None = None
    client: object | None = None
    original_buckets: tuple[str, str, str] | None = None
    namespace_owned = False

    sink = _DiscardText()
    with (
        _temporary_scratch_environment(
            scratch_database=scratch_database,
            secret_directory=secret_directory,
            f0_migration_dsn=scratch_f0_migration_dsn,
        ),
        contextlib.redirect_stdout(sink),
        contextlib.redirect_stderr(sink),
    ):
        try:
            _migrate_scratch()
            _assert_scratch_heads(scratch_bootstrap_dsn)
            _seed_scratch(scratch_bootstrap_dsn)

            from platform_foundation.f1 import storage as storage_module

            storage = storage_module
            if str(storage.MINIO_ENDPOINT) != EXPECTED_MINIO_ENDPOINT:
                raise IngestionVerifyError(
                    "LOCAL_INGESTION_ENDPOINT_MISMATCH"
                )
            from platform_foundation.f1.features.p3.scanner import scan_stream

            scanner_parameters = inspect.signature(scan_stream).parameters
            if (
                scanner_parameters["host"].default != EXPECTED_CLAMD_HOST
                or scanner_parameters["port"].default != EXPECTED_CLAMD_PORT
            ):
                raise IngestionVerifyError(
                    "LOCAL_INGESTION_ENDPOINT_MISMATCH"
                )
            original_buckets = _activate_unique_buckets(storage, resources)
            client = storage._client()
            _assert_buckets_absent(client, resources)
            namespace_owned = True

            pdf_body = _blank_pdf()
            identifiers = asyncio.run(
                _api_smoke(
                    idempotency_key=idempotency_key,
                    idempotency_key_sha256=idempotency_key_sha256,
                    pdf_body=pdf_body,
                    secret_directory=secret_directory,
                    scratch_bootstrap_dsn=scratch_bootstrap_dsn,
                )
            )
            observation = _observe_data(scratch_bootstrap_dsn, identifiers)
            verify_data_observation(
                observation,
                expected_sha256=hashlib.sha256(pdf_body).hexdigest(),
                expected_size=len(pdf_body),
            )
            asyncio.run(_verify_cross_tenant_rls(identifiers))
            _verify_object_identity(storage, observation)
        except IngestionVerifyError as error:
            pending_error = error
        except BaseException:
            pending_error = IngestionVerifyError(
                "LOCAL_INGESTION_INTERNAL_ERROR"
            )

        try:
            if _source_run_row_count(
                configuration,
                idempotency_key_sha256,
            ) != 0:
                pending_error = IngestionVerifyError(
                    "LOCAL_INGESTION_SOURCE_MUTATION"
                )
        except IngestionVerifyError as error:
            pending_error = error

        cleanup_failed = False
        try:
            asyncio.run(_dispose_database_engines())
        except BaseException:
            cleanup_failed = True
        if namespace_owned and client is not None:
            try:
                identities = _collect_task_objects(
                    scratch_bootstrap_dsn,
                    idempotency_key_sha256,
                )
                _cleanup_buckets(client, resources, identities)
            except BaseException:
                cleanup_failed = True
        if storage is not None and original_buckets is not None:
            _restore_buckets(storage, original_buckets)

        if cleanup_failed:
            raise IngestionVerifyError(
                "LOCAL_INGESTION_OBJECT_CLEANUP_FAILED"
            )
        if pending_error is not None:
            raise pending_error


def run() -> IngestionVerificationCounts:
    configuration: SourceConfiguration | None = None
    scratch_database: str | None = None
    secret_directory: Path | None = None
    scratch_created = False
    pending_error: IngestionVerifyError | None = None

    try:
        configuration = _load_source_configuration()
        nonce = uuid.uuid4().hex
        scratch_database = scratch_database_name(configuration.database, nonce)
        resources = resource_names(configuration.database, nonce)
        idempotency_key = secrets.token_hex(24)
        idempotency_key_sha256 = hashlib.sha256(
            idempotency_key.encode("utf-8")
        ).hexdigest()
        if _source_run_row_count(configuration, idempotency_key_sha256) != 0:
            raise IngestionVerifyError("LOCAL_INGESTION_SOURCE_NOT_READY")

        _create_scratch_database(configuration, scratch_database)
        scratch_created = True
        (
            secret_directory,
            scratch_bootstrap_dsn,
            scratch_f0_migration_dsn,
        ) = _scratch_secret_directory(configuration, scratch_database)
        _harden_scratch_database(scratch_bootstrap_dsn, scratch_database)
        _execute_scratch_probe(
            configuration=configuration,
            scratch_database=scratch_database,
            scratch_bootstrap_dsn=scratch_bootstrap_dsn,
            secret_directory=secret_directory,
            scratch_f0_migration_dsn=scratch_f0_migration_dsn,
            resources=resources,
            idempotency_key=idempotency_key,
            idempotency_key_sha256=idempotency_key_sha256,
        )
    except IngestionVerifyError as error:
        pending_error = error
    except BaseException:
        pending_error = IngestionVerifyError(
            "LOCAL_INGESTION_INTERNAL_ERROR"
        )

    cleanup_failed = False
    if (
        scratch_created
        and configuration is not None
        and scratch_database is not None
    ):
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
        raise IngestionVerifyError(
            "LOCAL_INGESTION_SCRATCH_CLEANUP_FAILED"
        )
    if pending_error is not None:
        raise pending_error
    return IngestionVerificationCounts()


def main() -> int:
    try:
        counts = run()
    except BaseException as error:
        reason = (
            error.reason
            if isinstance(error, IngestionVerifyError)
            else "LOCAL_INGESTION_INTERNAL_ERROR"
        )
        print(reason, file=sys.stderr)
        return 1
    for line in render_success(counts):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
