"""Static contracts for the additive F1.1.1 repair migration.

Live PostgreSQL concurrency and RLS probes are exercised by the repair
reverse verifier.  These tests keep the migration/model/session interfaces
from silently drifting before a scratch database is started.
"""
from __future__ import annotations

import hashlib
import inspect
from pathlib import Path
import unittest

from platform_foundation.f1 import database, models


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "infra/f1/alembic/versions"
REPAIR = MIGRATIONS / "f1_0004_repair_boundaries.py"
FROZEN_0003 = MIGRATIONS / "f1_0003_security_boundaries.py"


class RepairMigrationChainTests(unittest.TestCase):
    def test_f111_repair_revision_is_linear_0004(self) -> None:
        text = REPAIR.read_text(encoding="utf-8")
        self.assertIn('revision: str = "f1_0004"', text)
        self.assertIn('down_revision: str | None = "f1_0003"', text)

    def test_f111_applied_0003_bytes_are_unchanged(self) -> None:
        digest = hashlib.sha256(FROZEN_0003.read_bytes()).hexdigest()
        self.assertEqual(
            digest,
            "a8058d00719d26132b24671a4c802c4cea820d0b6ca1a3555a44fa58385d2da9",
        )

    def test_f111_crosswire_migration_fails_closed(self) -> None:
        text = REPAIR.read_text(encoding="utf-8")
        self.assertIn("F1_UPLOAD_DOCUMENT_CROSSWIRE_PRESENT", text)
        self.assertIn("F1_OUTBOX_TASK_CROSSWIRE_PRESENT", text)

    def test_f111_upload_and_outbox_use_composite_foreign_keys(self) -> None:
        text = REPAIR.read_text(encoding="utf-8")
        self.assertIn("upload_task_document_enterprise_fk", text)
        self.assertIn("outbox_task_enterprise_fk", text)

    def test_f111_identity_tables_are_force_rls(self) -> None:
        text = REPAIR.read_text(encoding="utf-8")
        for table in ("user_profile", "enterprise_user"):
            self.assertIn(
                f"ALTER TABLE f1.{table} ENABLE ROW LEVEL SECURITY",
                text,
            )
            self.assertIn(
                f"ALTER TABLE f1.{table} FORCE ROW LEVEL SECURITY",
                text,
            )


class RepairOwnershipContractTests(unittest.TestCase):
    def test_f111_worker_claim_has_unforgeable_token(self) -> None:
        text = REPAIR.read_text(encoding="utf-8")
        self.assertIn("claim_upload_task", text)
        self.assertIn("lease_token = v_token", text)
        self.assertIn("lease_until > statement_timestamp()", text)

    def test_f111_worker_session_authorization_binds_task_and_token(self) -> None:
        text = REPAIR.read_text(encoding="utf-8")
        self.assertIn("t.id = f1.current_task_id()", text)
        self.assertIn("t.lease_token = f1.current_lease_token()", text)

    def test_f111_dispatch_claim_is_cas_and_deterministic(self) -> None:
        text = REPAIR.read_text(encoding="utf-8")
        self.assertIn("claim_pending_dispatch", text)
        self.assertIn("outbox_rq_job_id_uq", text)
        self.assertIn("FOR UPDATE OF o SKIP LOCKED", text)

    def test_f111_qa_request_has_owner_and_state_contract(self) -> None:
        text = REPAIR.read_text(encoding="utf-8")
        for marker in ("owner_token", "owner_lease_until", "qa_request_state_ck"):
            self.assertIn(marker, text)


