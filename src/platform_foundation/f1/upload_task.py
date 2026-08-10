"""F1 upload task: persistent DB-backed pipeline coordinated via outbox + RQ.

The API writes the task + outbox event to PostgreSQL, then enqueues a job
that carries only the ``task_id``.  The worker updates state in PostgreSQL
and acks the outbox event.  ``_TASKS`` (in-memory registry) is gone: task
state lives only in ``f1.upload_task``, so worker restarts recover pending
work and duplicate deliveries are idempotent.
"""
from __future__ import annotations

import asyncio
import os
import socket
import uuid
from dataclasses import dataclass

from redis import Redis
from rq import Queue
from rq.job import JobStatus
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .database import session_scope
from .storage import opaque_object_key

from .config import redis_url as _redis_url

REDIS_URL = _redis_url()
QUEUE_NAME = "anhuan-f1-uploads"

# Lease duration for worker recovery (a task held past this is re-claimable).
LEASE_SECONDS = 300

TASK_STATUSES = ("pending", "scanning", "indexing", "done", "failed")


@dataclass(frozen=True, slots=True)
class UploadReservation:
    task_id: uuid.UUID
    document_id: uuid.UUID
    enterprise_id: uuid.UUID
    object_key: str
    content_sha256: str
    object_state: str
    rq_job_id: str
    created: bool


@dataclass(frozen=True, slots=True)
class UploadClaim:
    enterprise_id: uuid.UUID
    task_id: uuid.UUID
    lease_token: uuid.UUID
    document_id: uuid.UUID
    object_key: str
    content_sha256: str
    source_etag: str | None
    source_size: int | None


def _queue() -> Queue:
    return Queue(QUEUE_NAME, connection=Redis.from_url(REDIS_URL))


async def find_existing_upload(
    *,
    enterprise_id: uuid.UUID,
    content_sha256: str,
    sub: str,
) -> tuple[uuid.UUID, uuid.UUID] | None:
    """Return (task_id, document_id) for a same-enterprise same-SHA upload.

    Idempotency gate: a repeated upload of the same bytes returns the SAME
    document and task — zero new rows, zero new objects.
    """
    async with session_scope(
        role="f1_api", enterprise_id=enterprise_id, sub=sub
    ) as session:
        row = (
            await session.execute(
                text(
                    "SELECT t.id, t.document_id FROM f1.upload_task t "
                    "WHERE t.enterprise_id = :eid AND t.content_sha256 = :sha"
                ),
                {"eid": enterprise_id, "sha": content_sha256},
            )
        ).fetchone()
        if row is None or row[1] is None:
            return None
        return (row[0], row[1])


async def reserve_upload_task(
    *,
    enterprise_id: uuid.UUID,
    document_id: uuid.UUID,
    object_key: str,
    content_sha256: str,
    sub: str,
) -> uuid.UUID:
    """Insert task + dispatched outbox in ONE transaction (no duplicate SHA).

    The caller reserves document/task/outbox atomically BEFORE writing the
    opaque object, so a failed write can compensate only its own etag.
    """
    task_id = uuid.uuid4()
    async with session_scope(
        role="f1_api", enterprise_id=enterprise_id, sub=sub
    ) as session:
        await session.execute(
            text(
                "INSERT INTO f1.upload_task "
                "(id, enterprise_id, document_id, object_key, content_sha256, "
                "status, object_state) "
                "VALUES (:id, :eid, :doc, :key, :sha, 'pending', 'ready')"
            ),
            {
                "id": task_id,
                "eid": enterprise_id,
                "doc": document_id,
                "key": object_key,
                "sha": content_sha256,
            },
        )
        await session.execute(
            text(
                "INSERT INTO f1.outbox "
                "(id, enterprise_id, task_id, event_type, state, payload_sha256, rq_job_id) "
                "VALUES (:id, :eid, :task, 'upload.dispatched', 'pending', :sha, :job)"
            ),
            {
                "id": uuid.uuid4(), "eid": enterprise_id, "task": task_id,
                "sha": content_sha256, "job": rq_job_id(task_id),
            },
        )
        await session.commit()
    return task_id


def rq_job_id(task_id: uuid.UUID) -> str:
    return f"f1-upload-{task_id}"


