#!/usr/bin/env python3
"""Fail-closed plaintext canary verifier for one F1.1.1 scratch stack.

Only a UUIDv4 ``anhuan-f111-repair-*`` Compose project, its owner-only
private bundle, and three explicit loopback endpoints are accepted.  The
verifier is read-only: it scans Compose logs, Jaeger-visible traces, RAGFlow
logs, writable container layers, mounts/tmpfs, and the fixed v0.3 artifact
tree.  It never prints a canary, path, response body, credential, or exception.
"""
from __future__ import annotations

import base64
import json
import os
import re
import selectors
import stat
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DOCKER = Path("/usr/local/bin/docker")
BASE_COMPOSE = ROOT / "infra/f1/docker-compose.yml"
FIXED_OVERRIDE = ROOT / "infra/f1/docker-compose.repair.yml"
PUBLIC_ARTIFACT_ROOT = ROOT / "artifacts/f1-platform-shell/v0.3"
POSITIVE_CONTROL_DIRECTORY = "f111-log-canary-positive-control"
POSITIVE_CONTROL_FILE = "probe"

PROJECT_PREFIX = "anhuan-f111-repair-"
MAX_CANARY_FILE = 262144
MAX_COMMAND_BYTES = 32 * 1024 * 1024
MAX_HTTP_BYTES = 32 * 1024 * 1024
MAX_HOST_FILE = 64 * 1024 * 1024
MAX_HOST_TREE = 512 * 1024 * 1024
MAX_STREAM_BYTES = 4 * 1024 * 1024 * 1024
READ_CHUNK = 1024 * 1024

_UNSAFE_ENV = (
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "COMPOSE_FILE",
    "COMPOSE_PROJECT_NAME",
    "COMPOSE_PROFILES",
    "COMPOSE_ENV_FILES",
)
_REQUIRED_SERVICES = {"api", "jaeger", "otel-collector", "ragflow"}
_COMPOSE_ENV_KEYS = (
    "F1_SECRETS_DIR",
    "F1_PROVIDER_SECRETS_DIR",
    "F1_F0I_KEY_FILE",
    "F1_PG_PORT",
    "F1_PG_DATABASE",
    "F1_KEYCLOAK_ISSUER_URL",
    "F1_WEB_PUBLIC_ORIGIN",
    "F1_API_HOST_PORT",
    "F1_GRAFANA_HOST_PORT",
    "F1_JAEGER_OTLP_GRPC_HOST_PORT",
    "F1_JAEGER_OTLP_HTTP_HOST_PORT",
    "F1_JAEGER_UI_HOST_PORT",
    "F1_KEYCLOAK_HOST_PORT",
    "F1_MINIO_API_HOST_PORT",
    "F1_MINIO_CONSOLE_HOST_PORT",
    "F1_PROMETHEUS_HOST_PORT",
    "F1_RAGFLOW_API_HOST_PORT",
    "F1_RAGFLOW_HTTP_HOST_PORT",
    "F1_REDIS_HOST_PORT",
    "F1_WEB_HOST_PORT",
)


class CanaryError(RuntimeError):
    """Fixed-code verifier failure; its text is never emitted by ``main``."""


@dataclass(frozen=True, slots=True)
class LogConfig:
    project: str
    bundle: Path
    compose_override: Path
    canaries: tuple[bytes, ...]
    api_base: str
    jaeger_base: str
    ragflow_base: str
    runtime_home: Path
    runtime_tmp: Path
    compose_environment: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class DockerScope:
    containers: Mapping[str, str]
    scan_targets: tuple[tuple[str, str], ...]
    ragflow_container: str
    otel_container: str


def _private_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        raise CanaryError("BUNDLE_UNAVAILABLE") from None
    if (
        not path.is_absolute()
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.geteuid()
    ):
        raise CanaryError("BUNDLE_SCOPE_REJECTED")


