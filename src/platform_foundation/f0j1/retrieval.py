"""F0-J1 retrieval: business-corpus-domain queries -> candidate chunk IDs.

The API accepts only opaque dataset ids that the platform itself created
(business corpus domain).  Client-supplied index/dataset/table/database
names are rejected outright.
"""
from __future__ import annotations

import re
import time
import uuid
from typing import Any

from .ragflow_client import RagFlowClient, RagFlowProbeError

# Only opaque UUID dataset ids are acceptable corpus-domain references.
_UUID_RE = re.compile(r"^[0-9a-f]{32}$")


def validate_domain(dataset_ids: list[str]) -> list[str]:
    """Reject any non-UUID / index-name / table-name / database-name input."""
    if not isinstance(dataset_ids, list) or not dataset_ids:
        raise ValueError("INVALID_CORPUS_DOMAIN")
    cleaned: list[str] = []
    for dataset_id in dataset_ids:
        value = str(dataset_id).strip()
        if not _UUID_RE.fullmatch(value):
            raise ValueError("INVALID_CORPUS_DOMAIN")
        cleaned.append(value)
    return cleaned


class RetrievalService:
    def __init__(self, client: RagFlowClient, token: str) -> None:
        self.client = client
        self.token = token

    def search(self, query: str, dataset_ids: list[str], size: int = 5) -> list[str]:
        """Return canonical chunk IDs for the query within the corpus domain.

        RAGFlow's /retrieval returns its own chunk ``id`` (xxhash) without
        metadata; the canonical chunk_id lives in the chunk's tag_kwd and is
        retrieved via the chunk-detail API.  Raises ValueError for illegal
        corpus-domain input; a short retry absorbs transient 502s right after
        a service restart.
        """
        domain = validate_domain(dataset_ids)
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                chunks = self.client.retrieval(self.token, domain, query, page_size=size)
                candidates: list[str] = []
                for chunk in chunks:
                    canonical = self._canonical_chunk_id(chunk)
                    if canonical:
                        candidates.append(canonical)
                return candidates
            except RagFlowProbeError as error:
                last_error = error
                time.sleep(2 * (attempt + 1))
        raise last_error  # type: ignore[misc]

    def _canonical_chunk_id(self, chunk: dict[str, Any]) -> str | None:
        """Resolve a retrieval hit to its canonical chunk_id via detail API."""
        import json
        import urllib.request

        hit_id = chunk.get("chunk_id") or chunk.get("id")
        doc_id = chunk.get("document_id") or chunk.get("doc_id")
        dataset_id = chunk.get("dataset_id") or chunk.get("kb_id")
        if not hit_id or not doc_id or not dataset_id:
            return None
        url = (
            f"{self.client.base_url}/api/v1/datasets/{dataset_id}"
            f"/documents/{doc_id}/chunks/{hit_id}"
        )
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.token}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read())
        except Exception:  # noqa: BLE001
            return None
        tags = data.get("data", {}).get("tag_kwd", [])
        for tag in tags:
            key, _, value = tag.partition("=")
            if key == "chunk_id":
                return value
        return None


__all__ = ("RetrievalService", "validate_domain")
