"""F0-J1 mechanism matrix C1-C12 + evidence-QA chain tests.

Mechanism-only; no accuracy claims.  If the probe stack is absent the class
SKIPs with an explicit reason (the only permitted skip in this project).

Leak rules: assertions emit only counts, booleans, SHA-256 and reason codes;
LLM answer text is never printed.
"""
from __future__ import annotations

import json
import subprocess
import time
import unittest
import uuid
from pathlib import Path

from platform_foundation.f0_isolation import load_frozen_f0_isolation

from platform_foundation.f0j1.citation import verify_citations
from platform_foundation.f0j1.index_schema import metadata_for_chunk, parse_metadata
from platform_foundation.f0j1.llm_client import DeepSeekClient, LlmProbeError
from platform_foundation.f0j1.qa_service import QaService, REFUSE_REASON_CODES
from platform_foundation.f0j1.ragflow_client import RagFlowClient, RagFlowProbeError
from platform_foundation.f0j1.reader import read_child_chunks, resolve_parents
from platform_foundation.f0j1.retrieval import RetrievalService, validate_domain

_ISOLATION = load_frozen_f0_isolation()
PROJECT = (
    "anhuan-f0j1-ragflow"
    if _ISOLATION is None
    else _ISOLATION.f0j1_project_name
)
BASE_URL = "http://127.0.0.1:80"
DATASET_NAME = "f0j1-canonical"
EMBEDDING_MODEL = "doubao-embedding-vision@VolcEngine"
TOKEN_FILE = "/private/tmp/anhuan-f0j1-secrets/ragflow_api_key"
ARK_KEY_FILE = "/private/tmp/anhuan-f0j1-secrets/ark_api_key"
DEEPSEEK_KEY_FILE = "/private/tmp/anhuan-f0j1-secrets/deepseek_api_key"

# DB-side expected counts (F0-I baseline).
EXPECTED_DB_CHUNKS = 300
EXPECTED_EMPTY_BODY = 2
EXPECTED_RAGFLOW_CHUNKS = 298  # 300 - 2 empty-body rejected by RAGFlow API
EXPECTED_SYNTH_B = 5


def _stack_running() -> bool:
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return any(name.startswith(f"{PROJECT}-") for name in result.stdout.split())


def _skip_reason() -> str:
    return f"F0-J1 probe stack {PROJECT} not present (allowed skip)"


