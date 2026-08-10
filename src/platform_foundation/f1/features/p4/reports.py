"""Immutable, unsigned P4 business-report snapshots."""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...audit import add_event
from ...auth import Tenant
from ...business_timeline import add_timeline_event
from ...database import session_scope
from .common import accepted_capacities, current_actor_id, row_dict
from .contracts import (
    BUSINESS_SNAPSHOT_BOUNDARIES,
    is_manager,
    report_allowed_actions,
    report_collection_allowed_actions,
)


_REPORT_COLUMNS = (
    "id, enterprise_id, service_case_id, title, status, current_version_no, "
    "created_by_user_id, created_at, updated_at"
)
_VERSION_COLUMNS = (
    "id, enterprise_id, report_id, version_number, lifecycle, change_note, "
    "canonical_snapshot, snapshot_sha256, snapshot_size_bytes, source_counts, "
    "created_by_user_id, captured_at"
)
_ARTIFACT_COLUMNS = (
    "id, enterprise_id, report_version_id, artifact_kind, storage_kind, "
    "content_type, status, sha256, size_bytes, created_at"
)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _canonical_snapshot_bytes(snapshot: Mapping[str, Any]) -> bytes:
    return json.dumps(
        _json_safe(snapshot),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


async def _report_row(
    session: AsyncSession, report_id: uuid.UUID, *, lock: bool = False
) -> Mapping[str, Any]:
    suffix = " FOR UPDATE" if lock else ""
    row = (
        await session.execute(
            text(
                f"SELECT {_REPORT_COLUMNS} FROM f1.business_report "
                "WHERE id = :report_id" + suffix
            ),
            {"report_id": report_id},
        )
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="BUSINESS_REPORT_NOT_FOUND")
    return row


async def _report_out(
    session: AsyncSession,
    tenant: Tenant,
    actor_id: uuid.UUID,
    row: Mapping[str, Any],
) -> dict[str, Any]:
    capacities = await accepted_capacities(
        session,
        actor_id=actor_id,
        service_case_id=row["service_case_id"],
    )
    output = row_dict(row)
    output["allowed_actions"] = report_allowed_actions(
        tenant.role, str(row["status"]), capacities
    )
    return output


async def _version_with_artifact(
    session: AsyncSession, row: Mapping[str, Any]
) -> dict[str, Any]:
    artifact = (
        await session.execute(
            text(
                f"SELECT {_ARTIFACT_COLUMNS} "
                "FROM f1.business_report_artifact "
                "WHERE report_version_id = :version_id"
            ),
            {"version_id": row["id"]},
        )
    ).mappings().one_or_none()
    output = row_dict(row)
    output["artifact"] = row_dict(artifact) if artifact is not None else None
    output["allowed_actions"] = ["view"]
    output["boundaries"] = list(BUSINESS_SNAPSHOT_BOUNDARIES)
    return output


async def list_reports(tenant: Tenant) -> dict[str, Any]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        rows = (
            await session.execute(
                text(
                    f"SELECT {_REPORT_COLUMNS} FROM f1.business_report "
                    "ORDER BY updated_at DESC, id"
                )
            )
        ).mappings().all()
        items = [
            await _report_out(session, tenant, actor_id, row) for row in rows
        ]
    return {
        "items": items,
        "allowed_actions": report_collection_allowed_actions(tenant.role),
        "boundaries": list(BUSINESS_SNAPSHOT_BOUNDARIES),
    }


async def get_report(tenant: Tenant, report_id: uuid.UUID) -> dict[str, Any]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        report = await _report_row(session, report_id)
        output = await _report_out(session, tenant, actor_id, report)
        version_rows = (
            await session.execute(
                text(
                    f"SELECT {_VERSION_COLUMNS} "
                    "FROM f1.business_report_version "
                    "WHERE report_id = :report_id "
                    "ORDER BY version_number DESC, id"
                ),
                {"report_id": report_id},
            )
        ).mappings().all()
        output["versions"] = [
            await _version_with_artifact(session, row) for row in version_rows
        ]
    output["boundaries"] = list(BUSINESS_SNAPSHOT_BOUNDARIES)
    return output


async def create_report(
    tenant: Tenant,
    *,
    service_case_id: uuid.UUID,
    title: str,
) -> dict[str, Any]:
    if not is_manager(tenant.role):
        raise HTTPException(status_code=403, detail="BUSINESS_REPORT_MANAGER_REQUIRED")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        case_id = (
            await session.execute(
                text(
                    "SELECT id FROM f1.service_case "
                    "WHERE id = :service_case_id"
                ),
                {"service_case_id": service_case_id},
            )
        ).scalar_one_or_none()
        if case_id is None:
            raise HTTPException(status_code=404, detail="SERVICE_CASE_NOT_FOUND")
        report_id = uuid.uuid4()
        row = (
            await session.execute(
                text(
                    "INSERT INTO f1.business_report ("
                    "id, enterprise_id, service_case_id, title, status, "
                    "current_version_no, created_by_user_id) VALUES ("
                    ":id, :enterprise_id, :service_case_id, :title, 'active', "
                    "0, :actor_id) "
                    f"RETURNING {_REPORT_COLUMNS}"
                ),
                {
                    "id": report_id,
                    "enterprise_id": tenant.enterprise_id,
                    "service_case_id": service_case_id,
                    "title": title,
                    "actor_id": actor_id,
                },
            )
        ).mappings().one()
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "business_report.created",
            "business_report",
            str(report_id),
        )
        await add_timeline_event(
            session,
            enterprise_id=tenant.enterprise_id,
            service_case_id=service_case_id,
            actor_user_id=actor_id,
            event_type="business_report.created",
            subject_type="business_report",
            subject_id=report_id,
            status="active",
        )
        await session.commit()
    output = row_dict(row)
    output["allowed_actions"] = ["view", "create_version", "archive"]
    output["boundaries"] = list(BUSINESS_SNAPSHOT_BOUNDARIES)
    return output


