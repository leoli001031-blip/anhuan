"""F0-J1 chunk reader.

Reuses the F0-J0 read-only wrapper (which itself reuses the F0-I connection /
tenant context / decryption path).  No crypto or DSN code is copied here.
"""
from __future__ import annotations

from platform_foundation.f0j0.reader import (
    ChunkIndexDoc,
    chunk_summary,
    read_child_chunks,
    resolve_parents,
)

__all__ = ("ChunkIndexDoc", "chunk_summary", "read_child_chunks", "resolve_parents")
