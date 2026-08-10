"""Offline anti-fake tests for the isolated F1.1.1 clean rebuild runner."""
from __future__ import annotations

from contextlib import ExitStack
import io
import hashlib
import inspect
import json
import os
import shutil
import stat
import struct
import tempfile
import uuid
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests import f111_clean_rebuild as rebuild


UUID4_HEX = "0123456789ab4def8fedcba987654321"


def _synthetic_runtime_tree_bundle(
    source: Path,
    phase: str,
    *,
    relative_paths: tuple[str, ...] = ("component-lock.json", "nested/runner.py"),
) -> str:
    bodies = [f"runtime-{phase}-{index}\n".encode("ascii") for index in range(len(relative_paths))]
    entries: list[dict[str, object]] = []
    tree_entries: list[dict[str, object]] = []
    offset = 0
    for index, (relative, body) in enumerate(zip(relative_paths, bodies, strict=True)):
        mode = 0o755 if index else 0o644
        digest = hashlib.sha256(body).hexdigest()
        tree_entry = {
            "relative_path": relative,
            "mode": mode,
            "sha256": digest,
            "size": len(body),
        }
        tree_entries.append(tree_entry)
        entries.append({**tree_entry, "offset": offset})
        offset += len(body)
    tree_sha256 = hashlib.sha256(rebuild._canonical_bytes(tree_entries)).hexdigest()
    header = rebuild._canonical_bytes(
        {
            "schema": rebuild.RUNTIME_TREE_BUNDLE_SCHEMA,
            "phase": phase,
            "entry_count": len(entries),
            "payload_size": offset,
            "tree_sha256": tree_sha256,
            "entries": entries,
        }
    )
    bundle_name = rebuild.RUNTIME_TREE_BUNDLES[phase][0]
    target = source / bundle_name
    target.write_bytes(
        rebuild.RUNTIME_TREE_BUNDLE_MAGIC
        + struct.pack(">Q", len(header))
        + header
        + b"".join(bodies)
    )
    target.chmod(0o600)
    return tree_sha256


def _synthetic_source_bundle(
    source: Path,
) -> tuple[dict[str, tuple[Path, str]], bytes]:
    records: list[dict[str, object]] = []
    bodies: list[bytes] = []
    manifest_lines: dict[str, list[str]] = {"core": [], "negative": []}
    route_entries: list[dict[str, object]] = []
    offset = 0
    selected: list[dict[str, str]] = []
    for group, count in (("core", 24), ("negative", 2)):
        for line in range(1, count + 1):
            suffix = ".jpg" if group == "core" and line == 4 else (
                ".xlsx" if group == "negative" and line == 2 else ".pdf"
            )
            relative = Path(group) / f"item-{line:02d}{suffix}"
            body = f"fixture-{group}-{line}".encode("ascii")
            digest = hashlib.sha256(body).hexdigest()
            document_id = hashlib.sha256(
                f"document-{group}-{line}".encode("ascii")
            ).hexdigest()
            source_id = str(
                uuid.uuid5(
                    rebuild.SOURCE_ID_NAMESPACE,
                    "\0".join(
                        (
                            rebuild.FIXTURE_SET_ID,
                            rebuild.FIXTURE_SET_VERSION,
                            group,
                            str(line),
                            document_id,
                        )
                    ),
                )
            )
            records.append(
                {
                    "source_id": source_id,
                    "group": group,
                    "line": line,
                    "sha256": digest,
                    "size": len(body),
                    "offset": offset,
                }
            )
            bodies.append(body)
            manifest_lines[group].append(f"{digest}  {relative.as_posix()}")
            route_entries.append(
                {
                    "group": group,
                    "line": line,
                    "document_id": document_id,
                }
            )
            if group == "core" and line in {1, 2, 3, 4}:
                selected.append(
                    {
                        "path": f"/private/tmp/opaque-fixture-{line}",
                        "sha256": digest,
                        "content_type": (
                            "image/jpeg" if suffix == ".jpg" else "application/pdf"
                        ),
                    }
                )
            offset += len(body)
    payloads = {
        "fixture_core_manifest": (
            "\n".join(manifest_lines["core"]) + "\n"
        ).encode("utf-8"),
        "fixture_negative_manifest": (
            "\n".join(manifest_lines["negative"]) + "\n"
        ).encode("utf-8"),
        "fixture_route_plan_json": rebuild._canonical_bytes(
            {"entries": route_entries}
        ),
        "fixture_native_plan_json": b"synthetic-native-plan\n",
        "f0h_runtime_acceptance_json": b"synthetic-runtime-acceptance\n",
    }
    contracts = {
        name: (
            Path("fixture-contracts") / (name + ".bin"),
            hashlib.sha256(raw).hexdigest(),
        )
        for name, raw in payloads.items()
    }
    for name, raw in payloads.items():
        target = source / name
        target.write_bytes(raw)
        target.chmod(0o600)
    header = rebuild._canonical_bytes(
        {
            "schema": rebuild.SOURCE_BUNDLE_SCHEMA,
            "entry_count": len(records),
            "payload_size": sum(len(body) for body in bodies),
            "entries": records,
        }
    )
    bundle = source / rebuild.SOURCE_BUNDLE_NAME
    bundle.write_bytes(
        rebuild.SOURCE_BUNDLE_MAGIC
        + struct.pack(">Q", len(header))
        + header
        + b"".join(bodies)
    )
    bundle.chmod(0o600)
    return contracts, rebuild._canonical_bytes(selected)


class RoundContractTests(unittest.TestCase):
    def test_round_is_exactly_one_or_two(self) -> None:
        self.assertEqual(rebuild.parse_round("1"), 1)
        self.assertEqual(rebuild.parse_round("2"), 2)
        for value in ("", "0", "3", "01", "true", " 1"):
            with self.subTest(value=value), self.assertRaises(rebuild.RebuildError):
                rebuild.parse_round(value)

    def test_docker_exec_stdin_requires_interactive_without_tty(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout=b"")
        with mock.patch.object(
            rebuild.subprocess, "run", return_value=completed
        ) as run:
            for arguments, input_bytes in (
                (("docker", "exec", "container", "psql"), b"SELECT 1;\n"),
                (("docker", "exec", "-i", "container", "psql"), None),
                (("docker", "exec", "-t", "container", "psql"), b"SELECT 1;\n"),
                (("docker", "exec", "--tty", "container", "psql"), b"SELECT 1;\n"),
            ):
                with self.subTest(arguments=arguments), self.assertRaisesRegex(
                    rebuild.RebuildError, "COMMAND_REJECTED"
                ):
                    rebuild._process(
                        arguments,
                        cwd=rebuild.ROOT,
                        environment={},
                        timeout=30,
                        input_bytes=input_bytes,
                        check=False,
                    )
            result = rebuild._process(
                ("docker", "exec", "-i", "container", "psql"),
                cwd=rebuild.ROOT,
                environment={},
                timeout=30,
                input_bytes=b"SELECT 1;\n",
                check=False,
            )
            self.assertEqual(result.exit_code, 0)
            run.assert_called_once()

    def test_project_and_database_are_bound_to_one_fresh_uuid4(self) -> None:
        identity = rebuild.RoundIdentity.create(
            1, uuid_factory=lambda: uuid.UUID(hex=UUID4_HEX)
        )
        self.assertEqual(
            identity.project, "anhuan-f111-repair-" + UUID4_HEX
        )
        self.assertEqual(identity.database, "f111_repair_" + UUID4_HEX)
        self.assertEqual(identity.pg_container, identity.project + "-postgres")
        self.assertEqual(identity.pg_volume, identity.project + "-postgres-data")

    def test_non_v4_identity_is_rejected(self) -> None:
        with self.assertRaises(rebuild.RebuildError):
            rebuild.RoundIdentity.create(
                1,
                uuid_factory=lambda: uuid.UUID(
                    "01234567-89ab-1def-8fed-cba987654321"
                ),
            )


