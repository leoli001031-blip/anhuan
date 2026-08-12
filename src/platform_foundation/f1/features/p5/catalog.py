"""P5 policy-source registry and version candidates."""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import date
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...audit import add_event
from ...auth import Tenant
from ...database import session_scope
from .common import current_actor_id, row_dict
from .contracts import (
    POLICY_WORKFLOW_BOUNDARIES,
    is_manager,
    source_actions,
    source_collection_actions,
    version_actions,
)


SOURCE_COLUMNS = (
    "id, enterprise_id, title, publisher, source_type, jurisdiction, "
    "source_reference, status, created_by_user_id, created_at, updated_at"
)
VERSION_COLUMNS = (
    "id, enterprise_id, source_id, version_number, title, domain, "
    "effect_status, issued_on, effective_from, effective_to, summary, "
    "document_version_id, document_sha256, workflow_status, "
    "submitted_by_user_id, submitted_at, approved_by_user_id, approved_at, "
    "published_by_user_id, published_at, created_by_user_id, created_at, updated_at"
)
REVIEW_COLUMNS = (
    "id, enterprise_id, policy_version_id, action, comment, actor_user_id, "
    "occurred_at"
)


async def source_row(
    session: AsyncSession, source_id: uuid.UUID, *, lock: bool = False
) -> Mapping[str, Any]:
    suffix = " FOR UPDATE" if lock else ""
    row = (
        await session.execute(
            text(
                f"SELECT {SOURCE_COLUMNS} FROM f1.policy_source "
                "WHERE id = :source_id" + suffix
            ),
            {"source_id": source_id},
        )
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="POLICY_SOURCE_NOT_FOUND")
    return row


async def version_row(
    session: AsyncSession, version_id: uuid.UUID, *, lock: bool = False
) -> Mapping[str, Any]:
    suffix = " FOR UPDATE" if lock else ""
    row = (
        await session.execute(
            text(
                f"SELECT {VERSION_COLUMNS} FROM f1.policy_version "
                "WHERE id = :version_id" + suffix
            ),
            {"version_id": version_id},
        )
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="POLICY_VERSION_NOT_FOUND")
    return row


def source_out(row: Mapping[str, Any], tenant: Tenant) -> dict[str, Any]:
    output = row_dict(row)
    output["allowed_actions"] = source_actions(tenant.role, str(row["status"]))
    return output


def version_out(
    row: Mapping[str, Any], tenant: Tenant, actor_id: uuid.UUID
) -> dict[str, Any]:
    output = row_dict(row)
    output["allowed_actions"] = version_actions(tenant.role, row, actor_id)
    output["boundaries"] = list(POLICY_WORKFLOW_BOUNDARIES)
    return output


async def list_sources(tenant: Tenant) -> dict[str, Any]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await current_actor_id(session, tenant)
        rows = (
            await session.execute(
                text(
                    f"SELECT {SOURCE_COLUMNS} FROM f1.policy_source "
                    "ORDER BY updated_at DESC, id"
                )
            )
        ).mappings().all()
    return {
        "items": [source_out(row, tenant) for row in rows],
        "allowed_actions": source_collection_actions(tenant.role),
        "boundaries": list(POLICY_WORKFLOW_BOUNDARIES),
    }


async def get_source(tenant: Tenant, source_id: uuid.UUID) -> dict[str, Any]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        source = await source_row(session, source_id)
        versions = (
            await session.execute(
                text(
                    f"SELECT {VERSION_COLUMNS} FROM f1.policy_version "
                    "WHERE source_id = :source_id "
                    "ORDER BY version_number DESC, id"
                ),
                {"source_id": source_id},
            )
        ).mappings().all()
    output = source_out(source, tenant)
    output["versions"] = [
        version_out(row, tenant, actor_id) for row in versions
    ]
    output["boundaries"] = list(POLICY_WORKFLOW_BOUNDARIES)
    return output


async def create_source(
    tenant: Tenant,
    *,
    title: str,
    publisher: str,
    source_type: str,
    jurisdiction: str,
    source_reference: str,
) -> dict[str, Any]:
    if not is_manager(tenant.role):
        raise HTTPException(status_code=403, detail="POLICY_MANAGER_REQUIRED")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        source_id = uuid.uuid4()
        row = (
            await session.execute(
                text(
                    "INSERT INTO f1.policy_source ("
                    "id, enterprise_id, title, publisher, source_type, "
                    "jurisdiction, source_reference, status, created_by_user_id) "
                    "VALUES (:id, :enterprise_id, :title, :publisher, "
                    ":source_type, :jurisdiction, :source_reference, 'active', "
                    ":actor_id) "
                    f"RETURNING {SOURCE_COLUMNS}"
                ),
                {
                    "id": source_id,
                    "enterprise_id": tenant.enterprise_id,
                    "title": title,
                    "publisher": publisher,
                    "source_type": source_type,
                    "jurisdiction": jurisdiction,
                    "source_reference": source_reference,
                    "actor_id": actor_id,
                },
            )
        ).mappings().one()
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "policy.source.created",
            "policy_source",
            str(source_id),
        )
        await session.commit()
    return source_out(row, tenant)


