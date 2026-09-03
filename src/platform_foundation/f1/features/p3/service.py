"""P3 controlled-ingestion DB service.

This module owns only the new P3 feature contract.  Existing storage, queue,
worker, and migration code remain shared seams owned by the integrator.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import math
import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, BinaryIO

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import Tenant
from ...database import session_scope
from ...storage import opaque_object_key
from .contracts import (
    DocumentDetailOut,
    DocumentListOut,
    DocumentSummaryOut,
    IngestionError,
    KnowledgeScopeOut,
    MAX_ATTEMPTS,
    MAX_JPEG_PREVIEW_BYTES,
    MAX_PREVIEW_BYTES,
    MAX_VERSIONS_PER_DOCUMENT,
    MAX_XLSX_COLUMNS,
    MAX_XLSX_ROWS_PER_SHEET,
    PageTextOut,
    PreviewManifestOut,
    PreviewUnitOut,
    RESOURCE_POLICY_VERSION,
    UploadPreflight,
    VersionOut,
    WorksheetGridOut,
    collection_allowed_actions,
    document_allowed_actions,
    public_reason_code,
    reason_is_retryable,
    version_allowed_actions,
    validate_knowledge_scope_selection,
)


MANAGER_ROLES = frozenset(("super_admin", "enterprise_admin", "plant_admin"))
P3_PIPELINE_KIND = "controlled_ingestion"
_PREVIEW_UNIT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def _material_rag_orchestration_enabled() -> bool:
    return (
        os.environ.get("F1_MATERIAL_RAG_ORCHESTRATION_LOCAL") == "1"
        and os.environ.get("F1_LOCAL_ENGINEERING") == "1"
    )


async def _register_ingestion_delivery_if_enabled(
    session: AsyncSession,
    tenant: Tenant,
    document_version_id: uuid.UUID,
    *,
    rearm_terminal: bool = False,
) -> None:
    """Atomically hand a committed source or manual retry to ingestion."""
    from .delivery_repository import (
        delivery_enabled,
        register_delivery_in_session,
    )

    if not delivery_enabled():
        return
    await register_delivery_in_session(
        session,
        tenant,
        document_version_id,
        rearm_terminal=rearm_terminal,
    )

_VERSION_COLUMNS = (
    "version.id AS version_id, version.document_record_id, version.version_no, "
    "version.display_filename, "
    "source.size, source.content_type, task.processing_stage, task.object_state, "
    "task.quarantine_status, "
    "task.scan_verdict, task.preview_status, task.preview_kind, "
    "task.preview_sha256, task.attempt, task.error_reason, "
    "task.released_at, task.rejected_at, version.created_at, task.updated_at"
)


def _ingestion_delivery_enabled() -> bool:
    from .delivery_repository import delivery_enabled

    return delivery_enabled()


def _version_columns() -> str:
    if _ingestion_delivery_enabled():
        delivery = (
            ",ingestion_delivery.state AS ingestion_delivery_state,"
            "ingestion_delivery.reason_code AS ingestion_delivery_reason"
        )
    else:
        delivery = (
            ",NULL::text AS ingestion_delivery_state,"
            "NULL::text AS ingestion_delivery_reason"
        )
    return _VERSION_COLUMNS + delivery


def _version_delivery_join() -> str:
    if not _ingestion_delivery_enabled():
        return ""
    return (
        "LEFT JOIN f1.material_ingestion_delivery AS ingestion_delivery ON "
        "ingestion_delivery.enterprise_id=version.enterprise_id "
        "AND ingestion_delivery.document_version_id=version.id "
        "AND ingestion_delivery.delivery_kind='resume' "
    )


def _version_status_case() -> str:
    if _ingestion_delivery_enabled():
        delivery = (
            "WHEN ingestion_delivery.state='blocked' THEN 'blocked' "
            "WHEN ingestion_delivery.state='retry_wait' THEN 'processing' "
        )
    else:
        delivery = ""
    return (
        "CASE "
        + delivery
        + "WHEN task.processing_stage IN "
        "('received','scanning','validating','previewing') THEN 'processing' "
        "WHEN task.processing_stage='ready' THEN 'ready' "
        "WHEN task.processing_stage IN ('retry_wait','rejected') THEN 'blocked' "
        "ELSE 'failed' END"
    )


@dataclass(frozen=True, slots=True)
class VersionReservation:
    document_record_id: uuid.UUID
    version_id: uuid.UUID
    source_document_id: uuid.UUID
    task_id: uuid.UUID
    object_key: str
    object_state: str
    content_sha256: str
    size: int
    content_type: str
    display_filename: str
    record_title: str | None
    plant_id: uuid.UUID | None
    declared_material_kind: str
    knowledge_scope_id: uuid.UUID
    knowledge_scope_kind: str
    client_account_id: uuid.UUID | None
    processing_stage: str
    created_task: bool

    @property
    def needs_quarantine_write(self) -> bool:
        return self.object_state in {"reserved", "write_failed"}

def require_manager(tenant: Tenant) -> None:
    if tenant.role not in MANAGER_ROLES:
        raise IngestionError("P3_MANAGER_REQUIRED", http_status=403)


def normalize_title(value: str) -> str:
    if not isinstance(value, str):
        raise IngestionError("P3_TITLE_INVALID")
    normalized = " ".join(value.split())
    if not 1 <= len(normalized) <= 160 or any(ord(char) < 32 for char in normalized):
        raise IngestionError("P3_TITLE_INVALID")
    return normalized


def normalize_declared_material_kind(value: str) -> str:
    if value not in {"policy", "report", "unknown"}:
        raise IngestionError("P3_MATERIAL_KIND_INVALID", http_status=422)
    return value


async def _current_user_id(session: AsyncSession, tenant: Tenant) -> uuid.UUID:
    actor_id = (
        await session.execute(
            text(
                "SELECT membership.user_id FROM f1.enterprise_user AS membership "
                "JOIN f1.user_profile AS profile ON profile.id=membership.user_id "
                "WHERE membership.enterprise_id=:enterprise_id "
                "AND profile.keycloak_sub=:sub"
            ),
            {"enterprise_id": tenant.enterprise_id, "sub": tenant.sub},
        )
    ).scalar_one_or_none()
    if actor_id is None:
        raise IngestionError("P3_MEMBERSHIP_NOT_FOUND", http_status=404)
    return actor_id


async def _ensure_plant(
    session: AsyncSession, tenant: Tenant, plant_id: uuid.UUID | None
) -> None:
    if plant_id is None:
        return
    found = (
        await session.execute(
            text(
                "SELECT id FROM f1.plant WHERE enterprise_id=:enterprise_id "
                "AND id=:plant_id"
            ),
            {"enterprise_id": tenant.enterprise_id, "plant_id": plant_id},
        )
    ).scalar_one_or_none()
    if found is None:
        raise IngestionError("P3_PLANT_NOT_FOUND", http_status=404)


def knowledge_namespace_key(scope_id: uuid.UUID) -> str:
    """Derive the provider-neutral namespace key from the product scope UUID."""
    if not isinstance(scope_id, uuid.UUID):
        raise ValueError("P3_KNOWLEDGE_SCOPE_ID_INVALID")
    return str(scope_id)


async def _resolve_knowledge_scope(
    session: AsyncSession,
    tenant: Tenant,
    *,
    kind: str,
    client_account_id: uuid.UUID | None,
    actor_id: uuid.UUID,
) -> Mapping[str, Any]:
    require_manager(tenant)
    kind, client_account_id = validate_knowledge_scope_selection(
        kind, client_account_id
    )
    if kind == "service_provider":
        existing = (
            await session.execute(
                text(
                    "SELECT scope.id,scope.scope_kind,scope.client_account_id,"
                    "NULL::text AS client_display_name "
                    "FROM f1.material_knowledge_scope AS scope "
                    "WHERE scope.enterprise_id=:enterprise_id "
                    "AND scope.scope_kind='service_provider'"
                ),
                {"enterprise_id": tenant.enterprise_id},
            )
        ).mappings().one_or_none()
        if existing is not None:
            return existing
        scope_id = uuid.uuid4()
        created = (
            await session.execute(
                text(
                    "INSERT INTO f1.material_knowledge_scope "
                    "(id,enterprise_id,scope_kind,client_account_id) VALUES "
                    "(:id,:enterprise_id,'service_provider',NULL) "
                    "ON CONFLICT DO NOTHING RETURNING "
                    "id,scope_kind,client_account_id,"
                    "NULL::text AS client_display_name"
                ),
                {"id": scope_id, "enterprise_id": tenant.enterprise_id},
            )
        ).mappings().one_or_none()
        if created is not None:
            return created
        existing = (
            await session.execute(
                text(
                    "SELECT scope.id,scope.scope_kind,scope.client_account_id,"
                    "NULL::text AS client_display_name "
                    "FROM f1.material_knowledge_scope AS scope "
                    "WHERE scope.enterprise_id=:enterprise_id "
                    "AND scope.scope_kind='service_provider'"
                ),
                {"enterprise_id": tenant.enterprise_id},
            )
        ).mappings().one_or_none()
        if existing is None:
            raise IngestionError("MATERIAL_SCOPE_NOT_CONFIGURED", http_status=409)
        return existing

    account = (
        await session.execute(
            text(
                "SELECT id,display_name,owner_user_id FROM f1.crm_account "
                "WHERE enterprise_id=:enterprise_id AND id=:account_id"
            ),
            {
                "enterprise_id": tenant.enterprise_id,
                "account_id": client_account_id,
            },
        )
    ).mappings().one_or_none()
    if account is None:
        raise IngestionError("P3_CLIENT_ACCOUNT_NOT_FOUND", http_status=404)
    if tenant.role not in {"super_admin", "enterprise_admin"} and not (
        tenant.role == "plant_admin" and account["owner_user_id"] == actor_id
    ):
        # Preserve the same non-disclosing boundary as tenant/account RLS.
        raise IngestionError("P3_CLIENT_ACCOUNT_NOT_FOUND", http_status=404)
    existing = (
        await session.execute(
            text(
                "SELECT scope.id,scope.scope_kind,scope.client_account_id,"
                "account.display_name AS client_display_name "
                "FROM f1.material_knowledge_scope AS scope "
                "JOIN f1.crm_account AS account "
                "ON account.enterprise_id=scope.enterprise_id "
                "AND account.id=scope.client_account_id "
                "WHERE scope.enterprise_id=:enterprise_id "
                "AND scope.scope_kind='client' "
                "AND scope.client_account_id=:account_id"
            ),
            {
                "enterprise_id": tenant.enterprise_id,
                "account_id": client_account_id,
            },
        )
    ).mappings().one_or_none()
    if existing is not None:
        return existing
    scope_id = uuid.uuid4()
    created = (
        await session.execute(
            text(
                "INSERT INTO f1.material_knowledge_scope "
                "(id,enterprise_id,scope_kind,client_account_id) VALUES "
                "(:id,:enterprise_id,'client',:account_id) "
                "ON CONFLICT DO NOTHING RETURNING "
                "id,scope_kind,client_account_id,"
                "CAST(:client_display_name AS text) AS client_display_name"
            ),
            {
                "id": scope_id,
                "enterprise_id": tenant.enterprise_id,
                "account_id": client_account_id,
                "client_display_name": str(account["display_name"]),
            },
        )
    ).mappings().one_or_none()
    if created is not None:
        return created
    existing = (
        await session.execute(
            text(
                "SELECT scope.id,scope.scope_kind,scope.client_account_id,"
                "account.display_name AS client_display_name "
                "FROM f1.material_knowledge_scope AS scope "
                "JOIN f1.crm_account AS account "
                "ON account.enterprise_id=scope.enterprise_id "
                "AND account.id=scope.client_account_id "
                "WHERE scope.enterprise_id=:enterprise_id "
                "AND scope.scope_kind='client' "
                "AND scope.client_account_id=:account_id"
            ),
            {
                "enterprise_id": tenant.enterprise_id,
                "account_id": client_account_id,
            },
        )
    ).mappings().one_or_none()
    if existing is None:
        raise IngestionError("MATERIAL_SCOPE_NOT_CONFIGURED", http_status=409)
    return existing


def _knowledge_scope_out(row: Mapping[str, Any]) -> KnowledgeScopeOut:
    return KnowledgeScopeOut(
        id=row["knowledge_scope_id"],
        kind=str(row["knowledge_scope_kind"]),
        client_account_id=row.get("client_account_id"),
        client_display_name=(
            str(row["client_display_name"])
            if row.get("client_display_name") is not None
            else None
        ),
    )


async def _existing_reservation(
    session: AsyncSession,
    tenant: Tenant,
    *,
    idempotency_key_sha256: str,
) -> VersionReservation | None:
    row = (
        await session.execute(
            text(
                "SELECT version.document_record_id, version.id, "
                "version.source_document_id, version.upload_task_id, "
                "task.object_key, task.object_state, task.content_sha256, "
                "source.size, source.content_type, version.display_filename, "
                "record.title,record.plant_id,record.declared_material_kind,"
                "scope.id AS knowledge_scope_id,"
                "scope.scope_kind AS knowledge_scope_kind,"
                "scope.client_account_id,account.display_name AS client_display_name,"
                "task.processing_stage "
                "FROM f1.document_version AS version "
                "JOIN f1.document_record AS record "
                "ON record.enterprise_id=version.enterprise_id "
                "AND record.id=version.document_record_id "
                "JOIN f1.material_knowledge_scope AS scope "
                "ON scope.enterprise_id=record.enterprise_id "
                "AND scope.id=record.knowledge_scope_id "
                "LEFT JOIN f1.crm_account AS account "
                "ON account.enterprise_id=scope.enterprise_id "
                "AND account.id=scope.client_account_id "
                "JOIN f1.upload_task AS task "
                "ON task.enterprise_id=version.enterprise_id "
                "AND task.id=version.upload_task_id "
                "JOIN f1.document AS source "
                "ON source.enterprise_id=version.enterprise_id "
                "AND source.id=version.source_document_id "
                "WHERE version.enterprise_id=:enterprise_id "
                "AND version.idempotency_key_sha256=:idempotency_key"
            ),
            {
                "enterprise_id": tenant.enterprise_id,
                "idempotency_key": idempotency_key_sha256,
            },
        )
    ).first()
    if row is None:
        return None
    return VersionReservation(
        document_record_id=row[0],
        version_id=row[1],
        source_document_id=row[2],
        task_id=row[3],
        object_key=str(row[4]),
        object_state=str(row[5]),
        content_sha256=str(row[6]),
        size=int(row[7]),
        content_type=str(row[8]),
        display_filename=str(row[9]),
        record_title=str(row[10]),
        plant_id=row[11],
        declared_material_kind=str(row[12]),
        knowledge_scope_id=row[13],
        knowledge_scope_kind=str(row[14]),
        client_account_id=row[15],
        processing_stage=str(row[17]),
        created_task=False,
    )


async def reserve_initial_version(
    tenant: Tenant,
    *,
    display_name: str,
    plant_id: uuid.UUID | None,
    declared_material_kind: str = "unknown",
    knowledge_scope_kind: str = "service_provider",
    client_account_id: uuid.UUID | None = None,
    preflight: UploadPreflight,
    idempotency_key_sha256: str,
) -> VersionReservation:
    require_manager(tenant)
    return await _reserve_version(
        tenant,
        record_id=None,
        title=normalize_title(display_name),
        plant_id=plant_id,
        declared_material_kind=normalize_declared_material_kind(
            declared_material_kind
        ),
        knowledge_scope_kind=knowledge_scope_kind,
        client_account_id=client_account_id,
        preflight=preflight,
        idempotency_key_sha256=idempotency_key_sha256,
    )


async def reserve_next_version(
    tenant: Tenant,
    *,
    record_id: uuid.UUID,
    preflight: UploadPreflight,
    idempotency_key_sha256: str,
) -> VersionReservation:
    require_manager(tenant)
    return await _reserve_version(
        tenant,
        record_id=record_id,
        title=None,
        plant_id=None,
        declared_material_kind=None,
        knowledge_scope_kind=None,
        client_account_id=None,
        preflight=preflight,
        idempotency_key_sha256=idempotency_key_sha256,
    )


async def _reserve_version(
    tenant: Tenant,
    *,
    record_id: uuid.UUID | None,
    title: str | None,
    plant_id: uuid.UUID | None,
    declared_material_kind: str | None,
    knowledge_scope_kind: str | None,
    client_account_id: uuid.UUID | None,
    preflight: UploadPreflight,
    idempotency_key_sha256: str,
) -> VersionReservation:
    reservation: VersionReservation | None = None
    for _ in range(40):
        try:
            reservation = await _insert_version(
                tenant,
                record_id=record_id,
                title=title,
                plant_id=plant_id,
                declared_material_kind=declared_material_kind,
                knowledge_scope_kind=knowledge_scope_kind,
                client_account_id=client_account_id,
                preflight=preflight,
                idempotency_key_sha256=idempotency_key_sha256,
            )
            break
        except IntegrityError:
            # A same-SHA task or same idempotency key may have won
            # concurrently. Retrying the entire transaction preserves the
            # record version counter because the failed increment rolled back.
            await asyncio.sleep(0.05)
    if reservation is None:
        async with session_scope(
            role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
        ) as session:
            reservation = await _existing_reservation(
                session, tenant, idempotency_key_sha256=idempotency_key_sha256
            )
        if reservation is None:
            raise IngestionError("P3_RESERVATION_CONFLICT", http_status=409)
    if (
        reservation.content_sha256 != preflight.content_sha256
        or reservation.size != preflight.size
        or reservation.content_type != preflight.content_type
        or reservation.display_filename != preflight.display_filename
    ):
        raise IngestionError("P3_IDEMPOTENCY_KEY_CONFLICT", http_status=409)
    if title is not None and (
        reservation.record_title != title
        or reservation.plant_id != plant_id
        or reservation.declared_material_kind != declared_material_kind
        or reservation.knowledge_scope_kind != knowledge_scope_kind
        or reservation.client_account_id != client_account_id
    ):
        raise IngestionError("P3_IDEMPOTENCY_KEY_CONFLICT", http_status=409)
    if record_id is not None and reservation.document_record_id != record_id:
        raise IngestionError("P3_IDEMPOTENCY_KEY_CONFLICT", http_status=409)
    return reservation


async def _insert_version(
    tenant: Tenant,
    *,
    record_id: uuid.UUID | None,
    title: str | None,
    plant_id: uuid.UUID | None,
    declared_material_kind: str | None,
    knowledge_scope_kind: str | None,
    client_account_id: uuid.UUID | None,
    preflight: UploadPreflight,
    idempotency_key_sha256: str,
) -> VersionReservation:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        existing = await _existing_reservation(
            session, tenant, idempotency_key_sha256=idempotency_key_sha256
        )
        if existing is not None:
            return existing
        actor_id = await _current_user_id(session, tenant)

        if record_id is None:
            await _ensure_plant(session, tenant, plant_id)
            scope = await _resolve_knowledge_scope(
                session,
                tenant,
                kind=knowledge_scope_kind or "service_provider",
                client_account_id=client_account_id,
                actor_id=actor_id,
            )
            record_id = uuid.uuid4()
            version_no = 1
            await session.execute(
                text(
                    "INSERT INTO f1.document_record "
                    "(id,enterprise_id,plant_id,title,declared_material_kind,"
                    "knowledge_scope_id,scope_selection_source,"
                    "scope_selected_by_user_id,scope_selected_at,"
                    "status,latest_version_no,"
                    "created_by_user_id) VALUES "
                    "(:id,:enterprise_id,:plant_id,:title,:declared_material_kind,"
                    ":knowledge_scope_id,'upload_selection',:actor_id,"
                    "statement_timestamp(),"
                    "'active',1,:actor_id)"
                ),
                {
                    "id": record_id,
                    "enterprise_id": tenant.enterprise_id,
                    "plant_id": plant_id,
                    "title": title,
                    "declared_material_kind": declared_material_kind,
                    "knowledge_scope_id": scope["id"],
                    "actor_id": actor_id,
                },
            )
        else:
            row = (
                await session.execute(
                    text(
                        "UPDATE f1.document_record SET "
                        "latest_version_no=latest_version_no+1, "
                        "updated_at=statement_timestamp() "
                        "WHERE id=:record_id AND enterprise_id=:enterprise_id "
                        "AND status='active' "
                        "AND latest_version_no < :max_versions "
                        "RETURNING latest_version_no,declared_material_kind,"
                        "knowledge_scope_id"
                    ),
                    {
                        "record_id": record_id,
                        "enterprise_id": tenant.enterprise_id,
                        "max_versions": MAX_VERSIONS_PER_DOCUMENT,
                    },
                )
            ).first()
            if row is None:
                record_state = (
                    await session.execute(
                        text(
                            "SELECT status,latest_version_no FROM f1.document_record "
                            "WHERE id=:record_id AND enterprise_id=:enterprise_id"
                        ),
                        {
                            "record_id": record_id,
                            "enterprise_id": tenant.enterprise_id,
                        },
                    )
                ).first()
                if (
                    record_state is not None
                    and str(record_state[0]) == "active"
                    and int(record_state[1]) >= MAX_VERSIONS_PER_DOCUMENT
                ):
                    raise IngestionError("P3_DOCUMENT_VERSION_LIMIT", http_status=409)
                raise IngestionError("P3_DOCUMENT_NOT_FOUND", http_status=404)
            version_no = int(row[0])
            declared_material_kind = str(row[1])
            scope = (
                await session.execute(
                    text(
                        "SELECT scope.id,scope.scope_kind,scope.client_account_id "
                        "FROM f1.material_knowledge_scope AS scope "
                        "WHERE scope.id=:scope_id"
                    ),
                    {"scope_id": row[2]},
                )
            ).mappings().one()

        # Equal bytes in distinct logical versions still receive independent
        # quarantine, preview, release and rejection state. Only an exact
        # Idempotency-Key retry reaches the reservation lookup above.
        created_task = True
        if created_task:
            task_id = uuid.uuid4()
            source_document_id = uuid.uuid4()
            object_key = opaque_object_key(task_id, preflight.content_type)
            object_state = "reserved"
            await session.execute(
                text(
                    "INSERT INTO f1.document "
                    "(id,enterprise_id,knowledge_scope_id,object_key,filename,size,"
                    "content_type,status) "
                    "VALUES (:id,:enterprise_id,:knowledge_scope_id,:object_key,"
                    ":display_filename,:size,:content_type,'pending')"
                ),
                {
                    "id": source_document_id,
                    "enterprise_id": tenant.enterprise_id,
                    "knowledge_scope_id": scope["id"],
                    "object_key": object_key,
                    "display_filename": preflight.display_filename,
                    "size": preflight.size,
                    "content_type": preflight.content_type,
                },
            )
            await session.execute(
                text(
                    "INSERT INTO f1.upload_task "
                    "(id,enterprise_id,document_id,object_key,content_sha256,status,"
                    "object_state,source_size,pipeline_kind,processing_stage,"
                    "quarantine_status,"
                    "resource_policy_version,scan_verdict,preview_status,"
                    "preview_unit_count) VALUES "
                    "(:id,:enterprise_id,:document_id,:object_key,:content_sha256,"
                    "'pending','reserved',:source_size,:pipeline_kind,'received','held',"
                    ":resource_policy_version,'queued','blocked',0)"
                ),
                {
                    "id": task_id,
                    "enterprise_id": tenant.enterprise_id,
                    "document_id": source_document_id,
                    "object_key": object_key,
                    "content_sha256": preflight.content_sha256,
                    "source_size": preflight.size,
                    "pipeline_kind": P3_PIPELINE_KIND,
                    "resource_policy_version": RESOURCE_POLICY_VERSION,
                },
            )
        version_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO f1.document_version "
                "(id,enterprise_id,document_record_id,version_no,"
                "source_document_id,upload_task_id,display_filename,"
                "idempotency_key_sha256,created_by_user_id) VALUES "
                "(:id,:enterprise_id,:document_record_id,:version_no,"
                ":source_document_id,:upload_task_id,:display_filename,"
                ":idempotency_key_sha256,:actor_id)"
            ),
            {
                "id": version_id,
                "enterprise_id": tenant.enterprise_id,
                "document_record_id": record_id,
                "version_no": version_no,
                "source_document_id": source_document_id,
                "upload_task_id": task_id,
                "display_filename": preflight.display_filename,
                "idempotency_key_sha256": idempotency_key_sha256,
                "actor_id": actor_id,
            },
        )
        await session.execute(
            text(
                "INSERT INTO f1.audit_log "
                "(id,enterprise_id,user_sub,action,resource_type,resource_id,result) "
                "VALUES (:id,:enterprise_id,:sub,'document.version.create',"
                "'document_version',:resource_id,'received')"
            ),
            {
                "id": uuid.uuid4(),
                "enterprise_id": tenant.enterprise_id,
                "sub": tenant.sub,
                "resource_id": str(version_id),
            },
        )
        await session.commit()
    return VersionReservation(
        document_record_id=record_id,
        version_id=version_id,
        source_document_id=source_document_id,
        task_id=task_id,
        object_key=object_key,
        object_state=object_state,
        content_sha256=preflight.content_sha256,
        size=preflight.size,
        content_type=preflight.content_type,
        display_filename=preflight.display_filename,
        record_title=title,
        plant_id=plant_id,
        declared_material_kind=str(declared_material_kind),
        knowledge_scope_id=scope["id"],
        knowledge_scope_kind=str(scope["scope_kind"]),
        client_account_id=scope["client_account_id"],
        processing_stage="received",
        created_task=True,
    )


def write_quarantine_object(
    reservation: VersionReservation,
    file_obj: BinaryIO,
) -> tuple[str, int]:
    """Use the shared storage seam without importing a second MinIO client."""
    try:
        from ... import storage

        store = getattr(storage, "store_quarantine_stream")
        verify = getattr(storage, "verify_quarantine_object")
        stored = store(
            file_obj,
            content_type=reservation.content_type,
            length=reservation.size,
            object_key=reservation.object_key,
        )
        verified = verify(
            reservation.object_key,
            expected_sha256=reservation.content_sha256,
            expected_size=reservation.size,
            expected_etag=stored.etag,
        )
    except Exception as error:
        raise IngestionError("P3_QUARANTINE_WRITE_FAILED", http_status=503) from error
    return str(verified.etag), int(verified.size)


async def finalize_quarantine(
    tenant: Tenant,
    reservation: VersionReservation,
    *,
    source_etag: str,
    source_size: int,
) -> None:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        updated = (
            await session.execute(
                text(
                    "UPDATE f1.upload_task SET object_state='quarantined', "
                    "quarantine_status='held', "
                    "status='pending', processing_stage='received', "
                    "source_etag=:source_etag, source_size=:source_size, "
                    "error_reason=NULL, next_attempt_at=NULL, "
                    "updated_at=statement_timestamp() "
                    "WHERE id=:task_id AND enterprise_id=:enterprise_id "
                    "AND content_sha256=:content_sha256 "
                    "AND object_key=:object_key "
                    "AND pipeline_kind=:pipeline_kind "
                    "AND object_state IN ('reserved','write_failed','quarantined') "
                    "RETURNING id"
                ),
                {
                    "source_etag": source_etag,
                    "source_size": source_size,
                    "task_id": reservation.task_id,
                    "enterprise_id": tenant.enterprise_id,
                    "content_sha256": reservation.content_sha256,
                    "object_key": reservation.object_key,
                    "pipeline_kind": P3_PIPELINE_KIND,
                },
            )
        ).first()
        if updated is None:
            raise IngestionError("P3_QUARANTINE_FINALIZE_CONFLICT", http_status=409)
        await session.execute(
            text(
                "INSERT INTO f1.audit_log "
                "(id,enterprise_id,user_sub,action,resource_type,resource_id,result) "
                "VALUES (:id,:enterprise_id,:sub,'document.quarantine',"
                "'document_version',:resource_id,'held')"
            ),
            {
                "id": uuid.uuid4(),
                "enterprise_id": tenant.enterprise_id,
                "sub": tenant.sub,
                "resource_id": str(reservation.version_id),
            },
        )
        await _register_ingestion_delivery_if_enabled(
            session,
            tenant,
            reservation.version_id,
        )
        await session.commit()


async def mark_quarantine_failed(
    tenant: Tenant, reservation: VersionReservation
) -> None:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await session.execute(
            text(
                "UPDATE f1.upload_task SET object_state='write_failed', "
                "quarantine_status='blocked', status='failed', "
                "processing_stage='failed', "
                "error_reason='P3_QUARANTINE_WRITE_FAILED', "
                "updated_at=statement_timestamp() "
                "WHERE id=:task_id AND pipeline_kind=:pipeline_kind "
                "AND object_state<>'quarantined'"
            ),
            {"task_id": reservation.task_id, "pipeline_kind": P3_PIPELINE_KIND},
        )
        await session.execute(
            text(
                "INSERT INTO f1.audit_log "
                "(id,enterprise_id,user_sub,action,resource_type,resource_id,result) "
                "VALUES (:id,:enterprise_id,:sub,'document.quarantine',"
                "'document_version',:resource_id,'failed')"
            ),
            {
                "id": uuid.uuid4(),
                "enterprise_id": tenant.enterprise_id,
                "sub": tenant.sub,
                "resource_id": str(reservation.version_id),
            },
        )
        await session.commit()


async def complete_upload(
    tenant: Tenant,
    reservation: VersionReservation,
    file_obj: BinaryIO,
) -> bool:
    wrote_quarantine = reservation.needs_quarantine_write
    if wrote_quarantine:
        try:
            source_etag, source_size = write_quarantine_object(reservation, file_obj)
            await finalize_quarantine(
                tenant,
                reservation,
                source_etag=source_etag,
                source_size=source_size,
            )
        except IngestionError:
            await mark_quarantine_failed(tenant, reservation)
            raise
    # Storage completion never enters the legacy indexing queue.  Product API
    # callers may immediately invoke the controlled processor; retry remains
    # available when that bounded request is interrupted or fail-closed.
    return True


def _scan_status(row: Mapping[str, Any]) -> str:
    verdict = row.get("scan_verdict")
    if verdict in {"clean", "infected", "error", "unavailable"}:
        return str(verdict)
    if row.get("processing_stage") == "scanning":
        return "scanning"
    reason = str(row.get("error_reason") or "")
    if reason in {
        "P3_SCANNER_UNAVAILABLE",
        "P3_SCANNER_DNS_FAILED",
        "P3_SCANNER_REFUSED",
        "P3_SCANNER_TIMEOUT",
        "P3_SCANNER_CONNECT_REFUSED",
        "P3_SCANNER_CONNECT_RESET",
        "P3_SCANNER_CONNECT_PIPE",
        "P3_SCANNER_VERSION_REFUSED",
        "P3_SCANNER_VERSION_RESET",
        "P3_SCANNER_VERSION_PIPE",
        "P3_SCANNER_STREAM_REFUSED",
        "P3_SCANNER_STREAM_RESET",
        "P3_SCANNER_STREAM_PIPE",
        "P3_SCAN_ENGINE_ERROR",
        "P3_SCAN_PROTOCOL_ERROR",
    }:
        return "unavailable"
    if reason.startswith("P3_SCAN_"):
        return "error"
    return "queued"


def _workflow_status(stage: object) -> str:
    normalized = str(stage)
    if normalized == "received":
        return "received"
    if normalized in {"scanning", "validating", "previewing"}:
        return "processing"
    if normalized == "ready":
        return "ready"
    if normalized in {"retry_wait", "rejected"}:
        return "blocked"
    return "failed"


def _preview_status(row: Mapping[str, Any]) -> str:
    if row.get("processing_stage") == "previewing":
        return "generating"
    stored = str(row.get("preview_status") or "blocked")
    if stored in {"blocked", "queued", "generating", "ready", "failed"}:
        return stored
    if stored == "pending":
        return "queued"
    if stored == "error":
        return "failed"
    return "blocked"


def _version_out(row: Mapping[str, Any], tenant: Tenant) -> VersionOut:
    quarantine_status = str(row.get("quarantine_status") or "held")
    if quarantine_status not in {"held", "released", "blocked"}:
        raise IngestionError("P3_STATE_INVALID", http_status=503)
    scan_status = _scan_status(row)
    preview_status = _preview_status(row)
    workflow_status = _workflow_status(row["processing_stage"])
    attempt = int(row["attempt"])
    internal_reason = str(row["error_reason"]) if row.get("error_reason") else None
    reason_code = public_reason_code(internal_reason)
    retryable = reason_is_retryable(internal_reason) and (
        reason_code == "MATERIAL_ANALYSIS_RETRY_REQUIRED" or attempt < MAX_ATTEMPTS
    )
    delivery_state = (
        str(row["ingestion_delivery_state"])
        if row.get("ingestion_delivery_state")
        else None
    )
    if delivery_state == "blocked":
        workflow_status = "blocked"
        reason_code = (
            str(row["ingestion_delivery_reason"])
            if row.get("ingestion_delivery_reason")
            else "MATERIAL_INGESTION_DELIVERY_FAILED"
        )
        retryable = False
    elif delivery_state == "retry_wait":
        workflow_status = "processing"
        reason_code = (
            str(row["ingestion_delivery_reason"])
            if row.get("ingestion_delivery_reason")
            else "MATERIAL_INGESTION_DELIVERY_FAILED"
        )
        retryable = True
    allowed_actions = version_allowed_actions(
        tenant.role,
        workflow_status=workflow_status,
        scan_status=scan_status,
        preview_status=preview_status,
        quarantine_status=quarantine_status,
        attempt=attempt,
        reason_code=reason_code,
    )
    if (
        delivery_state == "blocked"
        and tenant.role in MANAGER_ROLES
        and "process" not in allowed_actions
    ):
        allowed_actions.append("process")
    return VersionOut(
        id=row["version_id"],
        document_id=row["document_record_id"],
        version_number=int(row["version_no"]),
        original_filename=str(row["display_filename"]),
        content_type=str(row["content_type"]),
        size_bytes=int(row["size"]),
        workflow_status=workflow_status,
        quarantine_status=quarantine_status,
        scan_status=scan_status,
        preview_status=preview_status,
        reason_code=reason_code,
        retryable=retryable,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        allowed_actions=allowed_actions,
    )


def _document_status(version: VersionOut) -> str:
    if version.workflow_status in {"received", "processing"}:
        return "processing"
    return version.workflow_status


def _encode_cursor(updated_at: datetime, record_id: uuid.UUID) -> str:
    raw = f"{updated_at.isoformat()}|{record_id}".encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str | None) -> tuple[datetime | None, uuid.UUID | None]:
    if value is None:
        return None, None
    if not isinstance(value, str) or not 1 <= len(value) <= 512:
        raise IngestionError("P3_CURSOR_INVALID", http_status=400)
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True).decode(
            "ascii"
        )
        timestamp_text, record_text = decoded.split("|", 1)
        updated_at = datetime.fromisoformat(timestamp_text)
        record_id = uuid.UUID(record_text)
    except (ValueError, UnicodeError, binascii.Error) as error:
        raise IngestionError("P3_CURSOR_INVALID", http_status=400) from error
    if updated_at.tzinfo is None:
        raise IngestionError("P3_CURSOR_INVALID", http_status=400)
    return updated_at, record_id


async def list_documents(
    tenant: Tenant,
    *,
    status: str | None = None,
    content_type: str | None = None,
    scope_kind: str | None = None,
    client_account_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> DocumentListOut:
    require_manager(tenant)
    if status is not None and status not in {"processing", "ready", "blocked", "failed"}:
        raise IngestionError("P3_FILTER_INVALID", http_status=400)
    if content_type is not None and content_type not in {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "image/jpeg",
    }:
        raise IngestionError("P3_FILTER_INVALID", http_status=400)
    if scope_kind is not None and scope_kind not in {"service_provider", "client"}:
        raise IngestionError("P3_FILTER_INVALID", http_status=400)
    if scope_kind == "service_provider" and client_account_id is not None:
        raise IngestionError("P3_FILTER_INVALID", http_status=400)
    if client_account_id is not None and scope_kind not in {None, "client"}:
        raise IngestionError("P3_FILTER_INVALID", http_status=400)
    if not 1 <= limit <= 100:
        raise IngestionError("P3_LIMIT_INVALID", http_status=400)
    cursor_updated_at, cursor_id = _decode_cursor(cursor)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT record.id AS record_id, record.title, "
                    "record.declared_material_kind, "
                    "scope.id AS knowledge_scope_id,"
                    "scope.scope_kind AS knowledge_scope_kind,"
                    "scope.client_account_id,"
                    "account.display_name AS client_display_name,"
                    "NOT EXISTS (SELECT 1 FROM f1.document_version AS scoped_version "
                    "JOIN f1.upload_task AS scoped_task "
                    "ON scoped_task.enterprise_id=scoped_version.enterprise_id "
                    "AND scoped_task.id=scoped_version.upload_task_id "
                    "WHERE scoped_version.enterprise_id=record.enterprise_id "
                    "AND scoped_version.document_record_id=record.id "
                    "AND scoped_task.quarantine_status='released') "
                    "AS knowledge_scope_editable,"
                    "record.latest_version_no, record.created_at AS "
                    "record_created_at, record.updated_at AS record_updated_at, "
                    f"{_version_columns()} "
                    "FROM f1.document_record AS record "
                    "JOIN f1.document_version AS version "
                    "ON version.enterprise_id=record.enterprise_id "
                    "AND version.document_record_id=record.id "
                    "AND version.version_no=record.latest_version_no "
                    "JOIN f1.document AS source "
                    "ON source.enterprise_id=version.enterprise_id "
                    "AND source.id=version.source_document_id "
                    "JOIN f1.upload_task AS task "
                    "ON task.enterprise_id=version.enterprise_id "
                    "AND task.id=version.upload_task_id "
                    "JOIN f1.material_knowledge_scope AS scope "
                    "ON scope.enterprise_id=record.enterprise_id "
                    "AND scope.id=record.knowledge_scope_id "
                    "LEFT JOIN f1.crm_account AS account "
                    "ON account.enterprise_id=scope.enterprise_id "
                    "AND account.id=scope.client_account_id "
                    + _version_delivery_join()
                    +
                    "WHERE record.enterprise_id=:enterprise_id "
                    "AND (CAST(:scope_kind AS text) IS NULL "
                    "OR scope.scope_kind=CAST(:scope_kind AS text)) "
                    "AND (CAST(:client_account_id AS uuid) IS NULL "
                    "OR scope.client_account_id=CAST(:client_account_id AS uuid)) "
                    "AND (CAST(:content_type AS text) IS NULL "
                    "OR source.content_type=CAST(:content_type AS text)) "
                    "AND (CAST(:status AS text) IS NULL OR "
                    + _version_status_case()
                    + "=:status) "
                    "AND (CAST(:cursor_updated_at AS timestamptz) IS NULL OR "
                    "(record.updated_at,record.id)<("
                    "CAST(:cursor_updated_at AS timestamptz),"
                    "CAST(:cursor_id AS uuid))) "
                    "ORDER BY record.updated_at DESC, record.id DESC LIMIT :row_limit"
                ),
                {
                    "enterprise_id": tenant.enterprise_id,
                    "content_type": content_type,
                    "scope_kind": scope_kind,
                    "client_account_id": client_account_id,
                    "status": status,
                    "cursor_updated_at": cursor_updated_at,
                    "cursor_id": cursor_id,
                    "row_limit": limit + 1,
                },
            )
        ).mappings().all()
    page_rows = rows[:limit]
    items: list[DocumentSummaryOut] = []
    for row in page_rows:
        latest = _version_out(row, tenant)
        items.append(
            DocumentSummaryOut(
                id=row["record_id"],
                display_name=str(row["title"]),
                declared_material_kind=str(row["declared_material_kind"]),
                knowledge_scope=_knowledge_scope_out(row),
                status=_document_status(latest),
                version_count=int(row["latest_version_no"]),
                latest_version=latest,
                created_at=row["record_created_at"],
                updated_at=row["record_updated_at"],
                allowed_actions=document_allowed_actions(
                    tenant.role,
                    knowledge_scope_editable=bool(row["knowledge_scope_editable"]),
                ),
            )
        )
    next_cursor = None
    if len(rows) > limit and page_rows:
        last = page_rows[-1]
        next_cursor = _encode_cursor(last["record_updated_at"], last["record_id"])
    return DocumentListOut(
        items=items,
        next_cursor=next_cursor,
        allowed_actions=collection_allowed_actions(tenant.role),
    )


async def get_document(tenant: Tenant, record_id: uuid.UUID) -> DocumentDetailOut:
    require_manager(tenant)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        record = (
            await session.execute(
                text(
                    "SELECT record.id,record.title,record.declared_material_kind,"
                    "record.latest_version_no,record.created_at,record.updated_at,"
                    "scope.id AS knowledge_scope_id,"
                    "scope.scope_kind AS knowledge_scope_kind,"
                    "scope.client_account_id,account.display_name AS client_display_name,"
                    "NOT EXISTS (SELECT 1 FROM f1.document_version AS scoped_version "
                    "JOIN f1.upload_task AS scoped_task "
                    "ON scoped_task.enterprise_id=scoped_version.enterprise_id "
                    "AND scoped_task.id=scoped_version.upload_task_id "
                    "WHERE scoped_version.enterprise_id=record.enterprise_id "
                    "AND scoped_version.document_record_id=record.id "
                    "AND scoped_task.quarantine_status='released') "
                    "AS knowledge_scope_editable "
                    "FROM f1.document_record AS record "
                    "JOIN f1.material_knowledge_scope AS scope "
                    "ON scope.enterprise_id=record.enterprise_id "
                    "AND scope.id=record.knowledge_scope_id "
                    "LEFT JOIN f1.crm_account AS account "
                    "ON account.enterprise_id=scope.enterprise_id "
                    "AND account.id=scope.client_account_id "
                    "WHERE record.id=:record_id "
                    "AND record.enterprise_id=:enterprise_id"
                ),
                {"record_id": record_id, "enterprise_id": tenant.enterprise_id},
            )
        ).mappings().first()
        if record is None:
            raise IngestionError("P3_DOCUMENT_NOT_FOUND", http_status=404)
        versions = (
            await session.execute(
                text(
                    f"SELECT {_version_columns()} "
                    "FROM f1.document_version AS version "
                    "JOIN f1.document AS source "
                    "ON source.enterprise_id=version.enterprise_id "
                    "AND source.id=version.source_document_id "
                    "JOIN f1.upload_task AS task "
                    "ON task.enterprise_id=version.enterprise_id "
                    "AND task.id=version.upload_task_id "
                    + _version_delivery_join()
                    +
                    "WHERE version.document_record_id=:record_id "
                    "AND version.enterprise_id=:enterprise_id "
                    "ORDER BY version.version_no DESC"
                ),
                {"record_id": record_id, "enterprise_id": tenant.enterprise_id},
            )
        ).mappings().all()
    version_outputs = [_version_out(row, tenant) for row in versions]
    if not version_outputs:
        raise IngestionError("P3_DOCUMENT_NOT_FOUND", http_status=404)
    latest = next(
        (
            item
            for item in version_outputs
            if item.version_number == int(record["latest_version_no"])
        ),
        version_outputs[0],
    )
    return DocumentDetailOut(
        id=record["id"],
        display_name=str(record["title"]),
        declared_material_kind=str(record["declared_material_kind"]),
        knowledge_scope=_knowledge_scope_out(record),
        status=_document_status(latest),
        version_count=int(record["latest_version_no"]),
        latest_version=latest,
        versions=version_outputs,
        created_at=record["created_at"],
        updated_at=record["updated_at"],
        allowed_actions=document_allowed_actions(
            tenant.role,
            knowledge_scope_editable=bool(record["knowledge_scope_editable"]),
        ),
    )


async def set_document_knowledge_scope(
    tenant: Tenant,
    record_id: uuid.UUID,
    *,
    kind: str,
    client_account_id: uuid.UUID | None,
) -> DocumentDetailOut:
    """Change a document's product scope before any version is released."""
    require_manager(tenant)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await _current_user_id(session, tenant)
        await session.execute(
            text(
                "SELECT task.id FROM f1.document_version AS version "
                "JOIN f1.upload_task AS task "
                "ON task.enterprise_id=version.enterprise_id "
                "AND task.id=version.upload_task_id "
                "WHERE version.enterprise_id=:enterprise_id "
                "AND version.document_record_id=:record_id "
                "ORDER BY task.id "
                "FOR UPDATE OF task"
            ),
            {
                "enterprise_id": tenant.enterprise_id,
                "record_id": record_id,
            },
        )
        record = (
            await session.execute(
                text(
                    "SELECT id,knowledge_scope_id FROM f1.document_record "
                    "WHERE enterprise_id=:enterprise_id AND id=:record_id "
                    "FOR UPDATE"
                ),
                {
                    "enterprise_id": tenant.enterprise_id,
                    "record_id": record_id,
                },
            )
        ).mappings().one_or_none()
        if record is None:
            raise IngestionError("P3_DOCUMENT_NOT_FOUND", http_status=404)
        released = (
            await session.execute(
                text(
                    "SELECT 1 FROM f1.document_version AS version "
                    "JOIN f1.upload_task AS task "
                    "ON task.enterprise_id=version.enterprise_id "
                    "AND task.id=version.upload_task_id "
                    "WHERE version.enterprise_id=:enterprise_id "
                    "AND version.document_record_id=:record_id "
                    "AND task.quarantine_status='released' LIMIT 1"
                ),
                {
                    "enterprise_id": tenant.enterprise_id,
                    "record_id": record_id,
                },
            )
        ).first()
        if released is not None:
            raise IngestionError("P3_KNOWLEDGE_SCOPE_LOCKED", http_status=409)
        scope = await _resolve_knowledge_scope(
            session,
            tenant,
            kind=kind,
            client_account_id=client_account_id,
            actor_id=actor_id,
        )
        updated = (
            await session.execute(
                text(
                    "UPDATE f1.document_record SET knowledge_scope_id=:scope_id,"
                    "scope_selection_source='human_review',"
                    "scope_selected_by_user_id=:actor_id,"
                    "scope_selected_at=statement_timestamp(),"
                    "updated_at=statement_timestamp() "
                    "WHERE enterprise_id=:enterprise_id AND id=:record_id "
                    "RETURNING id"
                ),
                {
                    "scope_id": scope["id"],
                    "actor_id": actor_id,
                    "enterprise_id": tenant.enterprise_id,
                    "record_id": record_id,
                },
            )
        ).first()
        if updated is None:
            raise IngestionError("P3_KNOWLEDGE_SCOPE_CONFLICT", http_status=409)
        await session.execute(
            text(
                "INSERT INTO f1.audit_log "
                "(id,enterprise_id,user_sub,action,resource_type,resource_id,result) "
                "VALUES (:id,:enterprise_id,:sub,'document.scope.updated',"
                "'document_record',:resource_id,'updated')"
            ),
            {
                "id": uuid.uuid4(),
                "enterprise_id": tenant.enterprise_id,
                "sub": tenant.sub,
                "resource_id": str(record_id),
            },
        )
        await session.commit()
    return await get_document(tenant, record_id)


