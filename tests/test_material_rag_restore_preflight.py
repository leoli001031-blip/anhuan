"""Offline contracts for the read-only restore operator preflight."""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from infra.f1.material_rag_backup_restore import (
    F1_HEAD,
    SCOPE,
    create_manifest,
)
from tests.test_material_rag_backup_restore import (
    BUSINESS_SNAPSHOT,
    DATABASE,
    PARENT_ID,
    PROJECT_ID,
    backup_stage,
    matching_inspects,
)


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_PATH = ROOT / "infra/f1/material-rag/restore_preflight.py"
BACKUP_ID = "20260821T090000Z-aaaaaaaaaaaa"
FORBIDDEN_CALLS = (
    "guarded_restore(",
    "pg_restore",
    "volume rm",
    "compose down",
    "docker compose down",
    "run_restore_maintenance",
    "prepare_empty_core",
)


def load_preflight():
    loader = importlib.machinery.SourceFileLoader(
        "material_rag_restore_preflight_tests", str(PREFLIGHT_PATH)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise AssertionError("restore_preflight import unavailable")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def write_package(store: Path) -> Path:
    os.chmod(store, 0o700)
    root = backup_stage(store, name=BACKUP_ID)
    create_manifest(
        root,
        project_id=PROJECT_ID,
        parent_project_id=PARENT_ID,
        database=DATABASE,
        scope=SCOPE,
        f1_head=F1_HEAD,
        business_snapshot=BUSINESS_SNAPSHOT,
    )
    return root


class MaterialRagRestorePreflightContractTests(unittest.TestCase):
    def test_source_forbids_destructive_entrypoints(self) -> None:
        source = PREFLIGHT_PATH.read_text(encoding="utf-8")
        for token in FORBIDDEN_CALLS:
            self.assertNotIn(token, source)
        self.assertIn("ready_to_apply", source)
        self.assertIn("destructive_started", source)

    def test_preflight_json_plan_and_zero_apply(self) -> None:
        module = load_preflight()
        with tempfile.TemporaryDirectory() as raw:
            store = Path(raw)
            os.chmod(store, 0o700)
            write_package(store)
            payload = module.run_preflight(
                backup_id=BACKUP_ID,
                store=store,
                volume_inspects=matching_inspects(),
            )
            self.assertEqual(payload["schema"], module.SCHEMA)
            self.assertEqual(payload["destructive_started"], 0)
            self.assertEqual(payload["ready_to_apply"], 0)
            self.assertEqual(payload["migration"]["apply"], 0)
            self.assertEqual(payload["package_f1_head"], "f1_0015")
            self.assertEqual(payload["target_f1_head"], "f1_0016")
            self.assertEqual(payload["business_table_count"], 38)
            self.assertEqual(payload["volume_count"], 2)
            self.assertRegex(payload["plan_sha256"], r"^[0-9a-f]{64}$")
            encoded = module.dump_payload(payload)
            self.assertEqual(
                json.loads(encoded),
                payload,
            )
            self.assertEqual(
                payload["plan_sha256"],
                hashlib.sha256(
                    json.dumps(
                        {
                            "identity": payload["identity"],
                            "migration": payload["migration"],
                            "minio_volume": "anhuan-mr-br-abc_br_minio_data",
                            "postgres_volume": "anhuan-mr-br-abc_br_postgres_data",
                            "schema": module.SCHEMA,
                        },
                        ensure_ascii=False,
                        allow_nan=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                    + b"\n"
                ).hexdigest(),
            )

    def test_rejects_symlink_and_keeps_zero_apply(self) -> None:
        module = load_preflight()
        with tempfile.TemporaryDirectory() as raw:
            store = Path(raw)
            os.chmod(store, 0o700)
            write_package(store)
            package = store / BACKUP_ID
            (package / "link.json").symlink_to(package / "manifest.json")
            with self.assertRaises(module.RestorePreflightError) as raised:
                module.run_preflight(
                    backup_id=BACKUP_ID,
                    store=store,
                    volume_inspects=matching_inspects(),
                )
            self.assertEqual(str(raised.exception), "PREFLIGHT_LINK_FORBIDDEN")

    def test_localctl_emits_canonical_json_and_zero_apply(self) -> None:
        module = load_preflight()
        with tempfile.TemporaryDirectory() as raw:
            store = Path(raw)
            os.chmod(store, 0o700)
            write_package(store)
            inspects = Path(raw) / "inspects.json"
            inspects.write_text(
                json.dumps(matching_inspects()),
                encoding="utf-8",
            )
            os.chmod(inspects, 0o600)
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + str(ROOT)
            env["MATERIAL_RAG_PREFLIGHT_STORE"] = str(store)
            env["MATERIAL_RAG_PREFLIGHT_VOLUME_INSPECTS"] = str(inspects)
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "scripts/localctl"),
                    "material-rag-restore-preflight",
                    "--backup-id",
                    BACKUP_ID,
                ],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            stdout_lines = result.stdout.splitlines()
            self.assertEqual(stdout_lines[-1], module.OK_TOKEN)
            payload = json.loads(stdout_lines[0])
            self.assertEqual(payload["destructive_started"], 0)
            self.assertEqual(payload["ready_to_apply"], 0)
            self.assertEqual(payload["migration"]["package_f1_head"], "f1_0015")
            self.assertEqual(payload["migration"]["target_f1_head"], "f1_0016")


if __name__ == "__main__":
    unittest.main()
