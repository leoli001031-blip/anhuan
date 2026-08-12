"""Offline targeted contracts for P2 Business Workbench Wave 1.

These tests deliberately avoid databases, Docker, shared services, and the
paused F1.1.1 formal harness.  They pin the linear migration, tenant-bound
schema, pure action/state rules, and HTTP response surface that the separate
Wave 1 smoke will exercise.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import textwrap
import unittest
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.routing import APIRoute
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from platform_foundation.f1 import business_workbench, models
from platform_foundation.f1.api.routers import service_cases
from platform_foundation.f1.audit import add_event


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "infra/f1/alembic/versions"
P2_MIGRATION = MIGRATIONS / "f1_0005_business_workbench.py"
MAIN_API = ROOT / "src/platform_foundation/f1/api/main.py"

FROZEN_MIGRATION_DIGESTS = {
    "f1_0001_platform_shell_baseline.py": (
        "18af367b01ff9d5cc8fe514aeba8ffc9e486ef1349d984473e4cfe41d49c5edd"
    ),
    "f1_0002_tenant_boundaries_and_workflow.py": (
        "710a2a88f76dadb16a890727f179faa6e44a5ddc27819bd3f6d8be8532b8ca3a"
    ),
    "f1_0003_security_boundaries.py": (
        "a8058d00719d26132b24671a4c802c4cea820d0b6ca1a3555a44fa58385d2da9"
    ),
    "f1_0004_repair_boundaries.py": (
        "b4befabca47939d7522bffbd8ed577717bead8f923e22120ed56ee138028d521"
    ),
}


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


class P2Wave1MigrationContractTests(unittest.TestCase):
    def test_p2_migration_is_the_only_linear_f1_head(self) -> None:
        config = Config(str(ROOT / "infra/f1/alembic.ini"))
        script = ScriptDirectory.from_config(config)

        self.assertEqual(script.get_heads(), ["f1_0011"])
        revision = script.get_revision("f1_0005")
        self.assertIsNotNone(revision)
        self.assertEqual(revision.down_revision, "f1_0004")

    def test_p2_does_not_rewrite_frozen_f1_0001_through_0004(self) -> None:
        observed = {
            name: hashlib.sha256((MIGRATIONS / name).read_bytes()).hexdigest()
            for name in FROZEN_MIGRATION_DIGESTS
        }
        self.assertEqual(observed, FROZEN_MIGRATION_DIGESTS)

    def test_p2_upgrade_only_alters_its_two_new_tables(self) -> None:
        upgrade_source = P2_MIGRATION.read_text(encoding="utf-8").split(
            "def downgrade()", 1
        )[0]
        frozen_tables = (
            "enterprise",
            "plant",
            "user_profile",
            "enterprise_user",
            "document",
            "audit_log",
            "upload_task",
            "outbox",
            "qa_request",
            "invite_jti",
        )
        for table in frozen_tables:
            with self.subTest(table=table):
                self.assertNotIn(f"ALTER TABLE f1.{table} ", upgrade_source)
                self.assertNotIn(f"DROP TABLE f1.{table} ", upgrade_source)
                self.assertNotIn(f"TRUNCATE TABLE f1.{table} ", upgrade_source)

    def test_p2_tables_enable_and_force_rls(self) -> None:
        source = P2_MIGRATION.read_text(encoding="utf-8")
        self.assertIn(
            'for table in ("service_case", "service_assignment"):',
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
        for table in ("service_case", "service_assignment"):
            with self.subTest(table=table):
                self.assertIn(table, source)
        self.assertIn("enterprise_id = f1.current_enterprise_id()", source)
        self.assertIn("f1.session_authorized(enterprise_id)", source)

    def test_p2_assignment_trigger_matches_pure_transition_contract(self) -> None:
        source = P2_MIGRATION.read_text(encoding="utf-8")
        for marker in (
            "OLD.status = 'pending'",
            "NEW.status IN ('accepted','rejected','revoked')",
            "OLD.status = 'accepted' AND NEW.status = 'revoked'",
            "P2_SERVICE_ASSIGNMENT_TRANSITION_INVALID",
        ):
            self.assertIn(marker, source)

    def test_rejected_assignee_remains_visible_to_complete_update_check(self) -> None:
        source = P2_MIGRATION.read_text(encoding="utf-8")
        policy = source.split("CREATE POLICY p2_assignment_select", 1)[1].split(
            '"""', 1
        )[0]
        self.assertIn("status IN ('pending','accepted','rejected')", policy)
        self.assertIn("profile.id = service_assignment.assignee_user_id", policy)


