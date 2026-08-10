"""Private-memory OCR body supervisor with exact F0-E evidence replay checks."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import PurePath
import re
import secrets
import struct
import subprocess
import threading
import unicodedata

from ..f0e.contracts import (
    F0EError,
    OcrPageEvidence,
    PageRoute,
    ResourceLimits,
    SandboxProfile,
)
from ..f0e.hashing import canonical_json_bytes
from ..f0e.supervisor import (
    _RESULT_KEYS as _F0E_RESULT_KEYS,
    _communicate_bounded,
    _container_prefix,
    _parse_result as _parse_f0e_result,
    _read_verified_source,
    _request_header,
    _terminate,
    _valid_docker_argv,
    _valid_container_name,
    docker_argv,
)
from ..f0e.vault_adapter import VerifiedSourceFd
from .contracts import F0FError, OcrBlock, OcrBodyResult, ocr_body


_HEADER_LIMIT = 4096
_RESULT_KEYS = _F0E_RESULT_KEYS | {"blocks"}
_BLOCK_KEYS = frozenset({"index", "text", "bbox", "confidence_ppm"})
_EXECUTION_LOCK = threading.Lock()
_MAX_PRIVATE_OUTPUT_BYTES = 8 * 1024 * 1024
_MAX_CANONICAL_BODY_BYTES = 4 * 1024 * 1024


class ControlledBodySupervisor:
    """Execute exactly one OCR route and keep returned body in captured memory."""

    __slots__ = ("_argv", "_base_profile_sha256", "_limits", "_profile")

    def __init__(
        self,
        argv: tuple[str, ...],
        runner_image_id: str,
        runner_profile_sha256: str,
        f0e_profile: SandboxProfile,
        limits: ResourceLimits | None = None,
    ) -> None:
        self._limits = (
            ResourceLimits(maximum_stdout_bytes=_MAX_PRIVATE_OUTPUT_BYTES)
            if limits is None
            else limits
        )
        try:
            self._profile = SandboxProfile(
                renderer_sha256=f0e_profile.renderer_sha256,
                ocr_engine_sha256=f0e_profile.ocr_engine_sha256,
                language_pack_sha256=f0e_profile.language_pack_sha256,
                execution_profile_sha256=runner_profile_sha256,
                container_image_id=runner_image_id,
            )
        except (AttributeError, F0EError):
            raise F0FError("RUNNER_CONFIGURATION_INVALID") from None
        self._base_profile_sha256 = f0e_profile.execution_profile_sha256
        if (
            not isinstance(f0e_profile, SandboxProfile)
            or not isinstance(self._limits, ResourceLimits)
            or self._limits.maximum_stdout_bytes != _MAX_PRIVATE_OUTPUT_BYTES
            or not _valid_docker_argv(argv, self._profile, phase="f0f")
        ):
            raise F0FError("RUNNER_CONFIGURATION_INVALID")
        self._argv = argv

    def execute_page(
        self,
        source: VerifiedSourceFd,
        route: PageRoute,
        expected: OcrPageEvidence,
    ) -> OcrBodyResult:
        if not _EXECUTION_LOCK.acquire(blocking=False):
            raise F0FError("RUNNER_INVOCATION_DENIED")
        try:
            return self._execute_page(source, route, expected)
        finally:
            _EXECUTION_LOCK.release()

    def _execute_page(
        self,
        source: VerifiedSourceFd,
        route: PageRoute,
        expected: OcrPageEvidence,
    ) -> OcrBodyResult:
        if (
            not isinstance(source, VerifiedSourceFd)
            or not isinstance(route, PageRoute)
            or not isinstance(expected, OcrPageEvidence)
            or route.evidence_method != "LOCAL_OCR"
            or route.processing_unit_id != expected.processing_unit_id
            or route.source_unit_id != expected.source_unit_id
            or expected.selected_route != "LOCAL_OCR"
        ):
            raise F0FError("RUNNER_INVOCATION_DENIED")
        source_bytes = bytearray()
        envelope = bytearray()
        output = bytearray()
        try:
            source.reverify()
            source_bytes = _read_verified_source(source)
            header_bytes = canonical_json_bytes(_request_header(source, route))
            if not 1 <= len(header_bytes) <= _HEADER_LIMIT:
                raise F0FError("RUNNER_INVOCATION_DENIED")
            envelope.extend(struct.pack(">I", len(header_bytes)))
            envelope.extend(header_bytes)
            envelope.extend(source_bytes)
            output = _run_body_bounded(self._argv, envelope, self._limits)
            result = _parse_body_result(
                output,
                source,
                route,
                expected,
                self._profile,
                self._base_profile_sha256,
                self._limits,
            )
            source.reverify()
            return result
        except F0FError:
            raise
        except F0EError as error:
            raise F0FError(_map_f0e_code(error.code)) from None
        finally:
            source_bytes[:] = b"\0" * len(source_bytes)
            source_bytes.clear()
            envelope[:] = b"\0" * len(envelope)
            envelope.clear()
            output[:] = b"\0" * len(output)
            output.clear()


def body_docker_argv(
    docker_executable: str, seccomp_profile: str, image_id: str
) -> tuple[str, ...]:
    """The F0-F runner uses the already-audited no-mount F0-E sandbox argv."""

    try:
        return docker_argv(
            docker_executable, seccomp_profile, image_id, phase="f0f"
        )
    except F0EError:
        raise F0FError("RUNNER_CONFIGURATION_INVALID") from None


def _parse_body_result(
    raw: bytearray,
    source: VerifiedSourceFd,
    route: PageRoute,
    expected: OcrPageEvidence,
    profile: SandboxProfile,
    base_profile_sha256: str,
    limits: ResourceLimits,
) -> OcrBodyResult:
    try:
        value = json.loads(bytes(raw).decode("ascii", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise F0FError("RUNNER_OUTPUT_INVALID") from None
    if not isinstance(value, dict) or frozenset(value) != _RESULT_KEYS:
        raise F0FError("RUNNER_OUTPUT_INVALID")
    try:
        canonical = canonical_json_bytes(value)
    except F0EError:
        raise F0FError("RUNNER_OUTPUT_INVALID") from None
    if bytes(raw) not in {canonical, canonical + b"\n"}:
        raise F0FError("RUNNER_OUTPUT_INVALID")
    if value.get("schema") != "f0f-body-result-v1" or value.get(
        "raw_text_emitted"
    ) is not True or value.get("raw_text_persisted") is not False:
        raise F0FError("RUNNER_OUTPUT_INVALID")

    aggregate = dict(value)
    raw_blocks = aggregate.pop("blocks")
    aggregate["schema"] = "f0e-result-v1"
    aggregate["raw_text_emitted"] = False
    try:
        validated = _parse_f0e_result(
            bytearray(canonical_json_bytes(aggregate)),
            source,
            route,
            profile,
            limits,
        )
    except F0EError:
        raise F0FError("RUNNER_OUTPUT_INVALID") from None
    blocks = _blocks(raw_blocks, int(validated["render_width_px"]), int(validated["render_height_px"]))
    recalculated = _summarize_blocks(blocks)
    checks = {
        "ocr_text_sha256": recalculated["text_sha256"],
        "bbox_sha256": recalculated["bbox_sha256"],
        "ocr_block_count": len(blocks),
        "ocr_char_count": recalculated["characters"],
        "ocr_nonblank_char_count": recalculated["nonblank"],
        "confidence_min_ppm": recalculated["confidence_min_ppm"],
        "confidence_mean_ppm": recalculated["confidence_mean_ppm"],
        "bbox_union_px": recalculated["bbox_union_px"],
    }
    if any(validated.get(key) != expected_value for key, expected_value in checks.items()):
        raise F0FError("BODY_EVIDENCE_MISMATCH")
    if (
        expected.render_sha256 != validated["render_sha256"]
        or expected.output_sha256 != validated["ocr_text_sha256"]
        or expected.output_block_count != validated["ocr_block_count"]
        or expected.output_character_count != validated["ocr_char_count"]
        or expected.output_non_blank_characters != validated["ocr_nonblank_char_count"]
        or expected.mean_confidence_ppm != validated["confidence_mean_ppm"]
        or expected.bbox_summary_sha256 != validated["bbox_sha256"]
        or expected.execution_profile_sha256 != base_profile_sha256
    ):
        raise F0FError("BODY_EVIDENCE_MISMATCH")
    body = ocr_body(tuple(block.text for block in blocks))
    if body.byte_count > _MAX_CANONICAL_BODY_BYTES:
        body.wipe()
        raise F0FError("BODY_LIMIT_EXCEEDED")
    return OcrBodyResult(
        body=body,
        blocks=blocks,
        render_sha256=str(validated["render_sha256"]),
        f0e_text_sequence_sha256=str(validated["ocr_text_sha256"]),
        f0e_bbox_sequence_sha256=str(validated["bbox_sha256"]),
        block_count=len(blocks),
        character_count=int(recalculated["characters"]),
        nonblank_character_count=int(recalculated["nonblank"]),
        mean_confidence_ppm=(
            None
            if recalculated["confidence_mean_ppm"] is None
            else int(recalculated["confidence_mean_ppm"])
        ),
        render_width_px=int(validated["render_width_px"]),
        render_height_px=int(validated["render_height_px"]),
    )


def _blocks(value: object, width: int, height: int) -> tuple[OcrBlock, ...]:
    if not isinstance(value, list) or len(value) > 4096:
        raise F0FError("RUNNER_OUTPUT_INVALID")
    result: list[OcrBlock] = []
    for expected_index, item in enumerate(value):
        if not isinstance(item, dict) or frozenset(item) != _BLOCK_KEYS:
            raise F0FError("RUNNER_OUTPUT_INVALID")
        index = item.get("index")
        text = item.get("text")
        confidence = item.get("confidence_ppm")
        raw_bbox = item.get("bbox")
        if (
            isinstance(index, bool)
            or index != expected_index
            or not isinstance(text, str)
            or unicodedata.normalize(
                "NFC", text.replace("\r\n", "\n").replace("\r", "\n")
            )
            != text
            or isinstance(confidence, bool)
            or not isinstance(confidence, int)
            or not 0 <= confidence <= 1_000_000
            or not isinstance(raw_bbox, list)
            or len(raw_bbox) != 4
        ):
            raise F0FError("RUNNER_OUTPUT_INVALID")
        points: list[tuple[int, int]] = []
        for point in raw_bbox:
            if (
                not isinstance(point, list)
                or len(point) != 2
                or any(isinstance(number, bool) or not isinstance(number, int) for number in point)
            ):
                raise F0FError("RUNNER_OUTPUT_INVALID")
            x, y = point
            if not 0 <= x <= width or not 0 <= y <= height:
                raise F0FError("RUNNER_OUTPUT_INVALID")
            points.append((x, y))
        result.append(
            OcrBlock(
                index=index,
                text=text,
                bbox=(points[0], points[1], points[2], points[3]),
                confidence_ppm=confidence,
            )
        )
    return tuple(result)


def _summarize_blocks(blocks: tuple[OcrBlock, ...]) -> dict[str, object]:
    text_digest = hashlib.sha256(
        b"F0E_TEXT_SEQUENCE_V1\0ocr-text-nfc-lf-v1\0"
    )
    box_digest = hashlib.sha256(b"F0E_BOX_SEQUENCE_V1\0")
    characters = 0
    nonblank = 0
    confidences: list[int] = []
    union: list[int] | None = None
    for block in blocks:
        encoded = block.text.encode("utf-8", errors="strict")
        characters += len(block.text)
        nonblank += sum(not character.isspace() for character in block.text)
        if characters > 2_000_000:
            raise F0FError("RUNNER_OUTPUT_INVALID")
        text_digest.update(block.index.to_bytes(4, "big"))
        text_digest.update(len(encoded).to_bytes(8, "big"))
        text_digest.update(encoded)
        box_list = [[x, y] for x, y in block.bbox]
        canonical_box = json.dumps(
            box_list,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
        box_digest.update(block.index.to_bytes(4, "big"))
        box_digest.update(len(canonical_box).to_bytes(4, "big"))
        box_digest.update(canonical_box)
        xs = [point[0] for point in block.bbox]
        ys = [point[1] for point in block.bbox]
        current = [min(xs), min(ys), max(xs), max(ys)]
        if union is None:
            union = current
        else:
            union = [
                min(union[0], current[0]),
                min(union[1], current[1]),
                max(union[2], current[2]),
                max(union[3], current[3]),
            ]
        confidences.append(block.confidence_ppm)
    if confidences and nonblank:
        minimum: int | None = min(confidences)
        mean: int | None = (sum(confidences) + len(confidences) // 2) // len(confidences)
    else:
        minimum = None
        mean = None
    return {
        "text_sha256": text_digest.hexdigest(),
        "bbox_sha256": box_digest.hexdigest(),
        "characters": characters,
        "nonblank": nonblank,
        "confidence_min_ppm": minimum,
        "confidence_mean_ppm": mean,
        "bbox_union_px": union,
    }


def _map_f0e_code(code: str) -> str:
    return {
        "RUNNER_CONFIGURATION_INVALID": "RUNNER_CONFIGURATION_INVALID",
        "RUNNER_INVOCATION_DENIED": "RUNNER_INVOCATION_DENIED",
        "RUNNER_TIMEOUT": "RUNNER_TIMEOUT",
        "RUNNER_OUTPUT_LIMIT": "RUNNER_OUTPUT_LIMIT",
        "RUNNER_OUTPUT_INVALID": "RUNNER_OUTPUT_INVALID",
        "RUNNER_FAILED": "RUNNER_FAILED",
        "SOURCE_OBJECT_CHANGED": "SOURCE_OBJECT_CHANGED",
        "SOURCE_FD_CLOSED": "SOURCE_OBJECT_CHANGED",
    }.get(code, "RUNNER_FAILED")


def _run_body_bounded(
    argv: tuple[str, ...], envelope: bytearray, limits: ResourceLimits
) -> bytearray:
    """Run the F0-F container under a unique name and prove cleanup."""

    container_name = _container_prefix("f0f") + secrets.token_hex(16)
    if not _valid_container_name(container_name, "f0f"):
        raise F0FError("RUNNER_FAILED")
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
            _remove_body_container(argv[0], container_name)
        except F0FError:
            pass
        raise F0FError("RUNNER_FAILED") from None
    try:
        try:
            return _communicate_bounded(process, envelope, limits)
        except F0EError as error:
            raise F0FError(_map_f0e_code(error.code)) from None
    finally:
        if process.poll() is None:
            _terminate(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass
        _remove_body_container(argv[0], container_name)


def _remove_body_container(docker_executable: str, container_name: str) -> None:
    if (
        not os.path.isabs(docker_executable)
        or PurePath(docker_executable).name != "docker"
        or not _valid_container_name(container_name, "f0f")
    ):
        raise F0FError("RUNNER_FAILED")
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
        raise F0FError("RUNNER_FAILED") from None
    if inspected.returncode == 0 or listed.returncode != 0 or listed.stdout.strip():
        raise F0FError("RUNNER_FAILED")


__all__ = ("ControlledBodySupervisor", "body_docker_argv")
