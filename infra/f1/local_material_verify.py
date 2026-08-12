"""One-shot synthetic PDF verifier for assisted material intake."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import os
import re
import secrets
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

SOURCE_DATABASE_RE = re.compile(r"anhuan_closeout_([0-9a-f]{24})\Z")
EMPLOYEE_SUB = "3247dddb-69bc-4ad1-841c-8fc338b603ce"
CONSULTANT_SUB = "7e9978c7-106f-4221-a6d7-79e8104a659b"
TENANT_B_SUB = "ddc4e27e-ccde-4c89-958f-798fc8f30175"
EXPECTED_FIELDS = {
    "source_title",
    "version_title",
    "source_type",
    "source_reference",
}
FAILURE_REASONS = frozenset(
    {
        "LOCAL_MATERIAL_SOURCE_NOT_READY",
        "LOCAL_MATERIAL_SCRATCH_CREATE_FAILED",
        "LOCAL_MATERIAL_PROCESS_FAILED",
        "LOCAL_MATERIAL_ANALYSIS_FAILED",
        "LOCAL_MATERIAL_RLS_FAILED",
        "LOCAL_MATERIAL_RLS_ADMIN_CLIENT_FAILED",
        "LOCAL_MATERIAL_RLS_OWNER_CLIENT_FAILED",
        "LOCAL_MATERIAL_RLS_NON_OWNER_FAILED",
        "LOCAL_MATERIAL_RLS_NON_OWNER_CLIENT_FAILED",
        "LOCAL_MATERIAL_RLS_NON_OWNER_PROVIDER_FAILED",
        "LOCAL_MATERIAL_RLS_CROSS_TENANT_FAILED",
        "LOCAL_MATERIAL_RLS_ADMIN_PROVIDER_FAILED",
        "LOCAL_MATERIAL_POLICY_GUARD_FAILED",
        "LOCAL_MATERIAL_CLEANUP_FAILED",
        "LOCAL_MATERIAL_INTERNAL_ERROR",
    }
)


class MaterialVerifyError(RuntimeError):
    def __init__(self, reason: str) -> None:
        safe = reason if reason in FAILURE_REASONS else "LOCAL_MATERIAL_INTERNAL_ERROR"
        super().__init__(safe)
        self.reason = safe


class _DiscardText:
    def write(self, value: str) -> int:
        return len(value)

    def flush(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class MaterialVerificationCounts:
    scratch_migration_head_count: int = 2
    uploaded_version_count: int = 2
    clean_scan_count: int = 2
    preview_unit_count: int = 2
    released_object_count: int = 2
    audit_action_count: int = 8
    material_analysis_count: int = 2
    material_page_count: int = 2
    material_candidate_count: int = 8
    uncalibrated_candidate_count: int = 8
    shadow_runtime_enabled_count: int = 0
    knowledge_scope_count: int = 2
    service_provider_scope_count: int = 1
    client_scope_count: int = 1
    client_owner_document_visible_count: int = 1
    client_owner_low_level_visible_count: int = 2
    same_tenant_non_owner_rls_visible_count: int = 0
    client_policy_api_rejection_count: int = 1
    client_policy_database_rejection_count: int = 1
    policy_source_count: int = 1
    policy_draft_count: int = 1
    authoritative_publication_count: int = 0
    cross_tenant_api_visible_count: int = 0
    cross_tenant_rls_visible_count: int = 0
    object_residual_count: int = 0
    bucket_residual_count: int = 0
    scratch_database_residual_count: int = 0


def _synthetic_pdf() -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {NameObject("/F1"): writer._add_object(font)}
            )
        }
    )
    lines = (
        "ENVIRONMENTAL STANDARD GB 12345-2026",
        "GB 12345-2026 synthetic material intake evidence",
        "Issued 2026-08-12 for internal engineering verification only",
        "This document describes environmental monitoring and emission controls",
        "Human confirmation is required before any policy draft is authoritative",
    )
    commands = ["BT /F1 12 Tf 16 TL 72 700 Td"]
    for index, line in enumerate(lines):
        if index:
            commands.append("T*")
        commands.append(f"({line}) Tj")
    commands.append("ET")
    stream = DecodedStreamObject()
    stream.set_data((" ".join(commands) + "\n").encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _json_object(response: object) -> dict[str, Any]:
    try:
        payload = response.json()
    except BaseException:
        raise MaterialVerifyError("LOCAL_MATERIAL_PROCESS_FAILED") from None
    if not isinstance(payload, dict):
        raise MaterialVerifyError("LOCAL_MATERIAL_PROCESS_FAILED")
    return payload


def _uuid_field(payload: dict[str, Any], field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(payload[field]))
    except (KeyError, TypeError, ValueError):
        raise MaterialVerifyError("LOCAL_MATERIAL_PROCESS_FAILED") from None


@dataclass(frozen=True, slots=True)
class MaterialRunIdentifiers:
    provider_document_id: uuid.UUID
    provider_version_id: uuid.UUID
    provider_analysis_id: uuid.UUID
    provider_scope_id: uuid.UUID
    client_account_id: uuid.UUID
    client_document_id: uuid.UUID
    client_version_id: uuid.UUID
    client_analysis_id: uuid.UUID
    client_scope_id: uuid.UUID
    policy_source_id: uuid.UUID
    policy_version_id: uuid.UUID


def _validate_material(
    payload: dict[str, Any],
    version_id: uuid.UUID,
    *,
    expected_actions: list[str],
    expected_scope_kind: str,
    expected_client_account_id: uuid.UUID | None,
) -> tuple[uuid.UUID, uuid.UUID]:
    analysis_id = _uuid_field(payload, "id")
    knowledge_scope = payload.get("knowledge_scope")
    pages = payload.get("pages")
    candidates = payload.get("candidates")
    if (
        payload.get("document_version_id") != str(version_id)
        or payload.get("analysis_version") != "material-v1"
        or payload.get("parser_backend") != "pypdf_heuristic"
        or payload.get("document_profile") != "text"
        or payload.get("status") != "ready"
        or payload.get("shadow_status") != "disabled"
        or payload.get("suggested_kind") != "unknown"
        or payload.get("suggested_kind_confidence_ppm") != 0
        or payload.get("resolved_kind") != "policy"
        or payload.get("classification_source") != "upload_selection"
        or not payload.get("classification_by_user_id")
        or not payload.get("classification_at")
        or payload.get("page_count") != 1
        or payload.get("candidate_count") != 4
        or payload.get("allowed_actions") != expected_actions
        or not isinstance(knowledge_scope, dict)
        or knowledge_scope.get("kind") != expected_scope_kind
        or knowledge_scope.get("client_account_id")
        != (
            str(expected_client_account_id)
            if expected_client_account_id is not None
            else None
        )
        or (
            expected_scope_kind == "service_provider"
            and knowledge_scope.get("client_display_name") is not None
        )
        or (
            expected_scope_kind == "client"
            and not knowledge_scope.get("client_display_name")
        )
        or "HUMAN_CONFIRMATION_REQUIRED" not in payload.get("boundaries", [])
        or "PDF_INSPECTOR_RUNTIME_DISABLED" not in payload.get("boundaries", [])
        or not isinstance(pages, list)
        or len(pages) != 1
        or not isinstance(pages[0], dict)
        or pages[0].get("page_number") != 1
        or pages[0].get("primary_kind") != "text"
        or pages[0].get("ocr_required") is not False
        or not isinstance(candidates, list)
        or len(candidates) != 4
        or {
            item.get("field_name")
            for item in candidates
            if isinstance(item, dict)
        }
        != EXPECTED_FIELDS
        or any(
            not isinstance(item, dict)
            or item.get("page_number") != 1
            or item.get("calibrated") is not False
            or item.get("producer") != "pypdf_heuristic"
            or type(item.get("confidence_ppm")) is not int
            or not isinstance(item.get("evidence_snippet"), str)
            or not item.get("evidence_snippet")
            for item in candidates
        )
    ):
        raise MaterialVerifyError("LOCAL_MATERIAL_ANALYSIS_FAILED")
    return analysis_id, _uuid_field(knowledge_scope, "id")


async def _api_smoke(
    pdf_body: bytes,
    *,
    provider_idempotency_key: str,
    client_idempotency_key: str,
) -> MaterialRunIdentifiers:
    import httpx
    from fastapi import FastAPI, Header, HTTPException

    from infra.f1 import local_seed
    from platform_foundation.f1 import auth
    from platform_foundation.f1.api.routers import (
        p3_controlled_ingestion,
        p4_views_reports,
        p5_policy_workflow,
    )

    actors = {
        "admin": {"sub": local_seed.ADMIN_SUB, "enterprise_id": local_seed.ENTERPRISE_A},
        "employee": {"sub": EMPLOYEE_SUB, "enterprise_id": local_seed.ENTERPRISE_A},
        "tenant_b": {"sub": TENANT_B_SUB, "enterprise_id": local_seed.ENTERPRISE_B},
    }
    app = FastAPI()
    app.include_router(p3_controlled_ingestion.router, prefix="/api/v1/ingestion")
    app.include_router(p4_views_reports.router, prefix="/api/v1/views-reports")
    app.include_router(
        p5_policy_workflow.router,
        prefix="/api/v1/policy-workflow",
    )

    async def synthetic_user(
        x_local_material_actor: str | None = Header(default=None),
    ) -> dict[str, object]:
        actor = actors.get(x_local_material_actor or "")
        if actor is None:
            raise HTTPException(status_code=401, detail="LOCAL_IDENTITY_REQUIRED")
        return {"sub": actor["sub"], "roles": ()}

    app.dependency_overrides[auth.current_user] = synthetic_user
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://local-material.invalid"
    ) as client:
        async def request(
            actor_name: str,
            method: str,
            path: str,
            expected_status: int,
            **kwargs: object,
        ) -> httpx.Response:
            actor = actors[actor_name]
            response = await client.request(
                method,
                path,
                headers={
                    "X-Local-Material-Actor": actor_name,
                    "X-Enterprise-Id": str(actor["enterprise_id"]),
                    **dict(kwargs.pop("headers", {})),
                },
                **kwargs,
            )
            if response.status_code != expected_status:
                raise MaterialVerifyError("LOCAL_MATERIAL_PROCESS_FAILED")
            return response

        employee_user_id = local_seed._stable_id("profile", EMPLOYEE_SUB)
        account = _json_object(
            await request(
                "admin",
                "POST",
                "/api/v1/views-reports/crm/accounts",
                201,
                json={
                    "display_name": "LOCAL_CLIENT_SCOPE",
                    "stage": "active",
                    "owner_user_id": str(employee_user_id),
                },
            )
        )
        client_account_id = _uuid_field(account, "id")
        if account.get("owner_user_id") != str(employee_user_id):
            raise MaterialVerifyError("LOCAL_MATERIAL_PROCESS_FAILED")

        uploaded = _json_object(
            await request(
                "admin",
                "POST",
                "/api/v1/ingestion/documents",
                202,
                headers={"Idempotency-Key": provider_idempotency_key},
                data={
                    "display_name": "LOCAL_MATERIAL_SYNTHETIC",
                    "declared_material_kind": "policy",
                    "knowledge_scope_kind": "service_provider",
                },
                files={"file": ("material.pdf", pdf_body, "application/pdf")},
            )
        )
        provider_document_id = _uuid_field(uploaded, "id")
        versions = uploaded.get("versions")
        if (
            not isinstance(versions, list)
            or len(versions) != 1
            or not isinstance(versions[0], dict)
        ):
            raise MaterialVerifyError("LOCAL_MATERIAL_PROCESS_FAILED")
        version = versions[0]
        provider_version_id = _uuid_field(version, "id")
        if (
            version.get("workflow_status") != "ready"
            or version.get("scan_status") != "clean"
            or version.get("preview_status") != "ready"
            or version.get("quarantine_status") != "held"
        ):
            raise MaterialVerifyError("LOCAL_MATERIAL_PROCESS_FAILED")
        material = _json_object(
            await request(
                "admin",
                "GET",
                f"/api/v1/ingestion/versions/{provider_version_id}/material-intake",
                200,
            )
        )
        provider_analysis_id, provider_scope_id = _validate_material(
            material,
            provider_version_id,
            expected_actions=["set_material_kind"],
            expected_scope_kind="service_provider",
            expected_client_account_id=None,
        )
        released = _json_object(
            await request(
                "admin",
                "POST",
                f"/api/v1/ingestion/versions/{provider_version_id}/release",
                200,
            )
        )
        if (
            released.get("workflow_status") != "ready"
            or released.get("quarantine_status") != "released"
            or released.get("scan_status") != "clean"
            or released.get("preview_status") != "ready"
        ):
            raise MaterialVerifyError("LOCAL_MATERIAL_PROCESS_FAILED")
        released_material = _json_object(
            await request(
                "admin",
                "GET",
                f"/api/v1/ingestion/versions/{provider_version_id}/material-intake",
                200,
            )
        )
        if _validate_material(
            released_material,
            provider_version_id,
            expected_actions=["set_material_kind", "confirm_policy_draft"],
            expected_scope_kind="service_provider",
            expected_client_account_id=None,
        ) != (provider_analysis_id, provider_scope_id):
            raise MaterialVerifyError("LOCAL_MATERIAL_ANALYSIS_FAILED")
        confirmation_key = secrets.token_hex(24)
        confirmation_body = {
            "source": {
                "title": "ENVIRONMENTAL STANDARD GB 12345-2026",
                "publisher": "LOCAL SYNTHETIC AUTHORITY",
                "source_type": "standard",
                "jurisdiction": "LOCAL ENGINEERING",
                "source_reference": "GB 12345-2026",
            },
            "version": {
                "title": "ENVIRONMENTAL STANDARD GB 12345-2026",
                "domain": "environment",
                "effect_status": "unknown",
                "issued_on": "2026-08-12",
                "summary": (
                    "Synthetic human-confirmed draft for local engineering "
                    "verification."
                ),
            },
        }
        confirmation = _json_object(
            await request(
                "admin",
                "POST",
                f"/api/v1/policy-workflow/material-analyses/"
                f"{provider_analysis_id}/confirm",
                201,
                headers={"Idempotency-Key": confirmation_key},
                json=confirmation_body,
            )
        )
        confirmed_analysis = confirmation.get("analysis")
        source = confirmation.get("source")
        policy_version = confirmation.get("version")
        if (
            not isinstance(confirmed_analysis, dict)
            or confirmed_analysis.get("id") != str(provider_analysis_id)
            or confirmed_analysis.get("status") != "confirmed"
            or confirmed_analysis.get("allowed_actions")
            != ["view_policy_source", "view_policy_version"]
            or not isinstance(source, dict)
            or source.get("status") != "active"
            or not isinstance(policy_version, dict)
            or policy_version.get("workflow_status") != "draft"
            or policy_version.get("document_version_id")
            != str(provider_version_id)
            or policy_version.get("document_sha256")
            != hashlib.sha256(pdf_body).hexdigest()
            or policy_version.get("published_at") is not None
        ):
            raise MaterialVerifyError("LOCAL_MATERIAL_ANALYSIS_FAILED")
        policy_source_id = _uuid_field(source, "id")
        policy_version_id = _uuid_field(policy_version, "id")
        replay = _json_object(
            await request(
                "admin",
                "POST",
                f"/api/v1/policy-workflow/material-analyses/"
                f"{provider_analysis_id}/confirm",
                201,
                headers={"Idempotency-Key": confirmation_key},
                json=confirmation_body,
            )
        )
        if (
            not isinstance(replay.get("source"), dict)
            or replay["source"].get("id") != str(policy_source_id)
            or not isinstance(replay.get("version"), dict)
            or replay["version"].get("id") != str(policy_version_id)
        ):
            raise MaterialVerifyError("LOCAL_MATERIAL_ANALYSIS_FAILED")

        client_upload = _json_object(
            await request(
                "employee",
                "POST",
                "/api/v1/ingestion/documents",
                202,
                headers={"Idempotency-Key": client_idempotency_key},
                data={
                    "display_name": "LOCAL_MATERIAL_SYNTHETIC",
                    "declared_material_kind": "policy",
                    "knowledge_scope_kind": "client",
                    "client_account_id": str(client_account_id),
                },
                files={"file": ("material.pdf", pdf_body, "application/pdf")},
            )
        )
        client_document_id = _uuid_field(client_upload, "id")
        client_versions = client_upload.get("versions")
        if (
            not isinstance(client_versions, list)
            or len(client_versions) != 1
            or not isinstance(client_versions[0], dict)
        ):
            raise MaterialVerifyError("LOCAL_MATERIAL_PROCESS_FAILED")
        client_version = client_versions[0]
        client_version_id = _uuid_field(client_version, "id")
        if (
            client_version.get("workflow_status") != "ready"
            or client_version.get("scan_status") != "clean"
            or client_version.get("preview_status") != "ready"
            or client_version.get("quarantine_status") != "held"
        ):
            raise MaterialVerifyError("LOCAL_MATERIAL_PROCESS_FAILED")
        client_material = _json_object(
            await request(
                "employee",
                "GET",
                f"/api/v1/ingestion/versions/{client_version_id}/material-intake",
                200,
            )
        )
        client_analysis_id, client_scope_id = _validate_material(
            client_material,
            client_version_id,
            expected_actions=["set_material_kind"],
            expected_scope_kind="client",
            expected_client_account_id=client_account_id,
        )
        client_release = _json_object(
            await request(
                "employee",
                "POST",
                f"/api/v1/ingestion/versions/{client_version_id}/release",
                200,
            )
        )
        if (
            client_release.get("workflow_status") != "ready"
            or client_release.get("quarantine_status") != "released"
            or client_release.get("scan_status") != "clean"
            or client_release.get("preview_status") != "ready"
        ):
            raise MaterialVerifyError("LOCAL_MATERIAL_PROCESS_FAILED")
        released_client_material = _json_object(
            await request(
                "admin",
                "GET",
                f"/api/v1/ingestion/versions/{client_version_id}/material-intake",
                200,
            )
        )
        if _validate_material(
            released_client_material,
            client_version_id,
            expected_actions=["set_material_kind"],
            expected_scope_kind="client",
            expected_client_account_id=client_account_id,
        ) != (client_analysis_id, client_scope_id):
            raise MaterialVerifyError("LOCAL_MATERIAL_ANALYSIS_FAILED")

        client_confirmation = await request(
            "admin",
            "POST",
            f"/api/v1/policy-workflow/material-analyses/"
            f"{client_analysis_id}/confirm",
            409,
            headers={"Idempotency-Key": secrets.token_hex(24)},
            json=confirmation_body,
        )
        if _json_object(client_confirmation).get("detail") != (
            "MATERIAL_POLICY_CLASSIFICATION_REQUIRED"
        ):
            raise MaterialVerifyError("LOCAL_MATERIAL_POLICY_GUARD_FAILED")

        generic_policy = await request(
            "admin",
            "POST",
            f"/api/v1/policy-workflow/sources/{policy_source_id}/versions",
            404,
            json={
                "title": "CLIENT MATERIAL MUST NOT BECOME POLICY",
                "domain": "environment",
                "effect_status": "unknown",
                "summary": "Synthetic policy boundary verification.",
                "document_version_id": str(client_version_id),
            },
        )
        if _json_object(generic_policy).get("detail") != (
            "POLICY_DOCUMENT_VERSION_NOT_FOUND"
        ):
            raise MaterialVerifyError("LOCAL_MATERIAL_POLICY_GUARD_FAILED")

        await request(
            "tenant_b",
            "GET",
            f"/api/v1/ingestion/versions/{provider_version_id}/material-intake",
            404,
        )
        await request(
            "tenant_b",
            "GET",
            f"/api/v1/ingestion/versions/{client_version_id}/material-intake",
            404,
        )
        await request(
            "tenant_b",
            "GET",
            f"/api/v1/policy-workflow/versions/{policy_version_id}",
            404,
        )
    return MaterialRunIdentifiers(
        provider_document_id=provider_document_id,
        provider_version_id=provider_version_id,
        provider_analysis_id=provider_analysis_id,
        provider_scope_id=provider_scope_id,
        client_account_id=client_account_id,
        client_document_id=client_document_id,
        client_version_id=client_version_id,
        client_analysis_id=client_analysis_id,
        client_scope_id=client_scope_id,
        policy_source_id=policy_source_id,
        policy_version_id=policy_version_id,
    )


async def _verify_rls(identifiers: MaterialRunIdentifiers) -> None:
    from sqlalchemy import text

    from infra.f1 import local_seed
    from platform_foundation.f1.database import session_scope

    client_query = text(
        "SELECT "
        "(SELECT count(*) FROM f1.material_knowledge_scope WHERE id=:scope_id),"
        "(SELECT count(*) FROM f1.crm_account WHERE id=:account_id),"
        "(SELECT count(*) FROM f1.document_record WHERE id=:document_id),"
        "(SELECT count(*) FROM f1.document_version WHERE id=:version_id),"
        "(SELECT count(*) FROM f1.document WHERE id=:source_document_id),"
        "(SELECT count(*) FROM f1.upload_task WHERE id=:upload_task_id),"
        "(SELECT count(*) FROM f1.material_analysis WHERE id=:analysis_id),"
        "(SELECT count(*) FROM f1.material_page_classification "
        "WHERE analysis_id=:analysis_id),"
        "(SELECT count(*) FROM f1.material_field_candidate "
        "WHERE analysis_id=:analysis_id),"
        "(SELECT count(*) FROM f1.policy_version "
        "WHERE document_version_id=:version_id)"
    )
    client_parameters = {
        "scope_id": identifiers.client_scope_id,
        "account_id": identifiers.client_account_id,
        "document_id": identifiers.client_document_id,
        "version_id": identifiers.client_version_id,
        "analysis_id": identifiers.client_analysis_id,
    }
    provider_query = text(
        "SELECT "
        "(SELECT count(*) FROM f1.material_knowledge_scope WHERE id=:scope_id),"
        "(SELECT count(*) FROM f1.document_record WHERE id=:document_id),"
        "(SELECT count(*) FROM f1.document_version WHERE id=:version_id),"
        "(SELECT count(*) FROM f1.document WHERE id=:source_document_id),"
        "(SELECT count(*) FROM f1.upload_task WHERE id=:upload_task_id),"
        "(SELECT count(*) FROM f1.material_analysis WHERE id=:analysis_id),"
        "(SELECT count(*) FROM f1.material_page_classification "
        "WHERE analysis_id=:analysis_id),"
        "(SELECT count(*) FROM f1.material_field_candidate "
        "WHERE analysis_id=:analysis_id),"
        "(SELECT count(*) FROM f1.policy_source WHERE id=:policy_source_id),"
        "(SELECT count(*) FROM f1.policy_version WHERE id=:policy_version_id),"
        "(SELECT count(*) FROM f1.policy_version WHERE id=:policy_version_id "
        "AND workflow_status='published')"
    )
    provider_parameters = {
        "scope_id": identifiers.provider_scope_id,
        "document_id": identifiers.provider_document_id,
        "version_id": identifiers.provider_version_id,
        "analysis_id": identifiers.provider_analysis_id,
        "policy_source_id": identifiers.policy_source_id,
        "policy_version_id": identifiers.policy_version_id,
    }
    async with session_scope(
        role="f1_api", enterprise_id=local_seed.ENTERPRISE_A, sub=local_seed.ADMIN_SUB
    ) as session:
        low_level_rows = (
            await session.execute(
                text(
                    "SELECT id,source_document_id,upload_task_id "
                    "FROM f1.document_version WHERE id IN (:provider_version_id,"
                    ":client_version_id)"
                ),
                {
                    "provider_version_id": identifiers.provider_version_id,
                    "client_version_id": identifiers.client_version_id,
                },
            )
        ).mappings().all()
        low_level_by_version = {row["id"]: row for row in low_level_rows}
        provider_low_level = low_level_by_version.get(
            identifiers.provider_version_id
        )
        client_low_level = low_level_by_version.get(identifiers.client_version_id)
        if provider_low_level is None or client_low_level is None:
            raise MaterialVerifyError("LOCAL_MATERIAL_RLS_FAILED")
        provider_parameters.update(
            {
                "source_document_id": provider_low_level["source_document_id"],
                "upload_task_id": provider_low_level["upload_task_id"],
            }
        )
        client_parameters.update(
            {
                "source_document_id": client_low_level["source_document_id"],
                "upload_task_id": client_low_level["upload_task_id"],
            }
        )
        admin_client = tuple(
            (await session.execute(client_query, client_parameters)).one()
        )
        admin_provider = tuple(
            (await session.execute(provider_query, provider_parameters)).one()
        )
    async with session_scope(
        role="f1_api", enterprise_id=local_seed.ENTERPRISE_A, sub=EMPLOYEE_SUB
    ) as session:
        owner_client = tuple(
            (await session.execute(client_query, client_parameters)).one()
        )
    async with session_scope(
        role="f1_api", enterprise_id=local_seed.ENTERPRISE_A, sub=CONSULTANT_SUB
    ) as session:
        non_owner_client = tuple(
            (await session.execute(client_query, client_parameters)).one()
        )
        non_owner_provider = tuple(
            (await session.execute(provider_query, provider_parameters)).one()
        )
    async with session_scope(
        role="f1_api", enterprise_id=local_seed.ENTERPRISE_B, sub=TENANT_B_SUB
    ) as session:
        foreign_client = tuple(
            (await session.execute(client_query, client_parameters)).one()
        )
        foreign_provider = tuple(
            (await session.execute(provider_query, provider_parameters)).one()
        )
    if admin_client != (1, 1, 1, 1, 1, 1, 1, 1, 4, 0):
        raise MaterialVerifyError("LOCAL_MATERIAL_RLS_ADMIN_CLIENT_FAILED")
    if owner_client != (1, 1, 1, 1, 1, 1, 1, 1, 4, 0):
        raise MaterialVerifyError("LOCAL_MATERIAL_RLS_OWNER_CLIENT_FAILED")
    if non_owner_client != (0, 0, 0, 0, 0, 0, 0, 0, 0, 0):
        raise MaterialVerifyError("LOCAL_MATERIAL_RLS_NON_OWNER_CLIENT_FAILED")
    # The auditor intentionally sees the derived P5 source/draft under the
    # existing review workflow, but must not see any P3 provider material row.
    if non_owner_provider != (0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0):
        raise MaterialVerifyError("LOCAL_MATERIAL_RLS_NON_OWNER_PROVIDER_FAILED")
    if foreign_client != (0, 0, 0, 0, 0, 0, 0, 0, 0, 0) or (
        foreign_provider != (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    ):
        raise MaterialVerifyError("LOCAL_MATERIAL_RLS_CROSS_TENANT_FAILED")
    if admin_provider != (1, 1, 1, 1, 1, 1, 1, 4, 1, 1, 0):
        raise MaterialVerifyError("LOCAL_MATERIAL_RLS_ADMIN_PROVIDER_FAILED")


def _verify_database_policy_guard(
    bootstrap_dsn: str,
    identifiers: MaterialRunIdentifiers,
    document_sha256: str,
) -> None:
    import psycopg

    from infra.f1 import local_seed

    actor_id = local_seed._stable_id("profile", local_seed.ADMIN_SUB)
    with psycopg.connect(bootstrap_dsn) as connection:
        try:
            connection.execute(
                "INSERT INTO f1.policy_version ("
                "id,enterprise_id,source_id,version_number,title,domain,"
                "effect_status,summary,document_version_id,document_sha256,"
                "workflow_status,created_by_user_id) VALUES ("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    uuid.uuid4(),
                    local_seed.ENTERPRISE_A,
                    identifiers.policy_source_id,
                    999_999,
                    "LOCAL_POLICY_SCOPE_GUARD",
                    "environment",
                    "unknown",
                    "Synthetic database policy scope guard verification.",
                    identifiers.client_version_id,
                    document_sha256,
                    "draft",
                    actor_id,
                ),
            )
        except psycopg.Error as error:
            connection.rollback()
            if error.diag.message_primary != "P5_POLICY_DOCUMENT_SCOPE_INVALID":
                raise MaterialVerifyError(
                    "LOCAL_MATERIAL_POLICY_GUARD_FAILED"
                ) from None
        else:
            connection.rollback()
            raise MaterialVerifyError("LOCAL_MATERIAL_POLICY_GUARD_FAILED")


def run() -> MaterialVerificationCounts:
    from infra.f1 import local_ingestion_verify as ingestion

    configuration = None
    scratch_database: str | None = None
    secret_directory: Path | None = None
    scratch_created = False
    storage = None
    client = None
    resources = None
    original_buckets = None
    scratch_bootstrap_dsn: str | None = None
    provider_idempotency_key = secrets.token_hex(24)
    client_idempotency_key = secrets.token_hex(24)
    idempotency_key_hashes = tuple(
        hashlib.sha256(key.encode("ascii")).hexdigest()
        for key in (provider_idempotency_key, client_idempotency_key)
    )
    namespace_owned = False
    pending: MaterialVerifyError | None = None
    try:
        configuration = ingestion._load_source_configuration()
        if any(
            ingestion._source_run_row_count(configuration, key_hash) != 0
            for key_hash in idempotency_key_hashes
        ):
            raise MaterialVerifyError("LOCAL_MATERIAL_SOURCE_NOT_READY")
        nonce = uuid.uuid4().hex
        scratch_database = ingestion.scratch_database_name(configuration.database, nonce)
        resources = ingestion.resource_names(configuration.database, nonce)
        ingestion._create_scratch_database(configuration, scratch_database)
        scratch_created = True
        secret_directory, scratch_bootstrap_dsn, scratch_f0_dsn = (
            ingestion._scratch_secret_directory(configuration, scratch_database)
        )
        ingestion._harden_scratch_database(scratch_bootstrap_dsn, scratch_database)
        sink = _DiscardText()
        with (
            ingestion._temporary_scratch_environment(
                scratch_database=scratch_database,
                secret_directory=secret_directory,
                f0_migration_dsn=scratch_f0_dsn,
            ),
            contextlib.redirect_stdout(sink),
            contextlib.redirect_stderr(sink),
        ):
            ingestion._migrate_scratch()
            ingestion._assert_scratch_heads(scratch_bootstrap_dsn)
            ingestion._seed_scratch(scratch_bootstrap_dsn)
            from platform_foundation.f1 import storage as storage_module

            storage = storage_module
            original_buckets = ingestion._activate_unique_buckets(storage, resources)
            client = storage._client()
            ingestion._assert_buckets_absent(client, resources)
            namespace_owned = True
            pdf_body = _synthetic_pdf()
            identifiers = asyncio.run(
                _api_smoke(
                    pdf_body,
                    provider_idempotency_key=provider_idempotency_key,
                    client_idempotency_key=client_idempotency_key,
                )
            )
            observations = tuple(
                ingestion._observe_data(
                    scratch_bootstrap_dsn,
                    ingestion.RunIdentifiers(
                        document_id=document_id,
                        version_id=version_id,
                    ),
                )
                for document_id, version_id in (
                    (
                        identifiers.provider_document_id,
                        identifiers.provider_version_id,
                    ),
                    (
                        identifiers.client_document_id,
                        identifiers.client_version_id,
                    ),
                )
            )
            expected_audit = {
                "document.version.create",
                "document.quarantine",
                "document.version.process",
                "document.version.release",
            }
            if any(
                observation.content_sha256
                != hashlib.sha256(pdf_body).hexdigest()
                or observation.source_size != len(pdf_body)
                or observation.status != "done"
                or observation.object_state != "ready"
                or observation.processing_stage != "ready"
                or observation.quarantine_status != "released"
                or observation.scan_verdict != "clean"
                or observation.scanner_engine != "clamav"
                or observation.preview_status != "ready"
                or observation.preview_kind != "page_text"
                or observation.preview_unit_count != 1
                or observation.released is not True
                or set(observation.audit_actions) != expected_audit
                for observation in observations
            ):
                raise MaterialVerifyError("LOCAL_MATERIAL_PROCESS_FAILED")
            for observation in observations:
                ingestion._verify_object_identity(storage, observation)
            asyncio.run(_verify_rls(identifiers))
            _verify_database_policy_guard(
                scratch_bootstrap_dsn,
                identifiers,
                hashlib.sha256(pdf_body).hexdigest(),
            )
    except MaterialVerifyError as error:
        pending = error
    except BaseException:
        pending = MaterialVerifyError("LOCAL_MATERIAL_INTERNAL_ERROR")

    if configuration is not None:
        try:
            if any(
                ingestion._source_run_row_count(configuration, key_hash) != 0
                for key_hash in idempotency_key_hashes
            ):
                pending = MaterialVerifyError("LOCAL_MATERIAL_SOURCE_NOT_READY")
        except BaseException:
            pending = MaterialVerifyError("LOCAL_MATERIAL_SOURCE_NOT_READY")

    cleanup_failed = False
    try:
        asyncio.run(ingestion._dispose_database_engines())
    except BaseException:
        cleanup_failed = True
    if (
        namespace_owned
        and client is not None
        and resources is not None
        and scratch_bootstrap_dsn is not None
    ):
        try:
            identities = tuple(
                identity
                for key_hash in idempotency_key_hashes
                for identity in ingestion._collect_task_objects(
                    scratch_bootstrap_dsn, key_hash
                )
            )
            ingestion._cleanup_buckets(client, resources, identities)
        except BaseException:
            cleanup_failed = True
    if storage is not None and original_buckets is not None:
        ingestion._restore_buckets(storage, original_buckets)
    if scratch_created and configuration is not None and scratch_database is not None:
        try:
            ingestion._drop_scratch_database(configuration, scratch_database)
        except BaseException:
            cleanup_failed = True
    if secret_directory is not None:
        try:
            ingestion._remove_secret_directory(secret_directory)
        except BaseException:
            cleanup_failed = True
    if cleanup_failed:
        raise MaterialVerifyError("LOCAL_MATERIAL_CLEANUP_FAILED")
    if pending is not None:
        raise pending
    return MaterialVerificationCounts()


def main() -> int:
    try:
        counts = run()
    except BaseException as error:
        reason = (
            error.reason
            if isinstance(error, MaterialVerifyError)
            else "LOCAL_MATERIAL_INTERNAL_ERROR"
        )
        print(reason, file=sys.stderr)
        return 1
    print(json.dumps(asdict(counts), sort_keys=True, separators=(",", ":")))
    print("LOCAL_MATERIAL_VERIFY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
