"""Body-free PostgreSQL delivery state for the automatic material pipeline."""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import Tenant
from ...database import session_scope


_DELIVERY_NAMESPACE = uuid.UUID("392f275a-e420-46e8-b304-d1984d4c1d6a")
_OUTCOMES = frozenset(("done", "retry", "blocked"))
_DELIVERY_RUNTIME_ROLES = frozenset(("f1_api", "f1_worker"))


@dataclass(frozen=True, slots=True)
class MaterialPipelineDelivery:
    id: uuid.UUID
    enterprise_id: uuid.UUID
    document_version_id: uuid.UUID
    actor_sub: str
    state: str
    attempt: int
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class MaterialPipelineDeliveryClaim:
    id: uuid.UUID
    enterprise_id: uuid.UUID
    document_version_id: uuid.UUID
    actor_sub: str
    dispatch_token: uuid.UUID
    attempt: int


def delivery_id_for(
    enterprise_id: uuid.UUID, document_version_id: uuid.UUID
) -> uuid.UUID:
    if not isinstance(enterprise_id, uuid.UUID) or not isinstance(
        document_version_id, uuid.UUID
    ):
        raise ValueError("MATERIAL_PIPELINE_DELIVERY_IDENTITY_INVALID")
    identity = (
        f"material-pipeline:advance:{enterprise_id}:{document_version_id}"
    )
    return uuid.UUID(hex=hashlib.md5(identity.encode("ascii")).hexdigest())


def _actor_sub(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 255
        or value.strip() != value
        or not value.isprintable()
    ):
        raise ValueError("MATERIAL_PIPELINE_ACTOR_INVALID")
    return value


def _delivery(row) -> MaterialPipelineDelivery:
    return MaterialPipelineDelivery(
        id=row["id"],
        enterprise_id=row["enterprise_id"],
        document_version_id=row["document_version_id"],
        actor_sub=str(row["actor_sub"]),
        state=str(row["state"]),
        attempt=int(row["attempt"]),
        reason_code=str(row["reason_code"]) if row["reason_code"] else None,
    )


async def register_delivery_in_session(
    session: AsyncSession,
    tenant: Tenant,
    document_version_id: uuid.UUID,
    *,
    rearm_terminal: bool = False,
) -> MaterialPipelineDelivery:
    """Write a stable delivery inside the caller's existing transaction.

    Active work keeps its actor and lease intact.  After a revoked actor is
    durably blocked, an explicit manual replay may re-arm ``done`` or
    ``blocked`` and transfer the stable identity to the current administrator.
    """
    stable_id = delivery_id_for(tenant.enterprise_id, document_version_id)
    actor_sub = _actor_sub(tenant.sub)
    row = (
        await session.execute(
            text(
                "SELECT delivery_id AS id,enterprise_id,document_version_id,"
                "actor_sub,state,attempt,reason_code "
                "FROM f1.register_material_pipeline_delivery("
                ":enterprise_id,:version_id,:actor_sub,:rearm_terminal)"
            ),
            {
                "enterprise_id": tenant.enterprise_id,
                "version_id": document_version_id,
                "actor_sub": actor_sub,
                "rearm_terminal": rearm_terminal,
            },
        )
    ).mappings().one_or_none()
    if row is None or row["id"] != stable_id:
        raise RuntimeError("MATERIAL_PIPELINE_DELIVERY_IDENTITY_CONFLICT")
    return _delivery(row)


async def register_delivery(
    tenant: Tenant,
    document_version_id: uuid.UUID,
    *,
    rearm_terminal: bool = False,
) -> MaterialPipelineDelivery:
    """Commit one stable delivery in its own tenant-scoped transaction."""
    actor_sub = _actor_sub(tenant.sub)
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=actor_sub
    ) as session:
        delivery = await register_delivery_in_session(
            session,
            tenant,
            document_version_id,
            rearm_terminal=rearm_terminal,
        )
        await session.commit()
        return delivery


async def claim_due_deliveries(
    *, limit: int = 100, lease_seconds: int = 900
) -> tuple[MaterialPipelineDeliveryClaim, ...]:
    async with session_scope(role="f1_worker") as session:
        rows = (
            await session.execute(
                text(
                    "SELECT delivery_id,enterprise_id,document_version_id,"
                    "actor_sub,dispatch_token,attempt "
                    "FROM f1.claim_material_pipeline_deliveries("
                    ":limit,:lease_seconds)"
                ),
                {"limit": limit, "lease_seconds": lease_seconds},
            )
        ).mappings().all()
        await session.commit()
    return tuple(
        MaterialPipelineDeliveryClaim(
            id=row["delivery_id"],
            enterprise_id=row["enterprise_id"],
            document_version_id=row["document_version_id"],
            actor_sub=str(row["actor_sub"]),
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
) -> MaterialPipelineDeliveryClaim | None:
    """Resolve all executable identities from the live PostgreSQL lease."""
    if runtime_role not in _DELIVERY_RUNTIME_ROLES:
        raise ValueError("MATERIAL_PIPELINE_DELIVERY_ROLE_INVALID")
    async with session_scope(role=runtime_role) as session:
        row = (
            await session.execute(
                text(
                    "SELECT delivery_id,enterprise_id,document_version_id,"
                    "actor_sub,dispatch_token,attempt "
                    "FROM f1.read_material_pipeline_delivery_claim(:id,:token)"
                ),
                {"id": delivery_id, "token": dispatch_token},
            )
        ).mappings().one_or_none()
    if row is None:
        return None
    return MaterialPipelineDeliveryClaim(
        id=row["delivery_id"],
        enterprise_id=row["enterprise_id"],
        document_version_id=row["document_version_id"],
        actor_sub=str(row["actor_sub"]),
        dispatch_token=row["dispatch_token"],
        attempt=int(row["attempt"]),
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
    if outcome not in _OUTCOMES:
        raise ValueError("MATERIAL_PIPELINE_DELIVERY_OUTCOME_INVALID")
    if runtime_role not in _DELIVERY_RUNTIME_ROLES:
        raise ValueError("MATERIAL_PIPELINE_DELIVERY_ROLE_INVALID")
    async with session_scope(role=runtime_role) as session:
        completed = bool(
            (
                await session.execute(
                    text(
                        "SELECT f1.finish_material_pipeline_delivery("
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
    "MaterialPipelineDelivery",
    "MaterialPipelineDeliveryClaim",
    "claim_due_deliveries",
    "delivery_id_for",
    "finish_delivery",
    "read_delivery_claim",
    "register_delivery",
    "register_delivery_in_session",
)
