"""Local-only due-queue orchestrator for the dedicated material-RAG gate.

Enabled exclusively when ``F1_MATERIAL_RAG_ORCHESTRATION_LOCAL=1`` and
``F1_LOCAL_ENGINEERING=1``.  Default API, default compose, and default
migrate stay closed.  Public ``run_once`` accepts only a worker id and
lease; it never takes a manifest key, arbitrary body, or physical ids.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass

from .contracts import MaterialRagJobClaim
from .repository import claim_next_job, finish_job
from .security import (
    CLIENT_B_ISOLATION_CANARY_TEXT,
    PROVIDER_POLICY_CANARY_TEXT,
    canonical_unit,
    create_synthetic_unit_manifest_proof,
)
from .worker import process_claimed_demo_job


ORCH_FLAG = "F1_MATERIAL_RAG_ORCHESTRATION_LOCAL"
ENGINEERING_FLAG = "F1_LOCAL_ENGINEERING"
HOLD_AFTER_CLAIM_MS_FLAG = "F1_MATERIAL_RAG_WORKER_HOLD_AFTER_CLAIM_MS"
PARSER_VERSION = "pgint1"
_CANARY_BODY = {
    hashlib.sha256(PROVIDER_POLICY_CANARY_TEXT.encode("utf-8")).hexdigest():
        PROVIDER_POLICY_CANARY_TEXT,
    hashlib.sha256(CLIENT_B_ISOLATION_CANARY_TEXT.encode("utf-8")).hexdigest():
        CLIENT_B_ISOLATION_CANARY_TEXT,
}


@dataclass(frozen=True, slots=True)
class OrchestratorOutcome:
    kind: str


def orchestration_enabled() -> bool:
    return os.environ.get(ORCH_FLAG) == "1" and os.environ.get(ENGINEERING_FLAG) == "1"


async def _process_fenced_claim(claim: MaterialRagJobClaim):
    if claim.action == "delete":
        return await process_claimed_demo_job(claim)
    text = _CANARY_BODY.get(claim.source_sha256)
    if text is None:
        finished = await finish_job(
            claim, status="failed", reason="MATERIAL_RAG_ORCH_SOURCE_UNSUPPORTED"
        )
        return OrchestratorOutcome(kind="FINISH_TRUE" if finished else "FINISH_FALSE")
    unit = canonical_unit(
        enterprise_id=claim.enterprise_id,
        knowledge_scope_id=claim.knowledge_scope_id,
        document_record_id=claim.document_record_id,
        document_version_id=claim.document_version_id,
        source_sha256=claim.source_sha256,
        page_number=1,
        ordinal=1,
        parser_version=PARSER_VERSION,
        text=text,
    )
    proof = create_synthetic_unit_manifest_proof(claim=claim, units=(unit,))
    return await process_claimed_demo_job(
        claim, units=(unit,), manifest_proof=proof
    )


async def _hold_after_claim() -> None:
    raw = os.environ.get(HOLD_AFTER_CLAIM_MS_FLAG, "")
    if raw == "":
        return
    if not raw.isdigit():
        raise RuntimeError("MATERIAL_RAG_WORKER_HOLD_INVALID")
    value = int(raw)
    if value < 1 or value > 5000:
        raise RuntimeError("MATERIAL_RAG_WORKER_HOLD_INVALID")
    await asyncio.sleep(value / 1000.0)


async def run_once(*, worker_id: str, lease_seconds: int = 30):
    if not orchestration_enabled():
        return OrchestratorOutcome(kind="DISABLED")
    claim = await claim_next_job(worker_id=worker_id, lease_seconds=lease_seconds)
    if claim is None:
        return OrchestratorOutcome(kind="EMPTY")
    await _hold_after_claim()
    return await _process_fenced_claim(claim)


__all__ = (
    "ENGINEERING_FLAG",
    "ORCH_FLAG",
    "OrchestratorOutcome",
    "orchestration_enabled",
    "run_once",
)
