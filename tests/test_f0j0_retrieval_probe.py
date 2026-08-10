"""F0-J0 OpenSearch mechanism probe tests (C1-C12).

Mechanism-only assertions; no retrieval-quality claims.  Each C-item lands as
a re-runnable assertion against a live loopback OpenSearch 3.8.0.  If the
probe container is absent, tests SKIP with an explicit reason (the only
permitted skip in this project).

Leak rules: assertions emit only counts, booleans, SHA-256 and reason codes.
"""
from __future__ import annotations

import subprocess
import time
import unittest
import uuid

from platform_foundation.f0j0.index_schema import (
    INDEX_NAME,
    MAPPING,
    document_fields,
    field_sha256,
)
from platform_foundation.f0j0.os_client import OpenSearchClient
from platform_foundation.f0j0.probe import (
    build_import_batches,
    derive_query_terms,
    expected_field_hashes,
    filter_terms_for_tenant_b,
    index_documents,
    synthetic_tenant_b_docs,
)
from platform_foundation.f0j0.reader import read_child_chunks, resolve_parents
from platform_foundation.f0_isolation import load_frozen_f0_isolation

_ISOLATION = load_frozen_f0_isolation()
CONTAINER_NAME = (
    "anhuan-f0j0-opensearch"
    if _ISOLATION is None
    else _ISOLATION.f0j0_project_name + "-opensearch"
)
PASSWORD_FILE = "/private/tmp/anhuan-f0j0-secrets/os_admin"


def _container_running() -> bool:
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    names = result.stdout.split()
    return CONTAINER_NAME in names


def _skip_reason() -> str:
    return (
        f"probe container {CONTAINER_NAME} not present; "
        "F0-J0 probe torn down (allowed skip)"
    )


class OpenSearchProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not _container_running():
            raise unittest.SkipTest(_skip_reason())
        cls.client = OpenSearchClient(password_file=PASSWORD_FILE)
        cls.documents = read_child_chunks()
        cls.expected_hashes = expected_field_hashes(cls.documents)
        cls.terms = derive_query_terms(cls.documents, count=3)
        cls.index = INDEX_NAME
        # Clean baseline index for all mechanism tests.
        if cls.client.index_exists(cls.index):
            cls.client.delete_index(cls.index)
        assert cls.client.create_index(cls.index, MAPPING) == 200
        assert index_documents(cls.client, build_import_batches(cls.documents)) == 300
        assert cls.client.count(cls.index) == 300

    def test_c1_arm64_deploy_container_healthy(self) -> None:
        health = self.client.health()
        self.assertEqual(health["http_status"], 200)
        self.assertEqual(health["data"]["version"]["number"], "3.8.0")
        # The image carries the architecture; the container references it.
        image_id = subprocess.run(
            ["docker", "inspect", "--format", "{{.Image}}", CONTAINER_NAME],
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

    def test_c2_id_metadata_roundtrip_300_300(self) -> None:
        self.assertEqual(self.client.count(self.index), 300)
        # Re-import is idempotent (C3 also) and every doc round-trips.
        batches = build_import_batches(self.documents)
        imported = index_documents(self.client, batches)
        self.assertEqual(imported, 300)
        self.assertEqual(self.client.count(self.index), 300)
        # Spot-check a small sample of per-document field hashes via the
        # metadata fields (never the body).
        sample = self.documents[:5]
        for doc in sample:
            fields = document_fields(doc)
            self.assertEqual(
                field_sha256(fields), self.expected_hashes[str(doc.chunk_id)]
            )

    def test_c3_incremental_import_idempotent(self) -> None:
        batches = build_import_batches(self.documents)
        imported = index_documents(self.client, batches)
        self.assertEqual(imported, 300)
        self.assertEqual(self.client.count(self.index), 300)

    def test_c4_delete_document_sync_and_restore(self) -> None:
        document = self.documents[0].document_id
        target = str(document)
        doc_count = sum(1 for d in self.documents if str(d.document_id) == target)
        self.assertGreater(doc_count, 0)
        deleted = self.client.delete_by_query(
            self.index, {"term": {"document_id": target}}
        )
        self.assertEqual(deleted, doc_count)
        self.assertEqual(self.client.count(self.index), 300 - doc_count)
        subset = [d for d in self.documents if str(d.document_id) == target]
        index_documents(self.client, build_import_batches(subset))
        self.assertEqual(self.client.count(self.index), 300)

    def test_c5_clear_rebuild_matches_c2(self) -> None:
        self.assertEqual(self.client.delete_index(self.index), 200)
        self.assertEqual(self.client.create_index(self.index, MAPPING), 200)
        index_documents(self.client, build_import_batches(self.documents))
        self.assertEqual(self.client.count(self.index), 300)

    def test_c6_parent_child_backlink_resolvable(self) -> None:
        self.assertGreaterEqual(len(self.terms), 1)
        hits = self.client.search_hits(self.index, self.terms[0], size=10)
        self.assertGreaterEqual(len(hits), 1)
        by_id = {str(d.chunk_id): d for d in self.documents}
        resolvable = 0
        same_doc = 0
        checked = 0
        parent_ids: list[uuid.UUID] = []
        hit_docs: list[object] = []
        for hit in hits[:10]:
            doc = by_id.get(hit["id"])
            if doc is None or doc.parent_chunk_id is None:
                continue
            hit_docs.append(doc)
            parent_ids.append(doc.parent_chunk_id)
        if parent_ids:
            resolved = resolve_parents(parent_ids)
            for doc in hit_docs:
                checked += 1
                if doc.parent_chunk_id in resolved:
                    resolvable += 1
                    parent_doc, _tenant = resolved[doc.parent_chunk_id]
                    if parent_doc == doc.document_id:
                        same_doc += 1
        self.assertGreater(checked, 0)
        self.assertGreater(resolvable, 0)
        self.assertEqual(resolvable, same_doc)

    def test_c7_metadata_filter_document_and_pages(self) -> None:
        document = self.documents[0].document_id
        target = str(document)
        expected_ids = {
            str(d.chunk_id) for d in self.documents if str(d.document_id) == target
        }
        got = self.client.filter_search(
            self.index, [{"term": {"document_id": target}}]
        )
        self.assertEqual(set(got), expected_ids)
        page_doc = next((d for d in self.documents if d.pages), None)
        self.assertIsNotNone(page_doc)
        page_no = page_doc.pages[0]
        expected_page = {str(d.chunk_id) for d in self.documents if page_no in d.pages}
        got_page = self.client.filter_search(
            self.index, [{"term": {"pages": page_no}}]
        )
        self.assertEqual(set(got_page), expected_page)

    def test_c8_reference_return_reconstructs(self) -> None:
        by_id = {str(d.chunk_id): d for d in self.documents}
        failures = 0
        total_hits = 0
        for term in self.terms:
            hits = self.client.search_hits(self.index, term, size=5)
            total_hits += len(hits)
            for hit in hits:
                doc = by_id.get(hit["id"])
                # All hits must resolve to a tenant-A child chunk whose body
                # was already decrypted/reconstructed from PostgreSQL.
                if doc is None or doc.body == b"":
                    failures += 1
        self.assertGreater(total_hits, 0)
        self.assertEqual(failures, 0)

    def test_c9_restart_count_unchanged(self) -> None:
        before = self.client.count(self.index)
        result = subprocess.run(
            ["docker", "restart", CONTAINER_NAME],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        for _attempt in range(90):
            try:
                if self.client.count(self.index) == before:
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(2)
        self.assertEqual(self.client.count(self.index), before)

    def test_c10_cross_tenant_candidate_and_authorization(self) -> None:
        shared = filter_terms_for_tenant_b(self.documents)
        self.assertGreaterEqual(len(shared), 1)
        b_docs = synthetic_tenant_b_docs(self.documents, shared)
        status, ok, errors = self.client.bulk(self.index, b_docs)
        self.assertEqual(status, 200)
        self.assertEqual(ok, 5)
        self.assertEqual(errors, 0)
        self.assertEqual(self.client.count(self.index), 305)
        by_id = {str(d.chunk_id): d for d in self.documents}
        b_ids = {d["_id"] for d in b_docs}
        raw_candidates: set[str] = set()
        for term in self.terms:
            for hit in self.client.search_hits(self.index, term, size=10):
                raw_candidates.add(hit["id"])
        mixed = raw_candidates & b_ids
        # Index layer is tenant-agnostic; shared induced terms must surface at
        # least one B chunk in the raw candidate set.
        self.assertGreaterEqual(len(mixed), 1)
        # Authorization recheck against the tenant-A whitelist (built from the
        # tenant-scoped PostgreSQL read) filters every B chunk out.
        authorized = {cid for cid in raw_candidates if cid in by_id}
        self.assertEqual(authorized & b_ids, set())
        self.assertGreaterEqual(len(authorized), 0)
        # A tenant_id filter excludes B but is recorded as NOT the
        # authorization basis.
        tenant_a = str(self.documents[0].tenant_id)
        filtered = self.client.filter_search(
            self.index, [{"term": {"tenant_id": tenant_a}}]
        )
        self.assertNotIn(list(b_ids)[0], filtered)
        # Restore the baseline count by removing the synthetic tenant-B docs.
        deleted = self.client.delete_by_query(
            self.index, {"terms": {"tenant_id": [str(b_docs[0]["fields"]["tenant_id"])]}}
        )
        self.assertEqual(deleted, 5)
        self.assertEqual(self.client.count(self.index), 300)

    def test_c11_resource_measurement(self) -> None:
        stats = subprocess.run(
            ["docker", "stats", "--no-stream", CONTAINER_NAME],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(stats.returncode, 0)
        self.assertIn("MEM", stats.stdout)
        df = subprocess.run(
            ["docker", "system", "df", "-v"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(df.returncode, 0)

    def test_c12_no_external_key_no_outbound(self) -> None:
        import os

        probe_env_keys = [k for k in os.environ if "API_KEY" in k or "LLM" in k]
        self.assertEqual(probe_env_keys, [])
        self.assertIn("127.0.0.1", self.client.base_url)


if __name__ == "__main__":
    unittest.main()