async def get_version(tenant: Tenant, version_id: uuid.UUID) -> VersionOut:
    require_manager(tenant)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        row = (
            await session.execute(
                text(
                    f"SELECT {_version_columns()} "
                    "FROM f1.document_version AS version "
                    "JOIN f1.document AS source "
                    "ON source.enterprise_id=version.enterprise_id "
                    "AND source.id=version.source_document_id "
                    "JOIN f1.upload_task AS task "
                    "ON task.enterprise_id=version.enterprise_id "
                    "AND task.id=version.upload_task_id "
                    + _version_delivery_join()
                    +
                    "WHERE version.id=:version_id "
                    "AND version.enterprise_id=:enterprise_id"
                ),
                {"version_id": version_id, "enterprise_id": tenant.enterprise_id},
            )
        ).mappings().first()
    if row is None:
        raise IngestionError("P3_DOCUMENT_NOT_FOUND", http_status=404)
    return _version_out(row, tenant)


def _release_quarantine_object(row: Mapping[str, Any]) -> None:
    try:
        from ... import storage

        release = getattr(storage, "release_ingestion_object")
        released = release(
            task_id=row["task_id"],
            object_key=str(row["object_key"]),
            expected_sha256=str(row["content_sha256"]),
            expected_size=int(row["source_size"]),
            expected_etag=str(row["source_etag"]),
        )
    except Exception as error:
        raise IngestionError("P3_RELEASE_WRITE_FAILED", http_status=503) from error
    if released is not True:
        raise IngestionError("P3_RELEASE_WRITE_FAILED", http_status=503)


