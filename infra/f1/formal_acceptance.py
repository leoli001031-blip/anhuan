#!/usr/bin/env python3
"""Fixed, fail-closed F1.1.1 formal acceptance orchestrator.

This is the only entry point permitted to issue the fixture-only completion
label.  The caller supplies one owner-only, data-only configuration file.  It
cannot supply commands, evidence, results, a repository root, an output path,
or a capability object.  The repository, command registry and publication
directory are fixed by this module.

All subprocess output is consumed in memory, reduced to numeric counters and
SHA-256 values, and then discarded.  Raw output, command lines, paths and
configuration values never enter the public artifacts.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import urllib.parse
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from infra.f1 import artifacts_v03 as artifacts
from infra.f1.repro_verify import normalized_digest, parse_reverse_metrics
from tests import f111_clean_rebuild as clean_rebuild
from tests import f111_log_canary as log_canary


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts/f1-platform-shell/v0.3"
DEFAULT_NPM = Path("/opt/homebrew/bin/npm")
DEFAULT_GIT = Path("/usr/bin/git")
BASE_REVISION = "262bf9fb7de4b076dbb6be47c14496c5a4549333"

CONFIG_SCHEMA = "f1.1.1-formal-source-config-v2"
FORMAL_SCHEMA = "f1.1.1-formal-orchestration-v1"
MAX_CONFIG_BYTES = 32768
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_RUNTIME_INVENTORY_BYTES = 128 * 1024
MAX_CLEAN_EVIDENCE_BYTES = 64 * 1024
MAX_SECRET_BUNDLE_BYTES = 512 * 1024
MAX_SECRET_BUNDLE_FILES = 96
FORMAL_TIMEOUT_SECONDS = 900
MIN_TARGETED_TESTS = 300
MIN_FULL_TESTS = 875
BASELINE_TEST_DEFINITIONS = 850
FROZEN_F0_TEST_DEFINITIONS = 599
FROZEN_FULL_SUITE_BLOCKER = "FROZEN_F0_LIVE_ISOLATION_REQUIRED"

FROZEN_F0_TEST_MODULES = (
    "tests/test_f0e_local_ocr.py",
    "tests/test_f0f_controlled_body_gold.py",
    "tests/test_f0g_fixture_annotation.py",
    "tests/test_f0h_ppocrv6_runtime.py",
    "tests/test_f0i_canonical_chunks.py",
    "tests/test_f0j0_ragflow_probe.py",
    "tests/test_f0j0_retrieval_probe.py",
    "tests/test_f0j1_retrieval_qa.py",
    "tests/test_platform_foundation.py",
)
_FROZEN_SKIP_SITES = {
    "tests/test_f0j0_ragflow_probe.py": (
        "RagFlowProbeTests",
        "_stack_running",
    ),
    "tests/test_f0j0_retrieval_probe.py": (
        "OpenSearchProbeTests",
        "_container_running",
    ),
    "tests/test_f0j1_retrieval_qa.py": (
        "F0J1ProbeTests",
        "_stack_running",
    ),
}

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

PG_METRICS = (
    "head_mismatches",
    "schema_owner_mismatches",
    "unsafe_definer_roles",
    "definer_memberships",
    "definer_owner_mismatches",
    "definer_search_path_violations",
    "public_definer_exec",
    "rls_force_missing",
    "migration_policy_violations",
    "runtime_role_violations",
    "composite_fk_missing",
    "api_direct_write_acceptances",
    "api_set_role_acceptances",
    "api_schema_create_acceptances",
    "nonmember_visible_rows",
    "migration_write_acceptances",
    "pool_context_leaks",
    "scratch_preexisting_rows",
    "enterprise_control_failures",
    "resolver_scope_violations",
    "invite_escalation_acceptances",
    "invite_concurrency_failures",
    "invite_membership_mismatches",
    "invite_audit_mismatches",
    "upload_claim_failures",
    "upload_token_guard_failures",
    "outbox_claim_failures",
    "outbox_token_guard_failures",
    "qa_claim_state_failures",
    "qa_owner_guard_failures",
    "qa_completion_audit_failures",
    "fixture_cleanup_residuals",
    "catalog_query_failures",
)

GATE_SEQUENCE = artifacts.REQUIRED_GATES

_CONFIG_KEYS = {
    "schema",
    "secrets_directory",
    "provider_secrets_directory",
    "f0i_key_file",
    "f0g_source_scope_file",
}
_AUTHORITY_KEYS = {
    "capability",
    "capabilities",
    "cmd",
    "command",
    "commands",
    "cwd",
    "evidence",
    "output",
    "port",
    "ports",
    "project",
    "result",
    "results",
    "root",
    "runner",
    "status",
}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SECRET_NAME = re.compile(r"^[a-z0-9_]{1,64}$")
_PAIR = re.compile(rb"\b([a-z][a-z0-9_]*)=(-?[0-9]+)\b")
_TEST_COUNT = re.compile(rb"\bRan ([0-9]+) tests?\b")
_SKIP_COUNT = re.compile(rb"\bskipped=([0-9]+)\b", re.I)
_SKIP_SOURCE = re.compile(
    rb"(?:\bunittest\.SkipTest\s*\(|\bself\.skipTest\s*\(|"
    rb"^\s*@(?:unittest\.)?skip(?:If|Unless)?\s*\()",
    re.MULTILINE,
)
_TEST_DEFINITION = re.compile(rb"^[ \t]*def (test_[A-Za-z0-9_]+)[ \t]*\(")
_CLEAN_RESULT = re.compile(rb"\bCLEAN_REBUILD_RESULT_SHA256=([0-9a-f]{64})\b")
_RUNTIME_INVENTORY = re.compile(
    rb"\bF111_RUNTIME_INVENTORY_SHA256=([0-9a-f]{64})\b"
)
_LOG_CANARY = re.compile(rb"\bF111_LOG_CANARY_HITS=([0-9]+)\b")
RUNTIME_INVENTORY_SCHEMA = "f1.1.1-runtime-inventory-v2"
RUNTIME_INVENTORY_FILE = "f111-runtime-inventory.json"
DOCKER_BINARY_SHA256 = "c9766c884e4f2de2aadf8eba072d4a19f45e7f7535138cd0c8bac143f1c26644"
RUNTIME_SERVICES = frozenset(
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


class FormalError(RuntimeError):
    """A fixed-code formal acceptance failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class SourceConfig:
    secrets_directory: Path
    provider_secrets_directory: Path
    f0i_key_file: Path
    f0g_source_scope_file: Path
    f0g_source_scope: clean_rebuild.F0GSourceScope
    f0g_source_scope_sha256: str
    sha256: str


@dataclass(frozen=True, slots=True)
class FormalConfig:
    """Internally derived authority for exactly one prepared random stack."""

    project: str
    secrets_directory: Path
    provider_secrets_directory: Path
    f0i_key_file: Path
    checkout: Path
    checkout_identity: clean_rebuild.CheckoutIdentity
    source_sha256: str
    source_file_count: int
    fixture_input_sha256: tuple[tuple[str, str], ...]
    python_bridge_identity: clean_rebuild.ExecutableIdentity
    frozen_f0_inputs: clean_rebuild.FrozenF0PreparedInputs = field(repr=False)
    frozen_f0_database_snapshot: clean_rebuild.FrozenF0DatabaseSnapshot
    ports: Mapping[str, int]
    timeout_seconds: int
    # This is deliberately the stable source-input digest.  Random project
    # names and ports never enter the public artifact identity.
    sha256: str


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    sha256: str
    file_count: int


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    gate: str
    executable: str
    arguments: tuple[str, ...]
    working_directory: str = "root"
    script: str | None = None
    environment: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ProcessResult:
    exit_code: int
    output: bytes


@dataclass(frozen=True, slots=True)
class FormalResult:
    exit_code: int
    batch_id: str
    current_path: Path
    conclusion: str


@dataclass(frozen=True, slots=True)
class FormalCandidate:
    """In-memory verdict that has no publication authority."""

    payload: dict[str, Any]
    components: list[dict[str, Any]]
    accepted: bool
    contents: tuple[tuple[str, bytes], ...]


_TARGETED_MODULES = (
    "tests.test_f111_clean_rebuild",
    "tests.test_f111_f0_isolation",
    "tests.test_f111_log_canary",
    "tests.test_f111_prepare_formal_inputs",
    "tests.test_f111_recovery_idempotency",
    "tests.test_f111_repair_artifacts",
    "tests.test_f111_repair_config",
    "tests.test_f111_repair_definers",
    "tests.test_f111_repair_formal",
    "tests.test_f111_repair_keycloak",
    "tests.test_f111_repair_migration",
    "tests.test_f111_repair_pg_live_contract",
    "tests.test_f111_repair_qa",
    "tests.test_f111_repair_recovery",
    "tests.test_f111_repair_reverse",
    "tests.test_f111_repair_secrets",
    "tests.test_f111_repair_security",
    "tests.test_f111_reproducibility",
    "tests.test_f111_sbom_reconcile",
    "tests.test_f111_security_boundaries",
)

