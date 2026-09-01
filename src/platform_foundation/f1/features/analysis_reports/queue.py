"""Redis/RQ dispatch for durable analysis-report generation jobs."""
from __future__ import annotations

import uuid
import re

from redis import Redis
from rq import Queue, Retry, get_current_job
from rq.job import JobStatus

from ...config import redis_url


QUEUE_NAME = "anhuan-f1-analysis-reports"
REDIS_URL = redis_url()
JOB_TIMEOUT_SECONDS = 240
_ACTIVE_STATUSES = frozenset(
    {
        JobStatus.CREATED,
        JobStatus.QUEUED,
        JobStatus.STARTED,
        JobStatus.DEFERRED,
        JobStatus.SCHEDULED,
    }
)
_REASON_RE = re.compile(r"^[A-Z0-9_]{1,80}$")


def rq_job_id(delivery_id: uuid.UUID, dispatch_token: uuid.UUID) -> str:
    """Fence every RQ identity to one PostgreSQL delivery lease."""
    if not isinstance(delivery_id, uuid.UUID) or not isinstance(
        dispatch_token, uuid.UUID
    ):
        raise ValueError("REPORT_QUEUE_IDENTITY_INVALID")
    return f"f1-analysis-report-{delivery_id}-{dispatch_token}"


def _queue() -> Queue:
    return Queue(QUEUE_NAME, connection=Redis.from_url(REDIS_URL))


def mark_current_dispatch_failure(reason_code: str) -> None:
    """Persist one body-free worker refusal on the RQ delivery itself."""
    if not isinstance(reason_code, str) or _REASON_RE.fullmatch(reason_code) is None:
        raise ValueError("REPORT_REASON_INVALID")
    current = get_current_job()
    if current is None:
        return
    current.meta["reason_code"] = reason_code
    current.save_meta()


def enqueue_generation(
    delivery_id: uuid.UUID,
    dispatch_token: uuid.UUID,
) -> None:
    """Enqueue only a body-free, token-fenced PostgreSQL delivery claim."""
    stable_id = rq_job_id(delivery_id, dispatch_token)
    queue = _queue()
    existing = queue.fetch_job(stable_id)
    if existing is not None:
        if existing.get_status(refresh=True) in _ACTIVE_STATUSES:
            return
        existing.delete(remove_from_queue=True)
    from .worker import run_generation_job

    try:
        queue.enqueue(
            run_generation_job,
            str(delivery_id),
            str(dispatch_token),
            job_id=stable_id,
            job_timeout=JOB_TIMEOUT_SECONDS,
            # The final retry is deliberately later than the 300s DB lease so
            # a hard-killed worker can be reclaimed without an external reaper.
            retry=Retry(max=3, interval=[5, 60, 310]),
        )
    except Exception:
        # A repeated dispatcher call for the same DB lease may race an uncertain
        # Redis result.  Only that exact token-bearing delivery can suppress it.
        raced = queue.fetch_job(stable_id)
        if raced is not None and raced.get_status(refresh=True) in _ACTIVE_STATUSES:
            return
        raise


__all__ = (
    "JOB_TIMEOUT_SECONDS",
    "QUEUE_NAME",
    "REDIS_URL",
    "enqueue_generation",
    "mark_current_dispatch_failure",
    "rq_job_id",
)
