"""Deterministic aggregate-only F0-E acceptance artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ..database import DatabaseConfig
from .acceptance import acceptance_snapshot
from .contracts import F0EError
from .runtime_config import load_runtime_bundle


ARTIFACT_ROOT = (
    Path(__file__).resolve().parents[3]
    / "artifacts/f0e-local-ocr/v0.1"
)
_OUTPUTS = frozenset({"acceptance.json", "status.html", "sbom.json"})
_EXPECTED = {
    "eligible_plans": 24,
    "runs": 24,
    "visual_units": 249,
    "unique_visual_units": 249,
    "native_references": 225,
    "local_ocr_routes": 24,
    "deferred_documents": 2,
    "render_calls": 23,
    "jobs": 24,
    "jobs_succeeded": 24,
    "f0c_ocr_executed_true": 0,
    "raw_text_persisted_true": 0,
    "page_images_persisted_true": 0,
    "gate_bypasses": 0,
    "negative_gate_violations": 0,
    "route_violations": 0,
}


def generate_artifacts(config: DatabaseConfig) -> dict[str, str]:
    snapshot = acceptance_snapshot(config)
    if any(snapshot.get(key) != value for key, value in _EXPECTED.items()):
        raise F0EError("REPLAY_MISMATCH")
    if (
        int(snapshot["local_ocr_evidence"])
        + int(snapshot["manual_review_required"])
        != 24
    ):
        raise F0EError("REPLAY_MISMATCH")
    runtime = load_runtime_bundle()
    acceptance = {
        "schema": "f0e-local-ocr-acceptance-v1",
        "status": "LOCAL_FIXTURE_OCR_EVIDENCE_ACCEPTED",
        "fixture_label": "FIXTURE_ONLY",
        "benchmark_tier": "NONE",
        "accuracy_claimed": False,
        "gold_status": "NOT_EVALUATED",
        "professional_status": "NOT_REVIEWED",
        "external_processing": "DENY",
        "external_calls": 0,
        "raw_text_persisted": False,
        "page_images_persisted": False,
        "counts": {
            "processing_plans": int(snapshot["runs"]),
            "visual_units": int(snapshot["visual_units"]),
            "native_references": int(snapshot["native_references"]),
            "local_ocr_executed": int(snapshot["local_ocr_routes"]),
            "local_ocr_evidence": int(snapshot["local_ocr_evidence"]),
            "manual_review_required": int(snapshot["manual_review_required"]),
            "pdf_render_calls": int(snapshot["render_calls"]),
            "deferred_documents": int(snapshot["deferred_documents"]),
            "jobs_succeeded": int(snapshot["jobs_succeeded"]),
        },
        "integrity": {
            "evidence_summary_sha256": snapshot["evidence_summary_sha256"],
            "runtime_lock_sha256": runtime.lock_sha256,
            "execution_profile_sha256": runtime.execution_profile_sha256,
            "container_image_id": runtime.container_image_id,
            "unique_route_per_visual_unit": True,
            "runtime_network": "NONE",
            "runtime_downloads": False,
            "temporary_residuals": 0,
        },
        "closed_gates": {
            "real_customer": True,
            "region_industry": True,
            "acceptance_gold": True,
            "external_ocr_llm": True,
            "professional_responsibility": True,
            "uat": True,
            "production": True,
        },
    }
    sbom = {
        "schema": "f0e-local-ocr-sbom-v1",
        "status": "ENGINEERING_INVENTORY_ONLY_NOT_LEGAL_APPROVAL",
        "platform": {"os": "linux", "architecture": "arm64", "python": "3.11.9"},
        "container_image_id": runtime.container_image_id,
        "components": [
            {
                "name": "pypdfium2",
                "version": "5.12.1",
                "binary_sha256": runtime.renderer_binary_sha256,
                "license": "BSD-3-Clause",
                "bundled_pdfium_license_review": "ENGINEERING_INVENTORY_ONLY",
            },
            {
                "name": "rapidocr-onnxruntime",
                "version": "1.4.4",
                "binary_sha256": runtime.ocr_engine_binary_sha256,
                "license": "Apache-2.0",
            },
            {
                "name": "onnxruntime",
                "version": "1.28.0",
                "license": "MIT",
            },
            {
                "name": "local-model-bundle",
                "version": "1.1.0",
                "binary_sha256": runtime.language_pack_bundle_sha256,
                "license_review": "ENGINEERING_INVENTORY_ONLY",
            },
        ],
        "runtime_policy": {
            "network": "NONE",
            "external_processing": "DENY",
            "runtime_downloads": False,
            "raw_text_persisted": False,
            "page_images_persisted": False,
        },
    }
    status = _status_html(acceptance)
    payloads = {
        "acceptance.json": _json_bytes(acceptance),
        "status.html": status,
        "sbom.json": _json_bytes(sbom),
    }
    for name, payload in payloads.items():
        _atomic_write(name, payload)
    return {
        "acceptance_sha256": hashlib.sha256(payloads["acceptance.json"]).hexdigest(),
        "status_sha256": hashlib.sha256(payloads["status.html"]).hexdigest(),
        "sbom_sha256": hashlib.sha256(payloads["sbom.json"]).hexdigest(),
    }


def _json_bytes(value: dict[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("ascii")


def _status_html(acceptance: dict[str, object]) -> bytes:
    counts = acceptance["counts"]
    assert isinstance(counts, dict)
    html = (
        "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
        "<title>F0-E Local Fixture OCR Evidence</title><body>"
        "<main><h1>LOCAL_FIXTURE_OCR_EVIDENCE_ACCEPTED</h1>"
        "<p>FIXTURE_ONLY / BENCHMARK_TIER=NONE / NOT GOLD / NOT PRODUCTION</p>"
        "<dl>"
        f"<dt>Visual units</dt><dd>{counts['visual_units']}</dd>"
        f"<dt>Native references</dt><dd>{counts['native_references']}</dd>"
        f"<dt>Local OCR executed</dt><dd>{counts['local_ocr_executed']}</dd>"
        f"<dt>PDF render calls</dt><dd>{counts['pdf_render_calls']}</dd>"
        f"<dt>Deferred documents</dt><dd>{counts['deferred_documents']}</dd>"
        "</dl><p>Raw text persisted: false. Page images persisted: false. "
        "External processing: DENY. Accuracy is not claimed.</p>"
        "</main></body></html>\n"
    )
    return html.encode("ascii")


def _atomic_write(name: str, payload: bytes) -> None:
    if name not in _OUTPUTS:
        raise F0EError("REPLAY_MISMATCH")
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    if ARTIFACT_ROOT.is_symlink():
        raise F0EError("REPLAY_MISMATCH")
    os.chmod(ARTIFACT_ROOT, 0o700)
    destination = ARTIFACT_ROOT / name
    temporary = ARTIFACT_ROOT / f".{name}.tmp"
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            view = memoryview(payload)
            offset = 0
            while offset < len(view):
                written = os.write(descriptor, view[offset:])
                if written <= 0:
                    raise F0EError("REPLAY_MISMATCH")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
        directory = os.open(ARTIFACT_ROOT, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except F0EError:
        raise
    except OSError:
        raise F0EError("REPLAY_MISMATCH") from None
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


__all__ = ("ARTIFACT_ROOT", "generate_artifacts")
