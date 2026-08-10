"""Offline attack tests for the F1.1.1 log/plaintext canary verifier."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from tests import f111_log_canary as canary


class LogCanaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = "anhuan-f111-repair-" + uuid.uuid4().hex
        self.bundle = Path(
            tempfile.mkdtemp(prefix=self.project + "-bundle-", dir="/private/tmp")
        )
        self.bundle.chmod(0o700)
        self.runtime_home = Path(
            tempfile.mkdtemp(prefix=self.project + "-formal-home-", dir="/private/tmp")
        )
        self.runtime_home.chmod(0o700)
        self.runtime_tmp = self.runtime_home / "tmp"
        self.runtime_tmp.mkdir(mode=0o700)
        self.needles = ["CANARY-" + uuid.uuid4().hex]
        self.canary_file = self.bundle / "leak_canaries"
        self.canary_file.write_text(json.dumps(self.needles), encoding="utf-8")
        self.canary_file.chmod(0o600)
        self.environment = {
            "F111_REVERSE_PROJECT": self.project,
            "F111_FORMAL_RUN_ID": self.project,
            "F111_REVERSE_SECRETS_DIR": str(self.bundle),
            "F111_REVERSE_COMPOSE_OVERRIDE": str(
                canary.ROOT / "infra/f1/docker-compose.repair.yml"
            ),
            "F1_API_HOST_PORT": "31001",
            "F1_JAEGER_UI_HOST_PORT": "31002",
            "F1_RAGFLOW_API_HOST_PORT": "31003",
            "HOME": str(self.runtime_home),
            "TMPDIR": str(self.runtime_tmp),
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.bundle, ignore_errors=True)
        shutil.rmtree(self.runtime_home, ignore_errors=True)

    def _config(self) -> canary.LogConfig:
        return canary.load_config(self.environment)

    def test_config_accepts_only_uuid4_scratch_project(self) -> None:
        config = self._config()
        self.assertEqual(config.project, self.project)
        bad = dict(self.environment, F111_REVERSE_PROJECT="anhuan-f111-repair-deadbeef")
        with self.assertRaises(canary.CanaryError):
            canary.load_config(bad)

    def test_bundle_and_canary_file_permissions_are_strict(self) -> None:
        self.bundle.chmod(0o755)
        with self.assertRaises(canary.CanaryError):
            self._config()
        self.bundle.chmod(0o700)
        self.canary_file.chmod(0o644)
        with self.assertRaises(canary.CanaryError):
            self._config()

    def test_symlinked_canary_file_is_rejected(self) -> None:
        target = self.bundle / "real"
        self.canary_file.rename(target)
        self.canary_file.symlink_to(target)
        with self.assertRaises(canary.CanaryError):
            self._config()

    def test_bundle_path_must_be_exact_private_scratch_prefix(self) -> None:
        outside = Path(tempfile.mkdtemp(prefix="not-the-project-", dir="/private/tmp"))
        try:
            outside.chmod(0o700)
            leak = outside / "leak_canaries"
            leak.write_text(json.dumps(self.needles), encoding="utf-8")
            leak.chmod(0o600)
            bad = dict(self.environment, F111_REVERSE_SECRETS_DIR=str(outside))
            with self.assertRaises(canary.CanaryError):
                canary.load_config(bad)
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_endpoints_are_distinct_high_loopback_ports(self) -> None:
        bad = dict(self.environment, F1_JAEGER_UI_HOST_PORT="16686")
        with self.assertRaises(canary.CanaryError):
            canary.load_config(bad)
        bad = dict(self.environment, F1_JAEGER_UI_HOST_PORT="31001")
        with self.assertRaises(canary.CanaryError):
            canary.load_config(bad)

    def test_compose_override_cannot_escape_fixed_repository_path(self) -> None:
        bad = dict(
            self.environment,
            F111_REVERSE_COMPOSE_OVERRIDE=str(self.bundle / "docker-compose.repair.yml"),
        )
        with self.assertRaises(canary.CanaryError):
            canary.load_config(bad)

    def test_docker_environment_binds_only_the_validated_project(self) -> None:
        environment = canary.Verifier(self._config())._docker_environment()
        self.assertEqual(environment["F111_REVERSE_PROJECT"], self.project)
        self.assertEqual(environment["HOME"], str(self.runtime_home))
        self.assertEqual(environment["TMPDIR"], str(self.runtime_tmp))
        self.assertNotIn("DOCKER_HOST", environment)
        self.assertNotIn("COMPOSE_PROJECT_NAME", environment)

    def test_scanner_finds_raw_json_url_and_base64_variants(self) -> None:
        probe = 'CANARY "x/y"+'.encode("utf-8")
        scanner = canary.CanaryScanner((probe,))
        variants = canary.canary_variants(probe)
        self.assertGreaterEqual(len(variants), 4)
        for value in variants:
            self.assertEqual(scanner.hits(b"prefix" + value + b"suffix"), 1)
        self.assertEqual(scanner.hits(b"opaque-safe-output"), 0)

    def test_stream_scanner_detects_chunk_boundary_and_drains(self) -> None:
        probe = b"boundary-canary-value"
        scanner = canary.CanaryScanner((probe,))

        class Chunks:
            def __init__(self) -> None:
                self.values = [b"prefix-boundary-can", b"ary-value-suffix", b""]
                self.reads = 0

            def read(self, _size: int) -> bytes:
                self.reads += 1
                return self.values.pop(0)

        stream = Chunks()
        self.assertEqual(scanner.stream_hits(stream), 1)
        self.assertEqual(stream.reads, 3)

    def test_remote_docker_context_is_rejected(self) -> None:
        verifier = canary.Verifier(self._config())
        with mock.patch.object(
            verifier,
            "_run_bytes",
            return_value=b'"tcp://remote.invalid:2376"\n',
        ):
            with self.assertRaises(canary.CanaryError):
                verifier.validate_docker_context()

    def test_compose_ports_must_match_explicit_scratch_endpoints(self) -> None:
        verifier = canary.Verifier(self._config())
        compose = {
            "name": self.project,
            "services": {
                "api": {"ports": [{"target": 8001, "published": "31001", "host_ip": "127.0.0.1"}]},
                "jaeger": {"ports": [{"target": 16686, "published": "31002", "host_ip": "127.0.0.1"}]},
                "ragflow": {"ports": [{"target": 9380, "published": "31003", "host_ip": "127.0.0.1"}]},
                "otel-collector": {},
            },
        }
        services = verifier.validate_compose(compose)
        self.assertEqual(set(services), set(compose["services"]))
        compose["services"]["api"]["ports"][0]["published"] = "31009"
        with self.assertRaises(canary.CanaryError):
            verifier.validate_compose(compose)

    def test_container_scope_requires_project_labels_and_log_tmpfs_mounts(self) -> None:
        verifier = canary.Verifier(self._config())
        services = ("api", "jaeger", "otel-collector", "ragflow")
        rows = [
            {
                "Id": "a" * 64,
                "Config": {"Labels": {"com.docker.compose.project": self.project, "com.docker.compose.service": "api"}},
                "State": {"Status": "running", "ExitCode": 0},
                "Mounts": [],
                "HostConfig": {"Tmpfs": {}},
            },
            {
                "Id": "b" * 64,
                "Config": {"Labels": {"com.docker.compose.project": self.project, "com.docker.compose.service": "jaeger"}},
                "State": {"Status": "running", "ExitCode": 0},
                "Mounts": [],
                "HostConfig": {"Tmpfs": {}},
            },
            {
                "Id": "c" * 64,
                "Config": {"Labels": {"com.docker.compose.project": self.project, "com.docker.compose.service": "otel-collector"}},
                "State": {"Status": "running", "ExitCode": 0},
                "Mounts": [],
                "HostConfig": {"Tmpfs": {"/var/log/otel": "rw,noexec"}},
            },
            {
                "Id": "d" * 64,
                "Config": {"Labels": {"com.docker.compose.project": self.project, "com.docker.compose.service": "ragflow"}},
                "State": {"Status": "running", "ExitCode": 0},
                "Mounts": [{"Type": "volume", "Destination": "/ragflow/logs", "RW": True}],
                "HostConfig": {"Tmpfs": {}},
            },
        ]
        scope = verifier.validate_containers(services, rows)
        self.assertEqual(scope.ragflow_container, "d" * 64)
        rows[3]["Mounts"] = []
        with self.assertRaises(canary.CanaryError):
            verifier.validate_containers(services, rows)
        rows[3]["Mounts"] = [{"Type": "volume", "Destination": "/ragflow/logs", "RW": True}]
        rows[0]["Config"]["Labels"]["com.docker.compose.project"] = "shared"
        with self.assertRaises(canary.CanaryError):
            verifier.validate_containers(services, rows)

    def test_trace_scan_requires_visible_api_service(self) -> None:
        verifier = canary.Verifier(self._config())
        responses = {
            verifier.config.api_base + "/healthz": b"{}",
            verifier.config.ragflow_base + "/": b"{}",
            verifier.config.jaeger_base + "/api/services": b'{"data":[]}',
        }
        with mock.patch.object(
            verifier, "_http_get", side_effect=lambda url: responses[url]
        ):
            with self.assertRaises(canary.CanaryError):
                verifier.scan_http_surfaces()

    def test_container_path_traversal_is_rejected(self) -> None:
        for value in ("/safe/../escape", "relative", "/"):
            with self.assertRaises(canary.CanaryError):
                canary._safe_container_path(value)

    def test_artifact_symlink_is_a_path_escape_failure(self) -> None:
        root = self.bundle / "artifact"
        root.mkdir(mode=0o700)
        (root / "escape").symlink_to("/private/tmp")
        scanner = canary.CanaryScanner(self._config().canaries)
        with self.assertRaises(canary.CanaryError):
            canary.scan_host_tree(root, scanner)

    def test_positive_control_is_detected_once_then_removed(self) -> None:
        verifier = canary.Verifier(self._config())
        target = self.runtime_tmp / canary.POSITIVE_CONTROL_DIRECTORY
        with mock.patch.object(
            canary, "scan_host_tree", wraps=canary.scan_host_tree
        ) as scan:
            verifier.positive_control()
        scan.assert_called_once_with(target, verifier.scanner)
        self.assertFalse(target.exists())

    def test_positive_control_tamper_blocks_real_surface_scan(self) -> None:
        verifier = canary.Verifier(self._config())
        with (
            mock.patch.object(canary, "scan_host_tree", return_value=0),
            mock.patch.object(verifier, "verify") as verify,
        ):
            with self.assertRaises(canary.CanaryError):
                verifier.positive_control()
        verify.assert_not_called()
        self.assertFalse(
            (self.runtime_tmp / canary.POSITIVE_CONTROL_DIRECTORY).exists()
        )

    def test_preexisting_positive_control_scope_is_not_deleted(self) -> None:
        target = self.runtime_tmp / canary.POSITIVE_CONTROL_DIRECTORY
        target.mkdir(mode=0o700)
        sentinel = target / "sentinel"
        sentinel.write_bytes(b"opaque")
        with self.assertRaises(canary.CanaryError):
            canary.Verifier(self._config()).positive_control()
        self.assertTrue(sentinel.exists())

    def test_complete_zero_coverage_returns_only_green_marker(self) -> None:
        with (
            mock.patch.dict(os.environ, self.environment, clear=True),
            mock.patch.object(canary.Verifier, "verify", return_value=0),
        ):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                exit_code = canary.main()
        self.assertEqual(exit_code, 0)
        self.assertEqual(stream.getvalue(), "F111_LOG_CANARY_HITS=0\n")

    def test_dependency_failure_and_hit_never_echo_sensitive_material(self) -> None:
        for effect in (canary.CanaryError("DEPENDENCY_UNREACHABLE"), 1):
            with (
                mock.patch.dict(os.environ, self.environment, clear=True),
                mock.patch.object(canary.Verifier, "verify", side_effect=effect if isinstance(effect, Exception) else None, return_value=effect if isinstance(effect, int) else 0),
            ):
                stream = io.StringIO()
                with contextlib.redirect_stdout(stream):
                    exit_code = canary.main()
            self.assertEqual(exit_code, 2)
            self.assertEqual(stream.getvalue(), "F111_LOG_CANARY_HITS=1\n")
            self.assertNotIn(self.needles[0], stream.getvalue())
            self.assertNotIn(str(self.bundle), stream.getvalue())


if __name__ == "__main__":
    unittest.main()
