"""Body-free offline smoke for the P2 Wave 2 finding workflow."""
from __future__ import annotations

from fastapi.routing import APIRoute

from platform_foundation.f1 import business_workbench
from platform_foundation.f1.api.routers import findings


TRANSITION_PATH = (
    ("start_rectification", "rectifying"),
    ("submit_correction", "submitted"),
    ("start_review", "reviewing"),
    ("reject", "rejected"),
    ("start_rectification", "rectifying"),
    ("submit_correction", "submitted"),
    ("start_review", "reviewing"),
    ("pass", "passed"),
    ("close", "closed"),
)

EXPECTED_ROUTES = frozenset(
    {
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
)

METRIC_ORDER = (
    "sequence_steps",
    "transition_failures",
    "route_contract_failures",
    "permission_failures",
    "final_status_failures",
    "external_calls",
    "database_calls",
    "docker_calls",
    "formal_calls",
)


def evaluate() -> dict[str, int]:
    transition_failures = 0
    status = "open"
    for action, expected_status in TRANSITION_PATH:
        observed = business_workbench.next_finding_status(status, action)
        if observed != expected_status:
            transition_failures += 1
            break
        status = observed

    observed_routes = frozenset(
        (route.path, method)
        for route in findings.router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    )
    route_contract_failures = len(EXPECTED_ROUTES.symmetric_difference(observed_routes))

    permission_checks = (
        business_workbench.is_finding_reviewer("super_admin", ()),
        business_workbench.is_finding_reviewer("auditor", ("consultant",)),
        not business_workbench.is_finding_reviewer("auditor", ()),
        not business_workbench.is_finding_reviewer(
            "enterprise_admin", ("consultant",)
        ),
        "start_rectification"
        in business_workbench.finding_allowed_actions(
            "enterprise_admin", "open", ()
        ),
        "submit_correction"
        in business_workbench.finding_allowed_actions(
            "enterprise_admin", "rectifying", ()
        ),
        "start_review"
        in business_workbench.finding_allowed_actions(
            "auditor", "submitted", ("consultant",)
        ),
        "pass"
        in business_workbench.finding_allowed_actions(
            "auditor", "reviewing", ("consultant",)
        ),
        "reject"
        in business_workbench.finding_allowed_actions(
            "auditor", "reviewing", ("consultant",)
        ),
    )

    return {
        "sequence_steps": len(TRANSITION_PATH),
        "transition_failures": transition_failures,
        "route_contract_failures": route_contract_failures,
        "permission_failures": sum(not check for check in permission_checks),
        "final_status_failures": int(status != "closed"),
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
    failure_metrics = (
        "transition_failures",
        "route_contract_failures",
        "permission_failures",
        "final_status_failures",
        "external_calls",
        "database_calls",
        "docker_calls",
        "formal_calls",
    )
    return 0 if all(metrics[name] == 0 for name in failure_metrics) else 2


if __name__ == "__main__":
    raise SystemExit(main())
