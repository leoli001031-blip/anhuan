"""PostgreSQL-to-RQ dispatcher for durable material-pipeline deliveries."""
from __future__ import annotations

import asyncio

from .queue import enqueue_durable_delivery
from .repository import claim_due_deliveries, finish_delivery


def _retry_delay(attempt: int) -> int:
    return min(900, 5 * (2 ** min(max(attempt - 1, 0), 8)))


def dispatch_pending_deliveries() -> int:
    """Claim body-free DB identities and rebuild their deterministic RQ jobs."""
    claims = asyncio.run(claim_due_deliveries())
    dispatched = 0
    for claim in claims:
        try:
            enqueue_durable_delivery(
                delivery_id=claim.id,
                dispatch_token=claim.dispatch_token,
            )
        except Exception:
            # An unavailable Redis leaves durable truth retryable.  No queue
            # exception or payload is persisted.
            asyncio.run(
                finish_delivery(
                    claim.id,
                    claim.dispatch_token,
                    outcome="retry",
                    reason_code="MATERIAL_PIPELINE_QUEUE_UNAVAILABLE",
                    retry_seconds=_retry_delay(claim.attempt),
                    runtime_role="f1_worker",
                )
            )
            continue
        dispatched += 1
    return dispatched


__all__ = ("dispatch_pending_deliveries",)
