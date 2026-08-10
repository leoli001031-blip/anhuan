"""F0-J1 chunk metadata schema (mirrors the F0-J0 index fields).

The RAGFlow chunk ``content`` field holds the plaintext body; the
``tag_kwd`` field carries the opaque metadata needed to map candidates back
to PostgreSQL.  No source filenames are stored anywhere.
"""
from __future__ import annotations

from platform_foundation.f0j0.index_schema import document_fields, field_sha256

# Metadata keys carried in RAGFlow's tag_kwd list (no source filenames).
METADATA_KEYS = (
    "chunk_id",
    "parent_chunk_id",
    "document_id",
    "tenant_id",
    "kind",
    "char_count",
    "pages",
)


def metadata_for_chunk(doc: object) -> list[str]:
    """Serialize a chunk's opaque metadata into RAGFlow tag_kwd strings.

    Format: key=value for each METADATA_KEYS field.  Values are hex/uuid/
    numeric strings only — never content.
    """
    tags = [
        f"chunk_id={doc.chunk_id}",
        f"parent_chunk_id={doc.parent_chunk_id}",
        f"document_id={doc.document_id}",
        f"tenant_id={doc.tenant_id}",
        f"kind={doc.kind}",
        f"char_count={doc.char_count}",
        f"pages={','.join(str(p) for p in doc.pages)}",
    ]
    return tags


def parse_metadata(tags: list[str]) -> dict[str, str]:
    """Parse tag_kwd list back into a metadata dict."""
    out: dict[str, str] = {}
    for tag in tags or []:
        if "=" in tag:
            key, _, value = tag.partition("=")
            out[key] = value
    return out


__all__ = (
    "METADATA_KEYS",
    "document_fields",
    "field_sha256",
    "metadata_for_chunk",
    "parse_metadata",
)
