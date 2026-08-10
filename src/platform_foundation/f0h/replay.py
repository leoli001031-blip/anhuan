"""Read-only F0-H replay over the registered fixture identity."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import runpy
import secrets
import subprocess
from collections.abc import Mapping

from .contracts import F0HError, canonical_json_bytes, canonical_sha256
from .fixture_reader import (
    RegisteredSource,
    load_registered_plan,
    open_registered_source,
)
from .runtime_config import load_runtime_bundle, runtime_paths
from .supervisor import (
    FixedArgvPpocrV6Supervisor,
    _remove_container,
    docker_argv,
)


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_OLD_IMAGE_ID = (
    "sha256:7316755e9776033453420b11292ed481b253196dc9db4bbe596a149dcd1a0a64"
)
_OLD_RUNTIME_PATHS = (
    "infra/f0e/component-lock.json",
    "infra/f0e/runtime-lock.json",
    "infra/f0e/requirements.lock",
    "infra/f0e/runner.py",
    "infra/f0f/component-lock.json",
    "infra/f0f/runtime-lock.json",
    "infra/f0f/runner.py",
    "artifacts/f0e-local-ocr/v0.1/acceptance.json",
    "artifacts/f0e-local-ocr/v0.1/status.html",
    "artifacts/f0e-local-ocr/v0.1/sbom.json",
    "artifacts/f0f-controlled-body/v0.1/acceptance.json",
    "artifacts/f0f-controlled-body/v0.1/status.html",
    "artifacts/f0f-controlled-body/v0.1/sbom.json",
)
_EXPECTED = {
    "smoke": {
        "documents": 10,
        "visual_units": 110,
        "native_bypass": 105,
        "ppocrv6_ocr": 5,
        "deferred_documents": 2,
    },
    "full": {
        "documents": 26,
        "visual_units": 249,
        "native_bypass": 225,
        "ppocrv6_ocr": 24,
        "deferred_documents": 2,
    },
}
_FORBIDDEN_SUMMARY_KEYS = frozenset(
    {
        "blocks",
        "body",
        "raw_text",
        "text",
        "filename",
        "path",
        "source_path",
    }
)


def replay_profile(profile: str) -> dict[str, object]:
    if profile not in _EXPECTED:
        raise F0HError("REPLAY_MISMATCH")
    before = _old_runtime_fingerprint()
    plan = load_registered_plan(profile)
    bundle = load_runtime_bundle()
    docker, seccomp = runtime_paths()
    supervisor = FixedArgvPpocrV6Supervisor(
        docker_argv(docker, seccomp, bundle.container_image_id), bundle
    )
    summary = plan.payload.get("summary")
    entries = plan.payload.get("entries")
    if not isinstance(summary, dict) or not isinstance(entries, list):
        raise F0HError("REPLAY_MISMATCH")

    documents = len(entries)
    visual_units = 0
    native_bypass = 0
    ppocrv6_ocr = 0
    deferred_documents = 0
    execution_digest = hashlib.sha256(b"F0H_PPOCRV6_REPLAY_V1\0")
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise F0HError("REPLAY_MISMATCH")
        document_type = entry.get("type")
        if document_type == "DOC":
            if entry.get("parse_status") != "DEFERRED_CONVERSION_REQUIRED":
                raise F0HError("REPLAY_MISMATCH")
            deferred_documents += 1
            execution_digest.update(b"DOC_DEFERRED\0")
            continue
        if document_type not in {"PDF", "JPEG"}:
            execution_digest.update(str(document_type).encode("ascii"))
            execution_digest.update(b"\0NO_VISUAL_UNITS\0")
            continue
        pages = entry.get("pages")
        if not isinstance(pages, list):
            raise F0HError("REPLAY_MISMATCH")
        visual_units += len(pages)
        ocr_pages = [
            page
            for page in pages
            if isinstance(page, dict)
            and page.get("decision") == "FULL_PAGE_OCR_REQUIRED"
        ]
        native_bypass += sum(
            isinstance(page, dict) and page.get("decision") == "NATIVE_CANDIDATE"
            for page in pages
        )
        if len(ocr_pages) + sum(
            isinstance(page, dict) and page.get("decision") == "NATIVE_CANDIDATE"
            for page in pages
        ) != len(pages):
            raise F0HError("REPLAY_MISMATCH")
        if not ocr_pages:
            for page in pages:
                execution_digest.update(str(page["page_id"]).encode("ascii"))
                execution_digest.update(b"\0NATIVE_BYPASS\0")
            continue
        with open_registered_source(plan, index) as source:
            for page in pages:
                if page.get("decision") == "NATIVE_CANDIDATE":
                    execution_digest.update(str(page["page_id"]).encode("ascii"))
                    execution_digest.update(b"\0NATIVE_BYPASS\0")
                    continue
                envelope = _envelope(source, entry, page)
                try:
                    expected = {
                        "document_type": document_type,
                        "expected_total_pages": int(entry.get("page_count", 1)),
                        "page_no": int(page["page_no"]),
                        "source_sha256": source.sha256,
                        "source_unit_id": str(page["page_id"]),
                    }
                    result = supervisor.execute_envelope(envelope, expected=expected)
                finally:
                    envelope[:] = b"\0" * len(envelope)
                    envelope.clear()
                blocks = result.pop("blocks")
                result["schema"] = "f0e-result-v1"
                result["raw_text_emitted"] = False
                execution_digest.update(canonical_json_bytes(result))
                if isinstance(blocks, list):
                    blocks.clear()
                del blocks
                del result
                ppocrv6_ocr += 1

    counts = {
        "documents": documents,
        "visual_units": visual_units,
        "native_bypass": native_bypass,
        "ppocrv6_ocr": ppocrv6_ocr,
        "deferred_documents": deferred_documents,
    }
    if counts != _EXPECTED[profile] or (
        summary.get("documents") != documents
        or summary.get("visual_units") != visual_units
        or summary.get("native_candidates") != native_bypass
        or summary.get("ocr_required") != ppocrv6_ocr
        or summary.get("doc_deferred") != deferred_documents
        or summary.get("errors") != 0
    ):
        raise F0HError("REPLAY_MISMATCH")
    rollback_ready = _legacy_v4_probe()
    after = _old_runtime_fingerprint()
    if not rollback_ready or before != after:
        raise F0HError("REPLAY_MISMATCH")
    result: dict[str, object] = {
        "schema": "f0h-replay-result-v1",
        "status": "LOCAL_PPOCRV6_RUNTIME_READY",
        "accuracy_status": "ACCURACY_NOT_EVALUATED",
        "search_status": "SEARCH_NOT_READY",
        "production_status": "NOT_PRODUCTION",
        "profile": profile,
        **counts,
        "errors": 0,
        "provider": "ppocrv6-small",
        "rapidocr_version": bundle.rapidocr_version,
        "ocr_family": bundle.ocr_family,
        "model_bundle_sha256": bundle.model_bundle_sha256,
        "configuration_sha256": bundle.configuration_sha256,
        "runtime_profile_sha256": bundle.execution_profile_sha256,
        "container_image_id": bundle.container_image_id,
        "registered_plan_sha256": plan.page_plan_sha256,
        "execution_summary_sha256": execution_digest.hexdigest(),
        "legacy_v4_rollback_ready": True,
        "old_runtime_fingerprint_sha256": before,
        "old_runtime_mutations": 0,
        "external_calls": 0,
        "runtime_downloads": 0,
        "raw_text_persisted": False,
        "body_leaks": 0,
    }
    _validate_summary_surface(result)
    return result


def verify_repeat(first: Mapping[str, object], second: Mapping[str, object]) -> None:
    if not isinstance(first, Mapping) or not isinstance(second, Mapping):
        raise F0HError("REPLAY_MISMATCH")
    left = dict(first)
    right = dict(second)
    _validate_summary_surface(left)
    _validate_summary_surface(right)
    if canonical_json_bytes(left) != canonical_json_bytes(right):
        raise F0HError("REPLAY_MISMATCH")


def _validate_summary_surface(value: Mapping[str, object]) -> None:
    if any(key in _FORBIDDEN_SUMMARY_KEYS for key in value):
        raise F0HError("REPLAY_MISMATCH")
    if (
        value.get("status") != "LOCAL_PPOCRV6_RUNTIME_READY"
        or value.get("accuracy_status") != "ACCURACY_NOT_EVALUATED"
        or value.get("search_status") != "SEARCH_NOT_READY"
        or value.get("production_status") != "NOT_PRODUCTION"
        or value.get("external_calls") != 0
        or value.get("runtime_downloads") != 0
        or value.get("raw_text_persisted") is not False
    ):
        raise F0HError("REPLAY_MISMATCH")
    encoded = canonical_json_bytes(dict(value))
    forbidden = (b"/Users/", b"environment-demo", b"@", b"http://", b"https://")
    if any(token in encoded for token in forbidden):
        raise F0HError("REPLAY_MISMATCH")


def _envelope(
    source: RegisteredSource, entry: Mapping[str, object], page: Mapping[str, object]
) -> bytearray:
    document_type = str(entry.get("type"))
    header: dict[str, object] = {
        "schema": "f0e-envelope-v1",
        "document_type": document_type,
        "source_sha256": source.sha256,
        "source_size": source.size,
        "expected_total_pages": int(entry.get("page_count", 1)),
        "page_no": int(page["page_no"]),
        "source_unit_id": str(page["page_id"]),
    }
    if document_type == "PDF":
        header.update(
            {
                "media_box": page["media_box"],
                "crop_box": page["crop_box"],
                "rotation_degrees": int(page["rotation"]),
            }
        )
    elif document_type == "JPEG":
        header.update(
            {
                "image_width_px": int(page["width_px"]),
                "image_height_px": int(page["height_px"]),
            }
        )
    else:
        raise F0HError("REPLAY_MISMATCH")
    header_bytes = canonical_json_bytes(header)
    if not 1 <= len(header_bytes) <= 4096:
        raise F0HError("REPLAY_MISMATCH")
    source_bytes = source.read_verified()
    output = bytearray()
    try:
        output.extend(len(header_bytes).to_bytes(4, "big"))
        output.extend(header_bytes)
        output.extend(source_bytes)
        return output
    finally:
        source_bytes[:] = b"\0" * len(source_bytes)
        source_bytes.clear()


def _old_runtime_fingerprint() -> str:
    material: dict[str, str] = {}
    for relative in _OLD_RUNTIME_PATHS:
        path = _PROJECT_ROOT / relative
        try:
            data = path.read_bytes()
        except OSError:
            raise F0HError("REPLAY_MISMATCH") from None
        material[relative] = hashlib.sha256(data).hexdigest()
    return canonical_sha256(material)


def _legacy_v4_probe() -> bool:
    try:
        probe = runpy.run_path(str(_PROJECT_ROOT / "infra/f0h/synthetic_probe.py"))
        envelope = bytearray(probe["_envelope"]("JPEG_BLANK", False))
        name = "anhuan-f0h-" + secrets.token_hex(16)
        seccomp = str((_PROJECT_ROOT / "infra/f0f/seccomp.json").resolve(strict=True))
        argv = (
            "/usr/local/bin/docker",
            "run",
            "--name",
            name,
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
            "--read-only",
            "--user",
            "65532:65532",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--security-opt",
            f"seccomp={seccomp}",
            "--pids-limit",
            "64",
            "--memory",
            "1024m",
            "--memory-swap",
            "1024m",
            "--cpus",
            "1",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=16m,uid=65532,gid=65532,mode=0700",
            "--tmpfs",
            "/work:rw,nosuid,nodev,noexec,size=256m,uid=65532,gid=65532,mode=0700",
            "--log-driver",
            "none",
            _OLD_IMAGE_ID,
        )
        try:
            completed = subprocess.run(
                argv,
                input=envelope,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
                cwd="/private/tmp",
                env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC"},
                timeout=120,
                check=False,
            )
        finally:
            envelope[:] = b"\0" * len(envelope)
            envelope.clear()
            _remove_container("/usr/local/bin/docker", name)
        if completed.returncode != 0 or completed.stderr or len(completed.stdout) > 1024 * 1024:
            return False
        value = json.loads(completed.stdout.decode("ascii", errors="strict"))
        blocks = value.get("blocks")
        valid = (
            value.get("schema") == "f0f-body-result-v1"
            and value.get("status") == "SUCCESS"
            and value.get("ocr_engine", {}).get("version") == "1.4.4"
            and value.get("raw_text_persisted") is False
            and value.get("external_calls") == 0
            and blocks == []
        )
        if isinstance(blocks, list):
            blocks.clear()
        return valid
    except (F0HError, OSError, ValueError, TypeError, subprocess.SubprocessError):
        return False


__all__ = ("replay_profile", "verify_repeat")
