"""Deterministic aggregate-only artifacts for the pending human workflow."""

from __future__ import annotations

import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import sys

from ..auth import SessionContext
from ..database import DatabaseConfig
from .acceptance import acceptance_snapshot, verify_token_bundle_binding
from .api import LOCAL_API_HOST, LOCAL_API_PORT
from .contracts import F0GError


ARTIFACT_ROOT = Path(__file__).resolve().parents[3] / "artifacts/f0g-annotation-workflow/v0.1"
_OUTPUTS = frozenset({"acceptance.json", "status.html", "sbom.json"})
_PACKAGE_COMPONENTS = (
    ("anyio", "AnyIO", "4.14.2", "MIT"),
    ("fastapi", "FastAPI", "0.133.1", "MIT"),
    ("h11", "h11", "0.16.0", "MIT"),
    (
        "psycopg",
        "psycopg",
        "3.2.9",
        "GNU Lesser General Public License v3 (LGPLv3)",
    ),
    (
        "psycopg-binary",
        "psycopg-binary",
        "3.2.9",
        "GNU Lesser General Public License v3 (LGPLv3)",
    ),
    ("pydantic", "Pydantic", "2.13.4", "MIT"),
    ("starlette", "Starlette", "1.3.1", "BSD-3-Clause"),
    ("uvicorn", "Uvicorn", "0.41.0", "BSD-3-Clause"),
)


