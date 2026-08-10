"""Deterministic, aggregate-only acceptance artifacts for F0-H."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from collections.abc import Mapping

from .contracts import F0HError, canonical_json_bytes
from .replay import replay_profile, verify_repeat
from .runtime_config import RuntimeBundle, load_runtime_bundle


ARTIFACT_ROOT = (
    Path(__file__).resolve().parents[3]
    / "artifacts/f0h-ppocrv6-runtime/v0.1"
)
_OUTPUTS = frozenset({"acceptance.json", "status.html", "sbom.json"})
_EXPECTED_COUNTS = {
    "smoke": {
        "documents": 10,
        "visual_units": 110,
        "native_bypass": 105,
        "ppocrv6_ocr": 5,
        "deferred_documents": 2,
        "errors": 0,
    },
    "full": {
        "documents": 26,
        "visual_units": 249,
        "native_bypass": 225,
        "ppocrv6_ocr": 24,
        "deferred_documents": 2,
        "errors": 0,
    },
}
_STATUS_FIELDS = {
    "status": "LOCAL_PPOCRV6_RUNTIME_READY",
    "accuracy_status": "ACCURACY_NOT_EVALUATED",
    "search_status": "SEARCH_NOT_READY",
    "production_status": "NOT_PRODUCTION",
}


def generate_artifacts() -> dict[str, str]:
    """Run the registered fixture replays and write the three fixed artifacts."""

    # The order is part of the acceptance contract: smoke, then two full replays.
    smoke = replay_profile("smoke")
    full_first = replay_profile("full")
    full_second = replay_profile("full")
    verify_repeat(full_first, full_second)

    smoke_proof = _replay_proof(smoke, "smoke")
    full_proof = _replay_proof(full_first, "full")
    bundle = load_runtime_bundle()
    _verify_bundle_matches_replay(bundle, smoke, full_first)

    replay_pair_sha256 = hashlib.sha256(
        canonical_json_bytes({"smoke": smoke_proof, "full": full_proof})
    ).hexdigest()
    acceptance: dict[str, object] = {
        "schema": "f0h-ppocrv6-runtime-acceptance-v1",
        **_STATUS_FIELDS,
        "fixture_label": "FIXTURE_ONLY",
        "benchmark_tier": "NONE",
        "accuracy_claimed": False,
        "external_processing": "DENY",
        "external_calls": 0,
        "body_leaks": 0,
        "runtime_downloads": False,
        "raw_text_persisted": False,
        "page_images_persisted": False,
        "provider": {
            "engine_distribution": "RapidOCR",
            "engine_version": bundle.rapidocr_version,
            "ocr_family": bundle.ocr_family,
            "provider_id": "ppocrv6-small",
        },
        "replays": {
            "smoke": smoke_proof,
            "full": full_proof,
            "full_repeat_identical": True,
        },
        "integrity": {
            "configuration_sha256": bundle.configuration_sha256,
            "container_image_id": bundle.container_image_id,
            "model_bundle_sha256": bundle.model_bundle_sha256,
            "replay_pair_sha256": replay_pair_sha256,
            "runtime_lock_sha256": bundle.lock_sha256,
            "runtime_profile_sha256": bundle.execution_profile_sha256,
            "legacy_v4_rollback_ready": True,
            "old_runtime_mutations": 0,
        },
        "closed_gates": {
            "acceptance_gold": True,
            "external_ocr_llm": True,
            "production": True,
            "professional_responsibility": True,
            "real_customer": True,
            "region_industry": True,
            "search": True,
            "uat": True,
        },
    }
    sbom = _sbom(bundle)
    payloads = {
        "acceptance.json": _json_bytes(acceptance),
        "status.html": _status_html(smoke_proof, full_proof),
        "sbom.json": _json_bytes(sbom),
    }
    _validate_public_payloads(payloads)
    _atomic_write_all(payloads)
    return {
        "acceptance_sha256": hashlib.sha256(payloads["acceptance.json"]).hexdigest(),
        "status_sha256": hashlib.sha256(payloads["status.html"]).hexdigest(),
        "sbom_sha256": hashlib.sha256(payloads["sbom.json"]).hexdigest(),
    }


def _replay_proof(value: Mapping[str, object], profile: str) -> dict[str, object]:
    if profile not in _EXPECTED_COUNTS:
        raise F0HError("ARTIFACT_GENERATION_FAILED")
    if any(value.get(key) != expected for key, expected in _STATUS_FIELDS.items()):
        raise F0HError("ARTIFACT_GENERATION_FAILED")
    expected_counts = _EXPECTED_COUNTS[profile]
    if (
        value.get("profile") != profile
        or any(value.get(key) != expected for key, expected in expected_counts.items())
        or value.get("provider") != "ppocrv6-small"
        or value.get("rapidocr_version") != "3.9.2"
        or value.get("ocr_family") != "PP-OCRv6"
        or value.get("legacy_v4_rollback_ready") is not True
        or value.get("old_runtime_mutations") != 0
        or value.get("external_calls") != 0
        or value.get("runtime_downloads") != 0
        or value.get("raw_text_persisted") is not False
        or value.get("body_leaks") != 0
    ):
        raise F0HError("ARTIFACT_GENERATION_FAILED")
    required_hashes = (
        "registered_plan_sha256",
        "execution_summary_sha256",
    )
    if any(not _is_sha256(value.get(key)) for key in required_hashes):
        raise F0HError("ARTIFACT_GENERATION_FAILED")
    return {
        **expected_counts,
        "registered_plan_sha256": value["registered_plan_sha256"],
        "execution_summary_sha256": value["execution_summary_sha256"],
    }


def _verify_bundle_matches_replay(
    bundle: RuntimeBundle,
    smoke: Mapping[str, object],
    full: Mapping[str, object],
) -> None:
    expected = {
        "configuration_sha256": bundle.configuration_sha256,
        "container_image_id": bundle.container_image_id,
        "model_bundle_sha256": bundle.model_bundle_sha256,
        "runtime_profile_sha256": bundle.execution_profile_sha256,
    }
    if any(
        replay.get(key) != value
        for replay in (smoke, full)
        for key, value in expected.items()
    ):
        raise F0HError("ARTIFACT_GENERATION_FAILED")


def _sbom(bundle: RuntimeBundle) -> dict[str, object]:
    return {
        "schema": "f0h-ppocrv6-runtime-sbom-v1",
        "status": "ENGINEERING_INVENTORY_ONLY_NOT_LEGAL_APPROVAL",
        "container_image_id": bundle.container_image_id,
        "platform": {
            "architecture": "arm64",
            "operating_system": "linux",
            "python": "3.11.9",
        },
        "components": [
            {
                "kind": "runtime",
                "license": "Apache-2.0",
                "name": "RapidOCR",
                "sha256": bundle.rapidocr_wheel_sha256,
                "version": bundle.rapidocr_version,
            },
            {
                "kind": "runtime",
                "license": "MIT",
                "name": "ONNX Runtime",
                "version": bundle.onnxruntime_version,
            },
            {
                "family": "PP-OCRv6",
                "kind": "model",
                "license": "Apache-2.0",
                "model_type": "small",
                "name": "PP-OCRv6 small detector",
                "sha256": bundle.detector_model_sha256,
            },
            {
                "family": "PP-OCRv6",
                "kind": "model",
                "license": "Apache-2.0",
                "model_type": "small",
                "name": "PP-OCRv6 small recognizer",
                "sha256": bundle.recognizer_model_sha256,
            },
            {
                "family": "PP-OCRv2-compatible-mobile",
                "is_ppocrv6": False,
                "kind": "model",
                "license": "Apache-2.0",
                "model_type": "mobile",
                "name": "orientation classifier",
                "sha256": bundle.classifier_model_sha256,
            },
            {
                "kind": "embedded_metadata",
                "name": "recognition character dictionary",
                "sha256": bundle.dictionary_sha256,
            },
        ],
        "integrity": {
            "configuration_sha256": bundle.configuration_sha256,
            "model_bundle_sha256": bundle.model_bundle_sha256,
            "runtime_lock_sha256": bundle.lock_sha256,
            "runtime_profile_sha256": bundle.execution_profile_sha256,
        },
        "runtime_policy": {
            "accuracy_status": "NOT_EVALUATED",
            "benchmark_tier": "NONE",
            "external_processing": "DENY",
            "network": "NONE",
            "page_images_persisted": False,
            "production_allowed": False,
            "raw_text_persisted": False,
            "runtime_downloads": False,
            "search_status": "NOT_READY",
        },
    }


def _status_html(
    smoke: Mapping[str, object], full: Mapping[str, object]
) -> bytes:
    return (
        "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
        "<title>F0-H Local PP-OCRv6 Runtime</title><body><main>"
        "<h1>LOCAL_PPOCRV6_RUNTIME_READY</h1>"
        "<p>ACCURACY_NOT_EVALUATED / SEARCH_NOT_READY / NOT_PRODUCTION</p>"
        "<p>FIXTURE_ONLY / BENCHMARK_TIER=NONE / EXTERNAL_PROCESSING=DENY</p>"
        "<h2>Smoke replay</h2><dl>"
        f"<dt>Documents</dt><dd>{smoke['documents']}</dd>"
        f"<dt>Visual units</dt><dd>{smoke['visual_units']}</dd>"
        f"<dt>Native bypass</dt><dd>{smoke['native_bypass']}</dd>"
        f"<dt>PP-OCRv6 OCR</dt><dd>{smoke['ppocrv6_ocr']}</dd>"
        "</dl><h2>Full replay</h2><dl>"
        f"<dt>Documents</dt><dd>{full['documents']}</dd>"
        f"<dt>Visual units</dt><dd>{full['visual_units']}</dd>"
        f"<dt>Native bypass</dt><dd>{full['native_bypass']}</dd>"
        f"<dt>PP-OCRv6 OCR</dt><dd>{full['ppocrv6_ocr']}</dd>"
        f"<dt>Deferred documents</dt><dd>{full['deferred_documents']}</dd>"
        "</dl><p>Full replay repeated with an identical aggregate summary.</p>"
        "<p>Raw text persisted: false. Page images persisted: false. "
        "Runtime downloads: false. External calls: 0.</p>"
        "<p>The orientation classifier is a separately disclosed legacy mobile "
        "component and is not PP-OCRv6.</p>"
        "</main></body></html>\n"
    ).encode("ascii")


def _json_bytes(value: Mapping[str, object]) -> bytes:
    try:
        return (
            json.dumps(
                dict(value),
                ensure_ascii=True,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        raise F0HError("ARTIFACT_GENERATION_FAILED") from None


def _validate_public_payloads(payloads: Mapping[str, bytes]) -> None:
    if set(payloads) != _OUTPUTS:
        raise F0HError("ARTIFACT_GENERATION_FAILED")
    combined = b"\0".join(payloads[name] for name in sorted(payloads))
    forbidden = (
        b"/Users/",
        b"environment-demo",
        b"http://",
        b"https://",
        b"raw_text\"",
        b"source_path",
        b"filename",
    )
    if any(token in combined for token in forbidden):
        raise F0HError("ARTIFACT_GENERATION_FAILED")
    if (
        re.search(rb"(?<![0-9])1[3-9][0-9]{9}(?![0-9])", combined)
        or re.search(
            rb"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
            rb"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+",
            combined,
        )
    ):
        raise F0HError("ARTIFACT_GENERATION_FAILED")


def _atomic_write_all(payloads: Mapping[str, bytes]) -> None:
    root = _prepare_root()
    staged: dict[str, Path] = {}
    try:
        for name in sorted(_OUTPUTS):
            payload = payloads.get(name)
            if not isinstance(payload, bytes):
                raise F0HError("ARTIFACT_GENERATION_FAILED")
            temporary = root / f".{name}.{secrets.token_hex(16)}.tmp"
            _write_new_file(temporary, payload)
            staged[name] = temporary
        for name in sorted(_OUTPUTS):
            destination = root / name
            _validate_replace_target(destination)
            os.replace(staged[name], destination)
            os.chmod(destination, 0o600)
            _verify_output(destination, payloads[name])
            del staged[name]
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except F0HError:
        raise
    except OSError:
        raise F0HError("ARTIFACT_GENERATION_FAILED") from None
    finally:
        for temporary in staged.values():
            _unlink_owned_temporary(temporary)


def _prepare_root() -> Path:
    try:
        ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
        listed = os.lstat(ARTIFACT_ROOT)
        resolved = ARTIFACT_ROOT.resolve(strict=True)
        if (
            not stat.S_ISDIR(listed.st_mode)
            or listed.st_uid != os.getuid()
            or resolved != ARTIFACT_ROOT.absolute()
        ):
            raise F0HError("ARTIFACT_GENERATION_FAILED")
        os.chmod(ARTIFACT_ROOT, 0o700)
        return resolved
    except F0HError:
        raise
    except (OSError, RuntimeError):
        raise F0HError("ARTIFACT_GENERATION_FAILED") from None


def _write_new_file(path: Path, payload: bytes) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, memoryview(payload)[offset:])
            if written <= 0:
                raise F0HError("ARTIFACT_GENERATION_FAILED")
            offset += written
        os.fsync(descriptor)
    except F0HError:
        raise
    except OSError:
        raise F0HError("ARTIFACT_GENERATION_FAILED") from None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                raise F0HError("ARTIFACT_GENERATION_FAILED") from None


def _validate_replace_target(path: Path) -> None:
    try:
        listed = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError:
        raise F0HError("ARTIFACT_GENERATION_FAILED") from None
    if (
        not stat.S_ISREG(listed.st_mode)
        or listed.st_uid != os.getuid()
        or listed.st_nlink != 1
        or stat.S_IMODE(listed.st_mode) != 0o600
    ):
        raise F0HError("ARTIFACT_GENERATION_FAILED")


def _verify_output(path: Path, expected: bytes) -> None:
    try:
        listed = os.lstat(path)
        if (
            not stat.S_ISREG(listed.st_mode)
            or listed.st_uid != os.getuid()
            or listed.st_nlink != 1
            or stat.S_IMODE(listed.st_mode) != 0o600
            or listed.st_size != len(expected)
            or hashlib.sha256(path.read_bytes()).digest()
            != hashlib.sha256(expected).digest()
        ):
            raise F0HError("ARTIFACT_GENERATION_FAILED")
    except F0HError:
        raise
    except OSError:
        raise F0HError("ARTIFACT_GENERATION_FAILED") from None


def _unlink_owned_temporary(path: Path) -> None:
    try:
        listed = os.lstat(path)
        if (
            stat.S_ISREG(listed.st_mode)
            and listed.st_uid == os.getuid()
            and listed.st_nlink == 1
        ):
            path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        raise F0HError("ARTIFACT_GENERATION_FAILED") from None


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(character in "0123456789abcdef" for character in value)


__all__ = ("ARTIFACT_ROOT", "generate_artifacts")
