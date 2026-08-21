"""Offline targeted contracts for P2 Business Workbench Wave 4."""
from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.routing import APIRoute
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from platform_foundation.f1 import audit, business_notifications, models
from platform_foundation.f1.api.routers import findings, service_cases, site_visits, workbench
from tests import p2_wave4_smoke


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


class P2Wave4MigrationAndModelTests(unittest.TestCase):
    def test_single_linear_head(self) -> None:
        script = ScriptDirectory.from_config(
            Config(str(ROOT / "infra/f1/alembic.ini"))
        )
        self.assertEqual(script.get_heads(), ["f1_0015"])
        self.assertEqual(script.get_revision("f1_0005").down_revision, "f1_0004")
        self.assertEqual(script.get_revision("f1_0015").down_revision, "f1_0014")

    def test_notification_fields_composite_fks_uniques_and_read_guard(self) -> None:
        table = models.InAppNotification.__table__
        self.assertEqual(
            set(table.columns.keys()),
            {
                "id",
                "enterprise_id",
                "recipient_user_id",
                "timeline_event_id",
                "created_at",
                "read_at",
            },
        )
        uniques = _constraints(table, UniqueConstraint)
        self.assertEqual(
            tuple(
                column.name
                for column in uniques[
                    "in_app_notification_enterprise_id_id_uq"
                ].columns
            ),
            ("enterprise_id", "id"),
        )
        self.assertEqual(
            tuple(
                column.name
                for column in uniques[
                    "in_app_notification_recipient_event_uq"
                ].columns
            ),
            ("enterprise_id", "recipient_user_id", "timeline_event_id"),
        )
        foreign_keys = _constraints(table, ForeignKeyConstraint)
        self.assertEqual(
            _fk_pairs(
                foreign_keys["in_app_notification_recipient_enterprise_fk"]
            ),
            (
                ("enterprise_id", "f1.enterprise_user.enterprise_id"),
                ("recipient_user_id", "f1.enterprise_user.user_id"),
            ),
        )
        self.assertEqual(
            _fk_pairs(foreign_keys["in_app_notification_timeline_enterprise_fk"]),
            (
                ("enterprise_id", "f1.business_timeline.enterprise_id"),
                ("timeline_event_id", "f1.business_timeline.id"),
            ),
        )
        self.assertIn(
            "read_at",
            str(
                _constraints(table, CheckConstraint)[
                    "in_app_notification_read_time_ck"
                ].sqltext
            ),
        )

    def test_notification_force_rls_monotonic_guard_and_no_delete(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8").split("def downgrade()", 1)[0]
        for marker in (
            "f1.p2_guard_notification_update()",
            "p2_notification_update_guard",
            "in_app_notification_unread_idx",
            "p2_notification_recipient_select",
            "p2_notification_recipient_update",
            "p2_notification_event_insert",
            "ALTER TABLE f1.in_app_notification FORCE ROW LEVEL SECURITY",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("GRANT DELETE ON f1.in_app_notification", source)
        self.assertNotIn("FOR DELETE ON f1.in_app_notification", source)

    def test_timeline_and_notification_policies_are_acyclic(self) -> None:
        source = MIGRATION.read_text(encoding="utf-8").split("def downgrade()", 1)[0]
        timeline_policy = source.split(
            "CREATE POLICY p2_business_timeline_select", 1
        )[1].split('"""', 1)[0]
        notification_policy = source.split(
            "CREATE POLICY p2_notification_event_insert", 1
        )[1].split('"""', 1)[0]
        self.assertNotIn("in_app_notification", timeline_policy)
        self.assertIn("FROM f1.service_case AS parent_case", timeline_policy)
        self.assertIn("FROM f1.business_timeline AS timeline", notification_policy)


class P2Wave4RoleAndApiTests(unittest.TestCase):
    def test_role_to_view_mapping(self) -> None:
        expected = {
            "super_admin": "admin",
            "enterprise_admin": "enterprise",
            "plant_admin": "executor",
            "auditor": "executor",
            "partner": "executor",
        }
        for role, view in expected.items():
            self.assertEqual(workbench._workbench_view(role), view)

    def test_five_workbench_routes_and_main_prefix(self) -> None:
        self.assertEqual(
            _routes(workbench.router),
            {
                ("/overview", "GET"),
                ("/calendar", "GET"),
                ("/notifications", "GET"),
                ("/notifications/unread-count", "GET"),
                ("/notifications/{notification_id}/read", "POST"),
            },
        )
        main_source = MAIN_API.read_text(encoding="utf-8")
        self.assertIn("workbench.router", main_source)
        self.assertIn('prefix="/api/v1/workbench"', main_source)

    def test_response_shapes_calendar_kinds_and_allowed_actions(self) -> None:
        self.assertEqual(
            set(workbench.WorkbenchOverviewOut.model_fields),
            {
                "view",
                "metrics",
                "service_cases",
                "findings",
                "upcoming_visits",
                "reviews",
            },
        )
        self.assertIn("items", workbench.CalendarOut.model_fields)
        self.assertIn("items", workbench.NotificationListOut.model_fields)
        self.assertIn("unread_count", workbench.UnreadCountOut.model_fields)
        notification_fields = workbench.NotificationOut.model_fields
        for field in (
            "event_type",
            "subject_type",
            "subject_id",
            "service_case_id",
            "read_at",
            "allowed_actions",
        ):
            self.assertIn(field, notification_fields)
        calendar_schema = workbench.CalendarItemOut.model_json_schema()
        self.assertEqual(
            set(calendar_schema["properties"]["item_type"]["enum"]),
            {"case", "visit", "finding_deadline"},
        )
        module_source = inspect.getsource(workbench)
        self.assertIn("mark_read", module_source)
        self.assertIn("view", module_source)

    def test_notification_read_is_same_tenant_recipient_only_and_monotonic(self) -> None:
        source = inspect.getsource(workbench.mark_notification_read)
        self.assertIn("enterprise_id=tenant.enterprise_id", source)
        self.assertIn("status_code=404", source)
        self.assertIn("recipient_user_id", source)
        self.assertIn("read_at IS NULL", source)
        self.assertEqual(source.count("await session.commit()"), 1)


class P2Wave4NotificationTransactionTests(unittest.TestCase):
    def test_audit_append_does_not_require_audit_read_visibility(self) -> None:
        source = inspect.getsource(audit.add_event)
        self.assertIn("INSERT INTO f1.audit_log", source)
        self.assertNotIn("RETURNING", source)
        self.assertNotIn("session.add", source)
        self.assertNotIn("session.commit", source)

    def test_notification_helper_is_idempotent_actor_excluding_and_non_committing(self) -> None:
        source = inspect.getsource(business_notifications.add_notifications)
        self.assertIn("recipient_user_ids", source)
        self.assertIn("actor_user_id", source)
        self.assertIn("session.begin_nested()", source)
        self.assertIn("in_app_notification_recipient_event_uq", source)
        self.assertIn("timeline_event_id", source)
        self.assertNotIn("ON CONFLICT", source)
        self.assertNotIn("RETURNING", source)
        self.assertNotIn("session.commit", source)

    def test_business_callers_add_notifications_before_their_commit(self) -> None:
        callers = 0
        for module in (service_cases, site_visits, findings):
            for _, function in inspect.getmembers(module, inspect.iscoroutinefunction):
                if function.__module__ != module.__name__:
                    continue
                source = inspect.getsource(function)
                if "await add_notifications(" not in source:
                    continue
                if "await session.commit()" not in source:
                    continue
                callers += 1
                self.assertLess(
                    source.rindex("await add_notifications("),
                    source.index("await session.commit()"),
                )
        self.assertGreater(callers, 0)


class P2Wave4SmokeTests(unittest.TestCase):
    def test_event_notification_read_calendar_and_view_smoke(self) -> None:
        metrics = p2_wave4_smoke.evaluate()
        self.assertEqual(metrics["sequence_steps"], 4)
        for name in p2_wave4_smoke.METRIC_ORDER[1:]:
            self.assertEqual(metrics[name], 0, name)
        self.assertEqual(
            p2_wave4_smoke.render(metrics),
            "sequence_steps=4 notification_failures=0 unread_before_failures=0 "
            "unread_after_failures=0 calendar_kind_failures=0 view_failures=0 "
            "route_contract_failures=0 action_contract_failures=0 external_calls=0 "
            "database_calls=0 container_calls=0 formal_calls=0",
        )

    def test_smoke_is_process_and_service_free(self) -> None:
        source = inspect.getsource(p2_wave4_smoke)
        for forbidden in (
            "subprocess",
            "socket",
            "httpx",
            "requests",
            "psycopg",
            "docker",
            "f11_support",
        ):
            self.assertNotIn(forbidden, source)
        self.assertEqual(source.count("print("), 1)


if __name__ == "__main__":
    unittest.main()
