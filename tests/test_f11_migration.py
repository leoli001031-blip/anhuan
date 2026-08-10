"""F1.1 migration tests: independent Alembic, replay zero-DDL, tenant tables."""
from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

import psycopg

from f11_support import (
    SUB_TESTER,
    configure_formal_runtime,
    control_connection,
    registered_fixture_sha,
    replay_database_url,
    role_conn,
)

ROOT = Path(__file__).resolve().parents[1]


def setUpModule() -> None:
    configure_formal_runtime()


class F11MigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dsn = replay_database_url()

    def _conn(self, role: str) -> psycopg.Connection:
        return role_conn(role)

    def test_f1_head_is_0002(self) -> None:
        with control_connection() as conn:
            value = conn.execute("SELECT version_num FROM f1.alembic_version").fetchone()[0]
        self.assertEqual(value, "f1_0004")

    def test_f0d_head_restored_to_0006(self) -> None:
        with control_connection() as conn:
            value = conn.execute("SELECT version_num FROM f0d.alembic_version").fetchone()[0]
        self.assertEqual(value, "f0d_0006")

    def test_workflow_tables_exist(self) -> None:
        with control_connection() as conn:
            rows = conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='f1' "
                "AND tablename IN ('upload_task','outbox','qa_request','invite_jti')"
            ).fetchall()
        names = {r[0] for r in rows}
        self.assertEqual(names, {"upload_task", "outbox", "qa_request", "invite_jti"})

    def test_tenant_tables_force_rls(self) -> None:
        with control_connection() as conn:
            rows = conn.execute(
                "SELECT relname, relforcerowsecurity FROM pg_class "
                "WHERE relnamespace = 'f1'::regnamespace "
                "AND relname IN ('enterprise','plant','document','audit_log',"
                "'upload_task','outbox','qa_request','invite_jti',"
                "'user_profile','enterprise_user')"
            ).fetchall()
        forced = {r[0] for r in rows if r[1]}
        expected = {"enterprise", "plant", "document", "audit_log",
                    "upload_task", "outbox", "qa_request", "invite_jti",
                    "user_profile", "enterprise_user"}
        self.assertEqual(forced, expected)

    def test_low_privilege_roles_exist(self) -> None:
        with control_connection() as conn:
            rows = conn.execute(
                "SELECT rolname, rolcanlogin, rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname IN ('f1_api','f1_worker')"
            ).fetchall()
        by_name = {r[0]: (r[1], r[2], r[3]) for r in rows}
        self.assertIn("f1_api", by_name)
        self.assertIn("f1_worker", by_name)
        # no superuser, no BYPASSRLS
        for role, (can_login, superuser, bypass) in by_name.items():
            self.assertTrue(can_login, role)
            self.assertFalse(superuser, role)
            self.assertFalse(bypass, role)

    def test_composite_plant_fk_exists(self) -> None:
        with control_connection() as conn:
            rows = conn.execute(
                "SELECT conname FROM pg_constraint WHERE conrelid = 'f1.document'::regclass "
                "AND contype = 'f' AND conname = 'document_plant_enterprise_fk'"
            ).fetchall()
        self.assertEqual(len(rows), 1)

    def test_fixture_bridge_registered_sha(self) -> None:
        # The bridge no longer accepts a caller F0-I tenant: it derives it from
        # the current F1 enterprise and requires the caller to be a member.
        import uuid as _uuid

        with self._conn("f1_api") as conn:
            conn.execute(
                "SELECT set_config('f1.enterprise_id', %s, true)",
                (str(_uuid.UUID("10000000-0000-4000-8000-00000000000a")),),
            )
            conn.execute(
                "SELECT set_config('f1.sub', %s, true)",
                ("d561ffe2-3be8-40cc-a87e-598dd7d84758",),  # admin, member of A
            )
            rows = conn.execute(
                "SELECT document_scope_id, document_type, chunk_count "
                "FROM f1.fixture_scope_for_sha(%s)",
                (registered_fixture_sha(),),
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "PDF")

    def test_fixture_bridge_unregistered_sha(self) -> None:
        import uuid as _uuid

        with self._conn("f1_api") as conn:
            conn.execute(
                "SELECT set_config('f1.enterprise_id', %s, true)",
                (str(_uuid.UUID("10000000-0000-4000-8000-00000000000a")),),
            )
            conn.execute(
                "SELECT set_config('f1.sub', %s, true)",
                ("d561ffe2-3be8-40cc-a87e-598dd7d84758",),
            )
            rows = conn.execute(
                "SELECT document_scope_id FROM f1.fixture_scope_for_sha(%s)",
                ("0" * 64,),
            ).fetchall()
        self.assertEqual(len(rows), 0)

    def test_replay_second_upgrade_zero_ddl(self) -> None:
        env = dict(os.environ)
        env["F1_MIGRATION_DSN"] = self.dsn.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
        env["PYTHONPATH"] = str(ROOT / "src")
        result = subprocess.run(
            [
                str(ROOT / ".venv" / "bin" / "python"),
                "-B",
                "-m",
                "alembic",
                "-c",
                "infra/f1/alembic.ini",
                "upgrade",
                "head",
            ],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(ROOT),
            check=False,
        )
        combined = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, "F1_REPLAY_COMMAND_FAILED")
        self.assertFalse(
            "Running upgrade" in combined, "F1_REPLAY_DDL_DETECTED"
        )

    def test_f1_api_cannot_read_f0i(self) -> None:
        with self._conn("f1_api") as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT count(*) FROM f0i.chunk")

    def test_f1_api_has_tenant_table_grants(self) -> None:
        with self._conn("f1_api") as conn:
            rows = conn.execute(
                "SELECT has_table_privilege('f1_api', 'f1.enterprise', 'INSERT'), "
                "has_table_privilege('f1_api', 'f1.document', 'INSERT'), "
                "has_table_privilege('f1_api', 'f1.upload_task', 'INSERT')"
            ).fetchone()
        self.assertEqual(tuple(rows), (False, True, True))

    def test_f1_worker_has_workflow_grants(self) -> None:
        with self._conn("f1_worker") as conn:
            rows = conn.execute(
                "SELECT has_table_privilege('f1_worker', 'f1.upload_task', 'UPDATE'), "
                "has_table_privilege('f1_worker', 'f1.outbox', 'INSERT'), "
                "has_table_privilege('f1_worker', 'f1.audit_log', 'INSERT')"
            ).fetchone()
        self.assertEqual(tuple(rows), (True, True, True))

    def test_membership_resolver_returns_names(self) -> None:
        with self._conn("f1_api") as conn:
            conn.execute("SELECT set_config('f1.sub', %s, true)", (SUB_TESTER,))
            rows = conn.execute(
                "SELECT enterprise_id, name, role "
                "FROM f1.resolve_current_enterprises()"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "Tenant A")


if __name__ == "__main__":
    unittest.main()