async def insert_source_in_session(
    session: AsyncSession,
    tenant: Tenant,
    *,
    actor_id: uuid.UUID,
    title: str,
    publisher: str,
    source_type: str,
    jurisdiction: str,
    source_reference: str,
) -> Mapping[str, Any]:
    """Insert a source without committing, for one larger atomic workflow."""
    if not is_manager(tenant.role):
        raise HTTPException(status_code=403, detail="POLICY_MANAGER_REQUIRED")
    source_id = uuid.uuid4()
    return (
        await session.execute(
            text(
                "INSERT INTO f1.policy_source ("
                "id,enterprise_id,title,publisher,source_type,jurisdiction,"
                "source_reference,status,created_by_user_id) VALUES ("
                ":id,:enterprise_id,:title,:publisher,:source_type,"
                ":jurisdiction,:source_reference,'active',:actor_id) "
                f"RETURNING {SOURCE_COLUMNS}"
            ),
            {
                "id": source_id,
                "enterprise_id": tenant.enterprise_id,
                "title": title,
                "publisher": publisher,
                "source_type": source_type,
                "jurisdiction": jurisdiction,
                "source_reference": source_reference,
                "actor_id": actor_id,
            },
        )
    ).mappings().one()


async def update_source(
    tenant: Tenant, source_id: uuid.UUID, changes: dict[str, Any]
) -> dict[str, Any]:
    if not is_manager(tenant.role):
        raise HTTPException(status_code=403, detail="POLICY_MANAGER_REQUIRED")
    if not changes:
        raise HTTPException(status_code=422, detail="POLICY_SOURCE_NO_CHANGES")
    allowed = {
        "title",
        "publisher",
        "source_type",
        "jurisdiction",
        "source_reference",
        "status",
    }
    if not set(changes).issubset(allowed):
        raise HTTPException(status_code=422, detail="POLICY_SOURCE_FIELD_INVALID")
    if any(value is None for value in changes.values()):
        raise HTTPException(status_code=422, detail="POLICY_SOURCE_FIELD_INVALID")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await current_actor_id(session, tenant)
        current = await source_row(session, source_id, lock=True)
        if current["status"] == "archived":
            raise HTTPException(status_code=409, detail="POLICY_SOURCE_ARCHIVED")
        assignments = ", ".join(f"{name} = :{name}" for name in changes)
        row = (
            await session.execute(
                text(
                    f"UPDATE f1.policy_source SET {assignments}, "
                    "updated_at = statement_timestamp() "
                    "WHERE id = :source_id AND status = 'active' "
                    f"RETURNING {SOURCE_COLUMNS}"
                ),
                {**changes, "source_id": source_id},
            )
        ).mappings().one_or_none()
        if row is None:
            raise HTTPException(status_code=409, detail="POLICY_SOURCE_CONFLICT")
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "policy.source.updated",
            "policy_source",
            str(source_id),
        )
        await session.commit()
    return source_out(row, tenant)


async def _controlled_document(
    session: AsyncSession, document_version_id: uuid.UUID
) -> str:
    sha256 = (
        await session.execute(
            text(
                "SELECT task.content_sha256 FROM f1.document_version AS version "
                "JOIN f1.upload_task AS task "
                "ON task.enterprise_id = version.enterprise_id "
                "AND task.id = version.upload_task_id "
                "WHERE version.id = :document_version_id "
                "AND task.pipeline_kind = 'controlled_ingestion' "
                "AND task.object_state = 'ready' "
                "AND task.quarantine_status = 'released' "
                "AND task.scan_verdict = 'clean' "
                "AND task.preview_status = 'ready'"
            ),
            {"document_version_id": document_version_id},
        )
    ).scalar_one_or_none()
    if sha256 is None:
        raise HTTPException(
            status_code=404, detail="POLICY_DOCUMENT_VERSION_NOT_FOUND"
        )
    return str(sha256)


