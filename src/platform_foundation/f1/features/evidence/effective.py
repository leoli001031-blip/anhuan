"""One authenticated effective corpus for local retrieval and report freezing."""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..material_rag.security import decrypt_text, unit_aad_for_identity
from .contracts import SourceLocatorV2, parse_locator
from .envelopes import decrypt_fragment
from .review import decrypt_review_fragment

from ..material_rag.contracts import MAX_CORPUS_FRAGMENTS
MAX_CORPUS_BYTES = 24_000_000


@dataclass(frozen=True, slots=True)
class EffectiveFragment:
    id: uuid.UUID
    evidence_revision_id: uuid.UUID
    ordinal: int
    locator: SourceLocatorV2
    body_sha256: str
    body: str = field(repr=False)
    entry_kind: str = 'text'
    field_name: str | None = None


@dataclass(frozen=True, slots=True)
class EffectiveSource:
    enterprise_id: uuid.UUID
    knowledge_scope_id: uuid.UUID
    document_record_id: uuid.UUID
    document_version_id: uuid.UUID
    document_name: str
    version_number: int
    scope_kind: str
    source_sha256: str
    source_format: str
    evidence_kind: str
    evidence_revision_id: uuid.UUID | None
    fragments: tuple[EffectiveFragment, ...]


def decode_source(source: dict) -> EffectiveSource:
    context = {k: uuid.UUID(str(source[k])) for k in (
        'enterprise_id','knowledge_scope_id','document_record_id','document_version_id')}
    revision = uuid.UUID(source['evidence_revision_id']) if source['evidence_revision_id'] else None
    kind = source['evidence_kind']
    if kind not in {'review','extraction','revoked'} or source['scope_kind'] not in {'service_provider','client'}:
        raise ValueError('EFFECTIVE_IDENTITY_INVALID')
    rows = source['fragments']
    if kind == 'revoked' and rows or bool(rows) != source['available'] or rows and revision is None:
        raise ValueError('EFFECTIVE_IDENTITY_INVALID')
    fragments = []
    seen = set()
    for order, row in enumerate(rows):
        if any(uuid.UUID(str(row[k])) != context[k] for k in context) or row['source_sha256'] != source['source_sha256']:
            raise ValueError('EFFECTIVE_IDENTITY_INVALID')
        locator = parse_locator(row['locator'])
        if locator.source_format != source['source_format']:
            raise ValueError('EFFECTIVE_IDENTITY_INVALID')
        if kind == 'review':
            if uuid.UUID(row['review_revision_id']) != revision:
                raise ValueError('EFFECTIVE_IDENTITY_INVALID')
            body = decrypt_review_fragment(row)
        elif source['source_format'] != 'pdf':
            if uuid.UUID(row['extraction_revision_id']) != revision:
                raise ValueError('EFFECTIVE_IDENTITY_INVALID')
            body = decrypt_fragment({**row, 'body_ciphertext': bytes.fromhex(row['body_ciphertext_hex'])})
        else:
            if locator.page_number != row['page_number']:
                raise ValueError('EFFECTIVE_IDENTITY_INVALID')
            aad = unit_aad_for_identity(**context, unit_id=uuid.UUID(row['id']),
                **{k: row[k] for k in ('source_sha256','page_number','ordinal','parser_version','body_sha256')})
            body = decrypt_text(bytes.fromhex(row['body_ciphertext_hex']), aad, row['body_aad_sha256'])
        if hashlib.sha256(body.encode('utf-8')).hexdigest() != row['body_sha256'] or row['id'] in seen:
            raise ValueError('EFFECTIVE_IDENTITY_INVALID')
        seen.add(row['id'])
        # Empty positions remain part of the immutable base, never search hits.
        if body.strip():
            fragments.append(EffectiveFragment(id=uuid.UUID(row['id']), evidence_revision_id=revision,
                ordinal=order+1, locator=locator, body_sha256=row['body_sha256'], body=body,
                entry_kind=row.get('entry_kind','text'), field_name=row.get('field_name')))
    return EffectiveSource(**context, **{k: source[k] for k in (
        'document_name','version_number','scope_kind','source_sha256','source_format','evidence_kind')},
        evidence_revision_id=revision, fragments=tuple(fragments))


async def load_effective_sources(session: AsyncSession, scope_ids: tuple[uuid.UUID, ...]) -> tuple[EffectiveSource, ...]:
    rows = (await session.execute(text('SELECT * FROM f1.read_effective_material_sources(CAST(:scopes AS uuid[]))'),
        {'scopes': list(scope_ids)})).scalars().all()
    sources = tuple(decode_source(row) for row in rows)
    if any(s.knowledge_scope_id not in scope_ids for s in sources):
        raise ValueError('EFFECTIVE_SCOPE_INVALID')
    return sources
