"""RQ worker for lease-fenced, evidence-driven report generation."""
from __future__ import annotations

import asyncio
import os
import re
import socket
import uuid

from cryptography.exceptions import InvalidTag
from redis import Redis
from rq import Queue, Worker, get_current_job

from ...database import session_scope
from . import repository
from .contracts import (
    ENGINEERING_FLAG,
    LOCAL_FLAG,
    EligibleSource,
    FrozenSourceSet,
    GenerationFailed,
    TEMPLATE_ID,
)
from .generator import EvidenceDrivenReportGenerator
from .llm_generator import LlmReportGenerator, llm_generation_enabled
from . import delivery_repository
from .queue import QUEUE_NAME, REDIS_URL, mark_current_dispatch_failure


LEASE_SECONDS = 300
_OWNER_CHARS = re.compile(r"[^A-Za-z0-9_.:-]+")


def _generation_enabled() -> bool:
    return os.environ.get(LOCAL_FLAG) == "1" and os.environ.get(ENGINEERING_FLAG) == "1"


def _provider_sub(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 255
        or value.strip() != value
        or not value.isprintable()
    ):
        raise ValueError("REPORT_PROVIDER_SUB_INVALID")
    return value


def _lease_owner() -> str:
    host = _OWNER_CHARS.sub("-", socket.gethostname()).strip("-") or "unknown"
    return f"analysis-report.{host[:96]}"


def _freeze_claimed(
    enterprise_id: uuid.UUID,
    client_account_id: uuid.UUID,
    sources: list[EligibleSource],
) -> FrozenSourceSet:
    kinds = {getattr(item, "scope_kind", None) for item in sources}
    if "service_provider" not in kinds or "client" not in kinds:
        raise GenerationFailed("REPORT_SOURCE_SET_INVALID")
    fingerprint = repository.fingerprint_for(
        enterprise_id,
        client_account_id,
        sources,
    )
    return FrozenSourceSet(
        enterprise_id=enterprise_id,
        client_account_id=client_account_id,
        template_id=TEMPLATE_ID,
        fingerprint_sha256=fingerprint,
        sources=tuple(
            sorted(sources, key=lambda item: str(item.document_version_id))
        ),
    )


async def _claim(
    job_id: uuid.UUID,
    enterprise_id: uuid.UUID,
    provider_sub: str,
    version_id: uuid.UUID,
    delivery_id: uuid.UUID,
    dispatch_token: uuid.UUID,
    lease_token: uuid.UUID,
) -> dict[str, object] | None:
    async with session_scope(
        role="f1_api", enterprise_id=enterprise_id, sub=provider_sub
    ) as session:
        if await repository.actor_user_id(session, enterprise_id, provider_sub) is None:
            # Persist a fixed, audited terminal state through the narrow DB
            # boundary.  This makes the job visible and replaceable by another
            # active provider admin without relying on the original browser.
            # Use a separate unscoped capability session: an ordinary tenant
            # request must not be able to invoke this cross-RLS reconciler.
            async with session_scope(role="f1_api") as revocation_session:
                failed = await repository.fail_revoked_actor_generation(
                    revocation_session,
                    enterprise_id=enterprise_id,
                    job_id=job_id,
                    provider_sub=provider_sub,
                )
                await revocation_session.commit()
            if failed:
                mark_current_dispatch_failure("REPORT_ACTOR_REVOKED")
                raise RuntimeError("REPORT_ACTOR_REVOKED")
            # Authority may have been restored between the two reads, or the
            # exact current/lease fence may have changed.  Retry without lying
            # about the terminal reason.
            raise RuntimeError("REPORT_JOB_NOT_CLAIMABLE")
        claimed = await repository.claim_generation_job(
            session,
            enterprise_id=enterprise_id,
            job_id=job_id,
            version_id=version_id,
            delivery_id=delivery_id,
            dispatch_token=dispatch_token,
            lease_token=lease_token,
            lease_owner=_lease_owner(),
            lease_seconds=LEASE_SECONDS,
        )
        if claimed is None:
            current = await repository.get_job(session, enterprise_id, job_id)
            if current is not None and current["status"] in {"queued", "generating"}:
                # A retry may arrive before a dead worker's lease expires.
                # Raising keeps RQ Retry alive until that lease is reclaimable.
                raise RuntimeError("REPORT_JOB_NOT_CLAIMABLE")
            return None
        await session.commit()
        return dict(claimed)


async def _fail_claim(
    *,
    job_id: uuid.UUID,
    enterprise_id: uuid.UUID,
    provider_sub: str,
    version_id: uuid.UUID,
    lease_token: uuid.UUID,
    reason: str,
) -> bool:
    async with session_scope(
        role="f1_api", enterprise_id=enterprise_id, sub=provider_sub
    ) as session:
        failed = await repository.fail_claimed_generation(
            session,
            enterprise_id=enterprise_id,
            job_id=job_id,
            version_id=version_id,
            lease_token=lease_token,
            reason=reason,
        )
        await session.commit()
        return failed


async def _release_claim(
    *,
    job_id: uuid.UUID,
    enterprise_id: uuid.UUID,
    provider_sub: str,
    version_id: uuid.UUID,
    lease_token: uuid.UUID,
) -> None:
    async with session_scope(
        role="f1_api", enterprise_id=enterprise_id, sub=provider_sub
    ) as session:
        await repository.release_generation_claim(
            session,
            enterprise_id=enterprise_id,
            job_id=job_id,
            version_id=version_id,
            lease_token=lease_token,
        )
        await session.commit()


def _rq_retry_available() -> bool:
    current = get_current_job()
    return bool(current is not None and current.should_retry)