class EvidenceContractTests(unittest.TestCase):
    def test_live_metric_line_must_be_exact_unique_and_zero(self) -> None:
        expected = ("alpha", "beta")
        self.assertEqual(
            rebuild.parse_zero_metric_line(b"alpha=0 beta=0\n", expected),
            {"alpha": 0, "beta": 0},
        )
        for raw in (
            b"alpha=0\n",
            b"alpha=0 beta=1\n",
            b"alpha=0 beta=0 alpha=0\n",
            b"alpha=0 beta=0\nalpha=0 beta=0\n",
            b"alpha=0 beta=0 gamma=0\n",
        ):
            with self.subTest(raw=raw), self.assertRaises(rebuild.RebuildError):
                rebuild.parse_zero_metric_line(raw, expected)

    def test_normalized_result_ignores_round_randomness(self) -> None:
        first = rebuild.normalized_result(
            source_sha256="a" * 64,
            fixture_source_sha256="e" * 64,
            fixture_e2e_sha256="f" * 64,
            schema_sha256="b" * 64,
            pg_contract_sha256="c" * 64,
            runtime_inventory_sha256="d" * 64,
            service_count=len(rebuild.EXPECTED_SERVICES),
        )
        second = rebuild.normalized_result(
            source_sha256="a" * 64,
            fixture_source_sha256="e" * 64,
            fixture_e2e_sha256="f" * 64,
            schema_sha256="b" * 64,
            pg_contract_sha256="c" * 64,
            runtime_inventory_sha256="d" * 64,
            service_count=len(rebuild.EXPECTED_SERVICES),
        )
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")

    def test_marker_is_emitted_once_only_after_cleanup(self) -> None:
        summary = rebuild.RoundSummary(
            source_sha256="a" * 64,
            fixture_source_sha256="e" * 64,
            fixture_e2e_sha256="f" * 64,
            schema_sha256="b" * 64,
            pg_contract_sha256="c" * 64,
            runtime_inventory_sha256="d" * 64,
            service_count=len(rebuild.EXPECTED_SERVICES),
            evidence_captured=True,
            cleanup_residuals=0,
        )
        temporary = Path(tempfile.mkdtemp(prefix="f111-clean-evidence-", dir="/private/tmp"))
        temporary.chmod(0o700)
        try:
            output = io.StringIO()
            with (
                mock.patch.object(rebuild.CleanRebuildRound, "run", return_value=summary),
                mock.patch("sys.stdout", output),
            ):
                exit_code = rebuild.main(
                    {rebuild.ROUND_ENV: "1", "TMPDIR": str(temporary)}
                )
            self.assertEqual(exit_code, 0)
            lines = output.getvalue().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertRegex(lines[0], r"^CLEAN_REBUILD_RESULT_SHA256=[0-9a-f]{64}$")
            target = temporary / rebuild.CLEAN_EVIDENCE_NAMES[1]
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            document = json.loads(target.read_bytes())
            self.assertEqual(
                lines[0].split("=", 1)[1],
                rebuild.validate_round_evidence(document, 1),
            )
        finally:
            shutil.rmtree(temporary)

    def test_failure_never_emits_success_marker_or_raw_exception(self) -> None:
        output = io.StringIO()
        error = io.StringIO()
        temporary = Path(tempfile.mkdtemp(prefix="f111-clean-evidence-", dir="/private/tmp"))
        temporary.chmod(0o700)
        try:
            with (
                mock.patch.object(
                    rebuild.CleanRebuildRound,
                    "run",
                    side_effect=rebuild.RebuildError("BUILD_RED"),
                ),
                mock.patch("sys.stdout", output),
                mock.patch("sys.stderr", error),
            ):
                exit_code = rebuild.main(
                    {rebuild.ROUND_ENV: "2", "TMPDIR": str(temporary)}
                )
            self.assertEqual(exit_code, 2)
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(error.getvalue(), "error=BUILD_RED\n")
            self.assertNotIn("CLEAN_REBUILD_RESULT", error.getvalue())
            self.assertFalse((temporary / rebuild.CLEAN_EVIDENCE_NAMES[2]).exists())
        finally:
            shutil.rmtree(temporary)

    def test_success_summary_rejects_pre_evidence_or_residuals(self) -> None:
        for captured, residuals in ((False, 0), (True, 1)):
            summary = rebuild.RoundSummary(
                source_sha256="a" * 64,
                fixture_source_sha256="e" * 64,
                fixture_e2e_sha256="f" * 64,
                schema_sha256="b" * 64,
                pg_contract_sha256="c" * 64,
                runtime_inventory_sha256="d" * 64,
                service_count=len(rebuild.EXPECTED_SERVICES),
                evidence_captured=captured,
                cleanup_residuals=residuals,
            )
            with self.subTest(captured=captured, residuals=residuals), self.assertRaises(
                rebuild.RebuildError
            ):
                summary.result_sha256()

    def test_runtime_inventory_binds_every_actual_service_image_id(self) -> None:
        actual = {
            service: "sha256:" + format(index + 1, "064x")
            for index, service in enumerate(sorted(rebuild.RUNTIME_SERVICES))
        }
        declared = {
            service: "pinned@sha256:" + format(index + 101, "064x")
            for index, service in enumerate(sorted(rebuild.RUNTIME_SERVICES))
        }
        bases = ("base@sha256:" + "e" * 64,)
        locks = {"python": "a" * 64, "npm": "b" * 64}
        provenance = {
            "source_snapshot_sha256": "c" * 64,
            "dockerfile_set_sha256": "d" * 64,
            "python_lock_sha256": locks["python"],
            "npm_lock_sha256": locks["npm"],
        }
        first = rebuild.runtime_inventory_digest(
            actual_images=actual,
            declared_provenance=declared,
            base_images=bases,
            lock_sha256=locks,
            build_provenance=provenance,
        )
        replaced = dict(actual)
        replaced[sorted(replaced)[0]] = "sha256:" + "f" * 64
        second = rebuild.runtime_inventory_digest(
            actual_images=replaced,
            declared_provenance=declared,
            base_images=bases,
            lock_sha256=locks,
            build_provenance=provenance,
        )
        self.assertNotEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")

    def test_evidence_rejects_every_bound_field_tamper(self) -> None:
        summary = rebuild.RoundSummary(
            source_sha256="a" * 64,
            fixture_source_sha256="b" * 64,
            fixture_e2e_sha256="c" * 64,
            schema_sha256="d" * 64,
            pg_contract_sha256="e" * 64,
            runtime_inventory_sha256="f" * 64,
            service_count=len(rebuild.EXPECTED_SERVICES),
            evidence_captured=True,
            cleanup_residuals=0,
        )
        document = rebuild.round_evidence_document(summary, 1)
        mutations = (
            ("source", "snapshot_sha256"),
            ("f0i", "fixture_source_sha256"),
            ("e2e", "fixture_sha256"),
            ("schema", "sha256"),
            ("pg", "contract_sha256"),
            ("runtime", "inventory_sha256"),
        )
        for section, key in mutations:
            changed = json.loads(json.dumps(document))
            changed[section][key] = "0" * 64
            with self.subTest(section=section), self.assertRaises(rebuild.RebuildError):
                rebuild.validate_round_evidence(changed, 1)
        changed = json.loads(json.dumps(document))
        changed["cleanup"]["residuals"] = 1
        with self.assertRaises(rebuild.RebuildError):
            rebuild.validate_round_evidence(changed, 1)

    def test_evidence_writer_never_overwrites_or_deletes_existing_target(self) -> None:
        summary = rebuild.RoundSummary(
            source_sha256="a" * 64,
            fixture_source_sha256="b" * 64,
            fixture_e2e_sha256="c" * 64,
            schema_sha256="d" * 64,
            pg_contract_sha256="e" * 64,
            runtime_inventory_sha256="f" * 64,
            service_count=len(rebuild.EXPECTED_SERVICES),
            evidence_captured=True,
            cleanup_residuals=0,
        )
        temporary = Path(tempfile.mkdtemp(prefix="f111-clean-evidence-", dir="/private/tmp"))
        temporary.chmod(0o700)
        target = temporary / rebuild.CLEAN_EVIDENCE_NAMES[1]
        target.write_bytes(b"opaque-existing")
        target.chmod(0o600)
        try:
            with self.assertRaises(rebuild.RebuildError):
                rebuild.write_round_evidence(
                    temporary, rebuild.round_evidence_document(summary, 1), 1
                )
            self.assertEqual(target.read_bytes(), b"opaque-existing")
        finally:
            shutil.rmtree(temporary)

    def test_build_provenance_labels_fail_closed_on_each_tamper(self) -> None:
        expected = {
            key: format(index + 1, "064x")
            for index, key in enumerate(sorted(rebuild.BUILD_PROVENANCE_LABELS))
        }
        labels = {
            label: expected[key]
            for key, label in rebuild.BUILD_PROVENANCE_LABELS.items()
        }
        labels["org.opencontainers.image.revision"] = expected[
            "source_snapshot_sha256"
        ]
        rebuild.validate_build_provenance_labels(labels, expected)
        for key, label in rebuild.BUILD_PROVENANCE_LABELS.items():
            changed = dict(labels)
            changed[label] = "0" * 64
            with self.subTest(key=key), self.assertRaises(rebuild.RebuildError):
                rebuild.validate_build_provenance_labels(changed, expected)

    def test_build_provenance_is_deterministic_and_declared_by_both_images(self) -> None:
        first = rebuild.build_provenance(rebuild.ROOT, "a" * 64)
        second = rebuild.build_provenance(rebuild.ROOT, "a" * 64)
        self.assertEqual(first, second)
        self.assertEqual(set(first), set(rebuild.BUILD_PROVENANCE_LABELS))
        dockerfiles = (
            rebuild.ROOT / "infra/f1/Dockerfile",
            rebuild.ROOT / "infra/f1/web.Dockerfile",
        )
        for path in dockerfiles:
            source = path.read_text(encoding="utf-8")
            for argument in rebuild.BUILD_PROVENANCE_ARGS.values():
                self.assertIn("ARG " + argument, source)
            for label in rebuild.BUILD_PROVENANCE_LABELS.values():
                self.assertIn(label, source)
        build_source = inspect.getsource(rebuild.CleanRebuildRound._build_images)
        self.assertIn('"--no-cache"', build_source)
        self.assertIn("BUILD_PROVENANCE_ARGS", build_source)

    def test_summary_digest_binds_fixture_source_and_real_e2e(self) -> None:
        common = dict(
            source_sha256="a" * 64,
            schema_sha256="b" * 64,
            pg_contract_sha256="c" * 64,
            runtime_inventory_sha256="d" * 64,
            service_count=len(rebuild.EXPECTED_SERVICES),
        )
        first = rebuild.normalized_result(
            **common,
            fixture_source_sha256="e" * 64,
            fixture_e2e_sha256="f" * 64,
        )
        source_changed = rebuild.normalized_result(
            **common,
            fixture_source_sha256="1" * 64,
            fixture_e2e_sha256="f" * 64,
        )
        e2e_changed = rebuild.normalized_result(
            **common,
            fixture_source_sha256="e" * 64,
            fixture_e2e_sha256="2" * 64,
        )
        self.assertEqual(len({first, source_changed, e2e_changed}), 3)


