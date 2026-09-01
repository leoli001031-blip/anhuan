"""Privilege-separated RQ dispatch for the local automatic material pipeline."""
from __future__ import annotations

import hashlib
import re
import uuid

from redis import Redis
from rq import Queue, Retry
from rq.job import JobStatus

from ...upload_task import QUEUE_NAME as WORKER_QUEUE_NAME
from ..analysis_reports.queue import QUEUE_NAME as REPORT_QUEUE_NAME
from ...config import redis_url


REDIS_URL = redis_url()
LOCAL_INDEX_TIMEOUT_SECONDS = 900
REPORT_DISPATCH_TIMEOUT_SECONDS = 120
RECOVERY_SWEEP_TIMEOUT_SECONDS = 240
DURABLE_DELIVERY_TIMEOUT_SECONDS = 240
_REASON_RE = re.compile(r"^[A-Z0-9_]{1,80}$")
_ACTIVE_STATUSES = frozenset(
    {
        JobStatus.CREATED,
        JobStatus.QUEUED,
        JobStatus.STARTED,
        JobStatus.DEFERRED,
        JobStatus.SCHEDULED,
    }
)


def _sub(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 255
        or value.strip() != value
        or not value.isprintable()
    ):
        raise ValueError("MATERIAL_PIPELINE_SUB_INVALID")
    return value


def _sub_fingerprint(value: str) -> str:
    return hashlib.sha256(_sub(value).encode("utf-8")).hexdigest()[:16]


def _failed_reason(queue_name: str, stable_id: str) -> str | None:
    queue = Queue(queue_name, connection=Redis.from_url(REDIS_URL))
    existing = queue.fetch_job(stable_id)
    if existing is None or existing.get_status(refresh=True) != JobStatus.FAILED:
        return None
    reason = existing.meta.get("reason_code")
    if not isinstance(reason, str) or _REASON_RE.fullmatch(reason) is None:
        return "MATERIAL_PIPELINE_DISPATCH_FAILED"
    return reason


def _enqueue(
    *,
    queue_name: str,
    stable_id: str,
    function,
    args: tuple[str, ...],
    timeout: int,
    retry: Retry,
) -> None:
    queue = Queue(queue_name, connection=Redis.from_url(REDIS_URL))
    existing = queue.fetch_job(stable_id)
    if existing is not None:
        if existing.get_status(refresh=True) in _ACTIVE_STATUSES:
            return
        existing.delete(remove_from_queue=True)
    try:
        queue.enqueue(
            function,
            *args,
            job_id=stable_id,
            job_timeout=timeout,
            retry=retry,
        )
    except Exception:
        raced = queue.fetch_job(stable_id)
        if raced is not None and raced.get_status(refresh=True) in _ACTIVE_STATUSES:
            return
        raise


def enqueue_local_index_stage(
    *,
    index_job_id: uuid.UUID,
    enterprise_id: uuid.UUID,
    provider_sub: str,
    version_id: uuid.UUID,
) -> None:
    if not all(
        isinstance(value, uuid.UUID)
        for value in (index_job_id, enterprise_id, version_id)
    ):
        raise ValueError("MATERIAL_PIPELINE_IDENTITY_INVALID")
    from .worker import run_local_index_stage

    _enqueue(
        queue_name=WORKER_QUEUE_NAME,
        stable_id=f"f1-material-auto-index-{index_job_id}",
        function=run_local_index_stage,
        args=(
            str(index_job_id),
            str(enterprise_id),
            _sub(provider_sub),
            str(version_id),
        ),
        timeout=LOCAL_INDEX_TIMEOUT_SECONDS,
        # The final retry is later than the 900s durable DB lease so an RQ
        # hard-kill can reclaim the exact job without an external reaper.
        retry=Retry(max=3, interval=[15, 120, 910]),
    )


def enqueue_report_stage(
    *,
    enterprise_id: uuid.UUID,
    provider_sub: str,
    version_id: uuid.UUID,
) -> None:
    if not all(
        isinstance(value, uuid.UUID) for value in (enterprise_id, version_id)
    ):
        raise ValueError("MATERIAL_PIPELINE_IDENTITY_INVALID")
    from .worker import run_report_stage

    _enqueue(
        queue_name=REPORT_QUEUE_NAME,
        stable_id=f"f1-material-auto-report-{version_id}",
        function=run_report_stage,
        args=(str(enterprise_id), _sub(provider_sub), str(version_id)),
        timeout=REPORT_DISPATCH_TIMEOUT_SECONDS,
        retry=Retry(max=3, interval=[5, 30, 120]),
    )


