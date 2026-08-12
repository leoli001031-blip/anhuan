"""Offline targeted contracts for P2 Business Workbench Wave 2."""
from __future__ import annotations

import ast
import inspect
import textwrap
import unittest
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.routing import APIRoute
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from platform_foundation.f1 import business_workbench, models
from platform_foundation.f1.api.routers import findings
from platform_foundation.f1.audit import add_event
from tests import p2_wave2_smoke


ROOT = Path(__file__).resolve().parents[1]
P2_MIGRATION = ROOT / "infra/f1/alembic/versions/f1_0005_business_workbench.py"
MAIN_API = ROOT / "src/platform_foundation/f1/api/main.py"


def _constraint_map(table, constraint_type):
    return {
        constraint.name: constraint
        for constraint in table.constraints
        if isinstance(constraint, constraint_type)
    }


def _foreign_key_pairs(constraint: ForeignKeyConstraint) -> tuple[tuple[str, str], ...]:
    return tuple(
        (element.parent.name, element.target_fullname)
        for element in constraint.elements
    )


class P2Wave2MigrationContractTests(unittest.TestCase):
    def test_wave2_remains_on_the_single_linear_f1_head(self) -> None:
        config = Config(str(ROOT / "infra/f1/alembic.ini"))
        script = ScriptDirectory.from_config(config)

        self.assertEqual(script.get_heads(), ["f1_0014"])
        revision = script.get_revision("f1_0005")
        self.assertIsNotNone(revision)
        self.assertEqual(revision.down_revision, "f1_0004")

    def test_all_wave2_tables_enable_and_force_rls(self) -> None:
        source = P2_MIGRATION.read_text(encoding="utf-8")
        self.assertIn(
            'for table in ("site_visit", "finding", "corrective_action", "finding_review"):',
            source,
        )
        self.assertIn(
            'op.execute(f"ALTER TABLE f1.{table} ENABLE ROW LEVEL SECURITY")',
            source,
        )
        self.assertIn(
            'op.execute(f"ALTER TABLE f1.{table} FORCE ROW LEVEL SECURITY")',
            source,
        )
        self.assertIn("enterprise_id = f1.current_enterprise_id()", source)
        self.assertIn("f1.session_authorized(enterprise_id)", source)

    def test_finding_trigger_matches_the_domain_state_machine(self) -> None:
        source = P2_MIGRATION.read_text(encoding="utf-8")
        markers = (
            "OLD.status = 'open' AND NEW.status = 'rectifying'",
            "OLD.status = 'rejected' AND NEW.status = 'rectifying'",
            "OLD.status = 'rectifying' AND NEW.status = 'submitted'",
            "OLD.status = 'submitted' AND NEW.status = 'reviewing'",
            "NEW.status IN ('passed','rejected')",
            "OLD.status = 'passed' AND NEW.status = 'closed'",
            "P2_FINDING_TRANSITION_INVALID",
        )
        for marker in markers:
            self.assertIn(marker, source)

    def test_corrective_actions_and_reviews_are_append_only(self) -> None:
        source = P2_MIGRATION.read_text(encoding="utf-8")
        upgrade_source = source.split("def downgrade()", 1)[0]
        self.assertIn(
            "GRANT SELECT, INSERT ON f1.corrective_action, f1.finding_review TO f1_api",
            upgrade_source,
        )
        for table in ("corrective_action", "finding_review"):
            with self.subTest(table=table):
                self.assertNotIn(
                    f"GRANT SELECT, INSERT, UPDATE ON f1.{table}",
                    upgrade_source,
                )
                self.assertNotIn(f"ON f1.{table}\n        FOR UPDATE", upgrade_source)
                self.assertNotIn(f"ON f1.{table}\n        FOR DELETE", upgrade_source)


