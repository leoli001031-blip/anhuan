"""Loopback-only, no-store HTTP API for blind local Fixture annotation."""

from __future__ import annotations

from collections.abc import AsyncIterator
import uuid

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, ValidationError
import uvicorn

from ..auth import AuthenticationError, SessionContext, authenticate_local_session
from .contracts import CanonicalLabel, F0GError, MAX_LABEL_BYTES, SensitiveBytes
from .service import AnnotationService, verify_function_catalog


_LOOPBACK = frozenset({"127.0.0.1", "::1"})
LOCAL_API_HOST = "127.0.0.1"
LOCAL_API_PORT = 8767
MAX_ADJUDICATION_REQUEST_BYTES = 4096


class AdjudicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_code: str
    selected_label_ordinal: int | None = None


def create_app(service: AnnotationService) -> FastAPI:
    if not isinstance(service, AnnotationService):
        raise F0GError("ANNOTATION_CONTRACT_INVALID")
    app = FastAPI(
        title="F0-G Local Fixture Annotation",
        version="0.1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def local_no_store(request: Request, call_next: object):
        if request.client is None or request.client.host not in _LOOPBACK:
            response = JSONResponse(
                status_code=403,
                content={"error": "F0G_ERROR", "reason_code": "LOCAL_ONLY_REQUIRED"},
            )
        else:
            response = await call_next(request)  # type: ignore[operator]
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(AuthenticationError)
    async def authentication_error(
        _request: Request, _error: AuthenticationError
    ) -> JSONResponse:
        return _error_response(401, "LOCAL_SESSION_INVALID")

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return _error_response(422, "ANNOTATION_CONTRACT_INVALID")

    @app.exception_handler(F0GError)
    async def workflow_error(_request: Request, error: F0GError) -> JSONResponse:
        if error.code in {"ANNOTATION_ASSIGNMENT_DENIED", "ANNOTATION_ADJUDICATION_DENIED"}:
            status = 403
        elif error.code in {"ANNOTATION_BODY_INVALID", "ANNOTATION_LABEL_INVALID"}:
            status = 400
        elif error.code == "ANNOTATION_CONTRACT_INVALID":
            status = 422
        elif error.code in {"ANNOTATION_STATE_INVALID"}:
            status = 409
        else:
            status = 503
        return _error_response(status, error.code)

    @app.exception_handler(Exception)
    async def unavailable(_request: Request, _error: Exception) -> JSONResponse:
        return _error_response(503, "ANNOTATION_UNAVAILABLE")

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
            "schema": "f0g-health-v1",
            "status": "LOCAL_FIXTURE_ANNOTATION_WORKFLOW",
            "external_calls": 0,
        }

    @app.get("/assignments")
    def assignments(
        context: SessionContext = Depends(authenticated_context),
    ) -> dict[str, object]:
        records = service.list_assignments(context)
        return {
            "schema": "f0g-assignment-list-v1",
            "assignments": [record.to_dict() for record in records],
            "count": len(records),
        }

    @app.get("/assignments/{assignment_id}/body")
    def assigned_body(
        assignment_id: uuid.UUID,
        context: SessionContext = Depends(authenticated_context),
    ) -> StreamingResponse:
        owner = service.read_assigned_body(context, assignment_id)
        return _sensitive_response(owner)

    @app.post("/assignments/{assignment_id}/labels", status_code=201)
    async def submit_label(
        assignment_id: uuid.UUID,
        request: Request,
        context: SessionContext = Depends(authenticated_context),
    ) -> dict[str, object]:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if not 0 <= int(content_length) <= MAX_LABEL_BYTES:
                    raise F0GError("ANNOTATION_LABEL_INVALID")
            except ValueError:
                raise F0GError("ANNOTATION_LABEL_INVALID") from None
        incoming = bytearray()
        label: CanonicalLabel | None = None
        try:
            async for chunk in request.stream():
                if len(incoming) + len(chunk) > MAX_LABEL_BYTES:
                    raise F0GError("ANNOTATION_LABEL_INVALID")
                incoming.extend(chunk)
            label = CanonicalLabel(incoming)
            label_id = service.submit_label(context, assignment_id, label)
            return {
                "schema": "f0g-label-submission-v1",
                "label_id": str(label_id),
                "status": "BLIND_LABEL_RECORDED",
                "gold_status": "NOT_GOLD",
            }
        finally:
            if label is not None:
                label.wipe()
            incoming[:] = b"\0" * len(incoming)
            incoming.clear()

    @app.get("/assignments/{assignment_id}/adjudication")
    def adjudication_metadata(
        assignment_id: uuid.UUID,
        context: SessionContext = Depends(authenticated_context),
    ) -> dict[str, object]:
        assignment = next(
            (
                item
                for item in service.list_assignments(context)
                if item.assignment_id == assignment_id
                and item.assignment_role == "ADJUDICATOR"
            ),
            None,
        )
        if assignment is None or not assignment.adjudication_ready:
            raise F0GError("ANNOTATION_ADJUDICATION_DENIED")
        return {
            "schema": "f0g-adjudication-input-v1",
            "assignment_id": str(assignment_id),
            "labels_submitted": assignment.labels_submitted,
            "adjudication_ready": True,
        }

    @app.get("/assignments/{assignment_id}/adjudication/labels/{label_ordinal}")
    def adjudication_label(
        assignment_id: uuid.UUID,
        label_ordinal: int,
        context: SessionContext = Depends(authenticated_context),
    ) -> StreamingResponse:
        owner = service.read_adjudication_label(
            context, assignment_id, label_ordinal
        )
        return _sensitive_response(owner)

    @app.post("/assignments/{assignment_id}/adjudication", status_code=201)
    async def adjudicate(
        assignment_id: uuid.UUID,
        request: Request,
        context: SessionContext = Depends(authenticated_context),
    ) -> dict[str, object]:
        incoming = await _read_limited_request(
            request,
            MAX_ADJUDICATION_REQUEST_BYTES,
            "ANNOTATION_CONTRACT_INVALID",
        )
        try:
            try:
                body = AdjudicationRequest.model_validate_json(incoming)
            except ValidationError:
                raise F0GError("ANNOTATION_CONTRACT_INVALID") from None
        finally:
            _wipe(incoming)
        adjudication_id = service.adjudicate(
            context,
            assignment_id,
            body.decision_code,
            body.selected_label_ordinal,
        )
        return {
            "schema": "f0g-adjudication-result-v1",
            "adjudication_id": str(adjudication_id),
            "status": "FIXTURE_ADJUDICATION_RECORDED",
            "production_allowed": False,
        }

    return app


