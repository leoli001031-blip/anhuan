"""QA endpoint: tenant-scoped evidence QA with idempotent, encrypted storage."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from ... import qa_service
from ...auth import Tenant, current_tenant, require_role

router = APIRouter()


class QaRequest(BaseModel):
    question: str
    enterprise_id: str | None = None
    request_id: str | None = None


class QaResponse(BaseModel):
    answer: str | None
    citations: list[dict]
    refusal_reason: str | None = None
    request_id: str | None = None


@router.post("", response_model=QaResponse)
async def ask(
    body: QaRequest,
    response: Response,
    user: dict = Depends(require_role("super_admin", "enterprise_admin", "plant_admin", "partner")),
) -> QaResponse:
    if not body.question or not body.question.strip():
        raise HTTPException(status_code=422, detail="EMPTY_QUESTION")
    enterprise_id: uuid.UUID | None = None
    if body.enterprise_id:
        try:
            enterprise_id = uuid.UUID(body.enterprise_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="enterprise not found") from None
    tenant = await current_tenant(user, enterprise_id)
    try:
        request_id = uuid.UUID(body.request_id) if body.request_id else uuid.uuid4()
    except ValueError:
        raise HTTPException(status_code=422, detail="REQUEST_ID_INVALID") from None

    try:
        outcome = await qa_service.ask_question(body.question.strip(), request_id, tenant)
    except qa_service.RequestIdConflict:
        raise HTTPException(status_code=409, detail="REQUEST_ID_CONFLICT") from None
    except qa_service.RequestInProgress:
        response.status_code = 202
        return QaResponse(
            answer=None,
            citations=[],
            refusal_reason="REQUEST_IN_PROGRESS",
            request_id=str(request_id),
        )
    except qa_service.RequestOwnershipLost:
        raise HTTPException(status_code=409, detail="REQUEST_OWNERSHIP_LOST") from None
    return QaResponse(**outcome.to_dict())


__all__ = ("router", "QaRequest", "QaResponse")
