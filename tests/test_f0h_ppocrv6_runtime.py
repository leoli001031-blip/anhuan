from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import math
import os
from pathlib import Path
import re
import runpy
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _f0h_infra() -> Path:
    from platform_foundation.f0_isolation import load_frozen_f0_isolation

    isolation = load_frozen_f0_isolation()
    return ROOT / "infra/f0h" if isolation is None else isolation.f0h_runtime_root


def _f0h_container_prefix() -> str:
    from platform_foundation.f0h.supervisor import _container_prefix

    return _container_prefix()


INFRA = _f0h_infra()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def _copy_runtime() -> tempfile.TemporaryDirectory[str]:
    from platform_foundation.f0_isolation import load_frozen_f0_isolation

    isolation = load_frozen_f0_isolation()
    temporary = tempfile.TemporaryDirectory(
        prefix="f0h-lock-test-",
        dir=(str(isolation.tmp_dir) if isolation is not None else "/private/tmp"),
    )
    shutil.copytree(INFRA, Path(temporary.name) / "f0h")
    return temporary


def _runtime_copy_scope(root: Path):
    from platform_foundation.f0_isolation import load_frozen_f0_isolation
    from platform_foundation.f0h import runtime_config

    isolation = load_frozen_f0_isolation()
    if isolation is None:
        return contextlib.nullcontext()
    scoped = dataclasses.replace(isolation, f0h_runtime_root=root)
    return mock.patch.object(
        runtime_config, "load_frozen_f0_isolation", return_value=scoped
    )


def _load_runtime_copy(root: Path):
    from platform_foundation.f0h.runtime_config import load_runtime_bundle

    with _runtime_copy_scope(root):
        return load_runtime_bundle(root)


def _runtime_paths_copy(root: Path) -> tuple[str, str]:
    from platform_foundation.f0h.runtime_config import runtime_paths

    with _runtime_copy_scope(root):
        return runtime_paths(root)


