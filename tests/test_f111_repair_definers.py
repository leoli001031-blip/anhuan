"""Attack contracts for F1.1.1 dedicated SECURITY DEFINER owners.

These are intentionally static.  The same ownership, RLS, and conflict
contracts are exercised against a random scratch PostgreSQL database during
M4; this suite prevents the migration runner from silently widening them.
"""
from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "infra/f1/alembic/versions/f1_0004_repair_boundaries.py"
RUNNER = ROOT / "infra/f1/migrate_f1.py"
ROLES = ROOT / "infra/f1/roles.sql"

DEFINER_ROLES = (
    "f1_auth_definer",
    "f1_identity_read_definer",
    "f1_enterprise_create_definer",
    "f1_invite_create_definer",
    "f1_invite_consume_definer",
    "f1_upload_definer",
    "f1_outbox_definer",
    "f1_qa_definer",
)


class DedicatedDefinerRoleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.migration = MIGRATION.read_text(encoding="utf-8")
        self.runner = RUNNER.read_text(encoding="utf-8")
        self.roles = ROLES.read_text(encoding="utf-8")

    def test_exact_eight_domain_roles_are_provisioned_everywhere(self) -> None:
        for role in DEFINER_ROLES:
            self.assertIn(role, self.migration)
            self.assertIn(role, self.runner)
            self.assertIn(role, self.roles)
        self.assertNotIn("f1_identity_definer", self.runner)
        self.assertNotIn("f1_worker_definer", self.runner)

    def test_roles_are_nonlogin_noninherit_nobypass_and_membership_free(self) -> None:
        for marker in (
            "NOLOGIN", "NOSUPERUSER", "NOINHERIT", "NOBYPASSRLS",
            "pg_auth_members", "F1_DEFINER_ROLE_MEMBERSHIP_FORBIDDEN",
        ):
            self.assertIn(marker, self.runner)
            self.assertIn(marker, self.roles)

    def test_owner_map_is_domain_exact_and_includes_qa_cas(self) -> None:
        expected = {
            "f1.session_authorized(uuid)": "f1_auth_definer",
            "f1.resolve_current_enterprises()": "f1_identity_read_definer",
            "f1.create_enterprise_for_current_sub(uuid,text,text,text)":
                "f1_enterprise_create_definer",
            "f1.create_invite_for_current_sub(text,text,text,timestamptz)":
                "f1_invite_create_definer",
            "f1.consume_invite(text,text,text,uuid,timestamptz,text)":
                "f1_invite_consume_definer",
            "f1.claim_upload_task(uuid,text,integer)": "f1_upload_definer",
            "f1.renew_upload_lease(uuid,uuid,integer)": "f1_upload_definer",
            "f1.claim_pending_dispatch(integer,integer)": "f1_outbox_definer",
            "f1.complete_dispatch(uuid,uuid,boolean)": "f1_outbox_definer",
            "f1.claim_qa_request(uuid,text,integer)": "f1_qa_definer",
            "f1.complete_qa_request(uuid,uuid,text,text,bytea,text,text)":
                "f1_qa_definer",
        }
        for signature, role in expected.items():
            self.assertIn(f'"{signature}": "{role}"', self.runner)

    def test_migration_read_policy_is_removed_from_every_tenant_table(self) -> None:
        self.assertIn(
            'op.execute(f"DROP POLICY IF EXISTS migration_f1_read ON f1.{table}")',
            self.migration,
        )
        for table in (
            "enterprise", "plant", "document", "audit_log", "upload_task",
            "outbox", "qa_request", "invite_jti",
        ):
            self.assertIn(f'"{table}"', self.migration)
        self.assertNotIn("current_enterprise_id() IS NULL", self.migration)
        self.assertNotIn("USING (true)", self.migration)
        self.assertNotIn("USING true", self.migration)

    def test_acl_and_rls_are_split_by_command_and_domain(self) -> None:
        for marker in (
            "f111_auth_profile_select",
            "f111_identity_membership_select",
            "f111_enterprise_insert",
            "f111_invite_create_insert",
            "f111_invite_consume_update",
            "f111_upload_update",
            "f111_outbox_update",
            "f111_qa_select",
            "f111_qa_insert",
            "f111_qa_update",
            "GRANT UPDATE (consumed_by_sub, consumed_at) ON f1.invite_jti",
        ):
            self.assertIn(marker, self.migration)
        self.assertNotIn("FOR ALL TO f1_", self.migration)

    def test_complete_dispatch_rejects_expired_claim(self) -> None:
        start = self.migration.index("CREATE FUNCTION f1.complete_dispatch")
        end = self.migration.index("for signature in (", start)
        body = self.migration[start:end]
        self.assertIn("dispatch_lease_until > statement_timestamp()", body)

    def test_qa_no_visible_row_is_conflict_not_in_progress(self) -> None:
        start = self.migration.index("CREATE FUNCTION f1.claim_qa_request")
        end = self.migration.index("CREATE FUNCTION f1.complete_qa_request", start)
        body = self.migration[start:end]
        self.assertIn("IF NOT FOUND THEN", body)
        self.assertIn("'CONFLICT'", body)


class BootstrapFinalizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = RUNNER.read_text(encoding="utf-8")
        self.migration = MIGRATION.read_text(encoding="utf-8")

    def test_finalizer_resolves_oids_and_validates_security_contract(self) -> None:
        for marker in (
            "to_regprocedure", "prosecdef", "proconfig",
            "aclexplode", "F1_DEFINER_OWNER_MAP_MISMATCH",
            "F1_DEFINER_PUBLIC_EXECUTE", "F1_DEFINER_SEARCH_PATH_INVALID",
        ):
            self.assertIn(marker, self.runner)
        self.assertIn("autocommit=False", self.runner)
        self.assertIn("connection.commit()", self.runner)
        self.assertIn("%s::text)", self.runner)
        self.assertIn("F1_DEFINER_OWNER_UNEXPECTED", self.runner)
        self.assertIn("if current_owner == role", self.runner)

    def test_downgrade_requires_bootstrap_owner_restore(self) -> None:
        self.assertIn("def _restore_definer_owners", self.runner)
        self.assertIn("F1_DEFINER_OWNER_RESTORE_REQUIRED", self.migration)


if __name__ == "__main__":
    unittest.main()
