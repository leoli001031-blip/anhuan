"""Fresh-process local worker probe. External RAGFlow/Redis boundaries are faked."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))


def _install_boundary_fakes() -> None:
    from platform_foundation.f0j1.ragflow_client import RagFlowClient
    from tests.test_material_rag_postgres_integration import (
        DeterministicRagFlow,
        FakeRedis,
    )

    rag = DeterministicRagFlow()
    patch("redis.Redis.from_url", FakeRedis.from_url).start()
    patch.object(RagFlowClient, "_request", rag.handle).start()
    patch(
        "platform_foundation.f1.features.p3.service._release_quarantine_object",
        lambda row: None,
    ).start()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="worker_runtime_probe")
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--heartbeat", required=True)
    parser.add_argument("--idle-ms", type=int, default=200)
    parser.add_argument("--error-ms", type=int, default=500)
    parser.add_argument("--lease-seconds", type=int, default=30)
    parser.add_argument("--max-rounds", type=int, default=0)
    parser.add_argument("--hold-ms", type=int, default=0)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    _install_boundary_fakes()
    from platform_foundation.f1.material_rag_runtime import (
        run_worker,
        worker_runtime_enabled,
        write_heartbeat,
    )

    heartbeat = Path(arguments.heartbeat)
    max_rounds = arguments.max_rounds if arguments.max_rounds > 0 else None
    if arguments.hold_ms:
        if arguments.hold_ms < 1 or arguments.hold_ms > 5000:
            raise RuntimeError("MATERIAL_RAG_WORKER_HOLD_INVALID")
        os.environ["F1_MATERIAL_RAG_WORKER_HOLD_AFTER_CLAIM_MS"] = str(arguments.hold_ms)
    empty = {
        "claimed": 0,
        "completed": 0,
        "retry": 0,
        "lease_lost": 0,
        "idle": 0,
        "last_success": None,
    }
    if not worker_runtime_enabled():
        write_heartbeat(heartbeat, empty)
        sys.stdout.write(
            json.dumps(
                empty,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        print("LOCAL_MATERIAL_RAG_WORKER_RUNTIME_DISABLED")
        return 0
    status = asyncio.run(
        run_worker(
            worker_id=arguments.worker_id,
            heartbeat_path=heartbeat,
            idle_ms=arguments.idle_ms,
            error_ms=arguments.error_ms,
            lease_seconds=arguments.lease_seconds,
            max_rounds=max_rounds,
        )
    )
    sys.stdout.write(heartbeat.read_text(encoding="utf-8"))
    if status == "DISABLED":
        print("LOCAL_MATERIAL_RAG_WORKER_RUNTIME_DISABLED")
        return 0
    print("LOCAL_MATERIAL_RAG_WORKER_RUNTIME_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
