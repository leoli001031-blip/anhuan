"""Minimal local HTTP surface for the closed F0-D upload foundation."""

from __future__ import annotations

import uuid

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from .auth import (
    AuthenticationError,
    SessionContext,
    authenticate_local_session,
)
from .database import database_health
from .governance import GovernanceDenied
from .service import PlatformError, PlatformService


_MAX_LOCAL_BODY_BYTES = 128 * 1024 * 1024


class CreateUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: uuid.UUID


def create_app(service: PlatformService) -> FastAPI:
    app = FastAPI(
        title="F0-D Local Fixture Foundation",
        version="0.1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.exception_handler(AuthenticationError)
    async def authentication_error(
        _request: Request, error: AuthenticationError
    ) -> JSONResponse:
        return JSONResponse(status_code=401, content=error.to_dict())

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": "REQUEST_INVALID",
                "reason_code": "REQUEST_SCHEMA_INVALID",
            },
        )

    @app.exception_handler(GovernanceDenied)
    async def governance_error(
        _request: Request, error: GovernanceDenied
    ) -> JSONResponse:
        return JSONResponse(status_code=403, content=error.to_dict())

    @app.exception_handler(PlatformError)
    async def platform_error(
        _request: Request, error: PlatformError
    ) -> JSONResponse:
        status = 404 if error.code in {"SOURCE_NOT_REGISTERED", "UPLOAD_NOT_FOUND"} else 409
        if error.code in {"IDEMPOTENCY_KEY_INVALID", "CONTENT_TOO_LARGE"}:
            status = 400
        return JSONResponse(status_code=status, content=error.to_dict())

    @app.exception_handler(Exception)
    async def foundation_error(_request: Request, _error: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "error": "FOUNDATION_UNAVAILABLE",
                "reason_code": "LOCAL_FOUNDATION_UNAVAILABLE",
            },
        )

    def authenticated_context(
        authorization: str | None = Header(default=None),
    ) -> SessionContext:
        if (
            authorization is None
            or not authorization.startswith("Bearer ")
            or authorization.count(" ") != 1
        ):
            raise AuthenticationError()
        return authenticate_local_session(service.config, authorization[7:])

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "schema": "platform-foundation-health-v1",
            **database_health(service.config),
            "external_calls": 0,
            "ocr_calls": 0,
        }

    @app.get("/readiness")
    def readiness() -> dict[str, object]:
        return service.readiness()

    @app.post("/upload-sessions", status_code=201)
    def create_upload_session(
        body: CreateUploadRequest,
        context: SessionContext = Depends(authenticated_context),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict[str, object]:
        return service.create_upload(
            context, body.source_id, idempotency_key
        ).to_dict()

    @app.put("/upload-sessions/{upload_id}/content")
    async def put_upload_content(
        upload_id: uuid.UUID,
        request: Request,
        context: SessionContext = Depends(authenticated_context),
    ) -> dict[str, object]:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                parsed_length = int(content_length)
                if parsed_length < 0 or parsed_length > _MAX_LOCAL_BODY_BYTES:
                    raise PlatformError("CONTENT_TOO_LARGE")
            except ValueError:
                raise PlatformError("CONTENT_TOO_LARGE") from None
        return (
            await service.store_content_stream(
                context,
                upload_id,
                request.stream(),
                maximum_size=_MAX_LOCAL_BODY_BYTES,
            )
        ).to_dict()

    @app.post("/upload-sessions/{upload_id}/complete")
    def complete_upload(
        upload_id: uuid.UUID,
        context: SessionContext = Depends(authenticated_context),
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> dict[str, str]:
        return service.complete_upload(
            context, upload_id, idempotency_key
        ).to_dict()

    @app.get("/documents")
    def documents(
        context: SessionContext = Depends(authenticated_context),
    ) -> dict[str, object]:
        records = service.list_documents(context)
        return {"documents": records, "count": len(records)}

    @app.get("/jobs")
    def jobs(
        context: SessionContext = Depends(authenticated_context),
    ) -> dict[str, object]:
        records = service.list_jobs(context)
        return {"jobs": records, "count": len(records)}

    return app


__all__ = ("CreateUploadRequest", "create_app")
