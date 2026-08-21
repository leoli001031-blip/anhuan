#!/usr/bin/env python3
"""Fail-closed supervisor for the three browser-verifier Compose operations.

The caller supplies identities and trusted input paths, never a command tail.
After the fixed Compose command exits, an aggregate-only receipt is atomically
committed inside the probe-bound control directory.  A successfully written
receipt, rather than the supervisor's process exit status, carries the Compose
exit code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


SCHEMA = "anhuan-browser-compose-receipt-v1"
STATE_SCHEMA = "anhuan-engineering-local-v1"
RECEIPT_BASENAME = "browser-compose-receipt.json"
RECEIPT_STAGING_BASENAME = RECEIPT_BASENAME + ".new"
RECEIPT_FIELDS = frozenset(
    {
        "schema",
        "probe",
        "kind",
        "project_id",
        "compose_project",
        "state_sha256",
        "service",
        "exit_code",
    }
)
KINDS = frozenset({"build_b", "swap_b", "restore_a"})
CONTRACT_ERROR_EXIT = 64
INTERNAL_ERROR_EXIT = 70
RECEIPT_ERROR_EXIT = 74
LOCAL_PATH = "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
LOCAL_NO_PROXY = (
    "127.0.0.1,localhost,postgres,keycloak,minio,redis,clamd,api,web"
)

_PROBE_RE = re.compile(r"[0-9a-f]{24}\Z")
_COMPOSE_PROJECT_RE = re.compile(r"anhuan-closeout-[0-9a-f]{12}\Z")
_DATABASE_RE = re.compile(r"anhuan_closeout_[0-9a-f]{24}\Z")
_BASE_COMMAND_TAIL = (
    "up",
    "-d",
    "--no-deps",
    "--force-recreate",
    "--wait",
    "--wait-timeout",
    "180",
    "web",
)


class SupervisorContractError(RuntimeError):
    """A quiet, caller-visible contract rejection."""


class SupervisorReceiptError(RuntimeError):
    """The fixed receipt could not be committed safely."""


def _fail(code: str) -> None:
    raise SupervisorContractError(code)


class _QuietParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise SupervisorContractError("BROWSER_COMPOSE_ARGUMENT_INVALID")

    def exit(self, status: int = 0, message: str | None = None) -> None:
        raise SupervisorContractError("BROWSER_COMPOSE_ARGUMENT_INVALID")


@dataclass(frozen=True)
class _FileSnapshot:
    path: Path
    identity: tuple[int, ...]
    sha256: str


@dataclass(frozen=True)
class _Contract:
    kind: str
    probe: str
    project_id: str
    compose_project: str
    root: Path
    state_file: _FileSnapshot
    compose_file: _FileSnapshot
    env_file: _FileSnapshot
    control_directory: Path
    control_identity: tuple[int, int]
    docker: Path
    state_sha256: str
    image_a: str
    image_b: str
    child_environment: dict[str, str]


def _parser() -> _QuietParser:
    parser = _QuietParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--kind", required=True, choices=sorted(KINDS))
    parser.add_argument("--probe", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--compose-project", required=True)
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--compose-file", required=True)
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--control-directory", required=True)
    parser.add_argument("--docker", required=True)
    return parser


def _resolved_path(raw: str, prefix: str) -> Path:
    if (
        not isinstance(raw, str)
        or not raw
        or any(character in raw for character in ("\x00", "\n", "\r"))
    ):
        _fail(f"{prefix}_PATH_INVALID")
    candidate = Path(raw)
    if not candidate.is_absolute():
        _fail(f"{prefix}_PATH_INVALID")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        _fail(f"{prefix}_PATH_INVALID")
    if candidate != resolved:
        _fail(f"{prefix}_PATH_INVALID")
    return candidate


def _private_directory(path: Path, prefix: str) -> tuple[int, int]:
    try:
        item = path.lstat()
    except OSError:
        _fail(f"{prefix}_DIRECTORY_INVALID")
    if (
        not stat.S_ISDIR(item.st_mode)
        or stat.S_ISLNK(item.st_mode)
        or stat.S_IMODE(item.st_mode) != 0o700
        or item.st_uid != os.geteuid()
    ):
        _fail(f"{prefix}_DIRECTORY_INVALID")
    return item.st_dev, item.st_ino


def _snapshot_file(
    path: Path,
    prefix: str,
    *,
    private: bool,
    maximum: int,
) -> tuple[_FileSnapshot, bytes]:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _fail(f"{prefix}_FILE_INVALID")
    try:
        before = os.fstat(descriptor)
        try:
            observed = path.lstat()
        except OSError:
            _fail(f"{prefix}_FILE_INVALID")
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_ISLNK(observed.st_mode)
            or before.st_dev != observed.st_dev
            or before.st_ino != observed.st_ino
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or before.st_size < 1
            or before.st_size > maximum
            or (private and stat.S_IMODE(before.st_mode) != 0o600)
            or (not private and before.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
        ):
            _fail(f"{prefix}_FILE_INVALID")
        body = bytearray()
        while True:
            chunk = os.read(descriptor, min(65536, maximum + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > maximum:
                _fail(f"{prefix}_FILE_INVALID")
        after = os.fstat(descriptor)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
        )
        if identity != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
            after.st_mode,
            after.st_uid,
            after.st_nlink,
        ):
            _fail(f"{prefix}_FILE_CHANGED")
        raw = bytes(body)
        return _FileSnapshot(path, identity, hashlib.sha256(raw).hexdigest()), raw
    finally:
        os.close(descriptor)


def _unique_json(raw: bytes, prefix: str) -> object:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                _fail(f"{prefix}_DUPLICATE_FIELD")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("ascii"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail(f"{prefix}_INVALID")
    raise AssertionError("unreachable")


def _validate_state(
    raw: bytes, project_id: str, compose_project: str
) -> dict[str, object]:
    state = _unique_json(raw, "BROWSER_COMPOSE_STATE")
    fields = {
        "schema",
        "project_id",
        "compose_project",
        "database",
        "web_port",
        "runtime_image",
        "web_image",
    }
    if not isinstance(state, dict) or set(state) != fields:
        _fail("BROWSER_COMPOSE_STATE_INVALID")
    suffix = compose_project[-12:]
    port = state.get("web_port")
    if (
        state.get("schema") != STATE_SCHEMA
        or state.get("project_id") != project_id
        or state.get("compose_project") != compose_project
        or not _DATABASE_RE.fullmatch(str(state.get("database")))
        or type(port) is not int
        or not 1024 <= port <= 65535
        or state.get("runtime_image") != f"anhuan-closeout-runtime:{suffix}"
        or state.get("web_image") != f"anhuan-closeout-web:{suffix}"
    ):
        _fail("BROWSER_COMPOSE_STATE_INVALID")
    canonical = (
        json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("ascii")
    if raw != canonical:
        _fail("BROWSER_COMPOSE_STATE_NONCANONICAL")
    return state


def _validate_env_file(
    raw: bytes, state: dict[str, object], state_directory: Path
) -> None:
    expected = {
        "LOCAL_DATABASE": state["database"],
        "LOCAL_GID": os.getegid(),
        "LOCAL_PROJECT_ID": state["project_id"],
        "LOCAL_RUNTIME_IMAGE": state["runtime_image"],
        "LOCAL_SECRETS_DIR": str(state_directory / "secrets"),
        "LOCAL_UID": os.geteuid(),
        "LOCAL_WEB_IMAGE": state["web_image"],
        "LOCAL_WEB_ORIGIN": f"http://127.0.0.1:{state['web_port']}",
        "LOCAL_WEB_PORT": state["web_port"],
    }
    canonical = "".join(
        f"{key}={expected[key]}\n" for key in sorted(expected)
    ).encode("utf-8")
    if raw != canonical:
        _fail("BROWSER_COMPOSE_ENV_INVALID")


def _validate_docker(path: Path) -> None:
    try:
        item = path.lstat()
    except OSError:
        _fail("BROWSER_COMPOSE_DOCKER_INVALID")
    if (
        not stat.S_ISREG(item.st_mode)
        or stat.S_ISLNK(item.st_mode)
        or item.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or not os.access(path, os.X_OK)
    ):
        _fail("BROWSER_COMPOSE_DOCKER_INVALID")


def _child_environment(
    environ: Mapping[str, str],
    state_directory: Path,
    kind: str,
    probe: str,
    image_a: str,
    image_b: str,
) -> dict[str, str]:
    expected = {
        "DOCKER_CONFIG": str(state_directory / "home" / ".docker"),
        "DOCKER_HOST": "unix:///var/run/docker.sock",
        "HOME": str(state_directory / "home"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "NO_PROXY": LOCAL_NO_PROXY,
        "PATH": LOCAL_PATH,
        "TMPDIR": str(state_directory / "tmp"),
        "no_proxy": LOCAL_NO_PROXY,
    }
    if any(environ.get(key) != value for key, value in expected.items()):
        _fail("BROWSER_COMPOSE_ENVIRONMENT_INVALID")
    if kind == "build_b":
        overrides = {
            "LOCAL_PWA_UPDATE_PROBE": probe,
            "LOCAL_WEB_IMAGE": image_b,
        }
    elif kind == "swap_b":
        overrides = {"LOCAL_WEB_IMAGE": image_b}
    else:
        overrides = {"LOCAL_WEB_IMAGE": image_a}
    if any(environ.get(key) != value for key, value in overrides.items()):
        _fail("BROWSER_COMPOSE_OVERRIDE_INVALID")
    if kind != "build_b" and "LOCAL_PWA_UPDATE_PROBE" in environ:
        _fail("BROWSER_COMPOSE_OVERRIDE_INVALID")
    return {**expected, **overrides}


def _build_contract(
    argv: Sequence[str] | None, environ: Mapping[str, str]
) -> _Contract:
    arguments = _parser().parse_args(argv)
    if not _PROBE_RE.fullmatch(arguments.probe):
        _fail("BROWSER_COMPOSE_PROBE_INVALID")
    try:
        parsed_project_id = str(uuid.UUID(arguments.project_id))
    except (TypeError, ValueError):
        _fail("BROWSER_COMPOSE_PROJECT_INVALID")
    if (
        parsed_project_id != arguments.project_id
        or not _COMPOSE_PROJECT_RE.fullmatch(arguments.compose_project)
    ):
        _fail("BROWSER_COMPOSE_PROJECT_INVALID")

    state_path = _resolved_path(arguments.state_file, "STATE")
    state_directory = state_path.parent
    if state_path.name != "state.json" or state_directory.name != ".local":
        _fail("BROWSER_COMPOSE_STATE_PATH_INVALID")
    _private_directory(state_directory, "STATE")
    root = state_directory.parent
    compose_path = _resolved_path(arguments.compose_file, "COMPOSE")
    env_path = _resolved_path(arguments.env_file, "ENV")
    control_path = _resolved_path(arguments.control_directory, "CONTROL")
    docker_path = _resolved_path(arguments.docker, "DOCKER")
    if compose_path != root / "infra" / "f1" / "docker-compose.local.yml":
        _fail("BROWSER_COMPOSE_PATH_INVALID")
    if env_path != state_directory / "compose.env":
        _fail("BROWSER_COMPOSE_ENV_PATH_INVALID")
    expected_control = (
        state_directory / "tmp" / f"pwa-update-{arguments.probe}"
    )
    if control_path != expected_control:
        _fail("BROWSER_COMPOSE_CONTROL_PATH_INVALID")

    for directory, prefix in (
        (state_directory / "home", "HOME"),
        (state_directory / "home" / ".docker", "DOCKER_CONFIG"),
        (state_directory / "tmp", "TMP"),
        (state_directory / "secrets", "SECRETS"),
    ):
        _private_directory(directory, prefix)
    control_identity = _private_directory(control_path, "CONTROL")
    state_snapshot, state_raw = _snapshot_file(
        state_path, "STATE", private=True, maximum=16384
    )
    compose_snapshot, _compose_raw = _snapshot_file(
        compose_path, "COMPOSE", private=False, maximum=1024 * 1024
    )
    env_snapshot, env_raw = _snapshot_file(
        env_path, "ENV", private=True, maximum=16384
    )
    state = _validate_state(
        state_raw, arguments.project_id, arguments.compose_project
    )
    _validate_env_file(env_raw, state, state_directory)
    _validate_docker(docker_path)
    image_a = str(state["web_image"])
    image_b = f"{image_a}-pwa-update-{arguments.probe}"
    child_environment = _child_environment(
        environ,
        state_directory,
        arguments.kind,
        arguments.probe,
        image_a,
        image_b,
    )
    return _Contract(
        kind=arguments.kind,
        probe=arguments.probe,
        project_id=arguments.project_id,
        compose_project=arguments.compose_project,
        root=root,
        state_file=state_snapshot,
        compose_file=compose_snapshot,
        env_file=env_snapshot,
        control_directory=control_path,
        control_identity=control_identity,
        docker=docker_path,
        state_sha256=state_snapshot.sha256,
        image_a=image_a,
        image_b=image_b,
        child_environment=child_environment,
    )


def _command(contract: _Contract) -> list[str]:
    prefix = [
        str(contract.docker),
        "compose",
        "--ansi",
        "never",
        "--project-name",
        contract.compose_project,
        "--env-file",
        str(contract.env_file.path),
        "-f",
        str(contract.compose_file.path),
        "--profile",
        "ops",
    ]
    tail = ["build", "web"] if contract.kind == "build_b" else list(
        _BASE_COMMAND_TAIL
    )
    return [*prefix, *tail]


def _open_control_directory(contract: _Contract) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(contract.control_directory, flags)
        observed = os.fstat(descriptor)
    except OSError:
        _fail("BROWSER_COMPOSE_CONTROL_DIRECTORY_INVALID")
    if (observed.st_dev, observed.st_ino) != contract.control_identity:
        os.close(descriptor)
        _fail("BROWSER_COMPOSE_CONTROL_DIRECTORY_CHANGED")
    for name in (RECEIPT_BASENAME, RECEIPT_STAGING_BASENAME):
        try:
            os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError:
            os.close(descriptor)
            _fail("BROWSER_COMPOSE_RECEIPT_INVALID")
        os.close(descriptor)
        _fail("BROWSER_COMPOSE_RECEIPT_PENDING")
    return descriptor


def _normalize_exit_code(returncode: int) -> int:
    if type(returncode) is not int:
        return 125
    if 0 <= returncode <= 255:
        return returncode
    if -127 <= returncode < 0:
        return 128 + abs(returncode)
    return 125


def _verify_unchanged(contract: _Contract, control_descriptor: int) -> None:
    try:
        directory = contract.control_directory.lstat()
        opened = os.fstat(control_descriptor)
    except OSError:
        _fail("BROWSER_COMPOSE_CONTROL_DIRECTORY_CHANGED")
    if (
        (directory.st_dev, directory.st_ino) != contract.control_identity
        or (opened.st_dev, opened.st_ino) != contract.control_identity
    ):
        _fail("BROWSER_COMPOSE_CONTROL_DIRECTORY_CHANGED")
    for expected, prefix, private, maximum in (
        (contract.state_file, "STATE", True, 16384),
        (contract.compose_file, "COMPOSE", False, 1024 * 1024),
        (contract.env_file, "ENV", True, 16384),
    ):
        observed, _raw = _snapshot_file(
            expected.path, prefix, private=private, maximum=maximum
        )
        if observed != expected:
            _fail(f"BROWSER_COMPOSE_{prefix}_CHANGED")


def _write_receipt(
    descriptor: int, contract: _Contract, exit_code: int
) -> None:
    document = {
        "schema": SCHEMA,
        "probe": contract.probe,
        "kind": contract.kind,
        "project_id": contract.project_id,
        "compose_project": contract.compose_project,
        "state_sha256": contract.state_sha256,
        "service": "web",
        "exit_code": exit_code,
    }
    body = (
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("ascii")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        output = os.open(
            RECEIPT_STAGING_BASENAME, flags, 0o600, dir_fd=descriptor
        )
        try:
            os.fchmod(output, 0o600)
            view = memoryview(body)
            while view:
                written = os.write(output, view)
                if written <= 0:
                    raise OSError("short write")
                view = view[written:]
            os.fsync(output)
            item = os.fstat(output)
            if (
                not stat.S_ISREG(item.st_mode)
                or stat.S_IMODE(item.st_mode) != 0o600
                or item.st_nlink != 1
                or item.st_uid != os.geteuid()
                or item.st_size != len(body)
            ):
                raise OSError("receipt mode")
        finally:
            os.close(output)
        os.replace(
            RECEIPT_STAGING_BASENAME,
            RECEIPT_BASENAME,
            src_dir_fd=descriptor,
            dst_dir_fd=descriptor,
        )
        os.fsync(descriptor)
        final = os.stat(
            RECEIPT_BASENAME, dir_fd=descriptor, follow_symlinks=False
        )
        if (
            not stat.S_ISREG(final.st_mode)
            or stat.S_IMODE(final.st_mode) != 0o600
            or final.st_nlink != 1
            or final.st_uid != os.geteuid()
            or final.st_size != len(body)
        ):
            raise OSError("receipt invalid")
    except OSError:
        raise SupervisorReceiptError("BROWSER_COMPOSE_RECEIPT_WRITE_FAILED") from None


def _run(contract: _Contract) -> int:
    control_descriptor = _open_control_directory(contract)
    try:
        try:
            process = subprocess.Popen(
                _command(contract),
                cwd=contract.root,
                env=contract.child_environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            returncode = 127
        else:
            returncode = _normalize_exit_code(process.wait())
        _verify_unchanged(contract, control_descriptor)
        _write_receipt(control_descriptor, contract, returncode)
        return 0
    finally:
        os.close(control_descriptor)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    try:
        contract = _build_contract(
            argv, os.environ if environ is None else environ
        )
        return _run(contract)
    except SupervisorContractError:
        return CONTRACT_ERROR_EXIT
    except SupervisorReceiptError:
        return RECEIPT_ERROR_EXIT
    except Exception:
        return INTERNAL_ERROR_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
