"""Single lightweight contract check for the P7 local rehearsal prototype."""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "infra/f1/alembic/versions/f1_0010_local_rehearsal.py"
MODELS = ROOT / "src/platform_foundation/f1/models.py"
ROUTER = ROOT / "src/platform_foundation/f1/api/routers/p7_local_rehearsal.py"
MAIN = ROOT / "src/platform_foundation/f1/api/main.py"
BACKEND = ROOT / "src/platform_foundation/f1/features/p7"
FRONTEND = ROOT / "src/web/src/features/p7"


class P7LocalRehearsalContractTests(unittest.TestCase):
    def test_python_sources_compile(self) -> None:
        for path in [MIGRATION, MODELS, ROUTER, MAIN, *sorted(BACKEND.glob("*.py"))]:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")

    def test_linear_migration_and_four_tenant_tables(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        self.assertIn('revision: str = "f1_0010"', source)
        self.assertIn('down_revision: str | None = "f1_0009"', source)
        for table in ("rehearsal_plan", "rehearsal_check", "rehearsal_run", "rehearsal_check_result"):
            self.assertEqual(source.count(f"CREATE TABLE f1.{table} ("), 1)
            self.assertIn(f"ALTER TABLE f1.{{table}} FORCE ROW LEVEL SECURITY", source)
        self.assertNotIn("SECURITY DEFINER", source)
        self.assertIn("P7_DOWNGRADE_REQUIRES_EMPTY_SCOPE", source)

    def test_snapshot_terminal_and_completion_guards(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        for token in (
            "P7_RESULT_SNAPSHOT_INVALID",
            "NEW.check_key <> source_check.check_key",
            "P7_RESULT_SNAPSHOT_IMMUTABLE",
            "OLD.status <> 'pending'",
            "P7_RUN_PASS_GATE_FAILED",
            "P7_RUN_FAILURE_GATE_INVALID",
            "actual_failed + actual_blocked = 0",
            "NEW.rollback_required := (NEW.status = 'failed')",
            "p7_rehearsal_run_operator_lock",
            "WITH CHECK (false)",
            "REVOKE DELETE ON f1.rehearsal_plan",
        ):
            self.assertIn(token, source)

    def test_models_mirror_rehearsal_entities(self) -> None:
        source = MODELS.read_text(encoding="utf-8")
        for class_name in ("RehearsalPlan", "RehearsalCheck", "RehearsalRun", "RehearsalCheckResult"):
            self.assertEqual(source.count(f"class {class_name}(Base):"), 1)
            self.assertIn(f'"{class_name}"', source)
        self.assertIn("rehearsal_check_result_run_check_uq", source)
        self.assertIn("rehearsal_check_result_recorder_enterprise_fk", source)

    def test_pure_actions_and_reason_pairing(self) -> None:
        from platform_foundation.f1.features.p7.contracts import reason_allowed, result_actions, run_actions

        self.assertEqual(result_actions("auditor", "running", "pending"), ["view", "record"])
        self.assertEqual(result_actions("auditor", "running", "passed"), ["view"])
        self.assertEqual(run_actions("enterprise_admin", "running", 0), ["view", "complete", "cancel"])
        self.assertEqual(run_actions("enterprise_admin", "running", 1), ["view", "cancel"])
        self.assertTrue(reason_allowed("blocked", "MANUAL_CHECK_BLOCKED"))
        self.assertFalse(reason_allowed("passed", "MANUAL_CHECK_FAILED"))

    def test_router_main_and_frontend_boundaries(self) -> None:
        router = ROUTER.read_text(encoding="utf-8")
        self.assertEqual(router.count("@router."), 11)
        main = MAIN.read_text(encoding="utf-8")
        self.assertIn("p7_local_rehearsal.router", main)
        self.assertIn('prefix="/api/v1/local-rehearsal"', main)
        backend_source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(BACKEND.glob("*.py")))
        for forbidden in (
            "import subprocess",
            "subprocess.",
            "os.system(",
            "docker run",
            "kubectl ",
            "ssh ",
        ):
            self.assertNotIn(forbidden, backend_source.lower())
        app = (ROOT / "src/web/src/App.tsx").read_text(encoding="utf-8")
        layout = (ROOT / "src/web/src/pages/Layout.tsx").read_text(encoding="utf-8")
        for path in ('path="rehearsal"', 'path="rehearsal/plans/:planId"', 'path="rehearsal/runs/:runId"'):
            self.assertIn(path, app)
        self.assertIn('key: "/rehearsal"', layout)
        frontend_source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(FRONTEND.rglob("*.ts*")))
        for boundary in ("LOCAL_REHEARSAL_ONLY", "MANUAL_EXECUTION", "NO_PRODUCTION_ACCESS", "NO_DEPLOYMENT", "NOT_PRODUCTION"):
            self.assertIn(boundary, frontend_source)

    def test_frontend_typecheck_without_build(self) -> None:
        tsc = ROOT / "src/web/node_modules/.bin/tsc"
        self.assertTrue(tsc.is_file(), "P7_TYPESCRIPT_COMPILER_MISSING")
        completed = subprocess.run(
            [str(tsc), "--noEmit", "-p", str(ROOT / "src/web/tsconfig.app.json")],
            cwd=ROOT / "src/web",
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
        self.assertEqual(completed.returncode, 0, "P7_TYPESCRIPT_TYPECHECK_FAILED")


if __name__ == "__main__":
    unittest.main()
