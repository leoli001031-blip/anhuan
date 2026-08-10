from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

from infra.f1 import prepare_formal_inputs as prep


_BUNDLE_TEST_NAMESPACE = uuid.UUID("d0373664-a4ee-5c41-a87c-419e6565de24")


def _private_file(path: Path, raw: bytes) -> Path:
    path.write_bytes(raw)
    path.chmod(0o600)
    return path


def _private_directory(path: Path) -> Path:
    path.mkdir()
    path.chmod(0o700)
    return path


def _source_bundle_parts() -> tuple[list[dict[str, object]], list[bytes]]:
    records: list[dict[str, object]] = []
    bodies: list[bytes] = []
    offset = 0
    total = prep.ENVIRONMENT_DEMO_V01.core_files + prep.ENVIRONMENT_DEMO_V01.negative_files
    for index in range(total):
        if index < prep.ENVIRONMENT_DEMO_V01.core_files:
            group = "core"
            line = index + 1
        else:
            group = "negative"
            line = index - prep.ENVIRONMENT_DEMO_V01.core_files + 1
        body = (f"opaque-body-{index:02d}-" * (index + 1)).encode("ascii")
        source_id = uuid.uuid5(_BUNDLE_TEST_NAMESPACE, f"{group}:{line}")
        records.append(
            {
                "source_id": str(source_id),
                "group": group,
                "line": line,
                "sha256": hashlib.sha256(body).hexdigest(),
                "size": len(body),
                "offset": offset,
            }
        )
        bodies.append(body)
        offset += len(body)
    records.sort(key=lambda value: (value["group"], value["line"], value["source_id"]))
    offset = 0
    body_by_id = {
        str(uuid.uuid5(_BUNDLE_TEST_NAMESPACE, f"{record['group']}:{record['line']}")): body
        for record, body in zip(
            sorted(
                records,
                key=lambda value: (
                    0 if value["group"] == "core" else 1,
                    value["line"],
                ),
            ),
            bodies,
        )
    }
    ordered_bodies: list[bytes] = []
    for record in records:
        body = body_by_id[str(record["source_id"])]
        record["offset"] = offset
        offset += len(body)
        ordered_bodies.append(body)
    return records, ordered_bodies


def _write_source_bundle(
    path: Path,
    records: list[dict[str, object]],
    bodies: list[bytes],
) -> bytes:
    header = prep._canonical_bytes(
        {
            "schema": prep.SOURCE_BUNDLE_SCHEMA,
            "entry_count": len(records),
            "payload_size": sum(len(body) for body in bodies),
            "entries": records,
        }
    )
    raw = prep.SOURCE_BUNDLE_MAGIC + struct.pack(">Q", len(header)) + header + b"".join(bodies)
    _private_file(path, raw)
    return raw


class FrozenRuntimeIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="f111-f0-runtime-test-", dir="/private/tmp"
        )
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.f0e_root = self.root / "f0e-runtime"
        self.f0f_root = self.root / "f0f-runtime"
        repository_root = Path(__file__).resolve().parents[1]
        shutil.copytree(repository_root / "infra/f0e", self.f0e_root)
        shutil.copytree(repository_root / "infra/f0f", self.f0f_root)
        for runtime_root in (self.f0e_root, self.f0f_root):
            runtime_root.chmod(0o700)
            for path in runtime_root.rglob("*"):
                path.chmod(0o700 if path.is_dir() else 0o600)
        self.project = "anhuan-f111-repair-f0-" + "a" * 32
        self.isolation = SimpleNamespace(
            f0e_runtime_root=self.f0e_root,
            f0f_runtime_root=self.f0f_root,
            f0f_key_file=self.root / "secrets/f0f.key",
            f0f_vault_root=self.root / "f0f-vault",
            f0h_runtime_root=self.root / "f0h-runtime",
            tmp_dir=self.root / "tmp",
            docker_project_for=lambda phase: self.project + "-" + phase,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_f0e_runtime_uses_only_isolated_root(self) -> None:
        from platform_foundation.f0e.contracts import F0EError
        from platform_foundation.f0e import runtime_config

        with mock.patch.object(
            runtime_config, "load_frozen_f0_isolation", return_value=self.isolation
        ):
            bundle = runtime_config.load_runtime_bundle()
            _docker, seccomp = runtime_config.runtime_paths()
            self.assertEqual(Path(seccomp).parent, self.f0e_root)
            self.assertRegex(bundle.lock_sha256, r"^[0-9a-f]{64}$")
            with self.assertRaisesRegex(F0EError, "RUNNER_CONFIGURATION_INVALID"):
                runtime_config.runtime_paths(
                    Path(__file__).resolve().parents[1] / "infra/f0e"
                )

    def test_f0f_runtime_uses_isolated_f0f_and_f0e_roots(self) -> None:
        from platform_foundation.f0f.contracts import F0FError
        from platform_foundation.f0f import runtime_config
        from platform_foundation.f0e import runtime_config as f0e_runtime_config

        with mock.patch.object(
            runtime_config, "load_frozen_f0_isolation", return_value=self.isolation
        ), mock.patch.object(
            f0e_runtime_config,
            "load_frozen_f0_isolation",
            return_value=self.isolation,
        ):
            bundle = runtime_config.load_runtime_bundle()
            _docker, seccomp = runtime_config.runtime_paths()
            self.assertEqual(Path(seccomp).parent, self.f0f_root)
            self.assertRegex(bundle.base_runtime_lock_sha256, r"^[0-9a-f]{64}$")
            with self.assertRaisesRegex(F0FError, "RUNNER_CONFIGURATION_INVALID"):
                runtime_config.load_runtime_bundle(
                    self.f0f_root,
                    f0e_root=Path(__file__).resolve().parents[1] / "infra/f0e",
                )

    def test_f0e_isolated_control_symlink_and_hardlink_are_rejected(self) -> None:
        from platform_foundation.f0e.contracts import F0EError
        from platform_foundation.f0e import runtime_config

        lock = self.f0e_root / "runtime-lock.json"
        target = self.f0e_root / "runtime-lock.target"
        lock.rename(target)
        lock.symlink_to(target.name)
        with mock.patch.object(
            runtime_config, "load_frozen_f0_isolation", return_value=self.isolation
        ):
            with self.assertRaisesRegex(F0EError, "RUNNER_CONFIGURATION_INVALID"):
                runtime_config.load_runtime_bundle()
        lock.unlink()
        target.rename(lock)
        hardlink = self.f0e_root / "runtime-lock.link"
        os.link(lock, hardlink)
        with mock.patch.object(
            runtime_config, "load_frozen_f0_isolation", return_value=self.isolation
        ):
            with self.assertRaisesRegex(F0EError, "RUNNER_CONFIGURATION_INVALID"):
                runtime_config.load_runtime_bundle()

    def test_legacy_runtime_root_overrides_are_rejected(self) -> None:
        from platform_foundation.f0e import runtime_config as f0e_runtime
        from platform_foundation.f0e.contracts import F0EError
        from platform_foundation.f0f import runtime_config as f0f_runtime
        from platform_foundation.f0f.contracts import F0FError

        with mock.patch.object(
            f0e_runtime, "load_frozen_f0_isolation", return_value=None
        ):
            with self.assertRaisesRegex(F0EError, "RUNNER_CONFIGURATION_INVALID"):
                f0e_runtime.load_runtime_bundle(self.f0e_root)
        with mock.patch.object(
            f0f_runtime, "load_frozen_f0_isolation", return_value=None
        ):
            with self.assertRaisesRegex(F0FError, "RUNNER_CONFIGURATION_INVALID"):
                f0f_runtime.load_runtime_bundle(
                    self.f0f_root, f0e_root=self.f0e_root
                )

    def test_f0e_and_f0f_argv_are_project_labeled_and_disjoint(self) -> None:
        from platform_foundation.f0e import supervisor as f0e_supervisor
        from platform_foundation.f0e.runtime_config import (
            load_runtime_bundle,
            runtime_paths,
        )
        from platform_foundation.f0f.supervisor import body_docker_argv
        from tests import test_f0f_controlled_body_gold as f0f_tests

        bundle = load_runtime_bundle()
        docker, seccomp = runtime_paths()
        with mock.patch.object(
            f0e_supervisor, "load_frozen_f0_isolation", return_value=self.isolation
        ):
            f0e_argv = f0e_supervisor.docker_argv(
                docker, seccomp, bundle.container_image_id
            )
            f0f_argv = body_docker_argv(docker, seccomp, bundle.container_image_id)
            self.assertIn(
                "com.anhuan.f111.project=" + self.project + "-f0e", f0e_argv
            )
            self.assertIn(
                "com.anhuan.f111.project=" + self.project + "-f0f", f0f_argv
            )
            self.assertNotEqual(
                f0e_supervisor._container_prefix("f0e"),
                f0e_supervisor._container_prefix("f0f"),
            )
            self.assertEqual(
                f0f_tests._f0f_container_prefix(), self.project + "-f0f-"
            )
            self.assertEqual(
                f0f_tests._f0f_residual_filter(),
                "name=^/" + self.project + "-f0f-",
            )

    def test_f0e_database_helpers_bind_exact_isolation_database(self) -> None:
        from platform_foundation.database import DatabaseConfig
        from tests import test_f0e_local_ocr as f0e_tests

        expected = "f111_f0e_" + "a" * 32

        def database_name(purpose: str) -> str:
            if purpose != "f0e-test":
                raise AssertionError("unexpected purpose")
            return expected

        def dsn_for(role: str, database: str | None = None) -> str:
            if role != "f0d_bootstrap" or database not in {"postgres", expected}:
                raise AssertionError("unexpected isolated DSN request")
            return "postgresql://f0d_bootstrap:synthetic@127.0.0.1:64321/" + database

        def database_config(database: str) -> DatabaseConfig:
            if database != expected:
                raise AssertionError("unexpected isolated database")
            base = "synthetic@127.0.0.1:64321/" + database
            return DatabaseConfig(
                migration_dsn="postgresql://f0d_migration:" + base,
                runtime_dsn="postgresql://f0d_runtime:" + base,
                worker_dsn="postgresql://f0d_worker:" + base,
            )

        isolation = SimpleNamespace(
            database_name=database_name,
            dsn_for=dsn_for,
            database_config=database_config,
        )
        with mock.patch.object(
            f0e_tests, "_FROZEN_F0_ISOLATION", isolation
        ):
            self.assertEqual(f0e_tests._f0e_test_database_name(), expected)
            self.assertEqual(
                f0e_tests._f0e_database_admin_dsn(expected),
                dsn_for("f0d_bootstrap", expected),
            )
            self.assertEqual(
                f0e_tests._f0e_database_config(expected),
                database_config(expected),
            )
            with self.assertRaisesRegex(AssertionError, "unsafe isolated"):
                f0e_tests._f0e_database_admin_dsn("f0e_test_unmanaged")
            with self.assertRaisesRegex(AssertionError, "unsafe isolated"):
                f0e_tests._f0e_database_config("f0e_test_unmanaged")

    def test_acceptance_resources_never_fall_back_under_isolation(self) -> None:
        from platform_foundation.f0e import acceptance as f0e_acceptance
        from platform_foundation.f0f import acceptance as f0f_acceptance

        with mock.patch.object(
            f0e_acceptance, "load_frozen_f0_isolation", return_value=self.isolation
        ):
            vault, runtime = f0e_acceptance._acceptance_resources()
            self.assertTrue(Path(vault).is_relative_to(self.root))
            self.assertEqual(runtime, self.f0e_root)
        with mock.patch.object(
            f0f_acceptance, "load_frozen_f0_isolation", return_value=self.isolation
        ):
            vault, key, runtime, f0e_runtime, observed = (
                f0f_acceptance._acceptance_resources()
            )
            self.assertEqual(Path(vault), self.isolation.f0f_vault_root)
            self.assertEqual(Path(key), self.isolation.f0f_key_file)
            self.assertEqual(
                (runtime, f0e_runtime, observed),
                (self.f0f_root, self.f0e_root, self.isolation),
            )

    def test_isolation_routes_runtime_and_labels_to_one_project(self) -> None:
        from platform_foundation.f0h import runtime_config, supervisor

        isolated = self.root / "f0h-runtime"
        isolated.mkdir(mode=0o700)
        scope = SimpleNamespace(
            f0h_runtime_root=isolated,
            docker_project_for=lambda phase: self.project + "-" + phase,
        )
        with mock.patch.object(
            runtime_config, "load_frozen_f0_isolation", return_value=scope
        ):
            self.assertEqual(runtime_config._infra_root(None), isolated)
            with self.assertRaisesRegex(
                runtime_config.F0HError, "RUNNER_CONFIGURATION_INVALID"
            ):
                runtime_config._infra_root(Path(__file__).resolve().parents[1] / "infra/f0h")
        repository_infra = Path(__file__).resolve().parents[1] / "infra/f0h"
        bundle = runtime_config.load_runtime_bundle(repository_infra)
        docker, seccomp = runtime_config.runtime_paths(repository_infra)
        with mock.patch.object(
            supervisor, "load_frozen_f0_isolation", return_value=scope
        ):
            argv = supervisor.docker_argv(docker, seccomp, bundle.container_image_id)
            self.assertIn(
                "com.anhuan.f111.project=" + self.project + "-f0h", argv
            )
            self.assertTrue(
                argv[argv.index("--name") + 1].startswith(self.project + "-f0h-")
            )


class PrepareFormalInputsUnitTests(unittest.TestCase):
    def test_f0f_key_source_is_task_private_not_legacy_test_scratch(self) -> None:
        self.assertEqual(
            prep.F0F_KEY_SOURCE.parent,
            Path("/private/tmp/anhuan-f111-formal-frozen-inputs"),
        )
        self.assertEqual(prep.F0F_KEY_SOURCE.name, "f0f_source_key")
        self.assertNotIn("acceptance-v01", str(prep.F0F_KEY_SOURCE))

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="f111-input-prep-test-", dir="/private/tmp"
        )
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @contextlib.contextmanager
    def _docker_contract(self, *, socket_ok: bool = True):
        contract_root = _private_directory(
            self.root / ("docker-contract-" + uuid.uuid4().hex)
        )
        binary = contract_root / "docker-real"
        binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
        launcher = contract_root / "docker"
        launcher.symlink_to(binary)
        empty = _private_directory(contract_root / "empty")
        empty.chmod(0o755)
        socket_target = _private_file(
            contract_root / "docker.sock.actual", b"synthetic-socket-placeholder"
        )
        socket_link = contract_root / "docker.sock"
        socket_link.symlink_to(socket_target)
        patches = [
            mock.patch.object(prep, "DOCKER", launcher),
            mock.patch.object(
                prep, "DOCKER_DESKTOP_BINARY", binary, create=True
            ),
            mock.patch.object(
                prep,
                "DOCKER_SHA256",
                hashlib.sha256(binary.read_bytes()).hexdigest(),
                create=True,
            ),
            mock.patch.object(
                prep, "DOCKER_SOCKET_LINK", socket_link, create=True
            ),
            mock.patch.object(
                prep, "DOCKER_EMPTY_CONFIG", empty, create=True
            ),
        ]
        if socket_ok:
            patches.append(mock.patch.object(prep.stat, "S_ISSOCK", return_value=True))
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            yield binary, socket_target

    def test_docker_launcher_executes_only_pinned_target_with_closed_environment(self) -> None:
        with self._docker_contract() as (binary, _socket_target), mock.patch.object(
            prep.subprocess,
            "run",
            return_value=SimpleNamespace(returncode=0, stdout=b"ok\n", stderr=b""),
        ) as run:
            expected_empty = prep.DOCKER_EMPTY_CONFIG
            self.assertEqual(prep._docker_output(("version",)), b"ok\n")
        arguments = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertEqual(arguments[0], str(binary))
        self.assertEqual(environment["DOCKER_HOST"], "unix:///var/run/docker.sock")
        self.assertEqual(environment["DOCKER_CONFIG"], str(expected_empty))
        self.assertEqual(environment["HOME"], str(expected_empty))
        for name in (
            "DOCKER_CONTEXT",
            "COMPOSE_FILE",
            "COMPOSE_PROJECT_NAME",
            "COMPOSE_PROFILES",
            "COMPOSE_ENV_FILES",
        ):
            self.assertNotIn(name, environment)

    def test_docker_launcher_hash_and_socket_type_are_fail_closed(self) -> None:
        with self._docker_contract(), mock.patch.object(
            prep, "DOCKER_SHA256", "0" * 64, create=True
        ), mock.patch.object(prep.subprocess, "run") as run:
            with self.assertRaisesRegex(prep.PrepError, "DOCKER_READ_REJECTED"):
                prep._docker_output(("version",))
            run.assert_not_called()

        with self._docker_contract(socket_ok=False), mock.patch.object(
            prep, "DOCKER_SOCKET_LINK", self.root / "not-a-socket", create=True
        ), mock.patch.object(prep.subprocess, "run") as run:
            _private_file(self.root / "not-a-socket", b"not-a-socket")
            with self.assertRaisesRegex(prep.PrepError, "DOCKER_READ_REJECTED"):
                prep._docker_output(("version",))
            run.assert_not_called()

    def test_docker_dump_uses_the_same_pinned_trust_contract(self) -> None:
        destination = self.root / "source-dump"

        def execute(arguments: object, **kwargs: object) -> SimpleNamespace:
            self.assertIsInstance(arguments, tuple)
            self.assertEqual(arguments[0], str(prep.DOCKER_DESKTOP_BINARY))
            stream = kwargs["stdout"]
            stream.write(b"opaque-dump\n")
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

        with self._docker_contract(), mock.patch.object(
            prep.subprocess, "run", side_effect=execute
        ) as run:
            prep._docker_output_to_private_file(("version",), destination)
        self.assertEqual(destination.read_bytes(), b"opaque-dump\n")
        self.assertEqual(run.call_count, 1)

    def test_docker_target_mutation_during_command_is_rejected(self) -> None:
        with self._docker_contract() as (binary, _socket_target):
            original = binary.read_bytes()

            def mutate(_arguments: object, **_kwargs: object) -> SimpleNamespace:
                binary.write_bytes(original + b"# mutation\n")
                binary.chmod(0o755)
                return SimpleNamespace(returncode=0, stdout=b"ok\n", stderr=b"")

            with mock.patch.object(prep.subprocess, "run", side_effect=mutate):
                with self.assertRaisesRegex(
                    prep.PrepError, "DOCKER_READ_REJECTED"
                ):
                    prep._docker_output(("version",))

    def test_duplicate_json_keys_are_rejected(self) -> None:
        with self.assertRaisesRegex(prep.PrepError, "JSON_DUPLICATE_KEY"):
            prep._load_unique_json(b'{"schema":"one","schema":"two"}')

    def test_sensitive_source_symlink_is_rejected(self) -> None:
        source = _private_file(self.root / "source", b"private-value\n")
        alias = self.root / "alias"
        alias.symlink_to(source)
        destination = self.root / "copy"
        with self.assertRaisesRegex(prep.PrepError, "SOURCE_FILE_REJECTED"):
            prep._copy_stable_file(
                alias,
                destination,
                code="SOURCE_FILE_REJECTED",
                allowed_modes=frozenset({0o600}),
            )
        self.assertFalse(destination.exists())

    def test_sensitive_source_hardlink_is_rejected(self) -> None:
        source = _private_file(self.root / "source", b"private-value\n")
        alias = self.root / "alias"
        os.link(source, alias)
        with self.assertRaisesRegex(prep.PrepError, "SOURCE_FILE_REJECTED"):
            prep._copy_stable_file(
                source,
                self.root / "copy",
                code="SOURCE_FILE_REJECTED",
                allowed_modes=frozenset({0o600}),
            )

    def test_sensitive_source_mode_is_exact(self) -> None:
        source = self.root / "source"
        source.write_bytes(b"private-value\n")
        source.chmod(0o640)
        with self.assertRaisesRegex(prep.PrepError, "SOURCE_FILE_REJECTED"):
            prep._copy_stable_file(
                source,
                self.root / "copy",
                code="SOURCE_FILE_REJECTED",
                allowed_modes=frozenset({0o600}),
            )

    def test_frozen_copy_requires_fixed_sha_and_private_target(self) -> None:
        source = self.root / "frozen"
        source.write_bytes(b"frozen-evidence\n")
        source.chmod(0o644)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        target = self.root / "copy"
        prep._copy_stable_file(
            source,
            target,
            code="FROZEN_INPUT_REJECTED",
            allowed_modes=frozenset({0o600, 0o644}),
            expected_sha256=digest,
        )
        metadata = target.lstat()
        self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
        self.assertEqual(metadata.st_nlink, 1)
        with self.assertRaisesRegex(prep.PrepError, "FROZEN_INPUT_REJECTED"):
            prep._copy_stable_file(
                source,
                self.root / "wrong",
                code="FROZEN_INPUT_REJECTED",
                allowed_modes=frozenset({0o600, 0o644}),
                expected_sha256="0" * 64,
            )

    def _inspect(self, *, port: int = 55432, image_id: str | None = None) -> bytes:
        container_id = "1" * 64
        values = (
            container_id,
            "/anhuan-f0d-postgres-1",
            image_id or "sha256:" + "2" * 64,
            "running",
            "healthy",
            prep.clean_rebuild.SOURCE_COMPOSE_PROJECT,
            prep.clean_rebuild.SOURCE_COMPOSE_SERVICE,
            prep.clean_rebuild.PG_IMAGE,
            [{"HostIp": "127.0.0.1", "HostPort": str(port)}],
        )
        return b"\n".join(
            json.dumps(value, separators=(",", ":")).encode("ascii")
            for value in values
        ) + b"\n"

    def test_source_identity_is_double_inspected_without_config_env(self) -> None:
        calls: list[tuple[str, ...]] = []
        outputs = [b"1" * 64 + b"\n", self._inspect(), self._inspect()]

        def fake(arguments: tuple[str, ...]) -> bytes:
            calls.append(arguments)
            return outputs.pop(0)

        with mock.patch.object(prep, "_docker_output", side_effect=fake):
            raw, port = prep._source_identity_bytes()
        self.assertEqual(port, 55432)
        self.assertEqual(
            prep.clean_rebuild.parse_source_container_identity(
                raw, expected_port=55432
            ).container_id,
            "1" * 64,
        )
        flattened = " ".join(" ".join(call) for call in calls)
        self.assertNotIn("Config.Env", flattened)
        self.assertEqual(len(calls), 3)

    def test_source_identity_drift_is_rejected(self) -> None:
        outputs = [
            b"1" * 64 + b"\n",
            self._inspect(),
            self._inspect(image_id="sha256:" + "3" * 64),
        ]
        with mock.patch.object(prep, "_docker_output", side_effect=outputs):
            with self.assertRaisesRegex(prep.PrepError, "SOURCE_IDENTITY_DRIFT"):
                prep._source_identity_bytes()

    def test_source_scope_is_exact_and_contains_no_database_credential(self) -> None:
        identity = prep._selected_inspect_identity(self._inspect(), "1" * 64)
        raw = prep._source_scope_bytes(
            prep._canonical_bytes(
                {
                    "container_id": identity.container_id,
                    "container_name": identity.container_name,
                    "compose_project": identity.compose_project,
                    "compose_service": identity.compose_service,
                    "image_id": identity.image_id,
                    "image_reference": identity.image_reference,
                    "published_port": identity.published_port,
                }
            )
        )
        value = json.loads(raw, object_pairs_hook=prep._unique_object)
        self.assertEqual(set(value), prep.SOURCE_SCOPE_KEYS)
        self.assertEqual(value["schema"], prep.SOURCE_SCOPE_SCHEMA)
        lowered = raw.lower()
        for forbidden in (b"password", b"username", b"dsn", b"postgresql://"):
            self.assertNotIn(forbidden, lowered)

    def test_f0g_source_aggregate_uses_fixed_read_only_contract(self) -> None:
        identity = prep._selected_inspect_identity(self._inspect(), "1" * 64)
        output = (
            b"BEGIN\n"
            + (
                "f0f_acceptance_v01|"
                + prep.clean_rebuild.F0G_SOURCE_ROLE
                + "|on|f0d_0004|2|3|4|5\n"
            ).encode("ascii")
            + b"COMMIT\n"
        )
        with mock.patch.object(prep, "_docker_output", return_value=output) as run:
            digest = prep._f0g_source_aggregate(identity)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        arguments = run.call_args.args[0]
        self.assertEqual(arguments[:5], ("exec", "--user", "postgres", identity.container_id, "psql"))
        self.assertIn(
            "--username=" + prep.clean_rebuild.SOURCE_DATABASE_SUPERUSER,
            arguments,
        )
        self.assertIn("--dbname=f0f_acceptance_v01", arguments)
        statement = arguments[-1]
        self.assertIn("READ ONLY", statement)
        self.assertIn("DEFERRABLE", statement)
        self.assertIn("--no-password", arguments)
        for forbidden in ("PGPASSWORD", "postgresql://", "Config.Env"):
            self.assertNotIn(forbidden, " ".join(arguments))

    def test_f0g_source_dump_uses_fixed_schema_filters(self) -> None:
        self.assertEqual(
            prep.clean_rebuild.F0G_SOURCE_ROLE,
            prep.clean_rebuild.SOURCE_DATABASE_SUPERUSER,
        )
        identity = prep._selected_inspect_identity(self._inspect(), "1" * 64)
        destination = self.root / "dump"

        def write_dump(arguments: tuple[str, ...], path: Path) -> None:
            self.assertEqual(path, destination)
            self.assertIn("--schema=f0d", arguments)
            self.assertIn("--schema=f0e", arguments)
            self.assertIn("--schema=f0f", arguments)
            self.assertIn("--exclude-table-data=f0d.alembic_version", arguments)
            self.assertIn("--data-only", arguments)
            self.assertIn("--no-password", arguments)
            _private_file(
                path,
                b"COPY f0e.opaque_parent (id) FROM stdin;\n1\n\\.\n"
                b"COPY f0f.opaque_table (id) FROM stdin;\n1\n\\.\n",
            )

        with mock.patch.object(
            prep, "_docker_output_to_private_file", side_effect=write_dump
        ):
            digest = prep._f0g_source_dump_digest(identity, destination)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_f0g_scope_is_exact_credential_free_and_cleans_dumps(self) -> None:
        identity = prep._selected_inspect_identity(self._inspect(), "1" * 64)
        identity_raw = prep._canonical_bytes(
            {
                "container_id": identity.container_id,
                "container_name": identity.container_name,
                "compose_project": identity.compose_project,
                "compose_service": identity.compose_service,
                "image_id": identity.image_id,
                "image_reference": identity.image_reference,
                "published_port": identity.published_port,
            }
        )

        def dump(_identity: object, path: Path) -> str:
            _private_file(path, b"private-dump-material")
            return "4" * 64

        with mock.patch.object(prep, "_validate_live_source") as validate, mock.patch.object(
            prep, "_f0g_source_aggregate", return_value="3" * 64
        ) as aggregate, mock.patch.object(
            prep, "_f0g_source_dump_digest", side_effect=dump
        ) as dump_call:
            raw = prep._f0g_source_scope_bytes(identity_raw, self.root)
        document = json.loads(raw, object_pairs_hook=prep._unique_object)
        self.assertEqual(set(document), prep.F0G_SOURCE_SCOPE_KEYS)
        self.assertEqual(document["schema"], prep.F0G_SOURCE_SCOPE_SCHEMA)
        self.assertEqual(document["schemas"], ["f0d", "f0e", "f0f"])
        self.assertIs(document["read_only"], True)
        self.assertEqual(document["dump_sha256"], "4" * 64)
        self.assertEqual(document["aggregate_sha256"], "3" * 64)
        self.assertEqual(validate.call_count, 3)
        self.assertEqual(aggregate.call_count, 3)
        self.assertEqual(dump_call.call_count, 2)
        self.assertFalse((self.root / ".f0g-source-dump-first").exists())
        self.assertFalse((self.root / ".f0g-source-dump-second").exists())
        lowered = raw.lower()
        for forbidden in (b"host", b"password", b"username", b"dsn", b"postgresql://"):
            self.assertNotIn(forbidden, lowered)
        changed = dict(document)
        changed["password"] = "forbidden"
        with self.assertRaisesRegex(prep.PrepError, "F0G_SOURCE_SCOPE_REJECTED"):
            prep._validate_f0g_source_scope(prep._canonical_bytes(changed))

    def test_f0g_scope_rejects_aggregate_or_dump_drift(self) -> None:
        identity = prep._selected_inspect_identity(self._inspect(), "1" * 64)
        identity_raw = prep._canonical_bytes(
            {
                "container_id": identity.container_id,
                "container_name": identity.container_name,
                "compose_project": identity.compose_project,
                "compose_service": identity.compose_service,
                "image_id": identity.image_id,
                "image_reference": identity.image_reference,
                "published_port": identity.published_port,
            }
        )
        cases = (
            (("1" * 64, "2" * 64, "2" * 64), ("3" * 64, "3" * 64)),
            (("1" * 64, "1" * 64, "1" * 64), ("3" * 64, "4" * 64)),
        )
        for aggregates, dumps in cases:
            with self.subTest(aggregates=aggregates, dumps=dumps), mock.patch.object(
                prep, "_validate_live_source"
            ), mock.patch.object(
                prep, "_f0g_source_aggregate", side_effect=aggregates
            ), mock.patch.object(
                prep, "_f0g_source_dump_digest", side_effect=dumps
            ):
                with self.assertRaisesRegex(prep.PrepError, "F0G_SOURCE_DRIFT"):
                    prep._f0g_source_scope_bytes(identity_raw, self.root)
            self.assertFalse((self.root / ".f0g-source-dump-first").exists())
            self.assertFalse((self.root / ".f0g-source-dump-second").exists())

    def test_grounding_query_is_read_only_opaque_and_requires_one_sha(self) -> None:
        identity = prep._selected_inspect_identity(self._inspect(), "1" * 64)
        first = uuid.UUID("10000000-0000-4000-8000-000000000001")
        second = uuid.UUID("10000000-0000-4000-8000-000000000002")
        digest = "4" * 64
        query_output = (
            b"BEGIN\n"
            + str(first).encode("ascii")
            + b"|"
            + digest.encode("ascii")
            + b"\n"
            + str(second).encode("ascii")
            + b"|"
            + digest.encode("ascii")
            + b"\nCOMMIT\n"
        )
        calls: list[tuple[str, ...]] = []

        def fake(arguments: tuple[str, ...]) -> bytes:
            calls.append(arguments)
            return self._inspect() if arguments[0] == "container" else query_output

        with mock.patch.object(prep, "_docker_output", side_effect=fake):
            observed = prep._document_fixture_sha256(identity, (first, second))
        self.assertEqual(observed, digest)
        exec_call = next(call for call in calls if call[0] == "exec")
        statement = exec_call[-1]
        self.assertIn("READ ONLY", statement)
        self.assertIn("CANONICAL_SCOPE_INCLUDED", statement)
        self.assertIn("CORE_FIXTURE", statement)
        self.assertIn("chunk_level='CHILD'", statement)
        for forbidden in ("body", "question", "answer", "Config.Env"):
            self.assertNotIn(forbidden, statement)

    def test_grounding_query_rejects_multiple_fixture_hashes(self) -> None:
        identity = prep._selected_inspect_identity(self._inspect(), "1" * 64)
        first = uuid.UUID("10000000-0000-4000-8000-000000000001")
        second = uuid.UUID("10000000-0000-4000-8000-000000000002")
        query_output = (
            b"BEGIN\n"
            + str(first).encode("ascii")
            + b"|"
            + b"4" * 64
            + b"\n"
            + str(second).encode("ascii")
            + b"|"
            + b"5" * 64
            + b"\nCOMMIT\n"
        )
        outputs = [self._inspect(), query_output, self._inspect()]
        with mock.patch.object(prep, "_docker_output", side_effect=outputs):
            with self.assertRaisesRegex(
                prep.PrepError, "QUESTION_GROUNDING_NOT_PROVEN"
            ):
                prep._document_fixture_sha256(identity, (first, second))

    def test_fixture_selection_requires_registered_pdf_and_jpeg(self) -> None:
        entries = tuple(
            SimpleNamespace(
                source_id=str(index),
                document_type=kind,
                expected_sha256=f"{index:064x}",
            )
            for index, kind in enumerate(("PDF", "PDF", "PDF", "JPEG", "DOC"), 1)
        )
        selected = prep._select_fixture_entries(entries)
        self.assertEqual([entry.document_type for entry in selected], ["PDF", "PDF", "PDF", "JPEG"])
        with self.assertRaisesRegex(prep.PrepError, "FIXTURE_NOT_REGISTERED"):
            prep._registered_fixture_record(
                selected[0],
                registered_ids=frozenset(),
                source_root=self.root,
                staging_directory=self.root,
                published_directory=self.root,
            )

    def test_registered_fixture_is_copied_to_an_opaque_private_target(self) -> None:
        source_root = _private_directory(self.root / "registered")
        raw = b"registered-fixture-body"
        source = _private_file(source_root / "source", raw)
        staging = _private_directory(self.root / "staging-fixtures")
        published = self.root / "published-fixtures"
        source_id = uuid.uuid5(_BUNDLE_TEST_NAMESPACE, "registered-fixture")
        entry = SimpleNamespace(
            source_id=source_id,
            expected_sha256=hashlib.sha256(raw).hexdigest(),
            expected_size=len(raw),
            document_type="PDF",
            _manifest_entry=SimpleNamespace(relative_path="source"),
        )

        @contextlib.contextmanager
        def open_source(_entry: SimpleNamespace):
            descriptor = os.open(source, os.O_RDONLY)
            try:
                yield descriptor
            finally:
                os.close(descriptor)

        before = source.lstat()
        with mock.patch.object(
            prep.catalog, "open_catalog_source", side_effect=open_source
        ):
            record = prep._registered_fixture_record(
                entry,
                registered_ids=frozenset({source_id}),
                source_root=source_root,
                staging_directory=staging,
                published_directory=published,
            )
        copy = staging / source_id.hex
        metadata = copy.lstat()
        self.assertEqual(record["path"], str(published / source_id.hex))
        self.assertNotIn(source.name, Path(record["path"]).name)
        self.assertEqual(copy.read_bytes(), raw)
        self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
        self.assertEqual(metadata.st_nlink, 1)
        after = source.lstat()
        self.assertEqual(
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns),
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns),
        )

    def test_evaluation_preflight_does_not_return_answer_or_citation_body(self) -> None:
        first = "10000000-0000-4000-8000-000000000001"
        second = "10000000-0000-4000-8000-000000000002"
        document = {
            "schema": "f0j1-evaluation-samples-v1",
            "samples": [
                {
                    "sample_id": "sample-b",
                    "expected_behavior": "answerable",
                    "query": "请仅依据资料回答并引用。",
                    "answer": "answer-private-marker",
                    "refusal_reason": None,
                    "citations": [
                        {"document_id": first, "body": "body-private-marker"},
                        {"document_id": second, "body": "body-private-marker"},
                    ],
                }
            ],
        }
        candidates = prep._evaluation_candidates(document)
        self.assertEqual(len(candidates), 1)
        rendered = repr(candidates)
        self.assertNotIn("answer-private-marker", rendered)
        self.assertNotIn("body-private-marker", rendered)

    def test_grounded_inputs_returns_the_proven_evaluation_query(self) -> None:
        staging = _private_directory(self.root / "fixture-stage")
        published = self.root / "published-fixtures"
        query = "请仅依据已验证样本回答并附引用。".encode("utf-8")
        document_ids = (
            uuid.UUID("10000000-0000-4000-8000-000000000001"),
            uuid.UUID("10000000-0000-4000-8000-000000000002"),
        )
        entries = tuple(
            SimpleNamespace(
                source_id=uuid.uuid5(_BUNDLE_TEST_NAMESPACE, f"grounded:{index}"),
                document_type=("JPEG" if index == 4 else "PDF"),
                expected_sha256=f"{index:064x}",
                corpus_role="CORE_FIXTURE",
                enterprise_fact_allowed=True,
            )
            for index in range(1, 5)
        )

        def resolve(_identity: object, values: tuple[uuid.UUID, ...]) -> str:
            return entries[document_ids.index(values[0])].expected_sha256

        def record(entry: SimpleNamespace, **arguments: object) -> dict[str, str]:
            stage = Path(str(arguments["staging_directory"]))
            final = Path(str(arguments["published_directory"]))
            _private_file(stage / entry.source_id.hex, b"fixture")
            return {
                "path": str(final / entry.source_id.hex),
                "sha256": entry.expected_sha256,
                "content_type": (
                    "image/jpeg" if entry.document_type == "JPEG" else "application/pdf"
                ),
            }

        identity = prep._selected_inspect_identity(self._inspect(), "1" * 64)
        identity_raw = prep._canonical_bytes(
            {
                "container_id": identity.container_id,
                "container_name": identity.container_name,
                "compose_project": identity.compose_project,
                "compose_service": identity.compose_service,
                "image_id": identity.image_id,
                "image_reference": identity.image_reference,
                "published_port": identity.published_port,
            }
        )
        with mock.patch.object(
            prep,
            "_evaluation_question_candidates",
            return_value=(("opaque-sample", query, document_ids),),
        ), mock.patch.object(
            prep, "_document_fixture_sha256", side_effect=resolve
        ), mock.patch.object(
            prep, "_registered_fixture_record", side_effect=record
        ):
            manifest, observed = prep._grounded_inputs(
                self.root,
                identity_raw,
                entries,
                staging,
                published,
            )
        self.assertEqual(observed, query)
        paths = [Path(item["path"]) for item in json.loads(manifest)]
        self.assertEqual(len(paths), 4)
        self.assertEqual({path.parent for path in paths}, {published})

    def test_frozen_bundle_contract_matches_clean_rebuild_consumer(self) -> None:
        observed = {
            item.bundle_name: (item.relative_path, item.sha256)
            for item in prep.FROZEN_INPUTS
        }
        self.assertEqual(observed, dict(prep.clean_rebuild.FIXTURE_PLAN_CONTRACTS))

    def test_conflict_probe_question_is_grounding_constrained(self) -> None:
        encoded = prep.QUESTION_ALTERNATE.encode("utf-8")
        self.assertLessEqual(len(encoded), 4096)
        self.assertIn("仅依据", prep.QUESTION_ALTERNATE)
        self.assertIn("引用", prep.QUESTION_ALTERNATE)
        self.assertEqual(prep._validate_question(encoded), encoded)

    def test_main_never_emits_exception_or_secret_material(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
            prep, "prepare_inputs", side_effect=RuntimeError("private-value")
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = prep.main(["prepare_formal_inputs.py"])
        self.assertEqual(result, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "error=INTERNAL_FAILURE\n")
        self.assertNotIn("private-value", stderr.getvalue())

    def test_main_success_stdout_is_one_fixed_token(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(prep, "prepare_inputs"), contextlib.redirect_stdout(
            stdout
        ), contextlib.redirect_stderr(stderr):
            result = prep.main(["prepare_formal_inputs.py"])
        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue(), "INPUT_PREP_READY\n")
        self.assertEqual(stderr.getvalue(), "")

    def test_existing_target_is_rejected_before_any_source_read(self) -> None:
        target = _private_directory(self.root / "occupied")
        with mock.patch.object(prep, "OUTPUT_ROOT", target), mock.patch.object(
            prep, "_derive_primary_root"
        ) as derive:
            with self.assertRaisesRegex(prep.PrepError, "TARGET_OCCUPIED"):
                prep.prepare_inputs()
        derive.assert_not_called()


class PrepareFormalInputsSourceBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="f111-source-bundle-test-", dir="/private/tmp"
        )
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.records, self.bodies = _source_bundle_parts()
        self.bundle = self.root / "bundle"
        self.raw = _write_source_bundle(self.bundle, self.records, self.bodies)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_canonical_bundle_validates_without_path_or_body_metadata(self) -> None:
        prep._validate_fixture_source_bundle(self.bundle, self.records)
        header_length = struct.unpack(">Q", self.raw[8:16])[0]
        header = self.raw[16 : 16 + header_length]
        lowered = header.lower()
        for forbidden in (b"path", b"filename", b"opaque-body"):
            self.assertNotIn(forbidden, lowered)

    def test_body_tamper_is_rejected(self) -> None:
        changed = bytearray(self.raw)
        changed[-1] ^= 1
        _private_file(self.root / "tampered", bytes(changed))
        with self.assertRaisesRegex(prep.PrepError, "SOURCE_BUNDLE_BODY_REJECTED"):
            prep._validate_fixture_source_bundle(self.root / "tampered")

    def test_truncated_payload_is_rejected(self) -> None:
        _private_file(self.root / "truncated", self.raw[:-1])
        with self.assertRaisesRegex(prep.PrepError, "SOURCE_BUNDLE_TRUNCATED"):
            prep._validate_fixture_source_bundle(self.root / "truncated")

    def test_discontinuous_offset_is_rejected(self) -> None:
        records = [dict(record) for record in self.records]
        records[1]["offset"] = int(records[1]["offset"]) + 1
        path = self.root / "offset"
        _write_source_bundle(path, records, self.bodies)
        with self.assertRaisesRegex(prep.PrepError, "SOURCE_BUNDLE_OFFSET_REJECTED"):
            prep._validate_fixture_source_bundle(path)

    def test_noncanonical_order_is_rejected(self) -> None:
        records = [dict(record) for record in reversed(self.records)]
        path = self.root / "order"
        _write_source_bundle(path, records, list(reversed(self.bodies)))
        with self.assertRaisesRegex(prep.PrepError, "SOURCE_BUNDLE_ORDER_REJECTED"):
            prep._validate_fixture_source_bundle(path)

    def test_oversize_bundle_is_rejected_before_read(self) -> None:
        with mock.patch.object(prep, "MAX_SOURCE_BUNDLE_BYTES", len(self.raw) - 1):
            with self.assertRaisesRegex(prep.PrepError, "SOURCE_BUNDLE_OVERSIZE"):
                prep._validate_fixture_source_bundle(self.bundle)


class PrepareFormalInputsRuntimeTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="f111-runtime-tree-test-", dir="/private/tmp"
        )
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.source = _private_directory(self.root / "source")
        nested = _private_directory(self.source / "nested")
        _private_file(self.source / "control", b"control-data")
        _private_file(nested / "model", b"model-data")
        entries = []
        for relative in (Path("control"), Path("nested/model")):
            path = self.source / relative
            raw = path.read_bytes()
            entries.append(
                {
                    "relative_path": relative.as_posix(),
                    "mode": stat.S_IMODE(path.lstat().st_mode),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "size": len(raw),
                }
            )
        tree_sha256 = hashlib.sha256(prep._canonical_bytes(entries)).hexdigest()
        self.contract = prep.RuntimeTreeContract(
            phase="test",
            bundle_name="test_runtime_tree_bundle",
            relative_root=Path("infra/test"),
            files=(Path("control"), Path("nested/model")),
            tree_sha256=tree_sha256,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_production_runtime_tree_contracts_are_frozen(self) -> None:
        contracts = {item.phase: item for item in prep.RUNTIME_TREE_CONTRACTS}
        self.assertEqual(set(contracts), {"f0e", "f0f", "f0h"})
        self.assertEqual(
            {phase: len(contract.files) for phase, contract in contracts.items()},
            {"f0e": 11, "f0f": 10, "f0h": 28},
        )
        self.assertEqual(
            {phase: contract.tree_sha256 for phase, contract in contracts.items()},
            {
                "f0e": "18108f9d5336b34b7a898b9683b325a251769e4ca080565ef0adda8f2eab7e55",
                "f0f": "229c9078caccdeb6ff0d94ad6da9eab72d167ee89d4236fe73aec3cffe9a7ea6",
                "f0h": "3b705b9c88f65df44db3afbe5a8b278b2c7e322c8f3f850152f96c93762026d8",
            },
        )

    def test_runtime_tree_bundle_is_private_canonical_and_replayable(self) -> None:
        bundle = self.root / "bundle"
        prep._write_runtime_tree_bundle(self.source, self.contract, bundle)
        prep._validate_runtime_tree_bundle(bundle, self.contract)
        info = bundle.lstat()
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        raw = bundle.read_bytes()
        header_size = struct.unpack(">Q", raw[8:16])[0]
        header = raw[16 : 16 + header_size]
        self.assertNotIn(str(self.source).encode(), header)
        self.assertNotIn(b"control-data", header)

    def test_runtime_tree_source_symlink_is_rejected(self) -> None:
        source = self.source / "control"
        target = self.source / "control-target"
        source.rename(target)
        source.symlink_to(target.name)
        with self.assertRaisesRegex(prep.PrepError, "RUNTIME_TREE_SOURCE_REJECTED"):
            prep._write_runtime_tree_bundle(
                self.source, self.contract, self.root / "symlink-bundle"
            )

    def test_runtime_tree_source_hardlink_is_rejected(self) -> None:
        os.link(self.source / "control", self.source / "hardlink")
        with self.assertRaisesRegex(prep.PrepError, "RUNTIME_TREE_SOURCE_REJECTED"):
            prep._write_runtime_tree_bundle(
                self.source, self.contract, self.root / "hardlink-bundle"
            )

    def test_runtime_tree_extra_source_is_rejected(self) -> None:
        _private_file(self.source / "extra", b"extra")
        with self.assertRaisesRegex(prep.PrepError, "RUNTIME_TREE_SOURCE_REJECTED"):
            prep._write_runtime_tree_bundle(
                self.source, self.contract, self.root / "extra-bundle"
            )

    def test_runtime_tree_bundle_tamper_is_rejected(self) -> None:
        bundle = self.root / "bundle"
        prep._write_runtime_tree_bundle(self.source, self.contract, bundle)
        changed = bytearray(bundle.read_bytes())
        changed[-1] ^= 1
        tampered = _private_file(self.root / "tampered", bytes(changed))
        with self.assertRaisesRegex(prep.PrepError, "RUNTIME_TREE_BODY_REJECTED"):
            prep._validate_runtime_tree_bundle(tampered, self.contract)


class PrepareFormalInputsBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="f111-input-bundle-test-", dir="/private/tmp"
        )
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.primary = _private_directory(self.root / "primary")
        self.provider = _private_directory(self.root / "provider-source")
        self.key = _private_file(self.root / "f0i-key-source", b"k" * 32 + b"\n")
        self.f0f_key = _private_file(self.root / "f0f-key-source", b"f" * 32)
        for name in prep.clean_rebuild.PROVIDER_SECRET_FILES:
            _private_file(self.provider / name, (name + "-private-value\n").encode())
        self.fixture_root = _private_directory(self.root / "fixture-sources")
        records, bodies = _source_bundle_parts()
        self.entries: list[SimpleNamespace] = []
        self.entry_paths: dict[uuid.UUID, Path] = {}
        for index, (record, body) in enumerate(zip(records, bodies)):
            source_id = uuid.UUID(str(record["source_id"]))
            path = _private_file(self.fixture_root / source_id.hex, body)
            self.entry_paths[source_id] = path
            self.entries.append(
                SimpleNamespace(
                    source_id=source_id,
                    group=record["group"],
                    line=record["line"],
                    expected_sha256=record["sha256"],
                    expected_size=record["size"],
                    document_type=("JPEG" if index == 3 else "PDF"),
                )
            )
        self.frozen: tuple[prep.FrozenInput, ...] = ()
        values: list[prep.FrozenInput] = []
        for index in range(4):
            relative = Path(f"frozen-{index}")
            raw = (f"frozen-{index}\n").encode()
            source = self.primary / relative
            source.write_bytes(raw)
            source.chmod(0o644)
            values.append(
                prep.FrozenInput(
                    bundle_name=f"catalog_input_{index}",
                    relative_path=relative,
                    sha256=hashlib.sha256(raw).hexdigest(),
                )
            )
        self.frozen = tuple(values)
        runtime_root = _private_directory(self.primary / "runtime-tree")
        runtime_file = _private_file(runtime_root / "control", b"runtime-control")
        runtime_record = {
            "relative_path": "control",
            "mode": stat.S_IMODE(runtime_file.lstat().st_mode),
            "sha256": hashlib.sha256(runtime_file.read_bytes()).hexdigest(),
            "size": runtime_file.lstat().st_size,
        }
        self.runtime_contracts = (
            prep.RuntimeTreeContract(
                phase="test",
                bundle_name="test_runtime_tree_bundle",
                relative_root=Path("runtime-tree"),
                files=(Path("control"),),
                tree_sha256=hashlib.sha256(
                    prep._canonical_bytes([runtime_record])
                ).hexdigest(),
            ),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_bundle_is_private_exact_and_config_v2(self) -> None:
        target = self.root / "formal-inputs"
        identity = {
            "container_id": "1" * 64,
            "container_name": "anhuan-f0d-postgres-1",
            "compose_project": prep.clean_rebuild.SOURCE_COMPOSE_PROJECT,
            "compose_service": prep.clean_rebuild.SOURCE_COMPOSE_SERVICE,
            "image_id": "sha256:" + "2" * 64,
            "image_reference": prep.clean_rebuild.PG_IMAGE,
            "published_port": 55432,
        }
        identity_raw = prep._canonical_bytes(identity)
        source_scope_raw = prep._canonical_bytes(
            {
                "schema": prep.SOURCE_SCOPE_SCHEMA,
                "host": "127.0.0.1",
                "database": prep.clean_rebuild.SOURCE_DATABASE_NAME,
                "access": "LOCAL_DOCKER_EXEC_READ_ONLY",
                **identity,
            }
        )
        f0g_scope_raw = prep._canonical_bytes(
            {
                "schema": prep.F0G_SOURCE_SCOPE_SCHEMA,
                "database": prep.clean_rebuild.F0G_SOURCE_DATABASE_NAME,
                "role": prep.clean_rebuild.F0G_SOURCE_ROLE,
                "schemas": list(prep.clean_rebuild.F0G_SOURCE_SCHEMAS),
                "access": "LOCAL_DOCKER_EXEC_READ_ONLY",
                "read_only": True,
                **identity,
                "dump_sha256": "3" * 64,
                "aggregate_sha256": "4" * 64,
            }
        )

        @contextlib.contextmanager
        def open_source(entry: SimpleNamespace):
            descriptor = os.open(self.entry_paths[entry.source_id], os.O_RDONLY)
            try:
                yield descriptor
            finally:
                os.close(descriptor)

        def grounded(
            _primary: Path,
            _identity: bytes,
            entries: list[SimpleNamespace],
            staging: Path,
            published: Path,
        ) -> tuple[bytes, bytes]:
            chosen = (entries[0], entries[1], entries[2], entries[3])
            manifest: list[dict[str, str]] = []
            for index, entry in enumerate(chosen):
                body = self.entry_paths[entry.source_id].read_bytes()
                _private_file(staging / entry.source_id.hex, body)
                manifest.append(
                    {
                        "path": str(published / entry.source_id.hex),
                        "sha256": entry.expected_sha256,
                        "content_type": (
                            "image/jpeg" if index == 3 else "application/pdf"
                        ),
                    }
                )
            return (
                prep._canonical_bytes(manifest),
                "请仅依据已登记资料回答并提供引用。".encode("utf-8"),
            )

        with mock.patch.object(prep, "OUTPUT_ROOT", target), mock.patch.object(
            prep, "PROVIDER_SOURCE", self.provider
        ), mock.patch.object(prep, "F0I_KEY_SOURCE", self.key), mock.patch.object(
            prep, "F0F_KEY_SOURCE", self.f0f_key
        ), mock.patch.object(
            prep, "FROZEN_INPUTS", self.frozen
        ), mock.patch.object(
            prep, "RUNTIME_TREE_CONTRACTS", self.runtime_contracts
        ), mock.patch.object(
            prep, "_derive_primary_root", return_value=self.primary
        ), mock.patch.object(
            prep, "_source_identity_bytes", return_value=(identity_raw, 55432)
        ), mock.patch.object(
            prep, "_source_scope_bytes", return_value=source_scope_raw
        ), mock.patch.object(
            prep, "_f0g_source_scope_bytes", return_value=f0g_scope_raw
        ), mock.patch.object(
            prep, "_catalog_entries", return_value=tuple(self.entries)
        ), mock.patch.object(
            prep.catalog, "open_catalog_source", side_effect=open_source
        ), mock.patch.object(
            prep,
            "_grounded_inputs",
            side_effect=grounded,
        ):
            prep.prepare_inputs()

        self.assertEqual(stat.S_IMODE(target.lstat().st_mode), 0o700)
        config_path = target / prep.SOURCE_CONFIG_NAME
        self.assertEqual(stat.S_IMODE(config_path.lstat().st_mode), 0o600)
        config = json.loads(config_path.read_bytes(), object_pairs_hook=prep._unique_object)
        self.assertEqual(
            set(config),
            {
                "schema",
                "secrets_directory",
                "provider_secrets_directory",
                "f0i_key_file",
                "f0g_source_scope_file",
            },
        )
        self.assertEqual(config["schema"], prep.CONFIG_SCHEMA)
        secrets_directory = Path(config["secrets_directory"])
        provider_directory = Path(config["provider_secrets_directory"])
        self.assertEqual(secrets_directory, target / prep.SECRETS_DIRECTORY_NAME)
        self.assertEqual(provider_directory, target / prep.PROVIDER_DIRECTORY_NAME)
        self.assertEqual(
            Path(config["f0g_source_scope_file"]),
            secrets_directory / prep.F0G_SOURCE_SCOPE_NAME,
        )
        fixture_directory = target / prep.FIXTURE_DIRECTORY_NAME
        for directory in (secrets_directory, provider_directory, fixture_directory):
            self.assertEqual(stat.S_IMODE(directory.lstat().st_mode), 0o700)
        expected_secret_names = set(prep.clean_rebuild.COPY_SECRET_FILES) | {
            "f0i_source_scope",
            prep.F0G_SOURCE_SCOPE_NAME,
            "fixture_manifest",
            "question_primary",
            "question_alternate",
            prep.F0F_KEY_BUNDLE_NAME,
            prep.SOURCE_BUNDLE_NAME,
            *(item.bundle_name for item in self.frozen),
            *(item.bundle_name for item in self.runtime_contracts),
        }
        self.assertEqual(
            {path.name for path in secrets_directory.iterdir()}, expected_secret_names
        )
        self.assertNotIn("f0i_source_dsn", expected_secret_names)
        self.assertEqual(
            (secrets_directory / prep.F0F_KEY_BUNDLE_NAME).lstat().st_size, 32
        )
        for path in (*secrets_directory.iterdir(), *provider_directory.iterdir(), target / prep.F0I_KEY_NAME):
            metadata = path.lstat()
            self.assertTrue(stat.S_ISREG(metadata.st_mode))
            self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
            self.assertEqual(metadata.st_nlink, 1)
        self.assertEqual(len(tuple(fixture_directory.iterdir())), 4)
        prep._validate_fixture_source_bundle(
            secrets_directory / prep.SOURCE_BUNDLE_NAME
        )
        prep._validate_runtime_tree_bundle(
            secrets_directory / self.runtime_contracts[0].bundle_name,
            self.runtime_contracts[0],
        )
        manifest = json.loads(
            (secrets_directory / "fixture_manifest").read_bytes(),
            object_pairs_hook=prep._unique_object,
        )
        self.assertEqual(
            {Path(item["path"]).parent for item in manifest}, {fixture_directory}
        )
        self.assertNotEqual(
            (secrets_directory / "question_primary").read_bytes(),
            (secrets_directory / "question_alternate").read_bytes(),
        )


if __name__ == "__main__":
    unittest.main()
