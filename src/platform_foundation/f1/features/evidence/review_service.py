"""Material review API service, with current authorization on every retry."""
from __future__ import annotations

import hashlib
import uuid
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from ...auth import Tenant
from ...database import session_scope
from ..material_rag.security import decrypt_text, unit_aad_for_identity
from ..p3.contracts import IngestionError
from ..p3.service import get_version
from .contracts import canonical_json, format_location, parse_locator
from .envelopes import decrypt_fragment
from .repository import native_extraction_enabled
from .review import ReviewWriteIn, build_review_payload, decrypt_review_fragment, request_digest


class ReviewEntryOut(BaseModel):
    id: uuid.UUID
    base_fragment_id: uuid.UUID
    ordinal: int
    entry_kind: Literal['text', 'field']
    field_name: str | None = None
    locator: dict
    location: str
    text: str
    body_sha256: str


class ReviewHeadOut(BaseModel):
    id: uuid.UUID
    action: Literal['confirm', 'revoke']
    revision_no: int
    base_revision_id: uuid.UUID | None = None
    base_manifest_sha256: str | None = None


class MaterialReviewOut(BaseModel):
    version_id: uuid.UUID
    source_format: Literal['pdf', 'docx', 'xlsx', 'jpeg']
    source_sha256: str
    base_revision_id: uuid.UUID | None = None
    base_manifest_sha256: str | None = None
    editable: bool
    review_head: ReviewHeadOut | None = None
    base_items: list[ReviewEntryOut]
    review_items: list[ReviewEntryOut]


class ReviewReceiptOut(BaseModel):
    request_id: uuid.UUID
    id: uuid.UUID
    revision_no: int
    action: Literal['confirm', 'revoke']
    replayed: bool


async def _read_raw(tenant: Tenant, version_id: uuid.UUID) -> dict:
    await get_version(tenant, version_id)
    if not native_extraction_enabled():
        raise IngestionError('P3_DOCUMENT_NOT_FOUND', http_status=404)
    async with session_scope(role='f1_api', enterprise_id=tenant.enterprise_id, sub=tenant.sub) as session:
        row = (await session.execute(text('SELECT f1.read_material_review(:version)'), {'version': version_id})).scalar_one()
    if row is None:
        raise IngestionError('P3_DOCUMENT_NOT_FOUND', http_status=404)
    return row


def _base_body(row: dict, source_format: str) -> str:
    if source_format != 'pdf':
        return decrypt_fragment({**row, 'body_ciphertext': bytes.fromhex(row['body_ciphertext_hex'])})
    aad = unit_aad_for_identity(
        **{k: uuid.UUID(str(row[k])) for k in ('enterprise_id','knowledge_scope_id','document_record_id','document_version_id')},
        unit_id=uuid.UUID(str(row['id'])), **{k: row[k] for k in ('source_sha256','page_number','ordinal','parser_version','body_sha256')})
    body = decrypt_text(bytes.fromhex(row['body_ciphertext_hex']), aad, row['body_aad_sha256'])
    if hashlib.sha256(body.encode('utf-8')).hexdigest() != row['body_sha256']:
        raise ValueError('REVIEW_BASE_BODY_INVALID')
    return body


async def get_review(tenant: Tenant, version_id: uuid.UUID) -> MaterialReviewOut:
    row = await _read_raw(tenant, version_id)
    base = []
    for n, f in enumerate(row.get('base_fragments', [])):
        base.append(ReviewEntryOut(id=f['id'], base_fragment_id=f['id'], ordinal=n, entry_kind='text',
            locator=f['locator'], location=format_location(parse_locator(f['locator'])),
            text=_base_body(f, row['source_format']), body_sha256=f['body_sha256']))
    reviewed = [ReviewEntryOut(id=f['id'], base_fragment_id=f['base_fragment_id'], ordinal=f['ordinal'],
        entry_kind=f['entry_kind'], field_name=f['field_name'], locator=f['locator'],
        location=format_location(parse_locator(f['locator'])), text=decrypt_review_fragment(f),
        body_sha256=f['body_sha256']) for f in row['review_fragments']]
    return MaterialReviewOut(version_id=version_id, source_format=row['source_format'], source_sha256=row['source_sha256'],
        base_revision_id=row.get('base_revision_id'), base_manifest_sha256=row.get('base_manifest_sha256'),
        editable=bool(base), review_head=row['review_head'], base_items=base, review_items=reviewed)


def _write_error(error: Exception) -> IngestionError:
    message = str(error)
    if 'REVIEW_SOURCE_UNAVAILABLE' in message:
        return IngestionError('P3_DOCUMENT_NOT_FOUND', http_status=404)
    for code in ('REVIEW_REQUEST_CONFLICT','REVIEW_REVISION_CONFLICT','REVIEW_BASE_CHANGED','REVIEW_SOURCE_CHANGED'):
        if code in message:
            return IngestionError(code, http_status=409)
    # SQL details, encrypted bytes and document bodies never reach the caller.
    return IngestionError('REVIEW_WRITE_INVALID', http_status=422)


async def write_review(tenant: Tenant, version_id: uuid.UUID, request: ReviewWriteIn) -> ReviewReceiptOut:
    source = await _read_raw(tenant, version_id)
    try:
        async with session_scope(role='f1_api', enterprise_id=tenant.enterprise_id, sub=tenant.sub) as session:
            receipt = (await session.execute(text('SELECT f1.probe_material_review_request(:version,:request,:sha)'),
                {'version': version_id, 'request': request.request_id, 'sha': request_digest(source, request)})).scalar_one()
            if receipt is None:
                payload = build_review_payload(source, request)
                receipt = (await session.execute(text('SELECT f1.write_material_review(CAST(:payload AS jsonb))'),
                    {'payload': canonical_json(payload).decode('ascii')})).scalar_one()
            from ..material_pipeline.coordinator import auto_pipeline_enabled
            if auto_pipeline_enabled():
                from ..material_pipeline.repository import register_delivery_in_session
                await register_delivery_in_session(session, tenant, version_id, rearm_terminal=True)
            await session.commit()
    except (DBAPIError, ValueError) as error:
        raise _write_error(error) from error
    return ReviewReceiptOut(**receipt)
