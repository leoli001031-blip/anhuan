"""Lease-bound encrypted OCR reuse. No source bytes or plaintext cross this API."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import uuid

import httpx

from .features.material_rag.security import encrypt_text, decrypt_text

MAX_WIRE_BYTES = 1_010_000


class OcrCacheError(RuntimeError):
    pass


def enabled():
    return os.environ.get('F1_OCR_RESULT_CACHE') == '1'


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')


def code_identity(*paths):
    return [hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in paths]


def cloud_identity(active, payload, backend, renderer_identity):
    return {'backend': backend, 'provider': active.provider, 'model': active.model,
        'dialect': active.dialect, 'endpoint_sha256': hashlib.sha256(active.base_url.encode()).hexdigest(),
        'request_sha256': hashlib.sha256(payload).hexdigest(), 'renderer': renderer_identity,
        'max_pages': active.max_pages, 'request_timeout': active.request_timeout_seconds,
        'total_timeout': active.total_timeout_seconds}


def cached_text(value, *, minimum):
    if (not isinstance(value, dict) or set(value) != {'text'} or not isinstance(value['text'], str)
        or not minimum <= sum(not char.isspace() for char in value['text'])
        or len(value['text']) > 100_000
        or any(ord(c) < 32 and c not in '\r\n\t' for c in value['text'])):
        raise OcrCacheError('OCR_CACHE_INVALID')
    value['text'].encode('utf-8', 'strict')
    return value['text']


@dataclass(frozen=True)
class Capability:
    kind: str
    job_id: uuid.UUID
    lease_token: uuid.UUID
    enterprise_id: uuid.UUID
    document_version_id: uuid.UUID


_current: ContextVar[Capability | None] = ContextVar('ocr_cache_capability', default=None)


@contextmanager
def task_scope(kind, job_id, token, enterprise_id, version_id):
    marker = _current.set(Capability(kind, job_id, token, enterprise_id, version_id))
    try:
        yield
    finally:
        _current.reset(marker)


def _exchange(request):
    try:
        with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
            with client.stream('POST', 'http://source-gateway:8080/ocr-cache', json=request) as response:
                if response.status_code != 200:
                    raise OcrCacheError('OCR_CACHE_UNAVAILABLE')
                raw = bytearray()
                for part in response.iter_bytes(65536):
                    if len(raw) + len(part) > MAX_WIRE_BYTES:
                        raise OcrCacheError('OCR_CACHE_INVALID')
                    raw.extend(part)
        return json.loads(raw)
    except OcrCacheError:
        raise
    except Exception:
        raise OcrCacheError('OCR_CACHE_UNAVAILABLE') from None


class Ticket:
    """One complete input identity; an unavailable or corrupt cache never hits."""
    def __init__(self, source_sha256, unit_no, identity):
        self.capability = _current.get() if enabled() else None
        if enabled() and self.capability is None:
            raise OcrCacheError('OCR_CACHE_CAPABILITY_REQUIRED')
        self.source_sha256 = source_sha256
        self.unit_no = unit_no
        self.input_sha256 = hashlib.sha256(canonical({
            'schema': 'ocr-result-cache-v1', 'epoch': os.environ.get('F1_OCR_CACHE_EPOCH', '1'),
            'source_sha256': source_sha256, 'unit_no': unit_no, 'processing': identity,
        })).hexdigest()

    def _aad(self, body_sha):
        c = self.capability
        return canonical({'schema': 'f1.ocr-result-cache.aad.v1',
            'enterprise_id': str(c.enterprise_id), 'document_version_id': str(c.document_version_id),
            'source_sha256': self.source_sha256, 'unit_no': self.unit_no,
            'input_sha256': self.input_sha256, 'body_sha256': body_sha})

    def _call(self, envelope=None):
        c = self.capability
        result = _exchange({'kind': c.kind, 'job_id': str(c.job_id), 'lease_token': str(c.lease_token),
            'document_version_id': str(c.document_version_id), 'source_sha256': self.source_sha256,
            'unit_no': self.unit_no, 'input_sha256': self.input_sha256, 'envelope': envelope})
        if result == {'status': 'capacity'}:
            return None
        if (result.get('status') not in ('hit', 'miss')
            or result.get('enterprise_id') != str(c.enterprise_id)
            or result.get('document_version_id') != str(c.document_version_id)
            or result.get('source_sha256') != self.source_sha256):
            raise OcrCacheError('OCR_CACHE_INVALID')
        if result['status'] == 'miss':
            return None
        try:
            e = result['envelope']
            raw = decrypt_text(bytes.fromhex(e['ciphertext_hex']), self._aad(e['body_sha256']), e['aad_sha256'])
            if hashlib.sha256(raw.encode()).hexdigest() != e['body_sha256']:
                raise ValueError
            value = json.loads(raw)
            if not isinstance(value, dict) or canonical(value).decode() != raw:
                raise ValueError
            return value
        except Exception:
            raise OcrCacheError('OCR_CACHE_INVALID') from None

    def load(self):
        return self._call() if self.capability else None

    def save(self, value):
        if not self.capability:
            return value
        raw = canonical(value)
        # Oversized valid results still run normally, without unsafe truncation.
        if len(raw) > 490_000:
            return value
        body_sha = hashlib.sha256(raw).hexdigest()
        ciphertext, aad_sha = encrypt_text(raw.decode(), self._aad(body_sha))
        # Concurrent calls adopt the first committed complete result.
        return self._call({'ciphertext_hex': ciphertext.hex(), 'aad_sha256': aad_sha,
                           'body_sha256': body_sha}) or value
