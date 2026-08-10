"""Fixed Docker-argv supervisor for the body-free F0-E runner protocol.

Source bytes are verified and streamed to container stdin in a length-prefixed
envelope.  The runner keeps OCR text in container memory and returns only
hashes, counts, geometry summaries, confidence evidence, and closed governance
labels.  No host path, vault mount, DSN, environment secret, or OCR body is
included in argv, stdin metadata, stdout, or exceptions.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import PurePath
import re
import secrets
import selectors
import struct
import subprocess
import threading
import time
from typing import Protocol, runtime_checkable

from ..f0_isolation import load_frozen_f0_isolation
from .contracts import (
    F0EError,
    OcrPageEvidence,
    PageRoute,
    ResourceLimits,
    SandboxProfile,
    require_non_negative,
    require_sha256,
)
from .hashing import canonical_json_bytes, stable_uuid4
from .vault_adapter import VerifiedSourceFd


_HEADER_LIMIT = 4096
_SOURCE_CHUNK = 1024 * 1024
_RESULT_KEYS = frozenset(
    {
        "accuracy_claimed",
        "bbox_coordinate_space",
        "bbox_sha256",
        "bbox_union_px",
        "benchmark_tier",
        "confidence_mean_ppm",
        "confidence_min_ppm",
        "decision",
        "document_type",
        "expected_total_pages",
        "external_calls",
        "external_processing",
        "fixture_label",
        "gold_status",
        "ocr_block_count",
        "ocr_char_count",
        "ocr_engine",
        "ocr_executed",
        "ocr_nonblank_char_count",
        "ocr_text_sha256",
        "normalization_rule",
        "normalization_rule_sha256",
        "page_no",
        "professional_status",
        "profile_sha256",
        "raw_text_emitted",
        "raw_text_persisted",
        "reason_codes",
        "render_dpi",
        "render_height_px",
        "render_origin",
        "render_pixel_format",
        "render_sha256",
        "render_width_px",
        "renderer",
        "schema",
        "source_sha256",
        "source_unit_id",
        "status",
        "temp_residuals",
    }
)
_EMPTY_DECISION = "MANUAL_REVIEW_REQUIRED"
_CAPTURED_DECISION = "OCR_EVIDENCE_CAPTURED_NOT_VALIDATED"
_EMPTY_REASONS = ["EMPTY_OCR_OUTPUT"]
_CAPTURED_REASONS = ["OCR_OUTPUT_HASHED", "CONFIDENCE_NOT_CALIBRATED"]
_EXECUTION_LOCK = threading.Lock()


@runtime_checkable
class SandboxSupervisor(Protocol):
    def execute_page(
        self, source: VerifiedSourceFd, route: PageRoute
    ) -> OcrPageEvidence: ...


class FixedArgvSandboxSupervisor:
    """Run exactly one page through an immutable Docker command."""

    __slots__ = ("_argv", "_limits", "_profile")

    def __init__(
        self,
        argv: tuple[str, ...],
        profile: SandboxProfile,
        limits: ResourceLimits | None = None,
    ) -> None:
        self._profile = profile
        self._limits = ResourceLimits() if limits is None else limits
        if (
            not isinstance(profile, SandboxProfile)
            or not isinstance(self._limits, ResourceLimits)
            or not _valid_docker_argv(argv, profile)
        ):
            raise F0EError("RUNNER_CONFIGURATION_INVALID")
        self._argv = argv

    def execute_page(
        self, source: VerifiedSourceFd, route: PageRoute
    ) -> OcrPageEvidence:
        if not _EXECUTION_LOCK.acquire(blocking=False):
            raise F0EError("RUNNER_INVOCATION_DENIED")
        try:
            return self._execute_page(source, route)
        finally:
            _EXECUTION_LOCK.release()

    def _execute_page(
        self, source: VerifiedSourceFd, route: PageRoute
    ) -> OcrPageEvidence:
        if not isinstance(source, VerifiedSourceFd) or not isinstance(route, PageRoute):
            raise F0EError("RUNNER_INVOCATION_DENIED")
        if (
            route.evidence_method != "LOCAL_OCR"
            or route.candidate_decision != "FULL_PAGE_OCR_REQUIRED"
            or source.size < 8
            or source.size > self._limits.maximum_source_bytes
            or route.expected_total_pages > self._limits.maximum_pages
        ):
            raise F0EError("RUNNER_INVOCATION_DENIED")
        if route.unit_kind == "IMAGE":
            if route.width_px is None or route.height_px is None:
                raise F0EError("RUNNER_INVOCATION_DENIED")
            if route.width_px * route.height_px > self._limits.maximum_pixels:
                raise F0EError("RUNNER_INVOCATION_DENIED")

        source.reverify()
        source_bytes = _read_verified_source(source)
        envelope = bytearray()
        output = bytearray()
        try:
            header = _request_header(source, route)
            header_bytes = canonical_json_bytes(header)
            if not 1 <= len(header_bytes) <= _HEADER_LIMIT:
                raise F0EError("RUNNER_INVOCATION_DENIED")
            envelope.extend(struct.pack(">I", len(header_bytes)))
            envelope.extend(header_bytes)
            envelope.extend(source_bytes)
            output = _run_bounded(
                self._argv,
                envelope,
                self._limits,
            )
            result = _parse_result(output, source, route, self._profile, self._limits)
            source.reverify()
            return _page_evidence(result, route, self._profile)
        finally:
            source_bytes[:] = b""
            envelope[:] = b""
            output[:] = b""


def docker_argv(
    docker_executable: str,
    seccomp_profile: str,
    image_id: str,
    *,
    phase: str = "f0e",
) -> tuple[str, ...]:
    """Build the single approved Docker argv; no mounts/env/entrypoint exist."""

    if (
        not isinstance(docker_executable, str)
        or not os.path.isabs(docker_executable)
        or PurePath(docker_executable).name != "docker"
        or not isinstance(seccomp_profile, str)
        or not os.path.isabs(seccomp_profile)
        or os.path.realpath(seccomp_profile) != seccomp_profile
        or os.path.islink(seccomp_profile)
        or not os.path.isfile(seccomp_profile)
        or not isinstance(image_id, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None
        or phase not in {"f0e", "f0f"}
    ):
        raise F0EError("RUNNER_CONFIGURATION_INVALID")
    arguments = (
        docker_executable,
        "run",
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
        project = isolation.docker_project_for(phase)
        arguments += (
            "--label",
            "com.anhuan.f111.project=" + project,
            "--label",
            "com.anhuan.f111.phase=" + phase,
        )
    return arguments + (image_id,)


def _valid_docker_argv(
    argv: object, profile: SandboxProfile, *, phase: str = "f0e"
) -> bool:
    if not isinstance(argv, tuple) or len(argv) < 2:
        return False
    try:
        if not all(
            isinstance(argument, str)
            and argument
            and "\x00" not in argument
            and len(argument) <= 1024
            for argument in argv
        ):
            return False
        if not os.path.isabs(argv[0]) or PurePath(argv[0]).name != "docker":
            return False
        if profile.container_image_id is None:
            return False
        image = argv[-1]
        if image != profile.container_image_id:
            return False
        seccomp = next(
            argument.removeprefix("seccomp=")
            for argument in argv
            if argument.startswith("seccomp=")
        )
        return argv == docker_argv(
            argv[0], seccomp, profile.container_image_id, phase=phase
        )
    except (F0EError, StopIteration, ValueError):
        return False


def _request_header(source: VerifiedSourceFd, route: PageRoute) -> dict[str, object]:
    document_type = "PDF" if route.unit_kind == "PAGE" else "JPEG"
    header: dict[str, object] = {
        "schema": "f0e-envelope-v1",
        "document_type": document_type,
        "source_sha256": source.sha256,
        "source_size": source.size,
        "expected_total_pages": route.expected_total_pages,
        "page_no": route.page_no,
        "source_unit_id": route.source_unit_id,
    }
    if document_type == "PDF":
        if route.media_box is None or route.crop_box is None or route.rotation is None:
            raise F0EError("RUNNER_INVOCATION_DENIED")
        header.update(
            {
                "media_box": _box(route.media_box),
                "crop_box": _box(route.crop_box),
                "rotation_degrees": route.rotation,
            }
        )
    else:
        if route.width_px is None or route.height_px is None:
            raise F0EError("RUNNER_INVOCATION_DENIED")
        header.update(
            {
                "image_width_px": route.width_px,
                "image_height_px": route.height_px,
            }
        )
    return header


def _box(values: tuple[str, str, str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, raw in zip(("left", "bottom", "right", "top"), values, strict=True):
        try:
            coordinate = Decimal(raw)
        except (InvalidOperation, ValueError):
            raise F0EError("RUNNER_INVOCATION_DENIED") from None
        if not coordinate.is_finite() or coordinate != coordinate.quantize(
            Decimal("0.001")
        ):
            raise F0EError("RUNNER_INVOCATION_DENIED")
        result[key] = f"{coordinate:.3f}"
    return result


def _read_verified_source(source: VerifiedSourceFd) -> bytearray:
    output = bytearray()
    digest = hashlib.sha256()
    offset = 0
    try:
        while offset < source.size:
            wanted = min(_SOURCE_CHUNK, source.size - offset)
            chunk = os.pread(source.fileno(), wanted, offset)
            if len(chunk) != wanted:
                raise F0EError("SOURCE_OBJECT_CHANGED")
            output.extend(chunk)
            digest.update(chunk)
            offset += len(chunk)
        if os.pread(source.fileno(), 1, source.size):
            raise F0EError("SOURCE_OBJECT_CHANGED")
        if offset != source.size or digest.hexdigest() != source.sha256:
            raise F0EError("SOURCE_OBJECT_CHANGED")
        return output
    except F0EError:
        output[:] = b""
        raise
    except OSError:
        output[:] = b""
        raise F0EError("SOURCE_OBJECT_CHANGED") from None


def _run_bounded(
    argv: tuple[str, ...], envelope: bytearray, limits: ResourceLimits
) -> bytearray:
    container_name = _container_prefix("f0e") + secrets.token_hex(16)
    if not _valid_container_name(container_name, "f0e"):
        raise F0EError("RUNNER_FAILED")
    execution_argv = argv[:2] + ("--name", container_name) + argv[2:]
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
        try:
            _remove_container(argv[0], container_name)
        except F0EError:
            pass
        raise F0EError("RUNNER_FAILED") from None
    try:
        return _communicate_bounded(process, envelope, limits)
    finally:
        if process.poll() is None:
            _terminate(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass
        _remove_container(argv[0], container_name)


def _communicate_bounded(
    process: subprocess.Popen[bytes], envelope: bytearray, limits: ResourceLimits
) -> bytearray:
    if process.stdin is None or process.stdout is None or process.stderr is None:
        raise F0EError("RUNNER_FAILED")
    selector = selectors.DefaultSelector()
    stdin_fd = process.stdin.fileno()
    stdout_fd = process.stdout.fileno()
    stderr_fd = process.stderr.fileno()
    for descriptor in (stdin_fd, stdout_fd, stderr_fd):
        os.set_blocking(descriptor, False)
    selector.register(stdin_fd, selectors.EVENT_WRITE, "stdin")
    selector.register(stdout_fd, selectors.EVENT_READ, "stdout")
    selector.register(stderr_fd, selectors.EVENT_READ, "stderr")
    output = bytearray()
    input_offset = 0
    stderr_size = 0
    deadline = time.monotonic() + limits.timeout_ms / 1000
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate(process)
                raise F0EError("RUNNER_TIMEOUT")
            events = selector.select(min(remaining, 0.1))
            for key, _ in events:
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
                    raise F0EError("RUNNER_FAILED") from None
                if not chunk:
                    selector.unregister(descriptor)
                    continue
                if kind == "stdout":
                    output.extend(chunk)
                    if len(output) > limits.maximum_stdout_bytes:
                        _terminate(process)
                        raise F0EError("RUNNER_OUTPUT_LIMIT")
                else:
                    stderr_size += len(chunk)
                    if stderr_size > limits.maximum_stderr_bytes:
                        _terminate(process)
                        raise F0EError("RUNNER_OUTPUT_LIMIT")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate(process)
            raise F0EError("RUNNER_TIMEOUT")
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _terminate(process)
            raise F0EError("RUNNER_TIMEOUT") from None
        if process.returncode != 0 or input_offset != len(envelope):
            raise F0EError("RUNNER_FAILED")
        return output
    finally:
        selector.close()


def _parse_result(
    raw: bytearray,
    source: VerifiedSourceFd,
    route: PageRoute,
    profile: SandboxProfile,
    limits: ResourceLimits,
) -> dict[str, object]:
    try:
        value = json.loads(bytes(raw).decode("ascii", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise F0EError("RUNNER_OUTPUT_INVALID") from None
    if not isinstance(value, dict) or frozenset(value) != _RESULT_KEYS:
        raise F0EError("RUNNER_OUTPUT_INVALID")
    canonical = canonical_json_bytes(value)
    if bytes(raw) not in {canonical, canonical + b"\n"}:
        raise F0EError("RUNNER_OUTPUT_INVALID")
    expected_type = "PDF" if route.unit_kind == "PAGE" else "JPEG"
    fixed = {
        "schema": "f0e-result-v1",
        "status": "SUCCESS",
        "source_unit_id": route.source_unit_id,
        "document_type": expected_type,
        "source_sha256": source.sha256,
        "page_no": route.page_no,
        "expected_total_pages": route.expected_total_pages,
        "fixture_label": "FIXTURE_ONLY",
        "benchmark_tier": "NONE",
        "accuracy_claimed": False,
        "gold_status": "NOT_EVALUATED",
        "professional_status": "NOT_REVIEWED",
        "external_processing": "DENY",
        "external_calls": 0,
        "raw_text_emitted": False,
        "raw_text_persisted": False,
        "ocr_executed": True,
        "profile_sha256": profile.execution_profile_sha256,
        "normalization_rule": profile.normalization_rule,
        "normalization_rule_sha256": profile.normalization_rule_sha256,
        "render_pixel_format": "BGR24",
        "bbox_coordinate_space": "RENDERED_PIXEL_TOP_LEFT_V1",
        "temp_residuals": 0,
    }
    if any(value.get(key) != expected for key, expected in fixed.items()):
        raise F0EError("RUNNER_OUTPUT_INVALID")
    if expected_type == "PDF":
        expected_renderer = {
            "name": "pypdfium2",
            "version": "5.12.1",
            "pdfium_version": "152.0.7947.0",
        }
        if value.get("render_origin") != "PDFIUM_250_DPI" or value.get(
            "render_dpi"
        ) != profile.render_dpi:
            raise F0EError("RUNNER_OUTPUT_INVALID")
    else:
        expected_renderer = {"name": "opencv-imdecode", "version": "5.0.0.93"}
        if value.get("render_origin") != "JPEG_DECODED_SOURCE_PIXELS" or value.get(
            "render_dpi"
        ) is not None:
            raise F0EError("RUNNER_OUTPUT_INVALID")
    if value.get("renderer") != expected_renderer:
        raise F0EError("RUNNER_OUTPUT_INVALID")
    if value.get("ocr_engine") != {
        "name": "rapidocr-onnxruntime",
        "version": "1.4.4",
        "onnxruntime_version": "1.28.0",
        "model_bundle_sha256": profile.language_pack_sha256,
    }:
        raise F0EError("RUNNER_OUTPUT_INVALID")

    for key in ("render_sha256", "bbox_sha256", "ocr_text_sha256"):
        try:
            require_sha256(value[key])
        except (KeyError, F0EError):
            raise F0EError("RUNNER_OUTPUT_INVALID") from None
    counts: dict[str, int] = {}
    for key in (
        "render_width_px",
        "render_height_px",
        "ocr_char_count",
        "ocr_nonblank_char_count",
        "ocr_block_count",
    ):
        try:
            counts[key] = require_non_negative(value[key])
        except (KeyError, F0EError):
            raise F0EError("RUNNER_OUTPUT_INVALID") from None
    if (
        counts["render_width_px"] == 0
        or counts["render_height_px"] == 0
        or counts["render_width_px"] * counts["render_height_px"]
        > limits.maximum_pixels
        or counts["ocr_nonblank_char_count"] > counts["ocr_char_count"]
    ):
        raise F0EError("RUNNER_OUTPUT_INVALID")
    _validate_bbox(value.get("bbox_union_px"), counts)
    _validate_decision(value, counts)
    return value


def _validate_bbox(value: object, counts: dict[str, int]) -> None:
    if value is None:
        if counts["ocr_block_count"] != 0:
            raise F0EError("RUNNER_OUTPUT_INVALID")
        return
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
    ):
        raise F0EError("RUNNER_OUTPUT_INVALID")
    if counts["ocr_block_count"] == 0:
        raise F0EError("RUNNER_OUTPUT_INVALID")
    left, top, right, bottom = value
    if not (
        0 <= left < right <= counts["render_width_px"]
        and 0 <= top < bottom <= counts["render_height_px"]
    ):
        raise F0EError("RUNNER_OUTPUT_INVALID")


def _validate_decision(value: dict[str, object], counts: dict[str, int]) -> None:
    empty = counts["ocr_nonblank_char_count"] == 0
    if empty:
        if (
            value.get("decision") != _EMPTY_DECISION
            or value.get("reason_codes") != _EMPTY_REASONS
            or value.get("confidence_min_ppm") is not None
            or value.get("confidence_mean_ppm") is not None
        ):
            raise F0EError("RUNNER_OUTPUT_INVALID")
        return
    if (
        value.get("decision") != _CAPTURED_DECISION
        or value.get("reason_codes") != _CAPTURED_REASONS
    ):
        raise F0EError("RUNNER_OUTPUT_INVALID")
    try:
        minimum = require_non_negative(value.get("confidence_min_ppm"))
        mean = require_non_negative(value.get("confidence_mean_ppm"))
    except F0EError:
        raise F0EError("RUNNER_OUTPUT_INVALID") from None
    if minimum > mean or mean > 1_000_000:
        raise F0EError("RUNNER_OUTPUT_INVALID")


def _page_evidence(
    result: dict[str, object], route: PageRoute, profile: SandboxProfile
) -> OcrPageEvidence:
    empty = result["decision"] == _EMPTY_DECISION
    return OcrPageEvidence(
        evidence_id=stable_uuid4(
            "page-evidence",
            route.processing_plan_id,
            route.processing_unit_id,
            profile.execution_profile_sha256,
            result["render_sha256"],
            result["ocr_text_sha256"],
        ),
        processing_unit_id=route.processing_unit_id,
        source_unit_id=route.source_unit_id,
        candidate_decision=route.candidate_decision,
        selected_route="LOCAL_OCR",
        terminal_status=(
            "MANUAL_REVIEW_REQUIRED" if empty else "LOCAL_OCR_EVIDENCE"
        ),
        source_evidence_sha256=route.source_evidence_sha256,
        render_sha256=str(result["render_sha256"]),
        output_sha256=str(result["ocr_text_sha256"]),
        output_block_count=int(result["ocr_block_count"]),
        output_character_count=int(result["ocr_char_count"]),
        output_non_blank_characters=int(result["ocr_nonblank_char_count"]),
        mean_confidence_ppm=(
            None if empty else int(result["confidence_mean_ppm"])
        ),
        bbox_summary_sha256=str(result["bbox_sha256"]),
        reason_code=(
            "LOCAL_OCR_EMPTY_REVIEW_REQUIRED"
            if empty
            else "LOCAL_OCR_CANDIDATE_CAPTURED"
        ),
        execution_profile_sha256=profile.execution_profile_sha256,
    )


def _terminate(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, 9)
    except OSError:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _remove_container(docker_executable: str, container_name: str) -> None:
    if (
        not os.path.isabs(docker_executable)
        or PurePath(docker_executable).name != "docker"
        or not _valid_container_name(container_name, "f0e")
    ):
        raise F0EError("RUNNER_FAILED")
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
    inspect_absent = False
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
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    else:
        inspect_absent = inspected.returncode != 0
    listed_clean = False
    try:
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
        pass
    else:
        listed_clean = listed.returncode == 0 and not listed.stdout.strip()
    if not inspect_absent or not listed_clean:
        raise F0EError("RUNNER_FAILED")


def _container_prefix(phase: str) -> str:
    if phase not in {"f0e", "f0f"}:
        raise F0EError("RUNNER_CONFIGURATION_INVALID")
    isolation = load_frozen_f0_isolation()
    if isolation is None:
        return "anhuan-" + phase + "-"
    return isolation.docker_project_for(phase) + "-"


def _valid_container_name(value: object, phase: str) -> bool:
    try:
        prefix = _container_prefix(phase)
    except F0EError:
        return False
    return isinstance(value, str) and re.fullmatch(
        re.escape(prefix) + r"[0-9a-f]{32}", value
    ) is not None


__all__ = (
    "FixedArgvSandboxSupervisor",
    "SandboxSupervisor",
    "docker_argv",
)
