#!/usr/bin/env python3
"""Disposable, tracked-source clean rebuild for F1.1.1 formal acceptance.

The runner owns one freshly generated Docker Compose project and one separate
PostgreSQL container/volume.  It never accepts a command, project name,
database name, port, image tag, repository root, or evidence payload from the
caller.  Subprocess output is retained only long enough to validate a fixed
machine contract and is never forwarded.

The two formal rounds intentionally use different random UUIDv4 namespaces.
Their sole success digest is calculated from a normalized summary that omits
round number, UUID, ports, timestamps, paths and container IDs while binding
every service's actual image ID and declared/base-image provenance.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import secrets as crypto_secrets
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Callable, Mapping, Sequence

from platform_foundation.f0_isolation import (
    ENVIRONMENT_VARIABLE as F0_ISOLATION_ENVIRONMENT_VARIABLE,
    FrozenF0Isolation,
    FrozenF0IsolationError,
    build_frozen_f0_isolation,
    load_frozen_f0_isolation,
    validate_frozen_f0_isolation,
    write_frozen_f0_isolation,
)


ROOT = Path(__file__).resolve().parents[1]
ROUND_ENV = "F111_FORMAL_REBUILD_ROUND"
BASE_REVISION = "262bf9fb7de4b076dbb6be47c14496c5a4549333"
PROJECT_PREFIX = "anhuan-f111-repair-"
DATABASE_PREFIX = "f111_repair_"
PG_IMAGE = (
    "postgres:18.3-bookworm@sha256:"
    "80630f83606d8db77d30b3851b16a9f78be2d0d4dda6f7b82a1fdca5ebe3acba"
)
DOCKER_COMPOSE_LAUNCHER = Path("/usr/local/bin/docker-compose")
DOCKER_COMPOSE_PLUGIN_DIRECTORY = Path(
    "/Applications/Docker.app/Contents/Resources/cli-plugins"
)
DOCKER_COMPOSE_PLUGIN = DOCKER_COMPOSE_PLUGIN_DIRECTORY / "docker-compose"
DOCKER_COMPOSE_SHA256 = "bd9ebf387820fdf1abf3194da5735fd835ad18d6e37f6022f24ccdb2ee9db124"
SCHEMA = "f1.1.1-clean-rebuild-v2"
CLEAN_EVIDENCE_SCHEMA = "f1.1.1-clean-rebuild-evidence-v1"
CLEAN_EVIDENCE_NAMES = {
    1: "f111-clean-rebuild-round-1.json",
    2: "f111-clean-rebuild-round-2.json",
}
MAX_PROCESS_OUTPUT = 64 * 1024 * 1024
MAX_PRIVATE_FILE = 512 * 1024
MAX_SOURCE_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_SOURCE_BUNDLE_HEADER_BYTES = 256 * 1024
MAX_SOURCE_FILE = 256 * 1024 * 1024
MAX_SOURCE_DUMP = 512 * 1024 * 1024
SOURCE_DATABASE_NAME = "f0i_acceptance_v01"
SOURCE_DATABASE_SCHEMAS = ("f0d", "f0i")
SOURCE_DATABASE_SUPERUSER = "f0d_bootstrap"
SOURCE_COMPOSE_PROJECT = "anhuan-f0d"
SOURCE_COMPOSE_SERVICE = "postgres"
F0I_SOURCE_SCOPE_SCHEMA = "f1.1.1-f0i-source-scope-v1"
F0I_SOURCE_ACCESS = "LOCAL_DOCKER_EXEC_READ_ONLY"
F0G_SOURCE_SCOPE_SCHEMA = "f1.1.1-f0g-source-scope-v2"
F0G_SOURCE_DATABASE_NAME = "f0f_acceptance_v01"
# FORCE RLS prevents the migration role from producing a complete frozen
# data-only copy.  The already-pinned local bootstrap role is used by both
# F0-I and F0G source reads; every statement remains fixed and read-only.
F0G_SOURCE_ROLE = SOURCE_DATABASE_SUPERUSER
F0G_SOURCE_SCHEMAS = ("f0d", "f0e", "f0f")
F0G_SOURCE_SCOPE_NAME = "f0g_source_scope"
SOURCE_BUNDLE_NAME = "fixture_source_objects_bundle"
SOURCE_BUNDLE_SCHEMA = "f1.1.1-fixture-source-objects-bundle-v1"
SOURCE_BUNDLE_MAGIC = b"F111FSB1"
SOURCE_BUNDLE_ENTRY_COUNT = 26
SOURCE_BUNDLE_GROUP_COUNTS = {"core": 24, "negative": 2}
RUNTIME_TREE_BUNDLE_SCHEMA = "f1.1.1-frozen-runtime-tree-bundle-v1"
RUNTIME_TREE_BUNDLE_MAGIC = b"F111RTB1"
RUNTIME_TREE_BUNDLE_HEADER_BYTES = 256 * 1024
RUNTIME_TREE_BUNDLES = {
    "f0e": ("f0e_runtime_tree_bundle", 512 * 1024),
    "f0f": ("f0f_runtime_tree_bundle", 512 * 1024),
    "f0h": ("f0h_runtime_tree_bundle", 64 * 1024 * 1024),
}
F0F_SOURCE_KEY_NAME = "f0f_source_key"
F0_ISOLATION_CONFIG_NAME = "frozen-f0-isolation.json"
FROZEN_RUNTIME_TREE_SHA256 = {
    "f0e": "18108f9d5336b34b7a898b9683b325a251769e4ca080565ef0adda8f2eab7e55",
    "f0f": "229c9078caccdeb6ff0d94ad6da9eab72d167ee89d4236fe73aec3cffe9a7ea6",
    "f0h": "3b705b9c88f65df44db3afbe5a8b278b2c7e322c8f3f850152f96c93762026d8",
}
SOURCE_ID_NAMESPACE = uuid.UUID("5a4940b4-cb56-5e76-8a62-70cd9e30084f")
FIXTURE_SET_ID = "environment-demo-seed"
FIXTURE_SET_VERSION = "v0.1"
REQUIRED_FIXTURES = 4
REQUIRED_SOURCE_TABLES = frozenset(
    {
        ("f0d", "fixture_source_registry"),
        ("f0i", "document_scope"),
        ("f0i", "page"),
        ("f0i", "chunk"),
    }
)

_SOURCE_INSPECT_FORMAT = "\n".join(
    (
        "{{json .Id}}",
        "{{json .Name}}",
        "{{json .Image}}",
        "{{json .State.Status}}",
        "{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}}",
        '{{json (index .Config.Labels "com.docker.compose.project")}}',
        '{{json (index .Config.Labels "com.docker.compose.service")}}',
        "{{json .Config.Image}}",
        '{{json (index .NetworkSettings.Ports "5432/tcp")}}',
    )
)

PORT_NAMES = (
    "api",
    "grafana",
    "jaeger_grpc",
    "jaeger_http",
    "jaeger_ui",
    "keycloak",
    "minio_api",
    "minio_console",
    "postgres",
    "prometheus",
    "ragflow_api",
    "ragflow_http",
    "redis",
    "web",
)
PUBLISHED_PORTS = {
    "api": ("api", 8001),
    "grafana": ("grafana", 3000),
    "jaeger_grpc": ("jaeger", 4317),
    "jaeger_http": ("jaeger", 4318),
    "jaeger_ui": ("jaeger", 16686),
    "keycloak": ("keycloak", 8080),
    "minio_api": ("minio", 9000),
    "minio_console": ("minio", 9001),
    "prometheus": ("prometheus", 9090),
    "ragflow_api": ("ragflow", 9380),
    "ragflow_http": ("ragflow", 80),
    "redis": ("redis", 6379),
    "web": ("web", 80),
}

EXPECTED_SERVICES = frozenset(
    {
        "keycloak",
        "keycloak-provisioner",
        "minio",
        "redis",
        "prometheus",
        "grafana",
        "jaeger",
        "otel-collector",
        "ragflow-mysql",
        "ragflow-es01",
        "ragflow-minio",
        "ragflow-redis",
        "ragflow",
        "api",
        "worker",
        "dispatcher",
        "web",
    }
)
RUNTIME_SERVICES = EXPECTED_SERVICES | {"postgres"}
EXPECTED_VOLUMES = frozenset(
    {
        "keycloak_data",
        "minio_data",
        "redis_data",
        "grafana_data",
        "ragflow_mysql_data",
        "ragflow_es_data",
        "ragflow_minio_data",
        "ragflow_redis_data",
        "ragflow_logs",
    }
)

COPY_SECRET_FILES = (
    "f1_api_password",
    "f1_worker_password",
    "f1_qa_key",
    "grafana_admin_password",
    "invite_signing_key",
    "keycloak_admin_password",
    "minio_root_password",
    "minio_root_user",
    "oidc_admin_anhuan_local",
    "oidc_auditor",
    "oidc_invitee",
    "oidc_tenant_a",
    "oidc_tenant_b",
    "oidc_tester",
    "auditor_password",
    "auditor_username",
    "invitee_password",
    "invitee_username",
    "tenant_a_password",
    "tenant_a_username",
    "tenant_b_password",
    "tenant_b_username",
)
PROVIDER_SECRET_FILES = (
    "ark_api_key",
    "deepseek_api_key",
    "ragflow_api_key",
)
PASSWORD_BINDINGS = (
    ("oidc_auditor", "auditor_password"),
    ("oidc_invitee", "invitee_password"),
    ("oidc_tenant_a", "tenant_a_password"),
    ("oidc_tenant_b", "tenant_b_password"),
)

REVERSE_METRICS = (
    "valid_http_e2e",
    "membership_mint",
    "invite_double_consume",
    "stale_lease_commit",
    "duplicate_dispatch",
    "upload_replay_effects",
    "enqueue_recovery",
    "worker_restart",
    "ragflow_recovery",
    "qa_request_races",
    "citation_crosswires",
    "tenant_crosswires",
    "audit_gaps",
    "object_orphans_delta",
    "rq_orphans_delta",
    "index_duplicates",
    "preclean_mutations",
    "new_plaintext_leaks",
    "upstream_mutations",
    "scratch_residuals",
)

_PAIR = re.compile(rb"\b([a-z][a-z0-9_]*)=(-?[0-9]+)\b")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_PROJECT = re.compile(r"^anhuan-f111-repair-([0-9a-f]{32})$")

BUILD_PROVENANCE_LABELS = {
    "source_snapshot_sha256": "io.anhuan.f111.source-snapshot-sha256",
    "dockerfile_set_sha256": "io.anhuan.f111.dockerfile-set-sha256",
    "python_lock_sha256": "io.anhuan.f111.python-lock-sha256",
    "npm_lock_sha256": "io.anhuan.f111.npm-lock-sha256",
}
BUILD_PROVENANCE_ARGS = {
    "source_snapshot_sha256": "F111_SOURCE_SNAPSHOT_SHA256",
    "dockerfile_set_sha256": "F111_DOCKERFILE_SET_SHA256",
    "python_lock_sha256": "F111_PYTHON_LOCK_SHA256",
    "npm_lock_sha256": "F111_NPM_LOCK_SHA256",
}

FIXTURE_PLAN_CONTRACTS: Mapping[str, tuple[Path, str]] = {
    "fixture_core_manifest": (
        Path("fixtures/environment-demo-seed/v0.1/core-manifest.sha256"),
        "e9425d59207461b9ff87c601016932653b8cd3522b7d70ede877ebb5ce6316ae",
    ),
    "fixture_negative_manifest": (
        Path("fixtures/environment-demo-seed/v0.1/negative-manifest.sha256"),
        "2238a2e084e5ff0c37756ed341244bb04cdf8361e45709dfb7939eba7be20e04",
    ),
    "fixture_route_plan_json": (
        Path("artifacts/fixture-routing/v0.1/route-plan.json"),
        "2937047ed5d2c6db7f73ba7d8ba597acd24ec376cde73b5b48e529ac6cf5004c",
    ),
    "fixture_native_plan_json": (
        Path("artifacts/fixture-native-plan/v0.1/full-plan.json"),
        "08c8a3e972950cd2be88f86a4f79d9727c39f81e12a321c50cb3a46581534436",
    ),
    "f0h_runtime_acceptance_json": (
        Path("artifacts/f0h-ppocrv6-runtime/v0.1/acceptance.json"),
        "0d25e1ec9addfa0d24d85523ebd621747835ea40f00d44090c5632dc4676093b",
    ),
}


class RebuildError(RuntimeError):
    """A body-free clean-rebuild failure with one fixed reason code."""

    def __init__(self, code: str) -> None:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", code):
            code = "INTERNAL_FAILURE"
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class RoundIdentity:
    round_number: int
    suffix: str
    project: str
    database: str
    pg_container: str
    pg_volume: str

    @classmethod
    def create(
        cls,
        round_number: int,
        *,
        uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    ) -> "RoundIdentity":
        if round_number not in {1, 2}:
            raise RebuildError("ROUND_REJECTED")
        value = uuid_factory()
        if not isinstance(value, uuid.UUID) or value.version != 4:
            raise RebuildError("RANDOM_SCOPE_REJECTED")
        suffix = value.hex
        project = PROJECT_PREFIX + suffix
        return cls(
            round_number=round_number,
            suffix=suffix,
            project=project,
            database=DATABASE_PREFIX + suffix,
            pg_container=project + "-postgres",
            pg_volume=project + "-postgres-data",
        )


@dataclass(frozen=True, slots=True)
class ProcessResult:
    exit_code: int
    output: bytes


@dataclass(frozen=True, slots=True)
class SourceEntry:
    relative: Path
    mode: int
    sha256: str
    size: int


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    entries: tuple[SourceEntry, ...]
    sha256: str
    repository_state_sha256: str


@dataclass(frozen=True, slots=True)
class DatabaseEndpoint:
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SourceContainerIdentity:
    container_id: str
    container_name: str
    compose_project: str
    compose_service: str
    image_id: str
    image_reference: str
    published_port: int


@dataclass(frozen=True, slots=True)
class SourceScope:
    host: str
    published_port: int
    database: str
    access: str
    container: SourceContainerIdentity


@dataclass(frozen=True, slots=True)
class F0GSourceScope:
    database: str
    role: str
    schemas: tuple[str, ...]
    access: str
    read_only: bool
    container: SourceContainerIdentity
    dump_sha256: str
    aggregate_sha256: str


@dataclass(frozen=True, slots=True)
class PrivateFileIdentity:
    device: int
    inode: int
    mode: int
    uid: int
    links: int
    size: int
    modified_ns: int
    changed_ns: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ExecutableIdentity:
    path: Path
    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    changed_ns: int
    sha256: str


@dataclass(frozen=True, slots=True)
class CheckoutIdentity:
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class SourceObjectSpec:
    source_id: str
    group: str
    line: int
    sha256: str
    size: int
    offset: int
    relative: Path = field(repr=False)


@dataclass(frozen=True, slots=True)
class FixtureSourceMaterialization:
    root: Path = field(repr=False)
    root_identity: CheckoutIdentity
    bundle_identity: PrivateFileIdentity
    objects: tuple[tuple[SourceObjectSpec, PrivateFileIdentity], ...]


@dataclass(frozen=True, slots=True)
class RuntimeTreeEntry:
    relative: Path = field(repr=False)
    source_mode: int
    sha256: str
    size: int
    offset: int


@dataclass(frozen=True, slots=True)
class FrozenRuntimeTreeMaterialization:
    phase: str
    root: Path = field(repr=False)
    root_identity: CheckoutIdentity
    bundle_identity: PrivateFileIdentity
    tree_sha256: str
    entries: tuple[RuntimeTreeEntry, ...]
    files: tuple[tuple[Path, PrivateFileIdentity], ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class FrozenF0PreparedInputs:
    isolation: FrozenF0Isolation
    config_path: Path = field(repr=False)
    config_identity: PrivateFileIdentity
    runtime_root_identity: CheckoutIdentity
    source_key_identity: PrivateFileIdentity
    target_key_identity: PrivateFileIdentity
    dsn_identities: tuple[tuple[str, PrivateFileIdentity], ...]
    fixture_source: FixtureSourceMaterialization
    runtime_trees: tuple[FrozenRuntimeTreeMaterialization, ...]


@dataclass(frozen=True, slots=True)
class FrozenF0DatabaseSnapshot:
    rows: tuple[tuple[str, int, str], ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class FixtureInput:
    path: Path = field(repr=False)
    sha256: str
    size: int
    device: int
    inode: int
    content_type: str


@dataclass(frozen=True, slots=True)
class FixtureSelectionCopy:
    source: Path = field(repr=False)
    source_identity: PrivateFileIdentity
    target: Path = field(repr=False)
    target_identity: PrivateFileIdentity
    sha256: str
    content_type: str


@dataclass(frozen=True, slots=True)
class RoundSummary:
    source_sha256: str
    fixture_source_sha256: str
    fixture_e2e_sha256: str
    schema_sha256: str
    pg_contract_sha256: str
    runtime_inventory_sha256: str
    service_count: int
    evidence_captured: bool
    cleanup_residuals: int

    def result_sha256(self) -> str:
        if not self.evidence_captured:
            raise RebuildError("EVIDENCE_NOT_CAPTURED")
        if self.cleanup_residuals != 0:
            raise RebuildError("CLEANUP_RESIDUALS")
        return normalized_result(
            source_sha256=self.source_sha256,
            fixture_source_sha256=self.fixture_source_sha256,
            fixture_e2e_sha256=self.fixture_e2e_sha256,
            schema_sha256=self.schema_sha256,
            pg_contract_sha256=self.pg_contract_sha256,
            runtime_inventory_sha256=self.runtime_inventory_sha256,
            service_count=self.service_count,
        )


@dataclass(slots=True)
class ResourceState:
    scratch_root: Path | None = None
    runtime_home: Path | None = None
    runtime_temporary: Path | None = None
    seed_repository: Path | None = None
    checkout: Path | None = None
    secrets_directory: Path | None = None
    provider_secrets_directory: Path | None = None
    f0i_key_file: Path | None = None
    migration_environment_file: Path | None = None
    f1_environment_file: Path | None = None
    target_database_environment_file: Path | None = None
    f0g_database_environment_file: Path | None = None
    f0g_migration_environment_file: Path | None = None
    f0i_database_environment_file: Path | None = None
    source_data_dump: Path | None = None
    source_data_after_dump: Path | None = None
    target_data_dump: Path | None = None
    f0g_source_data_dump: Path | None = None
    f0g_source_data_after_dump: Path | None = None
    f0g_target_data_dump: Path | None = None
    f0g_target_data_after_dump: Path | None = None
    f0i_template_data_dump: Path | None = None
    f0i_template_data_after_dump: Path | None = None
    compose_started: bool = False
    pg_volume_created: bool = False
    pg_container_created: bool = False
    local_images_created: bool = False
    evidence_captured: bool = False
    reservations: dict[str, socket.socket] = field(default_factory=dict)


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def build_provenance(root: Path, source_snapshot_sha256: str) -> dict[str, str]:
    """Hash the exact source/build inputs passed into every local image.

    Dockerfile bytes are hashed outside the Dockerfile so the label value does
    not become a self-referential build input.  Both local images carry the
    complete set, even when one lock is not consumed by that image directly.
    """

    if not _HEX64.fullmatch(source_snapshot_sha256):
        raise RebuildError("BUILD_PROVENANCE_REJECTED")
    raw_files: dict[str, bytes] = {}
    for relative in (
        "infra/f1/Dockerfile",
        "infra/f1/web.Dockerfile",
        "requirements/requirements-f1.lock",
        "src/web/package-lock.json",
    ):
        path = root / relative
        try:
            metadata = path.lstat()
            raw = path.read_bytes()
        except OSError:
            raise RebuildError("BUILD_PROVENANCE_REJECTED") from None
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_nlink != 1
            or not raw
            or len(raw) > MAX_SOURCE_FILE
        ):
            raise RebuildError("BUILD_PROVENANCE_REJECTED")
        raw_files[relative] = raw
    dockerfile_set = [
        {
            "path": relative,
            "sha256": _sha256(raw_files[relative]),
            "size": len(raw_files[relative]),
        }
        for relative in ("infra/f1/Dockerfile", "infra/f1/web.Dockerfile")
    ]
    result = {
        "source_snapshot_sha256": source_snapshot_sha256,
        "dockerfile_set_sha256": _sha256(_canonical_bytes(dockerfile_set)),
        "python_lock_sha256": _sha256(
            raw_files["requirements/requirements-f1.lock"]
        ),
        "npm_lock_sha256": _sha256(raw_files["src/web/package-lock.json"]),
    }
    if set(result) != set(BUILD_PROVENANCE_LABELS) or any(
        not _HEX64.fullmatch(value) for value in result.values()
    ):
        raise RebuildError("BUILD_PROVENANCE_REJECTED")
    return result


def validate_build_provenance_labels(
    labels: Mapping[str, Any], expected: Mapping[str, str]
) -> None:
    if set(expected) != set(BUILD_PROVENANCE_LABELS) or any(
        not _HEX64.fullmatch(value) for value in expected.values()
    ):
        raise RebuildError("BUILD_PROVENANCE_REJECTED")
    if not isinstance(labels, Mapping):
        raise RebuildError("BUILD_PROVENANCE_LABEL_RED")
    for key, label in BUILD_PROVENANCE_LABELS.items():
        if labels.get(label) != expected[key]:
            raise RebuildError("BUILD_PROVENANCE_LABEL_RED")
    if labels.get("org.opencontainers.image.revision") != expected[
        "source_snapshot_sha256"
    ]:
        raise RebuildError("BUILD_PROVENANCE_LABEL_RED")


def parse_round(value: str | None) -> int:
    if value not in {"1", "2"}:
        raise RebuildError("ROUND_REJECTED")
    return int(value)


def normalized_result(
    *,
    source_sha256: str,
    fixture_source_sha256: str,
    fixture_e2e_sha256: str,
    schema_sha256: str,
    pg_contract_sha256: str,
    runtime_inventory_sha256: str,
    service_count: int,
) -> str:
    if (
        not _HEX64.fullmatch(source_sha256)
        or not _HEX64.fullmatch(fixture_source_sha256)
        or not _HEX64.fullmatch(fixture_e2e_sha256)
        or not _HEX64.fullmatch(schema_sha256)
        or not _HEX64.fullmatch(pg_contract_sha256)
        or not _HEX64.fullmatch(runtime_inventory_sha256)
        or service_count != len(EXPECTED_SERVICES)
    ):
        raise RebuildError("NORMALIZED_RESULT_REJECTED")
    fields = {
        "source": {"snapshot_sha256": source_sha256},
        "f0i": {"fixture_source_sha256": fixture_source_sha256},
        "e2e": {"fixture_sha256": fixture_e2e_sha256},
        "schema": {
            "sha256": schema_sha256,
            "root_head": "f0d_0006",
            "f1_head": "f1_0004",
        },
        "pg": {"contract_sha256": pg_contract_sha256},
        "runtime": {"inventory_sha256": runtime_inventory_sha256},
        "service": {"count": service_count},
        "cleanup": {"residuals": 0},
    }
    return _sha256(_canonical_bytes(fields))


def round_evidence_document(summary: RoundSummary, round_number: int) -> dict[str, Any]:
    if round_number not in CLEAN_EVIDENCE_NAMES:
        raise RebuildError("ROUND_REJECTED")
    result = summary.result_sha256()
    document: dict[str, Any] = {
        "evidence_schema": CLEAN_EVIDENCE_SCHEMA,
        "round": round_number,
        "source": {"snapshot_sha256": summary.source_sha256},
        "f0i": {"fixture_source_sha256": summary.fixture_source_sha256},
        "e2e": {"fixture_sha256": summary.fixture_e2e_sha256},
        "schema": {
            "sha256": summary.schema_sha256,
            "root_head": "f0d_0006",
            "f1_head": "f1_0004",
        },
        "pg": {"contract_sha256": summary.pg_contract_sha256},
        "runtime": {"inventory_sha256": summary.runtime_inventory_sha256},
        "service": {"count": summary.service_count},
        "cleanup": {"residuals": summary.cleanup_residuals},
        "result": {"sha256": result},
    }
    validate_round_evidence(document, round_number)
    return document


def validate_round_evidence(document: Mapping[str, Any], round_number: int) -> str:
    expected_keys = {
        "evidence_schema",
        "round",
        "source",
        "f0i",
        "e2e",
        "schema",
        "pg",
        "runtime",
        "service",
        "cleanup",
        "result",
    }
    if (
        not isinstance(document, Mapping)
        or set(document) != expected_keys
        or document.get("evidence_schema") != CLEAN_EVIDENCE_SCHEMA
        or document.get("round") != round_number
    ):
        raise RebuildError("CLEAN_EVIDENCE_REJECTED")
    try:
        source = document["source"]
        f0i = document["f0i"]
        e2e = document["e2e"]
        schema = document["schema"]
        pg = document["pg"]
        runtime = document["runtime"]
        service = document["service"]
        cleanup = document["cleanup"]
        result = document["result"]
        if (
            not isinstance(source, Mapping)
            or set(source) != {"snapshot_sha256"}
            or not isinstance(f0i, Mapping)
            or set(f0i) != {"fixture_source_sha256"}
            or not isinstance(e2e, Mapping)
            or set(e2e) != {"fixture_sha256"}
            or not isinstance(schema, Mapping)
            or set(schema) != {"sha256", "root_head", "f1_head"}
            or schema.get("root_head") != "f0d_0006"
            or schema.get("f1_head") != "f1_0004"
            or not isinstance(pg, Mapping)
            or set(pg) != {"contract_sha256"}
            or not isinstance(runtime, Mapping)
            or set(runtime) != {"inventory_sha256"}
            or not isinstance(service, Mapping)
            or set(service) != {"count"}
            or not isinstance(cleanup, Mapping)
            or set(cleanup) != {"residuals"}
            or cleanup.get("residuals") != 0
            or not isinstance(result, Mapping)
            or set(result) != {"sha256"}
        ):
            raise RebuildError("CLEAN_EVIDENCE_REJECTED")
        calculated = normalized_result(
            source_sha256=str(source["snapshot_sha256"]),
            fixture_source_sha256=str(f0i["fixture_source_sha256"]),
            fixture_e2e_sha256=str(e2e["fixture_sha256"]),
            schema_sha256=str(schema["sha256"]),
            pg_contract_sha256=str(pg["contract_sha256"]),
            runtime_inventory_sha256=str(runtime["inventory_sha256"]),
            service_count=int(service["count"]),
        )
    except (KeyError, TypeError, ValueError):
        raise RebuildError("CLEAN_EVIDENCE_REJECTED") from None
    if result.get("sha256") != calculated:
        raise RebuildError("CLEAN_EVIDENCE_BINDING_RED")
    return calculated


def _parent_temporary(environment: Mapping[str, str]) -> Path:
    raw = environment.get("TMPDIR", "")
    path = Path(raw)
    try:
        metadata = path.lstat()
    except OSError:
        raise RebuildError("CLEAN_EVIDENCE_TMP_REJECTED") from None
    if (
        not raw
        or not path.is_absolute()
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.geteuid()
    ):
        raise RebuildError("CLEAN_EVIDENCE_TMP_REJECTED")
    return path


def write_round_evidence(
    temporary: Path, document: Mapping[str, Any], round_number: int
) -> Path:
    marker = validate_round_evidence(document, round_number)
    if not _HEX64.fullmatch(marker):
        raise RebuildError("CLEAN_EVIDENCE_BINDING_RED")
    name = CLEAN_EVIDENCE_NAMES[round_number]
    target = temporary / name
    staging = temporary / ("." + name + ".staging")
    if target.exists() or target.is_symlink() or staging.exists() or staging.is_symlink():
        raise RebuildError("CLEAN_EVIDENCE_TARGET_OCCUPIED")
    raw = _canonical_bytes(document)
    descriptor = -1
    published = False
    try:
        descriptor = os.open(
            staging,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(staging, target, follow_symlinks=False)
        published = True
        staging.unlink()
        metadata = target.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or target.read_bytes() != raw
        ):
            raise RebuildError("CLEAN_EVIDENCE_WRITE_RED")
    except RebuildError:
        staging.unlink(missing_ok=True)
        if published:
            target.unlink(missing_ok=True)
        raise
    except OSError:
        staging.unlink(missing_ok=True)
        if published:
            target.unlink(missing_ok=True)
        raise RebuildError("CLEAN_EVIDENCE_WRITE_RED") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return target


def runtime_inventory_digest(
    *,
    actual_images: Mapping[str, str],
    declared_provenance: Mapping[str, str],
    base_images: Sequence[str],
    lock_sha256: Mapping[str, str],
    build_provenance: Mapping[str, str],
) -> str:
    if (
        set(actual_images) != RUNTIME_SERVICES
        or set(declared_provenance) != RUNTIME_SERVICES
        or not base_images
        or set(lock_sha256) != {"python", "npm"}
        or set(build_provenance) != set(BUILD_PROVENANCE_LABELS)
    ):
        raise RebuildError("RUNTIME_INVENTORY_REJECTED")
    for image_id in actual_images.values():
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise RebuildError("RUNTIME_INVENTORY_REJECTED")
    for reference in declared_provenance.values():
        if not isinstance(reference, str) or not reference:
            raise RebuildError("RUNTIME_INVENTORY_REJECTED")
        if not reference.startswith("local:") and "@sha256:" not in reference:
            raise RebuildError("RUNTIME_INVENTORY_REJECTED")
    if any("@sha256:" not in reference for reference in base_images):
        raise RebuildError("RUNTIME_INVENTORY_REJECTED")
    if any(not _HEX64.fullmatch(value) for value in lock_sha256.values()):
        raise RebuildError("RUNTIME_INVENTORY_REJECTED")
    if any(not _HEX64.fullmatch(value) for value in build_provenance.values()):
        raise RebuildError("RUNTIME_INVENTORY_REJECTED")
    if (
        lock_sha256["python"] != build_provenance["python_lock_sha256"]
        or lock_sha256["npm"] != build_provenance["npm_lock_sha256"]
    ):
        raise RebuildError("RUNTIME_INVENTORY_REJECTED")
    payload = {
        "schema": "f1.1.1-runtime-inventory-v2",
        "actual_service_images": {
            name: actual_images[name] for name in sorted(actual_images)
        },
        "declared_provenance": {
            name: declared_provenance[name]
            for name in sorted(declared_provenance)
        },
        "base_images": sorted(base_images),
        "lock_sha256": {
            name: lock_sha256[name] for name in sorted(lock_sha256)
        },
        "build_provenance": {
            name: build_provenance[name] for name in sorted(build_provenance)
        },
    }
    return _sha256(_canonical_bytes(payload))


def parse_zero_metric_line(raw: bytes, expected: Sequence[str]) -> dict[str, int]:
    expected_tuple = tuple(expected)
    expected_set = set(expected_tuple)
    if (
        not expected_tuple
        or len(expected_tuple) != len(expected_set)
        or any(not re.fullmatch(r"[a-z][a-z0-9_]*", name) for name in expected_tuple)
    ):
        raise RebuildError("PG_CONTRACT_REJECTED")
    candidates: list[dict[str, int]] = []
    for line in raw.splitlines():
        pairs = _PAIR.findall(line)
        if not pairs:
            continue
        names = [name.decode("ascii") for name, _value in pairs]
        if set(names) & expected_set:
            if len(names) != len(set(names)):
                raise RebuildError("PG_CONTRACT_REJECTED")
            values = {
                name.decode("ascii"): int(value)
                for name, value in pairs
            }
            if set(values) != expected_set:
                raise RebuildError("PG_CONTRACT_REJECTED")
            candidates.append(values)
    if len(candidates) != 1 or any(candidates[0][name] != 0 for name in expected_tuple):
        raise RebuildError("PG_CONTRACT_RED")
    return candidates[0]


def expected_local_image(identity: RoundIdentity, service: str) -> str | None:
    if service in {"api", "keycloak-provisioner"}:
        return f"anhuan-f111-repair-api:{identity.project}"
    if service in {"worker", "dispatcher"}:
        return f"anhuan-f111-repair-worker:{identity.project}"
    if service == "web":
        return f"anhuan-f111-repair-web:{identity.project}"
    return None


def _labels(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items()}
    if isinstance(value, list):
        result: dict[str, str] = {}
        for raw in value:
            key, separator, item = str(raw).partition("=")
            if not separator or not key or key in result:
                raise RebuildError("COMPOSE_SCOPE_REJECTED")
            result[key] = item
        return result
    raise RebuildError("COMPOSE_SCOPE_REJECTED")


def _service_environment(value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items()}
    if isinstance(value, list):
        result: dict[str, str] = {}
        for raw in value:
            key, separator, item = str(raw).partition("=")
            if not separator or not key or key in result:
                raise RebuildError("COMPOSE_SCOPE_REJECTED")
            result[key] = item
        return result
    raise RebuildError("COMPOSE_SCOPE_REJECTED")


def validate_compose_payload(
    payload: Any,
    identity: RoundIdentity,
    postgres_port: int,
    *,
    expected_ports: Mapping[str, int] | None = None,
) -> int:
    if not isinstance(payload, dict) or payload.get("name") != identity.project:
        raise RebuildError("COMPOSE_PROJECT_REJECTED")
    services = payload.get("services")
    if not isinstance(services, dict) or set(services) != EXPECTED_SERVICES:
        raise RebuildError("COMPOSE_SERVICE_SET_REJECTED")
    published: set[int] = set()
    observed_bindings: dict[tuple[str, int], int] = {}
    for name, raw_service in services.items():
        if not isinstance(raw_service, dict) or raw_service.get("container_name"):
            raise RebuildError("COMPOSE_SCOPE_REJECTED")
        labels = _labels(raw_service.get("labels"))
        if (
            labels.get("anhuan.scope") != "f111-repair"
            or labels.get("anhuan.repair-project") != identity.project
            or str(raw_service.get("restart", "no")) != "no"
        ):
            raise RebuildError("COMPOSE_SCOPE_REJECTED")
        image = str(raw_service.get("image", ""))
        local = expected_local_image(identity, str(name))
        if local is not None:
            if image != local:
                raise RebuildError("COMPOSE_IMAGE_REJECTED")
        elif "@sha256:" not in image:
            raise RebuildError("COMPOSE_IMAGE_REJECTED")
        for raw_port in raw_service.get("ports") or ():
            if not isinstance(raw_port, dict):
                raise RebuildError("COMPOSE_PORT_REJECTED")
            try:
                port = int(raw_port.get("published"))
            except (TypeError, ValueError):
                raise RebuildError("COMPOSE_PORT_REJECTED") from None
            if (
                str(raw_port.get("host_ip", "")) != "127.0.0.1"
                or not 20000 <= port <= 60999
                or port == postgres_port
                or port in published
            ):
                raise RebuildError("COMPOSE_PORT_REJECTED")
            published.add(port)
            try:
                target = int(raw_port.get("target"))
            except (TypeError, ValueError):
                raise RebuildError("COMPOSE_PORT_REJECTED") from None
            binding = (str(name), target)
            if binding in observed_bindings:
                raise RebuildError("COMPOSE_PORT_REJECTED")
            observed_bindings[binding] = port
    if len(published) != len(PORT_NAMES) - 1:
        raise RebuildError("COMPOSE_PORT_REJECTED")
    if set(observed_bindings) != set(PUBLISHED_PORTS.values()):
        raise RebuildError("COMPOSE_PORT_REJECTED")
    if expected_ports is not None:
        if set(expected_ports) != set(PORT_NAMES):
            raise RebuildError("COMPOSE_PORT_REJECTED")
        for name, binding in PUBLISHED_PORTS.items():
            if observed_bindings.get(binding) != expected_ports[name]:
                raise RebuildError("COMPOSE_PORT_REJECTED")
        if postgres_port != expected_ports["postgres"]:
            raise RebuildError("COMPOSE_PORT_REJECTED")
    for name in ("api", "worker", "dispatcher"):
        environment = _service_environment(services[name].get("environment"))
        if (
            environment.get("F1_PG_HOST") != "host.docker.internal"
            or environment.get("F1_PG_PORT") != str(postgres_port)
            or environment.get("F1_PG_DATABASE") != identity.database
        ):
            raise RebuildError("COMPOSE_DATABASE_REJECTED")
    volumes = payload.get("volumes")
    if not isinstance(volumes, dict) or set(volumes) != EXPECTED_VOLUMES:
        raise RebuildError("COMPOSE_VOLUME_SET_REJECTED")
    for raw in volumes.values():
        if not isinstance(raw, dict):
            raise RebuildError("COMPOSE_VOLUME_SET_REJECTED")
        name = str(raw.get("name", ""))
        if not name.startswith(identity.project + "_"):
            raise RebuildError("COMPOSE_VOLUME_SET_REJECTED")
    networks = payload.get("networks")
    if not isinstance(networks, dict) or set(networks) != {"f1net"}:
        raise RebuildError("COMPOSE_NETWORK_REJECTED")
    network = networks["f1net"]
    if (
        not isinstance(network, dict)
        or str(network.get("name", "")) != identity.project + "_f1net"
    ):
        raise RebuildError("COMPOSE_NETWORK_REJECTED")
    return len(services)


def assert_owned_resource(value: str, identity: RoundIdentity) -> None:
    allowed = {
        identity.pg_container,
        identity.pg_volume,
        identity.project + "_f1net",
        *(identity.project + "_" + name for name in EXPECTED_VOLUMES),
        f"anhuan-f111-repair-api:{identity.project}",
        f"anhuan-f111-repair-worker:{identity.project}",
        f"anhuan-f111-repair-web:{identity.project}",
    }
    if value not in allowed:
        raise RebuildError("CLEANUP_TARGET_REJECTED")


def untracked_delivery_allowed(relative: Path) -> bool:
    value = relative.as_posix()
    lower_parts = {part.lower() for part in relative.parts}
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or any(part.startswith(".env") for part in relative.parts)
        or "secrets" in lower_parts
        or any(
            part.lower().endswith((".key", ".pem", ".p12", ".pfx"))
            for part in relative.parts
        )
    ):
        return False
    if value in {
        ".dockerignore",
        ".gitignore",
        "F1_1_1_REPAIR_TASKBOOK.md",
        "F1_1_1_REPAIR_PROGRESS.md",
        "F1_1_1_REPAIR_BLOCKED.md",
        "artifacts/f1-platform-shell/v0.2/revocation.json",
    }:
        return True
    if value.startswith("infra/f1/"):
        return True
    if value.startswith("src/platform_foundation/f1/"):
        return True
    if value.startswith("src/web/"):
        return True
    if value == "requirements/requirements-f1.lock":
        return True
    if value in {
        "src/platform_foundation/f0_isolation.py",
        "src/fixture_router/router.py",
        "src/platform_foundation/f0e/acceptance.py",
        "src/platform_foundation/f0e/runtime_config.py",
        "src/platform_foundation/f0e/supervisor.py",
        "src/platform_foundation/f0f/acceptance.py",
        "src/platform_foundation/f0f/keyfile.py",
        "src/platform_foundation/f0f/runtime_config.py",
        "src/platform_foundation/f0f/supervisor.py",
        "src/platform_foundation/f0g/config.py",
        "src/platform_foundation/f0g/tokens.py",
        "src/platform_foundation/f0h/runtime_config.py",
        "src/platform_foundation/f0h/supervisor.py",
        "src/platform_foundation/f0i/config.py",
        "src/platform_foundation/f0i/bootstrap.py",
        "src/platform_foundation/f0i/keyfile.py",
        "src/platform_foundation/f0i/locking.py",
    }:
        return True
    if value in {
        "tests/test_f111_f0_isolation.py",
        "tests/test_platform_foundation.py",
        "tests/test_f0e_local_ocr.py",
        "tests/test_f0f_controlled_body_gold.py",
        "tests/test_f0g_fixture_annotation.py",
        "tests/test_f0h_ppocrv6_runtime.py",
        "tests/test_f0i_canonical_chunks.py",
        "tests/test_f0j0_ragflow_probe.py",
        "tests/test_f0j0_retrieval_probe.py",
        "tests/test_f0j1_retrieval_qa.py",
    }:
        return True
    if relative.parent == Path("tests"):
        name = relative.name
        return bool(
            name in {"f11_support.py", "f11_reverse_verify.py"}
            or re.fullmatch(r"(?:test_)?f11?1?_[A-Za-z0-9_]+\.py", name)
            or re.fullmatch(r"f111_[A-Za-z0-9_]+\.py", name)
        )
    return False


_FORMAL_V03_OUTPUT_PARTS = (
    "artifacts",
    "f1-platform-shell",
    "v0.3",
)


def _formal_v03_output(relative: Path) -> bool:
    """Keep public acceptance authority outside the build source authority."""

    return (
        not relative.is_absolute()
        and ".." not in relative.parts
        and relative.parts[: len(_FORMAL_V03_OUTPUT_PARTS)]
        == _FORMAL_V03_OUTPUT_PARTS
        and len(relative.parts) > len(_FORMAL_V03_OUTPUT_PARTS)
    )


def _base_environment(source: Mapping[str, str]) -> dict[str, str]:
    unsafe = (
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "COMPOSE_FILE",
        "COMPOSE_PROJECT_NAME",
        "COMPOSE_PROFILES",
        "COMPOSE_ENV_FILES",
    )
    if any(source.get(name) for name in unsafe):
        raise RebuildError("DOCKER_CONTEXT_REJECTED")
    environment = {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": source.get(
            "PATH",
            "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        ),
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_ATTR_NOSYSTEM": "1",
    }
    for name in ("HOME", "TMPDIR"):
        raw = source.get(name, "")
        if not raw:
            continue
        path = Path(raw)
        try:
            info = path.lstat()
        except OSError:
            raise RebuildError("RUNTIME_DIRECTORY_REJECTED") from None
        if (
            not path.is_absolute()
            or not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.geteuid()
        ):
            raise RebuildError("RUNTIME_DIRECTORY_REJECTED")
        environment[name] = str(path)
    return environment


def _process(
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout: int,
    input_bytes: bytes | None = None,
    check: bool = True,
    failure_code: str = "SUBPROCESS_RED",
) -> ProcessResult:
    if (
        not arguments
        or any(not isinstance(item, str) or "\x00" in item for item in arguments)
        or not cwd.is_absolute()
    ):
        raise RebuildError("COMMAND_REJECTED")
    if tuple(arguments[:2]) == ("docker", "exec"):
        interactive = len(arguments) > 2 and arguments[2] in {
            "-i",
            "--interactive",
        }
        if (
            interactive != (input_bytes is not None)
            or any(item in {"-t", "--tty", "-it", "-ti"} for item in arguments[2:])
        ):
            raise RebuildError("COMMAND_REJECTED")
    compose_identity = (
        _compose_plugin_identity()
        if tuple(arguments[:2]) == ("docker", "compose")
        else None
    )
    try:
        completed = subprocess.run(
            list(arguments),
            cwd=str(cwd),
            env=dict(environment),
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        if compose_identity is not None:
            if _compose_plugin_identity() != compose_identity:
                raise RebuildError("COMPOSE_PLUGIN_REJECTED") from None
        raise RebuildError("COMMAND_TIMEOUT") from None
    except OSError:
        if compose_identity is not None:
            if _compose_plugin_identity() != compose_identity:
                raise RebuildError("COMPOSE_PLUGIN_REJECTED") from None
        raise RebuildError("COMMAND_UNAVAILABLE") from None
    if compose_identity is not None and _compose_plugin_identity() != compose_identity:
        raise RebuildError("COMPOSE_PLUGIN_REJECTED")
    output = bytes(completed.stdout or b"")
    if len(output) > MAX_PROCESS_OUTPUT:
        raise RebuildError("COMMAND_OUTPUT_LIMIT")
    result = ProcessResult(int(completed.returncode), output)
    if check and result.exit_code != 0:
        raise RebuildError(failure_code)
    return result


def _process_to_private_file(
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout: int,
    destination: Path,
    failure_code: str,
) -> None:
    """Run one fixed command with stdout captured in a new owner-only file.

    A source database archive can exceed the ordinary subprocess-output cap.
    It is therefore never materialized in chat-facing output or process
    memory.  The destination must be a new file in an already-private scratch
    directory and is removed on every failed attempt.
    """
    if (
        not arguments
        or any(not isinstance(item, str) or "\x00" in item for item in arguments)
        or not cwd.is_absolute()
    ):
        raise RebuildError("COMMAND_REJECTED")
    _regular_private_directory(destination.parent, "PRIVATE_OUTPUT_REJECTED")
    if destination.exists() or destination.is_symlink():
        raise RebuildError("PRIVATE_OUTPUT_REJECTED")
    descriptor = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    process: subprocess.Popen[bytes] | None = None
    try:
        with os.fdopen(descriptor, "wb") as output_stream:
            process = subprocess.Popen(
                list(arguments),
                cwd=str(cwd),
                env=dict(environment),
                stdin=subprocess.DEVNULL,
                stdout=output_stream,
                stderr=subprocess.PIPE,
            )
            try:
                _stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                raise RebuildError("COMMAND_TIMEOUT") from None
            output_stream.flush()
            os.fsync(output_stream.fileno())
        if len(stderr or b"") > MAX_PROCESS_OUTPUT:
            raise RebuildError("COMMAND_OUTPUT_LIMIT")
        if process.returncode != 0:
            raise RebuildError(failure_code)
        info = destination.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or not 0 < info.st_size <= MAX_SOURCE_DUMP
        ):
            raise RebuildError("SOURCE_DUMP_REJECTED")
    except RebuildError:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    except OSError:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise RebuildError(failure_code) from None


def _regular_private_directory(path: Path, code: str) -> Path:
    if not path.is_absolute():
        raise RebuildError(code)
    try:
        info = path.lstat()
    except OSError:
        raise RebuildError(code) from None
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.geteuid()
    ):
        raise RebuildError(code)
    return path


def _read_private_file(path: Path, code: str, maximum: int = MAX_PRIVATE_FILE) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise RebuildError(code) from None
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or info.st_size < 1
            or info.st_size > maximum
        ):
            raise RebuildError(code)
        raw = os.read(descriptor, maximum + 1)
    finally:
        os.close(descriptor)
    if len(raw) != info.st_size:
        raise RebuildError(code)
    return raw


def _private_file_identity(
    path: Path,
    *,
    code: str,
    expected_sha256: str | None,
    maximum: int = 4 * 1024 * 1024,
) -> tuple[bytes, PrivateFileIdentity]:
    """Read an owner-only source once while binding replacement-sensitive metadata."""

    if (
        not path.is_absolute()
        or (expected_sha256 is not None and not _HEX64.fullmatch(expected_sha256))
    ):
        raise RebuildError(code)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        before_path = path.lstat()
        descriptor = os.open(path, flags)
    except OSError:
        raise RebuildError(code) from None
    try:
        before_fd = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before_fd.st_mode)
            or stat.S_ISLNK(before_path.st_mode)
            or stat.S_IMODE(before_fd.st_mode) != 0o600
            or before_fd.st_uid != os.geteuid()
            or before_fd.st_nlink != 1
            or not 1 <= before_fd.st_size <= maximum
            or (before_path.st_dev, before_path.st_ino)
            != (before_fd.st_dev, before_fd.st_ino)
        ):
            raise RebuildError(code)
        raw = os.read(descriptor, maximum + 1)
        after_fd = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        after_path = path.lstat()
    except OSError:
        raise RebuildError(code) from None
    metadata = (
        int(before_fd.st_dev),
        int(before_fd.st_ino),
        stat.S_IMODE(before_fd.st_mode),
        int(before_fd.st_uid),
        int(before_fd.st_nlink),
        int(before_fd.st_size),
        int(before_fd.st_mtime_ns),
        int(before_fd.st_ctime_ns),
    )
    if (
        len(raw) != before_fd.st_size
        or metadata
        != (
            int(after_fd.st_dev),
            int(after_fd.st_ino),
            stat.S_IMODE(after_fd.st_mode),
            int(after_fd.st_uid),
            int(after_fd.st_nlink),
            int(after_fd.st_size),
            int(after_fd.st_mtime_ns),
            int(after_fd.st_ctime_ns),
        )
        or (after_path.st_dev, after_path.st_ino, after_path.st_mtime_ns, after_path.st_ctime_ns)
        != (before_fd.st_dev, before_fd.st_ino, before_fd.st_mtime_ns, before_fd.st_ctime_ns)
        or (
            expected_sha256 is not None
            and _sha256(raw) != expected_sha256
        )
    ):
        raise RebuildError(code)
    return raw, PrivateFileIdentity(*metadata, _sha256(raw))


def _checkout_directory(path: Path, code: str = "CLEAN_CHECKOUT_REJECTED") -> Path:
    if not path.is_absolute():
        raise RebuildError(code)
    try:
        info = path.lstat()
    except OSError:
        raise RebuildError(code) from None
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
    ):
        raise RebuildError(code)
    return path


def materialize_fixture_plans(
    source_directory: Path,
    checkout: Path,
) -> dict[str, PrivateFileIdentity]:
    """Copy four frozen ignored inputs into one disposable checkout only."""

    source = _regular_private_directory(source_directory, "FIXTURE_PLAN_SOURCE_REJECTED")
    checkout = _checkout_directory(checkout)
    identities: dict[str, PrivateFileIdentity] = {}
    payloads: dict[str, bytes] = {}
    for name, contract in sorted(FIXTURE_PLAN_CONTRACTS.items()):
        if (
            not re.fullmatch(r"[a-z0-9_]{1,64}", name)
            or not isinstance(contract, tuple)
            or len(contract) != 2
        ):
            raise RebuildError("FIXTURE_PLAN_CONTRACT_REJECTED")
        relative, expected_sha256 = contract
        if (
            not isinstance(relative, Path)
            or relative.is_absolute()
            or ".." in relative.parts
            or not _HEX64.fullmatch(expected_sha256)
        ):
            raise RebuildError("FIXTURE_PLAN_CONTRACT_REJECTED")
        raw, identity = _private_file_identity(
            source / name,
            code="FIXTURE_PLAN_SOURCE_REJECTED",
            expected_sha256=expected_sha256,
        )
        identities[name] = identity
        payloads[name] = raw
    for name, (relative, expected_sha256) in sorted(FIXTURE_PLAN_CONTRACTS.items()):
        target = checkout / relative
        current = checkout
        for part in relative.parts[:-1]:
            current = current / part
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                pass
            except OSError:
                raise RebuildError("FIXTURE_PLAN_WRITE_REJECTED") from None
            try:
                info = current.lstat()
            except OSError:
                raise RebuildError("FIXTURE_PLAN_WRITE_REJECTED") from None
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise RebuildError("FIXTURE_PLAN_WRITE_REJECTED")
        try:
            _write_private(target, payloads[name])
            raw = target.read_bytes()
            info = target.lstat()
        except RebuildError:
            raise
        except OSError:
            raise RebuildError("FIXTURE_PLAN_WRITE_REJECTED") from None
        if (
            _sha256(raw) != expected_sha256
            or not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
        ):
            raise RebuildError("FIXTURE_PLAN_WRITE_REJECTED")
    return identities


def verify_fixture_plan_sources(
    source_directory: Path,
    expected: Mapping[str, PrivateFileIdentity],
) -> None:
    source = _regular_private_directory(source_directory, "FIXTURE_PLAN_SOURCE_REJECTED")
    if set(expected) != set(FIXTURE_PLAN_CONTRACTS):
        raise RebuildError("FIXTURE_PLAN_SOURCE_REJECTED")
    for name, (_relative, expected_sha256) in sorted(FIXTURE_PLAN_CONTRACTS.items()):
        _raw, identity = _private_file_identity(
            source / name,
            code="FIXTURE_PLAN_SOURCE_REJECTED",
            expected_sha256=expected_sha256,
        )
        if identity != expected[name]:
            raise RebuildError("FIXTURE_PLAN_SOURCE_MUTATED")


def verify_checkout_fixture_plans(checkout: Path) -> None:
    checkout = _checkout_directory(checkout)
    for _name, (relative, expected_sha256) in sorted(FIXTURE_PLAN_CONTRACTS.items()):
        target = checkout / relative
        try:
            info = target.lstat()
            raw = target.read_bytes()
        except OSError:
            raise RebuildError("FIXTURE_PLAN_CHECKOUT_REJECTED") from None
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or _sha256(raw) != expected_sha256
        ):
            raise RebuildError("FIXTURE_PLAN_CHECKOUT_REJECTED")


def _unique_json(raw: bytes, code: str) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError("duplicate key")
        return value

    try:
        return json.loads(raw, object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise RebuildError(code) from None


def _source_manifest_entries(
    source_directory: Path,
) -> dict[tuple[str, int], tuple[Path, str]]:
    entries: dict[tuple[str, int], tuple[Path, str]] = {}
    normalized_paths: set[str] = set()
    digests: set[str] = set()
    for group, count in sorted(SOURCE_BUNDLE_GROUP_COUNTS.items()):
        name = "fixture_" + group + "_manifest"
        contract = FIXTURE_PLAN_CONTRACTS.get(name)
        if contract is None:
            raise RebuildError("SOURCE_BUNDLE_MANIFEST_REJECTED")
        raw, _identity = _private_file_identity(
            source_directory / name,
            code="SOURCE_BUNDLE_MANIFEST_REJECTED",
            expected_sha256=contract[1],
        )
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            raise RebuildError("SOURCE_BUNDLE_MANIFEST_REJECTED") from None
        if len(lines) != count or raw != ("\n".join(lines) + "\n").encode("utf-8"):
            raise RebuildError("SOURCE_BUNDLE_MANIFEST_REJECTED")
        for line_number, line in enumerate(lines, 1):
            if len(line) < 67 or line[64:66] != "  ":
                raise RebuildError("SOURCE_BUNDLE_MANIFEST_REJECTED")
            digest = line[:64]
            relative_text = line[66:]
            relative = Path(relative_text)
            normalized = unicodedata.normalize("NFC", relative_text).casefold()
            if (
                not _HEX64.fullmatch(digest)
                or not relative_text
                or "\x00" in relative_text
                or "\\" in relative_text
                or any(ord(character) < 0x20 for character in relative_text)
                or relative.is_absolute()
                or relative.as_posix() != relative_text
                or any(part in {"", ".", ".."} for part in relative.parts)
                or relative.suffix.casefold()
                not in {".pdf", ".jpg", ".jpeg", ".doc", ".docx", ".xlsx"}
                or normalized in normalized_paths
                or digest in digests
            ):
                raise RebuildError("SOURCE_BUNDLE_MANIFEST_REJECTED")
            entries[(group, line_number)] = (relative, digest)
            normalized_paths.add(normalized)
            digests.add(digest)
    if len(entries) != SOURCE_BUNDLE_ENTRY_COUNT:
        raise RebuildError("SOURCE_BUNDLE_MANIFEST_REJECTED")
    return entries


def _source_route_ids(source_directory: Path) -> dict[tuple[str, int], str]:
    contract = FIXTURE_PLAN_CONTRACTS.get("fixture_route_plan_json")
    if contract is None:
        raise RebuildError("SOURCE_BUNDLE_ROUTE_REJECTED")
    raw, _identity = _private_file_identity(
        source_directory / "fixture_route_plan_json",
        code="SOURCE_BUNDLE_ROUTE_REJECTED",
        expected_sha256=contract[1],
    )
    document = _unique_json(raw, "SOURCE_BUNDLE_ROUTE_REJECTED")
    if not isinstance(document, dict) or not isinstance(document.get("entries"), list):
        raise RebuildError("SOURCE_BUNDLE_ROUTE_REJECTED")
    values: dict[tuple[str, int], str] = {}
    for item in document["entries"]:
        if not isinstance(item, dict):
            raise RebuildError("SOURCE_BUNDLE_ROUTE_REJECTED")
        group = item.get("group")
        line = item.get("line")
        document_id = item.get("document_id")
        if (
            group not in SOURCE_BUNDLE_GROUP_COUNTS
            or type(line) is not int
            or not 1 <= line <= SOURCE_BUNDLE_GROUP_COUNTS[group]
            or not isinstance(document_id, str)
            or not _HEX64.fullmatch(document_id)
            or (group, line) in values
        ):
            raise RebuildError("SOURCE_BUNDLE_ROUTE_REJECTED")
        values[(group, line)] = str(
            uuid.uuid5(
                SOURCE_ID_NAMESPACE,
                "\0".join(
                    (FIXTURE_SET_ID, FIXTURE_SET_VERSION, group, str(line), document_id)
                ),
            )
        )
    if len(values) != SOURCE_BUNDLE_ENTRY_COUNT:
        raise RebuildError("SOURCE_BUNDLE_ROUTE_REJECTED")
    return values


def _read_exact(
    descriptor: int,
    size: int,
    digest: hashlib._Hash | None,
    code: str,
) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        try:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
        except OSError:
            raise RebuildError(code) from None
        if not chunk:
            raise RebuildError(code)
        if digest is not None:
            digest.update(chunk)
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _source_bundle_records(
    header_raw: bytes,
    source_directory: Path,
) -> tuple[SourceObjectSpec, ...]:
    document = _unique_json(header_raw, "SOURCE_BUNDLE_HEADER_REJECTED")
    if (
        not isinstance(document, dict)
        or set(document) != {"schema", "entry_count", "payload_size", "entries"}
        or document.get("schema") != SOURCE_BUNDLE_SCHEMA
        or type(document.get("entry_count")) is not int
        or document.get("entry_count") != SOURCE_BUNDLE_ENTRY_COUNT
        or type(document.get("payload_size")) is not int
        or not isinstance(document.get("entries"), list)
        or header_raw != _canonical_bytes(document)
    ):
        raise RebuildError("SOURCE_BUNDLE_HEADER_REJECTED")
    manifests = _source_manifest_entries(source_directory)
    route_ids = _source_route_ids(source_directory)
    records: list[SourceObjectSpec] = []
    offset = 0
    seen_ids: set[str] = set()
    for item in document["entries"]:
        if not isinstance(item, dict) or set(item) != {
            "source_id", "group", "line", "sha256", "size", "offset"
        }:
            raise RebuildError("SOURCE_BUNDLE_HEADER_REJECTED")
        group = item.get("group")
        line = item.get("line")
        source_id = item.get("source_id")
        digest = item.get("sha256")
        size = item.get("size")
        observed_offset = item.get("offset")
        if group not in SOURCE_BUNDLE_GROUP_COUNTS or type(line) is not int:
            raise RebuildError("SOURCE_BUNDLE_HEADER_REJECTED")
        key = (group, line)
        expected = manifests.get(key)
        if (
            expected is None
            or not isinstance(source_id, str)
            or source_id != route_ids.get(key)
            or source_id in seen_ids
            or not isinstance(digest, str)
            or digest != expected[1]
            or type(size) is not int
            or size < 1
            or type(observed_offset) is not int
            or observed_offset != offset
        ):
            raise RebuildError("SOURCE_BUNDLE_HEADER_REJECTED")
        records.append(
            SourceObjectSpec(
                source_id, group, line, digest, size, observed_offset, expected[0]
            )
        )
        seen_ids.add(source_id)
        offset += size
        if offset > MAX_SOURCE_BUNDLE_BYTES:
            raise RebuildError("SOURCE_BUNDLE_OVERSIZE")
    expected_order = sorted(records, key=lambda value: (value.group, value.line, value.source_id))
    if (
        records != expected_order
        or offset != document["payload_size"]
        or len(records) != SOURCE_BUNDLE_ENTRY_COUNT
    ):
        raise RebuildError("SOURCE_BUNDLE_ORDER_REJECTED")
    return tuple(records)


def _make_private_parent(root: Path, relative: Path) -> None:
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError:
            raise RebuildError("SOURCE_BUNDLE_WRITE_REJECTED") from None
        try:
            info = current.lstat()
        except OSError:
            raise RebuildError("SOURCE_BUNDLE_WRITE_REJECTED") from None
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.geteuid()
        ):
            raise RebuildError("SOURCE_BUNDLE_WRITE_REJECTED")


def _fixture_source_bundle(
    source_directory: Path,
    destination: Path | None,
) -> tuple[PrivateFileIdentity, tuple[SourceObjectSpec, ...], tuple[PrivateFileIdentity, ...]]:
    source = _regular_private_directory(source_directory, "SOURCE_BUNDLE_SOURCE_REJECTED")
    bundle = source / SOURCE_BUNDLE_NAME
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        path_before = bundle.lstat()
        descriptor = os.open(bundle, flags)
    except OSError:
        raise RebuildError("SOURCE_BUNDLE_SOURCE_REJECTED") from None
    written: list[PrivateFileIdentity] = []
    if destination is not None:
        if destination.exists() or destination.is_symlink() or not destination.is_absolute():
            os.close(descriptor)
            raise RebuildError("SOURCE_BUNDLE_WRITE_REJECTED")
        try:
            destination.mkdir(mode=0o700)
        except OSError:
            os.close(descriptor)
            raise RebuildError("SOURCE_BUNDLE_WRITE_REJECTED") from None
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(path_before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or not 18 <= before.st_size <= MAX_SOURCE_BUNDLE_BYTES
            or (path_before.st_dev, path_before.st_ino)
            != (before.st_dev, before.st_ino)
        ):
            raise RebuildError("SOURCE_BUNDLE_SOURCE_REJECTED")
        whole = hashlib.sha256()
        magic = _read_exact(descriptor, len(SOURCE_BUNDLE_MAGIC), whole, "SOURCE_BUNDLE_TRUNCATED")
        length_raw = _read_exact(descriptor, 8, whole, "SOURCE_BUNDLE_TRUNCATED")
        if magic != SOURCE_BUNDLE_MAGIC:
            raise RebuildError("SOURCE_BUNDLE_HEADER_REJECTED")
        header_length = struct.unpack(">Q", length_raw)[0]
        if not 2 <= header_length <= MAX_SOURCE_BUNDLE_HEADER_BYTES:
            raise RebuildError("SOURCE_BUNDLE_HEADER_REJECTED")
        header_raw = _read_exact(descriptor, header_length, whole, "SOURCE_BUNDLE_TRUNCATED")
        records = _source_bundle_records(header_raw, source)
        for record in records:
            output_descriptor = -1
            body_digest = hashlib.sha256()
            target = destination / record.relative if destination is not None else None
            if target is not None:
                _make_private_parent(destination, record.relative)
                try:
                    output_descriptor = os.open(
                        target,
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_CLOEXEC", 0),
                        0o600,
                    )
                except OSError:
                    raise RebuildError("SOURCE_BUNDLE_WRITE_REJECTED") from None
            remaining = record.size
            try:
                while remaining:
                    chunk = _read_exact(
                        descriptor,
                        min(1024 * 1024, remaining),
                        whole,
                        "SOURCE_BUNDLE_TRUNCATED",
                    )
                    body_digest.update(chunk)
                    if output_descriptor >= 0:
                        view = memoryview(chunk)
                        while view:
                            try:
                                count = os.write(output_descriptor, view)
                            except OSError:
                                raise RebuildError("SOURCE_BUNDLE_WRITE_REJECTED") from None
                            if count <= 0:
                                raise RebuildError("SOURCE_BUNDLE_WRITE_REJECTED")
                            view = view[count:]
                    remaining -= len(chunk)
                if output_descriptor >= 0:
                    os.fsync(output_descriptor)
            finally:
                if output_descriptor >= 0:
                    os.close(output_descriptor)
            if body_digest.hexdigest() != record.sha256:
                raise RebuildError("SOURCE_BUNDLE_BODY_REJECTED")
            if target is not None:
                _raw, identity = _private_file_identity(
                    target,
                    code="SOURCE_BUNDLE_WRITE_REJECTED",
                    expected_sha256=record.sha256,
                    maximum=MAX_SOURCE_BUNDLE_BYTES,
                )
                if identity.size != record.size:
                    raise RebuildError("SOURCE_BUNDLE_WRITE_REJECTED")
                written.append(identity)
        if os.read(descriptor, 1):
            raise RebuildError("SOURCE_BUNDLE_SIZE_REJECTED")
        after = os.fstat(descriptor)
    except Exception:
        if destination is not None and destination.exists() and not destination.is_symlink():
            shutil.rmtree(destination, ignore_errors=True)
        raise
    finally:
        os.close(descriptor)
    try:
        path_after = bundle.lstat()
    except OSError:
        raise RebuildError("SOURCE_BUNDLE_SOURCE_REJECTED") from None
    metadata = (
        int(before.st_dev), int(before.st_ino), stat.S_IMODE(before.st_mode),
        int(before.st_uid), int(before.st_nlink), int(before.st_size),
        int(before.st_mtime_ns), int(before.st_ctime_ns),
    )
    if (
        metadata
        != (
            int(after.st_dev), int(after.st_ino), stat.S_IMODE(after.st_mode),
            int(after.st_uid), int(after.st_nlink), int(after.st_size),
            int(after.st_mtime_ns), int(after.st_ctime_ns),
        )
        or (path_after.st_dev, path_after.st_ino, path_after.st_mtime_ns, path_after.st_ctime_ns)
        != (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns)
    ):
        raise RebuildError("SOURCE_BUNDLE_MUTATED")
    return PrivateFileIdentity(*metadata, whole.hexdigest()), records, tuple(written)


def materialize_fixture_source_bundle(
    source_directory: Path,
    destination: Path,
) -> FixtureSourceMaterialization:
    identity, records, written = _fixture_source_bundle(source_directory, destination)
    if len(written) != len(records):
        raise RebuildError("SOURCE_BUNDLE_WRITE_REJECTED")
    root_info = destination.lstat()
    materialized = FixtureSourceMaterialization(
        destination,
        CheckoutIdentity(int(root_info.st_dev), int(root_info.st_ino)),
        identity,
        tuple(zip(records, written, strict=True)),
    )
    verify_fixture_source_materialization(source_directory, materialized)
    return materialized


def copy_fixture_source_bundle(
    source_directory: Path,
    destination_directory: Path,
    expected_identity: PrivateFileIdentity,
) -> PrivateFileIdentity:
    source = _regular_private_directory(source_directory, "SOURCE_BUNDLE_SOURCE_REJECTED")
    destination = _regular_private_directory(
        destination_directory, "SOURCE_BUNDLE_WRITE_REJECTED"
    )
    source_path = source / SOURCE_BUNDLE_NAME
    target = destination / SOURCE_BUNDLE_NAME
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    output_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    input_descriptor = output_descriptor = -1
    digest = hashlib.sha256()
    try:
        input_descriptor = os.open(source_path, flags)
        before = os.fstat(input_descriptor)
        observed_metadata = (
            int(before.st_dev), int(before.st_ino), stat.S_IMODE(before.st_mode),
            int(before.st_uid), int(before.st_nlink), int(before.st_size),
            int(before.st_mtime_ns), int(before.st_ctime_ns),
        )
        if observed_metadata != (
            expected_identity.device,
            expected_identity.inode,
            expected_identity.mode,
            expected_identity.uid,
            expected_identity.links,
            expected_identity.size,
            expected_identity.modified_ns,
            expected_identity.changed_ns,
        ):
            raise RebuildError("SOURCE_BUNDLE_MUTATED")
        output_descriptor = os.open(target, output_flags, 0o600)
        remaining = before.st_size
        while remaining:
            chunk = os.read(input_descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise RebuildError("SOURCE_BUNDLE_MUTATED")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                count = os.write(output_descriptor, view)
                if count <= 0:
                    raise RebuildError("SOURCE_BUNDLE_WRITE_REJECTED")
                view = view[count:]
            remaining -= len(chunk)
        if os.read(input_descriptor, 1):
            raise RebuildError("SOURCE_BUNDLE_MUTATED")
        os.fsync(output_descriptor)
        after = os.fstat(input_descriptor)
        if (
            observed_metadata
            != (
                int(after.st_dev), int(after.st_ino), stat.S_IMODE(after.st_mode),
                int(after.st_uid), int(after.st_nlink), int(after.st_size),
                int(after.st_mtime_ns), int(after.st_ctime_ns),
            )
            or digest.hexdigest() != expected_identity.sha256
        ):
            raise RebuildError("SOURCE_BUNDLE_MUTATED")
    except RebuildError:
        target.unlink(missing_ok=True)
        raise
    except OSError:
        target.unlink(missing_ok=True)
        raise RebuildError("SOURCE_BUNDLE_WRITE_REJECTED") from None
    finally:
        if input_descriptor >= 0:
            os.close(input_descriptor)
        if output_descriptor >= 0:
            os.close(output_descriptor)
    copied_identity, _records, _written = _fixture_source_bundle(destination, None)
    return copied_identity


def verify_fixture_source_materialization(
    source_directory: Path,
    materialized: FixtureSourceMaterialization,
) -> None:
    identity, records, written = _fixture_source_bundle(source_directory, None)
    if written or identity != materialized.bundle_identity or records != tuple(
        record for record, _file_identity in materialized.objects
    ):
        raise RebuildError("SOURCE_BUNDLE_MUTATED")
    root = _regular_private_directory(materialized.root, "SOURCE_BUNDLE_ROOT_MUTATED")
    root_info = root.lstat()
    if CheckoutIdentity(int(root_info.st_dev), int(root_info.st_ino)) != materialized.root_identity:
        raise RebuildError("SOURCE_BUNDLE_ROOT_MUTATED")
    expected_files: set[Path] = set()
    expected_directories: set[Path] = {Path(".")}
    for record, expected_identity in materialized.objects:
        target = root / record.relative
        _raw, observed = _private_file_identity(
            target,
            code="SOURCE_BUNDLE_ROOT_MUTATED",
            expected_sha256=record.sha256,
            maximum=MAX_SOURCE_BUNDLE_BYTES,
        )
        if observed != expected_identity or observed.size != record.size:
            raise RebuildError("SOURCE_BUNDLE_ROOT_MUTATED")
        expected_files.add(record.relative)
        for index in range(1, len(record.relative.parts)):
            expected_directories.add(Path(*record.relative.parts[:index]))
    for current_raw, directories, files in os.walk(root, followlinks=False):
        current = Path(current_raw)
        relative_dir = current.relative_to(root)
        if relative_dir not in expected_directories:
            raise RebuildError("SOURCE_BUNDLE_ROOT_MUTATED")
        for name in directories:
            child = current / name
            relative = child.relative_to(root)
            info = child.lstat()
            if (
                relative not in expected_directories
                or not stat.S_ISDIR(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o700
                or info.st_uid != os.geteuid()
            ):
                raise RebuildError("SOURCE_BUNDLE_ROOT_MUTATED")
        for name in files:
            if (current / name).relative_to(root) not in expected_files:
                raise RebuildError("SOURCE_BUNDLE_ROOT_MUTATED")


def _runtime_tree_entries(
    raw: bytes, phase: str
) -> tuple[str, tuple[RuntimeTreeEntry, ...], int]:
    document = _unique_json(raw, "RUNTIME_TREE_HEADER_REJECTED")
    if (
        not isinstance(document, dict)
        or set(document)
        != {
            "schema",
            "phase",
            "entry_count",
            "payload_size",
            "tree_sha256",
            "entries",
        }
        or document.get("schema") != RUNTIME_TREE_BUNDLE_SCHEMA
        or document.get("phase") != phase
        or _canonical_bytes(document) != raw
    ):
        raise RebuildError("RUNTIME_TREE_HEADER_REJECTED")
    supplied_tree_sha256 = document.get("tree_sha256")
    payload_size = document.get("payload_size")
    values = document.get("entries")
    if (
        not isinstance(supplied_tree_sha256, str)
        or not _HEX64.fullmatch(supplied_tree_sha256)
        or isinstance(payload_size, bool)
        or not isinstance(payload_size, int)
        or payload_size < 1
        or not isinstance(values, list)
        or not values
        or isinstance(document.get("entry_count"), bool)
        or document.get("entry_count") != len(values)
    ):
        raise RebuildError("RUNTIME_TREE_HEADER_REJECTED")
    entries: list[RuntimeTreeEntry] = []
    tree_values: list[dict[str, object]] = []
    offset = 0
    names: set[str] = set()
    for value in values:
        if not isinstance(value, dict) or set(value) != {
            "relative_path",
            "mode",
            "sha256",
            "size",
            "offset",
        }:
            raise RebuildError("RUNTIME_TREE_ENTRY_REJECTED")
        relative_text = value.get("relative_path")
        source_mode = value.get("mode")
        sha256 = value.get("sha256")
        size = value.get("size")
        supplied_offset = value.get("offset")
        if not isinstance(relative_text, str):
            raise RebuildError("RUNTIME_TREE_PATH_REJECTED")
        relative = Path(relative_text)
        if (
            not relative_text
            or "\x00" in relative_text
            or "\\" in relative_text
            or relative.is_absolute()
            or relative.as_posix() != relative_text
            or any(part in {"", ".", ".."} for part in relative.parts)
            or relative_text in names
            or isinstance(source_mode, bool)
            or source_mode not in {0o600, 0o644, 0o755}
            or not isinstance(sha256, str)
            or not _HEX64.fullmatch(sha256)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 1
            or isinstance(supplied_offset, bool)
            or supplied_offset != offset
        ):
            raise RebuildError("RUNTIME_TREE_ENTRY_REJECTED")
        names.add(relative_text)
        entry = RuntimeTreeEntry(relative, source_mode, sha256, size, offset)
        entries.append(entry)
        tree_values.append(
            {
                "relative_path": relative_text,
                "mode": source_mode,
                "sha256": sha256,
                "size": size,
            }
        )
        offset += size
    expected_tree_sha256 = FROZEN_RUNTIME_TREE_SHA256.get(phase)
    if (
        [entry.relative.as_posix() for entry in entries] != sorted(names)
        or offset != payload_size
        or _sha256(_canonical_bytes(tree_values)) != supplied_tree_sha256
        or supplied_tree_sha256 != expected_tree_sha256
    ):
        raise RebuildError("RUNTIME_TREE_DIGEST_REJECTED")
    return supplied_tree_sha256, tuple(entries), payload_size


def _frozen_runtime_tree_bundle(
    source_directory: Path,
    phase: str,
    destination: Path | None,
) -> tuple[
    PrivateFileIdentity,
    str,
    tuple[RuntimeTreeEntry, ...],
    tuple[tuple[Path, PrivateFileIdentity], ...],
]:
    contract = RUNTIME_TREE_BUNDLES.get(phase)
    if contract is None:
        raise RebuildError("RUNTIME_TREE_PHASE_REJECTED")
    bundle_name, maximum = contract
    source = _regular_private_directory(
        source_directory, "RUNTIME_TREE_SOURCE_REJECTED"
    )
    bundle = source / bundle_name
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        path_before = bundle.lstat()
        descriptor = os.open(bundle, flags)
    except OSError:
        raise RebuildError("RUNTIME_TREE_SOURCE_REJECTED") from None
    written: list[tuple[Path, PrivateFileIdentity]] = []
    if destination is not None:
        if destination.exists() or destination.is_symlink() or not destination.is_absolute():
            os.close(descriptor)
            raise RebuildError("RUNTIME_TREE_WRITE_REJECTED")
        try:
            destination.mkdir(mode=0o700)
        except OSError:
            os.close(descriptor)
            raise RebuildError("RUNTIME_TREE_WRITE_REJECTED") from None
    whole = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(path_before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or not 18 <= before.st_size <= maximum
            or (path_before.st_dev, path_before.st_ino)
            != (before.st_dev, before.st_ino)
        ):
            raise RebuildError("RUNTIME_TREE_SOURCE_REJECTED")
        magic = _read_exact(
            descriptor,
            len(RUNTIME_TREE_BUNDLE_MAGIC),
            whole,
            "RUNTIME_TREE_TRUNCATED",
        )
        length_raw = _read_exact(
            descriptor, 8, whole, "RUNTIME_TREE_TRUNCATED"
        )
        if magic != RUNTIME_TREE_BUNDLE_MAGIC:
            raise RebuildError("RUNTIME_TREE_HEADER_REJECTED")
        header_length = struct.unpack(">Q", length_raw)[0]
        if not 2 <= header_length <= RUNTIME_TREE_BUNDLE_HEADER_BYTES:
            raise RebuildError("RUNTIME_TREE_HEADER_REJECTED")
        header = _read_exact(
            descriptor, header_length, whole, "RUNTIME_TREE_TRUNCATED"
        )
        tree_sha256, entries, payload_size = _runtime_tree_entries(header, phase)
        if before.st_size != len(RUNTIME_TREE_BUNDLE_MAGIC) + 8 + header_length + payload_size:
            raise RebuildError("RUNTIME_TREE_SIZE_REJECTED")
        for entry in entries:
            target = destination / entry.relative if destination is not None else None
            output_descriptor = -1
            body_digest = hashlib.sha256()
            if target is not None:
                _make_private_parent(destination, entry.relative)
                try:
                    output_descriptor = os.open(
                        target,
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_NOFOLLOW", 0)
                        | getattr(os, "O_CLOEXEC", 0),
                        0o600,
                    )
                except OSError:
                    raise RebuildError("RUNTIME_TREE_WRITE_REJECTED") from None
            remaining = entry.size
            try:
                while remaining:
                    chunk = _read_exact(
                        descriptor,
                        min(1024 * 1024, remaining),
                        whole,
                        "RUNTIME_TREE_TRUNCATED",
                    )
                    body_digest.update(chunk)
                    if output_descriptor >= 0:
                        view = memoryview(chunk)
                        while view:
                            try:
                                count = os.write(output_descriptor, view)
                            except OSError:
                                raise RebuildError("RUNTIME_TREE_WRITE_REJECTED") from None
                            if count <= 0:
                                raise RebuildError("RUNTIME_TREE_WRITE_REJECTED")
                            view = view[count:]
                    remaining -= len(chunk)
                if output_descriptor >= 0:
                    os.fsync(output_descriptor)
            finally:
                if output_descriptor >= 0:
                    os.close(output_descriptor)
            if body_digest.hexdigest() != entry.sha256:
                raise RebuildError("RUNTIME_TREE_BODY_REJECTED")
            if target is not None:
                _raw, identity = _private_file_identity(
                    target,
                    code="RUNTIME_TREE_WRITE_REJECTED",
                    expected_sha256=entry.sha256,
                    maximum=maximum,
                )
                if identity.size != entry.size:
                    raise RebuildError("RUNTIME_TREE_WRITE_REJECTED")
                written.append((target, identity))
        if os.read(descriptor, 1):
            raise RebuildError("RUNTIME_TREE_SIZE_REJECTED")
        after = os.fstat(descriptor)
    except Exception:
        if destination is not None and destination.exists() and not destination.is_symlink():
            shutil.rmtree(destination, ignore_errors=True)
        raise
    finally:
        os.close(descriptor)
    try:
        path_after = bundle.lstat()
    except OSError:
        raise RebuildError("RUNTIME_TREE_SOURCE_REJECTED") from None
    metadata = (
        int(before.st_dev),
        int(before.st_ino),
        stat.S_IMODE(before.st_mode),
        int(before.st_uid),
        int(before.st_nlink),
        int(before.st_size),
        int(before.st_mtime_ns),
        int(before.st_ctime_ns),
    )
    if (
        metadata
        != (
            int(after.st_dev),
            int(after.st_ino),
            stat.S_IMODE(after.st_mode),
            int(after.st_uid),
            int(after.st_nlink),
            int(after.st_size),
            int(after.st_mtime_ns),
            int(after.st_ctime_ns),
        )
        or (
            path_after.st_dev,
            path_after.st_ino,
            path_after.st_mtime_ns,
            path_after.st_ctime_ns,
        )
        != (
            before.st_dev,
            before.st_ino,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
    ):
        raise RebuildError("RUNTIME_TREE_SOURCE_MUTATED")
    return (
        PrivateFileIdentity(*metadata, whole.hexdigest()),
        tree_sha256,
        entries,
        tuple(written),
    )


def materialize_frozen_runtime_tree(
    source_directory: Path, phase: str, destination: Path
) -> FrozenRuntimeTreeMaterialization:
    identity, tree_sha256, entries, files = _frozen_runtime_tree_bundle(
        source_directory, phase, destination
    )
    if len(files) != len(entries):
        raise RebuildError("RUNTIME_TREE_WRITE_REJECTED")
    info = destination.lstat()
    materialized = FrozenRuntimeTreeMaterialization(
        phase,
        destination,
        CheckoutIdentity(int(info.st_dev), int(info.st_ino)),
        identity,
        tree_sha256,
        entries,
        files,
    )
    verify_frozen_runtime_tree(source_directory, materialized)
    return materialized


def copy_frozen_runtime_tree_bundle(
    source_directory: Path,
    destination_directory: Path,
    phase: str,
    expected_identity: PrivateFileIdentity,
) -> PrivateFileIdentity:
    """Stream one validated large runtime bundle into the next private round."""

    contract = RUNTIME_TREE_BUNDLES.get(phase)
    if contract is None:
        raise RebuildError("RUNTIME_TREE_PHASE_REJECTED")
    name, maximum = contract
    source = _regular_private_directory(
        source_directory, "RUNTIME_TREE_SOURCE_REJECTED"
    )
    destination = _regular_private_directory(
        destination_directory, "RUNTIME_TREE_WRITE_REJECTED"
    )
    observed_identity, expected_tree, expected_entries, writes = (
        _frozen_runtime_tree_bundle(source, phase, None)
    )
    if writes or observed_identity != expected_identity:
        raise RebuildError("RUNTIME_TREE_SOURCE_MUTATED")
    source_path = source / name
    target = destination / name
    input_descriptor = output_descriptor = -1
    digest = hashlib.sha256()
    try:
        input_descriptor = os.open(
            source_path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        before = os.fstat(input_descriptor)
        metadata = (
            int(before.st_dev),
            int(before.st_ino),
            stat.S_IMODE(before.st_mode),
            int(before.st_uid),
            int(before.st_nlink),
            int(before.st_size),
            int(before.st_mtime_ns),
            int(before.st_ctime_ns),
        )
        if metadata != (
            expected_identity.device,
            expected_identity.inode,
            expected_identity.mode,
            expected_identity.uid,
            expected_identity.links,
            expected_identity.size,
            expected_identity.modified_ns,
            expected_identity.changed_ns,
        ) or not 18 <= before.st_size <= maximum:
            raise RebuildError("RUNTIME_TREE_SOURCE_MUTATED")
        output_descriptor = os.open(
            target,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        remaining = before.st_size
        while remaining:
            chunk = os.read(input_descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise RebuildError("RUNTIME_TREE_SOURCE_MUTATED")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                count = os.write(output_descriptor, view)
                if count <= 0:
                    raise RebuildError("RUNTIME_TREE_WRITE_REJECTED")
                view = view[count:]
            remaining -= len(chunk)
        if os.read(input_descriptor, 1):
            raise RebuildError("RUNTIME_TREE_SOURCE_MUTATED")
        os.fsync(output_descriptor)
        after = os.fstat(input_descriptor)
        path_after = source_path.lstat()
        if (
            metadata
            != (
                int(after.st_dev),
                int(after.st_ino),
                stat.S_IMODE(after.st_mode),
                int(after.st_uid),
                int(after.st_nlink),
                int(after.st_size),
                int(after.st_mtime_ns),
                int(after.st_ctime_ns),
            )
            or (path_after.st_dev, path_after.st_ino, path_after.st_mtime_ns, path_after.st_ctime_ns)
            != (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns)
            or digest.hexdigest() != expected_identity.sha256
        ):
            raise RebuildError("RUNTIME_TREE_SOURCE_MUTATED")
    except RebuildError:
        target.unlink(missing_ok=True)
        raise
    except OSError:
        target.unlink(missing_ok=True)
        raise RebuildError("RUNTIME_TREE_WRITE_REJECTED") from None
    finally:
        if input_descriptor >= 0:
            os.close(input_descriptor)
        if output_descriptor >= 0:
            os.close(output_descriptor)
    copied_identity, copied_tree, copied_entries, copied_writes = (
        _frozen_runtime_tree_bundle(destination, phase, None)
    )
    if (
        copied_writes
        or copied_tree != expected_tree
        or copied_entries != expected_entries
        or copied_identity.sha256 != expected_identity.sha256
    ):
        target.unlink(missing_ok=True)
        raise RebuildError("RUNTIME_TREE_WRITE_REJECTED")
    return copied_identity


def verify_frozen_runtime_tree(
    source_directory: Path, materialized: FrozenRuntimeTreeMaterialization
) -> None:
    identity, tree_sha256, entries, files = _frozen_runtime_tree_bundle(
        source_directory, materialized.phase, None
    )
    if (
        files
        or identity != materialized.bundle_identity
        or tree_sha256 != materialized.tree_sha256
        or entries != materialized.entries
    ):
        raise RebuildError("RUNTIME_TREE_SOURCE_MUTATED")
    root = _regular_private_directory(materialized.root, "RUNTIME_TREE_ROOT_MUTATED")
    root_info = root.lstat()
    if CheckoutIdentity(int(root_info.st_dev), int(root_info.st_ino)) != materialized.root_identity:
        raise RebuildError("RUNTIME_TREE_ROOT_MUTATED")
    expected_files = {entry.relative for entry in entries}
    expected_directories: set[Path] = {Path(".")}
    expected_identities = {
        path.relative_to(root): expected for path, expected in materialized.files
    }
    for entry in entries:
        target = root / entry.relative
        _raw, observed = _private_file_identity(
            target,
            code="RUNTIME_TREE_ROOT_MUTATED",
            expected_sha256=entry.sha256,
            maximum=RUNTIME_TREE_BUNDLES[materialized.phase][1],
        )
        if observed != expected_identities.get(entry.relative) or observed.size != entry.size:
            raise RebuildError("RUNTIME_TREE_ROOT_MUTATED")
        for index in range(1, len(entry.relative.parts)):
            expected_directories.add(Path(*entry.relative.parts[:index]))
    for current_raw, directories, files_in_directory in os.walk(root, followlinks=False):
        current = Path(current_raw)
        relative_directory = current.relative_to(root)
        if relative_directory not in expected_directories:
            raise RebuildError("RUNTIME_TREE_ROOT_MUTATED")
        for name in directories:
            child = current / name
            relative = child.relative_to(root)
            info = child.lstat()
            if (
                relative not in expected_directories
                or not stat.S_ISDIR(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o700
                or info.st_uid != os.geteuid()
            ):
                raise RebuildError("RUNTIME_TREE_ROOT_MUTATED")
        for name in files_in_directory:
            if (current / name).relative_to(root) not in expected_files:
                raise RebuildError("RUNTIME_TREE_ROOT_MUTATED")


def _new_frozen_f0_runtime_root(project_id: uuid.UUID) -> tuple[Path, CheckoutIdentity]:
    if not isinstance(project_id, uuid.UUID) or project_id.version != 4:
        raise RebuildError("FROZEN_F0_RUNTIME_REJECTED")
    prefix = f"anhuan-f111-repair-f0-{project_id.hex}-"
    for _attempt in range(8):
        root = Path("/private/tmp") / (prefix + crypto_secrets.token_hex(8))
        try:
            root.mkdir(mode=0o700)
        except FileExistsError:
            continue
        except OSError:
            raise RebuildError("FROZEN_F0_RUNTIME_REJECTED") from None
        info = root.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.geteuid()
        ):
            raise RebuildError("FROZEN_F0_RUNTIME_REJECTED")
        return root, CheckoutIdentity(int(info.st_dev), int(info.st_ino))
    raise RebuildError("FROZEN_F0_RUNTIME_REJECTED")


def _remove_frozen_f0_runtime_root(
    root: Path, project_id: uuid.UUID, expected_identity: CheckoutIdentity
) -> None:
    prefix = f"anhuan-f111-repair-f0-{project_id.hex}-"
    try:
        info = root.lstat()
    except OSError:
        raise RebuildError("FROZEN_F0_CLEANUP_REJECTED") from None
    if (
        root.parent != Path("/private/tmp")
        or not root.name.startswith(prefix)
        or not re.fullmatch(r"[a-z0-9_]{8,32}", root.name[len(prefix) :])
        or not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.geteuid()
        or CheckoutIdentity(int(info.st_dev), int(info.st_ino)) != expected_identity
    ):
        raise RebuildError("FROZEN_F0_CLEANUP_REJECTED")
    try:
        shutil.rmtree(root)
    except OSError:
        raise RebuildError("FROZEN_F0_CLEANUP_RED") from None
    if root.exists() or root.is_symlink():
        raise RebuildError("FROZEN_F0_CLEANUP_RED")


def _frozen_role_dsn(
    role: str, password: str, isolation: FrozenF0Isolation
) -> bytes:
    if (
        role not in {"f0d_bootstrap", "f0d_migration", "f0d_runtime", "f0d_worker"}
        or not isinstance(password, str)
        or not 32 <= len(password) <= 128
        or not re.fullmatch(r"[A-Za-z0-9_-]+", password)
    ):
        raise RebuildError("FROZEN_F0_DSN_REJECTED")
    database = "postgres" if role == "f0d_bootstrap" else isolation.f0i_template_database
    return (
        "postgresql://"
        + urllib.parse.quote(role, safe="")
        + ":"
        + urllib.parse.quote(password, safe="")
        + "@127.0.0.1:"
        + str(isolation.postgres_port)
        + "/"
        + database
    ).encode("ascii")


def prepare_frozen_f0_inputs(
    source_directory: Path,
    project_id: uuid.UUID,
    postgres_port: int,
    role_passwords: Mapping[str, str],
) -> FrozenF0PreparedInputs:
    if set(role_passwords) != {
        "f0d_bootstrap",
        "f0d_migration",
        "f0d_runtime",
        "f0d_worker",
    }:
        raise RebuildError("FROZEN_F0_DSN_REJECTED")
    root, root_identity = _new_frozen_f0_runtime_root(project_id)
    try:
        isolation = build_frozen_f0_isolation(root, project_id, postgres_port)
        for directory in (
            isolation.tmp_dir,
            isolation.f0f_vault_root,
            isolation.bootstrap_dsn_file.parent,
        ):
            directory.mkdir(mode=0o700)
        fixture_source = materialize_fixture_source_bundle(
            source_directory, isolation.fixture_source_root
        )
        runtime_trees = tuple(
            materialize_frozen_runtime_tree(
                source_directory,
                phase,
                {
                    "f0e": isolation.f0e_runtime_root,
                    "f0f": isolation.f0f_runtime_root,
                    "f0h": isolation.f0h_runtime_root,
                }[phase],
            )
            for phase in ("f0e", "f0f", "f0h")
        )
        key_raw, source_key_identity = _private_file_identity(
            _regular_private_directory(
                source_directory, "FROZEN_F0_KEY_REJECTED"
            )
            / F0F_SOURCE_KEY_NAME,
            code="FROZEN_F0_KEY_REJECTED",
            expected_sha256=None,
            maximum=64,
        )
        if len(key_raw) != 32:
            raise RebuildError("FROZEN_F0_KEY_REJECTED")
        _write_private(isolation.f0f_key_file, key_raw)
        _target_key_raw, target_key_identity = _private_file_identity(
            isolation.f0f_key_file,
            code="FROZEN_F0_KEY_REJECTED",
            expected_sha256=source_key_identity.sha256,
            maximum=64,
        )
        role_files = {
            "f0d_bootstrap": isolation.bootstrap_dsn_file,
            "f0d_migration": isolation.migration_dsn_file,
            "f0d_runtime": isolation.runtime_dsn_file,
            "f0d_worker": isolation.worker_dsn_file,
        }
        dsn_identities: list[tuple[str, PrivateFileIdentity]] = []
        for role, path in role_files.items():
            _write_private(
                path, _frozen_role_dsn(role, role_passwords[role], isolation)
            )
            _dsn_raw, dsn_identity = _private_file_identity(
                path,
                code="FROZEN_F0_DSN_REJECTED",
                expected_sha256=None,
                maximum=4096,
            )
            dsn_identities.append((role, dsn_identity))
        validate_frozen_f0_isolation(isolation)
        config_path = write_frozen_f0_isolation(
            isolation.runtime_root / F0_ISOLATION_CONFIG_NAME, isolation
        )
        _config_raw, config_identity = _private_file_identity(
            config_path,
            code="FROZEN_F0_CONFIG_REJECTED",
            expected_sha256=None,
            maximum=64 * 1024,
        )
        loaded = load_frozen_f0_isolation(
            {F0_ISOLATION_ENVIRONMENT_VARIABLE: str(config_path)}
        )
        if loaded != isolation:
            raise RebuildError("FROZEN_F0_CONFIG_REJECTED")
        prepared = FrozenF0PreparedInputs(
            isolation,
            config_path,
            config_identity,
            root_identity,
            source_key_identity,
            target_key_identity,
            tuple(dsn_identities),
            fixture_source,
            runtime_trees,
        )
        verify_frozen_f0_inputs(source_directory, prepared)
        return prepared
    except RebuildError:
        _remove_frozen_f0_runtime_root(root, project_id, root_identity)
        raise
    except FrozenF0IsolationError:
        _remove_frozen_f0_runtime_root(root, project_id, root_identity)
        raise RebuildError("FROZEN_F0_CONFIG_REJECTED") from None
    except Exception:
        _remove_frozen_f0_runtime_root(root, project_id, root_identity)
        raise RebuildError("FROZEN_F0_PREPARATION_RED") from None


def verify_frozen_f0_inputs(
    source_directory: Path, prepared: FrozenF0PreparedInputs
) -> None:
    isolation = prepared.isolation
    try:
        validate_frozen_f0_isolation(isolation)
        loaded = load_frozen_f0_isolation(
            {F0_ISOLATION_ENVIRONMENT_VARIABLE: str(prepared.config_path)}
        )
    except FrozenF0IsolationError:
        raise RebuildError("FROZEN_F0_CONFIG_MUTATED") from None
    try:
        root_info = isolation.runtime_root.lstat()
    except OSError:
        raise RebuildError("FROZEN_F0_RUNTIME_MUTATED") from None
    if (
        loaded != isolation
        or CheckoutIdentity(int(root_info.st_dev), int(root_info.st_ino))
        != prepared.runtime_root_identity
    ):
        raise RebuildError("FROZEN_F0_RUNTIME_MUTATED")
    _config_raw, config_identity = _private_file_identity(
        prepared.config_path,
        code="FROZEN_F0_CONFIG_MUTATED",
        expected_sha256=prepared.config_identity.sha256,
        maximum=64 * 1024,
    )
    if config_identity != prepared.config_identity:
        raise RebuildError("FROZEN_F0_CONFIG_MUTATED")
    _raw, source_key_identity = _private_file_identity(
        _regular_private_directory(
            source_directory, "FROZEN_F0_KEY_MUTATED"
        )
        / F0F_SOURCE_KEY_NAME,
        code="FROZEN_F0_KEY_MUTATED",
        expected_sha256=prepared.source_key_identity.sha256,
        maximum=64,
    )
    if source_key_identity != prepared.source_key_identity:
        raise RebuildError("FROZEN_F0_KEY_MUTATED")
    _target_key_raw, target_key_identity = _private_file_identity(
        isolation.f0f_key_file,
        code="FROZEN_F0_KEY_MUTATED",
        expected_sha256=prepared.source_key_identity.sha256,
        maximum=64,
    )
    if target_key_identity != prepared.target_key_identity:
        raise RebuildError("FROZEN_F0_KEY_MUTATED")
    role_files = {
        "f0d_bootstrap": isolation.bootstrap_dsn_file,
        "f0d_migration": isolation.migration_dsn_file,
        "f0d_runtime": isolation.runtime_dsn_file,
        "f0d_worker": isolation.worker_dsn_file,
    }
    if tuple(role_files) != tuple(role for role, _identity in prepared.dsn_identities):
        raise RebuildError("FROZEN_F0_DSN_MUTATED")
    for role, expected_identity in prepared.dsn_identities:
        _dsn_raw, observed_identity = _private_file_identity(
            role_files[role],
            code="FROZEN_F0_DSN_MUTATED",
            expected_sha256=expected_identity.sha256,
            maximum=4096,
        )
        if observed_identity != expected_identity:
            raise RebuildError("FROZEN_F0_DSN_MUTATED")
    verify_fixture_source_materialization(source_directory, prepared.fixture_source)
    if tuple(tree.phase for tree in prepared.runtime_trees) != ("f0e", "f0f", "f0h"):
        raise RebuildError("FROZEN_F0_RUNTIME_MUTATED")
    for tree in prepared.runtime_trees:
        verify_frozen_runtime_tree(source_directory, tree)


def _frozen_f0_round_identity(
    project: str, isolation: FrozenF0Isolation
) -> RoundIdentity:
    try:
        validate_frozen_f0_isolation(isolation)
    except FrozenF0IsolationError:
        raise RebuildError("FROZEN_F0_CONFIG_MUTATED") from None
    suffix = isolation.project_id.hex
    if project != PROJECT_PREFIX + suffix:
        raise RebuildError("FROZEN_F0_CONFIG_MUTATED")
    return RoundIdentity(
        round_number=1,
        suffix=suffix,
        project=project,
        database=DATABASE_PREFIX + suffix,
        pg_container=project + "-postgres",
        pg_volume=project + "-postgres-data",
    )


def verify_frozen_f0_project_absence(
    isolation: FrozenF0Isolation,
    environment: Mapping[str, str],
    *,
    cwd: Path,
) -> None:
    """Prove every isolated F0 Docker/J scope is absent without deleting it."""

    try:
        validate_frozen_f0_isolation(isolation)
        projects = tuple(isolation.managed_project_names)
        containers = tuple(isolation.managed_container_names)
    except (FrozenF0IsolationError, AttributeError, TypeError):
        raise RebuildError("FROZEN_F0_CONFIG_MUTATED") from None
    if (
        not projects
        or len(projects) != len(set(projects))
        or len(containers) != len(set(containers))
        or any(
            not re.fullmatch(r"[a-z0-9][a-z0-9_-]{7,62}", value)
            for value in projects
        )
        or any(
            not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{7,254}", value)
            for value in containers
        )
    ):
        raise RebuildError("FROZEN_F0_CONFIG_MUTATED")
    for project in projects:
        for kind in ("container", "volume", "network"):
            labels = (
                ("com.docker.compose.project", "com.anhuan.f111.project")
                if kind == "container"
                else ("com.docker.compose.project",)
            )
            for label in labels:
                command = ["docker", kind, "ls"]
                if kind == "container":
                    command.append("--all")
                command.extend(
                    (
                        "-q",
                        "--filter",
                        "label=" + label + "=" + project,
                    )
                )
                result = _process(
                    tuple(command),
                    cwd=cwd,
                    environment=environment,
                    timeout=30,
                    failure_code="FROZEN_F0_PROJECT_PROBE_RED",
                )
                if result.output.strip():
                    raise RebuildError("FROZEN_F0_PROJECT_COLLISION")
            name_command = ["docker", kind, "ls"]
            if kind == "container":
                name_command.append("--all")
            name_command.extend(("-q", "--filter", "name=" + project))
            named = _process(
                tuple(name_command),
                cwd=cwd,
                environment=environment,
                timeout=30,
                failure_code="FROZEN_F0_PROJECT_PROBE_RED",
            )
            if named.output.strip():
                raise RebuildError("FROZEN_F0_PROJECT_COLLISION")
    for container in containers:
        result = _process(
            ("docker", "container", "inspect", container),
            cwd=cwd,
            environment=environment,
            timeout=30,
            check=False,
        )
        if result.exit_code == 0:
            raise RebuildError("FROZEN_F0_PROJECT_COLLISION")
        if result.exit_code != 1:
            raise RebuildError("FROZEN_F0_PROJECT_PROBE_RED")


def capture_frozen_f0_database_snapshot(
    project: str,
    isolation: FrozenF0Isolation,
    environment: Mapping[str, str],
    *,
    cwd: Path,
) -> FrozenF0DatabaseSnapshot:
    """Bind the two immutable templates and prove all managed scratch DBs absent."""

    identity = _frozen_f0_round_identity(project, isolation)
    names = (identity.database, *isolation.managed_database_names)
    if len(names) != len(set(names)) or any(
        not re.fullmatch(r"[a-z][a-z0-9_]{1,62}", name) for name in names
    ):
        raise RebuildError("FROZEN_F0_DATABASE_REJECTED")
    statement = (
        "SELECT datname,oid::text,pg_get_userbyid(datdba) FROM pg_database "
        "WHERE datname IN ("
        + ",".join("'" + name + "'" for name in names)
        + ") ORDER BY datname"
    )
    result = _process(
        (
            "docker",
            "exec",
            identity.pg_container,
            "psql",
            "--no-psqlrc",
            "--quiet",
            "--tuples-only",
            "--no-align",
            "--field-separator=|",
            "--set=ON_ERROR_STOP=1",
            "--username=f0d_bootstrap",
            "--dbname=postgres",
            "--command",
            statement,
        ),
        cwd=cwd,
        environment=environment,
        timeout=120,
        failure_code="FROZEN_F0_DATABASE_PROBE_RED",
    )
    rows: list[tuple[str, int, str]] = []
    try:
        for raw_line in result.output.splitlines():
            line = raw_line.decode("ascii").strip()
            if not line:
                continue
            name, oid_raw, owner = line.split("|")
            oid = int(oid_raw)
            if name not in names or oid <= 0 or owner not in {
                "f0d_bootstrap",
                "f0d_migration",
            }:
                raise ValueError
            rows.append((name, oid, owner))
    except (UnicodeDecodeError, ValueError):
        raise RebuildError("FROZEN_F0_DATABASE_PROBE_RED") from None
    expected_owners = {
        identity.database: "f0d_bootstrap",
        isolation.f0g_template_database: "f0d_migration",
        isolation.f0i_template_database: "f0d_migration",
    }
    if (
        tuple(rows) != tuple(sorted(rows))
        or {name: owner for name, _oid, owner in rows} != expected_owners
    ):
        raise RebuildError("FROZEN_F0_DATABASE_RESIDUAL")
    canonical = [
        {"database": name, "oid": oid, "owner": owner}
        for name, oid, owner in rows
    ]
    return FrozenF0DatabaseSnapshot(tuple(rows), _sha256(_canonical_bytes(canonical)))


def private_fixture_manifest(
    raw: bytes,
    materialized: FixtureSourceMaterialization,
) -> bytes:
    document = _unique_json(raw, "FIXTURE_MANIFEST_REJECTED")
    if not isinstance(document, list) or len(document) != REQUIRED_FIXTURES:
        raise RebuildError("FIXTURE_MANIFEST_REJECTED")
    by_digest = {
        record.sha256: record for record, _identity in materialized.objects
    }
    rewritten: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in document:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "content_type"}:
            raise RebuildError("FIXTURE_MANIFEST_REJECTED")
        supplied_path = item.get("path")
        digest = item.get("sha256")
        content_type = item.get("content_type")
        if (
            not isinstance(supplied_path, str)
            or not Path(supplied_path).is_absolute()
            or "\x00" in supplied_path
            or not isinstance(digest, str)
            or digest in seen
            or not isinstance(content_type, str)
        ):
            raise RebuildError("FIXTURE_MANIFEST_REJECTED")
        record = by_digest.get(digest)
        if record is None:
            raise RebuildError("FIXTURE_MANIFEST_REJECTED")
        expected_type = (
            "application/pdf"
            if record.relative.suffix.casefold() == ".pdf"
            else "image/jpeg"
            if record.relative.suffix.casefold() in {".jpg", ".jpeg"}
            else None
        )
        if content_type != expected_type:
            raise RebuildError("FIXTURE_MANIFEST_REJECTED")
        rewritten.append(
            {
                "path": str(materialized.root / record.relative),
                "sha256": digest,
                "content_type": content_type,
            }
        )
        seen.add(digest)
    result = _canonical_bytes(rewritten)
    parse_fixture_manifest(result)
    return result


def _executable_identity(path: Path) -> ExecutableIdentity:
    """Bind one resolved executable without following a replacement on open."""

    try:
        target = path.resolve(strict=True)
    except OSError:
        raise RebuildError("CHECKOUT_PYTHON_BRIDGE_REJECTED") from None
    if not target.is_absolute():
        raise RebuildError("CHECKOUT_PYTHON_BRIDGE_REJECTED")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        path_before = target.lstat()
        descriptor = os.open(target, flags)
    except OSError:
        raise RebuildError("CHECKOUT_PYTHON_BRIDGE_REJECTED") from None
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(path_before.st_mode)
            or not before.st_mode & stat.S_IXUSR
            or before.st_size < 1
            or (path_before.st_dev, path_before.st_ino)
            != (before.st_dev, before.st_ino)
        ):
            raise RebuildError("CHECKOUT_PYTHON_BRIDGE_REJECTED")
        digest = hashlib.sha256()
        remaining = int(before.st_size)
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise RebuildError("CHECKOUT_PYTHON_BRIDGE_REJECTED")
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise RebuildError("CHECKOUT_PYTHON_BRIDGE_REJECTED")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        path_after = target.lstat()
    except OSError:
        raise RebuildError("CHECKOUT_PYTHON_BRIDGE_REJECTED") from None
    metadata = (
        int(before.st_dev),
        int(before.st_ino),
        stat.S_IMODE(before.st_mode),
        int(before.st_size),
        int(before.st_mtime_ns),
        int(before.st_ctime_ns),
    )
    if metadata != (
        int(after.st_dev),
        int(after.st_ino),
        stat.S_IMODE(after.st_mode),
        int(after.st_size),
        int(after.st_mtime_ns),
        int(after.st_ctime_ns),
    ) or (
        path_after.st_dev,
        path_after.st_ino,
        path_after.st_mtime_ns,
        path_after.st_ctime_ns,
    ) != (
        before.st_dev,
        before.st_ino,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ):
        raise RebuildError("CHECKOUT_PYTHON_BRIDGE_REJECTED")
    return ExecutableIdentity(target, *metadata, digest.hexdigest())


def _compose_plugin_identity() -> ExecutableIdentity:
    """Bind the one reviewed Docker Desktop Compose plugin without user config."""

    try:
        launcher_info = DOCKER_COMPOSE_LAUNCHER.lstat()
        launcher_target = Path(os.readlink(DOCKER_COMPOSE_LAUNCHER))
        plugin_directory_info = DOCKER_COMPOSE_PLUGIN_DIRECTORY.lstat()
        resolved = DOCKER_COMPOSE_LAUNCHER.resolve(strict=True)
    except OSError:
        raise RebuildError("COMPOSE_PLUGIN_REJECTED") from None
    if (
        not stat.S_ISLNK(launcher_info.st_mode)
        or launcher_info.st_uid not in {0, os.geteuid()}
        or not launcher_target.is_absolute()
        or launcher_target != DOCKER_COMPOSE_PLUGIN
        or resolved != DOCKER_COMPOSE_PLUGIN
        or not stat.S_ISDIR(plugin_directory_info.st_mode)
        or stat.S_ISLNK(plugin_directory_info.st_mode)
        or plugin_directory_info.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(plugin_directory_info.st_mode) & 0o022
    ):
        raise RebuildError("COMPOSE_PLUGIN_REJECTED")
    try:
        identity = _executable_identity(DOCKER_COMPOSE_PLUGIN)
        plugin_info = DOCKER_COMPOSE_PLUGIN.lstat()
    except RebuildError:
        raise RebuildError("COMPOSE_PLUGIN_REJECTED") from None
    except OSError:
        raise RebuildError("COMPOSE_PLUGIN_REJECTED") from None
    if (
        identity.path != DOCKER_COMPOSE_PLUGIN
        or identity.sha256 != DOCKER_COMPOSE_SHA256
        or plugin_info.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(plugin_info.st_mode) & 0o022
    ):
        raise RebuildError("COMPOSE_PLUGIN_REJECTED")
    return identity


def launcher_python_identity() -> ExecutableIdentity:
    """Return the live identity of the interpreter launching this runner."""

    value = getattr(sys, "executable", "")
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RebuildError("CHECKOUT_PYTHON_BRIDGE_REJECTED")
    return _executable_identity(Path(value))


def materialize_checkout_python_bridge(
    checkout: Path,
    expected_identity: ExecutableIdentity | None = None,
) -> ExecutableIdentity:
    checkout = _checkout_directory(checkout)
    target = launcher_python_identity()
    if expected_identity is not None and target != expected_identity:
        raise RebuildError("CHECKOUT_PYTHON_BRIDGE_REJECTED")
    venv = checkout / ".venv"
    binary = venv / "bin"
    try:
        venv.mkdir(mode=0o700)
        binary.mkdir(mode=0o700)
        (binary / "python").symlink_to(target.path)
    except OSError:
        raise RebuildError("CHECKOUT_PYTHON_BRIDGE_REJECTED") from None
    return verify_checkout_python_bridge(checkout, target)


def verify_checkout_python_bridge(
    checkout: Path,
    expected_identity: ExecutableIdentity | None = None,
) -> ExecutableIdentity:
    checkout = _checkout_directory(checkout)
    bridge = checkout / ".venv/bin/python"
    target = launcher_python_identity()
    if expected_identity is not None and target != expected_identity:
        raise RebuildError("CHECKOUT_PYTHON_BRIDGE_REJECTED")
    try:
        info = bridge.lstat()
        link = Path(os.readlink(bridge))
        resolved = bridge.resolve(strict=True)
    except OSError:
        raise RebuildError("CHECKOUT_PYTHON_BRIDGE_REJECTED") from None
    if (
        not stat.S_ISLNK(info.st_mode)
        or not link.is_absolute()
        or link != target.path
        or resolved != target.path
        or _executable_identity(resolved) != target
    ):
        raise RebuildError("CHECKOUT_PYTHON_BRIDGE_REJECTED")
    return target


def _write_private(path: Path, raw: bytes) -> None:
    if path.exists() or path.is_symlink() or not raw:
        raise RebuildError("PRIVATE_WRITE_REJECTED")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        raise RebuildError("PRIVATE_WRITE_REJECTED") from None


def _docker_private_config_bytes() -> bytes:
    return _canonical_bytes(
        {"cliPluginsExtraDirs": [str(DOCKER_COMPOSE_PLUGIN_DIRECTORY)]}
    )


def _validate_private_docker_config(home: Path) -> ExecutableIdentity:
    _regular_private_directory(home, "DOCKER_PRIVATE_CONFIG_REJECTED")
    docker_directory = _regular_private_directory(
        home / ".docker", "DOCKER_PRIVATE_CONFIG_REJECTED"
    )
    raw = _read_private_file(
        docker_directory / "config.json", "DOCKER_PRIVATE_CONFIG_REJECTED"
    )
    if raw != _docker_private_config_bytes():
        raise RebuildError("DOCKER_PRIVATE_CONFIG_REJECTED")
    return _compose_plugin_identity()


def _materialize_private_docker_config(home: Path) -> ExecutableIdentity:
    _regular_private_directory(home, "DOCKER_PRIVATE_CONFIG_REJECTED")
    docker_directory = home / ".docker"
    try:
        docker_directory.mkdir(mode=0o700)
    except OSError:
        raise RebuildError("DOCKER_PRIVATE_CONFIG_REJECTED") from None
    before = _compose_plugin_identity()
    try:
        _write_private(
            docker_directory / "config.json", _docker_private_config_bytes()
        )
    except RebuildError:
        raise RebuildError("DOCKER_PRIVATE_CONFIG_REJECTED") from None
    after = _validate_private_docker_config(home)
    if after != before:
        raise RebuildError("COMPOSE_PLUGIN_REJECTED")
    return after


def source_scope_document(identity: SourceContainerIdentity) -> dict[str, Any]:
    """Return the exact, non-secret pin for local read-only Docker access."""

    identity_document = {
        "container_id": identity.container_id,
        "container_name": identity.container_name,
        "compose_project": identity.compose_project,
        "compose_service": identity.compose_service,
        "image_id": identity.image_id,
        "image_reference": identity.image_reference,
        "published_port": identity.published_port,
    }
    parse_source_container_identity(
        _canonical_bytes(identity_document),
        expected_port=identity.published_port,
    )
    return {
        "schema": F0I_SOURCE_SCOPE_SCHEMA,
        "host": "127.0.0.1",
        "published_port": identity.published_port,
        "database": SOURCE_DATABASE_NAME,
        "access": F0I_SOURCE_ACCESS,
        "container_id": identity.container_id,
        "container_name": identity.container_name,
        "compose_project": identity.compose_project,
        "compose_service": identity.compose_service,
        "image_id": identity.image_id,
        "image_reference": identity.image_reference,
    }


def parse_source_scope(raw: bytes) -> SourceScope:
    """Parse a credential-free pin; source reads still use fixed docker exec."""

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError("duplicate key")
        return value

    try:
        document = json.loads(raw, object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise RebuildError("F0I_SOURCE_SCOPE_REJECTED") from None
    expected_keys = {
        "schema",
        "host",
        "published_port",
        "database",
        "access",
        "container_id",
        "container_name",
        "compose_project",
        "compose_service",
        "image_id",
        "image_reference",
    }
    if (
        not isinstance(document, dict)
        or set(document) != expected_keys
        or type(document.get("published_port")) is not int
        or document.get("schema") != F0I_SOURCE_SCOPE_SCHEMA
        or document.get("host") != "127.0.0.1"
        or document.get("database") != SOURCE_DATABASE_NAME
        or document.get("access") != F0I_SOURCE_ACCESS
    ):
        raise RebuildError("F0I_SOURCE_SCOPE_REJECTED")
    identity_document = {
        key: document[key]
        for key in (
            "container_id",
            "container_name",
            "compose_project",
            "compose_service",
            "image_id",
            "image_reference",
            "published_port",
        )
    }
    try:
        identity = parse_source_container_identity(
            _canonical_bytes(identity_document),
            expected_port=int(document["published_port"]),
        )
    except RebuildError:
        raise RebuildError("F0I_SOURCE_SCOPE_REJECTED") from None
    return SourceScope(
        host="127.0.0.1",
        published_port=identity.published_port,
        database=SOURCE_DATABASE_NAME,
        access=F0I_SOURCE_ACCESS,
        container=identity,
    )


def parse_f0g_source_scope(raw: bytes) -> F0GSourceScope:
    """Parse the exact credential-free F0F source and its two frozen digests."""

    document = _unique_json(raw, "F0G_SOURCE_SCOPE_REJECTED")
    expected_keys = {
        "schema",
        "database",
        "role",
        "schemas",
        "access",
        "read_only",
        "container_id",
        "container_name",
        "compose_project",
        "compose_service",
        "image_id",
        "image_reference",
        "published_port",
        "dump_sha256",
        "aggregate_sha256",
    }
    if (
        not isinstance(document, dict)
        or set(document) != expected_keys
        or _canonical_bytes(document) != raw
        or document.get("schema") != F0G_SOURCE_SCOPE_SCHEMA
        or document.get("database") != F0G_SOURCE_DATABASE_NAME
        or document.get("role") != F0G_SOURCE_ROLE
        or document.get("schemas") != list(F0G_SOURCE_SCHEMAS)
        or document.get("access") != F0I_SOURCE_ACCESS
        or document.get("read_only") is not True
        or type(document.get("published_port")) is not int
        or not isinstance(document.get("dump_sha256"), str)
        or not _HEX64.fullmatch(document["dump_sha256"])
        or not isinstance(document.get("aggregate_sha256"), str)
        or not _HEX64.fullmatch(document["aggregate_sha256"])
    ):
        raise RebuildError("F0G_SOURCE_SCOPE_REJECTED")
    identity_document = {
        key: document[key]
        for key in (
            "container_id",
            "container_name",
            "compose_project",
            "compose_service",
            "image_id",
            "image_reference",
            "published_port",
        )
    }
    try:
        identity = parse_source_container_identity(
            _canonical_bytes(identity_document),
            expected_port=document["published_port"],
        )
    except RebuildError:
        raise RebuildError("F0G_SOURCE_SCOPE_REJECTED") from None
    return F0GSourceScope(
        database=F0G_SOURCE_DATABASE_NAME,
        role=F0G_SOURCE_ROLE,
        schemas=F0G_SOURCE_SCHEMAS,
        access=F0I_SOURCE_ACCESS,
        read_only=True,
        container=identity,
        dump_sha256=document["dump_sha256"],
        aggregate_sha256=document["aggregate_sha256"],
    )


def database_environment(endpoint: DatabaseEndpoint) -> bytes:
    values = {
        "F111_DB_EXPECTED_DATABASE": endpoint.database,
        "F111_DB_EXPECTED_USER": endpoint.user,
        "PGAPPNAME": "anhuan-f111-clean-rebuild",
        "PGCONNECT_TIMEOUT": "15",
        "PGDATABASE": endpoint.database,
        "PGHOST": endpoint.host,
        "PGPASSWORD": endpoint.password,
        "PGPORT": str(endpoint.port),
        "PGSSLMODE": "disable",
        "PGUSER": endpoint.user,
    }
    if any("\n" in value or "\x00" in value for value in values.values()):
        raise RebuildError("DATABASE_ENVIRONMENT_REJECTED")
    return "".join(
        f"{name}={values[name]}\n" for name in sorted(values)
    ).encode("utf-8")


def _sqlalchemy_psycopg_dsn(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "\n" in value
        or "\r" in value
    ):
        raise RebuildError("DATABASE_DSN_REJECTED")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
        username = urllib.parse.unquote(parsed.username or "")
        password = urllib.parse.unquote(parsed.password or "")
    except (UnicodeDecodeError, ValueError):
        raise RebuildError("DATABASE_DSN_REJECTED") from None
    if (
        parsed.scheme != "postgresql"
        or username != "f0d_migration"
        or not password
        or parsed.hostname not in {"127.0.0.1", "host.docker.internal"}
        or port is None
        or not re.fullmatch(r"/[A-Za-z][A-Za-z0-9_]{0,62}", parsed.path)
        or parsed.query
        or parsed.fragment
    ):
        raise RebuildError("DATABASE_DSN_REJECTED")
    return urllib.parse.urlunsplit(
        ("postgresql+psycopg", parsed.netloc, parsed.path, "", "")
    )


def _read_fixture(path: Path, expected_sha256: str) -> tuple[int, int, int]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise RebuildError("FIXTURE_INPUT_REJECTED") from None
    observed = hashlib.sha256()
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or not 0 < info.st_size <= 100 * 1024 * 1024
        ):
            raise RebuildError("FIXTURE_INPUT_REJECTED")
        remaining = info.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise RebuildError("FIXTURE_INPUT_REJECTED")
            observed.update(chunk)
            remaining -= len(chunk)
    finally:
        os.close(descriptor)
    if observed.hexdigest() != expected_sha256:
        raise RebuildError("FIXTURE_INPUT_REJECTED")
    return int(info.st_size), int(info.st_dev), int(info.st_ino)


def _fixture_selection_document(raw: bytes) -> list[dict[str, str]]:
    document = _unique_json(raw, "FIXTURE_MANIFEST_REJECTED")
    if not isinstance(document, list) or len(document) != REQUIRED_FIXTURES:
        raise RebuildError("FIXTURE_MANIFEST_REJECTED")
    records: list[dict[str, str]] = []
    parents: set[Path] = set()
    names: set[str] = set()
    digests: set[str] = set()
    types: list[str] = []
    for item in document:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "content_type"}:
            raise RebuildError("FIXTURE_MANIFEST_REJECTED")
        path = Path(str(item["path"]))
        digest = str(item["sha256"])
        content_type = str(item["content_type"])
        try:
            parsed_name = uuid.UUID(path.name)
            canonical = path.resolve(strict=True)
            parent_info = path.parent.lstat()
        except (ValueError, OSError):
            raise RebuildError("FIXTURE_MANIFEST_REJECTED") from None
        if (
            parsed_name.version != 5
            or not path.is_absolute()
            or canonical != path
            or path.name in names
            or not _HEX64.fullmatch(digest)
            or digest in digests
            or content_type not in {"application/pdf", "image/jpeg"}
            or not stat.S_ISDIR(parent_info.st_mode)
            or stat.S_ISLNK(parent_info.st_mode)
            or stat.S_IMODE(parent_info.st_mode) != 0o700
            or parent_info.st_uid != os.geteuid()
        ):
            raise RebuildError("FIXTURE_MANIFEST_REJECTED")
        parents.add(path.parent)
        names.add(path.name)
        digests.add(digest)
        types.append(content_type)
        records.append(
            {"path": str(path), "sha256": digest, "content_type": content_type}
        )
    if len(parents) != 1 or types.count("application/pdf") != 3 or types.count("image/jpeg") != 1:
        raise RebuildError("FIXTURE_MANIFEST_REJECTED")
    return records


def materialize_fixture_selection(
    raw: bytes,
    destination: Path,
) -> tuple[bytes, tuple[FixtureSelectionCopy, ...]]:
    records = _fixture_selection_document(raw)
    if destination.exists() or destination.is_symlink() or not destination.is_absolute():
        raise RebuildError("FIXTURE_SELECTION_WRITE_REJECTED")
    try:
        destination.mkdir(mode=0o700)
    except OSError:
        raise RebuildError("FIXTURE_SELECTION_WRITE_REJECTED") from None
    copies: list[FixtureSelectionCopy] = []
    rewritten: list[dict[str, str]] = []
    try:
        for record in records:
            source = Path(record["path"])
            raw_fixture, source_identity = _private_file_identity(
                source,
                code="FIXTURE_SELECTION_SOURCE_REJECTED",
                expected_sha256=record["sha256"],
                maximum=100 * 1024 * 1024,
            )
            target = destination / source.name
            _write_private(target, raw_fixture)
            _copied_raw, target_identity = _private_file_identity(
                target,
                code="FIXTURE_SELECTION_WRITE_REJECTED",
                expected_sha256=record["sha256"],
                maximum=100 * 1024 * 1024,
            )
            copies.append(
                FixtureSelectionCopy(
                    source,
                    source_identity,
                    target,
                    target_identity,
                    record["sha256"],
                    record["content_type"],
                )
            )
            rewritten.append(
                {
                    "path": str(target),
                    "sha256": record["sha256"],
                    "content_type": record["content_type"],
                }
            )
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    result = _canonical_bytes(rewritten)
    parse_fixture_manifest(result)
    return result, tuple(copies)


def verify_fixture_selection(
    source_manifest: bytes,
    copies: Sequence[FixtureSelectionCopy],
) -> bytes:
    records = _fixture_selection_document(source_manifest)
    if len(copies) != REQUIRED_FIXTURES:
        raise RebuildError("FIXTURE_SELECTION_MUTATED")
    rewritten: list[dict[str, str]] = []
    for record, copied in zip(records, copies, strict=True):
        if (
            Path(record["path"]) != copied.source
            or record["sha256"] != copied.sha256
            or record["content_type"] != copied.content_type
        ):
            raise RebuildError("FIXTURE_SELECTION_MUTATED")
        _source_raw, source_identity = _private_file_identity(
            copied.source,
            code="FIXTURE_SELECTION_MUTATED",
            expected_sha256=copied.sha256,
            maximum=100 * 1024 * 1024,
        )
        _target_raw, target_identity = _private_file_identity(
            copied.target,
            code="FIXTURE_SELECTION_MUTATED",
            expected_sha256=copied.sha256,
            maximum=100 * 1024 * 1024,
        )
        if source_identity != copied.source_identity or target_identity != copied.target_identity:
            raise RebuildError("FIXTURE_SELECTION_MUTATED")
        rewritten.append(
            {
                "path": str(copied.target),
                "sha256": copied.sha256,
                "content_type": copied.content_type,
            }
        )
    result = _canonical_bytes(rewritten)
    parse_fixture_manifest(result)
    return result


def parse_fixture_manifest(raw: bytes) -> tuple[FixtureInput, ...]:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError:
        raise RebuildError("FIXTURE_MANIFEST_REJECTED") from None
    if not isinstance(document, list) or len(document) < REQUIRED_FIXTURES:
        raise RebuildError("FIXTURE_MANIFEST_REJECTED")
    fixtures: list[FixtureInput] = []
    paths: set[Path] = set()
    digests: set[str] = set()
    for item in document:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "content_type"}:
            raise RebuildError("FIXTURE_MANIFEST_REJECTED")
        path = Path(str(item["path"]))
        digest = str(item["sha256"])
        content_type = str(item["content_type"])
        try:
            canonical = path.resolve(strict=True)
        except OSError:
            raise RebuildError("FIXTURE_INPUT_REJECTED") from None
        if (
            not path.is_absolute()
            or canonical != path
            or path in paths
            or not _HEX64.fullmatch(digest)
            or digest in digests
            or content_type not in {"application/pdf", "image/jpeg"}
        ):
            raise RebuildError("FIXTURE_MANIFEST_REJECTED")
        size, device, inode = _read_fixture(path, digest)
        fixtures.append(FixtureInput(path, digest, size, device, inode, content_type))
        paths.add(path)
        digests.add(digest)
    return tuple(fixtures)


def parse_source_container_identity(
    raw: bytes,
    *,
    expected_port: int,
) -> SourceContainerIdentity:
    """Parse a non-secret, exact identity pin for the existing F0-I source."""
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError("duplicate key")
        return value

    try:
        document = json.loads(raw, object_pairs_hook=unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise RebuildError("F0I_SOURCE_IDENTITY_REJECTED") from None
    keys = {
        "container_id",
        "container_name",
        "compose_project",
        "compose_service",
        "image_id",
        "image_reference",
        "published_port",
    }
    if (
        not isinstance(document, dict)
        or set(document) != keys
        or type(document["published_port"]) is not int
    ):
        raise RebuildError("F0I_SOURCE_IDENTITY_REJECTED")
    try:
        identity = SourceContainerIdentity(
            container_id=str(document["container_id"]),
            container_name=str(document["container_name"]),
            compose_project=str(document["compose_project"]),
            compose_service=str(document["compose_service"]),
            image_id=str(document["image_id"]),
            image_reference=str(document["image_reference"]),
            published_port=int(document["published_port"]),
        )
    except (TypeError, ValueError):
        raise RebuildError("F0I_SOURCE_IDENTITY_REJECTED") from None
    if (
        not re.fullmatch(r"[0-9a-f]{64}", identity.container_id)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", identity.container_name)
        or identity.compose_project != SOURCE_COMPOSE_PROJECT
        or identity.compose_service != SOURCE_COMPOSE_SERVICE
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", identity.image_id)
        or identity.image_reference != PG_IMAGE
        or identity.published_port != expected_port
        or not 1 <= identity.published_port <= 65535
    ):
        raise RebuildError("F0I_SOURCE_IDENTITY_REJECTED")
    return identity


def validate_source_container_inspect(
    raw: bytes,
    expected: SourceContainerIdentity,
) -> None:
    """Validate only selected inspect fields; Config.Env is never requested."""
    lines = raw.splitlines()
    if len(lines) != 9:
        raise RebuildError("F0I_SOURCE_CONTAINER_REJECTED")
    try:
        values = [json.loads(line) for line in lines]
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RebuildError("F0I_SOURCE_CONTAINER_REJECTED") from None
    (
        container_id,
        container_name,
        image_id,
        status,
        health,
        compose_project,
        compose_service,
        image_reference,
        ports,
    ) = values
    if (
        container_id != expected.container_id
        or container_name != "/" + expected.container_name
        or image_id != expected.image_id
        or status != "running"
        or health != "healthy"
        or compose_project != expected.compose_project
        or compose_service != expected.compose_service
        or image_reference != expected.image_reference
        or not isinstance(ports, list)
        or len(ports) != 1
        or not isinstance(ports[0], dict)
        or set(ports[0]) != {"HostIp", "HostPort"}
        or ports[0].get("HostIp") != "127.0.0.1"
        or ports[0].get("HostPort") != str(expected.published_port)
    ):
        raise RebuildError("F0I_SOURCE_CONTAINER_REJECTED")


def parse_source_aggregate(raw: bytes) -> str:
    """Bind the fixed DB/head and four non-empty registered Fixture relations."""
    lines = [line for line in raw.splitlines() if line]
    if len(lines) == 3 and lines[0] == b"BEGIN" and lines[2] == b"COMMIT":
        lines = lines[1:2]
    if len(lines) != 1:
        raise RebuildError("F0I_SOURCE_AGGREGATE_RED")
    try:
        fields = lines[0].decode("ascii").split("|")
    except UnicodeDecodeError:
        raise RebuildError("F0I_SOURCE_AGGREGATE_RED") from None
    if len(fields) != 8 or fields[:4] != [
        SOURCE_DATABASE_NAME,
        SOURCE_DATABASE_SUPERUSER,
        "on",
        "f0d_0006",
    ]:
        raise RebuildError("F0I_SOURCE_AGGREGATE_RED")
    try:
        counts = tuple(int(value) for value in fields[4:])
    except ValueError:
        raise RebuildError("F0I_SOURCE_AGGREGATE_RED") from None
    if any(value <= 0 or value > 10_000_000 for value in counts):
        raise RebuildError("F0I_SOURCE_AGGREGATE_RED")
    return _sha256(
        _canonical_bytes(
            {
                "database": fields[0],
                "head": fields[3],
                "fixture_source_registry": counts[0],
                "document_scope": counts[1],
                "page": counts[2],
                "chunk": counts[3],
            }
        )
    )


def f0g_source_aggregate_statement() -> bytes:
    return (
        "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY, DEFERRABLE;\n"
        "SELECT current_database(),current_user,"
        "current_setting('transaction_read_only'),"
        "COALESCE((SELECT string_agg(version_num,',' ORDER BY version_num) "
        "FROM f0d.alembic_version),''),"
        "(SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema IN ('f0d','f0e','f0f') AND table_type='BASE TABLE'),"
        "(SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema IN ('f0d','f0e','f0f')) ,"
        "(SELECT count(*) FROM pg_catalog.pg_constraint c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.connamespace "
        "WHERE n.nspname IN ('f0d','f0e','f0f')) ,"
        "(SELECT count(*) FROM pg_catalog.pg_class c "
        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname IN ('f0d','f0e','f0f') AND c.relkind='i');\n"
        "COMMIT;\n"
    ).encode("ascii")


def parse_f0g_source_aggregate(raw: bytes) -> str:
    lines = [line for line in raw.splitlines() if line]
    if len(lines) == 3 and lines[0] == b"BEGIN" and lines[2] == b"COMMIT":
        lines = lines[1:2]
    if len(lines) != 1:
        raise RebuildError("F0G_SOURCE_AGGREGATE_RED")
    try:
        fields = lines[0].decode("ascii").split("|")
    except UnicodeDecodeError:
        raise RebuildError("F0G_SOURCE_AGGREGATE_RED") from None
    if len(fields) != 8 or fields[:4] != [
        F0G_SOURCE_DATABASE_NAME,
        F0G_SOURCE_ROLE,
        "on",
        "f0d_0004",
    ]:
        raise RebuildError("F0G_SOURCE_AGGREGATE_RED")
    try:
        counts = tuple(int(value) for value in fields[4:])
    except ValueError:
        raise RebuildError("F0G_SOURCE_AGGREGATE_RED") from None
    if any(value <= 0 or value > 10_000_000 for value in counts):
        raise RebuildError("F0G_SOURCE_AGGREGATE_RED")
    return _sha256(
        _canonical_bytes(
            {
                "database": fields[0],
                "role": fields[1],
                "head": fields[3],
                "table_count": counts[0],
                "column_count": counts[1],
                "constraint_count": counts[2],
                "index_count": counts[3],
            }
        )
    )


_COPY_HEADER = re.compile(
    rb"^COPY (f0d|f0i)\.([a-z_][a-z0-9_]*) \(([^\r\n]+)\) FROM stdin;$"
)
_SETVAL = re.compile(
    rb"^SELECT pg_catalog\.setval\('(?:f0d|f0i)\.[a-z_][a-z0-9_]*'::regclass, "
    rb"[0-9]+, (?:true|false)\);$"
)
_DUMP_META = re.compile(
    rb"(?:SET [a-z_]+ = .*;|SELECT pg_catalog\.set_config\('search_path', '', false\);)"
)
_DUMP_RESTRICT = re.compile(rb"^\\(?:un)?restrict [A-Za-z0-9_+/=-]+$")
_F0G_COPY_HEADER = re.compile(
    rb"^COPY (f0d|f0e|f0f)\.([a-z_][a-z0-9_]*) \(([^\r\n]+)\) FROM stdin;$"
)
_F0G_SETVAL = re.compile(
    rb"^SELECT pg_catalog\.setval\('(?:f0d|f0e|f0f)\.[a-z_][a-z0-9_]*'::regclass, "
    rb"[0-9]+, (?:true|false)\);$"
)


def normalized_data_dump_digest(path: Path) -> str:
    """Hash every COPY row as a multiset while excluding pg_dump run noise."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise RebuildError("SOURCE_DUMP_REJECTED") from None
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or not 0 < info.st_size <= MAX_SOURCE_DUMP
        ):
            raise RebuildError("SOURCE_DUMP_REJECTED")
        sections: dict[tuple[str, str], dict[str, Any]] = {}
        sequence_lines: list[str] = []
        active: tuple[str, str] | None = None
        active_columns = b""
        active_rows: list[bytes] = []
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while True:
                line = stream.readline(MAX_PROCESS_OUTPUT + 1)
                if not line:
                    break
                if len(line) > MAX_PROCESS_OUTPUT or not line.endswith(b"\n"):
                    raise RebuildError("SOURCE_DUMP_REJECTED")
                value = line[:-1]
                if value.endswith(b"\r"):
                    value = value[:-1]
                if active is not None:
                    if value == b"\\.":
                        if active in sections:
                            raise RebuildError("SOURCE_DUMP_REJECTED")
                        sections[active] = {
                            "columns_sha256": _sha256(active_columns),
                            "row_count": len(active_rows),
                            "row_multiset_sha256": _sha256(b"".join(sorted(active_rows))),
                        }
                        active = None
                        active_columns = b""
                        active_rows = []
                    else:
                        if len(active_rows) >= 2_000_000:
                            raise RebuildError("SOURCE_DUMP_REJECTED")
                        active_rows.append(hashlib.sha256(value).digest())
                    continue
                match = _COPY_HEADER.fullmatch(value)
                if match is not None:
                    active = (
                        match.group(1).decode("ascii"),
                        match.group(2).decode("ascii"),
                    )
                    active_columns = match.group(3)
                    continue
                if _SETVAL.fullmatch(value):
                    sequence_lines.append(_sha256(value))
                    continue
                if (
                    not value
                    or value.startswith(b"--")
                    or _DUMP_META.fullmatch(value)
                    or _DUMP_RESTRICT.fullmatch(value)
                ):
                    continue
                raise RebuildError("SOURCE_DUMP_REJECTED")
        if active is not None or not sections:
            raise RebuildError("SOURCE_DUMP_REJECTED")
        if any(sections.get(table, {}).get("row_count", 0) <= 0 for table in REQUIRED_SOURCE_TABLES):
            raise RebuildError("SOURCE_DUMP_INCOMPLETE")
        payload = {
            "schema": "f1.1.1-f0i-data-dump-v1",
            "tables": [
                {
                    "schema": schema,
                    "table": table,
                    **sections[(schema, table)],
                }
                for schema, table in sorted(sections)
            ],
            "sequence_line_sha256": sorted(sequence_lines),
        }
        return _sha256(_canonical_bytes(payload))
    finally:
        os.close(descriptor)