class P2Wave1ModelContractTests(unittest.TestCase):
    def test_service_case_has_tenant_composite_foreign_keys(self) -> None:
        foreign_keys = _constraint_map(
            models.ServiceCase.__table__, ForeignKeyConstraint
        )
        self.assertEqual(
            _foreign_key_pairs(foreign_keys["service_case_plant_enterprise_fk"]),
            (
                ("enterprise_id", "f1.plant.enterprise_id"),
                ("plant_id", "f1.plant.id"),
            ),
        )
        self.assertEqual(
            _foreign_key_pairs(foreign_keys["service_case_creator_enterprise_fk"]),
            (
                ("enterprise_id", "f1.enterprise_user.enterprise_id"),
                ("created_by_user_id", "f1.enterprise_user.user_id"),
            ),
        )

    def test_service_assignment_has_all_tenant_composite_foreign_keys(self) -> None:
        foreign_keys = _constraint_map(
            models.ServiceAssignment.__table__, ForeignKeyConstraint
        )
        expected = {
            "service_assignment_case_enterprise_fk": (
                ("enterprise_id", "f1.service_case.enterprise_id"),
                ("service_case_id", "f1.service_case.id"),
            ),
            "service_assignment_assignee_enterprise_fk": (
                ("enterprise_id", "f1.enterprise_user.enterprise_id"),
                ("assignee_user_id", "f1.enterprise_user.user_id"),
            ),
            "service_assignment_assigner_enterprise_fk": (
                ("enterprise_id", "f1.enterprise_user.enterprise_id"),
                ("assigned_by_user_id", "f1.enterprise_user.user_id"),
            ),
        }
        for name, pairs in expected.items():
            with self.subTest(constraint=name):
                self.assertEqual(_foreign_key_pairs(foreign_keys[name]), pairs)

    def test_both_tables_have_tenant_identity_uniques(self) -> None:
        expected = (
            (
                models.ServiceCase.__table__,
                "service_case_enterprise_id_id_uq",
            ),
            (
                models.ServiceAssignment.__table__,
                "service_assignment_enterprise_id_id_uq",
            ),
        )
        for table, name in expected:
            with self.subTest(constraint=name):
                unique = _constraint_map(table, UniqueConstraint)[name]
                self.assertEqual(
                    tuple(column.name for column in unique.columns),
                    ("enterprise_id", "id"),
                )

    def test_service_case_state_and_window_constraints_are_modelled(self) -> None:
        checks = _constraint_map(models.ServiceCase.__table__, CheckConstraint)
        self.assertIn("service_case_status_ck", checks)
        self.assertIn("service_case_planned_window_ck", checks)
        status_sql = str(checks["service_case_status_ck"].sqltext)
        for status in models.SERVICE_CASE_STATUSES:
            self.assertIn(status, status_sql)
        self.assertIn(
            "planned_end_at >= planned_start_at",
            str(checks["service_case_planned_window_ck"].sqltext),
        )

    def test_assignment_capacity_state_and_timestamp_constraints_are_modelled(self) -> None:
        checks = _constraint_map(
            models.ServiceAssignment.__table__, CheckConstraint
        )
        capacity_sql = str(checks["service_assignment_capacity_ck"].sqltext)
        status_sql = str(checks["service_assignment_status_ck"].sqltext)
        time_sql = str(checks["service_assignment_state_time_ck"].sqltext)
        for capacity in models.SERVICE_ASSIGNMENT_CAPACITIES:
            self.assertIn(capacity, capacity_sql)
        for status in models.SERVICE_ASSIGNMENT_STATUSES:
            self.assertIn(status, status_sql)
        for column in ("responded_at", "revoked_at"):
            self.assertIn(column, time_sql)

    def test_employee_and_consultant_remain_assignment_capacities(self) -> None:
        self.assertEqual(
            models.ROLES,
            (
                "super_admin",
                "enterprise_admin",
                "plant_admin",
                "partner",
                "auditor",
            ),
        )
        self.assertEqual(
            business_workbench.CAPACITIES_BY_MEMBERSHIP_ROLE,
            {
                "plant_admin": ("employee",),
                "auditor": ("consultant",),
                "partner": ("partner",),
            },
        )