def local_server_config(service: AnnotationService) -> uvicorn.Config:
    """Return the fixed, non-forwarding loopback server configuration."""

    return uvicorn.Config(
        create_app(service),
        host=LOCAL_API_HOST,
        port=LOCAL_API_PORT,
        uds=None,
        fd=None,
        loop="asyncio",
        http="h11",
        ws="none",
        lifespan="on",
        log_config=None,
        log_level="critical",
        access_log=False,
        use_colors=False,
        interface="asgi3",
        reload=False,
        workers=1,
        proxy_headers=False,
        server_header=False,
        date_header=False,
        forwarded_allow_ips="",
        limit_concurrency=32,
        backlog=32,
        timeout_keep_alive=5,
        timeout_graceful_shutdown=5,
    )


def check_local_server_binding(service: AnnotationService) -> dict[str, object]:
    """Fail closed on catalog drift, then bind and immediately close 127.0.0.1."""

    _verify_server_service(service)
    config = local_server_config(service)
    listener = None
    try:
        try:
            listener = config.bind_socket()
        except SystemExit:
            raise F0GError("ANNOTATION_UNAVAILABLE") from None
        address = listener.getsockname()
        if (
            not isinstance(address, tuple)
            or len(address) < 2
            or address[0] != LOCAL_API_HOST
            or address[1] != LOCAL_API_PORT
        ):
            raise F0GError("ANNOTATION_UNAVAILABLE")
    finally:
        if listener is not None:
            listener.close()
    return {
        "schema": "f0g-loopback-bind-check-v1",
        "status": "LOCAL_FIXTURE_ANNOTATION_LOOPBACK_BIND_READY",
        "host": LOCAL_API_HOST,
        "port": LOCAL_API_PORT,
    }


def run_local_api(service: AnnotationService) -> None:
    """Run the fixed loopback API after validating its complete DB catalog."""

    _verify_server_service(service)
    server = uvicorn.Server(local_server_config(service))
    try:
        server.run()
    except SystemExit:
        raise F0GError("ANNOTATION_UNAVAILABLE") from None
    if not server.started:
        raise F0GError("ANNOTATION_UNAVAILABLE")


def _verify_server_service(service: AnnotationService) -> None:
    if not isinstance(service, AnnotationService):
        raise F0GError("ANNOTATION_CONTRACT_INVALID")
    verify_function_catalog(service.config)


def _error_response(status: int, reason_code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": "F0G_ERROR", "reason_code": reason_code},
    )


def _sensitive_response(owner: SensitiveBytes) -> StreamingResponse:
    async def stream() -> AsyncIterator[bytes]:
        try:
            yield bytes(owner.view())
        finally:
            owner.wipe()

    return StreamingResponse(stream(), media_type="application/octet-stream")


async def _read_limited_request(
    request: Request, maximum: int, reason_code: str
) -> bytearray:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            raise F0GError(reason_code) from None
        if not 0 <= declared <= maximum:
            raise F0GError(reason_code)
    incoming = bytearray()
    try:
        async for chunk in request.stream():
            if len(incoming) + len(chunk) > maximum:
                raise F0GError(reason_code)
            incoming.extend(chunk)
        return incoming
    except BaseException:
        _wipe(incoming)
        raise


def _wipe(buffer: bytearray) -> None:
    buffer[:] = b"\0" * len(buffer)
    buffer.clear()


__all__ = (
    "AdjudicationRequest",
    "LOCAL_API_HOST",
    "LOCAL_API_PORT",
    "MAX_ADJUDICATION_REQUEST_BYTES",
    "check_local_server_binding",
    "create_app",
    "local_server_config",
    "run_local_api",
)
