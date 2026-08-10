"""Run the real P2 and P4-P7 API/RLS contracts in one local scratch DB.

The command is designed for the existing one-shot migrator environment.  It
never starts PostgreSQL: it creates one project-bound database in the already
isolated local cluster, migrates and seeds that database with the closeout
helpers, then reuses the existing ASGI smoke contracts.  Those contracts
replace only ``auth.current_user``; application sessions, transactions,
PostgreSQL constraints and FORCE RLS continue to use the real ``f1_api`` role.

The persistent local database is read before and after the probe but is never a
business-write target.  The exact scratch database and temporary secret files
must be removed before success can be emitted.  Output is deliberately limited
to fixed integer metrics and a fixed status tag; response bodies, identifiers,
DSNs and exception text are never rendered.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
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
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

SOURCE_DATABASE_RE = re.compile(r"anhuan_closeout_([0-9a-f]{24})\Z")
SCRATCH_DATABASE_RE = re.compile(
    r"anhuan_business_([0-9a-f]{12})_([0-9a-f]{16})\Z"
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
        "LOCAL_BUSINESS_SOURCE_INVALID",
        "LOCAL_BUSINESS_SOURCE_NOT_READY",
        "LOCAL_BUSINESS_SCRATCH_CREATE_FAILED",
        "LOCAL_BUSINESS_SCRATCH_IDENTITY_MISMATCH",
        "LOCAL_BUSINESS_MIGRATION_FAILED",
        "LOCAL_BUSINESS_SEED_FAILED",
        "LOCAL_BUSINESS_CONTRACT_IMPORT_FAILED",
        "LOCAL_BUSINESS_P2_FAILED",
        "LOCAL_BUSINESS_P4_P7_FAILED",
        "LOCAL_BUSINESS_TENANT_BOUNDARY_FAILED",
        "LOCAL_BUSINESS_EVIDENCE_FAILED",
        "LOCAL_BUSINESS_ILLEGAL_TRANSITION_FAILED",
        "LOCAL_BUSINESS_ILLEGAL_TRANSITION_PRECONDITION_FAILED",
        "LOCAL_BUSINESS_ILLEGAL_TRANSITION_STATUS_FAILED",
        "LOCAL_BUSINESS_ILLEGAL_TRANSITION_STATUS_200",
        "LOCAL_BUSINESS_ILLEGAL_TRANSITION_STATUS_400",
        "LOCAL_BUSINESS_ILLEGAL_TRANSITION_STATUS_401",
        "LOCAL_BUSINESS_ILLEGAL_TRANSITION_STATUS_403",
        "LOCAL_BUSINESS_ILLEGAL_TRANSITION_STATUS_404",
        "LOCAL_BUSINESS_ILLEGAL_TRANSITION_CASE_NOT_FOUND",
        "LOCAL_BUSINESS_ILLEGAL_TRANSITION_ROUTE_NOT_FOUND",
        "LOCAL_BUSINESS_ILLEGAL_TRANSITION_STATUS_405",
        "LOCAL_BUSINESS_ILLEGAL_TRANSITION_STATUS_422",
        "LOCAL_BUSINESS_ILLEGAL_TRANSITION_STATUS_500",
        "LOCAL_BUSINESS_ILLEGAL_TRANSITION_DETAIL_FAILED",
        "LOCAL_BUSINESS_ILLEGAL_TRANSITION_TRANSACTION_FAILED",
        "LOCAL_BUSINESS_EXTERNAL_CALL_DETECTED",
        "LOCAL_BUSINESS_ENGINE_DISPOSE_FAILED",
        "LOCAL_BUSINESS_ENGINE_DATABASE_DRIFT",
        "LOCAL_BUSINESS_ILLEGAL_TRANSITION_RLS_PRECONDITION_FAILED",
        "LOCAL_BUSINESS_ENGINE_RECREATE_FAILED",
        "LOCAL_BUSINESS_RESTART_PERSISTENCE_FAILED",
        "LOCAL_BUSINESS_RESTART_TENANT_BOUNDARY_FAILED",
        "LOCAL_BUSINESS_SOURCE_MUTATED",
        "LOCAL_BUSINESS_CLEANUP_FAILED",
        "LOCAL_BUSINESS_INTERNAL_ERROR",
    }
)


class BusinessVerificationError(RuntimeError):
    """A fixed, non-sensitive business verification failure."""

    def __init__(self, reason: str) -> None:
        safe_reason = (
            reason
            if reason in FAILURE_REASONS
            else "LOCAL_BUSINESS_INTERNAL_ERROR"
        )
        super().__init__(safe_reason)
        self.reason = safe_reason


@dataclass(frozen=True, slots=True)
class BusinessVerificationCounts:
    scratch_database_count: int = 1
    p2_api_chain_count: int = 1
    p4_api_chain_count: int = 1
    p5_api_chain_count: int = 1
    p6_api_chain_count: int = 1
    p7_api_chain_count: int = 1
    oidc_dependency_override_count: int = 4
    illegal_state_transition_409_count: int = 1
    illegal_transition_business_delta_count: int = 0
    illegal_transition_audit_delta_count: int = 0
    illegal_transition_timeline_delta_count: int = 0
    illegal_transition_notification_delta_count: int = 0
    application_engine_restart_count: int = 1
    post_restart_business_read_count: int = 5
    post_restart_cross_tenant_detail_leak_count: int = 0
    post_restart_cross_tenant_list_leak_count: int = 0
    cross_tenant_api_leak_count: int = 0
    rls_select_leak_count: int = 0
    rls_write_leak_count: int = 0
    timeline_gap_count: int = 0
    audit_gap_count: int = 0
    notification_failure_count: int = 0
    external_call_count: int = 0
    source_business_row_delta_count: int = 0
    scratch_database_residual_count: int = 0


@dataclass(frozen=True, slots=True)
class ScratchAdapter:
    """The narrow fixture surface consumed by the existing smoke contracts."""

    run_id: str
    database: str
    host: str
    port: int
    secret_directory: Path
    bootstrap_dsn: str
    api_password: str

    def f1_environment(self) -> dict[str, str]:
        values = {
            "F1_PG_HOST": self.host,
            "F1_PG_PORT": str(self.port),
            "F1_PG_DATABASE": self.database,
            "F1_SECRETS_DIR": str(self.secret_directory),
            "F1_API_PASSWORD_FILE": str(
                self.secret_directory / "f1_api_password"
            ),
            "F1_WORKER_PASSWORD_FILE": str(
                self.secret_directory / "f1_worker_password"
            ),
        }
        for name in (
            "F1_KEYCLOAK_REALM",
            "F1_KEYCLOAK_ISSUER_URL",
            "KEYCLOAK_URL",
            "MINIO_ENDPOINT",
            "REDIS_URL",
            "OTEL_SDK_DISABLED",
            "F1_EXTERNAL_PIPELINES_ENABLED",
        ):
            value = os.environ.get(name)
            if value is not None:
                values[name] = value
        return values

    @staticmethod
    def _connection_kwargs(dsn: str) -> dict[str, Any]:
        from sqlalchemy.engine import make_url

        value = make_url(dsn)
        return {
            "host": value.host,
            "port": value.port,
            "dbname": value.database,
            "user": value.username,
            "password": value.password,
        }

    def bootstrap_kwargs(self) -> dict[str, Any]:
        return self._connection_kwargs(self.bootstrap_dsn)

    def api_kwargs(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.database,
            "user": "f1_api",
            "password": self.api_password,
        }


def scratch_database_name(source_database: str, nonce: str) -> str:
    """Return a random scratch name bound to the current closeout project."""
    source_match = SOURCE_DATABASE_RE.fullmatch(source_database)
    if source_match is None or re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        raise BusinessVerificationError("LOCAL_BUSINESS_SOURCE_INVALID")
    name = (
        f"anhuan_business_{source_match.group(1)[:12]}_{nonce[:16]}"
    )
    if SCRATCH_DATABASE_RE.fullmatch(name) is None:
        raise BusinessVerificationError("LOCAL_BUSINESS_SOURCE_INVALID")
    return name


def rewrite_dsn_database(
    dsn: str,
    *,
    source_database: str,
    scratch_database: str,
    expected_user: str,
) -> str:
    """Change only the database component of a validated local DSN."""
    from sqlalchemy.engine import make_url

    if (
        SOURCE_DATABASE_RE.fullmatch(source_database) is None
        or SCRATCH_DATABASE_RE.fullmatch(scratch_database) is None
    ):
        raise BusinessVerificationError("LOCAL_BUSINESS_SOURCE_INVALID")
    try:
        value = make_url(dsn)
    except (TypeError, ValueError):
        raise BusinessVerificationError("LOCAL_BUSINESS_SOURCE_INVALID") from None
    if (
        value.drivername not in {"postgresql", "postgresql+psycopg"}
        or value.username != expected_user
        or not value.password
        or not value.host
        or value.port is None
        or value.database != source_database
        or bool(value.query)
    ):
        raise BusinessVerificationError("LOCAL_BUSINESS_SOURCE_INVALID")
    return value.set(
        drivername="postgresql",
        database=scratch_database,
    ).render_as_string(hide_password=False)


def render_success(counts: BusinessVerificationCounts) -> tuple[str, str]:
    metrics = json.dumps(asdict(counts), separators=(",", ":"), sort_keys=True)
    return metrics, "LOCAL_BUSINESS_VERIFY_OK"


def _write_secret(path: Path, value: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        raw = value.encode("ascii")
        descriptor = os.open(path, flags, 0o600)
        try:
            offset = 0
            while offset < len(raw):
                written = os.write(descriptor, raw[offset:])
                if written < 1:
                    raise OSError
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        info = path.lstat()
    except (OSError, UnicodeEncodeError):
        raise BusinessVerificationError("LOCAL_BUSINESS_INTERNAL_ERROR") from None
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.geteuid()
        or info.st_size != len(raw)
    ):
        raise BusinessVerificationError("LOCAL_BUSINESS_INTERNAL_ERROR")


def _scratch_secret_directory(
    configuration: Any,
    scratch_database: str,
) -> tuple[Path, str, str]:
    directory = Path(tempfile.mkdtemp(prefix="anhuan-business-secrets-"))
    os.chmod(directory, 0o700)
    try:
        scratch_bootstrap = rewrite_dsn_database(
            configuration.bootstrap_dsn,
            source_database=configuration.database,
            scratch_database=scratch_database,
            expected_user="f0d_bootstrap",
        )
        scratch_f0_migration = rewrite_dsn_database(
            configuration.f0_migration_dsn,
            source_database=configuration.database,
            scratch_database=scratch_database,
            expected_user="f0d_migration",
        )
        values = {
            "f0d_migration_dsn": scratch_f0_migration,
            "f1_bootstrap_dsn": scratch_bootstrap,
            "f1_migration_dsn": rewrite_dsn_database(
                configuration.f1_migration_dsn,
                source_database=configuration.database,
                scratch_database=scratch_database,
                expected_user="f0d_migration",
            ),
            "f1_api_password": configuration.f1_api_password,
            "f1_worker_password": configuration.f1_worker_password,
        }
        for name in SECRET_NAMES:
            _write_secret(directory / name, values[name])
        return directory, scratch_bootstrap, scratch_f0_migration
    except BaseException as original_error:
        try:
            shutil.rmtree(directory)
        except BaseException:
            raise BusinessVerificationError(
                "LOCAL_BUSINESS_CLEANUP_FAILED"
            ) from None
        if directory.exists():
            raise BusinessVerificationError(
                "LOCAL_BUSINESS_CLEANUP_FAILED"
            )
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
            raise BusinessVerificationError("LOCAL_BUSINESS_CLEANUP_FAILED")
        for name in SECRET_NAMES:
            file_info = (directory / name).lstat()
            if (
                not stat.S_ISREG(file_info.st_mode)
                or stat.S_ISLNK(file_info.st_mode)
                or file_info.st_nlink != 1
                or stat.S_IMODE(file_info.st_mode) != 0o600
                or file_info.st_uid != os.geteuid()
            ):
                raise BusinessVerificationError(
                    "LOCAL_BUSINESS_CLEANUP_FAILED"
                )
        shutil.rmtree(directory)
        if directory.exists():
            raise BusinessVerificationError("LOCAL_BUSINESS_CLEANUP_FAILED")
    except BusinessVerificationError:
        raise
    except BaseException:
        raise BusinessVerificationError("LOCAL_BUSINESS_CLEANUP_FAILED") from None


@contextlib.contextmanager
def temporary_scratch_environment(
    adapter: ScratchAdapter,
    *,
    f0_migration_dsn: str,
) -> Iterator[None]:
    changes = adapter.f1_environment()
    changes["F0D_MIGRATION_DSN"] = f0_migration_dsn
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


def _scratch_comment(source_database: str, nonce: str) -> str:
    return (
        "io.anhuan.scope=engineering-closeout;"
        f"source={source_database};run={nonce}"
    )


def _assert_scratch_absent(configuration: Any, scratch_database: str) -> None:
    import psycopg

    try:
        with psycopg.connect(
            configuration.bootstrap_dsn, autocommit=True
        ) as connection:
            row = connection.execute(
                "SELECT count(*) FROM pg_database WHERE datname=%s",
                (scratch_database,),
            ).fetchone()
        if row is None or int(row[0]) != 0:
            raise BusinessVerificationError(
                "LOCAL_BUSINESS_SCRATCH_CREATE_FAILED"
            )
    except BusinessVerificationError:
        raise
    except BaseException:
        raise BusinessVerificationError(
            "LOCAL_BUSINESS_SCRATCH_CREATE_FAILED"
        ) from None


def _create_scratch_database(
    configuration: Any,
    scratch_database: str,
    comment: str,
) -> None:
    import psycopg
    from psycopg import sql

    created = False
    try:
        with psycopg.connect(
            configuration.bootstrap_dsn, autocommit=True
        ) as connection:
            connection.execute(
                sql.SQL(
                    "CREATE DATABASE {} OWNER f0d_migration TEMPLATE template0"
                ).format(sql.Identifier(scratch_database))
            )
            created = True
            connection.execute(
                sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                    sql.Identifier(scratch_database), sql.Literal(comment)
                )
            )
    except BaseException as original_error:
        if created:
            try:
                with psycopg.connect(
                    configuration.bootstrap_dsn, autocommit=True
                ) as cleanup_connection:
                    row = cleanup_connection.execute(
                        "SELECT r.rolname,description.description "
                        "FROM pg_database AS d JOIN pg_roles AS r ON r.oid=d.datdba "
                        "LEFT JOIN pg_shdescription AS description "
                        "ON description.objoid=d.oid AND description.classoid="
                        "'pg_database'::regclass WHERE d.datname=%s",
                        (scratch_database,),
                    ).fetchone()
                    if row is None or tuple(row) not in {
                        ("f0d_migration", None),
                        ("f0d_migration", comment),
                    }:
                        raise BusinessVerificationError(
                            "LOCAL_BUSINESS_CLEANUP_FAILED"
                        )
                    cleanup_connection.execute(
                        sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                            sql.Identifier(scratch_database)
                        )
                    )
            except BusinessVerificationError:
                raise
            except BaseException:
                raise BusinessVerificationError(
                    "LOCAL_BUSINESS_CLEANUP_FAILED"
                ) from None
        raise BusinessVerificationError(
            "LOCAL_BUSINESS_SCRATCH_CREATE_FAILED"
        ) from original_error


def _tag_and_harden_scratch_database(
    configuration: Any,
    *,
    scratch_database: str,
    scratch_bootstrap_dsn: str,
    comment: str,
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
                raise BusinessVerificationError(
                    "LOCAL_BUSINESS_SCRATCH_IDENTITY_MISMATCH"
                )
            connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            connection.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                    sql.Identifier(scratch_database)
                )
            )
    except BusinessVerificationError:
        raise
    except BaseException:
        raise BusinessVerificationError(
            "LOCAL_BUSINESS_SCRATCH_IDENTITY_MISMATCH"
        ) from None


def _drop_scratch_database(
    configuration: Any,
    *,
    scratch_database: str,
    comment: str,
) -> None:
    import psycopg
    from psycopg import sql

    if SCRATCH_DATABASE_RE.fullmatch(scratch_database) is None:
        raise BusinessVerificationError("LOCAL_BUSINESS_CLEANUP_FAILED")
    try:
        with psycopg.connect(
            configuration.bootstrap_dsn, autocommit=True
        ) as connection:
            row = connection.execute(
                "SELECT d.datname,r.rolname,description.description "
                "FROM pg_database AS d "
                "JOIN pg_roles AS r ON r.oid=d.datdba "
                "LEFT JOIN pg_shdescription AS description "
                "ON description.objoid=d.oid AND description.classoid="
                "'pg_database'::regclass WHERE d.datname=%s",
                (scratch_database,),
            ).fetchone()
            if row is None or tuple(row) != (
                scratch_database,
                "f0d_migration",
                comment,
            ):
                raise BusinessVerificationError(
                    "LOCAL_BUSINESS_CLEANUP_FAILED"
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
                raise BusinessVerificationError(
                    "LOCAL_BUSINESS_CLEANUP_FAILED"
                )
    except BusinessVerificationError:
        raise
    except BaseException:
        raise BusinessVerificationError("LOCAL_BUSINESS_CLEANUP_FAILED") from None


def _source_business_counts(configuration: Any) -> tuple[tuple[str, int], ...]:
    import psycopg
    from psycopg import sql

    try:
        from infra.f1.local_migrate import P2_P7_TABLES

        observed: list[tuple[str, int]] = []
        with psycopg.connect(
            configuration.bootstrap_dsn, autocommit=True
        ) as connection:
            for table in sorted(P2_P7_TABLES):
                row = connection.execute(
                    sql.SQL("SELECT count(*) FROM f1.{}").format(
                        sql.Identifier(table)
                    )
                ).fetchone()
                if row is None:
                    raise BusinessVerificationError(
                        "LOCAL_BUSINESS_SOURCE_NOT_READY"
                    )
                observed.append((table, int(row[0])))
        return tuple(observed)
    except BusinessVerificationError:
        raise
    except BaseException:
        raise BusinessVerificationError(
            "LOCAL_BUSINESS_SOURCE_NOT_READY"
        ) from None


def actors_from_local_seed() -> dict[str, dict[str, Any]]:
    """Build the smoke actor map from the actual deterministic seed contract."""
    try:
        from infra.f1 import local_seed

        aliases = {
            "admin-a": "admin",
            "enterprise": "enterprise",
            "employee": "employee",
            "consultant": "consultant",
            "partner": "partner",
            "tenant-b": "tenant_b",
        }
        actors: dict[str, dict[str, Any]] = {}
        for binding in local_seed.BINDINGS:
            alias = aliases.get(binding.name)
            if alias is None:
                continue
            actors[alias] = {
                "enterprise_id": binding.enterprise_id,
                "user_id": local_seed._stable_id("profile", binding.sub),
                "sub": binding.sub,
                "role": binding.role,
            }
        if set(actors) != set(aliases.values()):
            raise BusinessVerificationError("LOCAL_BUSINESS_SEED_FAILED")
        return actors
    except BusinessVerificationError:
        raise
    except BaseException:
        raise BusinessVerificationError("LOCAL_BUSINESS_SEED_FAILED") from None


def _load_smoke_contracts() -> tuple[Any, Any]:
    try:
        from tests import p2_real_pg_api_smoke as p2
        from tests import p4_p7_real_api_smoke as p4_p7

        if p4_p7.SmokeFailure is not p2.SmokeFailure:
            raise BusinessVerificationError(
                "LOCAL_BUSINESS_CONTRACT_IMPORT_FAILED"
            )
        return p2, p4_p7
    except BusinessVerificationError:
        raise
    except BaseException:
        raise BusinessVerificationError(
            "LOCAL_BUSINESS_CONTRACT_IMPORT_FAILED"
        ) from None


def _reset_contract_metrics(module: Any) -> None:
    try:
        for name in tuple(module.METRICS):
            module.METRICS[name] = 0
    except BaseException:
        raise BusinessVerificationError(
            "LOCAL_BUSINESS_CONTRACT_IMPORT_FAILED"
        ) from None


def _map_smoke_failure(code: str, *, p2_stage: bool) -> str:
    if code.startswith(("CROSS_TENANT", "RLS_SELECT", "RLS_WRITE")):
        return "LOCAL_BUSINESS_TENANT_BOUNDARY_FAILED"
    if code.startswith(("TIMELINE", "AUDIT", "NOTIFICATION")):
        return "LOCAL_BUSINESS_EVIDENCE_FAILED"
    if "NOTIFICATION" in code:
        return "LOCAL_BUSINESS_EVIDENCE_FAILED"
    return "LOCAL_BUSINESS_P2_FAILED" if p2_stage else "LOCAL_BUSINESS_P4_P7_FAILED"


def _metric_failure_reason(p2: Any, p4_p7: Any) -> str | None:
    external_calls = int(p2.METRICS.get("external_calls", 0)) + int(
        p4_p7.METRICS.get("external_calls", 0)
    )
    if external_calls:
        return "LOCAL_BUSINESS_EXTERNAL_CALL_DETECTED"
    boundary_names = (
        "cross_tenant_api_leaks",
        "rls_select_leaks",
        "rls_write_leaks",
    )
    if any(
        int(module.METRICS.get(name, 0))
        for module in (p2, p4_p7)
        for name in boundary_names
    ):
        return "LOCAL_BUSINESS_TENANT_BOUNDARY_FAILED"
    evidence_names = (
        "timeline_gaps",
        "audit_gaps",
        "notification_failures",
    )
    if any(
        int(module.METRICS.get(name, 0))
        for module in (p2, p4_p7)
        for name in evidence_names
    ):
        return "LOCAL_BUSINESS_EVIDENCE_FAILED"
    if any(int(value) for value in p2.METRICS.values()):
        return "LOCAL_BUSINESS_P2_FAILED"
    if any(int(value) for value in p4_p7.METRICS.values()):
        return "LOCAL_BUSINESS_P4_P7_FAILED"
    return None


def _build_business_probe_app(
    actors: dict[str, dict[str, Any]],
) -> Any:
    """Build a fresh API surface while retaining real tenant/RLS plumbing."""
    try:
        from fastapi import FastAPI, Header, HTTPException
        from platform_foundation.f1 import auth
        from platform_foundation.f1.api.routers import (
            p4_views_reports,
            p5_policy_workflow,
            p6_automated_quality,
            p7_local_rehearsal,
            service_cases,
        )

        app = FastAPI()
        app.include_router(service_cases.router, prefix="/api/v1/service-cases")
        app.include_router(
            p4_views_reports.router, prefix="/api/v1/views-reports"
        )
        app.include_router(
            p5_policy_workflow.router, prefix="/api/v1/policy-workflow"
        )
        app.include_router(
            p6_automated_quality.router, prefix="/api/v1/automated-quality"
        )
        app.include_router(
            p7_local_rehearsal.router, prefix="/api/v1/local-rehearsal"
        )

        async def synthetic_user(
            x_engineering_actor: str | None = Header(default=None),
        ) -> dict[str, Any]:
            actor = actors.get(x_engineering_actor or "")
            if actor is None:
                raise HTTPException(
                    status_code=401,
                    detail="ENGINEERING_IDENTITY_REQUIRED",
                )
            return {"sub": actor["sub"], "roles": ()}

        async def synthetic_tenant(
            x_engineering_actor: str | None = Header(default=None),
            x_enterprise_id: str | None = Header(default=None),
        ) -> auth.Tenant:
            actor = actors.get(x_engineering_actor or "")
            if actor is None:
                raise HTTPException(
                    status_code=401,
                    detail="ENGINEERING_IDENTITY_REQUIRED",
                )
            try:
                enterprise_id = uuid.UUID(x_enterprise_id or "")
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="ENGINEERING_TENANT_REQUIRED",
                ) from None
            return await auth.current_tenant(
                {"sub": actor["sub"], "roles": ()},
                enterprise_id,
            )

        app.dependency_overrides[auth.current_user] = synthetic_user
        app.dependency_overrides[service_cases.tenant_from_header] = (
            synthetic_tenant
        )
        return app
    except BaseException:
        raise BusinessVerificationError(
            "LOCAL_BUSINESS_CONTRACT_IMPORT_FAILED"
        ) from None


def _illegal_transition_snapshot(
    adapter: ScratchAdapter,
    *,
    enterprise_id: uuid.UUID,
    case_id: uuid.UUID,
) -> tuple[tuple[tuple[Any, ...], ...], ...]:
    """Read the complete rows that a rejected close must not mutate."""
    import psycopg

    try:
        with psycopg.connect(**adapter.bootstrap_kwargs()) as connection:
            business_rows = connection.execute(
                "SELECT id,enterprise_id,plant_id,title,description,service_type,"
                "status,planned_start_at,planned_end_at,created_by_user_id,"
                "created_at,updated_at FROM f1.service_case "
                "WHERE enterprise_id=%s AND id=%s ORDER BY id",
                (enterprise_id, case_id),
            ).fetchall()
            audit_rows = connection.execute(
                "SELECT id,user_sub,action,resource_type,resource_id,result,"
                "created_at FROM f1.audit_log WHERE enterprise_id=%s "
                "ORDER BY id",
                (enterprise_id,),
            ).fetchall()
            timeline_rows = connection.execute(
                "SELECT id,enterprise_id,service_case_id,event_type,subject_type,"
                "subject_id,status,actor_user_id,occurred_at "
                "FROM f1.business_timeline WHERE enterprise_id=%s "
                "AND service_case_id=%s ORDER BY id",
                (enterprise_id, case_id),
            ).fetchall()
            notification_rows = connection.execute(
                "SELECT notification.id,notification.enterprise_id,"
                "notification.recipient_user_id,notification.timeline_event_id,"
                "notification.created_at,notification.read_at "
                "FROM f1.in_app_notification AS notification "
                "JOIN f1.business_timeline AS timeline "
                "ON timeline.enterprise_id=notification.enterprise_id "
                "AND timeline.id=notification.timeline_event_id "
                "WHERE timeline.enterprise_id=%s "
                "AND timeline.service_case_id=%s ORDER BY notification.id",
                (enterprise_id, case_id),
            ).fetchall()
        snapshots = tuple(
            tuple(tuple(row) for row in rows)
            for rows in (
                business_rows,
                audit_rows,
                timeline_rows,
                notification_rows,
            )
        )
        if len(snapshots[0]) != 1 or str(snapshots[0][0][6]) != "closed":
            raise BusinessVerificationError(
                "LOCAL_BUSINESS_ILLEGAL_TRANSITION_PRECONDITION_FAILED"
            )
        return snapshots
    except BusinessVerificationError:
        raise
    except BaseException:
        raise BusinessVerificationError(
            "LOCAL_BUSINESS_ILLEGAL_TRANSITION_TRANSACTION_FAILED"
        ) from None


async def _verify_illegal_transition_rollback(
    adapter: ScratchAdapter,
    actors: dict[str, dict[str, Any]],
    case_id: uuid.UUID,
) -> None:
    """Reject a real terminal-state transition without any partial evidence."""
    import httpx
    from platform_foundation.f1 import database

    engine = database._engines.get("f1_api")
    try:
        engine_database = str(engine.url.database)  # type: ignore[union-attr]
    except (AttributeError, TypeError):
        engine_database = ""
    if engine_database != adapter.database:
        raise BusinessVerificationError("LOCAL_BUSINESS_ENGINE_DATABASE_DRIFT")

    enterprise_id = actors["admin"]["enterprise_id"]
    try:
        from sqlalchemy import text
        from platform_foundation.f1.database import session_scope

        async with session_scope(
            role="f1_api",
            enterprise_id=enterprise_id,
            sub=str(actors["admin"]["sub"]),
        ) as session:
            visible = (
                await session.execute(
                    text("SELECT count(*) FROM f1.service_case WHERE id=:id"),
                    {"id": case_id},
                )
            ).scalar_one()
        if int(visible) != 1:
            raise BusinessVerificationError(
                "LOCAL_BUSINESS_ILLEGAL_TRANSITION_RLS_PRECONDITION_FAILED"
            )
    except BusinessVerificationError:
        raise
    except BaseException:
        raise BusinessVerificationError(
            "LOCAL_BUSINESS_ILLEGAL_TRANSITION_RLS_PRECONDITION_FAILED"
        ) from None
    before = _illegal_transition_snapshot(
        adapter,
        enterprise_id=enterprise_id,
        case_id=case_id,
    )
    app = _build_business_probe_app(actors)
    try:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://business-illegal.invalid",
        ) as client:
            response = await client.post(
                f"/api/v1/service-cases/{case_id}/close",
                headers={
                    "X-Engineering-Actor": "admin",
                    "X-Enterprise-Id": str(enterprise_id),
                },
            )
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.status_code != 409:
            if response.status_code == 404 and payload == {
                "detail": "SERVICE_CASE_NOT_FOUND"
            }:
                raise BusinessVerificationError(
                    "LOCAL_BUSINESS_ILLEGAL_TRANSITION_CASE_NOT_FOUND"
                )
            if response.status_code == 404 and payload == {"detail": "Not Found"}:
                raise BusinessVerificationError(
                    "LOCAL_BUSINESS_ILLEGAL_TRANSITION_ROUTE_NOT_FOUND"
                )
            observed_status = (
                response.status_code
                if response.status_code in {200, 400, 401, 403, 404, 405, 422, 500}
                else None
            )
            raise BusinessVerificationError(
                "LOCAL_BUSINESS_ILLEGAL_TRANSITION_STATUS_FAILED"
                if observed_status is None
                else f"LOCAL_BUSINESS_ILLEGAL_TRANSITION_STATUS_{observed_status}"
            )
        if payload != {"detail": "SERVICE_CASE_NOT_CLOSABLE"}:
            raise BusinessVerificationError(
                "LOCAL_BUSINESS_ILLEGAL_TRANSITION_DETAIL_FAILED"
            )
    except BusinessVerificationError:
        raise
    except BaseException:
        raise BusinessVerificationError(
            "LOCAL_BUSINESS_ILLEGAL_TRANSITION_FAILED"
        ) from None

    after = _illegal_transition_snapshot(
        adapter,
        enterprise_id=enterprise_id,
        case_id=case_id,
    )
    if after != before:
        raise BusinessVerificationError(
            "LOCAL_BUSINESS_ILLEGAL_TRANSITION_TRANSACTION_FAILED"
        )


def _response_identifier(response: Any) -> uuid.UUID | None:
    try:
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        return uuid.UUID(str(payload.get("id")))
    except (TypeError, ValueError):
        return None


def _response_has_empty_items(response: Any) -> bool:
    try:
        payload = response.json()
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("items") == []


async def _verify_engine_restart_persistence(
    actors: dict[str, dict[str, Any]],
    p2_identifiers: dict[str, uuid.UUID],
    p4_p7_identifiers: dict[str, tuple[str, uuid.UUID]],
    *,
    dispose_database_engines: Any,
) -> None:
    """Dispose application pools, recreate them, and recheck data plus RLS."""
    import httpx
    from platform_foundation.f1 import database

    old_engine = database._engines.get("f1_api")
    if old_engine is None:
        raise BusinessVerificationError(
            "LOCAL_BUSINESS_ENGINE_DISPOSE_FAILED"
        )
    try:
        await dispose_database_engines()
    except BaseException:
        raise BusinessVerificationError(
            "LOCAL_BUSINESS_ENGINE_DISPOSE_FAILED"
        ) from None
    if database._engines or database._factories:
        raise BusinessVerificationError(
            "LOCAL_BUSINESS_ENGINE_DISPOSE_FAILED"
        )

    detail_contracts = (
        (
            f"/api/v1/service-cases/{p2_identifiers['case_id']}",
            p2_identifiers["case_id"],
        ),
        (
            "/api/v1/views-reports/crm/accounts/"
            f"{p4_p7_identifiers['crm_account'][1]}",
            p4_p7_identifiers["crm_account"][1],
        ),
        (
            "/api/v1/policy-workflow/sources/"
            f"{p4_p7_identifiers['policy_source'][1]}",
            p4_p7_identifiers["policy_source"][1],
        ),
        (
            "/api/v1/automated-quality/suites/"
            f"{p4_p7_identifiers['quality_suite'][1]}",
            p4_p7_identifiers["quality_suite"][1],
        ),
        (
            "/api/v1/local-rehearsal/plans/"
            f"{p4_p7_identifiers['rehearsal_plan'][1]}",
            p4_p7_identifiers["rehearsal_plan"][1],
        ),
    )
    list_contracts = (
        "/api/v1/service-cases",
        "/api/v1/views-reports/crm/accounts",
        "/api/v1/policy-workflow/sources",
        "/api/v1/automated-quality/suites",
        "/api/v1/local-rehearsal/plans",
    )
    app = _build_business_probe_app(actors)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://business-restart.invalid",
        ) as client:
            for path, expected_identifier in detail_contracts:
                response = await client.get(
                    path,
                    headers={
                        "X-Engineering-Actor": "admin",
                        "X-Enterprise-Id": str(
                            actors["admin"]["enterprise_id"]
                        ),
                    },
                )
                if (
                    response.status_code != 200
                    or _response_identifier(response) != expected_identifier
                ):
                    raise BusinessVerificationError(
                        "LOCAL_BUSINESS_RESTART_PERSISTENCE_FAILED"
                    )
            for path, _ in detail_contracts:
                response = await client.get(
                    path,
                    headers={
                        "X-Engineering-Actor": "tenant_b",
                        "X-Enterprise-Id": str(
                            actors["tenant_b"]["enterprise_id"]
                        ),
                    },
                )
                if response.status_code != 404:
                    raise BusinessVerificationError(
                        "LOCAL_BUSINESS_RESTART_TENANT_BOUNDARY_FAILED"
                    )
            for path in list_contracts:
                response = await client.get(
                    path,
                    headers={
                        "X-Engineering-Actor": "tenant_b",
                        "X-Enterprise-Id": str(
                            actors["tenant_b"]["enterprise_id"]
                        ),
                    },
                )
                if response.status_code != 200 or not _response_has_empty_items(
                    response
                ):
                    raise BusinessVerificationError(
                        "LOCAL_BUSINESS_RESTART_TENANT_BOUNDARY_FAILED"
                    )
    except BusinessVerificationError:
        raise
    except BaseException:
        raise BusinessVerificationError(
            "LOCAL_BUSINESS_RESTART_PERSISTENCE_FAILED"
        ) from None

    new_engine = database._engines.get("f1_api")
    if (
        new_engine is None
        or new_engine is old_engine
        or "f1_api" not in database._factories
    ):
        raise BusinessVerificationError(
            "LOCAL_BUSINESS_ENGINE_RECREATE_FAILED"
        )


async def _run_smoke_contracts(
    adapter: ScratchAdapter,
    actors: dict[str, dict[str, Any]],
) -> None:
    p2, p4_p7 = _load_smoke_contracts()
    _reset_contract_metrics(p2)
    _reset_contract_metrics(p4_p7)
    stage_is_p2 = True
    pending_error: BusinessVerificationError | None = None
    try:
        try:
            p2_identifiers = await p2._api_smoke(adapter, actors)
            p2._direct_rls_and_evidence(adapter, actors, p2_identifiers)
            stage_is_p2 = False
            p4_p7_identifiers = await p4_p7._api_smoke(adapter, actors)
            p4_p7._direct_rls_and_audit(
                adapter, actors, p4_p7_identifiers
            )
            await _verify_illegal_transition_rollback(
                adapter,
                actors,
                p2_identifiers["case_id"],
            )
            await _verify_engine_restart_persistence(
                actors,
                p2_identifiers,
                p4_p7_identifiers,
                dispose_database_engines=p2._dispose_database_engines,
            )
        except BusinessVerificationError as error:
            pending_error = error
        except p2.SmokeFailure as error:
            pending_error = BusinessVerificationError(
                _map_smoke_failure(error.code, p2_stage=stage_is_p2)
            )
        except BaseException:
            pending_error = BusinessVerificationError(
                "LOCAL_BUSINESS_P2_FAILED"
                if stage_is_p2
                else "LOCAL_BUSINESS_P4_P7_FAILED"
            )
    finally:
        try:
            await p2._dispose_database_engines()
        except BaseException:
            pending_error = BusinessVerificationError(
                "LOCAL_BUSINESS_ENGINE_DISPOSE_FAILED"
            )

    metric_reason = _metric_failure_reason(p2, p4_p7)
    if metric_reason is not None:
        pending_error = BusinessVerificationError(metric_reason)
    if pending_error is not None:
        raise pending_error


def _migrate_seed_and_verify(
    *,
    adapter: ScratchAdapter,
    f0_migration_dsn: str,
) -> None:
    try:
        from infra.f1 import local_migrate
        from infra.f1.local_migration_atomicity import (
            _scratch_f0_schema_bootstrap,
        )

        with temporary_scratch_environment(
            adapter, f0_migration_dsn=f0_migration_dsn
        ):
            with _scratch_f0_schema_bootstrap(local_migrate):
                local_migrate.migrate()
    except BusinessVerificationError:
        raise
    except BaseException:
        raise BusinessVerificationError("LOCAL_BUSINESS_MIGRATION_FAILED") from None

    try:
        from infra.f1 import local_seed

        with temporary_scratch_environment(
            adapter, f0_migration_dsn=f0_migration_dsn
        ):
            seed_output = io.StringIO()
            with contextlib.redirect_stdout(seed_output):
                result = local_seed.main()
            if result != 0 or seed_output.getvalue().splitlines() != [
                "LOCAL_SEED_OK"
            ]:
                raise BusinessVerificationError("LOCAL_BUSINESS_SEED_FAILED")
            asyncio.run(_run_smoke_contracts(adapter, actors_from_local_seed()))
    except BusinessVerificationError:
        raise
    except BaseException:
        raise BusinessVerificationError("LOCAL_BUSINESS_SEED_FAILED") from None


def _load_source_configuration() -> Any:
    try:
        from infra.f1 import local_migration_atomicity as atomicity

        configuration = atomicity._load_source_configuration()
        atomicity._assert_source_ready(configuration)
        if SOURCE_DATABASE_RE.fullmatch(configuration.database) is None:
            raise BusinessVerificationError("LOCAL_BUSINESS_SOURCE_INVALID")
        return configuration
    except BusinessVerificationError:
        raise
    except BaseException:
        raise BusinessVerificationError("LOCAL_BUSINESS_SOURCE_NOT_READY") from None


def run() -> BusinessVerificationCounts:
    configuration: Any | None = None
    scratch_database: str | None = None
    scratch_comment: str | None = None
    secret_directory: Path | None = None
    scratch_created = False
    pending_error: BusinessVerificationError | None = None
    source_counts_before: tuple[tuple[str, int], ...] | None = None

    try:
        configuration = _load_source_configuration()
        source_counts_before = _source_business_counts(configuration)
        nonce = uuid.uuid4().hex
        scratch_database = scratch_database_name(configuration.database, nonce)
        scratch_comment = _scratch_comment(configuration.database, nonce)
        _assert_scratch_absent(configuration, scratch_database)
        _create_scratch_database(
            configuration,
            scratch_database,
            scratch_comment,
        )
        scratch_created = True
        secret_directory, scratch_bootstrap_dsn, f0_migration_dsn = (
            _scratch_secret_directory(configuration, scratch_database)
        )
        _tag_and_harden_scratch_database(
            configuration,
            scratch_database=scratch_database,
            scratch_bootstrap_dsn=scratch_bootstrap_dsn,
            comment=scratch_comment,
        )

        from sqlalchemy.engine import make_url

        source_url = make_url(configuration.bootstrap_dsn)
        adapter = ScratchAdapter(
            run_id=nonce,
            database=scratch_database,
            host=str(source_url.host),
            port=int(source_url.port or 0),
            secret_directory=secret_directory,
            bootstrap_dsn=scratch_bootstrap_dsn,
            api_password=configuration.f1_api_password,
        )
        # Alembic and application internals may log diagnostics.  They are
        # intentionally retained only in memory and never cross this command's
        # aggregate-only output boundary.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
            io.StringIO()
        ):
            _migrate_seed_and_verify(
                adapter=adapter,
                f0_migration_dsn=f0_migration_dsn,
            )
    except BusinessVerificationError as error:
        pending_error = error
    except BaseException:
        pending_error = BusinessVerificationError(
            "LOCAL_BUSINESS_INTERNAL_ERROR"
        )

    cleanup_failed = False
    if (
        scratch_created
        and configuration is not None
        and scratch_database is not None
        and scratch_comment is not None
    ):
        try:
            _drop_scratch_database(
                configuration,
                scratch_database=scratch_database,
                comment=scratch_comment,
            )
        except BaseException:
            cleanup_failed = True
    if secret_directory is not None:
        try:
            _remove_secret_directory(secret_directory)
        except BaseException:
            cleanup_failed = True

    if cleanup_failed:
        raise BusinessVerificationError("LOCAL_BUSINESS_CLEANUP_FAILED")
    if configuration is not None and source_counts_before is not None:
        try:
            if _source_business_counts(configuration) != source_counts_before:
                raise BusinessVerificationError(
                    "LOCAL_BUSINESS_SOURCE_MUTATED"
                )
        except BusinessVerificationError as error:
            if pending_error is None:
                pending_error = error
    if pending_error is not None:
        raise pending_error
    return BusinessVerificationCounts()


def main() -> int:
    try:
        counts = run()
    except BaseException as error:
        reason = (
            error.reason
            if isinstance(error, BusinessVerificationError)
            else "LOCAL_BUSINESS_INTERNAL_ERROR"
        )
        print(reason, file=sys.stderr)
        return 1
    for line in render_success(counts):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
