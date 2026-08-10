#!/usr/bin/env python3
"""Fail-closed runtime image and lock inventory reconciliation.

The formal orchestrator is the only intended caller.  This verifier accepts
no command-line authority and emits a single body-free digest only after the
running random Compose project, pinned image references, Dockerfile bases and
both dependency locks have been reconciled.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import stat
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from infra.f1 import artifacts_v03 as artifacts  # noqa: E402
from tests import f111_clean_rebuild as clean_rebuild  # noqa: E402


DOCKER = Path("/usr/local/bin/docker")
DOCKER_SHA256 = "c9766c884e4f2de2aadf8eba072d4a19f45e7f7535138cd0c8bac143f1c26644"
RUNTIME_EVIDENCE_NAME = "f111-runtime-inventory.json"
RUNTIME_SCHEMA = "f1.1.1-runtime-inventory-v2"
COMPOSE_FILES = (
    ROOT / "infra/f1/docker-compose.yml",
    ROOT / "infra/f1/docker-compose.repair.yml",
)
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
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
_DIGEST_REFERENCE = re.compile(r"^[^\s@]+@sha256:([0-9a-f]{64})$")


class ReconcileError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CommandResult:
    stdout: bytes
    stderr: bytes


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _valid_project(value: str) -> bool:
    prefix = "anhuan-f111-repair-"
    if not isinstance(value, str) or not value.startswith(prefix):
        return False
    suffix = value.removeprefix(prefix)
    if re.fullmatch(r"[0-9a-f]{32}", suffix) is None:
        return False
    try:
        parsed = uuid.UUID(hex=suffix)
    except ValueError:
        return False
    return parsed.version == 4


def _fixed_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        raise ReconcileError("SOURCE_MISSING") from None
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ReconcileError("SOURCE_TYPE_REJECTED")


def _run_command(arguments: Sequence[str], timeout: int) -> CommandResult:
    try:
        executable = DOCKER.resolve(strict=True)
        metadata = executable.stat()
    except OSError:
        raise ReconcileError("COMMAND_UNAVAILABLE") from None
    if not stat.S_ISREG(metadata.st_mode) or not os.access(executable, os.X_OK):
        raise ReconcileError("COMMAND_UNAVAILABLE")
    try:
        completed = subprocess.run(
            [str(executable), *arguments],
            cwd=str(ROOT),
            env=dict(os.environ),
            capture_output=True,
            text=False,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ReconcileError("COMMAND_TIMEOUT") from None
    except OSError:
        raise ReconcileError("COMMAND_UNAVAILABLE") from None
    stdout = bytes(completed.stdout)
    stderr = bytes(completed.stderr)
    if len(stdout) + len(stderr) > MAX_OUTPUT_BYTES:
        raise ReconcileError("OUTPUT_LIMIT")
    if completed.returncode != 0 or stderr.strip():
        raise ReconcileError("COMMAND_FAILED")
    return CommandResult(stdout, stderr)


def _verify_docker_trust_base(timeout: int) -> None:
    try:
        executable = DOCKER.resolve(strict=True)
        metadata = executable.stat()
        digest = _sha256(executable.read_bytes())
    except OSError:
        raise ReconcileError("DOCKER_TRUST_BASE_REJECTED") from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or not os.access(executable, os.X_OK)
        or digest != DOCKER_SHA256
    ):
        raise ReconcileError("DOCKER_TRUST_BASE_REJECTED")
    result = _run_command(
        ["context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"],
        timeout,
    )
    value = _json(result.stdout, "DOCKER_CONTEXT_REJECTED")
    if value != "unix:///var/run/docker.sock":
        raise ReconcileError("DOCKER_CONTEXT_REJECTED")
    socket_link = Path("/var/run/docker.sock")
    try:
        socket_path = socket_link.resolve(strict=True)
        socket_metadata = socket_path.stat()
    except OSError:
        raise ReconcileError("DOCKER_CONTEXT_REJECTED") from None
    if (
        not stat.S_ISSOCK(socket_metadata.st_mode)
        or socket_metadata.st_uid != os.geteuid()
        or stat.S_IMODE(socket_metadata.st_mode) & 0o022
    ):
        raise ReconcileError("DOCKER_CONTEXT_REJECTED")


def _compose_prefix(project: str) -> list[str]:
    return [
        "compose",
        "-p",
        project,
        "-f",
        str(COMPOSE_FILES[0]),
        "-f",
        str(COMPOSE_FILES[1]),
    ]


def _json(raw: bytes, code: str) -> Any:
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ReconcileError(code) from None


def _compose_model(project: str, timeout: int) -> dict[str, Any]:
    result = _run_command(
        [*_compose_prefix(project), "config", "--format", "json"], timeout
    )
    model = _json(result.stdout, "COMPOSE_CONFIG_INVALID")
    if not isinstance(model, dict) or not isinstance(model.get("services"), dict):
        raise ReconcileError("COMPOSE_CONFIG_INVALID")
    services = model["services"]
    if set(services) != set(EXPECTED_SERVICES):
        raise ReconcileError("COMPOSE_SERVICE_SET_MISMATCH")
    return model


def _local_images(project: str) -> dict[str, str]:
    return {
        "keycloak-provisioner": f"anhuan-f111-repair-api:{project}",
        "api": f"anhuan-f111-repair-api:{project}",
        "worker": f"anhuan-f111-repair-worker:{project}",
        "dispatcher": f"anhuan-f111-repair-worker:{project}",
        "web": f"anhuan-f111-repair-web:{project}",
    }


def _service_images(model: Mapping[str, Any], project: str) -> dict[str, str]:
    services = model["services"]
    expected_local = _local_images(project)
    images: dict[str, str] = {}
    for name in sorted(EXPECTED_SERVICES):
        service = services.get(name)
        image = service.get("image") if isinstance(service, dict) else None
        if not isinstance(image, str) or not image or any(ch.isspace() for ch in image):
            raise ReconcileError("SERVICE_IMAGE_MISSING")
        if name in expected_local:
            if image != expected_local[name]:
                raise ReconcileError("LOCAL_IMAGE_TAG_MISMATCH")
        elif _DIGEST_REFERENCE.fullmatch(image) is None:
            raise ReconcileError("UNPINNED_RUNTIME_IMAGE")
        images[name] = image
    return images


def _compose_ps(project: str, timeout: int) -> dict[str, dict[str, Any]]:
    result = _run_command(
        [*_compose_prefix(project), "ps", "--all", "--format", "json"], timeout
    )
    raw = result.stdout.strip()
    if not raw:
        raise ReconcileError("COMPOSE_PS_EMPTY")
    try:
        parsed = json.loads(raw)
        rows = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        rows = []
        for line in raw.splitlines():
            value = _json(line, "COMPOSE_PS_INVALID")
            rows.append(value)
    by_service: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ReconcileError("COMPOSE_PS_INVALID")
        service = row.get("Service")
        if not isinstance(service, str) or service in by_service:
            raise ReconcileError("COMPOSE_PS_INVALID")
        by_service[service] = row
    if set(by_service) != set(EXPECTED_SERVICES):
        raise ReconcileError("RUNTIME_SERVICE_SET_MISMATCH")
    for service, row in by_service.items():
        state = str(row.get("State", "")).lower()
        health = str(row.get("Health", "")).lower()
        container_id = row.get("ID")
        if not isinstance(container_id, str) or not container_id:
            raise ReconcileError("RUNTIME_CONTAINER_MISSING")
        if service == "keycloak-provisioner":
            exit_code = row.get("ExitCode", row.get("ExitCode", 1))
            try:
                exit_value = int(exit_code)
            except (TypeError, ValueError):
                raise ReconcileError("PROVISIONER_STATE_INVALID") from None
            if state not in {"exited", "stopped"} or exit_value != 0:
                raise ReconcileError("PROVISIONER_STATE_INVALID")
        elif state != "running" or health != "healthy":
            raise ReconcileError("RUNTIME_HEALTH_INVALID")
    return by_service


def _inspect_containers(
    project: str, rows: Mapping[str, Mapping[str, Any]], timeout: int
) -> dict[str, str]:
    identifiers = [str(rows[name]["ID"]) for name in sorted(rows)]
    if len(identifiers) != len(set(identifiers)):
        raise ReconcileError("CONTAINER_INSPECT_INVALID")
    result = _run_command(["inspect", *identifiers], timeout)
    inspected = _json(result.stdout, "CONTAINER_INSPECT_INVALID")
    if not isinstance(inspected, list) or len(inspected) != len(identifiers):
        raise ReconcileError("CONTAINER_INSPECT_INVALID")
    images: dict[str, str] = {}
    for item in inspected:
        if not isinstance(item, dict):
            raise ReconcileError("CONTAINER_INSPECT_INVALID")
        config = item.get("Config")
        labels = config.get("Labels") if isinstance(config, dict) else None
        inspected_id = item.get("Id")
        image_id = item.get("Image")
        matching_ids = (
            [value for value in identifiers if inspected_id.startswith(value)]
            if isinstance(inspected_id, str)
            else []
        )
        if (
            not isinstance(labels, dict)
            or not isinstance(image_id, str)
            or len(matching_ids) != 1
        ):
            raise ReconcileError("CONTAINER_INSPECT_INVALID")
        if labels.get("com.docker.compose.project") != project:
            raise ReconcileError("CONTAINER_PROJECT_MISMATCH")
        service = labels.get("com.docker.compose.service")
        if service not in EXPECTED_SERVICES or service in images:
            raise ReconcileError("CONTAINER_SERVICE_MISMATCH")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
            raise ReconcileError("CONTAINER_IMAGE_ID_INVALID")
        images[str(service)] = image_id
    if set(images) != set(EXPECTED_SERVICES):
        raise ReconcileError("CONTAINER_SERVICE_MISMATCH")
    return images


def _inspect_image(reference: str, timeout: int) -> dict[str, Any]:
    result = _run_command(["image", "inspect", reference], timeout)
    values = _json(result.stdout, "IMAGE_INSPECT_INVALID")
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        raise ReconcileError("IMAGE_INSPECT_INVALID")
    return values[0]


def _reconcile_images(
    service_refs: Mapping[str, str],
    container_images: Mapping[str, str],
    project: str,
    timeout: int,
    build_provenance: Mapping[str, str],
) -> dict[str, str]:
    expected_local = _local_images(project)
    cache: dict[str, dict[str, Any]] = {}
    for service, reference in service_refs.items():
        if reference not in cache:
            cache[reference] = _inspect_image(reference, timeout)
        inspected = cache[reference]
        image_id = inspected.get("Id")
        if image_id != container_images[service]:
            raise ReconcileError("RUNTIME_IMAGE_ID_MISMATCH")
        if service in expected_local:
            tags = inspected.get("RepoTags")
            if not isinstance(tags, list) or reference not in tags:
                raise ReconcileError("LOCAL_IMAGE_TAG_UNBOUND")
            config = inspected.get("Config")
            labels = config.get("Labels") if isinstance(config, dict) else None
            try:
                clean_rebuild.validate_build_provenance_labels(
                    labels if isinstance(labels, dict) else {}, build_provenance
                )
            except clean_rebuild.RebuildError:
                raise ReconcileError("LOCAL_IMAGE_PROVENANCE_RED") from None
        else:
            match = _DIGEST_REFERENCE.fullmatch(reference)
            digests = inspected.get("RepoDigests")
            if match is None or not isinstance(digests, list):
                raise ReconcileError("PINNED_IMAGE_UNBOUND")
            expected = "@sha256:" + match.group(1)
            if not any(isinstance(value, str) and value.endswith(expected) for value in digests):
                raise ReconcileError("PINNED_IMAGE_UNBOUND")
    return {
        service: container_images[service].removeprefix("sha256:")
        for service in sorted(container_images)
    }


def _dockerfile_references() -> tuple[tuple[str, str], ...]:
    references: list[tuple[str, str]] = []
    for label, path in (
        ("api", ROOT / "infra/f1/Dockerfile"),
        ("web", ROOT / "infra/f1/web.Dockerfile"),
    ):
        _fixed_file(path)
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^FROM\s+([^\s]+)", line.strip(), re.I)
            if match:
                reference = match.group(1)
                if _DIGEST_REFERENCE.fullmatch(reference) is None:
                    raise ReconcileError("UNPINNED_DOCKERFILE_BASE")
                display = reference.split("@", 1)[0]
                references.append((f"dockerfile:{label}:{display}", reference))
    if len(references) != 3 or len(set(references)) != 3:
        raise ReconcileError("DOCKERFILE_BASE_SET_MISMATCH")
    return tuple(sorted(references))


def _reconcile_bases(timeout: int) -> dict[str, str]:
    result: dict[str, str] = {}
    for bom_ref, reference in _dockerfile_references():
        inspected = _inspect_image(reference, timeout)
        match = _DIGEST_REFERENCE.fullmatch(reference)
        digests = inspected.get("RepoDigests")
        if match is None or not isinstance(digests, list):
            raise ReconcileError("DOCKERFILE_BASE_UNBOUND")
        expected = "@sha256:" + match.group(1)
        if not any(isinstance(value, str) and value.endswith(expected) for value in digests):
            raise ReconcileError("DOCKERFILE_BASE_UNBOUND")
        image_id = inspected.get("Id")
        if not isinstance(image_id, str) or re.fullmatch(
            r"sha256:[0-9a-f]{64}", image_id
        ) is None:
            raise ReconcileError("DOCKERFILE_BASE_UNBOUND")
        result[bom_ref] = image_id.removeprefix("sha256:")
    return dict(sorted(result.items()))


def _static_inventory() -> str:
    for path in (
        ROOT / "requirements/requirements-f1.lock",
        ROOT / "src/web/package-lock.json",
        ROOT / "infra/f1/web.Dockerfile",
    ):
        _fixed_file(path)
    components = artifacts.build_inventory(ROOT)
    compose_refs = {
        str(item.get("bom-ref"))
        for item in components
        if str(item.get("bom-ref", "")).startswith("compose:")
    }
    if compose_refs != {f"compose:{name}" for name in EXPECTED_SERVICES}:
        raise ReconcileError("STATIC_COMPOSE_COVERAGE_MISMATCH")
    if not any(str(item.get("bom-ref", "")).startswith("pkg:pypi/") for item in components):
        raise ReconcileError("PYTHON_LOCK_EMPTY")
    if not any(str(item.get("bom-ref", "")).startswith("pkg:npm/") for item in components):
        raise ReconcileError("NPM_LOCK_EMPTY")
    return artifacts.inventory_digest(ROOT)


def _expected_build_provenance() -> dict[str, str]:
    try:
        environment = clean_rebuild._base_environment(os.environ)
        source = clean_rebuild.capture_source(environment)
        provenance = clean_rebuild.build_provenance(ROOT, source.sha256)
    except clean_rebuild.RebuildError:
        raise ReconcileError("BUILD_PROVENANCE_SOURCE_RED") from None
    return provenance


def _runtime_locations(project: str) -> tuple[Path, Path]:
    home = Path(os.environ.get("HOME", ""))
    temporary = Path(os.environ.get("TMPDIR", ""))
    try:
        home_metadata = home.lstat()
        temporary_metadata = temporary.lstat()
    except OSError:
        raise ReconcileError("RUNTIME_HOME_REJECTED") from None
    if (
        home.parent != Path("/private/tmp")
        or not home.name.startswith(project + "-formal-home-")
        or not stat.S_ISDIR(home_metadata.st_mode)
        or stat.S_ISLNK(home_metadata.st_mode)
        or stat.S_IMODE(home_metadata.st_mode) != 0o700
        or home_metadata.st_uid != os.geteuid()
        or temporary.parent != home
        or temporary.name != "tmp"
        or not stat.S_ISDIR(temporary_metadata.st_mode)
        or stat.S_ISLNK(temporary_metadata.st_mode)
        or stat.S_IMODE(temporary_metadata.st_mode) != 0o700
        or temporary_metadata.st_uid != os.geteuid()
    ):
        raise ReconcileError("RUNTIME_HOME_REJECTED")
    return home, temporary


def _write_runtime_evidence(temporary: Path, document: Mapping[str, Any]) -> None:
    target = temporary / RUNTIME_EVIDENCE_NAME
    raw = _canonical_bytes(document)
    descriptor = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    target.chmod(0o600)


def _runtime_document(
    *,
    static_inventory_sha256: str,
    service_images: Mapping[str, str],
    base_images: Mapping[str, str],
    build_provenance: Mapping[str, str],
) -> dict[str, Any]:
    if set(build_provenance) != set(clean_rebuild.BUILD_PROVENANCE_LABELS) or any(
        re.fullmatch(r"[0-9a-f]{64}", value) is None
        for value in build_provenance.values()
    ):
        raise ReconcileError("BUILD_PROVENANCE_SOURCE_RED")
    payload: dict[str, Any] = {
        "schema": RUNTIME_SCHEMA,
        "static_inventory_sha256": static_inventory_sha256,
        "services": [
            {"service": service, "image_sha256": service_images[service]}
            for service in sorted(service_images)
        ],
        "bases": [
            {"bom_ref": bom_ref, "image_sha256": base_images[bom_ref]}
            for bom_ref in sorted(base_images)
        ],
        "build_inputs": {
            name: build_provenance[name] for name in sorted(build_provenance)
        },
        "docker_binary_sha256": DOCKER_SHA256,
        "docker_context": "LOCAL_UNIX_SOCKET_TRUST_BASE",
    }
    digest = _sha256(_canonical_bytes(payload))
    return {**payload, "runtime_inventory_sha256": digest}


def reconcile() -> str:
    project = os.environ.get("F111_REVERSE_PROJECT", "")
    if not _valid_project(project):
        raise ReconcileError("PROJECT_SCOPE_REJECTED")
    if os.environ.get("F111_FORMAL_RUN_ID") != project:
        raise ReconcileError("FORMAL_SCOPE_REJECTED")
    _home, temporary = _runtime_locations(project)
    try:
        timeout = int(os.environ.get("F111_REVERSE_TIMEOUT_SECONDS", ""), 10)
    except ValueError:
        raise ReconcileError("TIMEOUT_REJECTED") from None
    if not 60 <= timeout <= 900:
        raise ReconcileError("TIMEOUT_REJECTED")
    for path in COMPOSE_FILES:
        _fixed_file(path)
    expected_provenance = _expected_build_provenance()
    _verify_docker_trust_base(timeout)
    model = _compose_model(project, timeout)
    service_refs = _service_images(model, project)
    runtime_rows = _compose_ps(project, timeout)
    container_images = _inspect_containers(project, runtime_rows, timeout)
    service_images = _reconcile_images(
        service_refs, container_images, project, timeout, expected_provenance
    )
    base_images = _reconcile_bases(timeout)
    document = _runtime_document(
        static_inventory_sha256=_static_inventory(),
        service_images=service_images,
        base_images=base_images,
        build_provenance=expected_provenance,
    )
    if _expected_build_provenance() != expected_provenance:
        raise ReconcileError("BUILD_PROVENANCE_SOURCE_DRIFT")
    _write_runtime_evidence(temporary, document)
    return str(document["runtime_inventory_sha256"])


def main() -> int:
    try:
        digest = reconcile()
    except Exception:
        return 2
    print(f"F111_RUNTIME_INVENTORY_SHA256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
