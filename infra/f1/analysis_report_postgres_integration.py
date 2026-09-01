"""Dedicated PostgreSQL harness for analysis-report authorization tests.

Closed compose set: secret-init + postgres. Host migrator and unittest stay
outside the container. Fake generator is the only allowed fake.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import psycopg


ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "infra/f1/docker-compose.analysis-report-postgres-integration.yml"
DOCKER = Path("/Applications/Docker.app/Contents/Resources/bin/docker")
SCOPE = "analysis-report-postgres-integration"
PYTHON = sys.executable
FIXTURE_NS = uuid.UUID("3e7c1d0a-8b24-4f11-9d56-21a9c4e0b7f2")
PARSER_VERSION = "pgint1"
ENTERPRISE_C = uuid.UUID("20000000-0000-4000-8000-00000000000c")
DUAL_SUB = "c0ffee00-1111-4111-8111-00000000dual"
CLIENT_SUB = "c1a11e00-2222-4222-8222-0000000client"
STRANGER_SUB = "57a11e00-3333-4333-8333-000000stranger"


def _failure_token(blob: bytes, table: tuple[tuple[str, str], ...]) -> str:
    text = blob.decode("ascii", "replace")
    lowered = text.lower()
    for needle, token in table:
        if needle.lower() in lowered:
            return token
    return "OTHER"


class HarnessError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass
class IntegrationWorld:
    enterprise_a: uuid.UUID
    enterprise_b: uuid.UUID
    enterprise_c: uuid.UUID
    bound_client_id: uuid.UUID
    unbound_client_id: uuid.UUID
    race_client_id: uuid.UUID
    foreign_client_id: uuid.UUID
    provider_a: object
    provider_a_on_b: object
    client_b: object
    stranger_c: object
    provider_b: object
    actor_a: uuid.UUID
    dual_sub: str
    client_sub: str
    stranger_sub: str
    provider_scope_id: uuid.UUID


def _write_secret(path: Path, value: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
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
        raise HarnessError("SECRET_FILE_INVALID")


def _docker_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        "HOME": os.environ.get("HOME", ""),
        "PATH": os.environ.get("PATH", ""),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    docker_host = os.environ.get("DOCKER_HOST", "").strip()
    if docker_host:
        env["DOCKER_HOST"] = docker_host
    docker_bin = str(DOCKER.parent)
    if docker_bin not in env["PATH"].split(":"):
        env["PATH"] = docker_bin + (":" + env["PATH"] if env["PATH"] else "")
    if extra:
        env.update(extra)
    return {key: value for key, value in env.items() if value}


def _run(
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
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HarnessError("PROCESS_FAILED") from exc


def canonical_shared_fingerprint(docker: Path = DOCKER) -> bytes:
    def capture(args: list[str]) -> list[str]:
        raw = subprocess.check_output([str(docker), *args], text=True).strip()
        return [line for line in raw.splitlines() if line]

    containers = []
    for cid in capture(
        [
            "ps",
            "-a",
            "--filter",
            "label=com.docker.compose.project=anhuan-f1",
            "--format",
            "{{.ID}}",
        ]
    ):
        payload = json.loads(
            subprocess.check_output([str(docker), "inspect", cid], text=True)
        )[0]
        containers.append(
            {
                "name": str(payload.get("Name", "")).removeprefix("/"),
                "id": payload["Id"],
                "image": payload["Config"]["Image"],
                "status": payload["State"]["Status"],
                "running": payload["State"]["Running"],
                "restart_policy": payload["HostConfig"]["RestartPolicy"]["Name"],
                "network_mode": payload["HostConfig"]["NetworkMode"],
            }
        )
    containers.sort(key=lambda item: item["name"])
    volumes = []
    for name in capture(
        [
            "volume",
            "ls",
            "--filter",
            "label=com.docker.compose.project=anhuan-f1",
            "--format",
            "{{.Name}}",
        ]
    ):
        payload = json.loads(
            subprocess.check_output(
                [str(docker), "volume", "inspect", name], text=True
            )
        )[0]
        volumes.append(
            {
                "name": payload["Name"],
                "driver": payload["Driver"],
                "labels": payload.get("Labels") or {},
            }
        )
    volumes.sort(key=lambda item: item["name"])
    networks = []
    for nid in capture(
        [
            "network",
            "ls",
            "--filter",
            "label=com.docker.compose.project=anhuan-f1",
            "--format",
            "{{.ID}}",
        ]
    ):
        payload = json.loads(
            subprocess.check_output(
                [str(docker), "network", "inspect", nid], text=True
            )
        )[0]
        networks.append(
            {
                "id": payload["Id"],
                "name": payload["Name"],
                "driver": payload["Driver"],
                "internal": payload.get("Internal"),
                "labels": payload.get("Labels") or {},
            }
        )
    networks.sort(key=lambda item: item["name"])
    return json.dumps(
        {"containers": containers, "volumes": volumes, "networks": networks},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def dedicated_counts() -> tuple[int, int, int]:
    def count(args: list[str]) -> int:
        raw = subprocess.check_output([str(DOCKER), *args], text=True).strip()
        return len([line for line in raw.splitlines() if line])

    containers = count(
        [
            "ps",
            "-a",
            "--filter",
            f"label=io.anhuan.scope={SCOPE}",
            "--format",
            "{{.ID}}",
        ]
    )
    volumes = count(
        [
            "volume",
            "ls",
            "--filter",
            f"label=io.anhuan.scope={SCOPE}",
            "--format",
            "{{.Name}}",
        ]
    )
    networks = count(
        [
            "network",
            "ls",
            "--filter",
            f"label=io.anhuan.scope={SCOPE}",
            "--format",
            "{{.ID}}",
        ]
    )
    return containers, volumes, networks


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    if port in {5432, 55432}:
        return _free_port()
    return port


def _stable(label: str, *parts: object) -> uuid.UUID:
    return uuid.uuid5(FIXTURE_NS, ":".join((label, *(str(part) for part in parts))))


class PostgresIntegrationStack:
    def __init__(self) -> None:
        self.run_id = uuid.uuid4().hex
        self.project_id = uuid.uuid4().hex
        self.project_name = f"anhuan-ar-pgint-{self.run_id[:12]}"
        self.database = f"f1_arpg_{self.run_id[:12]}"
        self.host_port = _free_port()
        self.control_dir = Path(f"/private/tmp/anhuan-ar-pgint-{self.run_id[:12]}")
        self.passwords = {
            "bootstrap": secrets.token_hex(24),
            "migration": secrets.token_hex(24),
            "runtime": secrets.token_hex(24),
            "worker": secrets.token_hex(24),
            "f1_api": secrets.token_hex(24),
            "f1_worker": secrets.token_hex(24),
        }
        self.secrets_dir: Path | None = None
        self.before_fingerprint = b""
        self.started = False
        self.cleanup_status = "NOT_STARTED"
        self.shared_match = 0
        self.dedicated_after = (-1, -1, -1)

    def non_sensitive_identity_env(self) -> dict[str, str]:
        return {
            "LOCAL_ANALYSIS_REPORT_PGINT_PROJECT_ID": self.project_id,
            "LOCAL_ANALYSIS_REPORT_PGINT_PROJECT_NAME": self.project_name,
            "LOCAL_ANALYSIS_REPORT_PGINT_DATABASE": self.database,
            "LOCAL_ANALYSIS_REPORT_PGINT_CONTROL_DIR": str(self.control_dir),
            "F1_PG_HOST": "127.0.0.1",
            "F1_PG_DATABASE": self.database,
            "F1_MATERIAL_ANALYSIS_REPORT_LOCAL": "1",
            "F1_LOCAL_ENGINEERING": "1",
        }

    def _compose_docker_env(self) -> dict[str, str]:
        if self.secrets_dir is None:
            raise HarnessError("SECRETS_DIR_MISSING")
        return _docker_env(
            {
                "LOCAL_ANALYSIS_REPORT_PGINT_PROJECT_ID": self.project_id,
                "LOCAL_ANALYSIS_REPORT_PGINT_DATABASE": self.database,
                "LOCAL_ANALYSIS_REPORT_PGINT_SECRETS_DIR": str(self.secrets_dir),
                "LOCAL_ANALYSIS_REPORT_PGINT_HOST_PORT": str(self.host_port),
            }
        )

    def runtime_env(self) -> dict[str, str]:
        if self.secrets_dir is None:
            raise HarnessError("SECRETS_DIR_MISSING")
        env = os.environ.copy()
        env.update(self.non_sensitive_identity_env())
        env.update(
            {
                "F1_PG_PORT": str(self.host_port),
                "F1_SECRETS_DIR": str(self.secrets_dir),
                "F1_KEYCLOAK_REALM": "anhuan",
                "KEYCLOAK_URL": "http://material-rag.invalid",
                "F1_KEYCLOAK_ISSUER_URL": "http://material-rag.invalid/realms/anhuan",
                "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT),
                "F1_PROVIDER_SECRETS_DIR": str(self.secrets_dir),
            }
        )
        return env

    def apply_env(self) -> None:
        os.environ.update(self.non_sensitive_identity_env())
        os.environ.update(
            {
                "F1_PG_PORT": str(self.host_port),
                "F1_SECRETS_DIR": str(self.secrets_dir or ""),
                "F1_PROVIDER_SECRETS_DIR": str(self.secrets_dir or ""),
                "F1_KEYCLOAK_REALM": "anhuan",
                "KEYCLOAK_URL": "http://material-rag.invalid",
                "F1_KEYCLOAK_ISSUER_URL": "http://material-rag.invalid/realms/anhuan",
            }
        )

    def start(self) -> None:
        if dedicated_counts() != (0, 0, 0):
            raise HarnessError("DEDICATED_PREEXISTING")
        self.before_fingerprint = canonical_shared_fingerprint()
        self.control_dir.mkdir(mode=0o700)
        info = self.control_dir.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise HarnessError("CONTROL_DIR_INVALID")
        home = self.control_dir / "home"
        tmp = self.control_dir / "tmp"
        self.secrets_dir = self.control_dir / "secrets"
        for directory in (home, tmp, self.secrets_dir):
            directory.mkdir(mode=0o700)
        _write_secret(
            self.control_dir / "identity.receipt",
            "\n".join(
                (
                    f"LOCAL_ANALYSIS_REPORT_PGINT_PROJECT_ID={self.project_id}",
                    f"LOCAL_ANALYSIS_REPORT_PGINT_PROJECT_NAME={self.project_name}",
                    f"LOCAL_ANALYSIS_REPORT_PGINT_DATABASE={self.database}",
                )
            )
            + "\n",
        )
        _write_secret(
            self.secrets_dir / "f0d_bootstrap_password", self.passwords["bootstrap"]
        )
        _write_secret(
            self.secrets_dir / "f0d_migration_password", self.passwords["migration"]
        )
        _write_secret(
            self.secrets_dir / "f0d_runtime_password", self.passwords["runtime"]
        )
        _write_secret(
            self.secrets_dir / "f0d_worker_password", self.passwords["worker"]
        )
        _write_secret(self.secrets_dir / "f1_api_password", self.passwords["f1_api"])
        _write_secret(
            self.secrets_dir / "f1_worker_password", self.passwords["f1_worker"]
        )
        _write_secret(self.secrets_dir / "f1_material_rag_key", secrets.token_hex(32))
        compose_env = self.control_dir / "compose.env"
        _write_secret(
            compose_env,
            "\n".join(
                (
                    f"LOCAL_ANALYSIS_REPORT_PGINT_PROJECT_ID={self.project_id}",
                    f"LOCAL_ANALYSIS_REPORT_PGINT_DATABASE={self.database}",
                    f"LOCAL_ANALYSIS_REPORT_PGINT_SECRETS_DIR={self.secrets_dir}",
                    f"LOCAL_ANALYSIS_REPORT_PGINT_HOST_PORT={self.host_port}",
                )
            )
            + "\n",
        )
        docker_env = self._compose_docker_env()
        up = _run(
            [
                str(DOCKER),
                "compose",
                "--progress",
                "quiet",
                "--project-name",
                self.project_name,
                "--env-file",
                str(compose_env),
                "-f",
                str(COMPOSE_FILE),
                "up",
                "-d",
                "--wait",
                "--wait-timeout",
                "90",
            ],
            environment=docker_env,
            timeout=120,
        )
        if up.returncode != 0:
            token = _failure_token(
                up.stderr,
                (
                    ("unknown command: docker compose", "COMPOSE_PLUGIN_MISSING"),
                    ("unknown flag", "COMPOSE_FLAG_INVALID"),
                    ("no such image", "IMAGE_MISSING"),
                    ("didn't complete successfully", "SECRET_INIT_FAILED"),
                    ("can't stat", "SECRET_SOURCE_MISSING"),
                    ("Cannot connect to the Docker daemon", "DOCKER_DAEMON"),
                ),
            )
            self.stop()
            raise HarnessError(f"COMPOSE_UP_FAILED:{token}")
        self.started = True
        self._wait_ready()
        self._write_dsns()
        self.apply_env()
        self._migrate()
        self._seed_identities()

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
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
            except psycopg.Error:
                time.sleep(0.5)
        self.stop()
        raise HarnessError("POSTGRES_READY_FAILED")

    def _write_dsns(self) -> None:
        assert self.secrets_dir is not None
        host = "127.0.0.1"
        port = self.host_port
        database = self.database

        def dsn(user: str, password: str) -> str:
            return (
                f"postgresql://{user}:{quote(password, safe='')}"
                f"@{host}:{port}/{database}"
            )

        _write_secret(
            self.secrets_dir / "f0d_migration_dsn",
            dsn("f0d_migration", self.passwords["migration"]),
        )
        _write_secret(
            self.secrets_dir / "f1_bootstrap_dsn",
            dsn("f0d_bootstrap", self.passwords["bootstrap"]),
        )
        _write_secret(
            self.secrets_dir / "f1_migration_dsn",
            dsn("f0d_migration", self.passwords["migration"]),
        )

    def _migrate(self) -> None:
        migrated = _run(
            [PYTHON, "-B", "infra/f1/analysis-reports/migrate.py"],
            environment=self.runtime_env(),
            timeout=300,
        )
        if (
            migrated.returncode != 0
            or migrated.stdout.decode("ascii", "replace").strip()
            != "LOCAL_ANALYSIS_REPORT_MIGRATE_OK"
        ):
            token = _failure_token(
                migrated.stderr + migrated.stdout,
                (
                    ("LOCAL_ANALYSIS_REPORT_MIGRATION_HEAD_MISMATCH", "HEAD_MISMATCH"),
                    ("LOCAL_ANALYSIS_REPORT_RLS_CATALOG_MISMATCH", "RLS_CATALOG"),
                    ("F1_BOOTSTRAP_CONNECTION_IDENTITY_MISMATCH", "BOOTSTRAP_IDENTITY"),
                    ("F0D_MIGRATION_DSN_IDENTITY_MISMATCH", "F0D_DSN"),
                    ("F1_MIGRATION_DSN_IDENTITY_MISMATCH", "F1_DSN"),
                    ("F1_SECRET_PERMISSIONS_INVALID", "SECRET_PERMISSIONS"),
                    ("F1_SECRETS_DIR_INVALID", "SECRETS_DIR"),
                ),
            )
            self.stop()
            raise HarnessError(f"MIGRATE_FAILED:{token}:{migrated.stderr[:400]!r}")

    def _seed_identities(self) -> None:
        from infra.f1 import local_seed
        from infra.f1.migrate_f1 import _bootstrap_dsn

        with psycopg.connect(_bootstrap_dsn(), autocommit=False) as connection:
            head = connection.execute(
                "SELECT string_agg(version_num, ',' ORDER BY version_num) "
                "FROM f1.alembic_version"
            ).fetchone()
            if head is None or head[0] != "f1_0024":
                self.stop()
                raise HarnessError("SEED_HEAD_MISMATCH")
            local_seed._ensure_enterprise(
                connection, local_seed.ENTERPRISE_A, "Local Enterprise A", "LOCAL-A"
            )
            local_seed._ensure_enterprise(
                connection, local_seed.ENTERPRISE_B, "Local Enterprise B", "LOCAL-B"
            )
            local_seed._ensure_enterprise(
                connection, ENTERPRISE_C, "Local Enterprise C", "LOCAL-C"
            )
            for binding in local_seed.BINDINGS:
                local_seed._ensure_binding(connection, binding)
            local_seed._ensure_binding(
                connection,
                local_seed.Binding(
                    "dual-a", DUAL_SUB, "dual@fixture.invalid",
                    local_seed.ENTERPRISE_A, "super_admin",
                ),
            )
            local_seed._ensure_binding(
                connection,
                local_seed.Binding(
                    "dual-b", DUAL_SUB, "dual@fixture.invalid",
                    local_seed.ENTERPRISE_B, "partner",
                ),
            )
            local_seed._ensure_binding(
                connection,
                local_seed.Binding(
                    "client-b", CLIENT_SUB, "client@fixture.invalid",
                    local_seed.ENTERPRISE_B, "partner",
                ),
            )
            local_seed._ensure_binding(
                connection,
                local_seed.Binding(
                    "stranger-c", STRANGER_SUB, "stranger@fixture.invalid",
                    ENTERPRISE_C, "partner",
                ),
            )
            local_seed._ensure_durability_canary(connection)
            connection.commit()

    def _bootstrap(self) -> psycopg.Connection:
        return psycopg.connect(
            host="127.0.0.1",
            port=self.host_port,
            dbname=self.database,
            user="f0d_bootstrap",
            password=self.passwords["bootstrap"],
        )

    def seed_world(self) -> IntegrationWorld:
        from infra.f1 import local_seed
        from platform_foundation.f1.auth import Tenant

        actor_a = local_seed._stable_id("profile", DUAL_SUB)
        actor_b = local_seed._stable_id("profile", local_seed.BINDINGS[-1].sub)
        enterprise_a = local_seed.ENTERPRISE_A
        enterprise_b = local_seed.ENTERPRISE_B
        bound_client_id = _stable("client", "bound")
        unbound_client_id = _stable("client", "unbound")
        race_client_id = _stable("client", "race")
        foreign_client_id = _stable("client", "foreign")
        provider_scope = _stable("scope", "provider-a")
        with self._bootstrap() as connection:
            connection.execute(
                "SELECT set_config('session_replication_role','replica',false)"
            )
            connection.execute(
                "INSERT INTO f1.crm_account "
                "(id,enterprise_id,display_name,stage,created_by_user_id) VALUES "
                "(%s,%s,'Bound Client','active',%s),"
                "(%s,%s,'Unbound Client','active',%s),"
                "(%s,%s,'Race Client','active',%s),"
                "(%s,%s,'Foreign Client','active',%s)",
                (
                    bound_client_id, enterprise_a, actor_a,
                    unbound_client_id, enterprise_a, actor_a,
                    race_client_id, enterprise_a, actor_a,
                    foreign_client_id, enterprise_b, actor_b,
                ),
            )
            connection.execute(
                "INSERT INTO f1.analysis_report_client_audience "
                "(id,enterprise_id,client_account_id,audience_enterprise_id,status) "
                "VALUES (%s,%s,%s,%s,'active'),(%s,%s,%s,%s,'active')",
                (
                    _stable("binding", "bound"), enterprise_a, bound_client_id, enterprise_b,
                    _stable("binding", "race"), enterprise_a, race_client_id, ENTERPRISE_C,
                ),
            )
            connection.execute(
                "INSERT INTO f1.material_knowledge_scope "
                "(id,enterprise_id,scope_kind,client_account_id) VALUES "
                "(%s,%s,'service_provider',NULL),(%s,%s,'client',%s),(%s,%s,'client',%s)",
                (
                    provider_scope, enterprise_a,
                    _stable("scope", "bound"), enterprise_a, bound_client_id,
                    _stable("scope", "race"), enterprise_a, race_client_id,
                ),
            )
            for label, scope_id, kind in (
                ("provider", provider_scope, "service_provider"),
                ("bound", _stable("scope", "bound"), "client"),
                ("race", _stable("scope", "race"), "client"),
            ):
                _insert_unit(
                    connection,
                    label=label,
                    enterprise_id=enterprise_a,
                    scope_id=scope_id,
                    actor_id=actor_a,
                    scope_kind=kind,
                )
            connection.commit()
        return IntegrationWorld(
            enterprise_a=enterprise_a,
            enterprise_b=enterprise_b,
            enterprise_c=ENTERPRISE_C,
            bound_client_id=bound_client_id,
            unbound_client_id=unbound_client_id,
            race_client_id=race_client_id,
            foreign_client_id=foreign_client_id,
            provider_a=Tenant(
                enterprise_id=enterprise_a,
                sub=DUAL_SUB,
                roles=("super_admin",),
                role="super_admin",
            ),
            provider_a_on_b=Tenant(
                enterprise_id=enterprise_b,
                sub=DUAL_SUB,
                roles=("super_admin",),
                role="partner",
            ),
            client_b=Tenant(
                enterprise_id=enterprise_b,
                sub=CLIENT_SUB,
                roles=(),
                role="partner",
            ),
            stranger_c=Tenant(
                enterprise_id=ENTERPRISE_C,
                sub=STRANGER_SUB,
                roles=(),
                role="partner",
            ),
            provider_b=Tenant(
                enterprise_id=enterprise_b,
                sub="ddc4e27e-ccde-4c89-958f-798fc8f30175",
                roles=("enterprise_admin",),
                role="enterprise_admin",
            ),
            actor_a=actor_a,
            dual_sub=DUAL_SUB,
            client_sub=CLIENT_SUB,
            stranger_sub=STRANGER_SUB,
            provider_scope_id=provider_scope,
        )

    def set_binding_status(self, client_account_id: uuid.UUID, status: str) -> None:
        with self._bootstrap() as connection:
            connection.execute(
                "UPDATE f1.analysis_report_client_audience "
                "SET status=%s, updated_at=statement_timestamp() "
                "WHERE client_account_id=%s",
                (status, client_account_id),
            )
            connection.commit()

    def set_binding_client(
        self,
        *,
        provider_enterprise_id: uuid.UUID,
        audience_enterprise_id: uuid.UUID,
        client_account_id: uuid.UUID,
    ) -> None:
        with self._bootstrap() as connection:
            changed = connection.execute(
                "UPDATE f1.analysis_report_client_audience "
                "SET client_account_id=%s, updated_at=statement_timestamp() "
                "WHERE enterprise_id=%s AND audience_enterprise_id=%s",
                (
                    client_account_id,
                    provider_enterprise_id,
                    audience_enterprise_id,
                ),
            ).rowcount
            if changed != 1:
                raise HarnessError("AUDIENCE_BINDING_NOT_UNIQUE")
            connection.commit()

    def api_counts(self, enterprise_id: uuid.UUID, sub: str) -> dict[str, int]:
        with psycopg.connect(
            host="127.0.0.1",
            port=self.host_port,
            dbname=self.database,
            user="f1_api",
            password=self.passwords["f1_api"],
        ) as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT set_config('f1.enterprise_id', %s, true)",
                    (str(enterprise_id),),
                )
                connection.execute("SELECT set_config('f1.sub', %s, true)", (sub,))
                tables = (
                    "analysis_report",
                    "analysis_report_version",
                    "analysis_report_section",
                    "analysis_report_citation",
                )
                counts = {}
                for table in tables:
                    row = connection.execute(
                        f"SELECT count(*) FROM f1.{table}"
                    ).fetchone()
                    counts[table] = int(row[0]) if row else 0
                return counts

    def api_visible_report(
        self, enterprise_id: uuid.UUID, sub: str, report_id: uuid.UUID
    ) -> dict[str, int]:
        with psycopg.connect(
            host="127.0.0.1",
            port=self.host_port,
            dbname=self.database,
            user="f1_api",
            password=self.passwords["f1_api"],
        ) as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT set_config('f1.enterprise_id', %s, true)",
                    (str(enterprise_id),),
                )
                connection.execute("SELECT set_config('f1.sub', %s, true)", (sub,))
                report_n = connection.execute(
                    "SELECT count(*) FROM f1.analysis_report WHERE id=%s",
                    (report_id,),
                ).fetchone()
                version_n = connection.execute(
                    "SELECT count(*) FROM f1.analysis_report_version "
                    "WHERE report_id=%s",
                    (report_id,),
                ).fetchone()
                section_n = connection.execute(
                    "SELECT count(*) FROM f1.analysis_report_section AS section "
                    "JOIN f1.analysis_report_version AS version "
                    "  ON version.enterprise_id = section.enterprise_id "
                    " AND version.id = section.version_id "
                    "WHERE version.report_id=%s",
                    (report_id,),
                ).fetchone()
                citation_n = connection.execute(
                    "SELECT count(*) FROM f1.analysis_report_citation AS citation "
                    "JOIN f1.analysis_report_version AS version "
                    "  ON version.enterprise_id = citation.enterprise_id "
                    " AND version.id = citation.version_id "
                    "WHERE version.report_id=%s",
                    (report_id,),
                ).fetchone()
                return {
                    "analysis_report": int(report_n[0]),
                    "analysis_report_version": int(version_n[0]),
                    "analysis_report_section": int(section_n[0]),
                    "analysis_report_citation": int(citation_n[0]),
                }

    def worker_privileges(self) -> dict[str, dict[str, bool]]:
        tables = (
            "analysis_report_client_audience",
            "analysis_report",
            "analysis_report_version",
            "analysis_report_section",
            "analysis_report_citation",
            "analysis_report_generation_job",
            "analysis_report_audit_event",
            "analysis_report_health_snapshot",
        )
        result: dict[str, dict[str, bool]] = {}
        with self._bootstrap() as connection:
            for table in tables:
                row = connection.execute(
                    "SELECT has_table_privilege('f1_worker', %s, 'SELECT'),"
                    "has_table_privilege('f1_worker', %s, 'INSERT'),"
                    "has_table_privilege('f1_worker', %s, 'UPDATE'),"
                    "has_table_privilege('f1_worker', %s, 'DELETE')",
                    (f"f1.{table}", f"f1.{table}", f"f1.{table}", f"f1.{table}"),
                ).fetchone()
                result[table] = {
                    "SELECT": bool(row[0]),
                    "INSERT": bool(row[1]),
                    "UPDATE": bool(row[2]),
                    "DELETE": bool(row[3]),
                }
        return result

    def public_privileges(self) -> dict[str, dict[str, bool]]:
        tables = (
            "analysis_report_client_audience",
            "analysis_report",
            "analysis_report_version",
            "analysis_report_section",
            "analysis_report_citation",
            "analysis_report_generation_job",
            "analysis_report_audit_event",
            "analysis_report_health_snapshot",
        )
        result: dict[str, dict[str, bool]] = {}
        with self._bootstrap() as connection:
            for table in tables:
                row = connection.execute(
                    "SELECT has_table_privilege('public', %s, 'SELECT'),"
                    "has_table_privilege('public', %s, 'INSERT'),"
                    "has_table_privilege('public', %s, 'UPDATE'),"
                    "has_table_privilege('public', %s, 'DELETE')",
                    (f"f1.{table}", f"f1.{table}", f"f1.{table}", f"f1.{table}"),
                ).fetchone()
                result[table] = {
                    "SELECT": bool(row[0]),
                    "INSERT": bool(row[1]),
                    "UPDATE": bool(row[2]),
                    "DELETE": bool(row[3]),
                }
        return result

    def catalog_head(self) -> str:
        with self._bootstrap() as connection:
            row = connection.execute(
                "SELECT version_num FROM f1.alembic_version"
            ).fetchone()
        return str(row[0]) if row else ""

    def force_rls_names(self) -> set[str]:
        with self._bootstrap() as connection:
            rows = connection.execute(
                "SELECT c.relname FROM pg_class AS c "
                "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'f1' AND c.relkind = 'r' "
                "AND c.relrowsecurity AND c.relforcerowsecurity"
            ).fetchall()
        return {str(row[0]) for row in rows}

    def table_privileges(self, role: str, table: str) -> dict[str, bool]:
        with self._bootstrap() as connection:
            row = connection.execute(
                "SELECT has_table_privilege(%s, %s, 'SELECT'),"
                "has_table_privilege(%s, %s, 'INSERT'),"
                "has_table_privilege(%s, %s, 'UPDATE'),"
                "has_table_privilege(%s, %s, 'DELETE')",
                (
                    role,
                    f"f1.{table}",
                    role,
                    f"f1.{table}",
                    role,
                    f"f1.{table}",
                    role,
                    f"f1.{table}",
                ),
            ).fetchone()
        return {
            "SELECT": bool(row[0]),
            "INSERT": bool(row[1]),
            "UPDATE": bool(row[2]),
            "DELETE": bool(row[3]),
        }

    def snapshot_row(self, version_id: uuid.UUID) -> dict[str, object] | None:
        with self._bootstrap() as connection:
            row = connection.execute(
                "SELECT report_id, version_id, payload, payload_sha256, score, max_score "
                "FROM f1.analysis_report_health_snapshot WHERE version_id = %s",
                (version_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "report_id": row[0],
            "version_id": row[1],
            "payload": row[2],
            "payload_sha256": row[3],
            "score": row[4],
            "max_score": row[5],
        }

    def snapshot_count(self, version_id: uuid.UUID | None = None) -> int:
        with self._bootstrap() as connection:
            if version_id is None:
                row = connection.execute(
                    "SELECT count(*) FROM f1.analysis_report_health_snapshot"
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT count(*) FROM f1.analysis_report_health_snapshot "
                    "WHERE version_id = %s",
                    (version_id,),
                ).fetchone()
        return int(row[0]) if row else 0

    def version_status(self, version_id: uuid.UUID) -> str:
        with self._bootstrap() as connection:
            row = connection.execute(
                "SELECT status FROM f1.analysis_report_version WHERE id = %s",
                (version_id,),
            ).fetchone()
        return str(row[0]) if row else ""

    def audit_actions(self, version_id: uuid.UUID) -> list[str]:
        with self._bootstrap() as connection:
            rows = connection.execute(
                "SELECT action FROM f1.analysis_report_audit_event "
                "WHERE version_id = %s ORDER BY id",
                (version_id,),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def dispose_runtime(self) -> None:
        try:
            from platform_foundation.f1 import database
        except Exception:
            return

        async def _dispose() -> None:
            engines = list(database._engines.values())
            database._engines.clear()
            database._factories.clear()
            for engine in engines:
                await engine.dispose()

        import asyncio

        asyncio.run(_dispose())

    def stop(self) -> None:
        compose_env = self.control_dir / "compose.env"
        docker_env = (
            self._compose_docker_env() if self.secrets_dir is not None else _docker_env()
        )
        if compose_env.exists():
            _run(
                [
                    str(DOCKER),
                    "compose",
                    "--progress",
                    "quiet",
                    "--project-name",
                    self.project_name,
                    "--env-file",
                    str(compose_env),
                    "-f",
                    str(COMPOSE_FILE),
                    "down",
                    "--volumes",
                    "--remove-orphans",
                    "--timeout",
                    "20",
                ],
                environment=docker_env,
                timeout=90,
            )
        leftovers = subprocess.check_output(
            [
                str(DOCKER),
                "ps",
                "-aq",
                "--filter",
                f"label=io.anhuan.scope={SCOPE}",
            ],
            text=True,
        ).strip()
        if leftovers:
            subprocess.run(
                [str(DOCKER), "rm", "-f", *leftovers.splitlines()],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        volumes = subprocess.check_output(
            [
                str(DOCKER),
                "volume",
                "ls",
                "-q",
                "--filter",
                f"label=io.anhuan.scope={SCOPE}",
            ],
            text=True,
        ).strip()
        if volumes:
            subprocess.run(
                [str(DOCKER), "volume", "rm", "-f", *volumes.splitlines()],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        networks = subprocess.check_output(
            [
                str(DOCKER),
                "network",
                "ls",
                "-q",
                "--filter",
                f"label=io.anhuan.scope={SCOPE}",
            ],
            text=True,
        ).strip()
        if networks:
            subprocess.run(
                [str(DOCKER), "network", "rm", *networks.splitlines()],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        if self.control_dir.exists():
            shutil.rmtree(self.control_dir)
        self.started = False
        self.dedicated_after = dedicated_counts()
        after = canonical_shared_fingerprint()
        self.shared_match = int(after == self.before_fingerprint)
        self.cleanup_status = (
            "CLEAN"
            if self.dedicated_after == (0, 0, 0)
            and self.shared_match == 1
            and not self.control_dir.exists()
            else "RESIDUAL"
        )


def _insert_unit(
    connection: psycopg.Connection,
    *,
    label: str,
    enterprise_id: uuid.UUID,
    scope_id: uuid.UUID,
    actor_id: uuid.UUID,
    scope_kind: str,
) -> None:
    from platform_foundation.f1.features.material_rag.security import (
        canonical_unit,
        encrypt_text,
        unit_aad,
    )

    document_id = _stable("document", label)
    record_id = _stable("record", label)
    task_id = _stable("task", label)
    version_id = _stable("version", label)
    source_sha = hashlib.sha256(f"arpg|{label}|{enterprise_id}".encode()).hexdigest()
    object_key = f"arpg/{label}"
    title = f"{label}-current"
    body = f"{label} 合成材料用于分析报告本地夹具。"
    connection.execute(
        "INSERT INTO f1.document "
        "(id,enterprise_id,object_key,filename,size,content_type,status,"
        "knowledge_scope_id) VALUES (%s,%s,%s,%s,32,'application/pdf','done',%s)",
        (document_id, enterprise_id, object_key, f"{label}.pdf", scope_id),
    )
    connection.execute(
        "INSERT INTO f1.document_record "
        "(id,enterprise_id,title,status,latest_version_no,created_by_user_id,"
        "declared_material_kind,knowledge_scope_id,scope_selection_source,"
        "scope_selected_by_user_id,scope_selected_at) "
        "VALUES (%s,%s,%s,'active',1,%s,'unknown',%s,'upload_selection',%s,"
        "statement_timestamp())",
        (record_id, enterprise_id, title, actor_id, scope_id, actor_id),
    )
    connection.execute(
        "INSERT INTO f1.upload_task "
        "(id,enterprise_id,document_id,object_key,content_sha256,status,"
        "object_state,pipeline_kind,processing_stage,quarantine_status,"
        "scan_verdict,preview_status,preview_kind,released_at) "
        "VALUES (%s,%s,%s,%s,%s,'done','ready','controlled_ingestion','ready',"
        "'released','clean','ready','page_text',statement_timestamp())",
        (task_id, enterprise_id, document_id, object_key, source_sha),
    )
    connection.execute(
        "INSERT INTO f1.document_version "
        "(id,enterprise_id,document_record_id,version_no,source_document_id,"
        "upload_task_id,display_filename,idempotency_key_sha256,"
        "created_by_user_id) VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s)",
        (
            version_id,
            enterprise_id,
            record_id,
            document_id,
            task_id,
            f"{label}.pdf",
            hashlib.sha256(f"idem|{label}".encode()).hexdigest(),
            actor_id,
        ),
    )
    unit = canonical_unit(
        enterprise_id=enterprise_id,
        knowledge_scope_id=scope_id,
        document_record_id=record_id,
        document_version_id=version_id,
        source_sha256=source_sha,
        page_number=1,
        ordinal=1,
        parser_version=PARSER_VERSION,
        text=body,
    )
    ciphertext, aad_sha = encrypt_text(unit.body.reveal(), unit_aad(unit))
    connection.execute(
        "INSERT INTO f1.material_rag_unit "
        "(id,enterprise_id,knowledge_scope_id,document_record_id,"
        "document_version_id,source_sha256,page_number,ordinal,parser_version,"
        "body_ciphertext,body_sha256,body_aad_sha256) "
        "VALUES (%s,%s,%s,%s,%s,%s,1,1,%s,%s,%s,%s)",
        (
            unit.id,
            enterprise_id,
            scope_id,
            record_id,
            version_id,
            source_sha,
            PARSER_VERSION,
            ciphertext,
            unit.body_sha256,
            aad_sha,
        ),
    )
    del scope_kind
