"""F1.1.1 repair contracts that do not touch the shared database or services."""
from __future__ import annotations

import ast
import os
import stat
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
F1 = ROOT / "src/platform_foundation/f1"
MIGRATION = ROOT / "infra/f1/alembic/versions/f1_0004_repair_boundaries.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class IdentityContractTests(unittest.TestCase):
    def test_membership_resolution_sets_sub_then_calls_zero_arg_definer(self) -> None:
        source = _source(F1 / "auth.py")
        self.assertIn('session_scope(role="f1_api", sub=sub)', source)
        self.assertIn("FROM f1.resolve_current_enterprises()", source)
        self.assertNotIn("resolve_enterprise_for_sub", source)

    def test_enterprise_create_uses_single_definer_transaction(self) -> None:
        source = _source(F1 / "api/routers/enterprises.py")
        self.assertIn("f1.create_enterprise_for_current_sub", source)
        self.assertNotIn("INSERT INTO f1.enterprise_user", source)
        self.assertNotIn("await log_event", source)


class InvitationContractTests(unittest.TestCase):
    def test_signing_key_has_no_source_literal(self) -> None:
        source = _source(F1 / "invitation.py")
        tree = ast.parse(source)
        assigned_names = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            if isinstance(target, ast.Name)
        }
        self.assertNotIn("INVITE_SECRET", assigned_names)
        self.assertIn("F1_INVITE_KEY_FILE", source)
        self.assertIn("stat.S_IMODE", source)

    def test_key_loader_requires_regular_0600_file(self) -> None:
        from platform_foundation.f1.invitation import InvitationError, _load_invite_key

        old = os.environ.get("F1_INVITE_KEY_FILE")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "key"
                path.write_bytes(os.urandom(32))
                path.chmod(0o644)
                os.environ["F1_INVITE_KEY_FILE"] = str(path)
                with self.assertRaises(InvitationError):
                    _load_invite_key()
                path.chmod(0o600)
                self.assertEqual(len(_load_invite_key()), 32)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        finally:
            if old is None:
                os.environ.pop("F1_INVITE_KEY_FILE", None)
            else:
                os.environ["F1_INVITE_KEY_FILE"] = old

    def test_invite_runtime_uses_current_sub_and_oidc_email_contract(self) -> None:
        source = _source(F1 / "invitation.py")
        self.assertIn('session_scope(role="f1_api", sub=user_sub)', source)
        self.assertIn("f1.create_invite_for_current_sub", source)
        self.assertIn("oidc_email: str", source)
        self.assertIn(":oidc_email", source)
        self.assertNotIn(":sub", source)

    def test_invite_router_enforces_local_membership_hierarchy(self) -> None:
        source = _source(F1 / "api/routers/invitation.py")
        self.assertIn("tenant.role", source)
        self.assertIn("INVITE_ROLE_ESCALATION", source)
        self.assertNotIn("tenant.roles", source)
        self.assertNotIn("await log_event", source)


class AuditContractTests(unittest.TestCase):
    def test_audit_read_gate_uses_local_membership_role(self) -> None:
        source = _source(F1 / "api/routers/audit.py")
        self.assertIn("tenant.role", source)
        self.assertNotIn("require_role", source)

    def test_audit_module_exposes_in_transaction_append(self) -> None:
        source = _source(F1 / "audit.py")
        self.assertIn("async def add_event", source)
        self.assertIn("session.add(row)", source)


class MigrationAttackContractTests(unittest.TestCase):
    def test_repair_migration_is_linear_and_removes_legacy_signatures(self) -> None:
        source = _source(MIGRATION)
        self.assertIn('revision: str = "f1_0004"', source)
        self.assertIn('down_revision: str | None = "f1_0003"', source)
        self.assertIn("DROP FUNCTION IF EXISTS f1.resolve_enterprise_for_sub(text)", source)
        self.assertIn(
            "DROP FUNCTION IF EXISTS f1.consume_invite(text,text,text,text,uuid,timestamptz)",
            source,
        )
        self.assertIn("DROP FUNCTION IF EXISTS f1.task_enterprise(uuid)", source)

    def test_invite_consume_serializes_and_checks_update(self) -> None:
        source = _source(MIGRATION)
        self.assertIn("FOR UPDATE", source)
        self.assertIn("IF NOT FOUND THEN RAISE EXCEPTION 'INVITE_ALREADY_USED'", source)
        self.assertIn("p_oidc_email text", source)

    def test_runtime_direct_membership_and_invite_writes_are_revoked(self) -> None:
        source = _source(MIGRATION)
        self.assertIn(
            "REVOKE INSERT, UPDATE, DELETE ON f1.enterprise_user FROM f1_api, f1_worker",
            source,
        )
        self.assertIn(
            "REVOKE INSERT, UPDATE, DELETE ON f1.invite_jti FROM f1_api, f1_worker",
            source,
        )


if __name__ == "__main__":
    unittest.main()