COMMAND_REGISTRY = (
    CommandSpec(
        "migration_apply_1",
        "migration_replay",
        "python",
        ("-B", "infra/f1/migrate_f1.py"),
        script="infra/f1/migrate_f1.py",
    ),
    CommandSpec(
        "migration_apply_2",
        "migration_replay",
        "python",
        ("-B", "infra/f1/migrate_f1.py"),
        script="infra/f1/migrate_f1.py",
    ),
    CommandSpec(
        "pg_live_verifier",
        "migration_replay",
        "python",
        ("-B", "tests/f111_repair_pg_verify.py"),
        script="tests/f111_repair_pg_verify.py",
    ),
    CommandSpec(
        "targeted_tests",
        "targeted_tests",
        "python",
        ("-B", "-m", "unittest", *_TARGETED_MODULES),
    ),
    CommandSpec(
        "full_repository_tests",
        "full_repository_tests",
        "python",
        ("-B", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"),
    ),
    CommandSpec("npm_ci", "npm_ci", "npm", ("ci",), "web"),
    CommandSpec("npm_lint", "npm_lint", "npm", ("run", "lint"), "web"),
    CommandSpec("npm_build", "npm_build", "npm", ("run", "build"), "web"),
    CommandSpec(
        "reverse",
        "reverse",
        "python",
        ("-B", "tests/f111_reverse_verify.py"),
        script="tests/f111_reverse_verify.py",
    ),
    CommandSpec(
        "clean_rebuild_1",
        "clean_rebuild_1",
        "python",
        ("-B", "tests/f111_clean_rebuild.py"),
        script="tests/f111_clean_rebuild.py",
        environment=(("F111_FORMAL_REBUILD_ROUND", "1"),),
    ),
    CommandSpec(
        "clean_rebuild_2",
        "clean_rebuild_2",
        "python",
        ("-B", "tests/f111_clean_rebuild.py"),
        script="tests/f111_clean_rebuild.py",
        environment=(("F111_FORMAL_REBUILD_ROUND", "2"),),
    ),
    CommandSpec(
        "log_canary",
        "log_canary",
        "python",
        ("-B", "tests/f111_log_canary.py"),
        script="tests/f111_log_canary.py",
    ),
    CommandSpec(
        "sbom_reconcile",
        "sbom_reconcile",
        "python",
        ("-B", "tests/f111_sbom_reconcile.py"),
        script="tests/f111_sbom_reconcile.py",
    ),
)


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate json key")
    return value


def _contains_authority_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in _AUTHORITY_KEYS or _contains_authority_key(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_authority_key(item) for item in value)
    return False


def _read_private_config(path: Path) -> tuple[dict[str, Any], bytes]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise FormalError("CONFIG_READ_REJECTED") from None
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or metadata.st_size < 2
            or metadata.st_size > MAX_CONFIG_BYTES
        ):
            raise FormalError("CONFIG_FILE_REJECTED")
        raw = os.read(descriptor, MAX_CONFIG_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(raw) != metadata.st_size:
        raise FormalError("CONFIG_READ_REJECTED")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise FormalError("CONFIG_JSON_REJECTED") from None
    if not isinstance(value, dict):
        raise FormalError("CONFIG_SCHEMA_REJECTED")
    return value, raw


def _private_directory(value: Any, code: str) -> Path:
    if not isinstance(value, str):
        raise FormalError(code)
    path = Path(value)
    if not path.is_absolute():
        raise FormalError(code)
    try:
        metadata = path.lstat()
    except OSError:
        raise FormalError(code) from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.geteuid()
    ):
        raise FormalError(code)
    return path


def _private_file(value: Any, code: str) -> Path:
    if not isinstance(value, str):
        raise FormalError(code)
    path = Path(value)
    if not path.is_absolute():
        raise FormalError(code)
    try:
        metadata = path.lstat()
    except OSError:
        raise FormalError(code) from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or metadata.st_size < 1
    ):
        raise FormalError(code)
    return path


def load_config(config_path: Path | str) -> SourceConfig:
    """Load the sole caller-controlled input under a strict data-only schema."""

    if not isinstance(config_path, (str, Path)):  # reject path-like capability objects
        raise FormalError("CONFIG_ARGUMENT_REJECTED")
    path = Path(config_path)
    if not path.is_absolute():
        path = path.absolute()
    value, raw = _read_private_config(path)
    if set(value) != _CONFIG_KEYS or _contains_authority_key(value):
        raise FormalError("CONFIG_AUTHORITY_REJECTED")
    if value.get("schema") != CONFIG_SCHEMA:
        raise FormalError("CONFIG_SCHEMA_REJECTED")
    secrets_directory = _private_directory(
        value.get("secrets_directory"), "CONFIG_SECRETS_REJECTED"
    )
    f0g_source_scope_file = _private_file(
        value.get("f0g_source_scope_file"), "CONFIG_F0G_SCOPE_REJECTED"
    )
    try:
        scope_metadata = f0g_source_scope_file.lstat()
        scope_raw = f0g_source_scope_file.read_bytes()
        if (
            f0g_source_scope_file.parent != secrets_directory
            or f0g_source_scope_file.name != clean_rebuild.F0G_SOURCE_SCOPE_NAME
            or scope_metadata.st_size != len(scope_raw)
            or not 2 <= len(scope_raw) <= 64 * 1024
        ):
            raise FormalError("CONFIG_F0G_SCOPE_REJECTED")
        f0g_source_scope = clean_rebuild.parse_f0g_source_scope(scope_raw)
    except FormalError:
        raise
    except Exception:
        raise FormalError("CONFIG_F0G_SCOPE_REJECTED") from None
    return SourceConfig(
        secrets_directory=secrets_directory,
        provider_secrets_directory=_private_directory(
            value.get("provider_secrets_directory"), "CONFIG_PROVIDER_REJECTED"
        ),
        f0i_key_file=_private_file(value.get("f0i_key_file"), "CONFIG_F0I_KEY_REJECTED"),
        f0g_source_scope_file=f0g_source_scope_file,
        f0g_source_scope=f0g_source_scope,
        f0g_source_scope_sha256=_sha256(scope_raw),
        sha256=_sha256(_canonical_bytes(value)),
    )


def _preparation_environment(
    config: SourceConfig, runtime_root: Path
) -> dict[str, str]:
    """Build the complete, data-only input for the fixed stack preparer."""

    try:
        root_info = runtime_root.lstat()
    except OSError:
        raise FormalError("PREPARATION_RUNTIME_REJECTED") from None
    if (
        runtime_root.parent != Path("/private/tmp")
        or not runtime_root.name.startswith("anhuan-f111-preparation-")
        or not stat.S_ISDIR(root_info.st_mode)
        or stat.S_ISLNK(root_info.st_mode)
        or stat.S_IMODE(root_info.st_mode) != 0o700
        or root_info.st_uid != os.geteuid()
    ):
        raise FormalError("PREPARATION_RUNTIME_REJECTED")
    home = runtime_root / "home"
    temporary = runtime_root / "tmp"
    try:
        home.mkdir(mode=0o700)
        temporary.mkdir(mode=0o700)
    except OSError:
        raise FormalError("PREPARATION_RUNTIME_REJECTED") from None
    return {
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(temporary),
        "F1_SECRETS_DIR": str(config.secrets_directory),
        "F111_REVERSE_SECRETS_DIR": str(config.secrets_directory),
        "F1_PROVIDER_SECRETS_DIR": str(config.provider_secrets_directory),
        "F1_F0I_KEY_FILE": str(config.f0i_key_file),
        "F111_REVERSE_TIMEOUT_SECONDS": str(FORMAL_TIMEOUT_SECONDS),
    }


def _prepared_config(config: SourceConfig, prepared: Any) -> FormalConfig:
    """Validate and internalize the random authority returned by the preparer."""

    project = getattr(prepared, "project", None)
    if not isinstance(project, str) or not project.startswith("anhuan-f111-repair-"):
        raise FormalError("PREPARED_PROJECT_REJECTED")
    suffix = project.removeprefix("anhuan-f111-repair-")
    try:
        parsed = uuid.UUID(hex=suffix)
    except (ValueError, AttributeError):
        raise FormalError("PREPARED_PROJECT_REJECTED") from None
    if parsed.version != 4 or parsed.hex != suffix:
        raise FormalError("PREPARED_PROJECT_REJECTED")

    raw_ports = getattr(prepared, "ports", None)
    if not isinstance(raw_ports, Mapping) or set(raw_ports) != set(PORT_NAMES):
        raise FormalError("PREPARED_PORTS_REJECTED")
    ports: dict[str, int] = {}
    for name in PORT_NAMES:
        port = raw_ports.get(name)
        if isinstance(port, bool) or not isinstance(port, int) or not 20000 <= port <= 60999:
            raise FormalError("PREPARED_PORTS_REJECTED")
        ports[name] = port
    if len(set(ports.values())) != len(ports):
        raise FormalError("PREPARED_PORTS_REJECTED")

    secrets = getattr(prepared, "secrets_directory", None)
    provider = getattr(prepared, "provider_secrets_directory", None)
    f0i_key = getattr(prepared, "f0i_key_file", None)
    checkout = getattr(prepared, "checkout", None)
    checkout_identity = getattr(prepared, "checkout_identity", None)
    source_sha256 = getattr(prepared, "source_snapshot_sha256", None)
    source_file_count = getattr(prepared, "source_file_count", None)
    fixture_input_sha256 = getattr(prepared, "fixture_input_sha256", None)
    python_bridge_identity = getattr(prepared, "python_bridge_identity", None)
    frozen_f0_inputs = getattr(prepared, "frozen_f0_inputs", None)
    frozen_f0_database_snapshot = getattr(
        prepared, "frozen_f0_database_snapshot", None
    )
    if not isinstance(secrets, Path):
        raise FormalError("PREPARED_SECRETS_REJECTED")
    if not isinstance(provider, Path):
        raise FormalError("PREPARED_PROVIDER_REJECTED")
    if not isinstance(f0i_key, Path):
        raise FormalError("PREPARED_F0I_KEY_REJECTED")
    try:
        prepared_f0g_raw = (
            secrets / clean_rebuild.F0G_SOURCE_SCOPE_NAME
        ).read_bytes()
        if (
            _sha256(prepared_f0g_raw) != config.f0g_source_scope_sha256
            or clean_rebuild.parse_f0g_source_scope(prepared_f0g_raw)
            != config.f0g_source_scope
        ):
            raise FormalError("PREPARED_F0G_SCOPE_REJECTED")
    except FormalError:
        raise
    except Exception:
        raise FormalError("PREPARED_F0G_SCOPE_REJECTED") from None
    if not isinstance(checkout, Path) or not checkout.is_absolute():
        raise FormalError("PREPARED_CHECKOUT_REJECTED")
    try:
        checkout_info = checkout.lstat()
        scratch_info = checkout.parent.lstat()
    except OSError:
        raise FormalError("PREPARED_CHECKOUT_REJECTED") from None
    if (
        checkout.name != "checkout"
        or checkout.parent.parent != Path("/private/tmp")
        or not checkout.parent.name.startswith(project + "-")
        or not stat.S_ISDIR(checkout_info.st_mode)
        or stat.S_ISLNK(checkout_info.st_mode)
        or checkout_info.st_uid != os.geteuid()
        or not stat.S_ISDIR(scratch_info.st_mode)
        or stat.S_ISLNK(scratch_info.st_mode)
        or stat.S_IMODE(scratch_info.st_mode) != 0o700
        or scratch_info.st_uid != os.geteuid()
        or not isinstance(checkout_identity, clean_rebuild.CheckoutIdentity)
        or checkout_identity
        != clean_rebuild.CheckoutIdentity(
            int(checkout_info.st_dev), int(checkout_info.st_ino)
        )
    ):
        raise FormalError("PREPARED_CHECKOUT_REJECTED")
    expected_fixture_inputs = tuple(
        (name, value[1])
        for name, value in sorted(clean_rebuild.FIXTURE_PLAN_CONTRACTS.items())
    )
    if (
        not isinstance(source_sha256, str)
        or not _HEX64.fullmatch(source_sha256)
        or isinstance(source_file_count, bool)
        or not isinstance(source_file_count, int)
        or source_file_count < 1
        or fixture_input_sha256 != expected_fixture_inputs
        or not isinstance(
            python_bridge_identity, clean_rebuild.ExecutableIdentity
        )
        or not isinstance(
            frozen_f0_inputs, clean_rebuild.FrozenF0PreparedInputs
        )
        or not isinstance(
            frozen_f0_database_snapshot,
            clean_rebuild.FrozenF0DatabaseSnapshot,
        )
    ):
        raise FormalError("PREPARED_CHECKOUT_EVIDENCE_REJECTED")
    try:
        if python_bridge_identity != clean_rebuild.launcher_python_identity():
            raise FormalError("PREPARED_CHECKOUT_EVIDENCE_REJECTED")
        clean_rebuild.verify_checkout_python_bridge(
            checkout, python_bridge_identity
        )
    except FormalError:
        raise
    except Exception:
        raise FormalError("PREPARED_CHECKOUT_EVIDENCE_REJECTED") from None
    try:
        clean_rebuild.verify_frozen_f0_inputs(
            config.secrets_directory, frozen_f0_inputs
        )
        loaded_isolation = clean_rebuild.load_frozen_f0_isolation(
            {
                clean_rebuild.F0_ISOLATION_ENVIRONMENT_VARIABLE: str(
                    frozen_f0_inputs.config_path
                )
            }
        )
        if (
            loaded_isolation != frozen_f0_inputs.isolation
            or frozen_f0_inputs.isolation.project_id != parsed
            or frozen_f0_inputs.isolation.postgres_port != ports["postgres"]
        ):
            raise FormalError("PREPARED_F0_ISOLATION_REJECTED")
    except FormalError:
        raise
    except Exception:
        raise FormalError("PREPARED_F0_ISOLATION_REJECTED") from None

    return FormalConfig(
        project=project,
        secrets_directory=_private_directory(
            str(secrets),
            "PREPARED_SECRETS_REJECTED",
        ),
        provider_secrets_directory=_private_directory(
            str(provider),
            "PREPARED_PROVIDER_REJECTED",
        ),
        f0i_key_file=_private_file(
            str(f0i_key),
            "PREPARED_F0I_KEY_REJECTED",
        ),
        checkout=checkout,
        checkout_identity=checkout_identity,
        source_sha256=source_sha256,
        source_file_count=source_file_count,
        fixture_input_sha256=fixture_input_sha256,
        python_bridge_identity=python_bridge_identity,
        frozen_f0_inputs=frozen_f0_inputs,
        frozen_f0_database_snapshot=frozen_f0_database_snapshot,
        ports=ports,
        timeout_seconds=FORMAL_TIMEOUT_SECONDS,
        sha256=config.sha256,
    )