class P2Wave1PureBusinessContractTests(unittest.TestCase):
    def test_manager_and_capacity_mapping(self) -> None:
        self.assertTrue(business_workbench.is_manager("super_admin"))
        self.assertTrue(business_workbench.is_manager("enterprise_admin"))
        for role in ("plant_admin", "auditor", "partner", None):
            with self.subTest(role=role):
                self.assertFalse(business_workbench.is_manager(role))
        self.assertEqual(
            business_workbench.allowed_capacities("plant_admin"), ["employee"]
        )
        self.assertEqual(
            business_workbench.allowed_capacities("auditor"), ["consultant"]
        )
        self.assertEqual(
            business_workbench.allowed_capacities("partner"), ["partner"]
        )
        self.assertEqual(business_workbench.allowed_capacities("unknown"), [])

    def test_legal_assignment_transitions(self) -> None:
        expected = {
            ("pending", "accept"): "accepted",
            ("pending", "reject"): "rejected",
            ("pending", "revoke"): "revoked",
            ("accepted", "revoke"): "revoked",
        }
        self.assertEqual(business_workbench.ASSIGNMENT_TRANSITIONS, expected)
        for (current, action), next_status in expected.items():
            with self.subTest(current=current, action=action):
                self.assertEqual(
                    business_workbench.next_assignment_status(current, action),
                    next_status,
                )

    def test_illegal_assignment_transitions_fail_closed(self) -> None:
        invalid = (
            ("accepted", "accept"),
            ("accepted", "reject"),
            ("rejected", "accept"),
            ("rejected", "reject"),
            ("rejected", "revoke"),
            ("revoked", "accept"),
            ("revoked", "reject"),
            ("revoked", "revoke"),
            ("pending", "unknown"),
            ("unknown", "accept"),
        )
        for current, action in invalid:
            with self.subTest(current=current, action=action):
                self.assertIsNone(
                    business_workbench.next_assignment_status(current, action)
                )

    def test_allowed_actions_match_actor_and_state(self) -> None:
        self.assertEqual(
            business_workbench.assignment_allowed_actions(
                "plant_admin", "pending", is_assignee=True
            ),
            ["accept", "reject"],
        )
        self.assertEqual(
            business_workbench.assignment_allowed_actions(
                "enterprise_admin", "pending", is_assignee=False
            ),
            ["revoke"],
        )
        self.assertEqual(
            business_workbench.assignment_allowed_actions(
                "enterprise_admin", "pending", is_assignee=True
            ),
            ["accept", "reject", "revoke"],
        )
        self.assertEqual(
            business_workbench.assignment_allowed_actions(
                "enterprise_admin", "accepted", is_assignee=False
            ),
            ["revoke"],
        )
        for status in ("accepted", "rejected", "revoked", "unknown"):
            with self.subTest(status=status):
                self.assertEqual(
                    business_workbench.assignment_allowed_actions(
                        "partner", status, is_assignee=True
                    ),
                    [],
                )

    def test_case_and_collection_actions_fail_closed(self) -> None:
        self.assertEqual(
            business_workbench.list_allowed_actions("enterprise_admin"),
            ["create"],
        )
        self.assertEqual(business_workbench.list_allowed_actions("partner"), [])
        self.assertEqual(
            business_workbench.case_allowed_actions("super_admin", "planned"),
            ["edit", "assign", "plan_visit"],
        )
        for status in ("closed", "cancelled"):
            with self.subTest(status=status):
                self.assertEqual(
                    business_workbench.case_allowed_actions(
                        "enterprise_admin", status
                    ),
                    [],
                )
        self.assertEqual(
            business_workbench.case_allowed_actions("auditor", "planned"), []
        )


