"""Narrow PostgreSQL entry points for leased native extraction jobs."""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import session_scope
from .contracts import canonical_json
from .formats import CONTRACTS


def native_extraction_enabled() -> bool:
    # Default 0014 and material-RAG 0016 never query the 0034 tables.
    return (os.environ.get("F1_LOCAL_ENGINEERING") == "1"
            and os.environ.get("F1_NATIVE_EVIDENCE_LOCAL") == "1")


@dataclass(frozen=True, slots=True)
class NativeClaim:
    job_id: uuid.UUID
    lease_token: uuid.UUID
    revision_id: uuid.UUID
    enterprise_id: uuid.UUID
    knowledge_scope_id: uuid.UUID
    document_record_id: uuid.UUID
    document_version_id: uuid.UUID
    source_document_id: uuid.UUID
    upload_task_id: uuid.UUID
    source_sha256: str
    source_size: int
    source_etag: str = field(repr=False)
    object_key: str = field(repr=False)
    actor_sub: str = field(repr=False)
    attempt: int
    parser_version: str
    support_profile: str
    extraction_contract: int
    source_format: str = "docx"


def _claim(value: dict) -> NativeClaim:
    return NativeClaim(
        **{key: uuid.UUID(str(value[key])) for key in (
            "job_id", "lease_token", "revision_id", "enterprise_id",
            "knowledge_scope_id", "document_record_id", "document_version_id",
            "source_document_id", "upload_task_id")},
        **{key: value[key] for key in (
            "source_sha256", "source_size", "source_etag", "object_key", "actor_sub",
            "attempt", "parser_version", "support_profile", "extraction_contract", "source_format")},
    )


async def register_in_session(session: AsyncSession, version_id: uuid.UUID, source_format: str = "docx") -> dict:
    parser, profile = CONTRACTS[source_format]
    return (await session.execute(text(
        "SELECT f1.register_native_extraction_job(:version,:parser,:profile)"
    ), {"version": version_id, "parser": parser, "profile": profile})).scalar_one()


async def claim_due_jobs(*, limit: int = 20, lease_seconds: int = 300) -> tuple[dict, ...]:
    async with session_scope(role="f1_worker") as session:
        rows = (await session.execute(text(
            "SELECT * FROM f1.claim_native_extraction_jobs(:limit,:seconds)"
        ), {"limit": limit, "seconds": lease_seconds})).scalars().all()
        await session.commit()
    return tuple(rows)


async def read_claim(job_id: uuid.UUID, token: uuid.UUID) -> NativeClaim | None:
    async with session_scope(role="f1_worker") as session:
        result = (await session.execute(text(
            "SELECT f1.read_native_extraction_claim(:job,:token)"
        ), {"job": job_id, "token": token})).scalar_one()
        # The closed reader can durably block a now-invalid source/actor.
        await session.commit()
    return _claim(result) if result else None


async def renew_lease(job_id: uuid.UUID, token: uuid.UUID, *, seconds: int = 300) -> bool:
    async with session_scope(role="f1_worker") as session:
        result = (await session.execute(text(
            "SELECT f1.renew_native_extraction_lease(:job,:token,:seconds)"
        ), {"job": job_id, "token": token, "seconds": seconds})).scalar_one()
        await session.commit()
    return bool(result)


async def finish_failure(job_id: uuid.UUID, token: uuid.UUID, *, reason: str,
                         retry_seconds: int | None = None) -> bool:
    async with session_scope(role="f1_worker") as session:
        result = (await session.execute(text(
            "SELECT f1.finish_native_extraction_failure(:job,:token,:reason,:retry)"
        ), {"job": job_id, "token": token, "reason": reason, "retry": retry_seconds})).scalar_one()
        await session.commit()
    return bool(result)


async def finalize(claim: NativeClaim, payload: dict) -> uuid.UUID | None:
    async with session_scope(role="f1_worker") as session:
        result = (await session.execute(text(
            "SELECT f1.finalize_native_extraction(:job,:token,CAST(:payload AS jsonb))"
        ), {"job": claim.job_id, "token": claim.lease_token,
            "payload": canonical_json(payload).decode("ascii")})).scalar_one()
        await session.commit()
    return uuid.UUID(str(result)) if result else None