async def _process_generation_job(
    job_id: uuid.UUID,
    enterprise_id: uuid.UUID,
    provider_sub: str,
    version_id: uuid.UUID,
    delivery_id: uuid.UUID,
    dispatch_token: uuid.UUID,
) -> None:
    """Claim once, recompute the frozen fingerprint, then persist under lease."""
    actor_sub = _provider_sub(provider_sub)
    lease_token = uuid.uuid4()
    claimed = await _claim(
        job_id,
        enterprise_id,
        actor_sub,
        version_id,
        delivery_id,
        dispatch_token,
        lease_token,
    )
    if claimed is None:
        return
    if uuid.UUID(str(claimed["version_id"])) != version_id:
        raise RuntimeError("REPORT_DELIVERY_IDENTITY_MISMATCH")
    client_account_id = uuid.UUID(str(claimed["client_account_id"]))
    try:
        if not _generation_enabled():
            raise GenerationFailed("REPORT_WORKER_GENERATION_DISABLED")
        async with session_scope(
            role="f1_api", enterprise_id=enterprise_id, sub=actor_sub
        ) as session:
            sources = await repository.load_eligible_sources(
                session,
                enterprise_id,
                client_account_id,
            )
            frozen = _freeze_claimed(enterprise_id, client_account_id, sources)
            if frozen.fingerprint_sha256 != claimed["source_fingerprint_sha256"]:
                raise GenerationFailed("REPORT_SOURCE_FINGERPRINT_CHANGED")
            if llm_generation_enabled():
                generated = LlmReportGenerator().generate(frozen)
            else:
                generated = EvidenceDrivenReportGenerator().generate(frozen)
            written = await repository.persist_generated(
                session,
                enterprise_id=enterprise_id,
                job_id=job_id,
                version_id=version_id,
                lease_token=lease_token,
                generated=generated,
            )
            if not written:
                raise GenerationFailed("REPORT_LEASE_STALE")
            await session.commit()
    except GenerationFailed as exc:
        if not await _fail_claim(
            job_id=job_id,
            enterprise_id=enterprise_id,
            provider_sub=actor_sub,
            version_id=version_id,
            lease_token=lease_token,
            reason=exc.reason,
        ):
            raise RuntimeError("REPORT_LEASE_STALE") from None
    except (ValueError, InvalidTag):
        if not await _fail_claim(
            job_id=job_id,
            enterprise_id=enterprise_id,
            provider_sub=actor_sub,
            version_id=version_id,
            lease_token=lease_token,
            reason="REPORT_SOURCE_EVIDENCE_INVALID",
        ):
            raise RuntimeError("REPORT_LEASE_STALE") from None
    except Exception:
        if _rq_retry_available():
            await _release_claim(
                job_id=job_id,
                enterprise_id=enterprise_id,
                provider_sub=actor_sub,
                version_id=version_id,
                lease_token=lease_token,
            )
            raise
        if not await _fail_claim(
            job_id=job_id,
            enterprise_id=enterprise_id,
            provider_sub=actor_sub,
            version_id=version_id,
            lease_token=lease_token,
            reason="REPORT_GENERATION_RETRIES_EXHAUSTED",
        ):
            raise RuntimeError("REPORT_LEASE_STALE") from None
        mark_current_dispatch_failure("REPORT_GENERATION_RETRIES_EXHAUSTED")
        raise


async def _finish_generation_delivery(
    claim: delivery_repository.ReportGenerationDeliveryClaim,
) -> None:
    if claim.job_status == "draft":
        await delivery_repository.finish_delivery(
            claim.id,
            claim.dispatch_token,
            outcome="done",
        )
        return
    if claim.job_status == "failed" and claim.error_reason is not None:
        await delivery_repository.finish_delivery(
            claim.id,
            claim.dispatch_token,
            outcome="blocked",
            reason_code=claim.error_reason,
        )
        return
    await delivery_repository.finish_delivery(
        claim.id,
        claim.dispatch_token,
        outcome="retry",
        reason_code="REPORT_GENERATION_INCOMPLETE",
        retry_seconds=5,
    )


async def _process_generation_delivery(
    delivery_id: uuid.UUID, dispatch_token: uuid.UUID
) -> None:
    claim = await delivery_repository.read_delivery_claim(
        delivery_id, dispatch_token
    )
    if claim is None:
        return
    if claim.job_status in {"draft", "failed"}:
        await _finish_generation_delivery(claim)
        return
    try:
        await _process_generation_job(
            claim.job_id,
            claim.enterprise_id,
            claim.actor_sub,
            claim.version_id,
            claim.id,
            claim.dispatch_token,
        )
    except Exception:
        current = await delivery_repository.read_delivery_claim(
            delivery_id, dispatch_token
        )
        if (
            current is not None
            and current.job_status == "failed"
            and current.error_reason is not None
        ):
            await _finish_generation_delivery(current)
        raise
    current = await delivery_repository.read_delivery_claim(
        delivery_id, dispatch_token
    )
    if current is not None:
        await _finish_generation_delivery(current)


def run_generation_job(delivery_id: str, dispatch_token: str) -> None:
    """RQ entrypoint; job/version/actor identities are reloaded from PostgreSQL."""
    asyncio.run(
        _process_generation_delivery(
            uuid.UUID(delivery_id),
            uuid.UUID(dispatch_token),
        )
    )


def main() -> int:
    connection = Redis.from_url(REDIS_URL)
    queue = Queue(QUEUE_NAME, connection=connection)
    Worker([queue], connection=connection).work(with_scheduler=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "LEASE_SECONDS",
    "main",
    "run_generation_job",
)