class RepairPrivilegeContractTests(unittest.TestCase):
    def test_f111_definers_use_nologin_domain_roles(self) -> None:
        migration = REPAIR.read_text(encoding="utf-8")
        roles = (ROOT / "infra/f1/roles.sql").read_text(encoding="utf-8")
        runner = (ROOT / "infra/f1/migrate_f1.py").read_text(encoding="utf-8")
        for role in (
            "f1_auth_definer",
            "f1_identity_read_definer",
            "f1_enterprise_create_definer",
            "f1_invite_create_definer",
            "f1_invite_consume_definer",
            "f1_upload_definer",
            "f1_outbox_definer",
            "f1_qa_definer",
        ):
            self.assertIn(role, migration)
            self.assertIn(role, roles)
            self.assertIn(role, runner)
        self.assertIn("NOLOGIN", roles)
        self.assertIn("NOBYPASSRLS", roles)

    def test_f111_migration_role_gets_no_permanent_write_policy(self) -> None:
        source = inspect.getsource(__import__(
            "infra.f1.alembic.versions.f1_0004_repair_boundaries",
            fromlist=["_policies_and_grants"],
        )._policies_and_grants)
        self.assertNotIn("FOR INSERT TO f0d_migration", source)
        self.assertNotIn("FOR UPDATE TO f0d_migration", source)
        self.assertIn("DROP POLICY IF EXISTS migration_f1_invite_consume", source)
        self.assertIn("DROP POLICY IF EXISTS migration_f1_audit_insert", source)

    def test_f111_runtime_roles_cannot_inherit_definer_roles(self) -> None:
        text = (ROOT / "infra/f1/roles.sql").read_text(encoding="utf-8")
        for role in (
            "f1_auth_definer", "f1_identity_read_definer",
            "f1_enterprise_create_definer", "f1_invite_create_definer",
            "f1_invite_consume_definer", "f1_upload_definer",
            "f1_outbox_definer", "f1_qa_definer",
        ):
            self.assertNotIn(f"GRANT {role} TO f1_api", text)
            self.assertNotIn(f"GRANT {role} TO f1_worker", text)

    def test_f111_migration_runner_reads_only_0600_secret_files(self) -> None:
        text = (ROOT / "infra/f1/migrate_f1.py").read_text(encoding="utf-8")
        self.assertIn('os.environ.get("F1_SECRETS_DIR"', text)
        self.assertIn('"f1_bootstrap_dsn"', text)
        self.assertIn('"f1_migration_dsn"', text)
        self.assertIn("make_url(_read_secret(secret_name))", text)
        self.assertIn("stat.S_ISREG", text)
        self.assertIn("info.st_nlink != 1", text)
        self.assertNotIn("BOOTSTRAP_PASSWORD", text)
        self.assertNotIn("/private/tmp/anhuan-f1-secrets", text)
        self.assertNotIn("PASSWORD '{password}'", text)

    def test_f111_connect_grants_are_replay_idempotent(self) -> None:
        text = (ROOT / "infra/f1/migrate_f1.py").read_text(encoding="utf-8")
        privilege_check = "SELECT has_database_privilege(%s, %s, 'CONNECT')"
        grant = 'sql.SQL("GRANT CONNECT ON DATABASE {} TO {}")'
        self.assertIn(privilege_check, text)
        self.assertIn(grant, text)
        self.assertLess(text.index(privilege_check), text.index(grant))
        self.assertIn("if can_connect is None or not bool(can_connect[0]):", text)

    def test_f111_migration_runner_is_not_bound_to_frozen_f0i_config(self) -> None:
        text = (ROOT / "infra/f1/migrate_f1.py").read_text(encoding="utf-8")
        self.assertNotIn("platform_foundation.f0i.config", text)
        self.assertNotIn("database_config()", text)
        self.assertIn("pg_database()", text)
        self.assertIn("F1_MIGRATION_DSN_IDENTITY_MISMATCH", text)
        self.assertIn("F1_BOOTSTRAP_DSN_IDENTITY_MISMATCH", text)

    def test_f111_alembic_env_parses_exact_migration_identity(self) -> None:
        text = (ROOT / "infra/f1/alembic/env.py").read_text(encoding="utf-8")
        self.assertIn("make_url(value)", text)
        self.assertIn('url.username != "f0d_migration"', text)
        self.assertNotIn('if "f0d_migration" not in value', text)

    def test_f111_runner_prepares_version_schema_before_alembic(self) -> None:
        text = (ROOT / "infra/f1/migrate_f1.py").read_text(encoding="utf-8")
        self.assertIn("def _ensure_f1_version_schema", text)
        self.assertIn("F1_SCHEMA_OWNER_MISMATCH", text)
        self.assertLess(
            text.index("_ensure_f1_version_schema()", text.index("def main")),
            text.index("command.upgrade", text.index("def main")),
        )

    def test_f111_old_arbitrary_sub_resolver_is_removed(self) -> None:
        text = REPAIR.read_text(encoding="utf-8")
        self.assertIn("DROP FUNCTION IF EXISTS f1.resolve_enterprise_for_sub(text)", text)
        self.assertIn("resolve_current_enterprises()", text)

    def test_f111_direct_membership_writes_are_revoked(self) -> None:
        text = REPAIR.read_text(encoding="utf-8")
        self.assertIn(
            "REVOKE INSERT, UPDATE, DELETE ON f1.enterprise_user FROM f1_api, f1_worker",
            text,
        )
        self.assertNotIn("CREATE POLICY membership_self_insert", text)

    def test_f111_old_task_tenant_bridge_is_removed(self) -> None:
        self.assertIn(
            "DROP FUNCTION IF EXISTS f1.task_enterprise(uuid)",
            REPAIR.read_text(encoding="utf-8"),
        )

    def test_f111_invite_consumer_locks_ledger_row(self) -> None:
        text = REPAIR.read_text(encoding="utf-8")
        self.assertIn("FOR UPDATE", text)
        self.assertIn("MEMBERSHIP_ALREADY_EXISTS", text)

    def test_f111_public_is_revoked_from_every_new_definer(self) -> None:
        text = REPAIR.read_text(encoding="utf-8")
        signatures = (
            "f1.current_task_id()",
            "f1.current_lease_token()",
            "f1.session_authorized(uuid)",
            "f1.resolve_current_enterprises()",
            "f1.create_enterprise_for_current_sub(uuid,text,text,text)",
            "f1.create_invite_for_current_sub(text,text,text,timestamptz)",
            "f1.consume_invite(text,text,text,uuid,timestamptz,text)",
            "f1.claim_upload_task(uuid,text,integer)",
            "f1.renew_upload_lease(uuid,uuid,integer)",
            "f1.claim_pending_dispatch(integer,integer)",
            "f1.complete_dispatch(uuid,uuid,boolean)",
        )
        self.assertIn("for signature in PUBLIC_REVOKED_SIGNATURES", text)
        for signature in signatures:
            self.assertIn(f'"{signature}"', text)