class P2Wave2ModelContractTests(unittest.TestCase):
    def test_site_visit_tenant_foreign_keys(self) -> None:
        foreign_keys = _constraint_map(models.SiteVisit.__table__, ForeignKeyConstraint)
        self.assertEqual(
            _foreign_key_pairs(foreign_keys["site_visit_case_enterprise_fk"]),
            (
                ("enterprise_id", "f1.service_case.enterprise_id"),
                ("service_case_id", "f1.service_case.id"),
            ),
        )
        self.assertEqual(
            _foreign_key_pairs(foreign_keys["site_visit_creator_enterprise_fk"]),
            (
                ("enterprise_id", "f1.enterprise_user.enterprise_id"),
                ("created_by_user_id", "f1.enterprise_user.user_id"),
            ),
        )

    def test_finding_tenant_foreign_keys(self) -> None:
        foreign_keys = _constraint_map(models.Finding.__table__, ForeignKeyConstraint)
        expected = {
            "finding_case_enterprise_fk": (
                ("enterprise_id", "f1.service_case.enterprise_id"),
                ("service_case_id", "f1.service_case.id"),
            ),
            "finding_visit_enterprise_fk": (
                ("enterprise_id", "f1.site_visit.enterprise_id"),
                ("site_visit_id", "f1.site_visit.id"),
            ),
            "finding_responsible_enterprise_fk": (
                ("enterprise_id", "f1.enterprise_user.enterprise_id"),
                ("responsible_user_id", "f1.enterprise_user.user_id"),
            ),
            "finding_creator_enterprise_fk": (
                ("enterprise_id", "f1.enterprise_user.enterprise_id"),
                ("created_by_user_id", "f1.enterprise_user.user_id"),
            ),
        }
        for name, pairs in expected.items():
            with self.subTest(constraint=name):
                self.assertEqual(_foreign_key_pairs(foreign_keys[name]), pairs)

    def test_action_and_review_tenant_foreign_keys(self) -> None:
        tables = (
            (
                models.CorrectiveAction.__table__,
                {
                    "corrective_action_finding_enterprise_fk": (
                        ("enterprise_id", "f1.finding.enterprise_id"),
                        ("finding_id", "f1.finding.id"),
                    ),
                    "corrective_action_submitter_enterprise_fk": (
                        ("enterprise_id", "f1.enterprise_user.enterprise_id"),
                        ("submitted_by_user_id", "f1.enterprise_user.user_id"),
                    ),
                },
            ),
            (
                models.FindingReview.__table__,
                {
                    "finding_review_finding_enterprise_fk": (
                        ("enterprise_id", "f1.finding.enterprise_id"),
                        ("finding_id", "f1.finding.id"),
                    ),
                    "finding_review_reviewer_enterprise_fk": (
                        ("enterprise_id", "f1.enterprise_user.enterprise_id"),
                        ("reviewer_user_id", "f1.enterprise_user.user_id"),
                    ),
                },
            ),
        )
        for table, expected in tables:
            foreign_keys = _constraint_map(table, ForeignKeyConstraint)
            for name, pairs in expected.items():
                with self.subTest(constraint=name):
                    self.assertEqual(_foreign_key_pairs(foreign_keys[name]), pairs)

    def test_all_wave2_tables_have_tenant_identity_uniques(self) -> None:
        expected = (
            (models.SiteVisit.__table__, "site_visit_enterprise_id_id_uq"),
            (models.Finding.__table__, "finding_enterprise_id_id_uq"),
            (
                models.CorrectiveAction.__table__,
                "corrective_action_enterprise_id_id_uq",
            ),
            (models.FindingReview.__table__, "finding_review_enterprise_id_id_uq"),
        )
        for table, name in expected:
            with self.subTest(constraint=name):
                unique = _constraint_map(table, UniqueConstraint)[name]
                self.assertEqual(
                    tuple(column.name for column in unique.columns),
                    ("enterprise_id", "id"),
                )

    def test_finding_severity_status_and_context_constraints(self) -> None:
        checks = _constraint_map(models.Finding.__table__, CheckConstraint)
        severity_sql = str(checks["finding_severity_ck"].sqltext)
        status_sql = str(checks["finding_status_ck"].sqltext)
        for severity in models.FINDING_SEVERITIES:
            self.assertIn(severity, severity_sql)
        for status in models.FINDING_STATUSES:
            self.assertIn(status, status_sql)
        self.assertIn(
            "service_case_id IS NOT NULL OR site_visit_id IS NOT NULL",
            str(checks["finding_context_required_ck"].sqltext),
        )

    def test_action_revisions_and_review_decisions_are_constrained(self) -> None:
        action_checks = _constraint_map(
            models.CorrectiveAction.__table__, CheckConstraint
        )
        self.assertIn("revision > 0", str(action_checks["corrective_action_revision_ck"].sqltext))
        uniques = _constraint_map(models.CorrectiveAction.__table__, UniqueConstraint)
        revision_unique = uniques["corrective_action_finding_revision_uq"]
        self.assertEqual(
            tuple(column.name for column in revision_unique.columns),
            ("enterprise_id", "finding_id", "revision"),
        )
        review_checks = _constraint_map(models.FindingReview.__table__, CheckConstraint)
        decision_sql = str(review_checks["finding_review_decision_ck"].sqltext)
        for decision in models.FINDING_REVIEW_DECISIONS:
            self.assertIn(decision, decision_sql)


