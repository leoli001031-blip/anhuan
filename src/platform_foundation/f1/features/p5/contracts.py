"""Pure P5 state and authorization contracts."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


POLICY_WORKFLOW_BOUNDARIES = (
    "CANDIDATE_ONLY",
    "INTERNAL_REVIEW_ONLY",
    "NOT_LEGAL_ADVICE",
    "PROFESSIONAL_JUDGMENT_REQUIRED",
    "NOT_PRODUCTION",
)

MANAGER_ROLES = frozenset(("super_admin", "enterprise_admin"))
REVIEWER_ROLES = frozenset(("super_admin", "auditor"))


def is_manager(role: str | None) -> bool:
    return role in MANAGER_ROLES


def is_reviewer(role: str | None) -> bool:
    return role in REVIEWER_ROLES


def source_actions(role: str | None, status: str) -> list[str]:
    actions = ["view"]
    if is_manager(role) and status == "active":
        actions.extend(("edit", "create_version"))
    return actions


def source_collection_actions(role: str | None) -> list[str]:
    return ["create"] if is_manager(role) else []


def version_actions(
    role: str | None,
    row: Mapping[str, Any],
    actor_id: object,
) -> list[str]:
    actions = ["view"]
    state = str(row["workflow_status"])
    if is_manager(role) and state in ("draft", "rejected"):
        actions.append("submit")
    if is_reviewer(role) and state == "in_review":
        if row.get("submitted_by_user_id") != actor_id:
            actions.extend(("approve", "reject"))
    if is_manager(role) and state == "approved":
        actions.append("publish")
    if (is_manager(role) or is_reviewer(role)) and state in (
        "approved",
        "published",
    ):
        actions.append("create_impact")
    return actions


def impact_actions(role: str | None, status: str) -> list[str]:
    actions = ["view"]
    if (is_manager(role) or is_reviewer(role)) and status == "open":
        actions.extend(("edit", "accept", "dismiss"))
    if (is_manager(role) or is_reviewer(role)) and status == "accepted":
        actions.append("create_task")
    return actions


def impact_collection_actions(role: str | None) -> list[str]:
    return ["create"] if is_manager(role) or is_reviewer(role) else []


def impact_task_actions(
    role: str | None, status: str, *, is_owner: bool
) -> list[str]:
    actions = ["view"]
    if status in ("completed", "dismissed"):
        return actions
    if is_manager(role) or is_reviewer(role):
        actions.append("edit")
        if status == "open":
            actions.append("start")
        if status in ("open", "in_progress"):
            actions.extend(("complete", "dismiss"))
    elif is_owner:
        if status == "open":
            actions.append("start")
        if status in ("open", "in_progress"):
            actions.append("complete")
    return actions


__all__ = (
    "POLICY_WORKFLOW_BOUNDARIES",
    "impact_actions",
    "impact_collection_actions",
    "impact_task_actions",
    "is_manager",
    "is_reviewer",
    "source_actions",
    "source_collection_actions",
    "version_actions",
)