class RepairRuntimeModelTests(unittest.TestCase):
    def test_f111_models_match_lease_and_dispatch_columns(self) -> None:
        self.assertTrue(hasattr(models.UploadTask, "lease_token"))
        self.assertTrue(hasattr(models.UploadTask, "object_state"))
        self.assertTrue(hasattr(models.Outbox, "rq_job_id"))
        self.assertTrue(hasattr(models.Outbox, "dispatch_token"))

    def test_f111_qa_ciphertext_is_binary_and_owner_bound(self) -> None:
        self.assertTrue(hasattr(models.QaRequest, "owner_token"))
        self.assertEqual(models.QaRequest.response_encrypted.type.__class__.__name__, "LargeBinary")

    def test_f111_session_scope_requires_complete_worker_lease_context(self) -> None:
        source = inspect.getsource(database.session_scope)
        self.assertIn("F1_WORKER_LEASE_CONTEXT_REQUIRED", source)
        self.assertIn("F1_LEASE_CONTEXT_INCOMPLETE", source)

    def test_f111_runtime_has_no_fixed_host_secret_directory(self) -> None:
        source = (ROOT / "src/platform_foundation/f1/database.py").read_text(encoding="utf-8")
        self.assertNotIn("/private/tmp/anhuan-f1-secrets", source)
        self.assertIn("read_f1_secret_text", source)
        self.assertIn("F1_API_PASSWORD_FILE", source)
        self.assertIn("F1_WORKER_PASSWORD_FILE", source)


if __name__ == "__main__":
    unittest.main()