async def _reservation_for_sha(
    *, enterprise_id: uuid.UUID, content_sha256: str, sub: str
) -> UploadReservation | None:
    async with session_scope(
        role="f1_api", enterprise_id=enterprise_id, sub=sub
    ) as session:
        row = (
            await session.execute(
                text(
                    "SELECT t.id, t.document_id, t.enterprise_id, t.object_key, "
                    "t.content_sha256, t.object_state, o.rq_job_id "
                    "FROM f1.upload_task AS t JOIN f1.outbox AS o "
                    "ON o.enterprise_id = t.enterprise_id AND o.task_id = t.id "
                    "AND o.event_type = 'upload.dispatched' "
                    "WHERE t.enterprise_id = :eid AND t.content_sha256 = :sha"
                ),
                {"eid": enterprise_id, "sha": content_sha256},
            )
        ).fetchone()
    if row is None or row[1] is None:
        return None
    return UploadReservation(
        task_id=row[0], document_id=row[1], enterprise_id=row[2],
        object_key=row[3], content_sha256=row[4], object_state=row[5],
        rq_job_id=row[6], created=False,
    )


async def reserve_api_upload(
    *,
    enterprise_id: uuid.UUID,
    filename: str,
    size: int,
    content_type: str,
    content_sha256: str,
    sub: str,
) -> UploadReservation:
    """Atomically reserve an upload without making its outbox dispatchable."""
    existing = await _reservation_for_sha(
        enterprise_id=enterprise_id, content_sha256=content_sha256, sub=sub
    )
    if existing is not None:
        return existing
    task_id = uuid.uuid4()
    document_id = uuid.uuid4()
    object_key = opaque_object_key(task_id, content_type)
    job_id = rq_job_id(task_id)
    try:
        async with session_scope(
            role="f1_api", enterprise_id=enterprise_id, sub=sub
        ) as session:
            await session.execute(
                text(
                    "INSERT INTO f1.document "
                    "(id, enterprise_id, object_key, filename, size, content_type, status) "
                    "VALUES (:id, :eid, :key, :name, :size, :ctype, 'pending')"
                ),
                {
                    "id": document_id, "eid": enterprise_id, "key": object_key,
                    "name": filename, "size": size, "ctype": content_type,
                },
            )
            await session.execute(
                text(
                    "INSERT INTO f1.upload_task "
                    "(id, enterprise_id, document_id, object_key, content_sha256, "
                    "status, object_state, source_size) "
                    "VALUES (:id, :eid, :doc, :key, :sha, 'pending', 'reserved', :size)"
                ),
                {
                    "id": task_id, "eid": enterprise_id, "doc": document_id,
                    "key": object_key, "sha": content_sha256, "size": size,
                },
            )
            await session.execute(
                text(
                    "INSERT INTO f1.outbox "
                    "(id, enterprise_id, task_id, event_type, state, "
                    "payload_sha256, rq_job_id) "
                    "VALUES (:id, :eid, :task, 'upload.dispatched', 'pending', :sha, :job)"
                ),
                {
                    "id": uuid.uuid4(), "eid": enterprise_id, "task": task_id,
                    "sha": content_sha256, "job": job_id,
                },
            )
            await session.commit()
    except IntegrityError:
        # A concurrent same-SHA winner may still be committing.  Resolve to its
        # durable identity instead of turning a uniqueness race into HTTP 503.
        for _ in range(40):
            winner = await _reservation_for_sha(
                enterprise_id=enterprise_id,
                content_sha256=content_sha256,
                sub=sub,
            )
            if winner is not None:
                return winner
            await asyncio.sleep(0.05)
        raise RuntimeError("UPLOAD_RESERVATION_CONFLICT") from None
    return UploadReservation(
        task_id=task_id, document_id=document_id, enterprise_id=enterprise_id,
        object_key=object_key, content_sha256=content_sha256,
        object_state="reserved", rq_job_id=job_id, created=True,
    )


async def finalize_upload_object(
    reservation: UploadReservation,
    *,
    source_etag: str,
    source_size: int,
    sub: str,
) -> bool:
    """CAS reserved/write_failed -> ready and append audit in one transaction."""
    async with session_scope(
        role="f1_api", enterprise_id=reservation.enterprise_id, sub=sub
    ) as session:
        row = (
            await session.execute(
                text(
                    "UPDATE f1.upload_task SET object_state='ready', status='pending', "
                    "source_etag=:etag, source_size=:size, error_reason=NULL, "
                    "next_attempt_at=NULL, updated_at=statement_timestamp() "
                    "WHERE id=:id AND enterprise_id=:eid "
                    "AND content_sha256=:sha AND object_key=:key "
                    "AND object_state IN ('reserved','write_failed') RETURNING id"
                ),
                {
                    "etag": source_etag, "size": source_size,
                    "id": reservation.task_id, "eid": reservation.enterprise_id,
                    "sha": reservation.content_sha256, "key": reservation.object_key,
                },
            )
        ).fetchone()
        if row is None:
            return False
        await session.execute(
            text("UPDATE f1.document SET status='pending' WHERE id=:id"),
            {"id": reservation.document_id},
        )
        await session.execute(
            text(
                "UPDATE f1.outbox SET state='pending', dispatch_token=NULL, "
                "dispatch_lease_until=NULL WHERE task_id=:id "
                "AND event_type='upload.dispatched'"
            ),
            {"id": reservation.task_id},
        )
        await session.execute(
            text(
                "INSERT INTO f1.audit_log "
                "(id, enterprise_id, user_sub, action, resource_type, resource_id, result) "
                "VALUES (:id, :eid, :sub, 'document.upload', 'document', :rid, 'success')"
            ),
            {
                "id": uuid.uuid4(), "eid": reservation.enterprise_id, "sub": sub,
                "rid": str(reservation.document_id),
            },
        )
        await session.commit()
        return True


