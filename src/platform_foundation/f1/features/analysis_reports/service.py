"""Analysis-report application service. Fail-closed generation."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from ...auth import Tenant
from ...database import session_scope
from . import health, repository
from .artifact import ReportArtifact, ReportArtifactInvalid, render_html_artifact
from .contracts import (
    CLIENT_CAPABILITIES,
    ENGINEERING_FLAG,
    FORBIDDEN_RESPONSE_KEYS,
    FrozenSourceSet,
    GenerationDisabled,
    HealthScoreContext,
    HealthSnapshotUnavailable,
    LOCAL_FLAG,
    PROVIDER_CAPABILITIES,
    PROVIDER_MEMBER_ROLES,
    RECOVERABLE_GENERATION_FAILURE_REASONS,
    ProductRole,
    ReportNotFound,
    ReportTransitionInvalid,
    RequestIdConflict,
    REVIEW_CHECKLIST_KEYS,
    SCHEMA_DRAFT,
    SCHEMA_GENERATION,
    SCHEMA_HISTORY,
    SCHEMA_JOB,
    SCHEMA_PROVIDER_LIST,
    SCHEMA_PUBLISHED_DETAIL,
    SCHEMA_PUBLISHED_LIST,
    SCHEMA_SESSION,
    TEMPLATE_ID,
    TEMPLATE_TITLE,
)
from .repository import RequestConflict

_TRANSITIONS = {
    "submit": ("draft", "review_pending"),
    "return": ("review_pending", "changes_requested"),
    "approve": ("review_pending", "approved"),
    "publish": ("approved", "published"),
    "withdraw": ("published", "withdrawn"),
}
_BINDING_REQUIRED_ACTIONS = frozenset({"submit", "return", "approve", "publish"})
_REVIEW_ACTIONS = frozenset({"submit", "return", "approve"})
_GENERATION_START_STATUSES = frozenset(
    {None, "changes_requested", "superseded", "withdrawn"}
)
_FINGERPRINT_FORK_STATUSES = frozenset(
    {"review_pending", "approved", "published", "failed"}
)


def generation_enabled() -> bool:
    return os.environ.get(LOCAL_FLAG) == "1" and os.environ.get(ENGINEERING_FLAG) == "1"


def product_role_for(tenant: Tenant) -> ProductRole:
    membership = tenant.role or ""
    if membership in PROVIDER_MEMBER_ROLES:
        return "provider_admin"
    return "client_user"


def session_access(tenant: Tenant) -> dict[str, Any]:
    role = product_role_for(tenant)
    payload = {
        "schema": SCHEMA_SESSION,
        "product_role": role,
        "enterprise_id": str(tenant.enterprise_id),
        "template_id": TEMPLATE_ID,
        "template_title": TEMPLATE_TITLE,
        "capabilities": list(
            PROVIDER_CAPABILITIES if role == "provider_admin" else CLIENT_CAPABILITIES
        ),
    }
    _forbid_leaks(payload)
    return payload


def _forbid_leaks(payload: Any) -> None:
    if isinstance(payload, dict):
        if FORBIDDEN_RESPONSE_KEYS.intersection(payload):
            raise RuntimeError("ANALYSIS_REPORT_CONTRACT_LEAK")
        for value in payload.values():
            _forbid_leaks(value)
    elif isinstance(payload, list):
        for item in payload:
            _forbid_leaks(item)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _review_evidence(
    action: str,
    checklist: dict[str, bool] | None,
    comment: str | None,
) -> tuple[dict[str, bool], str | None]:
    normalized_comment = comment.strip() if isinstance(comment, str) else None
    if normalized_comment == "":
        normalized_comment = None
    if normalized_comment is not None and len(normalized_comment) > 2_000:
        raise ReportTransitionInvalid()
    normalized = {} if checklist is None else dict(checklist)
    if any(
        key not in REVIEW_CHECKLIST_KEYS or type(value) is not bool
        for key, value in normalized.items()
    ):
        raise ReportTransitionInvalid()
    if action == "submit":
        if normalized or normalized_comment is not None:
            raise ReportTransitionInvalid()
        return {}, None
    if action == "return":
        if normalized or normalized_comment is None:
            raise ReportTransitionInvalid()
        return {}, normalized_comment
    if action == "approve":
        if set(normalized) != set(REVIEW_CHECKLIST_KEYS) or not all(
            normalized.values()
        ):
            raise ReportTransitionInvalid()
        return normalized, normalized_comment
    if normalized or normalized_comment is not None:
        raise ReportTransitionInvalid()
    return {}, None


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    status = row.get("current_status") or "empty"
    payload = {
        "report_id": str(row["id"]),
        "current_version_id": str(row["current_version_id"])
        if row.get("current_version_id")
        else None,
        "current_status": status,
        "version_number": int(row["current_version_no"] or 0),
        "title": TEMPLATE_TITLE,
        "updated_at": _iso(row["updated_at"]),
    }
    _forbid_leaks(payload)
    return payload


def _require_provider(tenant: Tenant) -> None:
    if product_role_for(tenant) != "provider_admin":
        raise ReportNotFound()


async def _require_owned_client(
    session, tenant: Tenant, client_account_id: uuid.UUID
) -> None:
    if not await repository.crm_account_owned(
        session, tenant.enterprise_id, client_account_id
    ):
        raise ReportNotFound()


async def _require_owned_bound_client(
    session, tenant: Tenant, client_account_id: uuid.UUID
) -> None:
    await _require_owned_client(session, tenant, client_account_id)
    if not await repository.active_binding_for_provider(
        session, tenant.enterprise_id, client_account_id
    ):
        raise ReportNotFound()


def _require_client(tenant: Tenant) -> None:
    if product_role_for(tenant) != "client_user":
        raise ReportNotFound()


async def list_published(tenant: Tenant) -> dict[str, Any]:
    _require_client(tenant)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        if not await repository.resolve_active_bindings_for_audience(
            session, tenant.enterprise_id
        ):
            payload = {"schema": SCHEMA_PUBLISHED_LIST, "reports": []}
            _forbid_leaks(payload)
            return payload
        rows = await repository.list_published_for_client(session, tenant.enterprise_id)
    reports = [
        {
            "report_id": str(row["report_id"]),
            "version_id": str(row["version_id"]),
            "version_number": int(row["version_number"]),
            "title": TEMPLATE_TITLE,
            "published_at": _iso(row["published_at"]),
            "artifact_ready": True,
        }
        for row in rows
    ]
    payload = {"schema": SCHEMA_PUBLISHED_LIST, "reports": reports}
    _forbid_leaks(payload)
    return payload


async def get_published(tenant: Tenant, report_id: uuid.UUID) -> dict[str, Any]:
    _require_client(tenant)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        if not await repository.resolve_active_bindings_for_audience(
            session, tenant.enterprise_id
        ):
            raise ReportNotFound()
        row = await repository.get_published_detail(
            session, tenant.enterprise_id, report_id
        )
    if row is None:
        raise ReportNotFound()
    payload = {
        "schema": SCHEMA_PUBLISHED_DETAIL,
        "report_id": str(row["report_id"]),
        "version_id": str(row["version_id"]),
        "version_number": int(row["version_number"]),
        "title": TEMPLATE_TITLE,
        "published_at": _iso(row["published_at"]),
        "artifact_ready": True,
        "sections": [
            {"key": item["section_key"], "title": item["title"], "body": item["body"]}
            for item in row["sections"]
        ],
        "citations": [
            {
                "citation_id": str(item["id"]),
                "document_version_id": str(item["document_version_id"]),
                "document_name": item["document_name"],
                "version_number": int(item["version_number"]),
                "page_number": int(item["page_number"]),
                "excerpt": item["excerpt"],
            }
            for item in row["citations"]
        ],
    }
    _forbid_leaks(payload)
    return payload


async def published_artifact(
    tenant: Tenant, report_id: uuid.UUID
) -> ReportArtifact:
    """Render the exact published version visible to the client session."""
    try:
        return render_html_artifact(await get_published(tenant, report_id))
    except ReportArtifactInvalid:
        raise ReportNotFound() from None


async def list_client_reports(tenant: Tenant, client_account_id: uuid.UUID) -> dict[str, Any]:
    _require_provider(tenant)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await _require_owned_client(session, tenant, client_account_id)
        rows = await repository.list_provider_reports(
            session, tenant.enterprise_id, client_account_id
        )
    payload = {
        "schema": SCHEMA_PROVIDER_LIST,
        "reports": [_summary(row) for row in rows],
    }
    _forbid_leaks(payload)
    return payload


async def create_report(
    tenant: Tenant, client_account_id: uuid.UUID, request_id: uuid.UUID
) -> dict[str, Any]:
    _require_provider(tenant)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await _require_owned_bound_client(session, tenant, client_account_id)
        actor_id = await repository.actor_user_id(
            session, tenant.enterprise_id, tenant.sub
        )
        if actor_id is None:
            raise ReportNotFound()
        existing = await repository.get_report_by_create_request(
            session, tenant.enterprise_id, request_id
        )
        if existing is not None:
            if existing["client_account_id"] != client_account_id:
                raise RequestIdConflict()
            row = await repository.get_report(
                session, tenant.enterprise_id, existing["id"]
            )
            return _summary(row or existing)
        try:
            row = await repository.insert_report(
                session,
                enterprise_id=tenant.enterprise_id,
                client_account_id=client_account_id,
                request_id=request_id,
                actor_id=actor_id,
            )
        except RequestConflict:
            raise RequestIdConflict() from None
        await session.commit()
    return _summary(row)


def _freeze(enterprise_id: uuid.UUID, client_account_id: uuid.UUID, sources) -> FrozenSourceSet:
    kinds = {item.scope_kind for item in sources}
    if "client" not in kinds:
        raise ReportNotFound()
    if "service_provider" not in kinds:
        raise ReportNotFound()
    fingerprint = repository.fingerprint_for(enterprise_id, client_account_id, sources)
    return FrozenSourceSet(
        enterprise_id=enterprise_id,
        client_account_id=client_account_id,
        template_id=TEMPLATE_ID,
        fingerprint_sha256=fingerprint,
        sources=tuple(sorted(sources, key=lambda item: str(item.document_version_id))),
    )


async def _resume_generation(
    session: Any,
    *,
    tenant: Tenant,
    report_id: uuid.UUID,
    actor_id: uuid.UUID,
    frozen: FrozenSourceSet,
    existing_job: dict[str, Any],
    audit_action: str,
) -> dict[str, Any]:
    if existing_job["report_id"] != report_id:
        raise RequestIdConflict()
    rebound = False
    if existing_job["status"] in {"queued", "generating"}:
        # The fixed migration marker must be recoverable even when source
        # material changed while no valid actor existed.  The worker will
        # apply the frozen fingerprint fence after this exact rebind; ordinary
        # active deliveries still return False and retain conflict semantics.
        rebound = await repository.rebind_historical_delivery_in_session(
            session,
            enterprise_id=tenant.enterprise_id,
            job_id=existing_job["id"],
            actor_sub=tenant.sub,
        )
        if rebound:
            await session.commit()
    if (
        existing_job["source_fingerprint_sha256"]
        != frozen.fingerprint_sha256
        and not rebound
    ):
        raise RequestIdConflict()
    payload = {
        "schema": SCHEMA_GENERATION,
        "job_id": str(existing_job["id"]),
        "version_id": str(existing_job["version_id"]),
        "status": existing_job["status"],
    }
    failure_reason = existing_job.get("error_reason")
    if (
        existing_job["status"] == "failed"
        and failure_reason in RECOVERABLE_GENERATION_FAILURE_REASONS
    ):
        # A fixed dispatch failure is reset only through this audited exact-job
        # branch; deterministic evidence failures never enter it.
        restored = await repository.requeue_failed_generation(
            session,
            enterprise_id=tenant.enterprise_id,
            report_id=report_id,
            job_id=existing_job["id"],
            version_id=existing_job["version_id"],
            actor_id=actor_id,
            actor_sub=tenant.sub,
            reason=str(failure_reason),
            audit_action=audit_action,
        )
        if restored:
            await session.commit()
            payload["status"] = "queued"
        else:
            # A concurrent exact replay may have won the row lock and committed
            # the audited reset.  Any other state remains fail-closed.
            current = await repository.get_job(
                session, tenant.enterprise_id, existing_job["id"]
            )
            if current is not None and current["status"] in {
                "queued",
                "generating",
            }:
                payload["status"] = current["status"]
    # PostgreSQL delivery is registered/re-armed in the same transaction as
    # the report job.  No browser request is the dispatch recovery authority.
    return payload


async def generate_report(
    tenant: Tenant,
    client_account_id: uuid.UUID,
    report_id: uuid.UUID,
    request_id: uuid.UUID,
) -> dict[str, Any]:
    _require_provider(tenant)
    if not generation_enabled():
        raise GenerationDisabled()
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await _require_owned_bound_client(session, tenant, client_account_id)
        report = await repository.get_report(
            session, tenant.enterprise_id, report_id
        )
        if report is None or report["client_account_id"] != client_account_id:
            raise ReportNotFound()
        actor_id = await repository.actor_user_id(
            session, tenant.enterprise_id, tenant.sub
        )
        if actor_id is None:
            raise ReportNotFound()
        existing_job = await repository.get_job_by_request(
            session, tenant.enterprise_id, request_id
        )
        sources = await repository.load_eligible_sources(
            session, tenant.enterprise_id, client_account_id
        )
        frozen = _freeze(tenant.enterprise_id, client_account_id, sources)
        if existing_job is not None:
            payload = await _resume_generation(
                session,
                tenant=tenant,
                report_id=report_id,
                actor_id=actor_id,
                frozen=frozen,
                existing_job=existing_job,
                audit_action="redispatch",
            )
        else:
            live = await repository.load_eligible_sources(
                session, tenant.enterprise_id, client_account_id
            )
            live_fp = repository.fingerprint_for(
                tenant.enterprise_id, client_account_id, live
            )
            if live_fp != frozen.fingerprint_sha256:
                raise ReportNotFound()
            locked_report = await repository.lock_report_for_generation(
                session, tenant.enterprise_id, report_id
            )
            if (
                locked_report is None
                or locked_report["client_account_id"] != client_account_id
            ):
                raise ReportNotFound()
            # Recheck after taking the report lock so concurrent exact requests
            # preserve idempotency and different request ids cannot duplicate a
            # deterministic failure for the same source fingerprint.
            concurrent_job = await repository.get_job_by_request(
                session, tenant.enterprise_id, request_id
            )
            if concurrent_job is not None:
                payload = await _resume_generation(
                    session,
                    tenant=tenant,
                    report_id=report_id,
                    actor_id=actor_id,
                    frozen=frozen,
                    existing_job=concurrent_job,
                    audit_action="redispatch",
                )
            else:
                current_version_job = await repository.get_job_for_version(
                    session,
                    tenant.enterprise_id,
                    report_id,
                    locked_report["current_version_id"],
                )
                if current_version_job is not None and current_version_job[
                    "status"
                ] in {"queued", "generating"}:
                    # Find a migration-blocked current job independently of its
                    # old source fingerprint.  Only the DB fixed marker can
                    # bypass the mismatch in _resume_generation.
                    same_source_job = current_version_job
                else:
                    same_source_job = await repository.get_job_by_fingerprint(
                        session,
                        tenant.enterprise_id,
                        report_id,
                        frozen.fingerprint_sha256,
                    )
                active_same_source = bool(
                    same_source_job is not None
                    and same_source_job["status"] in {"queued", "generating"}
                    and same_source_job["version_id"]
                    == locked_report.get("current_version_id")
                )
                if active_same_source:
                    # A different request id must not fork an already-active
                    # exact source job.  This also provides the audited entry
                    # point for a migration-blocked actor rebind.
                    payload = await _resume_generation(
                        session,
                        tenant=tenant,
                        report_id=report_id,
                        actor_id=actor_id,
                        frozen=frozen,
                        existing_job=same_source_job,
                        audit_action="redispatch",
                    )
                else:
                    recoverable_takeover = bool(
                        same_source_job is not None
                        and same_source_job["status"] == "failed"
                        and same_source_job.get("error_reason")
                        in RECOVERABLE_GENERATION_FAILURE_REASONS
                        and same_source_job["version_id"]
                        == locked_report.get("current_version_id")
                        and locked_report.get("current_status") == "failed"
                    )
                    if (
                        same_source_job is not None
                        and same_source_job["status"] == "failed"
                        and not recoverable_takeover
                    ):
                        # A new request may replace an availability/authority
                        # failure, but can never bypass a deterministic evidence
                        # or fingerprint failure for the same frozen source set.
                        raise RequestIdConflict()
                    current_status = locked_report["current_status"]
                    forked_from_review = (
                        current_status in _FINGERPRINT_FORK_STATUSES
                        and locked_report.get("current_source_fingerprint")
                        != frozen.fingerprint_sha256
                    )
                    if (
                        current_status not in _GENERATION_START_STATUSES
                        and not forked_from_review
                        and not recoverable_takeover
                    ):
                        raise ReportTransitionInvalid()
                    started = await repository.begin_generation(
                        session,
                        report=locked_report,
                        actor_id=actor_id,
                        actor_sub=tenant.sub,
                        request_id=request_id,
                        frozen=frozen,
                    )
                    await session.commit()
                    payload = {
                        "schema": SCHEMA_GENERATION,
                        "job_id": str(started["job_id"]),
                        "version_id": str(started["version_id"]),
                        "status": "queued",
                    }
    _forbid_leaks(payload)
    return payload


async def job_status(tenant: Tenant, job_id: uuid.UUID) -> dict[str, Any]:
    _require_provider(tenant)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        row = await repository.get_job(session, tenant.enterprise_id, job_id)
    if row is None:
        raise ReportNotFound()
    payload = {
        "schema": SCHEMA_JOB,
        "job_id": str(row["id"]),
        "version_id": str(row["version_id"]),
        "status": row["status"],
        "error_reason": row["error_reason"],
    }
    _forbid_leaks(payload)
    return payload


async def version_detail(tenant: Tenant, version_id: uuid.UUID) -> dict[str, Any]:
    _require_provider(tenant)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        row = await repository.get_version(session, tenant.enterprise_id, version_id)
        if row is None:
            raise ReportNotFound()
        payload = await repository.attach_sections(session, dict(row), version_id)
        review_events = await repository.list_review_events(
            session,
            enterprise_id=tenant.enterprise_id,
            version_id=version_id,
        )
    detail = {
        "schema": SCHEMA_DRAFT,
        "report_id": str(payload["report_id"]),
        "version_id": str(payload["id"]),
        "version_number": int(payload["version_number"]),
        "status": payload["status"],
        "title": TEMPLATE_TITLE,
        "sections": [
            {"key": item["section_key"], "title": item["title"], "body": item["body"]}
            for item in payload.get("sections", [])
        ],
        "citations": [
            {
                "citation_id": str(item["id"]),
                "document_version_id": str(item["document_version_id"]),
                "document_name": item["document_name"],
                "version_number": int(item["version_number"]),
                "page_number": int(item["page_number"]),
                "excerpt": item["excerpt"],
            }
            for item in payload.get("citations", [])
        ],
        "review_events": [
            {
                "event_id": str(item["id"]),
                "action": item["action"],
                "checklist": item["checklist"],
                "comment": item["comment"],
                "created_at": _iso(item["created_at"]),
            }
            for item in review_events
        ],
    }
    _forbid_leaks(detail)
    return detail


async def version_artifact(
    tenant: Tenant, version_id: uuid.UUID
) -> ReportArtifact:
    """Render a provider-visible generated version as a downloadable file."""
    try:
        return render_html_artifact(await version_detail(tenant, version_id))
    except ReportArtifactInvalid:
        raise ReportNotFound() from None


async def apply_transition(
    tenant: Tenant,
    version_id: uuid.UUID,
    action: str,
    *,
    checklist: dict[str, bool] | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    _require_provider(tenant)
    if action not in _TRANSITIONS:
        raise ReportTransitionInvalid()
    review_checklist, review_comment = _review_evidence(
        action, checklist, comment
    )
    from_status, to_status = _TRANSITIONS[action]
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await repository.actor_user_id(
            session, tenant.enterprise_id, tenant.sub
        )
        if actor_id is None:
            raise ReportNotFound()
        version = await repository.get_version(
            session, tenant.enterprise_id, version_id
        )
        if version is None:
            raise ReportNotFound()
        locked_report = None
        if action != "withdraw":
            # Serialize review/publish against a concurrent generation fork.
            # The earlier unlocked version read is only descriptive; this
            # report-row lock is the authority for the current-version fence.
            locked_report = await repository.lock_report_for_generation(
                session,
                tenant.enterprise_id,
                version["report_id"],
            )
        if (
            action != "withdraw"
            and (
                locked_report is None
                or locked_report.get("current_version_id") != version_id
            )
        ):
            # Once a changed fingerprint forks a new internal work version, an
            # older review/approval may not advance or replace the current line.
            raise ReportTransitionInvalid()
        if action == "publish":
            complete = await repository.attach_sections(
                session, dict(version), version_id
            )
            try:
                render_html_artifact(
                    {
                        "version_number": int(complete["version_number"]),
                        "sections": [
                            {
                                "key": item["section_key"],
                                "title": item["title"],
                                "body": item["body"],
                            }
                            for item in complete.get("sections", [])
                        ],
                        "citations": [
                            {
                                "document_version_id": str(
                                    item["document_version_id"]
                                ),
                                "document_name": item["document_name"],
                                "version_number": int(item["version_number"]),
                                "page_number": int(item["page_number"]),
                                "excerpt": item["excerpt"],
                            }
                            for item in complete.get("citations", [])
                        ],
                    }
                )
            except (KeyError, TypeError, ValueError, ReportArtifactInvalid):
                raise ReportTransitionInvalid() from None
        if action in _BINDING_REQUIRED_ACTIONS:
            report_row = locked_report or await repository.get_report(
                session,
                tenant.enterprise_id,
                version["report_id"],
            )
            if report_row is None:
                raise ReportNotFound()
            await _require_owned_bound_client(
                session, tenant, report_row["client_account_id"]
            )
        ok = await repository.transition_version(
            session,
            enterprise_id=tenant.enterprise_id,
            version_id=version_id,
            from_status=from_status,
            to_status=to_status,
            actor_id=actor_id,
            action=action,
            artifact_ready=True if action == "publish" else None,
            published_at=action == "publish",
        )
        if not ok:
            raise ReportTransitionInvalid()
        if action in _REVIEW_ACTIONS:
            await repository.insert_review_event(
                session,
                enterprise_id=tenant.enterprise_id,
                report_id=version["report_id"],
                version_id=version_id,
                actor_id=actor_id,
                action=action,
                checklist=review_checklist,
                comment=review_comment,
            )
        if action == "publish" and generation_enabled():
            published = await repository.get_version(
                session, tenant.enterprise_id, version_id
            )
            if published is None or published.get("published_at") is None:
                raise HealthSnapshotUnavailable()
            await _store_health_snapshot(
                session,
                enterprise_id=tenant.enterprise_id,
                version=published,
                actor_id=actor_id,
            )
        report = await repository.get_report(
            session, tenant.enterprise_id, version["report_id"]
        )
        await session.commit()
    if report is None:
        raise ReportNotFound()
    return _summary(report)


async def _store_health_snapshot(
    session: Any,
    *,
    enterprise_id: uuid.UUID,
    version: dict[str, Any],
    actor_id: uuid.UUID,
) -> None:
    context = HealthScoreContext(
        report_id=uuid.UUID(str(version["report_id"])),
        version_id=uuid.UUID(str(version["id"])),
        version_number=int(version["version_number"]),
        report_title=TEMPLATE_TITLE,
        assessed_on=version["published_at"],
    )
    snapshot = health.local_scorer().score(context)
    if snapshot is None:
        return
    snapshot = health.validate_snapshot(snapshot)
    digest = health.payload_sha256(snapshot)
    await repository.insert_health_snapshot(
        session,
        enterprise_id=enterprise_id,
        report_id=version["report_id"],
        version_id=version["id"],
        client_account_id=version["client_account_id"],
        payload=snapshot,
        payload_sha256=digest,
        score=int(snapshot["score"]),
        max_score=int(snapshot["max_score"]),
    )
    await repository.add_audit(
        session,
        enterprise_id=enterprise_id,
        report_id=version["report_id"],
        version_id=version["id"],
        actor_id=actor_id,
        action="health_snapshot_created",
        from_status="approved",
        to_status="published",
    )


async def latest_health(tenant: Tenant) -> dict[str, Any]:
    if product_role_for(tenant) != "client_user":
        raise ReportNotFound()
    if not generation_enabled():
        return health.empty_envelope()
    try:
        async with session_scope(
            role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
        ) as session:
            published = await repository.list_published_for_client(
                session, tenant.enterprise_id
            )
            if not published:
                return health.empty_envelope()
            latest = published[0]
            row = await repository.get_health_snapshot(
                session, latest["version_id"]
            )
            if row is None:
                return health.empty_envelope()
            if (
                str(row["report_id"]) != str(latest["report_id"])
                or str(row["version_id"]) != str(latest["version_id"])
                or int(row["version_number"]) != int(latest["version_number"])
            ):
                raise HealthSnapshotUnavailable()
            payload = row["payload"]
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError as extra:
                    raise HealthSnapshotUnavailable() from extra
            if not isinstance(payload, dict):
                raise HealthSnapshotUnavailable()
            snapshot = health.validate_snapshot(payload, from_storage=True)
            if (
                snapshot["report_id"] != str(latest["report_id"])
                or snapshot["version_id"] != str(latest["version_id"])
                or int(snapshot["version_number"]) != int(latest["version_number"])
                or snapshot["assessed_on"] != health.as_iso(row["published_at"])
            ):
                raise HealthSnapshotUnavailable()
            digest = health.payload_sha256(snapshot)
            if digest != row["payload_sha256"] or int(row["score"]) != int(
                snapshot["score"]
            ) or int(row["max_score"]) != int(snapshot["max_score"]):
                raise HealthSnapshotUnavailable()
            return health.http_envelope(snapshot)
    except HealthSnapshotUnavailable:
        raise
    except (SQLAlchemyError, TypeError, ValueError) as exc:
        raise HealthSnapshotUnavailable() from exc


async def version_history(tenant: Tenant, report_id: uuid.UUID) -> dict[str, Any]:
    _require_provider(tenant)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        report = await repository.get_report(session, tenant.enterprise_id, report_id)
        if report is None:
            raise ReportNotFound()
        rows = await repository.list_versions(
            session, tenant.enterprise_id, report_id
        )
    payload = {
        "schema": SCHEMA_HISTORY,
        "versions": [
            {
                "version_id": str(row["id"]),
                "version_number": int(row["version_number"]),
                "status": row["status"],
                "created_at": _iso(row["created_at"]),
            }
            for row in rows
        ],
    }
    _forbid_leaks(payload)
    return payload
