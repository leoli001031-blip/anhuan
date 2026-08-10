"""Fixed-argv Docker supervisor for the F0-H private PP-OCRv6 pipe."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePath
import re
import secrets
import selectors
import subprocess
import threading
import time
from collections.abc import Mapping

from ..f0_isolation import load_frozen_f0_isolation
from .contracts import (
    F0HError,
    canonical_json_bytes,
    validate_private_result,
)
from .runtime_config import RuntimeBundle, load_runtime_bundle, runtime_paths


_EXECUTION_LOCK = threading.Lock()
_IMAGE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class FixedArgvPpocrV6Supervisor:
    """Execute one unit at a time through one immutable sandbox argv."""

    __slots__ = ("_argv", "_bundle")

    def __init__(self, argv: tuple[str, ...], bundle: RuntimeBundle) -> None:
        if not isinstance(bundle, RuntimeBundle) or not _valid_docker_argv(argv, bundle):
            raise F0HError("RUNNER_CONFIGURATION_INVALID")
        self._argv = argv
        self._bundle = bundle

    def execute_envelope(
        self,
        envelope: bytes | bytearray,
        *,
        expected: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        if not _EXECUTION_LOCK.acquire(blocking=False):
            raise F0HError("RUNNER_INVOCATION_DENIED")
        private = bytearray()
        try:
            _validate_envelope_size(envelope, self._bundle)
            private = _run_bounded(
                self._argv, bytearray(envelope), self._bundle, mode="body"
            )
            value = _decode_success(private)
            return validate_private_result(value, self._bundle, expected)
        finally:
            private[:] = b"\0" * len(private)
            private.clear()
            _EXECUTION_LOCK.release()


def docker_argv(
    docker_executable: str, seccomp_profile: str, image_id: str
) -> tuple[str, ...]:
    if (
        not isinstance(docker_executable, str)
        or not os.path.isabs(docker_executable)
        or PurePath(docker_executable).name != "docker"
        or not Path(docker_executable).is_file()
        or not isinstance(seccomp_profile, str)
        or not os.path.isabs(seccomp_profile)
        or os.path.realpath(seccomp_profile) != seccomp_profile
        or os.path.islink(seccomp_profile)
        or not os.path.isfile(seccomp_profile)
        or not isinstance(image_id, str)
        or _IMAGE_RE.fullmatch(image_id) is None
    ):
        raise F0HError("RUNNER_CONFIGURATION_INVALID")
    return _docker_argv_with_name(
        docker_executable,
        seccomp_profile,
        image_id,
        _container_prefix() + secrets.token_hex(16),
    )


def _docker_argv_with_name(
    docker_executable: str,
    seccomp_profile: str,
    image_id: str,
    container_name: str,
) -> tuple[str, ...]:
    if not _valid_container_name(container_name):
        raise F0HError("RUNNER_CONFIGURATION_INVALID")
    arguments = (
        docker_executable,
        "run",
        "--name",
        container_name,
        "--rm",
        "-i",
        "--pull",
        "never",
        "--platform",
        "linux/arm64",
        "--network",
        "none",
        "--ipc",
        "none",
        "--shm-size",
        "64m",
        "--read-only",
        "--user",
        "65532:65532",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--security-opt",
        f"seccomp={seccomp_profile}",
        "--pids-limit",
        "64",
        "--memory",
        "1024m",
        "--memory-swap",
        "1024m",
        "--cpus",
        "1",
        "--ulimit",
        "core=0:0",
        "--ulimit",
        "nofile=64:64",
        "--ulimit",
        "nproc=64:64",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=16m,uid=65532,gid=65532,mode=0700",
        "--tmpfs",
        "/work:rw,nosuid,nodev,noexec,size=256m,uid=65532,gid=65532,mode=0700",
        "--log-driver",
        "none",
    )
    isolation = load_frozen_f0_isolation()
    if isolation is not None:
        project = isolation.docker_project_for("f0h")
        arguments += (
            "--label",
            "com.anhuan.f111.project=" + project,
            "--label",
            "com.anhuan.f111.phase=f0h",
        )
    return arguments + (image_id,)


def _valid_docker_argv(argv: object, bundle: RuntimeBundle) -> bool:
    if not isinstance(argv, tuple) or len(argv) < 5:
        return False
    try:
        if not all(
            isinstance(argument, str)
            and argument
            and "\0" not in argument
            and len(argument) <= 1024
            for argument in argv
        ):
            return False
        if argv[-1] != bundle.container_image_id:
            return False
        name = argv[argv.index("--name") + 1]
        seccomp = next(
            value.removeprefix("seccomp=")
            for value in argv
            if value.startswith("seccomp=")
        )
        return argv == _docker_argv_with_name(argv[0], seccomp, argv[-1], name)
    except (F0HError, StopIteration, ValueError, IndexError):
        return False


def run_envelope_for_test(
    envelope: bytes | bytearray,
    *,
    mode: str = "body",
    root: Path | None = None,
) -> dict[str, object]:
    """Run a synthetic envelope through the real immutable image."""

    if mode not in {"body", "evidence"}:
        raise F0HError("RUNNER_INVOCATION_DENIED")
    bundle = load_runtime_bundle(root)
    argv = docker_argv(*runtime_paths(root), bundle.container_image_id)
    _validate_envelope_size(envelope, bundle)
    raw = bytearray()
    try:
        raw = _run_bounded(argv, bytearray(envelope), bundle, mode=mode)
        value = _decode_success(raw)
        if mode == "body":
            return validate_private_result(value, bundle)
        _validate_evidence_projection(value, bundle)
        return value
    finally:
        raw[:] = b"\0" * len(raw)
        raw.clear()


def _validate_envelope_size(envelope: object, bundle: RuntimeBundle) -> None:
    if (
        not isinstance(envelope, (bytes, bytearray))
        or len(envelope) < 5
        or len(envelope) > bundle.maximum_source_bytes + 4096 + 4
    ):
        raise F0HError("RUNNER_INVOCATION_DENIED")


def _decode_success(raw: bytearray) -> dict[str, object]:
    try:
        value = json.loads(bytes(raw).decode("ascii", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise F0HError("RUNNER_OUTPUT_INVALID") from None
    if not isinstance(value, dict):
        raise F0HError("RUNNER_OUTPUT_INVALID")
    canonical = canonical_json_bytes(value)
    if bytes(raw) not in {canonical, canonical + b"\n"}:
        raise F0HError("RUNNER_OUTPUT_INVALID")
    return value


def _validate_evidence_projection(
    value: dict[str, object], bundle: RuntimeBundle
) -> None:
    if (
        value.get("schema") != "f0e-result-v1"
        or value.get("status") != "SUCCESS"
        or "blocks" in value
        or value.get("raw_text_emitted") is not False
        or value.get("raw_text_persisted") is not False
        or value.get("ocr_executed") is not True
        or value.get("external_calls") != 0
        or value.get("external_processing") != "DENY"
        or value.get("profile_sha256") != bundle.execution_profile_sha256
        or value.get("ocr_engine") != bundle.engine_identity
    ):
        raise F0HError("RUNNER_OUTPUT_INVALID")


def _run_bounded(
    argv: tuple[str, ...],
    envelope: bytearray,
    bundle: RuntimeBundle,
    *,
    mode: str,
) -> bytearray:
    if not _valid_docker_argv(argv, bundle) or mode not in {"body", "evidence"}:
        raise F0HError("RUNNER_CONFIGURATION_INVALID")
    container_name = argv[argv.index("--name") + 1]
    execution_argv = argv if mode == "body" else argv + ("evidence",)
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            execution_argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            cwd="/private/tmp",
            env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC"},
            start_new_session=True,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        _remove_container(argv[0], container_name)
        raise F0HError("RUNNER_FAILED") from None
    try:
        return _communicate_bounded(process, envelope, bundle)
    finally:
        cleanup_failed = False
        envelope[:] = b"\0" * len(envelope)
        envelope.clear()
        if process.poll() is None:
            try:
                _terminate(process)
            except F0HError:
                cleanup_failed = True
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    cleanup_failed = True
        try:
            _remove_container(argv[0], container_name)
        except F0HError:
            cleanup_failed = True
        if cleanup_failed:
            raise F0HError("RUNNER_FAILED")


def _communicate_bounded(
    process: subprocess.Popen[bytes], envelope: bytearray, bundle: RuntimeBundle
) -> bytearray:
    if process.stdin is None or process.stdout is None or process.stderr is None:
        raise F0HError("RUNNER_FAILED")
    selector = selectors.DefaultSelector()
    input_descriptor = process.stdin.fileno()
    output_descriptor = process.stdout.fileno()
    error_descriptor = process.stderr.fileno()
    for descriptor in (input_descriptor, output_descriptor, error_descriptor):
        os.set_blocking(descriptor, False)
    selector.register(input_descriptor, selectors.EVENT_WRITE, "stdin")
    selector.register(output_descriptor, selectors.EVENT_READ, "stdout")
    selector.register(error_descriptor, selectors.EVENT_READ, "stderr")
    output = bytearray()
    input_offset = 0
    stderr_size = 0
    deadline = time.monotonic() + bundle.timeout_seconds
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate(process)
                raise F0HError("RUNNER_TIMEOUT")
            for key, _ in selector.select(min(remaining, 0.1)):
                descriptor = int(key.fd)
                kind = str(key.data)
                if kind == "stdin":
                    try:
                        written = os.write(
                            descriptor,
                            memoryview(envelope)[input_offset : input_offset + 65536],
                        )
                    except BlockingIOError:
                        continue
                    except (BrokenPipeError, OSError):
                        selector.unregister(descriptor)
                        process.stdin.close()
                        continue
                    input_offset += written
                    if input_offset == len(envelope):
                        selector.unregister(descriptor)
                        process.stdin.close()
                    continue
                try:
                    chunk = os.read(descriptor, 65536)
                except BlockingIOError:
                    continue
                except OSError:
                    _terminate(process)
                    raise F0HError("RUNNER_FAILED") from None
                if not chunk:
                    selector.unregister(descriptor)
                    continue
                if kind == "stdout":
                    output.extend(chunk)
                    if len(output) > bundle.maximum_private_output_bytes:
                        _terminate(process)
                        raise F0HError("RUNNER_OUTPUT_LIMIT")
                else:
                    stderr_size += len(chunk)
                    if stderr_size > 0:
                        _terminate(process)
                        raise F0HError("RUNNER_FAILED")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate(process)
            raise F0HError("RUNNER_TIMEOUT")
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _terminate(process)
            raise F0HError("RUNNER_TIMEOUT") from None
        if process.returncode != 0 or input_offset != len(envelope):
            output[:] = b"\0" * len(output)
            output.clear()
            raise F0HError("RUNNER_FAILED")
        return output
    finally:
        selector.close()


def _terminate(process: subprocess.Popen[bytes]) -> None:
    cleanup_failed = False
    try:
        os.killpg(process.pid, 9)
    except OSError:
        try:
            process.kill()
        except OSError:
            cleanup_failed = process.poll() is None
    try:
        process.wait(timeout=1)
    except OSError:
        cleanup_failed = cleanup_failed or process.poll() is None
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            cleanup_failed = True
    if cleanup_failed or process.poll() is None:
        raise F0HError("RUNNER_FAILED")


def _remove_container(docker_executable: str, container_name: str) -> None:
    if (
        not os.path.isabs(docker_executable)
        or PurePath(docker_executable).name != "docker"
        or not _valid_container_name(container_name)
    ):
        raise F0HError("RUNNER_FAILED")
    environment = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC"}
    for command in (
        (docker_executable, "kill", container_name),
        (docker_executable, "rm", "-f", container_name),
    ):
        try:
            subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                close_fds=True,
                env=environment,
                timeout=10,
                check=False,
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
    try:
        inspected = subprocess.run(
            (docker_executable, "container", "inspect", container_name),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
            env=environment,
            timeout=10,
            check=False,
        )
        listed = subprocess.run(
            (
                docker_executable,
                "ps",
                "-a",
                "--filter",
                f"name=^/{container_name}$",
                "--format",
                "{{.ID}}",
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
            env=environment,
            timeout=10,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        raise F0HError("RUNNER_FAILED") from None
    if inspected.returncode == 0 or listed.returncode != 0 or listed.stdout.strip():
        raise F0HError("RUNNER_FAILED")


def _container_prefix() -> str:
    isolation = load_frozen_f0_isolation()
    if isolation is None:
        return "anhuan-f0h-"
    return isolation.docker_project_for("f0h") + "-"


def _valid_container_name(value: object) -> bool:
    try:
        prefix = _container_prefix()
    except Exception:
        return False
    return isinstance(value, str) and re.fullmatch(
        re.escape(prefix) + r"[0-9a-f]{32}", value
    ) is not None


__all__ = (
    "FixedArgvPpocrV6Supervisor",
    "docker_argv",
    "run_envelope_for_test",
)