async def _capture_snapshot(
    session: AsyncSession,
    *,
    service_case_id: uuid.UUID,
    document_version_ids: tuple[uuid.UUID, ...],
) -> tuple[dict[str, Any], dict[str, int]]:
    case = (
        await session.execute(
            text(
                "SELECT id, plant_id, title, description, service_type, status, "
                "planned_start_at, planned_end_at, created_by_user_id, "
                "created_at, updated_at FROM f1.service_case "
                "WHERE id = :service_case_id"
            ),
            {"service_case_id": service_case_id},
        )
    ).mappings().one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="SERVICE_CASE_NOT_FOUND")

    assignments = (
        await session.execute(
            text(
                "SELECT id, assignee_user_id, capacity, status, assigned_at, "
                "responded_at, revoked_at FROM f1.service_assignment "
                "WHERE service_case_id = :service_case_id "
                "ORDER BY assigned_at, id"
            ),
            {"service_case_id": service_case_id},
        )
    ).mappings().all()
    visits = (
        await session.execute(
            text(
                "SELECT id, status, planned_start_at, planned_end_at, started_at, "
                "completed_at, created_by_user_id, created_at, updated_at "
                "FROM f1.site_visit WHERE service_case_id = :service_case_id "
                "ORDER BY created_at, id"
            ),
            {"service_case_id": service_case_id},
        )
    ).mappings().all()
    findings = (
        await session.execute(
            text(
                "SELECT finding.id, finding.site_visit_id, finding.title, "
                "finding.description, finding.severity, "
                "finding.responsible_user_id, finding.due_at, finding.status, "
                "finding.created_by_user_id, finding.created_at, "
                "finding.updated_at FROM f1.finding AS finding "
                "LEFT JOIN f1.site_visit AS visit "
                "ON visit.enterprise_id = finding.enterprise_id "
                "AND visit.id = finding.site_visit_id "
                "WHERE COALESCE(finding.service_case_id, visit.service_case_id) "
                "= :service_case_id ORDER BY finding.created_at, finding.id"
            ),
            {"service_case_id": service_case_id},
        )
    ).mappings().all()
    corrective_actions = (
        await session.execute(
            text(
                "SELECT action.id, action.finding_id, action.revision, "
                "action.description, action.submitted_by_user_id, "
                "action.submitted_at FROM f1.corrective_action AS action "
                "JOIN f1.finding AS finding "
                "ON finding.enterprise_id = action.enterprise_id "
                "AND finding.id = action.finding_id "
                "LEFT JOIN f1.site_visit AS visit "
                "ON visit.enterprise_id = finding.enterprise_id "
                "AND visit.id = finding.site_visit_id "
                "WHERE COALESCE(finding.service_case_id, visit.service_case_id) "
                "= :service_case_id ORDER BY action.finding_id, "
                "action.revision, action.id"
            ),
            {"service_case_id": service_case_id},
        )
    ).mappings().all()
    reviews = (
        await session.execute(
            text(
                "SELECT review.id, review.finding_id, review.decision, "
                "review.comment, review.reviewer_user_id, review.created_at "
                "FROM f1.finding_review AS review JOIN f1.finding AS finding "
                "ON finding.enterprise_id = review.enterprise_id "
                "AND finding.id = review.finding_id "
                "LEFT JOIN f1.site_visit AS visit "
                "ON visit.enterprise_id = finding.enterprise_id "
                "AND visit.id = finding.site_visit_id "
                "WHERE COALESCE(finding.service_case_id, visit.service_case_id) "
                "= :service_case_id ORDER BY review.created_at, review.id"
            ),
            {"service_case_id": service_case_id},
        )
    ).mappings().all()
    timeline = (
        await session.execute(
            text(
                "SELECT id, event_type, subject_type, subject_id, status, "
                "actor_user_id, occurred_at FROM f1.business_timeline "
                "WHERE service_case_id = :service_case_id "
                "ORDER BY occurred_at, id"
            ),
            {"service_case_id": service_case_id},
        )
    ).mappings().all()

    document_versions: list[Mapping[str, Any]] = []
    if document_version_ids:
        document_versions = (
            await session.execute(
                text(
                    "SELECT version.id, version.document_record_id, "
                    "version.version_no, task.content_sha256 "
                    "FROM f1.document_version AS version "
                    "JOIN f1.upload_task AS task "
                    "ON task.enterprise_id = version.enterprise_id "
                    "AND task.id = version.upload_task_id "
                    "WHERE version.id = ANY(CAST(:version_ids AS uuid[])) "
                    "AND task.pipeline_kind = 'controlled_ingestion' "
                    "AND task.object_state = 'ready' "
                    "AND task.quarantine_status = 'released' "
                    "AND task.scan_verdict = 'clean' "
                    "AND task.preview_status = 'ready' "
                    "ORDER BY version.id"
                ),
                {"version_ids": list(document_version_ids)},
            )
        ).mappings().all()
        if len(document_versions) != len(document_version_ids):
            raise HTTPException(
                status_code=404, detail="REPORT_DOCUMENT_VERSION_NOT_FOUND"
            )

    source_counts = {
        "service_cases": 1,
        "assignments": len(assignments),
        "site_visits": len(visits),
        "findings": len(findings),
        "corrective_actions": len(corrective_actions),
        "finding_reviews": len(reviews),
        "timeline_events": len(timeline),
        "document_versions": len(document_versions),
    }
    snapshot = {
        "schema": "p4-business-snapshot-v1",
        "service_case": row_dict(case),
        "assignments": [row_dict(row) for row in assignments],
        "site_visits": [row_dict(row) for row in visits],
        "findings": [row_dict(row) for row in findings],
        "corrective_actions": [row_dict(row) for row in corrective_actions],
        "finding_reviews": [row_dict(row) for row in reviews],
        "timeline": [row_dict(row) for row in timeline],
        "document_versions": [row_dict(row) for row in document_versions],
        "source_counts": source_counts,
        "boundaries": list(BUSINESS_SNAPSHOT_BOUNDARIES),
    }
    return snapshot, source_counts


