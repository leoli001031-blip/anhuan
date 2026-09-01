"""PostgreSQL-to-RQ dispatcher for controlled-ingestion deliveries."""
from __future__ import annotations

import asyncio

from .delivery_queue import enqueue_durable_delivery
from .delivery_repository import (
    claim_due_deliveries,
    finish_delivery,
    purge_expired_ocr_checkpoints,
)


def _retry_delay(attempt: int) -> int:
    return min(3600, 5 * (2 ** min(max(attempt - 1, 0), 9)))


def dispatch_pending_ingestion_deliveries() -> int:
    """Purge expired OCR ciphertext, then rebuild due RQ deliveries."""
    asyncio.run(purge_expired_ocr_checkpoints())
    claims = asyncio.run(claim_due_deliveries())
    dispatched = 0
    for claim in claims:
        try:
            enqueue_durable_delivery(
                delivery_id=claim.id,
                dispatch_token=claim.dispatch_token,
            )
        except Exception:
            asyncio.run(
                finish_delivery(
                    claim.id,
                    claim.dispatch_token,
                    outcome="retry",
                    reason_code="MATERIAL_INGESTION_QUEUE_UNAVAILABLE",
                    retry_seconds=_retry_delay(claim.attempt),
                    runtime_role="f1_worker",
                )
            )
            continue
        dispatched += 1
    return dispatched


__all__ = ("dispatch_pending_ingestion_deliveries",)
