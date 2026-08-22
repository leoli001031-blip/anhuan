"""Real PostgreSQL gate for analysis-report local browser fixture.

Does not import or reuse seed_world(). Dedicated stack is stopped in finally.
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import os
import sys
import unittest
import uuid
from pathlib import Path

from infra.f1 import local_seed
from infra.f1.analysis_report_postgres_integration import (
    PostgresIntegrationStack,
    canonical_shared_fingerprint,
    dedicated_counts,
)


ROOT = Path(__file__).resolve().parents[1]


def _load_fixture():
    path = ROOT / "infra/f1/analysis-reports/local_browser_fixture.py"
    loader = importlib.machinery.SourceFileLoader(
        "analysis_report_local_browser_fixture", str(path)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None or spec.loader is None:
        raise AssertionError("fixture spec unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    spec.loader.exec_module(module)
    return module


FIXTURE = _load_fixture()
BINDING_ID = FIXTURE.BINDING_ID
CRM_ACCOUNT_ID = FIXTURE.CRM_ACCOUNT_ID
EMPLOYEE_SUB = FIXTURE.EMPLOYEE_SUB
INVITEE_SUB = FIXTURE.INVITEE_SUB
TENANT_A_SUB = FIXTURE.TENANT_A_SUB
apply = FIXTURE.apply


if os.environ.get("ANALYSIS_REPORT_PGINT_CYCLE") != "fixture-v1":
    raise RuntimeError("ANALYSIS_REPORT_PGINT_CYCLE_REQUIRED")


def _memberships(connection, sub: str) -> list[tuple[uuid.UUID, str]]:
    rows = connection.execute(
        "SELECT membership.enterprise_id, membership.role "
        "FROM f1.enterprise_user AS membership "
        "JOIN f1.user_profile AS profile ON profile.id = membership.user_id "
        "WHERE profile.keycloak_sub = %s "
        "ORDER BY membership.enterprise_id",
        (sub,),
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def _employee_fingerprint(connection) -> bytes:
    profile_id = local_seed._stable_id("profile", EMPLOYEE_SUB)
    profile = connection.execute(
        "SELECT id::text, keycloak_sub, email FROM f1.user_profile WHERE id=%s",
        (profile_id,),
    ).fetchone()
    memberships = connection.execute(
        "SELECT id::text, enterprise_id::text, user_id::text, role "
        "FROM f1.enterprise_user WHERE user_id=%s ORDER BY id",
        (profile_id,),
    ).fetchall()
    return repr((profile, memberships)).encode("utf-8")


def _identity_fingerprint(connection) -> str:
    rows = connection.execute(
        "SELECT user_id::text, enterprise_id::text, role "
        "FROM f1.enterprise_user ORDER BY 1,2,3"
    ).fetchall()
    crm = connection.execute(
        "SELECT id::text, enterprise_id::text, display_name, stage "
        "FROM f1.crm_account ORDER BY 1"
    ).fetchall()
    bindings = connection.execute(
        "SELECT id::text, enterprise_id::text, client_account_id::text, "
        "audience_enterprise_id::text, status "
        "FROM f1.analysis_report_client_audience ORDER BY 1"
    ).fetchall()
    blob = repr((rows, crm, bindings)).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class AnalysisReportLocalBrowserFixturePostgresTests(unittest.TestCase):
    def test_fixture_twice_and_fail_closed_identity(self) -> None:
        stack = PostgresIntegrationStack()
        shared_before = canonical_shared_fingerprint()
        try:
            stack.start()
            stack.apply_env()
            with stack._bootstrap() as connection:
                before_invitee = _memberships(connection, INVITEE_SUB)
                employee_before = _employee_fingerprint(connection)
                tenant_before = _memberships(connection, TENANT_A_SUB)
                self.assertEqual(before_invitee, [])
                self.assertEqual(
                    tenant_before,
                    [(local_seed.ENTERPRISE_A, "enterprise_admin")],
                )
                self.assertEqual(
                    _memberships(connection, EMPLOYEE_SUB),
                    [(local_seed.ENTERPRISE_A, "plant_admin")],
                )
            apply()
            apply()
            with stack._bootstrap() as connection:
                self.assertEqual(
                    _memberships(connection, TENANT_A_SUB),
                    [(local_seed.ENTERPRISE_A, "enterprise_admin")],
                )
                self.assertEqual(
                    _memberships(connection, INVITEE_SUB),
                    [(local_seed.ENTERPRISE_B, "plant_admin")],
                )
                self.assertEqual(
                    _memberships(connection, EMPLOYEE_SUB),
                    [(local_seed.ENTERPRISE_A, "plant_admin")],
                )
                self.assertEqual(_employee_fingerprint(connection), employee_before)
                crm_n = connection.execute(
                    "SELECT count(*) FROM f1.crm_account WHERE enterprise_id=%s",
                    (local_seed.ENTERPRISE_A,),
                ).fetchone()
                binding_n = connection.execute(
                    "SELECT count(*) FROM f1.analysis_report_client_audience "
                    "WHERE enterprise_id=%s AND audience_enterprise_id=%s "
                    "AND status='active'",
                    (local_seed.ENTERPRISE_A, local_seed.ENTERPRISE_B),
                ).fetchone()
                self.assertEqual(int(crm_n[0]), 1)
                self.assertEqual(int(binding_n[0]), 1)
                self.assertEqual(
                    int(
                        connection.execute(
                            "SELECT count(*) FROM f1.crm_account WHERE id=%s",
                            (CRM_ACCOUNT_ID,),
                        ).fetchone()[0]
                    ),
                    1,
                )
                self.assertEqual(
                    int(
                        connection.execute(
                            "SELECT count(*) FROM f1.analysis_report_client_audience "
                            "WHERE id=%s",
                            (BINDING_ID,),
                        ).fetchone()[0]
                    ),
                    1,
                )
                fingerprint = _identity_fingerprint(connection)
            self._assert_fail_closed(stack, fingerprint)
        finally:
            stack.stop()
        self.assertEqual(dedicated_counts(), (0, 0, 0))
        self.assertEqual(canonical_shared_fingerprint(), shared_before)

    def _assert_fail_closed(self, stack: PostgresIntegrationStack, fingerprint: str) -> None:
        original = dict(os.environ)
        try:
            os.environ["F1_LOCAL_ENGINEERING"] = "0"
            with self.assertRaises(RuntimeError) as raised:
                apply()
            self.assertEqual(str(raised.exception), "LOCAL_REPORT_FIXTURE_ENGINEERING_REQUIRED")
            os.environ["F1_LOCAL_ENGINEERING"] = "1"
            os.environ["LOCAL_ANALYSIS_REPORT_PGINT_PROJECT_NAME"] = "anhuan-ar-pgint-ffffffffffff"
            with self.assertRaises(RuntimeError) as raised:
                apply()
            self.assertEqual(str(raised.exception), "LOCAL_REPORT_FIXTURE_PROJECT_MISMATCH")
            os.environ["LOCAL_ANALYSIS_REPORT_PGINT_PROJECT_NAME"] = original[
                "LOCAL_ANALYSIS_REPORT_PGINT_PROJECT_NAME"
            ]
            os.environ["F1_PG_DATABASE"] = "f1_arpg_ffffffffffff"
            with self.assertRaises(RuntimeError) as raised:
                apply()
            self.assertEqual(str(raised.exception), "LOCAL_REPORT_FIXTURE_DATABASE_MISMATCH")
            os.environ["F1_PG_DATABASE"] = original["F1_PG_DATABASE"]
            receipt = Path(stack.control_dir) / "identity.receipt"
            receipt.chmod(0o644)
            with self.assertRaises(RuntimeError) as raised:
                apply()
            self.assertEqual(str(raised.exception), "LOCAL_REPORT_FIXTURE_CONTROL_DIR_INVALID")
            receipt.chmod(0o600)
            with stack._bootstrap() as connection:
                connection.execute("UPDATE f1.alembic_version SET version_num='f1_0016'")
                connection.commit()
            try:
                with self.assertRaises(RuntimeError) as raised:
                    apply()
                self.assertEqual(str(raised.exception), "LOCAL_REPORT_FIXTURE_HEAD_MISMATCH")
            finally:
                with stack._bootstrap() as connection:
                    connection.execute("UPDATE f1.alembic_version SET version_num='f1_0017'")
                    connection.commit()
            with stack._bootstrap() as connection:
                self.assertEqual(_identity_fingerprint(connection), fingerprint)
                self.assertEqual(
                    _memberships(connection, EMPLOYEE_SUB),
                    [(local_seed.ENTERPRISE_A, "plant_admin")],
                )
        finally:
            os.environ.clear()
            os.environ.update(original)


if __name__ == "__main__":
    unittest.main()
