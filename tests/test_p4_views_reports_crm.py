"""Single lightweight contract check for the P4 prototype."""
from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "infra/f1/alembic/versions/f1_0007_business_views_reports_crm.py"
MODELS = ROOT / "src/platform_foundation/f1/models.py"
ROUTER = ROOT / "src/platform_foundation/f1/api/routers/p4_views_reports.py"
MAIN = ROOT / "src/platform_foundation/f1/api/main.py"
P4_BACKEND = ROOT / "src/platform_foundation/f1/features/p4"
P4_FRONTEND = ROOT / "src/web/src/features/p4"


class P4ViewsReportsCrmContractTests(unittest.TestCase):
    def test_python_sources_compile(self) -> None:
        paths = [MIGRATION, MODELS, ROUTER, MAIN, *sorted(P4_BACKEND.glob("*.py"))]
        for path in paths:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")

    def test_migration_is_single_linear_0007(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        self.assertIn('revision: str = "f1_0007"', source)
        self.assertIn('down_revision: str | None = "f1_0006"', source)
        for table in (
            "crm_account",
            "crm_contact",
            "crm_follow_up",
            "business_report",
            "business_report_version",
            "business_report_artifact",
        ):
            self.assertEqual(source.count(f"CREATE TABLE f1.{table} ("), 1)
            self.assertIn(f"ALTER TABLE f1.{{table}} FORCE ROW LEVEL SECURITY", source)

    def test_tenant_and_immutability_boundaries_are_explicit(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("FOREIGN KEY (enterprise_id,"), 9)
        self.assertIn("P4_REPORT_VERSION_IMMUTABLE", source)
        self.assertIn("P4_REPORT_ARTIFACT_MISMATCH", source)
        self.assertIn("P4_REPORT_MANAGER_EDIT_REQUIRED", source)
        self.assertIn("assignment.capacity = 'consultant'", source)
        self.assertIn("REVOKE UPDATE ON f1.crm_follow_up", source)
        self.assertIn("P4_DOWNGRADE_REQUIRES_EMPTY_SCOPE", source)
        self.assertLess(
            source.index("NO FORCE ROW LEVEL SECURITY"),
            source.index("P4_DOWNGRADE_REQUIRES_EMPTY_SCOPE"),
        )
        self.assertNotIn("SECURITY DEFINER", source)

    def test_models_mirror_six_p4_entities(self) -> None:
        source = MODELS.read_text(encoding="utf-8")
        for class_name in (
            "CrmAccount",
            "CrmContact",
            "CrmFollowUp",
            "BusinessReport",
            "BusinessReportVersion",
            "BusinessReportArtifact",
        ):
            self.assertEqual(source.count(f"class {class_name}(Base):"), 1)
            self.assertIn(f'"{class_name}"', source)
        self.assertIn("business_report_version_current_uq", source)
        self.assertIn("canonical_snapshot: Mapped[dict[str, object]]", source)

    def test_backend_routes_and_main_mount_are_complete(self) -> None:
        router = ROUTER.read_text(encoding="utf-8")
        self.assertEqual(router.count("@router."), 14)
        for path in (
            '"/dashboard"',
            '"/crm/accounts"',
            '"/crm/accounts/{account_id}"',
            '"/crm/accounts/{account_id}/contacts"',
            '"/crm/contacts/{contact_id}"',
            '"/crm/accounts/{account_id}/follow-ups"',
            '"/reports"',
            '"/reports/{report_id}"',
            '"/reports/{report_id}/versions"',
            '"/report-versions/{version_id}"',
            '"/reports/{report_id}/archive"',
        ):
            self.assertIn(path, router)
        main = MAIN.read_text(encoding="utf-8")
        self.assertIn("p4_views_reports.router", main)
        self.assertIn('prefix="/api/v1/views-reports"', main)

    def test_report_snapshot_is_canonical_and_bounded(self) -> None:
        os.environ.setdefault(
            "F1_KEYCLOAK_ISSUER_URL",
            "http://127.0.0.1:31001/realms/anhuan",
        )
        from platform_foundation.f1.features.p4.reports import (
            _canonical_snapshot_bytes,
        )

        first = _canonical_snapshot_bytes({"z": 1, "a": {"b": 2}})
        second = _canonical_snapshot_bytes({"a": {"b": 2}, "z": 1})
        self.assertEqual(first, second)
        self.assertEqual(first, b'{"a":{"b":2},"z":1}')
        source = (P4_BACKEND / "reports.py").read_text(encoding="utf-8")
        self.assertIn("4 * 1024 * 1024", source)
        self.assertIn("task.quarantine_status = 'released'", source)
        self.assertNotIn("object_key", source)
        self.assertNotIn("display_filename", source)

    def test_frontend_routes_and_non_release_boundary(self) -> None:
        app = (ROOT / "src/web/src/App.tsx").read_text(encoding="utf-8")
        layout = (ROOT / "src/web/src/pages/Layout.tsx").read_text(encoding="utf-8")
        for path in (
            'path="dashboard"',
            'path="crm"',
            'path="crm/:accountId"',
            'path="reports"',
            'path="reports/:reportId"',
            'path="reports/:reportId/versions/:versionId"',
        ):
            self.assertIn(path, app)
        for key in ('key: "/dashboard"', 'key: "/crm"', 'key: "/reports"'):
            self.assertIn(key, layout)
        p4_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(P4_FRONTEND.rglob("*.ts*"))
        )
        for boundary in (
            "BUSINESS_SNAPSHOT_ONLY",
            "NOT_SIGNED",
            "NOT_PUBLISHED",
            "NOT_PRODUCTION",
        ):
            self.assertIn(boundary, p4_source)
        self.assertNotIn("/content", p4_source)
        self.assertNotIn("createObjectURL", p4_source)

    def test_frontend_typecheck_without_build(self) -> None:
        tsc = ROOT / "src/web/node_modules/.bin/tsc"
        self.assertTrue(tsc.is_file(), "P4_TYPESCRIPT_COMPILER_MISSING")
        completed = subprocess.run(
            [str(tsc), "--noEmit", "-p", str(ROOT / "src/web/tsconfig.app.json")],
            cwd=ROOT / "src/web",
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
        self.assertEqual(
            completed.returncode,
            0,
            "P4_TYPESCRIPT_TYPECHECK_FAILED",
        )


if __name__ == "__main__":
    unittest.main()
