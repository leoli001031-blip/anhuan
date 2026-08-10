"""P6 deterministic local run execution and immutable results."""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...audit import add_event
from ...auth import Tenant
from ...database import session_scope
from .common import current_actor_id, row_dict
from .contracts import (
    AUTOMATED_QUALITY_BOUNDARIES,
    disagreement_actions,
    is_manager,
)
from .oracle import canonical_json, evaluate
from .suites import RUN_COLUMNS, SCENARIO_COLUMNS, run_out, suite_row


RESULT_COLUMNS = (
    "id, enterprise_id, run_id, scenario_id, status, reason_code, "
    "observed_metrics, evidence_sha256, created_at"
)
DISAGREEMENT_COLUMNS = (
    "id, enterprise_id, result_id, kind, left_digest, right_digest, score, "
    "review_status, review_note, reviewed_by_user_id, reviewed_at, created_at"
)


async def run_row(
    session: AsyncSession, run_id: uuid.UUID, *, lock: bool = False
) -> Mapping[str, Any]:
    suffix = " FOR UPDATE" if lock else ""
    row = (
        await session.execute(
            text(
                f"SELECT {RUN_COLUMNS} FROM f1.quality_run "
                "WHERE id = :run_id" + suffix
            ),
            {"run_id": run_id},
        )
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="QUALITY_RUN_NOT_FOUND")
    return row


def result_out(
    row: Mapping[str, Any],
    disagreements: list[Mapping[str, Any]],
    tenant: Tenant,
) -> dict[str, Any]:
    output = row_dict(row)
    nested: list[dict[str, Any]] = []
    for item in disagreements:
        disagreement = row_dict(item)
        disagreement["allowed_actions"] = disagreement_actions(
            tenant.role, str(item["review_status"])
        )
        disagreement["boundaries"] = list(AUTOMATED_QUALITY_BOUNDARIES)
        nested.append(disagreement)
    output["disagreements"] = nested
    output["allowed_actions"] = ["view"]
    output["boundaries"] = list(AUTOMATED_QUALITY_BOUNDARIES)
    return output


