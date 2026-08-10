"""Structured, local-only P5 policy search."""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

from ...auth import Tenant
from ...database import session_scope
from .common import current_actor_id, row_dict
from .contracts import POLICY_WORKFLOW_BOUNDARIES, version_actions


async def search_policies(
    tenant: Tenant,
    *,
    query: str | None,
    domain: str | None,
    effect_status: str | None,
    workflow_status: str | None,
) -> dict[str, Any]:
    filters: list[str] = []
    parameters: dict[str, object] = {}
    if query and query.strip():
        filters.append(
            "position(lower(:query) in lower("
            "COALESCE(version.title, '') || ' ' || "
            "COALESCE(version.summary, '') || ' ' || "
            "COALESCE(source.publisher, '') || ' ' || "
            "COALESCE(source.source_reference, '')"
            ")) > 0"
        )
        parameters["query"] = query.strip()
    if domain is not None:
        filters.append("version.domain = :domain")
        parameters["domain"] = domain
    if effect_status is not None:
        filters.append("version.effect_status = :effect_status")
        parameters["effect_status"] = effect_status
    if workflow_status is not None:
        filters.append("version.workflow_status = :workflow_status")
        parameters["workflow_status"] = workflow_status
    where = " AND ".join(filters) if filters else "TRUE"

    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        rows = (
            await session.execute(
                text(
                    "SELECT version.id, version.source_id, version.version_number, "
                    "version.title, version.domain, version.effect_status, "
                    "version.issued_on, version.effective_from, "
                    "version.effective_to, version.summary, "
                    "version.workflow_status, version.submitted_by_user_id, "
                    "source.title AS source_title, source.publisher, "
                    "source.source_type, source.jurisdiction, "
                    "source.source_reference "
                    "FROM f1.policy_version AS version "
                    "JOIN f1.policy_source AS source "
                    "ON source.enterprise_id = version.enterprise_id "
                    "AND source.id = version.source_id "
                    f"WHERE {where} "
                    "ORDER BY version.effective_from DESC NULLS LAST, "
                    "version.created_at DESC, version.id LIMIT 100"
                ),
                parameters,
            )
        ).mappings().all()

    items: list[dict[str, Any]] = []
    for row in rows:
        item = row_dict(row)
        item["allowed_actions"] = version_actions(tenant.role, row, actor_id)
        items.append(item)
    return {
        "items": items,
        "count": len(items),
        "boundaries": list(POLICY_WORKFLOW_BOUNDARIES),
    }


__all__ = ("search_policies",)
