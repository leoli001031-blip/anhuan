"""OpenSearch index schema for the F0-J0 probe.

Chinese text uses the default ``standard`` analyzer (IK and other Chinese
plugins are recorded as a later evaluation item, not installed here).
"""
from __future__ import annotations

INDEX_NAME = "anhuan-f0j0-chunks"

MAPPING: dict[str, object] = {
    "settings": {"number_of_shards": 1, "number_of_replicas": 0},
    "mappings": {
        "properties": {
            "chunk_id": {"type": "keyword"},
            "parent_chunk_id": {"type": "keyword"},
            "document_id": {"type": "keyword"},
            "tenant_id": {"type": "keyword"},
            "kind": {"type": "keyword"},
            "char_count": {"type": "integer"},
            "pages": {"type": "integer"},
            "body": {"type": "text", "analyzer": "standard"},
        }
    },
}


def document_fields(doc: object) -> dict[str, object]:
    """Project a ChunkIndexDoc into index-source JSON fields."""
    return {
        "chunk_id": str(doc.chunk_id),
        "parent_chunk_id": (
            str(doc.parent_chunk_id) if doc.parent_chunk_id is not None else None
        ),
        "document_id": str(doc.document_id),
        "tenant_id": str(doc.tenant_id),
        "kind": doc.kind,
        "char_count": doc.char_count,
        "pages": list(doc.pages),
        "body": doc.body.decode("utf-8", errors="strict"),
    }


def field_sha256(fields: dict[str, object]) -> str:
    """Canonical SHA-256 over metadata fields (never the body)."""
    import hashlib
    import json

    payload = {
        "chunk_id": fields["chunk_id"],
        "parent_chunk_id": fields["parent_chunk_id"],
        "document_id": fields["document_id"],
        "tenant_id": fields["tenant_id"],
        "kind": fields["kind"],
        "char_count": fields["char_count"],
        "pages": fields["pages"],
    }
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ("INDEX_NAME", "MAPPING", "document_fields", "field_sha256")
