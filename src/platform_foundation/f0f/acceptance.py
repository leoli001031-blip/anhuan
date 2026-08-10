"""Fresh-database F0-F replay with encrypted bodies and pending annotations."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from ..auth import authenticate_local_session
from ..bootstrap import LOCAL_TENANT_A_TOKEN
from ..database import DatabaseConfig, tenant_transaction
from ..f0_isolation import FrozenF0Isolation, load_frozen_f0_isolation
from ..replay import replay_profile as replay_f0d_profile
from ..vault import LocalFixtureVault
from ..f0e.acceptance import acceptance_snapshot as f0e_snapshot
from ..f0e.hashing import canonical_sha256
from ..f0e.replay import assemble_run_envelope
from ..f0e.runtime_config import (
    load_runtime_bundle as load_f0e_runtime_bundle,
    register_runtime_configuration as register_f0e_runtime_configuration,
    runtime_paths as f0e_runtime_paths,
)
from ..f0e.service import LocalOcrService
from ..f0e.supervisor import FixedArgvSandboxSupervisor, docker_argv as f0e_docker_argv
from ..f0e.vault_adapter import open_verified_source
from .contracts import BoundPageBody, F0FError
from .keyfile import create_keyfile, load_keyfile
from .native import extract_native_page
from .runtime_config import load_runtime_bundle, runtime_paths
from .service import ControlledBodyService, bind_native_body, bind_ocr_body
from .supervisor import ControlledBodySupervisor, body_docker_argv


ACCEPTANCE_VAULT_ROOT = "/private/tmp/anhuan-f0f-acceptance-v01"
ACCEPTANCE_KEY_FILE = "/private/tmp/anhuan-f0f-acceptance-v01.key"
_F0E_WORKER = "f0f-base-ocr-worker-v01"
_F0F_WORKER = "f0f-body-worker-v01"
_DELTA_KEYS = (
    "configurations",
    "body_evidence",
    "body_jobs",
    "body_jobs_succeeded",
    "annotation_queue",
    "gold_labels",
    "gold_adjudications",
)
_EXPECTED_DOCUMENTS = {"smoke": 10, "full": 26}


def replay_profile(config: DatabaseConfig, profile: str) -> dict[str, object]:
    if profile not in {"smoke", "full"}:
        raise F0FError("BODY_REPLAY_MISMATCH")
    vault_root, key_file, runtime_root, f0e_runtime_root, isolation = (
        _acceptance_resources()
    )
    if Path(vault_root).is_symlink():
        raise F0FError("BODY_REPLAY_MISMATCH")

    foundation = replay_f0d_profile(
        config, profile, vault_root=vault_root
    )
    if foundation.get("selected_documents") != _EXPECTED_DOCUMENTS[profile]:
        raise F0FError("BODY_REPLAY_MISMATCH")
    base_replay = _replay_f0e(config)
    base = f0e_snapshot(config)
    before = acceptance_snapshot(config)
    context = authenticate_local_session(config, LOCAL_TENANT_A_TOKEN)
    runtime = load_runtime_bundle(runtime_root, f0e_root=f0e_runtime_root)
    _ensure_keyfile(key_file, isolation)
    service = ControlledBodyService(config)
    processed = 0
    ocr_body_calls = 0
    native_body_calls = 0
    with load_keyfile(key_file) as key:
        configuration = service.register_configuration(context, runtime, key)
        service.enqueue_all(context, configuration)
        docker, seccomp = runtime_paths(runtime_root)
        supervisor = ControlledBodySupervisor(
            body_docker_argv(docker, seccomp, runtime.container_image_id),
            runtime.container_image_id,
            runtime.execution_profile_sha256,
            runtime.base_sandbox_profile,
            runtime.resource_limits,
        )
        with LocalFixtureVault(vault_root) as vault:
            while True:
                lease = service.claim(context, _F0F_WORKER)
                if lease is None:
                    break
                execution = service.load_execution(context, lease)
                bodies: dict[object, BoundPageBody] = {}
                try:
                    with open_verified_source(
                        vault,
                        execution.vault_object_id,
                        execution.input_object_sha256,
                        execution.input_object_size,
                    ) as source:
                        for done, page in enumerate(execution.pages, start=1):
                            if page.route.evidence_method == "NATIVE_REFERENCE":
                                body = extract_native_page(source, page.route)
                                bound = bind_native_body(page, body)
                                native_body_calls += 1
                            else:
                                result = supervisor.execute_page(
                                    source, page.route, page.expected_evidence
                                )
                                bound = bind_ocr_body(page, result)
                                ocr_body_calls += 1
                            bodies[page.page_evidence_id] = bound
                            service.heartbeat(
                                context, lease, done, len(execution.pages)
                            )
                    service.finalize(context, execution, bodies, key)  # type: ignore[arg-type]
                finally:
                    for body in bodies.values():
                        body.wipe()
                processed += 1
            vault_objects = vault.final_count()
        if profile == "full":
            service.enqueue_annotation_candidates(context)
        decrypted = _verify_all_decryptions(config, context, service, key)

    after = acceptance_snapshot(config)
    if (
        after["body_evidence"] != base["visual_units"]
        or after["unique_visual_units"] != after["body_evidence"]
        or after["native_bodies"] != base["native_references"]
        or after["ocr_bodies"] != base["local_ocr_routes"]
        or after["body_jobs"] != base["runs"] - base["deferred_documents"]
        or after["body_jobs_succeeded"] != after["body_jobs"]
        or after["ciphertexts_below_minimum"] != 0
        or after["plaintext_columns"] != 0
        or after["gold_labels"] != 0
        or after["gold_adjudications"] != 0
        or after["gate_bypasses"] != 0
        or after["negative_queue_entries"] != 0
        or after["page_images_persisted_true"] != 0
        or after["body_evidence"] != decrypted
        or vault_objects != int(foundation["blobs"])
    ):
        raise F0FError("BODY_REPLAY_MISMATCH")
    if profile == "full" and (
        after["annotation_queue"] != 15
        or after["annotation_ocr"] != 10
        or after["annotation_native"] != 5
        or after["annotation_ocr_documents"] != 7
        or after["annotation_native_documents"] != 5
    ):
        raise F0FError("BODY_REPLAY_MISMATCH")

    return {
        "schema": "f0f-replay-result-v1",
        "status": "LOCAL_FIXTURE_CONTROLLED_BODY_REPLAYED",
        "fixture_label": "FIXTURE_ONLY",
        "benchmark_tier": "NONE",
        "gold_status": "ANNOTATION_PENDING",
        "production_allowed": False,
        "external_processing": "DENY",
        "profile": profile,
        "selected_documents": int(foundation["selected_documents"]),
        **after,
        "processed_this_run": processed,
        "base_f0e_processed_this_run": base_replay["processed"],
        "base_f0e_ocr_calls_this_run": base_replay["ocr_calls"],
        "native_body_calls_this_run": native_body_calls,
        "ocr_body_calls_this_run": ocr_body_calls,
        "decrypted_bodies_verified": decrypted,
        "vault_objects": vault_objects,
        "external_calls": 0,
        "page_images_persisted": False,
        "delta": {key: int(after[key]) - int(before[key]) for key in _DELTA_KEYS},
    }


def _replay_f0e(config: DatabaseConfig) -> dict[str, int]:
    vault_root, _key_file, _runtime_root, f0e_runtime_root, _isolation = (
        _acceptance_resources()
    )
    context = authenticate_local_session(config, LOCAL_TENANT_A_TOKEN)
    bundle = load_f0e_runtime_bundle(f0e_runtime_root)
    configuration = register_f0e_runtime_configuration(config, context, bundle)
    service = LocalOcrService(config)
    processed = 0
    ocr_calls = 0
    try:
        with tenant_transaction(config, "f0d_runtime", context) as connection:
            plan_rows = connection.execute(
                "SELECT p.id FROM f0d.document_processing_plan p JOIN "
                "f0d.fixture_source_registry r ON r.enterprise_id=p.enterprise_id "
                "AND r.source_document_id=p.source_document_id WHERE "
                "r.document_type IN ('PDF','JPEG','DOC') ORDER BY p.source_document_id"
            ).fetchall()
        for row in plan_rows:
            service.enqueue(context, row["id"], configuration.configuration_id)
        docker, seccomp = f0e_runtime_paths(f0e_runtime_root)
        supervisor = FixedArgvSandboxSupervisor(
            f0e_docker_argv(docker, seccomp, bundle.container_image_id),
            bundle.sandbox_profile,
            bundle.resource_limits,
        )
        with LocalFixtureVault(vault_root) as vault:
            while True:
                lease = service.claim(context, _F0E_WORKER)
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
                        for route in execution.ocr_routes:
                            evidence.append(supervisor.execute_page(source, route))
                            ocr_calls += 1
                            done += 1
                            service.heartbeat(
                                context, lease, done, len(execution.routes)
                            )
                service.finalize(
                    context, lease, assemble_run_envelope(execution, tuple(evidence))
                )
                processed += 1
        return {"processed": processed, "ocr_calls": ocr_calls}
    except F0FError:
        raise
    except Exception:
        raise F0FError("BODY_REPLAY_MISMATCH") from None


def acceptance_snapshot(config: DatabaseConfig) -> dict[str, object]:
    context = authenticate_local_session(config, LOCAL_TENANT_A_TOKEN)
    try:
        with tenant_transaction(config, "f0d_runtime", context) as connection:
            row = connection.execute(
                "SELECT "
                "(SELECT count(*) FROM f0d.fixture_source_registry) AS registered_sources,"
                "(SELECT count(*) FROM f0d.document_version) AS document_versions,"
                "(SELECT count(*) FROM f0d.document_processing_plan) AS processing_plans,"
                "(SELECT count(*) FROM f0e.local_ocr_run) AS f0e_runs,"
                "(SELECT count(*) FROM f0e.deferred_document_evidence) AS f0e_deferred,"
                "(SELECT count(*) FROM f0e.page_evidence_selection) AS f0e_visual_units,"
                "(SELECT count(*) FROM f0e.page_evidence_selection WHERE "
                "selected_route='NATIVE_REFERENCE') AS f0e_native,"
                "(SELECT count(*) FROM f0e.page_evidence_selection WHERE "
                "selected_route='LOCAL_OCR') AS f0e_ocr,"
                "(SELECT count(*) FROM f0f.body_configuration) AS configurations,"
                "(SELECT count(*) FROM f0f.page_body_evidence) AS body_evidence,"
                "(SELECT count(DISTINCT processing_unit_id) FROM f0f.page_body_evidence) "
                "AS unique_visual_units,"
                "(SELECT count(*) FROM f0f.page_body_evidence WHERE "
                "selected_route='NATIVE_REFERENCE') AS native_bodies,"
                "(SELECT count(*) FROM f0f.page_body_evidence WHERE "
                "selected_route='LOCAL_OCR') AS ocr_bodies,"
                "(SELECT count(*) FROM f0d.job WHERE kind='CAPTURE_CONTROLLED_BODY') "
                "AS body_jobs,"
                "(SELECT count(*) FROM f0d.job WHERE kind='CAPTURE_CONTROLLED_BODY' "
                "AND status='SUCCEEDED') AS body_jobs_succeeded,"
                "(SELECT count(*) FROM f0f.gold_annotation_queue) AS annotation_queue,"
                "(SELECT count(*) FROM f0f.gold_annotation_queue q JOIN "
                "f0f.page_body_evidence b ON b.enterprise_id=q.enterprise_id "
                "AND b.id=q.page_body_evidence_id WHERE b.selected_route='LOCAL_OCR') "
                "AS annotation_ocr,"
                "(SELECT count(*) FROM f0f.gold_annotation_queue q JOIN "
                "f0f.page_body_evidence b ON b.enterprise_id=q.enterprise_id "
                "AND b.id=q.page_body_evidence_id WHERE b.selected_route='NATIVE_REFERENCE') "
                "AS annotation_native,"
                "(SELECT count(DISTINCT b.source_document_id) FROM f0f.gold_annotation_queue q "
                "JOIN f0f.page_body_evidence b ON b.enterprise_id=q.enterprise_id "
                "AND b.id=q.page_body_evidence_id WHERE b.selected_route='LOCAL_OCR') "
                "AS annotation_ocr_documents,"
                "(SELECT count(DISTINCT b.source_document_id) FROM f0f.gold_annotation_queue q "
                "JOIN f0f.page_body_evidence b ON b.enterprise_id=q.enterprise_id "
                "AND b.id=q.page_body_evidence_id WHERE b.selected_route='NATIVE_REFERENCE') "
                "AS annotation_native_documents,"
                "(SELECT count(*) FROM f0f.gold_label_evidence) AS gold_labels,"
                "(SELECT count(*) FROM f0f.gold_adjudication) AS gold_adjudications,"
                "(SELECT count(*) FROM f0f.page_body_evidence WHERE "
                "octet_length(ciphertext)<32) AS ciphertexts_below_minimum,"
                "(SELECT count(*) FROM information_schema.columns WHERE table_schema='f0f' "
                "AND table_name='page_body_evidence' AND column_name IN "
                "('body','body_text','plaintext','raw_text')) AS plaintext_columns,"
                "(SELECT count(*) FROM f0d.capability_gate WHERE status<>'CLOSED') "
                "AS gate_bypasses,"
                "(SELECT count(*) FROM f0f.gold_annotation_queue q JOIN "
                "f0f.page_body_evidence b ON b.enterprise_id=q.enterprise_id "
                "AND b.id=q.page_body_evidence_id JOIN f0d.fixture_source_registry r "
                "ON r.enterprise_id=b.enterprise_id AND r.source_document_id=b.source_document_id "
                "WHERE r.source_group='negative') AS negative_queue_entries,"
                "((SELECT count(*) FROM f0e.local_ocr_configuration WHERE page_image_persisted)+"
                "(SELECT count(*) FROM f0e.local_ocr_run WHERE page_image_persisted)+"
                "(SELECT count(*) FROM f0e.page_evidence_selection WHERE page_image_persisted)+"
                "(SELECT count(*) FROM f0e.deferred_document_evidence WHERE page_image_persisted)) "
                "AS page_images_persisted_true"
            ).fetchone()
            if row is None:
                raise F0FError("BODY_REPLAY_MISMATCH")
            config_rows = connection.execute(
                "SELECT configuration_sha256,runner_lock_sha256,runner_profile_sha256 "
                "FROM f0f.body_configuration ORDER BY id"
            ).fetchall()
            body_rows = connection.execute(
                "SELECT processing_unit_id,plaintext_sha256,ciphertext_sha256,"
                "body_evidence_chain_sha256 FROM f0f.page_body_evidence "
                "ORDER BY processing_unit_id"
            ).fetchall()
            queue_rows = connection.execute(
                "SELECT id,processing_unit_id,selection_ordinal,body_evidence_chain_sha256 "
                "FROM f0f.gold_annotation_queue ORDER BY selection_ordinal"
            ).fetchall()
        snapshot = {key: int(value) for key, value in row.items()}
        snapshot["evidence_summary_sha256"] = canonical_sha256(
            {
                "configurations": [tuple(str(value) for value in item.values()) for item in config_rows],
                "bodies": [tuple(str(value) for value in item.values()) for item in body_rows],
                "queue": [tuple(str(value) for value in item.values()) for item in queue_rows],
            }
        )
        return snapshot
    except F0FError:
        raise
    except Exception:
        raise F0FError("DATABASE_OPERATION_FAILED") from None


def _verify_all_decryptions(
    config: DatabaseConfig,
    context: object,
    service: ControlledBodyService,
    key: object,
) -> int:
    try:
        with tenant_transaction(config, "f0d_runtime", context) as connection:  # type: ignore[arg-type]
            rows = connection.execute(
                "SELECT id FROM f0f.page_body_evidence ORDER BY processing_unit_id"
            ).fetchall()
        count = 0
        for row in rows:
            body = service.decrypt_verified(context, row["id"], key)  # type: ignore[arg-type]
            body.wipe()
            count += 1
        return count
    except F0FError:
        raise
    except Exception:
        raise F0FError("BODY_REPLAY_MISMATCH") from None


def _acceptance_resources(
) -> tuple[str, str, Path | None, Path | None, FrozenF0Isolation | None]:
    isolation = load_frozen_f0_isolation()
    if isolation is None:
        return ACCEPTANCE_VAULT_ROOT, ACCEPTANCE_KEY_FILE, None, None, None
    return (
        str(isolation.f0f_vault_root),
        str(isolation.f0f_key_file),
        isolation.f0f_runtime_root,
        isolation.f0e_runtime_root,
        isolation,
    )


def _ensure_keyfile(path: str, isolation: FrozenF0Isolation | None) -> None:
    if os.path.lexists(path):
        return
    if isolation is not None:
        raise F0FError("RUNNER_CONFIGURATION_INVALID")
    create_keyfile(path)


def verify_second_full(first: Mapping[str, object], second: Mapping[str, object]) -> None:
    if (
        first.get("profile") != "full"
        or second.get("profile") != "full"
        or first.get("evidence_summary_sha256") != second.get("evidence_summary_sha256")
        or second.get("processed_this_run") != 0
        or second.get("base_f0e_processed_this_run") != 0
        or second.get("base_f0e_ocr_calls_this_run") != 0
        or second.get("native_body_calls_this_run") != 0
        or second.get("ocr_body_calls_this_run") != 0
        or any(int(value) != 0 for value in dict(second.get("delta", {})).values())
    ):
        raise F0FError("BODY_REPLAY_MISMATCH")


__all__ = (
    "ACCEPTANCE_KEY_FILE",
    "ACCEPTANCE_VAULT_ROOT",
    "acceptance_snapshot",
    "replay_profile",
    "verify_second_full",
)
