"""P7 authorization and state-derived action contracts."""
from __future__ import annotations


LOCAL_REHEARSAL_BOUNDARIES = (
    "LOCAL_REHEARSAL_ONLY",
    "MANUAL_EXECUTION",
    "NO_PRODUCTION_ACCESS",
    "NO_DEPLOYMENT",
    "NOT_PRODUCTION",
)

MANAGER_ROLES = frozenset(("super_admin", "enterprise_admin"))
OPERATOR_ROLES = frozenset(("super_admin", "enterprise_admin", "auditor"))
RESULT_REASON_CODES = frozenset(
    (
        "MANUAL_CHECK_PASSED",
        "MANUAL_CHECK_FAILED",
        "MANUAL_CHECK_BLOCKED",
    )
)


def is_manager(role: str | None) -> bool:
    return role in MANAGER_ROLES


def is_operator(role: str | None) -> bool:
    return role in OPERATOR_ROLES


def plan_collection_actions(role: str | None) -> list[str]:
    return ["create"] if is_manager(role) else []


def plan_actions(role: str | None, status: str) -> list[str]:
    actions = ["view"]
    if is_manager(role) and status in ("draft", "active"):
        actions.append("add_check")
    if is_manager(role) and status == "active":
        actions.append("start_run")
    return actions


def check_actions(role: str | None, plan_status: str = "active") -> list[str]:
    actions = ["view"]
    if is_manager(role) and plan_status in ("draft", "active"):
        actions.append("edit")
    return actions


def run_actions(
    role: str | None, status: str, pending_count: int
) -> list[str]:
    actions = ["view"]
    if is_manager(role) and status == "running":
        if pending_count == 0:
            actions.append("complete")
        actions.append("cancel")
    return actions


def result_actions(role: str | None, run_status: str, status: str) -> list[str]:
    actions = ["view"]
    if is_operator(role) and run_status == "running" and status == "pending":
        actions.append("record")
    return actions


def reason_allowed(status: str, reason_code: str) -> bool:
    return reason_code == {
        "passed": "MANUAL_CHECK_PASSED",
        "failed": "MANUAL_CHECK_FAILED",
        "blocked": "MANUAL_CHECK_BLOCKED",
    }.get(status)


__all__ = (
    "LOCAL_REHEARSAL_BOUNDARIES",
    "RESULT_REASON_CODES",
    "check_actions",
    "is_manager",
    "is_operator",
    "plan_actions",
    "plan_collection_actions",
    "reason_allowed",
    "result_actions",
    "run_actions",
)
