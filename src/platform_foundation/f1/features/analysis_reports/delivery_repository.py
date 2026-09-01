"""PostgreSQL-authoritative, body-free report generation delivery state."""
from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import session_scope
from .contracts import ENGINEERING_FLAG, LOCAL_FLAG


@dataclass(frozen=True, slots=True)
class ReportGenerationDelivery:
    id: uuid.UUID
    enterprise_id: uuid.UUID
    report_id: uuid.UUID
    job_id: uuid.UUID
    version_id: uuid.UUID
    actor_sub: str
    state: str
    attempt: int
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class ReportGenerationDeliveryClaim:
    id: uuid.UUID
    enterprise_id: uuid.UUID
    report_id: uuid.UUID
    job_id: uuid.UUID
    version_id: uuid.UUID
    actor_sub: str
    dispatch_token: uuid.UUID
    attempt: int
    job_status: str
    error_reason: str | None


@dataclass(frozen=True, slots=True)
class ReportGenerationDispatchClaim:
    id: uuid.UUID
    dispatch_token: uuid.UUID
    attempt: int


def delivery_enabled() -> bool:
    return os.environ.get(LOCAL_FLAG) == "1" and os.environ.get(ENGINEERING_FLAG) == "1"


def delivery_id_for(job_id: uuid.UUID) -> uuid.UUID:
    if not isinstance(job_id, uuid.UUID):
        raise ValueError("REPORT_DELIVERY_IDENTITY_INVALID")
    identity = f"analysis-report-generation:{job_id}"
    return uuid.UUID(hex=hashlib.md5(identity.encode("ascii")).hexdigest())


def _delivery(row) -> ReportGenerationDelivery:
    return ReportGenerationDelivery(
        id=row["delivery_id"],
        enterprise_id=row["enterprise_id"],
        report_id=row["report_id"],
        job_id=row["job_id"],
        version_id=row["version_id"],
        actor_sub=str(row["actor_sub"]),
        state=str(row["state"]),
        attempt=int(row["attempt"]),
        reason_code=str(row["reason_code"]) if row["reason_code"] else None,
    )


async def register_delivery_in_session(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    job_id: uuid.UUID,
    actor_sub: str,
    rearm_failed: bool = False,
) -> ReportGenerationDelivery:
    """Register/re-arm delivery inside the job/version transaction."""
    row = (
        await session.execute(
            text(
                "SELECT delivery_id,enterprise_id,report_id,job_id,version_id,"
                "actor_sub,state,attempt,reason_code "
                "FROM f1.register_analysis_report_generation_delivery("
                ":enterprise_id,:job_id,:actor_sub,:rearm_failed)"
            ),
            {
                "enterprise_id": enterprise_id,
                "job_id": job_id,
                "actor_sub": actor_sub,
                "rearm_failed": rearm_failed,
            },
        )
    ).mappings().one_or_none()
    if row is None or row["delivery_id"] != delivery_id_for(job_id):
        raise RuntimeError("REPORT_DELIVERY_IDENTITY_CONFLICT")
    return _delivery(row)


async def rebind_historical_delivery_in_session(
    session: AsyncSession,
    *,
    enterprise_id: uuid.UUID,
    job_id: uuid.UUID,
    actor_sub: str,
) -> bool:
    """Rebind only a migration-blocked delivery to the current active admin."""
    return bool(
        (
            await session.execute(
                text(
                    "SELECT f1.rebind_analysis_report_generation_delivery("
                    ":enterprise_id,:job_id,:actor_sub)"
                ),
                {
                    "enterprise_id": enterprise_id,
                    "job_id": job_id,
                    "actor_sub": actor_sub,
                },
            )
        ).scalar_one()
    )


async def claim_due_deliveries(
    *, limit: int = 100, lease_seconds: int = 900
) -> tuple[ReportGenerationDispatchClaim, ...]:
    async with session_scope(role="f1_worker") as session:
        rows = (
            await session.execute(
                text(
                    "SELECT delivery_id,dispatch_token,attempt "
                    "FROM f1.claim_analysis_report_generation_deliveries("
                    ":limit,:lease_seconds)"
                ),
                {"limit": limit, "lease_seconds": lease_seconds},
            )
        ).mappings().all()
        await session.commit()
    return tuple(
        ReportGenerationDispatchClaim(
            id=row["delivery_id"],
            dispatch_token=row["dispatch_token"],
            attempt=int(row["attempt"]),
        )
        for row in rows
    )


async def read_delivery_claim(
    delivery_id: uuid.UUID,
    dispatch_token: uuid.UUID,
    *,
    runtime_role: str = "f1_api",
) -> ReportGenerationDeliveryClaim | None:
    if runtime_role not in {"f1_api", "f1_worker"}:
        raise ValueError("REPORT_DELIVERY_ROLE_INVALID")
    async with session_scope(role=runtime_role) as session:
        row = (
            await session.execute(
                text(
                    "SELECT delivery_id,enterprise_id,report_id,job_id,version_id,"
                    "actor_sub,dispatch_token,attempt,job_status,error_reason "
                    "FROM f1.read_analysis_report_generation_delivery_claim("
                    ":delivery_id,:dispatch_token)"
                ),
                {"delivery_id": delivery_id, "dispatch_token": dispatch_token},
            )
        ).mappings().one_or_none()
    if row is None:
        return None
    return ReportGenerationDeliveryClaim(
        id=row["delivery_id"],
        enterprise_id=row["enterprise_id"],
        report_id=row["report_id"],
        job_id=row["job_id"],
        version_id=row["version_id"],
        actor_sub=str(row["actor_sub"]),
        dispatch_token=row["dispatch_token"],
        attempt=int(row["attempt"]),
        job_status=str(row["job_status"]),
        error_reason=str(row["error_reason"]) if row["error_reason"] else None,
    )


async def finish_delivery(
    delivery_id: uuid.UUID,
    dispatch_token: uuid.UUID,
    *,
    outcome: str,
    reason_code: str | None = None,
    retry_seconds: int | None = None,
    runtime_role: str = "f1_api",
) -> bool:
    if outcome not in {"done", "retry", "blocked"}:
        raise ValueError("REPORT_DELIVERY_OUTCOME_INVALID")
    if runtime_role not in {"f1_api", "f1_worker"}:
        raise ValueError("REPORT_DELIVERY_ROLE_INVALID")
    async with session_scope(role=runtime_role) as session:
        completed = bool(
            (
                await session.execute(
                    text(
                        "SELECT f1.finish_analysis_report_generation_delivery("
                        ":delivery_id,:dispatch_token,:outcome,"
                        ":reason_code,:retry_seconds)"
                    ),
                    {
                        "delivery_id": delivery_id,
                        "dispatch_token": dispatch_token,
                        "outcome": outcome,
                        "reason_code": reason_code,
                        "retry_seconds": retry_seconds,
                    },
                )
            ).scalar()
        )
        await session.commit()
        return completed


__all__ = (
    "ReportGenerationDelivery",
    "ReportGenerationDeliveryClaim",
    "ReportGenerationDispatchClaim",
    "claim_due_deliveries",
    "delivery_enabled",
    "delivery_id_for",
    "finish_delivery",
    "read_delivery_claim",
    "rebind_historical_delivery_in_session",
    "register_delivery_in_session",
)
