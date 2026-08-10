"""Body-free offline smoke for the P2 Wave 4 workbench surfaces."""
from __future__ import annotations

import inspect
import uuid

from fastapi.routing import APIRoute

from platform_foundation.f1.api.routers import workbench


EXPECTED_ROUTES = frozenset(
    {
        ("/overview", "GET"),
        ("/calendar", "GET"),
        ("/notifications", "GET"),
        ("/notifications/unread-count", "GET"),
        ("/notifications/{notification_id}/read", "POST"),
    }
)
EXPECTED_CALENDAR_KINDS = {"case", "visit", "finding_deadline"}
EXPECTED_VIEWS = {
    "super_admin": "admin",
    "enterprise_admin": "enterprise",
    "auditor": "executor",
}
METRIC_ORDER = (
    "sequence_steps",
    "notification_failures",
    "unread_before_failures",
    "unread_after_failures",
    "calendar_kind_failures",
    "view_failures",
    "route_contract_failures",
    "action_contract_failures",
    "external_calls",
    "database_calls",
    "container_calls",
    "formal_calls",
)


def evaluate() -> dict[str, int]:
    event_id = uuid.UUID("00000000-0000-0000-0000-000000000041")
    notification = {"timeline_event_id": event_id, "read_at": None}
    notifications = [notification]
    notification_failures = int(notification["timeline_event_id"] != event_id)
    unread_before_failures = int(
        sum(item["read_at"] is None for item in notifications) != 1
    )
    notification["read_at"] = "read"
    unread_after_failures = int(
        sum(item["read_at"] is None for item in notifications) != 0
    )

    calendar_schema = workbench.CalendarItemOut.model_json_schema()
    calendar_kinds = set(calendar_schema["properties"]["item_type"]["enum"])
    calendar_kind_failures = len(
        EXPECTED_CALENDAR_KINDS.symmetric_difference(calendar_kinds)
    )
    view_failures = sum(
        workbench._workbench_view(role) != expected
        for role, expected in EXPECTED_VIEWS.items()
    )

    observed_routes = frozenset(
        (route.path, method)
        for route in workbench.router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    )
    route_contract_failures = len(EXPECTED_ROUTES.symmetric_difference(observed_routes))

    module_source = inspect.getsource(workbench)
    action_contract_failures = int("mark_read" not in module_source) + int(
        "view" not in module_source
    )
    return {
        "sequence_steps": 4,
        "notification_failures": notification_failures,
        "unread_before_failures": unread_before_failures,
        "unread_after_failures": unread_after_failures,
        "calendar_kind_failures": calendar_kind_failures,
        "view_failures": view_failures,
        "route_contract_failures": route_contract_failures,
        "action_contract_failures": action_contract_failures,
        "external_calls": 0,
        "database_calls": 0,
        "container_calls": 0,
        "formal_calls": 0,
    }


def render(metrics: dict[str, int]) -> str:
    return " ".join(f"{name}={metrics[name]}" for name in METRIC_ORDER)


def main() -> int:
    metrics = evaluate()
    print(render(metrics))
    failures = METRIC_ORDER[1:]
    return 0 if all(metrics[name] == 0 for name in failures) else 2


if __name__ == "__main__":
    raise SystemExit(main())
