"""Offline contracts for the production-shaped local worker dispatcher."""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
RUNTIME_PATH = ROOT / "src/platform_foundation/f1/material_rag_runtime.py"
PROBE_PATH = ROOT / "infra/f1/material-rag/worker_runtime_probe.py"
METRIC_KEYS = (
    "claimed",
    "completed",
    "idle",
    "last_success",
    "lease_lost",
    "retry",
)
FORBIDDEN_METRIC_TOKENS = (
    "tenant_id",
    "enterprise_id",
    "job_id",
    "document_id",
    "document_record_id",
    "document_version_id",
    "knowledge_scope_id",
)
FORBIDDEN_SQL = (
    "BYPASSRLS",
    "USING (true)",
    "USING(true)",
    "SECURITY DEFINER",
    "GRANT ALL",
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class MaterialRagWorkerRuntimeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("F1_MATERIAL_RAG_WORKER_RUNTIME_LOCAL", None)
        os.environ.pop("F1_LOCAL_ENGINEERING", None)
        os.environ.pop("F1_MATERIAL_RAG_ORCHESTRATION_LOCAL", None)

    def test_default_disabled_and_metric_schema(self) -> None:
        from platform_foundation.f1.material_rag_runtime import (
            METRIC_KEYS as runtime_keys,
            MaterialRagWorkerRuntime,
            worker_runtime_enabled,
        )

        self.assertFalse(worker_runtime_enabled())
        os.environ["F1_MATERIAL_RAG_WORKER_RUNTIME_LOCAL"] = "true"
        os.environ["F1_LOCAL_ENGINEERING"] = "1"
        self.assertFalse(worker_runtime_enabled())
        os.environ["F1_MATERIAL_RAG_WORKER_RUNTIME_LOCAL"] = "1"
        os.environ["F1_LOCAL_ENGINEERING"] = "true"
        self.assertFalse(worker_runtime_enabled())
        os.environ["F1_MATERIAL_RAG_WORKER_RUNTIME_LOCAL"] = "1"
        os.environ["F1_LOCAL_ENGINEERING"] = "1"
        self.assertTrue(worker_runtime_enabled())
        self.assertEqual(tuple(sorted(runtime_keys)), METRIC_KEYS)
        source = _source(RUNTIME_PATH)
        for token in FORBIDDEN_METRIC_TOKENS + FORBIDDEN_SQL:
            self.assertNotIn(token, source)
        tree = ast.parse(source)
        assigned = None
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "METRIC_KEYS":
                        assigned = ast.literal_eval(node.value)
        self.assertEqual(tuple(sorted(assigned)), METRIC_KEYS)

    def test_idle_path_sleeps_and_heartbeat_is_0600(self) -> None:
        from platform_foundation.f1.material_rag_runtime import (
            MaterialRagWorkerRuntime,
            write_heartbeat,
        )

        source = inspect.getsource(MaterialRagWorkerRuntime.run)
        self.assertIn("asyncio.sleep(self.idle_backoff)", source)
        self.assertIn("asyncio.sleep(self.error_backoff)", source)
        self.assertNotIn("while True:\n            outcome = await run_once", source)
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            os.chmod(parent, 0o700)
            path = parent / "heartbeat.json"
            write_heartbeat(
                path,
                {
                    "claimed": 0,
                    "completed": 0,
                    "retry": 0,
                    "lease_lost": 0,
                    "idle": 1,
                    "last_success": None,
                },
            )
            info = os.lstat(path)
            self.assertTrue(stat.S_ISREG(info.st_mode))
            self.assertFalse(stat.S_ISLNK(info.st_mode))
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
            self.assertEqual(info.st_nlink, 1)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(tuple(sorted(payload)), METRIC_KEYS)
            for token in FORBIDDEN_METRIC_TOKENS:
                self.assertNotIn(token, payload)

    def test_disabled_run_does_not_claim(self) -> None:
        from platform_foundation.f1.material_rag_runtime import MaterialRagWorkerRuntime

        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            os.chmod(parent, 0o700)
            runtime = MaterialRagWorkerRuntime(
                worker_id="contract-worker",
                heartbeat_path=parent / "heartbeat.json",
                idle_ms=50,
                error_ms=50,
                max_rounds=3,
            )
            status = asyncio.run(runtime.run())
            self.assertEqual(status, "DISABLED")
            self.assertEqual(runtime.metrics["claimed"], 0)
            self.assertFalse((parent / "heartbeat.json").exists())

    def test_probe_has_no_body_or_manifest_flags(self) -> None:
        source = _source(PROBE_PATH)
        self.assertNotIn("--manifest", source)
        self.assertNotIn("manifest_key", source)
        self.assertNotIn("arbitrary body", source)
        for token in FORBIDDEN_SQL:
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
