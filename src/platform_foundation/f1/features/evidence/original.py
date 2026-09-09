"""Authorized citation positions rendered from hash-verified original bytes."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import uuid

from sqlalchemy import text

from ... import storage
from ...auth import Tenant
from ...database import session_scope
from ..p3.contracts import IngestionError
from .contracts import parse_locator, format_location
from .repository import native_extraction_enabled


async def read_original(tenant: Tenant, *, citation_id: uuid.UUID | None = None,
                        version_id: uuid.UUID | None = None, fragment_id: uuid.UUID | None = None,
                        revision_id: uuid.UUID | None = None, review_base: bool = False) -> tuple[dict, bytes]:
    if not native_extraction_enabled():
        raise IngestionError('P3_DOCUMENT_NOT_FOUND', http_status=404)
    async with session_scope(role='f1_api', enterprise_id=tenant.enterprise_id, sub=tenant.sub) as session:
        if review_base and citation_id is not None:
            raise IngestionError('P3_DOCUMENT_NOT_FOUND', http_status=404)
        query = 'SELECT f1.read_review_original(:version,:fragment,:revision)' if review_base else 'SELECT f1.read_citation_original(:citation,:version,:fragment,:revision)'
        row = (await session.execute(text(query),
            {'citation': citation_id, 'version': version_id, 'fragment': fragment_id, 'revision': revision_id})).scalar_one()
        if row is None:
            raise IngestionError('P3_DOCUMENT_NOT_FOUND', http_status=404)
        try:
            raw = await asyncio.to_thread(storage.read_released_material_source,
                row['object_key'], row['source_sha256'], row['source_size'])
            # Retain the source/member locks through the actual read. Storage
            # adapters and later renderer code never supply authorization.
            if len(raw) != row['source_size'] or hashlib.sha256(raw).hexdigest() != row['source_sha256']:
                raise ValueError('CITATION_ORIGINAL_CHANGED')
        except Exception:
            raise IngestionError('CITATION_ORIGINAL_UNAVAILABLE', http_status=503) from None
    return row, raw


def render_original(row: dict, raw: bytes) -> dict:
    locator = parse_locator(row['locator'])
    if locator.source_format != row['source_format']:
        raise ValueError('CITATION_ORIGINAL_LOCATION_INVALID')
    result = {'document_version_id': row['document_version_id'], 'source_sha256': row['source_sha256'],
        'locator': locator.to_dict(), 'location': format_location(locator), 'original_text': None, 'image': None}
    if locator.source_format in {'docx', 'xlsx'}:
        from .docx_native import extract_docx
        from .xlsx_native import extract_xlsx
        parsed = (extract_docx if locator.source_format == 'docx' else extract_xlsx)(raw, expected_sha256=row['source_sha256'])
        if parsed.coverage_state != 'complete':
            raise ValueError('CITATION_ORIGINAL_UNAVAILABLE')
        matches = [b for b in parsed.blocks if b.locator == locator]
        if len(matches) != 1:
            raise ValueError('CITATION_ORIGINAL_LOCATION_INVALID')
        result['original_text'] = matches[0].text
    else:
        if locator.source_format == 'pdf':
            from ..material_intake.pdf_renderer import render_pdf_page
            image = render_pdf_page(raw, locator.page_number)
            mime = 'image/jpeg'
        else:
            from ..material_intake.jpeg_renderer import render_jpeg
            rendered = render_jpeg(raw, expected_sha256=row['source_sha256'])
            if (rendered.source_width, rendered.source_height, rendered.width, rendered.height,
                rendered.image_sha256, rendered.exif_orientation) != (locator.source_width, locator.source_height,
                locator.rendered_width, locator.rendered_height, locator.rendered_sha256, locator.exif_orientation):
                raise ValueError('CITATION_ORIGINAL_LOCATION_INVALID')
            image, mime = rendered.image, 'image/jpeg'
        result['image'] = 'data:' + mime + ';base64,' + base64.b64encode(image).decode('ascii')
    return result


async def original_view(tenant: Tenant, **identity) -> dict:
    row, raw = await read_original(tenant, **identity)
    try:
        return await asyncio.to_thread(render_original, row, raw)
    except Exception:
        raise IngestionError('CITATION_ORIGINAL_UNAVAILABLE', http_status=503) from None