async def mark_upload_write_failed(
    reservation: UploadReservation, *, sub: str
) -> bool:
    """Record a fixed failure without making the reserved task dispatchable."""
    async with session_scope(
        role="f1_api", enterprise_id=reservation.enterprise_id, sub=sub
    ) as session:
        row = (
            await session.execute(
                text(
                    "UPDATE f1.upload_task SET object_state='write_failed', "
                    "status='failed', error_reason='OBJECT_WRITE_FAILED', "
                    "updated_at=statement_timestamp() WHERE id=:id "
                    "AND object_state <> 'ready' RETURNING id"
                ),
                {"id": reservation.task_id},
            )
        ).fetchone()
        if row is None:
            return False
        await session.execute(
            text("UPDATE f1.document SET status='failed' WHERE id=:id"),
            {"id": reservation.document_id},
        )
        await session.execute(
            text(
                "INSERT INTO f1.audit_log "
                "(id, enterprise_id, user_sub, action, resource_type, resource_id, result) "
                "VALUES (:id, :eid, :sub, 'document.upload', 'document', :rid, 'failed')"
            ),
            {
                "id": uuid.uuid4(), "eid": reservation.enterprise_id, "sub": sub,
                "rid": str(reservation.document_id),
            },
        )
        await session.commit()
        return True


async def create_upload_task(
    *,
    enterprise_id: uuid.UUID,
    document_id: uuid.UUID,
    object_key: str,
    content_sha256: str,
    sub: str,
) -> uuid.UUID:
    """Idempotent by (enterprise, sha): returns the existing task if present."""
    existing = await find_existing_upload(
        enterprise_id=enterprise_id, content_sha256=content_sha256, sub=sub
    )
    if existing is not None:
        return existing[0]
    return await reserve_upload_task(
        enterprise_id=enterprise_id,
        document_id=document_id,
        object_key=object_key,
        content_sha256=content_sha256,
        sub=sub,
    )


def enqueue_upload(task_id: uuid.UUID, job_id: str | None = None) -> None:
    """Enqueue the pipeline job (only the task_id crosses the wire)."""
    queue = _queue()
    stable_job_id = job_id or rq_job_id(task_id)
    existing = queue.fetch_job(stable_job_id)
    if existing is not None:
        if existing.get_status(refresh=True) in {
            JobStatus.CREATED,
            JobStatus.QUEUED,
            JobStatus.STARTED,
            JobStatus.DEFERRED,
            JobStatus.SCHEDULED,
        }:
            return
        # Terminal RQ metadata is not the business truth.  A retryable DB task
        # reuses the same deterministic id after removing only that old job.
        existing.delete(remove_from_queue=True)
    try:
        queue.enqueue(run_upload_pipeline, str(task_id), job_id=stable_job_id)
    except Exception:
        # API direct-dispatch and the CAS dispatcher may race.  If the exact
        # stable job now exists, the desired effect already happened.
        if queue.fetch_job(stable_job_id) is not None:
            return
        raise


async def get_task(
    task_id: uuid.UUID,
    *,
    enterprise_id: uuid.UUID,
    sub: str,
    role: str = "f1_api",
) -> dict | None:
    async with session_scope(
        role=role, enterprise_id=enterprise_id, sub=sub
    ) as session:
        row = (
            await session.execute(
                text(
                    "SELECT id, enterprise_id, document_id, object_key, "
                    "content_sha256, status, attempt, error_reason, object_state, "
                    "source_etag, source_size, lease_token, lease_owner, next_attempt_at "
                    "FROM f1.upload_task WHERE id = :id"
                ),
                {"id": task_id},
            )
        ).fetchone()
        if row is None:
            return None
        return {
            "task_id": str(row[0]),
            "enterprise_id": str(row[1]),
            "document_id": str(row[2]),
            "object_key": row[3],
            "content_sha256": row[4],
            "status": row[5],
            "attempt": row[6],
            "error_reason": row[7],
            "object_state": row[8],
            "source_etag": row[9],
            "source_size": row[10],
            "lease_token": str(row[11]) if row[11] else None,
            "lease_owner": row[12],
            "next_attempt_at": row[13].isoformat() if row[13] else None,
        }