def normalized_f0g_data_dump_digest(path: Path) -> str:
    """Hash the F0F source data without pg_dump run tokens or row ordering."""

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise RebuildError("F0G_SOURCE_DUMP_REJECTED") from None
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or not 0 < info.st_size <= MAX_SOURCE_DUMP
        ):
            raise RebuildError("F0G_SOURCE_DUMP_REJECTED")
        sections: dict[tuple[str, str], dict[str, Any]] = {}
        sequence_lines: list[str] = []
        active: tuple[str, str] | None = None
        active_columns = b""
        active_rows: list[bytes] = []
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while True:
                line = stream.readline(MAX_PROCESS_OUTPUT + 1)
                if not line:
                    break
                if len(line) > MAX_PROCESS_OUTPUT or not line.endswith(b"\n"):
                    raise RebuildError("F0G_SOURCE_DUMP_REJECTED")
                value = line[:-1]
                if value.endswith(b"\r"):
                    value = value[:-1]
                if active is not None:
                    if value == b"\\.":
                        if active in sections:
                            raise RebuildError("F0G_SOURCE_DUMP_REJECTED")
                        sections[active] = {
                            "columns_sha256": _sha256(active_columns),
                            "row_count": len(active_rows),
                            "row_multiset_sha256": _sha256(b"".join(sorted(active_rows))),
                        }
                        active = None
                        active_columns = b""
                        active_rows = []
                    else:
                        if len(active_rows) >= 2_000_000:
                            raise RebuildError("F0G_SOURCE_DUMP_REJECTED")
                        active_rows.append(hashlib.sha256(value).digest())
                    continue
                match = _F0G_COPY_HEADER.fullmatch(value)
                if match is not None:
                    active = (
                        match.group(1).decode("ascii"),
                        match.group(2).decode("ascii"),
                    )
                    active_columns = match.group(3)
                    continue
                if _F0G_SETVAL.fullmatch(value):
                    sequence_lines.append(_sha256(value))
                    continue
                if (
                    not value
                    or value.startswith(b"--")
                    or _DUMP_META.fullmatch(value)
                    or _DUMP_RESTRICT.fullmatch(value)
                ):
                    continue
                raise RebuildError("F0G_SOURCE_DUMP_REJECTED")
        if (
            active is not None
            or not sections
            or not any(schema == "f0e" and value["row_count"] > 0 for (schema, _table), value in sections.items())
            or not any(schema == "f0f" and value["row_count"] > 0 for (schema, _table), value in sections.items())
        ):
            raise RebuildError("F0G_SOURCE_DUMP_INCOMPLETE")
        payload = {
            "schema": "f1.1.1-f0g-data-dump-v2",
            "tables": [
                {
                    "schema": schema,
                    "table": table,
                    **sections[(schema, table)],
                }
                for schema, table in sorted(sections)
            ],
            "sequence_line_sha256": sorted(sequence_lines),
        }
        return _sha256(_canonical_bytes(payload))
    finally:
        os.close(descriptor)


