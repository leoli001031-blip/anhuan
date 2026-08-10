"""P6 quality-suite and synthetic-scenario registry."""
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
    is_manager,
    run_actions,
    scenario_actions,
    suite_actions,
    suite_collection_actions,
)
from .oracle import canonical_json, normalize_payloads


SUITE_COLUMNS = (
    "id, enterprise_id, name, category, status, created_by_user_id, "
    "created_at, updated_at"
)
SCENARIO_COLUMNS = (
    "id, enterprise_id, suite_id, scenario_key, scenario_type, severity, "
    "oracle_config, synthetic_observation, scenario_sha256, enabled, "
    "created_by_user_id, created_at, updated_at"
)
RUN_COLUMNS = (
    "id, enterprise_id, suite_id, status, trigger_kind, total_count, "
    "passed_count, failed_count, error_count, created_by_user_id, created_at, "
    "started_at, completed_at"
)


async def suite_row(
    session: AsyncSession, suite_id: uuid.UUID, *, lock: bool = False
) -> Mapping[str, Any]:
    suffix = " FOR UPDATE" if lock else ""
    row = (
        await session.execute(
            text(
                f"SELECT {SUITE_COLUMNS} FROM f1.quality_suite "
                "WHERE id = :suite_id" + suffix
            ),
            {"suite_id": suite_id},
        )
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="QUALITY_SUITE_NOT_FOUND")
    return row


async def scenario_row(
    session: AsyncSession, scenario_id: uuid.UUID, *, lock: bool = False
) -> Mapping[str, Any]:
    suffix = " FOR UPDATE" if lock else ""
    row = (
        await session.execute(
            text(
                f"SELECT {SCENARIO_COLUMNS} FROM f1.quality_scenario "
                "WHERE id = :scenario_id" + suffix
            ),
            {"scenario_id": scenario_id},
        )
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="QUALITY_SCENARIO_NOT_FOUND")
    return row


def suite_out(row: Mapping[str, Any], tenant: Tenant) -> dict[str, Any]:
    output = row_dict(row)
    output["allowed_actions"] = suite_actions(tenant.role, str(row["status"]))
    output["boundaries"] = list(AUTOMATED_QUALITY_BOUNDARIES)
    return output


def scenario_out(
    row: Mapping[str, Any], tenant: Tenant, *, suite_status: str = "active"
) -> dict[str, Any]:
    output = row_dict(row)
    output["allowed_actions"] = scenario_actions(tenant.role, suite_status)
    output["boundaries"] = list(AUTOMATED_QUALITY_BOUNDARIES)
    return output


def run_out(row: Mapping[str, Any]) -> dict[str, Any]:
    output = row_dict(row)
    output["allowed_actions"] = run_actions()
    output["boundaries"] = list(AUTOMATED_QUALITY_BOUNDARIES)
    return output


async def list_suites(tenant: Tenant) -> dict[str, Any]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await current_actor_id(session, tenant)
        rows = (
            await session.execute(
                text(
                    f"SELECT {SUITE_COLUMNS} FROM f1.quality_suite "
                    "ORDER BY updated_at DESC, id"
                )
            )
        ).mappings().all()
    return {
        "items": [suite_out(row, tenant) for row in rows],
        "allowed_actions": suite_collection_actions(tenant.role),
        "boundaries": list(AUTOMATED_QUALITY_BOUNDARIES),
    }


async def get_suite(tenant: Tenant, suite_id: uuid.UUID) -> dict[str, Any]:
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await current_actor_id(session, tenant)
        suite = await suite_row(session, suite_id)
        scenarios = (
            await session.execute(
                text(
                    f"SELECT {SCENARIO_COLUMNS} FROM f1.quality_scenario "
                    "WHERE suite_id = :suite_id "
                    "ORDER BY scenario_key, id"
                ),
                {"suite_id": suite_id},
            )
        ).mappings().all()
        runs = (
            await session.execute(
                text(
                    f"SELECT {RUN_COLUMNS} FROM f1.quality_run "
                    "WHERE suite_id = :suite_id "
                    "ORDER BY created_at DESC, id DESC LIMIT 20"
                ),
                {"suite_id": suite_id},
            )
        ).mappings().all()
    output = suite_out(suite, tenant)
    output["scenarios"] = [
        scenario_out(row, tenant, suite_status=str(suite["status"]))
        for row in scenarios
    ]
    output["runs"] = [run_out(row) for row in runs]
    return output


async def create_suite(
    tenant: Tenant, *, name: str, category: str
) -> dict[str, Any]:
    if not is_manager(tenant.role):
        raise HTTPException(status_code=403, detail="QUALITY_MANAGER_REQUIRED")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        suite_id = uuid.uuid4()
        row = (
            await session.execute(
                text(
                    "INSERT INTO f1.quality_suite ("
                    "id, enterprise_id, name, category, status, created_by_user_id) "
                    "VALUES (:id, :enterprise_id, :name, :category, 'active', "
                    ":actor_id) "
                    f"RETURNING {SUITE_COLUMNS}"
                ),
                {
                    "id": suite_id,
                    "enterprise_id": tenant.enterprise_id,
                    "name": name,
                    "category": category,
                    "actor_id": actor_id,
                },
            )
        ).mappings().one()
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "quality.suite.created",
            "quality_suite",
            str(suite_id),
        )
        await session.commit()
    return suite_out(row, tenant)


