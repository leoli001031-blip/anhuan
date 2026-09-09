"""Reconstruct lost queue messages from the native extraction job table."""
import asyncio
import uuid

from . import queue, repository


def dispatch_pending_native_jobs() -> int:
    claims = asyncio.run(repository.claim_due_jobs())
    dispatched = 0
    for claim in claims:
        job_id, token = uuid.UUID(str(claim["job_id"])), uuid.UUID(str(claim["lease_token"]))
        try:
            queue.enqueue(job_id, token)
        except Exception:
            asyncio.run(repository.finish_failure(job_id, token,
                reason="NATIVE_QUEUE_UNAVAILABLE",
                retry_seconds=min(900, 5 * 2 ** min(max(claim["attempt"] - 1, 0), 8))))
            continue
        dispatched += 1
    return dispatched
