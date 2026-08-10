from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid

from platform_foundation.f0_isolation import (
    ENVIRONMENT_VARIABLE,
    REASON_CODE,
    SCHEMA,
    FrozenF0Isolation,
    FrozenF0IsolationError,
    build_frozen_f0_isolation,
    load_frozen_f0_isolation,
    validate_frozen_f0_isolation,
    write_frozen_f0_isolation,
)


def _canonical(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _private_write(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise AssertionError("short test write")
            offset += written
    finally:
        os.close(descriptor)


def _dsn(role: str, port: int, database: str) -> bytes:
    return (
        f"postgresql://{role}:synthetic-{role}@127.0.0.1:{port}/{database}"
    ).encode("ascii")


class _IsolationWorkspace:
    def __init__(self) -> None:
        self.project_id = uuid.uuid4()
        self.temporary = tempfile.TemporaryDirectory(
            prefix=f"anhuan-f111-repair-f0-{self.project_id.hex}-",
            dir="/private/tmp",
        )
        self.root = Path(self.temporary.name)
        os.chmod(self.root, 0o700)
        self.isolation = build_frozen_f0_isolation(
            self.root,
            self.project_id,
            48000 + self.project_id.int % 1000,
        )
        for directory in (
            self.isolation.tmp_dir,
            self.isolation.fixture_source_root,
            self.isolation.bootstrap_dsn_file.parent,
            self.isolation.f0e_runtime_root,
            self.isolation.f0f_runtime_root,
            self.isolation.f0f_vault_root,
            self.isolation.f0h_runtime_root,
        ):
            directory.mkdir(mode=0o700)
        for role, path in (
            ("f0d_bootstrap", self.isolation.bootstrap_dsn_file),
            ("f0d_migration", self.isolation.migration_dsn_file),
            ("f0d_runtime", self.isolation.runtime_dsn_file),
            ("f0d_worker", self.isolation.worker_dsn_file),
        ):
            database = (
                "postgres"
                if role == "f0d_bootstrap"
                else self.isolation.f0i_template_database
            )
            _private_write(path, _dsn(role, self.isolation.postgres_port, database))
        _private_write(self.isolation.f0f_key_file, bytes(range(32)))
        self.config_path = self.root / "isolation.json"

    def publish(self) -> Path:
        return write_frozen_f0_isolation(self.config_path, self.isolation)

    def rewrite_payload(self, payload: dict[str, object]) -> None:
        self.config_path.unlink(missing_ok=True)
        _private_write(self.config_path, _canonical(payload))

    def close(self) -> None:
        self.temporary.cleanup()

    def run_python(self, source: str, *, isolated: bool = True) -> int:
        environment = os.environ.copy()
        if isolated:
            if not self.config_path.exists():
                self.publish()
            environment[ENVIRONMENT_VARIABLE] = str(self.config_path)
        else:
            environment.pop(ENVIRONMENT_VARIABLE, None)
        project_root = Path(__file__).resolve().parents[1]
        existing_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = str(project_root / "src") + (
            os.pathsep + existing_pythonpath if existing_pythonpath else ""
        )
        result = subprocess.run(
            [sys.executable, "-c", source],
            cwd=project_root,
            env=environment,
            capture_output=True,
            text=False,
            check=False,
        )
        return result.returncode


class FrozenF0IsolationContractTests(unittest.TestCase):
    def test_missing_environment_preserves_legacy_branch(self) -> None:
        self.assertIsNone(load_frozen_f0_isolation({}))

    def test_valid_config_roundtrip_contains_no_dsn_body(self) -> None:
        workspace = _IsolationWorkspace()
        try:
            workspace.publish()
            loaded = load_frozen_f0_isolation(
                {ENVIRONMENT_VARIABLE: str(workspace.config_path)}
            )
            self.assertEqual(loaded, workspace.isolation)
            self.assertIs(validate_frozen_f0_isolation(loaded), loaded)
            payload = workspace.config_path.read_bytes()
            self.assertNotIn(b"synthetic-f0d", payload)
            self.assertNotIn(b"postgresql", payload)
        finally:
            workspace.close()

    def test_factory_derives_complete_deterministic_resource_sets(self) -> None:
        workspace = _IsolationWorkspace()
        try:
            isolation = workspace.isolation
            self.assertEqual(len(isolation.managed_database_names), 10)
            self.assertEqual(len(set(isolation.managed_database_names)), 10)
            self.assertEqual(len(isolation.managed_project_names), 6)
            self.assertEqual(len(set(isolation.managed_project_names)), 6)
            self.assertEqual(
                isolation.managed_container_names,
                (isolation.f0j0_retrieval_container_name,),
            )
            self.assertEqual(
                isolation.f0j0_retrieval_container_name,
                isolation.f0j0_project_name + "-opensearch",
            )
            self.assertEqual(len(isolation.managed_paths), 12)
            self.assertTrue(
                all(
                    path == isolation.runtime_root
                    or path.is_relative_to(isolation.runtime_root)
                    for path in isolation.managed_paths
                )
            )
        finally:
            workspace.close()

    def test_full_uuid_identity_prevents_shared_prefix_collisions(self) -> None:
        prefix = "0123456789abcdef"
        first_id = uuid.UUID(hex=prefix + "0" * 16, version=4)
        second_id = uuid.UUID(hex=prefix + "f" * 16, version=4)
        first_root = Path(
            f"/private/tmp/anhuan-f111-repair-f0-{first_id.hex}-aaaaaaaa"
        )
        second_root = Path(
            f"/private/tmp/anhuan-f111-repair-f0-{second_id.hex}-bbbbbbbb"
        )
        first = build_frozen_f0_isolation(first_root, first_id, 48001)
        second = build_frozen_f0_isolation(second_root, second_id, 48002)
        self.assertTrue(set(first.managed_database_names).isdisjoint(second.managed_database_names))
        self.assertTrue(set(first.managed_project_names).isdisjoint(second.managed_project_names))
        self.assertTrue(
            set(first.managed_container_names).isdisjoint(
                second.managed_container_names
            )
        )

    def test_runtime_root_is_bound_to_private_tmp_and_full_project_uuid(self) -> None:
        project_id = uuid.uuid4()
        bad_roots = (
            Path("/"),
            Path.home(),
            Path(__file__).resolve().parents[1],
            Path(f"/private/tmp/anhuan-f111-repair-f0-{uuid.uuid4().hex}-aaaaaaaa"),
            Path(f"/private/tmp/anhuan-f111-repair-f0-{project_id.hex}-short"),
        )
        for root in bad_roots:
            with self.subTest(root=root), self.assertRaisesRegex(
                FrozenF0IsolationError, REASON_CODE
            ):
                build_frozen_f0_isolation(root, project_id, 48000)

    def test_project_identity_rejects_nil_and_non_v4_uuids(self) -> None:
        identities = (
            uuid.UUID(int=0),
            uuid.uuid1(),
            uuid.uuid5(uuid.NAMESPACE_DNS, "fixture.invalid"),
        )
        for project_id in identities:
            root = Path(
                f"/private/tmp/anhuan-f111-repair-f0-{project_id.hex}-aaaaaaaa"
            )
            with self.subTest(version=project_id.version), self.assertRaisesRegex(
                FrozenF0IsolationError, REASON_CODE
            ):
                build_frozen_f0_isolation(root, project_id, 48000)

    def test_dsn_rebinding_keeps_role_host_port_password_and_managed_database(self) -> None:
        workspace = _IsolationWorkspace()
        try:
            target = workspace.isolation.database_name("f0g-case")
            config = workspace.isolation.database_config(target)
            self.assertIs(
                workspace.isolation.validate_database_config(config),
                config,
            )
            self.assertIn(f":{workspace.isolation.postgres_port}/", config.runtime_dsn)
            self.assertTrue(config.runtime_dsn.endswith("/" + target))
        finally:
            workspace.close()

    def test_unknown_purpose_phase_role_and_database_fail_closed(self) -> None:
        workspace = _IsolationWorkspace()
        try:
            calls = (
                lambda: workspace.isolation.database_name("customer"),
                lambda: workspace.isolation.docker_project_for("customer"),
                lambda: workspace.isolation.dsn_for("postgres"),
                lambda: workspace.isolation.database_config("customer"),
            )
            for call in calls:
                with self.subTest(call=call), self.assertRaisesRegex(
                    FrozenF0IsolationError, REASON_CODE
                ):
                    call()
        finally:
            workspace.close()

    def test_writer_is_create_only_and_requires_all_private_assets(self) -> None:
        workspace = _IsolationWorkspace()
        try:
            workspace.publish()
            with self.assertRaisesRegex(FrozenF0IsolationError, REASON_CODE):
                workspace.publish()
            workspace.config_path.unlink()
            os.chmod(workspace.isolation.tmp_dir, 0o755)
            with self.assertRaisesRegex(FrozenF0IsolationError, REASON_CODE):
                workspace.publish()
            self.assertFalse(workspace.config_path.exists())
        finally:
            workspace.close()

    def test_writer_cleans_its_staging_file_after_short_write(self) -> None:
        workspace = _IsolationWorkspace()
        try:
            with mock.patch("platform_foundation.f0_isolation.os.write", return_value=0):
                with self.assertRaisesRegex(FrozenF0IsolationError, REASON_CODE):
                    workspace.publish()
            self.assertFalse(workspace.config_path.exists())
            self.assertEqual(list(workspace.root.glob(".f0-isolation-*.tmp")), [])
        finally:
            workspace.close()

    def test_writer_removes_new_target_when_final_reread_fails(self) -> None:
        import platform_foundation.f0_isolation as module

        workspace = _IsolationWorkspace()
        original = module._read_private_file

        def fail_final(path: Path, **kwargs: object) -> bytes:
            if Path(path) == workspace.config_path:
                raise FrozenF0IsolationError()
            return original(path, **kwargs)  # type: ignore[arg-type]

        try:
            with mock.patch.object(module, "_read_private_file", side_effect=fail_final):
                with self.assertRaisesRegex(FrozenF0IsolationError, REASON_CODE):
                    workspace.publish()
            self.assertFalse(workspace.config_path.exists())
            self.assertEqual(list(workspace.root.glob(".f0-isolation-*.tmp")), [])
        finally:
            workspace.close()

    def test_writer_rejects_crash_residue_without_deleting_it(self) -> None:
        workspace = _IsolationWorkspace()
        residue = workspace.root / ".f0-isolation-deadbeef.tmp"
        try:
            _private_write(residue, b"residue")
            with self.assertRaisesRegex(FrozenF0IsolationError, REASON_CODE):
                workspace.publish()
            self.assertEqual(residue.read_bytes(), b"residue")
            self.assertFalse(workspace.config_path.exists())
        finally:
            workspace.close()

    def test_config_file_identity_and_canonical_bytes_are_exact(self) -> None:
        attacks = ("relative", "mode", "hardlink", "symlink", "pretty")
        for attack in attacks:
            workspace = _IsolationWorkspace()
            try:
                workspace.publish()
                configured = str(workspace.config_path)
                if attack == "relative":
                    configured = workspace.config_path.name
                elif attack == "mode":
                    os.chmod(workspace.config_path, 0o644)
                elif attack == "hardlink":
                    os.link(workspace.config_path, workspace.root / "second.json")
                elif attack == "symlink":
                    target = workspace.root / "target.json"
                    workspace.config_path.rename(target)
                    os.symlink(target, workspace.config_path)
                elif attack == "pretty":
                    payload = workspace.isolation.to_payload()
                    workspace.config_path.unlink()
                    _private_write(
                        workspace.config_path,
                        json.dumps(payload, indent=2, sort_keys=True).encode("ascii"),
                    )
                with self.subTest(attack=attack), self.assertRaisesRegex(
                    FrozenF0IsolationError, REASON_CODE
                ):
                    load_frozen_f0_isolation({ENVIRONMENT_VARIABLE: configured})
            finally:
                workspace.close()

    def test_environment_path_is_rejected_before_any_outside_root_read(self) -> None:
        import platform_foundation.f0_isolation as module

        with mock.patch.object(module, "_read_private_file") as read:
            with self.assertRaisesRegex(FrozenF0IsolationError, REASON_CODE):
                load_frozen_f0_isolation(
                    {ENVIRONMENT_VARIABLE: str(Path(__file__).resolve())}
                )
            read.assert_not_called()

    def test_exact_json_schema_rejects_unknown_or_mutated_identity(self) -> None:
        mutations = ("unknown", "schema", "project", "port", "path", "duplicate")
        for mutation in mutations:
            workspace = _IsolationWorkspace()
            try:
                payload = workspace.isolation.to_payload()
                if mutation == "unknown":
                    payload["unknown"] = True
                elif mutation == "schema":
                    payload["schema"] = SCHEMA + "-other"
                elif mutation == "project":
                    payload["project_id"] = str(uuid.uuid4())
                elif mutation == "port":
                    payload["postgres"]["port"] = 55432  # type: ignore[index]
                elif mutation == "path":
                    payload["tmp_dir"] = "/private/tmp"
                elif mutation == "duplicate":
                    projects = payload["projects"]
                    projects["f0j1"] = projects["f0j0"]  # type: ignore[index]
                workspace.rewrite_payload(payload)
                with self.subTest(mutation=mutation), self.assertRaisesRegex(
                    FrozenF0IsolationError, REASON_CODE
                ):
                    load_frozen_f0_isolation(
                        {ENVIRONMENT_VARIABLE: str(workspace.config_path)}
                    )
            finally:
                workspace.close()

    def test_dsn_files_reject_wrong_boundary_and_identity_attacks(self) -> None:
        attacks = ("host", "port", "role", "database", "query", "mode", "hardlink")
        for attack in attacks:
            workspace = _IsolationWorkspace()
            try:
                target = workspace.isolation.runtime_dsn_file
                target.unlink()
                host = "127.0.0.2" if attack == "host" else "127.0.0.1"
                port = 55432 if attack == "port" else workspace.isolation.postgres_port
                role = "f0d_worker" if attack == "role" else "f0d_runtime"
                database = (
                    workspace.isolation.f0g_template_database
                    if attack == "database"
                    else workspace.isolation.f0i_template_database
                )
                body = (
                    f"postgresql://{role}:synthetic-{role}@{host}:{port}/{database}"
                )
                if attack == "query":
                    body += "?passfile=/private/tmp/other"
                _private_write(target, body.encode("ascii"))
                if attack == "mode":
                    os.chmod(target, 0o640)
                elif attack == "hardlink":
                    os.link(target, workspace.root / "runtime-copy.dsn")
                with self.subTest(attack=attack), self.assertRaisesRegex(
                    FrozenF0IsolationError, REASON_CODE
                ):
                    validate_frozen_f0_isolation(workspace.isolation)
            finally:
                workspace.close()

    def test_database_config_rejects_cross_database_or_foreign_password(self) -> None:
        workspace = _IsolationWorkspace()
        try:
            name = workspace.isolation.database_name("f0i-migration")
            config = workspace.isolation.database_config(name)
            attacks = (
                replace(config, runtime_dsn=config.runtime_dsn.replace(name, workspace.isolation.f0i_template_database)),
                replace(config, worker_dsn=config.worker_dsn.replace("synthetic-f0d_worker", "foreign")),
            )
            for attack in attacks:
                with self.subTest(attack=attack), self.assertRaisesRegex(
                    FrozenF0IsolationError, REASON_CODE
                ):
                    workspace.isolation.validate_database_config(attack)
        finally:
            workspace.close()

    def test_error_never_renders_input_path_or_dsn_secret(self) -> None:
        workspace = _IsolationWorkspace()
        try:
            secret = "PRIVATE-SYNTHETIC-SECRET"
            workspace.isolation.runtime_dsn_file.unlink()
            _private_write(workspace.isolation.runtime_dsn_file, secret.encode("ascii"))
            with self.assertRaises(FrozenF0IsolationError) as raised:
                validate_frozen_f0_isolation(workspace.isolation)
            rendered = str(raised.exception) + repr(raised.exception.to_dict())
            self.assertEqual(str(raised.exception), REASON_CODE)
            self.assertNotIn(secret, rendered)
            self.assertNotIn(str(workspace.root), rendered)
        finally:
            workspace.close()

    def test_isolated_keyfile_accepts_only_the_validated_exact_path(self) -> None:
        workspace = _IsolationWorkspace()
        try:
            code = """
from pathlib import Path
from platform_foundation.f0_isolation import load_frozen_f0_isolation
from platform_foundation.f0f.keyfile import load_keyfile
from platform_foundation.f0f.contracts import F0FError
isolation = load_frozen_f0_isolation()
assert isolation is not None
with load_keyfile(str(isolation.f0f_key_file)) as key:
    assert len(key.view()) == 32
for candidate in (
    isolation.runtime_root / 'other.key',
    Path('/private/tmp') / ('anhuan-f0f-' + isolation.project_id.hex + '.key'),
):
    try:
        load_keyfile(str(candidate))
    except F0FError as error:
        assert error.code == 'KEYFILE_INVALID'
    else:
        raise AssertionError('key boundary bypass')
"""
            self.assertEqual(workspace.run_python(code), 0, "KEYFILE_ISOLATION_FAILED")
        finally:
            workspace.close()

    def test_keyfile_default_branch_keeps_direct_private_tmp_contract(self) -> None:
        workspace = _IsolationWorkspace()
        try:
            code = """
import os
from pathlib import Path
import tempfile
import uuid
from platform_foundation.f0f.keyfile import create_keyfile, load_keyfile
from platform_foundation.f0f.contracts import F0FError
path = '/private/tmp/anhuan-f0f-' + uuid.uuid4().hex + '.key'
try:
    create_keyfile(path)
    with load_keyfile(path) as key:
        assert len(key.view()) == 32
    with tempfile.TemporaryDirectory(dir='/private/tmp') as root:
        nested = str(Path(root) / 'anhuan-f0f-nested.key')
        try:
            create_keyfile(nested)
        except F0FError as error:
            assert error.code == 'KEYFILE_INVALID'
        else:
            raise AssertionError('legacy nested path accepted')
finally:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
"""
            self.assertEqual(
                workspace.run_python(code, isolated=False),
                0,
                "KEYFILE_DEFAULT_REGRESSION",
            )
        finally:
            workspace.close()

    def test_f0g_validator_and_router_consume_the_same_isolation(self) -> None:
        workspace = _IsolationWorkspace()
        try:
            code = """
from dataclasses import replace
import uuid
from fixture_router.router import REGISTERED_SOURCE_ROOT
from platform_foundation.f0_isolation import load_frozen_f0_isolation
from platform_foundation.f0g.config import validate_local_database_config
isolation = load_frozen_f0_isolation()
assert isolation is not None
assert REGISTERED_SOURCE_ROOT == isolation.fixture_source_root
config = isolation.database_config(isolation.database_name('f0g-case'))
assert validate_local_database_config(config) is config
other = 'f111_f0g_case_' + uuid.uuid4().hex
attacks = (
    replace(config, migration_dsn=config.migration_dsn.replace(str(isolation.postgres_port), '55432')),
    replace(config, runtime_dsn=config.runtime_dsn.rsplit('/', 1)[0] + '/' + other),
    replace(config, worker_dsn=config.worker_dsn.replace('f0d_worker', 'f0d_runtime', 1)),
)
for attack in attacks:
    try:
        validate_local_database_config(attack)
    except RuntimeError as error:
        assert str(error) == 'DATABASE_CONFIGURATION_INVALID'
    else:
        raise AssertionError('database boundary bypass')
"""
            self.assertEqual(workspace.run_python(code), 0, "F0G_ISOLATION_FAILED")
        finally:
            workspace.close()

    def test_existing_frozen_contract_tests_use_the_isolation_without_live_io(self) -> None:
        workspace = _IsolationWorkspace()
        try:
            code = """
import io
import unittest
names = (
    'tests.test_f0f_controlled_body_gold.F0FKeyfileTests',
    'tests.test_f0g_fixture_annotation.F0GContractTests',
    'tests.test_f0g_fixture_annotation.F0GTokenTests',
    'tests.test_f0i_canonical_chunks.F0IKeyLockAndConfigTests',
)
suite = unittest.TestSuite(
    unittest.defaultTestLoader.loadTestsFromName(name) for name in names
)
result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
if not result.wasSuccessful():
    raise AssertionError('FROZEN_F0_ISOLATION_CONTRACT_REGRESSION')
"""
            self.assertEqual(
                workspace.run_python(code),
                0,
                "FROZEN_F0_ISOLATION_CONTRACT_REGRESSION",
            )
        finally:
            workspace.close()

    def test_private_capabilities_are_bound_to_same_uuid_tmp_directory(self) -> None:
        workspace = _IsolationWorkspace()
        try:
            code = """
import contextlib
import os
from pathlib import Path
import stat
import uuid
from platform_foundation.f0_isolation import load_frozen_f0_isolation
from platform_foundation.f0g.contracts import F0GError
from platform_foundation.f0g.tokens import (
    ACCEPTANCE_TOKEN_BUNDLE,
    create_token_bundle,
    load_token_bundle,
)
from platform_foundation.f0i.contracts import F0IError
from platform_foundation.f0i.keyfile import (
    ACCEPTANCE_KEY_FILE,
    create_keyfile,
    load_keyfile,
)
from platform_foundation.f0i.locking import DEFAULT_HOST_LOCK_PATH, HostReplayLock
isolation = load_frozen_f0_isolation()
assert isolation is not None
token = isolation.project_id.hex
assert Path(ACCEPTANCE_KEY_FILE).parent == isolation.tmp_dir
assert Path(ACCEPTANCE_TOKEN_BUNDLE).parent == isolation.tmp_dir
assert Path(DEFAULT_HOST_LOCK_PATH).parent == isolation.tmp_dir
assert token in Path(ACCEPTANCE_KEY_FILE).name
assert token in Path(ACCEPTANCE_TOKEN_BUNDLE).name
assert token in Path(DEFAULT_HOST_LOCK_PATH).name
key_path = isolation.tmp_dir / ('anhuan-f0i-' + token + '-probe.key')
lock_path = isolation.tmp_dir / ('anhuan-f0i-' + token + '-probe.lock')
token_path = isolation.tmp_dir / ('anhuan-f0g-' + token + '-probe.tokens')
created = (key_path, lock_path, token_path)
try:
    create_keyfile(str(key_path))
    with load_keyfile(str(key_path)) as key:
        assert len(key.view()) == 32
    with HostReplayLock(str(lock_path)) as lock:
        assert lock.held
    create_token_bundle(str(token_path))
    with load_token_bundle(str(token_path)) as bundle:
        assert bundle.token('ANNOTATOR_ONE').startswith('f0g_')
    for path, size in ((key_path, 32), (lock_path, 0), (token_path, 96)):
        metadata = path.lstat()
        assert stat.S_IMODE(metadata.st_mode) == 0o600
        assert metadata.st_uid == os.getuid()
        assert metadata.st_nlink == 1
        assert metadata.st_size == size
    wrong = uuid.uuid4().hex
    attacks = (
        (
            lambda path: create_keyfile(str(path)),
            Path('/private/tmp') / ('anhuan-f0i-' + token + '-outside.key'),
            F0IError,
            'KEYFILE_INVALID',
        ),
        (
            lambda path: create_keyfile(str(path)),
            isolation.tmp_dir / ('anhuan-f0i-' + wrong + '-probe.key'),
            F0IError,
            'KEYFILE_INVALID',
        ),
        (
            lambda path: HostReplayLock(str(path)),
            Path('/private/tmp') / ('anhuan-f0i-' + token + '-outside.lock'),
            F0IError,
            'LOCK_INVALID',
        ),
        (
            lambda path: HostReplayLock(str(path)),
            isolation.tmp_dir / ('anhuan-f0i-' + wrong + '-probe.lock'),
            F0IError,
            'LOCK_INVALID',
        ),
        (
            lambda path: create_token_bundle(str(path)),
            Path('/private/tmp') / ('anhuan-f0g-' + token + '-outside.tokens'),
            F0GError,
            'ANNOTATION_PREPARE_FAILED',
        ),
        (
            lambda path: create_token_bundle(str(path)),
            isolation.tmp_dir / ('anhuan-f0g-' + wrong + '-probe.tokens'),
            F0GError,
            'ANNOTATION_PREPARE_FAILED',
        ),
    )
    for call, path, error_type, reason in attacks:
        try:
            call(path)
        except error_type as error:
            assert error.code == reason
        else:
            raise AssertionError('PRIVATE_CAPABILITY_BOUNDARY_BYPASS')
        assert not os.path.lexists(path)
finally:
    for path in reversed(created):
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
"""
            self.assertEqual(
                workspace.run_python(code),
                0,
                "PRIVATE_CAPABILITY_ISOLATION_REGRESSION",
            )
        finally:
            workspace.close()

    def test_private_capabilities_keep_legacy_direct_tmp_defaults_without_config(self) -> None:
        workspace = _IsolationWorkspace()
        try:
            code = """
import contextlib
import os
import uuid
from platform_foundation.f0g.tokens import (
    ACCEPTANCE_TOKEN_BUNDLE,
    create_token_bundle,
    load_token_bundle,
)
from platform_foundation.f0i.keyfile import (
    ACCEPTANCE_KEY_FILE,
    create_keyfile,
    load_keyfile,
)
from platform_foundation.f0i.locking import DEFAULT_HOST_LOCK_PATH, HostReplayLock
assert ACCEPTANCE_KEY_FILE == '/private/tmp/anhuan-f0i-acceptance-v01.key'
assert ACCEPTANCE_TOKEN_BUNDLE == '/private/tmp/anhuan-f0g-acceptance-v01.tokens'
assert DEFAULT_HOST_LOCK_PATH == '/private/tmp/anhuan-f0i-replay.lock'
token = uuid.uuid4().hex
key_path = '/private/tmp/anhuan-f0i-' + token + '.key'
lock_path = '/private/tmp/anhuan-f0i-' + token + '.lock'
bundle_path = '/private/tmp/anhuan-f0g-' + token + '.tokens'
try:
    create_keyfile(key_path)
    with load_keyfile(key_path) as key:
        assert len(key.view()) == 32
    with HostReplayLock(lock_path) as lock:
        assert lock.held
    create_token_bundle(bundle_path)
    with load_token_bundle(bundle_path) as bundle:
        assert bundle.token('ADJUDICATOR').startswith('f0g_')
finally:
    for path in (bundle_path, lock_path, key_path):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
"""
            self.assertEqual(
                workspace.run_python(code, isolated=False),
                0,
                "PRIVATE_CAPABILITY_DEFAULT_REGRESSION",
            )
        finally:
            workspace.close()

    def test_stage_database_wrappers_reject_cross_stage_targets_before_connect(self) -> None:
        workspace = _IsolationWorkspace()
        try:
            code = """
from unittest import mock
from platform_foundation.f0_isolation import load_frozen_f0_isolation
from platform_foundation.f0g.config import validate_local_database_config as validate_f0g
from platform_foundation.f0i.bootstrap import ensure_database
from platform_foundation.f0i.config import database_config as f0i_config
from platform_foundation.f0i.config import validate_local_database_config as validate_f0i
from platform_foundation.f0i.contracts import F0IError
from tests.test_f0f_controlled_body_gold import _database_config as f0f_config
from tests.test_platform_foundation import _database_config as f0d_config
isolation = load_frozen_f0_isolation()
assert isolation is not None
f0g_database = isolation.database_name('f0g-case')
f0i_database = isolation.database_name('f0i-migration')
f0f_database = isolation.database_name('f0f-test')
try:
    validate_f0g(isolation.database_config(f0i_database))
except RuntimeError as error:
    assert str(error) == 'DATABASE_CONFIGURATION_INVALID'
else:
    raise AssertionError('F0G_CROSS_STAGE_DATABASE_ACCEPTED')
for call in (
    lambda: validate_f0i(isolation.database_config(f0g_database)),
    lambda: f0i_config(f0g_database),
):
    try:
        call()
    except F0IError as error:
        assert error.code == 'DATABASE_CONFIGURATION_INVALID'
    else:
        raise AssertionError('F0I_CROSS_STAGE_DATABASE_ACCEPTED')
with mock.patch(
    'platform_foundation.f0i.bootstrap.psycopg.connect',
    side_effect=AssertionError('DATABASE_IO_ATTEMPTED'),
) as connect:
    try:
        ensure_database(f0g_database)
    except F0IError as error:
        assert error.code == 'DATABASE_CONFIGURATION_INVALID'
    else:
        raise AssertionError('F0I_CROSS_STAGE_DATABASE_ACCEPTED')
    connect.assert_not_called()
for call in (
    lambda: f0f_config(f0i_database),
    lambda: f0d_config(f0f_database),
):
    try:
        call()
    except AssertionError as error:
        assert 'unsafe isolated' in str(error)
    else:
        raise AssertionError('TEST_STAGE_DATABASE_ACCEPTED')
"""
            self.assertEqual(
                workspace.run_python(code),
                0,
                "CROSS_STAGE_DATABASE_BOUNDARY_REGRESSION",
            )
        finally:
            workspace.close()


if __name__ == "__main__":
    unittest.main()