def _read_canaries(path: Path) -> tuple[bytes, ...]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise CanaryError("CANARY_FILE_UNAVAILABLE") from None
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or metadata.st_size < 2
            or metadata.st_size > MAX_CANARY_FILE
        ):
            raise CanaryError("CANARY_FILE_REJECTED")
        raw = os.read(descriptor, MAX_CANARY_FILE + 1)
    finally:
        os.close(descriptor)
    if len(raw) != metadata.st_size:
        raise CanaryError("CANARY_FILE_REJECTED")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CanaryError("CANARY_JSON_REJECTED") from None
    if not isinstance(value, list) or not 1 <= len(value) <= 64:
        raise CanaryError("CANARY_SET_REJECTED")
    result: list[bytes] = []
    for item in value:
        if not isinstance(item, str):
            raise CanaryError("CANARY_SET_REJECTED")
        encoded = item.encode("utf-8")
        if not 8 <= len(encoded) <= 4096 or b"\x00" in encoded:
            raise CanaryError("CANARY_SET_REJECTED")
        result.append(encoded)
    if len(result) != len(set(result)):
        raise CanaryError("CANARY_SET_REJECTED")
    return tuple(result)


def _port(environment: Mapping[str, str], name: str) -> int:
    raw = environment.get(name, "")
    if not re.fullmatch(r"[0-9]{5}", raw):
        raise CanaryError("SCRATCH_ENDPOINT_REJECTED")
    port = int(raw)
    if not 20000 <= port <= 60999:
        raise CanaryError("SCRATCH_ENDPOINT_REJECTED")
    return port


def load_config(environment: Mapping[str, str]) -> LogConfig:
    """Resolve the fixed environment contract without accepting CLI paths."""

    if any(environment.get(name) for name in _UNSAFE_ENV):
        raise CanaryError("DOCKER_SCOPE_REJECTED")
    project = environment.get("F111_REVERSE_PROJECT", "")
    suffix = project.removeprefix(PROJECT_PREFIX)
    if (
        not project.startswith(PROJECT_PREFIX)
        or not re.fullmatch(r"[0-9a-f]{32}", suffix)
    ):
        raise CanaryError("PROJECT_SCOPE_REJECTED")
    try:
        parsed = uuid.UUID(hex=suffix)
    except ValueError:
        raise CanaryError("PROJECT_SCOPE_REJECTED") from None
    if parsed.version != 4:
        raise CanaryError("PROJECT_SCOPE_REJECTED")
    if environment.get("F111_FORMAL_RUN_ID") != project:
        raise CanaryError("FORMAL_SCOPE_REJECTED")

    runtime_home = Path(environment.get("HOME", ""))
    runtime_tmp = Path(environment.get("TMPDIR", ""))
    try:
        home_metadata = runtime_home.lstat()
        tmp_metadata = runtime_tmp.lstat()
    except OSError:
        raise CanaryError("RUNTIME_HOME_REJECTED") from None
    if (
        runtime_home.parent != Path("/private/tmp")
        or not runtime_home.name.startswith(project + "-formal-home-")
        or not stat.S_ISDIR(home_metadata.st_mode)
        or stat.S_ISLNK(home_metadata.st_mode)
        or stat.S_IMODE(home_metadata.st_mode) != 0o700
        or home_metadata.st_uid != os.geteuid()
        or runtime_tmp.parent != runtime_home
        or runtime_tmp.name != "tmp"
        or not stat.S_ISDIR(tmp_metadata.st_mode)
        or stat.S_ISLNK(tmp_metadata.st_mode)
        or stat.S_IMODE(tmp_metadata.st_mode) != 0o700
        or tmp_metadata.st_uid != os.geteuid()
    ):
        raise CanaryError("RUNTIME_HOME_REJECTED")

    raw_bundle = environment.get("F111_REVERSE_SECRETS_DIR", "")
    bundle = Path(raw_bundle)
    if (
        not raw_bundle
        or not bundle.is_absolute()
        or bundle.parent != Path("/private/tmp")
        or not bundle.name.startswith(project + "-bundle-")
    ):
        raise CanaryError("BUNDLE_SCOPE_REJECTED")
    _private_directory(bundle)
    canary_file = bundle / "leak_canaries"
    try:
        file_metadata = canary_file.lstat()
    except OSError:
        raise CanaryError("CANARY_FILE_UNAVAILABLE") from None
    if stat.S_ISLNK(file_metadata.st_mode):
        raise CanaryError("CANARY_FILE_REJECTED")
    canaries = _read_canaries(canary_file)

    raw_override = environment.get("F111_REVERSE_COMPOSE_OVERRIDE", "")
    override = Path(raw_override)
    try:
        override_metadata = override.lstat()
    except OSError:
        raise CanaryError("COMPOSE_OVERRIDE_REJECTED") from None
    if (
        not raw_override
        or not override.is_absolute()
        or override != FIXED_OVERRIDE
        or stat.S_ISLNK(override_metadata.st_mode)
        or not stat.S_ISREG(override_metadata.st_mode)
    ):
        raise CanaryError("COMPOSE_OVERRIDE_REJECTED")

    api_port = _port(environment, "F1_API_HOST_PORT")
    jaeger_port = _port(environment, "F1_JAEGER_UI_HOST_PORT")
    ragflow_port = _port(environment, "F1_RAGFLOW_API_HOST_PORT")
    if len({api_port, jaeger_port, ragflow_port}) != 3:
        raise CanaryError("SCRATCH_ENDPOINT_REJECTED")
    compose_environment = {
        key: str(environment[key])
        for key in _COMPOSE_ENV_KEYS
        if key in environment
    }
    return LogConfig(
        project=project,
        bundle=bundle,
        compose_override=override,
        canaries=canaries,
        api_base=f"http://127.0.0.1:{api_port}",
        jaeger_base=f"http://127.0.0.1:{jaeger_port}",
        ragflow_base=f"http://127.0.0.1:{ragflow_port}",
        runtime_home=runtime_home,
        runtime_tmp=runtime_tmp,
        compose_environment=compose_environment,
    )


