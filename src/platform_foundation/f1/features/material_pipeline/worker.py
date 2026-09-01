"""RQ entry points for the split-credential automatic material pipeline."""
from __future__ import annotations

import asyncio
import os
import uuid

from rq import get_current_job
from sqlalchemy import text

from ...auth import Tenant, memberships_for_sub
from ...database import session_scope
from . import repository as delivery_repository
from .local_index import run_local_index_job
from .queue import (
    enqueue_reconcile_stage,
    enqueue_recovery_sweep,
    enqueue_report_stage,
)


_REPORT_MANUAL_WAIT_REASONS = frozenset(
    {
        "REPORT_CLIENT_BINDING_REQUIRED",
        "REPORT_PROVIDER_SOURCES_MISSING",
        "REPORT_CLIENT_SOURCES_EMPTY",
        "REPORT_SOURCES_INCOMPLETE",
        "REPORT_REVIEW_REQUIRED",
    }
)


def _mark_current_failure(reason_code: str) -> None:
    current = get_current_job()
    if current is None:
        return
    current.meta["reason_code"] = reason_code
    current.save_meta()


async def _durable_index_status(
    job_id: uuid.UUID,
) -> tuple[str, uuid.UUID, uuid.UUID] | None:
    """Read only the named material job through the worker target RLS seam."""
    async with session_scope(role="f1_worker") as session:
        await session.execute(
            text("SELECT set_config('f1.material_rag_job_id',:id,true)"),
            {"id": str(job_id)},
        )
        await session.execute(
            text("SELECT set_config('f1.material_rag_lease_token',:token,true)"),
            {"token": str(uuid.uuid4())},
        )
        row = (
            await session.execute(
                text(
                    "SELECT status,enterprise_id,document_version_id "
                    "FROM f1.material_rag_job WHERE id=:id"
                ),
                {"id": job_id},
            )
        ).first()
    if row is None:
        return None
    return str(row[0]), row[1], row[2]


async def _run_local_index_stage(
    index_job_id: uuid.UUID,
    enterprise_id: uuid.UUID,
    provider_sub: str,
    version_id: uuid.UUID,
) -> None:
    if (
        os.environ.get("F1_MATERIAL_AUTO_PIPELINE_LOCAL") != "1"
        or os.environ.get("F1_LOCAL_ENGINEERING") != "1"
    ):
        _mark_current_failure("MATERIAL_PIPELINE_DISABLED")
        raise RuntimeError("MATERIAL_PIPELINE_DISABLED")
    outcome = await run_local_index_job(
        index_job_id,
        worker_id=f"auto-pipeline:{version_id.hex}",
    )
    durable = await _durable_index_status(index_job_id)
    if durable is None:
        raise RuntimeError("MATERIAL_LOCAL_INDEX_STATUS_UNAVAILABLE")
    status, durable_enterprise_id, durable_version_id = durable
    if (
        durable_enterprise_id != enterprise_id
        or durable_version_id != version_id
    ):
        raise RuntimeError("MATERIAL_PIPELINE_IDENTITY_MISMATCH")
    if status == "done":
        # Either continuation can recover the other one's commit/enqueue gap.
        enqueue_reconcile_stage(
            enterprise_id=enterprise_id,
            provider_sub=provider_sub,
            version_id=version_id,
        )
        enqueue_recovery_sweep(
            enterprise_id=enterprise_id,
            provider_sub=provider_sub,
        )
        enqueue_report_stage(
            enterprise_id=enterprise_id,
            provider_sub=provider_sub,
            version_id=version_id,
        )
        return
    if status == "failed" or outcome.kind == "FAILED":
        return
    # RQ Retry is the recovery clock for retry_wait, an active competing
    # lease, or a transient finish/dispatch gap.  Only fixed text is raised.
    raise RuntimeError("MATERIAL_LOCAL_INDEX_NOT_DONE")


async def _provider_tenant(
    enterprise_id: uuid.UUID, provider_sub: str
) -> Tenant | None:
    memberships = await memberships_for_sub(provider_sub)
    membership = next(
        (
            item
            for item in memberships
            if item.get("enterprise_id") == str(enterprise_id)
            and item.get("role") in {"super_admin", "enterprise_admin"}
        ),
        None,
    )
    if membership is None:
        return None
    role = str(membership["role"])
    return Tenant(
        enterprise_id=enterprise_id,
        sub=provider_sub,
        roles=(role,),
        role=role,
    )


async def _run_report_stage(
    enterprise_id: uuid.UUID,
    provider_sub: str,
    version_id: uuid.UUID,
) -> None:
    tenant = await _provider_tenant(enterprise_id, provider_sub)
    if tenant is None:
        _mark_current_failure("REPORT_ACTOR_REVOKED")
        raise RuntimeError("REPORT_ACTOR_REVOKED")
    # Import here so the worker-role process never needs the API/report path.
    from .coordinator import dispatch_report_after_index

    await dispatch_report_after_index(tenant, version_id)


async def _run_reconcile_stage(
    enterprise_id: uuid.UUID,
    provider_sub: str,
    version_id: uuid.UUID,
) -> None:
    tenant = await _provider_tenant(enterprise_id, provider_sub)
    if tenant is None:
        _mark_current_failure("REPORT_ACTOR_REVOKED")
        raise RuntimeError("REPORT_ACTOR_REVOKED")
    from .coordinator import advance_auto_pipeline

    await advance_auto_pipeline(
        tenant, version_id, _delivery_rearm=False
    )


