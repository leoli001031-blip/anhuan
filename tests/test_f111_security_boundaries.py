"""F1.1.1 identity and tenant-boundary contracts, shared-state free.

The old suite connected through a migration DSN and wrote seeded rows in a
shared database.  Formal acceptance now delegates adversarial SQL to
``f111_repair_pg_verify.py`` in a freshly empty random scratch database.  This
module provides the matching offline contract gate: it verifies that the
runtime, f1_0004 policy/DEFINER implementation and fixed live verifier remain
wired together without opening any database or service connection.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

from infra.f1 import formal_acceptance


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.migration = _source(
            "infra/f1/alembic/versions/f1_0004_repair_boundaries.py"
        )
        cls.pg_live = _source("tests/f111_repair_pg_verify.py")
        cls.reverse = _source("tests/f111_reverse_verify.py")
        for relative, value in (
            ("infra/f1/alembic/versions/f1_0004_repair_boundaries.py", cls.migration),
            ("tests/f111_repair_pg_verify.py", cls.pg_live),
            ("tests/f111_reverse_verify.py", cls.reverse),
        ):
            ast.parse(value, filename=relative)
        cls.assertIn(cls, "_cleanup_fixture(fixture)", cls.pg_live)
        cls.assertIn(cls, "SCRATCH_DATABASE_REQUIRED", cls.pg_live)

    def _assert_live_metric(self, metric: str) -> None:
        self.assertIn(metric, formal_acceptance.PG_METRICS)
        self.assertIn(f'metrics["{metric}"]', self.pg_live)


class MembershipBoundaryTests(_Base):
    """A caller-controlled tenant GUC is insufficient without membership."""

    def test_f111_nonmember_self_set_tenant_reads_zero(self) -> None:
        body = self.migration.split(
            "CREATE OR REPLACE FUNCTION f1.session_authorized", 1
        )[1].split("for signature in (", 1)[0]
        for marker in (
            "WHEN session_user = 'f1_api'",
            "eu.enterprise_id = p_enterprise_id",
            "up.keycloak_sub = f1.current_sub()",
            "ELSE false",
        ):
            self.assertIn(marker, body)
        self._assert_live_metric("nonmember_visible_rows")

    def test_f111_nonmember_self_set_tenant_writes_zero(self) -> None:
        for revocation in (
            "REVOKE INSERT, UPDATE, DELETE ON f1.enterprise_user FROM f1_api, f1_worker",
            "REVOKE INSERT, DELETE ON f1.enterprise FROM f1_api, f1_worker",
            "REVOKE INSERT, UPDATE, DELETE ON f1.user_profile FROM f1_api, f1_worker",
        ):
            self.assertIn(revocation, self.migration)
        self._assert_live_metric("api_direct_write_acceptances")

    def test_f111_nonmember_cannot_read_audit_after_write(self) -> None:
        policy = self.migration.split("CREATE POLICY audit_read", 1)[1].split(
            "CREATE POLICY audit_append_api", 1
        )[0]
        self.assertIn("f1.session_authorized(enterprise_id)", policy)
        self.assertIn("up.keycloak_sub = f1.current_sub()", policy)
        self.assertIn("eu.role IN ('super_admin','auditor')", policy)
        self._assert_live_metric("nonmember_visible_rows")


class PublicDefinerTests(_Base):
    """PUBLIC cannot execute a SECURITY DEFINER function."""

    def test_f111_public_cannot_execute_any_definer(self) -> None:
        signatures = (
            "f1.session_authorized(uuid)",
            "f1.resolve_current_enterprises()",
            "f1.create_enterprise_for_current_sub(uuid,text,text,text)",
            "f1.create_invite_for_current_sub(text,text,text,timestamptz)",
            "f1.consume_invite(text,text,text,uuid,timestamptz,text)",
            "f1.claim_upload_task(uuid,text,integer)",
            "f1.claim_pending_dispatch(integer,integer)",
            "f1.claim_qa_request(uuid,text,integer)",
        )
        for signature in signatures:
            self.assertIn(signature, self.migration)
        self.assertGreaterEqual(self.migration.count("REVOKE ALL ON FUNCTION"), 8)
        self._assert_live_metric("public_definer_exec")


class ArbitraryF0iTenantTests(_Base):
    """The citation bridge derives its tenant from authenticated F1 context."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.citation = _source("src/platform_foundation/f1/citation.py")
        cls.bridge = _source(
            "infra/f1/alembic/versions/f1_0003_security_boundaries.py"
        )
        ast.parse(cls.citation, filename="src/platform_foundation/f1/citation.py")

    def test_f111_bridge_tenant_a_scope_only(self) -> None:
        verify = self.citation.split("async def verify_candidates", 1)[1].split(
            "\n\n__all__", 1
        )[0]
        self.assertIn("str(tenant.enterprise_id)", verify)
        self.assertIn("tenant.sub", verify)
        self.assertIn("set_config('f1.enterprise_id'", verify)
        self.assertIn("set_config('f1.sub'", verify)

    def test_f111_bridge_does_not_accept_caller_tenant(self) -> None:
        verify = self.citation.split("async def verify_candidates", 1)[1].split(
            "\n\n__all__", 1
        )[0]
        self.assertIn("FROM f1.verify_citations(%s, %s, %s)", verify)
        self.assertNotIn("caller_tenant", verify)
        self.assertNotIn("f0i_tenant", verify)
        self.assertIn(
            "DROP FUNCTION IF EXISTS f1.verify_citations(uuid, uuid[], bytea, text)",
            self.bridge,
        )
        self.assertIn(
            "CREATE OR REPLACE FUNCTION f1.verify_citations(\n          p_chunk_ids uuid[]",
            self.bridge,
        )

    def test_f111_bridge_nonmember_gets_zero(self) -> None:
        policy = self.migration.split(
            "CREATE POLICY f111_bridge_enterprise_select", 1
        )[1].split("GRANT SELECT ON f1.user_profile", 1)[0]
        self.assertIn("id = f1.current_enterprise_id()", policy)
        bridge = self.bridge.split(
            "CREATE OR REPLACE FUNCTION f1.verify_citations", 1
        )[1].split("REVOKE ALL ON FUNCTION f1.verify_citations", 1)[0]
        self.assertIn("NOT f1.session_authorized(v_eid)", bridge)
        crosswire = self.reverse.split("def citation_and_tenant_crosswires", 1)[1].split(
            "\n    def ", 1
        )[0]
        self.assertIn("cross_status == 404", crosswire)
        self.assertIn("docs_status == 404", crosswire)


