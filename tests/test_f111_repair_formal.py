"""Offline anti-fake tests for the fixed F1.1.1 formal orchestrator."""
from __future__ import annotations

import inspect
import json
import os
import shutil
import stat
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from infra.f1 import artifacts_v03
from infra.f1 import formal_acceptance as formal


HEX_A = "a" * 64
HEX_B = "b" * 64


class FormalAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="anhuan-formal-test-")
        self.base = Path(self.temporary.name)
        self.output = self.base / "public"
        self.secrets = self.base / "anhuan-f111-repair-secrets"
        self.provider = self.base / "anhuan-f111-repair-provider"
        self.secrets.mkdir(mode=0o700)
        self.provider.mkdir(mode=0o700)
        self.canary = "FORMAL-CANARY-" + uuid.uuid4().hex
        self.f0i_key = self.base / "f0i_key"
        self.f0i_key.write_bytes(b"fixture-key")
        self.f0i_key.chmod(0o600)
        source_marker = self.secrets / "source_marker"
        source_marker.write_bytes(b"source-only\n")
        source_marker.chmod(0o600)
        self.f0g_scope = self.secrets / formal.clean_rebuild.F0G_SOURCE_SCOPE_NAME
        self.f0g_scope.write_bytes(
            formal.clean_rebuild._canonical_bytes(
                {
                    "schema": formal.clean_rebuild.F0G_SOURCE_SCOPE_SCHEMA,
                    "database": formal.clean_rebuild.F0G_SOURCE_DATABASE_NAME,
                    "role": formal.clean_rebuild.F0G_SOURCE_ROLE,
                    "schemas": list(formal.clean_rebuild.F0G_SOURCE_SCHEMAS),
                    "access": formal.clean_rebuild.F0I_SOURCE_ACCESS,
                    "read_only": True,
                    "container_id": "a" * 64,
                    "container_name": "anhuan-f0d-postgres-1",
                    "compose_project": formal.clean_rebuild.SOURCE_COMPOSE_PROJECT,
                    "compose_service": formal.clean_rebuild.SOURCE_COMPOSE_SERVICE,
                    "image_id": "sha256:" + "b" * 64,
                    "image_reference": formal.clean_rebuild.PG_IMAGE,
                    "published_port": 55432,
                    "dump_sha256": "c" * 64,
                    "aggregate_sha256": "d" * 64,
                }
            )
        )
        self.f0g_scope.chmod(0o600)
        self.config_value = {
            "schema": formal.CONFIG_SCHEMA,
            "secrets_directory": str(self.secrets),
            "provider_secrets_directory": str(self.provider),
            "f0i_key_file": str(self.f0i_key),
            "f0g_source_scope_file": str(self.f0g_scope),
        }
        self.config = self.base / "formal-config.json"
        self._write_config(self.config_value)
        self.prepared_roots: list[Path] = []

    def tearDown(self) -> None:
        for path in self.prepared_roots:
            shutil.rmtree(path, ignore_errors=True)
        self.temporary.cleanup()

    def _write_config(self, value: object, mode: int = 0o600) -> None:
        self.config.write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        self.config.chmod(mode)

    def _prepared_stack(self, *, project: str | None = None) -> SimpleNamespace:
        project = project or "anhuan-f111-repair-" + uuid.uuid4().hex
        suffix = project.removeprefix("anhuan-f111-repair-")
        root = Path(
            tempfile.mkdtemp(
                prefix=project + "-unit-", dir="/private/tmp"
            )
        )
        root.chmod(0o700)
        self.prepared_roots.append(root)
        secrets = root / "secrets"
        provider = root / "provider"
        secrets.mkdir(mode=0o700)
        provider.mkdir(mode=0o700)
        checkout = root / "checkout"
        shutil.copytree(
            formal.ROOT,
            checkout,
            ignore=shutil.ignore_patterns(".git", "node_modules", ".venv", "v0.3"),
        )
        key = root / "f0i-key"
        key.write_bytes(b"prepared-fixture-key\n")
        key.chmod(0o600)
        ports = {
            name: 30000 + index for index, name in enumerate(formal.PORT_NAMES)
        }
        database = "f111_repair_" + suffix
        for name, user in (
            ("f1_bootstrap_dsn", "f0d_bootstrap"),
            ("f1_migration_dsn", "f0d_migration"),
        ):
            target = secrets / name
            target.write_text(
                f"postgresql://{user}:synthetic-only@host.docker.internal:"
                f"{ports['postgres']}/{database}\n",
                encoding="ascii",
            )
            target.chmod(0o600)
        marker = secrets / "opaque_marker"
        marker.write_bytes(b"unchanged\n")
        marker.chmod(0o600)
        prepared_f0g_scope = secrets / formal.clean_rebuild.F0G_SOURCE_SCOPE_NAME
        prepared_f0g_scope.write_bytes(self.f0g_scope.read_bytes())
        prepared_f0g_scope.chmod(0o600)
        canaries = secrets / "leak_canaries"
        canaries.write_text(
            json.dumps([self.canary], separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        canaries.chmod(0o600)
        python_identity = formal.clean_rebuild.materialize_checkout_python_bridge(
            checkout
        )
        isolation_config = root / "frozen-f0-isolation.json"
        isolation_config.write_bytes(b"synthetic-only\n")
        isolation_config.chmod(0o600)
        isolation = SimpleNamespace(
            project_id=uuid.UUID(hex=suffix),
            postgres_port=ports["postgres"],
        )
        frozen_f0_inputs = formal.clean_rebuild.FrozenF0PreparedInputs(
            isolation=isolation,
            config_path=isolation_config,
            config_identity=mock.sentinel.config_identity,
            runtime_root_identity=formal.clean_rebuild.CheckoutIdentity(1, 1),
            source_key_identity=mock.sentinel.source_key_identity,
            target_key_identity=mock.sentinel.target_key_identity,
            dsn_identities=(),
            fixture_source=mock.sentinel.fixture_source,
            runtime_trees=(),
        )
        return SimpleNamespace(
            project=project,
            ports=ports,
            secrets_directory=secrets,
            provider_secrets_directory=provider,
            f0i_key_file=key,
            checkout=checkout,
            checkout_identity=formal.clean_rebuild.checkout_identity(checkout),
            source_snapshot_sha256=HEX_B,
            source_file_count=100,
            fixture_input_sha256=tuple(
                (name, value[1])
                for name, value in sorted(
                    formal.clean_rebuild.FIXTURE_PLAN_CONTRACTS.items()
                )
            ),
            python_bridge_identity=python_identity,
            frozen_f0_inputs=frozen_f0_inputs,
            frozen_f0_database_snapshot=(
                formal.clean_rebuild.FrozenF0DatabaseSnapshot(
                    ((database, 100, "f0d_bootstrap"),),
                    HEX_A,
                )
            ),
        )

    def _validated_prepared_config(
        self, source: formal.SourceConfig, prepared: SimpleNamespace
    ) -> formal.FormalConfig:
        with (
            mock.patch.object(
                formal.clean_rebuild, "verify_frozen_f0_inputs"
            ),
            mock.patch.object(
                formal.clean_rebuild,
                "load_frozen_f0_isolation",
                return_value=prepared.frozen_f0_inputs.isolation,
            ),
        ):
            return formal._prepared_config(source, prepared)

    @staticmethod
    def _green_output(name: str, inventory: str) -> bytes:
        if name.startswith("migration_apply_"):
            return b"F1_MIGRATE_OK\n"
        if name == "pg_live_verifier":
            return (
                " ".join(f"{metric}=0" for metric in formal.PG_METRICS) + "\n"
            ).encode("ascii")
        if name == "targeted_tests":
            return b"Ran 301 tests in 0.01s\n\nOK\n"
        if name == "full_repository_tests":
            return b"Ran 900 tests in 1.00s\n\nOK (skipped=3)\n"
        if name == "reverse":
            return (
                " ".join(f"{metric}=0" for metric in artifacts_v03.REVERSE_METRICS)
                + "\n"
            ).encode("ascii")
        if name.startswith("clean_rebuild_"):
            return f"CLEAN_REBUILD_RESULT_SHA256={HEX_A}\n".encode("ascii")
        if name == "log_canary":
            return b"F111_LOG_CANARY_HITS=0\n"
        if name == "sbom_reconcile":
            return f"F111_RUNTIME_INVENTORY_SHA256={inventory}\n".encode("ascii")
        return b"OK\n"

    def _run(
        self,
        *,
        outputs: dict[str, bytes] | None = None,
        exit_codes: dict[str, int] | None = None,
        source_snapshots: list[formal.SourceSnapshot] | None = None,
        missing: set[str] | None = None,
        runtime_service_overrides: dict[str, str] | None = None,
        runtime_build_input_overrides: dict[str, str] | None = None,
        runtime_evidence_kind: str = "regular",
        clean_evidence_kind: str = "regular",
        teardown_failure: bool = False,
        prepare_failure: bool = False,
        checkout_failure_call: int | None = None,
        full_isolation_blocked: bool = False,
        host_copy_failure: bool = False,
        lifecycle: list[str] | None = None,
    ) -> formal.FormalResult:
        outputs = outputs or {}
        exit_codes = exit_codes or {}
        missing = missing or set()
        runtime_service_overrides = runtime_service_overrides or {}
        runtime_build_input_overrides = runtime_build_input_overrides or {}

        prepared = self._prepared_stack()
        self.last_prepared = prepared
        inventory = artifacts_v03.inventory_digest(prepared.checkout)
        services = {
            name: formal._sha256(f"service:{name}".encode("ascii"))
            for name in formal.RUNTIME_SERVICES
        }
        services.update(runtime_service_overrides)
        bases = {
            str(component["bom-ref"]): formal._sha256(
                f"base:{component['bom-ref']}".encode("utf-8")
            )
            for component in artifacts_v03._dockerfile_components(prepared.checkout)
        }
        build_inputs = formal.clean_rebuild.build_provenance(
            prepared.checkout, HEX_B
        )
        build_inputs.update(runtime_build_input_overrides)
        runtime_payload = {
            "schema": formal.RUNTIME_INVENTORY_SCHEMA,
            "static_inventory_sha256": inventory,
            "services": [
                {"service": name, "image_sha256": services[name]}
                for name in sorted(services)
            ],
            "bases": [
                {"bom_ref": name, "image_sha256": bases[name]}
                for name in sorted(bases)
            ],
            "build_inputs": build_inputs,
            "docker_binary_sha256": formal.DOCKER_BINARY_SHA256,
            "docker_context": "LOCAL_UNIX_SOCKET_TRUST_BASE",
        }
        runtime_digest = formal._sha256(formal._canonical_bytes(runtime_payload))
        runtime_document = {
            **runtime_payload,
            "runtime_inventory_sha256": runtime_digest,
        }
        self.last_runtime_document = runtime_document
        lifecycle = lifecycle if lifecycle is not None else []

        class FakePreparedContext:
            def __enter__(inner_self) -> SimpleNamespace:
                lifecycle.append("enter")
                return prepared

            def __exit__(inner_self, exc_type: object, exc: object, tb: object) -> bool:
                lifecycle.append("exit")
                if teardown_failure:
                    raise RuntimeError("synthetic teardown failure")
                return False

            def assert_closed_clean(inner_self) -> None:
                lifecycle.append("assert_closed_clean")

        self.last_preparation_environment = None
        checkout_validations = 0

        def fake_validate_checkout(
            config: formal.FormalConfig, environment: dict[str, str]
        ) -> None:
            nonlocal checkout_validations
            del environment
            self.assertEqual(config.checkout, prepared.checkout)
            checkout_validations += 1
            if checkout_failure_call == checkout_validations:
                raise formal.FormalError("FORMAL_CHECKOUT_DRIFT")

        def fake_prepare(environment: dict[str, str]) -> FakePreparedContext:
            self.last_preparation_environment = dict(environment)
            lifecycle.append("prepare")
            if prepare_failure:
                raise RuntimeError("synthetic preparation failure")
            return FakePreparedContext()

        def fake_host_copy(
            _config: formal.FormalConfig, destination: Path
        ) -> Path:
            if host_copy_failure:
                raise formal.FormalError("FORMAL_SECRET_COPY_REJECTED")
            for source in prepared.secrets_directory.iterdir():
                if source.is_file() and not source.is_symlink():
                    target = destination / source.name
                    target.write_bytes(source.read_bytes())
                    target.chmod(0o600)
            return destination

        def fake_run(
            spec: formal.CommandSpec,
            environment: dict[str, str],
            _timeout: int,
            checkout: Path,
            python_identity: formal.clean_rebuild.ExecutableIdentity,
        ) -> formal.ProcessResult:
            self.assertEqual(checkout, prepared.checkout)
            self.assertEqual(python_identity, prepared.python_bridge_identity)
            clean_marker: str | None = None
            if spec.name.startswith("clean_rebuild_"):
                round_number = int(spec.name[-1])
                summary = formal.clean_rebuild.RoundSummary(
                    source_sha256=HEX_B,
                    fixture_source_sha256="c" * 64,
                    fixture_e2e_sha256="d" * 64,
                    schema_sha256="e" * 64,
                    pg_contract_sha256="f" * 64,
                    runtime_inventory_sha256="1" * 64,
                    service_count=len(formal.clean_rebuild.EXPECTED_SERVICES),
                    evidence_captured=True,
                    cleanup_residuals=0,
                )
                document = formal.clean_rebuild.round_evidence_document(
                    summary, round_number
                )
                formal.clean_rebuild.write_round_evidence(
                    Path(environment["TMPDIR"]), document, round_number
                )
                evidence_target = (
                    Path(environment["TMPDIR"])
                    / formal.clean_rebuild.CLEAN_EVIDENCE_NAMES[round_number]
                )
                if round_number == 2 and clean_evidence_kind == "missing":
                    evidence_target.unlink()
                elif round_number == 2 and clean_evidence_kind == "world":
                    evidence_target.chmod(0o644)
                elif round_number == 2 and clean_evidence_kind == "tampered":
                    changed = json.loads(evidence_target.read_text(encoding="utf-8"))
                    changed["cleanup"]["residuals"] = 1
                    evidence_target.write_text(
                        json.dumps(changed, sort_keys=True, separators=(",", ":"))
                        + "\n",
                        encoding="utf-8",
                    )
                    evidence_target.chmod(0o600)
                clean_marker = summary.result_sha256()
            if spec.name == "sbom_reconcile":
                target = Path(environment["TMPDIR"]) / formal.RUNTIME_INVENTORY_FILE
                if runtime_evidence_kind != "missing":
                    raw = formal._canonical_bytes(runtime_document)
                    if runtime_evidence_kind == "symlink":
                        backing = target.with_name("runtime-backing.json")
                        backing.write_bytes(raw)
                        backing.chmod(0o600)
                        target.symlink_to(backing)
                    else:
                        target.write_bytes(raw)
                        target.chmod(0o644 if runtime_evidence_kind == "world" else 0o600)
            return formal.ProcessResult(
                exit_code=exit_codes.get(spec.name, 0),
                output=outputs.get(
                    spec.name,
                    (
                        f"F111_RUNTIME_INVENTORY_SHA256={runtime_digest}\n".encode("ascii")
                        if spec.name == "sbom_reconcile"
                        else f"CLEAN_REBUILD_RESULT_SHA256={clean_marker}\n".encode("ascii")
                        if clean_marker is not None
                        else self._green_output(spec.name, inventory)
                    ),
                ),
            )

        stable = formal.SourceSnapshot(sha256=HEX_B, file_count=100)
        snapshots = source_snapshots or [stable] * 40
        with (
            mock.patch.object(formal, "DEFAULT_OUTPUT", self.output),
            mock.patch.object(
                formal.clean_rebuild,
                "prepare_primary_stack",
                side_effect=fake_prepare,
            ),
            mock.patch.object(formal, "_run_process", side_effect=fake_run),
            mock.patch.object(
                formal, "_materialize_host_secrets", side_effect=fake_host_copy
            ),
            mock.patch.object(
                formal,
                "_full_repository_isolation_blocker",
                return_value=(
                    formal.FROZEN_FULL_SUITE_BLOCKER
                    if full_isolation_blocked
                    else None
                ),
            ),
            mock.patch.object(
                formal, "_validate_formal_checkout", side_effect=fake_validate_checkout
            ),
            mock.patch.object(
                formal,
                "_command_available",
                side_effect=lambda spec, checkout, python_identity: (
                    checkout == prepared.checkout
                    and python_identity == prepared.python_bridge_identity
                    and spec.name not in missing
                ),
            ),
            mock.patch.object(formal, "_source_snapshot", side_effect=snapshots),
            mock.patch.object(
                formal.clean_rebuild, "verify_frozen_f0_inputs"
            ),
            mock.patch.object(
                formal.clean_rebuild,
                "load_frozen_f0_isolation",
                return_value=prepared.frozen_f0_inputs.isolation,
            ),
        ):
            return formal.run_formal_acceptance(self.config)

    def _all_public_bytes(self) -> bytes:
        return b"".join(
            path.read_bytes()
            for path in sorted(self.output.rglob("*"))
            if path.is_file()
        )

    def test_public_entry_accepts_only_config_path(self) -> None:
        signature = inspect.signature(formal.run_formal_acceptance)
        self.assertEqual(tuple(signature.parameters), ("config_path",))
        for forbidden in ("root", "output", "command", "evidence", "result", "capability"):
            self.assertNotIn(forbidden, signature.parameters)
        with self.assertRaises(TypeError):
            formal.run_formal_acceptance(self.config, object())  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            formal.run_formal_acceptance(self.config, root=self.base)  # type: ignore[call-arg]
        class FakeCapability:
            def __fspath__(self) -> str:
                return str(self.config)  # pragma: no cover - must not be called

        with self.assertRaises(formal.FormalError):
            formal.run_formal_acceptance(FakeCapability())  # type: ignore[arg-type]

    def test_config_rejects_serialized_authority_fields(self) -> None:
        for forbidden in (
            "evidence",
            "result",
            "command",
            "root",
            "output",
            "capability",
            "project",
            "ports",
        ):
            value = dict(self.config_value)
            value[forbidden] = {"accepted": True}
            self._write_config(value)
            with self.assertRaises(formal.FormalError):
                formal.load_config(self.config)

    def test_source_config_digest_is_semantic_and_has_no_random_authority(self) -> None:
        first = formal.load_config(self.config)
        self.config.write_text(
            json.dumps(self.config_value, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        self.config.chmod(0o600)
        second = formal.load_config(self.config)
        self.assertEqual(first.sha256, second.sha256)
        self.assertNotIn("project", self.config_value)
        self.assertNotIn("ports", self.config_value)
        self.assertNotIn("timeout_seconds", self.config_value)

    def test_source_config_cannot_control_gate_timeout(self) -> None:
        value = dict(self.config_value)
        value["timeout_seconds"] = 60
        self._write_config(value)
        with self.assertRaisesRegex(formal.FormalError, "CONFIG_AUTHORITY_REJECTED"):
            formal.load_config(self.config)

    def test_prepared_stack_payload_cannot_be_reused_as_public_input(self) -> None:
        prepared = self._prepared_stack()
        value = {
            "schema": "f1.1.1-formal-config-v1",
            "project": prepared.project,
            "secrets_directory": str(prepared.secrets_directory),
            "provider_secrets_directory": str(prepared.provider_secrets_directory),
            "f0i_key_file": str(prepared.f0i_key_file),
            "ports": prepared.ports,
            "timeout_seconds": 900,
        }
        self._write_config(value)
        with self.assertRaisesRegex(formal.FormalError, "CONFIG_AUTHORITY_REJECTED"):
            formal.run_formal_acceptance(self.config)

    def test_config_must_be_owned_0600_regular_file(self) -> None:
        self._write_config(self.config_value, 0o644)
        with self.assertRaises(formal.FormalError):
            formal.load_config(self.config)
        self._write_config(self.config_value)
        link = self.base / "linked-config.json"
        link.symlink_to(self.config)
        with self.assertRaises(formal.FormalError):
            formal.load_config(link)

    def test_child_environment_uses_fresh_private_home_not_user_home(self) -> None:
        source = formal.load_config(self.config)
        config = self._validated_prepared_config(source, self._prepared_stack())
        with tempfile.TemporaryDirectory(
            prefix="anhuan-f111-formal-home-test-", dir="/private/tmp"
        ) as raw_home:
            home = Path(raw_home)
            home.chmod(0o700)
            environment = formal._environment(config, home)
            self.assertEqual(environment["HOME"], str(home))
            self.assertTrue(environment["TMPDIR"].startswith(str(home)))
            self.assertTrue(environment["npm_config_cache"].startswith(str(home)))
            self.assertNotIn("/Users/", environment["HOME"])
            self.assertEqual(
                environment["PYTHONPATH"].split(os.pathsep),
                [str(config.checkout / "src"), str(config.checkout / "tests")],
            )
            self.assertEqual(
                environment["F111_REVERSE_COMPOSE_OVERRIDE"],
                str(config.checkout / "infra/f1/docker-compose.repair.yml"),
            )

    def test_host_secret_copy_requires_verified_source_object_bundle(self) -> None:
        source = formal.load_config(self.config)
        config = self._validated_prepared_config(source, self._prepared_stack())
        with tempfile.TemporaryDirectory(
            prefix=config.project + "-bundle-", dir="/private/tmp"
        ) as raw_bundle:
            bundle = Path(raw_bundle)
            bundle.chmod(0o700)
            with self.assertRaisesRegex(
                formal.FormalError, "FORMAL_SOURCE_BUNDLE_COPY_REJECTED"
            ):
                formal._materialize_host_secrets(config, bundle)

    def test_host_secret_bundle_is_removed_after_evaluation(self) -> None:
        observed: list[Path] = []
        def observe(config: formal.FormalConfig, destination: Path) -> Path:
            del config
            observed.append(destination)
            marker = destination / "leak_canaries"
            marker.write_text(
                json.dumps([self.canary], separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            marker.chmod(0o600)
            return destination

        source = formal.load_config(self.config)
        config = self._validated_prepared_config(source, self._prepared_stack())
        with mock.patch.object(formal, "_materialize_host_secrets", side_effect=observe):
            with formal._host_secret_bundle(config) as bundle:
                self.assertTrue(bundle.exists())
        self.assertEqual(len(observed), 1)
        self.assertFalse(observed[0].exists())

    def test_command_registry_is_fixed_and_complete(self) -> None:
        names = tuple(spec.name for spec in formal.COMMAND_REGISTRY)
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("pg_live_verifier", names)
        self.assertIn("clean_rebuild_1", names)
        self.assertIn("clean_rebuild_2", names)
        self.assertIn("sbom_reconcile", names)
        self.assertEqual(set(formal.GATE_SEQUENCE), set(artifacts_v03.REQUIRED_GATES))
        self.assertRegex(formal.command_registry_sha256(), r"^[0-9a-f]{64}$")
        self.assertEqual(
            set(formal._TARGETED_MODULES),
            {
                "tests." + path.stem
                for path in (formal.ROOT / "tests").glob("test_f111_*.py")
            },
        )
        self.assertGreaterEqual(formal.MIN_TARGETED_TESTS, 300)

    def test_frozen_base_test_definitions_are_preserved(self) -> None:
        self.assertEqual(formal.BASELINE_TEST_DEFINITIONS, 850)
        formal._require_baseline_tests()

    def test_missing_frozen_test_definition_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="anhuan-formal-tests-") as raw:
            root = Path(raw)
            (root / "tests").mkdir()
            (root / "tests/test_sample.py").write_text(
                "def test_other(): pass\n", encoding="utf-8"
            )
            baseline = (
                formal.BASE_REVISION.encode("ascii")
                + b":tests/test_sample.py:1:def test_required(): pass\n"
            )
            with mock.patch.object(formal, "ROOT", root), mock.patch.object(
                formal, "BASELINE_TEST_DEFINITIONS", 1
            ), mock.patch.object(formal, "_git_bytes", return_value=baseline):
                with self.assertRaisesRegex(
                    formal.FormalError, "BASELINE_TEST_REMOVED"
                ):
                    formal._require_baseline_tests()

    def test_repository_boundary_matches_taskbook_and_freezes_old_migrations(self) -> None:
        for allowed in (
            "infra/f1/formal_acceptance.py",
            "src/platform_foundation/f1/auth.py",
            "tests/test_f111_repair_formal.py",
            "artifacts/f1-platform-shell/v0.3/current.json",
        ):
            self.assertTrue(formal._allowed_change_path(allowed), allowed)
        for rejected in (
            "infra/f1/alembic/versions/f1_0003_security_boundaries.py",
            "migrations/versions/f0d_0006.py",
            ".env",
            "tests/unrelated.py",
            "../escape",
        ):
            self.assertFalse(formal._allowed_change_path(rejected), rejected)
        self.assertTrue(formal._repository_boundary())

    def test_changed_test_sources_cannot_add_skip_constructs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="anhuan-formal-skip-") as raw:
            root = Path(raw)
            tests = root / "tests"
            tests.mkdir()
            target = tests / "test_f111_new.py"
            target.write_text(
                "import unittest\nclass T(unittest.TestCase):\n"
                "    @unittest.skip('hidden')\n"
                "    def test_hidden(self): pass\n",
                encoding="utf-8",
            )
            with mock.patch.object(formal, "ROOT", root):
                with self.assertRaisesRegex(
                    formal.FormalError, "NEW_TEST_SKIP_FORBIDDEN"
                ):
                    formal._reject_new_skip_constructs(
                        ("tests/test_f111_new.py",)
                    )

    def test_full_suite_allows_only_frozen_three_probe_class_skips(self) -> None:
        self.assertTrue(
            formal._test_output_valid(
                b"Ran 1000 tests in 1.0s\nOK (skipped=3)\n",
                875,
                expected_skips=3,
            )
        )

    def test_frozen_f0_test_multiset_and_three_class_skips_are_exact(self) -> None:
        self.assertEqual(formal.FROZEN_F0_TEST_DEFINITIONS, 599)
        formal._require_frozen_f0_contract()
        self.assertFalse(
            formal._test_output_valid(
                b"Ran 1000 tests in 1.0s\nOK (skipped=2)\n",
                875,
                expected_skips=3,
            )
        )
        self.assertFalse(
            formal._test_output_valid(
                b"Ran 1000 tests in 1.0s\nOK (skipped=4)\n",
                875,
                expected_skips=3,
            )
        )

    def test_all_green_fixed_execution_can_publish_ready(self) -> None:
        result = self._run()
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.conclusion, artifacts_v03.READY_CONCLUSION)
        current = json.loads(result.current_path.read_text(encoding="utf-8"))
        self.assertEqual(current["conclusion"], artifacts_v03.READY_CONCLUSION)
        self.assertEqual(
            self.last_preparation_environment["F1_SECRETS_DIR"],
            str(self.secrets),
        )
        preparation_home = Path(self.last_preparation_environment["HOME"])
        preparation_tmp = Path(self.last_preparation_environment["TMPDIR"])
        self.assertEqual(preparation_home.name, "home")
        self.assertEqual(preparation_tmp.name, "tmp")
        self.assertEqual(preparation_home.parent, preparation_tmp.parent)
        self.assertTrue(
            preparation_home.parent.name.startswith("anhuan-f111-preparation-")
        )
        self.assertFalse(preparation_home.parent.exists())
        self.assertNotIn("project", self.last_preparation_environment)
        self.assertNotIn("ports", self.last_preparation_environment)

    def test_publication_occurs_only_after_context_exit_and_cleanup_assertion(self) -> None:
        lifecycle: list[str] = []
        original_publish = formal._publish

        def observe_publish(candidate: formal.FormalCandidate) -> formal.FormalResult:
            lifecycle.append("publish")
            return original_publish(candidate)

        with mock.patch.object(formal, "_publish", side_effect=observe_publish):
            result = self._run(lifecycle=lifecycle)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            lifecycle,
            ["prepare", "enter", "exit", "assert_closed_clean", "publish"],
        )

    def test_cleanup_failure_can_never_publish_ready(self) -> None:
        lifecycle: list[str] = []
        result = self._run(teardown_failure=True, lifecycle=lifecycle)
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.conclusion, artifacts_v03.REJECTED_CONCLUSION)
        self.assertIn("exit", lifecycle)
        self.assertNotIn("assert_closed_clean", lifecycle)
        self.assertNotIn(b"READY", self._all_public_bytes())

    def test_preparation_failure_publishes_only_rejected_authority(self) -> None:
        result = self._run(prepare_failure=True)
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.conclusion, artifacts_v03.REJECTED_CONCLUSION)
        self.assertNotIn(b"READY", self._all_public_bytes())

    def test_new_preparation_failure_revokes_prior_ready_current(self) -> None:
        self.assertEqual(self._run().conclusion, artifacts_v03.READY_CONCLUSION)
        result = self._run(prepare_failure=True)
        self.assertEqual(result.conclusion, artifacts_v03.REJECTED_CONCLUSION)
        current = json.loads(result.current_path.read_text(encoding="utf-8"))
        self.assertEqual(current["conclusion"], artifacts_v03.REJECTED_CONCLUSION)
        self.assertNotIn(b"READY", self._all_public_bytes())

    def test_new_host_bundle_failure_revokes_prior_ready_current(self) -> None:
        self.assertEqual(self._run().conclusion, artifacts_v03.READY_CONCLUSION)
        result = self._run(host_copy_failure=True)
        self.assertEqual(result.conclusion, artifacts_v03.REJECTED_CONCLUSION)
        current = json.loads(result.current_path.read_text(encoding="utf-8"))
        self.assertEqual(current["conclusion"], artifacts_v03.REJECTED_CONCLUSION)
        self.assertNotIn(b"READY", self._all_public_bytes())

    def test_two_identical_formal_runs_are_byte_deterministic(self) -> None:
        first = self._run()
        first_bytes = self._all_public_bytes()
        current = json.loads(first.current_path.read_text(encoding="utf-8"))
        acceptance = json.loads(
            (
                self.output
                / "batches"
                / current["batch_id"]
                / "acceptance.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            acceptance["formal"]["config_sha256"],
            formal.load_config(self.config).sha256,
        )
        self.assertNotIn("project", acceptance["formal"])
        self.assertNotIn("ports", acceptance["formal"])
        clean = acceptance["formal"]["clean_rebuild"]
        self.assertEqual(clean["schema"], formal.clean_rebuild.CLEAN_EVIDENCE_SCHEMA)
        self.assertEqual(clean["cleanup_residuals"], 0)
        self.assertEqual(
            clean["service_count"], len(formal.clean_rebuild.EXPECTED_SERVICES)
        )
        self.assertRegex(clean["result_sha256"], r"^[0-9a-f]{64}$")
        second = self._run()
        self.assertEqual(first.batch_id, second.batch_id)
        self.assertEqual(first_bytes, self._all_public_bytes())

    def test_different_clean_rebuild_rounds_reject_without_ready_bytes(self) -> None:
        result = self._run(
            outputs={
                "clean_rebuild_2": f"CLEAN_REBUILD_RESULT_SHA256={HEX_B}\n".encode()
            }
        )
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.conclusion, artifacts_v03.REJECTED_CONCLUSION)
        self.assertNotIn(b"READY", self._all_public_bytes())

    def test_missing_fixed_command_rejects_without_ready_bytes(self) -> None:
        result = self._run(missing={"clean_rebuild_1"})
        self.assertEqual(result.exit_code, 2)
        self.assertNotIn(b"READY", self._all_public_bytes())

    def test_source_drift_rejects_without_ready_bytes(self) -> None:
        result = self._run(
            source_snapshots=[
                formal.SourceSnapshot(HEX_A, 100),
                formal.SourceSnapshot(HEX_B, 100),
            ]
            + [formal.SourceSnapshot(HEX_B, 100)] * 30
        )
        self.assertEqual(result.exit_code, 2)
        self.assertNotIn(b"READY", self._all_public_bytes())

    def test_complete_delivery_snapshot_is_the_formal_source_authority(self) -> None:
        delivery = formal.clean_rebuild.SourceSnapshot(
            (
                formal.clean_rebuild.SourceEntry(
                    Path("first"), 0o644, HEX_A, 1
                ),
                formal.clean_rebuild.SourceEntry(
                    Path("second"), 0o755, HEX_B, 2
                ),
            ),
            "c" * 64,
            "d" * 64,
        )
        with (
            mock.patch.object(formal, "_repository_boundary"),
            mock.patch.object(
                formal.clean_rebuild, "capture_source", return_value=delivery
            ) as capture,
        ):
            observed = formal._source_snapshot()
        self.assertEqual(observed, formal.SourceSnapshot("c" * 64, 2))
        capture.assert_called_once()

    def test_prepared_checkout_path_and_inode_cannot_be_faked(self) -> None:
        source = formal.load_config(self.config)
        prepared = self._prepared_stack()
        fake = SimpleNamespace(**vars(prepared))
        fake.checkout = formal.ROOT
        fake.checkout_identity = formal.clean_rebuild.checkout_identity(formal.ROOT)
        with self.assertRaisesRegex(formal.FormalError, "PREPARED_CHECKOUT_REJECTED"):
            formal._prepared_config(source, fake)

        old_identity = prepared.checkout_identity
        moved = prepared.checkout.with_name("moved-checkout")
        prepared.checkout.rename(moved)
        prepared.checkout.mkdir(mode=0o700)
        replaced = SimpleNamespace(**vars(prepared))
        replaced.checkout_identity = old_identity
        with self.assertRaisesRegex(formal.FormalError, "PREPARED_CHECKOUT_REJECTED"):
            formal._prepared_config(source, replaced)

    def test_checkout_or_fixed_plan_drift_rejects_without_ready(self) -> None:
        result = self._run(checkout_failure_call=3)
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.conclusion, artifacts_v03.REJECTED_CONCLUSION)
        self.assertNotIn(b"READY", self._all_public_bytes())

    def test_formal_commands_inventory_build_and_normalization_use_checkout(self) -> None:
        inventory_roots: list[Path] = []
        build_roots: list[Path] = []
        normalization_roots: list[Path] = []
        original_inventory = artifacts_v03.inventory_digest
        original_build = formal.clean_rebuild.build_provenance
        original_normalized = formal.normalized_digest

        def inventory(root: Path) -> str:
            inventory_roots.append(root)
            return original_inventory(root)

        def build(root: Path, source_sha256: str) -> dict[str, str]:
            build_roots.append(root)
            return original_build(root, source_sha256)

        def normalized(value: str, root: Path) -> str:
            normalization_roots.append(root)
            return original_normalized(value, root)

        with (
            mock.patch.object(artifacts_v03, "inventory_digest", side_effect=inventory),
            mock.patch.object(
                formal.clean_rebuild, "build_provenance", side_effect=build
            ),
            mock.patch.object(formal, "normalized_digest", side_effect=normalized),
        ):
            result = self._run()
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(inventory_roots)
        self.assertTrue(build_roots)
        self.assertTrue(normalization_roots)
        for roots in (inventory_roots, build_roots, normalization_roots):
            self.assertEqual(set(roots), {self.last_prepared.checkout})
        self.assertNotEqual(self.last_prepared.checkout, formal.ROOT)

    def test_nonzero_gate_rejects_without_raw_output_or_ready(self) -> None:
        raw = b"token=not-for-artifact /private/tmp/raw-fixture-name\n"
        result = self._run(outputs={"npm_lint": raw}, exit_codes={"npm_lint": 1})
        self.assertEqual(result.exit_code, 2)
        public = self._all_public_bytes()
        self.assertNotIn(raw, public)
        self.assertNotIn(b"not-for-artifact", public)
        self.assertNotIn(b"READY", public)

    def test_reverse_requires_one_exact_twenty_metric_line(self) -> None:
        result = self._run(outputs={"reverse": b"valid_http_e2e=0\n"})
        self.assertEqual(result.exit_code, 2)
        self.assertNotIn(b"READY", self._all_public_bytes())

    def test_sbom_runtime_marker_must_match_actual_lock_inventory(self) -> None:
        result = self._run(
            outputs={
                "sbom_reconcile": f"F111_RUNTIME_INVENTORY_SHA256={HEX_A}\n".encode()
            }
        )
        self.assertEqual(result.exit_code, 2)
        self.assertNotIn(b"READY", self._all_public_bytes())

    def test_runtime_evidence_is_required_private_and_not_a_symlink(self) -> None:
        original_output = self.output
        for kind in ("missing", "world", "symlink"):
            with self.subTest(kind=kind):
                output = self.base / ("public-" + kind)
                self.output = output
                try:
                    result = self._run(runtime_evidence_kind=kind)
                finally:
                    self.output = original_output
                self.assertEqual(result.exit_code, 2)
                self.assertNotIn(
                    b"READY",
                    b"".join(
                        path.read_bytes()
                        for path in output.rglob("*")
                        if path.is_file()
                    ),
                )

    def test_clean_round_structured_evidence_is_required_and_private(self) -> None:
        original_output = self.output
        for kind in ("missing", "world", "tampered"):
            with self.subTest(kind=kind):
                output = self.base / ("clean-public-" + kind)
                self.output = output
                try:
                    result = self._run(clean_evidence_kind=kind)
                finally:
                    self.output = original_output
                self.assertEqual(result.exit_code, 2)
                self.assertNotIn(
                    b"READY",
                    b"".join(
                        path.read_bytes()
                        for path in output.rglob("*")
                        if path.is_file()
                    ),
                )

    def test_runtime_build_input_tamper_rejects(self) -> None:
        result = self._run(
            runtime_build_input_overrides={"python_lock_sha256": "0" * 64}
        )
        self.assertEqual(result.exit_code, 2)
        self.assertNotIn(b"READY", self._all_public_bytes())

    def test_candidate_artifact_canary_hit_revokes_without_leaking(self) -> None:
        original = formal._formal_payload

        def leak(*args: object, **kwargs: object) -> tuple[dict[str, object], list[dict[str, object]], bool]:
            payload, components, accepted = original(*args, **kwargs)
            payload["synthetic_probe"] = self.canary
            return payload, components, accepted

        with mock.patch.object(formal, "_formal_payload", side_effect=leak):
            result = self._run()
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.conclusion, artifacts_v03.REJECTED_CONCLUSION)
        public = self._all_public_bytes()
        self.assertNotIn(self.canary.encode("utf-8"), public)
        self.assertNotIn(b"READY", public)

    def test_scanned_candidate_bytes_are_reused_without_second_render(self) -> None:
        original = formal._artifact_contents
        calls = 0

        def changing(
            payload: dict[str, object],
            components: list[dict[str, object]],
            accepted: bool,
        ) -> dict[str, bytes]:
            nonlocal calls
            calls += 1
            contents = original(payload, components, accepted)
            if calls > 1:
                contents["status.html"] = self.canary.encode("utf-8")
            return contents

        with mock.patch.object(formal, "_artifact_contents", side_effect=changing):
            result = self._run()
        self.assertEqual(result.conclusion, artifacts_v03.READY_CONCLUSION)
        self.assertEqual(calls, 1)
        self.assertNotIn(self.canary.encode("utf-8"), self._all_public_bytes())

    def test_actual_service_image_ids_bind_formal_batch_and_cyclonedx(self) -> None:
        first = self._run()
        first_document = dict(self.last_runtime_document)
        second = self._run(runtime_service_overrides={"api": "f" * 64})
        self.assertEqual(first.exit_code, 0)
        self.assertEqual(second.exit_code, 0)
        self.assertNotEqual(first.batch_id, second.batch_id)
        self.assertNotEqual(
            first_document["runtime_inventory_sha256"],
            self.last_runtime_document["runtime_inventory_sha256"],
        )
        current = json.loads(second.current_path.read_text(encoding="utf-8"))
        sbom = json.loads(
            (
                self.output
                / "batches"
                / current["batch_id"]
                / "sbom.json"
            ).read_text(encoding="utf-8")
        )
        api = next(
            component
            for component in sbom["components"]
            if component["bom-ref"] == "compose:api"
        )
        self.assertEqual(api["hashes"], [{"alg": "SHA-256", "content": "f" * 64}])
        for name, value in self.last_runtime_document["build_inputs"].items():
            self.assertIn(
                {
                    "name": "oci:build-input:" + name.replace("_", "-"),
                    "value": value,
                },
                api["properties"],
            )
        self.assertEqual(
            sbom["metadata"]["properties"],
            [
                {
                    "name": "inventory:sha256",
                    "value": self.last_runtime_document[
                        "runtime_inventory_sha256"
                    ],
                }
            ],
        )

    def test_test_threshold_and_skip_are_fail_closed(self) -> None:
        result = self._run(
            outputs={"full_repository_tests": b"Ran 874 tests in 1.0s\nOK\n"}
        )
        self.assertEqual(result.exit_code, 2)
        self.assertNotIn(b"READY", self._all_public_bytes())
        self.output.rename(self.base / "first-rejected")
        result = self._run(
            outputs={
                "targeted_tests": b"Ran 37 tests in 1.0s\nOK (skipped=1)\n"
            }
        )
        self.assertEqual(result.exit_code, 2)
        self.assertNotIn(b"READY", self._all_public_bytes())

    def test_frozen_full_suite_is_fail_closed_without_running_shared_gate(self) -> None:
        result = self._run(full_isolation_blocked=True)
        self.assertEqual(result.exit_code, 2)
        current = json.loads(result.current_path.read_text(encoding="utf-8"))
        acceptance = json.loads(
            (
                self.output
                / "batches"
                / current["batch_id"]
                / "acceptance.json"
            ).read_text(encoding="utf-8")
        )
        self.assertIn(formal.FROZEN_FULL_SUITE_BLOCKER, acceptance["blockers"])
        self.assertNotIn(b"READY", self._all_public_bytes())

    def test_full_suite_unblocks_only_for_matching_prepared_isolation_boundary(self) -> None:
        source = formal.load_config(self.config)
        prepared = self._prepared_stack()
        config = self._validated_prepared_config(source, prepared)
        environment = {
            formal.clean_rebuild.F0_ISOLATION_ENVIRONMENT_VARIABLE: str(
                config.frozen_f0_inputs.config_path
            )
        }
        with (
            mock.patch.object(
                formal.clean_rebuild, "verify_frozen_f0_inputs"
            ) as verify,
            mock.patch.object(
                formal.clean_rebuild, "verify_frozen_f0_project_absence"
            ) as absence,
            mock.patch.object(
                formal.clean_rebuild,
                "capture_frozen_f0_database_snapshot",
                return_value=config.frozen_f0_database_snapshot,
            ) as snapshot,
        ):
            self.assertIsNone(
                formal._full_repository_isolation_blocker(config, environment)
            )
        verify.assert_called_once()
        absence.assert_called_once()
        snapshot.assert_called_once()

        replaced = formal.clean_rebuild.FrozenF0DatabaseSnapshot(
            config.frozen_f0_database_snapshot.rows,
            "f" * 64,
        )
        with (
            mock.patch.object(
                formal.clean_rebuild, "verify_frozen_f0_inputs"
            ),
            mock.patch.object(
                formal.clean_rebuild, "verify_frozen_f0_project_absence"
            ),
            mock.patch.object(
                formal.clean_rebuild,
                "capture_frozen_f0_database_snapshot",
                return_value=replaced,
            ),
        ):
            self.assertEqual(
                formal._full_repository_isolation_blocker(config, environment),
                formal.FROZEN_FULL_SUITE_BLOCKER,
            )
        self.assertEqual(
            formal._full_repository_isolation_blocker(config, {}),
            formal.FROZEN_FULL_SUITE_BLOCKER,
        )
        source_code = inspect.getsource(formal._evaluate_formal)
        self.assertIn("frozen_f0_post_boundary", source_code)

    def test_public_json_publisher_cannot_promote_fake_all_green(self) -> None:
        gates: dict[str, object] = {}
        inventory = artifacts_v03.inventory_digest(formal.ROOT)
        for name in artifacts_v03.REQUIRED_GATES:
            gates[name] = {"exit": 0, "normalized_output_sha256": HEX_A}
        gates["reverse"]["metrics"] = {  # type: ignore[index]
            name: 0 for name in artifacts_v03.REVERSE_METRICS
        }
        gates["clean_rebuild_1"]["result_sha256"] = HEX_B  # type: ignore[index]
        gates["clean_rebuild_2"]["result_sha256"] = HEX_B  # type: ignore[index]
        gates["sbom_reconcile"]["inventory_sha256"] = inventory  # type: ignore[index]
        evidence = self.base / "fake-all-green.json"
        evidence.write_text(
            json.dumps({"schema": artifacts_v03.EVIDENCE_SCHEMA, "gates": gates}),
            encoding="utf-8",
        )
        output = self.base / "diagnostic"
        result = artifacts_v03.publish(
            root=formal.ROOT, evidence_path=evidence, output_dir=output
        )
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.conclusion, artifacts_v03.REJECTED_CONCLUSION)
        self.assertNotIn(
            b"READY",
            b"".join(path.read_bytes() for path in output.rglob("*") if path.is_file()),
        )


if __name__ == "__main__":
    unittest.main()