async def _run_recovery_sweep(
    enterprise_id: uuid.UUID,
    provider_sub: str,
) -> None:
    tenant = await _provider_tenant(enterprise_id, provider_sub)
    if tenant is None:
        _mark_current_failure("REPORT_ACTOR_REVOKED")
        raise RuntimeError("REPORT_ACTOR_REVOKED")
    from .coordinator import sweep_auto_pipeline

    await sweep_auto_pipeline(tenant)


def _delivery_resolution(result) -> tuple[str, str | None, int | None]:
    """Map derived pipeline state to the fenced PostgreSQL delivery state."""
    if not result.enabled:
        return "blocked", "MATERIAL_PIPELINE_DISABLED", None
    for stage, fallback in (
        (result.ingestion, "INGESTION_UNAVAILABLE"),
        (result.analysis, "MATERIAL_ANALYSIS_FAILED"),
    ):
        if stage.status == "failed":
            return "blocked", stage.reason_code or fallback, None
    if result.index.status == "disabled":
        return "blocked", result.index.reason_code or "LOCAL_INDEX_DISABLED", None
    if result.index.status == "failed":
        return "retry", result.index.reason_code or "MATERIAL_INDEX_FAILED", 30
    if result.index.status not in {"ready", "skipped"}:
        return "retry", result.index.reason_code or "MATERIAL_INDEX_PENDING", 15
    if result.report.status in {"ready", "skipped"}:
        return "done", None, None
    if (
        result.report.status == "pending"
        and result.report.reason_code in _REPORT_MANUAL_WAIT_REASONS
    ):
        # These prerequisites require an external/admin transition.  A later
        # upload or explicit /process replay re-arms this stable identity.
        return "done", None, None
    if result.report.status == "disabled":
        return (
            "blocked",
            result.report.reason_code or "REPORT_GENERATION_DISABLED",
            None,
        )
    if result.report.status == "failed":
        return (
            "retry",
            result.report.reason_code or "REPORT_GENERATION_FAILED",
            30,
        )
    return "retry", result.report.reason_code or "REPORT_GENERATION_PENDING", 15


def _retry_delay(base_seconds: int, attempt: int) -> int:
    return min(900, max(base_seconds, 2 ** min(max(attempt, 0), 9)))


async def _run_durable_delivery(
    delivery_id: uuid.UUID, dispatch_token: uuid.UUID
) -> None:
    claim = await delivery_repository.read_delivery_claim(
        delivery_id, dispatch_token
    )
    if claim is None:
        return
    try:
        tenant = await _provider_tenant(claim.enterprise_id, claim.actor_sub)
        if tenant is None:
            await delivery_repository.finish_delivery(
                claim.id,
                claim.dispatch_token,
                outcome="blocked",
                reason_code="REPORT_ACTOR_REVOKED",
            )
            return

        from .coordinator import (
            advance_auto_pipeline,
            dispatch_report_after_index,
        )

        result = await advance_auto_pipeline(
            tenant,
            claim.document_version_id,
            _delivery_rearm=None,
        )
        if result.index.status == "ready":
            # Execute the body-free continuation under the report worker's
            # API credential.  The delivery remains live until the resulting
            # durable report job reaches a stable state, so a Redis flush is
            # repaired by the next PostgreSQL dispatch lease.
            result = await dispatch_report_after_index(
                tenant, claim.document_version_id
            )
        outcome, reason, retry_seconds = _delivery_resolution(result)
        if retry_seconds is not None:
            retry_seconds = _retry_delay(retry_seconds, claim.attempt)
        await delivery_repository.finish_delivery(
            claim.id,
            claim.dispatch_token,
            outcome=outcome,
            reason_code=reason,
            retry_seconds=retry_seconds,
        )
    except Exception:
        # Persist only a fixed reason; RQ/SQL exception text is not durable
        # data and is deliberately not copied into this boundary.
        await delivery_repository.finish_delivery(
            claim.id,
            claim.dispatch_token,
            outcome="retry",
            reason_code="MATERIAL_PIPELINE_DELIVERY_FAILED",
            retry_seconds=_retry_delay(30, claim.attempt),
        )


def run_local_index_stage(
    index_job_id: str,
    enterprise_id: str,
    provider_sub: str,
    version_id: str,
) -> None:
    asyncio.run(
        _run_local_index_stage(
            uuid.UUID(index_job_id),
            uuid.UUID(enterprise_id),
            provider_sub,
            uuid.UUID(version_id),
        )
    )


def run_report_stage(
    enterprise_id: str,
    provider_sub: str,
    version_id: str,
) -> None:
    asyncio.run(
        _run_report_stage(
            uuid.UUID(enterprise_id), provider_sub, uuid.UUID(version_id)
        )
    )


def run_reconcile_stage(
    enterprise_id: str,
    provider_sub: str,
    version_id: str,
) -> None:
    asyncio.run(
        _run_reconcile_stage(
            uuid.UUID(enterprise_id), provider_sub, uuid.UUID(version_id)
        )
    )


def run_recovery_sweep(
    enterprise_id: str,
    provider_sub: str,
) -> None:
    asyncio.run(_run_recovery_sweep(uuid.UUID(enterprise_id), provider_sub))


def run_durable_delivery(delivery_id: str, dispatch_token: str) -> None:
    """RQ entrypoint; executable identities are reloaded from PostgreSQL."""
    asyncio.run(
        _run_durable_delivery(
            uuid.UUID(delivery_id), uuid.UUID(dispatch_token)
        )
    )


__all__ = (
    "run_durable_delivery",
    "run_local_index_stage",
    "run_reconcile_stage",
    "run_recovery_sweep",
    "run_report_stage",
)