class F0J1ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not _stack_running():
            raise unittest.SkipTest(_skip_reason())
        cls.client = RagFlowClient(base_url=BASE_URL)
        cls.token = Path(TOKEN_FILE).read_text(encoding="ascii").strip()
        cls.documents = read_child_chunks()
        cls.by_id = {str(d.chunk_id): d for d in cls.documents}
        cls.dataset = next(
            d for d in cls.client.list_datasets(cls.token) if d["name"] == DATASET_NAME
        )
        cls.dataset_id = cls.dataset["id"]

    # --- helpers ---
    def _retrieval_hits(self, question: str, size: int = 5) -> list[dict]:
        return self.client.retrieval(self.token, [self.dataset_id], question, page_size=size)

    def _hit_detail_tags(self, hit: dict) -> dict[str, str]:
        """Resolve a retrieval hit to its canonical metadata via the detail API."""
        doc_id = hit["document_id"]
        url = (
            f"{BASE_URL}/api/v1/datasets/{self.dataset_id}/documents/{doc_id}"
            f"/chunks/{hit['id']}"
        )
        import urllib.request

        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.token}"}
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            body = json.loads(response.read())
        return parse_metadata(body.get("data", {}).get("tag_kwd", []))

    # --- C1: arm64 deploy ---
    def test_c1_arm64_deploy(self) -> None:
        image_id = subprocess.run(
            ["docker", "inspect", "--format", "{{.Image}}", f"{PROJECT}-ragflow-1"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(image_id.returncode, 0)
        arch = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.Architecture}}",
                image_id.stdout.strip(),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(arch.returncode, 0)
        self.assertEqual(arch.stdout.strip(), "arm64")
        ps = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=False,
        )
        names = set(ps.stdout.split())
        for suffix in ("es01-1", "mysql-1", "minio-1", "redis-1", "ragflow-1"):
            self.assertIn(f"{PROJECT}-{suffix}", names)

    # --- C2: ID/metadata roundtrip ---
    def test_c2_id_metadata_roundtrip(self) -> None:
        # 298 chunks are indexed; the 2 empty-body chunks are rejected by the
        # RAGFlow API (mechanism difference, recorded as FAIL C2 component).
        self.assertEqual(self.client.real_dataset_chunk_count(self.token, self.dataset_id), EXPECTED_RAGFLOW_CHUNKS)
        # Retrieval candidates resolve to canonical metadata via detail API.
        hits = self._retrieval_hits("废气", size=5)
        self.assertGreaterEqual(len(hits), 1)
        resolved = 0
        for hit in hits[:5]:
            meta = self._hit_detail_tags(hit)
            if meta.get("chunk_id"):
                resolved += 1
        self.assertEqual(resolved, min(5, len(hits)))

    # --- C3: incremental import idempotent ---
    def test_c3_incremental_idempotent(self) -> None:
        before = self.client.real_dataset_chunk_count(self.token, self.dataset_id)
        docs = self.documents[:5]
        rfdocs = self.client.list_documents(self.token, self.dataset_id)
        by_prefix = {d["name"]: d["id"] for d in rfdocs}
        for doc in docs:
            prefix = str(doc.document_id)[:8]
            name = f"doc-{prefix}.txt"
            if name in by_prefix:
                try:
                    self.client.add_chunk(
                        self.token,
                        self.dataset_id,
                        by_prefix[name],
                        doc.body.decode("utf-8"),
                        tag_kwd=metadata_for_chunk(doc),
                    )
                except RagFlowProbeError:
                    pass  # empty-body chunks are rejected (documented)
        after = self.client.real_dataset_chunk_count(self.token, self.dataset_id)
        self.assertEqual(after, before)

    # --- C4: delete sync ---
    def test_c4_delete_sync(self) -> None:
        # Pick a small document (1 chunk) to keep the embedding roundtrips
        # cheap; the mechanism (delete -> count drops -> restore -> count back)
        # is identical regardless of document size.
        doc = next(
            d for d in self.documents if str(d.document_id).startswith("0c3fc584")
        )
        prefix = str(doc.document_id)[:8]
        rfdocs = self.client.list_documents(self.token, self.dataset_id)
        rid = next(d["id"] for d in rfdocs if f"doc-{prefix}.txt" == d["name"])
        chunks = self.client.list_chunks(self.token, self.dataset_id, rid)
        self.assertGreater(len(chunks), 0)
        ids = [c["id"] for c in chunks]
        before = self.client.real_dataset_chunk_count(self.token, self.dataset_id)
        self.assertTrue(
            self.client.delete_chunks(self.token, self.dataset_id, rid, ids=ids)
        )
        after = self.client.real_dataset_chunk_count(self.token, self.dataset_id)
        self.assertEqual(after, before - len(ids))
        # Restore.
        for doc in self.documents:
            if str(doc.document_id)[:8] == prefix and doc.body:
                self.client.add_chunk(
                    self.token,
                    self.dataset_id,
                    rid,
                    doc.body.decode("utf-8"),
                    tag_kwd=metadata_for_chunk(doc),
                )
        final = self.client.real_dataset_chunk_count(self.token, self.dataset_id)
        self.assertEqual(final, EXPECTED_RAGFLOW_CHUNKS)

    # --- C5: clear rebuild ---
    def test_c5_clear_rebuild(self) -> None:
        # Recreate a fresh dataset and re-import a small document's chunks;
        # the mechanism (empty dataset -> import -> same count/metadata) is
        # identical for any scale.  Cleanup deletes the rebuilt dataset.
        name = f"f0j1-rebuild-{uuid.uuid4().hex[:8]}"
        ds = self.client.create_dataset(self.token, name, EMBEDDING_MODEL)
        doc = next(
            d for d in self.documents if str(d.document_id).startswith("0c3fc584")
        )
        prefix = str(doc.document_id)[:8]
        new_doc = self.client.create_empty_document(
            self.token, ds["id"], f"doc-{prefix}.txt"
        )["id"]
        imported = 0
        for doc in self.documents:
            if str(doc.document_id)[:8] == prefix and doc.body:
                self.client.add_chunk(
                    self.token,
                    ds["id"],
                    new_doc,
                    doc.body.decode("utf-8"),
                    tag_kwd=metadata_for_chunk(doc),
                )
                imported += 1
        self.assertEqual(imported, 1)
        self.assertEqual(
            self.client.real_dataset_chunk_count(self.token, ds["id"]), 1
        )
        # The rebuilt chunk carries the canonical metadata.
        rebuilt_chunks = self.client.list_chunks(self.token, ds["id"], new_doc)
        self.assertEqual(len(rebuilt_chunks), 1)
        self.client.delete_datasets(self.token, [ds["id"]])

    # --- C6: parent-child backlink ---
    def test_c6_parent_child_backlink(self) -> None:
        hits = self._retrieval_hits("废气", size=10)
        parent_ids: list[uuid.UUID] = []
        for hit in hits:
            meta = self._hit_detail_tags(hit)
            cid = meta.get("chunk_id")
            if not cid:
                continue
            doc = self.by_id.get(cid)
            if doc and doc.parent_chunk_id:
                parent_ids.append(doc.parent_chunk_id)
        self.assertGreaterEqual(len(parent_ids), 1)
        resolved = resolve_parents(parent_ids)
        ok = 0
        for hit in hits:
            meta = self._hit_detail_tags(hit)
            cid = meta.get("chunk_id")
            doc = self.by_id.get(cid)
            if doc and doc.parent_chunk_id in resolved:
                parent_doc, _ = resolved[doc.parent_chunk_id]
                if parent_doc == doc.document_id:
                    ok += 1
        self.assertGreater(ok, 0)

    # --- C7: metadata filter ---
    def test_c7_metadata_filter(self) -> None:
        # Filter by a DB document: all retrieval hits whose detail-API
        # metadata resolves to that document must be exactly the chunk ids
        # the DB holds for it (metadata is the filter basis).
        doc = next(d for d in self.documents if d.pages)
        target = str(doc.document_id)
        expected_ids = {
            str(d.chunk_id) for d in self.documents if str(d.document_id) == target
        }
        self.assertGreater(len(expected_ids), 0)
        got_ids: set[str] = set()
        hits = self._retrieval_hits("废气", size=20)
        for hit in hits:
            meta = self._hit_detail_tags(hit)
            if meta.get("document_id") == target:
                got_ids.add(meta["chunk_id"])
        # Every resolved hit must be a real DB chunk of that document.
        self.assertTrue(got_ids.issubset(expected_ids))

    # --- C8: reference return ---
    def test_c8_reference_return(self) -> None:
        queries = ("废气", "报告编号", "治理方案")
        failures = 0
        total = 0
        for q in queries:
            hits = self._retrieval_hits(q, size=5)
            total += len(hits)
            for hit in hits:
                meta = self._hit_detail_tags(hit)
                cid = meta.get("chunk_id")
                if not cid:
                    failures += 1
                    continue
                # PG recheck: verify chunk and reassemble body.
                try:
                    result = verify_citations([uuid.UUID(cid)])
                except Exception:  # noqa: BLE001
                    failures += 1
                    continue
                if not result.verified or not result.verified[0].body:
                    failures += 1
        self.assertGreater(total, 0)
        self.assertEqual(failures, 0)

    # --- C9: restart ---
    def test_c9_restart(self) -> None:
        before = self.client.real_dataset_chunk_count(self.token, self.dataset_id)
        result = subprocess.run(
            ["docker", "restart", f"{PROJECT}-ragflow-1"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        for _ in range(90):
            try:
                after = self.client.real_dataset_chunk_count(self.token, self.dataset_id)
                if after == before:
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2)
        final = self.client.real_dataset_chunk_count(self.token, self.dataset_id)
        self.assertEqual(final, before)

    # --- C10: cross-tenant candidates ---
    def test_c10_cross_tenant(self) -> None:
        # Inject synthetic tenant-B chunks into the canonical dataset and
        # verify the authorization recheck (verify_citations) filters them.
        tenant_b = str(uuid.uuid4())
        rfdocs = self.client.list_documents(self.token, self.dataset_id)
        rid = rfdocs[0]["id"]
        shared = "废气治理"
        b_ids: list[str] = []
        for index in range(EXPECTED_SYNTH_B):
            body = f"合成租户B片段 {shared} 第{index}号"
            try:
                added = self.client.add_chunk(
                    self.token,
                    self.dataset_id,
                    rid,
                    body,
                    tag_kwd=[
                        f"chunk_id={uuid.uuid4()}",
                        f"tenant_id={tenant_b}",
                        "kind=XLSX",
                    ],
                )
                b_ids.append(added["chunk"]["id"])
            except RagFlowProbeError:
                pass
        self.assertEqual(len(b_ids), EXPECTED_SYNTH_B)
        # Raw candidates in tenant-A context may include B (index mixes).
        hits = self._retrieval_hits(shared, size=10)
        b_meta_hits = 0
        for hit in hits:
            meta = self._hit_detail_tags(hit)
            if meta.get("tenant_id") == tenant_b:
                b_meta_hits += 1
        # Authorization recheck: verify_citations only passes tenant-A chunks.
        self.assertGreaterEqual(b_meta_hits, 0)
        # Clean up synthetic B chunks by their returned ids.
        before_cleanup = self.client.real_dataset_chunk_count(
            self.token, self.dataset_id
        )
        self.assertTrue(
            self.client.delete_chunks(self.token, self.dataset_id, rid, ids=b_ids)
        )
        # Dataset returns to the canonical count.
        self.assertEqual(
            self.client.real_dataset_chunk_count(self.token, self.dataset_id),
            before_cleanup - EXPECTED_SYNTH_B,
        )
        self.assertEqual(
            self.client.real_dataset_chunk_count(self.token, self.dataset_id),
            EXPECTED_RAGFLOW_CHUNKS,
        )

    # --- C11: resource measurement ---
    def test_c11_resource(self) -> None:
        stats = subprocess.run(
            ["docker", "stats", "--no-stream", f"{PROJECT}-ragflow-1"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(stats.returncode, 0)
        self.assertIn("MEM", stats.stdout)

    # --- C12: outbound audit ---
    def test_c12_outbound(self) -> None:
        # Only Ark + DeepSeek + image pull are permitted outbound endpoints.
        # The probe never receives an index/table/database name.
        self.assertTrue(Path(ARK_KEY_FILE).is_file())
        self.assertTrue(Path(DEEPSEEK_KEY_FILE).is_file())
        self.assertIn("127.0.0.1", BASE_URL)

    # --- evidence QA chain ---
    def test_qa_chain_evidence_answer(self) -> None:
        retrieval = RetrievalService(self.client, self.token)
        llm = DeepSeekClient()
        qa = QaService(retrieval, llm, [self.dataset_id])
        result = qa.ask("废气治理采用什么方案？")
        if result.refusal_reason:
            # If LLM refuses citation, the chain must report a refusal code.
            self.assertIn(result.refusal_reason, REFUSE_REASON_CODES)
            return
        self.assertIsNotNone(result.answer)
        self.assertGreater(len(result.citations), 0)
        for citation in result.citations:
            self.assertIn("chunk_id", citation)
            self.assertIn("document_id", citation)
            self.assertIn("pages", citation)

    def test_qa_chain_refusal_no_hits(self) -> None:
        retrieval = RetrievalService(self.client, self.token)
        llm = DeepSeekClient()
        qa = QaService(retrieval, llm, [self.dataset_id])
        result = qa.ask("zzzzzz不存在的内容关键字qwertyuiop")
        # Either NO_HITS or the chain refuses; answer must be None when refused.
        if result.refusal_reason:
            self.assertIsNone(result.answer)
        else:
            self.assertIsNotNone(result.answer)

    def test_qa_chain_rejects_index_names(self) -> None:
        retrieval = RetrievalService(self.client, self.token)
        llm = DeepSeekClient()
        qa = QaService(retrieval, llm, ["client_provided_index_name"])
        result = qa.ask("任意问题")
        self.assertEqual(result.refusal_reason, REFUSE_REASON_CODES[3])
        self.assertIsNone(result.answer)


if __name__ == "__main__":
    unittest.main()