async def create_report_version(
    tenant: Tenant,
    report_id: uuid.UUID,
    *,
    change_note: str | None,
    document_version_ids: tuple[uuid.UUID, ...],
) -> dict[str, Any]:
    document_version_ids = tuple(sorted(set(document_version_ids), key=str))
    if document_version_ids and not is_manager(tenant.role):
        raise HTTPException(
            status_code=403, detail="REPORT_DOCUMENT_SOURCE_FORBIDDEN"
        )
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        report = await _report_row(session, report_id, lock=True)
        capacities = await accepted_capacities(
            session,
            actor_id=actor_id,
            service_case_id=report["service_case_id"],
        )
        if "create_version" not in report_allowed_actions(
            tenant.role, str(report["status"]), capacities
        ):
            raise HTTPException(
                status_code=403, detail="BUSINESS_REPORT_VERSION_FORBIDDEN"
            )
        snapshot, source_counts = await _capture_snapshot(
            session,
            service_case_id=report["service_case_id"],
            document_version_ids=document_version_ids,
        )
        snapshot_bytes = _canonical_snapshot_bytes(snapshot)
        if len(snapshot_bytes) > 4 * 1024 * 1024:
            raise HTTPException(
                status_code=422, detail="BUSINESS_REPORT_SNAPSHOT_TOO_LARGE"
            )
        snapshot_sha256 = hashlib.sha256(snapshot_bytes).hexdigest()
        version_number = int(report["current_version_no"]) + 1
        version_id = uuid.uuid4()
        artifact_id = uuid.uuid4()

        await session.execute(
            text(
                "UPDATE f1.business_report_version "
                "SET lifecycle = 'superseded' "
                "WHERE report_id = :report_id AND lifecycle = 'current'"
            ),
            {"report_id": report_id},
        )
        version = (
            await session.execute(
                text(
                    "INSERT INTO f1.business_report_version ("
                    "id, enterprise_id, report_id, version_number, lifecycle, "
                    "change_note, canonical_snapshot, snapshot_sha256, "
                    "snapshot_size_bytes, source_counts, created_by_user_id) "
                    "VALUES (:id, :enterprise_id, :report_id, :version_number, "
                    "'current', :change_note, CAST(:snapshot AS jsonb), "
                    ":snapshot_sha256, :snapshot_size_bytes, "
                    "CAST(:source_counts AS jsonb), :actor_id) "
                    f"RETURNING {_VERSION_COLUMNS}"
                ),
                {
                    "id": version_id,
                    "enterprise_id": tenant.enterprise_id,
                    "report_id": report_id,
                    "version_number": version_number,
                    "change_note": change_note,
                    "snapshot": snapshot_bytes.decode("utf-8"),
                    "snapshot_sha256": snapshot_sha256,
                    "snapshot_size_bytes": len(snapshot_bytes),
                    "source_counts": json.dumps(
                        source_counts, sort_keys=True, separators=(",", ":")
                    ),
                    "actor_id": actor_id,
                },
            )
        ).mappings().one()
        artifact = (
            await session.execute(
                text(
                    "INSERT INTO f1.business_report_artifact ("
                    "id, enterprise_id, report_version_id, artifact_kind, "
                    "storage_kind, content_type, status, sha256, size_bytes) "
                    "VALUES (:id, :enterprise_id, :version_id, 'canonical_json', "
                    "'database_snapshot', 'application/json', 'ready', "
                    ":sha256, :size_bytes) "
                    f"RETURNING {_ARTIFACT_COLUMNS}"
                ),
                {
                    "id": artifact_id,
                    "enterprise_id": tenant.enterprise_id,
                    "version_id": version_id,
                    "sha256": snapshot_sha256,
                    "size_bytes": len(snapshot_bytes),
                },
            )
        ).mappings().one()
        changed = await session.execute(
            text(
                "UPDATE f1.business_report SET current_version_no = :next_no, "
                "updated_at = statement_timestamp() WHERE id = :report_id "
                "AND current_version_no = :current_no AND status = 'active'"
            ),
            {
                "next_no": version_number,
                "report_id": report_id,
                "current_no": report["current_version_no"],
            },
        )
        if changed.rowcount != 1:
            raise HTTPException(
                status_code=409, detail="BUSINESS_REPORT_VERSION_CONFLICT"
            )
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "business_report.version_created",
            "business_report_version",
            str(version_id),
        )
        await add_timeline_event(
            session,
            enterprise_id=tenant.enterprise_id,
            service_case_id=report["service_case_id"],
            actor_user_id=actor_id,
            event_type="business_report.version_created",
            subject_type="business_report_version",
            subject_id=version_id,
            status="current",
        )
        await session.commit()
    output = row_dict(version)
    output["artifact"] = row_dict(artifact)
    output["allowed_actions"] = ["view"]
    output["boundaries"] = list(BUSINESS_SNAPSHOT_BOUNDARIES)
    return output


