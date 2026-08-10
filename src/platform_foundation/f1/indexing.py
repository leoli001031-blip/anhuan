"""F1 registered-fixture indexing (SHA gate -> RAGFlow per-enterprise dataset).

An upload is indexed only when its SHA-256 matches a registered F0-I fixture
for the enterprise's F0-I tenant; otherwise the task fails with the fixed
``FIXTURE_ONLY_UNREGISTERED`` reason (no OCR/canonicalization is ever run).
Registered chunks are read through the RLS-bypassing bridge function and
written into the enterprise's own RAGFlow dataset with opaque metadata.
"""
from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import text

from .database import session_scope
from .ragflow_provision import dataset_for_enterprise
from .secret_files import read_f0i_key
from .upload_task import UploadClaim, claim_upload_task, renew_upload_lease
CIPHER_OPTIONS = "cipher-algo=aes256"


class LeaseLost(RuntimeError):
    pass


class RagflowReconcileMismatch(RuntimeError):
    pass


async def process_upload(
    task_id: uuid.UUID,
    enterprise_id: uuid.UUID | None = None,
    *,
    lease_token: uuid.UUID | None = None,
) -> None:
    """Apply the registered-fixture gate; index registered chunks to RAGFlow."""
    if lease_token is None:
        claimed = await claim_upload_task(task_id)
        if claimed is None:
            return
        enterprise_id = claimed.enterprise_id
        lease_token = claimed.lease_token
    if enterprise_id is None:
        return
    async with session_scope(
        role="f1_worker",
        enterprise_id=enterprise_id,
        task_id=task_id,
        lease_token=lease_token,
    ) as session:
        task = (
            await session.execute(
                text(
                    "SELECT object_key, content_sha256, document_id "
                    "FROM f1.upload_task WHERE id = :id"
                ),
                {"id": task_id},
            )
        ).fetchone()
        enterprise = (
            await session.execute(
                text(
                    "SELECT f0i_enterprise_id FROM f1.enterprise WHERE id = :eid"
                ),
                {"eid": enterprise_id},
            )
        ).fetchone()
        if task is None or enterprise is None:
            return
        if enterprise is None or enterprise[0] is None:
            await finish_claim_ids(
                task_id, enterprise_id, lease_token,
                status="failed", reason="FIXTURE_ONLY_UNREGISTERED",
            )
            return
        scope = (
            await session.execute(
                text(
                    "SELECT document_scope_id, chunk_count "
                    "FROM f1.fixture_scope_for_sha(:sha)"
                ),
                {"sha": task[1]},
            )
        ).fetchone()
        if scope is None:
            await finish_claim_ids(
                task_id, enterprise_id, lease_token,
                status="failed", reason="FIXTURE_ONLY_UNREGISTERED",
            )
            return
        # Move to indexing; the RAGFlow write happens outside the DB txn.
        moved = (
            await session.execute(
                text(
                    "UPDATE f1.upload_task SET status = 'indexing', "
                    "error_reason = NULL, updated_at=statement_timestamp() "
                    "WHERE id = :id AND lease_token=:token "
                    "AND lease_until > statement_timestamp() RETURNING id"
                ),
                {"id": task_id, "token": lease_token},
            )
        ).fetchone()
        if moved is None:
            return
        await session.execute(
            text(
                "UPDATE f1.document SET status = 'indexing' WHERE id = :id"
            ),
            {"id": task[2]},
        )
        await session.commit()
        scope_id = scope[0]

    # Read chunks + write to RAGFlow (network/DB bridge outside the txn).
    try:
        await _require_lease(task_id, lease_token)
        chunks = _read_fixture_chunks(
            enterprise_id, task[1], task_id=task_id, lease_token=lease_token
        )
        await _require_lease(task_id, lease_token)
        dataset_id = dataset_for_enterprise(enterprise_id)
        await _require_lease(task_id, lease_token)
        _write_chunks(
            dataset_id,
            chunks,
            scope_id,
            lease_guard=lambda: _renew_lease_sync(task_id, lease_token),
        )
        outcome = "done"
        reason: str | None = None
    except LeaseLost:
        return
    except RagflowReconcileMismatch:
        outcome = "failed"
        reason = "RAGFLOW_RECONCILE_MISMATCH"
    except Exception:  # noqa: BLE001
        outcome = "retry"
        reason = "RAGFLOW_UNAVAILABLE"
    await finish_claim_ids(
        task_id, enterprise_id, lease_token, status=outcome, reason=reason
    )


