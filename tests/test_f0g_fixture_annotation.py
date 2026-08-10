from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import contextlib
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import stat
import unittest
import uuid

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import psycopg
from psycopg import sql

from platform_foundation.auth import authenticate_local_session
from platform_foundation.bootstrap import LOCAL_TENANT_A_TOKEN, LOCAL_TENANT_B_TOKEN
from platform_foundation.database import (
    DatabaseConfig,
    DatabaseError,
    role_transaction,
    tenant_transaction,
)
from platform_foundation.f0_isolation import load_frozen_f0_isolation


_FROZEN_F0_ISOLATION = load_frozen_f0_isolation()
BOOTSTRAP_DSN = (
    _FROZEN_F0_ISOLATION.dsn_for("f0d_bootstrap", "postgres")
    if _FROZEN_F0_ISOLATION is not None
    else "postgresql://f0d_bootstrap:f0d-bootstrap-local-v01@127.0.0.1:55432/postgres"
)
SOURCE_DATABASE = (
    _FROZEN_F0_ISOLATION.f0g_template_database
    if _FROZEN_F0_ISOLATION is not None
    else "f0f_acceptance_v01"
)
KEY_PATH = (
    str(_FROZEN_F0_ISOLATION.f0f_key_file)
    if _FROZEN_F0_ISOLATION is not None
    else "/private/tmp/anhuan-f0f-acceptance-v01.key"
)


def _token_path(label: str = "test") -> str:
    token = uuid.uuid4().hex
    if _FROZEN_F0_ISOLATION is None:
        return f"/private/tmp/anhuan-f0g-{label}-{token}.tokens"
    return str(
        _FROZEN_F0_ISOLATION.tmp_dir
        / (
            f"anhuan-f0g-{_FROZEN_F0_ISOLATION.project_id.hex}-"
            f"{label}-{token}.tokens"
        )
    )


def _legacy_config(database_name: str) -> DatabaseConfig:
    base = "127.0.0.1:55432/" + database_name
    return DatabaseConfig(
        migration_dsn="postgresql://f0d_migration:f0d-migration-local-v01@" + base,
        runtime_dsn="postgresql://f0d_runtime:f0d-runtime-local-v01@" + base,
        worker_dsn="postgresql://f0d_worker:f0d-worker-local-v01@" + base,
    )


def _config(database_name: str) -> DatabaseConfig:
    if _FROZEN_F0_ISOLATION is not None:
        allowed = {
            _FROZEN_F0_ISOLATION.f0g_template_database,
            _FROZEN_F0_ISOLATION.database_name("f0g-base"),
            _FROZEN_F0_ISOLATION.database_name("f0g-case"),
        }
        if database_name in allowed:
            return _FROZEN_F0_ISOLATION.database_config(database_name)
        if database_name.startswith("f0g_test_"):
            return _FROZEN_F0_ISOLATION.database_config(
                _FROZEN_F0_ISOLATION.database_name("f0g-case")
            )
    return _legacy_config(database_name)


def _create_database(database_name: str, template: str) -> None:
    with psycopg.connect(BOOTSTRAP_DSN, autocommit=True) as connection:
        connection.execute(
            sql.SQL("CREATE DATABASE {} OWNER f0d_migration TEMPLATE {}").format(
                sql.Identifier(database_name), sql.Identifier(template)
            )
        )


def _drop_database(database_name: str) -> None:
    if _FROZEN_F0_ISOLATION is not None:
        allowed = {
            _FROZEN_F0_ISOLATION.database_name("f0g-base"),
            _FROZEN_F0_ISOLATION.database_name("f0g-case"),
        }
        safe = database_name in allowed
    else:
        safe = database_name.startswith("f0g_test_")
    if not safe:
        raise AssertionError("unsafe database name")
    with psycopg.connect(BOOTSTRAP_DSN, autocommit=True) as connection:
        connection.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(database_name)
            )
        )


@contextlib.contextmanager
def _wrong_key_path():
    from platform_foundation.f0f import create_keyfile

    if _FROZEN_F0_ISOLATION is None:
        path = "/private/tmp/anhuan-f0f-f0g-wrong-" + uuid.uuid4().hex + ".key"
        create_keyfile(path)
        try:
            yield path
        finally:
            os.unlink(path)
        return
    path = str(_FROZEN_F0_ISOLATION.f0f_key_file)
    original = bytearray(Path(path).read_bytes())
    try:
        os.unlink(path)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            material = os.urandom(32)
            if os.write(descriptor, material) != len(material):
                raise AssertionError("short test key write")
        finally:
            os.close(descriptor)
        yield path
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            if os.write(descriptor, original) != len(original):
                raise AssertionError("short test key restore")
        finally:
            os.close(descriptor)
            original[:] = b"\0" * len(original)
            original.clear()


def _upgrade(config: DatabaseConfig) -> None:
    previous = os.environ.get("F0D_MIGRATION_DSN")
    os.environ["F0D_MIGRATION_DSN"] = config.migration_dsn.replace(
        "postgresql://", "postgresql+psycopg://", 1
    )
    try:
        command.upgrade(Config("alembic.ini"), "f0d_0005")
    finally:
        if previous is None:
            os.environ.pop("F0D_MIGRATION_DSN", None)
        else:
            os.environ["F0D_MIGRATION_DSN"] = previous


