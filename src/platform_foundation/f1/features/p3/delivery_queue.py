"""RQ transport for body-free controlled-ingestion delivery claims."""
from __future__ import annotations

import uuid

from redis import Redis
from rq import Queue
from rq.job import JobStatus

from ...config import redis_url


QUEUE_NAME = "anhuan-f1-material-ingestion"
REDIS_URL = redis_url()
JOB_TIMEOUT_SECONDS = 1500
_ACTIVE_STATUSES = frozenset(
    {
        JobStatus.CREATED,
        JobStatus.QUEUED,
        JobStatus.STARTED,
        JobStatus.DEFERRED,
        JobStatus.SCHEDULED,
    }
)


def _job_id(delivery_id: uuid.UUID, dispatch_token: uuid.UUID) -> str:
    return f"f1-material-ingestion-{delivery_id}-{dispatch_token}"


def enqueue_durable_delivery(
    *, delivery_id: uuid.UUID, dispatch_token: uuid.UUID
) -> None:
    """Queue only the durable row identity and its unguessable fence token."""
    if not all(
        isinstance(value, uuid.UUID)
        for value in (delivery_id, dispatch_token)
    ):
        raise ValueError("MATERIAL_INGESTION_DELIVERY_IDENTITY_INVALID")
    from .delivery_worker import run_durable_ingestion

    queue = Queue(QUEUE_NAME, connection=Redis.from_url(REDIS_URL))
    stable_id = _job_id(delivery_id, dispatch_token)
    existing = queue.fetch_job(stable_id)
    if existing is not None:
        if existing.get_status(refresh=True) in _ACTIVE_STATUSES:
            return
        existing.delete(remove_from_queue=True)
    try:
        queue.enqueue(
            run_durable_ingestion,
            str(delivery_id),
            str(dispatch_token),
            job_id=stable_id,
            job_timeout=JOB_TIMEOUT_SECONDS,
        )
    except Exception:
        raced = queue.fetch_job(stable_id)
        if raced is not None and raced.get_status(refresh=True) in _ACTIVE_STATUSES:
            return
        raise


__all__ = (
    "JOB_TIMEOUT_SECONDS",
    "QUEUE_NAME",
    "REDIS_URL",
    "enqueue_durable_delivery",
)