class InviteSpoofTests(_Base):
    """Invite claims, OIDC identity and ledger transition are one transaction."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.invitation = _source("src/platform_foundation/f1/invitation.py")
        cls.router = _source("src/platform_foundation/f1/api/routers/invitation.py")

    def test_f111_invite_consume_ignores_client_sub(self) -> None:
        consume = self.router.split("async def consume", 1)[1].split(
            "\n\n__all__", 1
        )[0]
        self.assertIn('user_sub=user["sub"]', consume)
        self.assertIn("oidc_email=oidc_email", consume)
        self.assertNotIn("body.keycloak_sub", consume)
        self.assertNotIn("body.email", consume)
        self.assertIn("v_sub := f1.current_sub()", self.migration)

    def test_f111_invite_role_escalation_and_atomic_reject(self) -> None:
        consume = self.migration.split("CREATE FUNCTION f1.consume_invite", 1)[1].split(
            "def _worker_claims", 1
        )[0]
        for marker in (
            "WHERE i.jti = p_jti FOR UPDATE",
            "v_row.role <> p_role",
            "RAISE EXCEPTION 'INVITE_CLAIMS_MISMATCH'",
            "UPDATE f1.invite_jti",
            "INSERT INTO f1.enterprise_user",
            "INSERT INTO f1.audit_log",
        ):
            self.assertIn(marker, consume)
        self._assert_live_metric("invite_escalation_acceptances")
        self._assert_live_metric("invite_concurrency_failures")

    def test_f111_invite_no_role_override_on_existing_membership(self) -> None:
        consume = self.migration.split("CREATE FUNCTION f1.consume_invite", 1)[1].split(
            "def _worker_claims", 1
        )[0]
        self.assertIn("IF EXISTS (", consume)
        self.assertIn("RAISE EXCEPTION 'MEMBERSHIP_ALREADY_EXISTS'", consume)
        self.assertNotIn("ON CONFLICT (enterprise_id, user_id) DO UPDATE", consume)
        self._assert_live_metric("invite_membership_mismatches")


class AuditReadGateTests(_Base):
    """Audit authorization comes from the local membership role."""

    def test_f111_audit_read_requires_auditor_or_admin(self) -> None:
        router = _source("src/platform_foundation/f1/api/routers/audit.py")
        self.assertIn('tenant.role not in ("super_admin", "auditor")', router)
        self.assertNotIn("require_role", router)
        policy = self.migration.split("CREATE POLICY audit_read", 1)[1].split(
            "CREATE POLICY audit_append_api", 1
        )[0]
        self.assertIn("eu.role IN ('super_admin','auditor')", policy)


class ApiWorkerIsolationTests(_Base):
    """Runtime code accepts exactly the API and worker credentials."""

    def test_f111_database_rejects_unknown_role(self) -> None:
        from platform_foundation.f1.database import _get_factory

        with self.assertRaisesRegex(ValueError, "F1_ROLE_INVALID"):
            _get_factory("f0d_migration")
        with self.assertRaisesRegex(ValueError, "F1_ROLE_INVALID"):
            _get_factory("f1_superuser")

    def test_f111_api_dsn_is_never_worker_or_migration(self) -> None:
        database = _source("src/platform_foundation/f1/database.py")
        tree = ast.parse(database)
        role_passwords = next(
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "ROLE_PASSWORDS" for target in node.targets)
        )
        self.assertEqual(
            set(ast.literal_eval(role_passwords.value)), {"f1_api", "f1_worker"}
        )
        api_function = database.split("def _api_dsn", 1)[1].split("\n\ndef ", 1)[0]
        self.assertIn('_role_dsn("f1_api")', api_function)
        self.assertNotIn("f1_worker", api_function)
        self.assertNotIn("migration", api_function)

    def test_f111_runtime_does_not_parse_migration_dsn(self) -> None:
        database = _source("src/platform_foundation/f1/database.py")
        tree = ast.parse(database)
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        names = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        }
        attributes = {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        self.assertFalse(any("f0i" in module for module in imported))
        self.assertNotIn("database_config", names)
        self.assertNotIn("migration_dsn", attributes)
        self.assertIn("read_f1_secret_text", database)


if __name__ == "__main__":
    unittest.main()