def _relative_path(raw: bytes) -> Path:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise RebuildError("SOURCE_PATH_REJECTED") from None
    relative = Path(text)
    if (
        not text
        or relative.is_absolute()
        or ".." in relative.parts
        or any(part in {".git", "__pycache__", "node_modules"} for part in relative.parts)
    ):
        raise RebuildError("SOURCE_PATH_REJECTED")
    return relative


def _repository_state(environment: Mapping[str, str]) -> str:
    # Apple's command-line-tool shim can emit a one-time cache/FSEvents
    # diagnostic on the first git invocation under a freshly isolated HOME
    # and TMPDIR.  Consume that initialization before hashing repository
    # stdout; the measured commands below are still run twice by
    # ``capture_source`` and must match exactly.
    _process(
        ("git", "--version"),
        cwd=ROOT,
        environment=environment,
        timeout=60,
        failure_code="SOURCE_GIT_REJECTED",
    )
    pieces: list[bytes] = []
    for arguments in (
        ("git", "rev-parse", "HEAD"),
        (
            "git",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--no-renames",
            "--",
            ".",
            ":(top,exclude)artifacts/f1-platform-shell/v0.3/**",
        ),
        ("git", "show-ref", "--head"),
    ):
        result = _process(
            arguments,
            cwd=ROOT,
            environment=environment,
            timeout=60,
            failure_code="SOURCE_GIT_REJECTED",
        )
        pieces.append(result.output)
    return _sha256(b"\x00".join(pieces))


