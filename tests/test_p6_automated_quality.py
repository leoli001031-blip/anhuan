"""Single lightweight contract check for the P6 synthetic-quality prototype."""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "infra/f1/alembic/versions/f1_0009_automated_quality.py"
MODELS = ROOT / "src/platform_foundation/f1/models.py"
ROUTER = ROOT / "src/platform_foundation/f1/api/routers/p6_automated_quality.py"
MAIN = ROOT / "src/platform_foundation/f1/api/main.py"
BACKEND = ROOT / "src/platform_foundation/f1/features/p6"
FRONTEND = ROOT / "src/web/src/features/p6"


class P6AutomatedQualityContractTests(unittest.TestCase):
    def test_python_sources_compile(self) -> None:
        for path in [MIGRATION, MODELS, ROUTER, MAIN, *sorted(BACKEND.glob("*.py"))]:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")

    def test_linear_migration_and_database_guards(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        self.assertIn('revision: str = "f1_0009"', source)
        self.assertIn('down_revision: str | None = "f1_0008"', source)
        for table in ("quality_suite", "quality_scenario", "quality_run", "quality_result", "quality_disagreement"):
            self.assertEqual(source.count(f"CREATE TABLE f1.{table} ("), 1)
            self.assertIn(f"ALTER TABLE f1.{{table}} FORCE ROW LEVEL SECURITY", source)
        for token in (
            "P6_RESULT_RUN_SCOPE_INVALID",
            "run_suite <> scenario_suite",
            "run_status <> 'running'",
            "P6_RUN_RESULT_COUNTS_MISMATCH",
            "P6_DISAGREEMENT_RESULT_INVALID",
            "result_status <> 'failed'",
            "declared_kind IS DISTINCT FROM NEW.kind",
            "REVOKE UPDATE, DELETE ON f1.quality_result",
            "P6_DOWNGRADE_REQUIRES_EMPTY_SCOPE",
        ):
            self.assertIn(token, source)
        self.assertNotIn("SECURITY DEFINER", source)

    def test_models_mirror_quality_entities(self) -> None:
        source = MODELS.read_text(encoding="utf-8")
        for class_name in ("QualitySuite", "QualityScenario", "QualityRun", "QualityResult", "QualityDisagreement"):
            self.assertEqual(source.count(f"class {class_name}(Base):"), 1)
            self.assertIn(f'"{class_name}"', source)
        self.assertIn("quality_result_run_scenario_uq", source)
        self.assertIn("quality_disagreement_result_enterprise_fk", source)

    def test_oracle_is_deterministic_and_finite(self) -> None:
        from platform_foundation.f1.features.p6.oracle import evaluate, normalize_payloads

        digest = "a" * 64
        config, observation, scenario_sha = normalize_payloads(
            "exact_match",
            {"schema_version": 1, "expected_sha256": digest},
            {"schema_version": 1, "actual_sha256": digest},
        )
        first = evaluate(scenario_type="exact_match", oracle_config=config, synthetic_observation=observation, scenario_sha256=scenario_sha)
        second = evaluate(scenario_type="exact_match", oracle_config=config, synthetic_observation=observation, scenario_sha256=scenario_sha)
        self.assertEqual(first, second)
        self.assertEqual((first.status, first.reason_code), ("passed", "EXACT_MATCH"))
        with self.assertRaises(HTTPException) as caught:
            normalize_payloads("threshold", {"schema_version": 1, "max_value": 10}, {"schema_version": 1, "value": 10 ** 400})
        self.assertEqual(caught.exception.status_code, 422)

    def test_disagreement_review_does_not_change_verdict(self) -> None:
        from platform_foundation.f1.features.p6.contracts import disagreement_actions
        from platform_foundation.f1.features.p6.oracle import evaluate, normalize_payloads

        config, observation, scenario_sha = normalize_payloads(
            "disagreement_max",
            {"schema_version": 1, "max_score": 0.1, "disagreement_kind": "parser"},
            {"schema_version": 1, "left_sha256": "b" * 64, "right_sha256": "c" * 64, "score": 0.5},
        )
        decision = evaluate(scenario_type="disagreement_max", oracle_config=config, synthetic_observation=observation, scenario_sha256=scenario_sha)
        self.assertEqual(decision.status, "failed")
        self.assertEqual(decision.disagreement["kind"], "parser")
        self.assertEqual(disagreement_actions("auditor", "open"), ["view", "acknowledge", "waive"])
        self.assertEqual(disagreement_actions("auditor", "waived"), ["view"])

    def test_router_main_and_frontend_contract(self) -> None:
        router = ROUTER.read_text(encoding="utf-8")
        self.assertEqual(router.count("@router."), 10)
        main = MAIN.read_text(encoding="utf-8")
        self.assertIn("p6_automated_quality.router", main)
        self.assertIn('prefix="/api/v1/automated-quality"', main)
        app = (ROOT / "src/web/src/App.tsx").read_text(encoding="utf-8")
        layout = (ROOT / "src/web/src/pages/Layout.tsx").read_text(encoding="utf-8")
        for path in ('path="quality"', 'path="quality/suites/:suiteId"', 'path="quality/runs/:runId"', 'path="quality/disagreements"'):
            self.assertIn(path, app)
        self.assertIn('key: "/quality"', layout)
        frontend_source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(FRONTEND.rglob("*.ts*")))
        for boundary in ("SYNTHETIC_ORACLE_ONLY", "NON_GOLD", "ACCURACY_NOT_EVALUATED", "NO_EXTERNAL_MODEL_CALLS", "NOT_PRODUCTION"):
            self.assertIn(boundary, frontend_source)

    def test_frontend_typecheck_without_build(self) -> None:
        tsc = ROOT / "src/web/node_modules/.bin/tsc"
        self.assertTrue(tsc.is_file(), "P6_TYPESCRIPT_COMPILER_MISSING")
        completed = subprocess.run(
            [str(tsc), "--noEmit", "-p", str(ROOT / "src/web/tsconfig.app.json")],
            cwd=ROOT / "src/web",
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
        self.assertEqual(completed.returncode, 0, "P6_TYPESCRIPT_TYPECHECK_FAILED")


if __name__ == "__main__":
    unittest.main()
