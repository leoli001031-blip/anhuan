"""F0-J1 citation verification: PostgreSQL RLS recheck + body + page/bbox.

Every candidate chunk ID from RAGFlow must be re-verified against PostgreSQL
with the tenant context (enterprise/version/clearance/status).  Verified
chunks are decrypted/reassembled and their page numbers / bbox coordinates
are attached as citation evidence.  No source filenames are ever returned.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

from platform_foundation.auth import authenticate_local_session
from platform_foundation.bootstrap import LOCAL_TENANT_A_TOKEN
from platform_foundation.database import role_transaction
from platform_foundation.f0i.config import database_config
from platform_foundation.f0i.keyfile import ACCEPTANCE_KEY_FILE, load_keyfile
from platform_foundation.f0i.persistence import _CIPHER_OPTIONS, set_tenant_context


@dataclass(slots=True)
class Citation:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    tenant_id: uuid.UUID
    pages: tuple[int, ...]
    bbox: tuple[dict[str, Any], ...]  # one bbox per linked block, if present
    body_sha256: str
    body: bytes


@dataclass(slots=True)
class CitationResult:
    verified: list[Citation]
    rejected: list[uuid.UUID]  # candidate IDs that failed PG recheck


def verify_citations(chunk_ids: list[uuid.UUID]) -> CitationResult:
    """Re-verify candidate chunk IDs under the tenant context.

    Read-only.  A candidate is verified only if its chunk row exists for the
    tenant with CHILD level and its ciphertext decrypts to the registered
    plaintext hash.  Page numbers and bbox come from the linked blocks.
    """
    config = database_config()
    context = authenticate_local_session(config, LOCAL_TENANT_A_TOKEN)
    verified: list[Citation] = []
    rejected: list[uuid.UUID] = []
    if not chunk_ids:
        return CitationResult(verified, rejected)
    with load_keyfile(ACCEPTANCE_KEY_FILE) as key:
        key_view = key.view()
        try:
            with role_transaction(config, "f0d_migration") as connection:
                set_tenant_context(connection, context)
                cursor = connection.cursor()
                cursor.execute(
                    """
                    SELECT c.id AS chunk_id,
                           c.document_scope_id AS document_id,
                           c.enterprise_id AS tenant_id,
                           c.body_plaintext_sha256,
                           f0f_crypto.pgp_sym_decrypt_bytea(
                             c.body_ciphertext, encode(%s::bytea, 'hex'), %s
                           ) AS body
                    FROM f0i.chunk AS c
                    WHERE c.enterprise_id = %s
                      AND c.chunk_level = 'CHILD'
                      AND c.id = ANY(%s)
                    """,
                    (
                        key_view,
                        _CIPHER_OPTIONS,
                        context.enterprise_id,
                        [str(cid) for cid in chunk_ids],
                    ),
                )
                rows = {r["chunk_id"]: r for r in cursor.fetchall()}
                # Page / bbox association via chunk_block_link -> block.
                cursor.execute(
                    """
                    SELECT link.chunk_id,
                           b.page_no,
                           b.bbox_ppm,
                           b.location_status
                    FROM f0i.chunk_block_link AS link
                    JOIN f0i.block AS b
                      ON b.enterprise_id = link.enterprise_id
                     AND b.id = link.block_id
                    WHERE link.enterprise_id = %s
                      AND link.chunk_id = ANY(%s)
                    ORDER BY link.chunk_id, link.link_ordinal
                    """,
                    (
                        context.enterprise_id,
                        [str(cid) for cid in chunk_ids],
                    ),
                )
                links: dict[uuid.UUID, list[dict[str, Any]]] = {}
                for row in cursor.fetchall():
                    links.setdefault(row["chunk_id"], []).append(
                        {
                            "page_no": row["page_no"],
                            "bbox": row["bbox_ppm"],
                            "location_status": row["location_status"],
                        }
                    )
        finally:
            key_view.release()
    for cid in chunk_ids:
        row = rows.get(cid)
        if row is None:
            rejected.append(cid)
            continue
        body = bytes(row["body"])
        if hashlib.sha256(body).hexdigest() != str(row["body_plaintext_sha256"]):
            rejected.append(cid)
            continue
        pages: list[int] = []
        bboxes: list[dict[str, Any]] = []
        for link in links.get(cid, []):
            if link["page_no"] is not None:
                pages.append(int(link["page_no"]))
            if link["bbox"] is not None:
                bboxes.append({"bbox": link["bbox"], "location_status": link["location_status"]})
        verified.append(
            Citation(
                chunk_id=cid,
                document_id=row["document_id"],
                tenant_id=row["tenant_id"],
                pages=tuple(sorted(set(pages))),
                bbox=tuple(bboxes),
                body_sha256=str(row["body_plaintext_sha256"]),
                body=body,
            )
        )
    return CitationResult(verified, rejected)


__all__ = ("Citation", "CitationResult", "verify_citations")
