"""P6 authorization and state-derived action contracts."""
from __future__ import annotations


AUTOMATED_QUALITY_BOUNDARIES = (
    "SYNTHETIC_ORACLE_ONLY",
    "NON_GOLD",
    "ACCURACY_NOT_EVALUATED",
    "NO_EXTERNAL_MODEL_CALLS",
    "NOT_PRODUCTION",
)

MANAGER_ROLES = frozenset(("super_admin", "enterprise_admin"))
REVIEWER_ROLES = frozenset(("super_admin", "auditor"))


def is_manager(role: str | None) -> bool:
    return role in MANAGER_ROLES


def is_reviewer(role: str | None) -> bool:
    return role in REVIEWER_ROLES


def suite_collection_actions(role: str | None) -> list[str]:
    return ["create"] if is_manager(role) else []


def suite_actions(role: str | None, status: str) -> list[str]:
    actions = ["view"]
    if status == "active" and is_manager(role):
        actions.append("add_scenario")
    if status == "active" and is_manager(role):
        actions.append("run")
    return actions


def scenario_actions(role: str | None, suite_status: str = "active") -> list[str]:
    actions = ["view"]
    if suite_status == "active" and is_manager(role):
        actions.append("edit")
    return actions


def run_actions() -> list[str]:
    return ["view"]


def disagreement_actions(role: str | None, status: str) -> list[str]:
    actions = ["view"]
    if status == "open" and (is_manager(role) or is_reviewer(role)):
        actions.extend(("acknowledge", "waive"))
    return actions


__all__ = (
    "AUTOMATED_QUALITY_BOUNDARIES",
    "disagreement_actions",
    "is_manager",
    "is_reviewer",
    "run_actions",
    "scenario_actions",
    "suite_actions",
    "suite_collection_actions",
)