def _read_fixture_chunks(
    enterprise_id: uuid.UUID,
    sha256: str,
    *,
    task_id: uuid.UUID,
    lease_token: uuid.UUID,
) -> list[dict]:
    """Read decrypted CHILD chunks for a registered SHA via the bridge.

    The worker role carries the enterprise context (derived from the claimed
    task); the bridge derives the F0-I tenant from it.
    """
    import psycopg

    from .database import _worker_dsn

    key = read_f0i_key()
    dsn = _worker_dsn().replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "SELECT set_config('f1.enterprise_id', %s, true)",
            (str(enterprise_id),),
        )
        conn.execute("SELECT set_config('f1.task_id', %s, true)", (str(task_id),))
        conn.execute(
            "SELECT set_config('f1.lease_token', %s, true)",
            (str(lease_token),),
        )
        rows = conn.execute(
            "SELECT chunk_id, parent_chunk_id, document_id, tenant_id, kind, "
            "char_count, pages, body "
            "FROM f1.fixture_chunks(%s, %s, %s)",
            (sha256, bytes(key), CIPHER_OPTIONS),
        ).fetchall()
    chunks: list[dict] = []
    for r in rows:
        body = bytes(r[7])
        chunks.append({
            "chunk_id": str(r[0]),
            "parent_chunk_id": str(r[1]) if r[1] else None,
            "document_id": str(r[2]),
            "tenant_id": str(r[3]),
            "kind": r[4],
            "char_count": int(r[5]),
            "pages": list(r[6] or []),
            "body": body,
            "body_sha256": hashlib.sha256(body).hexdigest(),
        })
    return chunks


def _write_chunks(
    dataset_id: str,
    chunks: list[dict],
    scope_id: uuid.UUID,
    *,
    lease_guard=None,
) -> int:
    """Reconcile canonical chunks; retries add only proven-missing identities."""
    from platform_foundation.f0j1.ragflow_client import RagFlowClient

    from .ragflow_provision import RAGFLOW_BASE, ragflow_token

    token = ragflow_token()
    client = RagFlowClient(base_url=RAGFLOW_BASE)
    from .ragflow_provision import ragflow_lock

    with ragflow_lock(f"scope-{scope_id.hex}"):
        _guard_lease(lease_guard)
        docs = client.list_documents(token, dataset_id)
        matching = [d for d in docs if d.get("name") == f"scope-{scope_id.hex}"]
        if len(matching) > 1:
            raise RagflowReconcileMismatch("RAGFLOW_RECONCILE_MISMATCH")
        if matching:
            doc_id = matching[0]["id"]
        else:
            _guard_lease(lease_guard)
            created = client.create_empty_document(
                token, dataset_id, f"scope-{scope_id.hex}"
            )
            doc_id = created.get("id")
        if not doc_id:
            raise RagflowReconcileMismatch("RAGFLOW_RECONCILE_MISMATCH")

        expected = {c["chunk_id"]: c for c in chunks if c["body"]}
        _guard_lease(lease_guard)
        existing = _load_remote_canonical(
            client, token, dataset_id, doc_id, lease_guard=lease_guard
        )
        written = 0
        for chunk_id, chunk in expected.items():
            prior = existing.get(chunk_id)
            if prior is not None:
                if prior != chunk["body_sha256"]:
                    raise RagflowReconcileMismatch("RAGFLOW_RECONCILE_MISMATCH")
                continue
            _guard_lease(lease_guard)
            try:
                content = chunk["body"].decode("utf-8", errors="strict")
            except UnicodeDecodeError as error:
                raise RagflowReconcileMismatch("RAGFLOW_RECONCILE_MISMATCH") from error
            tags = [
                f"chunk_id={chunk_id}",
                f"body_sha256={chunk['body_sha256']}",
                f"parent_chunk_id={chunk['parent_chunk_id']}",
                f"document_id={chunk['document_id']}",
                f"tenant_id={chunk['tenant_id']}",
                f"kind={chunk['kind']}",
                f"char_count={chunk['char_count']}",
                f"pages={','.join(str(p) for p in chunk['pages'])}",
            ]
            client.add_chunk(token, dataset_id, doc_id, content, tag_kwd=tags)
            written += 1

        _guard_lease(lease_guard)
        final = _load_remote_canonical(
            client, token, dataset_id, doc_id, lease_guard=lease_guard
        )
        if set(final) != set(expected):
            raise RagflowReconcileMismatch("RAGFLOW_RECONCILE_MISMATCH")
        for chunk_id, chunk in expected.items():
            if final[chunk_id] != chunk["body_sha256"]:
                raise RagflowReconcileMismatch("RAGFLOW_RECONCILE_MISMATCH")
        return written


