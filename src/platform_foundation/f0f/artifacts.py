"""Deterministic aggregate-only F0-F acceptance artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ..database import DatabaseConfig
from .acceptance import acceptance_snapshot
from .contracts import F0FError
from .runtime_config import load_runtime_bundle


ARTIFACT_ROOT = Path(__file__).resolve().parents[3] / "artifacts/f0f-controlled-body/v0.1"
_OUTPUTS = frozenset({"acceptance.json", "status.html", "sbom.json"})


def generate_artifacts(config: DatabaseConfig) -> dict[str, str]:
    snapshot = acceptance_snapshot(config)
    if (
        snapshot["registered_sources"] != snapshot["document_versions"]
        or snapshot["registered_sources"] != snapshot["processing_plans"]
        or snapshot["body_evidence"] != snapshot["unique_visual_units"]
        or snapshot["body_evidence"] != snapshot["f0e_visual_units"]
        or snapshot["native_bodies"] != snapshot["f0e_native"]
        or snapshot["ocr_bodies"] != snapshot["f0e_ocr"]
        or snapshot["body_jobs"]
        != snapshot["f0e_runs"] - snapshot["f0e_deferred"]
        or snapshot["body_jobs"] != snapshot["body_jobs_succeeded"]
        or snapshot["annotation_queue"] != 15
        or snapshot["annotation_ocr"] != 10
        or snapshot["annotation_native"] != 5
        or snapshot["annotation_ocr_documents"] != 7
        or snapshot["annotation_native_documents"] != 5
        or snapshot["gold_labels"] != 0
        or snapshot["gold_adjudications"] != 0
        or snapshot["ciphertexts_below_minimum"] != 0
        or snapshot["plaintext_columns"] != 0
        or snapshot["gate_bypasses"] != 0
        or snapshot["negative_queue_entries"] != 0
        or snapshot["page_images_persisted_true"] != 0
    ):
        raise F0FError("BODY_REPLAY_MISMATCH")
    runtime = load_runtime_bundle()
    acceptance = {
        "schema": "f0f-controlled-body-acceptance-v1",
        "status": "LOCAL_FIXTURE_CONTROLLED_BODY_ACCEPTED",
        "fixture_label": "FIXTURE_ONLY",
        "benchmark_tier": "NONE",
        "gold_status": "ANNOTATION_PENDING",
        "accuracy_claimed": False,
        "search_ready": False,
        "professional_status": "NOT_REVIEWED",
        "production_allowed": False,
        "external_processing": "DENY",
        "external_calls": 0,
        "page_images_persisted": False,
        "counts": {
            "encrypted_page_bodies": int(snapshot["body_evidence"]),
            "native_bodies": int(snapshot["native_bodies"]),
            "local_ocr_bodies": int(snapshot["ocr_bodies"]),
            "body_jobs_succeeded": int(snapshot["body_jobs_succeeded"]),
            "annotation_required": int(snapshot["annotation_queue"]),
            "annotation_ocr": int(snapshot["annotation_ocr"]),
            "annotation_native": int(snapshot["annotation_native"]),
            "gold_labels": 0,
            "gold_adjudications": 0,
        },
        "integrity": {
            "evidence_summary_sha256": snapshot["evidence_summary_sha256"],
            "runner_image_id": runtime.container_image_id,
            "runner_lock_sha256": runtime.lock_sha256,
            "runner_profile_sha256": runtime.execution_profile_sha256,
            "base_f0e_image_id": runtime.base_container_image_id,
            "base_f0e_execution_profile_sha256": (
                runtime.base_execution_profile_sha256
            ),
            "runner_protocol": "f0f-body-result-v1",
            "runtime_network": "NONE",
            "runtime_downloads": False,
            "unique_body_per_visual_unit": True,
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
        "schema": "f0f-controlled-body-sbom-v1",
        "status": "ENGINEERING_INVENTORY_ONLY_NOT_LEGAL_APPROVAL",
        "platform": {"os": "linux", "architecture": "arm64", "python": "3.11.9"},
        "container_image_id": runtime.container_image_id,
        "base_container_image_id": runtime.base_container_image_id,
        "components": [
            {"name": "pypdf", "version": "6.14.2", "license": "BSD-3-Clause"},
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
            {"name": "onnxruntime", "version": "1.28.0", "license": "MIT"},
            {
                "name": "PostgreSQL-pgcrypto",
                "version": "18",
                "license": "PostgreSQL",
                "usage": "LOCAL_FIXTURE_PGP_SYMMETRIC_ENCRYPTION_ONLY",
            },
        ],
        "runtime_policy": {
            "network": "NONE",
            "external_processing": "DENY",
            "runtime_downloads": False,
            "private_ipc_body": True,
            "page_images_persisted": False,
            "production_allowed": False,
        },
    }
    payloads = {
        "acceptance.json": _json_bytes(acceptance),
        "status.html": _status_html(acceptance),
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
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("ascii")


def _status_html(acceptance: dict[str, object]) -> bytes:
    counts = acceptance["counts"]
    assert isinstance(counts, dict)
    return (
        "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
        "<title>F0-F Controlled Fixture Body</title><body><main>"
        "<h1>LOCAL_FIXTURE_CONTROLLED_BODY_ACCEPTED</h1>"
        "<p>FIXTURE_ONLY / ANNOTATION_PENDING / NOT GOLD / NOT SEARCH READY / NOT PRODUCTION</p>"
        "<dl>"
        f"<dt>Encrypted page bodies</dt><dd>{counts['encrypted_page_bodies']}</dd>"
        f"<dt>Native bodies</dt><dd>{counts['native_bodies']}</dd>"
        f"<dt>Local OCR bodies</dt><dd>{counts['local_ocr_bodies']}</dd>"
        f"<dt>Annotation required</dt><dd>{counts['annotation_required']}</dd>"
        "</dl><p>External processing: DENY. Page images persisted: false. "
        "Accuracy and professional conclusions are not claimed.</p>"
        "</main></body></html>\n"
    ).encode("ascii")


def _atomic_write(name: str, payload: bytes) -> None:
    if name not in _OUTPUTS:
        raise F0FError("BODY_REPLAY_MISMATCH")
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    if ARTIFACT_ROOT.is_symlink():
        raise F0FError("BODY_REPLAY_MISMATCH")
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
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, memoryview(payload)[offset:])
                if written <= 0:
                    raise F0FError("BODY_REPLAY_MISMATCH")
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
    except F0FError:
        raise
    except OSError:
        raise F0FError("BODY_REPLAY_MISMATCH") from None
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


__all__ = ("ARTIFACT_ROOT", "generate_artifacts")