class P2Wave2PureWorkflowContractTests(unittest.TestCase):
    def test_finding_scope_contracts(self) -> None:
        self.assertEqual(
            business_workbench.FINDING_SCOPE_STATUSES,
            {
                "rectification": ("open", "rectifying", "rejected"),
                "review": ("submitted", "reviewing"),
            },
        )

    def test_complete_finding_transition_contract(self) -> None:
        expected = {
            ("open", "start_rectification"): "rectifying",
            ("rejected", "start_rectification"): "rectifying",
            ("rectifying", "submit_correction"): "submitted",
            ("submitted", "start_review"): "reviewing",
            ("reviewing", "pass"): "passed",
            ("reviewing", "reject"): "rejected",
            ("passed", "close"): "closed",
        }
        self.assertEqual(business_workbench.FINDING_TRANSITIONS, expected)
        for (status, action), next_status in expected.items():
            with self.subTest(status=status, action=action):
                self.assertEqual(
                    business_workbench.next_finding_status(status, action),
                    next_status,
                )

    def test_illegal_finding_transitions_fail_closed(self) -> None:
        invalid = (
            ("open", "submit_correction"),
            ("open", "start_review"),
            ("rectifying", "start_review"),
            ("submitted", "pass"),
            ("submitted", "reject"),
            ("reviewing", "close"),
            ("passed", "reject"),
            ("rejected", "submit_correction"),
            ("closed", "start_rectification"),
            ("unknown", "close"),
        )
        for status, action in invalid:
            with self.subTest(status=status, action=action):
                self.assertIsNone(
                    business_workbench.next_finding_status(status, action)
                )

    def test_enterprise_rectification_permissions(self) -> None:
        self.assertIn(
            "start_rectification",
            business_workbench.finding_allowed_actions(
                "enterprise_admin", "open", ()
            ),
        )
        self.assertIn(
            "start_rectification",
            business_workbench.finding_allowed_actions(
                "enterprise_admin", "rejected", ()
            ),
        )
        self.assertEqual(
            business_workbench.finding_allowed_actions(
                "enterprise_admin", "rectifying", ()
            ),
            ["submit_correction"],
        )
        for status in ("submitted", "reviewing"):
            with self.subTest(status=status):
                self.assertEqual(
                    business_workbench.finding_allowed_actions(
                        "enterprise_admin", status, ()
                    ),
                    [],
                )

    def test_reviewer_requires_authorized_role_and_accepted_capacity(self) -> None:
        self.assertTrue(business_workbench.is_finding_reviewer("super_admin", ()))
        self.assertTrue(
            business_workbench.is_finding_reviewer(
                "auditor", ("consultant",)
            )
        )
        rejected = (
            ("auditor", ()),
            ("auditor", ("employee",)),
            ("enterprise_admin", ("consultant",)),
            ("plant_admin", ("consultant",)),
            ("partner", ("consultant",)),
        )
        for role, capacities in rejected:
            with self.subTest(role=role, capacities=capacities):
                self.assertFalse(
                    business_workbench.is_finding_reviewer(role, capacities)
                )

    def test_reviewer_actions_and_registration_fail_closed(self) -> None:
        self.assertEqual(
            business_workbench.finding_allowed_actions(
                "auditor", "submitted", ("consultant",)
            ),
            ["start_review"],
        )
        self.assertEqual(
            business_workbench.finding_allowed_actions(
                "auditor", "reviewing", ("consultant",)
            ),
            ["pass", "reject"],
        )
        self.assertTrue(
            business_workbench.can_register_finding(
                "plant_admin", ("employee",)
            )
        )
        self.assertFalse(
            business_workbench.can_register_finding("partner", ("partner",))
        )
        self.assertEqual(
            business_workbench.finding_collection_allowed_actions(
                "partner", ("partner",)
            ),
            [],
        )