async def act_on_version(
    tenant: Tenant, version_id: uuid.UUID, *, action: str
) -> VersionOut:
    """Apply one manager action with its audit record in the same transaction."""
    require_manager(tenant)
    if action not in {"retry", "release", "reject"}:
        raise IngestionError("P3_ILLEGAL_STATE_TRANSITION", http_status=409)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        row = (
            await session.execute(
                text(
                    "SELECT task.id AS task_id,task.document_id,task.object_key,"
                    "task.content_sha256,task.source_size,task.source_etag,"
                    "task.processing_stage,"
                    "task.object_state,task.quarantine_status,task.scan_verdict,"
                    "task.preview_status,task.attempt,"
                    "task.error_reason,task.released_at,task.rejected_at "
                    "FROM f1.document_version AS version "
                    "JOIN f1.upload_task AS task "
                    "ON task.enterprise_id=version.enterprise_id "
                    "AND task.id=version.upload_task_id "
                    "WHERE version.id=:version_id "
                    "AND version.enterprise_id=:enterprise_id "
                    "AND task.pipeline_kind=:pipeline_kind FOR UPDATE OF task"
                ),
                {
                    "version_id": version_id,
                    "enterprise_id": tenant.enterprise_id,
                    "pipeline_kind": P3_PIPELINE_KIND,
                },
            )
        ).mappings().first()
        if row is None:
            raise IngestionError("P3_DOCUMENT_NOT_FOUND", http_status=404)

        task_id = row["task_id"]
        stage = str(row["processing_stage"])
        scan_status = str(row["scan_verdict"] or "queued")
        preview_status = str(row["preview_status"] or "blocked")
        attempt = int(row["attempt"])
        internal_reason = (
            str(row["error_reason"]) if row.get("error_reason") else None
        )
        no_change = False

        if action == "release":
            if row.get("released_at") is not None:
                no_change = True
            elif (
                row.get("rejected_at") is not None
                or str(row["object_state"]) != "ready"
                or str(row["quarantine_status"]) != "held"
                or stage != "ready"
                or scan_status != "clean"
                or preview_status != "ready"
            ):
                raise IngestionError("P3_ILLEGAL_STATE_TRANSITION", http_status=409)
            else:
                _release_quarantine_object(row)
                released = await session.execute(
                    text(
                        "UPDATE f1.upload_task SET quarantine_status='released',"
                        "released_at=statement_timestamp(),"
                        "updated_at=statement_timestamp() WHERE id=:task_id "
                        "AND enterprise_id=:enterprise_id "
                        "AND object_state='ready' AND quarantine_status='held' "
                        "AND processing_stage='ready' AND scan_verdict='clean' "
                        "AND preview_status='ready' "
                        "AND released_at IS NULL AND rejected_at IS NULL "
                        "RETURNING id"
                    ),
                    {"task_id": task_id, "enterprise_id": tenant.enterprise_id},
                )
                if released.first() is None:
                    raise IngestionError("P3_ILLEGAL_STATE_TRANSITION", http_status=409)
                await session.execute(
                    text(
                        "UPDATE f1.document SET status='done' WHERE id=:document_id "
                        "AND enterprise_id=:enterprise_id"
                    ),
                    {
                        "document_id": row["document_id"],
                        "enterprise_id": tenant.enterprise_id,
                    },
                )
            audit_result = "released"
        elif action == "reject":
            if row.get("rejected_at") is not None:
                no_change = True
            elif (
                row.get("released_at") is not None
                or str(row["quarantine_status"]) != "held"
            ):
                raise IngestionError("P3_ILLEGAL_STATE_TRANSITION", http_status=409)
            else:
                rejected = await session.execute(
                    text(
                        "UPDATE f1.upload_task SET object_state='quarantined',"
                        "quarantine_status='blocked',processing_stage='rejected',"
                        "status='failed',rejected_at=statement_timestamp(),"
                        "next_attempt_at=NULL,lease_token=NULL,lease_owner=NULL,"
                        "lease_acquired_at=NULL,lease_until=NULL,"
                        "updated_at=statement_timestamp() WHERE id=:task_id "
                        "AND enterprise_id=:enterprise_id "
                        "AND quarantine_status='held' "
                        "AND released_at IS NULL AND rejected_at IS NULL "
                        "RETURNING id"
                    ),
                    {"task_id": task_id, "enterprise_id": tenant.enterprise_id},
                )
                if rejected.first() is None:
                    raise IngestionError(
                        "P3_ILLEGAL_STATE_TRANSITION", http_status=409
                    )
                await session.execute(
                    text(
                        "UPDATE f1.document SET status='failed' "
                        "WHERE id=:document_id AND enterprise_id=:enterprise_id"
                    ),
                    {
                        "document_id": row["document_id"],
                        "enterprise_id": tenant.enterprise_id,
                    },
                )
            audit_result = "rejected"
        else:
            if (
                stage not in {"retry_wait", "failed"}
                or str(row["object_state"]) != "quarantined"
                or str(row["quarantine_status"]) != "held"
                or attempt >= MAX_ATTEMPTS
                or not reason_is_retryable(internal_reason)
                or row.get("released_at") is not None
                or row.get("rejected_at") is not None
            ):
                raise IngestionError("P3_ILLEGAL_STATE_TRANSITION", http_status=409)
            await session.execute(
                text(
                    "UPDATE f1.upload_task SET status='pending',"
                    "processing_stage='received',scan_verdict='queued',"
                    "quarantine_status='held',"
                    "preview_status='blocked',preview_kind=NULL,"
                    "preview_sha256=NULL,preview_unit_count=0,error_reason=NULL,"
                    "next_attempt_at=NULL,"
                    "lease_token=NULL,lease_owner=NULL,lease_acquired_at=NULL,"
                    "lease_until=NULL,updated_at=statement_timestamp() "
                    "WHERE id=:task_id AND enterprise_id=:enterprise_id "
                    "AND pipeline_kind=:pipeline_kind"
                ),
                {
                    "task_id": task_id,
                    "enterprise_id": tenant.enterprise_id,
                    "pipeline_kind": P3_PIPELINE_KIND,
                },
            )
            audit_result = "retry_queued"

        if action == "retry":
            await _register_ingestion_delivery_if_enabled(
                session,
                tenant,
                version_id,
                rearm_terminal=True,
            )

        if action == "release" and _material_rag_orchestration_enabled():
            from platform_foundation.f1.features.material_rag.repository import (
                enqueue_job_in_session,
            )

            await enqueue_job_in_session(
                session,
                tenant,
                document_version_id=version_id,
                action="index",
                idempotency_key=f"p3-release-index:{version_id}",
            )
            if os.environ.get("F1_MATERIAL_RAG_ORCH_INJECT") == "FAIL_AFTER_JOB_INSERT":
                raise IngestionError(
                    "MATERIAL_RAG_ORCH_INJECTED_FAILURE", http_status=500
                )

        if not no_change:
            await session.execute(
                text(
                    "INSERT INTO f1.audit_log "
                    "(id,enterprise_id,user_sub,action,resource_type,resource_id,result) "
                    "VALUES (:id,:enterprise_id,:sub,:action,'document_version',"
                    ":resource_id,:result)"
                ),
                {
                    "id": uuid.uuid4(),
                    "enterprise_id": tenant.enterprise_id,
                    "sub": tenant.sub,
                    "action": f"document.version.{action}",
                    "resource_id": str(version_id),
                    "result": audit_result,
                },
            )
        await session.commit()
    return await get_version(tenant, version_id)