def capture_source(environment: Mapping[str, str]) -> SourceSnapshot:
    state = _repository_state(environment)
    tracked = _process(
        ("git", "ls-files", "-z", "--cached"),
        cwd=ROOT,
        environment=environment,
        timeout=60,
        failure_code="SOURCE_GIT_REJECTED",
    ).output.split(b"\x00")
    untracked = _process(
        ("git", "ls-files", "-z", "--others", "--exclude-standard"),
        cwd=ROOT,
        environment=environment,
        timeout=60,
        failure_code="SOURCE_GIT_REJECTED",
    ).output.split(b"\x00")
    paths: set[Path] = set()
    for raw in tracked:
        if raw:
            relative = _relative_path(raw)
            if not _formal_v03_output(relative):
                paths.add(relative)
    for raw in untracked:
        if not raw:
            continue
        relative = _relative_path(raw)
        if _formal_v03_output(relative):
            continue
        if not untracked_delivery_allowed(relative):
            raise RebuildError("UNTRACKED_SOURCE_REJECTED")
        paths.add(relative)
    entries: list[SourceEntry] = []
    for relative in sorted(paths, key=lambda item: item.as_posix()):
        path = ROOT / relative
        try:
            info = path.lstat()
        except OSError:
            raise RebuildError("SOURCE_FILE_REJECTED") from None
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_size < 0
            or info.st_size > MAX_SOURCE_FILE
        ):
            raise RebuildError("SOURCE_FILE_REJECTED")
        try:
            raw = path.read_bytes()
        except OSError:
            raise RebuildError("SOURCE_FILE_REJECTED") from None
        entries.append(
            SourceEntry(
                relative=relative,
                mode=0o755 if info.st_mode & stat.S_IXUSR else 0o644,
                sha256=_sha256(raw),
                size=len(raw),
            )
        )
    if not entries or _repository_state(environment) != state:
        raise RebuildError("SOURCE_DRIFT")
    manifest = [
        {
            "path": entry.relative.as_posix(),
            "mode": entry.mode,
            "sha256": entry.sha256,
            "size": entry.size,
        }
        for entry in entries
    ]
    return SourceSnapshot(tuple(entries), _sha256(_canonical_bytes(manifest)), state)