class P2Wave2ApiSurfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.routes = {
            (route.path, method): route
            for route in findings.router.routes
            if isinstance(route, APIRoute)
            for method in route.methods
        }

    def test_wave2_routes_are_complete_and_append_only(self) -> None:
        expected = {
            ("", "GET"),
            ("", "POST"),
            ("/{finding_id}", "GET"),
            ("/{finding_id}", "PATCH"),
            ("/{finding_id}/start-rectification", "POST"),
            ("/{finding_id}/corrective-actions", "POST"),
            ("/{finding_id}/start-review", "POST"),
            ("/{finding_id}/reviews", "POST"),
            ("/{finding_id}/close", "POST"),
        }
        self.assertEqual(set(self.routes), expected)
        self.assertFalse(any(method in ("PUT", "DELETE") for _, method in self.routes))

    def test_main_api_registers_findings_prefix(self) -> None:
        source = MAIN_API.read_text(encoding="utf-8")
        self.assertIn("findings.router", source)
        self.assertIn('prefix="/api/v1/findings"', source)

    def test_list_scope_and_case_filter_are_explicit(self) -> None:
        source = inspect.getsource(findings.list_findings)
        self.assertIn('Literal["all", "rectification", "review"]', source)
        self.assertIn("FINDING_SCOPE_STATUSES[scope]", source)
        self.assertIn("service_case_id", source)
        self.assertIn("enterprise_id=tenant.enterprise_id", source)

    def test_outputs_expose_actions_corrections_and_reviews(self) -> None:
        self.assertIn("items", findings.FindingListOut.model_fields)
        self.assertIn("allowed_actions", findings.FindingListOut.model_fields)
        self.assertIn("allowed_actions", findings.FindingOut.model_fields)
        self.assertIn("corrective_actions", findings.FindingDetailOut.model_fields)
        self.assertIn("reviews", findings.FindingDetailOut.model_fields)
        decision_schema = findings.FindingReviewCreate.model_json_schema()[
            "properties"
        ]["decision"]
        self.assertEqual(decision_schema["enum"], ["passed", "rejected"])

    def test_detail_and_allowed_actions_use_domain_contracts(self) -> None:
        finding_out_source = inspect.getsource(findings._finding_out)
        detail_source = inspect.getsource(findings._detail_out)
        self.assertIn("finding_allowed_actions", finding_out_source)
        self.assertIn("corrective_actions=", detail_source)
        self.assertIn("reviews=", detail_source)
        list_source = inspect.getsource(findings.list_findings)
        self.assertIn("finding_collection_allowed_actions", list_source)
        transition_source = inspect.getsource(findings._transition_finding)
        self.assertIn("next_finding_status", transition_source)
        self.assertIn("FINDING_STATE_CONFLICT", transition_source)

    def test_cross_tenant_objects_are_structurally_hidden_as_404_or_zero(self) -> None:
        list_source = inspect.getsource(findings.list_findings)
        self.assertIn("enterprise_id=tenant.enterprise_id", list_source)
        functions = (
            findings.get_finding,
            findings.update_finding,
            findings.create_finding,
            findings._transition_finding,
            findings.submit_corrective_action,
            findings.review_finding,
        )
        for function in functions:
            source = inspect.getsource(function)
            with self.subTest(function=function.__name__):
                self.assertIn("enterprise_id=tenant.enterprise_id", source)
                self.assertIn("status_code=404", source)
        self.assertIn("WHERE finding.id = :finding_id", inspect.getsource(findings._finding_row))
        lock_source = inspect.getsource(findings._lock_finding)
        self.assertIn("FOR UPDATE", lock_source)
        self.assertIn("status_code=404", lock_source)

    def test_public_review_decision_maps_to_internal_allowed_action(self) -> None:
        source = inspect.getsource(findings.review_finding)
        self.assertIn(
            'review_action = "pass" if body.decision == "passed" else "reject"',
            source,
        )
        self.assertIn("next_finding_status", source)
        self.assertIn("if review_action not in allowed", source)


