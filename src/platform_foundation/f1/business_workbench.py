"""Pure P2 business-workbench role and action contracts."""
from __future__ import annotations

from collections.abc import Iterable


MANAGER_LOCAL_ROLES = frozenset(("super_admin", "enterprise_admin"))
CAPACITIES_BY_MEMBERSHIP_ROLE: dict[str, tuple[str, ...]] = {
    "plant_admin": ("employee",),
    "auditor": ("consultant",),
    "partner": ("partner",),
}
TERMINAL_CASE_STATUSES = frozenset(("closed", "cancelled"))
ASSIGNMENT_TRANSITIONS: dict[tuple[str, str], str] = {
    ("pending", "accept"): "accepted",
    ("pending", "reject"): "rejected",
    ("pending", "revoke"): "revoked",
    ("accepted", "revoke"): "revoked",
}
FINDING_TRANSITIONS: dict[tuple[str, str], str] = {
    ("open", "start_rectification"): "rectifying",
    ("rejected", "start_rectification"): "rectifying",
    ("rectifying", "submit_correction"): "submitted",
    ("submitted", "start_review"): "reviewing",
    ("reviewing", "pass"): "passed",
    ("reviewing", "reject"): "rejected",
    ("passed", "close"): "closed",
}
FINDING_SCOPE_STATUSES: dict[str, tuple[str, ...]] = {
    "rectification": ("open", "rectifying", "rejected"),
    "review": ("submitted", "reviewing"),
}
SITE_VISIT_TRANSITIONS: dict[tuple[str, str], str] = {
    ("planned", "start"): "in_progress",
    ("in_progress", "complete"): "completed",
}


def is_manager(local_role: str | None) -> bool:
    return local_role in MANAGER_LOCAL_ROLES


def allowed_capacities(membership_role: str) -> list[str]:
    return list(CAPACITIES_BY_MEMBERSHIP_ROLE.get(membership_role, ()))


def list_allowed_actions(local_role: str | None) -> list[str]:
    return ["create"] if is_manager(local_role) else []


def case_allowed_actions(
    local_role: str | None,
    status: str,
) -> list[str]:
    if is_manager(local_role) and status in ("planned", "in_progress"):
        return ["edit", "assign", "plan_visit"]
    if is_manager(local_role) and status == "completed":
        return ["close"]
    return []


def assignment_allowed_actions(
    local_role: str | None,
    status: str,
    *,
    is_assignee: bool,
) -> list[str]:
    actions: list[str] = []
    if is_assignee and status == "pending":
        actions.extend(("accept", "reject"))
    if is_manager(local_role) and status in ("pending", "accepted"):
        actions.append("revoke")
    return actions


def capacity_is_allowed(membership_role: str, capacity: str) -> bool:
    return capacity in CAPACITIES_BY_MEMBERSHIP_ROLE.get(membership_role, ())


def next_assignment_status(current_status: str, action: str) -> str | None:
    return ASSIGNMENT_TRANSITIONS.get((current_status, action))


def next_finding_status(current_status: str, action: str) -> str | None:
    return FINDING_TRANSITIONS.get((current_status, action))


def next_site_visit_status(current_status: str, action: str) -> str | None:
    return SITE_VISIT_TRANSITIONS.get((current_status, action))


def site_visit_allowed_actions(
    local_role: str | None,
    status: str,
    accepted_capacities: Iterable[str],
) -> list[str]:
    capacities = frozenset(accepted_capacities)
    can_execute = is_manager(local_role) or bool(
        capacities & {"employee", "consultant"}
    )
    actions: list[str] = []
    if is_manager(local_role) and status == "planned":
        actions.append("edit_visit")
    if can_execute and status == "planned":
        actions.append("start_visit")
    elif can_execute and status == "in_progress":
        actions.append("complete_visit")
    return actions


def case_aggregate_target(
    current_status: str,
    visit_statuses: Iterable[str],
    finding_statuses: Iterable[str],
) -> str | None:
    if current_status != "in_progress":
        return None
    active_visits = tuple(
        status for status in visit_statuses if status != "cancelled"
    )
    if not active_visits or any(status != "completed" for status in active_visits):
        return None
    if any(status != "closed" for status in finding_statuses):
        return None
    return "completed"


def can_register_finding(
    local_role: str | None,
    accepted_capacities: Iterable[str],
) -> bool:
    capacities = frozenset(accepted_capacities)
    return is_manager(local_role) or bool(capacities & {"employee", "consultant"})


def is_finding_reviewer(
    local_role: str | None,
    accepted_capacities: Iterable[str],
) -> bool:
    if local_role == "super_admin":
        return True
    return local_role == "auditor" and "consultant" in frozenset(
        accepted_capacities
    )


def finding_collection_allowed_actions(
    local_role: str | None,
    accepted_capacities: Iterable[str],
) -> list[str]:
    if can_register_finding(local_role, accepted_capacities):
        return ["create"]
    return []


def finding_allowed_actions(
    local_role: str | None,
    status: str,
    accepted_capacities: Iterable[str],
) -> list[str]:
    capacities = tuple(accepted_capacities)
    actions: list[str] = []
    if status == "open" and can_register_finding(local_role, capacities):
        actions.append("edit")
    if local_role == "enterprise_admin" and status in ("open", "rejected"):
        actions.append("start_rectification")
    if local_role == "enterprise_admin" and status == "rectifying":
        actions.append("submit_correction")
    if is_finding_reviewer(local_role, capacities):
        if status == "submitted":
            actions.append("start_review")
        elif status == "reviewing":
            actions.extend(("pass", "reject"))
    if is_manager(local_role) and status == "passed":
        actions.append("close")
    return actions


def active_assignment_statuses(statuses: Iterable[str]) -> tuple[str, ...]:
    return tuple(status for status in statuses if status in ("pending", "accepted"))


__all__ = (
    "MANAGER_LOCAL_ROLES",
    "CAPACITIES_BY_MEMBERSHIP_ROLE",
    "TERMINAL_CASE_STATUSES",
    "ASSIGNMENT_TRANSITIONS",
    "FINDING_TRANSITIONS",
    "FINDING_SCOPE_STATUSES",
    "SITE_VISIT_TRANSITIONS",
    "is_manager",
    "allowed_capacities",
    "list_allowed_actions",
    "case_allowed_actions",
    "assignment_allowed_actions",
    "capacity_is_allowed",
    "next_assignment_status",
    "next_finding_status",
    "next_site_visit_status",
    "site_visit_allowed_actions",
    "case_aggregate_target",
    "can_register_finding",
    "is_finding_reviewer",
    "finding_collection_allowed_actions",
    "finding_allowed_actions",
    "active_assignment_statuses",
)
