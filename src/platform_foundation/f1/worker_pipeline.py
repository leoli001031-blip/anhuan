"""F1 upload worker pipeline (RQ consumer).

DB task + outbox coordinate MinIO/RQ; RQ only carries the task_id.  The
worker claims a task with a CAS lease, moves it through scanning/indexing,
and acks the outbox event.  No malware scanner is wired, so every run
records the fixed ``MALWARE_SCAN_NOT_CONFIGURED`` reason; the registered-
fixture indexing step (Task 2) decides done vs. FIXTURE_ONLY_UNREGISTERED.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from .database import session_scope
from .storage import StorageError, verify_stored_object
from .upload_task import (
    claim_upload_task,
    renew_upload_lease,
)


def process_task(task_id: str) -> None:
    """RQ worker entry: process one upload task (idempotent by lease)."""
    import asyncio

    asyncio.run(_process_task(uuid.UUID(task_id)))


async def _process_task(task_id: uuid.UUID) -> None:
    # The SECURITY DEFINER claim is the only tenant discovery path and returns
    # a fresh owner token.  Every later write and every external side effect is
    # guarded by that token.
    claim = await claim_upload_task(task_id)
    if claim is None:
        return
    if not await renew_upload_lease(claim.task_id, claim.lease_token):
        return
    try:
        verify_stored_object(
            claim.object_key,
            expected_sha256=claim.content_sha256,
            expected_size=claim.source_size,
            expected_etag=claim.source_etag,
        )
    except StorageError as error:
        from . import indexing

        reason = str(error)
        await indexing.finish_claim(
            claim,
            status=(
                "retry"
                if reason in ("SOURCE_OBJECT_STAT_FAILED", "SOURCE_OBJECT_READ_FAILED")
                else "failed"
            ),
            reason=reason,
        )
        return
    except Exception:  # secret/transport failures are retryable and redacted
        from . import indexing

        await indexing.finish_claim(
            claim, status="retry", reason="SOURCE_OBJECT_STAT_FAILED"
        )
        return
    if not await renew_upload_lease(claim.task_id, claim.lease_token):
        return

    # Establish the indexing event while the exact token authorizes worker
    # RLS.  The dispatched event is deliberately NOT acked here: if this
    # process dies, its expired task lease makes that same event claimable for
    # recovery.  Terminal completion performs the ack.
    async with session_scope(
        role="f1_worker",
        enterprise_id=claim.enterprise_id,
        task_id=claim.task_id,
        lease_token=claim.lease_token,
    ) as session:
        await session.execute(
            text(
                "INSERT INTO f1.outbox "
                "(id, enterprise_id, task_id, event_type, state, payload_sha256, rq_job_id) "
                "SELECT :id, :eid, :task, 'upload.indexing', 'pending', "
                "content_sha256, 'f1-indexing-' || id::text "
                "FROM f1.upload_task WHERE id = :task "
                "ON CONFLICT (task_id, event_type) DO NOTHING"
            ),
            {"id": uuid.uuid4(), "eid": claim.enterprise_id, "task": task_id},
        )
        await session.commit()
    # Task 2: registered-fixture indexing (RAGFlow) + terminal state.
    from . import indexing

    await indexing.process_upload(
        task_id, claim.enterprise_id, lease_token=claim.lease_token
    )


def dispatch_pending_outbox() -> int:
    """Re-enqueue every pending ``upload.dispatched`` outbox event.

    The API enqueues right after reserve; if that enqueue fails (e.g. Redis
    briefly down) the outbox stays ``pending`` and this dispatcher recovers it
    on the next worker sweep.  Returns the number of tasks enqueued.
    """
    import asyncio

    from . import upload_task

    async def _claim() -> list[tuple]:
        async with session_scope(role="f1_worker") as session:
            rows = (
                await session.execute(
                    text("SELECT * FROM f1.claim_pending_dispatch(100, 60)")
                )
            ).fetchall()
            await session.commit()
            return list(rows)

    async def _complete(outbox_id: uuid.UUID, token: uuid.UUID, success: bool) -> bool:
        async with session_scope(role="f1_worker") as session:
            completed = bool(
                (
                    await session.execute(
                        text("SELECT f1.complete_dispatch(:id, :token, :success)"),
                        {"id": outbox_id, "token": token, "success": success},
                    )
                ).scalar()
            )
            await session.commit()
            return completed

    pending = asyncio.run(_claim())
    dispatched = 0
    for outbox_id, _enterprise_id, task_id, job_id, dispatch_token in pending:
        success = False
        try:
            upload_task.enqueue_upload(task_id, job_id)
            success = True
        except Exception:  # noqa: BLE001
            success = False
        if asyncio.run(_complete(outbox_id, dispatch_token, success)) and success:
            dispatched += 1
    return dispatched


__all__ = ("process_task", "dispatch_pending_outbox")