def canary_variants(value: bytes) -> tuple[bytes, ...]:
    variants = {value, base64.b64encode(value), base64.urlsafe_b64encode(value)}
    try:
        decoded = value.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        decoded = ""
    if decoded:
        variants.add(json.dumps(decoded, ensure_ascii=True)[1:-1].encode("ascii"))
        variants.add(urllib.parse.quote(decoded, safe="").encode("ascii"))
    return tuple(sorted(item for item in variants if len(item) >= 8))


class CanaryScanner:
    def __init__(self, canaries: Iterable[bytes]) -> None:
        variants = {variant for canary in canaries for variant in canary_variants(canary)}
        if not variants:
            raise CanaryError("CANARY_SET_REJECTED")
        self._needles = tuple(sorted(variants))
        self._maximum = max(len(item) for item in self._needles)

    def hits(self, raw: bytes) -> int:
        return int(any(needle in raw for needle in self._needles))

    def stream_hits(self, stream: Any, maximum: int = MAX_STREAM_BYTES) -> int:
        total = 0
        tail = b""
        hits = 0
        while True:
            chunk = stream.read(READ_CHUNK)
            if not chunk:
                return hits
            total += len(chunk)
            if total > maximum:
                raise CanaryError("SURFACE_SIZE_REJECTED")
            observed = tail + chunk
            if self.hits(observed):
                hits = 1
            tail = observed[-(self._maximum - 1) :] if self._maximum > 1 else b""


def _safe_container_path(raw: Any) -> str:
    if not isinstance(raw, str) or not raw.startswith("/") or len(raw) > 4096:
        raise CanaryError("CONTAINER_PATH_REJECTED")
    pure = PurePosixPath(raw)
    if ".." in pure.parts or str(pure) != raw.rstrip("/") or raw == "/":
        raise CanaryError("CONTAINER_PATH_REJECTED")
    return str(pure)