class P2Wave1ApiSurfaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.routes = {
            (route.path, method): route
            for route in service_cases.router.routes
            if isinstance(route, APIRoute)
            for method in route.methods
        }

    def test_wave1_routes_are_complete_and_action_specific(self) -> None:
        expected = {
            ("", "GET"),
            ("", "POST"),
            ("/mine", "GET"),
            ("/assignment-candidates", "GET"),
            ("/{case_id}", "GET"),
            ("/{case_id}", "PATCH"),
            ("/{case_id}/assignments", "POST"),
            ("/{case_id}/assignments/{assignment_id}/accept", "POST"),
            ("/{case_id}/assignments/{assignment_id}/reject", "POST"),
            ("/{case_id}/assignments/{assignment_id}/revoke", "POST"),
            ("/{case_id}/close", "POST"),
        }
        self.assertEqual(set(self.routes), expected)

    def test_static_routes_precede_dynamic_case_route(self) -> None:
        paths = [
            route.path
            for route in service_cases.router.routes
            if isinstance(route, APIRoute)
        ]
        dynamic_index = paths.index("/{case_id}")
        self.assertLess(paths.index("/mine"), dynamic_index)
        self.assertLess(paths.index("/assignment-candidates"), dynamic_index)

    def test_main_api_registers_the_service_case_prefix(self) -> None:
        source = MAIN_API.read_text(encoding="utf-8")
        self.assertIn("service_cases.router", source)
        self.assertIn('prefix="/api/v1/service-cases"', source)

    def test_list_detail_and_assignment_models_expose_allowed_actions(self) -> None:
        self.assertIn("allowed_actions", service_cases.ServiceCaseListOut.model_fields)
        self.assertIn("allowed_actions", service_cases.ServiceCaseOut.model_fields)
        self.assertIn(
            "allowed_actions", service_cases.ServiceAssignmentOut.model_fields
        )
        self.assertIn("items", service_cases.ServiceCaseListOut.model_fields)
        self.assertIn("assignments", service_cases.ServiceCaseDetailOut.model_fields)

    def test_router_builds_allowed_actions_from_pure_contracts(self) -> None:
        self.assertIn(
            "case_allowed_actions",
            inspect.getsource(service_cases._case_out),
        )
        self.assertIn(
            "assignment_allowed_actions",
            inspect.getsource(service_cases._assignment_out),
        )
        self.assertIn(
            "list_allowed_actions",
            inspect.getsource(service_cases._list_cases),
        )
        change_source = inspect.getsource(service_cases._change_assignment)
        self.assertIn("next_assignment_status", change_source)
        self.assertIn("ASSIGNMENT_STATE_CONFLICT", change_source)

    def test_detail_embeds_assignments_loaded_in_the_same_tenant_session(self) -> None:
        source = inspect.getsource(service_cases._detail_out)
        self.assertIn("await _assignment_rows(session, row[\"id\"])", source)
        self.assertIn("assignments=assignments", source)

    def test_cross_tenant_objects_are_structurally_hidden_as_404(self) -> None:
        case_source = inspect.getsource(service_cases.get_service_case)
        create_assignment_source = inspect.getsource(
            service_cases.create_service_assignment
        )
        change_source = inspect.getsource(service_cases._change_assignment)
        for source in (case_source, create_assignment_source, change_source):
            self.assertIn("enterprise_id=tenant.enterprise_id", source)
            self.assertIn("status_code=404", source)
        self.assertIn("SERVICE_CASE_NOT_FOUND", case_source)
        self.assertIn("SERVICE_CASE_NOT_FOUND", create_assignment_source)
        self.assertIn("SERVICE_CASE_NOT_FOUND", change_source)
        self.assertIn("SERVICE_ASSIGNMENT_NOT_FOUND", change_source)
        case_lookup = inspect.getsource(service_cases._case_row)
        assignment_lookup = inspect.getsource(service_cases._assignment_rows)
        self.assertIn("WHERE id = :case_id", case_lookup)
        self.assertIn("WHERE service_case_id = :case_id", assignment_lookup)


class P2Wave1TransactionContractTests(unittest.TestCase):
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

    def test_audit_helper_never_commits_away_from_the_business_transaction(self) -> None:
        source = inspect.getsource(add_event)
        self.assertIn("await session.execute(", source)
        self.assertIn("INSERT INTO f1.audit_log", source)
        self.assertNotIn("RETURNING", source)
        self.assertNotIn("session.commit", source)

    def test_case_and_assignment_mutations_audit_before_one_commit(self) -> None:
        for function in (
            service_cases.create_service_case,
            service_cases.update_service_case,
            service_cases.create_service_assignment,
            service_cases._change_assignment,
        ):
            with self.subTest(function=function.__name__):
                self._assert_audit_before_commit(function)


if __name__ == "__main__":
    unittest.main()
