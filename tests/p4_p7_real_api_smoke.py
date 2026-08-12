"""One-shot real PostgreSQL/API/RLS smoke for the P4-P7 prototypes.

The runner reuses the P2 UUID-scoped PostgreSQL fixture, which migrates through
``f1_0014`` and seeds synthetic tenant memberships.  Authentication is replaced
only at FastAPI's ``current_user`` dependency; tenant selection, authorization,
SQLAlchemy transactions, PostgreSQL constraints, audit writes, and FORCE RLS
remain real.

P4 intentionally captures a report version without P3 document versions.  This
is a supported basic business snapshot, not a fabricated controlled-ingestion
release.  P7 records manual evidence metadata only and executes no operational
command.  Stdout is aggregate-only and the owned scratch resources are always
cleaned precisely.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import sys
from typing import Any
import uuid

import httpx
import psycopg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.p2_real_pg_api_smoke import (
    ScratchPostgres,
    SmokeFailure,
    _dispose_database_engines,
)


METRICS = OrderedDict(
    (
        ("migration_failures", 0),
        ("catalog_failures", 0),
        ("p4_failures", 0),
        ("p5_failures", 0),
        ("p6_failures", 0),
        ("p7_failures", 0),
        ("cross_tenant_api_leaks", 0),
        ("rls_select_leaks", 0),
        ("rls_write_leaks", 0),
        ("audit_gaps", 0),
        ("external_calls", 0),
        ("cleanup_residuals", 0),
        ("unexpected_failures", 0),
    )
)


def _payload(response: httpx.Response, code: str) -> dict[str, Any]:
    try:
        value = response.json()
    except ValueError:
        raise SmokeFailure(code) from None
    if not isinstance(value, dict):
        raise SmokeFailure(code)
    return value


def _identifier(payload: dict[str, Any], code: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(payload["id"]))
    except (KeyError, TypeError, ValueError):
        raise SmokeFailure(code) from None


async def _api_smoke(
    scratch: ScratchPostgres,
    actors: dict[str, dict[str, Any]],
) -> dict[str, tuple[str, uuid.UUID]]:
    for key, value in scratch.f1_environment().items():
        os.environ[key] = value

    from fastapi import FastAPI, Header, HTTPException
    from platform_foundation.f1 import auth
    from platform_foundation.f1.api.routers import (
        p4_views_reports,
        p5_policy_workflow,
        p6_automated_quality,
        p7_local_rehearsal,
        service_cases,
    )

    app = FastAPI()
    app.include_router(service_cases.router, prefix="/api/v1/service-cases")
    app.include_router(p4_views_reports.router, prefix="/api/v1/views-reports")
    app.include_router(p5_policy_workflow.router, prefix="/api/v1/policy-workflow")
    app.include_router(
        p6_automated_quality.router,
        prefix="/api/v1/automated-quality",
    )
    app.include_router(
        p7_local_rehearsal.router,
        prefix="/api/v1/local-rehearsal",
    )

    async def synthetic_user(
        x_p4_p7_smoke_actor: str | None = Header(default=None),
    ) -> dict[str, Any]:
        actor = actors.get(x_p4_p7_smoke_actor or "")
        if actor is None:
            raise HTTPException(status_code=401, detail="SMOKE_IDENTITY_REQUIRED")
        return {"sub": actor["sub"], "roles": ()}

    app.dependency_overrides[auth.current_user] = synthetic_user
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://p4-p7.invalid",
    ) as client:

        async def request(
            actor_name: str,
            method: str,
            path: str,
            expected: int,
            *,
            body: dict[str, Any] | None = None,
            params: dict[str, Any] | None = None,
            code: str,
        ) -> httpx.Response:
            actor = actors[actor_name]
            try:
                response = await client.request(
                    method,
                    path,
                    headers={
                        "X-P4-P7-Smoke-Actor": actor_name,
                        "X-Enterprise-Id": str(actor["enterprise_id"]),
                    },
                    json=body,
                    params=params,
                )
            except Exception as error:
                origin = getattr(error, "orig", None)
                sqlstate = getattr(origin, "sqlstate", None)
                if not isinstance(sqlstate, str) or re.fullmatch(
                    r"[0-9A-Z]{5}", sqlstate
                ) is None:
                    sqlstate = "UNCLASSIFIED"
                raise SmokeFailure(f"{code}_SQLSTATE_{sqlstate}_RED") from None
            if response.status_code != expected:
                raise SmokeFailure(f"{code}_HTTP_{response.status_code}_RED")
            return response

        now = datetime.now(timezone.utc).replace(microsecond=0)

        # P4: a minimal P2 case anchors both the report and its basic snapshot.
        case_response = await request(
            "admin",
            "POST",
            "/api/v1/service-cases",
            201,
            body={
                "title": "SYNTHETIC_P4_CASE",
                "description": "SYNTHETIC_P4_SCOPE",
                "service_type": "onsite",
                "planned_start_at": (now + timedelta(days=1)).isoformat(),
                "planned_end_at": (now + timedelta(days=2)).isoformat(),
            },
            code="P4_CASE_CREATE",
        )
        case_id = _identifier(_payload(case_response, "P4_CASE_CREATE"), "P4_CASE_CREATE")

        account_response = await request(
            "admin",
            "POST",
            "/api/v1/views-reports/crm/accounts",
            201,
            body={
                "display_name": "SYNTHETIC_ACCOUNT",
                "stage": "active",
                "owner_user_id": str(actors["employee"]["user_id"]),
                "industry_note": "SYNTHETIC_INDUSTRY",
                "region_note": "SYNTHETIC_REGION",
                "next_follow_up_at": (now + timedelta(days=3)).isoformat(),
            },
            code="P4_CRM_CREATE",
        )
        account_id = _identifier(
            _payload(account_response, "P4_CRM_CREATE"), "P4_CRM_CREATE"
        )
        await request(
            "admin",
            "POST",
            f"/api/v1/views-reports/crm/accounts/{account_id}/contacts",
            201,
            body={
                "display_name": "SYNTHETIC_CONTACT",
                "role_title": "SYNTHETIC_ROLE",
                "status": "active",
            },
            code="P4_CONTACT_CREATE",
        )
        await request(
            "admin",
            "POST",
            f"/api/v1/views-reports/crm/accounts/{account_id}/follow-ups",
            201,
            body={
                "channel": "internal_note",
                "summary": "SYNTHETIC_FOLLOW_UP",
                "next_action": "SYNTHETIC_NEXT_ACTION",
                "next_due_at": (now + timedelta(days=4)).isoformat(),
            },
            code="P4_FOLLOW_UP_CREATE",
        )
        account_detail = _payload(
            await request(
                "admin",
                "GET",
                f"/api/v1/views-reports/crm/accounts/{account_id}",
                200,
                code="P4_CRM_DETAIL",
            ),
            "P4_CRM_DETAIL",
        )
        if len(account_detail.get("contacts", ())) != 1 or len(
            account_detail.get("follow_ups", ())
        ) != 1:
            raise SmokeFailure("P4_CRM_DETAIL_RED")

        report_response = await request(
            "admin",
            "POST",
            "/api/v1/views-reports/reports",
            201,
            body={"service_case_id": str(case_id), "title": "SYNTHETIC_REPORT"},
            code="P4_REPORT_CREATE",
        )
        report_id = _identifier(
            _payload(report_response, "P4_REPORT_CREATE"), "P4_REPORT_CREATE"
        )
        version_payload = _payload(
            await request(
                "admin",
                "POST",
                f"/api/v1/views-reports/reports/{report_id}/versions",
                201,
                body={
                    "change_note": "SYNTHETIC_BASIC_SNAPSHOT",
                    "document_version_ids": [],
                },
                code="P4_REPORT_VERSION_CREATE",
            ),
            "P4_REPORT_VERSION_CREATE",
        )
        report_version_id = _identifier(version_payload, "P4_REPORT_VERSION_CREATE")
        source_counts = version_payload.get("source_counts")
        artifact = version_payload.get("artifact")
        if (
            version_payload.get("version_number") != 1
            or version_payload.get("lifecycle") != "current"
            or not isinstance(source_counts, dict)
            or source_counts.get("document_versions") != 0
            or not isinstance(artifact, dict)
            or artifact.get("status") != "ready"
        ):
            raise SmokeFailure("P4_REPORT_VERSION_STATE_RED")
        await request(
            "admin",
            "GET",
            "/api/v1/views-reports/dashboard",
            200,
            code="P4_DASHBOARD",
        )
        await request(
            "tenant_b",
            "GET",
            f"/api/v1/views-reports/crm/accounts/{account_id}",
            404,
            code="CROSS_TENANT_P4_CRM",
        )
        await request(
            "tenant_b",
            "GET",
            f"/api/v1/views-reports/reports/{report_id}",
            404,
            code="CROSS_TENANT_P4_REPORT",
        )

        # P5: manager submits, a distinct auditor approves, then manager
        # publishes and opens an accepted impact with an assigned task.
        source_response = await request(
            "admin",
            "POST",
            "/api/v1/policy-workflow/sources",
            201,
            body={
                "title": "SYNTHETIC_POLICY_SOURCE",
                "publisher": "SYNTHETIC_PUBLISHER",
                "source_type": "internal",
                "jurisdiction": "SYNTHETIC_SCOPE",
                "source_reference": "SYNTHETIC_REFERENCE",
            },
            code="P5_SOURCE_CREATE",
        )
        source_id = _identifier(
            _payload(source_response, "P5_SOURCE_CREATE"), "P5_SOURCE_CREATE"
        )
        policy_version_response = await request(
            "admin",
            "POST",
            f"/api/v1/policy-workflow/sources/{source_id}/versions",
            201,
            body={
                "title": "SYNTHETIC_POLICY_VERSION",
                "domain": "safety",
                "effect_status": "effective",
                "issued_on": now.date().isoformat(),
                "effective_from": now.date().isoformat(),
                "summary": "SYNTHETIC_POLICY_SUMMARY",
            },
            code="P5_VERSION_CREATE",
        )
        policy_version_id = _identifier(
            _payload(policy_version_response, "P5_VERSION_CREATE"),
            "P5_VERSION_CREATE",
        )
        submitted = _payload(
            await request(
                "admin",
                "POST",
                f"/api/v1/policy-workflow/versions/{policy_version_id}/submit",
                200,
                body={"comment": "SYNTHETIC_SUBMISSION"},
                code="P5_SUBMIT",
            ),
            "P5_SUBMIT",
        )
        approved = _payload(
            await request(
                "consultant",
                "POST",
                f"/api/v1/policy-workflow/versions/{policy_version_id}/approve",
                200,
                body={"comment": "SYNTHETIC_APPROVAL"},
                code="P5_APPROVE",
            ),
            "P5_APPROVE",
        )
        published = _payload(
            await request(
                "admin",
                "POST",
                f"/api/v1/policy-workflow/versions/{policy_version_id}/publish",
                200,
                body={"comment": "SYNTHETIC_PUBLICATION"},
                code="P5_PUBLISH",
            ),
            "P5_PUBLISH",
        )
        if (
            submitted.get("workflow_status") != "in_review"
            or approved.get("workflow_status") != "approved"
            or published.get("workflow_status") != "published"
        ):
            raise SmokeFailure("P5_WORKFLOW_STATE_RED")

        impact_response = await request(
            "consultant",
            "POST",
            "/api/v1/policy-workflow/impacts",
            201,
            body={
                "policy_version_id": str(policy_version_id),
                "domain": "safety",
                "scope_note": "SYNTHETIC_IMPACT_SCOPE",
                "priority": "high",
            },
            code="P5_IMPACT_CREATE",
        )
        impact_id = _identifier(
            _payload(impact_response, "P5_IMPACT_CREATE"), "P5_IMPACT_CREATE"
        )
        accepted = _payload(
            await request(
                "consultant",
                "PATCH",
                f"/api/v1/policy-workflow/impacts/{impact_id}",
                200,
                body={"status": "accepted"},
                code="P5_IMPACT_ACCEPT",
            ),
            "P5_IMPACT_ACCEPT",
        )
        if accepted.get("status") != "accepted":
            raise SmokeFailure("P5_IMPACT_STATE_RED")
        task_response = await request(
            "consultant",
            "POST",
            f"/api/v1/policy-workflow/impacts/{impact_id}/tasks",
            201,
            body={
                "title": "SYNTHETIC_IMPACT_TASK",
                "owner_user_id": str(actors["employee"]["user_id"]),
                "due_at": (now + timedelta(days=5)).isoformat(),
            },
            code="P5_TASK_CREATE",
        )
        impact_task_id = _identifier(
            _payload(task_response, "P5_TASK_CREATE"), "P5_TASK_CREATE"
        )
        impact_detail = _payload(
            await request(
                "admin",
                "GET",
                f"/api/v1/policy-workflow/impacts/{impact_id}",
                200,
                code="P5_IMPACT_DETAIL",
            ),
            "P5_IMPACT_DETAIL",
        )
        if len(impact_detail.get("tasks", ())) != 1:
            raise SmokeFailure("P5_IMPACT_DETAIL_RED")
        await request(
            "tenant_b",
            "GET",
            f"/api/v1/policy-workflow/sources/{source_id}",
            404,
            code="CROSS_TENANT_P5_SOURCE",
        )
        await request(
            "tenant_b",
            "GET",
            f"/api/v1/policy-workflow/versions/{policy_version_id}",
            404,
            code="CROSS_TENANT_P5_VERSION",
        )

        # P6: deterministic synthetic failure creates one disagreement, which
        # is then dispositioned by the auditor without changing the verdict.
        suite_response = await request(
            "admin",
            "POST",
            "/api/v1/automated-quality/suites",
            201,
            body={"name": "SYNTHETIC_SUITE", "category": "qa"},
            code="P6_SUITE_CREATE",
        )
        suite_id = _identifier(
            _payload(suite_response, "P6_SUITE_CREATE"), "P6_SUITE_CREATE"
        )
        await request(
            "admin",
            "POST",
            f"/api/v1/automated-quality/suites/{suite_id}/scenarios",
            201,
            body={
                "scenario_key": "synthetic.disagreement",
                "scenario_type": "disagreement_max",
                "severity": "high",
                "oracle_config": {
                    "schema_version": 1,
                    "max_score": 0.1,
                    "disagreement_kind": "parser",
                },
                "synthetic_observation": {
                    "schema_version": 1,
                    "left_sha256": "b" * 64,
                    "right_sha256": "c" * 64,
                    "score": 0.5,
                },
                "enabled": True,
            },
            code="P6_SCENARIO_CREATE",
        )
        quality_run = _payload(
            await request(
                "admin",
                "POST",
                f"/api/v1/automated-quality/suites/{suite_id}/runs",
                201,
                code="P6_RUN_CREATE",
            ),
            "P6_RUN_CREATE",
        )
        quality_run_id = _identifier(quality_run, "P6_RUN_CREATE")
        results = quality_run.get("results")
        if (
            quality_run.get("status") != "failed"
            or quality_run.get("total_count") != 1
            or quality_run.get("failed_count") != 1
            or not isinstance(results, list)
            or len(results) != 1
            or not isinstance(results[0].get("disagreements"), list)
            or len(results[0]["disagreements"]) != 1
        ):
            raise SmokeFailure("P6_RUN_STATE_RED")
        disagreement_id = _identifier(
            results[0]["disagreements"][0], "P6_DISAGREEMENT_ID"
        )
        reviewed = _payload(
            await request(
                "consultant",
                "PATCH",
                f"/api/v1/automated-quality/disagreements/{disagreement_id}",
                200,
                body={
                    "review_status": "acknowledged",
                    "review_note": "SYNTHETIC_REVIEW",
                },
                code="P6_DISAGREEMENT_REVIEW",
            ),
            "P6_DISAGREEMENT_REVIEW",
        )
        if reviewed.get("review_status") != "acknowledged":
            raise SmokeFailure("P6_DISAGREEMENT_STATE_RED")
        await request(
            "tenant_b",
            "GET",
            f"/api/v1/automated-quality/suites/{suite_id}",
            404,
            code="CROSS_TENANT_P6_SUITE",
        )
        await request(
            "tenant_b",
            "GET",
            f"/api/v1/automated-quality/runs/{quality_run_id}",
            404,
            code="CROSS_TENANT_P6_RUN",
        )

        # P7: manual metadata only.  A failed check must close the run as
        # failed and require rollback; no shell, Docker, or deployment runs.
        plan_response = await request(
            "admin",
            "POST",
            "/api/v1/local-rehearsal/plans",
            201,
            body={"name": "SYNTHETIC_LOCAL_REHEARSAL"},
            code="P7_PLAN_CREATE",
        )
        plan_id = _identifier(
            _payload(plan_response, "P7_PLAN_CREATE"), "P7_PLAN_CREATE"
        )
        await request(
            "admin",
            "POST",
            f"/api/v1/local-rehearsal/plans/{plan_id}/checks",
            201,
            body={
                "check_key": "synthetic.rollback",
                "category": "rollback",
                "label": "SYNTHETIC_ROLLBACK_CHECK",
                "sequence_no": 1,
                "required": True,
                "enabled": True,
            },
            code="P7_CHECK_CREATE",
        )
        rehearsal_run = _payload(
            await request(
                "admin",
                "POST",
                f"/api/v1/local-rehearsal/plans/{plan_id}/runs",
                201,
                code="P7_RUN_CREATE",
            ),
            "P7_RUN_CREATE",
        )
        rehearsal_run_id = _identifier(rehearsal_run, "P7_RUN_CREATE")
        rehearsal_results = rehearsal_run.get("results")
        if (
            rehearsal_run.get("status") != "running"
            or not isinstance(rehearsal_results, list)
            or len(rehearsal_results) != 1
        ):
            raise SmokeFailure("P7_RUN_INITIAL_STATE_RED")
        rehearsal_result_id = _identifier(
            rehearsal_results[0], "P7_RESULT_ID"
        )
        await request(
            "consultant",
            "PATCH",
            f"/api/v1/local-rehearsal/runs/{rehearsal_run_id}/checks/{rehearsal_result_id}",
            200,
            body={
                "status": "failed",
                "reason_code": "MANUAL_CHECK_FAILED",
                "evidence_sha256": "d" * 64,
            },
            code="P7_RESULT_RECORD",
        )
        completed = _payload(
            await request(
                "admin",
                "POST",
                f"/api/v1/local-rehearsal/runs/{rehearsal_run_id}/complete",
                200,
                code="P7_RUN_COMPLETE",
            ),
            "P7_RUN_COMPLETE",
        )
        if (
            completed.get("status") != "failed"
            or completed.get("failed_count") != 1
            or completed.get("pending_count") != 0
            or completed.get("rollback_required") is not True
        ):
            raise SmokeFailure("P7_RUN_TERMINAL_STATE_RED")
        await request(
            "tenant_b",
            "GET",
            f"/api/v1/local-rehearsal/plans/{plan_id}",
            404,
            code="CROSS_TENANT_P7_PLAN",
        )
        await request(
            "tenant_b",
            "GET",
            f"/api/v1/local-rehearsal/runs/{rehearsal_run_id}",
            404,
            code="CROSS_TENANT_P7_RUN",
        )

    return {
        "crm_account": ("crm_account", account_id),
        "business_report": ("business_report", report_id),
        "business_report_version": (
            "business_report_version",
            report_version_id,
        ),
        "policy_source": ("policy_source", source_id),
        "policy_version": ("policy_version", policy_version_id),
        "policy_impact_task": ("policy_impact_task", impact_task_id),
        "quality_suite": ("quality_suite", suite_id),
        "quality_run": ("quality_run", quality_run_id),
        "rehearsal_plan": ("rehearsal_plan", plan_id),
        "rehearsal_run": ("rehearsal_run", rehearsal_run_id),
    }


def _direct_rls_and_audit(
    scratch: ScratchPostgres,
    actors: dict[str, dict[str, Any]],
    identifiers: dict[str, tuple[str, uuid.UUID]],
) -> None:
    tenant_b = actors["tenant_b"]
    with psycopg.connect(**scratch.api_kwargs()) as connection:
        connection.execute(
            "SELECT set_config('f1.enterprise_id', %s, true)",
            (str(tenant_b["enterprise_id"]),),
        )
        connection.execute(
            "SELECT set_config('f1.sub', %s, true)",
            (tenant_b["sub"],),
        )
        for table, identifier in identifiers.values():
            visible = connection.execute(
                f"SELECT count(*) FROM f1.{table} WHERE id=%s",
                (identifier,),
            ).fetchone()
            if visible is None or int(visible[0]) != 0:
                METRICS["rls_select_leaks"] += 1
                raise SmokeFailure("RLS_SELECT_RED")
            changed = connection.execute(
                f"UPDATE f1.{table} SET id=id WHERE id=%s",
                (identifier,),
            )
            if changed.rowcount != 0:
                METRICS["rls_write_leaks"] += 1
                raise SmokeFailure("RLS_WRITE_RED")
        connection.rollback()

    required_actions = {
        "crm.account.created",
        "business_report.version_created",
        "policy.version.published",
        "policy.impact_task.created",
        "quality.run.completed",
        "quality.disagreement.acknowledged",
        "rehearsal.result.recorded",
        "rehearsal.run.failed",
    }
    with psycopg.connect(**scratch.bootstrap_kwargs()) as connection:
        observed = {
            str(row[0])
            for row in connection.execute(
                "SELECT action FROM f1.audit_log WHERE enterprise_id=%s",
                (actors["admin"]["enterprise_id"],),
            ).fetchall()
        }
    missing = required_actions - observed
    if missing:
        METRICS["audit_gaps"] = len(missing)
        raise SmokeFailure("AUDIT_GAP_RED")


def _metric_for(code: str) -> str:
    if code.startswith(("ROOT_MIGRATION", "F1_MIGRATION", "MIGRATION")):
        return "migration_failures"
    if "CATALOG" in code or "ROLE" in code or "GRANT" in code:
        return "catalog_failures"
    if code.startswith("P4"):
        return "p4_failures"
    if code.startswith("P5"):
        return "p5_failures"
    if code.startswith("P6"):
        return "p6_failures"
    if code.startswith("P7"):
        return "p7_failures"
    if code.startswith("CROSS_TENANT"):
        return "cross_tenant_api_leaks"
    if code.startswith("RLS_SELECT"):
        return "rls_select_leaks"
    if code.startswith("RLS_WRITE"):
        return "rls_write_leaks"
    if code.startswith("AUDIT"):
        return "audit_gaps"
    return "unexpected_failures"


def _render(status: str, reason: str | None = None) -> None:
    print(status)
    if reason is not None:
        print(f"reason={reason}")
    for name, value in METRICS.items():
        print(f"{name}={value}")


def main() -> int:
    scratch: ScratchPostgres | None = None
    primary_reason: str | None = None
    success = False
    try:
        scratch = ScratchPostgres()
        scratch.start()
        scratch.migrate()
        scratch.validate_catalog()
        actors = scratch.seed()
        identifiers = asyncio.run(_api_smoke(scratch, actors))
        _direct_rls_and_audit(scratch, actors, identifiers)
        success = True
    except SmokeFailure as error:
        primary_reason = error.code
        metric = _metric_for(error.code)
        if METRICS[metric] == 0:
            METRICS[metric] = 1
    except Exception:
        primary_reason = "UNEXPECTED_RED"
        METRICS["unexpected_failures"] = 1
    finally:
        if scratch is not None:
            try:
                asyncio.run(_dispose_database_engines())
            except Exception:
                METRICS["cleanup_residuals"] += 1
            METRICS["cleanup_residuals"] += scratch.cleanup()

    if success and all(value == 0 for value in METRICS.values()):
        _render("P4_P7_REAL_PG_API_RLS_SMOKE_PASSED_NOT_RELEASE_VERIFIED")
        return 0
    _render(
        "P4_P7_REAL_PG_API_RLS_SMOKE_REJECTED",
        primary_reason or "CLEANUP_RED",
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
