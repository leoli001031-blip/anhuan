"""Pure authorization and state contracts for the P4 prototype."""
from __future__ import annotations

from collections.abc import Iterable
from typing import Literal


BUSINESS_SNAPSHOT_BOUNDARIES = (
    "BUSINESS_SNAPSHOT_ONLY",
    "NOT_SIGNED",
    "NOT_PUBLISHED",
    "NOT_PRODUCTION",
)

MANAGER_ROLES = frozenset(("super_admin", "enterprise_admin"))
CRM_STAGES = frozenset(("lead", "active", "dormant", "closed"))
CONTACT_STATUSES = frozenset(("active", "inactive"))
FOLLOW_UP_CHANNELS = frozenset(
    ("onsite", "meeting", "phone", "internal_note")
)

DashboardView = Literal["admin", "consultant", "partner", "enterprise"]


def is_manager(role: str | None) -> bool:
    return role in MANAGER_ROLES


def dashboard_view(role: str | None) -> DashboardView:
    if role == "super_admin":
        return "admin"
    if role == "enterprise_admin":
        return "enterprise"
    if role == "partner":
        return "partner"
    return "consultant"


def dashboard_allowed_actions(role: str | None) -> list[str]:
    if is_manager(role):
        return ["create_crm_account", "create_report"]
    return []


def crm_account_allowed_actions(
    role: str | None, *, is_owner: bool
) -> list[str]:
    actions = ["view"]
    if is_manager(role):
        actions.extend(("edit", "add_contact", "add_follow_up"))
    elif is_owner:
        actions.extend(("edit", "add_contact", "add_follow_up"))
    return actions


def crm_collection_allowed_actions(role: str | None) -> list[str]:
    return ["create"] if is_manager(role) else []


def report_allowed_actions(
    role: str | None,
    status: str,
    accepted_capacities: Iterable[str],
) -> list[str]:
    actions = ["view"]
    if status != "active":
        return actions
    if is_manager(role):
        actions.extend(("create_version", "archive"))
    elif "consultant" in frozenset(accepted_capacities):
        actions.append("create_version")
    return actions


def report_collection_allowed_actions(role: str | None) -> list[str]:
    return ["create"] if is_manager(role) else []


__all__ = (
    "BUSINESS_SNAPSHOT_BOUNDARIES",
    "CONTACT_STATUSES",
    "CRM_STAGES",
    "FOLLOW_UP_CHANNELS",
    "crm_account_allowed_actions",
    "crm_collection_allowed_actions",
    "dashboard_allowed_actions",
    "dashboard_view",
    "is_manager",
    "report_allowed_actions",
    "report_collection_allowed_actions",
)