async def _preview_record(
    tenant: Tenant, version_id: uuid.UUID
) -> Mapping[str, Any]:
    require_manager(tenant)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        row = (
            await session.execute(
                text(
                    "SELECT task.id AS task_id,source.content_type,"
                    "task.processing_stage,task.preview_status,task.preview_kind,"
                    "task.preview_sha256,task.preview_unit_count,task.error_reason,"
                    "task.updated_at FROM f1.document_version AS version "
                    "JOIN f1.document AS source "
                    "ON source.enterprise_id=version.enterprise_id "
                    "AND source.id=version.source_document_id "
                    "JOIN f1.upload_task AS task "
                    "ON task.enterprise_id=version.enterprise_id "
                    "AND task.id=version.upload_task_id "
                    "WHERE version.id=:version_id "
                    "AND version.enterprise_id=:enterprise_id "
                    "AND task.pipeline_kind=:pipeline_kind"
                ),
                {
                    "version_id": version_id,
                    "enterprise_id": tenant.enterprise_id,
                    "pipeline_kind": P3_PIPELINE_KIND,
                },
            )
        ).mappings().first()
    if row is None:
        raise IngestionError("P3_DOCUMENT_NOT_FOUND", http_status=404)
    return row


def _manifest_kind(row: Mapping[str, Any]) -> str:
    stored = str(row.get("preview_kind") or "")
    if stored in {"page_text", "sheet_grid", "image"}:
        return stored
    if stored == "grid":
        return "sheet_grid"
    if row.get("content_type") in {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }:
        return "page_text"
    if row.get("content_type") == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ):
        return "sheet_grid"
    return "image"