async def create_scenario(
    tenant: Tenant,
    suite_id: uuid.UUID,
    *,
    scenario_key: str,
    scenario_type: str,
    severity: str,
    oracle_config: object,
    synthetic_observation: object,
    enabled: bool,
) -> dict[str, Any]:
    if not is_manager(tenant.role):
        raise HTTPException(status_code=403, detail="QUALITY_MANAGER_REQUIRED")
    config, observation, scenario_sha256 = normalize_payloads(
        scenario_type, oracle_config, synthetic_observation
    )
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await current_actor_id(session, tenant)
        suite = await suite_row(session, suite_id, lock=True)
        if suite["status"] != "active":
            raise HTTPException(status_code=409, detail="QUALITY_SUITE_ARCHIVED")
        duplicate = (
            await session.execute(
                text(
                    "SELECT 1 FROM f1.quality_scenario "
                    "WHERE suite_id = :suite_id AND scenario_key = :scenario_key"
                ),
                {"suite_id": suite_id, "scenario_key": scenario_key},
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="QUALITY_SCENARIO_KEY_EXISTS")
        scenario_id = uuid.uuid4()
        row = (
            await session.execute(
                text(
                    "INSERT INTO f1.quality_scenario ("
                    "id, enterprise_id, suite_id, scenario_key, scenario_type, "
                    "severity, oracle_config, synthetic_observation, "
                    "scenario_sha256, enabled, created_by_user_id) VALUES ("
                    ":id, :enterprise_id, :suite_id, :scenario_key, "
                    ":scenario_type, :severity, CAST(:oracle_config AS jsonb), "
                    "CAST(:synthetic_observation AS jsonb), :scenario_sha256, "
                    ":enabled, :actor_id) "
                    f"RETURNING {SCENARIO_COLUMNS}"
                ),
                {
                    "id": scenario_id,
                    "enterprise_id": tenant.enterprise_id,
                    "suite_id": suite_id,
                    "scenario_key": scenario_key,
                    "scenario_type": scenario_type,
                    "severity": severity,
                    "oracle_config": canonical_json(config),
                    "synthetic_observation": canonical_json(observation),
                    "scenario_sha256": scenario_sha256,
                    "enabled": enabled,
                    "actor_id": actor_id,
                },
            )
        ).mappings().one()
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "quality.scenario.created",
            "quality_scenario",
            str(scenario_id),
        )
        await session.commit()
    return scenario_out(row, tenant)


async def update_scenario(
    tenant: Tenant, scenario_id: uuid.UUID, changes: dict[str, Any]
) -> dict[str, Any]:
    if not is_manager(tenant.role):
        raise HTTPException(status_code=403, detail="QUALITY_MANAGER_REQUIRED")
    if not changes:
        raise HTTPException(status_code=422, detail="QUALITY_SCENARIO_NO_CHANGES")
    allowed = {"severity", "oracle_config", "synthetic_observation", "enabled"}
    if not set(changes).issubset(allowed) or any(
        value is None for value in changes.values()
    ):
        raise HTTPException(status_code=422, detail="QUALITY_SCENARIO_FIELD_INVALID")
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        await current_actor_id(session, tenant)
        visible = await scenario_row(session, scenario_id)
        suite = await suite_row(session, visible["suite_id"], lock=True)
        if suite["status"] != "active":
            raise HTTPException(status_code=409, detail="QUALITY_SUITE_ARCHIVED")
        current = await scenario_row(session, scenario_id, lock=True)
        config, observation, scenario_sha256 = normalize_payloads(
            str(current["scenario_type"]),
            changes.get("oracle_config", current["oracle_config"]),
            changes.get("synthetic_observation", current["synthetic_observation"]),
        )
        parameters: dict[str, Any] = {
            "scenario_id": scenario_id,
            "scenario_sha256": scenario_sha256,
        }
        assignments = ["scenario_sha256 = :scenario_sha256"]
        if "severity" in changes:
            assignments.append("severity = :severity")
            parameters["severity"] = changes["severity"]
        if "oracle_config" in changes:
            assignments.append("oracle_config = CAST(:oracle_config AS jsonb)")
            parameters["oracle_config"] = canonical_json(config)
        if "synthetic_observation" in changes:
            assignments.append(
                "synthetic_observation = CAST(:synthetic_observation AS jsonb)"
            )
            parameters["synthetic_observation"] = canonical_json(observation)
        if "enabled" in changes:
            assignments.append("enabled = :enabled")
            parameters["enabled"] = changes["enabled"]
        row = (
            await session.execute(
                text(
                    "UPDATE f1.quality_scenario SET "
                    + ", ".join(assignments)
                    + ", updated_at = statement_timestamp() "
                    "WHERE id = :scenario_id "
                    f"RETURNING {SCENARIO_COLUMNS}"
                ),
                parameters,
            )
        ).mappings().one_or_none()
        if row is None:
            raise HTTPException(status_code=409, detail="QUALITY_SCENARIO_CONFLICT")
        await add_event(
            session,
            tenant.enterprise_id,
            tenant.sub,
            "quality.scenario.updated",
            "quality_scenario",
            str(scenario_id),
        )
        await session.commit()
    return scenario_out(row, tenant)


__all__ = (
    "RUN_COLUMNS",
    "SCENARIO_COLUMNS",
    "SUITE_COLUMNS",
    "create_scenario",
    "create_suite",
    "get_suite",
    "list_suites",
    "run_out",
    "scenario_out",
    "scenario_row",
    "suite_out",
    "suite_row",
    "update_scenario",
)