def command_registry_sha256() -> str:
    value = [
        {
            "name": item.name,
            "gate": item.gate,
            "executable": item.executable,
            "arguments": item.arguments,
            "working_directory": item.working_directory,
            "script": item.script,
            "environment": item.environment,
        }
        for item in COMMAND_REGISTRY
    ]
    return _sha256(_canonical_bytes(value))


def _git_bytes(arguments: Sequence[str]) -> bytes:
    try:
        executable = DEFAULT_GIT.resolve(strict=True)
        metadata = executable.stat()
    except OSError:
        raise FormalError("GIT_UNAVAILABLE") from None
    if not stat.S_ISREG(metadata.st_mode) or not os.access(executable, os.X_OK):
        raise FormalError("GIT_UNAVAILABLE")
    try:
        completed = subprocess.run(
            [str(executable), *arguments],
            cwd=str(ROOT),
            env={
                "HOME": "/private/tmp",
                "TMPDIR": "/private/tmp",
                "USER": "formal",
                "LOGNAME": "formal",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PATH": "/usr/bin:/bin",
            },
            capture_output=True,
            text=False,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise FormalError("GIT_UNAVAILABLE") from None
    if completed.returncode != 0 or completed.stderr:
        raise FormalError("GIT_BOUNDARY_REJECTED")
    return bytes(completed.stdout)


def _allowed_change_path(relative: str) -> bool:
    if (
        not relative
        or relative.startswith(("/", ".env"))
        or "\x00" in relative
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        return False
    exact = {
        ".dockerignore",
        ".gitignore",
        "F1_1_1_REPAIR_TASKBOOK.md",
        "F1_1_1_REPAIR_PROGRESS.md",
        "F1_1_1_REPAIR_BLOCKED.md",
        "requirements/requirements-f1.lock",
        "tests/f11_support.py",
        "tests/f11_reverse_verify.py",
        "artifacts/f1-platform-shell/v0.2/revocation.json",
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
    }
    if relative in exact:
        return True
    if relative.startswith("artifacts/f1-platform-shell/v0.3/"):
        return True
    if relative.startswith("src/platform_foundation/f1/"):
        return True
    if relative.startswith("src/web/"):
        return True
    if relative.startswith("infra/f1/"):
        frozen = re.fullmatch(
            r"infra/f1/alembic/versions/f1_000[123][^/]*\.py", relative
        )
        return frozen is None
    if relative.startswith("tests/"):
        name = relative.removeprefix("tests/")
        return "/" not in name and bool(
            re.fullmatch(r"(?:test_f1_.*|test_f11_.*|test_f111_.*|f111_.*)\.py", name)
        )
    return False


def _repository_boundary() -> tuple[str, ...]:
    tracked = _git_bytes(("diff", "--name-only", "-z", BASE_REVISION, "--"))
    untracked = _git_bytes(("ls-files", "--others", "--exclude-standard", "-z"))
    try:
        names = {
            item.decode("utf-8")
            for item in (tracked + untracked).split(b"\x00")
            if item
        }
    except UnicodeDecodeError:
        raise FormalError("GIT_PATH_ENCODING_REJECTED") from None
    if any(not _allowed_change_path(name) for name in names):
        raise FormalError("WORKTREE_BOUNDARY_REJECTED")
    _require_baseline_tests()
    _require_frozen_f0_contract()
    _reject_new_skip_constructs(names)
    return tuple(sorted(names))


def _require_baseline_tests() -> None:
    """Require every test definition present at the frozen repair base.

    Counts alone are insufficient because a live regression can otherwise be
    replaced by a newly added static test.  The Counter preserves duplicate
    method names in the same module without depending on unstable line
    numbers.
    """

    raw = _git_bytes(
        (
            "grep",
            "-n",
            "-E",
            "^[[:space:]]*def test_",
            BASE_REVISION,
            "--",
            "tests",
        )
    )
    baseline: Counter[tuple[str, str]] = Counter()
    for line in raw.splitlines():
        fields = line.split(b":", 3)
        if len(fields) != 4:
            raise FormalError("BASELINE_TEST_INDEX_REJECTED")
        try:
            relative = fields[1].decode("utf-8")
        except UnicodeDecodeError:
            raise FormalError("BASELINE_TEST_INDEX_REJECTED") from None
        match = _TEST_DEFINITION.match(fields[3])
        if not relative.startswith("tests/") or match is None:
            raise FormalError("BASELINE_TEST_INDEX_REJECTED")
        baseline[(relative, match.group(1).decode("ascii"))] += 1
    if sum(baseline.values()) != BASELINE_TEST_DEFINITIONS:
        raise FormalError("BASELINE_TEST_INDEX_REJECTED")

    current: Counter[tuple[str, str]] = Counter()
    test_root = ROOT / "tests"
    if not test_root.is_dir() or test_root.is_symlink():
        raise FormalError("BASELINE_TEST_REMOVED")
    for path in sorted(test_root.rglob("*.py")):
        if path.is_symlink() or not path.is_file():
            raise FormalError("BASELINE_TEST_REMOVED")
        try:
            source = path.read_bytes()
        except OSError:
            raise FormalError("BASELINE_TEST_REMOVED") from None
        relative = path.relative_to(ROOT).as_posix()
        for line in source.splitlines():
            match = _TEST_DEFINITION.match(line)
            if match is not None:
                current[(relative, match.group(1).decode("ascii"))] += 1
    if baseline - current:
        raise FormalError("BASELINE_TEST_REMOVED")


def _frozen_test_methods(raw: bytes, relative: str) -> Counter[str]:
    try:
        tree = ast.parse(raw, filename=relative)
    except (SyntaxError, ValueError, TypeError):
        raise FormalError("FROZEN_F0_TEST_CONTRACT_REJECTED") from None
    methods: Counter[str] = Counter()

    def visit_body(body: Sequence[ast.stmt], prefix: tuple[str, ...]) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                visit_body(node.body, (*prefix, node.name))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test_"
            ):
                methods[".".join((relative, *prefix, node.name))] += 1

    visit_body(tree.body, ())
    return methods


def _skip_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    function = node.func
    if isinstance(function, ast.Attribute):
        return function.attr == "SkipTest" or function.attr == "skipTest" or function.attr.startswith(
            "skip"
        )
    return isinstance(function, ast.Name) and (
        function.id == "SkipTest" or function.id == "skipTest" or function.id.startswith("skip")
    )


def _frozen_skip_site_valid(
    tree: ast.Module, class_name: str, running_function: str
) -> bool:
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    ]
    if len(classes) != 1:
        return False
    methods = [
        node
        for node in classes[0].body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "setUpClass"
    ]
    if len(methods) != 1:
        return False
    method = methods[0]
    skip_calls = [node for node in ast.walk(method) if _skip_call(node)]
    if len(skip_calls) != 1:
        return False
    for node in method.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (
            not isinstance(test, ast.UnaryOp)
            or not isinstance(test.op, ast.Not)
            or not isinstance(test.operand, ast.Call)
            or not isinstance(test.operand.func, ast.Name)
            or test.operand.func.id != running_function
            or test.operand.args
            or test.operand.keywords
        ):
            continue
        if len(node.body) != 1 or not isinstance(node.body[0], ast.Raise):
            return False
        error = node.body[0].exc
        if (
            error is not skip_calls[0]
            or not isinstance(error, ast.Call)
            or not isinstance(error.func, ast.Attribute)
            or not isinstance(error.func.value, ast.Name)
            or error.func.value.id != "unittest"
            or error.func.attr != "SkipTest"
            or len(error.args) != 1
            or error.keywords
            or not isinstance(error.args[0], ast.Call)
            or not isinstance(error.args[0].func, ast.Name)
            or error.args[0].func.id != "_skip_reason"
            or error.args[0].args
            or error.args[0].keywords
        ):
            return False
        return True
    return False


