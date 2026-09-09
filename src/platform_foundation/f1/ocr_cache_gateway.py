"""Private OCR cache endpoint; the database derives all tenant/source scope."""
import uuid
from typing import Literal

import psycopg
from psycopg.types.json import Jsonb
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from .database import _role_dsn
from .ocr_cache import MAX_WIRE_BYTES

router = APIRouter()


class CacheRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['native', 'pdf-index', 'pdf-analysis']
    job_id: uuid.UUID
    lease_token: uuid.UUID
    document_version_id: uuid.UUID
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    unit_no: int = Field(ge=1, le=128)
    input_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    envelope: dict | None = None


def exchange_cache(request):
    dsn = _role_dsn('f1_source_reader').replace('postgresql+psycopg://', 'postgresql://', 1)
    with psycopg.connect(dsn, connect_timeout=5) as connection:
        connection.execute("SET LOCAL statement_timeout='8s'")
        connection.execute("SET LOCAL lock_timeout='3s'")
        return connection.execute('SELECT f1.leased_ocr_cache(%s,%s,%s,%s,%s,%s,%s,%s)',
            (request.kind, request.job_id, request.lease_token, request.document_version_id,
             request.source_sha256, request.unit_no, request.input_sha256,
             Jsonb(request.envelope) if request.envelope is not None else None)).fetchone()[0]


@router.post('/ocr-cache')
async def cache_endpoint(request: Request):
    raw = bytearray()
    try:
        async for part in request.stream():
            if len(raw) + len(part) > MAX_WIRE_BYTES:
                return Response(status_code=404, headers={'Cache-Control': 'no-store'})
            raw.extend(part)
        parsed = CacheRequest.model_validate_json(raw)
    except Exception:
        return Response(status_code=404, headers={'Cache-Control': 'no-store'})
    finally:
        raw[:] = b'\0' * len(raw)
        raw.clear()
    try:
        from starlette.concurrency import run_in_threadpool
        result = await run_in_threadpool(exchange_cache, parsed)
    except Exception:
        return Response(status_code=503, headers={'Cache-Control': 'no-store'})
    if result is None:
        return Response(status_code=404, headers={'Cache-Control': 'no-store'})
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})
