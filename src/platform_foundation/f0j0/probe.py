"""F0-J0 OpenSearch mechanism probe (C1-C12).

Mechanism-only.  No accuracy claims.  Bodies stay in memory / in the named
Docker volume; nothing plaintext touches the host filesystem.  Query terms
are derived from XLSX/DOCX structure-derived chunk bodies; only SHA-256 of
each term is ever emitted by the receipt layer.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from typing import Any

from .index_schema import INDEX_NAME, MAPPING, document_fields, field_sha256
from .os_client import OpenSearchClient
from .reader import ChunkIndexDoc


def derive_query_terms(
    documents: list[ChunkIndexDoc], count: int = 3
) -> list[str]:
    """Pick Chinese n-grams from XLSX/DOCX chunk bodies (structure-derived).

    Only used to form search terms; each term's SHA-256 is emitted, never the
    term text itself.
    """
    texts: list[str] = []
    for doc in documents:
        if doc.kind in ("XLSX", "DOCX"):
            texts.append(doc.body.decode("utf-8", errors="ignore"))
    combined = "\n".join(texts)
    # Chinese bigrams/trigrams of non-punctuation runs.
    candidates: Counter[str] = Counter()
    for run in re.findall(r"[一-鿿]+", combined):
        for size in (2, 3, 4):
            for index in range(0, max(0, len(run) - size + 1)):
                candidates[run[index : index + size]] += 1
    # Prefer terms that appear frequently and are not purely digits.
    ranked = [
        term
        for term, _freq in candidates.most_common()
        if any("一" <= ch <= "鿿" for ch in term)
        and not term.isdigit()
    ]
    return ranked[:count]


def term_sha256(term: str) -> str:
    return hashlib.sha256(term.encode("utf-8")).hexdigest()


def build_import_batches(
    documents: list[ChunkIndexDoc], size: int = 100
) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    for start in range(0, len(documents), size):
        batch: list[dict[str, Any]] = []
        for doc in documents[start : start + size]:
            fields = document_fields(doc)
            batch.append(
                {"_id": str(doc.chunk_id), "fields": fields, "sha": field_sha256(fields)}
            )
        batches.append(batch)
    return batches


def expected_field_hashes(documents: list[ChunkIndexDoc]) -> dict[str, str]:
    out: dict[str, str] = {}
    for doc in documents:
        out[str(doc.chunk_id)] = field_sha256(document_fields(doc))
    return out


def filter_terms_for_tenant_b(documents: list[ChunkIndexDoc]) -> list[str]:
    """Return up to 3 short Chinese terms to embed in synthetic tenant-B bodies."""
    terms = derive_query_terms(documents, count=3)
    return terms


def synthetic_tenant_b_docs(
    documents: list[ChunkIndexDoc], shared_terms: list[str]
) -> list[dict[str, Any]]:
    """Five synthetic tenant-B chunks with a made-up tenant id.

    Bodies reuse the induced shared terms so index-level matching can mix them
    into tenant-A candidates (proving the index layer is tenant-agnostic).
    """
    tenant_b = uuid.uuid4()
    docs: list[dict[str, Any]] = []
    for index in range(5):
        chunk_id = uuid.uuid4()
        body = (
            "合成租户B资料片段 " + " ".join(shared_terms) + f" 第{index}号样例"
        )
        fields = {
            "chunk_id": str(chunk_id),
            "parent_chunk_id": str(uuid.uuid4()),
            "document_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_b),
            "kind": "XLSX",
            "char_count": len(body),
            "pages": [],
            "body": body,
        }
        docs.append({"_id": str(chunk_id), "fields": fields, "sha": field_sha256(fields)})
    return docs


def index_documents(
    client: OpenSearchClient, batches: list[list[dict[str, Any]]]
) -> int:
    total = 0
    for batch in batches:
        status, ok, errors = client.bulk(INDEX_NAME, batch)
        if status not in (200, 201) or errors:
            raise RuntimeError(f"BULK_FAILED status={status} errors={errors}")
        total += ok
    # Make the writes visible to subsequent searches/counts.
    client._request("POST", f"/{INDEX_NAME}/_refresh")
    return total


__all__ = (
    "build_import_batches",
    "derive_query_terms",
    "expected_field_hashes",
    "filter_terms_for_tenant_b",
    "index_documents",
    "synthetic_tenant_b_docs",
    "term_sha256",
)
