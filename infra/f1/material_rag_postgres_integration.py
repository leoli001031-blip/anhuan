"""Dedicated PostgreSQL harness for material-RAG backend integration tests.

Closed compose set: secret-init + postgres.  Host migrator and unittest stay
outside the container.  Fake transport lives only in the test module.
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
COMPOSE_FILE = ROOT / "infra/f1/docker-compose.material-rag-postgres-integration.yml"
DOCKER = Path("/Applications/Docker.app/Contents/Resources/bin/docker")
SCOPE = "material-rag-postgres-integration"
EVIDENCE_ROOT = Path(
    "/Users/lichenhao/Desktop/安环项目/artifacts/material-rag-backup-design-hardening-20260819-v1"
)
PYTHON = sys.executable
FIXTURE_NS = uuid.UUID("6c2f8d1e-4a0b-4f33-9c7a-12b9e0d4a8f1")
BINDING_NS = uuid.UUID("fdc520dc-ffca-4ba3-a875-6ca74754655e")
PARSER_VERSION = "pgint1"


def _iso(value: object) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


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


@dataclass(frozen=True)
class UnitSpec:
    canonical_unit_id: uuid.UUID
    knowledge_scope_id: uuid.UUID
    document_record_id: uuid.UUID
    document_version_id: uuid.UUID
    source_sha256: str
    page_number: int
    body_sha256: str
    body: str
    scope_kind: str
    document_name: str
    version_number: int


@dataclass
class IntegrationWorld:
    tenant_a: object
    tenant_b: object
    client_a_id: uuid.UUID
    client_b_id: uuid.UUID
    empty_client_id: uuid.UUID
    foreign_client_id: uuid.UUID
    provider_context: object
    provider_b_context: object
    units: dict[str, UnitSpec]
    leak_tokens: tuple[str, ...]


@dataclass(frozen=True)
class LifecycleDoc:
    version_id: uuid.UUID
    record_id: uuid.UUID
    task_id: uuid.UUID
    source_sha256: str
    scope_id: uuid.UUID


@dataclass
class LifecycleWorld:
    tenant_a: object
    tenant_b: object
    docs: dict[str, LifecycleDoc]
    bodies: dict[str, str]

    def body_for(self, source_sha256: str) -> str:
        return self.bodies[source_sha256]


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


def canonical_shared_fingerprint() -> bytes:
    def capture(args: list[str]) -> list[str]:
        raw = subprocess.check_output([str(DOCKER), *args], text=True).strip()
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
            subprocess.check_output([str(DOCKER), "inspect", cid], text=True)
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
                [str(DOCKER), "volume", "inspect", name], text=True
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
                [str(DOCKER), "network", "inspect", nid], text=True
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


async def _insert_lifecycle_document(
    session,
    *,
    tenant,
    actor_id: uuid.UUID,
    scope_id: uuid.UUID,
    label: str,
    source_sha: str,
    body: str,
    released: bool,
    material_kind: str,
) -> LifecycleDoc:
    from sqlalchemy import text as sql_text

    source_size = len(body.encode("utf-8"))
    document_id = uuid.uuid5(FIXTURE_NS, f"life-doc:{label}:{scope_id}")
    record_id = uuid.uuid5(FIXTURE_NS, f"life-record:{label}:{scope_id}")
    task_id = uuid.uuid5(FIXTURE_NS, f"life-task:{label}:{scope_id}")
    version_id = uuid.uuid5(FIXTURE_NS, f"life-version:{label}:{scope_id}")
    object_key = f"{task_id.hex}.pdf"
    idempotency_sha = hashlib.sha256(
        f"material-rag-life-v1\x00{scope_id}\x00{label}".encode("ascii")
    ).hexdigest()
    await session.execute(
        sql_text(
            "INSERT INTO f1.document (id,enterprise_id,knowledge_scope_id,"
            "object_key,filename,size,content_type,status) VALUES "
            "(:id,:enterprise_id,:scope_id,:object_key,:filename,:size,"
            "'application/pdf','done')"
        ),
        {
            "id": document_id,
            "enterprise_id": tenant.enterprise_id,
            "scope_id": scope_id,
            "object_key": object_key,
            "filename": f"LIFE_{label.upper()}.pdf",
            "size": source_size,
        },
    )
    quarantine = "released" if released else "held"
    released_sql = "statement_timestamp()" if released else "NULL"
    await session.execute(
        sql_text(
            "INSERT INTO f1.upload_task (id,enterprise_id,document_id,object_key,"
            "content_sha256,status,object_state,source_size,pipeline_kind,"
            "processing_stage,quarantine_status,scan_verdict,preview_kind,"
            "preview_status,preview_sha256,preview_unit_count,"
            "resource_policy_version,released_at) VALUES "
            f"(:id,:enterprise_id,:document_id,:object_key,:source_sha,'done',"
            f"'ready',:source_size,'controlled_ingestion','ready',:quarantine,"
            f"'clean','page_text','ready',:source_sha,1,'p3-v1',{released_sql})"
        ),
        {
            "id": task_id,
            "enterprise_id": tenant.enterprise_id,
            "document_id": document_id,
            "object_key": object_key,
            "source_sha": source_sha,
            "source_size": source_size,
            "quarantine": quarantine,
        },
    )
    await session.execute(
        sql_text(
            "INSERT INTO f1.document_record (id,enterprise_id,title,status,"
            "declared_material_kind,knowledge_scope_id,scope_selection_source,"
            "scope_selected_by_user_id,scope_selected_at,latest_version_no,"
            "created_by_user_id) VALUES "
            "(:id,:enterprise_id,:title,'active',:material_kind,:scope_id,"
            "'upload_selection',:actor_id,statement_timestamp(),1,:actor_id)"
        ),
        {
            "id": record_id,
            "enterprise_id": tenant.enterprise_id,
            "title": f"LIFE_{label.upper()}",
            "material_kind": material_kind,
            "scope_id": scope_id,
            "actor_id": actor_id,
        },
    )
    await session.execute(
        sql_text(
            "INSERT INTO f1.document_version (id,enterprise_id,document_record_id,"
            "version_no,source_document_id,upload_task_id,display_filename,"
            "idempotency_key_sha256,created_by_user_id) VALUES "
            "(:id,:enterprise_id,:record_id,1,:source_document_id,:task_id,"
            ":display_filename,:idempotency_sha,:actor_id)"
        ),
        {
            "id": version_id,
            "enterprise_id": tenant.enterprise_id,
            "record_id": record_id,
            "source_document_id": document_id,
            "task_id": task_id,
            "display_filename": f"LIFE_{label.upper()}.pdf",
            "idempotency_sha": idempotency_sha,
            "actor_id": actor_id,
        },
    )
    return LifecycleDoc(
        version_id=version_id,
        record_id=record_id,
        task_id=task_id,
        source_sha256=source_sha,
        scope_id=scope_id,
    )


class PostgresIntegrationStack:
    def __init__(self) -> None:
        self.run_id = uuid.uuid4().hex
        self.project_id = uuid.uuid4().hex
        self.project_name = f"anhuan-mr-pgint-{self.run_id[:12]}"
        self.database = f"f1_pgint_{self.run_id[:12]}"
        self.host_port = _free_port()
        self.control_dir = Path(
            f"/private/tmp/anhuan-mr-pgint-{self.run_id[:12]}"
        )
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
        self.lifecycle_scope_ids: tuple[uuid.UUID, ...] = ()

    def _compose_docker_env(self) -> dict[str, str]:
        if self.secrets_dir is None:
            raise HarnessError("SECRETS_DIR_MISSING")
        return _docker_env(
            {
                "LOCAL_MATERIAL_RAG_PGINT_PROJECT_ID": self.project_id,
                "LOCAL_MATERIAL_RAG_PGINT_DATABASE": self.database,
                "LOCAL_MATERIAL_RAG_PGINT_SECRETS_DIR": str(self.secrets_dir),
                "LOCAL_MATERIAL_RAG_PGINT_HOST_PORT": str(self.host_port),
            }
        )

    def runtime_env(self) -> dict[str, str]:
        if self.secrets_dir is None:
            raise HarnessError("SECRETS_DIR_MISSING")
        env = os.environ.copy()
        env.update(
            {
                "F1_PG_HOST": "127.0.0.1",
                "F1_PG_PORT": str(self.host_port),
                "F1_PG_DATABASE": self.database,
                "F1_SECRETS_DIR": str(self.secrets_dir),
                "F1_KEYCLOAK_REALM": "anhuan",
                "KEYCLOAK_URL": "http://material-rag.invalid",
                "F1_KEYCLOAK_ISSUER_URL": "http://material-rag.invalid/realms/anhuan",
                "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT),
                "F1_PROVIDER_SECRETS_DIR": str(self.secrets_dir),
            }
        )
        env.pop("LOCAL_MATERIAL_RAG_PGINT_SECRETS_DIR", None)
        return env

    def apply_env(self) -> None:
        os.environ.update(
            {
                "F1_PG_HOST": "127.0.0.1",
                "F1_PG_PORT": str(self.host_port),
                "F1_PG_DATABASE": self.database,
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
        _write_secret(
            self.secrets_dir / "f1_material_rag_manifest_key", secrets.token_hex(32)
        )
        _write_secret(self.secrets_dir / "ragflow_api_key", secrets.token_hex(16))
        compose_env = self.control_dir / "compose.env"
        _write_secret(
            compose_env,
            "\n".join(
                (
                    f"LOCAL_MATERIAL_RAG_PGINT_PROJECT_ID={self.project_id}",
                    f"LOCAL_MATERIAL_RAG_PGINT_DATABASE={self.database}",
                    f"LOCAL_MATERIAL_RAG_PGINT_SECRETS_DIR={self.secrets_dir}",
                    f"LOCAL_MATERIAL_RAG_PGINT_HOST_PORT={self.host_port}",
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
            [PYTHON, "-B", "infra/f1/material-rag/migrate.py"],
            environment=self.runtime_env(),
            timeout=300,
        )
        if (
            migrated.returncode != 0
            or migrated.stdout.decode("ascii", "replace").strip()
            != "LOCAL_MATERIAL_RAG_MIGRATE_OK"
        ):
            token = _failure_token(
                migrated.stderr,
                (
                    ("LOCAL_MATERIAL_RAG_MIGRATION_HEAD_MISMATCH", "HEAD_MISMATCH"),
                    ("F1_BOOTSTRAP_CONNECTION_IDENTITY_MISMATCH", "BOOTSTRAP_IDENTITY"),
                    ("F0D_MIGRATION_DSN_IDENTITY_MISMATCH", "F0D_DSN"),
                    ("F1_MIGRATION_DSN_IDENTITY_MISMATCH", "F1_DSN"),
                    ("F1_SECRET_PERMISSIONS_INVALID", "SECRET_PERMISSIONS"),
                    ("F1_SECRETS_DIR_INVALID", "SECRETS_DIR"),
                ),
            )
            self.stop()
            raise HarnessError(f"MIGRATE_FAILED:{token}")

    def _seed_identities(self) -> None:
        from infra.f1 import local_seed
        from infra.f1.migrate_f1 import _bootstrap_dsn

        with psycopg.connect(_bootstrap_dsn(), autocommit=False) as connection:
            head = connection.execute(
                "SELECT string_agg(version_num, ',' ORDER BY version_num) "
                "FROM f1.alembic_version"
            ).fetchone()
            if head is None or head[0] != "f1_0016":
                self.stop()
                raise HarnessError("SEED_HEAD_MISMATCH")
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
            local_seed._ensure_durability_canary(connection)
            connection.commit()

    def seed_world(self) -> IntegrationWorld:
        from infra.f1 import local_seed
        from platform_foundation.f1.auth import Tenant
        from platform_foundation.f1.features.material_rag.contracts import (
            RetrievalContext,
        )
        from platform_foundation.f1.features.material_rag.security import (
            CLIENT_B_ISOLATION_CANARY_TEXT,
            PROVIDER_POLICY_CANARY_TEXT,
            canonical_unit,
            dataset_ref_aad,
            encrypt_text,
            unit_aad,
        )

        actor_a = local_seed._stable_id(
            "profile", "db906685-6906-4bc4-9d3a-9011975fd132"
        )
        actor_b = local_seed._stable_id(
            "profile", "ddc4e27e-ccde-4c89-958f-798fc8f30175"
        )
        enterprise_a = local_seed.ENTERPRISE_A
        enterprise_b = local_seed.ENTERPRISE_B
        client_a_id = uuid.uuid5(FIXTURE_NS, "client-a")
        client_b_id = uuid.uuid5(FIXTURE_NS, "client-b")
        empty_client_id = uuid.uuid5(FIXTURE_NS, "client-empty")
        foreign_client_id = uuid.uuid5(FIXTURE_NS, "client-foreign")
        provider_a = uuid.uuid5(FIXTURE_NS, "scope-provider-a")
        client_a_scope = uuid.uuid5(FIXTURE_NS, "scope-client-a")
        client_b_scope = uuid.uuid5(FIXTURE_NS, "scope-client-b")
        provider_b = uuid.uuid5(FIXTURE_NS, "scope-provider-b")
        refs = {
            "provider_a": "aa" * 16,
            "client_a": "ab" * 16,
            "client_b": "ac" * 16,
            "provider_b": "ba" * 16,
        }
        bodies = {
            "provider_a": PROVIDER_POLICY_CANARY_TEXT,
            "client_a": "客户甲作业前必须复核许可范围与现场条件。",
            "client_b": CLIENT_B_ISOLATION_CANARY_TEXT,
            "provider_b": "乙企业共享政策要求作业前核对适用边界。",
            "dirty": "已感染合成材料不得进入检索可见集。",
            "stale": "过期版本合成材料不得进入检索可见集。",
            "revoked": "已撤销合成材料不得进入检索可见集。",
            "preview_not_ready": "预览未完成合成材料不得进入检索可见集。",
            "forged_aad": "伪造附加数据合成材料不得进入检索可见集。",
        }
        units: dict[str, UnitSpec] = {}
        with psycopg.connect(
            host="127.0.0.1",
            port=self.host_port,
            dbname=self.database,
            user="f0d_bootstrap",
            password=self.passwords["bootstrap"],
        ) as connection:
            connection.execute("SELECT set_config('session_replication_role','replica',false)")
            connection.execute(
                "INSERT INTO f1.crm_account "
                "(id,enterprise_id,display_name,stage,created_by_user_id) "
                "VALUES (%s,%s,%s,'active',%s),(%s,%s,%s,'active',%s),"
                "(%s,%s,%s,'active',%s),(%s,%s,%s,'active',%s)",
                (
                    client_a_id,
                    enterprise_a,
                    "Client A",
                    actor_a,
                    client_b_id,
                    enterprise_a,
                    "Client B",
                    actor_a,
                    empty_client_id,
                    enterprise_a,
                    "Client Empty",
                    actor_a,
                    foreign_client_id,
                    enterprise_b,
                    "Client Foreign",
                    actor_b,
                ),
            )
            connection.execute(
                "INSERT INTO f1.material_knowledge_scope "
                "(id,enterprise_id,scope_kind,client_account_id) VALUES "
                "(%s,%s,'service_provider',NULL),(%s,%s,'client',%s),"
                "(%s,%s,'client',%s),(%s,%s,'service_provider',NULL)",
                (
                    provider_a,
                    enterprise_a,
                    client_a_scope,
                    enterprise_a,
                    client_a_id,
                    client_b_scope,
                    enterprise_a,
                    client_b_id,
                    provider_b,
                    enterprise_b,
                ),
            )
            for label, enterprise, scope_id in (
                ("provider_a", enterprise_a, provider_a),
                ("client_a", enterprise_a, client_a_scope),
                ("client_b", enterprise_a, client_b_scope),
                ("provider_b", enterprise_b, provider_b),
            ):
                binding_id = uuid.uuid5(
                    BINDING_NS, f"{enterprise}\x00{scope_id}\x00ragflow"
                )
                dataset_ref = refs[label]
                ciphertext, aad_sha = encrypt_text(
                    dataset_ref,
                    dataset_ref_aad(
                        enterprise_id=enterprise,
                        knowledge_scope_id=scope_id,
                        binding_id=binding_id,
                    ),
                )
                connection.execute(
                    "INSERT INTO f1.material_rag_scope_binding "
                    "(id,enterprise_id,knowledge_scope_id,backend,"
                    "dataset_ref_ciphertext,dataset_ref_sha256,"
                    "dataset_ref_aad_sha256,status) "
                    "VALUES (%s,%s,%s,'ragflow',%s,%s,%s,'ready')",
                    (
                        binding_id,
                        enterprise,
                        scope_id,
                        ciphertext,
                        hashlib.sha256(dataset_ref.encode("utf-8")).hexdigest(),
                        aad_sha,
                    ),
                )
            placements = (
                ("provider_a", enterprise_a, provider_a, actor_a, "service_provider"),
                ("client_a", enterprise_a, client_a_scope, actor_a, "client"),
                ("client_b", enterprise_a, client_b_scope, actor_a, "client"),
                ("provider_b", enterprise_b, provider_b, actor_b, "service_provider"),
                ("dirty", enterprise_a, provider_a, actor_a, "service_provider"),
                ("stale", enterprise_a, provider_a, actor_a, "service_provider"),
                ("revoked", enterprise_a, provider_a, actor_a, "service_provider"),
                (
                    "preview_not_ready",
                    enterprise_a,
                    provider_a,
                    actor_a,
                    "service_provider",
                ),
                ("forged_aad", enterprise_a, provider_a, actor_a, "service_provider"),
            )
            for label, enterprise, scope_id, actor, scope_kind in placements:
                units[label] = _insert_unit(
                    connection,
                    label=label,
                    enterprise_id=enterprise,
                    scope_id=scope_id,
                    actor_id=actor,
                    scope_kind=scope_kind,
                    body=bodies[label],
                    forged_aad=label == "forged_aad",
                )
            _disqualify(connection, units)
            connection.execute(
                "SELECT set_config('session_replication_role','origin',false)"
            )
            connection.commit()
        tenant_a = Tenant(
            enterprise_id=enterprise_a,
            sub="db906685-6906-4bc4-9d3a-9011975fd132",
            roles=("enterprise_admin",),
            role="enterprise_admin",
        )
        tenant_b = Tenant(
            enterprise_id=enterprise_b,
            sub="ddc4e27e-ccde-4c89-958f-798fc8f30175",
            roles=("enterprise_admin",),
            role="enterprise_admin",
        )
        return IntegrationWorld(
            tenant_a=tenant_a,
            tenant_b=tenant_b,
            client_a_id=client_a_id,
            client_b_id=client_b_id,
            empty_client_id=empty_client_id,
            foreign_client_id=foreign_client_id,
            provider_context=RetrievalContext(
                enterprise_id=enterprise_a,
                kind="service_provider",
                client_account_id=None,
                scope_ids=(provider_a,),
            ),
            provider_b_context=RetrievalContext(
                enterprise_id=enterprise_b,
                kind="service_provider",
                client_account_id=None,
                scope_ids=(provider_b,),
            ),
            units=units,
            leak_tokens=tuple(refs.values()),
        )

    def _bootstrap(self):
        return psycopg.connect(
            host="127.0.0.1",
            port=self.host_port,
            dbname=self.database,
            user="f0d_bootstrap",
            password=self.passwords["bootstrap"],
        )

    def seed_lifecycle_world(self) -> LifecycleWorld:
        import asyncio

        from infra.f1 import local_seed
        from platform_foundation.f1.auth import Tenant
        from platform_foundation.f1.database import session_scope
        from platform_foundation.f1.features.material_rag.security import (
            AUTHORIZED_DEMO_SOURCE_SHA256,
            CLIENT_B_ISOLATION_CANARY_TEXT,
            PROVIDER_POLICY_CANARY_TEXT,
        )
        from platform_foundation.f1.features.p3.service import (
            _current_user_id,
            _resolve_knowledge_scope,
        )

        tenant_a = Tenant(
            enterprise_id=local_seed.ENTERPRISE_A,
            sub="db906685-6906-4bc4-9d3a-9011975fd132",
            roles=("enterprise_admin",),
            role="enterprise_admin",
        )
        tenant_b = Tenant(
            enterprise_id=local_seed.ENTERPRISE_B,
            sub="ddc4e27e-ccde-4c89-958f-798fc8f30175",
            roles=("enterprise_admin",),
            role="enterprise_admin",
        )
        actor_a = local_seed._stable_id("profile", tenant_a.sub)
        actor_b = local_seed._stable_id("profile", tenant_b.sub)
        client_a_id = uuid.uuid5(FIXTURE_NS, "life-client-a")
        client_b_recovery_id = uuid.uuid5(FIXTURE_NS, "life-client-b-recovery")
        client_b_provision_id = uuid.uuid5(FIXTURE_NS, "life-client-b-provision")
        client_b_revoke_id = uuid.uuid5(FIXTURE_NS, "life-client-b-revoke")
        client_b_maintain_id = uuid.uuid5(FIXTURE_NS, "life-client-b-maintain")
        provider_sha = hashlib.sha256(
            PROVIDER_POLICY_CANARY_TEXT.encode("utf-8")
        ).hexdigest()
        client_sha = hashlib.sha256(
            CLIENT_B_ISOLATION_CANARY_TEXT.encode("utf-8")
        ).hexdigest()
        demo_sha = sorted(AUTHORIZED_DEMO_SOURCE_SHA256)[0]
        demo_body = "作业前核对隔离边界与许可范围。"
        maintain_sha = sorted(AUTHORIZED_DEMO_SOURCE_SHA256)[1]
        maintain_body = "维护演练只清离线任务行，不改生产接口。"
        unreleased_sha = hashlib.sha256(b"life-unreleased-source").hexdigest()
        bodies = {
            provider_sha: PROVIDER_POLICY_CANARY_TEXT,
            client_sha: CLIENT_B_ISOLATION_CANARY_TEXT,
            demo_sha: demo_body,
            maintain_sha: maintain_body,
        }
        with self._bootstrap() as connection:
            replica = connection.execute("SHOW session_replication_role").fetchone()
            if replica is None or replica[0] != "origin":
                raise HarnessError("REPLICA_ROLE_FORBIDDEN")
            connection.execute(
                "INSERT INTO f1.crm_account "
                "(id,enterprise_id,display_name,stage,created_by_user_id) "
                "VALUES (%s,%s,%s,'active',%s),(%s,%s,%s,'active',%s),"
                "(%s,%s,%s,'active',%s),(%s,%s,%s,'active',%s),"
                "(%s,%s,%s,'active',%s)",
                (
                    client_a_id,
                    local_seed.ENTERPRISE_A,
                    "Life Client A",
                    actor_a,
                    client_b_recovery_id,
                    local_seed.ENTERPRISE_B,
                    "Life Client B Recovery",
                    actor_b,
                    client_b_provision_id,
                    local_seed.ENTERPRISE_B,
                    "Life Client B Provision",
                    actor_b,
                    client_b_revoke_id,
                    local_seed.ENTERPRISE_B,
                    "Life Client B Revoke",
                    actor_b,
                    client_b_maintain_id,
                    local_seed.ENTERPRISE_B,
                    "Life Client B Maintain",
                    actor_b,
                ),
            )
            connection.commit()

        async def _seed() -> dict[str, LifecycleDoc]:
            docs: dict[str, LifecycleDoc] = {}
            async with session_scope(
                role="f1_api",
                enterprise_id=tenant_a.enterprise_id,
                sub=tenant_a.sub,
            ) as session:
                actor_id = await _current_user_id(session, tenant_a)
                scope = await _resolve_knowledge_scope(
                    session,
                    tenant_a,
                    kind="client",
                    client_account_id=client_a_id,
                    actor_id=actor_id,
                )
                scope_id = scope["id"]
                docs["sibling_a"] = await _insert_lifecycle_document(
                    session,
                    tenant=tenant_a,
                    actor_id=actor_id,
                    scope_id=scope_id,
                    label="sibling-a",
                    source_sha=provider_sha,
                    body=PROVIDER_POLICY_CANARY_TEXT,
                    released=True,
                    material_kind="policy",
                )
                docs["sibling_b"] = await _insert_lifecycle_document(
                    session,
                    tenant=tenant_a,
                    actor_id=actor_id,
                    scope_id=scope_id,
                    label="sibling-b",
                    source_sha=client_sha,
                    body=CLIENT_B_ISOLATION_CANARY_TEXT,
                    released=True,
                    material_kind="report",
                )
                docs["unreleased"] = await _insert_lifecycle_document(
                    session,
                    tenant=tenant_a,
                    actor_id=actor_id,
                    scope_id=scope_id,
                    label="unreleased",
                    source_sha=unreleased_sha,
                    body="未释放材料不得建索引。",
                    released=False,
                    material_kind="report",
                )
                await session.commit()
            async with session_scope(
                role="f1_api",
                enterprise_id=tenant_b.enterprise_id,
                sub=tenant_b.sub,
            ) as session:
                actor_id = await _current_user_id(session, tenant_b)
                recovery_scope = await _resolve_knowledge_scope(
                    session,
                    tenant_b,
                    kind="client",
                    client_account_id=client_b_recovery_id,
                    actor_id=actor_id,
                )
                provision_scope = await _resolve_knowledge_scope(
                    session,
                    tenant_b,
                    kind="client",
                    client_account_id=client_b_provision_id,
                    actor_id=actor_id,
                )
                revoke_scope = await _resolve_knowledge_scope(
                    session,
                    tenant_b,
                    kind="client",
                    client_account_id=client_b_revoke_id,
                    actor_id=actor_id,
                )
                maintain_scope = await _resolve_knowledge_scope(
                    session,
                    tenant_b,
                    kind="client",
                    client_account_id=client_b_maintain_id,
                    actor_id=actor_id,
                )
                docs["recovery"] = await _insert_lifecycle_document(
                    session,
                    tenant=tenant_b,
                    actor_id=actor_id,
                    scope_id=recovery_scope["id"],
                    label="recovery",
                    source_sha=provider_sha,
                    body=PROVIDER_POLICY_CANARY_TEXT,
                    released=True,
                    material_kind="policy",
                )
                docs["provision"] = await _insert_lifecycle_document(
                    session,
                    tenant=tenant_b,
                    actor_id=actor_id,
                    scope_id=provision_scope["id"],
                    label="provision",
                    source_sha=client_sha,
                    body=CLIENT_B_ISOLATION_CANARY_TEXT,
                    released=True,
                    material_kind="report",
                )
                docs["revoke"] = await _insert_lifecycle_document(
                    session,
                    tenant=tenant_b,
                    actor_id=actor_id,
                    scope_id=revoke_scope["id"],
                    label="revoke",
                    source_sha=demo_sha,
                    body=demo_body,
                    released=True,
                    material_kind="report",
                )
                docs["maintain"] = await _insert_lifecycle_document(
                    session,
                    tenant=tenant_b,
                    actor_id=actor_id,
                    scope_id=maintain_scope["id"],
                    label="maintain",
                    source_sha=maintain_sha,
                    body=maintain_body,
                    released=True,
                    material_kind="report",
                )
                await session.commit()
            return docs

        docs = asyncio.run(_seed())
        self.lifecycle_scope_ids = tuple(
            sorted({doc.scope_id for doc in docs.values()}, key=str)
        )
        return LifecycleWorld(
            tenant_a=tenant_a,
            tenant_b=tenant_b,
            docs=docs,
            bodies=bodies,
        )

    def seed_orchestration_world(self) -> LifecycleWorld:
        import asyncio

        from infra.f1 import local_seed
        from platform_foundation.f1.auth import Tenant
        from platform_foundation.f1.database import session_scope
        from platform_foundation.f1.features.material_rag.security import (
            CLIENT_B_ISOLATION_CANARY_TEXT,
            PROVIDER_POLICY_CANARY_TEXT,
        )
        from platform_foundation.f1.features.p3.service import (
            _current_user_id,
            _resolve_knowledge_scope,
        )
        from sqlalchemy import text as sql_text

        tenant_a = Tenant(
            enterprise_id=local_seed.ENTERPRISE_A,
            sub="db906685-6906-4bc4-9d3a-9011975fd132",
            roles=("enterprise_admin",),
            role="enterprise_admin",
        )
        tenant_b = Tenant(
            enterprise_id=local_seed.ENTERPRISE_B,
            sub="ddc4e27e-ccde-4c89-958f-798fc8f30175",
            roles=("enterprise_admin",),
            role="enterprise_admin",
        )
        actor_a = local_seed._stable_id("profile", tenant_a.sub)
        actor_b = local_seed._stable_id("profile", tenant_b.sub)
        client_a_id = uuid.uuid5(FIXTURE_NS, "orch-client-a")
        client_a2_id = uuid.uuid5(FIXTURE_NS, "orch-client-a2")
        client_b_id = uuid.uuid5(FIXTURE_NS, "orch-client-b")
        provider_sha = hashlib.sha256(
            PROVIDER_POLICY_CANARY_TEXT.encode("utf-8")
        ).hexdigest()
        client_sha = hashlib.sha256(
            CLIENT_B_ISOLATION_CANARY_TEXT.encode("utf-8")
        ).hexdigest()
        dirty_sha = hashlib.sha256(b"orch-dirty-source").hexdigest()
        unreleased_sha = hashlib.sha256(b"orch-unreleased-source").hexdigest()
        with self._bootstrap() as connection:
            replica = connection.execute("SHOW session_replication_role").fetchone()
            if replica is None or replica[0] != "origin":
                raise HarnessError("REPLICA_ROLE_FORBIDDEN")
            connection.execute(
                "INSERT INTO f1.crm_account "
                "(id,enterprise_id,display_name,stage,created_by_user_id) "
                "VALUES (%s,%s,%s,'active',%s),(%s,%s,%s,'active',%s),"
                "(%s,%s,%s,'active',%s)",
                (
                    client_a_id,
                    local_seed.ENTERPRISE_A,
                    "Orch Client A",
                    actor_a,
                    client_a2_id,
                    local_seed.ENTERPRISE_A,
                    "Orch Client A2",
                    actor_a,
                    client_b_id,
                    local_seed.ENTERPRISE_B,
                    "Orch Client B",
                    actor_b,
                ),
            )
            connection.commit()

        async def _seed() -> dict[str, LifecycleDoc]:
            docs: dict[str, LifecycleDoc] = {}
            async with session_scope(
                role="f1_api",
                enterprise_id=tenant_a.enterprise_id,
                sub=tenant_a.sub,
            ) as session:
                actor_id = await _current_user_id(session, tenant_a)
                scope = await _resolve_knowledge_scope(
                    session,
                    tenant_a,
                    kind="client",
                    client_account_id=client_a_id,
                    actor_id=actor_id,
                )
                scope2 = await _resolve_knowledge_scope(
                    session,
                    tenant_a,
                    kind="client",
                    client_account_id=client_a2_id,
                    actor_id=actor_id,
                )
                docs["held_a"] = await _insert_lifecycle_document(
                    session,
                    tenant=tenant_a,
                    actor_id=actor_id,
                    scope_id=scope["id"],
                    label="orch-held-a",
                    source_sha=provider_sha,
                    body=PROVIDER_POLICY_CANARY_TEXT,
                    released=False,
                    material_kind="policy",
                )
                docs["held_disabled"] = await _insert_lifecycle_document(
                    session,
                    tenant=tenant_a,
                    actor_id=actor_id,
                    scope_id=scope["id"],
                    label="orch-held-disabled",
                    source_sha=provider_sha,
                    body=PROVIDER_POLICY_CANARY_TEXT,
                    released=False,
                    material_kind="policy",
                )
                for worker_key, worker_label in (
                    ("worker_conc", "orch-worker-conc"),
                    ("worker_stale", "orch-worker-stale"),
                    ("worker_kill", "orch-worker-kill"),
                    ("worker_default", "orch-worker-default"),
                ):
                    docs[worker_key] = await _insert_lifecycle_document(
                        session,
                        tenant=tenant_a,
                        actor_id=actor_id,
                        scope_id=scope["id"],
                        label=worker_label,
                        source_sha=provider_sha,
                        body=PROVIDER_POLICY_CANARY_TEXT,
                        released=False,
                        material_kind="policy",
                    )
                docs["dirty"] = await _insert_lifecycle_document(
                    session,
                    tenant=tenant_a,
                    actor_id=actor_id,
                    scope_id=scope["id"],
                    label="orch-dirty",
                    source_sha=dirty_sha,
                    body="脏扫描不得入队。",
                    released=False,
                    material_kind="report",
                )
                docs["unreleased"] = await _insert_lifecycle_document(
                    session,
                    tenant=tenant_a,
                    actor_id=actor_id,
                    scope_id=scope["id"],
                    label="orch-unreleased",
                    source_sha=unreleased_sha,
                    body="未释放不得入队。",
                    released=False,
                    material_kind="report",
                )
                current = await _insert_lifecycle_document(
                    session,
                    tenant=tenant_a,
                    actor_id=actor_id,
                    scope_id=scope2["id"],
                    label="orch-stale-current",
                    source_sha=provider_sha,
                    body=PROVIDER_POLICY_CANARY_TEXT,
                    released=True,
                    material_kind="policy",
                )
                docs["stale_current"] = current
                docs["_stale_scope"] = scope2["id"]  # type: ignore[assignment]
                await session.commit()
            async with session_scope(
                role="f1_api",
                enterprise_id=tenant_b.enterprise_id,
                sub=tenant_b.sub,
            ) as session:
                actor_id = await _current_user_id(session, tenant_b)
                scope_b = await _resolve_knowledge_scope(
                    session,
                    tenant_b,
                    kind="client",
                    client_account_id=client_b_id,
                    actor_id=actor_id,
                )
                docs["held_b"] = await _insert_lifecycle_document(
                    session,
                    tenant=tenant_b,
                    actor_id=actor_id,
                    scope_id=scope_b["id"],
                    label="orch-held-b",
                    source_sha=client_sha,
                    body=CLIENT_B_ISOLATION_CANARY_TEXT,
                    released=False,
                    material_kind="report",
                )
                await session.commit()
            return docs

        docs = asyncio.run(_seed())
        current = docs.pop("stale_current")
        stale_scope = docs.pop("_stale_scope")
        stale_version_id = uuid.uuid5(
            FIXTURE_NS, f"life-version:orch-stale-old:{stale_scope}"
        )
        stale_task_id = uuid.uuid5(FIXTURE_NS, f"life-task:orch-stale-old:{stale_scope}")
        stale_document_id = uuid.uuid5(
            FIXTURE_NS, f"life-doc:orch-stale-old:{stale_scope}"
        )
        with self._bootstrap() as connection:
            replica = connection.execute("SHOW session_replication_role").fetchone()
            if replica is None or replica[0] != "origin":
                raise HarnessError("REPLICA_ROLE_FORBIDDEN")
            connection.execute(
                "UPDATE f1.upload_task SET scan_verdict='infected',"
                "object_state='quarantined',processing_stage='scanning' WHERE id=%s",
                (docs["dirty"].task_id,),
            )
            connection.execute(
                "UPDATE f1.document_version SET version_no=2 "
                "WHERE id=%s AND enterprise_id=%s",
                (current.version_id, tenant_a.enterprise_id),
            )
            connection.execute(
                "UPDATE f1.document_record SET latest_version_no=2 "
                "WHERE id=%s AND enterprise_id=%s",
                (current.record_id, tenant_a.enterprise_id),
            )
            connection.execute(
                "INSERT INTO f1.document (id,enterprise_id,knowledge_scope_id,"
                "object_key,filename,size,content_type,status) VALUES "
                "(%s,%s,%s,%s,'ORCH_STALE.pdf',%s,'application/pdf','done')",
                (
                    stale_document_id,
                    tenant_a.enterprise_id,
                    stale_scope,
                    f"{stale_task_id.hex}.pdf",
                    len(PROVIDER_POLICY_CANARY_TEXT.encode("utf-8")),
                ),
            )
            connection.execute(
                "INSERT INTO f1.upload_task (id,enterprise_id,document_id,object_key,"
                "content_sha256,status,object_state,source_size,pipeline_kind,"
                "processing_stage,quarantine_status,scan_verdict,preview_kind,"
                "preview_status,preview_sha256,preview_unit_count,"
                "resource_policy_version,released_at) VALUES "
                "(%s,%s,%s,%s,%s,'done','ready',%s,'controlled_ingestion','ready',"
                "'released','clean','page_text','ready',%s,1,'p3-v1',"
                "statement_timestamp())",
                (
                    stale_task_id,
                    tenant_a.enterprise_id,
                    stale_document_id,
                    f"{stale_task_id.hex}.pdf",
                    provider_sha,
                    len(PROVIDER_POLICY_CANARY_TEXT.encode("utf-8")),
                    provider_sha,
                ),
            )
            connection.execute(
                "INSERT INTO f1.document_version (id,enterprise_id,document_record_id,"
                "version_no,source_document_id,upload_task_id,display_filename,"
                "idempotency_key_sha256,created_by_user_id) VALUES "
                "(%s,%s,%s,1,%s,%s,'ORCH_STALE.pdf',%s,%s)",
                (
                    stale_version_id,
                    tenant_a.enterprise_id,
                    current.record_id,
                    stale_document_id,
                    stale_task_id,
                    hashlib.sha256(b"orch-stale-old").hexdigest(),
                    actor_a,
                ),
            )
            connection.commit()
        docs["stale_version"] = LifecycleDoc(
            version_id=stale_version_id,
            record_id=current.record_id,
            task_id=stale_task_id,
            source_sha256=provider_sha,
            scope_id=stale_scope,
        )
        return LifecycleWorld(
            tenant_a=tenant_a,
            tenant_b=tenant_b,
            docs=docs,
            bodies={
                provider_sha: PROVIDER_POLICY_CANARY_TEXT,
                client_sha: CLIENT_B_ISOLATION_CANARY_TEXT,
            },
        )

    def count_jobs_for_version(self, version_id: uuid.UUID) -> int:
        with self._bootstrap() as connection:
            row = connection.execute(
                "SELECT count(*) FROM f1.material_rag_job "
                "WHERE document_version_id=%s",
                (version_id,),
            ).fetchone()
        return int(row[0]) if row else 0

    def job_status_for_version(self, version_id: uuid.UUID) -> str | None:
        with self._bootstrap() as connection:
            row = connection.execute(
                "SELECT status FROM f1.material_rag_job "
                "WHERE document_version_id=%s ORDER BY created_at, id LIMIT 1",
                (version_id,),
            ).fetchone()
        return None if row is None else str(row[0])

    def claim_next_sync(self, worker_id: str, lease_seconds: int = 2):
        from platform_foundation.f1.features.material_rag.contracts import (
            MaterialRagJobClaim,
        )

        with psycopg.connect(
            host="127.0.0.1",
            port=self.host_port,
            dbname=self.database,
            user="f1_worker",
            password=self.passwords["f1_worker"],
        ) as connection:
            connection.execute("SET search_path = pg_catalog, f1")
            row = connection.execute(
                "SELECT job_id, enterprise_id, knowledge_scope_id,"
                "document_record_id, document_version_id, upload_task_id,"
                "source_sha256, action, lease_token, attempt "
                "FROM f1.claim_next_material_rag_job(%s, %s)",
                (worker_id, lease_seconds),
            ).fetchone()
            connection.commit()
        if row is None:
            return None
        return MaterialRagJobClaim(
            id=row[0],
            enterprise_id=row[1],
            knowledge_scope_id=row[2],
            document_record_id=row[3],
            document_version_id=row[4],
            upload_task_id=row[5],
            source_sha256=str(row[6]),
            action=str(row[7]),  # type: ignore[arg-type]
            lease_token=row[8],
            attempt=int(row[9]),
        )

    def job_id_for_version(self, version_id: uuid.UUID) -> uuid.UUID:
        with self._bootstrap() as connection:
            row = connection.execute(
                "SELECT id FROM f1.material_rag_job "
                "WHERE document_version_id=%s ORDER BY created_at, id LIMIT 1",
                (version_id,),
            ).fetchone()
        if row is None:
            raise HarnessError("JOB_ROW_MISSING")
        return row[0]

    def released_at(self, task_id: uuid.UUID):
        with self._bootstrap() as connection:
            row = connection.execute(
                "SELECT released_at FROM f1.upload_task WHERE id=%s",
                (task_id,),
            ).fetchone()
        if row is None:
            raise HarnessError("TASK_ROW_MISSING")
        return row[0]

    def count_jobs_visible(self, tenant) -> int:
        import asyncio

        from platform_foundation.f1.database import session_scope
        from sqlalchemy import text as sql_text

        async def _count() -> int:
            async with session_scope(
                role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
            ) as session:
                row = await session.execute(sql_text("SELECT count(*) FROM f1.material_rag_job"))
                return int(row.scalar_one())

        return asyncio.run(_count())

    def count_job_visible_to(self, tenant, version_id: uuid.UUID) -> int:
        import asyncio

        from platform_foundation.f1.database import session_scope
        from sqlalchemy import text as sql_text

        async def _count() -> int:
            async with session_scope(
                role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
            ) as session:
                row = await session.execute(
                    sql_text(
                        "SELECT count(*) FROM f1.material_rag_job "
                        "WHERE document_version_id=:version_id"
                    ),
                    {"version_id": version_id},
                )
                return int(row.scalar_one())

        return asyncio.run(_count())

    def execute_as_api(self, tenant, statement: str, params: tuple[object, ...]):
        with psycopg.connect(
            host="127.0.0.1",
            port=self.host_port,
            dbname=self.database,
            user="f1_api",
            password=self.passwords["f1_api"],
        ) as connection:
            connection.execute(
                "SELECT set_config('f1.enterprise_id', %s, false),"
                "set_config('f1.sub', %s, false)",
                (str(tenant.enterprise_id), tenant.sub),
            )
            return connection.execute(statement, params).fetchall()

    def lifecycle_snapshot(self, version_id: uuid.UUID) -> dict[str, object]:
        with self._bootstrap() as connection:
            unit_rows = connection.execute(
                "SELECT id::text, body_sha256 FROM f1.material_rag_unit "
                "WHERE document_version_id=%s ORDER BY id",
                (version_id,),
            ).fetchall()
            job = connection.execute(
                "SELECT status, result_manifest_sha256 FROM f1.material_rag_job "
                "WHERE document_version_id=%s ORDER BY created_at DESC, id DESC "
                "LIMIT 1",
                (version_id,),
            ).fetchone()
            terminals = connection.execute(
                "SELECT count(*) FROM f1.material_rag_job "
                "WHERE document_version_id=%s AND status IN ('done','failed')",
                (version_id,),
            ).fetchone()
            scope_id = connection.execute(
                "SELECT knowledge_scope_id FROM f1.document_version "
                "JOIN f1.document_record ON document_record.id="
                "document_version.document_record_id "
                "AND document_record.enterprise_id=document_version.enterprise_id "
                "WHERE document_version.id=%s",
                (version_id,),
            ).fetchone()
            binding = None
            if scope_id is not None:
                binding = connection.execute(
                    "SELECT status, "
                    "(dataset_ref_ciphertext IS NOT NULL OR "
                    "dataset_ref_sha256 IS NOT NULL OR "
                    "dataset_ref_aad_sha256 IS NOT NULL)::int AS secrets "
                    "FROM f1.material_rag_scope_binding "
                    "WHERE knowledge_scope_id=%s",
                    (scope_id[0],),
                ).fetchone()
        fingerprint = hashlib.sha256(
            b"\n".join(f"{row[0]}:{row[1]}".encode("ascii") for row in unit_rows)
        ).hexdigest()
        return {
            "unit_count": len(unit_rows),
            "unit_fingerprint": fingerprint,
            "manifest_sha": None if job is None else job[1],
            "job_status": None if job is None else job[0],
            "terminal_count": int(terminals[0]) if terminals else 0,
            "binding_status": "absent" if binding is None else binding[0],
            "binding_secrets": 0 if binding is None else int(binding[1]),
            "scope_id": None if scope_id is None else scope_id[0],
        }

    def local_job_world_snapshot(self, version_id: uuid.UUID) -> bytes:
        with self._bootstrap() as connection:
            replica = connection.execute("SHOW session_replication_role").fetchone()
            if replica is None or replica[0] != "origin":
                raise HarnessError("REPLICA_ROLE_FORBIDDEN")
            jobs = connection.execute(
                "SELECT status, attempt, lease_token::text, lease_owner, "
                "lease_acquired_at, lease_until, next_attempt_at, error_reason, "
                "result_manifest_sha256, indexed_unit_count, updated_at "
                "FROM f1.material_rag_job WHERE document_version_id=%s "
                "ORDER BY id",
                (version_id,),
            ).fetchall()
            unit_rows = connection.execute(
                "SELECT id::text, body_sha256 FROM f1.material_rag_unit "
                "WHERE document_version_id=%s ORDER BY id",
                (version_id,),
            ).fetchall()
            scope_id = connection.execute(
                "SELECT knowledge_scope_id FROM f1.document_version "
                "JOIN f1.document_record ON document_record.id="
                "document_version.document_record_id "
                "AND document_record.enterprise_id=document_version.enterprise_id "
                "WHERE document_version.id=%s",
                (version_id,),
            ).fetchone()
            binding = None
            if scope_id is not None:
                binding = connection.execute(
                    "SELECT status, "
                    "(dataset_ref_ciphertext IS NOT NULL)::int + "
                    "(dataset_ref_sha256 IS NOT NULL)::int + "
                    "(dataset_ref_aad_sha256 IS NOT NULL)::int "
                    "FROM f1.material_rag_scope_binding "
                    "WHERE knowledge_scope_id=%s",
                    (scope_id[0],),
                ).fetchone()
        fingerprint = hashlib.sha256(
            b"\n".join(f"{row[0]}:{row[1]}".encode("ascii") for row in unit_rows)
        ).hexdigest()
        payload = {
            "binding_secret_fields": 0 if binding is None else int(binding[1]),
            "binding_status": "absent" if binding is None else binding[0],
            "jobs": [
                {
                    "attempt": int(row[1]),
                    "error_reason": row[7],
                    "indexed_unit_count": None if row[9] is None else int(row[9]),
                    "lease_acquired_at": _iso(row[4]),
                    "lease_owner": row[3],
                    "lease_token": row[2],
                    "lease_until": _iso(row[5]),
                    "next_attempt_at": _iso(row[6]),
                    "result_manifest_sha256": row[8],
                    "status": row[0],
                    "updated_at": _iso(row[10]),
                }
                for row in jobs
            ],
            "unit_count": len(unit_rows),
            "unit_fingerprint": fingerprint,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )

    def prove_illegal_job_update_to_failed(
        self, job_id: uuid.UUID
    ) -> dict[str, object]:
        def dump(connection: psycopg.Connection) -> dict[str, object]:
            row = connection.execute(
                "SELECT status, attempt, lease_token::text, lease_owner, "
                "lease_acquired_at, lease_until, next_attempt_at, error_reason, "
                "result_manifest_sha256, indexed_unit_count, updated_at "
                "FROM f1.material_rag_job WHERE id=%s",
                (job_id,),
            ).fetchone()
            if row is None:
                raise HarnessError("JOB_ROW_MISSING")
            return {
                "attempt": int(row[1]),
                "error_reason": row[7],
                "indexed_unit_count": None if row[9] is None else int(row[9]),
                "lease_acquired_at": _iso(row[4]),
                "lease_owner": row[3],
                "lease_token": row[2],
                "lease_until": _iso(row[5]),
                "next_attempt_at": _iso(row[6]),
                "result_manifest_sha256": row[8],
                "status": row[0],
                "updated_at": _iso(row[10]),
            }

        with self._bootstrap() as connection:
            replica = connection.execute("SHOW session_replication_role").fetchone()
            if replica is None or replica[0] != "origin":
                raise HarnessError("REPLICA_ROLE_FORBIDDEN")
            identity = connection.execute(
                "SELECT current_user, session_user"
            ).fetchone()
            if identity is None or identity[0] != "f0d_bootstrap" or identity[1] != "f0d_bootstrap":
                raise HarnessError("BOOTSTRAP_IDENTITY_MISMATCH")
            before = dump(connection)
            committed = False
            sqlstate = ""
            message = ""
            try:
                connection.execute(
                    "UPDATE f1.material_rag_job SET status='failed', "
                    "lease_token=NULL, lease_owner=NULL, lease_acquired_at=NULL, "
                    "lease_until=NULL, next_attempt_at=NULL, "
                    "error_reason='MATERIAL_RAG_RESTORE_MAINTENANCE', "
                    "result_manifest_sha256=NULL, indexed_unit_count=NULL "
                    "WHERE id=%s",
                    (job_id,),
                )
                connection.commit()
                committed = True
            except Exception as exc:
                sqlstate = str(getattr(exc, "sqlstate", "") or "")
                message = str(exc)
                connection.rollback()
            after = dump(connection)
        return {
            "after": after,
            "before": before,
            "committed": committed,
            "message": message,
            "sqlstate": sqlstate,
        }

    def lifecycle_job_status_summary(self) -> dict[str, int]:
        if not self.lifecycle_scope_ids:
            raise HarnessError("LIFECYCLE_SCOPES_MISSING")
        scopes = list(self.lifecycle_scope_ids)
        with self._bootstrap() as connection:
            replica = connection.execute("SHOW session_replication_role").fetchone()
            if replica is None or replica[0] != "origin":
                raise HarnessError("REPLICA_ROLE_FORBIDDEN")
            rows = connection.execute(
                "SELECT "
                "count(*) FILTER (WHERE status='queued'), "
                "count(*) FILTER (WHERE status='retry_wait'), "
                "count(*) FILTER (WHERE status='done'), "
                "count(*) FILTER (WHERE status='failed'), "
                "count(*) FILTER ("
                "WHERE status='running' AND lease_until > statement_timestamp()"
                "), "
                "count(*) FILTER ("
                "WHERE status='running' AND lease_until <= statement_timestamp()"
                ") "
                "FROM f1.material_rag_job WHERE knowledge_scope_id=ANY(%s)",
                (scopes,),
            ).fetchone()
        if rows is None:
            raise HarnessError("JOB_STATUS_SUMMARY_MISSING")
        return {
            "done": int(rows[2]),
            "failed": int(rows[3]),
            "queued": int(rows[0]),
            "retry_wait": int(rows[1]),
            "running_expired": int(rows[5]),
            "running_live": int(rows[4]),
        }

    def lifecycle_row_counts(self) -> dict[str, int]:
        if not self.lifecycle_scope_ids:
            raise HarnessError("LIFECYCLE_SCOPES_MISSING")
        scopes = list(self.lifecycle_scope_ids)
        with self._bootstrap() as connection:
            replica = connection.execute("SHOW session_replication_role").fetchone()
            if replica is None or replica[0] != "origin":
                raise HarnessError("REPLICA_ROLE_FORBIDDEN")
            jobs = connection.execute(
                "SELECT count(*) FROM f1.material_rag_job "
                "WHERE knowledge_scope_id=ANY(%s)",
                (scopes,),
            ).fetchone()
            units = connection.execute(
                "SELECT count(*) FROM f1.material_rag_unit "
                "WHERE knowledge_scope_id=ANY(%s)",
                (scopes,),
            ).fetchone()
            residual = self._residual_sql(connection)
        return {
            "deleted_binding_secrets": residual["deleted_binding_secrets"],
            "job": int(jobs[0]) if jobs else 0,
            "live_lease": residual["live_lease"],
            "orphan_unit": residual["orphan_unit"],
            "provisioning_binding": residual["provisioning_binding"],
            "unit": int(units[0]) if units else 0,
        }

    def restore_maintenance_clear_lifecycle(self) -> dict[str, object]:
        if not self.lifecycle_scope_ids:
            raise HarnessError("LIFECYCLE_SCOPES_MISSING")
        scopes = list(self.lifecycle_scope_ids)
        with self._bootstrap() as connection:
            replica = connection.execute("SHOW session_replication_role").fetchone()
            if replica is None or replica[0] != "origin":
                raise HarnessError("REPLICA_ROLE_FORBIDDEN")
            identity = connection.execute(
                "SELECT current_user, session_user, "
                "current_setting('session_replication_role')"
            ).fetchone()
            if (
                identity is None
                or identity[0] != "f0d_bootstrap"
                or identity[1] != "f0d_bootstrap"
                or identity[2] != "origin"
            ):
                raise HarnessError("BOOTSTRAP_IDENTITY_MISMATCH")
            before_jobs = connection.execute(
                "SELECT count(*) FROM f1.material_rag_job "
                "WHERE knowledge_scope_id=ANY(%s)",
                (scopes,),
            ).fetchone()
            before_units = connection.execute(
                "SELECT count(*) FROM f1.material_rag_unit "
                "WHERE knowledge_scope_id=ANY(%s)",
                (scopes,),
            ).fetchone()
            before_live = connection.execute(
                "SELECT count(*) FROM f1.material_rag_job "
                "WHERE knowledge_scope_id=ANY(%s) AND status='running' "
                "AND lease_until > statement_timestamp()",
                (scopes,),
            ).fetchone()
            before = {
                "job": int(before_jobs[0]) if before_jobs else 0,
                "running_live": int(before_live[0]) if before_live else 0,
                "unit": int(before_units[0]) if before_units else 0,
            }
            connection.execute(
                "DELETE FROM f1.material_rag_job WHERE knowledge_scope_id=ANY(%s)",
                (scopes,),
            )
            connection.execute(
                "DELETE FROM f1.material_rag_unit WHERE knowledge_scope_id=ANY(%s)",
                (scopes,),
            )
            connection.execute(
                "UPDATE f1.material_rag_scope_binding SET "
                "dataset_ref_ciphertext=NULL, dataset_ref_sha256=NULL, "
                "dataset_ref_aad_sha256=NULL, status='deleted', "
                "error_reason=NULL WHERE knowledge_scope_id=ANY(%s)",
                (scopes,),
            )
            connection.commit()
        return {
            "before": before,
            "identity": {
                "current_user": identity[0],
                "replication_role": identity[2],
                "session_user": identity[1],
            },
        }

    def revoke_release(self, task_id: uuid.UUID) -> int:
        with self._bootstrap() as connection:
            replica = connection.execute("SHOW session_replication_role").fetchone()
            if replica is None or replica[0] != "origin":
                raise HarnessError("REPLICA_ROLE_FORBIDDEN")
            row = connection.execute(
                "UPDATE f1.upload_task SET quarantine_status='held', "
                "released_at=NULL, updated_at=statement_timestamp() "
                "WHERE id=%s AND quarantine_status='released' RETURNING id",
                (task_id,),
            ).fetchone()
            connection.commit()
        return 0 if row is None else 1

    def make_retry_due(self, job_id: uuid.UUID) -> int:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with self._bootstrap() as connection:
                row = connection.execute(
                    "SELECT 1 FROM f1.material_rag_job "
                    "WHERE id=%s AND status='retry_wait' "
                    "AND next_attempt_at <= statement_timestamp()",
                    (job_id,),
                ).fetchone()
            if row is not None:
                return 1
            time.sleep(0.5)
        return 0

    def expire_running_lease(self, job_id: uuid.UUID) -> int:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            with self._bootstrap() as connection:
                row = connection.execute(
                    "SELECT 1 FROM f1.material_rag_job "
                    "WHERE id=%s AND status='running' "
                    "AND lease_until <= statement_timestamp()",
                    (job_id,),
                ).fetchone()
            if row is not None:
                return 1
            time.sleep(0.5)
        return 0

    def _residual_sql(self, connection) -> dict[str, int]:
        replica = connection.execute("SHOW session_replication_role").fetchone()
        if replica is None or replica[0] != "origin":
            raise HarnessError("REPLICA_ROLE_FORBIDDEN")
        idle = connection.execute(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE usename IN ('f1_api','f1_worker') "
            "AND state='idle in transaction'"
        ).fetchone()
        live = connection.execute(
            "SELECT count(*) FROM f1.material_rag_job "
            "WHERE status='running' AND lease_until > statement_timestamp()"
        ).fetchone()
        if not self.lifecycle_scope_ids:
            raise HarnessError("LIFECYCLE_SCOPES_MISSING")
        scopes = list(self.lifecycle_scope_ids)
        provisioning = connection.execute(
            "SELECT count(*) FROM f1.material_rag_scope_binding "
            "WHERE status='provisioning' AND knowledge_scope_id=ANY(%s)",
            (scopes,),
        ).fetchone()
        deleted_secrets = connection.execute(
            "SELECT count(*) FROM f1.material_rag_scope_binding "
            "WHERE status='deleted' AND knowledge_scope_id=ANY(%s) AND ("
            "dataset_ref_ciphertext IS NOT NULL OR "
            "dataset_ref_sha256 IS NOT NULL OR "
            "dataset_ref_aad_sha256 IS NOT NULL)",
            (scopes,),
        ).fetchone()
        orphan = connection.execute(
            "SELECT count(*) FROM f1.material_rag_unit AS unit "
            "WHERE unit.knowledge_scope_id=ANY(%s) AND NOT EXISTS ("
            "SELECT 1 FROM f1.document_version AS version "
            "JOIN f1.document_record AS record "
            "ON record.enterprise_id=version.enterprise_id "
            "AND record.id=version.document_record_id "
            "JOIN f1.upload_task AS task "
            "ON task.enterprise_id=version.enterprise_id "
            "AND task.id=version.upload_task_id "
            "WHERE version.enterprise_id=unit.enterprise_id "
            "AND version.id=unit.document_version_id "
            "AND version.document_record_id=unit.document_record_id "
            "AND record.knowledge_scope_id=unit.knowledge_scope_id "
            "AND record.id=unit.document_record_id "
            "AND task.content_sha256=unit.source_sha256)",
            (scopes,),
        ).fetchone()
        return {
            "idle_in_transaction": int(idle[0]) if idle else 0,
            "live_lease": int(live[0]) if live else 0,
            "orphan_unit": int(orphan[0]) if orphan else 0,
            "provisioning_binding": int(provisioning[0]) if provisioning else 0,
            "deleted_binding_secrets": int(deleted_secrets[0])
            if deleted_secrets
            else 0,
        }

    def lifecycle_residuals(self) -> dict[str, int]:
        with self._bootstrap() as connection:
            return self._residual_sql(connection)

    def prove_orphan_unit_residual_then_rollback(self, task_id: uuid.UUID) -> dict[str, int]:
        broken = hashlib.sha256(b"lifecycle-orphan-source-break").hexdigest()
        with self._bootstrap() as connection:
            replica = connection.execute("SHOW session_replication_role").fetchone()
            if replica is None or replica[0] != "origin":
                raise HarnessError("REPLICA_ROLE_FORBIDDEN")
            connection.execute(
                "UPDATE f1.upload_task SET content_sha256=%s WHERE id=%s",
                (broken, task_id),
            )
            counts = self._residual_sql(connection)
            connection.rollback()
        return counts

    def unit_identities(self, version_id: uuid.UUID) -> tuple[tuple[str, str], ...]:
        with self._bootstrap() as connection:
            rows = connection.execute(
                "SELECT id::text, body_sha256 FROM f1.material_rag_unit "
                "WHERE document_version_id=%s ORDER BY id::text, body_sha256",
                (version_id,),
            ).fetchall()
        return tuple((str(row[0]), str(row[1])) for row in rows)

    def idle_in_transaction_count(self) -> int:
        with self._bootstrap() as connection:
            row = connection.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE usename IN ('f1_api','f1_worker') "
                "AND state='idle in transaction'"
            ).fetchone()
        return int(row[0]) if row else 0

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
        docker_env = self._compose_docker_env() if self.secrets_dir is not None else _docker_env()
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
        _write_cycle_evidence(self)

    def write_cycle_evidence(self) -> None:
        _write_cycle_evidence(self)


def _stable(label: str, *parts: object) -> uuid.UUID:
    return uuid.uuid5(FIXTURE_NS, ":".join((label, *(str(part) for part in parts))))


def _insert_unit(
    connection: psycopg.Connection,
    *,
    label: str,
    enterprise_id: uuid.UUID,
    scope_id: uuid.UUID,
    actor_id: uuid.UUID,
    scope_kind: str,
    body: str,
    forged_aad: bool,
) -> UnitSpec:
    from platform_foundation.f1.features.material_rag.security import (
        canonical_unit,
        encrypt_text,
        unit_aad,
    )

    document_id = _stable("document", label)
    record_id = _stable("record", label)
    task_id = _stable("task", label)
    version_id = _stable("version", label)
    source_sha = hashlib.sha256(f"pgint|{label}|{enterprise_id}".encode()).hexdigest()
    object_key = f"pgint/{label}"
    title = f"{label}-current"
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
    if forged_aad:
        aad_sha = hashlib.sha256(b"forged-aad").hexdigest()
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
    return UnitSpec(
        canonical_unit_id=unit.id,
        knowledge_scope_id=scope_id,
        document_record_id=record_id,
        document_version_id=version_id,
        source_sha256=source_sha,
        page_number=1,
        body_sha256=unit.body_sha256,
        body=unit.body.reveal(),
        scope_kind=scope_kind,
        document_name=title,
        version_number=1,
    )


def _disqualify(connection: psycopg.Connection, units: dict[str, UnitSpec]) -> None:
    dirty_task = _stable("task", "dirty")
    stale_task = _stable("task", "stale")
    revoked_task = _stable("task", "revoked")
    preview_task = _stable("task", "preview_not_ready")
    connection.execute(
        "UPDATE f1.upload_task SET object_state='quarantined',"
        "quarantine_status='held',released_at=NULL,scan_verdict='infected',"
        "processing_stage='scanning',status='scanning' WHERE id=%s",
        (dirty_task,),
    )
    connection.execute(
        "UPDATE f1.upload_task SET content_sha256=%s WHERE id=%s",
        (hashlib.sha256(b"stale-content").hexdigest(), stale_task),
    )
    connection.execute(
        "UPDATE f1.upload_task SET quarantine_status='held',released_at=NULL "
        "WHERE id=%s",
        (revoked_task,),
    )
    connection.execute(
        "UPDATE f1.upload_task SET object_state='quarantined',"
        "quarantine_status='held',released_at=NULL,preview_status='generating' "
        "WHERE id=%s",
        (preview_task,),
    )
    del units  # specs remain; visibility is enforced by SQL


def _write_cycle_evidence(stack: PostgresIntegrationStack) -> None:
    raw_dir = os.environ.get("MATERIAL_RAG_PGINT_EVIDENCE_DIR", "").strip()
    if not raw_dir:
        return
    root = Path(raw_dir)
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise HarnessError("EVIDENCE_DIR_INVALID")
    os.chmod(root, 0o700)
    cycle = os.environ.get("MATERIAL_RAG_PGINT_CYCLE", "integration")
    payload = {
        "phase": "CLEANUP",
        "operation": "COMPOSE_DOWN",
        "sqlstate": "NONE",
        "dedicated_c": stack.dedicated_after[0],
        "dedicated_v": stack.dedicated_after[1],
        "dedicated_n": stack.dedicated_after[2],
        "shared_fingerprint_match": stack.shared_match,
        "cleanup_status": stack.cleanup_status,
        "control_dir_present": int(stack.control_dir.exists()),
        "status_code": 0 if stack.cleanup_status == "CLEAN" else 2,
    }
    path = root / f"cycle-{cycle}.json"
    if path.exists():
        raise HarnessError("CYCLE_EVIDENCE_EXISTS")
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, raw)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(path, 0o600)
