from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
import uuid

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql

from platform_foundation.auth import authenticate_local_session
from platform_foundation.bootstrap import (
    LOCAL_TENANT_A_TOKEN,
    LOCAL_TENANT_B_TOKEN,
    seed_local_foundation,
)
from platform_foundation.database import (
    DatabaseConfig,
    DatabaseError,
    tenant_transaction,
)
from platform_foundation.f0_isolation import load_frozen_f0_isolation


_FROZEN_F0_ISOLATION = load_frozen_f0_isolation()
_PRIVATE_TMP = (
    str(_FROZEN_F0_ISOLATION.tmp_dir)
    if _FROZEN_F0_ISOLATION is not None
    else "/private/tmp"
)
BOOTSTRAP_DSN = (
    _FROZEN_F0_ISOLATION.dsn_for("f0d_bootstrap", "postgres")
    if _FROZEN_F0_ISOLATION is not None
    else "postgresql://f0d_bootstrap:f0d-bootstrap-local-v01@127.0.0.1:55432/postgres"
)


def _f0e_infra() -> Path:
    from platform_foundation.f0_isolation import load_frozen_f0_isolation

    isolation = load_frozen_f0_isolation()
    if isolation is not None:
        return isolation.f0e_runtime_root
    return Path(__file__).resolve().parents[1] / "infra/f0e"


def _f0e_container_prefix() -> str:
    from platform_foundation.f0e.supervisor import _container_prefix

    return _container_prefix("f0e")


def _f0e_test_database_name() -> str:
    if _FROZEN_F0_ISOLATION is not None:
        return _FROZEN_F0_ISOLATION.database_name("f0e-test")
    return f"f0e_test_{uuid.uuid4().hex[:16]}"


def _f0e_database_admin_dsn(database_name: str) -> str:
    if _FROZEN_F0_ISOLATION is not None:
        if database_name != _FROZEN_F0_ISOLATION.database_name("f0e-test"):
            raise AssertionError("unsafe isolated F0E database name")
        return _FROZEN_F0_ISOLATION.dsn_for("f0d_bootstrap", database_name)
    return BOOTSTRAP_DSN.rsplit("/", 1)[0] + "/" + database_name


def _f0e_database_config(database_name: str) -> DatabaseConfig:
    if _FROZEN_F0_ISOLATION is not None:
        if database_name != _FROZEN_F0_ISOLATION.database_name("f0e-test"):
            raise AssertionError("unsafe isolated F0E database name")
        return _FROZEN_F0_ISOLATION.database_config(database_name)
    base = "127.0.0.1:55432/" + database_name
    return DatabaseConfig(
        migration_dsn="postgresql://f0d_migration:f0d-migration-local-v01@" + base,
        runtime_dsn="postgresql://f0d_runtime:f0d-runtime-local-v01@" + base,
        worker_dsn="postgresql://f0d_worker:f0d-worker-local-v01@" + base,
    )


class F0EPublicContractTests(unittest.TestCase):
    def test_public_contract_is_available(self) -> None:
        from platform_foundation.f0e.contracts import (
            DeferredDocumentRoute,
            F0EError,
            NormalizedTextEvidence,
            OcrPageEvidence,
            OcrRunEnvelope,
            PageRoute,
            ProcessingUnitRecord,
            ResourceLimits,
            SandboxProfile,
        )
        from platform_foundation.f0e.routing import (
            build_deferred_route,
            build_page_routes,
        )
        from platform_foundation.f0e.service import LocalOcrService
        from platform_foundation.f0e.supervisor import (
            FixedArgvSandboxSupervisor,
            docker_argv,
        )
        from platform_foundation.f0e.vault_adapter import open_verified_source

        exported = (
            DeferredDocumentRoute,
            F0EError,
            NormalizedTextEvidence,
            OcrPageEvidence,
            OcrRunEnvelope,
            PageRoute,
            ProcessingUnitRecord,
            ResourceLimits,
            SandboxProfile,
            build_deferred_route,
            build_page_routes,
            LocalOcrService,
            FixedArgvSandboxSupervisor,
            docker_argv,
            open_verified_source,
        )
        self.assertEqual(len(exported), 15)