async def create_version(
    tenant: Tenant,
    source_id: uuid.UUID,
    *,
    title: str,
    domain: str,
    effect_status: str,
    issued_on: date | None,
    effective_from: date | None,
    effective_to: date | None,
    summary: str,
    document_version_id: uuid.UUID | None,
) -> dict[str, Any]:
    if not is_manager(tenant.role):
        raise HTTPException(status_code=403, detail="POLICY_MANAGER_REQUIRED")
    if effective_from and effective_to and effective_to < effective_from:
        raise HTTPException(status_code=422, detail="POLICY_EFFECTIVE_RANGE_INVALID")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        source = await source_row(session, source_id, lock=True)
        if source["status"] != "active":
            raise HTTPException(status_code=409, detail="POLICY_SOURCE_ARCHIVED")
        version_number = int(
            (
                await session.execute(
                    text(
                        "SELECT COALESCE(max(version_number), 0) "
                        "FROM f1.policy_version WHERE source_id = :source_id"
                    ),
                    {"source_id": source_id},
                )
            ).scalar_one()
        ) + 1
        document_sha256 = None
        if document_version_id is not None:
            document_sha256 = await _controlled_document(
                session, document_version_id
            )
        version_id = uuid.uuid4()
        row = (
            await session.execute(
                text(
                    "INSERT INTO f1.policy_version ("
                    "id, enterprise_id, source_id, version_number, title, domain, "
                    "effect_status, issued_on, effective_from, effective_to, "
                    "summary, document_version_id, document_sha256, "
                    "workflow_status, created_by_user_id) VALUES ("
                    ":id, :enterprise_id, :source_id, :version_number, :title, "
                    ":domain, :effect_status, :issued_on, :effective_from, "
                    ":effective_to, :summary, :document_version_id, "
                    ":document_sha256, 'draft', :actor_id) "
                    f"RETURNING {VERSION_COLUMNS}"
                ),
                {
                    "id": version_id,
                    "enterprise_id": tenant.enterprise_id,
                    "source_id": source_id,
                    "version_number": version_number,
                    "title": title,
                    "domain": domain,
                    "effect_status": effect_status,
                    "issued_on": issued_on,
                    "effective_from": effective_from,
                    "effective_to": effective_to,
                    "summary": summary,
                    "document_version_id": document_version_id,
                    "document_sha256": document_sha256,
                    "actor_id": actor_id,
                },
            )
        ).mappings().one()
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "policy.version.created",
            "policy_version",
            str(version_id),
        )
        await session.commit()
    return version_out(row, tenant, actor_id)


async def insert_version_in_session(
    session: AsyncSession,
    tenant: Tenant,
    *,
    actor_id: uuid.UUID,
    source_id: uuid.UUID,
    title: str,
    domain: str,
    effect_status: str,
    issued_on: date | None,
    effective_from: date | None,
    effective_to: date | None,
    summary: str,
    document_version_id: uuid.UUID,
) -> Mapping[str, Any]:
    """Insert a draft version without committing, for atomic confirmation."""
    if not is_manager(tenant.role):
        raise HTTPException(status_code=403, detail="POLICY_MANAGER_REQUIRED")
    if effective_from and effective_to and effective_to < effective_from:
        raise HTTPException(status_code=422, detail="POLICY_EFFECTIVE_RANGE_INVALID")
    source = await source_row(session, source_id, lock=True)
    if source["status"] != "active":
        raise HTTPException(status_code=409, detail="POLICY_SOURCE_ARCHIVED")
    document_sha256 = await _controlled_document(session, document_version_id)
    version_number = int(
        (
            await session.execute(
                text(
                    "SELECT COALESCE(max(version_number),0) FROM f1.policy_version "
                    "WHERE source_id=:source_id"
                ),
                {"source_id": source_id},
            )
        ).scalar_one()
    ) + 1
    version_id = uuid.uuid4()
    return (
        await session.execute(
            text(
                "INSERT INTO f1.policy_version ("
                "id,enterprise_id,source_id,version_number,title,domain,"
                "effect_status,issued_on,effective_from,effective_to,summary,"
                "document_version_id,document_sha256,workflow_status,"
                "created_by_user_id) VALUES ("
                ":id,:enterprise_id,:source_id,:version_number,:title,:domain,"
                ":effect_status,:issued_on,:effective_from,:effective_to,:summary,"
                ":document_version_id,:document_sha256,'draft',:actor_id) "
                f"RETURNING {VERSION_COLUMNS}"
            ),
            {
                "id": version_id,
                "enterprise_id": tenant.enterprise_id,
                "source_id": source_id,
                "version_number": version_number,
                "title": title,
                "domain": domain,
                "effect_status": effect_status,
                "issued_on": issued_on,
                "effective_from": effective_from,
                "effective_to": effective_to,
                "summary": summary,
                "document_version_id": document_version_id,
                "document_sha256": document_sha256,
                "actor_id": actor_id,
            },
        )
    ).mappings().one()


async def get_version(tenant: Tenant, version_id: uuid.UUID) -> dict[str, Any]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        version = await version_row(session, version_id)
        reviews = (
            await session.execute(
                text(
                    f"SELECT {REVIEW_COLUMNS} FROM f1.policy_review_event "
                    "WHERE policy_version_id = :version_id "
                    "ORDER BY occurred_at, id"
                ),
                {"version_id": version_id},
            )
        ).mappings().all()
    output = version_out(version, tenant, actor_id)
    output["review_events"] = [row_dict(row) for row in reviews]
    return output


__all__ = (
    "REVIEW_COLUMNS",
    "SOURCE_COLUMNS",
    "VERSION_COLUMNS",
    "create_source",
    "create_version",
    "get_source",
    "get_version",
    "insert_source_in_session",
    "insert_version_in_session",
    "list_sources",
    "source_out",
    "source_row",
    "update_source",
    "version_out",
    "version_row",
)