def _require_frozen_f0_contract() -> None:
    baseline: Counter[str] = Counter()
    current: Counter[str] = Counter()
    skip_count = 0
    for relative in FROZEN_F0_TEST_MODULES:
        baseline_raw = _git_bytes(("show", f"{BASE_REVISION}:{relative}"))
        try:
            current_raw = (ROOT / relative).read_bytes()
        except OSError:
            raise FormalError("FROZEN_F0_TEST_CONTRACT_REJECTED") from None
        baseline.update(_frozen_test_methods(baseline_raw, relative))
        current.update(_frozen_test_methods(current_raw, relative))
        try:
            tree = ast.parse(current_raw, filename=relative)
        except (SyntaxError, ValueError, TypeError):
            raise FormalError("FROZEN_F0_TEST_CONTRACT_REJECTED") from None
        observed = sum(1 for node in ast.walk(tree) if _skip_call(node))
        skip_count += observed
        expected_site = _FROZEN_SKIP_SITES.get(relative)
        if expected_site is None:
            if observed:
                raise FormalError("FROZEN_F0_SKIP_CONTRACT_REJECTED")
        elif observed != 1 or not _frozen_skip_site_valid(tree, *expected_site):
            raise FormalError("FROZEN_F0_SKIP_CONTRACT_REJECTED")
    if (
        sum(baseline.values()) != FROZEN_F0_TEST_DEFINITIONS
        or current != baseline
    ):
        raise FormalError("FROZEN_F0_TEST_CONTRACT_REJECTED")
    if skip_count != len(_FROZEN_SKIP_SITES):
        raise FormalError("FROZEN_F0_SKIP_CONTRACT_REJECTED")


def _reject_new_skip_constructs(names: Sequence[str]) -> None:
    for relative in names:
        if not relative.startswith("tests/") or not relative.endswith(".py"):
            continue
        if relative in _FROZEN_SKIP_SITES:
            continue
        try:
            raw = (ROOT / relative).read_bytes()
        except OSError:
            raise FormalError("TEST_SOURCE_READ_REJECTED") from None
        if _SKIP_SOURCE.search(raw):
            raise FormalError("NEW_TEST_SKIP_FORBIDDEN")


def _source_files() -> list[Path]:
    _repository_boundary()
    excluded = {"__pycache__", "node_modules", "dist", ".next", ".pytest_cache"}
    files: set[Path] = set()
    for relative in ("infra/f1", "src/platform_foundation/f1", "src/web"):
        base = ROOT / relative
        if not base.is_dir() or base.is_symlink():
            raise FormalError("SOURCE_TREE_REJECTED")
        for path in base.rglob("*"):
            rel = path.relative_to(ROOT)
            if any(part in excluded for part in rel.parts):
                continue
            try:
                metadata = path.lstat()
            except OSError:
                raise FormalError("SOURCE_TREE_REJECTED") from None
            if stat.S_ISLNK(metadata.st_mode):
                raise FormalError("SOURCE_SYMLINK_REJECTED")
            if stat.S_ISREG(metadata.st_mode):
                files.add(path)
    lock = ROOT / "requirements/requirements-f1.lock"
    if not lock.is_file() or lock.is_symlink():
        raise FormalError("SOURCE_TREE_REJECTED")
    files.add(lock)
    for relative in (
        ".dockerignore",
        ".gitignore",
        "F1_1_1_REPAIR_TASKBOOK.md",
        "F1_1_1_REPAIR_PROGRESS.md",
        "F1_1_1_REPAIR_BLOCKED.md",
        "artifacts/f1-platform-shell/v0.2/revocation.json",
    ):
        path = ROOT / relative
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise FormalError("SOURCE_TREE_REJECTED")
            files.add(path)
    tests = ROOT / "tests"
    for pattern in (
        "test_f1*.py",
        "test_f11*.py",
        "f111*.py",
        "f11_support.py",
        "f11_reverse_verify.py",
    ):
        for path in tests.glob(pattern):
            if path.is_symlink() or not path.is_file():
                raise FormalError("SOURCE_TREE_REJECTED")
            files.add(path)
    return sorted(files, key=lambda item: item.relative_to(ROOT).as_posix())


