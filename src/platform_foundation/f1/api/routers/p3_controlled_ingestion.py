"""P3 controlled-ingestion HTTP API.

The main application owns router registration.  Mount this router at
``/api/v1/ingestion``; this module deliberately does not mutate the app.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
)

from ...auth import Tenant, tenant_from_header
from ...features.p3.contracts import (
    CapabilitiesOut,
    DocumentDetailOut,
    DocumentListOut,
    IngestionError,
    PreviewManifestOut,
    VersionOut,
    WorksheetGridOut,
    KnowledgeScopeUpdateIn,
    capabilities,
    idempotency_key_sha256,
    preflight_stream,
    public_reason_code,
    reason_is_retryable,
)
from ...features.p3.service import (
    act_on_version,
    complete_upload,
    get_document,
    get_preview_manifest,
    get_version,
    list_documents,
    read_preview_grid_unit,
    read_preview_content_unit,
    require_manager,
    reserve_initial_version,
    reserve_next_version,
    set_document_knowledge_scope,
)
from ...features.p3.processor import process_controlled_ingestion
from ...features.p3.scanner import ScanFailure, scanner_version
from ...features.material_intake.contracts import (
    MaterialAnalysisOut,
    SetMaterialKindIn,
)
from ...features.material_intake.service import (
    get_material_analysis,
    set_material_kind,
)


router = APIRouter()


def _http_error(error: IngestionError) -> HTTPException:
    code = public_reason_code(error.code) or "INGESTION_UNAVAILABLE"
    return HTTPException(
        status_code=error.http_status,
        detail={
            "code": code,
            "message": code,
            "retryable": reason_is_retryable(error.code),
        },
    )


def _unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "INGESTION_UNAVAILABLE",
            "message": "INGESTION_UNAVAILABLE",
            "retryable": True,
        },
    )


@router.get("/capabilities", response_model=CapabilitiesOut)
async def ingestion_capabilities(
    tenant: Tenant = Depends(tenant_from_header),
) -> CapabilitiesOut:
    # Membership resolution is intentional even though limits are static:
    # the product endpoint must not become an unauthenticated capability leak.
    if tenant.role not in {"super_admin", "enterprise_admin", "plant_admin"}:
        raise _http_error(IngestionError("P3_MANAGER_REQUIRED", http_status=403))
    checked_at = datetime.now(UTC)
    try:
        await asyncio.to_thread(scanner_version, timeout_seconds=2)
        scanner_state = "ready"
    except ScanFailure:
        scanner_state = "unavailable"
    return capabilities(
        scanner_state=scanner_state,
        scanner_checked_at=checked_at,
    )


@router.get("/documents", response_model=DocumentListOut)
async def document_library(
    status: Annotated[str | None, Query()] = None,
    content_type: Annotated[str | None, Query()] = None,
    scope_kind: Annotated[
        Literal["service_provider", "client"] | None, Query()
    ] = None,
    client_account_id: Annotated[uuid.UUID | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query()] = 20,
    tenant: Tenant = Depends(tenant_from_header),
) -> DocumentListOut:
    try:
        return await list_documents(
            tenant,
            status=status,
            content_type=content_type,
            scope_kind=scope_kind,
            client_account_id=client_account_id,
            cursor=cursor,
            limit=limit,
        )
    except IngestionError as error:
        raise _http_error(error) from None
    except Exception:
        raise _unavailable() from None


@router.get("/documents/{document_id}", response_model=DocumentDetailOut)
async def document_detail(
    document_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> DocumentDetailOut:
    try:
        return await get_document(tenant, document_id)
    except IngestionError as error:
        raise _http_error(error) from None
    except Exception:
        raise _unavailable() from None


@router.post("/documents", response_model=DocumentDetailOut, status_code=202)
async def create_document(
    display_name: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    plant_id: Annotated[uuid.UUID | None, Form()] = None,
    declared_material_kind: Annotated[
        Literal["policy", "report", "unknown"], Form()
    ] = "unknown",
    knowledge_scope_kind: Annotated[
        Literal["service_provider", "client"], Form()
    ] = "service_provider",
    client_account_id: Annotated[uuid.UUID | None, Form()] = None,
    tenant: Tenant = Depends(tenant_from_header),
) -> DocumentDetailOut:
    try:
        require_manager(tenant)
        key_sha256 = idempotency_key_sha256(idempotency_key)
        preflight = preflight_stream(
            file.file,
            filename=file.filename,
            content_type=file.content_type,
        )
        reservation = await reserve_initial_version(
            tenant,
            display_name=display_name,
            plant_id=plant_id,
            declared_material_kind=declared_material_kind,
            knowledge_scope_kind=knowledge_scope_kind,
            client_account_id=client_account_id,
            preflight=preflight,
            idempotency_key_sha256=key_sha256,
        )
        should_process = reservation.needs_quarantine_write or (
            reservation.processing_stage in {"received", "retry_wait"}
        )
        await complete_upload(tenant, reservation, file.file)
        if should_process:
            await process_controlled_ingestion(tenant, reservation.version_id)
        return await get_document(tenant, reservation.document_record_id)
    except IngestionError as error:
        raise _http_error(error) from None
    except Exception:
        raise _unavailable() from None


@router.post(
    "/documents/{document_id}/versions",
    response_model=VersionOut,
    status_code=202,
)
async def append_version(
    document_id: uuid.UUID,
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    tenant: Tenant = Depends(tenant_from_header),
) -> VersionOut:
    try:
        require_manager(tenant)
        key_sha256 = idempotency_key_sha256(idempotency_key)
        preflight = preflight_stream(
            file.file,
            filename=file.filename,
            content_type=file.content_type,
        )
        reservation = await reserve_next_version(
            tenant,
            record_id=document_id,
            preflight=preflight,
            idempotency_key_sha256=key_sha256,
        )
        should_process = reservation.needs_quarantine_write or (
            reservation.processing_stage in {"received", "retry_wait"}
        )
        await complete_upload(tenant, reservation, file.file)
        if should_process:
            await process_controlled_ingestion(tenant, reservation.version_id)
        return await get_version(tenant, reservation.version_id)
    except IngestionError as error:
        raise _http_error(error) from None
    except Exception:
        raise _unavailable() from None


@router.get("/versions/{version_id}", response_model=VersionOut)
async def version_detail(
    version_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> VersionOut:
    try:
        return await get_version(tenant, version_id)
    except IngestionError as error:
        raise _http_error(error) from None
    except Exception:
        raise _unavailable() from None


@router.get(
    "/versions/{version_id}/material-intake",
    response_model=MaterialAnalysisOut,
)
async def material_intake_analysis(
    version_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> MaterialAnalysisOut:
    try:
        return await get_material_analysis(tenant, version_id)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MATERIAL_INTAKE_UNAVAILABLE",
                "message": "MATERIAL_INTAKE_UNAVAILABLE",
                "retryable": True,
            },
        ) from None


@router.patch(
    "/material-analyses/{analysis_id}/classification",
    response_model=MaterialAnalysisOut,
)
async def classify_material_intake(
    analysis_id: uuid.UUID,
    body: SetMaterialKindIn,
    tenant: Tenant = Depends(tenant_from_header),
) -> MaterialAnalysisOut:
    try:
        return await set_material_kind(
            tenant, analysis_id, kind=body.material_kind
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MATERIAL_INTAKE_UNAVAILABLE",
                "message": "MATERIAL_INTAKE_UNAVAILABLE",
                "retryable": True,
            },
        ) from None


@router.patch(
    "/documents/{document_id}/knowledge-scope",
    response_model=DocumentDetailOut,
)
async def update_document_knowledge_scope(
    document_id: uuid.UUID,
    body: KnowledgeScopeUpdateIn,
    tenant: Tenant = Depends(tenant_from_header),
) -> DocumentDetailOut:
    try:
        return await set_document_knowledge_scope(
            tenant,
            document_id,
            kind=body.kind,
            client_account_id=body.client_account_id,
        )
    except IngestionError as error:
        raise _http_error(error) from None
    except Exception:
        raise _unavailable() from None


@router.post("/versions/{version_id}/retry", response_model=VersionOut)
async def retry_version(
    version_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> VersionOut:
    try:
        await act_on_version(tenant, version_id, action="retry")
        await process_controlled_ingestion(tenant, version_id)
        return await get_version(tenant, version_id)
    except IngestionError as error:
        raise _http_error(error) from None
    except Exception:
        raise _unavailable() from None


@router.post("/versions/{version_id}/process", response_model=VersionOut, status_code=202)
async def process_version(
    version_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> VersionOut:
    try:
        await process_controlled_ingestion(tenant, version_id)
        return await get_version(tenant, version_id)
    except IngestionError as error:
        raise _http_error(error) from None
    except Exception:
        raise _unavailable() from None


@router.post("/versions/{version_id}/release", response_model=VersionOut)
async def release_version(
    version_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> VersionOut:
    try:
        return await act_on_version(tenant, version_id, action="release")
    except IngestionError as error:
        raise _http_error(error) from None
    except Exception:
        raise _unavailable() from None


@router.post("/versions/{version_id}/reject", response_model=VersionOut)
async def reject_version(
    version_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> VersionOut:
    try:
        return await act_on_version(tenant, version_id, action="reject")
    except IngestionError as error:
        raise _http_error(error) from None
    except Exception:
        raise _unavailable() from None


@router.get(
    "/versions/{version_id}/preview",
    response_model=PreviewManifestOut,
)
async def preview_manifest(
    version_id: uuid.UUID,
    tenant: Tenant = Depends(tenant_from_header),
) -> PreviewManifestOut:
    try:
        return await get_preview_manifest(tenant, version_id)
    except IngestionError as error:
        raise _http_error(error) from None
    except Exception:
        raise _unavailable() from None


@router.get("/versions/{version_id}/preview/units/{unit_id}/content")
async def preview_unit_content(
    version_id: uuid.UUID,
    unit_id: str,
    tenant: Tenant = Depends(tenant_from_header),
) -> Response:
    try:
        content_type, body = await read_preview_content_unit(
            tenant, version_id, unit_id
        )
        return Response(
            content=body,
            media_type=content_type,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'",
            },
        )
    except IngestionError as error:
        raise _http_error(error) from None
    except Exception:
        raise _unavailable() from None


@router.get(
    "/versions/{version_id}/preview/units/{unit_id}/grid",
    response_model=WorksheetGridOut,
)
async def preview_unit_grid(
    version_id: uuid.UUID,
    unit_id: str,
    row_offset: Annotated[int, Query()] = 0,
    row_limit: Annotated[int, Query()] = 50,
    tenant: Tenant = Depends(tenant_from_header),
) -> WorksheetGridOut:
    try:
        return await read_preview_grid_unit(
            tenant,
            version_id,
            unit_id,
            row_offset=row_offset,
            row_limit=row_limit,
        )
    except IngestionError as error:
        raise _http_error(error) from None
    except Exception:
        raise _unavailable() from None


__all__ = ("router",)
