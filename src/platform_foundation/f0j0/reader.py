"""Minimal read-only wrapper over F0-I canonical child chunks.

Reuses ``platform_foundation.f0i`` connection / tenant context / decryption
path (``database_config``, ``authenticate_local_session``, ``set_tenant_context``,
``load_keyfile``).  Does NOT copy crypto or DSN code: the pgp decryption is
delegated to the same f0f_crypto functions the F0-I module uses, and the only
cipher options constant is imported from ``f0i.persistence``.

This is a probe-only surface.  It streams decrypted bodies to the caller in
memory; nothing is written to the host filesystem.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from platform_foundation.auth import authenticate_local_session
from platform_foundation.bootstrap import LOCAL_TENANT_A_TOKEN
from platform_foundation.database import role_transaction
from platform_foundation.f0i.config import database_config
from platform_foundation.f0i.keyfile import ACCEPTANCE_KEY_FILE, load_keyfile
from platform_foundation.f0i.persistence import _CIPHER_OPTIONS, set_tenant_context


@dataclass(slots=True)
class ChunkIndexDoc:
    """One searchable child chunk document (body held only in memory)."""

    chunk_id: uuid.UUID
    parent_chunk_id: uuid.UUID | None
    document_id: uuid.UUID
    tenant_id: uuid.UUID
    kind: str
    char_count: int
    pages: tuple[int, ...]
    body: bytes


def read_child_chunks() -> list[ChunkIndexDoc]:
    """Read all tenant-A child chunks (300) with decrypted bodies.

    Returns documents without printing any content.  Uses the migration role
    + ``set_tenant_context`` exactly like the F0-I replay read path.
    """
    config = database_config()
    context = authenticate_local_session(config, LOCAL_TENANT_A_TOKEN)
    with load_keyfile(ACCEPTANCE_KEY_FILE) as key:
        key_view = key.view()
        try:
            return _read_with_key(config, context, key_view)
        finally:
            key_view.release()


def _read_with_key(
    config: Any, context: Any, key_view: Any
) -> list[ChunkIndexDoc]:
    documents: list[ChunkIndexDoc] = []
    with role_transaction(config, "f0d_migration") as connection:
        set_tenant_context(connection, context)
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT c.id AS chunk_id,
                   c.parent_chunk_id,
                   c.document_scope_id AS document_id,
                   c.enterprise_id AS tenant_id,
                   d.document_type AS kind,
                   c.body_plaintext_character_count AS char_count,
                   f0f_crypto.pgp_sym_decrypt_bytea(
                     c.body_ciphertext, encode(%s::bytea, 'hex'), %s
                   ) AS body
            FROM f0i.chunk AS c
            JOIN f0i.document_scope AS d
              ON d.enterprise_id = c.enterprise_id
             AND d.id = c.document_scope_id
            WHERE c.enterprise_id = %s
              AND c.chunk_level = 'CHILD'
            ORDER BY c.chunk_ordinal, c.id
            """,
            (key_view, _CIPHER_OPTIONS, context.enterprise_id),
        )
        rows = cursor.fetchall()
        # pages[] association: chunk_block_link -> block -> page.
        cursor.execute(
            """
            SELECT DISTINCT link.chunk_id, b.page_no
            FROM f0i.chunk_block_link AS link
            JOIN f0i.block AS b
              ON b.enterprise_id = link.enterprise_id
             AND b.id = link.block_id
            JOIN f0i.page AS p
              ON p.enterprise_id = b.enterprise_id
             AND p.id = b.page_id
            WHERE link.enterprise_id = %s
              AND b.page_no IS NOT NULL
            ORDER BY link.chunk_id, b.page_no
            """,
            (context.enterprise_id,),
        )
        pages_by_chunk: dict[uuid.UUID, set[int]] = {}
        for row in cursor.fetchall():
            pages_by_chunk.setdefault(row["chunk_id"], set()).add(
                int(row["page_no"])
            )
    for row in rows:
        chunk_id = row["chunk_id"]
        documents.append(
            ChunkIndexDoc(
                chunk_id=chunk_id,
                parent_chunk_id=row["parent_chunk_id"],
                document_id=row["document_id"],
                tenant_id=row["tenant_id"],
                kind=row["kind"],
                char_count=int(row["char_count"]),
                pages=tuple(sorted(pages_by_chunk.get(chunk_id, set()))),
                body=bytes(row["body"]),
            )
        )
    return documents


def resolve_parents(
    parent_ids: list[uuid.UUID],
) -> dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID]]:
    """Resolve parent chunk ids in PostgreSQL.

    Returns {parent_chunk_id: (document_id, tenant_id)} for parent rows that
    exist in the tenant-A scope.  Read-only; reuses the same connection path.
    """
    config = database_config()
    context = authenticate_local_session(config, LOCAL_TENANT_A_TOKEN)
    output: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID]] = {}
    with role_transaction(config, "f0d_migration") as connection:
        set_tenant_context(connection, context)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, document_scope_id, enterprise_id "
                "FROM f0i.chunk WHERE enterprise_id=%s AND id=ANY(%s) "
                "AND chunk_level='PARENT'",
                (context.enterprise_id, [str(pid) for pid in parent_ids]),
            )
            for row in cursor.fetchall():
                output[row["id"]] = (
                    row["document_scope_id"],
                    row["enterprise_id"],
                )
    return output


def chunk_summary(documents: list[ChunkIndexDoc]) -> dict[str, int]:
    """Aggregate-safe summary of the chunk set (no content)."""
    kinds: set[str] = set()
    for doc in documents:
        kinds.add(doc.kind)
    return {
        "count": len(documents),
        "distinct_kinds": len(kinds),
        "with_pages": sum(1 for doc in documents if doc.pages),
        "without_pages": sum(1 for doc in documents if not doc.pages),
    }


__all__ = ("ChunkIndexDoc", "read_child_chunks", "resolve_parents", "chunk_summary")
