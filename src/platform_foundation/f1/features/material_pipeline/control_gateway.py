"""Private pipeline continuation transport; no caller-selected business scope."""
from __future__ import annotations

import asyncio
import os
import uuid

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, ValidationError

from . import repository

PATH = '/internal/material-pipeline/run'
MAX_REQUEST_BYTES = 256
router = APIRouter()


class ContinuationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    delivery_id: uuid.UUID
    dispatch_token: uuid.UUID


def gateway_enabled() -> bool:
    return os.environ.get('F1_PIPELINE_COORDINATOR_GATEWAY') == '1'


def _response(status: int) -> Response:
    return Response(status_code=status, headers={'Cache-Control': 'no-store'})


@router.post(PATH, include_in_schema=False)
async def continue_pipeline(request: Request) -> Response:
    if not gateway_enabled():
        return _response(404)
    # Parse manually so validation failures cannot echo a bearer capability
    # through FastAPI's normal 422 response. Keep the complete input bounded.
    raw = bytearray()
    try:
        async for block in request.stream():
            if len(raw) + len(block) > MAX_REQUEST_BYTES:
                return _response(404)
            raw.extend(block)
        command = ContinuationRequest.model_validate_json(bytes(raw))
    except (ValidationError, ValueError):
        return _response(404)
    try:
        claim = await repository.read_delivery_claim(
            command.delivery_id, command.dispatch_token
        )
        if claim is None:
            return _response(404)
        from .worker import _run_durable_delivery

        # This is the already-trusted API control plane. Parsing, OCR and
        # report generation stay in their own workers. A timed-out request
        # leaves durable state for dispatcher recovery.
        async with asyncio.timeout(120):
            await _run_durable_delivery(command.delivery_id, command.dispatch_token)
    except Exception:
        return _response(503)
    return _response(204)


def forward_delivery(delivery_id: uuid.UUID, dispatch_token: uuid.UUID) -> None:
    if not gateway_enabled():
        raise RuntimeError('MATERIAL_PIPELINE_GATEWAY_DISABLED')
    command = ContinuationRequest(delivery_id=delivery_id, dispatch_token=dispatch_token)
    try:
        # Fixed Compose service, no proxy, URL override or redirect. The
        # request never contains a tenant, version, actor or executable name.
        with httpx.Client(timeout=130, trust_env=False, follow_redirects=False) as client:
            with client.stream('POST', 'http://api:8001' + PATH,
                               json=command.model_dump(mode='json')) as response:
                if response.status_code in {204, 404}:
                    # Expired/finished deliveries need no RQ retry. PostgreSQL
                    # alone determines whether a new dispatch is required.
                    return
    except httpx.HTTPError:
        pass
    raise RuntimeError('MATERIAL_PIPELINE_GATEWAY_UNAVAILABLE')