class F0EBodyFreeContractTests(unittest.TestCase):
    def _unit(self, **changes: object) -> object:
        from platform_foundation.f0e.contracts import ProcessingUnitRecord

        values: dict[str, object] = {
            "processing_unit_id": uuid.UUID("10000000-0000-4000-8000-000000000001"),
            "processing_plan_id": uuid.UUID("20000000-0000-4000-8000-000000000001"),
            "source_unit_id": "1" * 64,
            "unit_ordinal": 1,
            "unit_kind": "PAGE",
            "page_no": 1,
            "candidate_decision": "NATIVE_CANDIDATE",
            "reason_codes": ("NATIVE_TEXT_THRESHOLD_MET",),
            "evidence_sha256": "2" * 64,
            "native_text_sha256": "3" * 64,
            "native_characters": 20,
            "bad_character_ppm": 0,
            "rotation": 0,
            "media_box": ("0", "0", "612", "792"),
            "crop_box": ("0", "0", "612", "792"),
            "width_px": None,
            "height_px": None,
        }
        values.update(changes)
        return ProcessingUnitRecord(**values)

    def test_unknown_error_is_redacted_to_fixed_code(self) -> None:
        from platform_foundation.f0e.contracts import F0EError

        error = F0EError("private body should not escape")
        self.assertEqual(str(error), "CONTRACT_INVALID")
        self.assertNotIn("private", repr(error.to_dict()))

    def test_resource_limits_reject_zero_timeout(self) -> None:
        from platform_foundation.f0e.contracts import F0EError, ResourceLimits

        with self.assertRaisesRegex(F0EError, "CONTRACT_INVALID"):
            ResourceLimits(timeout_ms=0)

    def test_resource_limits_fix_concurrency_to_one(self) -> None:
        from platform_foundation.f0e.contracts import F0EError, ResourceLimits

        with self.assertRaisesRegex(F0EError, "CONTRACT_INVALID"):
            ResourceLimits(maximum_processes=2)

    def test_sandbox_profile_rejects_external_processing(self) -> None:
        from platform_foundation.f0e.contracts import F0EError, SandboxProfile

        with self.assertRaisesRegex(F0EError, "CONTRACT_INVALID"):
            SandboxProfile(
                renderer_sha256="1" * 64,
                ocr_engine_sha256="2" * 64,
                language_pack_sha256="3" * 64,
                execution_profile_sha256="4" * 64,
                external_processing_policy="ALLOW",
            )

    def test_pdf_unit_requires_page_geometry(self) -> None:
        from platform_foundation.f0e.contracts import F0EError

        with self.assertRaisesRegex(F0EError, "CONTRACT_INVALID"):
            self._unit(crop_box=None)

    def test_image_unit_requires_dimensions_and_no_pdf_geometry(self) -> None:
        image = self._unit(
            unit_kind="IMAGE",
            candidate_decision="FULL_PAGE_OCR_REQUIRED",
            reason_codes=("IMAGE_INPUT",),
            native_text_sha256=None,
            native_characters=0,
            rotation=None,
            media_box=None,
            crop_box=None,
            width_px=64,
            height_px=48,
        )
        self.assertEqual(image.unit_kind, "IMAGE")

    def test_candidate_maps_to_native_reference(self) -> None:
        from platform_foundation.f0e.routing import build_page_routes

        route = build_page_routes((self._unit(),))[0]
        self.assertEqual(route.evidence_method, "NATIVE_REFERENCE")
        self.assertEqual(route.candidate_decision, "NATIVE_CANDIDATE")

    def test_ocr_candidate_maps_to_local_ocr(self) -> None:
        from platform_foundation.f0e.routing import build_page_routes

        route = build_page_routes(
            (
                self._unit(
                    candidate_decision="FULL_PAGE_OCR_REQUIRED",
                    reason_codes=("LOW_NATIVE_TEXT",),
                    native_characters=19,
                ),
            )
        )[0]
        self.assertEqual(route.evidence_method, "LOCAL_OCR")

    def test_manual_candidate_is_not_silently_ocrd(self) -> None:
        from platform_foundation.f0e.routing import build_page_routes

        route = build_page_routes(
            (
                self._unit(
                    candidate_decision="MANUAL_REVIEW_REQUIRED",
                    reason_codes=("BAD_NATIVE_TEXT_RATIO",),
                ),
            )
        )[0]
        self.assertEqual(route.evidence_method, "MANUAL_REVIEW_REFERENCE")

    def test_route_hash_is_deterministic(self) -> None:
        from platform_foundation.f0e.routing import build_page_routes

        first = build_page_routes((self._unit(),))[0]
        second = build_page_routes((self._unit(),))[0]
        self.assertEqual(first.route_sha256, second.route_sha256)

    def test_duplicate_visual_unit_is_rejected(self) -> None:
        from platform_foundation.f0e.contracts import F0EError
        from platform_foundation.f0e.routing import build_page_routes

        with self.assertRaisesRegex(F0EError, "ROUTE_DUPLICATE"):
            build_page_routes((self._unit(), self._unit()))

    def test_cross_plan_route_set_is_rejected(self) -> None:
        from platform_foundation.f0e.contracts import F0EError
        from platform_foundation.f0e.routing import build_page_routes

        with self.assertRaisesRegex(F0EError, "ROUTE_DUPLICATE"):
            build_page_routes(
                (
                    self._unit(),
                    self._unit(
                        processing_unit_id=uuid.UUID(
                            "10000000-0000-4000-8000-000000000002"
                        ),
                        processing_plan_id=uuid.UUID(
                            "20000000-0000-4000-8000-000000000002"
                        ),
                        source_unit_id="4" * 64,
                        unit_ordinal=2,
                        page_no=2,
                        expected_total_pages=2,
                    ),
                )
            )

    def test_deferred_route_is_stable_and_body_free(self) -> None:
        from platform_foundation.f0e.routing import build_deferred_route

        plan = uuid.UUID("20000000-0000-4000-8000-000000000001")
        version = uuid.UUID("30000000-0000-4000-8000-000000000001")
        first = build_deferred_route(plan, version)
        second = build_deferred_route(plan, version)
        self.assertEqual(first.route_sha256, second.route_sha256)
        self.assertNotIn("path", repr(dataclasses.asdict(first)).lower())

    def test_normalization_is_nfc_and_newline_stable(self) -> None:
        from platform_foundation.f0e.hashing import normalize_text_evidence

        first = normalize_text_evidence("A\u030a\r\n".encode())
        second = normalize_text_evidence("\u00c5\n".encode())
        self.assertEqual(first.text_sha256, second.text_sha256)
        self.assertEqual(first.text_sha256, hashlib.sha256("\u00c5\n".encode()).hexdigest())

    def test_invalid_utf8_never_escapes_as_replacement_text(self) -> None:
        from platform_foundation.f0e.contracts import F0EError
        from platform_foundation.f0e.hashing import normalize_text_evidence

        with self.assertRaisesRegex(F0EError, "RUNNER_OUTPUT_INVALID"):
            normalize_text_evidence(b"\xffsecret")

    def test_body_keys_are_rejected_at_adapter_boundary(self) -> None:
        from platform_foundation.f0e.contracts import F0EError
        from platform_foundation.f0e.hashing import body_free_mapping

        for key in ("body", "content", "dsn", "page_image", "path", "raw_text", "text"):
            with self.subTest(key=key), self.assertRaisesRegex(
                F0EError, "CONTRACT_INVALID"
            ):
                body_free_mapping({key: "canary"})

    def test_binary_values_are_rejected_at_adapter_boundary(self) -> None:
        from platform_foundation.f0e.contracts import F0EError
        from platform_foundation.f0e.hashing import body_free_mapping

        with self.assertRaisesRegex(F0EError, "CONTRACT_INVALID"):
            body_free_mapping({"safe_key": b"body canary"})


