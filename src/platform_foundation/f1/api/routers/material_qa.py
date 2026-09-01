"""Scope-derived material evidence retrieval without client-selected datasets."""
from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from ... import qa_service
from ...auth import Tenant, tenant_from_header
from ...features import material_rag

router = APIRouter()


def _aeco_audience_enabled() -> bool:
    return (
        os.environ.get("F1_LOCAL_ENGINEERING") == "1"
        and os.environ.get("F1_MATERIAL_ANALYSIS_REPORT_LOCAL") == "1"
    )


class MaterialQaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    request_id: uuid.UUID
    client_account_id: uuid.UUID | None = None


class MaterialCitation(BaseModel):
    canonical_unit_id: uuid.UUID
    document_record_id: uuid.UUID
    document_version_id: uuid.UUID
    document_name: str = Field(min_length=1, max_length=200)
    version_number: int = Field(ge=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_number: int = Field(ge=1)
    body_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snippet: str = Field(min_length=1, max_length=320)


class MaterialQaResponse(BaseModel):
    answer: str | None
    citations: list[MaterialCitation]
    refusal_reason: str | None = None
    request_id: uuid.UUID


@router.post("", response_model=MaterialQaResponse)
async def ask_material_question(
    body: MaterialQaRequest,
    response: Response,
    tenant: Tenant = Depends(tenant_from_header),
) -> MaterialQaResponse:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="EMPTY_QUESTION")
    try:
        if tenant.role in {"super_admin", "enterprise_admin"}:
            context = await material_rag.derive_retrieval_context(
                tenant, body.client_account_id
            )
        elif _aeco_audience_enabled():
            if body.client_account_id is not None:
                raise material_rag.MaterialRagContextNotFound(
                    "MATERIAL_CONTEXT_NOT_FOUND"
                )
            context = await material_rag.derive_audience_retrieval_context(tenant)
        elif tenant.role == "plant_admin":
            # Preserve the f1_0014 provider-side contract when the f1_0020
            # client-audience bridge is not enabled/migrated.
            context = await material_rag.derive_retrieval_context(
                tenant, body.client_account_id
            )
        else:
            raise HTTPException(status_code=403, detail="ROLE_REQUIRED")
        outcome = await qa_service.ask_material_question(
            question, body.request_id, tenant, context
        )
    except (
        material_rag.MaterialRagContextNotFound,
        material_rag.MaterialRagForbidden,
    ):
        raise HTTPException(status_code=404, detail="MATERIAL_CONTEXT_NOT_FOUND") from None
    except material_rag.MaterialRagUnavailable:
        raise HTTPException(status_code=503, detail="MATERIAL_RAG_UNAVAILABLE") from None
    except qa_service.RequestIdConflict:
        raise HTTPException(status_code=409, detail="REQUEST_ID_CONFLICT") from None
    except qa_service.RequestInProgress:
        response.status_code = 202
        return MaterialQaResponse(
            answer=None,
            citations=[],
            refusal_reason="REQUEST_IN_PROGRESS",
            request_id=body.request_id,
        )
    except qa_service.RequestOwnershipLost:
        raise HTTPException(status_code=409, detail="REQUEST_OWNERSHIP_LOST") from None
    return MaterialQaResponse.model_validate(outcome.to_dict())


__all__ = (
    "router",
    "MaterialQaRequest",
    "MaterialQaResponse",
)
