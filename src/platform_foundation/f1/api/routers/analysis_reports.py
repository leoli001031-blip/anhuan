"""Analysis-report HTTP surface. Client identity is session-derived only."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from ...auth import Tenant, tenant_from_header
from ...features.analysis_reports import (
    GenerationDisabled,
    HealthSnapshotUnavailable,
    ReportNotFound,
    ReportTransitionInvalid,
    RequestIdConflict,
    create_report,
    generate_report,
    get_published,
    job_status,
    latest_health,
    list_client_reports,
    list_published,
    published_artifact,
    published_artifact_pdf,
    session_access,
    apply_transition,
    version_artifact,
    version_artifact_pdf,
    version_detail,
    version_history,
)
from ...features.analysis_reports.contracts import FORBIDDEN_CLIENT_IDENTITY_KEYS

router = APIRouter()
session_router = APIRouter()


class CreateReportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: uuid.UUID


class ReviewChecklistBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citation_traceable: bool
    risks_complete: bool
    usage_boundary: bool


class TransitionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checklist: ReviewChecklistBody | None = None
    comment: str | None = Field(default=None, max_length=2_000)


def _transition_kwargs(body: TransitionBody | None) -> dict[str, object]:
    if body is None:
        return {"checklist": None, "comment": None}
    return {
        "checklist": body.checklist.model_dump() if body.checklist else None,
        "comment": body.comment,
    }


def _client_identity_rejected(request: Request) -> None:
    names = set(request.query_params.keys())
    if names & FORBIDDEN_CLIENT_IDENTITY_KEYS:
        raise HTTPException(status_code=404, detail="REPORT_NOT_FOUND")


def _map_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ReportNotFound):
        return HTTPException(status_code=404, detail="REPORT_NOT_FOUND")
    if isinstance(exc, RequestIdConflict):
        return HTTPException(status_code=409, detail="REQUEST_ID_CONFLICT")
    if isinstance(exc, ReportTransitionInvalid):
        return HTTPException(status_code=409, detail="REPORT_TRANSITION_INVALID")
    if isinstance(exc, GenerationDisabled):
        return HTTPException(status_code=404, detail="ANALYSIS_REPORT_GENERATION_DISABLED")
    if isinstance(exc, HealthSnapshotUnavailable):
        return HTTPException(status_code=503, detail="HEALTH_SNAPSHOT_UNAVAILABLE")
    raise exc


def _artifact_response(artifact: object) -> Response:
    body = getattr(artifact, "body", None)
    filename = getattr(artifact, "filename", None)
    digest = getattr(artifact, "sha256", None)
    if (
        not isinstance(body, bytes)
        or not isinstance(filename, str)
        or filename.endswith("/") is False
        and filename.rsplit(".", 1)[-1] not in {"html", "pdf"}
        or not isinstance(digest, str)
        or len(digest) != 64
    ):
        raise HTTPException(status_code=404, detail="REPORT_NOT_FOUND")
    suffix = filename.rsplit(".", 1)[-1]
    headers = {
        "Cache-Control": "private, no-store",
        "Content-Disposition": f'attachment; filename="{filename}"',
        "ETag": f'"{digest}"',
        "X-Content-Type-Options": "nosniff",
    }
    if suffix == "pdf":
        if not body.startswith(b"%PDF-"):
            raise HTTPException(status_code=404, detail="REPORT_NOT_FOUND")
        return Response(
            content=body,
            media_type="application/pdf",
            headers=headers,
        )
    return Response(
        content=body,
        media_type="text/html; charset=utf-8",
        headers={
            **headers,
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
        },
    )


@session_router.get("/session/access")
async def get_session_access(
    request: Request,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict:
    _client_identity_rejected(request)
    return session_access(tenant)


@router.get("/published")
async def client_list_published(
    request: Request,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict:
    _client_identity_rejected(request)
    try:
        return await list_published(tenant)
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from None


@router.get("/published/{report_id}")
async def client_get_published(
    report_id: uuid.UUID,
    request: Request,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict:
    _client_identity_rejected(request)
    try:
        return await get_published(tenant, report_id)
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from None


@router.get("/published/{report_id}/artifact.pdf")
async def client_get_published_artifact_pdf(
    report_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> Response:
    try:
        return _artifact_response(await published_artifact_pdf(tenant, report_id))
    except Exception as extra:  # noqa: BLE001
        raise _map_error(extra) from None


@router.get("/published/{report_id}/artifact.html")
async def client_get_published_artifact(
    report_id: uuid.UUID,
    request: Request,
    tenant: Tenant = Depends(tenant_from_header),
) -> Response:
    _client_identity_rejected(request)
    try:
        return _artifact_response(await published_artifact(tenant, report_id))
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from None


@router.get("/health/latest")
async def client_latest_health(
    request: Request,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict:
    _client_identity_rejected(request)
    try:
        return await latest_health(tenant)
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from None


@router.get("/clients/{client_account_id}/reports")
async def provider_list_reports(
    client_account_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict:
    try:
        return await list_client_reports(tenant, client_account_id)
    except Exception as exc:  # noqa: BLE001
        raise _map_error(exc) from None


@router.post("/clients/{client_account_id}/reports")
async def provider_create_report(
    client_account_id: uuid.UUID,
    body: CreateReportBody,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict:
    try:
        return await create_report(tenant, client_account_id, body.request_id)
    except Exception as extra:  # noqa: BLE001
        raise _map_error(extra) from None


@router.post("/clients/{client_account_id}/reports/{report_id}/generations")
async def provider_generate(
    client_account_id: uuid.UUID,
    report_id: uuid.UUID,
    body: CreateReportBody,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict:
    try:
        return await generate_report(
            tenant, client_account_id, report_id, body.request_id
        )
    except Exception as extra:  # noqa: BLE001
        raise _map_error(extra) from None


@router.get("/jobs/{job_id}")
async def provider_job(
    job_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict:
    try:
        return await job_status(tenant, job_id)
    except Exception as extra:  # noqa: BLE001
        raise _map_error(extra) from None


@router.get("/versions/{version_id}")
async def provider_version(
    version_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict:
    try:
        return await version_detail(tenant, version_id)
    except Exception as extra:  # noqa: BLE001
        raise _map_error(extra) from None


@router.get("/versions/{version_id}/artifact.pdf")
async def provider_version_artifact_pdf(
    version_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> Response:
    try:
        return _artifact_response(await version_artifact_pdf(tenant, version_id))
    except Exception as extra:  # noqa: BLE001
        raise _map_error(extra) from None


@router.get("/versions/{version_id}/artifact.html")
async def provider_version_artifact(
    version_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> Response:
    try:
        return _artifact_response(await version_artifact(tenant, version_id))
    except Exception as extra:  # noqa: BLE001
        raise _map_error(extra) from None


@router.post("/versions/{version_id}/submit")
async def provider_submit(
    version_id: uuid.UUID,
    body: TransitionBody | None = None,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict:
    try:
        return await apply_transition(
            tenant, version_id, "submit", **_transition_kwargs(body)
        )
    except Exception as extra:  # noqa: BLE001
        raise _map_error(extra) from None


@router.post("/versions/{version_id}/return")
async def provider_return(
    version_id: uuid.UUID,
    body: TransitionBody | None = None,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict:
    try:
        return await apply_transition(
            tenant, version_id, "return", **_transition_kwargs(body)
        )
    except Exception as extra:  # noqa: BLE001
        raise _map_error(extra) from None


@router.post("/versions/{version_id}/approve")
async def provider_approve(
    version_id: uuid.UUID,
    body: TransitionBody | None = None,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict:
    try:
        return await apply_transition(
            tenant, version_id, "approve", **_transition_kwargs(body)
        )
    except Exception as extra:  # noqa: BLE001
        raise _map_error(extra) from None


@router.post("/versions/{version_id}/publish")
async def provider_publish(
    version_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict:
    try:
        return await apply_transition(tenant, version_id, "publish")
    except Exception as extra:  # noqa: BLE001
        raise _map_error(extra) from None


@router.post("/versions/{version_id}/withdraw")
async def provider_withdraw(
    version_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict:
    try:
        return await apply_transition(tenant, version_id, "withdraw")
    except Exception as extra:  # noqa: BLE001
        raise _map_error(extra) from None


@router.get("/{report_id}/versions")
async def provider_history(
    report_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> dict:
    try:
        return await version_history(tenant, report_id)
    except Exception as extra:  # noqa: BLE001
        raise _map_error(extra) from None
