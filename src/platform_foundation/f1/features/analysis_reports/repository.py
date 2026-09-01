"""Analysis-report persistence. API never sees scope/lease/dataset ids."""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..material_rag.repository import load_units_for_version
from .contracts import (
    EvidenceUnit,
    EligibleSource,
    FrozenSourceSet,
    GeneratedReport,
    RECOVERABLE_GENERATION_FAILURE_REASONS,
    SECTION_KEYS,
    TEMPLATE_ID,
)
from .delivery_repository import (
    rebind_historical_delivery_in_session,
    register_delivery_in_session,
)


async def actor_user_id(session: AsyncSession, enterprise_id: uuid.UUID, sub: str) -> uuid.UUID | None:
    return (
        await session.execute(
            text(
                "SELECT membership.user_id "
                "FROM f1.enterprise_user AS membership "
                "JOIN f1.user_profile AS profile ON profile.id = membership.user_id "
                "WHERE membership.enterprise_id = :enterprise_id "
                "AND profile.keycloak_sub = :sub "
                "AND membership.role IN ('super_admin','enterprise_admin')"
            ),
            {"enterprise_id": enterprise_id, "sub": sub},
        )
    ).scalar_one_or_none()


async def crm_account_owned(
    session: AsyncSession, enterprise_id: uuid.UUID, client_account_id: uuid.UUID
) -> bool:
    row = (
        await session.execute(
            text(
                "SELECT 1 FROM f1.crm_account "
                "WHERE enterprise_id = :enterprise_id AND id = :client_account_id "
                "AND stage IN ('lead','active','dormant')"
            ),
            {"enterprise_id": enterprise_id, "client_account_id": client_account_id},
        )
    ).first()
    return row is not None


async def active_binding_for_provider(
    session: AsyncSession, enterprise_id: uuid.UUID, client_account_id: uuid.UUID
) -> bool:
    row = (
        await session.execute(
            text(
                "SELECT 1 FROM f1.analysis_report_client_audience "
                "WHERE enterprise_id = :enterprise_id "
                "AND client_account_id = :client_account_id "
                "AND status = 'active'"
            ),
            {
                "enterprise_id": enterprise_id,
                "client_account_id": client_account_id,
            },
        )
    ).first()
    return row is not None


async def resolve_active_bindings_for_audience(
    session: AsyncSession, audience_enterprise_id: uuid.UUID
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                "SELECT enterprise_id, client_account_id "
                "FROM f1.analysis_report_client_audience "
                "WHERE audience_enterprise_id = :audience_enterprise_id "
                "AND status = 'active'"
            ),
            {"audience_enterprise_id": audience_enterprise_id},
        )
    ).mappings().all()
    return [dict(row) for row in rows]


def fingerprint_for(enterprise_id: uuid.UUID, client_account_id: uuid.UUID, sources: list[EligibleSource]) -> str:
    payload = {
        "tenant": str(enterprise_id),
        "client": str(client_account_id),
        "template": TEMPLATE_ID,
        "sources": [
            {
                "document_version_id": str(item.document_version_id),
                "source_sha256": item.source_sha256,
                "version_number": item.version_number,
                "evidence_body_sha256": [
                    unit.body_sha256
                    for unit in sorted(
                        item.evidence_units,
                        key=lambda unit: (unit.page_number, unit.ordinal),
                    )
                ],
            }
            for item in sorted(
                sources, key=lambda item: str(item.document_version_id)
            )
        ],
    }
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def load_eligible_sources(
    session: AsyncSession, enterprise_id: uuid.UUID, client_account_id: uuid.UUID
) -> list[EligibleSource]:
    rows = (
        await session.execute(
            text(
                "SELECT version.id, record.title, version.version_no, "
                "task.content_sha256, scope.scope_kind, scope.id "
                "FROM f1.document_version AS version "
                "JOIN f1.document_record AS record "
                "  ON record.enterprise_id = version.enterprise_id "
                " AND record.id = version.document_record_id "
                "JOIN f1.upload_task AS task "
                "  ON task.enterprise_id = version.enterprise_id "
                " AND task.id = version.upload_task_id "
                "JOIN f1.material_knowledge_scope AS scope "
                "  ON scope.enterprise_id = record.enterprise_id "
                " AND scope.id = record.knowledge_scope_id "
                "JOIN f1.material_rag_unit AS unit "
                "  ON unit.enterprise_id = version.enterprise_id "
                " AND unit.document_version_id = version.id "
                " AND unit.document_record_id = record.id "
                " AND unit.source_sha256 = task.content_sha256 "
                "WHERE version.enterprise_id = :enterprise_id "
                "  AND record.status = 'active' "
                "  AND version.version_no = record.latest_version_no "
                "  AND task.pipeline_kind = 'controlled_ingestion' "
                "  AND task.quarantine_status = 'released' "
                "  AND task.released_at IS NOT NULL "
                "  AND task.rejected_at IS NULL "
                "  AND task.scan_verdict = 'clean' "
                "  AND task.preview_status = 'ready' "
                "  AND task.object_state = 'ready' "
                "  AND task.status = 'done' "
                "  AND ("
                "    (scope.scope_kind = 'service_provider' "
                "     AND scope.client_account_id IS NULL) "
                "    OR (scope.scope_kind = 'client' "
                "        AND scope.client_account_id = :client_account_id)"
                "  ) "
                "GROUP BY version.id, record.title, version.version_no, "
                "task.content_sha256, scope.scope_kind, scope.id "
                "ORDER BY version.id"
            ),
            {
                "enterprise_id": enterprise_id,
                "client_account_id": client_account_id,
            },
        )
    ).all()
    sources: list[EligibleSource] = []
    for row in rows:
        units = await load_units_for_version(
            session,
            enterprise_id=enterprise_id,
            knowledge_scope_id=row[5],
            document_version_id=row[0],
        )
        evidence_units = tuple(
            EvidenceUnit(
                page_number=unit.page_number,
                ordinal=unit.ordinal,
                body_sha256=unit.body_sha256,
                text=unit.body.reveal(),
            )
            for unit in units
            if unit.source_sha256 == str(row[3])
        )
        if not evidence_units:
            continue
        sources.append(
            EligibleSource(
                document_version_id=row[0],
                document_name=str(row[1]),
                version_number=int(row[2]),
                source_sha256=str(row[3]),
                scope_kind=str(row[4]),
                page_number=evidence_units[0].page_number,
                evidence_units=evidence_units,
            )
        )
    return sources