def _copy_source(snapshot: SourceSnapshot, destination: Path) -> None:
    destination.mkdir(mode=0o700)
    for entry in snapshot.entries:
        source = ROOT / entry.relative
        target = destination / entry.relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        try:
            source_info = source.lstat()
            if not stat.S_ISREG(source_info.st_mode) or stat.S_ISLNK(source_info.st_mode):
                raise RebuildError("SOURCE_FILE_REJECTED")
            with source.open("rb") as input_stream, target.open("xb") as output_stream:
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
            target.chmod(entry.mode)
            raw = target.read_bytes()
        except RebuildError:
            raise
        except OSError:
            raise RebuildError("SOURCE_COPY_REJECTED") from None
        if len(raw) != entry.size or _sha256(raw) != entry.sha256:
            raise RebuildError("SOURCE_COPY_REJECTED")


def _checkout_snapshot(checkout: Path, environment: Mapping[str, str]) -> str:
    status = _process(
        ("git", "status", "--porcelain=v1", "-z"),
        cwd=checkout,
        environment=environment,
        timeout=60,
        failure_code="CLEAN_CHECKOUT_REJECTED",
    ).output
    if status:
        raise RebuildError("CLEAN_CHECKOUT_REJECTED")
    listed = _process(
        ("git", "ls-files", "-z"),
        cwd=checkout,
        environment=environment,
        timeout=60,
        failure_code="CLEAN_CHECKOUT_REJECTED",
    ).output.split(b"\x00")
    entries: list[dict[str, Any]] = []
    for raw in listed:
        if not raw:
            continue
        relative = _relative_path(raw)
        path = checkout / relative
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise RebuildError("CLEAN_CHECKOUT_REJECTED")
            body = path.read_bytes()
        except RebuildError:
            raise
        except OSError:
            raise RebuildError("CLEAN_CHECKOUT_REJECTED") from None
        entries.append(
            {
                "path": relative.as_posix(),
                "mode": 0o755 if info.st_mode & stat.S_IXUSR else 0o644,
                "sha256": _sha256(body),
                "size": len(body),
            }
        )
    return _sha256(_canonical_bytes(entries))


def checkout_identity(checkout: Path) -> CheckoutIdentity:
    checkout = _checkout_directory(checkout)
    info = checkout.lstat()
    return CheckoutIdentity(int(info.st_dev), int(info.st_ino))


def validate_delivery_checkout(
    checkout: Path,
    environment: Mapping[str, str],
    *,
    expected_source_sha256: str,
    expected_identity: CheckoutIdentity,
    expected_python_identity: ExecutableIdentity | None = None,
) -> None:
    """Bind tracked delivery plus five frozen inputs and one runtime bridge."""

    checkout = _checkout_directory(checkout)
    if (
        not _HEX64.fullmatch(expected_source_sha256)
        or checkout_identity(checkout) != expected_identity
        or _checkout_snapshot(checkout, environment) != expected_source_sha256
    ):
        raise RebuildError("CLEAN_CHECKOUT_DRIFT")
    verify_checkout_fixture_plans(checkout)
    verify_checkout_python_bridge(checkout, expected_python_identity)
    listed = _process(
        ("git", "ls-files", "-z"),
        cwd=checkout,
        environment=environment,
        timeout=60,
        failure_code="CLEAN_CHECKOUT_REJECTED",
    ).output.split(b"\x00")
    tracked = {_relative_path(raw).as_posix() for raw in listed if raw}
    fixed = {relative.as_posix() for relative, _sha in FIXTURE_PLAN_CONTRACTS.values()}
    bridge = ".venv/bin/python"
    allowed_paths = tracked | fixed | {bridge}
    allowed_directories = {"", ".venv", ".venv/bin"}
    for path in allowed_paths:
        parts = Path(path).parts[:-1]
        for index in range(1, len(parts) + 1):
            allowed_directories.add(Path(*parts[:index]).as_posix())
    for current_raw, directories, files in os.walk(checkout, followlinks=False):
        current = Path(current_raw)
        relative_dir = current.relative_to(checkout).as_posix()
        if relative_dir == ".":
            relative_dir = ""
        if relative_dir in {".git", "src/web/node_modules", "src/web/dist"}:
            directories[:] = []
            continue
        if relative_dir and relative_dir not in allowed_directories:
            raise RebuildError("CLEAN_CHECKOUT_UNTRACKED_REJECTED")
        kept: list[str] = []
        for name in directories:
            child = current / name
            relative = child.relative_to(checkout).as_posix()
            if relative in {".git", "src/web/node_modules", "src/web/dist"}:
                kept.append(name)
                continue
            try:
                info = child.lstat()
            except OSError:
                raise RebuildError("CLEAN_CHECKOUT_UNTRACKED_REJECTED") from None
            if stat.S_ISLNK(info.st_mode) or relative not in allowed_directories:
                raise RebuildError("CLEAN_CHECKOUT_UNTRACKED_REJECTED")
            kept.append(name)
        directories[:] = kept
        for name in files:
            child = current / name
            relative = child.relative_to(checkout).as_posix()
            try:
                info = child.lstat()
            except OSError:
                raise RebuildError("CLEAN_CHECKOUT_UNTRACKED_REJECTED") from None
            if relative == bridge:
                if not stat.S_ISLNK(info.st_mode):
                    raise RebuildError("CHECKOUT_PYTHON_BRIDGE_REJECTED")
                continue
            if (
                relative not in tracked
                and relative not in fixed
                and not relative.startswith("src/web/dist/")
            ):
                raise RebuildError("CLEAN_CHECKOUT_UNTRACKED_REJECTED")
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise RebuildError("CLEAN_CHECKOUT_UNTRACKED_REJECTED")


def _extract_metric_names(path: Path) -> tuple[str, ...]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, SyntaxError):
        raise RebuildError("PG_CONTRACT_REJECTED") from None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "METRICS"
            for target in node.targets
        ):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError, SyntaxError):
                break
            if (
                isinstance(value, tuple)
                and len(value) >= 18
                and all(isinstance(item, str) for item in value)
                and "catalog_query_failures" in value
                and "fixture_cleanup_residuals" in value
            ):
                return tuple(value)
            break
    raise RebuildError("PG_CONTRACT_REJECTED")