def _manifest_status(row: Mapping[str, Any]) -> str:
    if row.get("processing_stage") == "previewing":
        return "generating"
    status = str(row.get("preview_status") or "blocked")
    if status in {"blocked", "generating", "ready", "failed"}:
        return status
    if status == "error":
        return "failed"
    return "blocked"


def _stored_preview_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    expected_sha256 = str(row.get("preview_sha256") or "")
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise IngestionError("P3_PREVIEW_INVALID", http_status=503)
    try:
        from ... import storage

        loaded = storage.read_ingestion_preview_manifest(
            task_id=row["task_id"], expected_sha256=expected_sha256
        )
        payload = json.loads(loaded.decode("utf-8"))
    except Exception as error:
        raise IngestionError("P3_PREVIEW_TEMPORARY_FAILURE", http_status=503) from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"kind", "units"}
        or payload.get("kind") != _manifest_kind(row)
        or not isinstance(payload.get("units"), list)
    ):
        raise IngestionError("P3_PREVIEW_INVALID", http_status=503)
    return payload


async def get_preview_manifest(
    tenant: Tenant, version_id: uuid.UUID
) -> PreviewManifestOut:
    row = await _preview_record(tenant, version_id)
    status = _manifest_status(row)
    internal_reason = str(row["error_reason"]) if row.get("error_reason") else None
    kind = _manifest_kind(row)
    if status != "ready":
        return PreviewManifestOut(
            version_id=version_id,
            status=status,
            kind=kind,
            reason_code=public_reason_code(internal_reason),
            retryable=reason_is_retryable(internal_reason),
        )
    raw_units = _stored_preview_payload(row)["units"]
    expected_count = int(row.get("preview_unit_count") or 0)
    if expected_count < 1 or len(raw_units) != expected_count:
        raise IngestionError("P3_PREVIEW_INVALID", http_status=503)
    try:
        units = [
            PreviewUnitOut(
                id=str(item["id"]),
                kind=item["kind"],
                ordinal=item["ordinal"],
                label=item["label"],
                width_px=item["width_px"],
                height_px=item["height_px"],
                row_count=item["row_count"],
                column_count=item["column_count"],
            )
            for item in raw_units
        ]
        expected_unit_kind = {
            "page_text": "page_text",
            "sheet_grid": "worksheet_grid",
            "image": "image",
        }[kind]
        if (
            any(unit.kind != expected_unit_kind for unit in units)
            or len({unit.id for unit in units}) != len(units)
            or [unit.ordinal for unit in units] != list(range(1, len(units) + 1))
        ):
            raise ValueError("P3_PREVIEW_INVALID")
        return PreviewManifestOut(
            version_id=version_id,
            status="ready",
            kind=kind,
            units=units,
            generated_at=row["updated_at"],
        )
    except Exception as error:
        raise IngestionError("P3_PREVIEW_INVALID", http_status=503) from error