async def trigger_run(tenant: Tenant, suite_id: uuid.UUID) -> dict[str, Any]:
    if not is_manager(tenant.role):
        raise HTTPException(status_code=403, detail="QUALITY_RUN_FORBIDDEN")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        suite = await suite_row(session, suite_id, lock=True)
        if suite["status"] != "active":
            raise HTTPException(status_code=409, detail="QUALITY_SUITE_ARCHIVED")
        scenarios = (
            await session.execute(
                text(
                    f"SELECT {SCENARIO_COLUMNS} FROM f1.quality_scenario "
                    "WHERE suite_id = :suite_id AND enabled IS TRUE "
                    "ORDER BY scenario_key, id FOR UPDATE"
                ),
                {"suite_id": suite_id},
            )
        ).mappings().all()
        if not scenarios:
            raise HTTPException(status_code=409, detail="QUALITY_NO_ENABLED_SCENARIOS")

        run_id = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO f1.quality_run ("
                "id, enterprise_id, suite_id, status, trigger_kind, total_count, "
                "passed_count, failed_count, error_count, created_by_user_id) "
                "VALUES (:id, :enterprise_id, :suite_id, 'queued', 'manual', "
                ":total_count, 0, 0, 0, :actor_id)"
            ),
            {
                "id": run_id,
                "enterprise_id": tenant.enterprise_id,
                "suite_id": suite_id,
                "total_count": len(scenarios),
                "actor_id": actor_id,
            },
        )
        await session.execute(
            text(
                "UPDATE f1.quality_run SET status = 'running', "
                "started_at = statement_timestamp() "
                "WHERE id = :run_id AND status = 'queued'"
            ),
            {"run_id": run_id},
        )

        passed_count = 0
        failed_count = 0
        error_count = 0
        for scenario in scenarios:
            decision = evaluate(
                scenario_type=str(scenario["scenario_type"]),
                oracle_config=scenario["oracle_config"],
                synthetic_observation=scenario["synthetic_observation"],
                scenario_sha256=str(scenario["scenario_sha256"]),
            )
            if decision.status == "passed":
                passed_count += 1
            elif decision.status == "failed":
                failed_count += 1
            else:
                error_count += 1
            result_id = uuid.uuid4()
            await session.execute(
                text(
                    "INSERT INTO f1.quality_result ("
                    "id, enterprise_id, run_id, scenario_id, status, reason_code, "
                    "observed_metrics, evidence_sha256) VALUES ("
                    ":id, :enterprise_id, :run_id, :scenario_id, :status, "
                    ":reason_code, CAST(:observed_metrics AS jsonb), "
                    ":evidence_sha256)"
                ),
                {
                    "id": result_id,
                    "enterprise_id": tenant.enterprise_id,
                    "run_id": run_id,
                    "scenario_id": scenario["id"],
                    "status": decision.status,
                    "reason_code": decision.reason_code,
                    "observed_metrics": canonical_json(decision.observed_metrics),
                    "evidence_sha256": decision.evidence_sha256,
                },
            )
            if decision.status == "failed" and decision.disagreement is not None:
                disagreement_id = uuid.uuid4()
                await session.execute(
                    text(
                        "INSERT INTO f1.quality_disagreement ("
                        "id, enterprise_id, result_id, kind, left_digest, "
                        "right_digest, score, review_status) VALUES ("
                        ":id, :enterprise_id, :result_id, :kind, :left_digest, "
                        ":right_digest, :score, 'open')"
                    ),
                    {
                        "id": disagreement_id,
                        "enterprise_id": tenant.enterprise_id,
                        "result_id": result_id,
                        **decision.disagreement,
                    },
                )

        terminal_status = (
            "passed" if failed_count == 0 and error_count == 0 else "failed"
        )
        changed = await session.execute(
            text(
                "UPDATE f1.quality_run SET status = :terminal_status, "
                "passed_count = :passed_count, failed_count = :failed_count, "
                "error_count = :error_count, completed_at = statement_timestamp() "
                "WHERE id = :run_id AND status = 'running'"
            ),
            {
                "run_id": run_id,
                "terminal_status": terminal_status,
                "passed_count": passed_count,
                "failed_count": failed_count,
                "error_count": error_count,
            },
        )
        if changed.rowcount != 1:
            raise HTTPException(status_code=409, detail="QUALITY_RUN_STATE_CONFLICT")
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "quality.run.completed",
            "quality_run",
            str(run_id),
        )
        await session.commit()
    return await get_run(tenant, run_id)


async def get_run(tenant: Tenant, run_id: uuid.UUID) -> dict[str, Any]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await current_actor_id(session, tenant)
        run = await run_row(session, run_id)
        results = (
            await session.execute(
                text(
                    f"SELECT {RESULT_COLUMNS} FROM f1.quality_result "
                    "WHERE run_id = :run_id ORDER BY created_at, id"
                ),
                {"run_id": run_id},
            )
        ).mappings().all()
        disagreements = (
            await session.execute(
                text(
                    f"SELECT {DISAGREEMENT_COLUMNS} FROM f1.quality_disagreement "
                    "WHERE result_id IN ("
                    "SELECT id FROM f1.quality_result WHERE run_id = :run_id) "
                    "ORDER BY created_at, id"
                ),
                {"run_id": run_id},
            )
        ).mappings().all()
    by_result: dict[uuid.UUID, list[Mapping[str, Any]]] = {}
    for disagreement in disagreements:
        by_result.setdefault(disagreement["result_id"], []).append(disagreement)
    output = run_out(run)
    output["results"] = [
        result_out(row, by_result.get(row["id"], []), tenant) for row in results
    ]
    return output


__all__ = (
    "DISAGREEMENT_COLUMNS",
    "RESULT_COLUMNS",
    "get_run",
    "result_out",
    "run_row",
    "trigger_run",
)
