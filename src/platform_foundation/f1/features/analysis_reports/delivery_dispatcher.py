"""PostgreSQL-to-RQ dispatcher for durable report generation deliveries."""
from __future__ import annotations

import asyncio

from .delivery_repository import claim_due_deliveries, finish_delivery
from .queue import enqueue_generation


def _retry_delay(attempt: int) -> int:
    return min(900, 5 * (2 ** min(max(attempt - 1, 0), 8)))


def dispatch_pending_report_deliveries() -> int:
    """Claim body-free identities and enqueue one token-fenced RQ attempt."""
    claims = asyncio.run(claim_due_deliveries())
    dispatched = 0
    for claim in claims:
        try:
            enqueue_generation(claim.id, claim.dispatch_token)
        except Exception:
            asyncio.run(
                finish_delivery(
                    claim.id,
                    claim.dispatch_token,
                    outcome="retry",
                    reason_code="REPORT_QUEUE_DISPATCH_FAILED",
                    retry_seconds=_retry_delay(claim.attempt),
                    runtime_role="f1_worker",
                )
            )
            continue
        dispatched += 1
    return dispatched


__all__ = ("dispatch_pending_report_deliveries",)
