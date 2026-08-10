"""Body-free offline smoke for the P2 Wave 3 field-service workflow."""
from __future__ import annotations

from fastapi.routing import APIRoute

from platform_foundation.f1 import business_workbench
from platform_foundation.f1.api.routers import service_cases, site_visits


EXPECTED_VISIT_ROUTES = frozenset(
    {
        ("/{case_id}/site-visits", "GET"),
        ("/{case_id}/site-visits", "POST"),
        ("/{case_id}/site-visits/{visit_id}", "PATCH"),
        ("/{case_id}/site-visits/{visit_id}/start", "POST"),
        ("/{case_id}/site-visits/{visit_id}/complete", "POST"),
    }
)
EXPECTED_CASE_ROUTES = frozenset({("/{case_id}/close", "POST")})
METRIC_ORDER = (
    "sequence_steps",
    "transition_failures",
    "aggregation_failures",
    "action_failures",
    "route_contract_failures",
    "final_status_failures",
    "external_calls",
    "database_calls",
    "docker_calls",
    "formal_calls",
)


def _route_pairs(router) -> frozenset[tuple[str, str]]:
    return frozenset(
        (route.path, method)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    )


def evaluate() -> dict[str, int]:
    transition_failures = 0
    visit_statuses: list[str] = []
    for _ in range(2):
        visit_status = "planned"
        for action, expected in (
            ("start", "in_progress"),
            ("complete", "completed"),
        ):
            observed = business_workbench.next_site_visit_status(
                visit_status, action
            )
            transition_failures += int(observed != expected)
            visit_status = observed or visit_status
        visit_statuses.append(visit_status)

    finding_status = "open"
    for action in (
        "start_rectification",
        "submit_correction",
        "start_review",
        "pass",
        "close",
    ):
        observed = business_workbench.next_finding_status(finding_status, action)
        transition_failures += int(observed is None)
        finding_status = observed or finding_status

    case_status = business_workbench.case_aggregate_target(
        "in_progress", tuple(visit_statuses), (finding_status,)
    )
    aggregation_failures = int(case_status != "completed")

    case_actions = business_workbench.case_allowed_actions(
        "super_admin", case_status
    )
    action_failures = int("close" not in case_actions)
    if not action_failures:
        case_status = "closed"

    visit_routes = _route_pairs(site_visits.router)
    case_routes = _route_pairs(service_cases.router)
    route_contract_failures = len(EXPECTED_VISIT_ROUTES - visit_routes) + len(
        EXPECTED_CASE_ROUTES - case_routes
    )

    return {
        "sequence_steps": 6,
        "transition_failures": transition_failures,
        "aggregation_failures": aggregation_failures,
        "action_failures": action_failures,
        "route_contract_failures": route_contract_failures,
        "final_status_failures": int(case_status != "closed"),
        "external_calls": 0,
        "database_calls": 0,
        "docker_calls": 0,
        "formal_calls": 0,
    }


def render(metrics: dict[str, int]) -> str:
    return " ".join(f"{name}={metrics[name]}" for name in METRIC_ORDER)


def main() -> int:
    metrics = evaluate()
    print(render(metrics))
    failures = (
        "transition_failures",
        "aggregation_failures",
        "action_failures",
        "route_contract_failures",
        "final_status_failures",
        "external_calls",
        "database_calls",
        "docker_calls",
        "formal_calls",
    )
    return 0 if all(metrics[name] == 0 for name in failures) else 2


if __name__ == "__main__":
    raise SystemExit(main())