class F0EVerifiedSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        from platform_foundation.vault import LocalFixtureVault

        self.temporary = tempfile.TemporaryDirectory(
            prefix="f0e-source-test-", dir=_PRIVATE_TMP
        )
        self.vault = LocalFixtureVault(self.temporary.name)

    def tearDown(self) -> None:
        self.vault.close()
        self.temporary.cleanup()

    def test_verified_source_is_read_only_and_revalidated(self) -> None:
        from platform_foundation.f0e.vault_adapter import open_verified_source

        stored = self.vault.store_bytes(b"opaque fixture input")
        with open_verified_source(
            self.vault, stored.object_id, stored.sha256, stored.size
        ) as source:
            flags = __import__("fcntl").fcntl(source.fileno(), __import__("fcntl").F_GETFL)
            self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
            self.assertEqual(os.pread(source.fileno(), stored.size, 0), b"opaque fixture input")
            source.reverify()

    def test_source_capability_repr_has_no_path_or_body(self) -> None:
        from platform_foundation.f0e.vault_adapter import open_verified_source

        canary = b"body-canary-should-not-appear"
        stored = self.vault.store_bytes(canary)
        with open_verified_source(
            self.vault, stored.object_id, stored.sha256, stored.size
        ) as source:
            rendered = repr(source)
        self.assertNotIn(canary.decode(), rendered)
        self.assertNotIn(self.temporary.name, rendered)

    def test_wrong_source_hash_is_rejected(self) -> None:
        from platform_foundation.f0e.contracts import F0EError
        from platform_foundation.f0e.vault_adapter import open_verified_source

        stored = self.vault.store_bytes(b"fixture")
        with self.assertRaisesRegex(F0EError, "SOURCE_OBJECT_INVALID"):
            with open_verified_source(
                self.vault, stored.object_id, "0" * 64, stored.size
            ):
                self.fail("wrong hash must not open")

    def test_tamper_is_detected_before_capability_yields(self) -> None:
        from platform_foundation.f0e.contracts import F0EError
        from platform_foundation.f0e.vault_adapter import open_verified_source

        stored = self.vault.store_bytes(b"fixture")
        final = Path(self.temporary.name) / "final" / stored.object_id
        final.write_bytes(b"tamper!")
        os.chmod(final, 0o600)
        with self.assertRaisesRegex(F0EError, "SOURCE_OBJECT_INVALID"):
            with open_verified_source(
                self.vault, stored.object_id, stored.sha256, stored.size
            ):
                self.fail("tampered object must not open")

    def test_closed_source_capability_fails_with_fixed_code(self) -> None:
        from platform_foundation.f0e.contracts import F0EError
        from platform_foundation.f0e.vault_adapter import open_verified_source

        stored = self.vault.store_bytes(b"fixture")
        with self.assertRaisesRegex(F0EError, "SOURCE_FD_CLOSED"):
            with open_verified_source(
                self.vault, stored.object_id, stored.sha256, stored.size
            ) as source:
                source.close()