def _validate_preview_unit_id(unit_id: str) -> str:
    if not isinstance(unit_id, str) or _PREVIEW_UNIT_ID_RE.fullmatch(unit_id) is None:
        raise IngestionError("P3_PREVIEW_UNIT_NOT_FOUND", http_status=404)
    return unit_id


async def _preview_unit_record(
    tenant: Tenant, version_id: uuid.UUID, unit_id: str
) -> Mapping[str, Any]:
    checked_id = _validate_preview_unit_id(unit_id)
    task = await _preview_record(tenant, version_id)
    if task.get("preview_status") != "ready":
        raise IngestionError("P3_PREVIEW_UNIT_NOT_FOUND", http_status=404)
    payload = _stored_preview_payload(task)
    matches = [
        item
        for item in payload["units"]
        if isinstance(item, dict) and item.get("id") == checked_id
    ]
    if len(matches) != 1:
        raise IngestionError("P3_PREVIEW_UNIT_NOT_FOUND", http_status=404)
    unit = dict(matches[0])
    unit["task_id"] = task["task_id"]
    unit["unit_kind"] = unit.get("kind")
    unit["content_sha256"] = unit.get("sha256")
    return unit


def _read_preview_unit_object(row: Mapping[str, Any]) -> bytes:
    expected_sha256 = str(row.get("content_sha256") or "")
    expected_size = int(row.get("size_bytes") or 0)
    size_limit = (
        MAX_JPEG_PREVIEW_BYTES
        if row.get("unit_kind") == "image" and row.get("content_type") == "image/jpeg"
        else MAX_PREVIEW_BYTES
    )
    if (
        re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
        or not 1 <= expected_size <= size_limit
    ):
        raise IngestionError("P3_PREVIEW_INVALID", http_status=503)
    try:
        from ... import storage

        loader = getattr(storage, "read_ingestion_preview_artifact")
        loaded = loader(
            task_id=row["task_id"],
            unit_id=str(row["id"]),
            content_type=str(row["content_type"]),
            expected_sha256=expected_sha256,
            expected_size=expected_size,
        )
    except Exception as error:
        raise IngestionError(
            "P3_PREVIEW_TEMPORARY_FAILURE", http_status=503
        ) from error
    if not isinstance(loaded, bytes) or len(loaded) != expected_size:
        raise IngestionError("P3_PREVIEW_INVALID", http_status=503)
    return loaded