def _source_snapshot() -> SourceSnapshot:
    """Bind the complete delivery manifest used by tracked-only rebuilds."""

    _repository_boundary()
    try:
        if ROOT.resolve(strict=True) != clean_rebuild.ROOT.resolve(strict=True):
            raise FormalError("SOURCE_ROOT_MISMATCH")
        with tempfile.TemporaryDirectory(
            prefix="anhuan-f111-source-snapshot-", dir="/private/tmp"
        ) as raw_runtime:
            runtime = Path(raw_runtime)
            runtime.chmod(0o700)
            home = runtime / "home"
            temporary = runtime / "tmp"
            home.mkdir(mode=0o700)
            temporary.mkdir(mode=0o700)
            snapshot = clean_rebuild.capture_source(
                clean_rebuild._base_environment(
                    {
                        "HOME": str(home),
                        "TMPDIR": str(temporary),
                        "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                    }
                )
            )
    except FormalError:
        raise
    except Exception:
        raise FormalError("SOURCE_READ_REJECTED") from None
    if not snapshot.entries or not _HEX64.fullmatch(snapshot.sha256):
        raise FormalError("SOURCE_TREE_EMPTY")
    return SourceSnapshot(snapshot.sha256, len(snapshot.entries))


def _executable(
    spec: CommandSpec,
    checkout: Path,
    python_identity: clean_rebuild.ExecutableIdentity | None = None,
) -> Path:
    if spec.executable == "python":
        clean_rebuild.verify_checkout_python_bridge(checkout, python_identity)
        return checkout / ".venv/bin/python"
    if spec.executable == "npm":
        return DEFAULT_NPM
    raise FormalError("COMMAND_REGISTRY_REJECTED")


def _working_directory(spec: CommandSpec, checkout: Path) -> Path:
    if spec.working_directory == "root":
        return checkout
    if spec.working_directory == "web":
        return checkout / "src/web"
    raise FormalError("COMMAND_REGISTRY_REJECTED")


def _command_available(
    spec: CommandSpec,
    checkout: Path,
    python_identity: clean_rebuild.ExecutableIdentity | None = None,
) -> bool:
    try:
        executable = _executable(spec, checkout, python_identity)
    except (FormalError, clean_rebuild.RebuildError):
        return False
    try:
        target = executable.resolve(strict=True)
        metadata = target.stat()
    except OSError:
        return False
    if not stat.S_ISREG(metadata.st_mode) or not os.access(target, os.X_OK):
        return False
    working = _working_directory(spec, checkout)
    if not working.is_dir() or working.is_symlink():
        return False
    if spec.script is not None:
        script = checkout / spec.script
        try:
            script_metadata = script.lstat()
        except OSError:
            return False
        if not stat.S_ISREG(script_metadata.st_mode) or stat.S_ISLNK(script_metadata.st_mode):
            return False
    return True


def _full_repository_isolation_blocker(
    config: FormalConfig | None = None,
    environment: Mapping[str, str] | None = None,
) -> str | None:
    """Return ``None`` only for the prepared UUID-scoped frozen-F0 boundary."""

    if not isinstance(config, FormalConfig) or environment is None:
        return FROZEN_FULL_SUITE_BLOCKER
    try:
        isolation = config.frozen_f0_inputs.isolation
        if environment.get(clean_rebuild.F0_ISOLATION_ENVIRONMENT_VARIABLE) != str(
            config.frozen_f0_inputs.config_path
        ):
            return FROZEN_FULL_SUITE_BLOCKER
        clean_rebuild.verify_frozen_f0_inputs(
            config.secrets_directory, config.frozen_f0_inputs
        )
        clean_rebuild.verify_frozen_f0_project_absence(
            isolation,
            environment,
            cwd=config.checkout,
        )
        observed = clean_rebuild.capture_frozen_f0_database_snapshot(
            config.project,
            isolation,
            environment,
            cwd=config.checkout,
        )
        if observed != config.frozen_f0_database_snapshot:
            return FROZEN_FULL_SUITE_BLOCKER
    except Exception:
        return FROZEN_FULL_SUITE_BLOCKER
    return None


def _environment(config: FormalConfig, runtime_home: Path) -> dict[str, str]:
    suffix = config.project.removeprefix("anhuan-f111-repair-")
    ports = config.ports
    if (
        not runtime_home.is_absolute()
        or runtime_home.parent != Path("/private/tmp")
        or not runtime_home.is_dir()
        or runtime_home.is_symlink()
        or stat.S_IMODE(runtime_home.stat().st_mode) != 0o700
    ):
        raise FormalError("FORMAL_RUNTIME_HOME_REJECTED")
    cache = runtime_home / "cache"
    configuration = runtime_home / "config"
    temporary = runtime_home / "tmp"
    for directory in (cache, configuration, temporary):
        directory.mkdir(mode=0o700)
    environment = {
        "HOME": str(runtime_home),
        "XDG_CACHE_HOME": str(cache),
        "XDG_CONFIG_HOME": str(configuration),
        "npm_config_cache": str(cache / "npm"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "PYTHONDONTWRITEBYTECODE": "1",
        # Targeted legacy tests import the shared acceptance helper as the
        # top-level ``f11_support`` module.  Keep both roots explicit inside
        # the fixed repository boundary; never inherit the caller's path.
        "PYTHONPATH": os.pathsep.join(
            (str(config.checkout / "src"), str(config.checkout / "tests"))
        ),
        "TMPDIR": str(temporary),
        "F1_SECRETS_DIR": str(config.secrets_directory),
        "F1_PROVIDER_SECRETS_DIR": str(config.provider_secrets_directory),
        "F1_F0I_KEY_FILE": str(config.f0i_key_file),
        "F1_PG_HOST": "127.0.0.1",
        "F1_PG_PORT": str(ports["postgres"]),
        "F1_PG_DATABASE": "f111_repair_" + suffix,
        "F1_API_HOST_PORT": str(ports["api"]),
        "F1_GRAFANA_HOST_PORT": str(ports["grafana"]),
        "F1_JAEGER_OTLP_GRPC_HOST_PORT": str(ports["jaeger_grpc"]),
        "F1_JAEGER_OTLP_HTTP_HOST_PORT": str(ports["jaeger_http"]),
        "F1_JAEGER_UI_HOST_PORT": str(ports["jaeger_ui"]),
        "F1_KEYCLOAK_HOST_PORT": str(ports["keycloak"]),
        "F1_MINIO_API_HOST_PORT": str(ports["minio_api"]),
        "F1_MINIO_CONSOLE_HOST_PORT": str(ports["minio_console"]),
        "F1_PROMETHEUS_HOST_PORT": str(ports["prometheus"]),
        "F1_RAGFLOW_API_HOST_PORT": str(ports["ragflow_api"]),
        "F1_RAGFLOW_HTTP_HOST_PORT": str(ports["ragflow_http"]),
        "F1_REDIS_HOST_PORT": str(ports["redis"]),
        "F1_WEB_HOST_PORT": str(ports["web"]),
        "F1_KEYCLOAK_ISSUER_URL": (
            f"http://127.0.0.1:{ports['keycloak']}/realms/anhuan"
        ),
        "F1_WEB_PUBLIC_ORIGIN": f"http://127.0.0.1:{ports['web']}",
        "F111_REVERSE_PROJECT": config.project,
        "F111_REVERSE_SECRETS_DIR": str(config.secrets_directory),
        "F111_REVERSE_API_BASE": f"http://127.0.0.1:{ports['api']}",
        "F111_REVERSE_KEYCLOAK_BASE": f"http://127.0.0.1:{ports['keycloak']}",
        "F111_REVERSE_COMPOSE_OVERRIDE": str(
            config.checkout / "infra/f1/docker-compose.repair.yml"
        ),
        "F111_REVERSE_TIMEOUT_SECONDS": str(min(config.timeout_seconds, 900)),
        "F111_FORMAL_CONFIG_SHA256": config.sha256,
        "F111_FORMAL_RUN_ID": config.project,
        clean_rebuild.F0_ISOLATION_ENVIRONMENT_VARIABLE: str(
            config.frozen_f0_inputs.config_path
        ),
    }
    return environment


def _rewrite_database_dsn(
    raw: bytes,
    *,
    expected_user: str,
    expected_database: str,
    expected_port: int,
) -> bytes:
    try:
        value = raw.decode("ascii").strip()
        parsed = urllib.parse.urlsplit(value)
        user = urllib.parse.unquote(parsed.username or "")
        password = urllib.parse.unquote(parsed.password or "")
        port = parsed.port
    except (UnicodeDecodeError, ValueError):
        raise FormalError("FORMAL_DATABASE_SECRET_REJECTED") from None
    if (
        parsed.scheme != "postgresql"
        or user != expected_user
        or not password
        or parsed.hostname not in {"host.docker.internal", "127.0.0.1"}
        or port != expected_port
        or parsed.path != "/" + expected_database
        or parsed.query
        or parsed.fragment
        or any(ord(character) < 0x20 for character in password)
    ):
        raise FormalError("FORMAL_DATABASE_SECRET_REJECTED")
    rewritten = urllib.parse.urlunsplit(
        (
            "postgresql",
            urllib.parse.quote(user, safe="")
            + ":"
            + urllib.parse.quote(password, safe="")
            + f"@127.0.0.1:{expected_port}",
            "/" + expected_database,
            "",
            "",
        )
    )
    return (rewritten + "\n").encode("ascii")


def _materialize_host_secrets(
    config: FormalConfig, destination: Path
) -> Path:
    """Make an owner-only host view of the container-oriented bundle.

    Clean-stack DSNs use ``host.docker.internal``.  Formal migration commands
    run on the host and must not inherit or mutate that source bundle, so only
    the two bootstrap DSNs are rewritten in a private runtime copy.
    """

    prefix = config.project + "-bundle-"
    try:
        destination_info = destination.lstat()
        if (
            destination.parent != Path("/private/tmp")
            or not destination.name.startswith(prefix)
            or not stat.S_ISDIR(destination_info.st_mode)
            or stat.S_ISLNK(destination_info.st_mode)
            or stat.S_IMODE(destination_info.st_mode) != 0o700
            or destination_info.st_uid != os.geteuid()
        ):
            raise FormalError("FORMAL_SECRET_COPY_REJECTED")
        entries = sorted(config.secrets_directory.iterdir(), key=lambda item: item.name)
    except OSError:
        raise FormalError("FORMAL_SECRET_COPY_REJECTED") from None
    if not 1 <= len(entries) <= MAX_SECRET_BUNDLE_FILES:
        raise FormalError("FORMAL_SECRET_COPY_REJECTED")
    total = 0
    runtime_bundle_phases = {
        name: phase
        for phase, (name, _maximum) in clean_rebuild.RUNTIME_TREE_BUNDLES.items()
    }
    expected_database = "f111_repair_" + config.project.removeprefix(
        "anhuan-f111-repair-"
    )
    seen: set[str] = set()
    for source in entries:
        name = source.name
        try:
            metadata = source.lstat()
        except OSError:
            raise FormalError("FORMAL_SECRET_COPY_REJECTED") from None
        if (
            not _SECRET_NAME.fullmatch(name)
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or not 1 <= metadata.st_size <= (
                clean_rebuild.MAX_SOURCE_BUNDLE_BYTES
                if name == clean_rebuild.SOURCE_BUNDLE_NAME
                else clean_rebuild.RUNTIME_TREE_BUNDLES[
                    runtime_bundle_phases[name]
                ][1]
                if name in runtime_bundle_phases
                else MAX_SECRET_BUNDLE_BYTES
            )
        ):
            raise FormalError("FORMAL_SECRET_COPY_REJECTED")
        if name == clean_rebuild.SOURCE_BUNDLE_NAME:
            try:
                identity, _records, _written = clean_rebuild._fixture_source_bundle(
                    config.secrets_directory, None
                )
                clean_rebuild.copy_fixture_source_bundle(
                    config.secrets_directory, destination, identity
                )
            except Exception:
                raise FormalError("FORMAL_SOURCE_BUNDLE_COPY_REJECTED") from None
            seen.add(name)
            continue
        if name in runtime_bundle_phases:
            phase = runtime_bundle_phases[name]
            try:
                identity, _tree, _entries, writes = (
                    clean_rebuild._frozen_runtime_tree_bundle(
                        config.secrets_directory, phase, None
                    )
                )
                if writes:
                    raise FormalError("FORMAL_RUNTIME_BUNDLE_COPY_REJECTED")
                clean_rebuild.copy_frozen_runtime_tree_bundle(
                    config.secrets_directory,
                    destination,
                    phase,
                    identity,
                )
            except FormalError:
                raise
            except Exception:
                raise FormalError("FORMAL_RUNTIME_BUNDLE_COPY_REJECTED") from None
            seen.add(name)
            continue
        total += metadata.st_size
        if total > MAX_SECRET_BUNDLE_BYTES:
            raise FormalError("FORMAL_SECRET_COPY_REJECTED")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(source, flags)
            with os.fdopen(descriptor, "rb") as stream:
                raw = stream.read(metadata.st_size + 1)
        except OSError:
            raise FormalError("FORMAL_SECRET_COPY_REJECTED") from None
        if len(raw) != metadata.st_size:
            raise FormalError("FORMAL_SECRET_COPY_REJECTED")
        if name == "f1_bootstrap_dsn":
            raw = _rewrite_database_dsn(
                raw,
                expected_user="f0d_bootstrap",
                expected_database=expected_database,
                expected_port=config.ports["postgres"],
            )
        elif name == "f1_migration_dsn":
            raw = _rewrite_database_dsn(
                raw,
                expected_user="f0d_migration",
                expected_database=expected_database,
                expected_port=config.ports["postgres"],
            )
        target = destination / name
        try:
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            raise FormalError("FORMAL_SECRET_COPY_REJECTED") from None
        seen.add(name)
    if clean_rebuild.SOURCE_BUNDLE_NAME not in seen:
        raise FormalError("FORMAL_SOURCE_BUNDLE_COPY_REJECTED")
    if not set(runtime_bundle_phases).issubset(seen):
        raise FormalError("FORMAL_RUNTIME_BUNDLE_COPY_REJECTED")
    if not {"f1_bootstrap_dsn", "f1_migration_dsn"}.issubset(seen):
        raise FormalError("FORMAL_DATABASE_SECRET_REJECTED")
    return destination


@contextmanager
def _host_secret_bundle(config: FormalConfig) -> Iterator[Path]:
    """Yield a short-lived, direct child of /private/tmp for host gates."""

    raw_path: str | None = None
    try:
        with tempfile.TemporaryDirectory(
            prefix=config.project + "-bundle-", dir="/private/tmp"
        ) as raw_path:
            destination = Path(raw_path)
            destination.chmod(0o700)
            yield _materialize_host_secrets(config, destination)
    except FormalError:
        raise
    except OSError:
        raise FormalError("FORMAL_SECRET_COPY_REJECTED") from None
    finally:
        if raw_path is not None and Path(raw_path).exists():
            # TemporaryDirectory normally owns this.  Reaching this branch
            # means removal failed; publication must fail closed.
            raise FormalError("FORMAL_SECRET_CLEANUP_RED")


def _run_process(
    spec: CommandSpec,
    environment: dict[str, str],
    timeout: int,
    checkout: Path,
    python_identity: clean_rebuild.ExecutableIdentity,
) -> ProcessResult:
    command = [
        str(_executable(spec, checkout, python_identity)),
        *spec.arguments,
    ]
    child_environment = dict(environment)
    child_environment.update(dict(spec.environment))
    try:
        completed = subprocess.run(
            command,
            cwd=str(_working_directory(spec, checkout)),
            env=child_environment,
            capture_output=True,
            text=False,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ProcessResult(124, b"FORMAL_TIMEOUT\n")
    except OSError:
        return ProcessResult(127, b"FORMAL_EXEC_UNAVAILABLE\n")
    output = bytes(completed.stdout) + bytes(completed.stderr)
    if len(output) > MAX_OUTPUT_BYTES:
        return ProcessResult(125, b"FORMAL_OUTPUT_LIMIT\n")
    return ProcessResult(int(completed.returncode), output)


def _parse_exact_metrics(output: bytes, expected: Sequence[str]) -> dict[str, int] | None:
    candidates: list[dict[str, int]] = []
    expected_set = set(expected)
    for line in output.splitlines():
        pairs = _PAIR.findall(line)
        if not pairs:
            continue
        names = [name.decode("ascii") for name, _value in pairs]
        if set(names) & expected_set:
            if len(names) != len(set(names)):
                return None
            parsed = {
                name.decode("ascii"): int(value)
                for name, value in pairs
            }
            if set(parsed) != expected_set:
                return None
            candidates.append(parsed)
    return candidates[0] if len(candidates) == 1 else None


def _test_output_valid(output: bytes, minimum: int, *, expected_skips: int) -> bool:
    counts = [int(value) for value in _TEST_COUNT.findall(output)]
    skips = [int(value) for value in _SKIP_COUNT.findall(output)]
    skip_count = skips[0] if len(skips) == 1 else 0 if not skips else -1
    return (
        len(counts) == 1
        and counts[0] >= minimum
        and skip_count == expected_skips
    )


def _combined_output_digest(
    results: Sequence[tuple[str, ProcessResult]], normalization_root: Path
) -> str:
    values = [
        {
            "name": name,
            "exit": result.exit_code,
            "normalized_output_sha256": normalized_digest(
                result.output.decode("utf-8", errors="replace"), normalization_root
            ),
        }
        for name, result in results
    ]
    return _sha256(_canonical_bytes(values))


def _gate_record(
    gate: str,
    results: Sequence[tuple[str, ProcessResult]],
    inventory_sha256: str,
    normalization_root: Path,
) -> tuple[dict[str, Any], list[str]]:
    blockers: list[str] = []
    exit_code = 0
    if not results or any(result.exit_code != 0 for _name, result in results):
        exit_code = next(
            (result.exit_code for _name, result in results if result.exit_code != 0),
            127,
        )
        blockers.append(f"GATE_RED_{gate.upper()}")
    record: dict[str, Any] = {
        "exit": int(exit_code),
        "normalized_output_sha256": _combined_output_digest(
            results, normalization_root
        ),
    }
    by_name = {name: result for name, result in results}
    if gate == "migration_replay":
        for name in ("migration_apply_1", "migration_apply_2"):
            result = by_name.get(name)
            if result is None or result.output.count(b"F1_MIGRATE_OK") != 1:
                blockers.append("MIGRATION_REPLAY_MARKER_INVALID")
        pg = by_name.get("pg_live_verifier")
        metrics = _parse_exact_metrics(pg.output, PG_METRICS) if pg else None
        if metrics is None or any(metrics.get(name) != 0 for name in PG_METRICS):
            blockers.append("PG_LIVE_VERIFIER_RED")
    elif gate == "targeted_tests":
        result = by_name.get(gate)
        if result is None or not _test_output_valid(
            result.output, MIN_TARGETED_TESTS, expected_skips=0
        ):
            blockers.append("TARGETED_TEST_CONTRACT_RED")
    elif gate == "full_repository_tests":
        result = by_name.get(gate)
        if result is None or not _test_output_valid(
            result.output, MIN_FULL_TESTS, expected_skips=3
        ):
            blockers.append("FULL_TEST_CONTRACT_RED")
    elif gate == "reverse":
        result = by_name.get(gate)
        metrics: dict[str, int] | None = None
        if result is not None:
            try:
                metrics = parse_reverse_metrics(result.output.decode("utf-8", errors="replace"))
            except ValueError:
                metrics = None
        if metrics is None:
            record["metrics"] = {}
            blockers.append("REVERSE_METRICS_RED")
        else:
            record["metrics"] = metrics
            if any(metrics.get(name) != 0 for name in artifacts.REVERSE_METRICS):
                blockers.append("REVERSE_METRICS_RED")
    elif gate.startswith("clean_rebuild_"):
        result = by_name.get(gate)
        matches = _CLEAN_RESULT.findall(result.output) if result else []
        if len(matches) == 1:
            record["result_sha256"] = matches[0].decode("ascii")
        else:
            record["result_sha256"] = "INVALID"
            blockers.append("CLEAN_REBUILD_MARKER_RED")
    elif gate == "log_canary":
        result = by_name.get(gate)
        hits = _LOG_CANARY.findall(result.output) if result else []
        if len(hits) != 1 or int(hits[0]) != 0:
            blockers.append("LOG_CANARY_RED")
    elif gate == "sbom_reconcile":
        result = by_name.get(gate)
        values = _RUNTIME_INVENTORY.findall(result.output) if result else []
        value = values[0].decode("ascii") if len(values) == 1 else "INVALID"
        record["inventory_sha256"] = inventory_sha256
        record["runtime_inventory_sha256"] = value
        if not _HEX64.fullmatch(value):
            blockers.append("RUNTIME_IMAGE_RECONCILIATION_RED")
    if blockers:
        record["exit"] = 2 if record["exit"] == 0 else record["exit"]
    return record, blockers


def _load_clean_evidence(
    environment: Mapping[str, str],
    *,
    round_number: int,
    expected_result_sha256: str,
    expected_source_sha256: str,
) -> dict[str, Any]:
    """Load and independently validate one cleanup-complete round document."""

    name = clean_rebuild.CLEAN_EVIDENCE_NAMES.get(round_number)
    if not isinstance(name, str):
        raise FormalError("CLEAN_EVIDENCE_ROUND_REJECTED")
    temporary = Path(environment.get("TMPDIR", ""))
    target = temporary / name
    try:
        metadata = target.lstat()
    except OSError:
        raise FormalError("CLEAN_EVIDENCE_MISSING") from None
    if (
        target.parent != temporary
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or not 2 <= metadata.st_size <= MAX_CLEAN_EVIDENCE_BYTES
    ):
        raise FormalError("CLEAN_EVIDENCE_FILE_REJECTED")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(target, flags)
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read(MAX_CLEAN_EVIDENCE_BYTES + 1)
    except OSError:
        raise FormalError("CLEAN_EVIDENCE_READ_REJECTED") from None
    if len(raw) != metadata.st_size:
        raise FormalError("CLEAN_EVIDENCE_READ_REJECTED")
    try:
        document = json.loads(raw, object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise FormalError("CLEAN_EVIDENCE_JSON_REJECTED") from None
    if not isinstance(document, dict):
        raise FormalError("CLEAN_EVIDENCE_SCHEMA_REJECTED")
    try:
        calculated = clean_rebuild.validate_round_evidence(document, round_number)
    except Exception as error:
        code = getattr(error, "code", "")
        if code == "CLEAN_EVIDENCE_BINDING_RED":
            raise FormalError("CLEAN_EVIDENCE_BINDING_RED") from None
        raise FormalError("CLEAN_EVIDENCE_SCHEMA_REJECTED") from None
    if calculated != expected_result_sha256:
        raise FormalError("CLEAN_EVIDENCE_RESULT_REJECTED")
    source = document.get("source")
    if (
        not isinstance(source, dict)
        or source.get("snapshot_sha256") != expected_source_sha256
    ):
        raise FormalError("CLEAN_EVIDENCE_SOURCE_REJECTED")
    return document


def _clean_evidence_projection(
    first: Mapping[str, Any], second: Mapping[str, Any]
) -> dict[str, Any]:
    """Return the stable, body-free structure bound into public acceptance."""

    first_core = {key: value for key, value in first.items() if key != "round"}
    second_core = {key: value for key, value in second.items() if key != "round"}
    if _canonical_bytes(first_core) != _canonical_bytes(second_core):
        raise FormalError("CLEAN_EVIDENCE_NONDETERMINISTIC")
    return {
        "schema": clean_rebuild.CLEAN_EVIDENCE_SCHEMA,
        "result_sha256": str(first["result"]["sha256"]),
        "source_sha256": str(first["source"]["snapshot_sha256"]),
        "f0i_sha256": str(first["f0i"]["fixture_source_sha256"]),
        "e2e_sha256": str(first["e2e"]["fixture_sha256"]),
        "schema_sha256": str(first["schema"]["sha256"]),
        "pg_sha256": str(first["pg"]["contract_sha256"]),
        "runtime_sha256": str(first["runtime"]["inventory_sha256"]),
        "service_count": int(first["service"]["count"]),
        "cleanup_residuals": int(first["cleanup"]["residuals"]),
        "round_1_evidence_sha256": _sha256(_canonical_bytes(first)),
        "round_2_evidence_sha256": _sha256(_canonical_bytes(second)),
    }


def _load_runtime_inventory(
    environment: Mapping[str, str],
    *,
    checkout: Path,
    expected_static_sha256: str,
    expected_runtime_sha256: str,
    expected_build_inputs: Mapping[str, str],
) -> dict[str, Any]:
    temporary = Path(environment.get("TMPDIR", ""))
    target = temporary / RUNTIME_INVENTORY_FILE
    try:
        metadata = target.lstat()
    except OSError:
        raise FormalError("RUNTIME_INVENTORY_MISSING") from None
    if (
        target.parent != temporary
        or not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or not 2 <= metadata.st_size <= MAX_RUNTIME_INVENTORY_BYTES
    ):
        raise FormalError("RUNTIME_INVENTORY_FILE_REJECTED")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(target, flags)
        with os.fdopen(descriptor, "rb") as stream:
            raw = stream.read(MAX_RUNTIME_INVENTORY_BYTES + 1)
    except OSError:
        raise FormalError("RUNTIME_INVENTORY_READ_REJECTED") from None
    if len(raw) != metadata.st_size:
        raise FormalError("RUNTIME_INVENTORY_READ_REJECTED")
    try:
        document = json.loads(raw, object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise FormalError("RUNTIME_INVENTORY_JSON_REJECTED") from None
    if not isinstance(document, dict) or set(document) != {
        "schema",
        "static_inventory_sha256",
        "services",
        "bases",
        "build_inputs",
        "docker_binary_sha256",
        "docker_context",
        "runtime_inventory_sha256",
    }:
        raise FormalError("RUNTIME_INVENTORY_SCHEMA_REJECTED")
    if (
        document.get("schema") != RUNTIME_INVENTORY_SCHEMA
        or document.get("static_inventory_sha256") != expected_static_sha256
        or document.get("docker_binary_sha256") != DOCKER_BINARY_SHA256
        or document.get("docker_context") != "LOCAL_UNIX_SOCKET_TRUST_BASE"
    ):
        raise FormalError("RUNTIME_INVENTORY_BINDING_REJECTED")
    services = document.get("services")
    bases = document.get("bases")
    build_inputs = document.get("build_inputs")
    if (
        not isinstance(services, list)
        or not isinstance(bases, list)
        or not isinstance(build_inputs, dict)
        or set(build_inputs) != set(clean_rebuild.BUILD_PROVENANCE_LABELS)
        or any(
            not isinstance(value, str) or not _HEX64.fullmatch(value)
            for value in build_inputs.values()
        )
    ):
        raise FormalError("RUNTIME_INVENTORY_SCHEMA_REJECTED")
    if build_inputs != dict(expected_build_inputs):
        raise FormalError("RUNTIME_BUILD_INPUT_BINDING_REJECTED")
    service_map: dict[str, str] = {}
    for item in services:
        if not isinstance(item, dict) or set(item) != {"service", "image_sha256"}:
            raise FormalError("RUNTIME_INVENTORY_SCHEMA_REJECTED")
        service = item.get("service")
        digest = item.get("image_sha256")
        if (
            not isinstance(service, str)
            or service in service_map
            or service not in RUNTIME_SERVICES
            or not isinstance(digest, str)
            or not _HEX64.fullmatch(digest)
        ):
            raise FormalError("RUNTIME_INVENTORY_SERVICE_REJECTED")
        service_map[service] = digest
    if set(service_map) != set(RUNTIME_SERVICES):
        raise FormalError("RUNTIME_INVENTORY_SERVICE_REJECTED")
    base_map: dict[str, str] = {}
    for item in bases:
        if not isinstance(item, dict) or set(item) != {"bom_ref", "image_sha256"}:
            raise FormalError("RUNTIME_INVENTORY_SCHEMA_REJECTED")
        bom_ref = item.get("bom_ref")
        digest = item.get("image_sha256")
        if (
            not isinstance(bom_ref, str)
            or not bom_ref.startswith("dockerfile:")
            or bom_ref in base_map
            or not isinstance(digest, str)
            or not _HEX64.fullmatch(digest)
        ):
            raise FormalError("RUNTIME_INVENTORY_BASE_REJECTED")
        base_map[bom_ref] = digest
    expected_base_refs = {
        str(component["bom-ref"])
        for component in artifacts._dockerfile_components(checkout)
    }
    if set(base_map) != expected_base_refs:
        raise FormalError("RUNTIME_INVENTORY_BASE_REJECTED")
    supplied = document.get("runtime_inventory_sha256")
    payload = dict(document)
    payload.pop("runtime_inventory_sha256", None)
    calculated = _sha256(_canonical_bytes(payload))
    if (
        supplied != calculated
        or calculated != expected_runtime_sha256
        or not _HEX64.fullmatch(calculated)
    ):
        raise FormalError("RUNTIME_INVENTORY_DIGEST_REJECTED")
    return document


def _runtime_components(
    components: Sequence[Mapping[str, Any]], runtime_inventory: Mapping[str, Any]
) -> list[dict[str, Any]]:
    service_map = {
        str(item["service"]): str(item["image_sha256"])
        for item in runtime_inventory["services"]
    }
    base_map = {
        str(item["bom_ref"]): str(item["image_sha256"])
        for item in runtime_inventory["bases"]
    }
    build_inputs = {
        str(name): str(value)
        for name, value in runtime_inventory["build_inputs"].items()
    }
    local_services = {"keycloak-provisioner", "api", "worker", "dispatcher", "web"}
    enriched: list[dict[str, Any]] = []
    for original in components:
        component = dict(original)
        component["properties"] = [
            dict(value) for value in original.get("properties", [])
        ]
        bom_ref = str(component.get("bom-ref", ""))
        digest: str | None = None
        if bom_ref.startswith("compose:"):
            digest = service_map.get(bom_ref.removeprefix("compose:"))
        elif bom_ref.startswith("dockerfile:"):
            digest = base_map.get(bom_ref)
        if digest is not None:
            pinned = original.get("hashes")
            if isinstance(pinned, list):
                for value in pinned:
                    if isinstance(value, dict) and value.get("alg") == "SHA-256":
                        component["properties"].append(
                            {
                                "name": "oci:pinned-reference-sha256",
                                "value": str(value.get("content", "")),
                            }
                        )
            component["hashes"] = [{"alg": "SHA-256", "content": digest}]
            component["properties"].append(
                {"name": "oci:runtime-image-id-sha256", "value": digest}
            )
        if bom_ref.startswith("compose:") and bom_ref.removeprefix("compose:") in local_services:
            for name in sorted(build_inputs):
                component["properties"].append(
                    {
                        "name": "oci:build-input:" + name.replace("_", "-"),
                        "value": build_inputs[name],
                    }
                )
        enriched.append(component)
    return enriched


def _formal_payload(
    evidence: dict[str, Any],
    inventory_sha256: str,
    runtime_inventory: Mapping[str, Any],
    source: SourceSnapshot,
    config_sha256: str,
    clean_evidence: Mapping[str, Any] | None,
    blockers: Sequence[str],
    checkout: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    payload, components, accepted = artifacts._acceptance_payload(
        evidence, checkout, formal_orchestrator_executed=True
    )
    combined = sorted(set(payload.get("blockers", [])) | set(blockers))
    accepted = bool(accepted and not combined)
    payload["accepted"] = accepted
    payload["conclusion"] = (
        artifacts.READY_CONCLUSION if accepted else artifacts.REJECTED_CONCLUSION
    )
    payload["blockers"] = combined
    payload["formal"] = {
        "schema": FORMAL_SCHEMA,
        "command_registry_sha256": command_registry_sha256(),
        "source_sha256": source.sha256,
        "source_file_count": source.file_count,
        "config_sha256": config_sha256,
        "runtime_inventory_sha256": runtime_inventory["runtime_inventory_sha256"],
        "build_inputs": dict(runtime_inventory.get("build_inputs", {})),
        "clean_rebuild": (
            dict(clean_evidence)
            if clean_evidence is not None
            else {
                "schema": clean_rebuild.CLEAN_EVIDENCE_SCHEMA,
                "validated": False,
            }
        ),
    }
    return payload, _runtime_components(components, runtime_inventory), accepted


def _artifact_contents(
    payload: Mapping[str, Any],
    components: Sequence[Mapping[str, Any]],
    accepted: bool,
) -> dict[str, bytes]:
    public_components = components if accepted else []
    return {
        "acceptance.json": artifacts._pretty_bytes(payload),
        "status.html": artifacts._status_html(payload),
        "sbom.json": artifacts._pretty_bytes(
            artifacts._sbom(
                [dict(component) for component in public_components],
                str(payload["formal"]["runtime_inventory_sha256"]),
            )
        ),
    }


def _rendered_contents(
    payload: Mapping[str, Any],
    components: Sequence[Mapping[str, Any]],
    accepted: bool,
) -> tuple[tuple[str, bytes], ...]:
    return tuple(sorted(_artifact_contents(payload, components, accepted).items()))


def _candidate_artifact_bytes(
    payload: Mapping[str, Any],
    rendered: Sequence[tuple[str, bytes]],
) -> tuple[bytes, ...]:
    """Render every byte sequence that can become public before staging."""

    contents = dict(rendered)
    if set(contents) != {"acceptance.json", "status.html", "sbom.json"} or any(
        not isinstance(raw, bytes) for raw in contents.values()
    ):
        raise FormalError("CANDIDATE_CONTENTS_REJECTED")
    artifact_hashes = {
        name: _sha256(raw) for name, raw in sorted(contents.items())
    }
    manifest = {"schema": "f1.1.1-immutable-batch-v1", "files": artifact_hashes}
    manifest_raw = artifacts._pretty_bytes(manifest)
    contents["manifest.json"] = manifest_raw
    batch_id = _sha256(manifest_raw)
    all_hashes = {name: _sha256(raw) for name, raw in sorted(contents.items())}
    current_raw = artifacts._pretty_bytes(
        {
            "schema": "f1.1.1-current-batch-v1",
            "batch_id": batch_id,
            "conclusion": payload["conclusion"],
            "files": all_hashes,
        }
    )
    return tuple(contents[name] for name in sorted(contents)) + (current_raw,)


def _scan_candidate_artifacts(
    host_secrets: Path,
    payload: Mapping[str, Any],
    rendered: Sequence[tuple[str, bytes]],
) -> None:
    """Prove the shared canary scanner and reject any candidate byte hit."""

    try:
        canaries = log_canary._read_canaries(host_secrets / "leak_canaries")
        scanner = log_canary.CanaryScanner(canaries)
        if scanner.hits(canaries[0]) != 1:
            raise FormalError("CANDIDATE_CANARY_POSITIVE_CONTROL_RED")
        if any(
            scanner.hits(raw)
            for raw in _candidate_artifact_bytes(payload, rendered)
        ):
            raise FormalError("CANDIDATE_ARTIFACT_CANARY_RED")
    except FormalError:
        raise
    except (OSError, ValueError, TypeError, log_canary.CanaryError):
        raise FormalError("CANDIDATE_CANARY_SCAN_RED") from None


def _publish(candidate: FormalCandidate) -> FormalResult:
    output_dir = DEFAULT_OUTPUT
    payload = candidate.payload
    accepted = candidate.accepted
    contents = dict(candidate.contents)
    if set(contents) != {"acceptance.json", "status.html", "sbom.json"} or any(
        not isinstance(raw, bytes) for raw in contents.values()
    ):
        raise FormalError("CANDIDATE_CONTENTS_REJECTED")
    if not accepted and any(b"READY" in raw for raw in contents.values()):
        raise FormalError("REJECTED_ARTIFACT_SUCCESS_TOKEN")
    artifact_hashes = {
        name: _sha256(raw) for name, raw in sorted(contents.items())
    }
    manifest = {"schema": "f1.1.1-immutable-batch-v1", "files": artifact_hashes}
    manifest_bytes = artifacts._pretty_bytes(manifest)
    contents["manifest.json"] = manifest_bytes
    batch_id = _sha256(manifest_bytes)
    all_hashes = {name: _sha256(raw) for name, raw in sorted(contents.items())}

    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_dir.chmod(0o700)
    staging_root = output_dir / ".staging"
    batches_root = output_dir / "batches"
    staging_root.mkdir(mode=0o700, exist_ok=True)
    batches_root.mkdir(mode=0o700, exist_ok=True)
    staging_root.chmod(0o700)
    batches_root.chmod(0o700)
    batch = batches_root / batch_id
    if batch.exists():
        artifacts._verify_existing_batch(batch, all_hashes)
    else:
        stage = Path(tempfile.mkdtemp(prefix="formal-", dir=staging_root))
        stage.chmod(0o700)
        try:
            for name, raw in contents.items():
                artifacts._write_private(stage / name, raw)
            try:
                os.replace(stage, batch)
                batch.chmod(0o700)
            except OSError:
                if not batch.exists():
                    raise
                artifacts._verify_existing_batch(batch, all_hashes)
        finally:
            if stage.exists():
                for child in stage.iterdir():
                    if child.is_file() and not child.is_symlink():
                        child.unlink()
                stage.rmdir()
    current_payload = {
        "schema": "f1.1.1-current-batch-v1",
        "batch_id": batch_id,
        "conclusion": payload["conclusion"],
        "files": all_hashes,
    }
    current_raw = artifacts._pretty_bytes(current_payload)
    if not accepted and b"READY" in current_raw:
        raise FormalError("REJECTED_CURRENT_SUCCESS_TOKEN")
    current = artifacts._atomic_current(output_dir, current_raw)
    artifacts._remove_legacy_top_level(output_dir)
    return FormalResult(
        0 if accepted else 2,
        batch_id,
        current,
        str(payload["conclusion"]),
    )


def _validate_formal_checkout(
    config: FormalConfig, environment: Mapping[str, str]
) -> None:
    try:
        clean_rebuild.validate_delivery_checkout(
            config.checkout,
            clean_rebuild._base_environment(environment),
            expected_source_sha256=config.source_sha256,
            expected_identity=config.checkout_identity,
            expected_python_identity=config.python_bridge_identity,
        )
    except Exception:
        raise FormalError("FORMAL_CHECKOUT_DRIFT") from None


def _evaluate_formal(config: FormalConfig) -> FormalCandidate:
    """Evaluate a prepared stack without creating or updating public state."""

    blockers: list[str] = []
    records: dict[str, dict[str, Any]] = {}
    try:
        source_before = _source_snapshot()
        if source_before != SourceSnapshot(
            config.source_sha256, config.source_file_count
        ):
            raise FormalError("PREPARED_SOURCE_MISMATCH")
        _validate_formal_checkout(config, {})
        inventory_sha256 = artifacts.inventory_digest(config.checkout)
        expected_build_inputs = clean_rebuild.build_provenance(
            config.checkout, source_before.sha256
        )
    except (FormalError, OSError, ValueError, TypeError, json.JSONDecodeError):
        raise FormalError("FORMAL_BASELINE_REJECTED") from None

    with tempfile.TemporaryDirectory(
        prefix=config.project + "-formal-home-", dir="/private/tmp"
    ) as runtime_home_raw:
        runtime_home = Path(runtime_home_raw)
        runtime_home.chmod(0o700)
        with _host_secret_bundle(config) as host_secrets:
            environment = _environment(config, runtime_home)
            environment["F1_SECRETS_DIR"] = str(host_secrets)
            environment["F111_REVERSE_SECRETS_DIR"] = str(host_secrets)
            _validate_formal_checkout(config, environment)
            halted = False
            for gate in GATE_SEQUENCE:
                results: list[tuple[str, ProcessResult]] = []
                specs = [item for item in COMMAND_REGISTRY if item.gate == gate]
                if halted:
                    results.append((gate, ProcessResult(127, b"FORMAL_HALTED\n")))
                elif not specs:
                    results.append(
                        (gate, ProcessResult(127, b"FORMAL_COMMAND_MISSING\n"))
                    )
                    blockers.append(f"COMMAND_MISSING_{gate.upper()}")
                else:
                    isolation_blocker = (
                        _full_repository_isolation_blocker(config, environment)
                        if gate == "full_repository_tests"
                        else None
                    )
                    if isolation_blocker is not None:
                        results.append(
                            (
                                "full_repository_tests",
                                ProcessResult(
                                    126,
                                    (isolation_blocker + "\n").encode("ascii"),
                                ),
                            )
                        )
                        blockers.append(isolation_blocker)
                    for spec in (() if isolation_blocker is not None else specs):
                        if not _command_available(
                            spec,
                            config.checkout,
                            config.python_bridge_identity,
                        ):
                            results.append(
                                (
                                    spec.name,
                                    ProcessResult(127, b"FORMAL_COMMAND_MISSING\n"),
                                )
                            )
                            blockers.append(f"COMMAND_MISSING_{spec.name.upper()}")
                            continue
                        result = _run_process(
                            spec,
                            environment,
                            config.timeout_seconds,
                            config.checkout,
                            config.python_bridge_identity,
                        )
                        results.append((spec.name, result))
                        if gate == "full_repository_tests":
                            post_isolation_blocker = (
                                _full_repository_isolation_blocker(
                                    config, environment
                                )
                            )
                            if post_isolation_blocker is not None:
                                results.append(
                                    (
                                        "frozen_f0_post_boundary",
                                        ProcessResult(
                                            126,
                                            (
                                                post_isolation_blocker + "\n"
                                            ).encode("ascii"),
                                        ),
                                    )
                                )
                                blockers.append(post_isolation_blocker)
                                halted = True
                                break
                        try:
                            observed = _source_snapshot()
                        except FormalError:
                            observed = SourceSnapshot("INVALID", -1)
                        if observed != source_before:
                            blockers.append("IMPLEMENTATION_SOURCE_CHANGED")
                            halted = True
                            break
                        try:
                            _validate_formal_checkout(config, environment)
                        except FormalError:
                            blockers.append("IMPLEMENTATION_CHECKOUT_CHANGED")
                            halted = True
                            break
                try:
                    gate_source = _source_snapshot()
                except FormalError:
                    gate_source = SourceSnapshot("INVALID", -1)
                if gate_source != source_before:
                    blockers.append("IMPLEMENTATION_SOURCE_CHANGED")
                    halted = True
                try:
                    _validate_formal_checkout(config, environment)
                except FormalError:
                    blockers.append("IMPLEMENTATION_CHECKOUT_CHANGED")
                    halted = True
                record, gate_blockers = _gate_record(
                    gate, results, inventory_sha256, config.checkout
                )
                records[gate] = record
                blockers.extend(gate_blockers)

            try:
                source_after = _source_snapshot()
            except FormalError:
                source_after = SourceSnapshot("INVALID", -1)
            if source_after != source_before:
                blockers.append("IMPLEMENTATION_SOURCE_CHANGED")
            try:
                _validate_formal_checkout(config, environment)
            except FormalError:
                blockers.append("IMPLEMENTATION_CHECKOUT_CHANGED")
            first = records.get("clean_rebuild_1", {}).get("result_sha256")
            second = records.get("clean_rebuild_2", {}).get("result_sha256")
            if (
                not isinstance(first, str)
                or not _HEX64.fullmatch(first)
                or first != second
            ):
                blockers.append("CLEAN_REBUILD_NONDETERMINISTIC")
            if set(records) != set(artifacts.REQUIRED_GATES):
                blockers.append("FORMAL_GATE_SET_INCOMPLETE")

            clean_documents: dict[int, dict[str, Any]] = {}
            for round_number in (1, 2):
                marker = records.get(
                    f"clean_rebuild_{round_number}", {}
                ).get("result_sha256")
                if not isinstance(marker, str) or not _HEX64.fullmatch(marker):
                    blockers.append(f"CLEAN_EVIDENCE_ROUND_{round_number}_RED")
                    continue
                try:
                    clean_documents[round_number] = _load_clean_evidence(
                        environment,
                        round_number=round_number,
                        expected_result_sha256=marker,
                        expected_source_sha256=source_before.sha256,
                    )
                except FormalError as error:
                    blockers.extend(
                        [error.code, f"CLEAN_EVIDENCE_ROUND_{round_number}_RED"]
                    )
            clean_projection: dict[str, Any] | None = None
            if set(clean_documents) == {1, 2}:
                try:
                    clean_projection = _clean_evidence_projection(
                        clean_documents[1], clean_documents[2]
                    )
                except FormalError as error:
                    blockers.append(error.code)
            else:
                blockers.append("CLEAN_EVIDENCE_INCOMPLETE")

            evidence = {"schema": artifacts.EVIDENCE_SCHEMA, "gates": records}
            runtime_marker = str(
                records.get("sbom_reconcile", {}).get(
                    "runtime_inventory_sha256", "INVALID"
                )
            )
            try:
                runtime_inventory = _load_runtime_inventory(
                    environment,
                    checkout=config.checkout,
                    expected_static_sha256=inventory_sha256,
                    expected_runtime_sha256=runtime_marker,
                    expected_build_inputs=expected_build_inputs,
                )
            except FormalError:
                blockers.append("RUNTIME_INVENTORY_EVIDENCE_RED")
                runtime_inventory = {
                    "services": [],
                    "bases": [],
                    "build_inputs": {},
                    "runtime_inventory_sha256": "INVALID",
                }
            payload, components, accepted = _formal_payload(
                evidence,
                inventory_sha256,
                runtime_inventory,
                source_before,
                config.sha256,
                clean_projection,
                blockers,
                config.checkout,
            )
            rendered = _rendered_contents(payload, components, accepted)
            _scan_candidate_artifacts(host_secrets, payload, rendered)
    return FormalCandidate(payload, components, accepted, rendered)


def _failure_blocker(error: Exception, fallback: str) -> str:
    """Reduce an internal failure to one body-free, fixed-format reason code."""

    code = getattr(error, "code", None)
    if (
        isinstance(code, str)
        and re.fullmatch(r"[A-Z][A-Z0-9_]{2,95}", code)
        and "READY" not in code
    ):
        return code
    return fallback


def _fail_closed_candidate(config_sha256: str, blocker: str) -> FormalCandidate:
    """Build a dependency-free rejection capable of revoking stale READY.

    A validated source configuration has started a new formal run.  If stack
    preparation or evaluation then fails before a normal candidate exists,
    leaving an older accepted ``current.json`` authoritative would be a false
    green.  This minimal payload has no runtime components and contains only
    fixed labels, digests and a reason code.
    """

    if not _HEX64.fullmatch(config_sha256):
        config_sha256 = "INVALID"
    if (
        not re.fullmatch(r"[A-Z][A-Z0-9_]{2,95}", blocker)
        or "READY" in blocker
    ):
        blocker = "PRIMARY_STACK_PREPARATION_RED"
    empty_evidence = {"schema": artifacts.EVIDENCE_SCHEMA, "gates": {}}
    payload: dict[str, Any] = {
        "schema": artifacts.ACCEPTANCE_SCHEMA,
        "conclusion": artifacts.REJECTED_CONCLUSION,
        "accepted": False,
        "blockers": [blocker],
        "evidence_sha256": _sha256(_canonical_bytes(empty_evidence)),
        "gates": {},
        "inventory_sha256": "INVALID",
        "production": False,
        "accuracy_evaluated": False,
        "professional_judgment_required": True,
        "arbitrary_upload_closed": True,
        "malware_scan_closed": True,
        "formal": {
            "schema": FORMAL_SCHEMA,
            "command_registry_sha256": command_registry_sha256(),
            "source_sha256": "INVALID",
            "source_file_count": 0,
            "config_sha256": config_sha256,
            "runtime_inventory_sha256": "INVALID",
        },
    }
    return FormalCandidate(payload, [], False, _rendered_contents(payload, [], False))


def run_formal_acceptance(config_path: Path | str) -> FormalResult:
    """Prepare, evaluate, tear down, then and only then publish a verdict."""

    source_config = load_config(config_path)
    candidate: FormalCandidate | None = None
    context: Any = None
    try:
        with tempfile.TemporaryDirectory(
            prefix="anhuan-f111-preparation-", dir="/private/tmp"
        ) as runtime_raw:
            runtime_root = Path(runtime_raw)
            runtime_root.chmod(0o700)
            context = clean_rebuild.prepare_primary_stack(
                _preparation_environment(source_config, runtime_root)
            )
            with context as prepared:
                candidate = _evaluate_formal(
                    _prepared_config(source_config, prepared)
                )
            # The context's own post-exit assertion is part of the authority
            # boundary.  Publication below cannot be reached while it is open.
            context.assert_closed_clean()
    except Exception as error:
        blocker = _failure_blocker(error, "PRIMARY_STACK_PREPARATION_RED")
        rejected = _fail_closed_candidate(source_config.sha256, blocker)
        return _publish(rejected)
    return _publish(candidate)


def _main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description="Run fixed F1.1.1 formal acceptance")
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(list(argv[1:]))
    result = run_formal_acceptance(args.config)
    sys.stdout.write(
        json.dumps(
            {"batch_id": result.batch_id, "conclusion": result.conclusion},
            sort_keys=True,
        )
        + "\n"
    )
    return result.exit_code


__all__ = ("run_formal_acceptance",)


if __name__ == "__main__":
    try:
        raise SystemExit(_main(sys.argv))
    except FormalError as error:
        sys.stderr.write(f"error={error.code}\n")
        raise SystemExit(2) from None
    except Exception:
        sys.stderr.write("error=INTERNAL_FAILURE\n")
        raise SystemExit(2) from None