class F0GContractTests(unittest.TestCase):
    def test_public_contract_exports_workflow_surface(self) -> None:
        import platform_foundation.f0g as f0g

        self.assertGreaterEqual(len(f0g.__all__), 22)
        self.assertIn("AnnotationService", f0g.__all__)
        self.assertIn("prepare_workflow", f0g.__all__)

    def test_local_api_constants_pin_ipv4_loopback_and_port(self) -> None:
        from platform_foundation.f0g import LOCAL_API_HOST, LOCAL_API_PORT

        self.assertEqual((LOCAL_API_HOST, LOCAL_API_PORT), ("127.0.0.1", 8767))

    def test_adjudication_request_limit_is_exactly_4096_bytes(self) -> None:
        from platform_foundation.f0g import MAX_ADJUDICATION_REQUEST_BYTES

        self.assertEqual(MAX_ADJUDICATION_REQUEST_BYTES, 4096)

    def test_serve_cli_rejects_host_override(self) -> None:
        from platform_foundation.f0g.__main__ import main

        error = io.StringIO()
        with contextlib.redirect_stderr(error), self.assertRaises(SystemExit) as raised:
            main(["serve", "--host", "0.0.0.0"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("unrecognized arguments", error.getvalue())

    def test_serve_cli_rejects_port_override(self) -> None:
        from platform_foundation.f0g.__main__ import main

        error = io.StringIO()
        with contextlib.redirect_stderr(error), self.assertRaises(SystemExit) as raised:
            main(["serve", "--port", "9000"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("unrecognized arguments", error.getvalue())

    def test_sbom_component_inventory_is_installed_and_license_pinned(self) -> None:
        from platform_foundation.f0g.artifacts import _installed_components

        components = {
            item["name"]: (item["version"], item["license"])
            for item in _installed_components()
        }
        self.assertEqual(
            components,
            {
                "AnyIO": ("4.14.2", "MIT"),
                "FastAPI": ("0.133.1", "MIT"),
                "h11": ("0.16.0", "MIT"),
                "Pydantic": ("2.13.4", "MIT"),
                "Starlette": ("1.3.1", "BSD-3-Clause"),
                "Uvicorn": ("0.41.0", "BSD-3-Clause"),
                "psycopg": (
                    "3.2.9",
                    "GNU Lesser General Public License v3 (LGPLv3)",
                ),
                "psycopg-binary": (
                    "3.2.9",
                    "GNU Lesser General Public License v3 (LGPLv3)",
                ),
            },
        )

    def test_artifact_json_serializer_is_deterministic_ascii(self) -> None:
        from platform_foundation.f0g.artifacts import _json_bytes

        value = {
            "verification_scope": {
                "listener": "SEPARATE_GATE_NOT_BOUND",
                "reverse": "SEPARATE_GATE_NOT_BOUND",
            },
            "status": "未标注",
        }
        first = _json_bytes(value)
        second = _json_bytes(value)
        self.assertEqual(first, second)
        self.assertTrue(first.isascii())
        self.assertEqual(json.loads(first), value)

    def test_status_html_is_self_contained_and_not_gold(self) -> None:
        from platform_foundation.f0g.artifacts import _status_html

        payload = _status_html(
            {
                "counts": {
                    "blind_assignments": 2,
                    "independent_label_slots": 4,
                    "human_labels": 0,
                    "adjudications": 0,
                }
            }
        )
        lowered = payload.lower()
        self.assertIn(b"not gold", lowered)
        self.assertIn(b"not production", lowered)
        self.assertNotIn(b"http://", lowered)
        self.assertNotIn(b"https://", lowered)
        self.assertNotIn(b"<script", lowered)

    def test_unknown_error_is_redacted(self) -> None:
        from platform_foundation.f0g import F0GError

        error = F0GError("SYNTHETIC_SECRET_VALUE")
        self.assertEqual(str(error), "ANNOTATION_CONTRACT_INVALID")
        self.assertNotIn("SECRET", repr(error))

    def test_known_error_has_fixed_dictionary(self) -> None:
        from platform_foundation.f0g import F0GError

        self.assertEqual(
            F0GError("ANNOTATION_ASSIGNMENT_DENIED").to_dict(),
            {"error": "F0G_ERROR", "reason_code": "ANNOTATION_ASSIGNMENT_DENIED"},
        )

    def test_sensitive_bytes_repr_redacts_body(self) -> None:
        from platform_foundation.f0g import SensitiveBytes

        owner = SensitiveBytes(b"SYNTHETIC_BODY_CANARY", maximum=64)
        try:
            self.assertNotIn("CANARY", repr(owner))
        finally:
            owner.wipe()

    def test_sensitive_bytes_hash_is_exact(self) -> None:
        from platform_foundation.f0g import SensitiveBytes

        value = b"SYNTHETIC_BODY_HASH"
        owner = SensitiveBytes(value, maximum=64)
        try:
            self.assertEqual(owner.sha256, hashlib.sha256(value).hexdigest())
        finally:
            owner.wipe()

    def test_sensitive_bytes_view_is_read_only(self) -> None:
        from platform_foundation.f0g import SensitiveBytes

        owner = SensitiveBytes(b"SYNTHETIC_BODY_VIEW", maximum=64)
        try:
            self.assertTrue(owner.view().readonly)
        finally:
            owner.wipe()

    def test_sensitive_bytes_context_wipes(self) -> None:
        from platform_foundation.f0g import F0GError, SensitiveBytes

        with SensitiveBytes(b"SYNTHETIC_BODY_CONTEXT", maximum=64) as owner:
            self.assertGreater(owner.byte_count, 0)
        with self.assertRaisesRegex(F0GError, "ANNOTATION_BODY_INVALID"):
            owner.view()

    def test_sensitive_bytes_rejects_limit(self) -> None:
        from platform_foundation.f0g import F0GError, SensitiveBytes

        with self.assertRaisesRegex(F0GError, "ANNOTATION_BODY_INVALID"):
            SensitiveBytes(b"12345", maximum=4)

    def test_canonical_label_accepts_utf8_nfc_lf(self) -> None:
        from platform_foundation.f0g import CanonicalLabel

        label = CanonicalLabel("合格\nSYNTHETIC".encode())
        try:
            self.assertEqual(label.byte_count, len("合格\nSYNTHETIC".encode()))
        finally:
            label.wipe()

    def test_canonical_label_accepts_empty_value(self) -> None:
        from platform_foundation.f0g import CanonicalLabel

        label = CanonicalLabel(b"")
        try:
            self.assertEqual(label.sha256, hashlib.sha256(b"").hexdigest())
        finally:
            label.wipe()

    def test_canonical_label_rejects_invalid_utf8(self) -> None:
        from platform_foundation.f0g import CanonicalLabel, F0GError

        with self.assertRaisesRegex(F0GError, "ANNOTATION_LABEL_INVALID"):
            CanonicalLabel(b"\xff")

    def test_canonical_label_rejects_crlf(self) -> None:
        from platform_foundation.f0g import CanonicalLabel, F0GError

        with self.assertRaisesRegex(F0GError, "ANNOTATION_LABEL_INVALID"):
            CanonicalLabel(b"A\r\nB")

    def test_canonical_label_rejects_non_nfc(self) -> None:
        from platform_foundation.f0g import CanonicalLabel, F0GError

        with self.assertRaisesRegex(F0GError, "ANNOTATION_LABEL_INVALID"):
            CanonicalLabel("e\u0301".encode())

    def test_fixture_actor_session_redacts_token(self) -> None:
        from platform_foundation.f0g import FixtureActorSession

        token = "x" * 40
        session = FixtureActorSession("ANNOTATOR_ONE", uuid.uuid4(), uuid.uuid4(), token)
        self.assertNotIn(token, repr(session))

    def test_fixture_actor_session_rejects_unknown_role(self) -> None:
        from platform_foundation.f0g import F0GError, FixtureActorSession

        with self.assertRaisesRegex(F0GError, "ANNOTATION_CONTRACT_INVALID"):
            FixtureActorSession("OWNER", uuid.uuid4(), uuid.uuid4(), "x" * 40)

    def test_assignment_annotator_one_requires_slot_one(self) -> None:
        from platform_foundation.f0g import AssignmentMetadata, F0GError

        with self.assertRaisesRegex(F0GError, "ANNOTATION_CONTRACT_INVALID"):
            AssignmentMetadata(uuid.uuid4(), uuid.uuid4(), "ANNOTATOR_ONE", 2, 1, "v", "1" * 64, "LABEL_REQUIRED", False, None, False)

    def test_assignment_annotator_two_requires_slot_two(self) -> None:
        from platform_foundation.f0g import AssignmentMetadata, F0GError

        with self.assertRaisesRegex(F0GError, "ANNOTATION_CONTRACT_INVALID"):
            AssignmentMetadata(uuid.uuid4(), uuid.uuid4(), "ANNOTATOR_TWO", 1, 1, "v", "1" * 64, "LABEL_REQUIRED", False, None, False)

    def test_assignment_adjudicator_requires_no_slot(self) -> None:
        from platform_foundation.f0g import AssignmentMetadata, F0GError

        with self.assertRaisesRegex(F0GError, "ANNOTATION_CONTRACT_INVALID"):
            AssignmentMetadata(uuid.uuid4(), uuid.uuid4(), "ADJUDICATOR", 1, 1, "v", "1" * 64, "LABELS_PENDING", False, 0, False)

    def test_assignment_rejects_nonpositive_ordinal(self) -> None:
        from platform_foundation.f0g import AssignmentMetadata, F0GError

        with self.assertRaisesRegex(F0GError, "ANNOTATION_CONTRACT_INVALID"):
            AssignmentMetadata(uuid.uuid4(), uuid.uuid4(), "ANNOTATOR_ONE", 1, 0, "v", "1" * 64, "LABEL_REQUIRED", False, None, False)

    def test_assignment_rejects_bad_guideline_hash(self) -> None:
        from platform_foundation.f0g import AssignmentMetadata, F0GError

        with self.assertRaisesRegex(F0GError, "ANNOTATION_CONTRACT_INVALID"):
            AssignmentMetadata(uuid.uuid4(), uuid.uuid4(), "ANNOTATOR_ONE", 1, 1, "v", "bad", "LABEL_REQUIRED", False, None, False)

    def test_assignment_adjudication_ready_is_derived(self) -> None:
        from platform_foundation.f0g import AssignmentMetadata

        item = AssignmentMetadata(uuid.uuid4(), uuid.uuid4(), "ADJUDICATOR", None, 1, "v", "1" * 64, "ADJUDICATION_READY", False, 2, False)
        self.assertTrue(item.adjudication_ready)

    def test_assignment_pending_is_not_adjudication_ready(self) -> None:
        from platform_foundation.f0g import AssignmentMetadata

        item = AssignmentMetadata(uuid.uuid4(), uuid.uuid4(), "ADJUDICATOR", None, 1, "v", "1" * 64, "LABELS_PENDING", False, 1, False)
        self.assertFalse(item.adjudication_ready)

    def test_label_metadata_accepts_slot_one(self) -> None:
        from platform_foundation.f0g import LabelMetadata

        item = LabelMetadata(uuid.uuid4(), 1, "1" * 64, 0)
        self.assertEqual(item.to_dict()["label_ordinal"], 1)

    def test_label_metadata_rejects_slot_three(self) -> None:
        from platform_foundation.f0g import F0GError, LabelMetadata

        with self.assertRaisesRegex(F0GError, "ANNOTATION_CONTRACT_INVALID"):
            LabelMetadata(uuid.uuid4(), 3, "1" * 64, 1)

    def test_guideline_contract_denies_gold_and_external_processing(self) -> None:
        from platform_foundation.f0g import GUIDELINE_SPEC

        self.assertEqual(GUIDELINE_SPEC["benchmark_tier"], "NONE")
        self.assertFalse(GUIDELINE_SPEC["acceptance_gold"])
        self.assertEqual(GUIDELINE_SPEC["external_processing"], "DENY")

    def test_local_database_validator_accepts_exact_test_target(self) -> None:
        from platform_foundation.f0g import validate_local_database_config

        config = _config("f0g_test_" + "a" * 16)
        self.assertIs(validate_local_database_config(config), config)

    def test_service_rejects_remote_database_before_connect(self) -> None:
        from platform_foundation.f0g import AnnotationService, F0GError

        config = _config("f0g_test_" + "a" * 16)
        remote = DatabaseConfig(
            migration_dsn=config.migration_dsn.replace("127.0.0.1", "203.0.113.10"),
            runtime_dsn=config.runtime_dsn.replace("127.0.0.1", "203.0.113.10"),
            worker_dsn=config.worker_dsn.replace("127.0.0.1", "203.0.113.10"),
        )
        with self.assertRaisesRegex(F0GError, "ANNOTATION_CONTRACT_INVALID"):
            AnnotationService(remote, KEY_PATH)

    def test_prepare_rejects_remote_database_without_creating_token(self) -> None:
        from platform_foundation.auth import SessionContext
        from platform_foundation.f0g import F0GError, prepare_workflow

        config = _config("f0g_test_" + "b" * 16)
        remote = DatabaseConfig(
            migration_dsn=config.migration_dsn.replace("127.0.0.1", "203.0.113.11"),
            runtime_dsn=config.runtime_dsn.replace("127.0.0.1", "203.0.113.11"),
            worker_dsn=config.worker_dsn.replace("127.0.0.1", "203.0.113.11"),
        )
        context = SessionContext(uuid.uuid4(), uuid.uuid4(), "0" * 64)
        path = _token_path()
        with self.assertRaisesRegex(F0GError, "ANNOTATION_PREPARE_FAILED"):
            prepare_workflow(remote, context, path)
        self.assertFalse(os.path.lexists(path))

    def test_acceptance_rejects_remote_database_before_connect(self) -> None:
        from platform_foundation.auth import SessionContext
        from platform_foundation.f0g.acceptance import acceptance_snapshot
        from platform_foundation.f0g import F0GError

        config = _config("f0g_test_" + "c" * 16)
        remote = DatabaseConfig(
            migration_dsn=config.migration_dsn.replace("127.0.0.1", "203.0.113.12"),
            runtime_dsn=config.runtime_dsn.replace("127.0.0.1", "203.0.113.12"),
            worker_dsn=config.worker_dsn.replace("127.0.0.1", "203.0.113.12"),
        )
        context = SessionContext(uuid.uuid4(), uuid.uuid4(), "0" * 64)
        with self.assertRaisesRegex(F0GError, "ANNOTATION_ACCEPTANCE_MISMATCH"):
            acceptance_snapshot(remote, context)

    def test_local_database_validator_rejects_query_options(self) -> None:
        from platform_foundation.f0g import validate_local_database_config

        config = _config("f0g_test_" + "d" * 16)
        invalid = DatabaseConfig(
            migration_dsn=config.migration_dsn + "?hostaddr=127.0.0.1",
            runtime_dsn=config.runtime_dsn,
            worker_dsn=config.worker_dsn,
        )
        with self.assertRaisesRegex(RuntimeError, "DATABASE_CONFIGURATION_INVALID"):
            validate_local_database_config(invalid)

    def test_local_database_validator_rejects_source_database(self) -> None:
        from platform_foundation.f0g import validate_local_database_config

        source = (
            _legacy_config("f0f_acceptance_v01")
            if _FROZEN_F0_ISOLATION is not None
            else _config(SOURCE_DATABASE)
        )
        with self.assertRaisesRegex(RuntimeError, "DATABASE_CONFIGURATION_INVALID"):
            validate_local_database_config(source)

    def test_local_database_validator_rejects_mixed_targets(self) -> None:
        from platform_foundation.f0g import validate_local_database_config

        first = _config("f0g_test_" + "e" * 16)
        second = (
            _FROZEN_F0_ISOLATION.database_name("f0g-base")
            if _FROZEN_F0_ISOLATION is not None
            else "f0g_test_" + "f" * 16
        )
        invalid = DatabaseConfig(
            migration_dsn=first.migration_dsn,
            runtime_dsn=first.runtime_dsn.rsplit("/", 1)[0] + "/" + second,
            worker_dsn=first.worker_dsn,
        )
        with self.assertRaisesRegex(RuntimeError, "DATABASE_CONFIGURATION_INVALID"):
            validate_local_database_config(invalid)


class F0GTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = _token_path()
        self.created: list[str] = []

    def tearDown(self) -> None:
        for path in reversed(self.created):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(path)

    def _create(self, path: str | None = None) -> str:
        from platform_foundation.f0g import create_token_bundle

        target = self.path if path is None else path
        self.created.append(target)
        create_token_bundle(target)
        return target

    def test_create_bundle_has_strict_mode_size_and_link_count(self) -> None:
        self._create()
        metadata = os.lstat(self.path)
        self.assertEqual((stat.S_IMODE(metadata.st_mode), metadata.st_size, metadata.st_nlink), (0o600, 96, 1))

    def test_bundle_contains_three_distinct_tokens(self) -> None:
        from platform_foundation.f0g import load_token_bundle

        self._create()
        with load_token_bundle(self.path) as bundle:
            tokens = {bundle.token(role) for role in ("ANNOTATOR_ONE", "ANNOTATOR_TWO", "ADJUDICATOR")}
        self.assertEqual(len(tokens), 3)

    def test_bundle_repr_redacts_token_material(self) -> None:
        from platform_foundation.f0g import load_token_bundle

        self._create()
        with load_token_bundle(self.path) as bundle:
            token = bundle.token("ANNOTATOR_ONE")
            self.assertNotIn(token, repr(bundle))

    def test_bundle_wipe_blocks_future_token_access(self) -> None:
        from platform_foundation.f0g import F0GError, load_token_bundle

        self._create()
        with load_token_bundle(self.path) as bundle:
            self.assertTrue(bundle.token("ADJUDICATOR").startswith("f0g_"))
        with self.assertRaisesRegex(F0GError, "ANNOTATION_PREPARE_FAILED"):
            bundle.token("ADJUDICATOR")

    def test_create_bundle_never_overwrites(self) -> None:
        from platform_foundation.f0g import F0GError, create_token_bundle

        self._create()
        with self.assertRaisesRegex(F0GError, "ANNOTATION_STATE_INVALID"):
            create_token_bundle(self.path)

    def test_bundle_rejects_relative_path(self) -> None:
        from platform_foundation.f0g import F0GError, create_token_bundle

        with self.assertRaisesRegex(F0GError, "ANNOTATION_PREPARE_FAILED"):
            create_token_bundle("anhuan-f0g-relative.tokens")

    def test_bundle_rejects_wrong_prefix(self) -> None:
        from platform_foundation.f0g import F0GError, create_token_bundle

        with self.assertRaisesRegex(F0GError, "ANNOTATION_PREPARE_FAILED"):
            create_token_bundle("/private/tmp/wrong.tokens")

    def test_bundle_rejects_wrong_suffix(self) -> None:
        from platform_foundation.f0g import F0GError, create_token_bundle

        with self.assertRaisesRegex(F0GError, "ANNOTATION_PREPARE_FAILED"):
            create_token_bundle("/private/tmp/anhuan-f0g-wrong.key")

    def test_bundle_rejects_noncanonical_path(self) -> None:
        from platform_foundation.f0g import F0GError, create_token_bundle

        with self.assertRaisesRegex(F0GError, "ANNOTATION_PREPARE_FAILED"):
            create_token_bundle("/private/tmp/../tmp/anhuan-f0g-wrong.tokens")

    def test_bundle_rejects_uppercase_name(self) -> None:
        from platform_foundation.f0g import F0GError, create_token_bundle

        with self.assertRaisesRegex(F0GError, "ANNOTATION_PREPARE_FAILED"):
            create_token_bundle("/private/tmp/anhuan-f0g-WRONG.tokens")

    def test_bundle_load_rejects_world_readable_mode(self) -> None:
        from platform_foundation.f0g import F0GError, load_token_bundle

        self._create()
        os.chmod(self.path, 0o644)
        with self.assertRaisesRegex(F0GError, "ANNOTATION_PREPARE_FAILED"):
            load_token_bundle(self.path)

    def test_bundle_load_rejects_hardlink(self) -> None:
        from platform_foundation.f0g import F0GError, load_token_bundle

        self._create()
        link = self.path.removesuffix(".tokens") + "-link.tokens"
        os.link(self.path, link)
        self.created.append(link)
        with self.assertRaisesRegex(F0GError, "ANNOTATION_PREPARE_FAILED"):
            load_token_bundle(self.path)

    def test_bundle_load_rejects_symlink(self) -> None:
        from platform_foundation.f0g import F0GError, load_token_bundle

        real = self.path.removesuffix(".tokens") + "-real.tokens"
        link = self.path.removesuffix(".tokens") + "-symlink.tokens"
        self._create(real)
        os.symlink(real, link)
        self.created.append(link)
        with self.assertRaisesRegex(F0GError, "ANNOTATION_PREPARE_FAILED"):
            load_token_bundle(link)

    def test_bundle_load_rejects_fifo_without_blocking(self) -> None:
        from platform_foundation.f0g import F0GError, load_token_bundle

        os.mkfifo(self.path, 0o600)
        self.created.append(self.path)
        with self.assertRaisesRegex(F0GError, "ANNOTATION_PREPARE_FAILED"):
            load_token_bundle(self.path)

    def test_bundle_load_rejects_wrong_size(self) -> None:
        from platform_foundation.f0g import F0GError, load_token_bundle

        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.created.append(self.path)
        try:
            os.write(descriptor, b"X" * 95)
        finally:
            os.close(descriptor)
        with self.assertRaisesRegex(F0GError, "ANNOTATION_PREPARE_FAILED"):
            load_token_bundle(self.path)


class F0GDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from platform_foundation.f0g import (
            AnnotationService,
            load_fixture_actor_sessions,
            prepare_workflow,
        )

        cls.database_name = (
            _FROZEN_F0_ISOLATION.database_name("f0g-base")
            if _FROZEN_F0_ISOLATION is not None
            else "f0g_test_base_" + uuid.uuid4().hex[:16]
        )
        cls.token_path = _token_path()
        _create_database(cls.database_name, SOURCE_DATABASE)
        try:
            cls.config = _config(cls.database_name)
            _upgrade(cls.config)
            cls.operator = authenticate_local_session(cls.config, LOCAL_TENANT_A_TOKEN)
            cls.first_prepare = prepare_workflow(cls.config, cls.operator, cls.token_path)
            cls.second_prepare = prepare_workflow(cls.config, cls.operator, cls.token_path)
            cls.sessions = load_fixture_actor_sessions(cls.operator.enterprise_id, cls.token_path)
            cls.session_by_role = {item.role: item for item in cls.sessions}
            cls.context_by_role = {
                role: authenticate_local_session(cls.config, session.token)
                for role, session in cls.session_by_role.items()
            }
            cls.service = AnnotationService(cls.config, KEY_PATH)
            cls.assignment = cls.service.list_assignments(cls.context_by_role["ANNOTATOR_ONE"])[0]
        except Exception:
            _drop_database(cls.database_name)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(cls.token_path)
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            _drop_database(cls.database_name)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(cls.token_path)

    def _scratch(self) -> tuple[str, DatabaseConfig, object, dict[str, object]]:
        from platform_foundation.f0g import AnnotationService

        name = (
            _FROZEN_F0_ISOLATION.database_name("f0g-case")
            if _FROZEN_F0_ISOLATION is not None
            else "f0g_test_case_" + uuid.uuid4().hex[:16]
        )
        _create_database(name, self.database_name)
        config = _config(name)
        contexts = {
            role: authenticate_local_session(config, session.token)
            for role, session in self.session_by_role.items()
        }
        return name, config, AnnotationService(config, KEY_PATH), contexts

    def _scoped_connection(self, config: DatabaseConfig):
        connection = psycopg.connect(config.migration_dsn)
        connection.execute(
            "SELECT set_config('f0d.enterprise_id',%s,true),"
            "set_config('f0d.actor_id',%s,true),"
            "set_config('f0d.session_token_sha256',%s,true)",
            (
                str(self.operator.enterprise_id),
                str(self.operator.actor_id),
                self.operator.session_token_sha256,
            ),
        )
        return connection

    def _submit_pair(self, service: object, contexts: dict[str, object], assignment_id: uuid.UUID, *, same: bool = False) -> tuple[uuid.UUID, uuid.UUID]:
        from platform_foundation.f0g import CanonicalLabel

        one = CanonicalLabel(b"SYNTHETIC_LABEL_ONE")
        two = CanonicalLabel(b"SYNTHETIC_LABEL_ONE" if same else b"SYNTHETIC_LABEL_TWO")
        try:
            label_two = service.submit_label(contexts["ANNOTATOR_TWO"], assignment_id, two)
            label_one = service.submit_label(contexts["ANNOTATOR_ONE"], assignment_id, one)
            return label_one, label_two
        finally:
            one.wipe()
            two.wipe()

    def _label_and_audit_counts(self, config: DatabaseConfig) -> tuple[int, int, int]:
        with self._scoped_connection(config) as connection:
            row = connection.execute(
                "SELECT (SELECT count(*) FROM f0f.gold_label_evidence),"
                "(SELECT count(*) FROM f0f.gold_adjudication),"
                "(SELECT count(*) FROM f0d.audit_event WHERE event_code IN "
                "('F0G_ASSIGNED_BODY_READ','F0G_BLIND_LABEL_RECORDED',"
                "'F0G_LABEL_PAIR_READ','F0G_ASSIGNMENT_ADJUDICATED'))"
            ).fetchone()
        return int(row[0]), int(row[1]), int(row[2])

    def _prepare_arguments(self, config: DatabaseConfig) -> list[object]:
        with self._scoped_connection(config) as connection:
            guideline = connection.execute(
                "SELECT id,guideline_version,guideline_sha256 FROM f0g.annotation_guideline"
            ).fetchone()
            rows = connection.execute(
                "SELECT id,annotation_queue_id,annotator_one_actor_id,"
                "annotator_two_actor_id,adjudicator_actor_id FROM f0g.blind_assignment "
                "ORDER BY annotation_queue_id"
            ).fetchall()
        return [
            guideline[0], guideline[1], guideline[2],
            [row[0] for row in rows], [row[1] for row in rows],
            rows[0][2], rows[0][3], rows[0][4], uuid.uuid4(),
        ]

    def _invalid_prepare(self, config: DatabaseConfig, arguments: list[object]) -> None:
        with psycopg.connect(config.migration_dsn) as connection:
            connection.execute(
                "SELECT set_config('f0d.enterprise_id',%s,true),"
                "set_config('f0d.actor_id',%s,true),"
                "set_config('f0d.session_token_sha256',%s,true)",
                (str(self.operator.enterprise_id), str(self.operator.actor_id), self.operator.session_token_sha256),
            )
            connection.execute(
                "SELECT * FROM f0g.prepare_annotation_workflow(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                arguments,
            ).fetchone()

    def test_migration_revision_is_explicit_0005(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            value = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        self.assertEqual(value, "f0d_0005")

    def test_prepare_first_creates_one_guideline(self) -> None:
        self.assertEqual((self.first_prepare.guidelines, self.first_prepare.guideline_delta), (1, 1))

    def test_prepare_first_covers_entire_queue(self) -> None:
        self.assertEqual(self.first_prepare.assignments, 15)
        self.assertEqual(self.first_prepare.assignment_delta, 15)

    def test_prepare_second_is_zero_delta(self) -> None:
        self.assertEqual((self.second_prepare.guideline_delta, self.second_prepare.assignment_delta), (0, 0))

    def test_prepare_rejects_subset_queue(self) -> None:
        name, config, _service, _contexts = self._scratch()
        try:
            arguments = self._prepare_arguments(config)
            arguments[3] = arguments[3][:1]
            arguments[4] = arguments[4][:1]
            with self.assertRaises(psycopg.Error):
                self._invalid_prepare(config, arguments)
            self.assertEqual(self._label_and_audit_counts(config)[:2], (0, 0))
        finally:
            _drop_database(name)

    def test_prepare_rejects_duplicate_queue_identity(self) -> None:
        name, config, _service, _contexts = self._scratch()
        try:
            arguments = self._prepare_arguments(config)
            arguments[4] = [arguments[4][0], *arguments[4][:-1]]
            with self.assertRaises(psycopg.Error):
                self._invalid_prepare(config, arguments)
            self.assertEqual(self._label_and_audit_counts(config)[:2], (0, 0))
        finally:
            _drop_database(name)

    def test_prepare_rejects_wrong_guideline_version(self) -> None:
        name, config, _service, _contexts = self._scratch()
        try:
            arguments = self._prepare_arguments(config)
            arguments[1] = "f0g_wrong_guideline"
            with self.assertRaises(psycopg.Error):
                self._invalid_prepare(config, arguments)
            self.assertEqual(self._label_and_audit_counts(config)[:2], (0, 0))
        finally:
            _drop_database(name)

    def test_prepare_rejects_actor_crosswire(self) -> None:
        name, config, _service, _contexts = self._scratch()
        try:
            arguments = self._prepare_arguments(config)
            arguments[6] = arguments[5]
            with self.assertRaises(psycopg.Error):
                self._invalid_prepare(config, arguments)
            self.assertEqual(self._label_and_audit_counts(config)[:2], (0, 0))
        finally:
            _drop_database(name)

    def test_prepare_creates_three_distinct_viewer_actors(self) -> None:
        self.assertEqual(len({item.actor_id for item in self.sessions}), 3)
        self.assertEqual(len({item.session_id for item in self.sessions}), 3)

    def test_guideline_is_fixture_only_and_not_gold(self) -> None:
        with self._scoped_connection(self.config) as connection:
            row = connection.execute("SELECT workflow_status,benchmark_tier,acceptance_gold,production_allowed,external_processing_policy FROM f0g.annotation_guideline").fetchone()
        self.assertEqual(row, ("HUMAN_LABELS_REQUIRED", "NONE", False, False, "DENY"))

    def test_assignment_queue_identity_is_complete(self) -> None:
        with self._scoped_connection(self.config) as connection:
            row = connection.execute("SELECT count(*),count(DISTINCT annotation_queue_id) FROM f0g.blind_assignment").fetchone()
        self.assertEqual(row, (15, 15))

    def test_real_fixture_labels_remain_zero_after_prepare(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            value = connection.execute("SELECT count(*) FROM f0f.gold_label_evidence").fetchone()[0]
        self.assertEqual(value, 0)

    def test_real_fixture_adjudications_remain_zero_after_prepare(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            value = connection.execute("SELECT count(*) FROM f0f.gold_adjudication").fetchone()[0]
        self.assertEqual(value, 0)

    def test_real_prepared_base_has_zero_annotation_action_audits(self) -> None:
        self.assertEqual(self._label_and_audit_counts(self.config), (0, 0, 0))

    def test_actor_tokens_are_stored_only_as_nonplaintext_hashes(self) -> None:
        tokens = tuple(session.token for session in self.sessions)
        with self._scoped_connection(self.config) as connection:
            values = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT token_sha256 FROM f0d.local_fixture_session "
                    "WHERE actor_id=ANY(%s)",
                    ([session.actor_id for session in self.sessions],),
                ).fetchall()
            )
        self.assertEqual(len(values), 3)
        self.assertTrue(all(token not in values for token in tokens))

    def test_actor_tokens_do_not_exist_in_workspace_or_artifacts(self) -> None:
        encoded = tuple(session.token.encode() for session in self.sessions)
        raw = tuple(bytes.fromhex(session.token.removeprefix("f0g_")) for session in self.sessions)
        needles = (*encoded, *raw, b"".join(raw))
        root = Path(__file__).resolve().parents[1]
        for relative in ("src", "tests", "artifacts", "PROGRESS.md", "BLOCKED.md"):
            candidate = root / relative
            paths = candidate.rglob("*") if candidate.is_dir() else (candidate,)
            for path in paths:
                if path.is_file() and "__pycache__" not in path.parts:
                    data = path.read_bytes()
                    self.assertTrue(all(needle not in data for needle in needles))

    def test_tables_have_forced_rls(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            rows = connection.execute("SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class JOIN pg_namespace ON pg_namespace.oid=relnamespace WHERE nspname='f0g' AND relkind='r' ORDER BY relname").fetchall()
        self.assertEqual(rows, [("annotation_guideline", True, True), ("blind_assignment", True, True)])

    def test_public_has_no_schema_privilege(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            value = connection.execute("SELECT has_schema_privilege('public','f0g','USAGE')").fetchone()[0]
        self.assertFalse(value)

    def test_public_has_no_f0g_table_privileges(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            values = connection.execute(
                "SELECT has_table_privilege('public','f0g.annotation_guideline','SELECT,INSERT,UPDATE,DELETE'),"
                "has_table_privilege('public','f0g.blind_assignment','SELECT,INSERT,UPDATE,DELETE')"
            ).fetchone()
        self.assertEqual(values, (False, False))

    def test_public_has_no_f0g_function_privileges(self) -> None:
        signatures = (
            "f0g.prepare_annotation_workflow(uuid,text,text,uuid[],uuid[],uuid,uuid,uuid,uuid)",
            "f0g.list_assigned_work()",
            "f0g.read_assigned_body(uuid,bytea,uuid)",
            "f0g.record_blind_label(uuid,uuid,bytea,bytea,text,bigint,uuid)",
            "f0g.read_adjudication_labels(uuid,bytea,uuid)",
            "f0g.adjudicate_assignment(uuid,uuid,bytea,text,uuid,uuid)",
        )
        with psycopg.connect(self.config.migration_dsn) as connection:
            values = tuple(
                connection.execute(
                    "SELECT has_function_privilege('public',%s,'EXECUTE')", (signature,)
                ).fetchone()[0]
                for signature in signatures
            )
        self.assertEqual(values, (False,) * len(signatures))

    def test_security_definer_functions_pin_pg_catalog_search_path(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            rows = connection.execute(
                "SELECT prosecdef,proconfig FROM pg_proc JOIN pg_namespace ON "
                "pg_namespace.oid=pronamespace WHERE nspname='f0g' ORDER BY proname"
            ).fetchall()
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(row[0] and "search_path=pg_catalog" in row[1] for row in rows))

    def test_complete_function_signature_and_grant_matrix(self) -> None:
        from platform_foundation.f0g import verify_function_catalog

        verify_function_catalog(self.config)

    def test_runtime_has_no_old_gold_function_execute(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            value = connection.execute("SELECT has_function_privilege('f0d_runtime','f0f.record_gold_label(uuid,uuid,bytea,bytea,text,bigint)','EXECUTE')").fetchone()[0]
        self.assertFalse(value)

    def test_worker_has_no_old_decrypt_execute(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            value = connection.execute("SELECT has_function_privilege('f0d_worker','f0f.decrypt_verified_body(uuid,bytea)','EXECUTE')").fetchone()[0]
        self.assertFalse(value)

    def test_runtime_has_no_old_decrypt_execute(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            value = connection.execute("SELECT has_function_privilege('f0d_runtime','f0f.decrypt_verified_body(uuid,bytea)','EXECUTE')").fetchone()[0]
        self.assertFalse(value)

    def test_runtime_cannot_direct_select_sensitive_f0f_tables(self) -> None:
        with self.assertRaises(psycopg.Error), psycopg.connect(self.config.runtime_dsn) as connection:
            connection.execute("SELECT count(*) FROM f0f.gold_label_evidence")

    def test_worker_cannot_direct_select_sensitive_f0f_tables(self) -> None:
        with self.assertRaises(psycopg.Error), psycopg.connect(self.config.worker_dsn) as connection:
            connection.execute("SELECT count(*) FROM f0f.page_body_evidence")

    def test_runtime_has_new_list_execute_only_via_function(self) -> None:
        with psycopg.connect(self.config.migration_dsn) as connection:
            value = connection.execute("SELECT has_function_privilege('f0d_runtime','f0g.list_assigned_work()','EXECUTE')").fetchone()[0]
        self.assertTrue(value)

    def test_guideline_update_is_immutable(self) -> None:
        with self.assertRaises(psycopg.Error), self._scoped_connection(self.config) as connection:
            connection.execute("UPDATE f0g.annotation_guideline SET guideline_version='changed'")

    def test_assignment_delete_is_immutable(self) -> None:
        with self.assertRaises(psycopg.Error), self._scoped_connection(self.config) as connection:
            connection.execute("DELETE FROM f0g.blind_assignment")

    def test_assignment_update_is_immutable(self) -> None:
        with self.assertRaises(psycopg.Error), self._scoped_connection(self.config) as connection:
            connection.execute("UPDATE f0g.blind_assignment SET assignment_status='HUMAN_LABELS_REQUIRED'")

    def test_assignment_truncate_is_immutable(self) -> None:
        with self.assertRaises(psycopg.Error), self._scoped_connection(self.config) as connection:
            connection.execute("TRUNCATE f0g.blind_assignment")

    def test_annotator_one_lists_fifteen_slot_one_assignments(self) -> None:
        rows = self.service.list_assignments(self.context_by_role["ANNOTATOR_ONE"])
        self.assertEqual(len(rows), 15)
        self.assertEqual({item.label_slot for item in rows}, {1})

    def test_annotator_two_lists_fifteen_slot_two_assignments(self) -> None:
        rows = self.service.list_assignments(self.context_by_role["ANNOTATOR_TWO"])
        self.assertEqual(len(rows), 15)
        self.assertEqual({item.label_slot for item in rows}, {2})

    def test_adjudicator_list_discloses_only_counts_not_labels(self) -> None:
        rows = self.service.list_assignments(self.context_by_role["ADJUDICATOR"])
        self.assertEqual(len(rows), 15)
        self.assertEqual({item.labels_submitted for item in rows}, {0})
        self.assertTrue(all("label_body" not in item.to_dict() for item in rows))

    def test_unrelated_operator_cannot_list_assignments(self) -> None:
        rows = self.service.list_assignments(self.operator)
        self.assertEqual(rows, ())

    def test_other_tenant_cannot_list_assignments(self) -> None:
        context = authenticate_local_session(self.config, LOCAL_TENANT_B_TOKEN)
        rows = self.service.list_assignments(context)
        self.assertEqual(rows, ())

    def test_other_tenant_body_submit_and_adjudicate_are_zero_write(self) -> None:
        from platform_foundation.f0g import CanonicalLabel, F0GError

        name, config, service, _contexts = self._scratch()
        try:
            context = authenticate_local_session(config, LOCAL_TENANT_B_TOKEN)
            label = CanonicalLabel(b"SYNTHETIC_TENANT_DENIAL_LABEL")
            try:
                with self.assertRaises(F0GError):
                    service.read_assigned_body(context, self.assignment.assignment_id)
                with self.assertRaises(F0GError):
                    service.submit_label(context, self.assignment.assignment_id, label)
                with self.assertRaises(F0GError):
                    service.adjudicate(context, self.assignment.assignment_id, "NO_CONSENSUS", None)
            finally:
                label.wipe()
            self.assertEqual(self._label_and_audit_counts(config), (0, 0, 0))
        finally:
            _drop_database(name)

    def test_unassigned_operator_submit_and_adjudicate_are_zero_write(self) -> None:
        from platform_foundation.f0g import CanonicalLabel, F0GError

        name, config, service, _contexts = self._scratch()
        try:
            label = CanonicalLabel(b"SYNTHETIC_OPERATOR_DENIAL_LABEL")
            try:
                with self.assertRaises(F0GError):
                    service.submit_label(self.operator, self.assignment.assignment_id, label)
                with self.assertRaises(F0GError):
                    service.adjudicate(self.operator, self.assignment.assignment_id, "NO_CONSENSUS", None)
            finally:
                label.wipe()
            self.assertEqual(self._label_and_audit_counts(config), (0, 0, 0))
        finally:
            _drop_database(name)

    def test_operator_cannot_read_assigned_body(self) -> None:
        from platform_foundation.f0g import F0GError

        with self.assertRaisesRegex(F0GError, "ANNOTATION_ASSIGNMENT_DENIED"):
            self.service.read_assigned_body(self.operator, self.assignment.assignment_id)

    def test_annotator_one_can_read_assigned_body_in_scratch(self) -> None:
        name, _config_value, service, contexts = self._scratch()
        try:
            body = service.read_assigned_body(contexts["ANNOTATOR_ONE"], self.assignment.assignment_id)
            try:
                self.assertGreater(body.byte_count, 0)
            finally:
                body.wipe()
        finally:
            _drop_database(name)

    def test_annotator_two_can_read_assigned_body_in_scratch(self) -> None:
        name, _config_value, service, contexts = self._scratch()
        try:
            body = service.read_assigned_body(contexts["ANNOTATOR_TWO"], self.assignment.assignment_id)
            try:
                self.assertGreater(body.byte_count, 0)
            finally:
                body.wipe()
        finally:
            _drop_database(name)

    def test_wrong_assignment_id_cannot_read_body(self) -> None:
        from platform_foundation.f0g import F0GError

        with self.assertRaisesRegex(F0GError, "ANNOTATION_ASSIGNMENT_DENIED"):
            self.service.read_assigned_body(self.context_by_role["ANNOTATOR_ONE"], uuid.uuid4())

    def test_wrong_key_cannot_read_body(self) -> None:
        from platform_foundation.f0g import AnnotationService, F0GError

        with _wrong_key_path() as path:
            service = AnnotationService(self.config, path)
            with self.assertRaisesRegex(F0GError, "ANNOTATION_ASSIGNMENT_DENIED"):
                service.read_assigned_body(self.context_by_role["ANNOTATOR_ONE"], self.assignment.assignment_id)

    def test_second_annotator_submitting_first_keeps_slot_two(self) -> None:
        name, _config_value, service, contexts = self._scratch()
        try:
            self._submit_pair(service, contexts, self.assignment.assignment_id)
            pairs = service.read_adjudication_labels(contexts["ADJUDICATOR"], self.assignment.assignment_id)
            try:
                self.assertEqual(tuple(metadata.label_ordinal for metadata, _body in pairs), (1, 2))
            finally:
                for _metadata, body in pairs:
                    body.wipe()
        finally:
            _drop_database(name)

    def test_same_label_from_distinct_actors_has_distinct_ids(self) -> None:
        name, _config_value, service, contexts = self._scratch()
        try:
            one, two = self._submit_pair(service, contexts, self.assignment.assignment_id, same=True)
            self.assertNotEqual(one, two)
        finally:
            _drop_database(name)

    def test_exact_label_retry_is_idempotent(self) -> None:
        from platform_foundation.f0g import CanonicalLabel

        name, _config_value, service, contexts = self._scratch()
        try:
            label = CanonicalLabel(b"SYNTHETIC_RETRY_LABEL")
            try:
                first = service.submit_label(contexts["ANNOTATOR_ONE"], self.assignment.assignment_id, label)
                second = service.submit_label(contexts["ANNOTATOR_ONE"], self.assignment.assignment_id, label)
            finally:
                label.wipe()
            self.assertEqual(first, second)
        finally:
            _drop_database(name)

    def test_conflicting_label_retry_is_rejected(self) -> None:
        from platform_foundation.f0g import CanonicalLabel, F0GError

        name, _config_value, service, contexts = self._scratch()
        try:
            one = CanonicalLabel(b"SYNTHETIC_FIRST_LABEL")
            two = CanonicalLabel(b"SYNTHETIC_CONFLICT_LABEL")
            try:
                service.submit_label(contexts["ANNOTATOR_ONE"], self.assignment.assignment_id, one)
                with self.assertRaisesRegex(F0GError, "ANNOTATION_STATE_INVALID"):
                    service.submit_label(contexts["ANNOTATOR_ONE"], self.assignment.assignment_id, two)
            finally:
                one.wipe()
                two.wipe()
        finally:
            _drop_database(name)

    def test_noncanonical_label_rejection_leaves_zero_rows_and_audits(self) -> None:
        from platform_foundation.f0g import CanonicalLabel, F0GError

        name, config, _service, _contexts = self._scratch()
        try:
            with self.assertRaisesRegex(F0GError, "ANNOTATION_LABEL_INVALID"):
                CanonicalLabel(b"SYNTHETIC\r\nNONCANONICAL")
            self.assertEqual(self._label_and_audit_counts(config), (0, 0, 0))
        finally:
            _drop_database(name)

    def test_concurrent_exact_retry_converges(self) -> None:
        from platform_foundation.f0g import CanonicalLabel

        name, _config_value, service, contexts = self._scratch()
        try:
            def submit() -> uuid.UUID:
                label = CanonicalLabel(b"SYNTHETIC_CONCURRENT_LABEL")
                try:
                    return service.submit_label(contexts["ANNOTATOR_ONE"], self.assignment.assignment_id, label)
                finally:
                    label.wipe()
            with ThreadPoolExecutor(max_workers=2) as pool:
                values = tuple(pool.map(lambda _item: submit(), range(2)))
            self.assertEqual(values[0], values[1])
        finally:
            _drop_database(name)

    def test_adjudication_denied_with_zero_labels(self) -> None:
        from platform_foundation.f0g import F0GError

        with self.assertRaisesRegex(F0GError, "ANNOTATION_ADJUDICATION_DENIED"):
            self.service.adjudicate(self.context_by_role["ADJUDICATOR"], self.assignment.assignment_id, "NO_CONSENSUS", None)

    def test_adjudicator_body_denied_before_two_labels(self) -> None:
        from platform_foundation.f0g import F0GError

        with self.assertRaisesRegex(F0GError, "ANNOTATION_ASSIGNMENT_DENIED"):
            self.service.read_assigned_body(self.context_by_role["ADJUDICATOR"], self.assignment.assignment_id)

    def test_adjudicator_body_allowed_after_two_labels(self) -> None:
        name, _config_value, service, contexts = self._scratch()
        try:
            self._submit_pair(service, contexts, self.assignment.assignment_id)
            body = service.read_assigned_body(contexts["ADJUDICATOR"], self.assignment.assignment_id)
            try:
                self.assertGreater(body.byte_count, 0)
            finally:
                body.wipe()
        finally:
            _drop_database(name)

    def test_peer_submission_does_not_change_annotator_own_status(self) -> None:
        from platform_foundation.f0g import CanonicalLabel

        name, _config_value, service, contexts = self._scratch()
        try:
            label = CanonicalLabel(b"SYNTHETIC_BLIND_PEER_LABEL")
            try:
                service.submit_label(contexts["ANNOTATOR_ONE"], self.assignment.assignment_id, label)
            finally:
                label.wipe()
            rows = service.list_assignments(contexts["ANNOTATOR_TWO"])
            current = next(item for item in rows if item.assignment_id == self.assignment.assignment_id)
            self.assertFalse(current.own_label_submitted)
            self.assertIsNone(current.labels_submitted)
            self.assertEqual(current.assignment_status, "ANNOTATION_PENDING")
        finally:
            _drop_database(name)

    def test_annotator_cannot_read_adjudication_pair_after_peer_submission(self) -> None:
        from platform_foundation.f0g import CanonicalLabel, F0GError

        name, _config_value, service, contexts = self._scratch()
        try:
            label = CanonicalLabel(b"SYNTHETIC_PEER_PAIR_DENIAL")
            try:
                service.submit_label(contexts["ANNOTATOR_ONE"], self.assignment.assignment_id, label)
            finally:
                label.wipe()
            with self.assertRaisesRegex(F0GError, "ANNOTATION_ADJUDICATION_DENIED"):
                service.read_adjudication_labels(contexts["ANNOTATOR_TWO"], self.assignment.assignment_id)
        finally:
            _drop_database(name)

    def test_annotator_direct_label_table_select_is_denied(self) -> None:
        name, config, service, contexts = self._scratch()
        try:
            self._submit_pair(service, contexts, self.assignment.assignment_id)
            with self.assertRaises(psycopg.Error), psycopg.connect(config.runtime_dsn) as connection:
                connection.execute("SELECT label_plaintext_sha256 FROM f0f.gold_label_evidence")
        finally:
            _drop_database(name)

    def test_adjudication_denied_with_one_label(self) -> None:
        from platform_foundation.f0g import CanonicalLabel, F0GError

        name, _config_value, service, contexts = self._scratch()
        try:
            label = CanonicalLabel(b"SYNTHETIC_SINGLE_LABEL")
            try:
                service.submit_label(contexts["ANNOTATOR_ONE"], self.assignment.assignment_id, label)
            finally:
                label.wipe()
            with self.assertRaisesRegex(F0GError, "ANNOTATION_ADJUDICATION_DENIED"):
                service.adjudicate(contexts["ADJUDICATOR"], self.assignment.assignment_id, "NO_CONSENSUS", None)
        finally:
            _drop_database(name)

    def test_annotator_cannot_self_adjudicate(self) -> None:
        from platform_foundation.f0g import F0GError

        name, _config_value, service, contexts = self._scratch()
        try:
            self._submit_pair(service, contexts, self.assignment.assignment_id)
            with self.assertRaisesRegex(F0GError, "ANNOTATION_ADJUDICATION_DENIED"):
                service.adjudicate(contexts["ANNOTATOR_ONE"], self.assignment.assignment_id, "NO_CONSENSUS", None)
        finally:
            _drop_database(name)

    def test_accept_label_one_records_seed_fixture_status(self) -> None:
        name, config, service, contexts = self._scratch()
        try:
            self._submit_pair(service, contexts, self.assignment.assignment_id)
            service.adjudicate(contexts["ADJUDICATOR"], self.assignment.assignment_id, "ACCEPT_LABEL_ONE", 1)
            with self._scoped_connection(config) as connection:
                row = connection.execute("SELECT decision_code,gold_status,benchmark_tier,acceptance_gold,production_allowed FROM f0f.gold_adjudication").fetchone()
            self.assertEqual(row, ("ACCEPT_LABEL_ONE", "FIXTURE_SEED_GOLD", "NONE", False, False))
        finally:
            _drop_database(name)

    def test_accept_label_two_records_selected_second_label(self) -> None:
        name, config, service, contexts = self._scratch()
        try:
            _one, two = self._submit_pair(service, contexts, self.assignment.assignment_id)
            service.adjudicate(contexts["ADJUDICATOR"], self.assignment.assignment_id, "ACCEPT_LABEL_TWO", 2)
            with self._scoped_connection(config) as connection:
                selected = connection.execute("SELECT selected_label_id FROM f0f.gold_adjudication").fetchone()[0]
            self.assertEqual(selected, two)
        finally:
            _drop_database(name)

    def test_no_consensus_records_unresolved_status(self) -> None:
        name, config, service, contexts = self._scratch()
        try:
            self._submit_pair(service, contexts, self.assignment.assignment_id)
            service.adjudicate(contexts["ADJUDICATOR"], self.assignment.assignment_id, "NO_CONSENSUS", None)
            with self._scoped_connection(config) as connection:
                row = connection.execute("SELECT gold_status,selected_label_id FROM f0f.gold_adjudication").fetchone()
            self.assertEqual(row, ("ADJUDICATION_UNRESOLVED", None))
        finally:
            _drop_database(name)

    def test_exact_adjudication_retry_is_idempotent(self) -> None:
        name, config, service, contexts = self._scratch()
        try:
            self._submit_pair(service, contexts, self.assignment.assignment_id)
            first = service.adjudicate(contexts["ADJUDICATOR"], self.assignment.assignment_id, "ACCEPT_LABEL_ONE", 1)
            after_first = self._label_and_audit_counts(config)
            second = service.adjudicate(contexts["ADJUDICATOR"], self.assignment.assignment_id, "ACCEPT_LABEL_ONE", 1)
            self.assertEqual(first, second)
            self.assertEqual(after_first, (2, 1, 3))
            self.assertEqual(self._label_and_audit_counts(config), after_first)
        finally:
            _drop_database(name)

    def test_conflicting_adjudication_retry_is_rejected(self) -> None:
        from platform_foundation.f0g import F0GError

        name, config, service, contexts = self._scratch()
        try:
            self._submit_pair(service, contexts, self.assignment.assignment_id)
            service.adjudicate(contexts["ADJUDICATOR"], self.assignment.assignment_id, "ACCEPT_LABEL_ONE", 1)
            with self.assertRaisesRegex(F0GError, "ANNOTATION_ADJUDICATION_DENIED"):
                service.adjudicate(contexts["ADJUDICATOR"], self.assignment.assignment_id, "NO_CONSENSUS", None)
            self.assertEqual(self._label_and_audit_counts(config), (2, 1, 3))
        finally:
            _drop_database(name)

    def test_concurrent_exact_adjudication_creates_at_most_one_row(self) -> None:
        name, config, service, contexts = self._scratch()
        try:
            self._submit_pair(service, contexts, self.assignment.assignment_id)
            def decide() -> object:
                return service.adjudicate(contexts["ADJUDICATOR"], self.assignment.assignment_id, "NO_CONSENSUS", None)
            with ThreadPoolExecutor(max_workers=2) as pool:
                values = tuple(pool.map(lambda _item: decide(), range(2)))
            self.assertEqual(values[0], values[1])
            self.assertEqual(self._label_and_audit_counts(config), (2, 1, 3))
        finally:
            _drop_database(name)

    def test_tampered_label_rollback_restores_read_and_leaves_no_failed_audit(self) -> None:
        from platform_foundation.f0f import load_keyfile

        name, config, service, contexts = self._scratch()
        try:
            self._submit_pair(service, contexts, self.assignment.assignment_id)
            before = self._label_and_audit_counts(config)[2]
            with load_keyfile(KEY_PATH) as key:
                key_material = bytes(key.view())
            connection = self._scoped_connection(config)
            try:
                connection.execute("ALTER TABLE f0f.gold_label_evidence NO FORCE ROW LEVEL SECURITY")
                connection.execute("ALTER TABLE f0f.gold_label_evidence DISABLE TRIGGER USER")
                connection.execute(
                    "UPDATE f0f.gold_label_evidence SET "
                    "label_ciphertext=label_ciphertext || %s,"
                    "label_ciphertext_sha256=encode(f0f_crypto.digest(label_ciphertext || %s,'sha256'),'hex') "
                    "WHERE label_ordinal=1", (b"X", b"X")
                )
                with self.assertRaises(psycopg.Error):
                    connection.execute(
                        "SELECT * FROM f0g.read_adjudication_labels(%s,%s,%s)",
                        (self.assignment.assignment_id, key_material, uuid.uuid4()),
                    ).fetchall()
            finally:
                connection.rollback()
                connection.close()
            self.assertEqual(self._label_and_audit_counts(config)[2], before)
            pairs = service.read_adjudication_labels(contexts["ADJUDICATOR"], self.assignment.assignment_id)
            for _metadata, body in pairs:
                body.wipe()
            self.assertEqual(self._label_and_audit_counts(config)[2], before + 1)
        finally:
            _drop_database(name)

    def test_local_server_config_disables_forwarding_and_parallel_workers(self) -> None:
        from platform_foundation.f0g import local_server_config

        config = local_server_config(self.service)
        self.assertEqual((config.host, config.port), ("127.0.0.1", 8767))
        self.assertEqual((config.uds, config.fd), (None, None))
        self.assertFalse(config.proxy_headers)
        self.assertEqual(config.forwarded_allow_ips, "")
        self.assertFalse(config.access_log)
        self.assertFalse(config.reload)
        self.assertEqual(config.workers, 1)

    def test_loopback_bind_check_opens_and_closes_fixed_listener(self) -> None:
        from platform_foundation.f0g import check_local_server_binding

        result = check_local_server_binding(self.service)
        self.assertEqual(
            result,
            {
                "schema": "f0g-loopback-bind-check-v1",
                "status": "LOCAL_FIXTURE_ANNOTATION_LOOPBACK_BIND_READY",
                "host": "127.0.0.1",
                "port": 8767,
            },
        )

    def test_api_health_is_loopback_only_and_no_store(self) -> None:
        from platform_foundation.f0g import create_app

        client = TestClient(create_app(self.service), client=("127.0.0.1", 55000))
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_api_remote_client_is_denied(self) -> None:
        from platform_foundation.f0g import create_app

        client = TestClient(create_app(self.service), client=("198.51.100.4", 55000))
        response = client.get("/health")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["reason_code"], "LOCAL_ONLY_REQUIRED")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_api_missing_authentication_is_no_store(self) -> None:
        from platform_foundation.f0g import create_app

        client = TestClient(create_app(self.service), client=("127.0.0.1", 55000))
        response = client.get("/assignments")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_api_docs_and_openapi_are_disabled(self) -> None:
        from platform_foundation.f0g import create_app

        client = TestClient(create_app(self.service), client=("127.0.0.1", 55000))
        self.assertEqual(client.get("/docs").status_code, 404)
        self.assertEqual(client.get("/openapi.json").status_code, 404)

    def test_api_assignments_do_not_disclose_peer_label(self) -> None:
        from platform_foundation.f0g import create_app

        token = self.session_by_role["ANNOTATOR_ONE"].token
        client = TestClient(create_app(self.service), client=("127.0.0.1", 55000))
        response = client.get("/assignments", headers={"Authorization": "Bearer " + token})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("label_body", response.text)

    def test_api_error_is_redacted_and_no_store(self) -> None:
        from platform_foundation.f0g import create_app

        canary = "SYNTHETIC_ERROR_CANARY"
        client = TestClient(create_app(self.service), client=("127.0.0.1", 55000))
        response = client.get("/assignments", headers={"Authorization": "Bearer " + canary})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn(canary, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_api_label_post_returns_nongold_schema_and_no_store(self) -> None:
        from platform_foundation.f0g import create_app

        name, config, service, _contexts = self._scratch()
        try:
            client = TestClient(create_app(service), client=("127.0.0.1", 55000))
            response = client.post(
                f"/assignments/{self.assignment.assignment_id}/labels",
                headers={"Authorization": "Bearer " + self.session_by_role["ANNOTATOR_ONE"].token},
                content=b"SYNTHETIC_API_LABEL",
            )
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json()["gold_status"], "NOT_GOLD")
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(self._label_and_audit_counts(config)[:2], (1, 0))
        finally:
            _drop_database(name)

    def test_api_adjudication_rejects_extra_fields_and_is_no_store(self) -> None:
        from platform_foundation.f0g import create_app

        name, _config_value, service, contexts = self._scratch()
        try:
            self._submit_pair(service, contexts, self.assignment.assignment_id)
            client = TestClient(create_app(service), client=("127.0.0.1", 55000))
            response = client.post(
                f"/assignments/{self.assignment.assignment_id}/adjudication",
                headers={"Authorization": "Bearer " + self.session_by_role["ADJUDICATOR"].token},
                json={"decision_code": "NO_CONSENSUS", "selected_label_ordinal": None, "extra": "DENY"},
            )
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.headers["cache-control"], "no-store")
        finally:
            _drop_database(name)

    def test_api_adjudication_accepts_exact_4096_byte_transport_boundary(self) -> None:
        from platform_foundation.f0g import create_app

        base = b'{"decision_code":"NO_CONSENSUS","selected_label_ordinal":null}'
        payload = base + (b" " * (4096 - len(base)))
        client = TestClient(create_app(self.service), client=("127.0.0.1", 55000))
        response = client.post(
            f"/assignments/{self.assignment.assignment_id}/adjudication",
            headers={
                "Authorization": "Bearer "
                + self.session_by_role["ADJUDICATOR"].token
            },
            content=payload,
        )
        self.assertEqual(response.request.headers["content-length"], "4096")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["reason_code"], "ANNOTATION_ADJUDICATION_DENIED"
        )
        self.assertEqual(self._label_and_audit_counts(self.config), (0, 0, 0))

    def test_api_adjudication_rejects_4097_byte_content_length(self) -> None:
        from platform_foundation.f0g import create_app

        base = b'{"decision_code":"NO_CONSENSUS","selected_label_ordinal":null}'
        payload = base + (b" " * (4097 - len(base)))
        client = TestClient(create_app(self.service), client=("127.0.0.1", 55000))
        response = client.post(
            f"/assignments/{self.assignment.assignment_id}/adjudication",
            headers={
                "Authorization": "Bearer "
                + self.session_by_role["ADJUDICATOR"].token
            },
            content=payload,
        )
        self.assertEqual(response.request.headers["content-length"], "4097")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["reason_code"], "ANNOTATION_CONTRACT_INVALID")
        self.assertEqual(self._label_and_audit_counts(self.config), (0, 0, 0))

    def test_api_adjudication_rejects_chunked_stream_past_4096_bytes(self) -> None:
        from platform_foundation.f0g import create_app

        base = b'{"decision_code":"NO_CONSENSUS","selected_label_ordinal":null}'
        chunks = iter((base, b" " * (4096 - len(base)), b"X"))
        client = TestClient(create_app(self.service), client=("127.0.0.1", 55000))
        response = client.post(
            f"/assignments/{self.assignment.assignment_id}/adjudication",
            headers={
                "Authorization": "Bearer "
                + self.session_by_role["ADJUDICATOR"].token
            },
            content=chunks,
        )
        self.assertNotIn("content-length", response.request.headers)
        self.assertEqual(response.request.headers["transfer-encoding"], "chunked")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["reason_code"], "ANNOTATION_CONTRACT_INVALID")
        self.assertEqual(self._label_and_audit_counts(self.config), (0, 0, 0))

    def test_api_adjudication_post_returns_nonproduction_schema(self) -> None:
        from platform_foundation.f0g import create_app

        name, config, service, contexts = self._scratch()
        try:
            self._submit_pair(service, contexts, self.assignment.assignment_id)
            client = TestClient(create_app(service), client=("127.0.0.1", 55000))
            response = client.post(
                f"/assignments/{self.assignment.assignment_id}/adjudication",
                headers={"Authorization": "Bearer " + self.session_by_role["ADJUDICATOR"].token},
                json={"decision_code": "NO_CONSENSUS", "selected_label_ordinal": None},
            )
            self.assertEqual(response.status_code, 201)
            self.assertFalse(response.json()["production_allowed"])
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(self._label_and_audit_counts(config)[:2], (2, 1))
        finally:
            _drop_database(name)

    def test_api_body_response_is_binary_and_no_store(self) -> None:
        from platform_foundation.f0g import create_app

        name, _config_value, service, _contexts = self._scratch()
        try:
            token = self.session_by_role["ANNOTATOR_ONE"].token
            client = TestClient(create_app(service), client=("127.0.0.1", 55000))
            response = client.get(
                f"/assignments/{self.assignment.assignment_id}/body",
                headers={"Authorization": "Bearer " + token},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], "application/octet-stream")
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn("content-disposition", response.headers)
            self.assertNotIn("filename", " ".join(response.headers.values()).lower())
            self.assertNotIn("http://", " ".join(response.headers.values()).lower())
            self.assertGreater(len(response.content), 0)
        finally:
            _drop_database(name)

    def _assert_catalog_tamper_rejected(self, statement: str) -> None:
        from platform_foundation.f0g import F0GError, verify_function_catalog

        name, config, _service, _contexts = self._scratch()
        try:
            with psycopg.connect(config.migration_dsn) as connection:
                connection.execute(statement)
            with self.assertRaisesRegex(F0GError, "ANNOTATION_DATABASE_FAILED"):
                verify_function_catalog(config)
        finally:
            _drop_database(name)

    def test_exact_label_retry_adds_no_audit(self) -> None:
        from platform_foundation.f0g import CanonicalLabel

        name, config, service, contexts = self._scratch()
        try:
            label = CanonicalLabel(b"SYNTHETIC_EXACT_LABEL_AUDIT")
            try:
                first = service.submit_label(
                    contexts["ANNOTATOR_ONE"], self.assignment.assignment_id, label
                )
                after_first = self._label_and_audit_counts(config)
                second = service.submit_label(
                    contexts["ANNOTATOR_ONE"], self.assignment.assignment_id, label
                )
            finally:
                label.wipe()
            self.assertEqual(first, second)
            self.assertEqual(after_first, (1, 0, 1))
            self.assertEqual(self._label_and_audit_counts(config), after_first)
        finally:
            _drop_database(name)

    def test_cross_assignment_label_id_reuse_is_zero_write(self) -> None:
        from platform_foundation.f0f import load_keyfile
        from platform_foundation.f0g import CanonicalLabel

        name, config, service, contexts = self._scratch()
        key_material = bytearray()
        try:
            assignments = service.list_assignments(contexts["ANNOTATOR_ONE"])
            first_assignment = assignments[0]
            second_assignment = assignments[1]
            label = CanonicalLabel(b"SYNTHETIC_CROSS_ASSIGNMENT_LABEL")
            try:
                first_label_id = service.submit_label(
                    contexts["ANNOTATOR_ONE"], first_assignment.assignment_id, label
                )
                before = self._label_and_audit_counts(config)
                with load_keyfile(KEY_PATH) as key:
                    key_material.extend(key.view())
                with self.assertRaises(DatabaseError):
                    with tenant_transaction(
                        config, "f0d_runtime", contexts["ANNOTATOR_ONE"]
                    ) as connection:
                        connection.execute(
                            "SELECT f0g.record_blind_label("
                            "%s,%s,%s,%s,%s,%s,%s)",
                            (
                                first_label_id,
                                second_assignment.assignment_id,
                                key_material,
                                label.view(),
                                label.sha256,
                                label.byte_count,
                                uuid.uuid4(),
                            ),
                        ).fetchone()
                self.assertEqual(before, (1, 0, 1))
                self.assertEqual(self._label_and_audit_counts(config), before)
            finally:
                label.wipe()
        finally:
            key_material[:] = b"\0" * len(key_material)
            key_material.clear()
            _drop_database(name)

    def test_stored_label_ciphertext_differs_from_plaintext(self) -> None:
        from platform_foundation.f0g import CanonicalLabel

        name, config, service, contexts = self._scratch()
        plaintext = b"SYNTHETIC_CIPHERTEXT_DISTINCTION"
        try:
            label = CanonicalLabel(plaintext)
            try:
                service.submit_label(
                    contexts["ANNOTATOR_ONE"], self.assignment.assignment_id, label
                )
            finally:
                label.wipe()
            with self._scoped_connection(config) as connection:
                ciphertext = bytes(
                    connection.execute(
                        "SELECT label_ciphertext FROM f0f.gold_label_evidence"
                    ).fetchone()[0]
                )
            self.assertNotEqual(ciphertext, plaintext)
            self.assertFalse(plaintext in ciphertext)
        finally:
            _drop_database(name)

    def test_body_key_token_and_label_do_not_reach_stdout_stderr_or_logs(self) -> None:
        from platform_foundation.f0f import load_keyfile
        from platform_foundation.f0g import CanonicalLabel

        name, _config_value, service, contexts = self._scratch()
        stdout = io.StringIO()
        stderr = io.StringIO()
        log_output = io.StringIO()
        handler = logging.StreamHandler(log_output)
        root_logger = logging.getLogger()
        previous_level = root_logger.level
        body_plaintext = b""
        label_plaintext = b"SYNTHETIC_CAPTURED_LOG_LABEL_CANARY"
        try:
            with load_keyfile(KEY_PATH) as key:
                key_plaintext = bytes(key.view())
            root_logger.addHandler(handler)
            root_logger.setLevel(logging.DEBUG)
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                body = service.read_assigned_body(
                    contexts["ANNOTATOR_ONE"], self.assignment.assignment_id
                )
                try:
                    body_plaintext = bytes(body.view())
                finally:
                    body.wipe()
                label = CanonicalLabel(label_plaintext)
                try:
                    service.submit_label(
                        contexts["ANNOTATOR_ONE"],
                        self.assignment.assignment_id,
                        label,
                    )
                finally:
                    label.wipe()
            captured = (
                stdout.getvalue() + stderr.getvalue() + log_output.getvalue()
            ).encode("utf-8")
            needles = (
                body_plaintext,
                key_plaintext,
                self.session_by_role["ANNOTATOR_ONE"].token.encode("utf-8"),
                label_plaintext,
            )
            self.assertFalse(any(needle in captured for needle in needles))
        finally:
            root_logger.removeHandler(handler)
            root_logger.setLevel(previous_level)
            handler.close()
            _drop_database(name)

    def test_exact_label_retry_revalidates_stored_ciphertext(self) -> None:
        from platform_foundation.f0g import CanonicalLabel, F0GError

        name, config, service, contexts = self._scratch()
        try:
            label = CanonicalLabel(b"SYNTHETIC_EXACT_LABEL_TAMPER")
            try:
                service.submit_label(
                    contexts["ANNOTATOR_ONE"], self.assignment.assignment_id, label
                )
                before = self._label_and_audit_counts(config)
                bootstrap_target = BOOTSTRAP_DSN.rsplit("/", 1)[0] + "/" + name
                with psycopg.connect(bootstrap_target) as connection:
                    connection.execute("SET LOCAL session_replication_role='replica'")
                    connection.execute(
                        "UPDATE f0f.gold_label_evidence SET "
                        "label_ciphertext=label_ciphertext || %s,"
                        "label_ciphertext_sha256=encode(f0f_crypto.digest("
                        "label_ciphertext || %s,'sha256'),'hex')",
                        (b"X", b"X"),
                    )
                with self.assertRaisesRegex(F0GError, "ANNOTATION_STATE_INVALID"):
                    service.submit_label(
                        contexts["ANNOTATOR_ONE"],
                        self.assignment.assignment_id,
                        label,
                    )
                self.assertEqual(self._label_and_audit_counts(config), before)
            finally:
                label.wipe()
        finally:
            _drop_database(name)

    def test_exact_adjudication_retry_revalidates_stored_ciphertext(self) -> None:
        from platform_foundation.f0g import F0GError

        name, config, service, contexts = self._scratch()
        try:
            self._submit_pair(service, contexts, self.assignment.assignment_id)
            service.adjudicate(
                contexts["ADJUDICATOR"],
                self.assignment.assignment_id,
                "ACCEPT_LABEL_ONE",
                1,
            )
            before = self._label_and_audit_counts(config)
            bootstrap_target = BOOTSTRAP_DSN.rsplit("/", 1)[0] + "/" + name
            with psycopg.connect(bootstrap_target) as connection:
                connection.execute("SET LOCAL session_replication_role='replica'")
                connection.execute(
                    "UPDATE f0f.gold_label_evidence SET "
                    "label_ciphertext=label_ciphertext || %s,"
                    "label_ciphertext_sha256=encode(f0f_crypto.digest("
                    "label_ciphertext || %s,'sha256'),'hex') WHERE label_ordinal=1",
                    (b"X", b"X"),
                )
            with self.assertRaisesRegex(F0GError, "ANNOTATION_ADJUDICATION_DENIED"):
                service.adjudicate(
                    contexts["ADJUDICATOR"],
                    self.assignment.assignment_id,
                    "ACCEPT_LABEL_ONE",
                    1,
                )
            self.assertEqual(self._label_and_audit_counts(config), before)
        finally:
            _drop_database(name)

    def test_acceptance_requires_three_current_fixture_sessions(self) -> None:
        from platform_foundation.f0g.acceptance import acceptance_snapshot

        snapshot = acceptance_snapshot(self.config, self.operator)
        self.assertEqual(
            (
                snapshot["fixture_actors"],
                snapshot["active_fixture_memberships"],
                snapshot["active_fixture_sessions"],
                snapshot["unique_fixture_session_token_hashes"],
                snapshot["fixture_actor_violations"],
                snapshot["fixture_membership_violations"],
                snapshot["fixture_session_violations"],
                snapshot["fixture_session_token_violations"],
                snapshot["prepare_audits"],
            ),
            (3, 3, 3, 3, 0, 0, 0, 0, 1),
        )

    def test_acceptance_detects_revoked_fixture_session(self) -> None:
        from platform_foundation.f0g.acceptance import acceptance_snapshot

        name, config, _service, contexts = self._scratch()
        try:
            bootstrap_target = BOOTSTRAP_DSN.rsplit("/", 1)[0] + "/" + name
            with psycopg.connect(bootstrap_target) as connection:
                connection.execute("SET LOCAL session_replication_role='replica'")
                connection.execute(
                    "UPDATE f0d.local_fixture_session SET "
                    "revoked_at=statement_timestamp() WHERE actor_id=%s",
                    (contexts["ANNOTATOR_ONE"].actor_id,),
                )
            snapshot = acceptance_snapshot(config, self.operator)
            self.assertEqual(snapshot["active_fixture_sessions"], 2)
            self.assertEqual(snapshot["fixture_session_violations"], 1)
        finally:
            _drop_database(name)

    def test_token_bundle_binding_rejects_replaced_session_hash(self) -> None:
        from platform_foundation.f0g import F0GError, verify_token_bundle_binding

        name, config, _service, contexts = self._scratch()
        try:
            bootstrap_target = BOOTSTRAP_DSN.rsplit("/", 1)[0] + "/" + name
            with psycopg.connect(bootstrap_target) as connection:
                connection.execute("SET LOCAL session_replication_role='replica'")
                connection.execute(
                    "UPDATE f0d.local_fixture_session SET token_sha256=%s "
                    "WHERE actor_id=%s",
                    ("f" * 64, contexts["ANNOTATOR_ONE"].actor_id),
                )
            with self.assertRaisesRegex(F0GError, "ANNOTATION_ACCEPTANCE_MISMATCH"):
                verify_token_bundle_binding(config, self.operator, self.token_path)
        finally:
            _drop_database(name)

    def test_token_bundle_binding_rejects_assignment_role_crosswire(self) -> None:
        from platform_foundation.f0g import F0GError, verify_token_bundle_binding

        name, config, _service, _contexts = self._scratch()
        try:
            bootstrap_target = BOOTSTRAP_DSN.rsplit("/", 1)[0] + "/" + name
            with psycopg.connect(bootstrap_target) as connection:
                connection.execute("SET LOCAL session_replication_role='replica'")
                connection.execute(
                    "UPDATE f0g.blind_assignment SET "
                    "annotator_one_actor_id=annotator_two_actor_id,"
                    "annotator_two_actor_id=annotator_one_actor_id WHERE id=("
                    "SELECT id FROM f0g.blind_assignment "
                    "ORDER BY annotation_queue_id LIMIT 1)"
                )
            with self.assertRaisesRegex(
                F0GError, "ANNOTATION_ACCEPTANCE_MISMATCH"
            ):
                verify_token_bundle_binding(config, self.operator, self.token_path)
        finally:
            _drop_database(name)

    def test_acceptance_detects_duplicate_prepare_audit(self) -> None:
        from platform_foundation.f0g.acceptance import acceptance_snapshot

        name, config, _service, _contexts = self._scratch()
        try:
            with self._scoped_connection(config) as connection:
                guideline_id = connection.execute(
                    "SELECT id FROM f0g.annotation_guideline"
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO f0d.audit_event("
                    "id,enterprise_id,actor_id,event_code,target_type,target_id,"
                    "correlation_id,outcome_code) VALUES "
                    "(%s,%s,%s,'F0G_WORKFLOW_PREPARED','ANNOTATION_GUIDELINE',"
                    "%s,%s,'SUCCESS')",
                    (
                        uuid.uuid4(),
                        self.operator.enterprise_id,
                        self.operator.actor_id,
                        guideline_id,
                        guideline_id,
                    ),
                )
            self.assertEqual(
                acceptance_snapshot(config, self.operator)["prepare_audits"], 2
            )
        finally:
            _drop_database(name)

    def test_catalog_rejects_search_path_drift(self) -> None:
        self._assert_catalog_tamper_rejected(
            "ALTER FUNCTION f0g.list_assigned_work() "
            "SET search_path=pg_catalog,public"
        )

    def test_catalog_rejects_public_function_execute(self) -> None:
        self._assert_catalog_tamper_rejected(
            "GRANT EXECUTE ON FUNCTION f0g.list_assigned_work() TO PUBLIC"
        )

    def test_catalog_rejects_force_rls_drift(self) -> None:
        self._assert_catalog_tamper_rejected(
            "ALTER TABLE f0g.blind_assignment NO FORCE ROW LEVEL SECURITY"
        )

    def test_catalog_rejects_immutable_trigger_drift(self) -> None:
        self._assert_catalog_tamper_rejected(
            "ALTER TABLE f0g.blind_assignment "
            "DISABLE TRIGGER reject_immutable_row_mutation"
        )

    def test_catalog_rejects_sensitive_column_select_grant(self) -> None:
        self._assert_catalog_tamper_rejected(
            "GRANT SELECT (label_ciphertext) ON "
            "f0f.gold_label_evidence TO f0d_runtime"
        )

    def test_catalog_rejects_runtime_f0g_table_insert_grant(self) -> None:
        self._assert_catalog_tamper_rejected(
            "GRANT INSERT ON f0g.blind_assignment TO f0d_runtime"
        )

    def test_catalog_rejects_worker_f0g_column_select_grant(self) -> None:
        self._assert_catalog_tamper_rejected(
            "GRANT SELECT (guideline_version) ON "
            "f0g.annotation_guideline TO f0d_worker"
        )

    def test_catalog_rejects_runtime_f0f_table_delete_grant(self) -> None:
        self._assert_catalog_tamper_rejected(
            "GRANT DELETE ON f0f.gold_adjudication TO f0d_runtime"
        )

    def test_catalog_rejects_worker_f0f_column_update_grant(self) -> None:
        self._assert_catalog_tamper_rejected(
            "GRANT UPDATE (label_ciphertext) ON "
            "f0f.gold_label_evidence TO f0d_worker"
        )

    def test_catalog_rejects_worker_f0g_schema_usage_grant(self) -> None:
        self._assert_catalog_tamper_rejected(
            "GRANT USAGE ON SCHEMA f0g TO f0d_worker"
        )

    def test_catalog_rejects_runtime_f0g_schema_create_grant(self) -> None:
        self._assert_catalog_tamper_rejected(
            "GRANT CREATE ON SCHEMA f0g TO f0d_runtime"
        )

    def test_catalog_rejects_runtime_prepare_execute_grant(self) -> None:
        self._assert_catalog_tamper_rejected(
            "GRANT EXECUTE ON FUNCTION "
            "f0g.prepare_annotation_workflow("
            "uuid,text,text,uuid[],uuid[],uuid,uuid,uuid,uuid) TO f0d_runtime"
        )

    def test_catalog_rejects_worker_list_execute_grant(self) -> None:
        self._assert_catalog_tamper_rejected(
            "GRANT EXECUTE ON FUNCTION f0g.list_assigned_work() TO f0d_worker"
        )

    def test_prepare_late_failure_rolls_back_all_database_capabilities(self) -> None:
        from platform_foundation.f0e.hashing import stable_uuid4
        from platform_foundation.f0g import (
            F0GError,
            GUIDELINE_SHA256,
            prepare_workflow,
        )

        name = (
            _FROZEN_F0_ISOLATION.database_name("f0g-case")
            if _FROZEN_F0_ISOLATION is not None
            else "f0g_test_" + uuid.uuid4().hex[:16]
        )
        config = _config(name)
        token_path = _token_path()
        _create_database(name, SOURCE_DATABASE)
        try:
            _upgrade(config)
            operator = authenticate_local_session(config, LOCAL_TENANT_A_TOKEN)
            guideline_id = stable_uuid4(
                "f0g-guideline-v1", operator.enterprise_id, GUIDELINE_SHA256
            )
            audit_id = stable_uuid4(
                "f0g-prepare-audit-v1", operator.enterprise_id, guideline_id
            )
            with psycopg.connect(config.migration_dsn) as connection:
                connection.execute(
                    "SELECT set_config('f0d.enterprise_id',%s,true),"
                    "set_config('f0d.actor_id',%s,true),"
                    "set_config('f0d.session_token_sha256',%s,true)",
                    (
                        str(operator.enterprise_id),
                        str(operator.actor_id),
                        operator.session_token_sha256,
                    ),
                )
                connection.execute(
                    "INSERT INTO f0d.audit_event("
                    "id,enterprise_id,actor_id,event_code,target_type,target_id,"
                    "correlation_id,outcome_code) VALUES "
                    "(%s,%s,%s,'F0G_WORKFLOW_PREPARED','ANNOTATION_GUIDELINE',"
                    "%s,%s,'SUCCESS')",
                    (
                        audit_id,
                        operator.enterprise_id,
                        operator.actor_id,
                        guideline_id,
                        guideline_id,
                    ),
                )
            with self.assertRaisesRegex(F0GError, "ANNOTATION_PREPARE_FAILED"):
                prepare_workflow(config, operator, token_path)
            with psycopg.connect(config.migration_dsn) as connection:
                connection.execute(
                    "SELECT set_config('f0d.enterprise_id',%s,true),"
                    "set_config('f0d.actor_id',%s,true),"
                    "set_config('f0d.session_token_sha256',%s,true)",
                    (
                        str(operator.enterprise_id),
                        str(operator.actor_id),
                        operator.session_token_sha256,
                    ),
                )
                row = connection.execute(
                    "SELECT "
                    "(SELECT count(*) FROM f0d.actor WHERE actor_kind='FIXTURE_VIEWER'),"
                    "(SELECT count(*) FROM f0d.enterprise_membership "
                    " WHERE role_code='FIXTURE_VIEWER'),"
                    "(SELECT count(*) FROM f0d.local_fixture_session AS session "
                    " JOIN f0d.actor ON actor.id=session.actor_id "
                    " WHERE actor.actor_kind='FIXTURE_VIEWER'),"
                    "(SELECT count(*) FROM f0g.annotation_guideline),"
                    "(SELECT count(*) FROM f0g.blind_assignment),"
                    "(SELECT count(*) FROM f0d.audit_event "
                    " WHERE event_code='F0G_WORKFLOW_PREPARED')"
                ).fetchone()
            self.assertEqual(row, (0, 0, 0, 0, 0, 1))
            metadata = os.lstat(token_path)
            self.assertEqual(
                (stat.S_IMODE(metadata.st_mode), metadata.st_size, metadata.st_nlink),
                (0o600, 96, 1),
            )
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(token_path)
            _drop_database(name)


if __name__ == "__main__":
    unittest.main()