async def read_preview_content_unit(
    tenant: Tenant, version_id: uuid.UUID, unit_id: str
) -> tuple[str, bytes]:
    row = await _preview_unit_record(tenant, version_id, unit_id)
    unit_kind = str(row["unit_kind"])
    if unit_kind not in {"page_text", "image"}:
        raise IngestionError("P3_PREVIEW_UNIT_NOT_FOUND", http_status=404)
    loaded = _read_preview_unit_object(row)
    if unit_kind == "page_text":
        if row["content_type"] != "application/json":
            raise IngestionError("P3_PREVIEW_INVALID", http_status=503)
        try:
            payload = json.loads(loaded.decode("utf-8"))
            page = PageTextOut(**payload)
        except Exception as error:
            raise IngestionError("P3_PREVIEW_INVALID", http_status=503) from error
        if any(len(line) > 80 for line in page.lines):
            raise IngestionError("P3_PREVIEW_INVALID", http_status=503)
        lines = list(page.lines)
        if not lines:
            # Scanned pages ingest with empty pypdf text; after analysis the
            # OCR'd text lives in the encrypted canonical units.  Surface it
            # here so the preview reflects what the pipeline actually read.
            lines, truncated = await _ocr_backfill_lines(
                tenant, version_id, int(row.get("ordinal") or 0)
            )
            if lines:
                return (
                    "application/json",
                    json.dumps(
                        {"lines": lines, "truncated": truncated},
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8"),
                )
        return (
            "application/json",
            json.dumps(
                {"lines": page.lines, "truncated": page.truncated},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        )
    if row["content_type"] != "image/jpeg":
        raise IngestionError("P3_PREVIEW_INVALID", http_status=503)
    if not loaded.startswith(b"\xff\xd8") or not loaded.endswith(b"\xff\xd9"):
        raise IngestionError("P3_PREVIEW_INVALID", http_status=503)
    return "image/jpeg", loaded


def _wrap_preview_lines(text: str, *, width: int = 80, max_lines: int = 400) -> tuple[list[str], bool]:
    lines: list[str] = []
    truncated = False
    for raw_line in text.splitlines() or [text]:
        stripped = raw_line.strip()
        if not stripped:
            continue
        while stripped:
            if len(lines) >= max_lines:
                return lines, True
            lines.append(stripped[:width])
            stripped = stripped[width:]
    return lines, truncated


async def _ocr_backfill_lines(
    tenant: Tenant, version_id: uuid.UUID, page_number: int
) -> tuple[list[str], bool]:
    """Return OCR'd page text from the indexed canonical units, if present."""
    if page_number < 1:
        return [], False
    from ..material_rag.repository import decrypt_text, unit_aad_for_identity

    try:
        async with session_scope(
            role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
        ) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT unit.id,unit.ordinal,unit.parser_version,"
                        "unit.body_ciphertext,unit.body_sha256,unit.body_aad_sha256,"
                        "record.id AS record_id,record.source_sha256,"
                        "record.knowledge_scope_id "
                        "FROM f1.material_rag_unit AS unit "
                        "JOIN f1.document_record AS record "
                        "ON record.enterprise_id=unit.enterprise_id "
                        "AND record.id=unit.document_record_id "
                        "WHERE unit.enterprise_id=:enterprise_id "
                        "AND unit.document_version_id=:version_id "
                        "AND unit.page_number=:page_number "
                        "ORDER BY unit.ordinal,unit.id"
                    ),
                    {
                        "enterprise_id": tenant.enterprise_id,
                        "version_id": version_id,
                        "page_number": page_number,
                    },
                )
            ).mappings().all()
            parts: list[str] = []
            for row in rows:
                aad = unit_aad_for_identity(
                    enterprise_id=tenant.enterprise_id,
                    knowledge_scope_id=row["knowledge_scope_id"],
                    unit_id=row["id"],
                    document_record_id=row["record_id"],
                    document_version_id=version_id,
                    source_sha256=str(row["source_sha256"]),
                    page_number=page_number,
                    ordinal=int(row["ordinal"]),
                    parser_version=str(row["parser_version"]),
                    body_sha256=str(row["body_sha256"]),
                )
                parts.append(
                    decrypt_text(
                        bytes(row["body_ciphertext"]),
                        aad,
                        str(row["body_aad_sha256"]),
                    )
                )
    except Exception:
        # Backfill is best-effort presentation only; the strict preview
        # contract (empty lines) stays authoritative on any failure.
        return [], False
    if not parts:
        return [], False
    return _wrap_preview_lines("\n".join(parts))


