"""Durable outbox dispatcher process for the local F1 Fixture stack."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import threading
import time

from .worker_pipeline import dispatch_pending_outbox


_HEARTBEAT = Path("/tmp/f1-dispatcher-heartbeat")
_STOP = threading.Event()


def _stop(_signum: int, _frame: object) -> None:
    _STOP.set()


def _mark_heartbeat(state: str) -> None:
    temporary = _HEARTBEAT.with_suffix(".new")
    temporary.write_text(f"{state}\n{time.time_ns()}\n", encoding="ascii")
    os.chmod(temporary, 0o600)
    temporary.replace(_HEARTBEAT)


def main() -> int:
    interval = int(os.environ.get("F1_DISPATCH_INTERVAL_SECONDS", "5"))
    if interval < 1 or interval > 60:
        raise RuntimeError("F1_DISPATCH_INTERVAL_INVALID")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    dispatchers = [dispatch_pending_outbox]
    from .features.analysis_reports.delivery_repository import (
        delivery_enabled as report_delivery_enabled,
    )

    if report_delivery_enabled():
        from .features.analysis_reports.delivery_dispatcher import (
            dispatch_pending_report_deliveries,
        )

        dispatchers.append(dispatch_pending_report_deliveries)
    from .features.material_pipeline.coordinator import auto_pipeline_enabled

    if auto_pipeline_enabled():
        # The default f1_0014 and material-RAG f1_0016 stacks deliberately do
        # not own the analysis-report delivery table.  Import and poll this
        # domain only in the explicitly migrated analysis-report runtime.
        from .features.material_pipeline.dispatcher import (
            dispatch_pending_deliveries,
        )

        dispatchers.append(dispatch_pending_deliveries)
    from .features.p3.delivery_repository import delivery_enabled

    if delivery_enabled():
        # This branch owns both the f1_0023 delivery table and the bounded
        # physical purge of expired encrypted OCR checkpoints.  Default and
        # older schema heads never import or query either capability.
        from .features.p3.delivery_dispatcher import (
            dispatch_pending_ingestion_deliveries,
        )

        dispatchers.append(dispatch_pending_ingestion_deliveries)
    while not _STOP.is_set():
        state = "ok"
        for dispatch in dispatchers:
            try:
                dispatch()
            except Exception:  # keep retrying without exception details
                state = "retry"
        _mark_heartbeat(state)
        _STOP.wait(interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
