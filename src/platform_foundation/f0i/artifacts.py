"""Deterministic aggregate-only acceptance artifacts for F0-I."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat

from platform_foundation.f0h.runtime_config import load_runtime_bundle

from .contracts import F0IError, canonical_json_bytes


ARTIFACT_ROOT = (
    Path(__file__).resolve().parents[3]
    / "artifacts/f0i-canonical-chunks/v0.1"
)
_OUTPUTS = frozenset({"acceptance.json", "status.html", "sbom.json"})
_STATUS_FIELDS = {
    "status": "LOCAL_CANONICAL_CHUNKS_READY",
    "accuracy_status": "ACCURACY_NOT_EVALUATED",
    "search_status": "SEARCH_NOT_READY",
    "production_status": "NOT_PRODUCTION",
}
_EXPECTED = {
    "smoke": {
        "documents": 10,
        "document_scopes": 10,
        "visual_documents": 6,
        "structure_documents": 2,
        "deferred_documents": 2,
        "visual_units": 110,
        "native_visual_units": 105,
        "ocr_visual_units": 5,
        "structure_units": 4,
        "pages": 110,
    },
    "full": {
        "documents": 26,
        "document_scopes": 26,
        "visual_documents": 22,
        "structure_documents": 2,
        "deferred_documents": 2,
        "visual_units": 249,
        "native_visual_units": 225,
        "ocr_visual_units": 24,
        "structure_units": 4,
        "pages": 249,
    },
}
_FULL_STRUCTURE = {
    "docx_sections": 1,
    "docx_paragraphs": 60,
    "docx_tables": 1,
    "docx_rows": 5,
    "docx_table_cells": 58,
    "xlsx_sheets": 3,
    "xlsx_cells": 306,
    "xlsx_formula_cells": 0,
    "xlsx_formula_cached_values": 0,
    "xlsx_value_cells": 19,
}
_RUN_PROOF = {
    "smoke": {
        "persisted_runs": 1,
        "persisted_ocr_calls": 5,
        "persisted_smoke_ocr_calls": 5,
        "persisted_full_ocr_calls": 0,
    },
    "full": {
        "persisted_runs": 2,
        "persisted_ocr_calls": 24,
        "persisted_smoke_ocr_calls": 5,
        "persisted_full_ocr_calls": 19,
    },
}
_ZERO_PROOF_FIELDS = (
    "errors",
    "tenant_version_crosswires",
    "orphan_blocks",
    "orphan_chunks",
    "plaintext_leaks",
    "external_calls",
    "search_calls",
)
_POSTGRESQL_VERSION = "18.3"
_PGCRYPTO_VERSION = "1.4"


def generate_artifacts() -> dict[str, str]:
    """Run smoke/full/full serially and publish only stable aggregate evidence."""

    from .replay import replay_sequence

    results = replay_sequence(("smoke", "full", "full"))
    if not isinstance(results, tuple) or len(results) != 3:
        raise F0IError("ARTIFACT_GENERATION_FAILED")
    smoke, full_first, full_second = results
    smoke_proof = _replay_proof(smoke, "smoke")
    full_proof = _replay_proof(full_first, "full")
    repeated_proof = _replay_proof(full_second, "full")
    _verify_sequence(smoke, full_first, full_second, full_proof, repeated_proof)

    bundle = load_runtime_bundle()
    _verify_runtime(bundle, smoke, full_first, full_second)
    stable_binding = {
        "configuration_sha256": full_first["configuration_sha256"],
        "input_manifest_sha256": full_first["input_manifest_sha256"],
        "registered_plan_sha256": full_first["registered_plan_sha256"],
        "replay_summary_sha256": full_first["replay_summary_sha256"],
        "runtime_configuration_sha256": bundle.configuration_sha256,
        "runtime_lock_sha256": bundle.lock_sha256,
        "runtime_model_bundle_sha256": bundle.model_bundle_sha256,
        "runtime_profile_sha256": bundle.execution_profile_sha256,
    }
    replay_pair_sha256 = hashlib.sha256(
        canonical_json_bytes({"smoke": smoke_proof, "full": full_proof})
    ).hexdigest()
    acceptance: dict[str, object] = {
        "schema": "f0i-canonical-chunks-acceptance-v1",
        **_STATUS_FIELDS,
        "fixture_label": "FIXTURE_ONLY",
        "benchmark_tier": "NONE",
        "accuracy_claimed": False,
        "external_processing": "DENY",
        "raw_text_persisted": False,
        "plaintext_columns": 0,
        "external_calls": 0,
        "search_calls": 0,
        "ocr_provider": "ppocrv6-small",
        "ocr_family": "PP-OCRv6",
        "ocr_status": "NOT_EVALUATED",
        "chunking": {
            "child_target_characters": [300, 800],
            "overlap_characters": 0,
            "cross_processing_unit": False,
            "leaf_span_reconstruction": True,
        },
        "replays": {
            "smoke": smoke_proof,
            "full": full_proof,
            "full_repeat_identical": True,
            "second_full_rows_inserted": 0,
            "second_full_ocr_calls": 0,
        },
        "integrity": {
            **stable_binding,
            "replay_pair_sha256": replay_pair_sha256,
            "legacy_v4_rollback_ready": True,
            "upstream_mutations": 0,
        },
        "closed_gates": {
            "acceptance_gold": True,
            "classification": True,
            "embedding": True,
            "external_ocr_llm": True,
            "human_annotation": True,
            "production": True,
            "professional_responsibility": True,
            "real_customer": True,
            "region_industry": True,
            "search": True,
            "uat": True,
        },
    }
    payloads = {
        "acceptance.json": _json_bytes(acceptance),
        "status.html": _status_html(smoke_proof, full_proof),
        "sbom.json": _json_bytes(_sbom(bundle, stable_binding)),
    }
    _validate_public_payloads(payloads)
    _atomic_write_all(payloads)
    return {
        "acceptance_sha256": hashlib.sha256(payloads["acceptance.json"]).hexdigest(),
        "status_sha256": hashlib.sha256(payloads["status.html"]).hexdigest(),
        "sbom_sha256": hashlib.sha256(payloads["sbom.json"]).hexdigest(),
    }


def _replay_proof(value: Mapping[str, object], profile: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or profile not in _EXPECTED:
        raise F0IError("ARTIFACT_GENERATION_FAILED")
    if value.get("schema") != "f0i-replay-result-v1" or value.get("profile") != profile:
        raise F0IError("ARTIFACT_GENERATION_FAILED")
    if any(value.get(key) != expected for key, expected in _STATUS_FIELDS.items()):
        raise F0IError("ARTIFACT_GENERATION_FAILED")
    summary = value.get("summary")
    if not isinstance(summary, Mapping):
        raise F0IError("ARTIFACT_GENERATION_FAILED")
    expected = _EXPECTED[profile]
    if any(summary.get(key) != item for key, item in expected.items()):
        raise F0IError("ARTIFACT_GENERATION_FAILED")
    if profile == "full" and any(
        summary.get(key) != item for key, item in _FULL_STRUCTURE.items()
    ):
        raise F0IError("ARTIFACT_GENERATION_FAILED")
    if any(summary.get(key) != item for key, item in _RUN_PROOF[profile].items()):
        raise F0IError("ARTIFACT_GENERATION_FAILED")
    if any(value.get(key) != 0 for key in ("external_calls", "search_calls", "errors")):
        raise F0IError("ARTIFACT_GENERATION_FAILED")
    if any(summary.get(key, 0) != 0 for key in _ZERO_PROOF_FIELDS):
        raise F0IError("ARTIFACT_GENERATION_FAILED")
    if value.get("raw_text_persisted") is not False:
        raise F0IError("ARTIFACT_GENERATION_FAILED")
    if summary.get("negative_scopes") != 2 or summary.get("negative_enabled_gates") != 0:
        raise F0IError("ARTIFACT_GENERATION_FAILED")
    if summary.get("reconstruction_failures") != 0:
        raise F0IError("ARTIFACT_GENERATION_FAILED")
    for name in (
        "configuration_sha256",
        "registered_plan_sha256",
        "input_manifest_sha256",
        "input_summary_sha256",
        "replay_summary_sha256",
    ):
        if not _is_sha256(value.get(name)):
            raise F0IError("ARTIFACT_GENERATION_FAILED")
    dynamic_keys = {
        "blocks",
        "parent_chunks",
        "child_chunks",
        "child_block_links",
    }
    if any(not _is_positive_int(summary.get(name)) for name in dynamic_keys):
        raise F0IError("ARTIFACT_GENERATION_FAILED")
    return {
        **expected,
        **({key: summary[key] for key in _FULL_STRUCTURE} if profile == "full" else {}),
        **_RUN_PROOF[profile],
        "blocks": summary["blocks"],
        "parent_chunks": summary["parent_chunks"],
        "child_chunks": summary["child_chunks"],
        "child_block_links": summary["child_block_links"],
        "negative_scopes": 2,
        "negative_enabled_gates": 0,
        "replay_summary_sha256": value["replay_summary_sha256"],
    }


def _verify_sequence(
    smoke: Mapping[str, object],
    full_first: Mapping[str, object],
    full_second: Mapping[str, object],
    full_proof: Mapping[str, object],
    repeated_proof: Mapping[str, object],
) -> None:
    deltas: list[tuple[int, int]] = []
    for value in (smoke, full_first, full_second):
        delta = value.get("delta")
        if not isinstance(delta, Mapping):
            raise F0IError("ARTIFACT_GENERATION_FAILED")
        rows = delta.get("rows_inserted")
        calls = delta.get("ocr_calls")
        if not _is_non_negative_int(rows) or not _is_non_negative_int(calls):
            raise F0IError("ARTIFACT_GENERATION_FAILED")
        deltas.append((rows, calls))
    if deltas[2] != (0, 0):
        raise F0IError("ARTIFACT_GENERATION_FAILED")
    # A fresh acceptance performs 5+19 OCR calls. A deterministic rebuild over
    # the already accepted database performs no OCR at all.
    if (deltas[0][1], deltas[1][1]) not in {(5, 19), (0, 0)}:
        raise F0IError("ARTIFACT_GENERATION_FAILED")
    if (deltas[0][1], deltas[1][1]) == (5, 19) and (
        deltas[0][0] <= 0 or deltas[1][0] <= 0
    ):
        raise F0IError("ARTIFACT_GENERATION_FAILED")
    if (deltas[0][1], deltas[1][1]) == (0, 0) and (
        deltas[0][0] != 0 or deltas[1][0] != 0
    ):
        raise F0IError("ARTIFACT_GENERATION_FAILED")
    if dict(full_first.get("summary", {})) != dict(full_second.get("summary", {})):
        raise F0IError("ARTIFACT_GENERATION_FAILED")
    if full_first.get("replay_summary_sha256") != full_second.get("replay_summary_sha256"):
        raise F0IError("ARTIFACT_GENERATION_FAILED")
    if dict(full_proof) != dict(repeated_proof):
        raise F0IError("ARTIFACT_GENERATION_FAILED")


def _verify_runtime(bundle: object, *values: Mapping[str, object]) -> None:
    expected = {
        "model_bundle_sha256": bundle.model_bundle_sha256,
        "configuration_sha256": bundle.configuration_sha256,
        "execution_profile_sha256": bundle.execution_profile_sha256,
        "lock_sha256": bundle.lock_sha256,
        "image_id": bundle.container_image_id,
    }
    for value in values:
        runtime = value.get("runtime")
        if not isinstance(runtime, Mapping) or any(
            runtime.get(key) != item for key, item in expected.items()
        ):
            raise F0IError("ARTIFACT_GENERATION_FAILED")


def _sbom(bundle: object, binding: Mapping[str, object]) -> dict[str, object]:
    return {
        "schema": "f0i-canonical-chunks-sbom-v1",
        "status": "ENGINEERING_INVENTORY_ONLY_NOT_LEGAL_APPROVAL",
        "components": [
            {"kind": "runtime", "name": "CPython", "version": "3.11.9", "license": "PSF-2.0"},
            {"kind": "database", "name": "PostgreSQL", "version": _POSTGRESQL_VERSION, "license": "PostgreSQL"},
            {"kind": "database-extension", "name": "pgcrypto", "version": _PGCRYPTO_VERSION, "license": "PostgreSQL"},
            {"kind": "parser", "name": "pypdf", "version": "6.14.2", "license": "BSD-3-Clause"},
            {"kind": "ocr-runtime", "name": "RapidOCR", "version": bundle.rapidocr_version, "license": "Apache-2.0", "sha256": bundle.rapidocr_wheel_sha256},
            {"kind": "ocr-runtime", "name": "ONNX Runtime", "version": bundle.onnxruntime_version, "license": "MIT"},
            {"kind": "ocr-model", "name": "PP-OCRv6 small detector", "family": "PP-OCRv6", "license": "Apache-2.0", "sha256": bundle.detector_model_sha256},
            {"kind": "ocr-model", "name": "PP-OCRv6 small recognizer", "family": "PP-OCRv6", "license": "Apache-2.0", "sha256": bundle.recognizer_model_sha256},
        ],
        "integrity": dict(binding),
        "policy": {
            "accuracy_status": "NOT_EVALUATED",
            "benchmark_tier": "NONE",
            "external_processing": "DENY",
            "network": "NONE",
            "plaintext_columns": 0,
            "production_allowed": False,
            "raw_text_persisted": False,
            "search_status": "NOT_READY",
        },
    }


def _status_html(smoke: Mapping[str, object], full: Mapping[str, object]) -> bytes:
    return (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<title>F0-I Canonical Chunks</title><body><main>'
        '<h1>LOCAL_CANONICAL_CHUNKS_READY</h1>'
        '<p>ACCURACY_NOT_EVALUATED / SEARCH_NOT_READY / NOT_PRODUCTION</p>'
        '<p>FIXTURE_ONLY / BENCHMARK_TIER=NONE / EXTERNAL_PROCESSING=DENY</p>'
        '<h2>Smoke replay</h2><dl>'
        f'<dt>Documents</dt><dd>{smoke["documents"]}</dd>'
        f'<dt>Visual units</dt><dd>{smoke["visual_units"]}</dd>'
        f'<dt>Native</dt><dd>{smoke["native_visual_units"]}</dd>'
        f'<dt>PP-OCRv6</dt><dd>{smoke["ocr_visual_units"]}</dd>'
        '</dl><h2>Full replay</h2><dl>'
        f'<dt>Documents</dt><dd>{full["documents"]}</dd>'
        f'<dt>Visual units</dt><dd>{full["visual_units"]}</dd>'
        f'<dt>Native</dt><dd>{full["native_visual_units"]}</dd>'
        f'<dt>PP-OCRv6</dt><dd>{full["ocr_visual_units"]}</dd>'
        f'<dt>Structure units</dt><dd>{full["structure_units"]}</dd>'
        f'<dt>Deferred documents</dt><dd>{full["deferred_documents"]}</dd>'
        f'<dt>Blocks</dt><dd>{full["blocks"]}</dd>'
        f'<dt>Parent chunks</dt><dd>{full["parent_chunks"]}</dd>'
        f'<dt>Child chunks</dt><dd>{full["child_chunks"]}</dd>'
        '</dl><p>Full replay was repeated with identical aggregate evidence; '
        'the second run inserted zero rows and invoked OCR zero times.</p>'
        '<p>Canonical bodies exist only as pgcrypto-encrypted values. Search, Gold, '
        'classification, external processing and production remain closed.</p>'
        '</main></body></html>\n'
    ).encode("ascii")


def _json_bytes(value: Mapping[str, object]) -> bytes:
    try:
        return (
            json.dumps(dict(value), ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False)
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        raise F0IError("ARTIFACT_GENERATION_FAILED") from None


def _validate_public_payloads(payloads: Mapping[str, bytes]) -> None:
    if set(payloads) != _OUTPUTS:
        raise F0IError("ARTIFACT_GENERATION_FAILED")
    combined = b"\0".join(payloads[name] for name in sorted(payloads))
    forbidden = (
        b"/Users/",
        b"environment-demo",
        b"http://",
        b"https://",
        b"source_path",
        b"filename",
        b"plaintext_sha256",
        b"ciphertext",
    )
    if any(token in combined for token in forbidden):
        raise F0IError("ARTIFACT_GENERATION_FAILED")
    # Cryptographic identities are expected public aggregate metadata.  Mask
    # exact SHA-256 tokens before the PII heuristic so a random digit run in a
    # new key-derived hash cannot make an otherwise valid acceptance
    # permanently unpublishable.
    pii_surface = re.sub(
        rb"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", b"<sha256>", combined
    )
    if re.search(rb"(?<![0-9])1[3-9][0-9]{9}(?![0-9])", pii_surface) or re.search(
        rb"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+",
        pii_surface,
    ):
        raise F0IError("ARTIFACT_GENERATION_FAILED")


def _atomic_write_all(payloads: Mapping[str, bytes]) -> None:
    _atomic_write_all_at(ARTIFACT_ROOT, payloads)


def _atomic_write_all_at(root_path: Path, payloads: Mapping[str, bytes]) -> None:
    root = _prepare_root(root_path)
    staged: dict[str, Path] = {}
    try:
        for name in sorted(_OUTPUTS):
            payload = payloads.get(name)
            if not isinstance(payload, bytes):
                raise F0IError("ARTIFACT_GENERATION_FAILED")
            temporary = root / f".{name}.{secrets.token_hex(16)}.tmp"
            _write_new_file(temporary, payload)
            staged[name] = temporary
        # Reject every unsafe destination before publishing any member.  In
        # particular, a late-sorted symlink/FIFO/hardlink must not leave an
        # earlier member replaced and a mixed artifact set behind.
        for name in sorted(_OUTPUTS):
            _validate_replace_target(root / name)
        for name in sorted(_OUTPUTS):
            destination = root / name
            os.replace(staged[name], destination)
            os.chmod(destination, 0o600)
            _verify_output(destination, payloads[name])
            del staged[name]
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except F0IError:
        raise
    except OSError:
        raise F0IError("ARTIFACT_GENERATION_FAILED") from None
    finally:
        for temporary in staged.values():
            _unlink_owned_temporary(temporary)


def _prepare_root(root_path: Path = ARTIFACT_ROOT) -> Path:
    try:
        if not isinstance(root_path, Path) or not root_path.is_absolute():
            raise F0IError("ARTIFACT_GENERATION_FAILED")
        root_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        listed = os.lstat(root_path)
        resolved = root_path.resolve(strict=True)
        if not stat.S_ISDIR(listed.st_mode) or listed.st_uid != os.getuid() or resolved != root_path.absolute():
            raise F0IError("ARTIFACT_GENERATION_FAILED")
        os.chmod(root_path, 0o700)
        return resolved
    except F0IError:
        raise
    except (OSError, RuntimeError):
        raise F0IError("ARTIFACT_GENERATION_FAILED") from None


def _write_new_file(path: Path, payload: bytes) -> None:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, memoryview(payload)[offset:])
            if written <= 0:
                raise F0IError("ARTIFACT_GENERATION_FAILED")
            offset += written
        os.fsync(descriptor)
    except F0IError:
        raise
    except OSError:
        raise F0IError("ARTIFACT_GENERATION_FAILED") from None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                raise F0IError("ARTIFACT_GENERATION_FAILED") from None


def _validate_replace_target(path: Path) -> None:
    try:
        listed = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError:
        raise F0IError("ARTIFACT_GENERATION_FAILED") from None
    if not stat.S_ISREG(listed.st_mode) or listed.st_uid != os.getuid() or listed.st_nlink != 1 or stat.S_IMODE(listed.st_mode) != 0o600:
        raise F0IError("ARTIFACT_GENERATION_FAILED")


def _verify_output(path: Path, expected: bytes) -> None:
    try:
        listed = os.lstat(path)
        if (
            not stat.S_ISREG(listed.st_mode)
            or listed.st_uid != os.getuid()
            or listed.st_nlink != 1
            or stat.S_IMODE(listed.st_mode) != 0o600
            or listed.st_size != len(expected)
            or hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(expected).digest()
        ):
            raise F0IError("ARTIFACT_GENERATION_FAILED")
    except F0IError:
        raise
    except OSError:
        raise F0IError("ARTIFACT_GENERATION_FAILED") from None


def _unlink_owned_temporary(path: Path) -> None:
    try:
        listed = os.lstat(path)
        if stat.S_ISREG(listed.st_mode) and listed.st_uid == os.getuid() and listed.st_nlink == 1:
            path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        raise F0IError("ARTIFACT_GENERATION_FAILED") from None


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _is_non_negative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_positive_int(value: object) -> bool:
    return _is_non_negative_int(value) and value > 0


__all__ = ("ARTIFACT_ROOT", "generate_artifacts")
