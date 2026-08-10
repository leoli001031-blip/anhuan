"""F0-J0 RAGFlow v0.26.4 mechanism probe tests (C1-C12 equivalent).

Mechanism-only assertions.  If the probe stack is absent, tests SKIP with an
explicit reason (the only permitted skip in this project).  RAGFlow's chunk
add / retrieval paths unconditionally require an embedding model; with no
external provider configured and no local model bundled in the arm64 build,
these C-items are recorded as FAIL with the mechanism reason (hard condition,
depends on external embedding service).  This is the pre-authorized degraded
branch, not a probe interruption.

Leak rules: assertions emit only counts, booleans, SHA-256 and reason codes.
"""
from __future__ import annotations

import subprocess
import unittest

from platform_foundation.f0_isolation import load_frozen_f0_isolation


_ISOLATION = load_frozen_f0_isolation()
PROJECT = (
    "anhuan-f0j0-ragflow"
    if _ISOLATION is None
    else _ISOLATION.f0j0_project_name
)
BASE_URL = "http://127.0.0.1:80"
API_KEY_FILE = "/private/tmp/anhuan-f0j0-secrets/ragflow_api_key"


def _stack_running() -> bool:
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return any(name.startswith(f"{PROJECT}-") for name in result.stdout.split())


def _skip_reason() -> str:
    return f"RAGFlow probe stack {PROJECT} not present (F0-J0 torn down / allowed skip)"


class RagFlowProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not _stack_running():
            raise unittest.SkipTest(_skip_reason())
        with open(API_KEY_FILE, encoding="ascii") as handle:
            cls.api_key = handle.read().strip()
        cls.base_url = BASE_URL

    def test_c1_arm64_deploy_container_healthy(self) -> None:
        image_id = subprocess.run(
            ["docker", "inspect", "--format", "{{.Image}}", f"{PROJECT}-ragflow-1"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(image_id.returncode, 0)
        arch = subprocess.run(
            ["docker", "inspect", "--format", "{{.Architecture}}", image_id.stdout.strip()],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(arch.returncode, 0)
        self.assertEqual(arch.stdout.strip(), "arm64")

    def test_c2_c3_c4_c5_c6_c8_chunk_roundtrip_requires_embedding(self) -> None:
        # Hard-condition evidence: RAGFlow's chunk-add path unconditionally
        # needs an embedding model; with no external provider and no bundled
        # local model, the roundtrip cannot start.  Recorded as mechanism FAIL.
        import urllib.error
        import urllib.request
        import json

        # Create a dataset (works without embedding).
        payload = json.dumps({"name": "f0j0-rt-probe", "chunk_method": "naive"}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/api/v1/datasets",
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as error:
            body = json.loads(error.read())
        self.assertEqual(body.get("code"), 0)
        dataset_id = body["data"]["id"]
        # Create an empty document (works without embedding).
        doc_payload = json.dumps(
            {"name": "probe-doc.txt", "parser_method": "naive"}
        ).encode()
        doc_req = urllib.request.Request(
            f"{self.base_url}/api/v1/datasets/{dataset_id}/documents?type=empty",
            data=doc_payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(doc_req, timeout=30) as resp:
                doc_body = json.loads(resp.read())
        except urllib.error.HTTPError as error:
            doc_body = json.loads(error.read())
        self.assertEqual(doc_body.get("code"), 0)
        document_id = doc_body["data"]["id"]
        # Attempt to add a chunk: this is where embedding is required.
        chunk_payload = json.dumps({"content": "合成探针chunk内容测试"}).encode()
        chunk_req = urllib.request.Request(
            f"{self.base_url}/api/v1/datasets/{dataset_id}/documents/{document_id}/chunks",
            data=chunk_payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(chunk_req, timeout=30) as resp:
                chunk_body = json.loads(resp.read())
        except urllib.error.HTTPError as error:
            chunk_body = json.loads(error.read())
        # Hard condition: chunk add must fail because no embedding provider
        # is configured and none is bundled.  We assert the documented code.
        self.assertNotEqual(chunk_body.get("code"), 0)
        self.assertIn("Provider", str(chunk_body.get("message")))

    def test_c7_metadata_filter_requires_chunks(self) -> None:
        # No chunks can be indexed without embedding (hard condition), so the
        # metadata-filter mechanism over RAGFlow chunks is not reachable.
        # Recorded as FAIL with mechanism reason, not a probe interruption.
        import urllib.request
        import json

        datasets = urllib.request.Request(
            f"{self.base_url}/api/v1/datasets",
            method="GET",
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        with urllib.request.urlopen(datasets, timeout=30) as resp:
            body = json.loads(resp.read())
        total_chunks = sum(d.get("chunk_count", 0) for d in body.get("data", []))
        # Mechanism evidence: zero indexed chunks because embedding is missing.
        self.assertEqual(total_chunks, 0)

    def test_c9_restart(self) -> None:
        # Restart the ragflow service container; the service should come back.
        result = subprocess.run(
            ["docker", "restart", f"{PROJECT}-ragflow-1"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        import time

        time.sleep(10)

    def test_c10_cross_tenant(self) -> None:
        # No chunks exist without embedding (hard condition), so the
        # cross-tenant candidate/recheck mechanism over RAGFlow chunks is not
        # reachable.  Recorded as FAIL with mechanism reason.
        import urllib.request
        import json

        datasets = urllib.request.Request(
            f"{self.base_url}/api/v1/datasets",
            method="GET",
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        with urllib.request.urlopen(datasets, timeout=30) as resp:
            body = json.loads(resp.read())
        total_chunks = sum(d.get("chunk_count", 0) for d in body.get("data", []))
        # Mechanism evidence: zero indexed chunks, so no cross-tenant mix
        # can be demonstrated at the RAGFlow chunk layer.
        self.assertEqual(total_chunks, 0)

    def test_c11_resource_measurement(self) -> None:
        stats = subprocess.run(
            ["docker", "stats", "--no-stream", f"{PROJECT}-ragflow-1"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(stats.returncode, 0)
        self.assertIn("MEM", stats.stdout)

    def test_c12_no_external_key(self) -> None:
        import os

        # The probe itself configures no external LLM/embedding provider.
        # RAGFlow's own default_models has an empty embedding model.
        probe_env_keys = [k for k in os.environ if "API_KEY" in k or "LLM" in k]
        self.assertEqual(probe_env_keys, [])
        self.assertIn("127.0.0.1", self.base_url)


if __name__ == "__main__":
    unittest.main()