def _tag_map(remote: dict) -> dict[str, str]:
    tags = remote.get("tag_kwd") or []
    if isinstance(tags, str):
        tags = [tags]
    result: dict[str, str] = {}
    for raw in tags:
        key, separator, value = str(raw).partition("=")
        if separator and key not in result:
            result[key] = value
    return result


def _canonical_remote_chunks(remote_chunks: list[dict]) -> dict[str, str]:
    canonical: dict[str, str] = {}
    for remote in remote_chunks:
        tags = _tag_map(remote)
        chunk_id = tags.get("chunk_id")
        if not chunk_id:
            continue
        body_sha = tags.get("body_sha256")
        if body_sha is None and isinstance(remote.get("content"), str):
            body_sha = hashlib.sha256(remote["content"].encode("utf-8")).hexdigest()
        if body_sha is None or chunk_id in canonical:
            raise RagflowReconcileMismatch("RAGFLOW_RECONCILE_MISMATCH")
        canonical[chunk_id] = body_sha
    return canonical


def _load_remote_canonical(
    client,
    token: str,
    dataset_id: str,
    document_id: str,
    *,
    lease_guard=None,
) -> dict[str, str]:
    """Load remote canonical identities, hydrating metadata only when needed."""
    _guard_lease(lease_guard)
    remote_chunks = client.list_chunks(token, dataset_id, document_id)
    hydrated: list[dict] = []
    for remote in remote_chunks:
        if remote.get("tag_kwd"):
            hydrated.append(remote)
            continue
        remote_id = remote.get("id") or remote.get("chunk_id")
        if not remote_id:
            hydrated.append(remote)
            continue
        _guard_lease(lease_guard)
        status, payload = client._request(  # existing F0-J1 detail contract
            "GET",
            f"/datasets/{dataset_id}/documents/{document_id}/chunks/{remote_id}",
            token,
        )
        if status != 200 or payload.get("code") != 0:
            raise RagflowReconcileMismatch("RAGFLOW_RECONCILE_MISMATCH")
        detail = payload.get("data") or {}
        merged = dict(remote)
        if isinstance(detail, dict):
            merged.update(detail)
        hydrated.append(merged)
    return _canonical_remote_chunks(hydrated)


def _guard_lease(guard) -> None:
    if guard is not None and not bool(guard()):
        raise LeaseLost("STALE_WORKER_REJECTED")


def _renew_lease_sync(task_id: uuid.UUID, lease_token: uuid.UUID) -> bool:
    import psycopg

    from .database import _worker_dsn

    dsn = _worker_dsn().replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn) as conn:
        return bool(
            conn.execute(
                "SELECT f1.renew_upload_lease(%s, %s, %s)",
                (str(task_id), str(lease_token), 300),
            ).fetchone()[0]
        )


async def _require_lease(task_id: uuid.UUID, lease_token: uuid.UUID) -> None:
    if not await renew_upload_lease(task_id, lease_token):
        raise LeaseLost("STALE_WORKER_REJECTED")


async def _set_worker_context(session, task_id: uuid.UUID, lease_token: uuid.UUID) -> None:
    await session.execute(
        text("SELECT set_config('f1.task_id', :id, true)"), {"id": str(task_id)}
    )
    await session.execute(
        text("SELECT set_config('f1.lease_token', :token, true)"),
        {"token": str(lease_token)},
    )


async def finish_claim(
    claim: UploadClaim,
    *,
    status: str,
    reason: str | None,
) -> bool:
    return await finish_claim_ids(
        claim.task_id, claim.enterprise_id, claim.lease_token,
        status=status, reason=reason,
    )


