"""Offline targeted contracts for P2 Business Workbench Wave 3."""
from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.routing import APIRoute
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from platform_foundation.f1 import business_timeline, business_workbench, models
from platform_foundation.f1.api.routers import findings, service_cases, site_visits
from tests import p2_wave3_smoke


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "infra/f1/alembic/versions/f1_0005_business_workbench.py"
MAIN_API = ROOT / "src/platform_foundation/f1/api/main.py"


def _constraints(table, kind):
    return {
        constraint.name: constraint
        for constraint in table.constraints
        if isinstance(constraint, kind)
    }


def _fk_pairs(constraint: ForeignKeyConstraint) -> tuple[tuple[str, str], ...]:
    return tuple(
        (item.parent.name, item.target_fullname) for item in constraint.elements
    )


def _routes(router) -> set[tuple[str, str]]:
    return {
        (route.path, method)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }


class P2Wave3MigrationTests(unittest.TestCase):
    def test_single_linear_head(self) -> None:
        script = ScriptDirectory.from_config(
            Config(str(ROOT / "infra/f1/alembic.ini"))
        )
        self.assertEqual(script.get_heads(), ["f1_0014"])
        self.assertEqual(script.get_revision("f1_0005").down_revision, "f1_0004")

    def test_visit_guard_and_append_only_timeline_contract(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8").split("def downgrade()", 1)[0]
        for marker in (
            "f1.p2_guard_site_visit_update()",
            "p2_site_visit_update_guard",
            "planned_end_at >= planned_start_at",
            "p2_business_timeline_select",
            "p2_business_timeline_insert",
            "business_timeline_case_time_idx",
            "ALTER TABLE f1.business_timeline FORCE ROW LEVEL SECURITY",
            "GRANT SELECT, INSERT ON f1.business_timeline TO f1_api",
            "REVOKE UPDATE, DELETE ON f1.business_timeline FROM f1_api",
        ):
            self.assertIn(marker, source)
        self.assertIn(
            'for table in ("site_visit", "finding", "corrective_action", "finding_review"):',
            source,
        )
        self.assertIn(
            'op.execute(f"ALTER TABLE f1.{table} FORCE ROW LEVEL SECURITY")',
            source,
        )


class P2Wave3ModelAndDomainTests(unittest.TestCase):
    def test_visit_and_timeline_model_constraints(self) -> None:
        visit_checks = _constraints(models.SiteVisit.__table__, CheckConstraint)
        status_sql = str(visit_checks["site_visit_status_ck"].sqltext)
        for status in ("planned", "in_progress", "completed", "cancelled"):
            self.assertIn(status, status_sql)
        self.assertIn(
            "planned_end_at >= planned_start_at",
            str(visit_checks["site_visit_planned_window_ck"].sqltext),
        )

        table = models.BusinessTimeline.__table__
        self.assertEqual(
            set(table.columns.keys()),
            {
                "id",
                "enterprise_id",
                "service_case_id",
                "event_type",
                "subject_type",
                "subject_id",
                "status",
                "actor_user_id",
                "occurred_at",
            },
        )
        unique = _constraints(table, UniqueConstraint)[
            "business_timeline_enterprise_id_id_uq"
        ]
        self.assertEqual(
            tuple(column.name for column in unique.columns),
            ("enterprise_id", "id"),
        )
        foreign_keys = _constraints(table, ForeignKeyConstraint)
        self.assertEqual(
            _fk_pairs(foreign_keys["business_timeline_case_enterprise_fk"]),
            (
                ("enterprise_id", "f1.service_case.enterprise_id"),
                ("service_case_id", "f1.service_case.id"),
            ),
        )
        self.assertEqual(
            _fk_pairs(foreign_keys["business_timeline_actor_enterprise_fk"]),
            (
                ("enterprise_id", "f1.enterprise_user.enterprise_id"),
                ("actor_user_id", "f1.enterprise_user.user_id"),
            ),
        )

    def test_visit_state_machine_and_partner_denial(self) -> None:
        self.assertEqual(
            business_workbench.next_site_visit_status("planned", "start"),
            "in_progress",
        )
        self.assertEqual(
            business_workbench.next_site_visit_status("in_progress", "complete"),
            "completed",
        )
        for current, action in (
            ("planned", "complete"),
            ("in_progress", "start"),
            ("completed", "start"),
            ("cancelled", "complete"),
        ):
            self.assertIsNone(
                business_workbench.next_site_visit_status(current, action)
            )
        self.assertEqual(
            business_workbench.site_visit_allowed_actions(
                "partner", "planned", ("partner",)
            ),
            [],
        )
        employee = business_workbench.site_visit_allowed_actions(
            "plant_admin", "planned", ("employee",)
        )
        self.assertNotIn("edit_visit", employee)
        self.assertIn("start_visit", employee)
        self.assertIn(
            "edit_visit",
            business_workbench.site_visit_allowed_actions(
                "super_admin", "planned", ()
            ),
        )
        self.assertIn(
            "complete_visit",
            business_workbench.site_visit_allowed_actions(
                "auditor", "in_progress", ("consultant",)
            ),
        )

    def test_case_aggregation_waits_for_two_visits_and_all_findings(self) -> None:
        aggregate = business_workbench.case_aggregate_target
        self.assertIsNone(aggregate("planned", ("planned", "planned"), ()))
        self.assertIsNone(
            aggregate("planned", ("in_progress", "planned"), ())
        )
        self.assertIsNone(
            aggregate("in_progress", ("completed", "completed"), ("open",))
        )
        self.assertEqual(
            aggregate("in_progress", ("completed", "completed"), ("closed",)),
            "completed",
        )


class P2Wave3ApiTests(unittest.TestCase):
    def test_visit_and_case_close_routes(self) -> None:
        self.assertEqual(
            _routes(site_visits.router),
            {
                ("/{case_id}/site-visits", "GET"),
                ("/{case_id}/site-visits", "POST"),
                ("/{case_id}/site-visits/{visit_id}", "PATCH"),
                ("/{case_id}/site-visits/{visit_id}/start", "POST"),
                ("/{case_id}/site-visits/{visit_id}/complete", "POST"),
            },
        )
        self.assertIn(("/{case_id}/close", "POST"), _routes(service_cases.router))
        main_source = MAIN_API.read_text(encoding="utf-8")
        self.assertIn("site_visits.router", main_source)
        self.assertIn('prefix="/api/v1/service-cases"', main_source)

    def test_server_authored_actions_and_embedded_detail(self) -> None:
        self.assertIn("allowed_actions", site_visits.SiteVisitOut.model_fields)
        self.assertIn("allowed_actions", site_visits.SiteVisitListOut.model_fields)
        for field in ("site_visits", "findings", "finding_summary", "timeline"):
            self.assertIn(field, service_cases.ServiceCaseDetailOut.model_fields)
        for field in ("subject_type", "subject_id"):
            self.assertIn(field, service_cases.BusinessTimelineOut.model_fields)
        detail_source = inspect.getsource(service_cases._detail_out)
        for marker in ("site_visits=", "findings=", "finding_summary=", "timeline="):
            self.assertIn(marker, detail_source)
        self.assertIn(
            "plan_visit",
            business_workbench.case_allowed_actions("super_admin", "planned"),
        )

    def test_cross_tenant_visit_and_close_objects_are_hidden(self) -> None:
        functions = (
            site_visits.list_site_visits,
            site_visits.create_site_visit,
            site_visits.update_site_visit,
            site_visits._transition_site_visit,
            service_cases.close_service_case,
        )
        for function in functions:
            source = inspect.getsource(function)
            self.assertIn("enterprise_id=tenant.enterprise_id", source)
        for function in functions[1:]:
            self.assertIn("status_code=404", inspect.getsource(function))


class P2Wave3TransactionTests(unittest.TestCase):
    def test_timeline_helpers_never_commit_independently(self) -> None:
        add_source = inspect.getsource(business_timeline.add_timeline_event)
        self.assertIn("await session.execute", add_source)
        self.assertIn("INSERT INTO f1.business_timeline", add_source)
        self.assertNotIn("session.commit", add_source)
        aggregate_source = inspect.getsource(
            business_timeline.maybe_complete_service_case
        )
        self.assertIn("add_timeline_event", aggregate_source)
        self.assertIn("add_event", aggregate_source)
        self.assertNotIn("session.commit", aggregate_source)

    def test_core_writes_put_timeline_and_audit_before_commit(self) -> None:
        functions = (
            site_visits.create_site_visit,
            site_visits.update_site_visit,
            site_visits._transition_site_visit,
            findings._transition_finding,
            service_cases.close_service_case,
        )
        for function in functions:
            source = inspect.getsource(function)
            with self.subTest(function=function.__name__):
                self.assertIn("await add_timeline_event(", source)
                self.assertIn("await add_event(", source)
                self.assertEqual(source.count("await session.commit()"), 1)
                commit_at = source.index("await session.commit()")
                self.assertLess(source.rindex("await add_timeline_event("), commit_at)
                self.assertLess(source.rindex("await add_event("), commit_at)


class P2Wave3SmokeTests(unittest.TestCase):
    def test_offline_main_chain_has_fixed_zero_failure_output(self) -> None:
        metrics = p2_wave3_smoke.evaluate()
        self.assertEqual(metrics["sequence_steps"], 6)
        for name in p2_wave3_smoke.METRIC_ORDER[1:]:
            self.assertEqual(metrics[name], 0, name)
        self.assertEqual(
            p2_wave3_smoke.render(metrics),
            "sequence_steps=6 transition_failures=0 aggregation_failures=0 "
            "action_failures=0 route_contract_failures=0 final_status_failures=0 "
            "external_calls=0 database_calls=0 docker_calls=0 formal_calls=0",
        )

    def test_smoke_is_body_free_and_process_free(self) -> None:
        source = inspect.getsource(p2_wave3_smoke)
        for forbidden in (
            "subprocess",
            "socket",
            "httpx",
            "requests",
            "psycopg",
            "import docker",
            "docker.",
            "f11_support",
        ):
            self.assertNotIn(forbidden, source)
        self.assertEqual(source.count("print("), 1)


if __name__ == "__main__":
    unittest.main()