class FixtureSourceContractTests(unittest.TestCase):
    @staticmethod
    def _identity(*, port: int = 55432) -> rebuild.SourceContainerIdentity:
        return rebuild.SourceContainerIdentity(
            container_id="a" * 64,
            container_name="anhuan-f0d-postgres-1",
            compose_project=rebuild.SOURCE_COMPOSE_PROJECT,
            compose_service=rebuild.SOURCE_COMPOSE_SERVICE,
            image_id="sha256:" + "b" * 64,
            image_reference=rebuild.PG_IMAGE,
            published_port=port,
        )

    def test_source_scope_is_non_secret_and_binds_local_container_identity(self) -> None:
        identity = self._identity()
        document = {
            "schema": rebuild.F0I_SOURCE_SCOPE_SCHEMA,
            "host": "127.0.0.1",
            "published_port": identity.published_port,
            "database": rebuild.SOURCE_DATABASE_NAME,
            "access": "LOCAL_DOCKER_EXEC_READ_ONLY",
            "container_id": identity.container_id,
            "container_name": identity.container_name,
            "compose_project": identity.compose_project,
            "compose_service": identity.compose_service,
            "image_id": identity.image_id,
            "image_reference": identity.image_reference,
        }
        raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("ascii")
        scope = rebuild.parse_source_scope(raw)
        self.assertEqual(scope.container, identity)
        self.assertEqual(scope.database, rebuild.SOURCE_DATABASE_NAME)
        self.assertEqual(scope.access, "LOCAL_DOCKER_EXEC_READ_ONLY")
        self.assertNotIn(b"password", raw.lower())
        self.assertNotIn(b"dsn", raw.lower())

        for key, value in (
            ("host", "192.0.2.1"),
            ("database", "shared"),
            ("access", "NETWORK_PASSWORD"),
            ("published_port", 0),
            ("password", "fake"),
        ):
            changed = dict(document)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(rebuild.RebuildError):
                rebuild.parse_source_scope(json.dumps(changed).encode("ascii"))

    def test_f0g_source_scope_is_exact_canonical_and_non_secret(self) -> None:
        identity = self._identity()
        document = {
            "schema": rebuild.F0G_SOURCE_SCOPE_SCHEMA,
            "database": rebuild.F0G_SOURCE_DATABASE_NAME,
            "role": rebuild.F0G_SOURCE_ROLE,
            "schemas": list(rebuild.F0G_SOURCE_SCHEMAS),
            "access": rebuild.F0I_SOURCE_ACCESS,
            "read_only": True,
            "container_id": identity.container_id,
            "container_name": identity.container_name,
            "compose_project": identity.compose_project,
            "compose_service": identity.compose_service,
            "image_id": identity.image_id,
            "image_reference": identity.image_reference,
            "published_port": identity.published_port,
            "dump_sha256": "c" * 64,
            "aggregate_sha256": "d" * 64,
        }
        raw = rebuild._canonical_bytes(document)
        scope = rebuild.parse_f0g_source_scope(raw)
        self.assertEqual(scope.container, identity)
        self.assertEqual(scope.schemas, ("f0d", "f0e", "f0f"))
        self.assertTrue(scope.read_only)
        self.assertNotIn(b"password", raw.lower())
        self.assertNotIn(b"dsn", raw.lower())
        with self.assertRaisesRegex(rebuild.RebuildError, "F0G_SOURCE_SCOPE_REJECTED"):
            rebuild.parse_f0g_source_scope(raw + b"\n")
        for key, value in (
            ("database", rebuild.SOURCE_DATABASE_NAME),
            ("role", "f0d_migration"),
            ("schemas", ["f0d"]),
            ("read_only", False),
            ("access", "NETWORK_PASSWORD"),
            ("password", "fake"),
        ):
            changed = dict(document)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(rebuild.RebuildError):
                rebuild.parse_f0g_source_scope(rebuild._canonical_bytes(changed))

    def test_source_container_identity_and_selected_inspect_are_exact(self) -> None:
        identity = self._identity()
        raw = json.dumps(
            {
                "container_id": identity.container_id,
                "container_name": identity.container_name,
                "compose_project": identity.compose_project,
                "compose_service": identity.compose_service,
                "image_id": identity.image_id,
                "image_reference": identity.image_reference,
                "published_port": identity.published_port,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        self.assertEqual(
            rebuild.parse_source_container_identity(raw, expected_port=55432),
            identity,
        )
        inspect_lines = (
            identity.container_id,
            "/" + identity.container_name,
            identity.image_id,
            "running",
            "healthy",
            identity.compose_project,
            identity.compose_service,
            identity.image_reference,
            [{"HostIp": "127.0.0.1", "HostPort": "55432"}],
        )
        inspect_raw = b"\n".join(
            json.dumps(item, separators=(",", ":")).encode("ascii")
            for item in inspect_lines
        ) + b"\n"
        rebuild.validate_source_container_inspect(inspect_raw, identity)

        for key, value in (
            ("compose_project", "shared"),
            ("compose_service", "api"),
            ("published_port", 55433),
            ("image_reference", "postgres:latest"),
        ):
            changed = json.loads(raw)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(rebuild.RebuildError):
                rebuild.parse_source_container_identity(
                    json.dumps(changed).encode("ascii"), expected_port=55432
                )
        with self.assertRaises(rebuild.RebuildError):
            rebuild.parse_source_container_identity(
                raw[:-1] + b',"published_port":55432}', expected_port=55432
            )

        unhealthy = list(inspect_lines)
        unhealthy[4] = "unhealthy"
        with self.assertRaises(rebuild.RebuildError):
            rebuild.validate_source_container_inspect(
                b"\n".join(
                    json.dumps(item, separators=(",", ":")).encode("ascii")
                    for item in unhealthy
                )
                + b"\n",
                identity,
            )

    def test_source_aggregate_is_fixed_read_only_and_nonempty(self) -> None:
        green = (
            b"f0i_acceptance_v01|f0d_bootstrap|on|f0d_0006|26|26|248|900\n"
        )
        self.assertRegex(rebuild.parse_source_aggregate(green), r"^[0-9a-f]{64}$")
        self.assertEqual(
            rebuild.parse_source_aggregate(green),
            rebuild.parse_source_aggregate(b"BEGIN\n" + green + b"COMMIT\n"),
        )
        for red in (
            green.replace(b"|on|", b"|off|"),
            green.replace(b"f0d_0006", b"f0d_0005"),
            green.replace(b"|26|26|", b"|0|26|", 1),
            green + green,
        ):
            with self.subTest(red=red), self.assertRaises(rebuild.RebuildError):
                rebuild.parse_source_aggregate(red)

    def test_source_aggregate_methods_forward_fixed_interactive_stdin(self) -> None:
        identity = self._identity()
        runner = rebuild.CleanRebuildRound(1, {})
        runner.state.checkout = rebuild.ROOT
        runner.source_container_identity = identity
        runner.source_scope = rebuild.SourceScope(
            "127.0.0.1",
            identity.published_port,
            rebuild.SOURCE_DATABASE_NAME,
            rebuild.F0I_SOURCE_ACCESS,
            identity,
        )
        runner.f0g_source_scope = rebuild.F0GSourceScope(
            rebuild.F0G_SOURCE_DATABASE_NAME,
            rebuild.F0G_SOURCE_ROLE,
            rebuild.F0G_SOURCE_SCHEMAS,
            rebuild.F0I_SOURCE_ACCESS,
            True,
            identity,
            "c" * 64,
            "d" * 64,
        )
        f0i = b"f0i_acceptance_v01|f0d_bootstrap|on|f0d_0006|26|26|248|900\n"
        f0g = (
            "f0f_acceptance_v01|"
            + rebuild.F0G_SOURCE_ROLE
            + "|on|f0d_0004|8|42|12|19\n"
        ).encode("ascii")
        with mock.patch.object(
            rebuild,
            "_process",
            side_effect=(
                rebuild.ProcessResult(0, f0i),
                rebuild.ProcessResult(0, f0g),
            ),
        ) as process:
            runner._source_database_aggregate()
            runner._f0g_source_aggregate()
        calls = process.call_args_list
        self.assertEqual(len(calls), 2)
        for call in calls:
            arguments = call.args[0]
            self.assertEqual(arguments[:3], ("docker", "exec", "-i"))
            self.assertIn("--no-password", arguments)
            self.assertNotIn("-t", arguments)
            self.assertNotIn("--tty", arguments)
            self.assertIsInstance(call.kwargs.get("input_bytes"), bytes)
        self.assertEqual(
            calls[1].kwargs["input_bytes"],
            rebuild.f0g_source_aggregate_statement(),
        )

    def test_f0g_migration_dsn_is_explicit_psycopg3(self) -> None:
        source = "postgresql://f0d_migration:fixture@127.0.0.1:31003/private"
        self.assertEqual(
            rebuild._sqlalchemy_psycopg_dsn(source),
            "postgresql+psycopg://f0d_migration:fixture@127.0.0.1:31003/private",
        )
        for rejected in (
            "postgresql+psycopg://f0d_migration:fixture@127.0.0.1:31003/private",
            "postgresql://f0d_migration@127.0.0.1:31003/private",
            "postgresql://f0d_migration:fixture@127.0.0.1/private",
            "postgresql://f0d_migration:fixture@127.0.0.1:31003/private?sslmode=disable",
        ):
            with self.subTest(rejected=rejected), self.assertRaisesRegex(
                rebuild.RebuildError, "DATABASE_DSN_REJECTED"
            ):
                rebuild._sqlalchemy_psycopg_dsn(rejected)

    def test_new_f0g_template_database_gets_minimal_f0d_bootstrap(self) -> None:
        runner = rebuild.CleanRebuildRound(1, {})
        database = "f0g_template_" + UUID4_HEX
        isolation = SimpleNamespace(
            f0g_template_database=database,
            f0i_template_database="f0i_template_" + UUID4_HEX,
        )
        with (
            mock.patch.object(runner, "_frozen_isolation", return_value=isolation),
            mock.patch.object(runner, "_isolated_scalar", side_effect=("0", "1")),
            mock.patch.object(
                rebuild,
                "_process",
                return_value=rebuild.ProcessResult(0, b""),
            ) as process,
        ):
            runner._create_isolated_database(database)
        self.assertEqual(process.call_count, 2)
        create_database = process.call_args_list[0].args[0]
        bootstrap = process.call_args_list[1].args[0]
        self.assertIn("CREATE DATABASE", create_database[-1])
        self.assertIn("OWNER f0d_migration", create_database[-1])
        self.assertIn("--dbname=" + database, bootstrap)
        statement = bootstrap[-1]
        for required in (
            "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
            "REVOKE ALL ON DATABASE",
            "GRANT CONNECT, CREATE ON DATABASE",
            "GRANT CONNECT ON DATABASE",
            "CREATE SCHEMA f0d AUTHORIZATION f0d_migration",
            "REVOKE ALL ON SCHEMA f0d FROM PUBLIC",
        ):
            self.assertIn(required, statement)

    def test_f0g_aggregate_and_dump_bind_fixed_source_without_run_noise(self) -> None:
        dump_source = inspect.getsource(rebuild.CleanRebuildRound._f0g_source_dump)
        for schema in rebuild.F0G_SOURCE_SCHEMAS:
            self.assertIn('"--schema=' + schema + '"', dump_source)
        green = (
            "f0f_acceptance_v01|"
            + rebuild.F0G_SOURCE_ROLE
            + "|on|f0d_0004|8|42|12|19\n"
        ).encode("ascii")
        self.assertRegex(rebuild.parse_f0g_source_aggregate(green), r"^[0-9a-f]{64}$")
        self.assertEqual(
            rebuild.parse_f0g_source_aggregate(green),
            rebuild.parse_f0g_source_aggregate(b"BEGIN\n" + green + b"COMMIT\n"),
        )
        for red in (
            green.replace(b"|on|", b"|off|"),
            green.replace(b"f0d_0004", b"f0d_0006"),
            green.replace(b"|8|", b"|0|", 1),
            green + green,
        ):
            with self.subTest(red=red), self.assertRaises(rebuild.RebuildError):
                rebuild.parse_f0g_source_aggregate(red)

        def dump(
            *,
            token: str,
            first_row: bytes = b"1\talpha",
            include_f0e: bool = True,
        ) -> bytes:
            sections = [
                    b"-- PostgreSQL database dump\n",
                    f"\\restrict {token}\n".encode("ascii"),
                    b"SET statement_timeout = 0;\n",
                    b"COPY f0d.enterprise (id, value) FROM stdin;\n",
                    b"1\troot\n",
                    b"\\.\n",
            ]
            if include_f0e:
                sections.extend(
                    (
                        b"COPY f0e.local_ocr_run (id, value) FROM stdin;\n",
                        b"1\tocr\n",
                        b"\\.\n",
                    )
                )
            sections.extend(
                (
                    b"COPY f0f.controlled_body (id, value) FROM stdin;\n",
                    first_row + b"\n",
                    b"2\tbeta\n",
                    b"\\.\n",
                    b"SELECT pg_catalog.setval('f0f.example_seq'::regclass, 2, true);\n",
                    f"\\unrestrict {token}\n".encode("ascii"),
                )
            )
            return b"".join(sections)

        with tempfile.TemporaryDirectory(
            prefix="anhuan-f111-f0g-dump-", dir="/private/tmp"
        ) as raw_root:
            root = Path(raw_root)
            first = root / "first.sql"
            second = root / "second.sql"
            changed = root / "changed.sql"
            incomplete = root / "incomplete.sql"
            for path, body in (
                (first, dump(token="first-token")),
                (second, dump(token="second-token")),
                (changed, dump(token="third-token", first_row=b"1\tchanged")),
                (incomplete, dump(token="fourth-token", include_f0e=False)),
            ):
                path.write_bytes(body)
                path.chmod(0o600)
            self.assertEqual(
                rebuild.normalized_f0g_data_dump_digest(first),
                rebuild.normalized_f0g_data_dump_digest(second),
            )
            self.assertNotEqual(
                rebuild.normalized_f0g_data_dump_digest(first),
                rebuild.normalized_f0g_data_dump_digest(changed),
            )
            with self.assertRaisesRegex(
                rebuild.RebuildError, "F0G_SOURCE_DUMP_INCOMPLETE"
            ):
                rebuild.normalized_f0g_data_dump_digest(incomplete)

    def test_manifest_requires_four_unique_canonical_registered_bytes(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="anhuan-f111-manifest-", dir="/private/tmp"
        ) as raw_root:
            root = Path(raw_root)
            root.chmod(0o700)
            manifest = []
            for index in range(4):
                body = (f"fixture-{index}-" * 16).encode("ascii")
                path = root / f"opaque-{index}.pdf"
                path.write_bytes(body)
                manifest.append(
                    {
                        "path": str(path),
                        "sha256": hashlib.sha256(body).hexdigest(),
                        "content_type": "application/pdf",
                    }
                )
            raw = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
            fixtures = rebuild.parse_fixture_manifest(raw)
            self.assertEqual(len(fixtures), 4)
            self.assertTrue(all(item.size > 0 for item in fixtures))

            manifest[0]["sha256"] = "0" * 64
            with self.assertRaises(rebuild.RebuildError):
                rebuild.parse_fixture_manifest(
                    json.dumps(manifest, separators=(",", ":")).encode("utf-8")
                )

    def test_plain_data_dump_digest_ignores_run_noise_but_binds_every_row(self) -> None:
        def body(*, token: str, first_row: bytes = b"1\talpha") -> bytes:
            chunks = [
                b"-- PostgreSQL database dump\n",
                f"\\restrict {token}\n".encode("ascii"),
                b"SET statement_timeout = 0;\n",
                b"SELECT pg_catalog.set_config('search_path', '', false);\n",
            ]
            for schema, table in sorted(rebuild.REQUIRED_SOURCE_TABLES):
                chunks.extend(
                    (
                        f"COPY {schema}.{table} (id, value) FROM stdin;\n".encode("ascii"),
                        first_row + b"\n",
                        b"2\tbeta\n",
                        b"\\.\n",
                    )
                )
            chunks.extend(
                (
                    b"SELECT pg_catalog.setval('f0i.example_seq'::regclass, 2, true);\n",
                    f"\\unrestrict {token}\n".encode("ascii"),
                )
            )
            return b"".join(chunks)

        with tempfile.TemporaryDirectory(
            prefix="anhuan-f111-dump-", dir="/private/tmp"
        ) as raw_root:
            root = Path(raw_root)
            first = root / "first.sql"
            second = root / "second.sql"
            changed = root / "changed.sql"
            for path, raw in (
                (first, body(token="first-token")),
                (second, body(token="second-token")),
                (changed, body(token="third-token", first_row=b"1\tchanged")),
            ):
                path.write_bytes(raw)
                path.chmod(0o600)
            self.assertEqual(
                rebuild.normalized_data_dump_digest(first),
                rebuild.normalized_data_dump_digest(second),
            )
            self.assertNotEqual(
                rebuild.normalized_data_dump_digest(first),
                rebuild.normalized_data_dump_digest(changed),
            )

    def test_source_restore_fails_closed_on_aggregate_target_or_restore_output(self) -> None:
        round_runner = rebuild.CleanRebuildRound(1, {})
        round_runner._prepare_runtime_scope()
        self.addCleanup(round_runner._cleanup)
        with tempfile.TemporaryDirectory(
            prefix="anhuan-f111-restore-", dir="/private/tmp"
        ) as raw_root:
            root = Path(raw_root)
            root.chmod(0o700)
            round_runner.state.checkout = rebuild.ROOT
            round_runner.state.target_database_environment_file = root / "target.env"
            round_runner.state.source_data_dump = root / "source.dump"
            round_runner.state.source_data_after_dump = root / "source-after.dump"
            round_runner.state.target_data_dump = root / "target.dump"
            identity = self._identity()
            round_runner.source_scope = rebuild.SourceScope(
                "127.0.0.1",
                identity.published_port,
                rebuild.SOURCE_DATABASE_NAME,
                rebuild.F0I_SOURCE_ACCESS,
                identity,
            )
            round_runner.source_container_identity = identity
            round_runner.fixture_inputs = (
                rebuild.FixtureInput(
                    root / "opaque.pdf",
                    "a" * 64,
                    1,
                    1,
                    1,
                    "application/pdf",
                ),
            )
            # The restore contract requires proof that the already-validated
            # four-object selection was materialized.  Its detailed identity
            # is exercised by the dedicated selection tests; this test only
            # drives the source/target restore failure ordering.
            round_runner.fixture_selection_copies = (mock.sentinel.selection_copy,)

            with (
                mock.patch.object(round_runner, "_source_container_inspect"),
                mock.patch.object(
                    round_runner,
                    "_source_database_aggregate",
                    side_effect=("a" * 64, "b" * 64),
                ),
                mock.patch.object(
                    round_runner, "_source_dump", return_value="c" * 64
                ) as dump,
                mock.patch.object(rebuild, "_process") as restore,
            ):
                with self.assertRaisesRegex(rebuild.RebuildError, "F0I_SOURCE_MUTATED"):
                    round_runner._restore_fixture_source()
                dump.assert_called_once()
                restore.assert_not_called()

            with (
                mock.patch.object(round_runner, "_source_container_inspect"),
                mock.patch.object(
                    round_runner,
                    "_source_database_aggregate",
                    side_effect=("a" * 64, "a" * 64),
                ),
                mock.patch.object(round_runner, "_source_dump", return_value="c" * 64),
                mock.patch.object(round_runner, "_target_dump", return_value="d" * 64),
                mock.patch.object(
                    rebuild, "_process", return_value=rebuild.ProcessResult(0, b"")
                ),
            ):
                with self.assertRaisesRegex(
                    rebuild.RebuildError, "F0I_TARGET_RESTORE_MISMATCH"
                ):
                    round_runner._restore_fixture_source()

            with (
                mock.patch.object(round_runner, "_source_container_inspect"),
                mock.patch.object(
                    round_runner,
                    "_source_database_aggregate",
                    side_effect=("a" * 64, "a" * 64),
                ),
                mock.patch.object(round_runner, "_source_dump", return_value="c" * 64),
                mock.patch.object(round_runner, "_target_dump") as target_dump,
                mock.patch.object(
                    rebuild,
                    "_process",
                    side_effect=(
                        rebuild.ProcessResult(0, b""),
                        rebuild.ProcessResult(0, b"COPY 1\n"),
                    ),
                ),
            ):
                with self.assertRaisesRegex(
                    rebuild.RebuildError, "F0I_TARGET_RESTORE_RED"
                ):
                    round_runner._restore_fixture_source()
                target_dump.assert_not_called()

    def test_dump_contract_is_read_only_and_never_carries_source_credentials(self) -> None:
        source_dump = inspect.getsource(rebuild.CleanRebuildRound._source_dump)
        aggregate = inspect.getsource(rebuild.CleanRebuildRound._source_database_aggregate)
        source_inspect = inspect.getsource(rebuild.CleanRebuildRound._source_container_inspect)
        restore = inspect.getsource(rebuild.CleanRebuildRound._restore_fixture_source)
        for marker in (
            '"--data-only"',
            '"--schema=f0d"',
            '"--schema=f0i"',
            '"--exclude-table-data=f0d.alembic_version"',
            '"docker"',
            '"exec"',
            '"postgres"',
        ):
            self.assertIn(marker, source_dump)
        self.assertIn("READ ONLY", aggregate)
        self.assertIn("_SOURCE_INSPECT_FORMAT", source_inspect)
        self.assertNotIn("Config.Env", rebuild._SOURCE_INSPECT_FORMAT)
        self.assertIn("TRUNCATE TABLE f0d.capability_gate", restore)
        self.assertIn('"--file=/input/source.sql"', restore)
        source_only = source_dump + aggregate + source_inspect
        for forbidden in (
            "source_database_endpoint.password",
            "f0i_source_dsn",
            "TRUNCATE",
            "DELETE FROM",
            "ALTER TABLE",
            "CREATE TABLE",
            "container rm",
            "restart",
        ):
            self.assertNotIn(forbidden, source_only)


class FixtureHttpE2ETests(unittest.TestCase):
    @staticmethod
    def _metric_output(*, red: str | None = None) -> bytes:
        return (
            " ".join(
                f"{name}={1 if name == red else 0}"
                for name in rebuild.REVERSE_METRICS
            )
            + "\n"
        ).encode("ascii")

    def test_registered_fixture_e2e_requires_every_reverse_metric_zero(self) -> None:
        runner = rebuild.CleanRebuildRound(1, {})
        runner.state.checkout = rebuild.ROOT
        runner.state.secrets_directory = rebuild.ROOT
        runner.ports = {"api": 31001, "keycloak": 31002}
        runner.python_bridge_identity = rebuild.launcher_python_identity()
        with (
            mock.patch.object(
                rebuild,
                "verify_checkout_python_bridge",
                return_value=runner.python_bridge_identity,
            ),
            mock.patch.object(
                rebuild,
                "_process",
                return_value=rebuild.ProcessResult(0, self._metric_output()),
            ) as process,
        ):
            digest = runner._fixture_http_e2e()
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        arguments = process.call_args.args[0]
        self.assertEqual(
            arguments[:2],
            (str(rebuild.ROOT / ".venv/bin/python"), "-B"),
        )
        environment = process.call_args.kwargs["environment"]
        self.assertEqual(environment["F111_REVERSE_PROJECT"], runner.identity.project)
        self.assertIn("F111_REVERSE_COMPOSE_OVERRIDE", environment)

        with (
            mock.patch.object(
                rebuild,
                "verify_checkout_python_bridge",
                return_value=runner.python_bridge_identity,
            ),
            mock.patch.object(
                rebuild,
                "_process",
                return_value=rebuild.ProcessResult(
                    0, self._metric_output(red="valid_http_e2e")
                ),
            ),
        ):
            with self.assertRaises(rebuild.RebuildError):
                runner._fixture_http_e2e()

    def test_root_restore_f1_and_fixture_e2e_order_is_fixed(self) -> None:
        migration = inspect.getsource(
            rebuild.CleanRebuildRound._run_migrations_and_pg_contract
        )
        self.assertLess(migration.index('"ROOT_MIGRATION_RED"'), migration.index("self._restore_fixture_source()"))
        self.assertLess(migration.index("self._restore_fixture_source()"), migration.index("self._prepare_f0i_template()"))
        self.assertLess(migration.index("self._prepare_f0i_template()"), migration.index('"/app/infra/f1/migrate_f1.py"'))
        run = inspect.getsource(rebuild.CleanRebuildRound.run)
        self.assertLess(run.index("self._bootstrap_database()"), run.index("self._prepare_f0g_template()"))
        self.assertLess(run.index("self._prepare_f0g_template()"), run.index("self._run_migrations_and_pg_contract()"))
        self.assertLess(run.index("self._start_compose()"), run.index("self._fixture_http_e2e()"))
        self.assertLess(run.index("self._fixture_http_e2e()"), run.index("self._leak_contract"))
        self.assertLess(run.index("self._leak_contract"), run.index("self._frozen_templates_unchanged()"))
        self.assertLess(run.index("self._frozen_f0_projects_absent()", run.index("self._leak_contract")), run.index("self._fixture_source_unchanged()"))


class FrozenF0LiveBoundaryTests(unittest.TestCase):
    def test_managed_projects_and_exact_j_container_must_all_be_absent(self) -> None:
        token = uuid.uuid4().hex
        project = "anhuan-f111-repair-f0-" + token
        retrieval = "anhuan-f111-repair-j0-" + token + "-opensearch"
        isolation = SimpleNamespace(
            managed_project_names=(project, project + "-f0e"),
            managed_container_names=(retrieval,),
        )
        observed: list[tuple[str, ...]] = []

        def absent(arguments: object, **_kwargs: object) -> rebuild.ProcessResult:
            command = tuple(arguments)  # type: ignore[arg-type]
            observed.append(command)
            if command[:3] == ("docker", "container", "inspect"):
                return rebuild.ProcessResult(1, b"absent\n")
            return rebuild.ProcessResult(0, b"")

        with (
            mock.patch.object(rebuild, "validate_frozen_f0_isolation"),
            mock.patch.object(rebuild, "_process", side_effect=absent),
        ):
            rebuild.verify_frozen_f0_project_absence(
                isolation, {}, cwd=rebuild.ROOT  # type: ignore[arg-type]
            )
        filters = {
            command[-1]
            for command in observed
            if "--filter" in command
        }
        self.assertIn(
            "label=com.docker.compose.project=" + project,
            filters,
        )
        self.assertIn("label=com.anhuan.f111.project=" + project, filters)
        self.assertIn("name=" + project, filters)
        self.assertIn(("docker", "container", "inspect", retrieval), observed)

        with (
            mock.patch.object(rebuild, "validate_frozen_f0_isolation"),
            mock.patch.object(
                rebuild,
                "_process",
                return_value=rebuild.ProcessResult(0, b"opaque-id\n"),
            ),
            self.assertRaisesRegex(
                rebuild.RebuildError, "FROZEN_F0_PROJECT_COLLISION"
            ),
        ):
            rebuild.verify_frozen_f0_project_absence(
                isolation, {}, cwd=rebuild.ROOT  # type: ignore[arg-type]
            )

    def test_database_snapshot_rejects_scratch_residual_and_oid_replacement(self) -> None:
        project_id = uuid.uuid4()
        project = rebuild.PROJECT_PREFIX + project_id.hex
        main = rebuild.DATABASE_PREFIX + project_id.hex
        f0g = "f111_f0g_template_" + project_id.hex
        f0i = "f111_f0i_template_" + project_id.hex
        scratch = "f111_f0d_" + project_id.hex
        isolation = SimpleNamespace(
            project_id=project_id,
            f0g_template_database=f0g,
            f0i_template_database=f0i,
            managed_database_names=(f0g, f0i, scratch),
        )

        def output(rows: list[tuple[str, int, str]]) -> rebuild.ProcessResult:
            return rebuild.ProcessResult(
                0,
                b"".join(
                    f"{name}|{oid}|{owner}\n".encode("ascii")
                    for name, oid, owner in sorted(rows)
                ),
            )

        baseline_rows = [
            (main, 10, "f0d_bootstrap"),
            (f0g, 11, "f0d_migration"),
            (f0i, 12, "f0d_migration"),
        ]
        with (
            mock.patch.object(rebuild, "validate_frozen_f0_isolation"),
            mock.patch.object(rebuild, "_process", return_value=output(baseline_rows)),
        ):
            baseline = rebuild.capture_frozen_f0_database_snapshot(
                project, isolation, {}, cwd=rebuild.ROOT  # type: ignore[arg-type]
            )
        self.assertRegex(baseline.sha256, r"^[0-9a-f]{64}$")

        with (
            mock.patch.object(rebuild, "validate_frozen_f0_isolation"),
            mock.patch.object(
                rebuild,
                "_process",
                return_value=output(
                    baseline_rows + [(scratch, 13, "f0d_migration")]
                ),
            ),
            self.assertRaisesRegex(
                rebuild.RebuildError, "FROZEN_F0_DATABASE_RESIDUAL"
            ),
        ):
            rebuild.capture_frozen_f0_database_snapshot(
                project, isolation, {}, cwd=rebuild.ROOT  # type: ignore[arg-type]
            )

        with (
            mock.patch.object(rebuild, "validate_frozen_f0_isolation"),
            mock.patch.object(
                rebuild,
                "_process",
                return_value=output(
                    [(name, oid + 100, owner) for name, oid, owner in baseline_rows]
                ),
            ),
        ):
            replaced = rebuild.capture_frozen_f0_database_snapshot(
                project, isolation, {}, cwd=rebuild.ROOT  # type: ignore[arg-type]
            )
        self.assertNotEqual(replaced, baseline)

    def test_template_post_dumps_cannot_be_faked_or_omitted(self) -> None:
        runner = rebuild.CleanRebuildRound(1, {})
        isolation = SimpleNamespace(
            f0g_template_database="f0g-template",
            f0i_template_database="f0i-template",
        )
        runner.state.f0g_target_data_after_dump = Path("/private/tmp/f0g-after")
        runner.state.f0i_template_data_after_dump = Path("/private/tmp/f0i-after")
        runner.f0g_source_dump_sha256 = "a" * 64
        runner.f0i_template_dump_sha256 = "b" * 64
        with (
            mock.patch.object(runner, "_frozen_isolation", return_value=isolation),
            mock.patch.object(
                runner,
                "_isolated_database_dump",
                side_effect=("a" * 64, "b" * 64),
            ) as dump,
        ):
            runner._frozen_templates_unchanged()
        self.assertEqual(dump.call_count, 2)

        with (
            mock.patch.object(runner, "_frozen_isolation", return_value=isolation),
            mock.patch.object(
                runner,
                "_isolated_database_dump",
                side_effect=("a" * 64, "c" * 64),
            ),
            self.assertRaisesRegex(
                rebuild.RebuildError, "FROZEN_F0_TEMPLATE_MUTATED"
            ),
        ):
            runner._frozen_templates_unchanged()


class PreparedPrimaryStackContextTests(unittest.TestCase):
    def test_context_prepares_for_caller_owned_formal_and_always_cleans(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="anhuan-f111-context-", dir="/private/tmp"
        ) as raw_root:
            root = Path(raw_root)
            secrets = root / "secrets"
            provider = root / "provider"
            secrets.mkdir(mode=0o700)
            provider.mkdir(mode=0o700)
            key = root / "f0i-key"
            key.write_bytes(b"opaque-key\n")
            key.chmod(0o600)
            context = rebuild.PreparedPrimaryStackContext({})
            runner = context.round
            runner.state.secrets_directory = secrets
            runner.state.provider_secrets_directory = provider
            runner.state.f0i_key_file = key
            checkout = root / "checkout"
            checkout.mkdir(mode=0o700)
            runner.state.checkout = checkout
            runner.checkout_identity = rebuild.checkout_identity(checkout)
            runner.python_bridge_identity = rebuild.launcher_python_identity()
            runner.frozen_f0_inputs = mock.sentinel.frozen_f0_inputs
            runner.frozen_database_snapshot = rebuild.FrozenF0DatabaseSnapshot(
                ((runner.identity.database, 1, "f0d_bootstrap"),),
                "d" * 64,
            )
            runner.source_snapshot = rebuild.SourceSnapshot(
                (
                    rebuild.SourceEntry(
                        Path("tracked"), 0o644, "a" * 64, 1
                    ),
                ),
                "b" * 64,
                "c" * 64,
            )
            runner.ports = {
                name: 30000 + index
                for index, name in enumerate(rebuild.PORT_NAMES)
            }
            order: list[str] = []

            def mark(name: str, result: object = None):
                def invoke(*_args: object, **_kwargs: object) -> object:
                    order.append(name)
                    return result

                return invoke

            methods = (
                "_prepare_runtime_scope",
                "_validate_scope",
                "_probe_absence",
                "_reserve_ports",
                "_create_clean_checkout",
                "_prepare_secrets",
                "_frozen_f0_projects_absent",
                "_validate_compose",
                "_build_images",
                "_start_postgres",
                "_bootstrap_database",
                "_prepare_f0g_template",
                "_run_migrations_and_pg_contract",
                "_record_frozen_database_baseline",
                "_seed_f1",
                "_start_compose",
                "_runtime_inventory",
            )
            patches = [
                mock.patch.object(runner, name, side_effect=mark(name))
                for name in methods
            ]
            with ExitStack() as stack:
                for patcher in patches:
                    stack.enter_context(patcher)
                stack.enter_context(
                    mock.patch.object(
                        runner,
                        "_frozen_templates_unchanged",
                        side_effect=mark("_frozen_templates_unchanged"),
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        runner,
                        "_frozen_database_unchanged",
                        side_effect=mark("_frozen_database_unchanged"),
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        runner,
                        "_fixture_source_unchanged",
                        side_effect=mark("_fixture_source_unchanged"),
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        runner,
                        "_f0g_source_unchanged",
                        side_effect=mark("_f0g_source_unchanged"),
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        runner,
                        "_source_unchanged",
                        side_effect=mark("_source_unchanged"),
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        runner,
                        "_validate_delivery_checkout",
                        side_effect=mark("_validate_delivery_checkout"),
                    )
                )
                stack.enter_context(
                    mock.patch.object(
                        runner,
                        "_frozen_inputs_unchanged",
                        side_effect=mark("_frozen_inputs_unchanged"),
                    )
                )
                cleanup = stack.enter_context(
                    mock.patch.object(
                        runner, "_cleanup", side_effect=mark("_cleanup", 0)
                    )
                )
                with context as prepared:
                    self.assertEqual(prepared.project, runner.identity.project)
                    self.assertEqual(prepared.ports, runner.ports)
                    with self.assertRaisesRegex(
                        rebuild.RebuildError,
                        "PREPARED_STACK_FORMAL_CONFIG_FORBIDDEN",
                    ):
                        prepared.formal_config_payload(timeout_seconds=600)
                    with self.assertRaisesRegex(
                        rebuild.RebuildError, "PREPARED_STACK_PUBLICATION_BLOCKED"
                    ):
                        context.assert_closed_clean()
            context.assert_closed_clean()
            self.assertEqual(order[: len(methods)], list(methods))
            self.assertIn("_validate_delivery_checkout", order)
            self.assertEqual(
                order[-7:],
                [
                    "_frozen_templates_unchanged",
                    "_frozen_database_unchanged",
                    "_frozen_f0_projects_absent",
                    "_fixture_source_unchanged",
                    "_f0g_source_unchanged",
                    "_source_unchanged",
                    "_cleanup",
                ],
            )
            cleanup.assert_called_once()

    def test_context_entry_failure_cleans_without_a_formal_verdict(self) -> None:
        context = rebuild.PreparedPrimaryStackContext({})
        runner = context.round
        with (
            mock.patch.object(runner, "_validate_scope"),
            mock.patch.object(
                runner,
                "_probe_absence",
                side_effect=rebuild.RebuildError("DOCKER_BASELINE_REJECTED"),
            ),
            mock.patch.object(runner, "_cleanup", return_value=0) as cleanup,
        ):
            with self.assertRaisesRegex(
                rebuild.RebuildError, "DOCKER_BASELINE_REJECTED"
            ):
                context.__enter__()
        cleanup.assert_called_once()


class IsolationContractTests(unittest.TestCase):
    def _compose_payload(self, identity: rebuild.RoundIdentity) -> dict:
        ports = {
            "api": 31001,
            "keycloak": 31002,
            "minio_api": 31003,
            "minio_console": 31004,
            "redis": 31005,
            "prometheus": 31006,
            "grafana": 31007,
            "jaeger_ui": 31008,
            "jaeger_grpc": 31009,
            "jaeger_http": 31010,
            "ragflow_api": 31011,
            "ragflow_http": 31012,
            "web": 31013,
            "postgres": 31014,
        }
        service_names = rebuild.EXPECTED_SERVICES
        services: dict[str, object] = {}
        for name in service_names:
            environment: dict[str, str] = {}
            if name in {"api", "worker", "dispatcher"}:
                environment.update(
                    {
                        "F1_PG_HOST": "host.docker.internal",
                        "F1_PG_PORT": str(ports["postgres"]),
                        "F1_PG_DATABASE": identity.database,
                    }
                )
            image = rebuild.expected_local_image(identity, name)
            services[name] = {
                "image": image or "pinned@sha256:" + "d" * 64,
                "labels": {
                    "anhuan.scope": "f111-repair",
                    "anhuan.repair-project": identity.project,
                },
                "environment": environment,
                "restart": "no",
            }
        port_targets = {
            "api": ("api", 8001),
            "keycloak": ("keycloak", 8080),
            "minio_api": ("minio", 9000),
            "minio_console": ("minio", 9001),
            "redis": ("redis", 6379),
            "prometheus": ("prometheus", 9090),
            "grafana": ("grafana", 3000),
            "jaeger_ui": ("jaeger", 16686),
            "jaeger_grpc": ("jaeger", 4317),
            "jaeger_http": ("jaeger", 4318),
            "ragflow_api": ("ragflow", 9380),
            "ragflow_http": ("ragflow", 80),
            "web": ("web", 80),
        }
        for key, (service, target) in port_targets.items():
            services[service].setdefault("ports", []).append(  # type: ignore[union-attr]
                {
                    "host_ip": "127.0.0.1",
                    "published": str(ports[key]),
                    "target": target,
                }
            )
        return {
            "name": identity.project,
            "services": services,
            "volumes": {
                name: {"name": identity.project + "_" + name}
                for name in rebuild.EXPECTED_VOLUMES
            },
            "networks": {"f1net": {"name": identity.project + "_f1net"}},
        }

    def test_effective_compose_is_strongly_bound_to_round(self) -> None:
        identity = rebuild.RoundIdentity.create(
            1, uuid_factory=lambda: uuid.UUID(hex=UUID4_HEX)
        )
        payload = self._compose_payload(identity)
        expected_ports = {"postgres": 31014}
        for key, (service, target) in rebuild.PUBLISHED_PORTS.items():
            matches = [
                port
                for port in payload["services"][service].get("ports", [])  # type: ignore[union-attr]
                if int(port["target"]) == target
            ]
            self.assertEqual(len(matches), 1)
            expected_ports[key] = int(matches[0]["published"])
        self.assertEqual(
            rebuild.validate_compose_payload(
                payload,
                identity,
                31014,
                expected_ports=expected_ports,
            ),
            len(rebuild.EXPECTED_SERVICES),
        )
        api_port = payload["services"]["api"]["ports"][0]  # type: ignore[index]
        keycloak_port = payload["services"]["keycloak"]["ports"][0]  # type: ignore[index]
        api_port["published"], keycloak_port["published"] = (
            keycloak_port["published"],
            api_port["published"],
        )
        with self.assertRaises(rebuild.RebuildError):
            rebuild.validate_compose_payload(
                payload,
                identity,
                31014,
                expected_ports=expected_ports,
            )
        api_port["published"], keycloak_port["published"] = (
            keycloak_port["published"],
            api_port["published"],
        )
        payload["services"]["api"]["environment"]["F1_PG_DATABASE"] = "shared"  # type: ignore[index]
        with self.assertRaises(rebuild.RebuildError):
            rebuild.validate_compose_payload(payload, identity, 31014)

    def test_shared_or_broad_cleanup_target_is_rejected(self) -> None:
        identity = rebuild.RoundIdentity.create(
            2, uuid_factory=lambda: uuid.UUID(hex=UUID4_HEX)
        )
        for value in ("anhuan-f1", "shared", "/", ""):
            with self.subTest(value=value), self.assertRaises(rebuild.RebuildError):
                rebuild.assert_owned_resource(value, identity)
        rebuild.assert_owned_resource(identity.pg_container, identity)
        rebuild.assert_owned_resource(identity.pg_volume, identity)

    def test_untracked_allowlist_rejects_secrets_and_environment_files(self) -> None:
        self.assertTrue(rebuild.untracked_delivery_allowed(Path("tests/f111_new.py")))
        self.assertTrue(
            rebuild.untracked_delivery_allowed(
                Path("infra/f1/alembic/versions/f1_0004_repair_boundaries.py")
            )
        )
        for path in (
            Path(".env"),
            Path("infra/f1/private.key"),
            Path("secrets/value"),
            Path("outside.py"),
        ):
            with self.subTest(path=path):
                self.assertFalse(rebuild.untracked_delivery_allowed(path))

    def test_public_v03_authority_is_not_part_of_the_source_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="anhuan-f111-source-authority-", dir="/private/tmp"
        ) as raw_root, tempfile.TemporaryDirectory(
            prefix="anhuan-f111-source-runtime-", dir="/private/tmp"
        ) as raw_runtime:
            root = Path(raw_root)
            runtime = Path(raw_runtime)
            root.chmod(0o700)
            runtime.chmod(0o700)
            home = runtime / "home"
            temporary = runtime / "tmp"
            home.mkdir(mode=0o700)
            temporary.mkdir(mode=0o700)
            source = root / "infra/f1/source.py"
            source.parent.mkdir(parents=True)
            source.write_text("VALUE = 1\n", encoding="utf-8")
            legacy = root / "artifacts/f1-platform-shell/v0.3/acceptance.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(b"legacy\n")
            revocation = root / "artifacts/f1-platform-shell/v0.2/revocation.json"
            revocation.parent.mkdir(parents=True)
            revocation.write_bytes(b"revoked\n")
            environment = rebuild._base_environment(
                {"HOME": str(home), "TMPDIR": str(temporary)}
            )
            git_environment = {
                **environment,
                "GIT_AUTHOR_NAME": "fixture",
                "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                "GIT_COMMITTER_NAME": "fixture",
                "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            }
            for command in (
                ("git", "init", "-q"),
                ("git", "add", "--all"),
                ("git", "commit", "-q", "-m", "base"),
            ):
                rebuild._process(
                    command,
                    cwd=root,
                    environment=git_environment,
                    timeout=30,
                )
            with mock.patch.object(rebuild, "ROOT", root):
                before = rebuild.capture_source(environment)
                legacy.unlink()
                batch = (
                    root
                    / "artifacts/f1-platform-shell/v0.3/batches"
                    / ("a" * 64)
                    / "acceptance.json"
                )
                batch.parent.mkdir(parents=True)
                batch.write_bytes(b"rejected\n")
                current = root / "artifacts/f1-platform-shell/v0.3/current.json"
                current.write_bytes(b"current\n")
                after = rebuild.capture_source(environment)
                self.assertEqual(after.sha256, before.sha256)
                self.assertEqual(
                    after.repository_state_sha256,
                    before.repository_state_sha256,
                )
                self.assertFalse(
                    any(
                        entry.relative.as_posix().startswith(
                            "artifacts/f1-platform-shell/v0.3/"
                        )
                        for entry in after.entries
                    )
                )
                destination = runtime / "delivery"
                rebuild._copy_source(after, destination)
                self.assertFalse(
                    (destination / "artifacts/f1-platform-shell/v0.3").exists()
                )
                revocation.write_bytes(b"revoked-again\n")
                changed = rebuild.capture_source(environment)
                self.assertNotEqual(changed.sha256, after.sha256)
                self.assertNotEqual(
                    changed.repository_state_sha256,
                    after.repository_state_sha256,
                )
                revocation.write_bytes(b"revoked\n")
                outside = root / "artifacts/f1-platform-shell/v0.4/current.json"
                outside.parent.mkdir(parents=True)
                outside.write_bytes(b"outside\n")
                with self.assertRaisesRegex(
                    rebuild.RebuildError, "UNTRACKED_SOURCE_REJECTED"
                ):
                    rebuild.capture_source(environment)
            self.assertTrue(
                rebuild._formal_v03_output(
                    Path("artifacts/f1-platform-shell/v0.3/current.json")
                )
            )
            for candidate in (
                Path("artifacts/f1-platform-shell/v0.3"),
                Path("artifacts/f1-platform-shell/v0.30/current.json"),
                Path("nested/artifacts/f1-platform-shell/v0.3/current.json"),
                Path("/artifacts/f1-platform-shell/v0.3/current.json"),
                Path("artifacts/f1-platform-shell/v0.3/../v0.2/revocation.json"),
            ):
                with self.subTest(candidate=candidate):
                    self.assertFalse(rebuild._formal_v03_output(candidate))

    def test_each_round_owns_private_home_and_tmpdir(self) -> None:
        runner = rebuild.CleanRebuildRound(
            1, {"DOCKER_CONFIG": "/forbidden-caller-config"}
        )
        try:
            runner._prepare_runtime_scope()
            scratch = runner.state.scratch_root
            self.assertIsNotNone(scratch)
            assert scratch is not None
            for name in ("HOME", "TMPDIR"):
                path = Path(runner.environment[name])
                self.assertEqual(path.parent, scratch)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
                self.assertNotEqual(path, Path("/private/tmp"))
            self.assertNotIn("DOCKER_CONFIG", runner.environment)
            docker_directory = Path(runner.environment["HOME"]) / ".docker"
            config = docker_directory / "config.json"
            self.assertEqual(stat.S_IMODE(docker_directory.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
            self.assertEqual(
                json.loads(config.read_bytes()),
                {
                    "cliPluginsExtraDirs": [
                        str(rebuild.DOCKER_COMPOSE_PLUGIN_DIRECTORY)
                    ]
                },
            )
            self.assertEqual(
                config.read_bytes(),
                rebuild._canonical_bytes(
                    {
                        "cliPluginsExtraDirs": [
                            str(rebuild.DOCKER_COMPOSE_PLUGIN_DIRECTORY)
                        ]
                    }
                ),
            )
        finally:
            runner._cleanup()

    def test_compose_plugin_hash_config_and_execution_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="anhuan-f111-compose-plugin-", dir="/private/tmp"
        ) as raw:
            root = Path(raw)
            root.chmod(0o700)
            binary = root / "docker-compose-real"
            binary.write_bytes(b"#!/bin/sh\nexit 0\n")
            binary.chmod(0o755)
            launcher = root / "docker-compose"
            launcher.symlink_to(binary)
            digest = hashlib.sha256(binary.read_bytes()).hexdigest()
            patches = (
                mock.patch.object(rebuild, "DOCKER_COMPOSE_LAUNCHER", launcher, create=True),
                mock.patch.object(rebuild, "DOCKER_COMPOSE_PLUGIN", binary, create=True),
                mock.patch.object(
                    rebuild, "DOCKER_COMPOSE_PLUGIN_DIRECTORY", binary.parent, create=True
                ),
                mock.patch.object(rebuild, "DOCKER_COMPOSE_SHA256", digest, create=True),
            )
            with ExitStack() as stack:
                for patcher in patches:
                    stack.enter_context(patcher)
                identity = rebuild._compose_plugin_identity()
                self.assertEqual(identity.path, binary)
                binary.write_bytes(b"#!/bin/sh\nexit 1\n")
                binary.chmod(0o755)
                with self.assertRaisesRegex(
                    rebuild.RebuildError, "COMPOSE_PLUGIN_REJECTED"
                ):
                    rebuild._compose_plugin_identity()

        runner = rebuild.CleanRebuildRound(1, {})
        try:
            runner._prepare_runtime_scope()
            home = Path(runner.environment["HOME"])
            config = home / ".docker/config.json"
            config.write_bytes(b'{"auths":{"forbidden":{}}}\n')
            config.chmod(0o600)
            with self.assertRaisesRegex(
                rebuild.RebuildError, "DOCKER_PRIVATE_CONFIG_REJECTED"
            ):
                runner._prepare_runtime_scope()
        finally:
            runner._cleanup()

    def test_compose_subprocess_rechecks_plugin_after_execution(self) -> None:
        identity = rebuild.ExecutableIdentity(
            Path("/private/tmp/compose-plugin"), 1, 2, 0o755, 3, 4, 5, "a" * 64
        )
        with mock.patch.object(
            rebuild,
            "_compose_plugin_identity",
            side_effect=(identity, rebuild.RebuildError("COMPOSE_PLUGIN_REJECTED")),
            create=True,
        ), mock.patch.object(
            rebuild.subprocess,
            "run",
            return_value=mock.Mock(returncode=0, stdout=b"ok"),
        ):
            with self.assertRaisesRegex(
                rebuild.RebuildError, "COMPOSE_PLUGIN_REJECTED"
            ):
                rebuild._process(
                    ("docker", "compose", "version"),
                    cwd=rebuild.ROOT,
                    environment={"PATH": "/usr/bin:/bin"},
                    timeout=30,
                )

    def test_e2e_selection_copies_only_four_opaque_prep_objects(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="anhuan-f111-repair-selection-", dir="/private/tmp"
        ) as raw:
            base = Path(raw)
            base.chmod(0o700)
            source = base / "published-fixtures"
            source.mkdir(mode=0o700)
            records: list[dict[str, str]] = []
            for index in range(4):
                name = str(uuid.uuid5(uuid.NAMESPACE_URL, f"fixture-{index}"))
                body = f"opaque-{index}".encode("ascii")
                path = source / name
                path.write_bytes(body)
                path.chmod(0o600)
                records.append(
                    {
                        "path": str(path),
                        "sha256": hashlib.sha256(body).hexdigest(),
                        "content_type": (
                            "image/jpeg" if index == 3 else "application/pdf"
                        ),
                    }
                )
            manifest = rebuild._canonical_bytes(records)
            rewritten, copies = rebuild.materialize_fixture_selection(
                manifest, base / "round-fixtures"
            )
            self.assertEqual(len(copies), 4)
            self.assertEqual(rebuild.verify_fixture_selection(manifest, copies), rewritten)
            self.assertTrue(
                all(Path(item["path"]).parent == base / "round-fixtures" for item in json.loads(rewritten))
            )
            copies[0].source.write_bytes(b"tampered")
            with self.assertRaises(rebuild.RebuildError):
                rebuild.verify_fixture_selection(manifest, copies)

    def test_source_bundle_materializes_private_fixture_root_and_rewrites_selection(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="anhuan-f111-repair-source-bundle-", dir="/private/tmp"
        ) as raw:
            base = Path(raw)
            base.chmod(0o700)
            source = base / "secrets"
            source.mkdir(mode=0o700)
            contracts, selection = _synthetic_source_bundle(source)
            with mock.patch.object(rebuild, "FIXTURE_PLAN_CONTRACTS", contracts):
                materialized = rebuild.materialize_fixture_source_bundle(
                    source, base / "fixture-source"
                )
                self.assertEqual(len(materialized.objects), 26)
                rewritten = rebuild.private_fixture_manifest(selection, materialized)
                fixtures = rebuild.parse_fixture_manifest(rewritten)
                self.assertEqual(len(fixtures), 4)
                self.assertTrue(
                    all(value.path.is_relative_to(materialized.root) for value in fixtures)
                )
                rebuild.verify_fixture_source_materialization(source, materialized)
                self.assertFalse((rebuild.ROOT / "fixture-source").exists())

    def test_source_bundle_tamper_offset_and_root_replacement_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="anhuan-f111-repair-source-attacks-", dir="/private/tmp"
        ) as raw:
            base = Path(raw)
            base.chmod(0o700)
            source = base / "secrets"
            source.mkdir(mode=0o700)
            contracts, _selection = _synthetic_source_bundle(source)
            with mock.patch.object(rebuild, "FIXTURE_PLAN_CONTRACTS", contracts):
                materialized = rebuild.materialize_fixture_source_bundle(
                    source, base / "fixture-source"
                )
                first = materialized.root / materialized.objects[0][0].relative
                first.write_bytes(b"tampered")
                with self.assertRaises(rebuild.RebuildError):
                    rebuild.verify_fixture_source_materialization(source, materialized)

            source_two = base / "secrets-two"
            source_two.mkdir(mode=0o700)
            contracts_two, _selection = _synthetic_source_bundle(source_two)
            bundle = source_two / rebuild.SOURCE_BUNDLE_NAME
            raw_bundle = bytearray(bundle.read_bytes())
            header_length = struct.unpack(">Q", raw_bundle[8:16])[0]
            header = json.loads(raw_bundle[16 : 16 + header_length])
            header["entries"][1]["offset"] += 1
            changed = rebuild._canonical_bytes(header)
            bundle.write_bytes(
                rebuild.SOURCE_BUNDLE_MAGIC
                + struct.pack(">Q", len(changed))
                + changed
                + raw_bundle[16 + header_length :]
            )
            bundle.chmod(0o600)
            with (
                mock.patch.object(rebuild, "FIXTURE_PLAN_CONTRACTS", contracts_two),
                self.assertRaises(rebuild.RebuildError),
            ):
                rebuild.materialize_fixture_source_bundle(
                    source_two, base / "fixture-source-two"
                )

    def test_frozen_runtime_tree_bundle_materializes_private_exact_tree(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="anhuan-f111-runtime-tree-", dir="/private/tmp"
        ) as raw:
            base = Path(raw)
            base.chmod(0o700)
            source = base / "source"
            source.mkdir(mode=0o700)
            expected = _synthetic_runtime_tree_bundle(source, "f0e")
            with mock.patch.dict(
                rebuild.FROZEN_RUNTIME_TREE_SHA256, {"f0e": expected}
            ):
                materialized = rebuild.materialize_frozen_runtime_tree(
                    source, "f0e", base / "runtime"
                )
                self.assertEqual(materialized.phase, "f0e")
                self.assertEqual(materialized.tree_sha256, expected)
                self.assertEqual(stat.S_IMODE(materialized.root.stat().st_mode), 0o700)
                self.assertEqual(len(materialized.files), 2)
                self.assertTrue(
                    all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path, _ in materialized.files)
                )
                rebuild.verify_frozen_runtime_tree(source, materialized)
                copied_root = base / "copied-bundle"
                copied_root.mkdir(mode=0o700)
                copied = rebuild.copy_frozen_runtime_tree_bundle(
                    source,
                    copied_root,
                    "f0e",
                    materialized.bundle_identity,
                )
                self.assertEqual(copied.sha256, materialized.bundle_identity.sha256)
                copied_observed, copied_tree, copied_entries, copied_writes = (
                    rebuild._frozen_runtime_tree_bundle(
                        copied_root, "f0e", None
                    )
                )
                self.assertFalse(copied_writes)
                self.assertEqual(copied_observed, copied)
                self.assertEqual(copied_tree, materialized.tree_sha256)
                self.assertEqual(copied_entries, materialized.entries)
                materialized.files[0][0].write_bytes(b"tampered\n")
                with self.assertRaises(rebuild.RebuildError):
                    rebuild.verify_frozen_runtime_tree(source, materialized)

    def test_frozen_runtime_tree_bundle_rejects_traversal_and_source_mutation(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="anhuan-f111-runtime-tree-attack-", dir="/private/tmp"
        ) as raw:
            base = Path(raw)
            base.chmod(0o700)
            source = base / "source"
            source.mkdir(mode=0o700)
            expected = _synthetic_runtime_tree_bundle(
                source, "f0f", relative_paths=("../escape",)
            )
            with (
                mock.patch.dict(
                    rebuild.FROZEN_RUNTIME_TREE_SHA256, {"f0f": expected}
                ),
                self.assertRaises(rebuild.RebuildError),
            ):
                rebuild.materialize_frozen_runtime_tree(
                    source, "f0f", base / "runtime"
                )
            self.assertFalse((base / "escape").exists())

            source_two = base / "source-two"
            source_two.mkdir(mode=0o700)
            expected_two = _synthetic_runtime_tree_bundle(source_two, "f0h")
            with mock.patch.dict(
                rebuild.FROZEN_RUNTIME_TREE_SHA256, {"f0h": expected_two}
            ):
                materialized = rebuild.materialize_frozen_runtime_tree(
                    source_two, "f0h", base / "runtime-two"
                )
                bundle = source_two / rebuild.RUNTIME_TREE_BUNDLES["f0h"][0]
                bundle.write_bytes(bundle.read_bytes() + b"x")
                bundle.chmod(0o600)
                with self.assertRaises(rebuild.RebuildError):
                    rebuild.verify_frozen_runtime_tree(source_two, materialized)

    def test_frozen_f0_inputs_are_private_exact_and_tamper_evident(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="anhuan-f111-frozen-f0-inputs-", dir="/private/tmp"
        ) as raw:
            base = Path(raw)
            base.chmod(0o700)
            source = base / "source"
            source.mkdir(mode=0o700)
            contracts, _selection = _synthetic_source_bundle(source)
            tree_digests = {
                phase: _synthetic_runtime_tree_bundle(source, phase)
                for phase in ("f0e", "f0f", "f0h")
            }
            (source / rebuild.F0F_SOURCE_KEY_NAME).write_bytes(b"k" * 32)
            (source / rebuild.F0F_SOURCE_KEY_NAME).chmod(0o600)
            project_id = uuid.uuid4()
            passwords = {
                role: (role.replace("f0d_", "") + "_" + "x" * 64)[:48]
                for role in (
                    "f0d_bootstrap",
                    "f0d_migration",
                    "f0d_runtime",
                    "f0d_worker",
                )
            }
            prepared: rebuild.FrozenF0PreparedInputs | None = None
            with (
                mock.patch.object(rebuild, "FIXTURE_PLAN_CONTRACTS", contracts),
                mock.patch.dict(
                    rebuild.FROZEN_RUNTIME_TREE_SHA256,
                    tree_digests,
                ),
            ):
                try:
                    prepared = rebuild.prepare_frozen_f0_inputs(
                        source, project_id, 55433, passwords
                    )
                    rebuild.verify_frozen_f0_inputs(source, prepared)
                    isolation = prepared.isolation
                    self.assertEqual(
                        stat.S_IMODE(isolation.runtime_root.stat().st_mode), 0o700
                    )
                    self.assertTrue(
                        all(
                            stat.S_IMODE(path.stat().st_mode) == 0o600
                            for path in (
                                prepared.config_path,
                                isolation.bootstrap_dsn_file,
                                isolation.migration_dsn_file,
                                isolation.runtime_dsn_file,
                                isolation.worker_dsn_file,
                                isolation.f0f_key_file,
                            )
                        )
                    )
                    self.assertEqual(
                        isolation.dsn_for("f0d_bootstrap").split("/")[-1],
                        "postgres",
                    )
                    self.assertEqual(
                        isolation.dsn_for("f0d_runtime").split("/")[-1],
                        isolation.f0i_template_database,
                    )
                    isolation.f0f_key_file.write_bytes(b"z" * 32)
                    isolation.f0f_key_file.chmod(0o600)
                    with self.assertRaisesRegex(
                        rebuild.RebuildError, "FROZEN_F0_KEY_MUTATED"
                    ):
                        rebuild.verify_frozen_f0_inputs(source, prepared)
                finally:
                    if prepared is not None:
                        rebuild._remove_frozen_f0_runtime_root(
                            prepared.isolation.runtime_root,
                            project_id,
                            prepared.runtime_root_identity,
                        )
            self.assertFalse(
                any(
                    Path("/private/tmp").glob(
                        f"anhuan-f111-repair-f0-{project_id.hex}-*"
                    )
                )
            )

    def test_fixed_ignored_plans_are_materialized_only_inside_random_checkout(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="anhuan-f111-plan-source-", dir="/private/tmp"
        ) as raw_source, tempfile.TemporaryDirectory(
            prefix="anhuan-f111-plan-checkout-", dir="/private/tmp"
        ) as raw_checkout:
            source = Path(raw_source)
            checkout = Path(raw_checkout)
            source.chmod(0o700)
            checkout.chmod(0o700)
            route = b'{"kind":"route"}\n'
            native = b'{"kind":"native"}\n'
            for name, body in (
                ("fixture_route_plan_json", route),
                ("fixture_native_plan_json", native),
            ):
                target = source / name
                target.write_bytes(body)
                target.chmod(0o600)
            contracts = {
                "fixture_route_plan_json": (
                    Path("artifacts/fixture-routing/v0.1/route-plan.json"),
                    hashlib.sha256(route).hexdigest(),
                ),
                "fixture_native_plan_json": (
                    Path("artifacts/fixture-native-plan/v0.1/full-plan.json"),
                    hashlib.sha256(native).hexdigest(),
                ),
            }
            with mock.patch.object(rebuild, "FIXTURE_PLAN_CONTRACTS", contracts):
                identities = rebuild.materialize_fixture_plans(source, checkout)
                rebuild.verify_fixture_plan_sources(source, identities)
            self.assertEqual(
                (checkout / contracts["fixture_route_plan_json"][0]).read_bytes(),
                route,
            )
            self.assertEqual(
                stat.S_IMODE(
                    (checkout / contracts["fixture_native_plan_json"][0]).stat().st_mode
                ),
                0o600,
            )
            self.assertFalse((rebuild.ROOT / contracts["fixture_route_plan_json"][0]).exists())

    def test_missing_wrong_or_replaced_plan_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="anhuan-f111-plan-source-", dir="/private/tmp"
        ) as raw_source, tempfile.TemporaryDirectory(
            prefix="anhuan-f111-plan-checkout-", dir="/private/tmp"
        ) as raw_checkout:
            source = Path(raw_source)
            checkout = Path(raw_checkout)
            source.chmod(0o700)
            checkout.chmod(0o700)
            body = b"fixed-plan\n"
            contracts = {
                "fixture_route_plan_json": (
                    Path("artifacts/fixture-routing/v0.1/route-plan.json"),
                    hashlib.sha256(body).hexdigest(),
                )
            }
            with mock.patch.object(rebuild, "FIXTURE_PLAN_CONTRACTS", contracts):
                with self.assertRaises(rebuild.RebuildError):
                    rebuild.materialize_fixture_plans(source, checkout)
                source_file = source / "fixture_route_plan_json"
                source_file.write_bytes(b"wrong\n")
                source_file.chmod(0o600)
                with self.assertRaises(rebuild.RebuildError):
                    rebuild.materialize_fixture_plans(source, checkout)
                source_file.write_bytes(body)
                source_file.chmod(0o600)
                identities = rebuild.materialize_fixture_plans(source, checkout)
                source_file.unlink()
                source_file.write_bytes(body)
                source_file.chmod(0o600)
                with self.assertRaises(rebuild.RebuildError):
                    rebuild.verify_fixture_plan_sources(source, identities)

    def test_checkout_gate_rejects_tracked_plan_bridge_and_arbitrary_drift(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="anhuan-f111-checkout-gate-", dir="/private/tmp"
        ) as raw_checkout, tempfile.TemporaryDirectory(
            prefix="anhuan-f111-checkout-input-", dir="/private/tmp"
        ) as raw_source:
            checkout = Path(raw_checkout)
            source = Path(raw_source)
            checkout.chmod(0o700)
            source.chmod(0o700)
            (checkout / "src/web").mkdir(parents=True)
            (checkout / ".gitignore").write_text(
                ".venv/\nfixtures/\nartifacts/*/v0.1/*.json\n"
                "src/web/node_modules/\nsrc/web/dist/\n",
                encoding="utf-8",
            )
            (checkout / "src/web/package.json").write_text("{}\n", encoding="utf-8")
            runtime_home = source / "home"
            runtime_temporary = source / "tmp"
            runtime_home.mkdir(mode=0o700)
            runtime_temporary.mkdir(mode=0o700)
            environment = rebuild._base_environment(
                {"HOME": str(runtime_home), "TMPDIR": str(runtime_temporary)}
            )
            git_environment = {
                **environment,
                "GIT_AUTHOR_NAME": "fixture",
                "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                "GIT_COMMITTER_NAME": "fixture",
                "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            }
            for command in (
                ("git", "init", "-q"),
                ("git", "add", "--all"),
                ("git", "commit", "-q", "-m", "fixture"),
            ):
                rebuild._process(
                    command,
                    cwd=checkout,
                    environment=git_environment,
                    timeout=30,
                )
            payloads = {
                "fixture_core_manifest": b"core\n",
                "fixture_negative_manifest": b"negative\n",
                "fixture_route_plan_json": b"route\n",
                "fixture_native_plan_json": b"native\n",
                "f0h_runtime_acceptance_json": b"runtime\n",
            }
            contracts = {
                name: (
                    Path("fixtures") / (name + ".bin")
                    if name.startswith("fixture_core") or name.startswith("fixture_negative")
                    else Path("artifacts") / name / "v0.1/input.json",
                    hashlib.sha256(body).hexdigest(),
                )
                for name, body in payloads.items()
            }
            for name, body in payloads.items():
                target = source / name
                target.write_bytes(body)
                target.chmod(0o600)
            source_sha = rebuild._checkout_snapshot(checkout, environment)
            with mock.patch.object(rebuild, "FIXTURE_PLAN_CONTRACTS", contracts):
                rebuild.materialize_fixture_plans(source, checkout)
                python_identity = rebuild.materialize_checkout_python_bridge(checkout)
                identity = rebuild.checkout_identity(checkout)
                rebuild.validate_delivery_checkout(
                    checkout,
                    environment,
                    expected_source_sha256=source_sha,
                    expected_identity=identity,
                    expected_python_identity=python_identity,
                )
                for relative in (
                    Path("src/web/node_modules/pkg/index.js"),
                    Path("src/web/dist/index.html"),
                ):
                    target = checkout / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("generated\n", encoding="utf-8")
                rebuild.validate_delivery_checkout(
                    checkout,
                    environment,
                    expected_source_sha256=source_sha,
                    expected_identity=identity,
                    expected_python_identity=python_identity,
                )
                arbitrary = checkout / "fixtures/evil.bin"
                arbitrary.parent.mkdir(parents=True, exist_ok=True)
                arbitrary.write_bytes(b"evil")
                with self.assertRaises(rebuild.RebuildError):
                    rebuild.validate_delivery_checkout(
                        checkout,
                        environment,
                        expected_source_sha256=source_sha,
                        expected_identity=identity,
                        expected_python_identity=python_identity,
                    )
                arbitrary.unlink()
                plan = checkout / contracts["fixture_route_plan_json"][0]
                plan.write_bytes(b"tampered\n")
                with self.assertRaises(rebuild.RebuildError):
                    rebuild.validate_delivery_checkout(
                        checkout,
                        environment,
                        expected_source_sha256=source_sha,
                        expected_identity=identity,
                        expected_python_identity=python_identity,
                    )

    def test_python_bridge_rejects_wrong_target_or_digest(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="anhuan-f111-python-bridge-", dir="/private/tmp"
        ) as raw_checkout:
            checkout = Path(raw_checkout)
            checkout.chmod(0o700)
            identity = rebuild.materialize_checkout_python_bridge(checkout)
            rebuild.verify_checkout_python_bridge(checkout, identity)
            wrong = rebuild.ExecutableIdentity(
                identity.path,
                identity.device,
                identity.inode,
                identity.mode,
                identity.size,
                identity.modified_ns,
                identity.changed_ns,
                "0" * 64,
            )
            with self.assertRaises(rebuild.RebuildError):
                rebuild.verify_checkout_python_bridge(checkout, wrong)
            bridge = checkout / ".venv/bin/python"
            bridge.unlink()
            bridge.symlink_to("/usr/bin/false")
            with self.assertRaises(rebuild.RebuildError):
                rebuild.verify_checkout_python_bridge(checkout, identity)
            with mock.patch.object(rebuild.sys, "executable", ""):
                with self.assertRaises(rebuild.RebuildError):
                    rebuild.launcher_python_identity()

    def test_tracked_only_checkout_retains_frozen_base_for_formal_audit(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="anhuan-f111-delivery-root-", dir="/private/tmp"
        ) as raw_root, tempfile.TemporaryDirectory(
            prefix="anhuan-f111-delivery-input-", dir="/private/tmp"
        ) as raw_source:
            root = Path(raw_root)
            source = Path(raw_source)
            root.chmod(0o700)
            source.chmod(0o700)
            (root / "src/web").mkdir(parents=True)
            (root / ".gitignore").write_text(
                ".venv/\nfixtures/\nartifacts/*/v0.1/*.json\n",
                encoding="utf-8",
            )
            tracked = root / "src/web/package.json"
            tracked.write_text('{"version":1}\n', encoding="utf-8")
            runtime_home = source / "home"
            runtime_temporary = source / "tmp"
            runtime_home.mkdir(mode=0o700)
            runtime_temporary.mkdir(mode=0o700)
            environment = rebuild._base_environment(
                {"HOME": str(runtime_home), "TMPDIR": str(runtime_temporary)}
            )
            git_environment = {
                **environment,
                "GIT_AUTHOR_NAME": "fixture",
                "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                "GIT_COMMITTER_NAME": "fixture",
                "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            }
            for command in (
                ("git", "init", "-q"),
                ("git", "add", "--all"),
                ("git", "commit", "-q", "-m", "base"),
            ):
                rebuild._process(
                    command,
                    cwd=root,
                    environment=git_environment,
                    timeout=30,
                )
            base_revision = rebuild._process(
                ("git", "rev-parse", "HEAD"),
                cwd=root,
                environment=environment,
                timeout=30,
            ).output.decode("ascii").strip()
            tracked.write_text('{"version":2}\n', encoding="utf-8")
            payloads = {
                "fixture_core_manifest": b"core\n",
                "fixture_negative_manifest": b"negative\n",
                "fixture_route_plan_json": b"route\n",
                "fixture_native_plan_json": b"native\n",
                "f0h_runtime_acceptance_json": b"runtime\n",
            }
            contracts = {
                name: (
                    Path("fixtures") / (name + ".bin"),
                    hashlib.sha256(body).hexdigest(),
                )
                for name, body in payloads.items()
            }
            for name, body in payloads.items():
                target = source / name
                target.write_bytes(body)
                target.chmod(0o600)
            runner = rebuild.CleanRebuildRound(
                1,
                {
                    "F1_SECRETS_DIR": str(source),
                    "F111_REVERSE_SECRETS_DIR": str(source),
                },
            )
            try:
                with (
                    mock.patch.object(rebuild, "ROOT", root),
                    mock.patch.object(rebuild, "BASE_REVISION", base_revision),
                    mock.patch.object(rebuild, "FIXTURE_PLAN_CONTRACTS", contracts),
                    mock.patch.object(
                        rebuild,
                        "_fixture_source_bundle",
                        return_value=(
                            mock.sentinel.bundle_identity,
                            (mock.sentinel.bundle_record,) * 26,
                            (),
                        ),
                    ),
                ):
                    runner._create_clean_checkout()
                    self.assertIsNotNone(runner.state.checkout)
                    checkout = runner.state.checkout
                    assert checkout is not None
                    rebuild._process(
                        ("git", "cat-file", "-e", base_revision + "^{commit}"),
                        cwd=checkout,
                        environment=environment,
                        timeout=30,
                    )
                    self.assertEqual(
                        (checkout / "src/web/package.json").read_text(encoding="utf-8"),
                        '{"version":2}\n',
                    )
                    self.assertFalse((root / contracts["fixture_route_plan_json"][0]).exists())
            finally:
                if runner.state.scratch_root is not None:
                    shutil.rmtree(runner.state.scratch_root, ignore_errors=True)

    def test_source_has_no_shell_escape_or_fake_green_fallback(self) -> None:
        source = Path(rebuild.__file__).read_text(encoding="utf-8")
        self.assertNotIn("shell=True", source)
        self.assertNotIn("|| true", source)
        self.assertNotIn("os.system", source)
        self.assertNotIn("docker system prune", source)
        self.assertNotIn("docker builder prune", source)
        self.assertIn('"SOURCE_DATE_EPOCH=946684800"', source)
        self.assertEqual(source.count("CLEAN_REBUILD_RESULT_SHA256="), 1)


if __name__ == "__main__":
    unittest.main()