def generate_artifacts(
    config: DatabaseConfig,
    operator: SessionContext,
    token_bundle_path: str,
) -> dict[str, str]:
    verify_token_bundle_binding(config, operator, token_bundle_path)
    snapshot = acceptance_snapshot(config, operator)
    if (
        int(snapshot["annotation_queue"]) <= 0
        or snapshot["guidelines"] != 1
        or snapshot["assignments"] != snapshot["annotation_queue"]
        or snapshot["unique_assignment_queues"] != snapshot["assignments"]
        or snapshot["label_slots"] != int(snapshot["assignments"]) * 2
        or snapshot["fixture_actors"] != 3
        or snapshot["active_fixture_memberships"] != 3
        or snapshot["active_fixture_sessions"] != 3
        or snapshot["unique_fixture_session_token_hashes"] != 3
        or snapshot["fixture_actor_violations"] != 0
        or snapshot["fixture_membership_violations"] != 0
        or snapshot["fixture_session_violations"] != 0
        or snapshot["fixture_session_token_violations"] != 0
        or snapshot["labels"] != 0
        or snapshot["adjudications"] != 0
        or snapshot["fixture_seed_gold"] != 0
        or snapshot["policy_bypasses"] != 0
        or snapshot["invalid_assignments"] != 0
        or snapshot["real_actions"] != 0
        or snapshot["prepare_audits"] != 1
        or snapshot["plaintext_columns"] != 0
        or snapshot["gate_bypasses"] != 0
    ):
        raise F0GError("ANNOTATION_ACCEPTANCE_MISMATCH")
    acceptance = {
        "schema": "f0g-annotation-workflow-acceptance-v1",
        "status": "LOCAL_FIXTURE_ANNOTATION_WORKFLOW_READY",
        "fixture_label": "FIXTURE_ONLY",
        "annotation_status": "HUMAN_LABELS_REQUIRED",
        "gold_status": "NOT_GOLD",
        "benchmark_tier": "NONE",
        "accuracy_claimed": False,
        "search_ready": False,
        "professional_status": "NOT_REVIEWED",
        "production_allowed": False,
        "external_processing": "DENY",
        "external_calls": 0,
        "verification_scope": {
            "database_aggregate": "IN_PROCESS_VERIFIED",
            "database_security_catalog": "IN_PROCESS_VERIFIED",
            "token_bundle_binding": "IN_PROCESS_VERIFIED",
            "loopback_listener_bind": "SEPARATE_GATE_NOT_BOUND",
            "reverse_attack_suite": "SEPARATE_GATE_NOT_BOUND",
            "external_calls": "GENERATOR_PROCESS_ONLY",
        },
        "counts": {
            "annotation_candidates": int(snapshot["annotation_queue"]),
            "blind_assignments": int(snapshot["assignments"]),
            "independent_label_slots": int(snapshot["label_slots"]),
            "fixture_actors": int(snapshot["fixture_actors"]),
            "active_fixture_memberships": int(snapshot["active_fixture_memberships"]),
            "active_fixture_sessions": int(snapshot["active_fixture_sessions"]),
            "unique_fixture_session_token_hashes": int(
                snapshot["unique_fixture_session_token_hashes"]
            ),
            "human_labels": 0,
            "adjudications": 0,
            "fixture_seed_gold": 0,
        },
        "integrity": {
            "workflow_summary_sha256": snapshot["workflow_summary_sha256"],
            "guideline_versioned": True,
            "blind_peer_labels": True,
            "third_party_adjudication": True,
            "body_and_label_cipher": "POSTGRESQL_PGCRYPTO_AES256",
            "http_cache": "NO_STORE",
            "runtime_network": "FIXED_LOOPBACK_CONFIGURATION",
            "runtime_host": LOCAL_API_HOST,
            "runtime_port": LOCAL_API_PORT,
            "prepare_audits": int(snapshot["prepare_audits"]),
            "fixture_actor_violations": int(snapshot["fixture_actor_violations"]),
            "fixture_membership_violations": int(
                snapshot["fixture_membership_violations"]
            ),
            "fixture_session_violations": int(
                snapshot["fixture_session_violations"]
            ),
            "fixture_session_token_violations": int(
                snapshot["fixture_session_token_violations"]
            ),
            "token_bundle_binding_violations": 0,
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
        "schema": "f0g-annotation-workflow-sbom-v1",
        "status": "ENGINEERING_INVENTORY_ONLY_NOT_LEGAL_APPROVAL",
        "application_runtime": {
            "os": platform.system(),
            "architecture": platform.machine(),
            "python": ".".join(str(item) for item in sys.version_info[:3]),
            "network": f"FIXED_{LOCAL_API_HOST}_{LOCAL_API_PORT}",
        },
        "database_runtime": {
            "os": "Linux-container",
            "architecture": "arm64",
            "postgresql_major": "18",
            "network": "LOCAL_CONTAINER_ONLY",
        },
        "components": [
            *_installed_components(),
            {
                "name": "PostgreSQL-pgcrypto",
                "version": "18",
                "license": "PostgreSQL",
                "usage": "LOCAL_FIXTURE_PGP_SYMMETRIC_ENCRYPTION_ONLY",
            },
        ],
        "runtime_policy": {
            "network": "FIXED_LOOPBACK_CONFIGURATION",
            "listener_bind_evidence": "SEPARATE_GATE_NOT_BOUND",
            "reverse_evidence": "SEPARATE_GATE_NOT_BOUND",
            "external_processing": "DENY",
            "runtime_downloads": False,
            "plaintext_persistence": False,
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


def _installed_components() -> list[dict[str, str]]:
    components: list[dict[str, str]] = []
    try:
        for package, display_name, expected_version, license_expression in _PACKAGE_COMPONENTS:
            installed_version = metadata.version(package)
            if installed_version != expected_version:
                raise F0GError("ANNOTATION_ACCEPTANCE_MISMATCH")
            components.append(
                {
                    "name": display_name,
                    "version": installed_version,
                    "license": license_expression,
                }
            )
    except F0GError:
        raise
    except metadata.PackageNotFoundError:
        raise F0GError("ANNOTATION_ACCEPTANCE_MISMATCH") from None
    return components


def _status_html(acceptance: dict[str, object]) -> bytes:
    counts = acceptance["counts"]
    if not isinstance(counts, dict):
        raise F0GError("ANNOTATION_ACCEPTANCE_MISMATCH")
    return (
        "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
        "<title>F0-G Fixture Annotation Workflow</title><body><main>"
        "<h1>LOCAL_FIXTURE_ANNOTATION_WORKFLOW_READY</h1>"
        "<p>FIXTURE_ONLY / HUMAN_LABELS_REQUIRED / NOT GOLD / NOT PRODUCTION</p><dl>"
        f"<dt>Blind assignments</dt><dd>{counts['blind_assignments']}</dd>"
        f"<dt>Independent label slots</dt><dd>{counts['independent_label_slots']}</dd>"
        f"<dt>Human labels</dt><dd>{counts['human_labels']}</dd>"
        f"<dt>Adjudications</dt><dd>{counts['adjudications']}</dd>"
        "</dl><p>External processing: DENY. Accuracy, Acceptance Gold and "
        "professional conclusions are not claimed.</p>"
        "<p>Database aggregate, catalog and token binding are verified here; "
        "listener bind and reverse attacks remain separate required gates.</p>"
        "</main></body></html>\n"
    ).encode("ascii")


def _atomic_write(name: str, payload: bytes) -> None:
    if name not in _OUTPUTS:
        raise F0GError("ANNOTATION_ACCEPTANCE_MISMATCH")
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    if ARTIFACT_ROOT.is_symlink():
        raise F0GError("ANNOTATION_ACCEPTANCE_MISMATCH")
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
                    raise F0GError("ANNOTATION_ACCEPTANCE_MISMATCH")
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
    except F0GError:
        raise
    except OSError:
        raise F0GError("ANNOTATION_ACCEPTANCE_MISMATCH") from None
    finally:
        _unlink_if_present(temporary)


def _unlink_if_present(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        raise F0GError("ANNOTATION_ACCEPTANCE_MISMATCH") from None


__all__ = ("ARTIFACT_ROOT", "generate_artifacts")
