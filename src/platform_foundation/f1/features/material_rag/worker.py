"""Closed-manifest durable indexing worker for the isolated verifier.

This round's external-processing authority is intentionally encoded as a
closed SHA allowlist for four Demo sources and two fixed canaries.  A future
production worker needs separate approval;
there is no environment switch that broadens this list.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable, Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from platform_foundation.f0j1.ragflow_client import RagFlowProbeError

from ...database import _worker_dsn, session_scope
from ...ragflow_provision import RagflowProvisionError, dataset_for_material_scope
from .contracts import (
    CanonicalUnit,
    DemoUnitManifestProof,
    MaterialRagIntegrityError,
    MaterialRagJobClaim,
    MaterialRagLeaseLost,
)
from .ragflow_adapter import (
    delete_empty_scope_dataset,
    delete_version,
    reconcile_version,
)
from .repository import (
    claim_job,
    ensure_dataset_binding_intent,
    finalize_empty_scope_dataset_delete,
    finish_job,
    load_dataset_binding,
    load_dataset_binding_state,
    load_units_for_version,
    live_scope_job_lock,
    live_source_mutation_fence,
    persist_canonical_units,
    persist_dataset_binding,
    prepare_empty_scope_dataset_delete,
)
from .security import (
    AUTHORIZED_MATERIAL_RAG_SOURCE_SHA256,
    verify_demo_unit_manifest_proof,
)


@asynccontextmanager
async def claimed_session(
    claim: MaterialRagJobClaim,
) -> AsyncIterator[AsyncSession]:
    """Open a transaction whose RLS scope is one live material-job lease."""
    async with session_scope(role="f1_worker") as session:
        await session.execute(
            text("SELECT set_config('f1.material_rag_job_id',:id,true)"),
            {"id": str(claim.id)},
        )
        await session.execute(
            text("SELECT set_config('f1.material_rag_lease_token',:token,true)"),
            {"token": str(claim.lease_token)},
        )
        yield session


def _renew_sync(claim: MaterialRagJobClaim, lease_seconds: int = 300) -> bool:
    import psycopg

    dsn = _worker_dsn().replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn) as connection:
        renewed = bool(
            connection.execute(
                "SELECT f1.renew_material_rag_job_lease(%s,%s,%s)",
                (str(claim.id), str(claim.lease_token), lease_seconds),
            ).fetchone()[0]
        )
        connection.commit()
        return renewed


def _released_sync(claim: MaterialRagJobClaim) -> bool:
    """Re-prove current clean/released state under this exact live job lease."""
    import psycopg

    dsn = _worker_dsn().replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(dsn) as connection:
        connection.execute(
            "SELECT set_config('f1.material_rag_job_id',%s,true),"
            "set_config('f1.material_rag_lease_token',%s,true)",
            (str(claim.id), str(claim.lease_token)),
        )
        released = bool(
            connection.execute(
                "SELECT f1.material_rag_released_version(%s,%s,%s,%s,%s)",
                (
                    str(claim.enterprise_id),
                    str(claim.knowledge_scope_id),
                    str(claim.document_record_id),
                    str(claim.document_version_id),
                    claim.source_sha256,
                ),
            ).fetchone()[0]
        )
        connection.commit()
        return released


def _dataset_for_scope_fenced_sync(claim: MaterialRagJobClaim) -> str:
    """Provision/reuse a dataset while the exact P3 source remains locked."""
    with live_source_mutation_fence(claim):
        return dataset_for_material_scope(claim.knowledge_scope_id)


def _compensate_unbound_scope_dataset_sync(claim: MaterialRagJobClaim) -> int:
    """Delete an empty deterministic dataset left before binding commit."""
    from platform_foundation.f0j1.ragflow_client import RagFlowClient
    from ...config import ragflow_base_url
    from ...ragflow_provision import ragflow_token

    expected_name = f"f1-material-{claim.knowledge_scope_id.hex}"
    client = RagFlowClient(base_url=ragflow_base_url())
    token = ragflow_token()
    # The caller already holds the cross-process scope advisory lock.
    matches = [
        dataset
        for dataset in client.list_all_datasets(token)
        if dataset.get("name") == expected_name
    ]
    if len(matches) > 1:
        raise MaterialRagIntegrityError(
            "MATERIAL_RAG_REMOTE_DATASET_IDENTITY_INVALID"
        )
    if not matches:
        return 0
    dataset_id = str(matches[0].get("id") or "")
    if (
        len(dataset_id) != 32
        or any(character not in "0123456789abcdef" for character in dataset_id)
        or client.list_all_documents(token, dataset_id)
    ):
        raise MaterialRagIntegrityError("MATERIAL_RAG_REMOTE_DATASET_NOT_EMPTY")
    if not _renew_sync(claim):
        raise MaterialRagLeaseLost("MATERIAL_RAG_LEASE_LOST")
    if client.delete_datasets(token, [dataset_id]) != 1:
        raise MaterialRagIntegrityError(
            "MATERIAL_RAG_REMOTE_DATASET_DELETE_MISMATCH"
        )
    if any(
        dataset.get("id") == dataset_id or dataset.get("name") == expected_name
        for dataset in client.list_all_datasets(token)
    ):
        raise MaterialRagIntegrityError(
            "MATERIAL_RAG_REMOTE_DATASET_DELETE_MISMATCH"
        )
    return 1


def _validate_units(
    claim: MaterialRagJobClaim, units: tuple[CanonicalUnit, ...]
) -> None:
    if claim.source_sha256 not in AUTHORIZED_MATERIAL_RAG_SOURCE_SHA256:
        raise MaterialRagIntegrityError("MATERIAL_RAG_SOURCE_NOT_AUTHORIZED")
    if any(
        unit.enterprise_id != claim.enterprise_id
        or unit.knowledge_scope_id != claim.knowledge_scope_id
        or unit.document_record_id != claim.document_record_id
        or unit.document_version_id != claim.document_version_id
        or unit.source_sha256 != claim.source_sha256
        for unit in units
    ):
        raise MaterialRagIntegrityError("MATERIAL_RAG_UNIT_JOB_MISMATCH")


def _manifest(claim: MaterialRagJobClaim, units: tuple[CanonicalUnit, ...]) -> str:
    body = json.dumps(
        {
            "document_version_id": str(claim.document_version_id),
            "source_sha256": claim.source_sha256,
            "units": [
                [str(unit.id), unit.page_number, unit.ordinal, unit.body_sha256]
                for unit in units
            ],
            "version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(body).hexdigest()


def _unit_fingerprint(unit: CanonicalUnit) -> tuple[object, ...]:
    return (
        unit.id,
        unit.enterprise_id,
        unit.knowledge_scope_id,
        unit.document_record_id,
        unit.document_version_id,
        unit.source_sha256,
        unit.page_number,
        unit.ordinal,
        unit.parser_version,
        unit.body_sha256,
        unit.ocr_applied,
        unit.table_candidate,
        unit.two_column_candidate,
    )


async def claim_demo_job(
    job_id: uuid.UUID, *, worker_id: str
) -> MaterialRagJobClaim | None:
    """Acquire the attempt before the verifier signs its short-lived proof."""
    return await claim_job(job_id, worker_id=worker_id)


async def _process_claimed_demo_job_locked(
    claim: MaterialRagJobClaim,
    *,
    units: Iterable[CanonicalUnit] | None = None,
    manifest_proof: DemoUnitManifestProof | None = None,
) -> bool:
    """Process one already-claimed job after proof creation for this lease."""
    if not isinstance(claim, MaterialRagJobClaim):
        raise ValueError("MATERIAL_RAG_JOB_CLAIM_INVALID")
    supplied = tuple(units) if units is not None else ()
    try:
        if claim.source_sha256 not in AUTHORIZED_MATERIAL_RAG_SOURCE_SHA256:
            raise MaterialRagIntegrityError("MATERIAL_RAG_SOURCE_NOT_AUTHORIZED")
        if claim.action == "delete":
            if supplied or manifest_proof is not None:
                raise MaterialRagIntegrityError("MATERIAL_RAG_DELETE_UNITS_FORBIDDEN")
        else:
            if not supplied or manifest_proof is None:
                raise MaterialRagIntegrityError("MATERIAL_RAG_MANIFEST_REQUIRED")
            supplied = verify_demo_unit_manifest_proof(
                claim, supplied, manifest_proof
            )
            _validate_units(claim, supplied)
            # Hold the exact released P3 source rows across persistence.  A
            # concurrent revoke therefore serializes before or after the
            # canonical-unit write instead of racing a detached bool check.
            with live_source_mutation_fence(claim):
                async with claimed_session(claim) as session:
                    await persist_canonical_units(session, supplied)
                    await session.commit()

        async with claimed_session(claim) as session:
            stored = await load_units_for_version(
                session,
                enterprise_id=claim.enterprise_id,
                knowledge_scope_id=claim.knowledge_scope_id,
                document_version_id=claim.document_version_id,
            )
            binding_state = await load_dataset_binding_state(
                session,
                enterprise_id=claim.enterprise_id,
                knowledge_scope_id=claim.knowledge_scope_id,
            )
            binding = binding_state.binding if binding_state is not None else None
        if claim.action in {"index", "rebuild"} and not stored:
            raise MaterialRagIntegrityError("MATERIAL_RAG_UNITS_MISSING")
        _validate_units(claim, stored)
        if claim.action in {"index", "rebuild"} and tuple(
            _unit_fingerprint(unit) for unit in stored
        ) != tuple(_unit_fingerprint(unit) for unit in supplied):
            raise MaterialRagIntegrityError("MATERIAL_RAG_STORED_MANIFEST_MISMATCH")

        if (
            (binding_state is None or binding_state.status == "deleted")
            and claim.action != "delete"
        ):
            async with claimed_session(claim) as session:
                intent_status = await ensure_dataset_binding_intent(
                    session,
                    enterprise_id=claim.enterprise_id,
                    knowledge_scope_id=claim.knowledge_scope_id,
                )
                await session.commit()
            if intent_status == "ready":
                async with claimed_session(claim) as session:
                    binding = await load_dataset_binding(
                        session,
                        enterprise_id=claim.enterprise_id,
                        knowledge_scope_id=claim.knowledge_scope_id,
                    )
            if binding is not None:
                pass
            else:
                dataset_ref = await asyncio.to_thread(
                    _dataset_for_scope_fenced_sync,
                    claim,
                )
                if not await asyncio.to_thread(_renew_sync, claim):
                    raise MaterialRagLeaseLost("MATERIAL_RAG_LEASE_LOST")
                async with claimed_session(claim) as session:
                    binding = await persist_dataset_binding(
                        session,
                        enterprise_id=claim.enterprise_id,
                        knowledge_scope_id=claim.knowledge_scope_id,
                        dataset_ref=dataset_ref,
                    )
                    await session.commit()
        elif binding_state is not None and claim.action in {"index", "rebuild"}:
            if binding_state.status == "deleting":
                raise RagflowProvisionError("MATERIAL_RAG_BINDING_DELETING")
            if binding_state.status == "provisioning":
                dataset_ref = await asyncio.to_thread(
                    _dataset_for_scope_fenced_sync,
                    claim,
                )
                if not await asyncio.to_thread(_renew_sync, claim):
                    raise MaterialRagLeaseLost("MATERIAL_RAG_LEASE_LOST")
                async with claimed_session(claim) as session:
                    binding = await persist_dataset_binding(
                        session,
                        enterprise_id=claim.enterprise_id,
                        knowledge_scope_id=claim.knowledge_scope_id,
                        dataset_ref=dataset_ref,
                    )
                    await session.commit()
            elif binding_state.status != "ready":
                raise MaterialRagIntegrityError("MATERIAL_RAG_DATASET_BINDING_INVALID")

        lease_guard = lambda: _renew_sync(claim)
        mutation_fence = lambda: live_source_mutation_fence(claim)
        if claim.action == "delete":
            if binding_state is not None and binding_state.status == "provisioning":
                await asyncio.to_thread(_compensate_unbound_scope_dataset_sync, claim)
                async with claimed_session(claim) as session:
                    await session.execute(
                        text(
                            "DELETE FROM f1.material_rag_scope_binding "
                            "WHERE enterprise_id=:enterprise_id "
                            "AND knowledge_scope_id=:scope_id "
                            "AND backend='ragflow' AND status='provisioning'"
                        ),
                        {
                            "enterprise_id": claim.enterprise_id,
                            "scope_id": claim.knowledge_scope_id,
                        },
                    )
                    await session.commit()
                binding_state = None
            if binding is not None and binding.status == "ready":
                await asyncio.to_thread(
                    delete_version,
                    dataset_id=binding.dataset_ref,
                    knowledge_scope_id=claim.knowledge_scope_id,
                    document_version_id=claim.document_version_id,
                    source_sha256=claim.source_sha256,
                    lease_guard=lease_guard,
                )
            elif binding is not None and binding.status != "deleting":
                raise MaterialRagIntegrityError(
                    "MATERIAL_RAG_DATASET_BINDING_INVALID"
                )
            async with claimed_session(claim) as session:
                await session.execute(
                    text(
                        "DELETE FROM f1.material_rag_unit "
                        "WHERE enterprise_id=:enterprise_id "
                        "AND knowledge_scope_id=:scope_id "
                        "AND document_record_id=:record_id "
                        "AND document_version_id=:version_id "
                        "AND source_sha256=:source_sha"
                    ),
                    {
                        "enterprise_id": claim.enterprise_id,
                        "scope_id": claim.knowledge_scope_id,
                        "record_id": claim.document_record_id,
                        "version_id": claim.document_version_id,
                        "source_sha": claim.source_sha256,
                    },
                )
                await session.commit()
            if binding is not None:
                dataset_ref_sha256 = hashlib.sha256(
                    binding.dataset_ref.encode("utf-8")
                ).hexdigest()
                async with claimed_session(claim) as session:
                    delete_dataset = await prepare_empty_scope_dataset_delete(
                        session,
                        claim=claim,
                        dataset_ref_sha256=dataset_ref_sha256,
                    )
                    await session.commit()
                if delete_dataset:
                    await asyncio.to_thread(
                        delete_empty_scope_dataset,
                        dataset_id=binding.dataset_ref,
                        knowledge_scope_id=claim.knowledge_scope_id,
                        lease_guard=lease_guard,
                    )
                    if not await asyncio.to_thread(_renew_sync, claim):
                        raise MaterialRagLeaseLost("MATERIAL_RAG_LEASE_LOST")
                    async with claimed_session(claim) as session:
                        finalized = await finalize_empty_scope_dataset_delete(
                            session,
                            claim=claim,
                            dataset_ref_sha256=dataset_ref_sha256,
                        )
                        await session.commit()
                    if not finalized:
                        raise MaterialRagIntegrityError(
                            "MATERIAL_RAG_DATASET_FINALIZE_FAILED"
                        )
            final_units: tuple[CanonicalUnit, ...] = ()
        else:
            if binding is None:
                raise MaterialRagIntegrityError("MATERIAL_RAG_BINDING_MISSING")
            await asyncio.to_thread(
                reconcile_version,
                dataset_id=binding.dataset_ref,
                knowledge_scope_id=claim.knowledge_scope_id,
                document_version_id=claim.document_version_id,
                units=stored,
                rebuild=claim.action == "rebuild",
                lease_guard=lease_guard,
                mutation_fence=mutation_fence,
            )
            final_units = stored
            if not await asyncio.to_thread(_released_sync, claim):
                raise MaterialRagIntegrityError("MATERIAL_VERSION_NOT_INDEXABLE")
        if not await finish_job(
            claim,
            status="done",
            result_manifest_sha256=_manifest(claim, final_units),
            indexed_unit_count=len(final_units),
        ):
            raise MaterialRagLeaseLost("MATERIAL_RAG_LEASE_LOST")
        return True
    except MaterialRagLeaseLost:
        return False


async def process_claimed_demo_job(
    claim: MaterialRagJobClaim,
    *,
    units: Iterable[CanonicalUnit] | None = None,
    manifest_proof: DemoUnitManifestProof | None = None,
) -> bool:
    """Run one claimed job under a cross-process scope serialization lock."""
    if not isinstance(claim, MaterialRagJobClaim):
        raise ValueError("MATERIAL_RAG_JOB_CLAIM_INVALID")
    try:
        with live_scope_job_lock(claim):
            return await _process_claimed_demo_job_locked(
                claim,
                units=units,
                manifest_proof=manifest_proof,
            )
    except MaterialRagLeaseLost:
        return False
    except (RagFlowProbeError, RagflowProvisionError, ConnectionError, OSError):
        try:
            await finish_job(
                claim,
                status="retry_wait",
                reason="MATERIAL_RAG_UNAVAILABLE",
                retry_seconds=min(300, 15 * max(1, claim.attempt)),
            )
        except Exception:  # Lease loss during failure recording is final here.
            pass
        return False
    except MaterialRagIntegrityError as error:
        reason = str(error)
        if not reason or len(reason) > 80:
            reason = "MATERIAL_RAG_INTEGRITY_FAILED"
        try:
            await finish_job(claim, status="failed", reason=reason)
        except Exception:
            pass
        return False
    except (RuntimeError, ValueError):
        try:
            await finish_job(
                claim, status="failed", reason="MATERIAL_RAG_LOCAL_FAILED"
            )
        except Exception:
            pass
        return False


async def process_demo_job(
    job_id: uuid.UUID,
    *,
    worker_id: str,
    prepare: Callable[
        [MaterialRagJobClaim],
        tuple[Iterable[CanonicalUnit], DemoUnitManifestProof],
    ]
    | None = None,
) -> bool:
    """Claim then let the isolated verifier attest this exact attempt.

    ``prepare`` is verifier-only and is invoked only after the lease token and
    attempt are known.  Product APIs must never receive the manifest key or
    call this worker entry point.
    """
    claim = await claim_demo_job(job_id, worker_id=worker_id)
    if claim is None:
        return False
    if claim.action == "delete":
        return await process_claimed_demo_job(claim)
    if prepare is None:
        return await process_claimed_demo_job(claim)
    try:
        units, proof = prepare(claim)
    except (MaterialRagIntegrityError, RuntimeError, ValueError):
        try:
            await finish_job(
                claim,
                status="failed",
                reason="MATERIAL_RAG_MANIFEST_INVALID",
            )
        except Exception:
            pass
        return False
    return await process_claimed_demo_job(
        claim, units=units, manifest_proof=proof
    )


__all__ = (
    "AUTHORIZED_MATERIAL_RAG_SOURCE_SHA256",
    "claim_demo_job",
    "claimed_session",
    "process_claimed_demo_job",
    "process_demo_job",
)
