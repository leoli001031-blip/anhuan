"""F1 citation verification: RLS-bypassing bridge into the read-only F0-I.

Candidate chunk IDs from RAGFlow are re-verified against PostgreSQL under the
enterprise's F0-I tenant context (never hardcoded).  Uses the host-overridable
F1 DB layer so the API/worker run identically as host processes or containers.
"""
from __future__ import annotations

import hashlib
import uuid

import psycopg

from .auth import Tenant
from .secret_files import read_f0i_key

CIPHER_OPTIONS = "cipher-algo=aes256"


class VerifiedCitation:
    __slots__ = ("chunk_id", "document_id", "tenant_id", "pages", "body_sha256", "body")

    def __init__(self, chunk_id, document_id, tenant_id, pages, body_sha256, body):
        self.chunk_id = chunk_id
        self.document_id = document_id
        self.tenant_id = tenant_id
        self.pages = pages
        self.body_sha256 = body_sha256
        self.body = body


def _validated_rows(
    rows: list[object] | tuple[object, ...],
    requested_ids: set[uuid.UUID],
) -> list[VerifiedCitation]:
    """Validate the evidence returned by the database bridge.

    A candidate is usable only when its chunk identity was requested, its body
    is non-empty and matches the observed SHA, and every cited page is a real,
    positive, unique 1-based page.  Invalid or duplicate bridge rows are
    discarded rather than exposed to the LLM.
    """
    verified: list[VerifiedCitation] = []
    seen: set[uuid.UUID] = set()
    for raw in rows:
        row = tuple(raw)  # type: ignore[arg-type]
        if len(row) != 6:
            continue
        try:
            chunk_id = uuid.UUID(str(row[0]))
            document_id = uuid.UUID(str(row[1]))
            tenant_id = uuid.UUID(str(row[2]))
        except (TypeError, ValueError, AttributeError):
            continue
        if chunk_id not in requested_ids or chunk_id in seen:
            continue

        raw_pages = row[3]
        if not isinstance(raw_pages, (list, tuple)) or not raw_pages:
            continue
        if any(not isinstance(page, int) or isinstance(page, bool) or page <= 0 for page in raw_pages):
            continue
        pages = tuple(raw_pages)
        if len(set(pages)) != len(pages):
            continue

        try:
            body = bytes(row[5])
        except (TypeError, ValueError):
            continue
        if not body:
            continue
        expected = row[4]
        if isinstance(expected, bytes):
            try:
                expected = expected.decode("ascii")
            except UnicodeDecodeError:
                continue
        if not isinstance(expected, str):
            continue
        observed = hashlib.sha256(body).hexdigest()
        if expected.lower() != observed:
            continue

        seen.add(chunk_id)
        verified.append(
            VerifiedCitation(
                chunk_id=chunk_id,
                document_id=document_id,
                tenant_id=tenant_id,
                pages=pages,
                body_sha256=observed,
                body=body,
            )
        )
    return verified


async def verify_candidates(
    chunk_ids: list[uuid.UUID], tenant: Tenant
) -> list[VerifiedCitation]:
    """Re-verify candidate chunk IDs under the tenant's F0-I context."""
    if not chunk_ids:
        return []
    # The API uses the f1_api role (never the worker or migration role).  The
    # bridge derives the F0-I tenant from the session enterprise context.
    from .database import _api_dsn

    key = read_f0i_key()
    pg_dsn = _api_dsn().replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(pg_dsn) as conn:
        conn.execute(
            "SELECT set_config('f1.enterprise_id', %s, true)",
            (str(tenant.enterprise_id),),
        )
        conn.execute(
            "SELECT set_config('f1.sub', %s, true)",
            (tenant.sub,),
        )
        rows = conn.execute(
            "SELECT chunk_id, document_id, tenant_id, pages, body_sha256, body "
            "FROM f1.verify_citations(%s, %s, %s)",
            ([str(c) for c in chunk_ids], bytes(key), CIPHER_OPTIONS),
        ).fetchall()
    return _validated_rows(rows, set(chunk_ids))


__all__ = ("VerifiedCitation", "verify_candidates")