async def insert_report(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    client_account_id: uuid.UUID,
    request_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> dict[str, Any]:
    report_id = uuid.uuid4()
    try:
        await session.execute(
            text(
                "INSERT INTO f1.analysis_report ("
                "id, enterprise_id, client_account_id, template_id, title, "
                "current_version_id, current_version_no, create_request_id, "
                "created_by_user_id"
                ") VALUES ("
                ":id, :enterprise_id, :client_account_id, :template_id, :title, "
                "NULL, 0, :request_id, :actor_id"
                ")"
            ),
            {
                "id": report_id,
                "enterprise_id": enterprise_id,
                "client_account_id": client_account_id,
                "template_id": TEMPLATE_ID,
                "title": "企业安环资料分析报告",
                "request_id": request_id,
                "actor_id": actor_id,
            },
        )
    except IntegrityError as exc:
        await session.rollback()
        existing = await get_report_by_create_request(session, enterprise_id, request_id)
        if existing is None:
            raise
        if existing["client_account_id"] != client_account_id:
            raise RequestConflict() from exc
        return existing
    return await get_report(session, enterprise_id, report_id)  # type: ignore[return-value]


class RequestConflict(Exception):
    pass


async def get_report_by_create_request(
    session: AsyncSession, enterprise_id: uuid.UUID, request_id: uuid.UUID
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "SELECT id, enterprise_id, client_account_id, current_version_id, "
                "current_version_no, updated_at "
                "FROM f1.analysis_report "
                "WHERE enterprise_id = :enterprise_id "
                "AND create_request_id = :request_id"
            ),
            {"enterprise_id": enterprise_id, "request_id": request_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def get_report(
    session: AsyncSession, enterprise_id: uuid.UUID, report_id: uuid.UUID
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "SELECT report.id, report.enterprise_id, report.client_account_id, "
                "report.current_version_id, report.current_version_no, "
                "report.updated_at, version.status AS current_status "
                "FROM f1.analysis_report AS report "
                "LEFT JOIN f1.analysis_report_version AS version "
                "  ON version.enterprise_id = report.enterprise_id "
                " AND version.id = report.current_version_id "
                "WHERE report.enterprise_id = :enterprise_id "
                "AND report.id = :report_id"
            ),
            {"enterprise_id": enterprise_id, "report_id": report_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def lock_report_for_generation(
    session: AsyncSession, enterprise_id: uuid.UUID, report_id: uuid.UUID
) -> dict[str, Any] | None:
    """Reload and lock the report row before allocating its next version."""
    row = (
        await session.execute(
            text(
                "SELECT report.id, report.enterprise_id, report.client_account_id, "
                "report.current_version_id, report.current_version_no, "
                "report.updated_at, version.status AS current_status, "
                "version.source_fingerprint_sha256 AS current_source_fingerprint "
                "FROM f1.analysis_report AS report "
                "LEFT JOIN f1.analysis_report_version AS version "
                "  ON version.enterprise_id = report.enterprise_id "
                " AND version.id = report.current_version_id "
                "WHERE report.enterprise_id = :enterprise_id "
                "AND report.id = :report_id FOR UPDATE OF report"
            ),
            {"enterprise_id": enterprise_id, "report_id": report_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def list_provider_reports(
    session: AsyncSession, enterprise_id: uuid.UUID, client_account_id: uuid.UUID
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                "SELECT report.id, report.current_version_id, report.current_version_no, "
                "report.updated_at, version.status AS current_status "
                "FROM f1.analysis_report AS report "
                "LEFT JOIN f1.analysis_report_version AS version "
                "  ON version.enterprise_id = report.enterprise_id "
                " AND version.id = report.current_version_id "
                "WHERE report.enterprise_id = :enterprise_id "
                "AND report.client_account_id = :client_account_id "
                "ORDER BY report.updated_at DESC"
            ),
            {
                "enterprise_id": enterprise_id,
                "client_account_id": client_account_id,
            },
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def list_published_for_client(
    session: AsyncSession, audience_enterprise_id: uuid.UUID
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                "SELECT report.id AS report_id, version.id AS version_id, "
                "version.version_number, version.published_at "
                "FROM f1.analysis_report AS report "
                "JOIN f1.analysis_report_client_audience AS binding "
                "  ON binding.enterprise_id = report.enterprise_id "
                " AND binding.client_account_id = report.client_account_id "
                " AND binding.status = 'active' "
                " AND binding.audience_enterprise_id = :audience_enterprise_id "
                "JOIN f1.analysis_report_version AS version "
                "  ON version.enterprise_id = report.enterprise_id "
                " AND version.report_id = report.id "
                "WHERE version.status = 'published' "
                "AND version.artifact_ready IS TRUE "
                "ORDER BY version.published_at DESC, version.version_number DESC, version.id DESC"
            ),
            {"audience_enterprise_id": audience_enterprise_id},
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def get_published_detail(
    session: AsyncSession, audience_enterprise_id: uuid.UUID, report_id: uuid.UUID
) -> dict[str, Any] | None:
    header = (
        await session.execute(
            text(
                "SELECT report.id AS report_id, version.id AS version_id, "
                "version.version_number, version.published_at "
                "FROM f1.analysis_report AS report "
                "JOIN f1.analysis_report_client_audience AS binding "
                "  ON binding.enterprise_id = report.enterprise_id "
                " AND binding.client_account_id = report.client_account_id "
                " AND binding.status = 'active' "
                " AND binding.audience_enterprise_id = :audience_enterprise_id "
                "JOIN f1.analysis_report_version AS version "
                "  ON version.enterprise_id = report.enterprise_id "
                " AND version.report_id = report.id "
                "WHERE report.id = :report_id "
                "AND version.status = 'published' "
                "AND version.artifact_ready IS TRUE"
            ),
            {
                "report_id": report_id,
                "audience_enterprise_id": audience_enterprise_id,
            },
        )
    ).mappings().first()
    if header is None:
        return None
    return await attach_sections(session, dict(header), header["version_id"])


async def attach_sections(
    session: AsyncSession, payload: dict[str, Any], version_id: uuid.UUID
) -> dict[str, Any]:
    sections = (
        await session.execute(
            text(
                "SELECT section_key, title, body "
                "FROM f1.analysis_report_section "
                "WHERE version_id = :version_id "
                "ORDER BY ordinal"
            ),
            {"version_id": version_id},
        )
    ).mappings().all()
    citations = (
        await session.execute(
            text(
                "SELECT id, document_version_id, document_name, version_number, "
                "page_number, excerpt "
                "FROM f1.analysis_report_citation "
                "WHERE version_id = :version_id "
                "ORDER BY ordinal"
            ),
            {"version_id": version_id},
        )
    ).mappings().all()
    payload["sections"] = [dict(row) for row in sections]
    payload["citations"] = [dict(row) for row in citations]
    return payload


async def insert_review_event(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    report_id: uuid.UUID,
    version_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: str,
    checklist: dict[str, bool],
    comment: str | None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO f1.analysis_report_review_event ("
            "id, enterprise_id, report_id, version_id, actor_user_id, "
            "action, checklist, comment) VALUES ("
            ":id, :enterprise_id, :report_id, :version_id, :actor_id, "
            ":action, CAST(:checklist AS jsonb), :comment)"
        ),
        {
            "id": uuid.uuid4(),
            "enterprise_id": enterprise_id,
            "report_id": report_id,
            "version_id": version_id,
            "actor_id": actor_id,
            "action": action,
            "checklist": json.dumps(
                checklist, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            ),
            "comment": comment,
        },
    )


async def list_review_events(
    session: AsyncSession, *, enterprise_id: uuid.UUID, version_id: uuid.UUID
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                "SELECT id, action, checklist, comment, created_at "
                "FROM f1.analysis_report_review_event "
                "WHERE enterprise_id = :enterprise_id AND version_id = :version_id "
                "ORDER BY created_at, id"
            ),
            {"enterprise_id": enterprise_id, "version_id": version_id},
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def get_version(
    session: AsyncSession, enterprise_id: uuid.UUID, version_id: uuid.UUID
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "SELECT version.id, version.report_id, version.version_number, "
                "version.status, version.artifact_ready, version.published_at, "
                "version.source_fingerprint_sha256, version.created_at, "
                "report.client_account_id, report.current_version_id "
                "FROM f1.analysis_report_version AS version "
                "JOIN f1.analysis_report AS report "
                "  ON report.enterprise_id = version.enterprise_id "
                " AND report.id = version.report_id "
                "WHERE version.enterprise_id = :enterprise_id "
                "AND version.id = :version_id"
            ),
            {"enterprise_id": enterprise_id, "version_id": version_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def list_versions(
    session: AsyncSession, enterprise_id: uuid.UUID, report_id: uuid.UUID
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                "SELECT id, version_number, status, created_at "
                "FROM f1.analysis_report_version "
                "WHERE enterprise_id = :enterprise_id AND report_id = :report_id "
                "ORDER BY version_number"
            ),
            {"enterprise_id": enterprise_id, "report_id": report_id},
        )
    ).mappings().all()
    return [dict(row) for row in rows]


async def get_job(
    session: AsyncSession, enterprise_id: uuid.UUID, job_id: uuid.UUID
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "SELECT id, version_id, status, error_reason, "
                "source_fingerprint_sha256, request_id, lease_until "
                "FROM f1.analysis_report_generation_job "
                "WHERE enterprise_id = :enterprise_id AND id = :job_id"
            ),
            {"enterprise_id": enterprise_id, "job_id": job_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def fail_revoked_actor_generation(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    job_id: uuid.UUID,
    provider_sub: str,
) -> bool:
    """Close an exact unclaimed job after its bound delivery actor lost authority.

    The database function owns the report→job/version lock order, rechecks the
    delivery actor identity and current-version fence, and records the fixed audit
    event.  The runtime role cannot directly widen the affected row or reason.
    """

    return bool(
        (
            await session.execute(
                text(
                    "SELECT f1.fail_revoked_report_generation("
                    ":enterprise_id,:job_id,:provider_sub)"
                ),
                {
                    "enterprise_id": enterprise_id,
                    "job_id": job_id,
                    "provider_sub": provider_sub,
                },
            )
        ).scalar_one()
    )


async def get_job_by_request(
    session: AsyncSession, enterprise_id: uuid.UUID, request_id: uuid.UUID
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "SELECT id, version_id, status, error_reason, "
                "source_fingerprint_sha256, request_id, report_id, "
                "(status = 'queued' OR (status = 'generating' "
                "AND lease_until <= statement_timestamp())) "
                "AS dispatch_recoverable "
                "FROM f1.analysis_report_generation_job "
                "WHERE enterprise_id = :enterprise_id AND request_id = :request_id"
            ),
            {"enterprise_id": enterprise_id, "request_id": request_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def get_job_by_fingerprint(
    session: AsyncSession,
    enterprise_id: uuid.UUID,
    report_id: uuid.UUID,
    fingerprint: str,
) -> dict[str, Any] | None:
    """Find prior work for an exact report/source set under the report lock."""
    row = (
        await session.execute(
            text(
                "SELECT id,version_id,request_id,status,error_reason,"
                "source_fingerprint_sha256,report_id "
                "FROM f1.analysis_report_generation_job "
                "WHERE enterprise_id=:enterprise_id AND report_id=:report_id "
                "AND source_fingerprint_sha256=:fingerprint "
                "ORDER BY created_at DESC,id DESC LIMIT 1"
            ),
            {
                "enterprise_id": enterprise_id,
                "report_id": report_id,
                "fingerprint": fingerprint,
            },
        )
    ).mappings().first()
    return dict(row) if row else None


async def get_job_for_version(
    session: AsyncSession,
    enterprise_id: uuid.UUID,
    report_id: uuid.UUID,
    version_id: uuid.UUID,
) -> dict[str, Any] | None:
    """Load the exact current version job while the caller holds report lock."""
    row = (
        await session.execute(
            text(
                "SELECT id,version_id,request_id,status,error_reason,"
                "source_fingerprint_sha256,report_id "
                "FROM f1.analysis_report_generation_job "
                "WHERE enterprise_id=:enterprise_id AND report_id=:report_id "
                "AND version_id=:version_id ORDER BY created_at DESC,id DESC LIMIT 1"
            ),
            {
                "enterprise_id": enterprise_id,
                "report_id": report_id,
                "version_id": version_id,
            },
        )
    ).mappings().first()
    return dict(row) if row else None


async def begin_generation(
    session: AsyncSession,
    *,
    report: dict[str, Any],
    actor_id: uuid.UUID,
    actor_sub: str,
    request_id: uuid.UUID,
    frozen: FrozenSourceSet,
) -> dict[str, Any]:
    version_id = uuid.uuid4()
    job_id = uuid.uuid4()
    next_no = int(report["current_version_no"]) + 1
    await session.execute(
        text(
            "INSERT INTO f1.analysis_report_version ("
            "id, enterprise_id, report_id, client_account_id, version_number, status, "
            "source_fingerprint_sha256, artifact_ready, created_by_user_id"
            ") VALUES ("
            ":id, :enterprise_id, :report_id, :client_account_id, :version_number, 'queued', "
            ":fingerprint, FALSE, :actor_id"
            ")"
        ),
        {
            "id": version_id,
            "enterprise_id": report["enterprise_id"],
            "report_id": report["id"],
            "client_account_id": report["client_account_id"],
            "version_number": next_no,
            "fingerprint": frozen.fingerprint_sha256,
            "actor_id": actor_id,
        },
    )
    await session.execute(
        text(
            "INSERT INTO f1.analysis_report_generation_job ("
            "id, enterprise_id, report_id, version_id, request_id, status, "
            "source_fingerprint_sha256, lease_token, lease_until, lease_owner"
            ") VALUES ("
            ":id, :enterprise_id, :report_id, :version_id, :request_id, "
            "'queued', :fingerprint, NULL, NULL, NULL"
            ")"
        ),
        {
            "id": job_id,
            "enterprise_id": report["enterprise_id"],
            "report_id": report["id"],
            "version_id": version_id,
            "request_id": request_id,
            "fingerprint": frozen.fingerprint_sha256,
        },
    )
    await session.execute(
        text(
            "UPDATE f1.analysis_report SET current_version_id = :version_id, "
            "current_version_no = :version_number, "
            "updated_at = statement_timestamp() "
            "WHERE enterprise_id = :enterprise_id AND id = :report_id"
        ),
        {
            "version_id": version_id,
            "version_number": next_no,
            "enterprise_id": report["enterprise_id"],
            "report_id": report["id"],
        },
    )
    await add_audit(
        session,
        enterprise_id=report["enterprise_id"],
        report_id=report["id"],
        version_id=version_id,
        actor_id=actor_id,
        action="generate",
        from_status="empty" if next_no == 1 else "prior",
        to_status="queued",
    )
    await register_delivery_in_session(
        session,
        enterprise_id=report["enterprise_id"],
        job_id=job_id,
        actor_sub=actor_sub,
    )
    return {
        "job_id": job_id,
        "version_id": version_id,
        "fingerprint": frozen.fingerprint_sha256,
    }


async def claim_generation_job(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    job_id: uuid.UUID,
    version_id: uuid.UUID,
    delivery_id: uuid.UUID,
    dispatch_token: uuid.UUID,
    lease_token: uuid.UUID,
    lease_owner: str,
    lease_seconds: int = 300,
) -> dict[str, Any] | None:
    if not 30 <= lease_seconds <= 900:
        raise ValueError("REPORT_LEASE_INVALID")
    claimed = (
        await session.execute(
            text(
                "UPDATE f1.analysis_report_generation_job AS job SET "
                "status = 'generating', lease_token = :lease_token, "
                "lease_owner = :lease_owner, "
                "lease_until = statement_timestamp() "
                "+ make_interval(secs => :lease_seconds), "
                "updated_at = statement_timestamp() "
                "WHERE job.enterprise_id = :enterprise_id AND job.id = :job_id "
                "AND job.version_id = :version_id "
                "AND EXISTS (SELECT 1 "
                "FROM f1.analysis_report_generation_delivery AS delivery "
                "WHERE delivery.enterprise_id = :enterprise_id "
                "AND delivery.id = :delivery_id "
                "AND delivery.report_id = job.report_id "
                "AND delivery.job_id = job.id "
                "AND delivery.version_id = job.version_id "
                "AND delivery.state = 'dispatched' "
                "AND delivery.dispatch_token = :dispatch_token "
                "AND delivery.dispatch_lease_until > statement_timestamp()) "
                "AND EXISTS (SELECT 1 FROM f1.analysis_report AS report "
                "WHERE report.enterprise_id = job.enterprise_id "
                "AND report.id = job.report_id "
                "AND report.current_version_id = job.version_id) "
                "AND (job.status = 'queued' OR (job.status = 'generating' "
                "AND job.lease_until <= statement_timestamp())) "
                "RETURNING job.report_id, job.version_id, "
                "job.source_fingerprint_sha256"
            ),
            {
                "enterprise_id": enterprise_id,
                "job_id": job_id,
                "version_id": version_id,
                "delivery_id": delivery_id,
                "dispatch_token": dispatch_token,
                "lease_token": lease_token,
                "lease_owner": lease_owner,
                "lease_seconds": lease_seconds,
            },
        )
    ).mappings().first()
    if claimed is None:
        return None
    await session.execute(
        text(
            "UPDATE f1.analysis_report_version SET status = 'generating', "
            "updated_at = statement_timestamp() "
            "WHERE enterprise_id = :enterprise_id AND id = :version_id "
            "AND status IN ('queued','generating')"
        ),
        {
            "enterprise_id": enterprise_id,
            "version_id": claimed["version_id"],
        },
    )
    detail = (
        await session.execute(
            text(
                "SELECT report.client_account_id, version.created_by_user_id "
                "FROM f1.analysis_report AS report "
                "JOIN f1.analysis_report_version AS version "
                "ON version.enterprise_id = report.enterprise_id "
                "AND version.report_id = report.id "
                "WHERE report.enterprise_id = :enterprise_id "
                "AND report.id = :report_id AND version.id = :version_id"
            ),
            {
                "enterprise_id": enterprise_id,
                "report_id": claimed["report_id"],
                "version_id": claimed["version_id"],
            },
        )
    ).mappings().first()
    if detail is None:
        return None
    return {
        **dict(claimed),
        **dict(detail),
        "lease_token": lease_token,
    }


async def fail_queued_generation(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    job_id: uuid.UUID,
    version_id: uuid.UUID,
    reason: str,
) -> bool:
    """Fail only an undispatched job that no worker has claimed."""
    failed = (
        await session.execute(
            text(
                "UPDATE f1.analysis_report_generation_job SET status = 'failed', "
                "error_reason = :reason, updated_at = statement_timestamp() "
                "WHERE enterprise_id = :enterprise_id AND id = :job_id "
                "AND version_id = :version_id AND status = 'queued' "
                "AND lease_token IS NULL AND lease_until IS NULL "
                "RETURNING id"
            ),
            {
                "enterprise_id": enterprise_id,
                "job_id": job_id,
                "version_id": version_id,
                "reason": reason,
            },
        )
    ).first()
    if failed is None:
        return False
    await session.execute(
        text(
            "UPDATE f1.analysis_report_version SET status = 'failed', "
            "updated_at = statement_timestamp() "
            "WHERE enterprise_id = :enterprise_id AND id = :version_id "
            "AND status = 'queued'"
        ),
        {"enterprise_id": enterprise_id, "version_id": version_id},
    )
    return True


async def requeue_failed_generation(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    report_id: uuid.UUID,
    job_id: uuid.UUID,
    version_id: uuid.UUID,
    actor_id: uuid.UUID,
    actor_sub: str,
    reason: str,
    audit_action: str,
) -> bool:
    """Audit and replay only an explicitly recoverable terminal failure."""
    if reason not in RECOVERABLE_GENERATION_FAILURE_REASONS:
        raise ValueError("REPORT_FAILURE_NOT_RECOVERABLE")
    if audit_action != "redispatch":
        raise ValueError("REPORT_REDISPATCH_AUDIT_INVALID")
    report = (
        await session.execute(
            text(
                "SELECT current_version_id FROM f1.analysis_report "
                "WHERE enterprise_id=:enterprise_id AND id=:report_id "
                "FOR UPDATE"
            ),
            {"enterprise_id": enterprise_id, "report_id": report_id},
        )
    ).first()
    if report is None or report[0] != version_id:
        return False
    locked = (
        await session.execute(
            text(
                "SELECT report_id FROM f1.analysis_report_generation_job "
                "WHERE enterprise_id=:enterprise_id AND report_id=:report_id "
                "AND id=:job_id AND version_id=:version_id AND status='failed' "
                "AND error_reason=:reason FOR UPDATE"
            ),
            {
                "enterprise_id": enterprise_id,
                "report_id": report_id,
                "job_id": job_id,
                "version_id": version_id,
                "reason": reason,
            },
        )
    ).first()
    if locked is None:
        return False
    await register_delivery_in_session(
        session,
        enterprise_id=enterprise_id,
        job_id=job_id,
        actor_sub=actor_sub,
        rearm_failed=True,
    )
    version = (
        await session.execute(
            text(
                "UPDATE f1.analysis_report_version SET status='queued', "
                "updated_at=statement_timestamp() "
                "WHERE enterprise_id=:enterprise_id AND id=:version_id "
                "AND report_id=:report_id AND status='failed' RETURNING id"
            ),
            {
                "enterprise_id": enterprise_id,
                "version_id": version_id,
                "report_id": locked[0],
            },
        )
    ).first()
    if version is None:
        raise RuntimeError("REPORT_REDISPATCH_STATE_INVALID")
    job = (
        await session.execute(
            text(
                "UPDATE f1.analysis_report_generation_job SET status='queued', "
                "error_reason=NULL,lease_token=NULL,lease_until=NULL,lease_owner=NULL, "
                "updated_at=statement_timestamp() "
                "WHERE enterprise_id=:enterprise_id AND id=:job_id "
                "AND version_id=:version_id AND status='failed' "
                "AND error_reason=:reason RETURNING id"
            ),
            {
                "enterprise_id": enterprise_id,
                "job_id": job_id,
                "version_id": version_id,
                "reason": reason,
            },
        )
    ).first()
    if job is None:
        raise RuntimeError("REPORT_REDISPATCH_STATE_INVALID")
    await add_audit(
        session,
        enterprise_id=enterprise_id,
        report_id=locked[0],
        version_id=version_id,
        actor_id=actor_id,
        action=audit_action,
        from_status="failed",
        to_status="queued",
    )
    return True


async def fail_claimed_generation(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    job_id: uuid.UUID,
    version_id: uuid.UUID,
    lease_token: uuid.UUID,
    reason: str,
) -> bool:
    """Fail only while the caller still owns the live generation lease."""
    owned = (
        await session.execute(
            text(
                "SELECT 1 FROM f1.analysis_report_generation_job "
                "WHERE enterprise_id = :enterprise_id AND id = :job_id "
                "AND version_id = :version_id AND status = 'generating' "
                "AND lease_token = :lease_token "
                "AND lease_until > statement_timestamp() FOR UPDATE"
            ),
            {
                "enterprise_id": enterprise_id,
                "job_id": job_id,
                "version_id": version_id,
                "lease_token": lease_token,
            },
        )
    ).first()
    if owned is None:
        return False
    await session.execute(
        text(
            "DELETE FROM f1.analysis_report_citation "
            "WHERE enterprise_id = :enterprise_id AND version_id = :version_id"
        ),
        {"enterprise_id": enterprise_id, "version_id": version_id},
    )
    await session.execute(
        text(
            "DELETE FROM f1.analysis_report_section "
            "WHERE enterprise_id = :enterprise_id AND version_id = :version_id"
        ),
        {"enterprise_id": enterprise_id, "version_id": version_id},
    )
    await session.execute(
        text(
            "UPDATE f1.analysis_report_version SET status = 'failed', "
            "updated_at = statement_timestamp() "
            "WHERE enterprise_id = :enterprise_id AND id = :version_id "
            "AND status = 'generating'"
        ),
        {"enterprise_id": enterprise_id, "version_id": version_id},
    )
    failed = (
        await session.execute(
            text(
                "UPDATE f1.analysis_report_generation_job SET status = 'failed', "
                "error_reason = :reason, lease_token = NULL, lease_until = NULL, "
                "lease_owner = NULL, updated_at = statement_timestamp() "
                "WHERE enterprise_id = :enterprise_id AND id = :job_id "
                "AND version_id = :version_id AND status = 'generating' "
                "AND lease_token = :lease_token RETURNING id"
            ),
            {
                "enterprise_id": enterprise_id,
                "job_id": job_id,
                "version_id": version_id,
                "lease_token": lease_token,
                "reason": reason,
            },
        )
    ).first()
    return failed is not None


async def release_generation_claim(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    job_id: uuid.UUID,
    version_id: uuid.UUID,
    lease_token: uuid.UUID,
) -> bool:
    """Return an owned transient failure to queued for RQ Retry."""
    released = (
        await session.execute(
            text(
                "UPDATE f1.analysis_report_generation_job SET status = 'queued', "
                "lease_token = NULL, lease_until = NULL, lease_owner = NULL, "
                "updated_at = statement_timestamp() "
                "WHERE enterprise_id = :enterprise_id AND id = :job_id "
                "AND version_id = :version_id AND status = 'generating' "
                "AND lease_token = :lease_token RETURNING id"
            ),
            {
                "enterprise_id": enterprise_id,
                "job_id": job_id,
                "version_id": version_id,
                "lease_token": lease_token,
            },
        )
    ).first()
    if released is None:
        return False
    await session.execute(
        text(
            "UPDATE f1.analysis_report_version SET status = 'queued', "
            "updated_at = statement_timestamp() "
            "WHERE enterprise_id = :enterprise_id AND id = :version_id "
            "AND status = 'generating'"
        ),
        {"enterprise_id": enterprise_id, "version_id": version_id},
    )
    return True


async def claim_live_lease(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    job_id: uuid.UUID,
    lease_token: uuid.UUID,
    lease_owner: str,
) -> bool:
    row = (
        await session.execute(
            text(
                "UPDATE f1.analysis_report_generation_job "
                "SET status = 'generating', updated_at = statement_timestamp() "
                "WHERE enterprise_id = :enterprise_id AND id = :job_id "
                "AND lease_token = :lease_token AND lease_owner = :lease_owner "
                "AND status = 'queued' "
                "AND lease_until > statement_timestamp() "
                "RETURNING id"
            ),
            {
                "enterprise_id": enterprise_id,
                "job_id": job_id,
                "lease_token": lease_token,
                "lease_owner": lease_owner,
            },
        )
    ).first()
    if row is None:
        return False
    await session.execute(
        text(
            "UPDATE f1.analysis_report_version SET status = 'generating', "
            "updated_at = statement_timestamp() "
            "WHERE enterprise_id = :enterprise_id AND id = ("
            "  SELECT version_id FROM f1.analysis_report_generation_job "
            "  WHERE id = :job_id"
            ")"
        ),
        {"enterprise_id": enterprise_id, "job_id": job_id},
    )
    return True


async def persist_generated(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    job_id: uuid.UUID,
    version_id: uuid.UUID,
    lease_token: uuid.UUID,
    generated: GeneratedReport,
) -> bool:
    live = (
        await session.execute(
            text(
                "SELECT version_id FROM f1.analysis_report_generation_job "
                "WHERE enterprise_id = :enterprise_id AND id = :job_id "
                "AND version_id = :version_id "
                "AND lease_token = :lease_token AND status = 'generating' "
                "AND lease_until > statement_timestamp() FOR UPDATE"
            ),
            {
                "enterprise_id": enterprise_id,
                "job_id": job_id,
                "version_id": version_id,
                "lease_token": lease_token,
            },
        )
    ).first()
    if live is None:
        return False
    if not generated.citations:
        return False
    if {section.key for section in generated.sections} != set(SECTION_KEYS):
        return False
    for ordinal, section in enumerate(generated.sections, start=1):
        if section.key not in SECTION_KEYS:
            return False
        await session.execute(
            text(
                "INSERT INTO f1.analysis_report_section ("
                "id, enterprise_id, version_id, section_key, title, body, ordinal"
                ") VALUES ("
                ":id, :enterprise_id, :version_id, :section_key, :title, :body, :ordinal"
                ")"
            ),
            {
                "id": uuid.uuid4(),
                "enterprise_id": enterprise_id,
                "version_id": version_id,
                "section_key": section.key,
                "title": section.title,
                "body": section.body,
                "ordinal": ordinal,
            },
        )
    for ordinal, citation in enumerate(generated.citations, start=1):
        await session.execute(
            text(
                "INSERT INTO f1.analysis_report_citation ("
                "id, enterprise_id, version_id, document_version_id, "
                "document_name, version_number, page_number, excerpt, ordinal"
                ") VALUES ("
                ":id, :enterprise_id, :version_id, :document_version_id, "
                ":document_name, :version_number, :page_number, :excerpt, :ordinal"
                ")"
            ),
            {
                "id": uuid.uuid4(),
                "enterprise_id": enterprise_id,
                "version_id": version_id,
                "document_version_id": citation.document_version_id,
                "document_name": citation.document_name,
                "version_number": citation.version_number,
                "page_number": citation.page_number,
                "excerpt": citation.excerpt,
                "ordinal": ordinal,
            },
        )
    job_row = (
        await session.execute(
            text(
                "UPDATE f1.analysis_report_generation_job SET status = 'draft', "
                "error_reason = NULL, updated_at = statement_timestamp() "
                "WHERE enterprise_id = :enterprise_id AND id = :job_id "
                "AND version_id = :version_id "
                "AND lease_token = :lease_token AND status = 'generating' "
                "AND lease_until > statement_timestamp() "
                "RETURNING id"
            ),
            {
                "enterprise_id": enterprise_id,
                "job_id": job_id,
                "version_id": version_id,
                "lease_token": lease_token,
            },
        )
    ).first()
    if job_row is None:
        await session.execute(
            text(
                "DELETE FROM f1.analysis_report_citation "
                "WHERE enterprise_id = :enterprise_id AND version_id = :version_id"
            ),
            {"enterprise_id": enterprise_id, "version_id": version_id},
        )
        await session.execute(
            text(
                "DELETE FROM f1.analysis_report_section "
                "WHERE enterprise_id = :enterprise_id AND version_id = :version_id"
            ),
            {"enterprise_id": enterprise_id, "version_id": version_id},
        )
        return False
    version_row = (
        await session.execute(
            text(
                "UPDATE f1.analysis_report_version SET status = 'draft', "
                "updated_at = statement_timestamp() "
                "WHERE enterprise_id = :enterprise_id AND id = :version_id "
                "AND status = 'generating' RETURNING id"
            ),
            {"enterprise_id": enterprise_id, "version_id": version_id},
        )
    ).first()
    if version_row is None:
        # The job update and generated rows must roll back together; accepting a
        # draft job without the exact version transition would corrupt the fence.
        raise RuntimeError("REPORT_GENERATION_STATE_INVALID")
    return True


async def fail_generation(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    job_id: uuid.UUID,
    version_id: uuid.UUID,
    reason: str,
) -> None:
    await session.execute(
        text(
            "DELETE FROM f1.analysis_report_citation "
            "WHERE enterprise_id = :enterprise_id AND version_id = :version_id"
        ),
        {"enterprise_id": enterprise_id, "version_id": version_id},
    )
    await session.execute(
        text(
            "DELETE FROM f1.analysis_report_section "
            "WHERE enterprise_id = :enterprise_id AND version_id = :version_id"
        ),
        {"enterprise_id": enterprise_id, "version_id": version_id},
    )
    await session.execute(
        text(
            "UPDATE f1.analysis_report_version SET status = 'failed', "
            "updated_at = statement_timestamp() "
            "WHERE enterprise_id = :enterprise_id AND id = :version_id "
            "AND status IN ('queued','generating')"
        ),
        {"enterprise_id": enterprise_id, "version_id": version_id},
    )
    await session.execute(
        text(
            "UPDATE f1.analysis_report_generation_job SET status = 'failed', "
            "error_reason = :reason, updated_at = statement_timestamp() "
            "WHERE enterprise_id = :enterprise_id AND id = :job_id"
        ),
        {"enterprise_id": enterprise_id, "job_id": job_id, "reason": reason},
    )


async def transition_version(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    version_id: uuid.UUID,
    from_status: str,
    to_status: str,
    actor_id: uuid.UUID,
    action: str,
    artifact_ready: bool | None = None,
    published_at: bool = False,
) -> bool:
    sets = ["status = :to_status", "updated_at = statement_timestamp()"]
    params: dict[str, Any] = {
        "enterprise_id": enterprise_id,
        "version_id": version_id,
        "from_status": from_status,
        "to_status": to_status,
        "actor_id": actor_id,
        "action": action,
    }
    if artifact_ready is not None:
        sets.append("artifact_ready = :artifact_ready")
        params["artifact_ready"] = artifact_ready
    if published_at:
        sets.append("published_at = statement_timestamp()")
    row = (
        await session.execute(
            text(
                "UPDATE f1.analysis_report_version SET "
                + ", ".join(sets)
                + " WHERE enterprise_id = :enterprise_id AND id = :version_id "
                "AND status = :from_status RETURNING report_id"
            ),
            params,
        )
    ).first()
    if row is None:
        return False
    report_id = row[0]
    if to_status == "published":
        await session.execute(
            text(
                "UPDATE f1.analysis_report_version SET status = 'superseded', "
                "updated_at = statement_timestamp() "
                "WHERE enterprise_id = :enterprise_id AND report_id = :report_id "
                "AND status = 'published' AND id <> :version_id"
            ),
            {
                "enterprise_id": enterprise_id,
                "report_id": report_id,
                "version_id": version_id,
            },
        )
        current = (
            await session.execute(
                text(
                    "UPDATE f1.analysis_report SET current_version_id = :version_id, "
                    "updated_at = statement_timestamp() "
                    "WHERE enterprise_id = :enterprise_id AND id = :report_id "
                    "AND current_version_id = :version_id RETURNING id"
                ),
                {
                    "version_id": version_id,
                    "enterprise_id": enterprise_id,
                    "report_id": report_id,
                },
            )
        ).first()
        if current is None:
            # The caller must roll the transaction back; never repoint a report
            # after a concurrent generation has installed a newer current row.
            return False
    if to_status in {"published", "withdrawn"}:
        await refresh_report_client_visible(
            session, enterprise_id=enterprise_id, report_id=report_id
        )
    await add_audit(
        session,
        enterprise_id=enterprise_id,
        report_id=report_id,
        version_id=version_id,
        actor_id=actor_id,
        action=action,
        from_status=from_status,
        to_status=to_status,
    )
    return True


async def refresh_report_client_visible(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    report_id: uuid.UUID,
) -> None:
    await session.execute(
        text(
            "UPDATE f1.analysis_report AS report "
            "SET client_visible = EXISTS ("
            "  SELECT 1 FROM f1.analysis_report_version AS version "
            "  WHERE version.enterprise_id = report.enterprise_id "
            "    AND version.report_id = report.id "
            "    AND version.status = 'published' "
            "    AND version.artifact_ready IS TRUE"
            "), updated_at = statement_timestamp() "
            "WHERE report.enterprise_id = :enterprise_id AND report.id = :report_id"
        ),
        {"enterprise_id": enterprise_id, "report_id": report_id},
    )


async def add_audit(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    report_id: uuid.UUID,
    version_id: uuid.UUID | None,
    actor_id: uuid.UUID,
    action: str,
    from_status: str,
    to_status: str,
) -> None:
    await session.execute(
        text(
            "INSERT INTO f1.analysis_report_audit_event ("
            "id, enterprise_id, report_id, version_id, actor_user_id, "
            "action, from_status, to_status"
            ") VALUES ("
            ":id, :enterprise_id, :report_id, :version_id, :actor_id, "
            ":action, :from_status, :to_status"
            ")"
        ),
        {
            "id": uuid.uuid4(),
            "enterprise_id": enterprise_id,
            "report_id": report_id,
            "version_id": version_id,
            "actor_id": actor_id,
            "action": action,
            "from_status": from_status,
            "to_status": to_status,
        },
    )


async def insert_health_snapshot(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    report_id: uuid.UUID,
    version_id: uuid.UUID,
    client_account_id: uuid.UUID,
    payload: dict[str, Any],
    payload_sha256: str,
    score: int,
    max_score: int,
) -> None:
    await session.execute(
        text(
            "INSERT INTO f1.analysis_report_health_snapshot ("
            "id, enterprise_id, report_id, version_id, client_account_id, "
            "payload, payload_sha256, score, max_score"
            ") VALUES ("
            ":id, :enterprise_id, :report_id, :version_id, :client_account_id, "
            "CAST(:payload AS jsonb), :payload_sha256, :score, :max_score"
            ")"
        ),
        {
            "id": uuid.uuid4(),
            "enterprise_id": enterprise_id,
            "report_id": report_id,
            "version_id": version_id,
            "client_account_id": client_account_id,
            "payload": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            "payload_sha256": payload_sha256,
            "score": score,
            "max_score": max_score,
        },
    )


async def get_health_snapshot(
    session: AsyncSession, version_id: uuid.UUID
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "SELECT snapshot.payload, snapshot.payload_sha256, snapshot.score, "
                "snapshot.max_score, snapshot.report_id, snapshot.version_id, "
                "version.version_number, version.published_at "
                "FROM f1.analysis_report_health_snapshot AS snapshot "
                "JOIN f1.analysis_report_version AS version "
                "  ON version.enterprise_id = snapshot.enterprise_id "
                " AND version.id = snapshot.version_id "
                "WHERE snapshot.version_id = :version_id"
            ),
            {"version_id": version_id},
        )
    ).mappings().first()
    return dict(row) if row else None