async def finish_claim_ids(
    task_id: uuid.UUID,
    enterprise_id: uuid.UUID,
    lease_token: uuid.UUID,
    *,
    status: str,
    reason: str | None,
) -> bool:
    # Compatibility for the historical positional order is intentionally not
    # accepted: every terminal transition must name its CAS owner explicitly.
    return await _finish(task_id, enterprise_id, lease_token, status, reason)


async def _finish(
    task_id: uuid.UUID,
    enterprise_id: uuid.UUID,
    lease_token: uuid.UUID,
    status: str,
    reason: str | None,
) -> bool:
    if not await renew_upload_lease(task_id, lease_token):
        return False
    async with session_scope(
        role="f1_worker",
        enterprise_id=enterprise_id,
        task_id=task_id,
        lease_token=lease_token,
    ) as session:
    # Order matters for the worker's RLS: the tenant policy requires an
    # in-flight task in the enterprise.  Update document + outbox while the
    # task is still 'indexing'; move the task to its terminal status last.
        await session.execute(
            text(
                "UPDATE f1.document SET status = :status "
                "WHERE id = (SELECT document_id FROM f1.upload_task "
                "WHERE id = :id AND lease_token=:token)"
            ),
            {
                "status": "pending" if status == "retry" else status,
                "id": task_id, "token": lease_token,
            },
        )
        if status == "done":
            await session.execute(
                text(
                    "UPDATE f1.outbox SET state = 'acked', "
                    "acked_at = statement_timestamp() WHERE task_id = :id "
                    "AND event_type IN ('upload.dispatched','upload.indexing')"
                ),
                {"id": task_id},
            )
        elif status == "retry":
            await session.execute(
                text(
                    "UPDATE f1.outbox SET state='dispatched', dispatch_token=NULL, "
                    "dispatch_lease_until=statement_timestamp()+interval '15 seconds' "
                    "WHERE task_id=:id AND event_type='upload.dispatched'"
                ),
                {"id": task_id},
            )
        else:
            await session.execute(
                text(
                    "UPDATE f1.outbox SET state='acked', "
                    "acked_at=statement_timestamp() WHERE task_id=:id "
                    "AND event_type IN ('upload.dispatched','upload.indexing')"
                ),
                {"id": task_id},
            )
            await session.execute(
                text(
                    "INSERT INTO f1.outbox "
                    "(id, enterprise_id, task_id, event_type, state, payload_sha256, rq_job_id) "
                    "SELECT :id, :eid, :task, 'upload.failed', 'acked', content_sha256, "
                    "'f1-failed-' || id::text FROM f1.upload_task WHERE id = :task "
                    "ON CONFLICT (task_id, event_type) DO NOTHING"
                ),
                {"id": uuid.uuid4(), "eid": enterprise_id, "task": task_id},
            )
        await session.execute(
            text(
                "INSERT INTO f1.audit_log "
                "(id, enterprise_id, user_sub, action, resource_type, resource_id, result) "
                "VALUES (:aid, :eid, 'f1_worker', 'document.index', 'upload_task', "
                ":rid, :result)"
            ),
            {
                "aid": uuid.uuid4(), "eid": enterprise_id, "rid": str(task_id),
                "result": "retry" if status == "retry" else status,
            },
        )
        terminal = (
            await session.execute(
                text(
                    "UPDATE f1.upload_task SET status=:status, error_reason=:reason, "
                    "next_attempt_at=CASE WHEN :retry THEN "
                    "statement_timestamp()+interval '15 seconds' ELSE NULL END, "
                    "lease_token=NULL, lease_owner=NULL, lease_acquired_at=NULL, "
                    "lease_until=NULL, updated_at=statement_timestamp() "
                    "WHERE id=:id AND lease_token=:token RETURNING id"
                ),
                {
                    "status": "pending" if status == "retry" else status,
                    "reason": reason, "retry": status == "retry",
                    "id": task_id, "token": lease_token,
                },
            )
        ).fetchone()
        if terminal is None:
            await session.rollback()
            return False
        await session.commit()
        return True


__all__ = (
    "process_upload", "finish_claim", "finish_claim_ids", "LeaseLost",
    "RagflowReconcileMismatch",
)
