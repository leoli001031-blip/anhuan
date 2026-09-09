"""Append-only human review contracts and authenticated encrypted fragments.

Review bodies remain separate from original PDF v1/native v2 evidence. A
confirmed review covers every original position; a revoke contains no body.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, model_validator

from ..material_intake.contracts import MATERIAL_FIELD_NAMES
from ..material_rag.security import decrypt_text, encrypt_text
from .contracts import SourceLocatorV2, canonical_json, parse_locator, _sha

REVISION_NAMESPACE = uuid.UUID('55b7ce1a-7b14-5ba8-84f0-fcc0d495ba66')
FRAGMENT_NAMESPACE = uuid.UUID('d5949baf-21ee-543c-822e-ea9a3d92b233')
DOMAIN = b'anhuan.material.review.v1\x00'


class ReviewTextIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_fragment_id: uuid.UUID
    text: StrictStr = Field(max_length=2_000_000)


class ReviewFieldIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_fragment_id: uuid.UUID
    field_name: StrictStr
    text: StrictStr = Field(max_length=4096)

    @model_validator(mode='after')
    def valid_field(self):
        if self.field_name not in MATERIAL_FIELD_NAMES:
            raise ValueError('REVIEW_FIELD_INVALID')
        return self


class ReviewWriteIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: uuid.UUID
    expected_review_revision_id: uuid.UUID | None
    action: Literal['confirm', 'revoke']
    base_revision_id: uuid.UUID | None = None
    base_manifest_sha256: StrictStr | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    checked_against_source: StrictBool = False
    texts: list[ReviewTextIn] = Field(default_factory=list, max_length=20_000)
    fields: list[ReviewFieldIn] = Field(default_factory=list, max_length=15)

    @model_validator(mode='after')
    def valid_review(self):
        if self.action == 'confirm':
            if not self.checked_against_source or not self.base_revision_id or not self.base_manifest_sha256 or not self.texts:
                raise ValueError('REVIEW_CONFIRMATION_REQUIRED')
        elif self.texts or self.fields or self.base_revision_id or self.base_manifest_sha256 or self.checked_against_source or not self.expected_review_revision_id:
            raise ValueError('REVIEW_REVOKE_INVALID')
        if (len({x.base_fragment_id for x in self.texts}) != len(self.texts)
                or len({x.field_name for x in self.fields}) != len(self.fields)
                or sum(len(x.text) for x in [*self.texts, *self.fields]) > 2_000_000
                or any('\x00' in x.text or any(0xd800 <= ord(c) <= 0xdfff for c in x.text) for x in [*self.texts, *self.fields])):
            raise ValueError('REVIEW_CONTENT_INVALID')
        return self


def revision_id(enterprise_id: uuid.UUID, request_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(REVISION_NAMESPACE, f'{enterprise_id}\x00{request_id}')


@dataclass(frozen=True, slots=True)
class ReviewFragmentIdentity:
    enterprise_id: uuid.UUID
    knowledge_scope_id: uuid.UUID
    document_record_id: uuid.UUID
    document_version_id: uuid.UUID
    review_revision_id: uuid.UUID
    base_revision_id: uuid.UUID
    base_fragment_id: uuid.UUID
    base_manifest_sha256: str
    source_sha256: str
    entry_kind: str
    field_name: str | None
    ordinal: int
    locator: SourceLocatorV2
    body_sha256: str

    def __post_init__(self):
        if (not all(type(getattr(self, k)) is uuid.UUID for k in (
            'enterprise_id','knowledge_scope_id','document_record_id','document_version_id',
            'review_revision_id','base_revision_id','base_fragment_id'))
            or not all(_sha(getattr(self, k)) for k in ('base_manifest_sha256','source_sha256','body_sha256'))
            or type(self.ordinal) is not int or not 0 <= self.ordinal < 20015
            or not ((self.entry_kind == 'text' and self.field_name is None)
                or (self.entry_kind == 'field' and self.field_name in MATERIAL_FIELD_NAMES))):
            raise ValueError('REVIEW_IDENTITY_INVALID')
        parse_locator(self.locator.to_dict())

    def aad(self) -> bytes:
        values = {k: str(getattr(self, k)) for k in (
            'enterprise_id','knowledge_scope_id','document_record_id','document_version_id',
            'review_revision_id','base_revision_id','base_fragment_id')}
        values.update(schema_version=1, base_manifest_sha256=self.base_manifest_sha256,
            source_sha256=self.source_sha256, source_format=self.locator.source_format,
            entry_kind=self.entry_kind, field_name=self.field_name, ordinal=self.ordinal,
            locator=self.locator.to_dict(), locator_sha256=self.locator.sha256,
            body_sha256=self.body_sha256)
        return DOMAIN + canonical_json(values)

    @property
    def id(self) -> uuid.UUID:
        return uuid.uuid5(FRAGMENT_NAMESPACE, self.aad().decode('ascii'))


def build_review_payload(source: dict, request: ReviewWriteIn) -> dict:
    """Build using a freshly authorized base; the SQL writer rechecks it under locks."""
    eid = uuid.UUID(str(source['enterprise_id']))
    rid = revision_id(eid, request.request_id)
    entries = []
    if request.action == 'confirm':
        if (str(request.base_revision_id) != str(source['base_revision_id'])
            or request.base_manifest_sha256 != source['base_manifest_sha256']):
            raise ValueError('REVIEW_BASE_CHANGED')
        base = source['base_fragments']
        if [str(x.base_fragment_id) for x in request.texts] != [str(x['id']) for x in base]:
            raise ValueError('REVIEW_COVERAGE_INVALID')
        locations = {str(x['id']): parse_locator(x['locator']) for x in base}
        for item in [*request.texts, *sorted(request.fields, key=lambda x: x.field_name)]:
            locator = locations.get(str(item.base_fragment_id))
            if locator is None:
                raise ValueError('REVIEW_FIELD_LOCATION_INVALID')
            if locator.source_format != source['source_format']:
                raise ValueError('REVIEW_IDENTITY_INVALID')
            identity = ReviewFragmentIdentity(eid,
                *(uuid.UUID(str(source[k])) for k in ('knowledge_scope_id','document_record_id','document_version_id')),
                rid, request.base_revision_id, item.base_fragment_id,
                request.base_manifest_sha256, source['source_sha256'],
                'field' if isinstance(item, ReviewFieldIn) else 'text',
                item.field_name if isinstance(item, ReviewFieldIn) else None,
                len(entries), locator, hashlib.sha256(item.text.encode('utf-8')).hexdigest())
            ciphertext, aad_hash = encrypt_text(item.text, identity.aad())
            entries.append(dict(id=str(identity.id), base_fragment_id=str(item.base_fragment_id),
                entry_kind=identity.entry_kind, field_name=identity.field_name, ordinal=identity.ordinal,
                locator=locator.to_dict(), locator_sha256=locator.sha256, body_sha256=identity.body_sha256,
                character_count=len(item.text), has_nonblank_text=bool(item.text.strip()),
                body_ciphertext_hex=ciphertext.hex(), body_aad_sha256=aad_hash))
    payload = dict(request_id=str(request.request_id), revision_id=str(rid),
        document_version_id=str(source['document_version_id']), source_sha256=source['source_sha256'],
        action=request.action, expected_review_revision_id=str(request.expected_review_revision_id) if request.expected_review_revision_id else None,
        base_revision_id=str(request.base_revision_id) if request.base_revision_id else None,
        base_manifest_sha256=request.base_manifest_sha256, checked_against_source=request.checked_against_source,
        fragments=entries)
    payload['request_sha256'] = semantic_hash(payload)
    if len(canonical_json(payload)) > 20_000_000:
        raise ValueError('REVIEW_PAYLOAD_LIMIT')
    return payload


def semantic_hash(payload: dict) -> str:
    stable = {k: v for k, v in payload.items() if k not in ('request_sha256', 'fragments')}
    stable['fragments'] = [{k: f[k] for k in ('base_fragment_id','entry_kind','field_name','ordinal','body_sha256')} for f in payload['fragments']]
    return hashlib.sha256(canonical_json(stable)).hexdigest()


def decrypt_review_fragment(row: dict) -> str:
    identity = ReviewFragmentIdentity(
        *(uuid.UUID(str(row[k])) for k in ('enterprise_id','knowledge_scope_id','document_record_id',
            'document_version_id','review_revision_id','base_revision_id','base_fragment_id')),
        *(row[k] for k in ('base_manifest_sha256','source_sha256','entry_kind','field_name','ordinal')),
        parse_locator(row['locator']), row['body_sha256'])
    if str(identity.id) != str(row['id']) or identity.locator.sha256 != row['locator_sha256']:
        raise ValueError('REVIEW_IDENTITY_INVALID')
    body = decrypt_text(bytes.fromhex(row['body_ciphertext_hex']), identity.aad(), row['body_aad_sha256'])
    if (hashlib.sha256(body.encode('utf-8')).hexdigest() != row['body_sha256']
        or len(body) != row['character_count'] or bool(body.strip()) != row['has_nonblank_text']):
        raise ValueError('REVIEW_BODY_INVALID')
    return body


def request_digest(source: dict, request: ReviewWriteIn) -> str:
    items = [*request.texts, *sorted(request.fields, key=lambda x: x.field_name)]
    projection = dict(request_id=str(request.request_id),
        revision_id=str(revision_id(uuid.UUID(str(source['enterprise_id'])), request.request_id)),
        document_version_id=str(source['document_version_id']), source_sha256=source['source_sha256'],
        action=request.action, expected_review_revision_id=str(request.expected_review_revision_id) if request.expected_review_revision_id else None,
        base_revision_id=str(request.base_revision_id) if request.base_revision_id else None,
        base_manifest_sha256=request.base_manifest_sha256, checked_against_source=request.checked_against_source,
        fragments=[dict(base_fragment_id=str(item.base_fragment_id), ordinal=n,
            entry_kind='field' if isinstance(item, ReviewFieldIn) else 'text',
            field_name=item.field_name if isinstance(item, ReviewFieldIn) else None,
            body_sha256=hashlib.sha256(item.text.encode('utf-8')).hexdigest()) for n,item in enumerate(items)])
    return semantic_hash(projection)
