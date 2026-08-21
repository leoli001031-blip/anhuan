"""F1 FastAPI application (platform shell) with OpenTelemetry."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from ..observability import init_telemetry
from ..health import readiness
from .routers import (
    audit,
    documents,
    enterprises,
    findings,
    invitation,
    material_qa,
    material_qa_uat,
    p3_controlled_ingestion,
    p4_views_reports,
    p5_policy_workflow,
    p6_automated_quality,
    p7_local_rehearsal,
    plants,
    qa,
    service_cases,
    site_visits,
    users,
    workbench,
)

app = FastAPI(title="AnHuan F1 Platform Shell")

# OpenTelemetry: SDK + FastAPI auto-instrumentation.
init_telemetry()
FastAPIInstrumentor.instrument_app(app)

app.include_router(enterprises.router, prefix="/api/v1/enterprises", tags=["enterprises"])
app.include_router(plants.router, prefix="/api/v1/plants", tags=["plants"])
app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
app.include_router(documents.router, prefix="/api/v1/documents", tags=["documents"])
app.include_router(qa.router, prefix="/api/v1/qa", tags=["qa"])
app.include_router(
    material_qa.router,
    prefix="/api/v1/material-qa",
    tags=["material-qa"],
)
app.include_router(audit.router, prefix="/api/v1/audit", tags=["audit"])
app.include_router(invitation.router, prefix="/api/v1/invitations", tags=["invitations"])
app.include_router(
    p3_controlled_ingestion.router,
    prefix="/api/v1/ingestion",
    tags=["controlled-ingestion"],
)
app.include_router(
    p4_views_reports.router,
    prefix="/api/v1/views-reports",
    tags=["views-reports"],
)
app.include_router(
    p5_policy_workflow.router,
    prefix="/api/v1/policy-workflow",
    tags=["policy-workflow"],
)
app.include_router(
    p6_automated_quality.router,
    prefix="/api/v1/automated-quality",
    tags=["automated-quality"],
)
app.include_router(
    p7_local_rehearsal.router,
    prefix="/api/v1/local-rehearsal",
    tags=["local-rehearsal"],
)
app.include_router(
    site_visits.router,
    prefix="/api/v1/service-cases",
    tags=["site-visits"],
)
app.include_router(
    service_cases.router,
    prefix="/api/v1/service-cases",
    tags=["service-cases"],
)
app.include_router(
    findings.router,
    prefix="/api/v1/findings",
    tags=["findings"],
)
app.include_router(
    workbench.router,
    prefix="/api/v1/workbench",
    tags=["workbench"],
)
material_qa_uat.mount_if_enabled(app)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> JSONResponse:
    components = await readiness()
    ready = all(components.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "unavailable",
            "components": components,
        },
        headers={"Cache-Control": "no-store"},
    )
