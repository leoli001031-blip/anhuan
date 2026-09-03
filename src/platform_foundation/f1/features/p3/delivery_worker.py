"""Dedicated RQ worker for durable scan, preview, OCR, and analysis resume."""
from __future__ import annotations

import asyncio
import re
import socket
import uuid

from fastapi import HTTPException
from redis import Redis
from rq import Queue, Worker

from ...auth import Tenant, memberships_for_sub
from ..material_intake.service import get_material_analysis
from .contracts import IngestionError, public_reason_code, reason_is_retryable
from .delivery_queue import QUEUE_NAME, REDIS_URL
from .delivery_repository import (
    delivery_enabled,
    finish_delivery,
    read_delivery_claim,
)
from .processor import resume_controlled_ingestion
from .service import get_version


_MANAGER_ROLES = frozenset({"super_admin", "enterprise_admin", "plant_admin"})
_OWNER_CHARS = re.compile(r"[^A-Za-z0-9_.:-]+")


def _worker_name() -> str:
    host = _OWNER_CHARS.sub("-", socket.gethostname()).strip("-") or "unknown"
    return f"material-ingestion.{host[:80]}"


def _retry_delay(
    base_seconds: int, attempt: int, *, cap_seconds: int = 3600
) -> int:
    """Bounded exponential backoff.

    The ingestion path passes ``cap_seconds=120``: these retries sit behind a
    user-visible upload, so a transient cloud-OCR/engine failure should retry
    within two minutes instead of the infrastructure-grade one-hour cap used
    by dispatcher-internal failures.
    """
    return min(cap_seconds, max(base_seconds, 2 ** min(max(attempt, 0), 11)))


async def _manager_tenant(
    enterprise_id: uuid.UUID, actor_sub: str
) -> Tenant | None:
    memberships = await memberships_for_sub(actor_sub)
    membership = next(
        (
            item
            for item in memberships
            if item.get("enterprise_id") == str(enterprise_id)
            and item.get("role") in _MANAGER_ROLES
        ),
        None,
    )
    if membership is None:
        return None
    role = str(membership["role"])
    return Tenant(
        enterprise_id=enterprise_id,
        sub=actor_sub,
        roles=(role,),
        role=role,
    )


async def _resolved_outcome(
    tenant: Tenant, version_id: uuid.UUID
) -> tuple[str, str | None, int | None]:
    version = await get_version(tenant, version_id)
    if version.reason_code and version.retryable:
        return "retry", version.reason_code, 15
    if version.workflow_status in {"blocked", "failed"}:
        return "blocked", version.reason_code or "INGESTION_UNAVAILABLE", None
    if version.workflow_status != "ready":
        return "retry", version.reason_code or "INGESTION_PROCESSING", 15
    if version.content_type != "application/pdf":
        return "done", None, None

    try:
        analysis = await get_material_analysis(tenant, version_id)
    except HTTPException as error:
        if error.status_code == 404:
            return "retry", "MATERIAL_ANALYSIS_PENDING", 30
        raise
    if analysis.status in {"ready", "confirmed"} and not any(
        page.ocr_required for page in analysis.pages
    ):
        return "done", None, None
    reason = analysis.reason_code or "MATERIAL_ANALYSIS_FAILED"
    if reason_is_retryable(reason):
        return "retry", reason, 30
    return "blocked", reason, None


async def _run_durable_ingestion(
    delivery_id: uuid.UUID, dispatch_token: uuid.UUID
) -> None:
    claim = await read_delivery_claim(delivery_id, dispatch_token)
    if claim is None:
        return
    if not delivery_enabled():
        await finish_delivery(
            claim.id,
            claim.dispatch_token,
            outcome="blocked",
            reason_code="MATERIAL_INGESTION_DISABLED",
        )
        return
    try:
        tenant = await _manager_tenant(claim.enterprise_id, claim.actor_sub)
        if tenant is None:
            await finish_delivery(
                claim.id,
                claim.dispatch_token,
                outcome="blocked",
                reason_code="MATERIAL_INGESTION_ACTOR_REVOKED",
            )
            return

        await resume_controlled_ingestion(
            tenant, claim.document_version_id
        )
        outcome, reason, retry_seconds = await _resolved_outcome(
            tenant, claim.document_version_id
        )
        if retry_seconds is not None:
            retry_seconds = _retry_delay(
                retry_seconds, claim.attempt, cap_seconds=120
            )
        finished = await finish_delivery(
            claim.id,
            claim.dispatch_token,
            outcome=outcome,
            reason_code=reason,
            retry_seconds=retry_seconds,
        )
        if finished and outcome == "done" and tenant.role in {
            "super_admin",
            "enterprise_admin",
        }:
            # Successful analysis already registers the downstream delivery in
            # the same PostgreSQL transaction.  This call is only a low-latency
            # nudge; failure cannot roll ingestion truth back.
            try:
                from ..material_pipeline.coordinator import (
                    advance_auto_pipeline,
                    auto_pipeline_enabled,
                )

                if auto_pipeline_enabled():
                    await advance_auto_pipeline(
                        tenant,
                        claim.document_version_id,
                        _delivery_rearm=False,
                    )
            except Exception:
                pass
    except IngestionError as error:
        retryable = reason_is_retryable(error.code)
        reason = public_reason_code(error.code) or "INGESTION_UNAVAILABLE"
        await finish_delivery(
            claim.id,
            claim.dispatch_token,
            outcome="retry" if retryable else "blocked",
            reason_code=reason,
            retry_seconds=(
                _retry_delay(30, claim.attempt, cap_seconds=120)
                if retryable
                else None
            ),
        )
    except Exception:
        await finish_delivery(
            claim.id,
            claim.dispatch_token,
            outcome="retry",
            reason_code="MATERIAL_INGESTION_DELIVERY_FAILED",
            retry_seconds=_retry_delay(30, claim.attempt, cap_seconds=120),
        )


def run_durable_ingestion(delivery_id: str, dispatch_token: str) -> None:
    """RQ entrypoint; tenant, version, and actor are reloaded from PostgreSQL."""
    asyncio.run(
        _run_durable_ingestion(
            uuid.UUID(delivery_id), uuid.UUID(dispatch_token)
        )
    )


def main() -> int:
    connection = Redis.from_url(REDIS_URL)
    queue = Queue(QUEUE_NAME, connection=connection)
    Worker(
        [queue],
        connection=connection,
        name=_worker_name(),
    ).work(with_scheduler=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("main", "run_durable_ingestion")