async def list_tasks(*, enterprise_id: uuid.UUID, sub: str) -> list[dict]:
    async with session_scope(
        role="f1_api", enterprise_id=enterprise_id, sub=sub
    ) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, status, content_sha256, error_reason "
                    "FROM f1.upload_task WHERE enterprise_id = :eid "
                    "ORDER BY created_at DESC LIMIT 100"
                ),
                {"eid": enterprise_id},
            )
        ).fetchall()
    return [
        {
            "task_id": str(r[0]),
            "status": r[1],
            "content_sha256": r[2],
            "error_reason": r[3],
        }
        for r in rows
    ]


async def _lease_next_pending(enterprise_id: uuid.UUID) -> uuid.UUID | None:
    """Compatibility helper using the f1_0004 SECURITY DEFINER claims."""
    async with session_scope(role="f1_worker") as session:
        rows = (
            await session.execute(
                text("SELECT * FROM f1.claim_pending_dispatch(100, 30)")
            )
        ).fetchall()
        await session.commit()
    selected: uuid.UUID | None = None
    for row in rows:
        async with session_scope(role="f1_worker") as session:
            await session.execute(
                text("SELECT f1.complete_dispatch(:oid, :token, false)"),
                {"oid": row[0], "token": row[4]},
            )
            await session.commit()
        if selected is None and row[1] == enterprise_id:
            selected = row[2]
    if selected is None:
        return None
    claim = await claim_upload_task(selected)
    return claim.task_id if claim else None


def worker_identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def claim_upload_task(
    task_id: uuid.UUID,
    *,
    worker_id: str | None = None,
    lease_seconds: int = LEASE_SECONDS,
) -> UploadClaim | None:
    async with session_scope(role="f1_worker") as session:
        row = (
            await session.execute(
                text("SELECT * FROM f1.claim_upload_task(:id, :worker, :seconds)"),
                {
                    "id": task_id, "worker": worker_id or worker_identity(),
                    "seconds": lease_seconds,
                },
            )
        ).fetchone()
        await session.commit()
    if row is None:
        return None
    return UploadClaim(
        enterprise_id=row[0], task_id=row[1], lease_token=row[2],
        document_id=row[3], object_key=row[4], content_sha256=row[5],
        source_etag=row[6], source_size=int(row[7]) if row[7] is not None else None,
    )


async def renew_upload_lease(
    task_id: uuid.UUID,
    lease_token: uuid.UUID,
    *,
    lease_seconds: int = LEASE_SECONDS,
) -> bool:
    async with session_scope(role="f1_worker") as session:
        renewed = bool(
            (
                await session.execute(
                    text("SELECT f1.renew_upload_lease(:id, :token, :seconds)"),
                    {"id": task_id, "token": lease_token, "seconds": lease_seconds},
                )
            ).scalar()
        )
        await session.commit()
        return renewed


async def set_claim_context(session, claim: UploadClaim) -> None:
    """Bind worker RLS to the exact task and unexpired lease owner."""
    await session.execute(
        text("SELECT set_config('f1.task_id', :id, true)"),
        {"id": str(claim.task_id)},
    )
    await session.execute(
        text("SELECT set_config('f1.lease_token', :token, true)"),
        {"token": str(claim.lease_token)},
    )


def run_upload_pipeline(task_id: str) -> None:
    """RQ worker entry: scan (placeholder) -> index (Task 2 wires indexing).

    Uses the f1_worker role and a per-task tenant scope; the pipeline body is
    implemented in Task 2 (registered-fixture indexing + QA).
    """
    from . import worker_pipeline  # local import avoids RQ pickling cycles

    worker_pipeline.process_task(task_id)


__all__ = (
    "create_upload_task",
    "reserve_api_upload",
    "finalize_upload_object",
    "mark_upload_write_failed",
    "UploadReservation",
    "UploadClaim",
    "find_existing_upload",
    "reserve_upload_task",
    "enqueue_upload",
    "get_task",
    "list_tasks",
    "run_upload_pipeline",
    "rq_job_id",
    "claim_upload_task",
    "renew_upload_lease",
    "set_claim_context",
    "TASK_STATUSES",
    "LEASE_SECONDS",
)
