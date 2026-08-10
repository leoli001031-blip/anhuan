"""Document endpoints: tenant-scoped streaming upload to MinIO + RQ pipeline."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import text
from ...auth import Tenant, require_role, tenant_from_header
from ...database import session_scope
from ...storage import (
    Preflight,
    StorageError,
    preflight_upload,
    store_stream,
    verify_stored_object,
)
from ...upload_task import (
    enqueue_upload,
    finalize_upload_object,
    mark_upload_write_failed,
    reserve_api_upload,
)

router = APIRouter()

MAX_UPLOAD = 100 * 1024 * 1024  # 100 MiB


class DocumentOut(BaseModel):
    id: uuid.UUID
    enterprise_id: uuid.UUID
    filename: str
    size: int
    content_type: str
    status: str


async def _get_tenant(
    x_enterprise_id: str | None = Header(default=None),
    user: dict = Depends(require_role("super_admin", "enterprise_admin", "plant_admin")),
) -> Tenant:
    from ...auth import current_tenant

    enterprise_id: uuid.UUID | None = None
    if x_enterprise_id:
        try:
            enterprise_id = uuid.UUID(x_enterprise_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid enterprise id") from None
    return await current_tenant(user, enterprise_id)


@router.post("/upload", response_model=DocumentOut, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    tenant: Tenant = Depends(_get_tenant),
) -> DocumentOut:
    # Pass 1: read the stream, validate size/container, compute SHA-256.
    # NO object is written yet — idempotency is checked by SHA first.
    try:
        pre = preflight_upload(
            file.filename or "upload",
            file.content_type or "",
            file.file,
            max_size=MAX_UPLOAD,
        )
    except StorageError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None

    # Reserve is non-dispatchable until MinIO read-back proves the exact source
    # SHA.  A concurrent same-SHA loser resolves to the winner's identity.
    try:
        reservation = await reserve_api_upload(
            enterprise_id=tenant.enterprise_id,
            filename=file.filename or "upload",
            size=pre.size,
            content_type=pre.content_type,
            content_sha256=pre.sha256,
            sub=tenant.sub,
        )
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="UPLOAD_TASK_FAILED") from None
    if reservation.object_state == "ready":
        return await _existing_document(
            reservation.document_id, reservation.task_id, tenant, pre, file.filename
        )

    # Pass 2: write to the pre-registered opaque key and read it back.  A crash
    # before finalize leaves a referenced, non-dispatchable reservation that a
    # retry of the same SHA resumes; it never leaves a free-floating object.
    try:
        stored = store_stream(
            file.file,
            content_type=pre.content_type,
            length=pre.size,
            object_key=reservation.object_key,
        )
        verified = verify_stored_object(
            reservation.object_key,
            expected_sha256=pre.sha256,
            expected_size=pre.size,
            expected_etag=stored.etag,
        )
        await finalize_upload_object(
            reservation,
            source_etag=verified.etag,
            source_size=verified.size,
            sub=tenant.sub,
        )
    except Exception:  # noqa: BLE001
        await mark_upload_write_failed(reservation, sub=tenant.sub)
        raise HTTPException(status_code=503, detail="UPLOAD_TASK_FAILED") from None

    # RQ carries only task_id and uses its deterministic job id.  A failed
    # direct enqueue leaves the CAS-claimable outbox event for the dispatcher.
    try:
        enqueue_upload(reservation.task_id, reservation.rq_job_id)
    except Exception:  # noqa: BLE001
        pass
    return await _existing_document(
        reservation.document_id,
        reservation.task_id,
        tenant,
        pre,
        file.filename,
    )


async def _existing_document(
    document_id: uuid.UUID,
    task_id: uuid.UUID,
    tenant: Tenant,
    pre: "Preflight",
    filename: str | None,
) -> DocumentOut:
    """Return the SAME document for a duplicate upload (zero new rows)."""
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        row = (
            await session.execute(
                text(
                    "SELECT id, enterprise_id, filename, size, content_type, status "
                    "FROM f1.document WHERE id = :id"
                ),
                {"id": document_id},
            )
        ).fetchone()
        task_row = (
            await session.execute(
                text("SELECT status FROM f1.upload_task WHERE id = :id"),
                {"id": task_id},
            )
        ).fetchone()
    status = task_row[0] if task_row else "pending"
    return DocumentOut(
        id=row[0],
        enterprise_id=row[1],
        filename=row[2],
        size=row[3],
        content_type=row[4],
        status=status,
    )


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    tenant: Tenant = Depends(tenant_from_header),
) -> list[DocumentOut]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, enterprise_id, filename, size, content_type, status "
                    "FROM f1.document ORDER BY created_at DESC"
                )
            )
        ).fetchall()
    return [
        DocumentOut(
            id=r[0], enterprise_id=r[1], filename=r[2], size=r[3],
            content_type=r[4], status=r[5],
        )
        for r in rows
    ]

__all__ = ("router", "DocumentOut")