class P2Wave2TransactionContractTests(unittest.TestCase):
    def _assert_audit_before_commit(self, function) -> None:
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        event_lines: list[int] = []
        commit_lines: list[int] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Await) or not isinstance(node.value, ast.Call):
                continue
            call = node.value
            if isinstance(call.func, ast.Name) and call.func.id == "add_event":
                self.assertTrue(call.args)
                self.assertIsInstance(call.args[0], ast.Name)
                self.assertEqual(call.args[0].id, "session")
                event_lines.append(node.lineno)
            if (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "commit"
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "session"
            ):
                commit_lines.append(node.lineno)
        self.assertEqual(len(event_lines), 1)
        self.assertEqual(len(commit_lines), 1)
        self.assertLess(event_lines[0], commit_lines[0])

    def test_audit_helper_never_commits_outside_the_business_transaction(self) -> None:
        source = inspect.getsource(add_event)
        self.assertIn("await session.execute(", source)
        self.assertIn("INSERT INTO f1.audit_log", source)
        self.assertNotIn("RETURNING", source)
        self.assertNotIn("session.commit", source)

    def test_all_wave2_writes_audit_before_one_commit(self) -> None:
        functions = (
            findings.create_finding,
            findings.update_finding,
            findings._transition_finding,
            findings.submit_corrective_action,
            findings.review_finding,
        )
        for function in functions:
            with self.subTest(function=function.__name__):
                self._assert_audit_before_commit(function)


class P2Wave2OfflineSmokeContractTests(unittest.TestCase):
    def test_smoke_executes_the_required_rejection_resubmission_path(self) -> None:
        self.assertEqual(
            p2_wave2_smoke.TRANSITION_PATH,
            (
                ("start_rectification", "rectifying"),
                ("submit_correction", "submitted"),
                ("start_review", "reviewing"),
                ("reject", "rejected"),
                ("start_rectification", "rectifying"),
                ("submit_correction", "submitted"),
                ("start_review", "reviewing"),
                ("pass", "passed"),
                ("close", "closed"),
            ),
        )
        metrics = p2_wave2_smoke.evaluate()
        self.assertEqual(metrics["sequence_steps"], 9)
        for name in (
            "transition_failures",
            "route_contract_failures",
            "permission_failures",
            "final_status_failures",
            "external_calls",
            "database_calls",
            "docker_calls",
            "formal_calls",
        ):
            self.assertEqual(metrics[name], 0, name)

    def test_smoke_output_is_fixed_aggregate_only(self) -> None:
        self.assertEqual(
            p2_wave2_smoke.render(p2_wave2_smoke.evaluate()),
            "sequence_steps=9 transition_failures=0 route_contract_failures=0 "
            "permission_failures=0 final_status_failures=0 external_calls=0 "
            "database_calls=0 docker_calls=0 formal_calls=0",
        )

    def test_smoke_has_no_service_or_process_imports(self) -> None:
        source = inspect.getsource(p2_wave2_smoke)
        tree = ast.parse(source)
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
        self.assertTrue(
            imported_roots.isdisjoint(
                {"subprocess", "socket", "urllib", "httpx", "requests", "psycopg", "docker"}
            )
        )
        self.assertNotIn("f11_support", source)
        print_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ]
        self.assertEqual(len(print_calls), 1)


if __name__ == "__main__":
    unittest.main()
