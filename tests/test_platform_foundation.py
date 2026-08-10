from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import tempfile
import threading
import traceback
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql

from platform_foundation.api import create_app
from platform_foundation.auth import (
    AuthenticationError,
    SessionContext,
    authenticate_local_session,
)
from platform_foundation.bootstrap import (
    BootstrapError,
    LOCAL_TENANT_A_TOKEN,
    LOCAL_TENANT_B_TOKEN,
    TENANT_A,
    TENANT_B,
    LocalPrincipal,
    _seed_principal,
    _seed_source,
    registry_source_id,
    seed_local_foundation,
)
from platform_foundation.catalog import load_catalog, open_catalog_source
from platform_foundation.database import (
    DatabaseConfig,
    database_health,
    role_transaction,
    tenant_transaction,
)
from platform_foundation.evidence import aggregate_evidence, processing_evidence
from platform_foundation.f0_isolation import load_frozen_f0_isolation
from platform_foundation.governance import (
    GovernanceDenied,
    UploadIntent,
    closed_readiness_snapshot,
    require_acceptance_gold_promotion,
    require_external_processing,
    require_production_entry,
    require_professional_publication,
    require_real_customer_upload,
    require_registered_fixture_upload,
    require_uat_entry,
)
from platform_foundation.replay import replay_profile
from platform_foundation.service import (
    JobLease,
    PlatformError,
    PlatformService,
    _stable_uuid4,
)
from platform_foundation.vault import LocalFixtureVault, VaultError, _write_once


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


def _database_config(database_name: str) -> DatabaseConfig:
    if _FROZEN_F0_ISOLATION is not None:
        allowed = {
            _FROZEN_F0_ISOLATION.database_name("f0d-test"),
            _FROZEN_F0_ISOLATION.database_name("f0d-upgrade"),
        }
        if database_name not in allowed:
            raise AssertionError("unsafe isolated F0D database name")
        return _FROZEN_F0_ISOLATION.database_config(database_name)
    base = "127.0.0.1:55432/" + database_name
    return DatabaseConfig(
        migration_dsn="postgresql://f0d_migration:f0d-migration-local-v01@" + base,
        runtime_dsn="postgresql://f0d_runtime:f0d-runtime-local-v01@" + base,
        worker_dsn="postgresql://f0d_worker:f0d-worker-local-v01@" + base,
    )


class GovernanceContractTests(unittest.TestCase):
    def test_readiness_snapshot_exactly_closed(self) -> None:
        value = closed_readiness_snapshot().to_dict()
        self.assertEqual(value["pilot_context"], "UNCONFIRMED")
        self.assertEqual(value["region_industry"], "UNCONFIRMED")
        self.assertEqual(value["benchmark_tier"], "NONE")
        self.assertEqual(value["external_processing_policy"], "DENY")
        self.assertEqual(value["professional_authority"], "UNASSIGNED")
        self.assertFalse(value["uat_allowed"])
        self.assertFalse(value["production_allowed"])

    def test_readiness_snapshot_copy_cannot_open_singleton(self) -> None:
        value = closed_readiness_snapshot().to_dict()
        value["production_allowed"] = True
        self.assertFalse(closed_readiness_snapshot().production_allowed)

    def test_registered_enum_fixture_is_only_allowed_intent(self) -> None:
        self.assertIsNone(
            require_registered_fixture_upload(
                UploadIntent.REGISTERED_LOCAL_FIXTURE
            )
        )

    def test_raw_string_cannot_bypass_fixture_intent(self) -> None:
        with self.assertRaises(GovernanceDenied):
            require_registered_fixture_upload("REGISTERED_LOCAL_FIXTURE")  # type: ignore[arg-type]

    def test_real_customer_upload_closed(self) -> None:
        with self.assertRaisesRegex(
            GovernanceDenied, "GOV_REAL_CUSTOMER_CONTEXT_UNCONFIRMED"
        ):
            require_real_customer_upload()

    def test_acceptance_gold_closed(self) -> None:
        with self.assertRaisesRegex(
            GovernanceDenied, "GOV_ACCEPTANCE_GOLD_UNAVAILABLE"
        ):
            require_acceptance_gold_promotion()

    def test_external_processing_closed(self) -> None:
        with self.assertRaisesRegex(
            GovernanceDenied, "GOV_EXTERNAL_PROCESSING_DENIED"
        ):
            require_external_processing()

    def test_professional_publication_closed(self) -> None:
        with self.assertRaisesRegex(
            GovernanceDenied, "GOV_PROFESSIONAL_AUTHORITY_UNASSIGNED"
        ):
            require_professional_publication()

    def test_uat_closed(self) -> None:
        with self.assertRaisesRegex(GovernanceDenied, "GOV_UAT_NOT_AUTHORIZED"):
            require_uat_entry()

    def test_production_closed(self) -> None:
        with self.assertRaisesRegex(
            GovernanceDenied, "GOV_PRODUCTION_NOT_AUTHORIZED"
        ):
            require_production_entry()


class CatalogEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.full = load_catalog("full")
        cls.smoke = load_catalog("smoke")

    def test_smoke_catalog_has_ten_registered_sources(self) -> None:
        self.assertEqual(len(self.smoke), 10)
        self.assertEqual(sum(entry.group == "negative" for entry in self.smoke), 2)

    def test_full_catalog_has_exact_registered_sources(self) -> None:
        self.assertEqual(len(self.full), 26)
        self.assertEqual(sum(entry.expected_size for entry in self.full), 41_878_200)

    def test_catalog_opaque_ids_are_stable(self) -> None:
        again = load_catalog("full")
        self.assertEqual(
            [entry.source_id for entry in self.full],
            [entry.source_id for entry in again],
        )

    def test_public_catalog_records_contain_no_source_path(self) -> None:
        payload = json.dumps(
            [entry.public_record() for entry in self.full], ensure_ascii=False
        )
        self.assertNotIn("relative_path", payload)
        self.assertNotIn("/Users/", payload)
        self.assertNotIn("环境demo", payload)

    def test_f0c_aggregate_is_exact(self) -> None:
        self.assertEqual(
            aggregate_evidence(self.full),
            {
                "documents": 26,
                "visual_units": 249,
                "native_candidates": 225,
                "ocr_candidates": 24,
                "manual_review_candidates": 0,
                "doc_deferred": 2,
            },
        )

    def test_every_visual_unit_has_one_candidate_decision(self) -> None:
        units = [
            unit
            for entry in self.full
            for unit in processing_evidence(entry).units
        ]
        self.assertEqual(len(units), 249)
        self.assertTrue(all(unit.decision for unit in units))
        self.assertEqual(len({unit.source_unit_id for unit in units}), 249)

    def test_registered_source_fd_hashes_without_offset_mutation(self) -> None:
        entry = self.full[0]
        with open_catalog_source(entry) as descriptor:
            before = os.lseek(descriptor, 0, os.SEEK_CUR)
            body = os.pread(descriptor, entry.expected_size, 0)
            after = os.lseek(descriptor, 0, os.SEEK_CUR)
        self.assertEqual(before, after)
        self.assertEqual(hashlib.sha256(body).hexdigest(), entry.expected_sha256)


class VaultContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="f0d-vault-test-", dir=_PRIVATE_TMP
        )
        self.vault = LocalFixtureVault(self.temporary.name)

    def tearDown(self) -> None:
        self.vault.close()
        self.temporary.cleanup()

    def _assert_fifo_call_returns(self, fifo: Path, call: object) -> object:
        started = threading.Event()
        finished = threading.Event()
        result: list[object] = []

        def run() -> None:
            started.set()
            try:
                result.append(call())  # type: ignore[operator]
            except Exception as exc:
                result.append(exc)
            finally:
                finished.set()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(started.wait(1))
        returned_without_writer = finished.wait(0.5)
        if not returned_without_writer:
            writer = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
            os.close(writer)
            self.assertTrue(finished.wait(1))
        self.assertTrue(returned_without_writer, "vault blocked opening a FIFO")
        return result[0]

    def test_root_and_child_modes_are_private(self) -> None:
        self.assertEqual(stat.S_IMODE(os.stat(self.temporary.name).st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE(os.stat(Path(self.temporary.name) / "staging").st_mode),
            0o700,
        )
        self.assertEqual(
            stat.S_IMODE(os.stat(Path(self.temporary.name) / "final").st_mode),
            0o700,
        )

    def test_store_and_verify_bytes(self) -> None:
        stored = self.vault.store_bytes(b"fixture-bytes")
        self.assertEqual(self.vault.verify(stored.object_id, stored.sha256, stored.size), stored)

    def test_chunk_boundaries_do_not_change_identity(self) -> None:
        stored = self.vault.store_chunks((b"fixture-", b"chunks"))
        self.assertEqual(stored.sha256, hashlib.sha256(b"fixture-chunks").hexdigest())

    def test_async_chunks_are_written_incrementally_without_body_aggregation(self) -> None:
        observed_sizes: list[int] = []

        async def chunks() -> object:
            yield b"first"
            names = os.listdir(Path(self.temporary.name) / "staging")
            observed_sizes.append(
                os.stat(Path(self.temporary.name) / "staging" / names[0]).st_size
            )
            yield b"second"

        staged = asyncio.run(
            self.vault.stage_async_chunks(chunks(), maximum_size=11)  # type: ignore[arg-type]
        )
        self.assertEqual(observed_sizes, [5])
        self.assertEqual(staged.size, 11)
        self.vault.discard(staged)

    def test_async_size_limit_cleans_partial_staging(self) -> None:
        async def chunks() -> object:
            yield b"first"
            yield b"second"

        with self.assertRaisesRegex(VaultError, "CONTENT_TOO_LARGE"):
            asyncio.run(
                self.vault.stage_async_chunks(chunks(), maximum_size=10)  # type: ignore[arg-type]
            )
        self.assertEqual(os.listdir(Path(self.temporary.name) / "staging"), [])

    def test_read_only_fd_offset_is_preserved(self) -> None:
        source = Path(self.temporary.name) / "source"
        source.write_bytes(b"read-only-fixture")
        os.chmod(source, 0o400)
        descriptor = os.open(source, os.O_RDONLY)
        try:
            os.lseek(descriptor, 3, os.SEEK_SET)
            self.vault.store_fd(descriptor)
            self.assertEqual(os.lseek(descriptor, 0, os.SEEK_CUR), 3)
        finally:
            os.close(descriptor)

    def test_writable_source_fd_is_rejected(self) -> None:
        source = Path(self.temporary.name) / "source-writable"
        source.write_bytes(b"fixture")
        descriptor = os.open(source, os.O_RDWR)
        try:
            with self.assertRaises(VaultError):
                self.vault.stage_fd(descriptor)
        finally:
            os.close(descriptor)

    def test_hardlinked_source_is_rejected(self) -> None:
        source = Path(self.temporary.name) / "source-hardlink"
        linked = Path(self.temporary.name) / "source-hardlink-2"
        source.write_bytes(b"fixture")
        os.link(source, linked)
        descriptor = os.open(source, os.O_RDONLY)
        try:
            with self.assertRaises(VaultError):
                self.vault.stage_fd(descriptor)
        finally:
            os.close(descriptor)

    def test_fifo_source_is_rejected(self) -> None:
        fifo = Path(self.temporary.name) / "source-fifo"
        os.mkfifo(fifo, 0o600)
        descriptor = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        try:
            with self.assertRaises(VaultError):
                self.vault.stage_fd(descriptor)
        finally:
            os.close(descriptor)

    def test_symlink_root_is_rejected_without_path_in_error(self) -> None:
        target = tempfile.mkdtemp(prefix="f0d-target-", dir=_PRIVATE_TMP)
        link = Path(_PRIVATE_TMP) / f"f0d-link-{uuid.uuid4().hex}"
        try:
            os.symlink(target, link)
            with self.assertRaises(VaultError) as caught:
                LocalFixtureVault(str(link))
            self.assertNotIn(str(link), str(caught.exception))
        finally:
            os.unlink(link)
            os.rmdir(target)

    def test_stage_recovers_across_vault_instances(self) -> None:
        staged = self.vault.stage_bytes(b"recoverable")
        self.vault.close()
        self.vault = LocalFixtureVault(self.temporary.name)
        recovered = self.vault.recover_stage(
            staged.stage_id, staged.sha256, staged.size
        )
        stored = self.vault.promote_as(recovered, uuid.uuid4().hex)
        self.assertEqual(stored.sha256, staged.sha256)

    def test_promote_same_object_is_idempotent(self) -> None:
        staged = self.vault.stage_bytes(b"idempotent")
        object_id = uuid.uuid4().hex
        first = self.vault.promote_as(staged, object_id)
        second = self.vault.promote_as(staged, object_id)
        self.assertEqual(first, second)
        self.assertEqual(self.vault.final_count(), 1)

    def test_existing_different_object_is_conflict(self) -> None:
        object_id = uuid.uuid4().hex
        first = self.vault.stage_bytes(b"first")
        self.vault.promote_as(first, object_id)
        second = self.vault.stage_bytes(b"second")
        with self.assertRaisesRegex(VaultError, "FINAL_CONFLICT"):
            self.vault.promote_as(second, object_id)

    def test_iterable_failure_cleans_staging(self) -> None:
        def broken() -> object:
            yield b"partial"
            raise RuntimeError("body canary must not escape")

        with self.assertRaisesRegex(VaultError, "SOURCE_READ_FAILED"):
            self.vault.stage_chunks(broken())  # type: ignore[arg-type]
        self.assertEqual(os.listdir(Path(self.temporary.name) / "staging"), [])

    def test_iterable_failure_traceback_does_not_echo_body(self) -> None:
        canary = "private-body-traceback-canary"

        def broken() -> object:
            yield b"partial"
            raise RuntimeError(canary)

        try:
            self.vault.stage_chunks(broken())  # type: ignore[arg-type]
        except VaultError as exc:
            rendered = "".join(traceback.format_exception(exc))
        else:
            self.fail("expected VaultError")
        self.assertNotIn(canary, rendered)

    def test_recover_stage_fifo_name_does_not_block(self) -> None:
        stage_id = uuid.uuid4().hex
        fifo = Path(self.temporary.name) / "staging" / stage_id
        os.mkfifo(fifo, 0o600)
        result = self._assert_fifo_call_returns(
            fifo,
            lambda: self.vault.recover_stage(stage_id, "0" * 64, 0),
        )
        self.assertIsInstance(result, VaultError)

    def test_verify_final_fifo_name_does_not_block(self) -> None:
        object_id = uuid.uuid4().hex
        fifo = Path(self.temporary.name) / "final" / object_id
        os.mkfifo(fifo, 0o600)
        result = self._assert_fifo_call_returns(
            fifo,
            lambda: self.vault.verify(object_id, "0" * 64, 0),
        )
        self.assertIsInstance(result, VaultError)

    def test_final_count_fifo_name_does_not_block(self) -> None:
        object_id = uuid.uuid4().hex
        fifo = Path(self.temporary.name) / "final" / object_id
        os.mkfifo(fifo, 0o600)
        result = self._assert_fifo_call_returns(fifo, self.vault.final_count)
        self.assertEqual(result, 0)

    def test_verify_recovers_interrupted_promotion_hardlink(self) -> None:
        payload = b"interrupted-promotion"
        digest = hashlib.sha256(payload).hexdigest()
        original = self.vault.stage_bytes(b"independent-stage")
        promotion_name = uuid.uuid4().hex
        object_id = uuid.uuid4().hex
        staging = Path(self.temporary.name) / "staging"
        final = Path(self.temporary.name) / "final"
        promotion = staging / promotion_name
        promotion.write_bytes(payload)
        os.chmod(promotion, 0o600)
        os.link(promotion, final / object_id)
        self.assertEqual(os.stat(promotion).st_nlink, 2)

        stored = self.vault.verify(object_id, digest, len(payload))

        self.assertEqual(stored.object_id, object_id)
        self.assertFalse(promotion.exists())
        self.assertEqual(os.stat(final / object_id).st_nlink, 1)
        self.assertTrue((staging / original.stage_id).exists())

    def test_short_write_is_detected_without_mocking(self) -> None:
        read_fd, write_fd = os.pipe()
        try:
            os.set_blocking(write_fd, False)
            with self.assertRaisesRegex(VaultError, "SHORT_WRITE"):
                _write_once(write_fd, memoryview(b"x" * (1024 * 1024)))
        finally:
            os.close(write_fd)
            os.close(read_fd)

    def test_final_symlink_is_never_followed(self) -> None:
        object_id = uuid.uuid4().hex
        target = Path(self.temporary.name) / "target"
        target.write_bytes(b"target")
        os.symlink(target, Path(self.temporary.name) / "final" / object_id)
        with self.assertRaises(VaultError):
            self.vault.verify(object_id, hashlib.sha256(b"target").hexdigest(), 6)

    def test_final_hardlink_is_rejected(self) -> None:
        object_id = uuid.uuid4().hex
        target = Path(self.temporary.name) / "target-hardlink"
        target.write_bytes(b"target")
        os.chmod(target, 0o600)
        os.link(target, Path(self.temporary.name) / "final" / object_id)
        with self.assertRaisesRegex(VaultError, "OBJECT_LINK_COUNT_INVALID"):
            self.vault.verify(object_id, hashlib.sha256(b"target").hexdigest(), 6)

    def test_invalid_final_name_is_rejected(self) -> None:
        with self.assertRaisesRegex(VaultError, "INVALID_OBJECT_ID"):
            self.vault.verify("../not-opaque", "0" * 64, 1)

    def test_closed_vault_rejects_operations(self) -> None:
        self.vault.close()
        with self.assertRaisesRegex(VaultError, "VAULT_CLOSED"):
            self.vault.final_count()


class PostgreSQLFoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.database_name = (
            _FROZEN_F0_ISOLATION.database_name("f0d-test")
            if _FROZEN_F0_ISOLATION is not None
            else f"f0d_test_{uuid.uuid4().hex[:16]}"
        )
        admin = psycopg.connect(BOOTSTRAP_DSN, autocommit=True)
        try:
            admin.execute(
                sql.SQL("CREATE DATABASE {} OWNER f0d_migration TEMPLATE template0").format(
                    sql.Identifier(cls.database_name)
                )
            )
        finally:
            admin.close()
        cls.database_admin_dsn = (
            BOOTSTRAP_DSN.rsplit("/", 1)[0] + "/" + cls.database_name
        )
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
        cls.config = _database_config(cls.database_name)
        previous = os.environ.get("F0D_MIGRATION_DSN")
        os.environ["F0D_MIGRATION_DSN"] = cls.config.migration_dsn.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
        try:
            command.upgrade(Config("alembic.ini"), "head")
        finally:
            if previous is None:
                os.environ.pop("F0D_MIGRATION_DSN", None)
            else:
                os.environ["F0D_MIGRATION_DSN"] = previous
        cls.catalog = load_catalog("full")
        seed_local_foundation(cls.config)

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

    def _principal_with_sources(
        self, count: int = 1
    ) -> tuple[LocalPrincipal, SessionContext, list[uuid.UUID]]:
        principal = LocalPrincipal(
            enterprise_id=uuid.uuid4(),
            actor_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            token=f"fixture_test_token_{uuid.uuid4().hex}",
        )
        entries = list(self.catalog[:count])
        with role_transaction(self.config, "f0d_migration") as connection:
            with connection.cursor() as cursor:
                _seed_principal(
                    cursor,
                    principal,
                    label=f"TEST_{principal.enterprise_id.hex[:12].upper()}",
                    data_context="SYNTHETIC_CANARY",
                    fixture_set_id=None,
                    fixture_version=None,
                )
                for entry in entries:
                    _seed_source(cursor, principal, entry)
        context = authenticate_local_session(self.config, principal.token)
        return (
            principal,
            context,
            [
                registry_source_id(context.enterprise_id, entry.document_id)
                for entry in entries
            ],
        )

    def _service(self) -> tuple[PlatformService, LocalFixtureVault, tempfile.TemporaryDirectory[str]]:
        temporary = tempfile.TemporaryDirectory(
            prefix="f0d-service-test-", dir=_PRIVATE_TMP
        )
        vault = LocalFixtureVault(temporary.name)
        service = PlatformService(self.config, vault, catalog=self.catalog)
        self.addCleanup(temporary.cleanup)
        self.addCleanup(vault.close)
        return service, vault, temporary

    def _completed_pipeline(
        self,
    ) -> tuple[PlatformService, SessionContext, uuid.UUID, LocalFixtureVault]:
        _principal, context, sources = self._principal_with_sources(1)
        service, vault, _temporary = self._service()
        upload = service.create_upload(context, sources[0], "pipeline-create-001")
        service.store_catalog_content(context, upload.upload_id)
        service.complete_upload(context, upload.upload_id, "pipeline-complete-001")
        return service, context, upload.upload_id, vault

    def test_postgresql_major_is_18(self) -> None:
        health = database_health(self.config)
        self.assertEqual(health["status"], "OK")
        self.assertEqual(health["postgresql_major"], 18)

    def test_principal_replay_rejects_poisoned_role(self) -> None:
        principal, _context, _sources = self._principal_with_sources(0)
        with psycopg.connect(self.database_admin_dsn) as connection:
            connection.execute(
                "UPDATE f0d.enterprise_membership SET role_code='FIXTURE_VIEWER' "
                "WHERE enterprise_id=%s AND actor_id=%s",
                (principal.enterprise_id, principal.actor_id),
            )
        with self.assertRaisesRegex(BootstrapError, "LOCAL_PRINCIPAL_MISMATCH"):
            with role_transaction(self.config, "f0d_migration") as connection:
                with connection.cursor() as cursor:
                    _seed_principal(
                        cursor,
                        principal,
                        label=f"TEST_{principal.enterprise_id.hex[:12].upper()}",
                        data_context="SYNTHETIC_CANARY",
                        fixture_set_id=None,
                        fixture_version=None,
                    )

    def test_source_replay_rejects_poisoned_fixture_version(self) -> None:
        principal, _context, _sources = self._principal_with_sources(0)
        entry = self.catalog[0]
        with psycopg.connect(self.database_admin_dsn) as connection:
            connection.execute(
                "INSERT INTO f0d.fixture_source_registry("
                "id,enterprise_id,source_document_id,fixture_set_id,fixture_version,"
                "source_group,source_line,expected_sha256,expected_size_bytes,"
                "document_type,corpus_role,enterprise_fact_allowed,"
                "current_regulation_allowed,search_publish_allowed) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,false,false)",
                (
                    registry_source_id(principal.enterprise_id, entry.document_id),
                    principal.enterprise_id,
                    entry.document_id,
                    entry.fixture_set_id,
                    "poison-v0.1",
                    entry.group,
                    entry.line,
                    entry.expected_sha256,
                    entry.expected_size,
                    entry.document_type,
                    entry.corpus_role,
                    entry.enterprise_fact_allowed,
                ),
            )
        with self.assertRaisesRegex(BootstrapError, "LOCAL_SOURCE_MISMATCH"):
            with role_transaction(self.config, "f0d_migration") as connection:
                with connection.cursor() as cursor:
                    _seed_source(cursor, principal, entry)

    def test_existing_v1_version_is_backfilled_by_security_migration(self) -> None:
        database_name = (
            _FROZEN_F0_ISOLATION.database_name("f0d-upgrade")
            if _FROZEN_F0_ISOLATION is not None
            else f"f0d_upgrade_{uuid.uuid4().hex[:16]}"
        )
        bootstrap_root = BOOTSTRAP_DSN.rsplit("/", 1)[0]
        admin = psycopg.connect(BOOTSTRAP_DSN, autocommit=True)
        try:
            admin.execute(
                sql.SQL("CREATE DATABASE {} OWNER f0d_migration TEMPLATE template0").format(
                    sql.Identifier(database_name)
                )
            )
        finally:
            admin.close()
        admin_dsn = f"{bootstrap_root}/{database_name}"
        config = _database_config(database_name)
        migration_dsn = config.migration_dsn
        previous = os.environ.get("F0D_MIGRATION_DSN")
        try:
            admin = psycopg.connect(admin_dsn, autocommit=True)
            try:
                admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
                admin.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO f0d_runtime, f0d_worker").format(
                        sql.Identifier(database_name)
                    )
                )
                admin.execute("CREATE SCHEMA f0d AUTHORIZATION f0d_migration")
                admin.execute("REVOKE ALL ON SCHEMA f0d FROM PUBLIC")
            finally:
                admin.close()
            os.environ["F0D_MIGRATION_DSN"] = migration_dsn.replace(
                "postgresql://", "postgresql+psycopg://", 1
            )
            command.upgrade(Config("alembic.ini"), "f0d_0001")
            seed_local_foundation(config)

            entry = self.catalog[0]
            upload_id = uuid.uuid4()
            blob_id = uuid.uuid4()
            document_id = uuid.uuid4()
            version_id = uuid.uuid4()
            admin = psycopg.connect(admin_dsn, autocommit=True)
            try:
                admin.execute(
                    "INSERT INTO f0d.upload_session("
                    "id,enterprise_id,actor_id,source_document_id,expected_sha256,"
                    "expected_size_bytes,quarantine_object_key,status,captured_sha256,"
                    "captured_size_bytes,completed_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,'COMPLETED',%s,%s,statement_timestamp())",
                    (
                        upload_id,
                        TENANT_A.enterprise_id,
                        TENANT_A.actor_id,
                        entry.document_id,
                        entry.expected_sha256,
                        entry.expected_size,
                        uuid.uuid4().hex,
                        entry.expected_sha256,
                        entry.expected_size,
                    ),
                )
                admin.execute(
                    "INSERT INTO f0d.object_blob("
                    "id,enterprise_id,upload_session_id,object_key,object_version_id,"
                    "sha256,size_bytes) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        blob_id,
                        TENANT_A.enterprise_id,
                        upload_id,
                        uuid.uuid4().hex,
                        uuid.uuid4(),
                        entry.expected_sha256,
                        entry.expected_size,
                    ),
                )
                admin.execute(
                    "INSERT INTO f0d.document(id,enterprise_id,source_document_id) "
                    "VALUES (%s,%s,%s)",
                    (document_id, TENANT_A.enterprise_id, entry.document_id),
                )
                admin.execute(
                    "INSERT INTO f0d.document_version("
                    "id,enterprise_id,document_id,object_blob_id,source_document_id,version_no) "
                    "VALUES (%s,%s,%s,%s,%s,1)",
                    (
                        version_id,
                        TENANT_A.enterprise_id,
                        document_id,
                        blob_id,
                        entry.document_id,
                    ),
                )
            finally:
                admin.close()

            command.upgrade(Config("alembic.ini"), "head")
            admin = psycopg.connect(admin_dsn, autocommit=True)
            try:
                row = admin.execute(
                    "SELECT upload_session_id FROM f0d.document_version WHERE id=%s",
                    (version_id,),
                ).fetchone()
                trigger = admin.execute(
                    "SELECT tgenabled FROM pg_trigger WHERE tgrelid='f0d.document_version'::regclass "
                    "AND tgname='reject_immutable_mutation'"
                ).fetchone()
            finally:
                admin.close()
            self.assertEqual(row[0], upload_id)
            self.assertEqual(trigger[0], "O")
        finally:
            if previous is None:
                os.environ.pop("F0D_MIGRATION_DSN", None)
            else:
                os.environ["F0D_MIGRATION_DSN"] = previous
            admin = psycopg.connect(BOOTSTRAP_DSN, autocommit=True)
            try:
                admin.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                        sql.Identifier(database_name)
                    )
                )
            finally:
                admin.close()

    def test_runtime_roles_have_no_high_privileges(self) -> None:
        with role_transaction(self.config, "f0d_migration") as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolbypassrls "
                    "FROM pg_roles WHERE rolname IN "
                    "('f0d_migration','f0d_runtime','f0d_worker')"
                )
                rows = cursor.fetchall()
        self.assertEqual(len(rows), 3)
        self.assertTrue(
            all(
                not row[flag]
                for row in rows
                for flag in ("rolsuper", "rolcreatedb", "rolcreaterole", "rolbypassrls")
            )
        )

    def test_fourteen_tenant_tables_force_rls(self) -> None:
        with role_transaction(self.config, "f0d_migration") as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) AS count FROM pg_class c JOIN pg_namespace n "
                    "ON n.oid=c.relnamespace WHERE n.nspname='f0d' AND c.relkind='r' "
                    "AND c.relrowsecurity AND c.relforcerowsecurity"
                )
                count = cursor.fetchone()["count"]
        self.assertEqual(count, 14)

    def test_missing_tenant_context_denies_rows(self) -> None:
        with role_transaction(self.config, "f0d_runtime") as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) AS count FROM f0d.enterprise")
                count = cursor.fetchone()["count"]
        self.assertEqual(count, 0)

    def test_session_authentication_maps_server_side_context(self) -> None:
        context = authenticate_local_session(self.config, LOCAL_TENANT_A_TOKEN)
        self.assertEqual(context.enterprise_id, TENANT_A.enterprise_id)
        self.assertEqual(context.actor_id, TENANT_A.actor_id)
        self.assertEqual(
            context.session_token_sha256,
            hashlib.sha256(LOCAL_TENANT_A_TOKEN.encode("utf-8")).hexdigest(),
        )
        self.assertNotIn(context.session_token_sha256, repr(context))

    def test_invalid_session_token_fails_closed(self) -> None:
        with self.assertRaises(AuthenticationError):
            authenticate_local_session(
                self.config, "fixture_invalid_token_000000000000"
            )

    def test_a_cannot_read_b_enterprise(self) -> None:
        context = authenticate_local_session(self.config, LOCAL_TENANT_A_TOKEN)
        with tenant_transaction(
            self.config, "f0d_runtime", context
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) AS count FROM f0d.enterprise WHERE id=%s",
                    (TENANT_B.enterprise_id,),
                )
                count = cursor.fetchone()["count"]
        self.assertEqual(count, 0)

    def test_synthetic_canary_tenant_has_no_real_fixture_sources(self) -> None:
        context = authenticate_local_session(self.config, LOCAL_TENANT_B_TOKEN)
        with tenant_transaction(self.config, "f0d_runtime", context) as connection:
            count = connection.execute(
                "SELECT count(*) AS count FROM f0d.fixture_source_registry"
            ).fetchone()["count"]
        self.assertEqual(count, 0)

    def test_transaction_local_context_does_not_survive_rollback(self) -> None:
        context = authenticate_local_session(self.config, LOCAL_TENANT_A_TOKEN)
        connection = psycopg.connect(self.config.runtime_dsn)
        try:
            with connection.transaction():
                connection.execute(
                    "SELECT set_config('f0d.enterprise_id', %s, true),"
                    "set_config('f0d.actor_id', %s, true),"
                    "set_config('f0d.session_token_sha256', %s, true)",
                    (
                        str(context.enterprise_id),
                        str(context.actor_id),
                        context.session_token_sha256,
                    ),
                )
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM f0d.enterprise").fetchone()[0],
                    1,
                )
            with connection.transaction():
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM f0d.enterprise").fetchone()[0],
                    0,
                )
        finally:
            connection.close()

    def test_runtime_cannot_write_auth_session_table(self) -> None:
        context = authenticate_local_session(self.config, LOCAL_TENANT_A_TOKEN)
        with self.assertRaises(Exception):
            with tenant_transaction(
                self.config,
                "f0d_runtime",
                context,
            ) as connection:
                connection.execute(
                    "INSERT INTO f0d.local_fixture_session("
                    "id,enterprise_id,actor_id,token_sha256,expires_at) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (
                        uuid.uuid4(),
                        TENANT_A.enterprise_id,
                        TENANT_A.actor_id,
                        "a" * 64,
                        datetime(2099, 1, 1, tzinfo=timezone.utc),
                    ),
                )

    def test_auth_tables_and_global_actor_are_not_directly_granted(self) -> None:
        with role_transaction(self.config, "f0d_migration") as connection:
            row = connection.execute(
                "SELECT "
                "has_table_privilege('f0d_runtime','f0d.local_fixture_session','SELECT') "
                "AS runtime_select,"
                "has_table_privilege('f0d_runtime','f0d.local_fixture_session','INSERT') "
                "AS runtime_insert,"
                "has_table_privilege('f0d_runtime','f0d.local_fixture_session','UPDATE') "
                "AS runtime_update,"
                "has_table_privilege('f0d_worker','f0d.local_fixture_session','SELECT') "
                "AS worker_select,"
                "has_table_privilege('f0d_runtime','f0d.actor','SELECT') AS runtime_actor_select,"
                "has_table_privilege('f0d_worker','f0d.actor','SELECT') AS worker_actor_select,"
                "has_table_privilege('f0d_runtime','f0d.audit_event','INSERT') AS runtime_audit_insert,"
                "has_table_privilege('f0d_runtime','f0d.outbox_event','INSERT') AS runtime_outbox_insert"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertFalse(any(row.values()))

    def test_identity_columns_are_not_updateable_by_runtime_roles(self) -> None:
        with role_transaction(self.config, "f0d_migration") as connection:
            row = connection.execute(
                "SELECT "
                "has_column_privilege('f0d_runtime','f0d.upload_session',"
                "'expected_sha256','UPDATE') AS runtime_expected_hash,"
                "has_column_privilege('f0d_runtime','f0d.upload_session',"
                "'source_document_id','UPDATE') AS runtime_source,"
                "has_column_privilege('f0d_worker','f0d.upload_session',"
                "'expected_size_bytes','UPDATE') AS worker_expected_size,"
                "has_column_privilege('f0d_worker','f0d.job',"
                "'document_version_id','UPDATE') AS worker_job_target,"
                "has_column_privilege('f0d_runtime','f0d.upload_session',"
                "'status','INSERT') AS runtime_insert_status"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertFalse(any(row.values()))

    def test_spliced_context_is_rejected_before_transaction_yield(self) -> None:
        a = authenticate_local_session(self.config, LOCAL_TENANT_A_TOKEN)
        b = authenticate_local_session(self.config, LOCAL_TENANT_B_TOKEN)
        for role in ("f0d_runtime", "f0d_worker"):
            for context in (
                SessionContext(b.enterprise_id, a.actor_id, a.session_token_sha256),
                SessionContext(b.enterprise_id, b.actor_id, a.session_token_sha256),
            ):
                with self.subTest(role=role, actor=context.actor_id):
                    with self.assertRaises(Exception):
                        with tenant_transaction(self.config, role, context):  # type: ignore[arg-type]
                            self.fail("spliced context yielded a transaction")

    def test_revoked_membership_invalidates_existing_context(self) -> None:
        principal, context, _sources = self._principal_with_sources(0)
        admin = psycopg.connect(self.database_admin_dsn, autocommit=True)
        try:
            admin.execute(
                "UPDATE f0d.enterprise_membership SET status='REVOKED' "
                "WHERE enterprise_id=%s AND actor_id=%s",
                (principal.enterprise_id, principal.actor_id),
            )
            with self.assertRaises(Exception):
                with tenant_transaction(self.config, "f0d_runtime", context):
                    self.fail("revoked membership yielded a transaction")
        finally:
            admin.execute(
                "UPDATE f0d.enterprise_membership SET status='ACTIVE' "
                "WHERE enterprise_id=%s AND actor_id=%s",
                (principal.enterprise_id, principal.actor_id),
            )
            admin.close()

    def test_revoked_actor_invalidates_existing_context(self) -> None:
        principal, context, _sources = self._principal_with_sources(0)
        admin = psycopg.connect(self.database_admin_dsn, autocommit=True)
        try:
            admin.execute(
                "UPDATE f0d.actor SET status='REVOKED' WHERE id=%s",
                (principal.actor_id,),
            )
            with self.assertRaises(Exception):
                with tenant_transaction(self.config, "f0d_worker", context):
                    self.fail("revoked actor yielded a transaction")
        finally:
            admin.execute(
                "UPDATE f0d.actor SET status='ACTIVE' WHERE id=%s",
                (principal.actor_id,),
            )
            admin.close()

    def test_revoked_session_invalidates_existing_context(self) -> None:
        principal, context, _sources = self._principal_with_sources(0)
        admin = psycopg.connect(self.database_admin_dsn, autocommit=True)
        try:
            admin.execute(
                "UPDATE f0d.local_fixture_session "
                "SET revoked_at=statement_timestamp() WHERE id=%s",
                (principal.session_id,),
            )
            with self.assertRaises(Exception):
                with tenant_transaction(self.config, "f0d_runtime", context):
                    self.fail("revoked session yielded a transaction")
        finally:
            admin.execute(
                "UPDATE f0d.local_fixture_session SET revoked_at=NULL WHERE id=%s",
                (principal.session_id,),
            )
            admin.close()

    def test_expired_session_invalidates_existing_context(self) -> None:
        principal, context, _sources = self._principal_with_sources(0)
        admin = psycopg.connect(self.database_admin_dsn, autocommit=True)
        try:
            admin.execute(
                "UPDATE f0d.local_fixture_session "
                "SET expires_at=issued_at + interval '1 microsecond' WHERE id=%s",
                (principal.session_id,),
            )
            with self.assertRaises(Exception):
                with tenant_transaction(self.config, "f0d_worker", context):
                    self.fail("expired session yielded a transaction")
        finally:
            admin.execute(
                "UPDATE f0d.local_fixture_session "
                "SET expires_at='2099-01-01T00:00:00Z' WHERE id=%s",
                (principal.session_id,),
            )
            admin.close()

    def test_rls_rechecks_context_after_enterprise_and_actor_splice(self) -> None:
        a = authenticate_local_session(self.config, LOCAL_TENANT_A_TOKEN)
        b = authenticate_local_session(self.config, LOCAL_TENANT_B_TOKEN)
        for role in ("f0d_runtime", "f0d_worker"):
            with self.subTest(role=role):
                with tenant_transaction(self.config, role, a) as connection:  # type: ignore[arg-type]
                    connection.execute(
                        "SELECT set_config('f0d.enterprise_id', %s, true)",
                        (str(b.enterprise_id),),
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT count(*) AS count FROM f0d.enterprise"
                        ).fetchone()["count"],
                        0,
                    )
                    connection.execute(
                        "SELECT set_config('f0d.actor_id', %s, true)",
                        (str(b.actor_id),),
                    )
                    self.assertEqual(
                        connection.execute(
                            "SELECT count(*) AS count FROM f0d.enterprise"
                        ).fetchone()["count"],
                        0,
                    )

    def test_rls_rejects_insert_after_context_splice(self) -> None:
        a = authenticate_local_session(self.config, LOCAL_TENANT_A_TOKEN)
        b = authenticate_local_session(self.config, LOCAL_TENANT_B_TOKEN)
        for role, dsn in (
            ("f0d_runtime", self.config.runtime_dsn),
            ("f0d_worker", self.config.worker_dsn),
        ):
            with self.subTest(role=role):
                connection = psycopg.connect(dsn)
                try:
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                        with connection.transaction():
                            connection.execute(
                                "SELECT set_config('f0d.enterprise_id', %s, true),"
                                "set_config('f0d.actor_id', %s, true),"
                                "set_config('f0d.session_token_sha256', %s, true)",
                                (
                                    str(b.enterprise_id),
                                    str(b.actor_id),
                                    a.session_token_sha256,
                                ),
                            )
                            connection.execute(
                                "INSERT INTO f0d.idempotency_record("
                                "id,enterprise_id,actor_id,method,route_code,"
                                "idempotency_key_sha256,request_sha256) "
                                "VALUES (%s,%s,%s,'POST','SPLICE_TEST',%s,%s)",
                                (
                                    uuid.uuid4(),
                                    b.enterprise_id,
                                    b.actor_id,
                                    "a" * 64,
                                    "b" * 64,
                                ),
                            )
                finally:
                    connection.close()

    def test_audit_actor_must_belong_to_same_enterprise(self) -> None:
        connection = psycopg.connect(self.database_admin_dsn)
        try:
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                with connection.transaction():
                    connection.execute(
                        "INSERT INTO f0d.audit_event("
                        "id,enterprise_id,actor_id,event_code,target_type,target_id,"
                        "correlation_id,outcome_code) "
                        "VALUES (%s,%s,%s,'CROSS_ACTOR','CANARY',%s,%s,'DENIED')",
                        (
                            uuid.uuid4(),
                            TENANT_A.enterprise_id,
                            TENANT_B.actor_id,
                            uuid.uuid4(),
                            uuid.uuid4(),
                        ),
                    )
        finally:
            connection.close()

    def test_authenticated_actor_cannot_spoof_same_tenant_audit_actor(self) -> None:
        principal, context, _sources = self._principal_with_sources(0)
        other_actor = uuid.uuid4()
        admin = psycopg.connect(self.database_admin_dsn, autocommit=True)
        try:
            admin.execute(
                "INSERT INTO f0d.actor(id,actor_kind) VALUES (%s,'FIXTURE_VIEWER')",
                (other_actor,),
            )
            admin.execute(
                "INSERT INTO f0d.enterprise_membership("
                "enterprise_id,actor_id,role_code) VALUES (%s,%s,'FIXTURE_VIEWER')",
                (principal.enterprise_id, other_actor),
            )
        finally:
            admin.close()
        connection = psycopg.connect(self.config.worker_dsn)
        try:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                with connection.transaction():
                    connection.execute(
                        "SELECT set_config('f0d.enterprise_id', %s, true),"
                        "set_config('f0d.actor_id', %s, true),"
                        "set_config('f0d.session_token_sha256', %s, true)",
                        (
                            str(context.enterprise_id),
                            str(context.actor_id),
                            context.session_token_sha256,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO f0d.audit_event("
                        "id,enterprise_id,actor_id,event_code,target_type,target_id,"
                        "correlation_id,outcome_code) "
                        "VALUES (%s,%s,%s,'SAME_TENANT_SPOOF','CANARY',%s,%s,'DENIED')",
                        (
                            uuid.uuid4(),
                            principal.enterprise_id,
                            other_actor,
                            uuid.uuid4(),
                            uuid.uuid4(),
                        ),
                    )
        finally:
            connection.close()

    def test_all_five_database_gates_are_closed(self) -> None:
        with role_transaction(self.config, "f0d_runtime") as connection:
            rows = connection.execute(
                "SELECT code,status FROM f0d.capability_gate ORDER BY code"
            ).fetchall()
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(row["status"] == "CLOSED" for row in rows))

    def test_database_gate_is_immutable(self) -> None:
        with self.assertRaises(Exception):
            with role_transaction(self.config, "f0d_migration") as connection:
                connection.execute(
                    "UPDATE f0d.capability_gate SET status='CLOSED' "
                    "WHERE code='ACCEPTANCE_GOLD'"
                )

    def test_runtime_cannot_create_table(self) -> None:
        with self.assertRaises(Exception):
            with role_transaction(self.config, "f0d_runtime") as connection:
                connection.execute("CREATE TABLE f0d.runtime_must_not_create(id int)")

    def test_runtime_cannot_truncate(self) -> None:
        context = authenticate_local_session(self.config, LOCAL_TENANT_A_TOKEN)
        with self.assertRaises(Exception):
            with tenant_transaction(
                self.config,
                "f0d_runtime",
                context,
            ) as connection:
                connection.execute("TRUNCATE f0d.upload_session")

    def test_create_upload_idempotency_returns_same_session(self) -> None:
        _principal, context, sources = self._principal_with_sources(1)
        service, _vault, _temporary = self._service()
        first = service.create_upload(context, sources[0], "create-idem-001")
        second = service.create_upload(context, sources[0], "create-idem-001")
        self.assertEqual(first.upload_id, second.upload_id)

    def test_idempotency_key_conflict_rejects_different_source(self) -> None:
        _principal, context, sources = self._principal_with_sources(2)
        service, _vault, _temporary = self._service()
        service.create_upload(context, sources[0], "create-conflict-001")
        with self.assertRaisesRegex(PlatformError, "IDEMPOTENCY_CONFLICT"):
            service.create_upload(context, sources[1], "create-conflict-001")

    def test_tenant_cannot_create_upload_from_other_registry(self) -> None:
        _principal, context, _sources = self._principal_with_sources(1)
        service, _vault, _temporary = self._service()
        foreign_source = registry_source_id(
            TENANT_A.enterprise_id, self.catalog[0].document_id
        )
        with self.assertRaisesRegex(PlatformError, "SOURCE_NOT_REGISTERED"):
            service.create_upload(context, foreign_source, "cross-source-001")

    def test_wrong_content_is_rejected_and_body_not_echoed(self) -> None:
        _principal, context, sources = self._principal_with_sources(1)
        service, _vault, _temporary = self._service()
        upload = service.create_upload(context, sources[0], "wrong-content-001")
        canary = b"SECRET_BODY_CANARY_789"
        with self.assertRaises(PlatformError) as caught:
            service.store_content_chunks(context, upload.upload_id, (canary,))
        self.assertEqual(caught.exception.code, "CONTENT_IDENTITY_MISMATCH")
        self.assertNotIn(canary.decode(), str(caught.exception))

    def test_crash_after_reserved_stage_recovers_without_orphan(self) -> None:
        _principal, context, sources = self._principal_with_sources(1)
        service, vault, temporary = self._service()
        upload = service.create_upload(context, sources[0], "crash-stage-create-001")
        record = service._load_upload(context, upload.upload_id, "f0d_runtime")
        entry = self.catalog[0]
        with open_catalog_source(entry) as source_fd:
            vault.stage_fd(
                source_fd, stage_id=str(record["quarantine_object_key"])
            )
        vault.close()

        recovered_vault = LocalFixtureVault(temporary.name)
        self.addCleanup(recovered_vault.close)
        recovered_service = PlatformService(
            self.config, recovered_vault, catalog=self.catalog
        )
        stored = recovered_service.store_catalog_content(context, upload.upload_id)
        self.assertEqual(stored.status, "CONTENT_STORED")
        recovered_service.complete_upload(
            context, upload.upload_id, "crash-stage-complete-001"
        )
        self.assertEqual(
            os.listdir(Path(temporary.name) / "staging"),
            [],
        )
        self.assertEqual(recovered_vault.final_count(), 1)

    def test_crashed_wrong_reserved_stage_is_rejected_then_retryable(self) -> None:
        _principal, context, sources = self._principal_with_sources(1)
        service, vault, temporary = self._service()
        first = service.create_upload(context, sources[0], "wrong-stage-create-001")
        record = service._load_upload(context, first.upload_id, "f0d_runtime")
        vault.stage_chunks(
            (b"wrong-crash-body",),
            stage_id=str(record["quarantine_object_key"]),
        )
        vault.close()

        recovered_vault = LocalFixtureVault(temporary.name)
        self.addCleanup(recovered_vault.close)
        recovered_service = PlatformService(
            self.config, recovered_vault, catalog=self.catalog
        )
        with self.assertRaisesRegex(PlatformError, "CONTENT_IDENTITY_MISMATCH"):
            recovered_service.store_catalog_content(context, first.upload_id)
        self.assertEqual(
            recovered_service.get_upload(context, first.upload_id).status,
            "REJECTED",
        )
        self.assertEqual(os.listdir(Path(temporary.name) / "staging"), [])

        second = recovered_service.create_upload(
            context, sources[0], "wrong-stage-create-002"
        )
        self.assertNotEqual(first.upload_id, second.upload_id)
        recovered_service.store_catalog_content(context, second.upload_id)
        recovered_service.complete_upload(
            context, second.upload_id, "wrong-stage-complete-002"
        )
        self.assertEqual(recovered_vault.final_count(), 1)

    def test_rejected_upload_can_start_new_attempt(self) -> None:
        _principal, context, sources = self._principal_with_sources(1)
        service, _vault, _temporary = self._service()
        first = service.create_upload(context, sources[0], "reject-retry-create-1")
        with self.assertRaisesRegex(PlatformError, "CONTENT_IDENTITY_MISMATCH"):
            service.store_content_chunks(context, first.upload_id, (b"wrong",))
        second = service.create_upload(context, sources[0], "reject-retry-create-2")
        self.assertNotEqual(first.upload_id, second.upload_id)
        self.assertEqual(second.status, "PENDING")

    def test_complete_is_idempotent_and_object_count_stable(self) -> None:
        service, context, upload_id, vault = self._completed_pipeline()
        first = service.complete_upload(context, upload_id, "pipeline-complete-001")
        second = service.complete_upload(context, upload_id, "different-safe-key-001")
        self.assertEqual(first, second)
        self.assertEqual(vault.final_count(), 1)

    def test_completed_upload_rejects_key_bound_to_another_upload(self) -> None:
        _principal, context, sources = self._principal_with_sources(2)
        service, _vault, _temporary = self._service()
        first = service.create_upload(context, sources[0], "complete-key-create-1")
        service.store_catalog_content(context, first.upload_id)
        service.complete_upload(context, first.upload_id, "complete-key-shared")
        second = service.create_upload(context, sources[1], "complete-key-create-2")
        service.store_catalog_content(context, second.upload_id)
        service.complete_upload(context, second.upload_id, "complete-key-second")
        with self.assertRaisesRegex(PlatformError, "IDEMPOTENCY_CONFLICT"):
            service.complete_upload(context, second.upload_id, "complete-key-shared")

    def test_new_key_on_completed_upload_becomes_permanently_bound(self) -> None:
        _principal, context, sources = self._principal_with_sources(2)
        service, _vault, _temporary = self._service()
        first = service.create_upload(context, sources[0], "late-key-create-1")
        service.store_catalog_content(context, first.upload_id)
        first_result = service.complete_upload(
            context, first.upload_id, "late-key-original-1"
        )
        self.assertEqual(
            service.complete_upload(context, first.upload_id, "late-key-shared"),
            first_result,
        )
        second = service.create_upload(context, sources[1], "late-key-create-2")
        service.store_catalog_content(context, second.upload_id)
        service.complete_upload(context, second.upload_id, "late-key-original-2")
        with self.assertRaisesRegex(PlatformError, "IDEMPOTENCY_CONFLICT"):
            service.complete_upload(context, second.upload_id, "late-key-shared")

    def test_poisoned_audit_id_rolls_back_finalize(self) -> None:
        _principal, context, sources = self._principal_with_sources(1)
        service, _vault, _temporary = self._service()
        upload = service.create_upload(context, sources[0], "poison-audit-create-001")
        service.store_catalog_content(context, upload.upload_id)
        audit_id = _stable_uuid4(
            f"audit:UPLOAD_COMPLETED:{context.enterprise_id}:{upload.upload_id}"
        )
        with tenant_transaction(self.config, "f0d_worker", context) as connection:
            connection.execute(
                "INSERT INTO f0d.audit_event("
                "id,enterprise_id,actor_id,event_code,target_type,target_id,"
                "correlation_id,outcome_code) "
                "VALUES (%s,%s,%s,'POISONED_AUDIT','UPLOAD_SESSION',%s,%s,'DENIED')",
                (
                    audit_id,
                    context.enterprise_id,
                    context.actor_id,
                    upload.upload_id,
                    upload.upload_id,
                ),
            )
        with self.assertRaisesRegex(PlatformError, "FINALIZE_FAILED"):
            service.complete_upload(context, upload.upload_id, "poison-audit-complete-001")
        self.assertEqual(service.get_upload(context, upload.upload_id).status, "CONTENT_STORED")
        self.assertEqual(service.stats(context)["versions"], 0)

    def test_poisoned_outbox_id_rolls_back_finalize(self) -> None:
        _principal, context, sources = self._principal_with_sources(1)
        service, _vault, _temporary = self._service()
        upload = service.create_upload(context, sources[0], "poison-outbox-create-001")
        service.store_catalog_content(context, upload.upload_id)
        version_id = _stable_uuid4(f"version:{context.enterprise_id}:{upload.upload_id}")
        outbox_id = _stable_uuid4(
            f"outbox:DOCUMENT_VERSION_STORED:{context.enterprise_id}:{version_id}"
        )
        with tenant_transaction(self.config, "f0d_worker", context) as connection:
            connection.execute(
                "INSERT INTO f0d.outbox_event("
                "id,enterprise_id,event_type,upload_session_id,idempotency_key) "
                "VALUES (%s,%s,'UPLOAD_COMPLETED',%s,%s)",
                (
                    outbox_id,
                    context.enterprise_id,
                    upload.upload_id,
                    "c" * 64,
                ),
            )
        with self.assertRaisesRegex(PlatformError, "FINALIZE_FAILED"):
            service.complete_upload(context, upload.upload_id, "poison-outbox-complete-001")
        self.assertEqual(service.get_upload(context, upload.upload_id).status, "CONTENT_STORED")
        self.assertEqual(service.stats(context)["versions"], 0)

    def test_outbox_relay_is_idempotent(self) -> None:
        service, context, _upload_id, _vault = self._completed_pipeline()
        self.assertIsNotNone(service.relay_once(context))
        self.assertIsNone(service.relay_once(context))
        self.assertEqual(len(service.list_jobs(context)), 1)

    def test_poisoned_job_id_keeps_outbox_pending(self) -> None:
        service, context, upload_id, _vault = self._completed_pipeline()
        completion = service._completion_result(context, upload_id)
        job_id = _stable_uuid4(
            f"job:ATTACH_NATIVE_PLAN:{context.enterprise_id}:{completion.version_id}"
        )
        with tenant_transaction(self.config, "f0d_worker", context) as connection:
            connection.execute(
                "INSERT INTO f0d.job("
                "id,enterprise_id,kind,document_version_id,idempotency_key,"
                "input_version,trace_id) "
                "VALUES (%s,%s,'ATTACH_NATIVE_PLAN',%s,%s,%s,%s)",
                (
                    job_id,
                    context.enterprise_id,
                    completion.version_id,
                    "d" * 64,
                    "poisoned-input",
                    uuid.uuid4(),
                ),
            )
        with self.assertRaisesRegex(PlatformError, "OUTBOX_RELAY_FAILED"):
            service.relay_once(context)
        with tenant_transaction(self.config, "f0d_worker", context) as connection:
            status = connection.execute(
                "SELECT status FROM f0d.outbox_event WHERE enterprise_id=%s "
                "AND document_version_id=%s",
                (context.enterprise_id, completion.version_id),
            ).fetchone()["status"]
        self.assertEqual(status, "PENDING")

    def test_version_cannot_cross_wire_blob_and_source(self) -> None:
        _principal, context, sources = self._principal_with_sources(2)
        service, _vault, _temporary = self._service()
        for index, source in enumerate(sources):
            upload = service.create_upload(
                context, source, f"lineage-create-{index:03d}"
            )
            service.store_catalog_content(context, upload.upload_id)
            service.complete_upload(
                context, upload.upload_id, f"lineage-complete-{index:03d}"
            )
        with tenant_transaction(self.config, "f0d_worker", context) as connection:
            rows = connection.execute(
                "SELECT v.document_id,v.object_blob_id,v.upload_session_id,"
                "v.source_document_id FROM f0d.document_version v "
                "WHERE v.enterprise_id=%s ORDER BY v.source_document_id",
                (context.enterprise_id,),
            ).fetchall()
        self.assertEqual(len(rows), 2)
        extra_upload_id = uuid.uuid4()
        extra_blob_id = uuid.uuid4()
        source_entry = self.catalog[0]
        admin = psycopg.connect(self.database_admin_dsn, autocommit=True)
        try:
            admin.execute(
                "INSERT INTO f0d.upload_session("
                "id,enterprise_id,actor_id,source_document_id,expected_sha256,"
                "expected_size_bytes,quarantine_object_key,status,captured_sha256,"
                "captured_size_bytes,completed_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,'COMPLETED',%s,%s,statement_timestamp())",
                (
                    extra_upload_id,
                    context.enterprise_id,
                    context.actor_id,
                    source_entry.document_id,
                    source_entry.expected_sha256,
                    source_entry.expected_size,
                    uuid.uuid4().hex,
                    source_entry.expected_sha256,
                    source_entry.expected_size,
                ),
            )
            admin.execute(
                "INSERT INTO f0d.object_blob("
                "id,enterprise_id,upload_session_id,object_key,object_version_id,"
                "sha256,size_bytes) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    extra_blob_id,
                    context.enterprise_id,
                    extra_upload_id,
                    uuid.uuid4().hex,
                    uuid.uuid4(),
                    source_entry.expected_sha256,
                    source_entry.expected_size,
                ),
            )
        finally:
            admin.close()
        connection = psycopg.connect(self.config.worker_dsn)
        try:
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                with connection.transaction():
                    connection.execute(
                        "SELECT set_config('f0d.enterprise_id', %s, true),"
                        "set_config('f0d.actor_id', %s, true),"
                        "set_config('f0d.session_token_sha256', %s, true)",
                        (
                            str(context.enterprise_id),
                            str(context.actor_id),
                            context.session_token_sha256,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO f0d.document_version("
                        "id,enterprise_id,document_id,object_blob_id,upload_session_id,"
                        "source_document_id,version_no) VALUES (%s,%s,%s,%s,%s,%s,2)",
                        (
                            uuid.uuid4(),
                            context.enterprise_id,
                            rows[1]["document_id"],
                            extra_blob_id,
                            extra_upload_id,
                            rows[1]["source_document_id"],
                        ),
                    )
        finally:
            connection.close()

    def test_worker_attaches_body_free_page_plan(self) -> None:
        service, context, _upload_id, _vault = self._completed_pipeline()
        service.relay_once(context)
        self.assertIsNotNone(service.process_once(context))
        documents = service.list_documents(context)
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]["visual_units"], 49)
        self.assertEqual(documents[0]["native_candidates"], 49)

    def test_old_lease_cannot_finish_after_reclaim(self) -> None:
        service, context, _upload_id, _vault = self._completed_pipeline()
        service.relay_once(context)
        first = service.claim_job(context, "lease-worker-a")
        self.assertIsNotNone(first)
        assert first is not None
        with tenant_transaction(
            self.config, "f0d_worker", context
        ) as connection:
            connection.execute(
                "UPDATE f0d.job SET lease_until=statement_timestamp()-interval '1 second' "
                "WHERE enterprise_id=%s AND id=%s",
                (context.enterprise_id, first.job_id),
            )
        second = service.claim_job(context, "lease-worker-b")
        self.assertIsNotNone(second)
        assert second is not None
        self.assertGreater(second.generation, first.generation)
        with self.assertRaisesRegex(PlatformError, "JOB_LEASE_STALE"):
            service.finish_job(context, first)
        self.assertIsInstance(service.finish_job(context, second), uuid.UUID)

    def test_expired_unreclaimed_lease_cannot_finish(self) -> None:
        service, context, _upload_id, _vault = self._completed_pipeline()
        service.relay_once(context)
        lease = service.claim_job(context, "expired-lease-worker")
        self.assertIsNotNone(lease)
        assert lease is not None
        with tenant_transaction(self.config, "f0d_worker", context) as connection:
            connection.execute(
                "UPDATE f0d.job SET lease_until=statement_timestamp()-interval '1 second' "
                "WHERE enterprise_id=%s AND id=%s",
                (context.enterprise_id, lease.job_id),
            )
        with self.assertRaisesRegex(PlatformError, "JOB_LEASE_STALE"):
            service.finish_job(context, lease)

    def test_concurrent_create_collapses_to_one_upload(self) -> None:
        _principal, context, sources = self._principal_with_sources(1)
        service, _vault, _temporary = self._service()
        barrier = threading.Barrier(2)

        def create() -> uuid.UUID:
            barrier.wait()
            return service.create_upload(
                context, sources[0], "concurrent-create-001"
            ).upload_id

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _value: create(), range(2)))
        self.assertEqual(results[0], results[1])

    def test_concurrent_complete_collapses_to_one_version(self) -> None:
        _principal, context, sources = self._principal_with_sources(1)
        service, vault, _temporary = self._service()
        upload = service.create_upload(context, sources[0], "concurrent-complete-create")
        service.store_catalog_content(context, upload.upload_id)
        barrier = threading.Barrier(2)

        def complete() -> object:
            barrier.wait()
            return service.complete_upload(
                context, upload.upload_id, "concurrent-complete-key"
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _value: complete(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(vault.final_count(), 1)
        self.assertEqual(service.stats(context)["versions"], 1)

    def test_api_rejects_client_tenant_field(self) -> None:
        service, _vault, _temporary = self._service()
        source_id = registry_source_id(
            TENANT_A.enterprise_id, self.catalog[0].document_id
        )
        client = TestClient(create_app(service))
        response = client.post(
            "/upload-sessions",
            headers={
                "Authorization": f"Bearer {LOCAL_TENANT_A_TOKEN}",
                "Idempotency-Key": "api-extra-field-001",
            },
            json={"source_id": str(source_id), "enterprise_id": str(uuid.uuid4())},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["reason_code"], "REQUEST_SCHEMA_INVALID")
        self.assertNotIn("enterprise_id", response.text)

    def test_api_requires_bearer_session(self) -> None:
        service, _vault, _temporary = self._service()
        client = TestClient(create_app(service))
        response = client.get("/documents")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("Authorization", response.text)

    def test_api_body_canary_is_not_returned(self) -> None:
        principal, _context, sources = self._principal_with_sources(1)
        service, _vault, _temporary = self._service()
        client = TestClient(create_app(service))
        headers = {
            "Authorization": f"Bearer {principal.token}",
            "Idempotency-Key": "api-body-create-001",
        }
        created = client.post(
            "/upload-sessions", headers=headers, json={"source_id": str(sources[0])}
        )
        self.assertEqual(created.status_code, 201)
        canary = "SECRET_API_BODY_CANARY_8421"
        response = client.put(
            f"/upload-sessions/{created.json()['upload_id']}/content",
            headers={"Authorization": f"Bearer {principal.token}"},
            content=canary.encode(),
        )
        self.assertEqual(response.status_code, 409)
        self.assertNotIn(canary, response.text)

    def test_api_oversized_content_fails_before_storage(self) -> None:
        service, _vault, _temporary = self._service()
        client = TestClient(create_app(service))
        response = client.put(
            f"/upload-sessions/{uuid.uuid4()}/content",
            headers={
                "Authorization": f"Bearer {LOCAL_TENANT_A_TOKEN}",
                "Content-Length": str(129 * 1024 * 1024),
            },
            content=b"",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["reason_code"], "CONTENT_TOO_LARGE")

    def test_client_tenant_header_cannot_cross_registry(self) -> None:
        service, _vault, _temporary = self._service()
        client = TestClient(create_app(service))
        source_id = registry_source_id(
            TENANT_A.enterprise_id, self.catalog[1].document_id
        )
        response = client.post(
            "/upload-sessions",
            headers={
                "Authorization": f"Bearer {LOCAL_TENANT_B_TOKEN}",
                "X-Tenant-ID": str(TENANT_A.enterprise_id),
                "Idempotency-Key": "tenant-header-bypass-001",
            },
            json={"source_id": str(source_id)},
        )
        self.assertEqual(response.status_code, 404)

    def test_api_docs_are_disabled(self) -> None:
        service, _vault, _temporary = self._service()
        client = TestClient(create_app(service))
        self.assertEqual(client.get("/docs").status_code, 404)
        self.assertEqual(client.get("/openapi.json").status_code, 404)

    def test_readiness_combines_code_and_database_closed_gates(self) -> None:
        service, _vault, _temporary = self._service()
        readiness = service.readiness()
        self.assertEqual(readiness["gate_store_integrity"], "VALID")
        self.assertEqual(readiness["gate_count"], 5)
        self.assertFalse(readiness["production_allowed"])

    def test_api_health_declares_no_external_or_ocr_calls(self) -> None:
        service, _vault, _temporary = self._service()
        response = TestClient(create_app(service)).get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["postgresql_major"], 18)
        self.assertEqual(response.json()["external_calls"], 0)
        self.assertEqual(response.json()["ocr_calls"], 0)

    def test_smoke_full_full_replay_is_stable(self) -> None:
        temporary = tempfile.TemporaryDirectory(
            prefix="f0d-replay-test-", dir=_PRIVATE_TMP
        )
        self.addCleanup(temporary.cleanup)
        smoke = replay_profile(self.config, "smoke", vault_root=temporary.name)
        full = replay_profile(self.config, "full", vault_root=temporary.name)
        repeated = replay_profile(self.config, "full", vault_root=temporary.name)
        self.assertEqual(
            {key: smoke[key] for key in ("blobs", "versions", "units", "native", "ocr")},
            {"blobs": 10, "versions": 10, "units": 110, "native": 105, "ocr": 5},
        )
        self.assertEqual(full["bytes"], 41_878_200)
        self.assertEqual(full["blobs"], 26)
        self.assertEqual(full["units"], 249)
        self.assertEqual(full["native"], 225)
        self.assertEqual(full["ocr"], 24)
        self.assertEqual(full["deferred"], 2)
        self.assertEqual(repeated["processed_this_run"], 0)
        self.assertEqual(repeated["relayed_this_run"], 0)
        for key in ("uploads", "blobs", "bytes", "versions", "plans", "units"):
            self.assertEqual(full[key], repeated[key])


if __name__ == "__main__":
    unittest.main()