class CleanRebuildRound:
    def __init__(self, round_number: int, environment: Mapping[str, str]) -> None:
        self.identity = RoundIdentity.create(round_number)
        self.inherited = dict(environment)
        self.environment: dict[str, str] = {}
        self.timeout = self._timeout(environment)
        self.state = ResourceState()
        self.ports: dict[str, int] = {}
        self.source_snapshot: SourceSnapshot | None = None
        self.source_scope: SourceScope | None = None
        self.source_scope_sha256 = ""
        self.source_scope_file_identity: PrivateFileIdentity | None = None
        self.source_container_identity: SourceContainerIdentity | None = None
        self.f0g_source_scope: F0GSourceScope | None = None
        self.f0g_source_scope_sha256 = ""
        self.f0g_source_scope_file_identity: PrivateFileIdentity | None = None
        self.f0g_scope_copy_identity: PrivateFileIdentity | None = None
        self.frozen_f0_inputs: FrozenF0PreparedInputs | None = None
        self.frozen_role_passwords: dict[str, str] = {}
        self.f0f_key_copy_identity: PrivateFileIdentity | None = None
        self.runtime_bundle_copy_identities: dict[str, PrivateFileIdentity] = {}
        self.f0g_source_aggregate_sha256 = ""
        self.f0g_source_dump_sha256 = ""
        self.f0i_template_dump_sha256 = ""
        self.frozen_database_snapshot: FrozenF0DatabaseSnapshot | None = None
        self.fixture_plan_source_identities: dict[str, PrivateFileIdentity] = {}
        self.fixture_source_bundle_identity: PrivateFileIdentity | None = None
        self.fixture_source_records: tuple[SourceObjectSpec, ...] = ()
        self.fixture_manifest_source_identity: PrivateFileIdentity | None = None
        self.fixture_selection_copies: tuple[FixtureSelectionCopy, ...] = ()
        self.checkout_identity: CheckoutIdentity | None = None
        self.python_bridge_identity: ExecutableIdentity | None = None
        self.source_aggregate_sha256 = ""
        self.fixture_inputs: tuple[FixtureInput, ...] = ()
        self.fixture_manifest_sha256 = ""
        self.fixture_source_sha256 = ""
        self.root_migration_dsn = ""
        self.sensitive_canaries: tuple[bytes, ...] = ()
        self.service_count = 0
        self.declared_images: dict[str, str] = {}
        self.build_provenance: dict[str, str] = {}

    @staticmethod
    def _timeout(environment: Mapping[str, str]) -> int:
        try:
            value = int(environment.get("F111_REVERSE_TIMEOUT_SECONDS", "900"))
        except ValueError:
            raise RebuildError("TIMEOUT_REJECTED") from None
        if not 60 <= value <= 900:
            raise RebuildError("TIMEOUT_REJECTED")
        return value

    def _validate_scope(self) -> None:
        match = _PROJECT.fullmatch(self.identity.project)
        if (
            match is None
            or match.group(1) != self.identity.suffix
            or self.identity.database != DATABASE_PREFIX + self.identity.suffix
            or uuid.UUID(hex=self.identity.suffix).version != 4
        ):
            raise RebuildError("RANDOM_SCOPE_REJECTED")
        context = _process(
            ("docker", "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"),
            cwd=ROOT,
            environment=self.environment,
            timeout=30,
            failure_code="DOCKER_CONTEXT_REJECTED",
        ).output
        try:
            endpoint = json.loads(context)
            parsed = urllib.parse.urlsplit(str(endpoint))
        except (json.JSONDecodeError, ValueError):
            raise RebuildError("DOCKER_CONTEXT_REJECTED") from None
        if parsed.scheme != "unix" or parsed.netloc or not Path(parsed.path).is_absolute():
            raise RebuildError("DOCKER_CONTEXT_REJECTED")

    def _probe_absence(self) -> None:
        probes = (
            ("docker", "container", "inspect", self.identity.pg_container),
            ("docker", "volume", "inspect", self.identity.pg_volume),
            ("docker", "network", "inspect", self.identity.project + "_f1net"),
            *(
                (
                    "docker",
                    "volume",
                    "inspect",
                    self.identity.project + "_" + name,
                )
                for name in sorted(EXPECTED_VOLUMES)
            ),
            *(
                ("docker", "image", "inspect", image)
                for image in self._local_images()
            ),
        )
        for command in probes:
            result = _process(
                command,
                cwd=ROOT,
                environment=self.environment,
                timeout=30,
                check=False,
            )
            if result.exit_code == 0:
                raise RebuildError("RANDOM_SCOPE_COLLISION")
        for kind in ("container", "volume", "network"):
            command = ["docker", kind, "ls"]
            if kind == "container":
                command.append("--all")
            command.extend(
                (
                    "-q",
                    "--filter",
                    "label=anhuan.repair-project=" + self.identity.project,
                )
            )
            result = _process(
                tuple(command),
                cwd=ROOT,
                environment=self.environment,
                timeout=30,
                failure_code="DOCKER_BASELINE_REJECTED",
            )
            if result.output.strip():
                raise RebuildError("RANDOM_SCOPE_COLLISION")

    def _reserve_ports(self) -> None:
        for name in PORT_NAMES:
            reserved = False
            for _attempt in range(256):
                port = 20000 + crypto_secrets.randbelow(41000)
                if port in self.ports.values():
                    continue
                stream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    stream.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
                    stream.bind(("127.0.0.1", port))
                    stream.listen(1)
                except OSError:
                    stream.close()
                    continue
                self.ports[name] = port
                self.state.reservations[name] = stream
                reserved = True
                break
            if not reserved:
                raise RebuildError("PORT_RESERVATION_RED")

    def _release_port(self, name: str) -> None:
        stream = self.state.reservations.pop(name, None)
        if stream is not None:
            stream.close()

    def _release_all_ports(self) -> None:
        for name in tuple(self.state.reservations):
            self._release_port(name)

    def _prepare_runtime_scope(self) -> None:
        if self.state.scratch_root is not None:
            if self.state.runtime_home is None or self.state.runtime_temporary is None:
                raise RebuildError("RUNTIME_DIRECTORY_REJECTED")
            _validate_private_docker_config(self.state.runtime_home)
            return
        scratch = Path(
            tempfile.mkdtemp(
                prefix=self.identity.project + f"-round{self.identity.round_number}-",
                dir="/private/tmp",
            )
        )
        scratch.chmod(0o700)
        if not scratch.name.startswith(self.identity.project + "-"):
            raise RebuildError("SCRATCH_SCOPE_REJECTED")
        home = scratch / "home"
        temporary = scratch / "tmp"
        home.mkdir(mode=0o700)
        temporary.mkdir(mode=0o700)
        self.state.scratch_root = scratch
        self.state.runtime_home = home
        self.state.runtime_temporary = temporary
        self.environment = _base_environment(
            {**self.inherited, "HOME": str(home), "TMPDIR": str(temporary)}
        )
        _materialize_private_docker_config(home)

    def _create_clean_checkout(self) -> None:
        self._prepare_runtime_scope()
        scratch = self.state.scratch_root
        if scratch is None:
            raise RebuildError("SCRATCH_SCOPE_REJECTED")
        (
            self.fixture_source_bundle_identity,
            self.fixture_source_records,
            bundle_writes,
        ) = _fixture_source_bundle(
            self._source_secret_directory(), None
        )
        if bundle_writes or len(self.fixture_source_records) != SOURCE_BUNDLE_ENTRY_COUNT:
            raise RebuildError("SOURCE_BUNDLE_BASELINE_MISSING")
        self.source_snapshot = capture_source(self.environment)
        seed = scratch / "seed"
        checkout = scratch / "checkout"
        self.state.seed_repository = seed
        self.state.checkout = checkout
        _copy_source(self.source_snapshot, seed)
        git_environment = dict(self.environment)
        git_environment.update(
            {
                "GIT_AUTHOR_NAME": "F1.1.1 Rebuild",
                "GIT_AUTHOR_EMAIL": "rebuild@fixture.invalid",
                "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
                "GIT_COMMITTER_NAME": "F1.1.1 Rebuild",
                "GIT_COMMITTER_EMAIL": "rebuild@fixture.invalid",
                "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
            }
        )
        _process(
            ("git", "init", "-q", "--initial-branch=rebuild"),
            cwd=seed,
            environment=git_environment,
            timeout=60,
            failure_code="SCRATCH_GIT_REJECTED",
        )
        _process(
            (
                "git",
                "fetch",
                "-q",
                "--no-tags",
                "--no-write-fetch-head",
                str(ROOT),
                BASE_REVISION,
            ),
            cwd=seed,
            environment=git_environment,
            timeout=180,
            failure_code="SCRATCH_GIT_REJECTED",
        )
        _process(
            ("git", "add", "--all", "--force"),
            cwd=seed,
            environment=git_environment,
            timeout=120,
            failure_code="SCRATCH_GIT_REJECTED",
        )
        tree = _process(
            ("git", "write-tree"),
            cwd=seed,
            environment=git_environment,
            timeout=60,
            failure_code="SCRATCH_GIT_REJECTED",
        ).output.strip()
        if not re.fullmatch(rb"[0-9a-f]{40,64}", tree):
            raise RebuildError("SCRATCH_GIT_REJECTED")
        commit = _process(
            (
                "git",
                "commit-tree",
                tree.decode("ascii"),
                "-p",
                BASE_REVISION,
                "-m",
                "fixture rebuild snapshot",
            ),
            cwd=seed,
            environment=git_environment,
            timeout=60,
            failure_code="SCRATCH_GIT_REJECTED",
        ).output.strip()
        if not re.fullmatch(rb"[0-9a-f]{40,64}", commit):
            raise RebuildError("SCRATCH_GIT_REJECTED")
        _process(
            ("git", "update-ref", "refs/heads/rebuild", commit.decode("ascii")),
            cwd=seed,
            environment=git_environment,
            timeout=60,
            failure_code="SCRATCH_GIT_REJECTED",
        )
        _process(
            (
                "git",
                "clone",
                "-q",
                "--no-local",
                "--no-hardlinks",
                "--branch",
                "rebuild",
                str(seed),
                str(checkout),
            ),
            cwd=scratch,
            environment=git_environment,
            timeout=180,
            failure_code="SCRATCH_GIT_REJECTED",
        )
        if _checkout_snapshot(checkout, self.environment) != self.source_snapshot.sha256:
            raise RebuildError("CLEAN_CHECKOUT_REJECTED")
        self.fixture_plan_source_identities = materialize_fixture_plans(
            self._source_secret_directory(), checkout
        )
        self.python_bridge_identity = materialize_checkout_python_bridge(checkout)
        self.checkout_identity = checkout_identity(checkout)
        validate_delivery_checkout(
            checkout,
            self.environment,
            expected_source_sha256=self.source_snapshot.sha256,
            expected_identity=self.checkout_identity,
            expected_python_identity=self.python_bridge_identity,
        )

    def _source_secret_directory(self) -> Path:
        first = self.inherited.get("F1_SECRETS_DIR", "")
        second = self.inherited.get("F111_REVERSE_SECRETS_DIR", first)
        if not first or first != second:
            raise RebuildError("SECRET_SCOPE_REJECTED")
        return _regular_private_directory(Path(first), "SECRET_SCOPE_REJECTED")

    def _prepare_secrets(self) -> None:
        if self.state.scratch_root is None:
            raise RebuildError("SCRATCH_SCOPE_REJECTED")
        source = self._source_secret_directory()
        provider = _regular_private_directory(
            Path(self.inherited.get("F1_PROVIDER_SECRETS_DIR", "")),
            "PROVIDER_SCOPE_REJECTED",
        )
        f0i_key = Path(self.inherited.get("F1_F0I_KEY_FILE", ""))
        private = self.state.scratch_root / "private"
        private.mkdir(mode=0o700)
        target = private / "f1"
        target.mkdir(mode=0o700)
        provider_target = private / "provider"
        provider_target.mkdir(mode=0o700)
        self.state.secrets_directory = target
        self.state.provider_secrets_directory = provider_target
        canaries: list[bytes] = []
        f0i_material = _read_private_file(f0i_key, "F0I_KEY_REJECTED").strip()
        copied_f0i_key = private / "f0i-key"
        _write_private(copied_f0i_key, f0i_material + b"\n")
        self.state.f0i_key_file = copied_f0i_key
        if len(f0i_material) >= 8:
            canaries.append(f0i_material)
        provider_materials: dict[str, bytes] = {}
        for name in PROVIDER_SECRET_FILES:
            material = _read_private_file(
                provider / name, "PROVIDER_FILE_REJECTED"
            ).strip()
            if not material or b"\x00" in material or b"\n" in material:
                raise RebuildError("PROVIDER_FILE_REJECTED")
            provider_materials[name] = material
            _write_private(provider_target / name, material + b"\n")
            if len(material) >= 8:
                canaries.append(material)
        copied: dict[str, bytes] = {}
        for name in COPY_SECRET_FILES:
            raw = _read_private_file(source / name, "SECRET_FILE_REJECTED")
            if b"\x00" in raw or b"\n" in raw.rstrip(b"\n") or not raw.strip():
                raise RebuildError("SECRET_FILE_REJECTED")
            copied[name] = raw.strip()
            _write_private(target / name, raw.strip() + b"\n")
            if len(raw.strip()) >= 8:
                canaries.append(raw.strip())
        for provisioned, client in PASSWORD_BINDINGS:
            if copied[provisioned] != copied[client]:
                raise RebuildError("OIDC_PASSWORD_BINDING_REJECTED")

        source_scope_raw, self.source_scope_file_identity = _private_file_identity(
            source / "f0i_source_scope",
            code="F0I_SOURCE_SCOPE_REJECTED",
            expected_sha256=None,
        )
        self.source_scope = parse_source_scope(source_scope_raw)
        self.source_scope_sha256 = _sha256(source_scope_raw)
        self.source_container_identity = self.source_scope.container
        _write_private(target / "f0i_source_scope", source_scope_raw.strip() + b"\n")
        f0g_scope_raw, self.f0g_source_scope_file_identity = _private_file_identity(
            source / F0G_SOURCE_SCOPE_NAME,
            code="F0G_SOURCE_SCOPE_REJECTED",
            expected_sha256=None,
            maximum=64 * 1024,
        )
        self.f0g_source_scope = parse_f0g_source_scope(f0g_scope_raw)
        if self.f0g_source_scope.container != self.source_container_identity:
            raise RebuildError("F0G_SOURCE_SCOPE_REJECTED")
        self.f0g_source_scope_sha256 = _sha256(f0g_scope_raw)
        _write_private(target / F0G_SOURCE_SCOPE_NAME, f0g_scope_raw)
        _copied_scope_raw, self.f0g_scope_copy_identity = _private_file_identity(
            target / F0G_SOURCE_SCOPE_NAME,
            code="F0G_SOURCE_SCOPE_REJECTED",
            expected_sha256=self.f0g_source_scope_sha256,
            maximum=64 * 1024,
        )
        verify_fixture_plan_sources(source, self.fixture_plan_source_identities)
        for name, (_relative, expected_sha256) in sorted(
            FIXTURE_PLAN_CONTRACTS.items()
        ):
            raw, identity = _private_file_identity(
                source / name,
                code="FIXTURE_PLAN_SOURCE_REJECTED",
                expected_sha256=expected_sha256,
            )
            if identity != self.fixture_plan_source_identities.get(name):
                raise RebuildError("FIXTURE_PLAN_SOURCE_MUTATED")
            _write_private(target / name, raw)
        if self.fixture_source_bundle_identity is None:
            raise RebuildError("SOURCE_BUNDLE_BASELINE_MISSING")
        copy_fixture_source_bundle(
            source,
            target,
            self.fixture_source_bundle_identity,
        )

        fixture_manifest_source, self.fixture_manifest_source_identity = (
            _private_file_identity(
                source / "fixture_manifest",
                code="FIXTURE_MANIFEST_REJECTED",
                expected_sha256=None,
                maximum=262144,
            )
        )
        fixture_manifest, self.fixture_selection_copies = materialize_fixture_selection(
            fixture_manifest_source, private / "fixtures"
        )
        self.fixture_inputs = parse_fixture_manifest(fixture_manifest)
        self.fixture_manifest_sha256 = _sha256(fixture_manifest_source)
        questions: dict[str, bytes] = {}
        for name in ("question_primary", "question_alternate"):
            raw = _read_private_file(source / name, "QUESTION_SECRET_REJECTED").strip()
            try:
                decoded = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise RebuildError("QUESTION_SECRET_REJECTED") from None
            if (
                not decoded
                or len(raw) > 4096
                or b"\x00" in raw
                or any(ord(character) < 0x20 and character not in "\t\n\r" for character in decoded)
            ):
                raise RebuildError("QUESTION_SECRET_REJECTED")
            questions[name] = raw
            if len(raw) >= 8:
                canaries.append(raw)
        if questions["question_primary"] == questions["question_alternate"]:
            raise RebuildError("QUESTION_SECRET_REJECTED")

        bootstrap_password = crypto_secrets.token_urlsafe(36)
        migration_password = crypto_secrets.token_urlsafe(36)
        runtime_password = crypto_secrets.token_urlsafe(36)
        worker_password = crypto_secrets.token_urlsafe(36)
        self.frozen_role_passwords = {
            "f0d_bootstrap": bootstrap_password,
            "f0d_migration": migration_password,
            "f0d_runtime": runtime_password,
            "f0d_worker": worker_password,
        }
        try:
            project_id = uuid.UUID(hex=self.identity.suffix)
        except ValueError:
            raise RebuildError("RANDOM_SCOPE_REJECTED") from None
        self.frozen_f0_inputs = prepare_frozen_f0_inputs(
            source,
            project_id,
            self.ports["postgres"],
            self.frozen_role_passwords,
        )
        for tree in self.frozen_f0_inputs.runtime_trees:
            self.runtime_bundle_copy_identities[tree.phase] = (
                copy_frozen_runtime_tree_bundle(
                source,
                target,
                tree.phase,
                tree.bundle_identity,
            )
            )
        f0f_key_raw, f0f_key_identity = _private_file_identity(
            source / F0F_SOURCE_KEY_NAME,
            code="FROZEN_F0_KEY_REJECTED",
            expected_sha256=self.frozen_f0_inputs.source_key_identity.sha256,
            maximum=64,
        )
        if f0f_key_identity != self.frozen_f0_inputs.source_key_identity:
            raise RebuildError("FROZEN_F0_KEY_MUTATED")
        _write_private(target / F0F_SOURCE_KEY_NAME, f0f_key_raw)
        _copied_f0f_key, self.f0f_key_copy_identity = _private_file_identity(
            target / F0F_SOURCE_KEY_NAME,
            code="FROZEN_F0_KEY_REJECTED",
            expected_sha256=self.frozen_f0_inputs.source_key_identity.sha256,
            maximum=64,
        )
        _write_private(target / "postgres_bootstrap_password", (bootstrap_password + "\n").encode())
        host = "host.docker.internal"
        port = self.ports["postgres"]

        def dsn(user: str, password: str) -> str:
            encoded_user = urllib.parse.quote(user, safe="")
            encoded_password = urllib.parse.quote(password, safe="")
            return (
                f"postgresql://{encoded_user}:{encoded_password}@{host}:{port}/"
                f"{self.identity.database}"
            )

        bootstrap_dsn = dsn("f0d_bootstrap", bootstrap_password)
        migration_dsn = dsn("f0d_migration", migration_password)
        canaries.extend(
            value.encode("ascii")
            for value in (
                bootstrap_password,
                migration_password,
                runtime_password,
                worker_password,
                bootstrap_dsn,
                migration_dsn,
            )
        )
        _write_private(target / "f1_bootstrap_dsn", (bootstrap_dsn + "\n").encode())
        _write_private(target / "f1_migration_dsn", (migration_dsn + "\n").encode())
        self.root_migration_dsn = migration_dsn
        target_database_env = private / "target-database.env"
        _write_private(
            target_database_env,
            database_environment(
                DatabaseEndpoint(
                    host="host.docker.internal",
                    port=port,
                    database=self.identity.database,
                    user="f0d_bootstrap",
                    password=bootstrap_password,
                )
            ),
        )
        self.state.target_database_environment_file = target_database_env
        self.state.source_data_dump = private / "f0i-source-data.sql"
        self.state.source_data_after_dump = private / "f0i-source-data-after.sql"
        self.state.target_data_dump = private / "f0i-target-data.sql"
        self.state.f0g_source_data_dump = private / "f0g-source-data.sql"
        self.state.f0g_source_data_after_dump = private / "f0g-source-data-after.sql"
        self.state.f0g_target_data_dump = private / "f0g-target-data.sql"
        self.state.f0g_target_data_after_dump = private / "f0g-target-data-after.sql"
        self.state.f0i_template_data_dump = private / "f0i-template-data.sql"
        self.state.f0i_template_data_after_dump = private / "f0i-template-data-after.sql"
        isolation = self.frozen_f0_inputs.isolation
        for attribute, database in (
            ("f0g_database_environment_file", isolation.f0g_template_database),
            ("f0i_database_environment_file", isolation.f0i_template_database),
        ):
            path = private / (database + ".env")
            _write_private(
                path,
                database_environment(
                    DatabaseEndpoint(
                        host="host.docker.internal",
                        port=port,
                        database=database,
                        user="f0d_bootstrap",
                        password=bootstrap_password,
                    )
                ),
            )
            setattr(self.state, attribute, path)
        f0g_migration_environment = private / "f0g-migration.env"
        _write_private(
            f0g_migration_environment,
            (
                "F0D_MIGRATION_DSN="
                + _sqlalchemy_psycopg_dsn(
                    migration_dsn.rsplit("/", 1)[0]
                    + "/"
                    + isolation.f0g_template_database
                )
                + "\n"
            ).encode("ascii"),
        )
        self.state.f0g_migration_environment_file = f0g_migration_environment
        migration_env = private / "root-migration.env"
        _write_private(
            migration_env,
            ("F0D_MIGRATION_DSN=" + migration_dsn + "\n").encode(),
        )
        self.state.migration_environment_file = migration_env
        f1_env = private / "f1-runtime.env"
        lines = {
            "F1_SECRETS_DIR": "/run/secrets/f1",
            "F1_PG_HOST": host,
            "F1_PG_PORT": str(port),
            "F1_PG_DATABASE": self.identity.database,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": "/app/src",
        }
        _write_private(
            f1_env,
            "".join(f"{key}={value}\n" for key, value in sorted(lines.items())).encode(),
        )
        self.state.f1_environment_file = f1_env

        def host_dsn(user: str, password: bytes | str) -> str:
            value = password.decode("utf-8") if isinstance(password, bytes) else password
            return (
                "postgresql://"
                + urllib.parse.quote(user, safe="")
                + ":"
                + urllib.parse.quote(value, safe="")
                + f"@127.0.0.1:{port}/{self.identity.database}"
            )

        control_dsn = host_dsn("f0d_bootstrap", bootstrap_password)
        worker_dsn = host_dsn("f1_worker", copied["f1_worker_password"])
        invitee_email = copied["invitee_username"]
        try:
            invitee_email_text = invitee_email.decode("utf-8")
        except UnicodeDecodeError:
            raise RebuildError("INVITEE_EMAIL_REJECTED") from None
        if (
            not 3 <= len(invitee_email_text) <= 254
            or invitee_email_text.count("@") != 1
            or any(character.isspace() for character in invitee_email_text)
        ):
            raise RebuildError("INVITEE_EMAIL_REJECTED")
        leak_values = [
            "f111-canary-" + crypto_secrets.token_urlsafe(32),
            "f111-canary-" + crypto_secrets.token_urlsafe(32),
        ]
        reverse_files: dict[str, bytes] = {
            "control_dsn": control_dsn.encode("ascii"),
            "enterprise_a_id": b"10000000-0000-4000-8000-00000000000a",
            "enterprise_b_id": b"10000000-0000-4000-8000-00000000000b",
            "fixture_manifest": fixture_manifest,
            "invitee_email": invitee_email,
            "jaeger_base_url": f"http://127.0.0.1:{self.ports['jaeger_ui']}".encode(),
            "leak_canaries": _canonical_bytes(leak_values).rstrip(b"\n"),
            "minio_access_key": copied["minio_root_user"],
            "minio_bucket": b"anhuan-f1-documents",
            "minio_endpoint": f"127.0.0.1:{self.ports['minio_api']}".encode(),
            "minio_secret_key": copied["minio_root_password"],
            "minio_secure": b"false",
            "question_alternate": questions["question_alternate"],
            "question_primary": questions["question_primary"],
            "ragflow_api_key": provider_materials["ragflow_api_key"],
            "ragflow_base_url": f"http://127.0.0.1:{self.ports['ragflow_api']}".encode(),
            "redis_url": f"redis://127.0.0.1:{self.ports['redis']}/0".encode(),
            "worker_dsn": worker_dsn.encode("ascii"),
        }
        for name, raw in reverse_files.items():
            _write_private(target / name, raw.rstrip(b"\n") + b"\n")
        canaries.extend(
            (
                control_dsn.encode("ascii"),
                worker_dsn.encode("ascii"),
                *(value.encode("ascii") for value in leak_values),
            )
        )
        self.sensitive_canaries = tuple(dict.fromkeys(canaries))
        self.environment.update(
            {
                "F1_SECRETS_DIR": str(target),
                "F1_PROVIDER_SECRETS_DIR": str(provider_target),
                "F1_F0I_KEY_FILE": str(copied_f0i_key),
                "F1_PG_HOST": "host.docker.internal",
                "F1_PG_PORT": str(port),
                "F1_PG_DATABASE": self.identity.database,
                "F1_API_HOST_PORT": str(self.ports["api"]),
                "F1_GRAFANA_HOST_PORT": str(self.ports["grafana"]),
                "F1_JAEGER_OTLP_GRPC_HOST_PORT": str(self.ports["jaeger_grpc"]),
                "F1_JAEGER_OTLP_HTTP_HOST_PORT": str(self.ports["jaeger_http"]),
                "F1_JAEGER_UI_HOST_PORT": str(self.ports["jaeger_ui"]),
                "F1_KEYCLOAK_HOST_PORT": str(self.ports["keycloak"]),
                "F1_MINIO_API_HOST_PORT": str(self.ports["minio_api"]),
                "F1_MINIO_CONSOLE_HOST_PORT": str(self.ports["minio_console"]),
                "F1_PROMETHEUS_HOST_PORT": str(self.ports["prometheus"]),
                "F1_RAGFLOW_API_HOST_PORT": str(self.ports["ragflow_api"]),
                "F1_RAGFLOW_HTTP_HOST_PORT": str(self.ports["ragflow_http"]),
                "F1_REDIS_HOST_PORT": str(self.ports["redis"]),
                "F1_WEB_HOST_PORT": str(self.ports["web"]),
                "F1_KEYCLOAK_ISSUER_URL": (
                    f"http://127.0.0.1:{self.ports['keycloak']}/realms/anhuan"
                ),
                "F1_WEB_PUBLIC_ORIGIN": f"http://127.0.0.1:{self.ports['web']}",
                "F111_REVERSE_PROJECT": self.identity.project,
                F0_ISOLATION_ENVIRONMENT_VARIABLE: str(
                    self.frozen_f0_inputs.config_path
                ),
            }
        )

    def _compose_command(self, *arguments: str) -> tuple[str, ...]:
        if self.state.checkout is None:
            raise RebuildError("CLEAN_CHECKOUT_REJECTED")
        return (
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-p",
            self.identity.project,
            "-f",
            str(self.state.checkout / "infra/f1/docker-compose.yml"),
            "-f",
            str(self.state.checkout / "infra/f1/docker-compose.repair.yml"),
            *arguments,
        )

    def _validate_compose(self) -> None:
        if self.state.checkout is None:
            raise RebuildError("CLEAN_CHECKOUT_REJECTED")
        result = _process(
            self._compose_command("config", "--format", "json"),
            cwd=self.state.checkout,
            environment=self.environment,
            timeout=120,
            failure_code="COMPOSE_CONFIG_RED",
        )
        try:
            payload = json.loads(result.output)
        except json.JSONDecodeError:
            raise RebuildError("COMPOSE_CONFIG_RED") from None
        self.service_count = validate_compose_payload(
            payload,
            self.identity,
            self.ports["postgres"],
            expected_ports=self.ports,
        )
        services = payload["services"]
        declared: dict[str, str] = {}
        for name in sorted(EXPECTED_SERVICES):
            local = expected_local_image(self.identity, name)
            if local is None:
                declared[name] = str(services[name]["image"])
            elif name in {"api", "keycloak-provisioner"}:
                declared[name] = "local:api"
            elif name in {"worker", "dispatcher"}:
                declared[name] = "local:worker"
            else:
                declared[name] = "local:web"
        self.declared_images = declared

    def _local_images(self) -> tuple[str, str, str]:
        return (
            f"anhuan-f111-repair-api:{self.identity.project}",
            f"anhuan-f111-repair-worker:{self.identity.project}",
            f"anhuan-f111-repair-web:{self.identity.project}",
        )

    def _build_images(self) -> None:
        if self.state.checkout is None or self.source_snapshot is None:
            raise RebuildError("CLEAN_CHECKOUT_REJECTED")
        self.build_provenance = build_provenance(
            self.state.checkout, self.source_snapshot.sha256
        )
        build_arguments: list[str] = []
        for key in sorted(BUILD_PROVENANCE_ARGS):
            build_arguments.extend(
                (
                    "--build-arg",
                    BUILD_PROVENANCE_ARGS[key] + "=" + self.build_provenance[key],
                )
            )
        # The random tags were proven absent before this point.  From here on
        # cleanup owns them even when BuildKit fails after creating only one.
        self.state.local_images_created = True
        _process(
            self._compose_command(
                "build",
                "--pull",
                "--no-cache",
                "--build-arg",
                "SOURCE_DATE_EPOCH=946684800",
                *build_arguments,
                "api",
                "worker",
                "web",
            ),
            cwd=self.state.checkout,
            environment=self.environment,
            timeout=self.timeout,
            failure_code="NO_CACHE_BUILD_RED",
        )
        for image in self._local_images():
            result = _process(
                ("docker", "image", "inspect", "--format", "{{.Id}}", image),
                cwd=self.state.checkout,
                environment=self.environment,
                timeout=60,
                failure_code="BUILT_IMAGE_MISSING",
            )
            if not re.fullmatch(rb"sha256:[0-9a-f]{64}\n?", result.output):
                raise RebuildError("BUILT_IMAGE_MISSING")

    def _start_postgres(self) -> None:
        if self.state.secrets_directory is None:
            raise RebuildError("SECRET_SCOPE_REJECTED")
        self._release_port("postgres")
        assert_owned_resource(self.identity.pg_volume, self.identity)
        _process(
            (
                "docker",
                "volume",
                "create",
                "--label",
                "anhuan.scope=f111-repair",
                "--label",
                "anhuan.repair-project=" + self.identity.project,
                self.identity.pg_volume,
            ),
            cwd=ROOT,
            environment=self.environment,
            timeout=60,
            failure_code="PG_VOLUME_RED",
        )
        self.state.pg_volume_created = True
        assert_owned_resource(self.identity.pg_container, self.identity)
        _process(
            (
                "docker",
                "run",
                "-d",
                "--name",
                self.identity.pg_container,
                "--label",
                "anhuan.scope=f111-repair",
                "--label",
                "anhuan.repair-project=" + self.identity.project,
                "--restart=no",
                "--security-opt",
                "no-new-privileges:true",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,noexec,size=67108864",
                "-e",
                "POSTGRES_DB=" + self.identity.database,
                "-e",
                "POSTGRES_USER=f0d_bootstrap",
                "-e",
                "POSTGRES_PASSWORD_FILE=/run/secrets/bootstrap",
                "-e",
                "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256 --auth-local=trust --data-checksums",
                "-e",
                "PGDATA=/var/lib/postgresql/18/docker",
                "-p",
                f"127.0.0.1:{self.ports['postgres']}:5432",
                "-v",
                self.identity.pg_volume + ":/var/lib/postgresql",
                "-v",
                str(self.state.secrets_directory / "postgres_bootstrap_password")
                + ":/run/secrets/bootstrap:ro",
                PG_IMAGE,
            ),
            cwd=ROOT,
            environment=self.environment,
            timeout=120,
            failure_code="PG_START_RED",
        )
        self.state.pg_container_created = True
        deadline = time.monotonic() + min(self.timeout, 180)
        while time.monotonic() < deadline:
            result = _process(
                (
                    "docker",
                    "exec",
                    self.identity.pg_container,
                    "pg_isready",
                    "--username=f0d_bootstrap",
                    "--dbname=" + self.identity.database,
                    "--host=127.0.0.1",
                    "--port=5432",
                ),
                cwd=ROOT,
                environment=self.environment,
                timeout=15,
                check=False,
            )
            if result.exit_code == 0:
                return
            time.sleep(1)
        raise RebuildError("PG_HEALTH_RED")

    @staticmethod
    def _sql_literal(value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value):
            raise RebuildError("GENERATED_SECRET_REJECTED")
        return "'" + value + "'"

    def _bootstrap_database(self) -> None:
        if (
            self.state.secrets_directory is None
            or set(self.frozen_role_passwords)
            != {
                "f0d_bootstrap",
                "f0d_migration",
                "f0d_runtime",
                "f0d_worker",
            }
        ):
            raise RebuildError("SECRET_SCOPE_REJECTED")
        bootstrap = _read_private_file(
            self.state.secrets_directory / "postgres_bootstrap_password",
            "SECRET_FILE_REJECTED",
        ).decode("ascii").strip()
        migration_url = urllib.parse.urlsplit(self.root_migration_dsn)
        migration_password = urllib.parse.unquote(migration_url.password or "")
        runtime_password = self.frozen_role_passwords["f0d_runtime"]
        worker_password = self.frozen_role_passwords["f0d_worker"]
        if (
            bootstrap != self.frozen_role_passwords["f0d_bootstrap"]
            or migration_password != self.frozen_role_passwords["f0d_migration"]
        ):
            raise RebuildError("GENERATED_SECRET_REJECTED")
        statement = f"""
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE {self.identity.database} FROM PUBLIC;
CREATE ROLE f0d_migration LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 4 PASSWORD {self._sql_literal(migration_password)};
CREATE ROLE f0d_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 20 PASSWORD {self._sql_literal(runtime_password)};
CREATE ROLE f0d_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 10 PASSWORD {self._sql_literal(worker_password)};
GRANT CONNECT, CREATE ON DATABASE {self.identity.database} TO f0d_migration;
GRANT CONNECT ON DATABASE {self.identity.database} TO f0d_runtime, f0d_worker;
CREATE SCHEMA f0d AUTHORIZATION f0d_migration;
REVOKE ALL ON SCHEMA f0d FROM PUBLIC;
ALTER ROLE f0d_migration SET search_path = f0d, pg_catalog;
ALTER ROLE f0d_runtime SET search_path = f0d, pg_catalog;
ALTER ROLE f0d_worker SET search_path = f0d, pg_catalog;
ALTER ROLE f0d_migration SET statement_timeout = '60s';
ALTER ROLE f0d_runtime SET statement_timeout = '15s';
ALTER ROLE f0d_worker SET statement_timeout = '60s';
ALTER ROLE f0d_migration SET lock_timeout = '10s';
ALTER ROLE f0d_runtime SET lock_timeout = '3s';
ALTER ROLE f0d_worker SET lock_timeout = '5s';
""".encode("ascii")
        del bootstrap
        _process(
            (
                "docker",
                "exec",
                "-i",
                self.identity.pg_container,
                "psql",
                "--no-psqlrc",
                "--set=ON_ERROR_STOP=1",
                "--username=f0d_bootstrap",
                "--dbname=" + self.identity.database,
            ),
            cwd=ROOT,
            environment=self.environment,
            timeout=120,
            input_bytes=statement,
            failure_code="PG_BOOTSTRAP_RED",
        )

    def _image_run(
        self,
        *command: str,
        env_file: Path,
        mounts: Sequence[tuple[Path, str]] = (),
        failure_code: str,
        timeout: int | None = None,
    ) -> ProcessResult:
        if self.state.checkout is None or self.state.secrets_directory is None:
            raise RebuildError("CLEAN_CHECKOUT_REJECTED")
        arguments: list[str] = [
            "docker",
            "run",
            "--rm",
            "--label",
            "anhuan.scope=f111-repair",
            "--label",
            "anhuan.repair-project=" + self.identity.project,
            "--add-host",
            "host.docker.internal:host-gateway",
            "--env-file",
            str(env_file),
            "-v",
            str(self.state.secrets_directory) + ":/run/secrets/f1:ro",
        ]
        for source, destination in mounts:
            arguments.extend(("-v", str(source) + ":" + destination + ":ro"))
        arguments.append(self._local_images()[0])
        arguments.extend(command)
        return _process(
            tuple(arguments),
            cwd=self.state.checkout,
            environment=self.environment,
            timeout=timeout or self.timeout,
            failure_code=failure_code,
        )

    def _database_container_command(
        self,
        env_file: Path,
        *command: str,
        dump_mount: bool = False,
        dump_path: Path | None = None,
    ) -> tuple[str, ...]:
        if env_file not in {
            self.state.target_database_environment_file,
            self.state.f0g_database_environment_file,
            self.state.f0i_database_environment_file,
        }:
            raise RebuildError("DATABASE_ENVIRONMENT_REJECTED")
        arguments: list[str] = [
            "docker",
            "run",
            "--rm",
            "--label",
            "anhuan.scope=f111-repair",
            "--label",
            "anhuan.repair-project=" + self.identity.project,
            "--security-opt",
            "no-new-privileges:true",
            "--cap-drop=ALL",
            "--add-host",
            "host.docker.internal:host-gateway",
            "--env-file",
            str(env_file),
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=67108864",
        ]
        if dump_mount and dump_path is not None:
            raise RebuildError("SOURCE_DUMP_REJECTED")
        if dump_mount or dump_path is not None:
            dump = self.state.source_data_dump if dump_mount else dump_path
            if (
                dump is None
                or not dump.is_absolute()
                or dump
                not in {
                    self.state.source_data_dump,
                    self.state.f0g_source_data_dump,
                }
            ):
                raise RebuildError("SOURCE_DUMP_REJECTED")
            arguments.extend(("-v", str(dump) + ":/input/source.sql:ro"))
        arguments.append(PG_IMAGE)
        arguments.extend(command)
        return tuple(arguments)

    def _source_container_inspect(self) -> None:
        if self.source_container_identity is None or self.state.checkout is None:
            raise RebuildError("F0I_SOURCE_SCOPE_REJECTED")
        result = _process(
            (
                "docker",
                "container",
                "inspect",
                "--format",
                _SOURCE_INSPECT_FORMAT,
                self.source_container_identity.container_id,
            ),
            cwd=self.state.checkout,
            environment=self.environment,
            timeout=60,
            failure_code="F0I_SOURCE_CONTAINER_REJECTED",
        )
        validate_source_container_inspect(result.output, self.source_container_identity)

    def _source_database_aggregate(self) -> str:
        if self.source_container_identity is None or self.state.checkout is None:
            raise RebuildError("F0I_SOURCE_SCOPE_REJECTED")
        statement = (
            "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY, DEFERRABLE;\n"
            "SELECT current_database(),current_user,"
            "current_setting('transaction_read_only'),"
            "COALESCE((SELECT string_agg(version_num,',' ORDER BY version_num) "
            "FROM f0d.alembic_version),''),"
            "(SELECT count(*) FROM f0d.fixture_source_registry),"
            "(SELECT count(*) FROM f0i.document_scope),"
            "(SELECT count(*) FROM f0i.page),"
            "(SELECT count(*) FROM f0i.chunk);\n"
            "COMMIT;\n"
        ).encode("ascii")
        result = _process(
            (
                "docker",
                "exec",
                "-i",
                "--user",
                "postgres",
                self.source_container_identity.container_id,
                "psql",
                "--username=" + SOURCE_DATABASE_SUPERUSER,
                "--dbname=" + SOURCE_DATABASE_NAME,
                "--no-password",
                "--no-psqlrc",
                "--quiet",
                "--tuples-only",
                "--no-align",
                "--field-separator=|",
                "--set=ON_ERROR_STOP=1",
            ),
            cwd=self.state.checkout,
            environment=self.environment,
            timeout=120,
            input_bytes=statement,
            failure_code="F0I_SOURCE_AGGREGATE_RED",
        )
        return parse_source_aggregate(result.output)

    def _source_dump(self, destination: Path) -> str:
        if self.source_container_identity is None or self.state.checkout is None:
            raise RebuildError("F0I_SOURCE_SCOPE_REJECTED")
        _process_to_private_file(
            (
                "docker",
                "exec",
                "--user",
                "postgres",
                self.source_container_identity.container_id,
                "pg_dump",
                "--username=" + SOURCE_DATABASE_SUPERUSER,
                "--dbname=" + SOURCE_DATABASE_NAME,
                "--format=plain",
                "--data-only",
                "--no-owner",
                "--no-privileges",
                "--schema=f0d",
                "--schema=f0i",
                "--exclude-table-data=f0d.alembic_version",
            ),
            cwd=self.state.checkout,
            environment=self.environment,
            timeout=min(self.timeout, 900),
            destination=destination,
            failure_code="F0I_SOURCE_DUMP_RED",
        )
        return normalized_data_dump_digest(destination)

    def _f0g_source_aggregate(self) -> str:
        if (
            self.f0g_source_scope is None
            or self.source_container_identity is None
            or self.state.checkout is None
            or self.f0g_source_scope.container != self.source_container_identity
        ):
            raise RebuildError("F0G_SOURCE_SCOPE_REJECTED")
        result = _process(
            (
                "docker",
                "exec",
                "-i",
                "--user",
                "postgres",
                self.source_container_identity.container_id,
                "psql",
                "--username=" + F0G_SOURCE_ROLE,
                "--dbname=" + F0G_SOURCE_DATABASE_NAME,
                "--no-password",
                "--no-psqlrc",
                "--quiet",
                "--tuples-only",
                "--no-align",
                "--field-separator=|",
                "--set=ON_ERROR_STOP=1",
            ),
            cwd=self.state.checkout,
            environment=self.environment,
            timeout=120,
            input_bytes=f0g_source_aggregate_statement(),
            failure_code="F0G_SOURCE_AGGREGATE_RED",
        )
        return parse_f0g_source_aggregate(result.output)

    def _f0g_source_dump(self, destination: Path) -> str:
        if (
            self.f0g_source_scope is None
            or self.source_container_identity is None
            or self.state.checkout is None
            or self.f0g_source_scope.container != self.source_container_identity
        ):
            raise RebuildError("F0G_SOURCE_SCOPE_REJECTED")
        _process_to_private_file(
            (
                "docker",
                "exec",
                "--user",
                "postgres",
                self.source_container_identity.container_id,
                "pg_dump",
                "--username=" + F0G_SOURCE_ROLE,
                "--dbname=" + F0G_SOURCE_DATABASE_NAME,
                "--format=plain",
                "--data-only",
                "--no-owner",
                "--no-privileges",
                "--schema=f0d",
                "--schema=f0e",
                "--schema=f0f",
                "--exclude-table-data=f0d.alembic_version",
            ),
            cwd=self.state.checkout,
            environment=self.environment,
            timeout=min(self.timeout, 900),
            destination=destination,
            failure_code="F0G_SOURCE_DUMP_RED",
        )
        return normalized_f0g_data_dump_digest(destination)

    def _frozen_isolation(self) -> FrozenF0Isolation:
        if self.frozen_f0_inputs is None:
            raise RebuildError("FROZEN_F0_BASELINE_MISSING")
        isolation = self.frozen_f0_inputs.isolation
        if (
            isolation.project_id.hex != self.identity.suffix
            or isolation.postgres_port != self.ports.get("postgres")
        ):
            raise RebuildError("FROZEN_F0_CONFIG_MUTATED")
        return isolation

    @staticmethod
    def _sql_identifier(value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,62}", value):
            raise RebuildError("FROZEN_F0_DATABASE_REJECTED")
        return '"' + value + '"'

    def _isolated_scalar(self, database: str, statement: str) -> str:
        isolation = self._frozen_isolation()
        if (
            database not in {"postgres", self.identity.database, *isolation.managed_database_names}
            or not statement
            or "\x00" in statement
        ):
            raise RebuildError("FROZEN_F0_DATABASE_REJECTED")
        result = _process(
            (
                "docker",
                "exec",
                self.identity.pg_container,
                "psql",
                "--no-psqlrc",
                "--quiet",
                "--tuples-only",
                "--no-align",
                "--set=ON_ERROR_STOP=1",
                "--username=f0d_bootstrap",
                "--dbname=" + database,
                "--command",
                statement,
            ),
            cwd=ROOT,
            environment=self.environment,
            timeout=120,
            failure_code="FROZEN_F0_DATABASE_RED",
        )
        try:
            value = result.output.decode("ascii").strip()
        except UnicodeDecodeError:
            raise RebuildError("FROZEN_F0_DATABASE_RED") from None
        if not value or "\n" in value:
            raise RebuildError("FROZEN_F0_DATABASE_RED")
        return value

    def _create_isolated_database(
        self, database: str, *, template: str | None = None
    ) -> None:
        isolation = self._frozen_isolation()
        if database not in {
            isolation.f0g_template_database,
            isolation.f0i_template_database,
        }:
            raise RebuildError("FROZEN_F0_DATABASE_REJECTED")
        if template is not None and template not in {
            self.identity.database,
            isolation.f0g_template_database,
        }:
            raise RebuildError("FROZEN_F0_DATABASE_REJECTED")
        if self._isolated_scalar(
            "postgres",
            "SELECT count(*) FROM pg_database WHERE datname='" + database + "'",
        ) != "0":
            raise RebuildError("FROZEN_F0_DATABASE_COLLISION")
        suffix = ""
        if template is not None:
            if self._isolated_scalar(
                "postgres",
                "SELECT count(*) FROM pg_stat_activity WHERE datname='"
                + template
                + "'",
            ) != "0":
                raise RebuildError("FROZEN_F0_TEMPLATE_BUSY")
            suffix = " TEMPLATE " + self._sql_identifier(template)
        _process(
            (
                "docker",
                "exec",
                self.identity.pg_container,
                "psql",
                "--no-psqlrc",
                "--quiet",
                "--set=ON_ERROR_STOP=1",
                "--username=f0d_bootstrap",
                "--dbname=postgres",
                "--command",
                "CREATE DATABASE "
                + self._sql_identifier(database)
                + " OWNER f0d_migration"
                + suffix,
            ),
            cwd=ROOT,
            environment=self.environment,
            timeout=180,
            failure_code="FROZEN_F0_DATABASE_CREATE_RED",
        )
        if template is None:
            quoted = self._sql_identifier(database)
            bootstrap = (
                "REVOKE CREATE ON SCHEMA public FROM PUBLIC;"
                "REVOKE ALL ON DATABASE "
                + quoted
                + " FROM PUBLIC;"
                "GRANT CONNECT, CREATE ON DATABASE "
                + quoted
                + " TO f0d_migration;"
                "GRANT CONNECT ON DATABASE "
                + quoted
                + " TO f0d_runtime, f0d_worker;"
                "CREATE SCHEMA f0d AUTHORIZATION f0d_migration;"
                "REVOKE ALL ON SCHEMA f0d FROM PUBLIC;"
            )
            _process(
                (
                    "docker",
                    "exec",
                    self.identity.pg_container,
                    "psql",
                    "--no-psqlrc",
                    "--quiet",
                    "--set=ON_ERROR_STOP=1",
                    "--username=f0d_bootstrap",
                    "--dbname=" + database,
                    "--command",
                    bootstrap,
                ),
                cwd=ROOT,
                environment=self.environment,
                timeout=120,
                failure_code="FROZEN_F0_DATABASE_CREATE_RED",
            )
        if self._isolated_scalar(
            "postgres",
            "SELECT count(*) FROM pg_database WHERE datname='" + database + "'",
        ) != "1":
            raise RebuildError("FROZEN_F0_DATABASE_CREATE_RED")

    def _isolated_database_dump(
        self,
        database: str,
        destination: Path,
        *,
        schemas: tuple[str, ...],
    ) -> str:
        isolation = self._frozen_isolation()
        if database == isolation.f0g_template_database:
            env_file = self.state.f0g_database_environment_file
            digest = normalized_f0g_data_dump_digest
        elif database == isolation.f0i_template_database:
            env_file = self.state.f0i_database_environment_file
            digest = normalized_data_dump_digest
        else:
            raise RebuildError("FROZEN_F0_DATABASE_REJECTED")
        if env_file is None or schemas not in {F0G_SOURCE_SCHEMAS, SOURCE_DATABASE_SCHEMAS}:
            raise RebuildError("FROZEN_F0_DATABASE_REJECTED")
        arguments: list[str] = [
            "pg_dump",
            "--dbname=" + database,
            "--format=plain",
            "--data-only",
            "--no-owner",
            "--no-privileges",
        ]
        arguments.extend("--schema=" + schema for schema in schemas)
        arguments.append("--exclude-table-data=f0d.alembic_version")
        _process_to_private_file(
            self._database_container_command(env_file, *arguments),
            cwd=self.state.checkout or ROOT,
            environment=self.environment,
            timeout=min(self.timeout, 900),
            destination=destination,
            failure_code="FROZEN_F0_TARGET_DUMP_RED",
        )
        return digest(destination)

    def _prepare_f0g_template(self) -> None:
        isolation = self._frozen_isolation()
        if (
            self.f0g_source_scope is None
            or self.state.f0g_source_data_dump is None
            or self.state.f0g_target_data_dump is None
            or self.state.f0g_database_environment_file is None
            or self.state.f0g_migration_environment_file is None
        ):
            raise RebuildError("F0G_SOURCE_SCOPE_REJECTED")
        self._source_container_inspect()
        aggregate = self._f0g_source_aggregate()
        source_dump = self._f0g_source_dump(self.state.f0g_source_data_dump)
        self._source_container_inspect()
        if (
            aggregate != self.f0g_source_scope.aggregate_sha256
            or source_dump != self.f0g_source_scope.dump_sha256
            or self._f0g_source_aggregate() != aggregate
        ):
            raise RebuildError("F0G_SOURCE_MUTATED")
        self.f0g_source_aggregate_sha256 = aggregate
        self.f0g_source_dump_sha256 = source_dump
        self._create_isolated_database(isolation.f0g_template_database)
        migrated = self._image_run(
            "python",
            "-B",
            "-m",
            "alembic",
            "-c",
            "/app/alembic.ini",
            "upgrade",
            "f0d_0004",
            env_file=self.state.f0g_migration_environment_file,
            failure_code="F0G_TEMPLATE_MIGRATION_RED",
        )
        if migrated.exit_code != 0:
            raise RebuildError("F0G_TEMPLATE_MIGRATION_RED")
        restored = _process(
            self._database_container_command(
                self.state.f0g_database_environment_file,
                "psql",
                "--no-psqlrc",
                "--quiet",
                "--set=ON_ERROR_STOP=1",
                "--single-transaction",
                "--dbname=" + isolation.f0g_template_database,
                "--file=/input/source.sql",
                dump_path=self.state.f0g_source_data_dump,
            ),
            cwd=self.state.checkout or ROOT,
            environment=self.environment,
            timeout=min(self.timeout, 900),
            failure_code="F0G_TEMPLATE_RESTORE_RED",
        )
        if restored.output.strip():
            raise RebuildError("F0G_TEMPLATE_RESTORE_RED")
        target = self._isolated_database_dump(
            isolation.f0g_template_database,
            self.state.f0g_target_data_dump,
            schemas=F0G_SOURCE_SCHEMAS,
        )
        head = self._isolated_scalar(
            isolation.f0g_template_database,
            "SELECT version_num FROM f0d.alembic_version",
        )
        if target != source_dump or head != "f0d_0004":
            raise RebuildError("F0G_TEMPLATE_RESTORE_MISMATCH")

    def _prepare_f0i_template(self) -> None:
        isolation = self._frozen_isolation()
        if (
            self.state.f0i_template_data_dump is None
            or not self.fixture_source_sha256
        ):
            raise RebuildError("F0I_TEMPLATE_REJECTED")
        self._create_isolated_database(
            isolation.f0i_template_database, template=self.identity.database
        )
        observed = self._isolated_database_dump(
            isolation.f0i_template_database,
            self.state.f0i_template_data_dump,
            schemas=SOURCE_DATABASE_SCHEMAS,
        )
        contract = self._isolated_scalar(
            isolation.f0i_template_database,
            "SELECT (SELECT version_num FROM f0d.alembic_version LIMIT 1)"
            "||'|'||(to_regnamespace('f1') IS NULL)::text",
        )
        if observed != self.fixture_source_sha256 or contract != "f0d_0006|true":
            raise RebuildError("F0I_TEMPLATE_REJECTED")
        self.f0i_template_dump_sha256 = observed

    def _f0g_source_unchanged(self) -> None:
        if (
            self.f0g_source_scope is None
            or self.state.f0g_source_data_after_dump is None
            or not self.f0g_source_aggregate_sha256
            or not self.f0g_source_dump_sha256
        ):
            raise RebuildError("F0G_SOURCE_BASELINE_MISSING")
        self._source_container_inspect()
        aggregate = self._f0g_source_aggregate()
        dump = self._f0g_source_dump(self.state.f0g_source_data_after_dump)
        self._source_container_inspect()
        if (
            aggregate != self.f0g_source_aggregate_sha256
            or dump != self.f0g_source_dump_sha256
            or aggregate != self.f0g_source_scope.aggregate_sha256
            or dump != self.f0g_source_scope.dump_sha256
        ):
            raise RebuildError("F0G_SOURCE_MUTATED")

    def _frozen_f0_projects_absent(self) -> None:
        isolation = self._frozen_isolation()
        verify_frozen_f0_project_absence(
            isolation,
            self.environment,
            cwd=self.state.checkout or ROOT,
        )

    def _record_frozen_database_baseline(self) -> None:
        if self.frozen_database_snapshot is not None:
            raise RebuildError("FROZEN_F0_DATABASE_BASELINE_REJECTED")
        self.frozen_database_snapshot = capture_frozen_f0_database_snapshot(
            self.identity.project,
            self._frozen_isolation(),
            self.environment,
            cwd=self.state.checkout or ROOT,
        )

    def _frozen_templates_unchanged(self) -> None:
        isolation = self._frozen_isolation()
        if (
            self.state.f0g_target_data_after_dump is None
            or self.state.f0i_template_data_after_dump is None
            or not self.f0g_source_dump_sha256
            or not self.f0i_template_dump_sha256
        ):
            raise RebuildError("FROZEN_F0_DATABASE_BASELINE_MISSING")
        f0g = self._isolated_database_dump(
            isolation.f0g_template_database,
            self.state.f0g_target_data_after_dump,
            schemas=F0G_SOURCE_SCHEMAS,
        )
        f0i = self._isolated_database_dump(
            isolation.f0i_template_database,
            self.state.f0i_template_data_after_dump,
            schemas=SOURCE_DATABASE_SCHEMAS,
        )
        if (
            f0g != self.f0g_source_dump_sha256
            or f0i != self.f0i_template_dump_sha256
        ):
            raise RebuildError("FROZEN_F0_TEMPLATE_MUTATED")

    def _frozen_database_unchanged(self) -> None:
        if self.frozen_database_snapshot is None:
            raise RebuildError("FROZEN_F0_DATABASE_BASELINE_MISSING")
        observed = capture_frozen_f0_database_snapshot(
            self.identity.project,
            self._frozen_isolation(),
            self.environment,
            cwd=self.state.checkout or ROOT,
        )
        if observed != self.frozen_database_snapshot:
            raise RebuildError("FROZEN_F0_DATABASE_MUTATED")

    def _target_dump(self) -> str:
        if (
            self.state.checkout is None
            or self.state.target_database_environment_file is None
            or self.state.target_data_dump is None
        ):
            raise RebuildError("F0I_TARGET_SCOPE_REJECTED")
        _process_to_private_file(
            self._database_container_command(
                self.state.target_database_environment_file,
                "pg_dump",
                "--dbname=" + self.identity.database,
                "--format=plain",
                "--data-only",
                "--no-owner",
                "--no-privileges",
                "--schema=f0d",
                "--schema=f0i",
                "--exclude-table-data=f0d.alembic_version",
            ),
            cwd=self.state.checkout,
            environment=self.environment,
            timeout=min(self.timeout, 900),
            destination=self.state.target_data_dump,
            failure_code="F0I_TARGET_DUMP_RED",
        )
        return normalized_data_dump_digest(self.state.target_data_dump)

    def _restore_fixture_source(self) -> str:
        if (
            self.state.checkout is None
            or self.state.target_database_environment_file is None
            or self.state.source_data_dump is None
            or self.state.source_data_after_dump is None
            or self.state.target_data_dump is None
            or self.source_scope is None
            or self.source_container_identity is None
            or not self.fixture_inputs
            or not self.fixture_selection_copies
        ):
            raise RebuildError("F0I_SOURCE_SCOPE_REJECTED")
        self._source_container_inspect()
        aggregate_before = self._source_database_aggregate()
        source_before = self._source_dump(self.state.source_data_dump)
        self._source_container_inspect()
        aggregate_after_dump = self._source_database_aggregate()
        if aggregate_before != aggregate_after_dump:
            raise RebuildError("F0I_SOURCE_MUTATED")
        seed_reset = _process(
            self._database_container_command(
                self.state.target_database_environment_file,
                "psql",
                "--no-psqlrc",
                "--quiet",
                "--set=ON_ERROR_STOP=1",
                "--dbname=" + self.identity.database,
                "--command=TRUNCATE TABLE f0d.capability_gate",
            ),
            cwd=self.state.checkout,
            environment=self.environment,
            timeout=120,
            failure_code="F0I_TARGET_SEED_RESET_RED",
        )
        if seed_reset.output.strip():
            raise RebuildError("F0I_TARGET_SEED_RESET_RED")
        restore = _process(
            self._database_container_command(
                self.state.target_database_environment_file,
                "psql",
                "--no-psqlrc",
                "--quiet",
                "--set=ON_ERROR_STOP=1",
                "--single-transaction",
                "--dbname=" + self.identity.database,
                "--file=/input/source.sql",
                dump_mount=True,
            ),
            cwd=self.state.checkout,
            environment=self.environment,
            timeout=min(self.timeout, 900),
            failure_code="F0I_TARGET_RESTORE_RED",
        )
        if restore.output.strip():
            raise RebuildError("F0I_TARGET_RESTORE_RED")
        target = self._target_dump()
        if target != source_before:
            raise RebuildError("F0I_TARGET_RESTORE_MISMATCH")
        self.fixture_source_sha256 = source_before
        self.source_aggregate_sha256 = aggregate_before
        return source_before

    def _run_migrations_and_pg_contract(self) -> tuple[str, str]:
        if (
            self.state.checkout is None
            or self.state.migration_environment_file is None
            or self.state.f1_environment_file is None
        ):
            raise RebuildError("CLEAN_CHECKOUT_REJECTED")
        root = self._image_run(
            "python",
            "-B",
            "-m",
            "alembic",
            "-c",
            "/app/alembic.ini",
            "upgrade",
            "head",
            env_file=self.state.migration_environment_file,
            failure_code="ROOT_MIGRATION_RED",
        )
        if root.exit_code != 0:
            raise RebuildError("ROOT_MIGRATION_RED")
        self._restore_fixture_source()
        self._prepare_f0i_template()
        first = self._image_run(
            "python",
            "-B",
            "/app/infra/f1/migrate_f1.py",
            env_file=self.state.f1_environment_file,
            failure_code="F1_MIGRATION_RED",
        )
        if first.output.count(b"F1_MIGRATE_OK") != 1:
            raise RebuildError("F1_MIGRATION_MARKER_RED")
        schema_before = self._schema_digest()
        self._enable_ddl_logging()
        replay_log_offset = len(self._pg_logs())
        second = self._image_run(
            "python",
            "-B",
            "/app/infra/f1/migrate_f1.py",
            env_file=self.state.f1_environment_file,
            failure_code="F1_MIGRATION_REPLAY_RED",
        )
        if second.output.count(b"F1_MIGRATE_OK") != 1:
            raise RebuildError("F1_MIGRATION_MARKER_RED")
        replay_logs = self._pg_logs()[replay_log_offset:]
        if re.search(
            rb"\bstatement:\s*(?:CREATE|ALTER|DROP|TRUNCATE|COMMENT|GRANT|REVOKE|SECURITY\s+LABEL)\b",
            replay_logs,
            re.IGNORECASE,
        ):
            raise RebuildError("MIGRATION_REPLAY_DDL_DELTA")
        schema_after = self._schema_digest()
        if schema_before != schema_after:
            raise RebuildError("MIGRATION_REPLAY_DDL_DELTA")
        heads = self._psql_scalar(
            "SELECT (SELECT version_num FROM f0d.alembic_version LIMIT 1)"
            "||':'||(SELECT version_num FROM f1.alembic_version LIMIT 1)"
        )
        if heads != "f0d_0006:f1_0004":
            raise RebuildError("MIGRATION_HEAD_RED")
        metric_names = _extract_metric_names(
            self.state.checkout / "tests/f111_repair_pg_verify.py"
        )
        verifier = self._image_run(
            "python",
            "-B",
            "/acceptance/f111_repair_pg_verify.py",
            env_file=self.state.f1_environment_file,
            mounts=(
                (
                    self.state.checkout / "tests/f111_repair_pg_verify.py",
                    "/acceptance/f111_repair_pg_verify.py",
                ),
            ),
            failure_code="PG_LIVE_CONTRACT_RED",
        )
        metrics = parse_zero_metric_line(verifier.output, metric_names)
        metric_digest = _sha256(
            _canonical_bytes({name: metrics[name] for name in sorted(metrics)})
        )
        return schema_after, metric_digest

    def _enable_ddl_logging(self) -> None:
        for statement in (
            "ALTER SYSTEM SET log_statement = 'ddl'",
            "SELECT pg_reload_conf()",
        ):
            _process(
                (
                    "docker",
                    "exec",
                    self.identity.pg_container,
                    "psql",
                    "--no-psqlrc",
                    "--set=ON_ERROR_STOP=1",
                    "--username=f0d_bootstrap",
                    "--dbname=" + self.identity.database,
                    "--command",
                    statement,
                ),
                cwd=ROOT,
                environment=self.environment,
                timeout=120,
                failure_code="DDL_AUDIT_RED",
            )

    def _pg_logs(self) -> bytes:
        return _process(
            ("docker", "container", "logs", self.identity.pg_container),
            cwd=ROOT,
            environment=self.environment,
            timeout=120,
            failure_code="DDL_AUDIT_RED",
        ).output

    def _psql_scalar(self, statement: str) -> str:
        if not statement or "\x00" in statement:
            raise RebuildError("PG_QUERY_REJECTED")
        result = _process(
            (
                "docker",
                "exec",
                self.identity.pg_container,
                "psql",
                "--no-psqlrc",
                "--tuples-only",
                "--no-align",
                "--set=ON_ERROR_STOP=1",
                "--username=f0d_bootstrap",
                "--dbname=" + self.identity.database,
                "--command",
                statement,
            ),
            cwd=ROOT,
            environment=self.environment,
            timeout=120,
            failure_code="PG_QUERY_RED",
        )
        try:
            value = result.output.decode("ascii").strip()
        except UnicodeDecodeError:
            raise RebuildError("PG_QUERY_RED") from None
        if not value or "\n" in value:
            raise RebuildError("PG_QUERY_RED")
        return value

    def _schema_digest(self) -> str:
        result = _process(
            (
                "docker",
                "exec",
                self.identity.pg_container,
                "pg_dump",
                "--schema-only",
                "--no-owner",
                "--no-privileges",
                "--username=f0d_bootstrap",
                "--dbname=" + self.identity.database,
            ),
            cwd=ROOT,
            environment=self.environment,
            timeout=180,
            failure_code="SCHEMA_DUMP_RED",
        )
        normalized: list[bytes] = []
        for line in result.output.splitlines():
            stripped = line.strip()
            if (
                not stripped
                or stripped.startswith(b"--")
                or stripped.startswith(b"\\restrict")
                or stripped.startswith(b"\\unrestrict")
            ):
                continue
            normalized.append(stripped)
        if not normalized:
            raise RebuildError("SCHEMA_DUMP_RED")
        return _sha256(b"\n".join(normalized) + b"\n")

    def _seed_f1(self) -> None:
        namespace = uuid.UUID("3d2f5ef4-f633-4e31-bf50-99928b3dc98c")
        enterprise_a = uuid.UUID("10000000-0000-4000-8000-00000000000a")
        enterprise_b = uuid.UUID("10000000-0000-4000-8000-00000000000b")
        identities = (
            (
                "f1f70ce5-465f-489c-a89d-974a63216ab4",
                enterprise_a,
                "partner",
                "tester@fixture.invalid",
            ),
            (
                "d561ffe2-3be8-40cc-a87e-598dd7d84758",
                enterprise_a,
                "super_admin",
                "admin@fixture.invalid",
            ),
            (
                "db906685-6906-4bc4-9d3a-9011975fd132",
                enterprise_a,
                "enterprise_admin",
                "tenant-a@fixture.invalid",
            ),
            (
                "ddc4e27e-ccde-4c89-958f-798fc8f30175",
                enterprise_b,
                "enterprise_admin",
                "tenant-b@fixture.invalid",
            ),
            (
                "7e9978c7-106f-4221-a6d7-79e8104a659b",
                enterprise_a,
                "auditor",
                "auditor@fixture.invalid",
            ),
        )

        def seeded(kind: str, *parts: object) -> uuid.UUID:
            return uuid.uuid5(namespace, ":".join((kind, *(str(part) for part in parts))))

        statements = [
            "BEGIN;",
            "INSERT INTO f1.enterprise(id,name,license_no,f0i_enterprise_id) "
            "VALUES "
            "('10000000-0000-4000-8000-00000000000a','Tenant A','FIX-A',"
            "'4842a9d5-b719-5d5c-b2de-6ad679d1cb8d'),"
            "('10000000-0000-4000-8000-00000000000b','Tenant B','FIX-B',NULL);",
        ]
        for sub, enterprise, role, email in identities:
            profile = seeded("profile", sub)
            membership = seeded("membership", enterprise, sub)
            statements.append(
                "INSERT INTO f1.user_profile(id,keycloak_sub,email) VALUES "
                f"('{profile}','{sub}','{email}');"
            )
            statements.append(
                "INSERT INTO f1.enterprise_user(id,enterprise_id,user_id,role) VALUES "
                f"('{membership}','{enterprise}','{profile}','{role}');"
            )
        statements.extend(("COMMIT;", ""))
        _process(
            (
                "docker",
                "exec",
                "-i",
                self.identity.pg_container,
                "psql",
                "--no-psqlrc",
                "--set=ON_ERROR_STOP=1",
                "--username=f0d_bootstrap",
                "--dbname=" + self.identity.database,
            ),
            cwd=ROOT,
            environment=self.environment,
            timeout=120,
            input_bytes="\n".join(statements).encode("ascii"),
            failure_code="F1_SEED_RED",
        )

    def _start_compose(self) -> None:
        if self.state.checkout is None:
            raise RebuildError("CLEAN_CHECKOUT_REJECTED")
        self._release_all_ports()
        _process(
            self._compose_command(
                "up", "-d", "--no-build", "--wait", "--wait-timeout", str(self.timeout)
            ),
            cwd=self.state.checkout,
            environment=self.environment,
            timeout=self.timeout,
            failure_code="COMPOSE_UP_RED",
        )
        self.state.compose_started = True
        result = _process(
            self._compose_command("ps", "--all", "--format", "json"),
            cwd=self.state.checkout,
            environment=self.environment,
            timeout=120,
            failure_code="COMPOSE_STATE_RED",
        )
        raw = result.output.strip()
        try:
            decoded = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            try:
                decoded = [json.loads(line) for line in raw.splitlines() if line]
            except json.JSONDecodeError:
                raise RebuildError("COMPOSE_STATE_RED") from None
        rows = decoded if isinstance(decoded, list) else [decoded]
        states: dict[str, tuple[str, str, int]] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise RebuildError("COMPOSE_STATE_RED")
            service = str(row.get("Service", ""))
            if not service or service in states:
                raise RebuildError("COMPOSE_STATE_RED")
            states[service] = (
                str(row.get("State", "")),
                str(row.get("Health", "")),
                int(row.get("ExitCode", 0) or 0),
            )
        if set(states) != EXPECTED_SERVICES:
            raise RebuildError("COMPOSE_STATE_RED")
        for service, (state, health, exit_code) in states.items():
            if service == "keycloak-provisioner":
                if state.lower() not in {"exited", "stopped"} or exit_code != 0:
                    raise RebuildError("COMPOSE_STATE_RED")
            elif state.lower() != "running" or health.lower() not in {"healthy", ""}:
                raise RebuildError("COMPOSE_STATE_RED")

    def _runtime_inventory(self) -> str:
        if (
            self.state.checkout is None
            or set(self.declared_images) != EXPECTED_SERVICES
            or set(self.build_provenance) != set(BUILD_PROVENANCE_LABELS)
        ):
            raise RebuildError("RUNTIME_INVENTORY_REJECTED")
        actual: dict[str, str] = {}
        for service in sorted(EXPECTED_SERVICES):
            container = _process(
                self._compose_command("ps", "--all", "-q", service),
                cwd=self.state.checkout,
                environment=self.environment,
                timeout=60,
                failure_code="RUNTIME_INVENTORY_REJECTED",
            ).output.strip()
            if not re.fullmatch(rb"[0-9a-f]{12,64}", container):
                raise RebuildError("RUNTIME_INVENTORY_REJECTED")
            image_id = _process(
                (
                    "docker",
                    "container",
                    "inspect",
                    "--format",
                    "{{.Image}}",
                    container.decode("ascii"),
                ),
                cwd=self.state.checkout,
                environment=self.environment,
                timeout=60,
                failure_code="RUNTIME_INVENTORY_REJECTED",
            ).output.decode("ascii", errors="strict").strip()
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
                raise RebuildError("RUNTIME_INVENTORY_REJECTED")
            actual[service] = image_id
            labels_raw = _process(
                (
                    "docker",
                    "container",
                    "inspect",
                    "--format",
                    "{{json .Config.Labels}}",
                    container.decode("ascii"),
                ),
                cwd=self.state.checkout,
                environment=self.environment,
                timeout=60,
                failure_code="RUNTIME_INVENTORY_REJECTED",
            ).output
            try:
                labels = json.loads(labels_raw)
            except json.JSONDecodeError:
                raise RebuildError("RUNTIME_INVENTORY_REJECTED") from None
            if (
                not isinstance(labels, dict)
                or labels.get("com.docker.compose.project") != self.identity.project
                or labels.get("com.docker.compose.service") != service
                or labels.get("anhuan.scope") != "f111-repair"
                or labels.get("anhuan.repair-project") != self.identity.project
            ):
                raise RebuildError("RUNTIME_INVENTORY_REJECTED")
            if expected_local_image(self.identity, service) is not None:
                validate_build_provenance_labels(labels, self.build_provenance)
        pg_image = _process(
            (
                "docker",
                "container",
                "inspect",
                "--format",
                "{{.Image}}",
                self.identity.pg_container,
            ),
            cwd=self.state.checkout,
            environment=self.environment,
            timeout=60,
            failure_code="RUNTIME_INVENTORY_REJECTED",
        ).output.decode("ascii", errors="strict").strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", pg_image):
            raise RebuildError("RUNTIME_INVENTORY_REJECTED")
        actual["postgres"] = pg_image
        pg_labels_raw = _process(
            (
                "docker",
                "container",
                "inspect",
                "--format",
                "{{json .Config.Labels}}",
                self.identity.pg_container,
            ),
            cwd=self.state.checkout,
            environment=self.environment,
            timeout=60,
            failure_code="RUNTIME_INVENTORY_REJECTED",
        ).output
        try:
            pg_labels = json.loads(pg_labels_raw)
        except json.JSONDecodeError:
            raise RebuildError("RUNTIME_INVENTORY_REJECTED") from None
        if (
            not isinstance(pg_labels, dict)
            or pg_labels.get("anhuan.scope") != "f111-repair"
            or pg_labels.get("anhuan.repair-project") != self.identity.project
        ):
            raise RebuildError("RUNTIME_INVENTORY_REJECTED")
        for service in ("api", "keycloak-provisioner", "worker", "dispatcher", "web"):
            tag = expected_local_image(self.identity, service)
            if tag is None:
                raise RebuildError("RUNTIME_INVENTORY_REJECTED")
            tagged = _process(
                ("docker", "image", "inspect", "--format", "{{.Id}}", tag),
                cwd=self.state.checkout,
                environment=self.environment,
                timeout=60,
                failure_code="RUNTIME_INVENTORY_REJECTED",
            ).output.decode("ascii", errors="strict").strip()
            if tagged != actual[service]:
                raise RebuildError("RUNTIME_TAG_BINDING_RED")
            image_labels_raw = _process(
                (
                    "docker",
                    "image",
                    "inspect",
                    "--format",
                    "{{json .Config.Labels}}",
                    tag,
                ),
                cwd=self.state.checkout,
                environment=self.environment,
                timeout=60,
                failure_code="RUNTIME_INVENTORY_REJECTED",
            ).output
            try:
                image_labels = json.loads(image_labels_raw)
            except json.JSONDecodeError:
                raise RebuildError("RUNTIME_INVENTORY_REJECTED") from None
            if not isinstance(image_labels, dict):
                raise RebuildError("RUNTIME_INVENTORY_REJECTED")
            validate_build_provenance_labels(
                image_labels, self.build_provenance
            )
        bases: list[str] = []
        for relative in ("infra/f1/Dockerfile", "infra/f1/web.Dockerfile"):
            try:
                lines = (self.state.checkout / relative).read_text(
                    encoding="utf-8"
                ).splitlines()
            except (OSError, UnicodeDecodeError):
                raise RebuildError("RUNTIME_INVENTORY_REJECTED") from None
            found = [line.split()[1] for line in lines if line.startswith("FROM ")]
            if not found or any("@sha256:" not in reference for reference in found):
                raise RebuildError("RUNTIME_INVENTORY_REJECTED")
            bases.extend(found)
        locks: dict[str, str] = {}
        for name, relative in (
            ("python", "requirements/requirements-f1.lock"),
            ("npm", "src/web/package-lock.json"),
        ):
            try:
                locks[name] = _sha256((self.state.checkout / relative).read_bytes())
            except OSError:
                raise RebuildError("RUNTIME_INVENTORY_REJECTED") from None
        declared = dict(self.declared_images)
        declared["postgres"] = PG_IMAGE
        return runtime_inventory_digest(
            actual_images=actual,
            declared_provenance=declared,
            base_images=tuple(bases),
            lock_sha256=locks,
            build_provenance=self.build_provenance,
        )

    def _read_round_secret(self, name: str) -> str:
        if self.state.secrets_directory is None:
            raise RebuildError("SECRET_SCOPE_REJECTED")
        try:
            return _read_private_file(
                self.state.secrets_directory / name, "SECRET_FILE_REJECTED"
            ).decode("utf-8").strip()
        except UnicodeDecodeError:
            raise RebuildError("SECRET_FILE_REJECTED") from None

    @staticmethod
    def _http_request(
        url: str,
        *,
        method: str = "GET",
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: int = 30,
    ) -> tuple[int, bytes]:
        request = urllib.request.Request(
            url,
            method=method,
            data=data,
            headers=dict(headers or {}),
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return int(response.status), response.read(MAX_PRIVATE_FILE + 1)
        except urllib.error.HTTPError as error:
            return int(error.code), error.read(MAX_PRIVATE_FILE + 1)
        except (OSError, urllib.error.URLError):
            raise RebuildError("HTTP_DEPENDENCY_RED") from None

    def _token(self, identity: str) -> str:
        if identity not in {"tenant_a", "tenant_b"}:
            raise RebuildError("OIDC_IDENTITY_REJECTED")
        data = urllib.parse.urlencode(
            {
                "username": self._read_round_secret(identity + "_username"),
                "password": self._read_round_secret(identity + "_password"),
                "grant_type": "password",
                "client_id": "anhuan-web",
            }
        ).encode("ascii")
        status, raw = self._http_request(
            f"http://127.0.0.1:{self.ports['keycloak']}"
            "/realms/anhuan/protocol/openid-connect/token",
            method="POST",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            raise RebuildError("OIDC_TOKEN_RED") from None
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if status != 200 or not isinstance(token, str) or len(token) < 64:
            raise RebuildError("OIDC_TOKEN_RED")
        return token

    def _http_contract(self) -> tuple[bytes, ...]:
        api = f"http://127.0.0.1:{self.ports['api']}"
        status, _raw = self._http_request(api + "/healthz")
        if status != 200:
            raise RebuildError("HTTP_HEALTH_RED")
        token_a = self._token("tenant_a")
        token_b = self._token("tenant_b")
        enterprise_a = "10000000-0000-4000-8000-00000000000a"
        enterprise_b = "10000000-0000-4000-8000-00000000000b"

        def enterprise(token: str, identifier: str) -> int:
            status_code, _body = self._http_request(
                api + "/api/v1/enterprises/" + identifier,
                headers={"Authorization": "Bearer " + token},
            )
            return status_code

        if (
            enterprise(token_a, enterprise_a) != 200
            or enterprise(token_b, enterprise_b) != 200
            or enterprise(token_b, enterprise_a) != 404
        ):
            raise RebuildError("HTTP_TENANT_CONTRACT_RED")
        return (token_a.encode("utf-8"), token_b.encode("utf-8"))

    def _fixture_http_e2e(self) -> str:
        """Run the fixed reverse verifier as the registered-Fixture E2E gate."""
        if self.state.checkout is None or self.state.secrets_directory is None:
            raise RebuildError("CLEAN_CHECKOUT_REJECTED")
        verifier = self.state.checkout / "tests/f111_reverse_verify.py"
        try:
            info = verifier.lstat()
        except OSError:
            raise RebuildError("FIXTURE_E2E_VERIFIER_REJECTED") from None
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise RebuildError("FIXTURE_E2E_VERIFIER_REJECTED")
        environment = dict(self.environment)
        environment.update(
            {
                "F111_REVERSE_API_BASE": f"http://127.0.0.1:{self.ports['api']}",
                "F111_REVERSE_COMPOSE_OVERRIDE": str(
                    self.state.checkout / "infra/f1/docker-compose.repair.yml"
                ),
                "F111_REVERSE_KEYCLOAK_BASE": (
                    f"http://127.0.0.1:{self.ports['keycloak']}"
                ),
                "F111_REVERSE_PROJECT": self.identity.project,
                "F111_REVERSE_SECRETS_DIR": str(self.state.secrets_directory),
                "F111_REVERSE_TIMEOUT_SECONDS": str(self.timeout),
                "PYTHONPATH": str(self.state.checkout / "src"),
            }
        )
        if self.python_bridge_identity is None:
            raise RebuildError("CHECKOUT_PYTHON_BRIDGE_REJECTED")
        verify_checkout_python_bridge(
            self.state.checkout, self.python_bridge_identity
        )
        result = _process(
            (str(self.state.checkout / ".venv/bin/python"), "-B", str(verifier)),
            cwd=self.state.checkout,
            environment=environment,
            timeout=self.timeout,
            failure_code="FIXTURE_HTTP_E2E_RED",
        )
        metrics = parse_zero_metric_line(result.output, REVERSE_METRICS)
        return _sha256(
            _canonical_bytes({name: metrics[name] for name in REVERSE_METRICS})
        )

    def _leak_contract(self, token_canaries: Sequence[bytes]) -> None:
        if self.state.checkout is None or self.state.scratch_root is None:
            raise RebuildError("CLEAN_CHECKOUT_REJECTED")
        logs = _process(
            self._compose_command("logs", "--no-color"),
            cwd=self.state.checkout,
            environment=self.environment,
            timeout=180,
            failure_code="LOG_CAPTURE_RED",
        ).output
        trace_status, traces = self._http_request(
            f"http://127.0.0.1:{self.ports['jaeger_ui']}"
            "/api/traces?service=anhuan-f1-api&limit=200"
        )
        if trace_status != 200:
            raise RebuildError("TRACE_CAPTURE_RED")
        path_canaries = (
            str(ROOT).encode("utf-8"),
            str(self.state.scratch_root).encode("utf-8"),
            str(self.state.secrets_directory).encode("utf-8"),
        )
        canaries = tuple(self.sensitive_canaries) + tuple(token_canaries) + path_canaries
        postgres_logs = self._pg_logs()
        for payload in (logs, traces, postgres_logs):
            for canary in canaries:
                if len(canary) >= 8 and canary in payload:
                    raise RebuildError("NEW_PLAINTEXT_LEAK")

    def _source_unchanged(self) -> None:
        if self.source_snapshot is None:
            raise RebuildError("SOURCE_BASELINE_MISSING")
        self._frozen_inputs_unchanged()
        current = capture_source(self.environment)
        if current != self.source_snapshot:
            raise RebuildError("SOURCE_DRIFT")
        verify_fixture_plan_sources(
            self._source_secret_directory(), self.fixture_plan_source_identities
        )
        if self.fixture_source_bundle_identity is None or not self.fixture_source_records:
            raise RebuildError("SOURCE_BUNDLE_BASELINE_MISSING")
        bundle_identity, bundle_records, bundle_writes = _fixture_source_bundle(
            self._source_secret_directory(), None
        )
        if (
            bundle_writes
            or bundle_identity != self.fixture_source_bundle_identity
            or bundle_records != self.fixture_source_records
        ):
            raise RebuildError("SOURCE_BUNDLE_MUTATED")
        self._validate_delivery_checkout()

    def _frozen_inputs_unchanged(self) -> None:
        if (
            self.state.secrets_directory is None
            or self.f0g_source_scope is None
            or not self.f0g_source_scope_sha256
            or self.f0g_source_scope_file_identity is None
            or self.f0g_scope_copy_identity is None
            or self.frozen_f0_inputs is None
            or self.f0f_key_copy_identity is None
            or set(self.runtime_bundle_copy_identities) != set(RUNTIME_TREE_BUNDLES)
        ):
            raise RebuildError("FROZEN_F0_BASELINE_MISSING")
        source = self._source_secret_directory()
        scope_raw, scope_identity = _private_file_identity(
            source / F0G_SOURCE_SCOPE_NAME,
            code="F0G_SOURCE_SCOPE_MUTATED",
            expected_sha256=self.f0g_source_scope_sha256,
            maximum=64 * 1024,
        )
        copied_scope_raw, copied_scope_identity = _private_file_identity(
            self.state.secrets_directory / F0G_SOURCE_SCOPE_NAME,
            code="F0G_SOURCE_SCOPE_MUTATED",
            expected_sha256=self.f0g_source_scope_sha256,
            maximum=64 * 1024,
        )
        if (
            scope_identity != self.f0g_source_scope_file_identity
            or copied_scope_identity != self.f0g_scope_copy_identity
            or copied_scope_raw != scope_raw
            or parse_f0g_source_scope(scope_raw) != self.f0g_source_scope
        ):
            raise RebuildError("F0G_SOURCE_SCOPE_MUTATED")
        verify_frozen_f0_inputs(source, self.frozen_f0_inputs)
        _source_key_raw, source_key_identity = _private_file_identity(
            source / F0F_SOURCE_KEY_NAME,
            code="FROZEN_F0_KEY_MUTATED",
            expected_sha256=self.frozen_f0_inputs.source_key_identity.sha256,
            maximum=64,
        )
        _target_key_raw, target_key_identity = _private_file_identity(
            self.state.secrets_directory / F0F_SOURCE_KEY_NAME,
            code="FROZEN_F0_KEY_MUTATED",
            expected_sha256=self.frozen_f0_inputs.source_key_identity.sha256,
            maximum=64,
        )
        if (
            source_key_identity != self.frozen_f0_inputs.source_key_identity
            or target_key_identity != self.f0f_key_copy_identity
        ):
            raise RebuildError("FROZEN_F0_KEY_MUTATED")
        expected_trees = {
            tree.phase: tree for tree in self.frozen_f0_inputs.runtime_trees
        }
        if set(expected_trees) != set(RUNTIME_TREE_BUNDLES):
            raise RebuildError("FROZEN_F0_RUNTIME_MUTATED")
        for phase, tree in sorted(expected_trees.items()):
            source_identity, source_tree, source_entries, source_writes = (
                _frozen_runtime_tree_bundle(source, phase, None)
            )
            copied_identity, copied_tree, copied_entries, copied_writes = (
                _frozen_runtime_tree_bundle(
                    self.state.secrets_directory, phase, None
                )
            )
            if (
                source_writes
                or copied_writes
                or source_identity != tree.bundle_identity
                or source_tree != tree.tree_sha256
                or source_entries != tree.entries
                or copied_identity != self.runtime_bundle_copy_identities[phase]
                or copied_identity.sha256 != source_identity.sha256
                or copied_tree != source_tree
                or copied_entries != source_entries
            ):
                raise RebuildError("FROZEN_F0_RUNTIME_MUTATED")

    def _validate_delivery_checkout(self) -> None:
        if (
            self.state.checkout is None
            or self.source_snapshot is None
            or self.checkout_identity is None
            or self.python_bridge_identity is None
        ):
            raise RebuildError("CLEAN_CHECKOUT_REJECTED")
        validate_delivery_checkout(
            self.state.checkout,
            self.environment,
            expected_source_sha256=self.source_snapshot.sha256,
            expected_identity=self.checkout_identity,
            expected_python_identity=self.python_bridge_identity,
        )

    def _fixture_source_unchanged(self) -> None:
        if (
            self.state.source_data_after_dump is None
            or self.source_container_identity is None
            or self.source_scope is None
            or not self.source_scope_sha256
            or self.source_scope_file_identity is None
            or not self.fixture_source_sha256
            or not self.source_aggregate_sha256
            or not self.fixture_manifest_sha256
            or self.fixture_manifest_source_identity is None
            or not self.fixture_inputs
            or not self.fixture_plan_source_identities
            or self.fixture_source_bundle_identity is None
            or not self.fixture_source_records
        ):
            raise RebuildError("F0I_SOURCE_BASELINE_MISSING")
        source = self._source_secret_directory()
        source_scope_raw, source_scope_identity = _private_file_identity(
            source / "f0i_source_scope",
            code="F0I_SOURCE_SCOPE_REJECTED",
            expected_sha256=None,
        )
        if (
            _sha256(source_scope_raw) != self.source_scope_sha256
            or parse_source_scope(source_scope_raw) != self.source_scope
            or source_scope_identity != self.source_scope_file_identity
        ):
            raise RebuildError("F0I_SOURCE_SCOPE_MUTATED")
        verify_fixture_plan_sources(source, self.fixture_plan_source_identities)
        bundle_identity, bundle_records, bundle_writes = _fixture_source_bundle(
            source, None
        )
        if (
            bundle_writes
            or bundle_identity != self.fixture_source_bundle_identity
            or bundle_records != self.fixture_source_records
        ):
            raise RebuildError("SOURCE_BUNDLE_MUTATED")
        self._validate_delivery_checkout()
        self._source_container_inspect()
        aggregate = self._source_database_aggregate()
        observed = self._source_dump(self.state.source_data_after_dump)
        self._source_container_inspect()
        if (
            aggregate != self.source_aggregate_sha256
            or observed != self.fixture_source_sha256
        ):
            raise RebuildError("F0I_SOURCE_MUTATED")
        manifest, manifest_identity = _private_file_identity(
            source / "fixture_manifest",
            code="FIXTURE_MANIFEST_REJECTED",
            expected_sha256=None,
            maximum=262144,
        )
        if (
            _sha256(manifest) != self.fixture_manifest_sha256
            or manifest_identity != self.fixture_manifest_source_identity
            or parse_fixture_manifest(
                verify_fixture_selection(manifest, self.fixture_selection_copies)
            )
            != self.fixture_inputs
        ):
            raise RebuildError("FIXTURE_INPUT_MUTATED")

    def _cleanup(self) -> int:
        residuals = 0
        self._release_all_ports()
        if self.state.checkout is not None:
            _process(
                self._compose_command("down", "-v", "--remove-orphans", "--timeout", "30"),
                cwd=self.state.checkout,
                environment=self.environment,
                timeout=240,
                check=False,
            )
        if self.state.pg_container_created:
            assert_owned_resource(self.identity.pg_container, self.identity)
            _process(
                ("docker", "container", "rm", "-f", self.identity.pg_container),
                cwd=ROOT,
                environment=self.environment,
                timeout=120,
                check=False,
            )
        if self.state.pg_volume_created:
            assert_owned_resource(self.identity.pg_volume, self.identity)
            _process(
                ("docker", "volume", "rm", self.identity.pg_volume),
                cwd=ROOT,
                environment=self.environment,
                timeout=120,
                check=False,
            )
        if self.state.local_images_created:
            for image in self._local_images():
                assert_owned_resource(image, self.identity)
                _process(
                    ("docker", "image", "rm", image),
                    cwd=ROOT,
                    environment=self.environment,
                    timeout=180,
                    check=False,
                )
        if self.frozen_f0_inputs is not None:
            try:
                verify_frozen_f0_project_absence(
                    self.frozen_f0_inputs.isolation,
                    self.environment,
                    cwd=self.state.checkout or ROOT,
                )
            except RebuildError:
                residuals += 1
            try:
                _remove_frozen_f0_runtime_root(
                    self.frozen_f0_inputs.isolation.runtime_root,
                    self.frozen_f0_inputs.isolation.project_id,
                    self.frozen_f0_inputs.runtime_root_identity,
                )
                self.frozen_f0_inputs = None
            except RebuildError:
                residuals += 1
        probes = (
            ("docker", "container", "inspect", self.identity.pg_container),
            ("docker", "volume", "inspect", self.identity.pg_volume),
            ("docker", "network", "inspect", self.identity.project + "_f1net"),
            *(
                (
                    "docker",
                    "volume",
                    "inspect",
                    self.identity.project + "_" + name,
                )
                for name in sorted(EXPECTED_VOLUMES)
            ),
            *(
                ("docker", "image", "inspect", image)
                for image in self._local_images()
            ),
        )
        for command in probes:
            result = _process(
                command,
                cwd=ROOT,
                environment=self.environment,
                timeout=30,
                check=False,
            )
            residuals += int(result.exit_code == 0)
        for kind in ("container", "volume", "network"):
            command = ["docker", kind, "ls"]
            if kind == "container":
                command.append("--all")
            command.extend(
                (
                    "-q",
                    "--filter",
                    "label=anhuan.repair-project=" + self.identity.project,
                )
            )
            result = _process(
                tuple(command),
                cwd=ROOT,
                environment=self.environment,
                timeout=30,
                check=False,
            )
            residuals += int(bool(result.output.strip()))
        scratch = self.state.scratch_root
        if scratch is not None:
            if (
                scratch.parent != Path("/private/tmp")
                or not scratch.name.startswith(self.identity.project + "-")
            ):
                raise RebuildError("CLEANUP_TARGET_REJECTED")
            try:
                shutil.rmtree(scratch)
            except OSError:
                residuals += 1
            residuals += int(scratch.exists() or scratch.is_symlink())
        return residuals

    def run(self) -> RoundSummary:
        schema_sha256 = ""
        pg_contract_sha256 = ""
        runtime_inventory_sha256 = ""
        fixture_e2e_sha256 = ""
        cleanup_residuals = 1
        failure: RebuildError | None = None
        try:
            self._prepare_runtime_scope()
            self._validate_scope()
            self._probe_absence()
            self._reserve_ports()
            self._create_clean_checkout()
            self._prepare_secrets()
            self._frozen_f0_projects_absent()
            self._validate_compose()
            self._build_images()
            self._start_postgres()
            self._bootstrap_database()
            self._prepare_f0g_template()
            schema_sha256, pg_contract_sha256 = self._run_migrations_and_pg_contract()
            self._record_frozen_database_baseline()
            self._seed_f1()
            self._start_compose()
            runtime_inventory_sha256 = self._runtime_inventory()
            fixture_e2e_sha256 = self._fixture_http_e2e()
            token_canaries = self._http_contract()
            self._leak_contract(token_canaries)
            self._frozen_templates_unchanged()
            self._frozen_database_unchanged()
            self._frozen_f0_projects_absent()
            self._fixture_source_unchanged()
            self._f0g_source_unchanged()
            self._source_unchanged()
            self.state.evidence_captured = True
        except RebuildError as error:
            failure = error
        except Exception:
            failure = RebuildError("INTERNAL_FAILURE")
        finally:
            try:
                cleanup_residuals = self._cleanup()
            except RebuildError as error:
                cleanup_residuals = 1
                if failure is None:
                    failure = error
            except Exception:
                cleanup_residuals = 1
                if failure is None:
                    failure = RebuildError("CLEANUP_INTERNAL_FAILURE")
        if failure is not None:
            raise failure
        if self.source_snapshot is None:
            raise RebuildError("SOURCE_BASELINE_MISSING")
        summary = RoundSummary(
            source_sha256=self.source_snapshot.sha256,
            fixture_source_sha256=self.fixture_source_sha256,
            fixture_e2e_sha256=fixture_e2e_sha256,
            schema_sha256=schema_sha256,
            pg_contract_sha256=pg_contract_sha256,
            runtime_inventory_sha256=runtime_inventory_sha256,
            service_count=self.service_count,
            evidence_captured=self.state.evidence_captured,
            cleanup_residuals=cleanup_residuals,
        )
        summary.result_sha256()
        return summary


@dataclass(frozen=True, slots=True)
class PreparedPrimaryStack:
    """Non-authoritative inputs for a caller-owned formal acceptance run."""

    project: str
    ports: Mapping[str, int]
    secrets_directory: Path = field(repr=False)
    provider_secrets_directory: Path = field(repr=False)
    f0i_key_file: Path = field(repr=False)
    checkout: Path = field(repr=False)
    checkout_identity: CheckoutIdentity
    source_snapshot_sha256: str
    source_file_count: int
    fixture_input_sha256: tuple[tuple[str, str], ...]
    python_bridge_identity: ExecutableIdentity
    frozen_f0_inputs: FrozenF0PreparedInputs = field(repr=False)
    frozen_f0_database_snapshot: FrozenF0DatabaseSnapshot

    def formal_config_payload(self, *, timeout_seconds: int = 900) -> dict[str, Any]:
        """Reject the retired caller-authored formal authority payload."""

        del timeout_seconds
        raise RebuildError("PREPARED_STACK_FORMAL_CONFIG_FORBIDDEN")


class PreparedPrimaryStackContext:
    """Prepare one random primary stack and always tear down its exact scope.

    The context deliberately does not run or interpret formal acceptance.
    Its fields are runtime observations, never caller authority.  The retired
    ``formal_config_payload`` method is a permanent fail-closed tripwire.
    Publication remains blocked until ``__exit__`` and
    :meth:`assert_closed_clean` both succeed.
    """

    def __init__(self, environment: Mapping[str, str]) -> None:
        self.round = CleanRebuildRound(1, environment)
        self._entered = False
        self._closed = False
        self._cleanup_verified = False

    def __enter__(self) -> PreparedPrimaryStack:
        if self._entered or self._closed:
            raise RebuildError("PREPARED_STACK_STATE_REJECTED")
        runner = self.round
        try:
            runner._prepare_runtime_scope()
            runner._validate_scope()
            runner._probe_absence()
            runner._reserve_ports()
            runner._create_clean_checkout()
            runner._prepare_secrets()
            runner._frozen_f0_projects_absent()
            runner._validate_compose()
            runner._build_images()
            runner._start_postgres()
            runner._bootstrap_database()
            runner._prepare_f0g_template()
            runner._run_migrations_and_pg_contract()
            runner._record_frozen_database_baseline()
            runner._seed_f1()
            runner._start_compose()
            runner._runtime_inventory()
            if (
                runner.state.secrets_directory is None
                or runner.state.provider_secrets_directory is None
                or runner.state.f0i_key_file is None
                or runner.state.checkout is None
                or runner.checkout_identity is None
                or runner.python_bridge_identity is None
                or runner.source_snapshot is None
                or runner.frozen_f0_inputs is None
                or runner.frozen_database_snapshot is None
            ):
                raise RebuildError("PREPARED_STACK_STATE_REJECTED")
            _regular_private_directory(
                runner.state.secrets_directory, "PREPARED_STACK_STATE_REJECTED"
            )
            _regular_private_directory(
                runner.state.provider_secrets_directory,
                "PREPARED_STACK_STATE_REJECTED",
            )
            _read_private_file(
                runner.state.f0i_key_file, "PREPARED_STACK_STATE_REJECTED"
            )
            runner._validate_delivery_checkout()
            runner._frozen_inputs_unchanged()
            prepared = PreparedPrimaryStack(
                project=runner.identity.project,
                ports=dict(runner.ports),
                secrets_directory=runner.state.secrets_directory,
                provider_secrets_directory=runner.state.provider_secrets_directory,
                f0i_key_file=runner.state.f0i_key_file,
                checkout=runner.state.checkout,
                checkout_identity=runner.checkout_identity,
                source_snapshot_sha256=runner.source_snapshot.sha256,
                source_file_count=len(runner.source_snapshot.entries),
                fixture_input_sha256=tuple(
                    (name, value[1])
                    for name, value in sorted(FIXTURE_PLAN_CONTRACTS.items())
                ),
                python_bridge_identity=runner.python_bridge_identity,
                frozen_f0_inputs=runner.frozen_f0_inputs,
                frozen_f0_database_snapshot=runner.frozen_database_snapshot,
            )
        except Exception:
            residuals = runner._cleanup()
            self._closed = True
            if residuals:
                raise RebuildError("PREPARED_STACK_CLEANUP_RED") from None
            raise
        self._entered = True
        return prepared

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        if not self._entered or self._closed:
            raise RebuildError("PREPARED_STACK_STATE_REJECTED")
        verification_failure: RebuildError | None = None
        try:
            self.round._frozen_templates_unchanged()
            self.round._frozen_database_unchanged()
            self.round._frozen_f0_projects_absent()
            self.round._fixture_source_unchanged()
            self.round._f0g_source_unchanged()
            self.round._source_unchanged()
        except RebuildError as error:
            verification_failure = error
        except Exception:
            verification_failure = RebuildError("PREPARED_STACK_VERIFY_RED")
        try:
            residuals = self.round._cleanup()
        except Exception:
            residuals = 1
        self._closed = True
        if residuals:
            raise RebuildError("PREPARED_STACK_CLEANUP_RED") from exc
        if verification_failure is not None:
            raise verification_failure from exc
        self._cleanup_verified = True
        return False

    def assert_closed_clean(self) -> None:
        if not self._closed or not self._cleanup_verified:
            raise RebuildError("PREPARED_STACK_PUBLICATION_BLOCKED")


def prepare_primary_stack(
    environment: Mapping[str, str] | None = None,
) -> PreparedPrimaryStackContext:
    """Return a one-use context; no Docker action occurs before ``__enter__``."""
    return PreparedPrimaryStackContext(
        dict(os.environ if environment is None else environment)
    )


def main(environment: Mapping[str, str] | None = None) -> int:
    values = dict(os.environ if environment is None else environment)
    try:
        round_number = parse_round(values.get(ROUND_ENV))
        temporary = _parent_temporary(values)
        summary = CleanRebuildRound(round_number, values).run()
        document = round_evidence_document(summary, round_number)
        write_round_evidence(temporary, document, round_number)
        marker = validate_round_evidence(document, round_number)
        sys.stdout.write("CLEAN_REBUILD_RESULT_SHA256=" + marker + "\n")
        return 0
    except RebuildError as error:
        sys.stderr.write("error=" + error.code + "\n")
        return 2
    except Exception:
        sys.stderr.write("error=INTERNAL_FAILURE\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
