"""Fresh-database F0-E replay using only registered local fixture objects."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from ..auth import authenticate_local_session
from ..bootstrap import LOCAL_TENANT_A_TOKEN
from ..database import DatabaseConfig, tenant_transaction
from ..f0_isolation import load_frozen_f0_isolation
from ..replay import replay_profile as replay_f0d_profile
from ..vault import LocalFixtureVault
from .contracts import F0EError
from .hashing import canonical_sha256
from .replay import assemble_run_envelope
from .runtime_config import (
    load_runtime_bundle,
    register_runtime_configuration,
    runtime_paths,
)
from .service import LocalOcrService
from .supervisor import FixedArgvSandboxSupervisor, docker_argv
from .vault_adapter import open_verified_source


ACCEPTANCE_VAULT_ROOT = "/private/tmp/anhuan-f0e-acceptance-v01"
_WORKER_ID = "f0e-local-worker-v01"
_ELIGIBLE_TYPES = ("PDF", "JPEG", "DOC")
_DELTA_KEYS = (
    "configurations",
    "runs",
    "page_evidence",
    "deferred_documents",
    "jobs",
    "jobs_succeeded",
)


def replay_profile(config: DatabaseConfig, profile: str) -> dict[str, object]:
    if profile not in {"smoke", "full"}:
        raise F0EError("REPLAY_MISMATCH")
    vault_root, runtime_root = _acceptance_resources()
    if Path(vault_root).is_symlink():
        raise F0EError("SOURCE_OBJECT_INVALID")

    foundation = replay_f0d_profile(
        config, profile, vault_root=vault_root
    )
    expected_foundation = {
        "smoke": (10, 110, 105, 5),
        "full": (26, 249, 225, 24),
    }[profile]
    if (
        foundation.get("selected_documents"),
        foundation.get("units"),
        foundation.get("native"),
        foundation.get("ocr"),
    ) != expected_foundation:
        raise F0EError("REPLAY_MISMATCH")

    context = authenticate_local_session(config, LOCAL_TENANT_A_TOKEN)
    before = acceptance_snapshot(config)
    bundle = load_runtime_bundle(runtime_root)
    configuration = register_runtime_configuration(config, context, bundle)
    service = LocalOcrService(config)
    for processing_plan_id in _eligible_plan_ids(config):
        service.enqueue(
            context, processing_plan_id, configuration.configuration_id
        )

    docker, seccomp = runtime_paths(runtime_root)
    supervisor = FixedArgvSandboxSupervisor(
        docker_argv(docker, seccomp, bundle.container_image_id),
        bundle.sandbox_profile,
        bundle.resource_limits,
    )
    processed = 0
    render_calls = 0
    local_ocr_calls = 0
    with LocalFixtureVault(vault_root) as vault:
        while True:
            lease = service.claim(context, _WORKER_ID)
            if lease is None:
                break
            execution = service.load_execution(context, lease)
            evidence = []
            if execution.deferred_document is None:
                with open_verified_source(
                    vault,
                    execution.vault_object_id,
                    execution.input_object_sha256,
                    execution.input_object_size,
                ) as source:
                    done = len(execution.native_evidence)
                    total = len(execution.routes)
                    for route in execution.ocr_routes:
                        page_evidence = supervisor.execute_page(source, route)
                        evidence.append(page_evidence)
                        local_ocr_calls += 1
                        render_calls += int(route.unit_kind == "PAGE")
                        done += 1
                        service.heartbeat(context, lease, done, total)
            envelope = assemble_run_envelope(execution, tuple(evidence))
            service.finalize(context, lease, envelope)
            processed += 1
        vault_objects = vault.final_count()

    after = acceptance_snapshot(config)
    expected = {
        "smoke": {
            "eligible_plans": 8,
            "runs": 8,
            "visual_units": 110,
            "native_references": 105,
            "local_ocr_routes": 5,
            "deferred_documents": 2,
            "render_calls": 4,
        },
        "full": {
            "eligible_plans": 24,
            "runs": 24,
            "visual_units": 249,
            "native_references": 225,
            "local_ocr_routes": 24,
            "deferred_documents": 2,
            "render_calls": 23,
        },
    }[profile]
    if any(after.get(key) != value for key, value in expected.items()):
        raise F0EError("REPLAY_MISMATCH")
    if (
        after["local_ocr_evidence"] + after["manual_review_required"]
        != after["local_ocr_routes"]
        or after["unique_visual_units"] != after["visual_units"]
        or after["jobs"] != after["eligible_plans"]
        or after["jobs_succeeded"] != after["jobs"]
        or after["f0c_ocr_executed_true"] != 0
        or after["raw_text_persisted_true"] != 0
        or after["page_images_persisted_true"] != 0
        or after["gate_bypasses"] != 0
        or after["negative_gate_violations"] != 0
        or after["route_violations"] != 0
        or vault_objects != int(foundation["blobs"])
    ):
        raise F0EError("REPLAY_MISMATCH")

    result: dict[str, object] = {
        "schema": "f0e-replay-result-v1",
        "status": "LOCAL_FIXTURE_OCR_EVIDENCE_REPLAYED",
        "fixture_label": "FIXTURE_ONLY",
        "benchmark_tier": "NONE",
        "external_processing": "DENY",
        "profile": profile,
        "selected_documents": int(foundation["selected_documents"]),
        **after,
        "processed_this_run": processed,
        "render_calls_this_run": render_calls,
        "local_ocr_calls_this_run": local_ocr_calls,
        "vault_objects": vault_objects,
        "external_calls": 0,
        "raw_text_persisted": False,
        "page_images_persisted": False,
        "delta": {
            key: int(after[key]) - int(before[key]) for key in _DELTA_KEYS
        },
    }
    return result


def _acceptance_resources() -> tuple[str, Path | None]:
    isolation = load_frozen_f0_isolation()
    if isolation is None:
        return ACCEPTANCE_VAULT_ROOT, None
    return str(isolation.tmp_dir / "f0e-vault"), isolation.f0e_runtime_root


def acceptance_snapshot(config: DatabaseConfig) -> dict[str, object]:
    context = authenticate_local_session(config, LOCAL_TENANT_A_TOKEN)
    try:
        with tenant_transaction(config, "f0d_runtime", context) as connection:
            row = connection.execute(
                "SELECT "
                "(SELECT count(*) FROM f0e.local_ocr_configuration) AS configurations,"
                "(SELECT count(*) FROM f0e.local_ocr_run) AS runs,"
                "(SELECT count(*) FROM f0e.page_evidence_selection) AS page_evidence,"
                "(SELECT count(*) FROM f0e.deferred_document_evidence) AS deferred_documents,"
                "(SELECT count(*) FROM f0d.job WHERE kind='EXECUTE_LOCAL_OCR') AS jobs,"
                "(SELECT count(*) FROM f0d.job WHERE kind='EXECUTE_LOCAL_OCR' "
                "AND status='SUCCEEDED') AS jobs_succeeded,"
                "(SELECT count(*) FROM f0d.document_processing_plan p JOIN "
                "f0d.fixture_source_registry r ON r.enterprise_id=p.enterprise_id "
                "AND r.source_document_id=p.source_document_id WHERE "
                "r.document_type IN ('PDF','JPEG','DOC')) AS eligible_plans,"
                "(SELECT count(*) FROM f0e.page_evidence_selection) AS visual_units,"
                "(SELECT count(DISTINCT processing_unit_id) FROM "
                "f0e.page_evidence_selection) AS unique_visual_units,"
                "(SELECT count(*) FROM f0e.page_evidence_selection WHERE "
                "selected_route='NATIVE_REFERENCE') AS native_references,"
                "(SELECT count(*) FROM f0e.page_evidence_selection WHERE "
                "selected_route='LOCAL_OCR') AS local_ocr_routes,"
                "(SELECT count(*) FROM f0e.page_evidence_selection WHERE "
                "terminal_status='LOCAL_OCR_EVIDENCE') AS local_ocr_evidence,"
                "(SELECT count(*) FROM f0e.page_evidence_selection WHERE "
                "terminal_status='MANUAL_REVIEW_REQUIRED') AS manual_review_required,"
                "(SELECT count(*) FROM f0e.page_evidence_selection e JOIN "
                "f0d.document_processing_unit u ON u.enterprise_id=e.enterprise_id "
                "AND u.id=e.processing_unit_id WHERE e.selected_route='LOCAL_OCR' "
                "AND u.unit_kind='PAGE') AS render_calls,"
                "(SELECT count(*) FROM f0d.document_processing_plan WHERE "
                "ocr_executed) AS f0c_ocr_executed_true,"
                "((SELECT count(*) FROM f0e.local_ocr_configuration WHERE "
                "raw_text_persisted)+(SELECT count(*) FROM f0e.local_ocr_run WHERE "
                "raw_text_persisted)+(SELECT count(*) FROM "
                "f0e.page_evidence_selection WHERE raw_text_persisted)+(SELECT "
                "count(*) FROM f0e.deferred_document_evidence WHERE "
                "raw_text_persisted)) AS raw_text_persisted_true,"
                "((SELECT count(*) FROM f0e.local_ocr_configuration WHERE "
                "page_image_persisted)+(SELECT count(*) FROM f0e.local_ocr_run WHERE "
                "page_image_persisted)+(SELECT count(*) FROM "
                "f0e.page_evidence_selection WHERE page_image_persisted)+(SELECT "
                "count(*) FROM f0e.deferred_document_evidence WHERE "
                "page_image_persisted)) AS page_images_persisted_true,"
                "(SELECT count(*) FROM f0d.capability_gate WHERE status<>'CLOSED') "
                "AS gate_bypasses,"
                "(SELECT count(*) FROM f0e.local_ocr_run WHERE source_group='negative' "
                "AND (enterprise_fact_allowed OR current_regulation_allowed OR "
                "search_publish_allowed)) AS negative_gate_violations,"
                "(SELECT count(*) FROM f0e.page_evidence_selection WHERE NOT (("
                "candidate_decision='NATIVE_CANDIDATE' AND "
                "selected_route='NATIVE_REFERENCE') OR ("
                "candidate_decision='FULL_PAGE_OCR_REQUIRED' AND "
                "selected_route='LOCAL_OCR'))) AS route_violations"
            ).fetchone()
            if row is None:
                raise F0EError("REPLAY_MISMATCH")
            run_rows = connection.execute(
                "SELECT processing_plan_id,source_plan_sha256,"
                "local_ocr_configuration_sha256,input_object_sha256,"
                "output_manifest_sha256,evidence_chain_sha256 "
                "FROM f0e.local_ocr_run ORDER BY processing_plan_id"
            ).fetchall()
            page_rows = connection.execute(
                "SELECT processing_unit_id,evidence_chain_sha256 FROM "
                "f0e.page_evidence_selection ORDER BY processing_unit_id"
            ).fetchall()
            deferred_rows = connection.execute(
                "SELECT processing_plan_id,evidence_chain_sha256 FROM "
                "f0e.deferred_document_evidence ORDER BY processing_plan_id"
            ).fetchall()
    except F0EError:
        raise
    except Exception:
        raise F0EError("DATABASE_OPERATION_FAILED") from None
    snapshot = {key: int(value) for key, value in row.items()}
    snapshot["evidence_summary_sha256"] = canonical_sha256(
        {
            "runs": [tuple(str(value) for value in item.values()) for item in run_rows],
            "pages": [tuple(str(value) for value in item.values()) for item in page_rows],
            "deferred": [
                tuple(str(value) for value in item.values()) for item in deferred_rows
            ],
        }
    )
    return snapshot


def _eligible_plan_ids(config: DatabaseConfig) -> tuple[object, ...]:
    context = authenticate_local_session(config, LOCAL_TENANT_A_TOKEN)
    try:
        with tenant_transaction(config, "f0d_runtime", context) as connection:
            rows = connection.execute(
                "SELECT p.id FROM f0d.document_processing_plan p JOIN "
                "f0d.fixture_source_registry r ON r.enterprise_id=p.enterprise_id "
                "AND r.source_document_id=p.source_document_id WHERE "
                "r.document_type IN ('PDF','JPEG','DOC') "
                "ORDER BY p.source_document_id"
            ).fetchall()
        return tuple(row["id"] for row in rows)
    except Exception:
        raise F0EError("DATABASE_OPERATION_FAILED") from None


def verify_second_full(first: Mapping[str, object], second: Mapping[str, object]) -> None:
    if (
        first.get("profile") != "full"
        or second.get("profile") != "full"
        or first.get("evidence_summary_sha256")
        != second.get("evidence_summary_sha256")
        or second.get("processed_this_run") != 0
        or second.get("render_calls_this_run") != 0
        or second.get("local_ocr_calls_this_run") != 0
        or any(int(value) != 0 for value in dict(second.get("delta", {})).values())
    ):
        raise F0EError("REPLAY_MISMATCH")


__all__ = (
    "ACCEPTANCE_VAULT_ROOT",
    "acceptance_snapshot",
    "replay_profile",
    "verify_second_full",
)
