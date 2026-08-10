"""Single lightweight contract check for the P5 prototype."""
from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "infra/f1/alembic/versions/f1_0008_policy_workflow.py"
MODELS = ROOT / "src/platform_foundation/f1/models.py"
ROUTER = ROOT / "src/platform_foundation/f1/api/routers/p5_policy_workflow.py"
MAIN = ROOT / "src/platform_foundation/f1/api/main.py"
BACKEND = ROOT / "src/platform_foundation/f1/features/p5"
FRONTEND = ROOT / "src/web/src/features/p5"


class P5PolicyWorkflowContractTests(unittest.TestCase):
    def test_python_sources_compile(self) -> None:
        for path in [MIGRATION, MODELS, ROUTER, MAIN, *sorted(BACKEND.glob("*.py"))]:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")

    def test_linear_migration_and_five_tenant_tables(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        self.assertIn('revision: str = "f1_0008"', source)
        self.assertIn('down_revision: str | None = "f1_0007"', source)
        for table in (
            "policy_source",
            "policy_version",
            "policy_review_event",
            "policy_impact_candidate",
            "policy_impact_task",
        ):
            self.assertEqual(source.count(f"CREATE TABLE f1.{table} ("), 1)
            self.assertIn(f"ALTER TABLE f1.{{table}} FORCE ROW LEVEL SECURITY", source)
        self.assertNotIn("SECURITY DEFINER", source)

    def test_review_and_publication_guards_bind_current_actor(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8")
        for token in (
            "P5_POLICY_VERSION_CONTENT_IMMUTABLE",
            "P5_POLICY_VERSION_TRANSITION_INVALID",
            "NEW.submitted_by_user_id = actor_id",
            "NEW.approved_by_user_id = actor_id",
            "NEW.published_by_user_id = actor_id",
            "actor_id <> OLD.submitted_by_user_id",
            "P5_POLICY_REVIEW_EVENT_REQUIRED",
            "P5_POLICY_REVIEW_EVENT_MISMATCH",
            "NEW.occurred_at := statement_timestamp()",
        ):
            self.assertIn(token, source)
        self.assertIn("DEFERRABLE INITIALLY DEFERRED", source)
        self.assertIn("REVOKE UPDATE, DELETE ON f1.policy_review_event", source)
        self.assertIn("P5_DOWNGRADE_REQUIRES_EMPTY_SCOPE", source)

    def test_models_mirror_policy_entities(self) -> None:
        source = MODELS.read_text(encoding="utf-8")
        for class_name in (
            "PolicySource",
            "PolicyVersion",
            "PolicyReviewEvent",
            "PolicyImpactCandidate",
            "PolicyImpactTask",
        ):
            self.assertEqual(source.count(f"class {class_name}(Base):"), 1)
            self.assertIn(f'"{class_name}"', source)
        self.assertIn("policy_version_published_uq", source)
        self.assertIn("policy_impact_task_owner_enterprise_fk", source)

    def test_pure_role_and_state_actions(self) -> None:
        from platform_foundation.f1.features.p5.contracts import (
            impact_task_actions,
            version_actions,
        )

        actor = object()
        self.assertEqual(
            version_actions(
                "enterprise_admin",
                {"workflow_status": "draft", "submitted_by_user_id": None},
                actor,
            ),
            ["view", "submit"],
        )
        self.assertEqual(
            version_actions(
                "auditor",
                {"workflow_status": "in_review", "submitted_by_user_id": actor},
                actor,
            ),
            ["view"],
        )
        self.assertEqual(
            version_actions(
                "auditor",
                {"workflow_status": "in_review", "submitted_by_user_id": object()},
                actor,
            ),
            ["view", "approve", "reject"],
        )
        self.assertEqual(
            impact_task_actions(None, "open", is_owner=True),
            ["view", "start", "complete"],
        )
        self.assertEqual(
            impact_task_actions("auditor", "completed", is_owner=False),
            ["view"],
        )

    def test_router_main_and_transaction_order_contract(self) -> None:
        router = ROUTER.read_text(encoding="utf-8")
        self.assertEqual(router.count("@router."), 17)
        for path in (
            '"/sources"',
            '"/sources/{source_id}"',
            '"/sources/{source_id}/versions"',
            '"/versions/{version_id}"',
            '"/versions/{version_id}/submit"',
            '"/versions/{version_id}/approve"',
            '"/versions/{version_id}/reject"',
            '"/versions/{version_id}/publish"',
            '"/impacts"',
            '"/impacts/{impact_id}"',
            '"/impacts/{impact_id}/tasks"',
            '"/impact-tasks/{task_id}"',
            '"/search"',
        ):
            self.assertIn(path, router)
        main = MAIN.read_text(encoding="utf-8")
        self.assertIn("p5_policy_workflow.router", main)
        self.assertIn('prefix="/api/v1/policy-workflow"', main)
        workflow = (BACKEND / "workflow.py").read_text(encoding="utf-8")
        self.assertLess(
            workflow.index("UPDATE f1.policy_version SET {update_sql}"),
            workflow.index("INSERT INTO f1.policy_review_event"),
        )
        self.assertEqual(workflow.count("await session.commit()"), 1)

    def test_frontend_routes_and_professional_boundaries(self) -> None:
        app = (ROOT / "src/web/src/App.tsx").read_text(encoding="utf-8")
        layout = (ROOT / "src/web/src/pages/Layout.tsx").read_text(encoding="utf-8")
        for path in (
            'path="policies"',
            'path="policies/sources/:sourceId"',
            'path="policies/versions/:versionId"',
            'path="policy-impact"',
        ):
            self.assertIn(path, app)
        self.assertIn('key: "/policies"', layout)
        self.assertIn('key: "/policy-impact"', layout)
        p5_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(FRONTEND.rglob("*.ts*"))
        )
        for boundary in (
            "CANDIDATE_ONLY",
            "INTERNAL_REVIEW_ONLY",
            "NOT_LEGAL_ADVICE",
            "PROFESSIONAL_JUDGMENT_REQUIRED",
            "NOT_PRODUCTION",
        ):
            self.assertIn(boundary, p5_source)
        self.assertNotIn("/content", p5_source)
        self.assertNotIn("createObjectURL", p5_source)

    def test_frontend_typecheck_without_build(self) -> None:
        tsc = ROOT / "src/web/node_modules/.bin/tsc"
        self.assertTrue(tsc.is_file(), "P5_TYPESCRIPT_COMPILER_MISSING")
        completed = subprocess.run(
            [str(tsc), "--noEmit", "-p", str(ROOT / "src/web/tsconfig.app.json")],
            cwd=ROOT / "src/web",
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
        self.assertEqual(completed.returncode, 0, "P5_TYPESCRIPT_TYPECHECK_FAILED")


if __name__ == "__main__":
    unittest.main()