def enqueue_reconcile_stage(
    *,
    enterprise_id: uuid.UUID,
    provider_sub: str,
    version_id: uuid.UUID,
) -> None:
    if not all(
        isinstance(value, uuid.UUID) for value in (enterprise_id, version_id)
    ):
        raise ValueError("MATERIAL_PIPELINE_IDENTITY_INVALID")
    from .worker import run_reconcile_stage

    _enqueue(
        queue_name=REPORT_QUEUE_NAME,
        stable_id=f"f1-material-auto-reconcile-{version_id}",
        function=run_reconcile_stage,
        args=(str(enterprise_id), _sub(provider_sub), str(version_id)),
        timeout=REPORT_DISPATCH_TIMEOUT_SECONDS,
        retry=Retry(max=3, interval=[5, 60, 310]),
    )


def enqueue_recovery_sweep(
    *, enterprise_id: uuid.UUID, provider_sub: str
) -> None:
    if not isinstance(enterprise_id, uuid.UUID):
        raise ValueError("MATERIAL_PIPELINE_IDENTITY_INVALID")
    from .worker import run_recovery_sweep

    actor_sub = _sub(provider_sub)
    _enqueue(
        queue_name=REPORT_QUEUE_NAME,
        stable_id=(
            f"f1-material-auto-sweep-{enterprise_id}-"
            f"{_sub_fingerprint(actor_sub)}"
        ),
        function=run_recovery_sweep,
        args=(str(enterprise_id), actor_sub),
        timeout=RECOVERY_SWEEP_TIMEOUT_SECONDS,
        retry=Retry(max=3, interval=[5, 60, 310]),
    )


def enqueue_durable_delivery(
    *,
    delivery_id: uuid.UUID,
    dispatch_token: uuid.UUID,
) -> None:
    """Queue only a DB delivery id and its fenced claim token."""
    if not all(
        isinstance(value, uuid.UUID)
        for value in (delivery_id, dispatch_token)
    ):
        raise ValueError("MATERIAL_PIPELINE_IDENTITY_INVALID")
    from .worker import run_durable_delivery

    _enqueue(
        queue_name=REPORT_QUEUE_NAME,
        # A reclaimed DB lease has a new token and must not be suppressed by a
        # stale active RQ registry entry from the previous lease.
        stable_id=(
            f"f1-material-pipeline-delivery-{delivery_id}-{dispatch_token}"
        ),
        function=run_durable_delivery,
        args=(
            str(delivery_id),
            str(dispatch_token),
        ),
        timeout=DURABLE_DELIVERY_TIMEOUT_SECONDS,
        retry=Retry(max=3, interval=[5, 60, 310]),
    )


def pipeline_dispatch_failure_reason(version_id: uuid.UUID) -> str | None:
    if not isinstance(version_id, uuid.UUID):
        raise ValueError("MATERIAL_PIPELINE_IDENTITY_INVALID")
    return _failed_reason(
        REPORT_QUEUE_NAME, f"f1-material-auto-reconcile-{version_id}"
    ) or _failed_reason(
        REPORT_QUEUE_NAME, f"f1-material-auto-report-{version_id}"
    )


def index_dispatch_failure_reason(index_job_id: uuid.UUID) -> str | None:
    if not isinstance(index_job_id, uuid.UUID):
        raise ValueError("MATERIAL_PIPELINE_IDENTITY_INVALID")
    return _failed_reason(
        WORKER_QUEUE_NAME, f"f1-material-auto-index-{index_job_id}"
    )


__all__ = (
    "DURABLE_DELIVERY_TIMEOUT_SECONDS",
    "LOCAL_INDEX_TIMEOUT_SECONDS",
    "RECOVERY_SWEEP_TIMEOUT_SECONDS",
    "REDIS_URL",
    "REPORT_DISPATCH_TIMEOUT_SECONDS",
    "enqueue_durable_delivery",
    "enqueue_local_index_stage",
    "enqueue_reconcile_stage",
    "enqueue_recovery_sweep",
    "enqueue_report_stage",
    "index_dispatch_failure_reason",
    "pipeline_dispatch_failure_reason",
)