async def read_preview_grid_unit(
    tenant: Tenant,
    version_id: uuid.UUID,
    unit_id: str,
    *,
    row_offset: int,
    row_limit: int,
) -> WorksheetGridOut:
    if row_offset < 0 or not 1 <= row_limit <= 200:
        raise IngestionError("P3_PREVIEW_RANGE_INVALID", http_status=400)
    row = await _preview_unit_record(tenant, version_id, unit_id)
    if row["unit_kind"] != "worksheet_grid" or row["content_type"] != "application/json":
        raise IngestionError("P3_PREVIEW_UNIT_NOT_FOUND", http_status=404)
    loaded = _read_preview_unit_object(row)
    try:
        all_rows = json.loads(loaded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IngestionError("P3_PREVIEW_INVALID", http_status=503) from error
    if not isinstance(all_rows, list) or any(
        not isinstance(cells, list) for cells in all_rows
    ):
        raise IngestionError("P3_PREVIEW_INVALID", http_status=503)
    total_rows = int(row.get("row_count") or 0)
    total_columns = int(row.get("column_count") or 0)
    observed_columns = max((len(cells) for cells in all_rows), default=0)
    if (
        len(all_rows) != total_rows
        or total_rows > MAX_XLSX_ROWS_PER_SHEET
        or total_columns > MAX_XLSX_COLUMNS
        or observed_columns != total_columns
        or any(len(cells) > MAX_XLSX_COLUMNS for cells in all_rows)
        or any(
            not _safe_grid_cell(cell)
            for cells in all_rows
            for cell in cells
        )
    ):
        raise IngestionError("P3_PREVIEW_INVALID", http_status=503)
    sliced = all_rows[row_offset : row_offset + row_limit]
    try:
        grid = WorksheetGridOut(
            unit_id=unit_id,
            row_offset=row_offset,
            total_rows=total_rows,
            total_columns=total_columns,
            rows=sliced,
            truncated=row_offset + len(sliced) < total_rows,
        )
    except Exception as error:
        raise IngestionError("P3_PREVIEW_INVALID", http_status=503) from error
    if len(
        json.dumps(
            grid.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ) > MAX_PREVIEW_BYTES:
        raise IngestionError("P3_PREVIEW_INVALID", http_status=503)
    return grid


def _safe_grid_cell(value: object) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    return isinstance(value, float) and math.isfinite(value)


__all__ = (
    "P3_PIPELINE_KIND",
    "VersionReservation",
    "complete_upload",
    "act_on_version",
    "finalize_quarantine",
    "get_document",
    "get_preview_manifest",
    "get_version",
    "list_documents",
    "knowledge_namespace_key",
    "mark_quarantine_failed",
    "normalize_title",
    "read_preview_grid_unit",
    "read_preview_content_unit",
    "require_manager",
    "reserve_initial_version",
    "reserve_next_version",
    "set_document_knowledge_scope",
    "write_quarantine_object",
)
