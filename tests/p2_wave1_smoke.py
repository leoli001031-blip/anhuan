"""Body-free offline smoke for the P2 Wave 1 visible business flow."""
from __future__ import annotations

import uuid

from fastapi.routing import APIRoute

from platform_foundation.f1.business_workbench import (
    allowed_capacities,
    assignment_allowed_actions,
    case_allowed_actions,
    list_allowed_actions,
    next_assignment_status,
)
from platform_foundation.f1.api.routers.service_cases import (
    ServiceAssignmentCreate,
    ServiceCaseCreate,
    router,
)


def main() -> int:
    case = ServiceCaseCreate(title="fixture", service_type="onsite")
    if case.status != "planned":
        raise RuntimeError("P2_SMOKE_CASE_CREATE_RED")
    if list_allowed_actions("enterprise_admin") != ["create"]:
        raise RuntimeError("P2_SMOKE_CASE_CREATE_ACTION_RED")
    if case_allowed_actions("enterprise_admin", "planned") != [
        "edit",
        "assign",
        "plan_visit",
    ]:
        raise RuntimeError("P2_SMOKE_CASE_MANAGE_ACTION_RED")

    flows = (
        ("plant_admin", "employee", "accept", "accepted"),
        ("auditor", "consultant", "reject", "rejected"),
        ("partner", "partner", "revoke", "revoked"),
    )
    for membership_role, capacity, action, expected in flows:
        if allowed_capacities(membership_role) != [capacity]:
            raise RuntimeError("P2_SMOKE_CAPACITY_RED")
        ServiceAssignmentCreate(
            assignee_user_id=uuid.uuid4(),
            capacity=capacity,
        )
        if action in ("accept", "reject"):
            actions = assignment_allowed_actions(
                membership_role,
                "pending",
                is_assignee=True,
            )
            if action not in actions:
                raise RuntimeError("P2_SMOKE_ASSIGNEE_ACTION_RED")
        if next_assignment_status("pending", action) != expected:
            raise RuntimeError("P2_SMOKE_ASSIGNMENT_FLOW_RED")

    paths = {
        (route.path, method)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }
    required = {
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
    if paths != required:
        raise RuntimeError("P2_SMOKE_ROUTE_RED")

    print("P2_WAVE1_SMOKE=SMOKE_PASSED")
    print(f"assignment_flows={len(flows)}")
    print(f"route_contracts={len(required)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
