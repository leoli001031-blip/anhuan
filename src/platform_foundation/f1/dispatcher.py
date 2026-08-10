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
    while not _STOP.is_set():
        state = "ok"
        try:
            dispatch_pending_outbox()
        except Exception:  # keep retrying without persisting exception details
            state = "retry"
        _mark_heartbeat(state)
        _STOP.wait(interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