async def get_report_version(
    tenant: Tenant, version_id: uuid.UUID
) -> dict[str, Any]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await current_actor_id(session, tenant)
        row = (
            await session.execute(
                text(
                    f"SELECT {_VERSION_COLUMNS} "
                    "FROM f1.business_report_version WHERE id = :version_id"
                ),
                {"version_id": version_id},
            )
        ).mappings().one_or_none()
        if row is None:
            raise HTTPException(
                status_code=404, detail="BUSINESS_REPORT_VERSION_NOT_FOUND"
            )
        return await _version_with_artifact(session, row)


async def archive_report(
    tenant: Tenant, report_id: uuid.UUID
) -> dict[str, Any]:
    if not is_manager(tenant.role):
        raise HTTPException(status_code=403, detail="BUSINESS_REPORT_MANAGER_REQUIRED")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        current = await _report_row(session, report_id, lock=True)
        if current["status"] != "active":
            raise HTTPException(status_code=409, detail="BUSINESS_REPORT_STATE_CONFLICT")
        row = (
            await session.execute(
                text(
                    "UPDATE f1.business_report SET status = 'archived', "
                    "updated_at = statement_timestamp() "
                    "WHERE id = :report_id AND status = 'active' "
                    f"RETURNING {_REPORT_COLUMNS}"
                ),
                {"report_id": report_id},
            )
        ).mappings().one_or_none()
        if row is None:
            raise HTTPException(status_code=409, detail="BUSINESS_REPORT_STATE_CONFLICT")
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "business_report.archived",
            "business_report",
            str(report_id),
        )
        await add_timeline_event(
            session,
            enterprise_id=tenant.enterprise_id,
            service_case_id=current["service_case_id"],
            actor_user_id=actor_id,
            event_type="business_report.archived",
            subject_type="business_report",
            subject_id=report_id,
            status="archived",
        )
        await session.commit()
    output = row_dict(row)
    output["allowed_actions"] = ["view"]
    output["boundaries"] = list(BUSINESS_SNAPSHOT_BOUNDARIES)
    return output


__all__ = (
    "archive_report",
    "create_report",
    "create_report_version",
    "get_report",
    "get_report_version",
    "list_reports",
)