class F0HContractTests(unittest.TestCase):
    def test_public_contract_is_importable(self) -> None:
        from platform_foundation.f0h.contracts import (
            F0HError,
            adapt_output_parts,
            canonical_json_bytes,
            summarize_blocks,
            validate_private_result,
        )
        from platform_foundation.f0h.replay import replay_profile, verify_repeat
        from platform_foundation.f0h.runtime_config import (
            load_runtime_bundle,
            runtime_paths,
        )
        from platform_foundation.f0h.supervisor import (
            FixedArgvPpocrV6Supervisor,
            docker_argv,
            run_envelope_for_test,
        )

        exported = (
            F0HError,
            adapt_output_parts,
            canonical_json_bytes,
            summarize_blocks,
            validate_private_result,
            replay_profile,
            verify_repeat,
            load_runtime_bundle,
            runtime_paths,
            FixedArgvPpocrV6Supervisor,
            docker_argv,
            run_envelope_for_test,
        )
        self.assertEqual(len(exported), 12)

    def test_unknown_error_is_redacted(self) -> None:
        from platform_foundation.f0h.contracts import F0HError

        error = F0HError("SYNTHETIC_PRIVATE_BODY_13900000000")
        self.assertEqual(str(error), "CONTRACT_INVALID")
        self.assertNotIn("PRIVATE", repr(error.to_dict()))
        self.assertNotIn("13900000000", repr(error.to_dict()))

    def test_canonical_json_is_ascii_sorted_and_compact(self) -> None:
        from platform_foundation.f0h.contracts import canonical_json_bytes

        self.assertEqual(
            canonical_json_bytes({"z": "中", "a": [2, 1]}),
            b'{"a":[2,1],"z":"\\u4e2d"}',
        )

    def test_canonical_json_rejects_non_finite_float(self) -> None:
        from platform_foundation.f0h.contracts import F0HError, canonical_json_bytes

        with self.assertRaisesRegex(F0HError, "CONTRACT_INVALID"):
            canonical_json_bytes({"score": math.nan})

    def test_output_adapter_preserves_order_and_normalizes_text(self) -> None:
        from platform_foundation.f0h.contracts import adapt_output_parts

        blocks = adapt_output_parts(
            (
                ((1, 2), (8, 2), (8, 9), (1, 9)),
                ((11, 12), (18, 12), (18, 19), (11, 19)),
            ),
            ("e\u0301\r\nX", "SECOND"),
            (0.875001, 0.5),
        )
        self.assertEqual(tuple(block["index"] for block in blocks), (0, 1))
        self.assertEqual(blocks[0]["text"], "é\nX")
        self.assertEqual(blocks[0]["confidence_ppm"], 875_001)

    def test_output_adapter_accepts_empty_rapidocr_parts(self) -> None:
        from platform_foundation.f0h.contracts import adapt_output_parts

        self.assertEqual(adapt_output_parts(None, None, None), ())

    def test_output_adapter_rejects_mismatched_part_lengths(self) -> None:
        from platform_foundation.f0h.contracts import F0HError, adapt_output_parts

        with self.assertRaisesRegex(F0HError, "RUNNER_OUTPUT_INVALID"):
            adapt_output_parts(
                (((0, 0), (1, 0), (1, 1), (0, 1)),),
                ("ONE", "TWO"),
                (0.9,),
            )

    def test_output_adapter_rejects_boolean_coordinate(self) -> None:
        from platform_foundation.f0h.contracts import F0HError, adapt_output_parts

        with self.assertRaisesRegex(F0HError, "RUNNER_OUTPUT_INVALID"):
            adapt_output_parts(
                (((False, 0), (1, 0), (1, 1), (0, 1)),),
                ("ONE",),
                (0.9,),
            )

    def test_output_adapter_rejects_out_of_range_confidence(self) -> None:
        from platform_foundation.f0h.contracts import F0HError, adapt_output_parts

        with self.assertRaisesRegex(F0HError, "RUNNER_OUTPUT_INVALID"):
            adapt_output_parts(
                (((0, 0), (1, 0), (1, 1), (0, 1)),),
                ("ONE",),
                (1.000001,),
            )

    def test_output_adapter_rejects_unpaired_surrogate(self) -> None:
        from platform_foundation.f0h.contracts import F0HError, adapt_output_parts

        with self.assertRaisesRegex(F0HError, "RUNNER_OUTPUT_INVALID"):
            adapt_output_parts(
                (((0, 0), (1, 0), (1, 1), (0, 1)),),
                ("\ud800",),
                (0.9,),
            )

    def test_block_summary_contains_counts_not_body(self) -> None:
        from platform_foundation.f0h.contracts import (
            adapt_output_parts,
            summarize_blocks,
        )

        canary = "SYNTHETIC_BODY_CANARY"
        blocks = adapt_output_parts(
            (((1, 2), (8, 2), (8, 9), (1, 9)),), (canary,), (0.8,)
        )
        summary = summarize_blocks(blocks)
        self.assertEqual(summary["ocr_block_count"], 1)
        self.assertEqual(summary["ocr_char_count"], len(canary))
        self.assertEqual(summary["ocr_nonblank_char_count"], len(canary))
        self.assertEqual(summary["confidence_min_ppm"], 800_000)
        self.assertEqual(summary["confidence_mean_ppm"], 800_000)
        self.assertEqual(summary["bbox_union_px"], [1, 2, 8, 9])
        self.assertNotIn(canary, repr(summary))

    def test_empty_block_summary_is_explicit(self) -> None:
        from platform_foundation.f0h.contracts import summarize_blocks

        summary = summarize_blocks(())
        self.assertEqual(summary["ocr_block_count"], 0)
        self.assertEqual(summary["ocr_char_count"], 0)
        self.assertEqual(summary["ocr_nonblank_char_count"], 0)
        self.assertIsNone(summary["confidence_min_ppm"])
        self.assertIsNone(summary["confidence_mean_ppm"])
        self.assertIsNone(summary["bbox_union_px"])

    def test_block_order_changes_text_sequence_hash(self) -> None:
        from platform_foundation.f0h.contracts import (
            adapt_output_parts,
            summarize_blocks,
        )

        boxes = (
            ((0, 0), (1, 0), (1, 1), (0, 1)),
            ((2, 0), (3, 0), (3, 1), (2, 1)),
        )
        first = summarize_blocks(adapt_output_parts(boxes, ("A", "BC"), (0.8, 0.9)))
        second = summarize_blocks(adapt_output_parts(boxes, ("BC", "A"), (0.8, 0.9)))
        self.assertNotEqual(first["ocr_text_sha256"], second["ocr_text_sha256"])

    def test_block_summary_is_canonical_json_serializable(self) -> None:
        from platform_foundation.f0h.contracts import (
            adapt_output_parts,
            canonical_json_bytes,
            summarize_blocks,
        )

        blocks = adapt_output_parts(
            (((0, 0), (2, 0), (2, 2), (0, 2)),), ("TEXT",), (0.75,)
        )
        encoded = canonical_json_bytes(summarize_blocks(blocks))
        self.assertEqual(encoded, _canonical(json.loads(encoded)))