def scan_host_tree(root: Path, scanner: CanaryScanner) -> int:
    if not root.exists():
        return 0
    try:
        root_metadata = root.lstat()
    except OSError:
        raise CanaryError("ARTIFACT_SURFACE_REJECTED") from None
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
        raise CanaryError("ARTIFACT_SURFACE_REJECTED")
    total = 0
    hits = 0
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        directories.sort()
        files.sort()
        for name in directories:
            path = Path(current) / name
            metadata = path.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise CanaryError("ARTIFACT_PATH_ESCAPE")
        for name in files:
            path = Path(current) / name
            metadata = path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size > MAX_HOST_FILE
            ):
                raise CanaryError("ARTIFACT_SURFACE_REJECTED")
            total += metadata.st_size
            if total > MAX_HOST_TREE:
                raise CanaryError("ARTIFACT_SURFACE_REJECTED")
            try:
                raw = path.read_bytes()
            except OSError:
                raise CanaryError("ARTIFACT_SURFACE_REJECTED") from None
            hits += scanner.hits(raw)
    return hits


class Verifier:
    def __init__(self, config: LogConfig) -> None:
        self.config = config
        self.scanner = CanaryScanner(config.canaries)

    def _docker_environment(self) -> dict[str, str]:
        result = {
            "HOME": str(self.config.runtime_home),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "TMPDIR": str(self.config.runtime_tmp),
            "F111_REVERSE_PROJECT": self.config.project,
        }
        result.update(self.config.compose_environment)
        return result

    def positive_control(self) -> None:
        """Prove the host-tree scanner detects one body-external canary.

        The control lives only under the formal run's private TMPDIR, never in
        the artifact tree.  It is removed before any real surface is scanned.
        A pre-existing target is rejected without deleting caller data.
        """

        root = self.config.runtime_tmp / POSITIVE_CONTROL_DIRECTORY
        probe = root / POSITIVE_CONTROL_FILE
        if root.exists() or root.is_symlink():
            raise CanaryError("POSITIVE_CONTROL_SCOPE_REJECTED")
        owned = False
        descriptor = -1
        failure: CanaryError | None = None
        try:
            root.mkdir(mode=0o700)
            owned = True
            descriptor = os.open(
                probe,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(self.config.canaries[0])
                stream.flush()
                os.fsync(stream.fileno())
            if scan_host_tree(root, self.scanner) != 1:
                raise CanaryError("POSITIVE_CONTROL_RED")
        except CanaryError as error:
            failure = error
        except OSError:
            failure = CanaryError("POSITIVE_CONTROL_RED")
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if owned:
                try:
                    probe.unlink(missing_ok=True)
                    root.rmdir()
                except OSError:
                    failure = CanaryError("POSITIVE_CONTROL_CLEANUP_RED")
        if root.exists() or root.is_symlink():
            raise CanaryError("POSITIVE_CONTROL_CLEANUP_RED")
        if failure is not None:
            raise failure

    def _base_command(self) -> list[str]:
        return [
            str(DOCKER),
            "compose",
            "--env-file",
            "/dev/null",
            "-p",
            self.config.project,
            "-f",
            str(BASE_COMPOSE),
            "-f",
            str(self.config.compose_override),
        ]

    def _run_bytes(self, command: Sequence[str], timeout: int = 120) -> bytes:
        try:
            completed = subprocess.run(
                list(command),
                cwd=str(ROOT),
                env=self._docker_environment(),
                capture_output=True,
                text=False,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise CanaryError("DEPENDENCY_UNREACHABLE") from None
        output = bytes(completed.stdout) + bytes(completed.stderr)
        if completed.returncode != 0 or len(output) > MAX_COMMAND_BYTES:
            raise CanaryError("DEPENDENCY_UNREACHABLE")
        return output

    def _run_json(self, command: Sequence[str], timeout: int = 120) -> Any:
        raw = self._run_bytes(command, timeout)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            try:
                return [json.loads(line) for line in raw.splitlines() if line.strip()]
            except json.JSONDecodeError:
                raise CanaryError("DEPENDENCY_RESPONSE_REJECTED") from None

    def validate_docker_context(self) -> None:
        raw = self._run_bytes(
            [str(DOCKER), "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"],
            30,
        )
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            raise CanaryError("DOCKER_SCOPE_REJECTED") from None
        parsed = urllib.parse.urlsplit(str(value))
        if parsed.scheme != "unix" or parsed.netloc or not Path(parsed.path).is_absolute():
            raise CanaryError("DOCKER_SCOPE_REJECTED")

    @staticmethod
    def _published_port(service: Mapping[str, Any], target: int) -> int:
        matches: list[int] = []
        for raw in service.get("ports") or []:
            if not isinstance(raw, dict):
                raise CanaryError("COMPOSE_SCOPE_REJECTED")
            try:
                observed_target = int(raw.get("target", -1))
                published = int(raw.get("published", -1))
            except (TypeError, ValueError):
                raise CanaryError("COMPOSE_SCOPE_REJECTED") from None
            if observed_target == target:
                if str(raw.get("host_ip", "")) != "127.0.0.1":
                    raise CanaryError("COMPOSE_SCOPE_REJECTED")
                matches.append(published)
        if len(matches) != 1:
            raise CanaryError("COMPOSE_SCOPE_REJECTED")
        return matches[0]

    def validate_compose(self, value: Any) -> tuple[str, ...]:
        if not isinstance(value, dict) or value.get("name") != self.config.project:
            raise CanaryError("COMPOSE_SCOPE_REJECTED")
        services = value.get("services")
        if not isinstance(services, dict) or not _REQUIRED_SERVICES.issubset(services):
            raise CanaryError("COMPOSE_SCOPE_REJECTED")
        expected = (
            ("api", 8001, int(urllib.parse.urlsplit(self.config.api_base).port or 0)),
            ("jaeger", 16686, int(urllib.parse.urlsplit(self.config.jaeger_base).port or 0)),
            ("ragflow", 9380, int(urllib.parse.urlsplit(self.config.ragflow_base).port or 0)),
        )
        for name, target, published in expected:
            service = services.get(name)
            if not isinstance(service, dict) or self._published_port(service, target) != published:
                raise CanaryError("COMPOSE_SCOPE_REJECTED")
        return tuple(sorted(str(name) for name in services))

    def _compose_scope(self) -> tuple[str, ...]:
        value = self._run_json(self._base_command() + ["config", "--format", "json"])
        return self.validate_compose(value)

    def _container_rows(self, services: Sequence[str]) -> list[dict[str, Any]]:
        value = self._run_json(self._base_command() + ["ps", "--all", "--format", "json"])
        rows = value if isinstance(value, list) else [value]
        identities: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise CanaryError("CONTAINER_SCOPE_REJECTED")
            service = str(row.get("Service", ""))
            identity = str(row.get("ID", row.get("Id", "")))
            if service in identities or not re.fullmatch(r"[0-9a-f]{12,64}", identity):
                raise CanaryError("CONTAINER_SCOPE_REJECTED")
            identities[service] = identity
        if set(identities) != set(services):
            raise CanaryError("CONTAINER_SCOPE_REJECTED")
        inspected = self._run_json([str(DOCKER), "inspect", *identities.values()])
        if not isinstance(inspected, list) or len(inspected) != len(services):
            raise CanaryError("CONTAINER_SCOPE_REJECTED")
        return inspected

    def validate_containers(
        self, services: Sequence[str], rows: Sequence[Mapping[str, Any]]
    ) -> DockerScope:
        containers: dict[str, str] = {}
        targets: set[tuple[str, str]] = set()
        ragflow_log_mount = False
        otel_tmpfs = False
        for row in rows:
            identity = str(row.get("Id", ""))
            config = row.get("Config")
            state = row.get("State")
            host = row.get("HostConfig")
            mounts = row.get("Mounts")
            if (
                not re.fullmatch(r"[0-9a-f]{64}", identity)
                or not isinstance(config, dict)
                or not isinstance(state, dict)
                or not isinstance(host, dict)
                or not isinstance(mounts, list)
            ):
                raise CanaryError("CONTAINER_SCOPE_REJECTED")
            labels = config.get("Labels")
            if not isinstance(labels, dict):
                raise CanaryError("CONTAINER_SCOPE_REJECTED")
            project = labels.get("com.docker.compose.project")
            service = labels.get("com.docker.compose.service")
            if project != self.config.project or service not in services or service in containers:
                raise CanaryError("CONTAINER_SCOPE_REJECTED")
            status = state.get("Status")
            exit_code = int(state.get("ExitCode", -1))
            if status != "running" and not (
                service == "keycloak-provisioner" and status == "exited" and exit_code == 0
            ):
                raise CanaryError("CONTAINER_STATE_REJECTED")
            containers[str(service)] = identity
            for mount in mounts:
                if not isinstance(mount, dict):
                    raise CanaryError("MOUNT_SCOPE_REJECTED")
                destination = _safe_container_path(mount.get("Destination"))
                if mount.get("RW") is True:
                    targets.add((identity, destination))
                if service == "ragflow" and destination == "/ragflow/logs" and mount.get("RW") is True:
                    ragflow_log_mount = True
            tmpfs = host.get("Tmpfs") or {}
            if not isinstance(tmpfs, dict):
                raise CanaryError("TMPFS_SCOPE_REJECTED")
            for raw_path in tmpfs:
                path = _safe_container_path(raw_path)
                targets.add((identity, path))
                if service == "otel-collector" and path == "/var/log/otel":
                    otel_tmpfs = True
        if set(containers) != set(services) or not ragflow_log_mount or not otel_tmpfs:
            raise CanaryError("SURFACE_COVERAGE_INCOMPLETE")
        return DockerScope(
            containers=containers,
            scan_targets=tuple(sorted(targets)),
            ragflow_container=containers["ragflow"],
            otel_container=containers["otel-collector"],
        )

    def collect_scope(self) -> DockerScope:
        self.validate_docker_context()
        services = self._compose_scope()
        rows = self._container_rows(services)
        return self.validate_containers(services, rows)

    def _stream_command_hits(self, command: Sequence[str], timeout: int = 900) -> int:
        try:
            process = subprocess.Popen(
                list(command),
                cwd=str(ROOT),
                env=self._docker_environment(),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            raise CanaryError("DEPENDENCY_UNREACHABLE") from None
        if process.stdout is None:
            process.kill()
            raise CanaryError("DEPENDENCY_UNREACHABLE")
        started = time.monotonic()
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)

        class DeadlineStream:
            def read(inner_self, size: int) -> bytes:
                while True:
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        process.kill()
                        raise CanaryError("DEPENDENCY_TIMEOUT")
                    if selector.select(min(1.0, remaining)):
                        try:
                            return os.read(process.stdout.fileno(), size)
                        except OSError:
                            raise CanaryError("DEPENDENCY_UNREACHABLE") from None
                    if process.poll() is not None:
                        try:
                            return os.read(process.stdout.fileno(), size)
                        except OSError:
                            return b""

        try:
            hits = self.scanner.stream_hits(DeadlineStream())
            return_code = process.wait(timeout=max(1, timeout - int(time.monotonic() - started)))
        except (subprocess.TimeoutExpired, CanaryError):
            process.kill()
            process.wait()
            raise CanaryError("DEPENDENCY_UNREACHABLE") from None
        finally:
            selector.close()
            process.stdout.close()
        if return_code != 0:
            raise CanaryError("DEPENDENCY_UNREACHABLE")
        return hits

    def _container_layer_targets(self, identity: str) -> tuple[str, ...]:
        raw = self._run_bytes([str(DOCKER), "diff", identity], 120)
        paths: set[str] = set()
        for line in raw.splitlines():
            match = re.fullmatch(rb"([ACD]) (/.+)", line)
            if match is None:
                raise CanaryError("CONTAINER_DIFF_REJECTED")
            if match.group(1) == b"D":
                continue
            try:
                path = _safe_container_path(match.group(2).decode("utf-8"))
            except UnicodeDecodeError:
                raise CanaryError("CONTAINER_PATH_REJECTED") from None
            paths.add(path)
        if len(paths) > 8192:
            raise CanaryError("CONTAINER_DIFF_REJECTED")
        selected: list[str] = []
        for path in sorted(paths, key=lambda item: (item.count("/"), item)):
            if not any(path == parent or path.startswith(parent + "/") for parent in selected):
                selected.append(path)
        return tuple(selected)

    def scan_container_surfaces(self, scope: DockerScope) -> int:
        hits = 0
        targets = set(scope.scan_targets)
        for identity in scope.containers.values():
            for path in self._container_layer_targets(identity):
                if any(path == mount or path.startswith(mount + "/") for mounted_id, mount in targets if mounted_id == identity):
                    continue
                targets.add((identity, path))
        for identity, path in sorted(targets):
            hits += self._stream_command_hits(
                [str(DOCKER), "cp", f"{identity}:{path}", "-"]
            )
        return hits

    def _http_get(self, url: str) -> bytes:
        try:
            request = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read(MAX_HTTP_BYTES + 1)
                status = int(getattr(response, "status", 200))
        except (OSError, urllib.error.URLError, ValueError):
            raise CanaryError("DEPENDENCY_UNREACHABLE") from None
        if not 200 <= status < 300 or len(raw) > MAX_HTTP_BYTES:
            raise CanaryError("DEPENDENCY_RESPONSE_REJECTED")
        return raw

    def scan_http_surfaces(self) -> int:
        hits = 0
        hits += self.scanner.hits(self._http_get(self.config.api_base + "/healthz"))
        hits += self.scanner.hits(self._http_get(self.config.ragflow_base + "/"))
        raw_services = self._http_get(self.config.jaeger_base + "/api/services")
        hits += self.scanner.hits(raw_services)
        try:
            decoded = json.loads(raw_services)
        except json.JSONDecodeError:
            raise CanaryError("TRACE_RESPONSE_REJECTED") from None
        services = decoded.get("data") if isinstance(decoded, dict) else None
        if (
            not isinstance(services, list)
            or not services
            or len(services) > 128
            or "anhuan-f1-api" not in services
            or any(not isinstance(service, str) or not service for service in services)
        ):
            raise CanaryError("TRACE_COVERAGE_INCOMPLETE")
        for service in sorted(set(services)):
            query = urllib.parse.urlencode({"service": service, "limit": "10000"})
            hits += self.scanner.hits(
                self._http_get(self.config.jaeger_base + "/api/traces?" + query)
            )
        return hits

    def verify(self) -> int:
        scope = self.collect_scope()
        hits = self.scanner.hits(
            self._run_bytes(self._base_command() + ["logs", "--no-color"], 180)
        )
        hits += self.scan_http_surfaces()
        hits += self.scan_container_surfaces(scope)
        hits += scan_host_tree(PUBLIC_ARTIFACT_ROOT, self.scanner)
        return hits


def main() -> int:
    hits = 1
    try:
        config = load_config(os.environ)
        verifier = Verifier(config)
        verifier.positive_control()
        observed = verifier.verify()
        hits = 0 if observed == 0 else 1
    except Exception:
        hits = 1
    print(f"F111_LOG_CANARY_HITS={hits}")
    return 0 if hits == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
