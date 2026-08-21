"""Analysis-report application service. Fail-closed generation."""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Any

from ...auth import Tenant
from ...database import session_scope
from . import repository
from .contracts import (
    CLIENT_CAPABILITIES,
    ENGINEERING_FLAG,
    FORBIDDEN_RESPONSE_KEYS,
    FrozenSourceSet,
    GenerationDisabled,
    GenerationFailed,
    LOCAL_FLAG,
    PROVIDER_CAPABILITIES,
    PROVIDER_MEMBER_ROLES,
    ProductRole,
    ReportNotFound,
    ReportTransitionInvalid,
    RequestIdConflict,
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
from .generator import FakeDeterministicReportGenerator
from .repository import RequestConflict

_TRANSITIONS = {
    "submit": ("draft", "review_pending"),
    "return": ("review_pending", "changes_requested"),
    "approve": ("review_pending", "approved"),
    "publish": ("approved", "published"),
    "withdraw": ("published", "withdrawn"),
}
_BINDING_REQUIRED_ACTIONS = frozenset({"submit", "return", "approve", "publish"})


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


async def generate_report(
    tenant: Tenant,
    client_account_id: uuid.UUID,
    report_id: uuid.UUID,
    request_id: uuid.UUID,
) -> dict[str, Any]:
    _require_provider(tenant)
    if not generation_enabled():
        raise GenerationDisabled()
    generator = FakeDeterministicReportGenerator()
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
            if (
                existing_job["source_fingerprint_sha256"] != frozen.fingerprint_sha256
                or existing_job["report_id"] != report_id
            ):
                raise RequestIdConflict()
            return {
                "schema": SCHEMA_GENERATION,
                "job_id": str(existing_job["id"]),
                "version_id": str(existing_job["version_id"]),
                "status": existing_job["status"],
            }
        live = await repository.load_eligible_sources(
            session, tenant.enterprise_id, client_account_id
        )
        live_fp = repository.fingerprint_for(
            tenant.enterprise_id, client_account_id, live
        )
        if live_fp != frozen.fingerprint_sha256:
            raise ReportNotFound()
        lease_owner = "ar." + uuid.uuid5(
            uuid.UUID("7c2e1a90-9f3d-4c1b-8a6e-0123456789ab"), tenant.sub
        ).hex
        started = await repository.begin_generation(
            session,
            report=report,
            actor_id=actor_id,
            request_id=request_id,
            frozen=frozen,
            lease_owner=lease_owner,
        )
        claimed = await repository.claim_live_lease(
            session,
            enterprise_id=tenant.enterprise_id,
            job_id=started["job_id"],
            lease_token=started["lease_token"],
            lease_owner=lease_owner,
        )
        if not claimed:
            await session.rollback()
            raise ReportNotFound()
        try:
            generated = generator.generate(frozen)
        except GenerationFailed as exc:
            await repository.fail_generation(
                session,
                enterprise_id=tenant.enterprise_id,
                job_id=started["job_id"],
                version_id=started["version_id"],
                reason=exc.reason,
            )
            await session.commit()
            return {
                "schema": SCHEMA_GENERATION,
                "job_id": str(started["job_id"]),
                "version_id": str(started["version_id"]),
                "status": "failed",
            }
        written = await repository.persist_generated(
            session,
            enterprise_id=tenant.enterprise_id,
            job_id=started["job_id"],
            version_id=started["version_id"],
            lease_token=started["lease_token"],
            generated=generated,
        )
        if not written:
            await repository.fail_generation(
                session,
                enterprise_id=tenant.enterprise_id,
                job_id=started["job_id"],
                version_id=started["version_id"],
                reason="REPORT_LEASE_STALE",
            )
            await session.commit()
            return {
                "schema": SCHEMA_GENERATION,
                "job_id": str(started["job_id"]),
                "version_id": str(started["version_id"]),
                "status": "failed",
            }
        await session.commit()
        status = "draft"
    payload = {
        "schema": SCHEMA_GENERATION,
        "job_id": str(started["job_id"]),
        "version_id": str(started["version_id"]),
        "status": status,
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
    }
    _forbid_leaks(detail)
    return detail


async def apply_transition(
    tenant: Tenant, version_id: uuid.UUID, action: str
) -> dict[str, Any]:
    _require_provider(tenant)
    if action not in _TRANSITIONS:
        raise ReportTransitionInvalid()
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
        if action in _BINDING_REQUIRED_ACTIONS:
            report_row = await repository.get_report(
                session, tenant.enterprise_id, version["report_id"]
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
        report = await repository.get_report(
            session, tenant.enterprise_id, version["report_id"]
        )
        await session.commit()
    if report is None:
        raise ReportNotFound()
    return _summary(report)


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
