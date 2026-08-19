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
    "/Users/lichenhao/Desktop/安环项目/artifacts/material-rag-backend-postgres-20260819-v1"
)
PYTHON = sys.executable
FIXTURE_NS = uuid.UUID("6c2f8d1e-4a0b-4f33-9c7a-12b9e0d4a8f1")
BINDING_NS = uuid.UUID("fdc520dc-ffca-4ba3-a875-6ca74754655e")
PARSER_VERSION = "pgint1"


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
        seeded = _run(
            [PYTHON, "-B", "infra/f1/material-rag/seed.py"],
            environment=self.runtime_env(),
            timeout=60,
        )
        if (
            seeded.returncode != 0
            or seeded.stdout.decode("ascii", "replace").strip()
            != "LOCAL_MATERIAL_RAG_SEED_OK"
        ):
            self.stop()
            raise HarnessError("SEED_FAILED")

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

    def idle_in_transaction_count(self) -> int:
        with psycopg.connect(
            host="127.0.0.1",
            port=self.host_port,
            dbname=self.database,
            user="f0d_bootstrap",
            password=self.passwords["bootstrap"],
        ) as connection:
            row = connection.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE usename='f1_api' AND state='idle in transaction'"
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
    EVIDENCE_ROOT.mkdir(mode=0o700, exist_ok=True)
    info = EVIDENCE_ROOT.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise HarnessError("EVIDENCE_DIR_INVALID")
    os.chmod(EVIDENCE_ROOT, 0o700)
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
    path = EVIDENCE_ROOT / f"cycle-{cycle}.json"
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if path.exists():
        path.unlink()
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
