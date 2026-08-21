"""Production-shaped local material-RAG worker dispatcher.

Enabled only when ``F1_MATERIAL_RAG_WORKER_RUNTIME_LOCAL=1`` and
``F1_LOCAL_ENGINEERING=1``.  Default compose, default API, and default
migrate stay closed.  This is not a production worker.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from platform_foundation.f1.features.material_rag.orchestrator import run_once


WORKER_FLAG = "F1_MATERIAL_RAG_WORKER_RUNTIME_LOCAL"
ENGINEERING_FLAG = "F1_LOCAL_ENGINEERING"
METRIC_KEYS = (
    "claimed",
    "completed",
    "retry",
    "lease_lost",
    "idle",
    "last_success",
)
WORKER_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_IDLE_MS_MIN = 50
_IDLE_MS_MAX = 5000
_ERROR_MS_MIN = 50
_ERROR_MS_MAX = 5000
_LEASE_MIN = 1
_LEASE_MAX = 120


def worker_runtime_enabled() -> bool:
    return os.environ.get(WORKER_FLAG) == "1" and os.environ.get(ENGINEERING_FLAG) == "1"


def _bounded_ms(raw: int, *, low: int, high: int, token: str) -> float:
    if raw < low or raw > high:
        raise RuntimeError(token)
    return raw / 1000.0


def _canonical_metrics(metrics: Mapping[str, Any]) -> bytes:
    if tuple(sorted(metrics)) != tuple(sorted(METRIC_KEYS)):
        raise RuntimeError("MATERIAL_RAG_WORKER_METRICS_INVALID")
    payload = {
        "claimed": int(metrics["claimed"]),
        "completed": int(metrics["completed"]),
        "retry": int(metrics["retry"]),
        "lease_lost": int(metrics["lease_lost"]),
        "idle": int(metrics["idle"]),
        "last_success": metrics["last_success"],
    }
    if payload["last_success"] is not None:
        if not isinstance(payload["last_success"], str) or not _UTC_RE.fullmatch(
            payload["last_success"]
        ):
            raise RuntimeError("MATERIAL_RAG_WORKER_METRICS_INVALID")
    for key in ("claimed", "completed", "retry", "lease_lost", "idle"):
        if payload[key] < 0:
            raise RuntimeError("MATERIAL_RAG_WORKER_METRICS_INVALID")
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def write_heartbeat(path: Path, metrics: Mapping[str, Any]) -> None:
    target = path if path.is_absolute() else path.resolve()
    parent = target.parent
    parent_info = os.lstat(parent)
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        raise RuntimeError("MATERIAL_RAG_WORKER_HEARTBEAT_INVALID")
    body = _canonical_metrics(metrics)
    temporary = parent / f".{target.name}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        os.write(descriptor, body)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
    os.chmod(target, 0o600)
    info = os.lstat(target)
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise RuntimeError("MATERIAL_RAG_WORKER_HEARTBEAT_INVALID")


class MaterialRagWorkerRuntime:
    def __init__(
        self,
        *,
        worker_id: str,
        heartbeat_path: Path,
        idle_ms: int = 200,
        error_ms: int = 500,
        lease_seconds: int = 30,
        max_rounds: int | None = None,
    ) -> None:
        if not isinstance(worker_id, str) or WORKER_ID_RE.fullmatch(worker_id) is None:
            raise RuntimeError("MATERIAL_RAG_WORKER_ID_INVALID")
        if lease_seconds < _LEASE_MIN or lease_seconds > _LEASE_MAX:
            raise RuntimeError("MATERIAL_RAG_WORKER_LEASE_INVALID")
        if max_rounds is not None and max_rounds < 1:
            raise RuntimeError("MATERIAL_RAG_WORKER_ROUNDS_INVALID")
        self.worker_id = worker_id
        self.heartbeat_path = heartbeat_path
        self.idle_backoff = _bounded_ms(
            idle_ms, low=_IDLE_MS_MIN, high=_IDLE_MS_MAX, token="MATERIAL_RAG_WORKER_IDLE_INVALID"
        )
        self.error_backoff = _bounded_ms(
            error_ms,
            low=_ERROR_MS_MIN,
            high=_ERROR_MS_MAX,
            token="MATERIAL_RAG_WORKER_ERROR_INVALID",
        )
        self.lease_seconds = lease_seconds
        self.max_rounds = max_rounds
        self._stop = False
        self.metrics: dict[str, Any] = {
            "claimed": 0,
            "completed": 0,
            "retry": 0,
            "lease_lost": 0,
            "idle": 0,
            "last_success": None,
        }

    def request_stop(self, *_args: object) -> None:
        self._stop = True

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)

    def _record(self, kind: str) -> None:
        if kind == "EMPTY":
            self.metrics["idle"] += 1
            return
        if kind in {"DISABLED", ""}:
            return
        self.metrics["claimed"] += 1
        if kind == "SUCCESS":
            self.metrics["completed"] += 1
            self.metrics["last_success"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            return
        if kind == "LEASE_LOST":
            self.metrics["lease_lost"] += 1
            return
        self.metrics["retry"] += 1

    async def run(self) -> str:
        if not worker_runtime_enabled():
            return "DISABLED"
        rounds = 0
        while not self._stop:
            if self.max_rounds is not None and rounds >= self.max_rounds:
                break
            rounds += 1
            try:
                outcome = await run_once(
                    worker_id=self.worker_id, lease_seconds=self.lease_seconds
                )
                kind = getattr(outcome, "kind", "")
            except Exception:
                write_heartbeat(self.heartbeat_path, self.metrics)
                await asyncio.sleep(self.error_backoff)
                continue
            if kind == "DISABLED":
                write_heartbeat(self.heartbeat_path, self.metrics)
                return "DISABLED"
            self._record(kind)
            write_heartbeat(self.heartbeat_path, self.metrics)
            if kind == "EMPTY":
                await asyncio.sleep(self.idle_backoff)
        return "STOPPED" if self._stop else "DONE"


async def run_worker(
    *,
    worker_id: str,
    heartbeat_path: Path,
    idle_ms: int = 200,
    error_ms: int = 500,
    lease_seconds: int = 30,
    max_rounds: int | None = None,
    install_signals: bool = True,
) -> str:
    runtime = MaterialRagWorkerRuntime(
        worker_id=worker_id,
        heartbeat_path=heartbeat_path,
        idle_ms=idle_ms,
        error_ms=error_ms,
        lease_seconds=lease_seconds,
        max_rounds=max_rounds,
    )
    if install_signals:
        runtime.install_signal_handlers()
    return await runtime.run()


__all__ = (
    "ENGINEERING_FLAG",
    "METRIC_KEYS",
    "MaterialRagWorkerRuntime",
    "WORKER_FLAG",
    "run_worker",
    "worker_runtime_enabled",
    "write_heartbeat",
)
