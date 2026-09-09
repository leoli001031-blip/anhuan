"""Private ingestion object gateway; all object identities come from a live lease."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import uuid
from datetime import datetime, timezone

import httpx
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from . import storage
from .database import _role_dsn
from .ingestion_context import current_capability
from .features.p3.preview import PreviewFailure, PreviewResult, PreviewUnitArtifact, _canonical_preview, _unit_manifest

router = APIRouter(include_in_schema=False)
MAX_BUNDLE = 28 * 1024 * 1024


class Capability(BaseModel):
    model_config = ConfigDict(extra='forbid')
    delivery_id: uuid.UUID
    dispatch_token: uuid.UUID
    process_token: uuid.UUID | None = None


class PreviewBundle(Capability):
    manifest: dict
    artifacts: list[str] = Field(min_length=1, max_length=128)


def _live(source):
    if source is None or datetime.fromisoformat(source['lease_until']) <= datetime.now(timezone.utc):
        raise ValueError('INGESTION_STORAGE_DENIED')


def _connection():
    return psycopg.connect(_role_dsn('f1_source_reader').replace('postgresql+psycopg://', 'postgresql://', 1), connect_timeout=5)


def _source(connection, request, preview):
    connection.execute("SET LOCAL statement_timeout='8s'")
    connection.execute("SET LOCAL lock_timeout='3s'")
    source = connection.execute('SELECT f1.read_ingestion_task_source(%s,%s,%s,%s)',
        (request.delivery_id, request.dispatch_token, request.process_token, preview)).fetchone()[0]
    _live(source)
    return source


def read_source(request: Capability) -> bytes:
    with _connection() as connection:
        source = _source(connection, request, False)
        size = int(source['source_size'])
        if not 1 <= size <= 50 * 1024 * 1024:
            raise ValueError('INGESTION_STORAGE_DENIED')
        with storage.open_quarantine_source(source['object_key'], source['source_sha256'], size, source['source_etag']) as stream:
            raw = stream.read(size + 1)
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != source['source_sha256']:
            raise ValueError('INGESTION_STORAGE_DENIED')
        _live(source)
        return raw


def _validate_bundle(request: PreviewBundle, source):
    task = uuid.UUID(source['upload_task_id'])
    kinds = {'application/pdf': 'page_text',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'page_text',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'sheet_grid', 'image/jpeg': 'image'}
    kind = kinds[source['content_type']]
    if set(request.manifest) != {'kind', 'units'} or request.manifest['kind'] != kind:
        raise ValueError('INGESTION_STORAGE_DENIED')
    metadata = request.manifest['units']
    if not isinstance(metadata, list) or len(metadata) != len(request.artifacts) or (kind == 'image' and len(metadata) != 1):
        raise ValueError('INGESTION_STORAGE_DENIED')
    units = []
    maximum = 20 * 1024 * 1024 if kind == 'image' else 256 * 1024
    for ordinal, (meta, encoded) in enumerate(zip(metadata, request.artifacts), 1):
        if len(encoded) > ((maximum + 2) // 3) * 4:
            raise ValueError('INGESTION_STORAGE_DENIED')
        raw = base64.b64decode(encoded, validate=True)
        sha = hashlib.sha256(raw).hexdigest()
        content_type = 'image/jpeg' if kind == 'image' else 'application/json'
        unit_kind = {'page_text':'page_text', 'sheet_grid':'worksheet_grid', 'image':'image'}[kind]
        # Exact metadata shape excludes caller-selected paths and task identities.
        fields = {'id','kind','ordinal','label','content_type','size_bytes','width_px','height_px','row_count','column_count','sha256'}
        if not isinstance(meta, dict) or set(meta) != fields or not isinstance(meta['label'], str) or len(meta['label']) > 512:
            raise ValueError('INGESTION_STORAGE_DENIED')
        if any(value is not None and (type(value) is not int or value < 0 or value > 1000000)
               for value in (meta[k] for k in ('width_px','height_px','row_count','column_count'))):
            raise ValueError('INGESTION_STORAGE_DENIED')
        if not raw or len(raw) > maximum or type(meta['ordinal']) is not int or type(meta['size_bytes']) is not int:
            raise ValueError('INGESTION_STORAGE_DENIED')
        unit = PreviewUnitArtifact(id=str(uuid.uuid5(task, f'{ordinal}:{content_type}:{sha}')),
            kind=unit_kind, ordinal=ordinal, label=meta['label'], sha256=sha, content_type=content_type, content=raw,
            **{key: meta[key] for key in ('width_px','height_px','row_count','column_count')})
        if meta != _unit_manifest(unit):
            raise ValueError('INGESTION_STORAGE_DENIED')
        units.append(unit)
    manifest = _canonical_preview(request.manifest)
    if len(manifest) > 256 * 1024 or (kind != 'image' and len(manifest) + sum(len(u.content) for u in units) > maximum):
        raise ValueError('INGESTION_STORAGE_DENIED')
    return task, units, manifest


def store_preview(request: PreviewBundle):
    with _connection() as connection:
        source = _source(connection, request, True)
        task, units, manifest = _validate_bundle(request, source)
        manifest_sha = hashlib.sha256(manifest).hexdigest()
        objects = [(u.id, u.content_type, u.content, u.sha256) for u in units]
        objects.append((str(uuid.uuid5(task, 'manifest:' + manifest_sha)), 'application/json', manifest, manifest_sha))
        for unit_id, content_type, raw, sha in objects:
            _live(source)
            storage.store_ingestion_preview_unit(task_id=task, unit_id=unit_id, content=raw, content_type=content_type)
            loaded = storage.read_ingestion_preview_artifact(task_id=task, unit_id=unit_id,
                content_type=content_type, expected_sha256=sha, expected_size=len(raw))
            if loaded != raw:
                raise ValueError('INGESTION_STORAGE_DENIED')
        _live(source)


async def _endpoint(request: Request, preview: bool):
    try:
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > (MAX_BUNDLE if preview else 256):
                raise ValueError('INGESTION_STORAGE_DENIED')
            body.extend(chunk)
        command = (PreviewBundle if preview else Capability).model_validate_json(bytes(body))
        result = await asyncio.to_thread(store_preview if preview else read_source, command)
    except (ValueError, TypeError, KeyError, PreviewFailure):
        return Response(status_code=404, headers={'Cache-Control':'no-store'})
    except storage.StorageError as error:
        transient = str(error) in {'SOURCE_OBJECT_STAT_FAILED', 'SOURCE_OBJECT_READ_FAILED', 'SOURCE_OBJECT_MISSING'}
        return Response(status_code=503 if transient else 404, headers={'Cache-Control':'no-store'})
    except Exception:
        return Response(status_code=503, headers={'Cache-Control':'no-store'})
    return Response(content=result if not preview else None, status_code=204 if preview else 200,
        media_type='application/octet-stream' if not preview else None, headers={'Cache-Control':'no-store'})


@router.post('/ingestion/source')
async def source_endpoint(request: Request):
    return await _endpoint(request, False)


@router.post('/ingestion/preview')
async def preview_endpoint(request: Request):
    return await _endpoint(request, True)


def _command(process_token):
    cap = current_capability()
    if cap is None:
        raise storage.StorageError('SOURCE_OBJECT_INVALID')
    return Capability(delivery_id=cap.delivery_id, dispatch_token=cap.dispatch_token, process_token=process_token).model_dump(mode='json')


def fetch_source(process_token, sha256: str, size: int) -> bytes:
    if not 1 <= size <= 50 * 1024 * 1024:
        raise storage.StorageError('SOURCE_OBJECT_INVALID')
    try:
        with httpx.Client(timeout=90, trust_env=False, follow_redirects=False) as client:
            with client.stream('POST', 'http://source-gateway:8080/ingestion/source', json=_command(process_token)) as response:
                if response.status_code != 200:
                    raise storage.StorageError('SOURCE_OBJECT_READ_FAILED' if response.status_code == 503 else 'SOURCE_OBJECT_INVALID')
                raw = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    if len(raw) + len(chunk) > size:
                        raise storage.StorageError('SOURCE_OBJECT_INVALID')
                    raw.extend(chunk)
    except httpx.HTTPError:
        raise storage.StorageError('SOURCE_OBJECT_READ_FAILED') from None
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != sha256:
        raise storage.StorageError('SOURCE_OBJECT_INVALID')
    return bytes(raw)


def send_preview(process_token, result: PreviewResult):
    command = {**_command(process_token), 'manifest': result.payload,
        'artifacts': [base64.b64encode(unit.content).decode('ascii') for unit in result.units]}
    encoded = json.dumps(command, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(encoded) > MAX_BUNDLE:
        raise storage.StorageError('PREVIEW_SIZE_INVALID')
    try:
        with httpx.Client(timeout=90, trust_env=False, follow_redirects=False) as client:
            with client.stream('POST', 'http://source-gateway:8080/ingestion/preview', content=encoded,
                headers={'Content-Type':'application/json'}) as response:
                if response.status_code != 204:
                    raise storage.StorageError('SOURCE_OBJECT_READ_FAILED' if response.status_code == 503 else 'PREVIEW_IDENTITY_MISMATCH')
    except httpx.HTTPError:
        raise storage.StorageError('SOURCE_OBJECT_READ_FAILED') from None