class F0ERuntimeBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        from platform_foundation.f0e.contracts import ProcessingUnitRecord
        from platform_foundation.f0e.routing import build_page_routes
        from platform_foundation.f0e.runtime_config import load_runtime_bundle
        from platform_foundation.vault import LocalFixtureVault

        self.temporary = tempfile.TemporaryDirectory(
            prefix="f0e-runtime-test-", dir=_PRIVATE_TMP
        )
        self.vault = LocalFixtureVault(self.temporary.name)
        self.stored = self.vault.store_bytes(b"synthetic-pdf-source")
        self.bundle = load_runtime_bundle()
        unit = ProcessingUnitRecord(
            processing_unit_id=uuid.UUID("10000000-0000-4000-8000-000000000101"),
            processing_plan_id=uuid.UUID("20000000-0000-4000-8000-000000000101"),
            source_unit_id="a" * 64,
            unit_ordinal=1,
            unit_kind="PAGE",
            page_no=1,
            candidate_decision="FULL_PAGE_OCR_REQUIRED",
            reason_codes=("LOW_NATIVE_TEXT",),
            evidence_sha256="b" * 64,
            native_characters=0,
            bad_character_ppm=0,
            rotation=90,
            media_box=("0.000", "0.000", "612.000", "792.000"),
            crop_box=("12.000", "24.000", "600.000", "768.000"),
            expected_total_pages=1,
        )
        self.route = build_page_routes((unit,))[0]

    def tearDown(self) -> None:
        self.vault.close()
        self.temporary.cleanup()

    def _result(self, **changes: object) -> bytearray:
        value: dict[str, object] = {
            "schema": "f0e-result-v1",
            "status": "SUCCESS",
            "source_unit_id": self.route.source_unit_id,
            "document_type": "PDF",
            "source_sha256": self.stored.sha256,
            "page_no": 1,
            "expected_total_pages": 1,
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
            "profile_sha256": self.bundle.execution_profile_sha256,
            "normalization_rule": "ocr-text-nfc-lf-v1",
            "normalization_rule_sha256": hashlib.sha256(
                b"ocr-text-nfc-lf-v1"
            ).hexdigest(),
            "renderer": {
                "name": "pypdfium2",
                "version": "5.12.1",
                "pdfium_version": "152.0.7947.0",
            },
            "ocr_engine": {
                "name": "rapidocr-onnxruntime",
                "version": "1.4.4",
                "onnxruntime_version": "1.28.0",
                "model_bundle_sha256": self.bundle.language_pack_bundle_sha256,
            },
            "render_origin": "PDFIUM_250_DPI",
            "render_dpi": 250,
            "render_width_px": 100,
            "render_height_px": 100,
            "render_pixel_format": "BGR24",
            "render_sha256": "c" * 64,
            "ocr_char_count": 3,
            "ocr_nonblank_char_count": 3,
            "ocr_block_count": 1,
            "ocr_text_sha256": "d" * 64,
            "confidence_min_ppm": 400_000,
            "confidence_mean_ppm": 500_000,
            "bbox_union_px": [1, 2, 20, 30],
            "bbox_sha256": "e" * 64,
            "bbox_coordinate_space": "RENDERED_PIXEL_TOP_LEFT_V1",
            "decision": "OCR_EVIDENCE_CAPTURED_NOT_VALIDATED",
            "reason_codes": ["OCR_OUTPUT_HASHED", "CONFIDENCE_NOT_CALIBRATED"],
            "temp_residuals": 0,
        }
        value.update(changes)
        return bytearray(
            json.dumps(
                value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            ).encode("ascii")
        )

    def _parse(self, payload: bytearray) -> dict[str, object]:
        from platform_foundation.f0e.supervisor import _parse_result
        from platform_foundation.f0e.vault_adapter import open_verified_source

        with open_verified_source(
            self.vault,
            self.stored.object_id,
            self.stored.sha256,
            self.stored.size,
        ) as source:
            return _parse_result(
                payload,
                source,
                self.route,
                self.bundle.sandbox_profile,
                self.bundle.resource_limits,
            )

    def test_runtime_lock_and_all_control_hashes_are_valid(self) -> None:
        self.assertRegex(self.bundle.container_image_id, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(self.bundle.timeout_seconds, 120)

    def test_fixed_argv_has_no_network_mount_env_or_shell(self) -> None:
        from platform_foundation.f0e.runtime_config import runtime_paths
        from platform_foundation.f0e.supervisor import docker_argv

        argv = docker_argv(*runtime_paths(), self.bundle.container_image_id)
        self.assertIn("none", argv[argv.index("--network") + 1 :])
        self.assertIn("--read-only", argv)
        self.assertNotIn("--volume", argv)
        self.assertNotIn("--env", argv)
        self.assertNotIn("sh", argv)
        self.assertEqual(argv[-1], self.bundle.container_image_id)

    def test_mutable_image_reference_is_rejected(self) -> None:
        from platform_foundation.f0e.contracts import F0EError
        from platform_foundation.f0e.runtime_config import runtime_paths
        from platform_foundation.f0e.supervisor import docker_argv

        with self.assertRaisesRegex(F0EError, "RUNNER_CONFIGURATION_INVALID"):
            docker_argv(*runtime_paths(), "mutable:latest")

    def test_seccomp_explicitly_denies_network_syscalls(self) -> None:
        payload = json.loads(
            (_f0e_infra() / "seccomp.json").read_text()
        )
        denied = {
            name
            for rule in payload["syscalls"]
            if rule["action"] != "SCMP_ACT_ALLOW"
            for name in rule["names"]
        }
        self.assertTrue({"socket", "connect", "accept", "bind"}.issubset(denied))

    def test_strict_result_accepts_valid_body_free_evidence(self) -> None:
        value = self._parse(self._result())
        self.assertEqual(value["external_calls"], 0)
        self.assertFalse(value["raw_text_persisted"])

    def test_strict_result_rejects_extra_body_key(self) -> None:
        from platform_foundation.f0e.contracts import F0EError

        value = json.loads(self._result())
        value["raw_text"] = "canary"
        with self.assertRaisesRegex(F0EError, "RUNNER_OUTPUT_INVALID"):
            self._parse(bytearray(json.dumps(value).encode("ascii")))

    def test_engine_version_tamper_is_rejected(self) -> None:
        from platform_foundation.f0e.contracts import F0EError

        engine = {
            "name": "rapidocr-onnxruntime",
            "version": "9.9.9",
            "onnxruntime_version": "1.28.0",
            "model_bundle_sha256": self.bundle.language_pack_bundle_sha256,
        }
        with self.assertRaisesRegex(F0EError, "RUNNER_OUTPUT_INVALID"):
            self._parse(self._result(ocr_engine=engine))

    def test_model_hash_tamper_is_rejected(self) -> None:
        from platform_foundation.f0e.contracts import F0EError

        engine = {
            "name": "rapidocr-onnxruntime",
            "version": "1.4.4",
            "onnxruntime_version": "1.28.0",
            "model_bundle_sha256": "0" * 64,
        }
        with self.assertRaisesRegex(F0EError, "RUNNER_OUTPUT_INVALID"):
            self._parse(self._result(ocr_engine=engine))

    def test_profile_hash_tamper_is_rejected(self) -> None:
        from platform_foundation.f0e.contracts import F0EError

        with self.assertRaisesRegex(F0EError, "RUNNER_OUTPUT_INVALID"):
            self._parse(self._result(profile_sha256="0" * 64))

    def test_wrong_page_identity_is_rejected(self) -> None:
        from platform_foundation.f0e.contracts import F0EError

        with self.assertRaisesRegex(F0EError, "RUNNER_OUTPUT_INVALID"):
            self._parse(self._result(source_unit_id="0" * 64))

    def test_invalid_output_hash_is_rejected(self) -> None:
        from platform_foundation.f0e.contracts import F0EError

        with self.assertRaisesRegex(F0EError, "RUNNER_OUTPUT_INVALID"):
            self._parse(self._result(ocr_text_sha256="not-a-digest"))

    def test_oversized_render_pixels_are_rejected(self) -> None:
        from platform_foundation.f0e.contracts import F0EError

        with self.assertRaisesRegex(F0EError, "RUNNER_OUTPUT_INVALID"):
            self._parse(
                self._result(render_width_px=5000, render_height_px=5000)
            )

    def test_out_of_bounds_bbox_is_rejected(self) -> None:
        from platform_foundation.f0e.contracts import F0EError

        with self.assertRaisesRegex(F0EError, "RUNNER_OUTPUT_INVALID"):
            self._parse(self._result(bbox_union_px=[1, 2, 101, 30]))

    def test_inverted_confidence_range_is_rejected(self) -> None:
        from platform_foundation.f0e.contracts import F0EError

        with self.assertRaisesRegex(F0EError, "RUNNER_OUTPUT_INVALID"):
            self._parse(
                self._result(confidence_min_ppm=600_000, confidence_mean_ppm=500_000)
            )

    def test_whitespace_only_output_routes_to_manual_review(self) -> None:
        from platform_foundation.f0e.supervisor import _page_evidence

        result = self._parse(
            self._result(
                ocr_nonblank_char_count=0,
                confidence_min_ppm=None,
                confidence_mean_ppm=None,
                decision="MANUAL_REVIEW_REQUIRED",
                reason_codes=["EMPTY_OCR_OUTPUT"],
            )
        )
        evidence = _page_evidence(result, self.route, self.bundle.sandbox_profile)
        self.assertEqual(evidence.terminal_status, "MANUAL_REVIEW_REQUIRED")
        self.assertEqual(evidence.output_character_count, 3)

    def test_nonblank_output_cannot_claim_empty_decision(self) -> None:
        from platform_foundation.f0e.contracts import F0EError

        with self.assertRaisesRegex(F0EError, "RUNNER_OUTPUT_INVALID"):
            self._parse(
                self._result(
                    confidence_min_ppm=None,
                    confidence_mean_ppm=None,
                    decision="MANUAL_REVIEW_REQUIRED",
                    reason_codes=["EMPTY_OCR_OUTPUT"],
                )
            )

    def test_request_header_preserves_cropbox_and_rotation(self) -> None:
        from platform_foundation.f0e.supervisor import _request_header
        from platform_foundation.f0e.vault_adapter import open_verified_source

        with open_verified_source(
            self.vault,
            self.stored.object_id,
            self.stored.sha256,
            self.stored.size,
        ) as source:
            header = _request_header(source, self.route)
        self.assertEqual(header["rotation_degrees"], 90)
        self.assertEqual(header["crop_box"]["left"], "12.000")

    def test_global_concurrency_gate_rejects_second_supervisor(self) -> None:
        from platform_foundation.f0e.contracts import F0EError
        from platform_foundation.f0e.runtime_config import runtime_paths
        from platform_foundation.f0e.supervisor import (
            FixedArgvSandboxSupervisor,
            _EXECUTION_LOCK,
            docker_argv,
        )
        from platform_foundation.f0e.vault_adapter import open_verified_source

        supervisor = FixedArgvSandboxSupervisor(
            docker_argv(*runtime_paths(), self.bundle.container_image_id),
            self.bundle.sandbox_profile,
            self.bundle.resource_limits,
        )
        self.assertTrue(_EXECUTION_LOCK.acquire(blocking=False))
        try:
            with open_verified_source(
                self.vault,
                self.stored.object_id,
                self.stored.sha256,
                self.stored.size,
            ) as source, self.assertRaisesRegex(
                F0EError, "RUNNER_INVOCATION_DENIED"
            ):
                supervisor.execute_page(source, self.route)
        finally:
            _EXECUTION_LOCK.release()


class F0ERealRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        probe = runpy.run_path(
            str(
                _f0e_infra() / "synthetic_probe.py"
            )
        )
        cls.pdf = probe["_minimal_pdf"]()

    def _run(self, timeout_ms: int) -> object:
        from platform_foundation.f0e.contracts import ProcessingUnitRecord, ResourceLimits
        from platform_foundation.f0e.routing import build_page_routes
        from platform_foundation.f0e.runtime_config import load_runtime_bundle, runtime_paths
        from platform_foundation.f0e.supervisor import FixedArgvSandboxSupervisor, docker_argv
        from platform_foundation.f0e.vault_adapter import open_verified_source
        from platform_foundation.vault import LocalFixtureVault

        bundle = load_runtime_bundle()
        route = build_page_routes(
            (
                ProcessingUnitRecord(
                    processing_unit_id=uuid.uuid4(),
                    processing_plan_id=uuid.uuid4(),
                    source_unit_id="f" * 64,
                    unit_ordinal=1,
                    unit_kind="PAGE",
                    page_no=1,
                    candidate_decision="FULL_PAGE_OCR_REQUIRED",
                    reason_codes=("LOW_NATIVE_TEXT",),
                    evidence_sha256="e" * 64,
                    rotation=0,
                    media_box=("0.000", "0.000", "612.000", "792.000"),
                    crop_box=("0.000", "0.000", "612.000", "792.000"),
                ),
            )
        )[0]
        limits = dataclasses.replace(bundle.resource_limits, timeout_ms=timeout_ms)
        supervisor = FixedArgvSandboxSupervisor(
            docker_argv(*runtime_paths(), bundle.container_image_id),
            bundle.sandbox_profile,
            limits,
        )
        with tempfile.TemporaryDirectory(
            prefix="f0e-real-runner-", dir=_PRIVATE_TMP
        ) as root, LocalFixtureVault(root) as vault:
            stored = vault.store_bytes(self.pdf)
            with open_verified_source(
                vault, stored.object_id, stored.sha256, stored.size
            ) as source:
                return supervisor.execute_page(source, route)

    def test_real_digest_runner_returns_body_free_evidence(self) -> None:
        evidence = self._run(120_000)
        self.assertEqual(evidence.terminal_status, "LOCAL_OCR_EVIDENCE")
        self.assertFalse(evidence.raw_text_persisted)
        self.assertNotIn("CANARY", repr(evidence))

    def test_real_timeout_force_kills_without_container_residue(self) -> None:
        from platform_foundation.f0e.contracts import F0EError

        with self.assertRaisesRegex(F0EError, "RUNNER_TIMEOUT|RUNNER_FAILED"):
            self._run(1)
        result = subprocess.run(
            (
                "/usr/local/bin/docker",
                "ps",
                "-a",
                "--filter",
                "name=^/" + _f0e_container_prefix(),
                "--format",
                "{{.ID}}",
            ),
            check=False,
            capture_output=True,
            timeout=15,
        )
        self.assertEqual((result.returncode, result.stdout.strip()), (0, b""))


class F0EReplayCoverageTests(unittest.TestCase):
    def _native(self) -> tuple[object, object]:
        from platform_foundation.f0e.contracts import ProcessingUnitRecord
        from platform_foundation.f0e.routing import (
            build_page_routes,
            native_reference_evidence,
        )
        from platform_foundation.f0e.runtime_config import load_runtime_bundle

        unit = ProcessingUnitRecord(
            processing_unit_id=uuid.uuid4(),
            processing_plan_id=uuid.uuid4(),
            source_unit_id="9" * 64,
            unit_ordinal=1,
            unit_kind="PAGE",
            page_no=1,
            candidate_decision="NATIVE_CANDIDATE",
            reason_codes=("NATIVE_TEXT_THRESHOLD_MET",),
            evidence_sha256="8" * 64,
            native_text_sha256="7" * 64,
            native_characters=20,
            rotation=0,
            media_box=("0", "0", "10", "10"),
            crop_box=("0", "0", "10", "10"),
        )
        route = build_page_routes((unit,))[0]
        evidence = native_reference_evidence(
            route, load_runtime_bundle().sandbox_profile
        )
        return route, evidence

    def test_custom_profile_cannot_pass_without_terminal_evidence(self) -> None:
        from platform_foundation.f0e.contracts import F0EError
        from platform_foundation.f0e.replay import aggregate_replay, verify_replay

        route, _ = self._native()
        aggregate = aggregate_replay((route,), ())
        with self.assertRaisesRegex(F0EError, "REPLAY_MISMATCH"):
            verify_replay(
                aggregate,
                {
                    "processing_plans": 1,
                    "visual_units": 1,
                    "native_references": 1,
                    "local_ocr_routes": 0,
                    "manual_review_source_routes": 0,
                    "deferred_documents": 0,
                },
            )

    def test_exact_terminal_coverage_passes_custom_profile(self) -> None:
        from platform_foundation.f0e.replay import aggregate_replay, verify_replay

        route, evidence = self._native()
        aggregate = aggregate_replay((route,), (), (evidence,))
        self.assertIs(
            verify_replay(
                aggregate,
                {
                    "visual_units": 1,
                    "native_references": 1,
                    "local_ocr_routes": 0,
                },
            ),
            aggregate,
        )

    def test_duplicate_terminal_evidence_is_rejected(self) -> None:
        from platform_foundation.f0e.contracts import F0EError
        from platform_foundation.f0e.replay import aggregate_replay

        route, evidence = self._native()
        with self.assertRaisesRegex(F0EError, "REPLAY_MISMATCH"):
            aggregate_replay((route,), (), (evidence, evidence))


class F0EDatabaseMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_name = _f0e_test_database_name()
        admin = psycopg.connect(BOOTSTRAP_DSN, autocommit=True)
        try:
            admin.execute(
                sql.SQL("CREATE DATABASE {} OWNER f0d_migration TEMPLATE template0").format(
                    sql.Identifier(cls.database_name)
                )
            )
        finally:
            admin.close()
        cls.database_admin_dsn = _f0e_database_admin_dsn(cls.database_name)
        admin = psycopg.connect(cls.database_admin_dsn, autocommit=True)
        try:
            admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
            admin.execute("REVOKE ALL ON DATABASE " + cls.database_name + " FROM PUBLIC")
            admin.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO f0d_runtime, f0d_worker").format(
                    sql.Identifier(cls.database_name)
                )
            )
            admin.execute("CREATE SCHEMA f0d AUTHORIZATION f0d_migration")
            admin.execute("REVOKE ALL ON SCHEMA f0d FROM PUBLIC")
        finally:
            admin.close()
        cls.config = _f0e_database_config(cls.database_name)
        previous = os.environ.get("F0D_MIGRATION_DSN")
        os.environ["F0D_MIGRATION_DSN"] = cls.config.migration_dsn.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
        try:
            command.upgrade(Config("alembic.ini"), "f0d_0003")
        finally:
            if previous is None:
                os.environ.pop("F0D_MIGRATION_DSN", None)
            else:
                os.environ["F0D_MIGRATION_DSN"] = previous
        seed_local_foundation(cls.config)
        cls.context_a = authenticate_local_session(cls.config, LOCAL_TENANT_A_TOKEN)
        cls.context_b = authenticate_local_session(cls.config, LOCAL_TENANT_B_TOKEN)
        cls.configuration_id = uuid.uuid4()
        with tenant_transaction(cls.config, "f0d_worker", cls.context_a) as connection:
            row = connection.execute(
                "INSERT INTO f0e.local_ocr_configuration("
                "id,enterprise_id,actor_id,renderer_id,renderer_version,"
                "renderer_binary_sha256,ocr_engine_id,ocr_engine_version,"
                "ocr_engine_binary_sha256,language_pack_ids,"
                "language_pack_bundle_sha256,normalization_profile_sha256,"
                "execution_profile_sha256,container_image_id,lock_sha256,"
                "timeout_seconds,coordinate_space_version) VALUES ("
                "%s,%s,%s,'pypdfium2','5.12.1',%s,'rapidocr-onnxruntime',"
                "'1.4.4',%s,'det,rec,cls',%s,%s,%s,%s,%s,120,"
                "'RENDERED_PIXEL_TOP_LEFT_V1') RETURNING configuration_sha256",
                (
                    cls.configuration_id,
                    cls.context_a.enterprise_id,
                    cls.context_a.actor_id,
                    "1" * 64,
                    "2" * 64,
                    "3" * 64,
                    "4" * 64,
                    "5" * 64,
                    "sha256:" + "6" * 64,
                    "7" * 64,
                ),
            ).fetchone()
        cls.configuration_sha256 = str(row["configuration_sha256"])

    @classmethod
    def tearDownClass(cls) -> None:
        admin = psycopg.connect(BOOTSTRAP_DSN, autocommit=True)
        try:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(cls.database_name)
                )
            )
        finally:
            admin.close()

    def test_migration_revision_is_f0d_0003(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            revision = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0]
        self.assertEqual(revision, "f0d_0003")

    def test_frozen_f0d_and_new_f0e_force_rls_sets_are_separate(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            rows = connection.execute(
                "SELECT n.nspname,count(*) FROM pg_class c JOIN pg_namespace n "
                "ON n.oid=c.relnamespace WHERE c.relkind='r' AND c.relrowsecurity "
                "AND c.relforcerowsecurity AND n.nspname IN ('f0d','f0e') "
                "GROUP BY n.nspname ORDER BY n.nspname"
            ).fetchall()
        self.assertEqual(rows, [("f0d", 14), ("f0e", 4)])

    def test_public_has_no_f0e_schema_table_or_function_privileges(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            value = connection.execute(
                "SELECT has_schema_privilege('public','f0e','USAGE')::int + "
                "has_table_privilege('public','f0e.local_ocr_configuration','SELECT')::int + "
                "has_function_privilege('public',"
                "'f0e.finalize_local_ocr_run(uuid,bigint,uuid,uuid,uuid,jsonb,uuid)',"
                "'EXECUTE')::int"
            ).fetchone()[0]
        self.assertEqual(value, 0)

    def test_missing_session_context_denies_configuration_rows(self) -> None:
        with psycopg.connect(self.config.runtime_dsn) as connection:
            count = connection.execute(
                "SELECT count(*) FROM f0e.local_ocr_configuration"
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_tenant_b_cannot_read_tenant_a_configuration(self) -> None:
        with tenant_transaction(self.config, "f0d_runtime", self.context_a) as connection:
            own = connection.execute(
                "SELECT count(*) AS count FROM f0e.local_ocr_configuration"
            ).fetchone()["count"]
        with tenant_transaction(self.config, "f0d_runtime", self.context_b) as connection:
            foreign = connection.execute(
                "SELECT count(*) AS count FROM f0e.local_ocr_configuration"
            ).fetchone()["count"]
        self.assertEqual((own, foreign), (1, 0))

    def test_configuration_is_immutable(self) -> None:
        with self.assertRaises(DatabaseError):
            with tenant_transaction(
                self.config, "f0d_worker", self.context_a
            ) as connection:
                connection.execute(
                    "UPDATE f0e.local_ocr_configuration SET timeout_seconds=121 "
                    "WHERE id=%s",
                    (self.configuration_id,),
                )

    def test_runtime_role_cannot_insert_configuration(self) -> None:
        with self.assertRaises(DatabaseError):
            with tenant_transaction(
                self.config, "f0d_runtime", self.context_a
            ) as connection:
                connection.execute(
                    "INSERT INTO f0e.local_ocr_configuration("
                    "id,enterprise_id,actor_id,renderer_id,renderer_version,"
                    "renderer_binary_sha256,ocr_engine_id,ocr_engine_version,"
                    "ocr_engine_binary_sha256,language_pack_ids,"
                    "language_pack_bundle_sha256,normalization_profile_sha256,"
                    "execution_profile_sha256,container_image_id,lock_sha256,"
                    "timeout_seconds,coordinate_space_version) SELECT "
                    "%s,enterprise_id,actor_id,renderer_id,renderer_version,"
                    "renderer_binary_sha256,ocr_engine_id,ocr_engine_version,"
                    "ocr_engine_binary_sha256,language_pack_ids,"
                    "language_pack_bundle_sha256,normalization_profile_sha256,"
                    "execution_profile_sha256,container_image_id,lock_sha256,"
                    "timeout_seconds,coordinate_space_version FROM "
                    "f0e.local_ocr_configuration WHERE id=%s",
                    (uuid.uuid4(), self.configuration_id),
                )

    def test_worker_cannot_spoof_configuration_actor(self) -> None:
        with self.assertRaises(DatabaseError):
            with tenant_transaction(
                self.config, "f0d_worker", self.context_a
            ) as connection:
                connection.execute(
                    "INSERT INTO f0e.local_ocr_configuration("
                    "id,enterprise_id,actor_id,renderer_id,renderer_version,"
                    "renderer_binary_sha256,ocr_engine_id,ocr_engine_version,"
                    "ocr_engine_binary_sha256,language_pack_ids,"
                    "language_pack_bundle_sha256,normalization_profile_sha256,"
                    "execution_profile_sha256,container_image_id,lock_sha256,"
                    "timeout_seconds,coordinate_space_version) SELECT "
                    "%s,enterprise_id,%s,renderer_id,renderer_version,"
                    "renderer_binary_sha256,ocr_engine_id,ocr_engine_version,"
                    "ocr_engine_binary_sha256,language_pack_ids,"
                    "language_pack_bundle_sha256,normalization_profile_sha256,"
                    "execution_profile_sha256,container_image_id,lock_sha256,"
                    "timeout_seconds,coordinate_space_version FROM "
                    "f0e.local_ocr_configuration WHERE id=%s",
                    (uuid.uuid4(), self.context_b.actor_id, self.configuration_id),
                )

    def test_worker_cannot_delete_or_truncate_evidence_tables(self) -> None:
        with self.assertRaises(DatabaseError):
            with tenant_transaction(
                self.config, "f0d_worker", self.context_a
            ) as connection:
                connection.execute("DELETE FROM f0e.local_ocr_configuration")
        with self.assertRaises(DatabaseError):
            with tenant_transaction(
                self.config, "f0d_worker", self.context_a
            ) as connection:
                connection.execute("TRUNCATE f0e.page_evidence_selection")

    def test_persistence_schema_has_no_body_path_or_image_payload_columns(self) -> None:
        forbidden = {
            "body",
            "content",
            "dsn",
            "page_image",
            "path",
            "raw_text",
            "source_path",
            "text",
        }
        with psycopg.connect(self.config.migration_dsn) as connection:
            rows = connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='f0e'"
            ).fetchall()
        self.assertTrue(forbidden.isdisjoint({row[0] for row in rows}))

    def test_configuration_rejects_mutable_image_tag(self) -> None:
        with self.assertRaises(DatabaseError):
            with tenant_transaction(
                self.config, "f0d_worker", self.context_a
            ) as connection:
                connection.execute(
                    "INSERT INTO f0e.local_ocr_configuration("
                    "id,enterprise_id,actor_id,renderer_id,renderer_version,"
                    "renderer_binary_sha256,ocr_engine_id,ocr_engine_version,"
                    "ocr_engine_binary_sha256,language_pack_ids,"
                    "language_pack_bundle_sha256,normalization_profile_sha256,"
                    "execution_profile_sha256,container_image_id,lock_sha256,"
                    "timeout_seconds,coordinate_space_version) SELECT "
                    "%s,enterprise_id,actor_id,renderer_id,renderer_version,"
                    "renderer_binary_sha256,ocr_engine_id,ocr_engine_version,"
                    "ocr_engine_binary_sha256,language_pack_ids,"
                    "language_pack_bundle_sha256,normalization_profile_sha256,"
                    "execution_profile_sha256,'mutable:latest',lock_sha256,"
                    "timeout_seconds,coordinate_space_version FROM "
                    "f0e.local_ocr_configuration WHERE id=%s",
                    (uuid.uuid4(), self.configuration_id),
                )


if __name__ == "__main__":
    unittest.main()