class F0HRuntimeIdentityTests(unittest.TestCase):
    def test_runtime_lock_loads_exact_ppocrv6_identity(self) -> None:
        from platform_foundation.f0h.runtime_config import load_runtime_bundle

        bundle = load_runtime_bundle()
        self.assertEqual(bundle.rapidocr_version, "3.9.2")
        self.assertEqual(bundle.ocr_family, "PP-OCRv6")
        self.assertEqual(bundle.detector_model, "PP-OCRv6_det_small.onnx")
        self.assertEqual(bundle.recognizer_model, "PP-OCRv6_rec_small.onnx")
        self.assertRegex(bundle.container_image_id, r"^sha256:[0-9a-f]{64}$")

    def test_runtime_identity_is_offline_and_nonproduction(self) -> None:
        from platform_foundation.f0h.runtime_config import load_runtime_bundle

        bundle = load_runtime_bundle()
        self.assertEqual(bundle.network_mode, "none")
        self.assertFalse(bundle.runtime_downloads)
        self.assertFalse(bundle.production_allowed)
        self.assertEqual(bundle.external_processing, "DENY")
        self.assertEqual(bundle.benchmark_tier, "NONE")

    def test_registered_model_hashes_are_exact_sha256(self) -> None:
        from platform_foundation.f0h.runtime_config import load_runtime_bundle

        bundle = load_runtime_bundle()
        self.assertRegex(bundle.detector_model_sha256, r"^[0-9a-f]{64}$")
        self.assertRegex(bundle.recognizer_model_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(
            bundle.detector_model_sha256,
            "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f",
        )
        self.assertEqual(
            bundle.recognizer_model_sha256,
            "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884",
        )

    def test_rapidocr_wheel_hash_is_registered(self) -> None:
        from platform_foundation.f0h.runtime_config import load_runtime_bundle

        bundle = load_runtime_bundle()
        self.assertEqual(
            bundle.rapidocr_wheel_sha256,
            "04d6b8d151f823d930bd91910555f57bea897c0c44fa6794267b94cf9c1ef9a0",
        )

    def test_runtime_lock_tamper_is_rejected_from_private_copy(self) -> None:
        from platform_foundation.f0h.contracts import F0HError

        temporary = _copy_runtime()
        try:
            root = Path(temporary.name) / "f0h"
            _load_runtime_copy(root)
            path = root / "runtime-lock.json"
            payload = json.loads(path.read_bytes())
            payload["container_image_id"] = "sha256:" + "0" * 64
            path.write_bytes(_canonical(payload))
            with self.assertRaisesRegex(F0HError, "RUNNER_CONFIGURATION_INVALID"):
                _load_runtime_copy(root)
        finally:
            temporary.cleanup()

    def test_component_lock_tamper_is_rejected_from_private_copy(self) -> None:
        from platform_foundation.f0h.contracts import F0HError

        temporary = _copy_runtime()
        try:
            root = Path(temporary.name) / "f0h"
            _load_runtime_copy(root)
            path = root / "component-lock.json"
            payload = json.loads(path.read_bytes())
            payload["profile_sha256"] = "0" * 64
            path.write_bytes(_canonical(payload))
            with self.assertRaisesRegex(F0HError, "RUNNER_CONFIGURATION_INVALID"):
                _load_runtime_copy(root)
        finally:
            temporary.cleanup()

    def test_missing_registered_wheel_is_rejected(self) -> None:
        from platform_foundation.f0h.contracts import F0HError

        temporary = _copy_runtime()
        try:
            root = Path(temporary.name) / "f0h"
            _load_runtime_copy(root)
            wheel = next((root / "wheels").glob("rapidocr-3.9.2-*.whl"))
            wheel.unlink()
            with self.assertRaisesRegex(F0HError, "RUNNER_CONFIGURATION_INVALID"):
                _load_runtime_copy(root)
        finally:
            temporary.cleanup()

    def test_missing_detector_model_is_rejected(self) -> None:
        from platform_foundation.f0h.contracts import F0HError

        temporary = _copy_runtime()
        try:
            root = Path(temporary.name) / "f0h"
            _load_runtime_copy(root)
            (root / "models/PP-OCRv6_det_small.onnx").unlink()
            with self.assertRaisesRegex(F0HError, "RUNNER_CONFIGURATION_INVALID"):
                _load_runtime_copy(root)
        finally:
            temporary.cleanup()

    def test_recognizer_model_tamper_is_rejected(self) -> None:
        from platform_foundation.f0h.contracts import F0HError

        temporary = _copy_runtime()
        try:
            root = Path(temporary.name) / "f0h"
            _load_runtime_copy(root)
            model = root / "models/PP-OCRv6_rec_small.onnx"
            with model.open("r+b") as handle:
                original = handle.read(1)
                handle.seek(0)
                handle.write(bytes((original[0] ^ 1,)))
            with self.assertRaisesRegex(F0HError, "RUNNER_CONFIGURATION_INVALID"):
                _load_runtime_copy(root)
        finally:
            temporary.cleanup()

    def test_runtime_lock_symlink_is_rejected(self) -> None:
        from platform_foundation.f0h.contracts import F0HError

        temporary = _copy_runtime()
        try:
            root = Path(temporary.name) / "f0h"
            _load_runtime_copy(root)
            lock = root / "runtime-lock.json"
            target = root / "runtime-lock.target"
            lock.rename(target)
            lock.symlink_to(target.name)
            with self.assertRaisesRegex(F0HError, "RUNNER_CONFIGURATION_INVALID"):
                _load_runtime_copy(root)
        finally:
            temporary.cleanup()

    def test_runtime_lock_hardlink_is_rejected(self) -> None:
        from platform_foundation.f0h.contracts import F0HError

        temporary = _copy_runtime()
        try:
            root = Path(temporary.name) / "f0h"
            _load_runtime_copy(root)
            os.link(root / "runtime-lock.json", root / "runtime-lock.link")
            with self.assertRaisesRegex(F0HError, "RUNNER_CONFIGURATION_INVALID"):
                _load_runtime_copy(root)
        finally:
            temporary.cleanup()

    def test_runtime_control_files_have_private_safe_modes(self) -> None:
        for path in INFRA.rglob("*"):
            if path.is_file():
                self.assertIn(stat.S_IMODE(path.stat().st_mode), {0o600, 0o644, 0o755})
                self.assertEqual(path.stat().st_nlink, 1)

    def test_previous_runtime_files_remain_frozen(self) -> None:
        expected = {
            "infra/f0e/component-lock.json": "8b248d05c83155a9df768773691371ac32876f2a9e1404ca8534a0bdf88fbe3b",
            "infra/f0e/runtime-lock.json": "d996594ed9b44804849cbad728c96d349b940cc50b8e35d6175e44056b7e541e",
            "infra/f0e/requirements.lock": "080779c62e3598835d51a035d72327b3de2f3ce62e0968e8bce9a893fd17740e",
            "infra/f0e/runner.py": "a876401a40376de1ca5ec9280f3633821776679b6a584af189c7acfe665a1e11",
            "infra/f0f/component-lock.json": "a144f1b742911d6b549ddef97c2ee3d4fe7b7f3c9eb32206c7f47b53b0a065c1",
            "infra/f0f/runtime-lock.json": "9519651a554a0fa0c2887ef15746e79e8ac0562c22bcfa19e611b086cc267df4",
            "infra/f0f/runner.py": "04d09151dc4b8316a0f1f1af0f8588b58af921aa5cd6ae7d3e3edba3953d723c",
        }
        actual = {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in expected
        }
        self.assertEqual(actual, expected)


class F0HSandboxTests(unittest.TestCase):
    def test_docker_argv_is_fixed_offline_read_only_and_mount_free(self) -> None:
        from platform_foundation.f0h.runtime_config import (
            load_runtime_bundle,
            runtime_paths,
        )
        from platform_foundation.f0h.supervisor import docker_argv

        bundle = load_runtime_bundle()
        argv = docker_argv(*runtime_paths(), bundle.container_image_id)
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertIn("--read-only", argv)
        self.assertIn("--pull", argv)
        self.assertEqual(argv[argv.index("--pull") + 1], "never")
        self.assertNotIn("--volume", argv)
        self.assertNotIn("--mount", argv)
        self.assertNotIn("--env", argv)
        self.assertNotIn("--entrypoint", argv)
        self.assertEqual(argv[-1], bundle.container_image_id)

    def test_docker_argv_has_named_cleanup_target_and_resource_ceilings(self) -> None:
        from platform_foundation.f0h.runtime_config import (
            load_runtime_bundle,
            runtime_paths,
        )
        from platform_foundation.f0h.supervisor import docker_argv

        bundle = load_runtime_bundle()
        argv = docker_argv(*runtime_paths(), bundle.container_image_id)
        self.assertRegex(
            argv[argv.index("--name") + 1],
            r"^" + re.escape(_f0h_container_prefix()) + r"[0-9a-f]{32}$",
        )
        self.assertEqual(argv[argv.index("--ipc") + 1], "none")
        self.assertEqual(argv[argv.index("--pids-limit") + 1], "64")
        self.assertEqual(argv[argv.index("--memory") + 1], "1024m")
        self.assertEqual(argv[argv.index("--cpus") + 1], "1")
        self.assertIn("no-new-privileges", argv)

    def test_mutable_image_reference_is_rejected(self) -> None:
        from platform_foundation.f0h.contracts import F0HError
        from platform_foundation.f0h.runtime_config import runtime_paths
        from platform_foundation.f0h.supervisor import docker_argv

        with self.assertRaisesRegex(F0HError, "RUNNER_CONFIGURATION_INVALID"):
            docker_argv(*runtime_paths(), "anhuan-f0h-runtime:latest")

    def test_runtime_paths_rejects_symlinked_seccomp(self) -> None:
        from platform_foundation.f0h.contracts import F0HError

        temporary = _copy_runtime()
        try:
            root = Path(temporary.name) / "f0h"
            _runtime_paths_copy(root)
            seccomp = root / "seccomp.json"
            target = root / "seccomp.target"
            seccomp.rename(target)
            seccomp.symlink_to(target.name)
            with self.assertRaisesRegex(F0HError, "RUNNER_CONFIGURATION_INVALID"):
                _runtime_paths_copy(root)
        finally:
            temporary.cleanup()

    def test_seccomp_denies_network_namespace_and_new_mount_syscalls(self) -> None:
        payload = json.loads((INFRA / "seccomp.json").read_bytes())
        denied = {
            name
            for rule in payload["syscalls"]
            if rule["action"] != "SCMP_ACT_ALLOW"
            for name in rule["names"]
        }
        self.assertTrue(
            {
                "socket",
                "connect",
                "accept",
                "bind",
                "unshare",
                "setns",
                "clone3",
                "mount",
                "fsopen",
                "fsconfig",
                "fsmount",
                "move_mount",
                "open_tree",
                "mount_setattr",
            }.issubset(denied)
        )

    def test_dockerfile_has_immutable_base_and_offline_install(self) -> None:
        body = (INFRA / "Dockerfile").read_text(encoding="utf-8")
        self.assertRegex(
            body,
            r"(?m)^FROM (?:[a-z0-9./_-]+@)?sha256:[0-9a-f]{64}(?:\s+AS\s+[a-z0-9_-]+)?$",
        )
        self.assertTrue("--no-index" in body or "PIP_NO_INDEX=1" in body)
        self.assertIn("--no-deps", body)
        self.assertNotRegex(body, r"(?i)https?://|curl|wget")
        self.assertNotRegex(body, r"(?m)^\s*(ADD|RUN)\s+https?://")

    def test_runner_has_fixed_entrypoint_and_no_download_fallback(self) -> None:
        dockerfile = (INFRA / "Dockerfile").read_text(encoding="utf-8")
        runner = (INFRA / "runner.py").read_text(encoding="utf-8")
        self.assertRegex(dockerfile, r'ENTRYPOINT\s*\[\s*"python3"\s*,\s*"-I"\s*,\s*"-B"')
        self.assertNotRegex(
            runner,
            r"(?m)^\s*(?:import|from)\s+(?:requests|httpx|urllib|socket|subprocess)\b",
        )
        self.assertNotRegex(runner, r"(?i)https?://|tesseract|pdftoppm|soffice")

    def test_supervisor_rejects_modified_argv(self) -> None:
        from platform_foundation.f0h.contracts import F0HError
        from platform_foundation.f0h.runtime_config import (
            load_runtime_bundle,
            runtime_paths,
        )
        from platform_foundation.f0h.supervisor import (
            FixedArgvPpocrV6Supervisor,
            docker_argv,
        )

        bundle = load_runtime_bundle()
        argv = list(docker_argv(*runtime_paths(), bundle.container_image_id))
        argv[argv.index("none")] = "bridge"
        with self.assertRaisesRegex(F0HError, "RUNNER_CONFIGURATION_INVALID"):
            FixedArgvPpocrV6Supervisor(tuple(argv), bundle)

    def test_sbom_names_rapidocr_onnxruntime_and_models(self) -> None:
        body = (INFRA / "sbom.spdx.json").read_text(encoding="utf-8")
        self.assertIn("RapidOCR", body)
        self.assertIn("3.9.2", body)
        self.assertIn("onnxruntime", body)
        self.assertIn("PP-OCRv6_det_small.onnx", body)
        self.assertIn("PP-OCRv6_rec_small.onnx", body)


class F0HRealSyntheticRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from platform_foundation.f0h.supervisor import run_envelope_for_test

        probe = runpy.run_path(str(INFRA / "synthetic_probe.py"))
        cls.envelope = staticmethod(probe["_envelope"])
        cls.pdf_result = run_envelope_for_test(cls.envelope("PDF", False))
        cls.jpeg_result = run_envelope_for_test(cls.envelope("JPEG", False))
        cls.blank_result = run_envelope_for_test(cls.envelope("JPEG_BLANK", False))

    def test_real_pdf_uses_ppocrv6_and_keeps_body_private(self) -> None:
        result = self.pdf_result
        self.assertEqual(result["schema"], "f0f-body-result-v1")
        self.assertEqual(result["ocr_engine"]["name"], "rapidocr")
        self.assertEqual(result["ocr_engine"]["version"], "3.9.2")
        self.assertTrue(result["ocr_executed"])
        self.assertFalse(result["raw_text_persisted"])
        self.assertEqual(result["external_calls"], 0)
        self.assertGreater(result["ocr_nonblank_char_count"], 0)

    def test_real_jpeg_returns_ordered_blocks(self) -> None:
        blocks = self.jpeg_result["blocks"]
        self.assertGreater(len(blocks), 0)
        self.assertEqual([block["index"] for block in blocks], list(range(len(blocks))))
        self.assertEqual(self.jpeg_result["document_type"], "JPEG")

    def test_real_blank_jpeg_is_manual_review_without_fabricated_text(self) -> None:
        result = self.blank_result
        self.assertEqual(result["blocks"], [])
        self.assertEqual(result["ocr_nonblank_char_count"], 0)
        self.assertEqual(result["decision"], "MANUAL_REVIEW_REQUIRED")
        self.assertEqual(result["reason_codes"], ["EMPTY_OCR_OUTPUT"])

    def test_private_result_validator_accepts_real_body_result(self) -> None:
        from platform_foundation.f0h.contracts import validate_private_result
        from platform_foundation.f0h.runtime_config import load_runtime_bundle

        validated = validate_private_result(self.pdf_result, load_runtime_bundle())
        self.assertFalse(validated["raw_text_persisted"])
        self.assertEqual(validated["ocr_engine"]["version"], "3.9.2")

    def test_private_result_validator_rejects_engine_identity_tamper(self) -> None:
        from platform_foundation.f0h.contracts import F0HError, validate_private_result
        from platform_foundation.f0h.runtime_config import load_runtime_bundle

        tampered = json.loads(json.dumps(self.pdf_result))
        tampered["ocr_engine"]["version"] = "9.9.9"
        with self.assertRaisesRegex(F0HError, "RUNNER_OUTPUT_INVALID"):
            validate_private_result(tampered, load_runtime_bundle())

    def test_private_result_validator_rejects_model_identity_tamper(self) -> None:
        from platform_foundation.f0h.contracts import F0HError, validate_private_result
        from platform_foundation.f0h.runtime_config import load_runtime_bundle

        tampered = json.loads(json.dumps(self.pdf_result))
        tampered["ocr_engine"]["model_bundle_sha256"] = "0" * 64
        with self.assertRaisesRegex(F0HError, "RUNNER_OUTPUT_INVALID"):
            validate_private_result(tampered, load_runtime_bundle())

    def test_tampered_source_envelope_is_rejected_without_echo(self) -> None:
        from platform_foundation.f0h.contracts import F0HError
        from platform_foundation.f0h.supervisor import run_envelope_for_test

        with self.assertRaisesRegex(F0HError, "RUNNER_FAILED|RUNNER_OUTPUT_INVALID") as raised:
            run_envelope_for_test(self.envelope("PDF", True))
        self.assertNotIn("SYNTHETIC", repr(raised.exception.to_dict()))

    def test_evidence_mode_emits_no_blocks_or_body(self) -> None:
        from platform_foundation.f0h.supervisor import run_envelope_for_test

        result = run_envelope_for_test(self.envelope("JPEG", False), mode="evidence")
        self.assertEqual(result["schema"], "f0e-result-v1")
        self.assertNotIn("blocks", result)
        self.assertFalse(result["raw_text_emitted"])
        self.assertFalse(result["raw_text_persisted"])

    def test_no_f0h_container_residuals_remain(self) -> None:
        result = subprocess.run(
            (
                "/usr/local/bin/docker",
                "ps",
                "-a",
                "--filter",
                "name=^/" + _f0h_container_prefix(),
                "--format",
                "{{.ID}}",
            ),
            check=False,
            capture_output=True,
            timeout=15,
        )
        self.assertEqual((result.returncode, result.stdout.strip()), (0, b""))


class F0HReplayContractTests(unittest.TestCase):
    def _summary(self, **changes: object) -> dict[str, object]:
        value: dict[str, object] = {
            "profile": "smoke",
            "documents": 10,
            "visual_units": 110,
            "native_bypass": 105,
            "ppocrv6_ocr": 5,
            "deferred_documents": 2,
            "errors": 0,
            "external_calls": 0,
            "runtime_downloads": 0,
            "raw_text_persisted": False,
            "status": "LOCAL_PPOCRV6_RUNTIME_READY",
            "accuracy_status": "ACCURACY_NOT_EVALUATED",
            "search_status": "SEARCH_NOT_READY",
            "production_status": "NOT_PRODUCTION",
        }
        value.update(changes)
        return value

    def test_verify_repeat_accepts_identical_canonical_summary(self) -> None:
        from platform_foundation.f0h.replay import verify_repeat

        first = self._summary()
        second = json.loads(json.dumps(first))
        self.assertIsNone(verify_repeat(first, second))

    def test_verify_repeat_rejects_changed_count(self) -> None:
        from platform_foundation.f0h.contracts import F0HError
        from platform_foundation.f0h.replay import verify_repeat

        with self.assertRaisesRegex(F0HError, "REPLAY_MISMATCH"):
            verify_repeat(self._summary(), self._summary(ppocrv6_ocr=4))

    def test_verify_repeat_rejects_body_field(self) -> None:
        from platform_foundation.f0h.contracts import F0HError
        from platform_foundation.f0h.replay import verify_repeat

        with self.assertRaisesRegex(F0HError, "REPLAY_MISMATCH"):
            verify_repeat(self._summary(), self._summary(raw_text="CANARY"))

    def test_replay_rejects_unknown_profile_without_traceback(self) -> None:
        result = subprocess.run(
            (
                str(ROOT / ".venv/bin/python"),
                "-B",
                "-m",
                "platform_foundation.f0h",
                "replay",
                "--profile",
                "custom",
            ),
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": "src"},
            check=False,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(b"Traceback", result.stderr + result.stdout)

    def test_cli_help_exposes_only_replay_and_artifacts(self) -> None:
        result = subprocess.run(
            (
                str(ROOT / ".venv/bin/python"),
                "-B",
                "-m",
                "platform_foundation.f0h",
                "--help",
            ),
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": "src"},
            check=False,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0)
        output = result.stdout.decode("utf-8")
        self.assertIn("replay", output)
        self.assertIn("artifacts", output)
        self.assertNotIn("serve", output)
        self.assertNotIn("download", output)

    def test_registered_artifact_statuses_do_not_claim_accuracy_or_production(self) -> None:
        acceptance = ROOT / "artifacts/f0h-ppocrv6-runtime/v0.1/acceptance.json"
        self.assertTrue(acceptance.is_file())
        payload = json.loads(acceptance.read_bytes())
        self.assertEqual(payload["status"], "LOCAL_PPOCRV6_RUNTIME_READY")
        self.assertEqual(payload["accuracy_status"], "ACCURACY_NOT_EVALUATED")
        self.assertEqual(payload["search_status"], "SEARCH_NOT_READY")
        self.assertEqual(payload["production_status"], "NOT_PRODUCTION")
        self.assertFalse(payload["raw_text_persisted"])


if __name__ == "__main__":
    unittest.main()
