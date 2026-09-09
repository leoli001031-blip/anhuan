"""Private, bounded source reads authorized by one live database lease."""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from typing import Literal

import httpx
import psycopg
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from .database import _role_dsn
from . import storage

MAX_SOURCE_BYTES = 50 * 1024 * 1024


class SourceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['native', 'pdf-index']
    job_id: uuid.UUID
    lease_token: uuid.UUID


class SourceDenied(RuntimeError):
    pass


def gateway_enabled() -> bool:
    return os.environ.get('F1_TASK_SOURCE_GATEWAY') == '1'


def read_source(request: SourceRequest) -> bytes:
    """Keep source/job/member locks until the exact bytes pass validation."""
    dsn = _role_dsn('f1_source_reader').replace('postgresql+psycopg://', 'postgresql://', 1)
    with psycopg.connect(dsn, connect_timeout=5) as connection:
        connection.execute("SET LOCAL statement_timeout='8s'")
        connection.execute("SET LOCAL lock_timeout='3s'")
        source = connection.execute('SELECT f1.read_leased_task_source(%s,%s,%s)',
            (request.kind, request.job_id, request.lease_token)).fetchone()[0]
        if source is None:
            raise SourceDenied('TASK_SOURCE_DENIED')
        size = int(source['source_size'])
        if not 1 <= size <= MAX_SOURCE_BYTES:
            raise SourceDenied('TASK_SOURCE_DENIED')
        if source['storage_area'] == 'released':
            raw = storage.read_released_material_source(source['object_key'], source['source_sha256'], size)
        elif source['storage_area'] == 'quarantine':
            with storage.open_quarantine_source(source['object_key'], source['source_sha256'], size, source['source_etag']) as stream:
                raw = stream.read(size + 1)
        else:
            raise SourceDenied('TASK_SOURCE_DENIED')
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != source['source_sha256']:
            raise SourceDenied('TASK_SOURCE_DENIED')
        # A lock prevents reassignment but does not stop wall-clock expiry.
        if datetime.fromisoformat(source['lease_until']) <= datetime.now(timezone.utc):
            raise SourceDenied('TASK_SOURCE_DENIED')
        return raw


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(RequestValidationError)
async def invalid_request(_request, _error):
    return Response(status_code=404, headers={'Cache-Control': 'no-store'})


@app.post('/source')
def source_endpoint(request: SourceRequest):
    try:
        raw = read_source(request)
    except (SourceDenied, storage.StorageError) as error:
        transient = str(error) in {'SOURCE_OBJECT_STAT_FAILED', 'SOURCE_OBJECT_READ_FAILED', 'SOURCE_OBJECT_MISSING'}
        return Response(status_code=503 if transient else 404, headers={'Cache-Control': 'no-store'})
    except Exception:
        return Response(status_code=503, headers={'Cache-Control': 'no-store'})
    return Response(content=raw, media_type='application/octet-stream', headers={'Cache-Control': 'no-store'})


@app.get('/health')
def health():
    return Response(status_code=204, headers={'Cache-Control': 'no-store'})


def fetch_source(kind: str, job_id: uuid.UUID, token: uuid.UUID, sha256: str, size: int) -> bytes:
    """Workers send a capability, never an object key or caller-selected tenant."""
    if not gateway_enabled() or not 1 <= size <= MAX_SOURCE_BYTES:
        raise storage.StorageError('SOURCE_OBJECT_INVALID')
    request = SourceRequest(kind=kind, job_id=job_id, lease_token=token)
    # Internal fixed service address: redirects, proxies and arbitrary URL overrides
    # cannot forward the lease to a different destination.
    try:
        with httpx.Client(timeout=30, trust_env=False, follow_redirects=False) as client:
            with client.stream('POST', 'http://source-gateway:8080/source', json=request.model_dump(mode='json')) as response:
                if response.status_code != 200:
                    raise storage.StorageError('SOURCE_OBJECT_READ_FAILED' if response.status_code == 503 else 'SOURCE_OBJECT_INVALID')
                raw = bytearray()
                for block in response.iter_bytes(chunk_size=65536):
                    if len(raw) + len(block) > size:
                        raise storage.StorageError('SOURCE_OBJECT_INVALID')
                    raw.extend(block)
    except httpx.HTTPError:
        raise storage.StorageError('SOURCE_OBJECT_READ_FAILED') from None
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != sha256:
        raise storage.StorageError('SOURCE_OBJECT_INVALID')
    return bytes(raw)


from .ingestion_storage_gateway import router as ingestion_router
app.include_router(ingestion_router)
from .ocr_cache_gateway import router as ocr_cache_router
app.include_router(ocr_cache_router)
