"""One-shot real PostgreSQL/API/RLS smoke for P2 Business Workbench.

The runner owns one UUID-labelled PostgreSQL container and no other resource.
It runs the frozen F0 migrations only to provide the empty schemas required by
the independent F1 migration, then exercises the actual P2 routers as the
low-privilege ``f1_api`` role.  OIDC signature verification is deliberately
replaced by an in-process synthetic identity dependency; tenant resolution,
SQLAlchemy sessions, PostgreSQL constraints, transactions and FORCE RLS remain
real.

Stdout is aggregate-only.  No response body, SQL, UUID, path, DSN or secret is
printed.  This is P2 validation, not F1.1.1 formal acceptance or a release gate.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any
import uuid

import httpx
import psycopg
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
DOCKER = Path("/Applications/Docker.app/Contents/Resources/bin/docker")
POSTGRES_IMAGE = (
    "postgres:18.3-bookworm@sha256:"
    "80630f83606d8db77d30b3851b16a9f78be2d0d4dda6f7b82a1fdca5ebe3acba"
)
POSTGRES_IMAGE_ID = (
    "sha256:80630f83606d8db77d30b3851b16a9f78be2d0d4dda6f7b82a1fdca5ebe3acba"
)
SCOPE_LABEL = "p2-real-pg-api-smoke"
NAME_RE = re.compile(r"anhuan-p2-smoke-[0-9a-f]{32}\Z")
DATABASE_RE = re.compile(r"p2_smoke_[0-9a-f]{32}\Z")

METRICS = OrderedDict(
    (
        ("migration_head_mismatches", 0),
        ("catalog_failures", 0),
        ("wave1_failures", 0),
        ("wave2_failures", 0),
        ("wave3_failures", 0),
        ("wave4_failures", 0),
        ("cross_tenant_api_leaks", 0),
        ("rls_select_leaks", 0),
        ("rls_write_leaks", 0),
        ("timeline_gaps", 0),
        ("audit_gaps", 0),
        ("notification_failures", 0),
        ("calendar_kind_failures", 0),
        ("view_failures", 0),
        ("external_calls", 0),
        ("shared_pg_connections", 0),
        ("cleanup_residuals", 0),
        ("unexpected_failures", 0),
    )
)


class SmokeFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _secure_write(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, value.encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.geteuid()
    ):
        raise SmokeFailure("SECRET_FILE_RED")


def _clean_environment() -> dict[str, str]:
    kept = {
        "HOME": os.environ.get("HOME", ""),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONPATH": str(ROOT / "src"),
    }
    return {key: value for key, value in kept.items() if value}


def _process(
    arguments: list[str],
    *,
    environment: dict[str, str],
    timeout: int,
    cwd: Path = ROOT,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            arguments,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SmokeFailure("PROCESS_EXECUTION_RED") from None


class ScratchPostgres:
    def __init__(self) -> None:
        self.run_id = uuid.uuid4().hex
        self.container_name = f"anhuan-p2-smoke-{self.run_id}"
        self.database = f"p2_smoke_{self.run_id}"
        if not NAME_RE.fullmatch(self.container_name):
            raise SmokeFailure("SCRATCH_NAME_RED")
        if not DATABASE_RE.fullmatch(self.database):
            raise SmokeFailure("SCRATCH_DATABASE_RED")
        self.container_id: str | None = None
        self.endpoint = ""
        self.host_port = 0
        self.runtime_prefix = f"anhuan-p2-smoke-{self.run_id[:8]}-"
        self.runtime_root = Path(
            tempfile.mkdtemp(prefix=self.runtime_prefix, dir="/private/tmp")
        )
        os.chmod(self.runtime_root, 0o700)
        self.home = self.runtime_root / "home"
        self.tmp = self.runtime_root / "tmp"
        self.secrets_dir = self.runtime_root / "secrets"
        for directory in (self.home, self.tmp, self.secrets_dir):
            directory.mkdir(mode=0o700)
        self.passwords = {
            "bootstrap": secrets.token_hex(24),
            "migration": secrets.token_hex(24),
            "runtime": secrets.token_hex(24),
            "worker": secrets.token_hex(24),
            "f1_api": secrets.token_hex(24),
            "f1_worker": secrets.token_hex(24),
        }

    def _ambient_docker(self, *arguments: str) -> subprocess.CompletedProcess[bytes]:
        environment = _clean_environment()
        result = _process(
            [str(DOCKER), *arguments],
            environment=environment,
            timeout=30,
        )
        return result

    def _docker(self, *arguments: str, timeout: int = 60) -> subprocess.CompletedProcess[bytes]:
        if not self.endpoint.startswith("unix://"):
            raise SmokeFailure("DOCKER_ENDPOINT_RED")
        environment = _clean_environment()
        environment["HOME"] = str(self.home)
        environment["TMPDIR"] = str(self.tmp)
        return _process(
            [str(DOCKER), "--host", self.endpoint, *arguments],
            environment=environment,
            timeout=timeout,
        )

    def _validate_docker(self) -> None:
        try:
            info = DOCKER.lstat()
        except OSError:
            raise SmokeFailure("DOCKER_BINARY_RED") from None
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) & 0o022
            or not os.access(DOCKER, os.X_OK)
        ):
            raise SmokeFailure("DOCKER_BINARY_RED")
        context = self._ambient_docker("context", "inspect")
        if context.returncode != 0:
            raise SmokeFailure("DOCKER_CONTEXT_RED")
        try:
            payload = json.loads(context.stdout)
            endpoint = str(payload[0]["Endpoints"]["docker"]["Host"])
        except (IndexError, KeyError, TypeError, ValueError):
            raise SmokeFailure("DOCKER_CONTEXT_RED") from None
        if not endpoint.startswith("unix://"):
            raise SmokeFailure("DOCKER_CONTEXT_RED")
        socket_path = Path(endpoint.removeprefix("unix://"))
        try:
            socket_info = socket_path.resolve(strict=True).stat()
        except OSError:
            raise SmokeFailure("DOCKER_SOCKET_RED") from None
        if (
            not stat.S_ISSOCK(socket_info.st_mode)
            or socket_info.st_uid != os.geteuid()
            or stat.S_IMODE(socket_info.st_mode) & 0o022
        ):
            raise SmokeFailure("DOCKER_SOCKET_RED")
        self.endpoint = endpoint
        image = self._docker("image", "inspect", POSTGRES_IMAGE)
        try:
            image_payload = json.loads(image.stdout)[0]
            image_id = str(image_payload["Id"])
            repo_digests = {str(value) for value in image_payload["RepoDigests"]}
        except (IndexError, KeyError, TypeError, ValueError):
            raise SmokeFailure("POSTGRES_IMAGE_RED") from None
        expected_repo_digest = (
            "postgres@sha256:"
            "80630f83606d8db77d30b3851b16a9f78be2d0d4dda6f7b82a1fdca5ebe3acba"
        )
        if (
            image.returncode != 0
            or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None
            or expected_repo_digest not in repo_digests
        ):
            raise SmokeFailure("POSTGRES_IMAGE_RED")
        existing = self._docker(
            "ps",
            "-a",
            "--filter",
            f"label=com.anhuan.run={self.run_id}",
            "--format",
            "{{.ID}}",
        )
        if existing.returncode != 0 or existing.stdout.strip():
            raise SmokeFailure("SCRATCH_PREEXISTING_RED")

    def _write_container_environment(self) -> Path:
        environment_file = self.runtime_root / "postgres.env"
        lines = (
            f"POSTGRES_DB={self.database}",
            "POSTGRES_USER=f0d_bootstrap",
            f"POSTGRES_PASSWORD={self.passwords['bootstrap']}",
            "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256 --auth-local=trust --data-checksums",
            "PGDATA=/var/lib/postgresql/18/docker",
            f"F0D_MIGRATION_PASSWORD={self.passwords['migration']}",
            f"F0D_RUNTIME_PASSWORD={self.passwords['runtime']}",
            f"F0D_WORKER_PASSWORD={self.passwords['worker']}",
        )
        _secure_write(environment_file, "\n".join(lines) + "\n")
        return environment_file

    def start(self) -> None:
        self._validate_docker()
        environment_file = self._write_container_environment()
        roles_file = ROOT / "migrations/bootstrap/00_roles.sql"
        run = self._docker(
            "run",
            "--detach",
            "--pull=never",
            "--name",
            self.container_name,
            "--label",
            f"com.anhuan.scope={SCOPE_LABEL}",
            "--label",
            f"com.anhuan.run={self.run_id}",
            "--env-file",
            str(environment_file),
            "--publish",
            "127.0.0.1::5432",
            "--security-opt",
            "no-new-privileges:true",
            "--shm-size",
            "256m",
            "--tmpfs",
            "/var/lib/postgresql:rw,nosuid,nodev,noexec,size=1024m",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=64m",
            "--mount",
            f"type=bind,src={roles_file},dst=/docker-entrypoint-initdb.d/00_roles.sql,readonly",
            POSTGRES_IMAGE,
            timeout=90,
        )
        if run.returncode != 0:
            raise SmokeFailure("POSTGRES_START_RED")
        container_id = run.stdout.decode("ascii", "strict").strip()
        if not re.fullmatch(r"[0-9a-f]{64}", container_id):
            raise SmokeFailure("POSTGRES_ID_RED")
        self.container_id = container_id
        inspect = self._docker("inspect", container_id)
        if inspect.returncode != 0:
            raise SmokeFailure("POSTGRES_INSPECT_RED")
        try:
            payload = json.loads(inspect.stdout)[0]
            labels = payload["Config"]["Labels"]
            binding = payload["NetworkSettings"]["Ports"]["5432/tcp"]
            name = str(payload["Name"]).removeprefix("/")
            host_ip = str(binding[0]["HostIp"])
            host_port = int(binding[0]["HostPort"])
        except (IndexError, KeyError, TypeError, ValueError):
            raise SmokeFailure("POSTGRES_INSPECT_RED") from None
        if (
            payload["Id"] != container_id
            or name != self.container_name
            or labels.get("com.anhuan.scope") != SCOPE_LABEL
            or labels.get("com.anhuan.run") != self.run_id
            or host_ip != "127.0.0.1"
            or host_port in {5432, 55432}
            or host_port < 1
            or host_port > 65535
        ):
            raise SmokeFailure("POSTGRES_IDENTITY_RED")
        self.host_port = host_port
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            state = self._docker(
                "inspect", container_id, "--format", "{{.State.Status}}"
            )
            if state.returncode == 0 and state.stdout.strip() == b"exited":
                raise SmokeFailure("POSTGRES_EXITED_RED")
            try:
                with psycopg.connect(
                    host="127.0.0.1",
                    port=self.host_port,
                    dbname=self.database,
                    user="f0d_bootstrap",
                    password=self.passwords["bootstrap"],
                    connect_timeout=2,
                ) as connection:
                    identity = connection.execute(
                        "SELECT current_database(), current_user"
                    ).fetchone()
                if identity == (self.database, "f0d_bootstrap"):
                    return
                METRICS["shared_pg_connections"] = 1
            except psycopg.Error:
                time.sleep(0.5)
        raise SmokeFailure("POSTGRES_READY_RED")

    def f1_environment(self) -> dict[str, str]:
        environment = _clean_environment()
        environment.update(
            {
                "F1_PG_HOST": "127.0.0.1",
                "F1_PG_PORT": str(self.host_port),
                "F1_PG_DATABASE": self.database,
                "F1_SECRETS_DIR": str(self.secrets_dir),
                "F1_KEYCLOAK_REALM": "anhuan",
                "KEYCLOAK_URL": "http://127.0.0.1:31001",
                "F1_KEYCLOAK_ISSUER_URL": "http://127.0.0.1:31001/realms/anhuan",
            }
        )
        return environment

    def bootstrap_kwargs(self) -> dict[str, Any]:
        return {
            "host": "127.0.0.1",
            "port": self.host_port,
            "dbname": self.database,
            "user": "f0d_bootstrap",
            "password": self.passwords["bootstrap"],
        }

    def api_kwargs(self) -> dict[str, Any]:
        return {
            "host": "127.0.0.1",
            "port": self.host_port,
            "dbname": self.database,
            "user": "f1_api",
            "password": self.passwords["f1_api"],
        }

    def migrate(self) -> None:
        root_environment = _clean_environment()
        root_environment["F0D_MIGRATION_DSN"] = (
            "postgresql+psycopg://f0d_migration:"
            f"{self.passwords['migration']}@127.0.0.1:{self.host_port}/{self.database}"
        )
        root_migration = _process(
            [
                sys.executable,
                "-B",
                "-m",
                "alembic",
                "-c",
                "alembic.ini",
                "upgrade",
                "f0d_0006",
            ],
            environment=root_environment,
            timeout=300,
        )
        if root_migration.returncode != 0:
            raise SmokeFailure("ROOT_MIGRATION_RED")
        _secure_write(self.secrets_dir / "f1_api_password", self.passwords["f1_api"])
        _secure_write(
            self.secrets_dir / "f1_worker_password", self.passwords["f1_worker"]
        )
        _secure_write(
            self.secrets_dir / "f1_bootstrap_dsn",
            "postgresql://f0d_bootstrap:"
            f"{self.passwords['bootstrap']}@127.0.0.1:{self.host_port}/{self.database}",
        )
        _secure_write(
            self.secrets_dir / "f1_migration_dsn",
            "postgresql://f0d_migration:"
            f"{self.passwords['migration']}@127.0.0.1:{self.host_port}/{self.database}",
        )
        f1_migration = _process(
            [sys.executable, "-B", "infra/f1/migrate_f1.py"],
            environment=self.f1_environment(),
            timeout=300,
        )
        if f1_migration.returncode != 0:
            raise SmokeFailure("F1_MIGRATION_RED")

    def seed(self) -> dict[str, dict[str, Any]]:
        actors: dict[str, dict[str, Any]] = {}
        enterprise_a = uuid.uuid4()
        enterprise_b = uuid.uuid4()
        definitions = (
            ("admin", enterprise_a, "super_admin"),
            ("enterprise", enterprise_a, "enterprise_admin"),
            ("employee", enterprise_a, "plant_admin"),
            ("consultant", enterprise_a, "auditor"),
            ("partner", enterprise_a, "partner"),
            ("tenant_b", enterprise_b, "enterprise_admin"),
        )
        with psycopg.connect(**self.bootstrap_kwargs()) as connection:
            connection.execute(
                "INSERT INTO f1.enterprise(id,name,license_no,f0i_enterprise_id) "
                "VALUES (%s,%s,%s,NULL),(%s,%s,%s,NULL)",
                (
                    enterprise_a,
                    "SYNTHETIC_A",
                    f"A-{self.run_id[:12]}",
                    enterprise_b,
                    "SYNTHETIC_B",
                    f"B-{self.run_id[:12]}",
                ),
            )
            for name, enterprise_id, role in definitions:
                user_id = uuid.uuid4()
                sub = f"p2-{self.run_id}-{name}"
                connection.execute(
                    "INSERT INTO f1.user_profile(id,keycloak_sub,email) VALUES (%s,%s,%s)",
                    (user_id, sub, f"{name}-{self.run_id}@fixture.invalid"),
                )
                connection.execute(
                    "INSERT INTO f1.enterprise_user(id,enterprise_id,user_id,role) "
                    "VALUES (%s,%s,%s,%s)",
                    (uuid.uuid4(), enterprise_id, user_id, role),
                )
                actors[name] = {
                    "enterprise_id": enterprise_id,
                    "user_id": user_id,
                    "sub": sub,
                    "role": role,
                }
            connection.commit()
        return actors

    def validate_catalog(self) -> None:
        protected_tables = (
            "service_case",
            "service_assignment",
            "site_visit",
            "finding",
            "corrective_action",
            "finding_review",
            "business_timeline",
            "in_app_notification",
            "document_record",
            "document_version",
            "document_preview_unit",
            "crm_account",
            "crm_contact",
            "crm_follow_up",
            "business_report",
            "business_report_version",
            "business_report_artifact",
            "policy_source",
            "policy_version",
            "policy_review_event",
            "policy_impact_candidate",
            "policy_impact_task",
            "quality_suite",
            "quality_scenario",
            "quality_run",
            "quality_result",
            "quality_disagreement",
            "rehearsal_plan",
            "rehearsal_check",
            "rehearsal_run",
            "rehearsal_check_result",
        )
        with psycopg.connect(**self.bootstrap_kwargs()) as connection:
            heads = connection.execute(
                "SELECT "
                "(SELECT string_agg(version_num, ',' ORDER BY version_num) "
                "FROM f0d.alembic_version), "
                "(SELECT string_agg(version_num, ',' ORDER BY version_num) "
                "FROM f1.alembic_version)"
            ).fetchone()
            if heads != ("f0d_0006", "f1_0010"):
                METRICS["migration_head_mismatches"] = 1
                raise SmokeFailure("MIGRATION_HEAD_RED")
            role_rows = connection.execute(
                "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname IN ('f1_api','f1_worker') ORDER BY rolname"
            ).fetchall()
            if len(role_rows) != 2 or any(bool(row[1]) or bool(row[2]) for row in role_rows):
                METRICS["catalog_failures"] = 1
                raise SmokeFailure("RUNTIME_ROLE_RED")
            table_rows = connection.execute(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                "FROM pg_class AS c JOIN pg_namespace AS n ON n.oid=c.relnamespace "
                "WHERE n.nspname='f1' AND c.relname=ANY(%s) ORDER BY c.relname",
                (list(protected_tables),),
            ).fetchall()
            if (
                {str(row[0]) for row in table_rows} != set(protected_tables)
                or any(not bool(row[1]) or not bool(row[2]) for row in table_rows)
            ):
                METRICS["catalog_failures"] = 1
                raise SmokeFailure("P2_RLS_CATALOG_RED")
            public_grants = connection.execute(
                "SELECT count(*) FROM information_schema.table_privileges "
                "WHERE table_schema='f1' AND table_name=ANY(%s) "
                "AND grantee='PUBLIC'",
                (list(protected_tables),),
            ).fetchone()
            if public_grants is None or int(public_grants[0]) != 0:
                METRICS["catalog_failures"] = 1
                raise SmokeFailure("P2_PUBLIC_GRANT_RED")

    def cleanup(self) -> int:
        failures = 0
        if self.endpoint.startswith("unix://"):
            target = self.container_id or self.container_name
            inspect = self._docker("inspect", target)
            if inspect.returncode == 0:
                try:
                    payload = json.loads(inspect.stdout)[0]
                    labels = payload["Config"]["Labels"]
                    valid = (
                        re.fullmatch(r"[0-9a-f]{64}", str(payload["Id"])) is not None
                        and (
                            self.container_id is None
                            or payload["Id"] == self.container_id
                        )
                        and str(payload["Name"]).removeprefix("/")
                        == self.container_name
                        and labels.get("com.anhuan.scope") == SCOPE_LABEL
                        and labels.get("com.anhuan.run") == self.run_id
                    )
                except (IndexError, KeyError, TypeError, ValueError):
                    valid = False
                if not valid:
                    failures += 1
                else:
                    removed = self._docker(
                        "rm", "--force", "--volumes", str(payload["Id"])
                    )
                    if removed.returncode != 0:
                        failures += 1
            elif b"No such object" not in inspect.stderr:
                failures += 1
            residual = self._docker(
                "ps",
                "-a",
                "--filter",
                f"label=com.anhuan.run={self.run_id}",
                "--format",
                "{{.ID}}",
            )
            if residual.returncode != 0 or residual.stdout.strip():
                failures += 1
        try:
            runtime_info = self.runtime_root.lstat()
            runtime_valid = (
                self.runtime_root.parent == Path("/private/tmp")
                and self.runtime_root.name.startswith(self.runtime_prefix)
                and stat.S_ISDIR(runtime_info.st_mode)
                and not stat.S_ISLNK(runtime_info.st_mode)
                and stat.S_IMODE(runtime_info.st_mode) == 0o700
                and runtime_info.st_uid == os.geteuid()
            )
        except OSError:
            runtime_valid = False
        if not runtime_valid:
            failures += 1
        else:
            try:
                shutil.rmtree(self.runtime_root)
            except OSError:
                failures += 1
        if self.runtime_root.exists():
            failures += 1
        return failures


def _json(response: httpx.Response, code: str) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError:
        raise SmokeFailure(code) from None
    if not isinstance(value, dict):
        raise SmokeFailure(code)
    return value


async def _dispose_database_engines() -> None:
    try:
        from platform_foundation.f1 import database
    except ImportError:
        return
    for engine in tuple(database._engines.values()):
        await engine.dispose()  # type: ignore[union-attr]
    database._engines.clear()
    database._factories.clear()


def _probe_notification_insert(
    scratch: ScratchPostgres,
    actors: dict[str, dict[str, Any]],
    case_id: uuid.UUID,
) -> None:
    """Differentiate notification WITH CHECK from ON CONFLICT visibility."""
    enterprise_id = actors["admin"]["enterprise_id"]
    assignment_id = uuid.uuid4()
    timeline_id = uuid.uuid4()
    notification_id = uuid.uuid4()
    with psycopg.connect(**scratch.api_kwargs()) as connection:
        connection.execute(
            "SELECT set_config('f1.enterprise_id', %s, true)",
            (str(enterprise_id),),
        )
        connection.execute(
            "SELECT set_config('f1.sub', %s, true)",
            (actors["admin"]["sub"],),
        )
        connection.execute(
            "INSERT INTO f1.service_assignment ("
            "id,enterprise_id,service_case_id,assignee_user_id,"
            "assigned_by_user_id,capacity,status) "
            "VALUES (%s,%s,%s,%s,%s,'employee','pending')",
            (
                assignment_id,
                enterprise_id,
                case_id,
                actors["employee"]["user_id"],
                actors["admin"]["user_id"],
            ),
        )
        connection.execute(
            "INSERT INTO f1.business_timeline ("
            "id,enterprise_id,service_case_id,event_type,subject_type,"
            "subject_id,status,actor_user_id) "
            "VALUES (%s,%s,%s,'service_assignment.created',"
            "'service_assignment',%s,'pending',%s)",
            (
                timeline_id,
                enterprise_id,
                case_id,
                assignment_id,
                actors["admin"]["user_id"],
            ),
        )
        conditions = connection.execute(
            "SELECT "
            "f1.current_enterprise_id()=%s, f1.session_authorized(%s), "
            "EXISTS (SELECT 1 FROM f1.enterprise_user WHERE enterprise_id=%s "
            "AND user_id=%s), "
            "NOT EXISTS (SELECT 1 FROM f1.user_profile WHERE id=%s "
            "AND keycloak_sub=f1.current_sub()), "
            "EXISTS (SELECT 1 FROM f1.business_timeline WHERE enterprise_id=%s "
            "AND id=%s)",
            (
                enterprise_id,
                enterprise_id,
                enterprise_id,
                actors["employee"]["user_id"],
                actors["employee"]["user_id"],
                enterprise_id,
                timeline_id,
            ),
        ).fetchone()
        condition_code = "".join("1" if value else "0" for value in conditions or ())
        try:
            connection.execute(
                "INSERT INTO f1.in_app_notification ("
                "id,enterprise_id,recipient_user_id,timeline_event_id) "
                "VALUES (%s,%s,%s,%s)",
                (
                    notification_id,
                    enterprise_id,
                    actors["employee"]["user_id"],
                    timeline_id,
                ),
            )
        except psycopg.Error as error:
            sqlstate = error.sqlstate or "UNCLASSIFIED"
            raise SmokeFailure(
                f"WAVE1_NOTIFICATION_PROBE_{condition_code}_{sqlstate}_RED"
            ) from None
        finally:
            connection.rollback()


async def _api_smoke(
    scratch: ScratchPostgres, actors: dict[str, dict[str, Any]]
) -> dict[str, uuid.UUID]:
    environment = scratch.f1_environment()
    for key, value in environment.items():
        os.environ[key] = value

    from fastapi import FastAPI, Header, HTTPException
    from platform_foundation.f1 import auth
    from platform_foundation.f1.api.routers import (
        findings,
        service_cases,
        site_visits,
        workbench,
    )

    app = FastAPI()
    app.include_router(site_visits.router, prefix="/api/v1/service-cases")
    app.include_router(service_cases.router, prefix="/api/v1/service-cases")
    app.include_router(findings.router, prefix="/api/v1/findings")
    app.include_router(workbench.router, prefix="/api/v1/workbench")

    async def synthetic_user(
        x_p2_smoke_actor: str | None = Header(default=None),
    ) -> dict[str, Any]:
        actor = actors.get(x_p2_smoke_actor or "")
        if actor is None:
            raise HTTPException(status_code=401, detail="SMOKE_IDENTITY_REQUIRED")
        return {"sub": actor["sub"], "roles": ()}

    app.dependency_overrides[auth.current_user] = synthetic_user
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)

    async with httpx.AsyncClient(transport=transport, base_url="http://p2.invalid") as client:
        async def request(
            actor_name: str,
            method: str,
            path: str,
            expected: int,
            *,
            enterprise_id: uuid.UUID | None = None,
            body: dict[str, Any] | None = None,
            params: dict[str, Any] | None = None,
            code: str,
        ) -> httpx.Response:
            selected = actors[actor_name]
            try:
                response = await client.request(
                    method,
                    path,
                    headers={
                        "X-P2-Smoke-Actor": actor_name,
                        "X-Enterprise-Id": str(
                            enterprise_id or selected["enterprise_id"]
                        ),
                    },
                    json=body,
                    params=params,
                )
            except Exception as error:
                origin = getattr(error, "orig", None)
                sqlstate = getattr(origin, "sqlstate", None)
                if not isinstance(sqlstate, str) or re.fullmatch(
                    r"[0-9A-Z]{5}", sqlstate
                ) is None:
                    sqlstate = "UNCLASSIFIED"
                diagnostic = getattr(origin, "diag", None)
                table_name = getattr(diagnostic, "table_name", None)
                table_codes = {
                    "service_assignment": "ASSIGNMENT",
                    "audit_log": "AUDIT",
                    "business_timeline": "TIMELINE",
                    "in_app_notification": "NOTIFICATION",
                }
                table_code = table_codes.get(table_name)
                if table_code is None:
                    error_text = str(origin)
                    table_code = next(
                        (
                            fixed_code
                            for fixed_table, fixed_code in table_codes.items()
                            if fixed_table in error_text
                        ),
                        "UNCLASSIFIED",
                    )
                raise SmokeFailure(
                    f"{code}_SQLSTATE_{sqlstate}_TABLE_{table_code}_RED"
                ) from None
            if response.status_code != expected:
                raise SmokeFailure(f"{code}_HTTP_{response.status_code}_RED")
            return response

        enterprise_a = actors["admin"]["enterprise_id"]
        now = datetime.now(timezone.utc).replace(microsecond=0)
        start = now + timedelta(days=1)
        end = now + timedelta(days=2)
        due = now + timedelta(days=3)

        created = await request(
            "admin",
            "POST",
            "/api/v1/service-cases",
            201,
            body={
                "title": "SYNTHETIC_SERVICE",
                "description": "SYNTHETIC_SCOPE",
                "service_type": "onsite",
                "planned_start_at": start.isoformat(),
                "planned_end_at": end.isoformat(),
            },
            code="WAVE1_CREATE_RED",
        )
        case_id = uuid.UUID(str(_json(created, "WAVE1_CREATE_RED").get("id")))
        _probe_notification_insert(scratch, actors, case_id)

        async def assign(name: str, capacity: str) -> uuid.UUID:
            response = await request(
                "admin",
                "POST",
                f"/api/v1/service-cases/{case_id}/assignments",
                201,
                body={
                    "assignee_user_id": str(actors[name]["user_id"]),
                    "capacity": capacity,
                },
                code="WAVE1_ASSIGN_RED",
            )
            return uuid.UUID(str(_json(response, "WAVE1_ASSIGN_RED").get("id")))

        employee_assignment = await assign("employee", "employee")
        unread = await request(
            "employee",
            "GET",
            "/api/v1/workbench/notifications/unread-count",
            200,
            code="WAVE4_UNREAD_RED",
        )
        if _json(unread, "WAVE4_UNREAD_RED").get("unread_count") != 1:
            METRICS["notification_failures"] = 1
            raise SmokeFailure("WAVE4_UNREAD_RED")
        notifications = await request(
            "employee",
            "GET",
            "/api/v1/workbench/notifications",
            200,
            code="WAVE4_NOTIFICATION_RED",
        )
        items = _json(notifications, "WAVE4_NOTIFICATION_RED").get("items")
        if not isinstance(items, list) or len(items) != 1:
            METRICS["notification_failures"] = 1
            raise SmokeFailure("WAVE4_NOTIFICATION_RED")
        notification_id = uuid.UUID(str(items[0].get("id")))
        await request(
            "employee",
            "POST",
            f"/api/v1/workbench/notifications/{notification_id}/read",
            200,
            code="WAVE4_MARK_READ_RED",
        )
        unread_after = await request(
            "employee",
            "GET",
            "/api/v1/workbench/notifications/unread-count",
            200,
            code="WAVE4_UNREAD_AFTER_RED",
        )
        if _json(unread_after, "WAVE4_UNREAD_AFTER_RED").get("unread_count") != 0:
            METRICS["notification_failures"] = 1
            raise SmokeFailure("WAVE4_UNREAD_AFTER_RED")
        await request(
            "employee",
            "POST",
            f"/api/v1/service-cases/{case_id}/assignments/{employee_assignment}/accept",
            200,
            code="WAVE1_ACCEPT_RED",
        )
        first_partner = await assign("partner", "partner")
        await request(
            "partner",
            "POST",
            f"/api/v1/service-cases/{case_id}/assignments/{first_partner}/reject",
            200,
            code="WAVE1_REJECT_RED",
        )
        second_partner = await assign("partner", "partner")
        await request(
            "admin",
            "POST",
            f"/api/v1/service-cases/{case_id}/assignments/{second_partner}/revoke",
            200,
            code="WAVE1_REVOKE_RED",
        )
        consultant_assignment = await assign("consultant", "consultant")
        await request(
            "consultant",
            "POST",
            f"/api/v1/service-cases/{case_id}/assignments/{consultant_assignment}/accept",
            200,
            code="WAVE1_CONSULTANT_RED",
        )
        mine = await request(
            "employee",
            "GET",
            "/api/v1/service-cases/mine",
            200,
            code="WAVE1_MINE_RED",
        )
        mine_items = _json(mine, "WAVE1_MINE_RED").get("items")
        if not isinstance(mine_items, list) or case_id not in {
            uuid.UUID(str(item.get("id"))) for item in mine_items
        }:
            raise SmokeFailure("WAVE1_MINE_RED")

        visit_response = await request(
            "admin",
            "POST",
            f"/api/v1/service-cases/{case_id}/site-visits",
            201,
            body={
                "planned_start_at": start.isoformat(),
                "planned_end_at": end.isoformat(),
            },
            code="WAVE3_VISIT_CREATE_RED",
        )
        visit_id = uuid.UUID(str(_json(visit_response, "WAVE3_VISIT_CREATE_RED").get("id")))
        await request(
            "employee",
            "POST",
            f"/api/v1/service-cases/{case_id}/site-visits/{visit_id}/start",
            200,
            code="WAVE3_VISIT_START_RED",
        )
        finding_response = await request(
            "employee",
            "POST",
            "/api/v1/findings",
            201,
            body={
                "service_case_id": str(case_id),
                "title": "SYNTHETIC_FINDING",
                "description": "SYNTHETIC_RECTIFICATION",
                "severity": "high",
                "responsible_user_id": str(actors["enterprise"]["user_id"]),
                "due_at": due.isoformat(),
            },
            code="WAVE2_FINDING_CREATE_RED",
        )
        finding_id = uuid.UUID(str(_json(finding_response, "WAVE2_FINDING_CREATE_RED").get("id")))
        await request(
            "employee",
            "POST",
            f"/api/v1/service-cases/{case_id}/site-visits/{visit_id}/complete",
            200,
            code="WAVE3_VISIT_COMPLETE_RED",
        )

        async def rectify(description: str) -> None:
            await request(
                "enterprise",
                "POST",
                f"/api/v1/findings/{finding_id}/start-rectification",
                200,
                code="WAVE2_RECTIFY_RED",
            )
            await request(
                "enterprise",
                "POST",
                f"/api/v1/findings/{finding_id}/corrective-actions",
                201,
                body={"description": description},
                code="WAVE2_CORRECTION_RED",
            )
            await request(
                "consultant",
                "POST",
                f"/api/v1/findings/{finding_id}/start-review",
                200,
                code="WAVE2_START_REVIEW_RED",
            )

        await rectify("SYNTHETIC_CORRECTION_ONE")
        await request(
            "consultant",
            "POST",
            f"/api/v1/findings/{finding_id}/reviews",
            201,
            body={"decision": "rejected", "comment": "SYNTHETIC_REWORK"},
            code="WAVE2_REJECT_REVIEW_RED",
        )
        await rectify("SYNTHETIC_CORRECTION_TWO")
        await request(
            "consultant",
            "POST",
            f"/api/v1/findings/{finding_id}/reviews",
            201,
            body={"decision": "passed", "comment": ""},
            code="WAVE2_PASS_REVIEW_RED",
        )
        await request(
            "admin",
            "POST",
            f"/api/v1/findings/{finding_id}/close",
            200,
            code="WAVE2_CLOSE_FINDING_RED",
        )
        await request(
            "admin",
            "POST",
            f"/api/v1/service-cases/{case_id}/close",
            200,
            code="WAVE3_CLOSE_CASE_RED",
        )
        detail = await request(
            "admin",
            "GET",
            f"/api/v1/service-cases/{case_id}",
            200,
            code="WAVE3_DETAIL_RED",
        )
        detail_payload = _json(detail, "WAVE3_DETAIL_RED")
        if (
            detail_payload.get("status") != "closed"
            or len(detail_payload.get("assignments", ())) != 4
            or len(detail_payload.get("site_visits", ())) != 1
            or len(detail_payload.get("findings", ())) != 1
            or len(detail_payload.get("timeline", ())) < 15
        ):
            raise SmokeFailure("WAVE3_DETAIL_RED")

        views = {}
        for actor_name, expected_view in (
            ("admin", "admin"),
            ("enterprise", "enterprise"),
            ("employee", "executor"),
        ):
            response = await request(
                actor_name,
                "GET",
                "/api/v1/workbench/overview",
                200,
                code="WAVE4_VIEW_RED",
            )
            views[actor_name] = _json(response, "WAVE4_VIEW_RED").get("view")
            if views[actor_name] != expected_view:
                METRICS["view_failures"] = 1
                raise SmokeFailure("WAVE4_VIEW_RED")
        calendar = await request(
            "admin",
            "GET",
            "/api/v1/workbench/calendar",
            200,
            params={
                "start_at": now.isoformat(),
                "end_at": (now + timedelta(days=4)).isoformat(),
            },
            code="WAVE4_CALENDAR_RED",
        )
        calendar_items = _json(calendar, "WAVE4_CALENDAR_RED").get("items")
        kinds = {
            str(item.get("item_type"))
            for item in calendar_items
        } if isinstance(calendar_items, list) else set()
        if kinds != {"case", "visit", "finding_deadline"}:
            METRICS["calendar_kind_failures"] = 1
            raise SmokeFailure("WAVE4_CALENDAR_RED")

        cross = await request(
            "tenant_b",
            "GET",
            f"/api/v1/service-cases/{case_id}",
            404,
            code="CROSS_TENANT_DETAIL_RED",
        )
        _ = cross
        b_list = await request(
            "tenant_b",
            "GET",
            "/api/v1/service-cases",
            200,
            code="CROSS_TENANT_LIST_RED",
        )
        if _json(b_list, "CROSS_TENANT_LIST_RED").get("items") != []:
            METRICS["cross_tenant_api_leaks"] = 1
            raise SmokeFailure("CROSS_TENANT_LIST_RED")
        await request(
            "admin",
            "GET",
            f"/api/v1/service-cases/{case_id}",
            404,
            enterprise_id=actors["tenant_b"]["enterprise_id"],
            code="CROSS_TENANT_MEMBERSHIP_RED",
        )

    return {
        "case_id": case_id,
        "finding_id": finding_id,
        "enterprise_a": enterprise_a,
    }


def _direct_rls_and_evidence(
    scratch: ScratchPostgres,
    actors: dict[str, dict[str, Any]],
    identifiers: dict[str, uuid.UUID],
) -> None:
    with psycopg.connect(**scratch.api_kwargs()) as connection:
        connection.execute(
            "SELECT set_config('f1.enterprise_id', %s, true)",
            (str(actors["tenant_b"]["enterprise_id"]),),
        )
        connection.execute(
            "SELECT set_config('f1.sub', %s, true)",
            (actors["tenant_b"]["sub"],),
        )
        visible = connection.execute(
            "SELECT count(*) FROM f1.service_case WHERE id=%s",
            (identifiers["case_id"],),
        ).fetchone()
        if visible is None or int(visible[0]) != 0:
            METRICS["rls_select_leaks"] = 1
            raise SmokeFailure("RLS_SELECT_RED")
        changed = connection.execute(
            "UPDATE f1.service_case SET title=title WHERE id=%s",
            (identifiers["case_id"],),
        )
        if changed.rowcount != 0:
            METRICS["rls_write_leaks"] = 1
            raise SmokeFailure("RLS_WRITE_RED")
        connection.rollback()

    required_timeline = {
        "service_case.created",
        "service_assignment.created",
        "service_assignment.accept",
        "service_assignment.reject",
        "service_assignment.revoke",
        "site_visit.planned",
        "site_visit.started",
        "site_visit.completed",
        "finding.created",
        "finding.start_rectification",
        "corrective_action.submitted",
        "finding.start_review",
        "finding.review_reject",
        "finding.review_pass",
        "finding.close",
        "service_case.auto_completed",
        "service_case.closed",
    }
    required_audit = {
        "service_case.create",
        "service_assignment.create",
        "service_assignment.accept",
        "service_assignment.reject",
        "service_assignment.revoke",
        "site_visit.create",
        "site_visit.start",
        "site_visit.complete",
        "finding.create",
        "finding.start_rectification",
        "finding.submit_correction",
        "finding.start_review",
        "finding.review_reject",
        "finding.review_pass",
        "finding.close",
        "service_case.auto_complete",
        "service_case.close",
    }
    with psycopg.connect(**scratch.bootstrap_kwargs()) as connection:
        timeline = {
            str(row[0])
            for row in connection.execute(
                "SELECT event_type FROM f1.business_timeline WHERE service_case_id=%s",
                (identifiers["case_id"],),
            ).fetchall()
        }
        if not required_timeline.issubset(timeline):
            METRICS["timeline_gaps"] = len(required_timeline - timeline)
            raise SmokeFailure("TIMELINE_GAP_RED")
        audit = {
            str(row[0])
            for row in connection.execute(
                "SELECT action FROM f1.audit_log WHERE enterprise_id=%s",
                (identifiers["enterprise_a"],),
            ).fetchall()
        }
        if not required_audit.issubset(audit):
            METRICS["audit_gaps"] = len(required_audit - audit)
            raise SmokeFailure("AUDIT_GAP_RED")
        bad_notifications = connection.execute(
            "SELECT count(*) FROM f1.in_app_notification AS notification "
            "LEFT JOIN f1.enterprise_user AS recipient "
            "ON recipient.enterprise_id=notification.enterprise_id "
            "AND recipient.user_id=notification.recipient_user_id "
            "LEFT JOIN f1.business_timeline AS timeline "
            "ON timeline.enterprise_id=notification.enterprise_id "
            "AND timeline.id=notification.timeline_event_id "
            "WHERE notification.enterprise_id=%s "
            "AND (recipient.id IS NULL OR timeline.id IS NULL)",
            (identifiers["enterprise_a"],),
        ).fetchone()
        if bad_notifications is None or int(bad_notifications[0]) != 0:
            METRICS["notification_failures"] = 1
            raise SmokeFailure("NOTIFICATION_LINK_RED")


def _metric_for(code: str) -> str:
    if code.startswith("ROOT_MIGRATION") or code.startswith("F1_MIGRATION") or code.startswith("MIGRATION"):
        return "migration_head_mismatches"
    if code.startswith("WAVE1"):
        return "wave1_failures"
    if code.startswith("WAVE2"):
        return "wave2_failures"
    if code.startswith("WAVE3"):
        return "wave3_failures"
    if code.startswith("WAVE4"):
        return "wave4_failures"
    if code.startswith("CROSS_TENANT"):
        return "cross_tenant_api_leaks"
    if code.startswith("RLS_SELECT"):
        return "rls_select_leaks"
    if code.startswith("RLS_WRITE"):
        return "rls_write_leaks"
    if code.startswith("TIMELINE"):
        return "timeline_gaps"
    if code.startswith("AUDIT"):
        return "audit_gaps"
    if code.startswith("NOTIFICATION"):
        return "notification_failures"
    if "CATALOG" in code or "ROLE" in code or "GRANT" in code:
        return "catalog_failures"
    return "unexpected_failures"


def _render(status: str, reason: str | None = None) -> None:
    print(status)
    if reason is not None:
        print(f"reason={reason}")
    for name, value in METRICS.items():
        print(f"{name}={value}")


def main() -> int:
    scratch: ScratchPostgres | None = None
    primary_reason: str | None = None
    success = False
    try:
        scratch = ScratchPostgres()
        scratch.start()
        scratch.migrate()
        scratch.validate_catalog()
        actors = scratch.seed()
        identifiers = asyncio.run(_api_smoke(scratch, actors))
        _direct_rls_and_evidence(scratch, actors, identifiers)
        success = True
    except SmokeFailure as error:
        primary_reason = error.code
        metric = _metric_for(error.code)
        if METRICS[metric] == 0:
            METRICS[metric] = 1
    except Exception:
        primary_reason = "UNEXPECTED_RED"
        METRICS["unexpected_failures"] = 1
    finally:
        if scratch is not None:
            try:
                asyncio.run(_dispose_database_engines())
            except Exception:
                METRICS["cleanup_residuals"] += 1
            METRICS["cleanup_residuals"] += scratch.cleanup()

    if success and all(value == 0 for value in METRICS.values()):
        _render("P2_REAL_PG_API_RLS_SMOKE_PASSED_NOT_RELEASE_VERIFIED")
        return 0
    _render(
        "P2_REAL_PG_API_RLS_SMOKE_REJECTED",
        primary_reason or "CLEANUP_RED",
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
