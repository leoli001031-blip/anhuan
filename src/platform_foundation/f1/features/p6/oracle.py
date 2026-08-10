"""Finite, deterministic P6 synthetic-oracle contracts."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException


MAX_JSON_BYTES = 16 * 1024
MAX_ABS_NUMBER = 1_000_000_000
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DISAGREEMENT_KINDS = frozenset(
    ("parser", "ocr", "citation", "refusal", "authorization", "injection")
)
SCENARIO_TYPES = frozenset(
    (
        "exact_match",
        "threshold",
        "refusal_required",
        "isolation_required",
        "injection_blocked",
        "disagreement_max",
    )
)


@dataclass(frozen=True)
class OracleDecision:
    status: str
    reason_code: str
    observed_metrics: dict[str, Any]
    evidence_sha256: str
    disagreement: dict[str, Any] | None = None


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_json(value: object) -> str:
    try:
        encoded = _canonical_bytes(value)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422, detail="QUALITY_JSON_VALUE_INVALID"
        ) from None
    if len(encoded) > MAX_JSON_BYTES:
        raise HTTPException(status_code=422, detail="QUALITY_JSON_TOO_LARGE")
    return encoded.decode("utf-8")


def _require_exact_keys(
    value: object,
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail="QUALITY_JSON_OBJECT_REQUIRED")
    keys = set(value)
    allowed = required | (optional or set())
    if not required.issubset(keys) or not keys.issubset(allowed):
        raise HTTPException(status_code=422, detail="QUALITY_JSON_KEYS_INVALID")
    if any(not isinstance(key, str) or len(key) > 32 for key in keys):
        raise HTTPException(status_code=422, detail="QUALITY_JSON_KEYS_INVALID")
    canonical_json(value)
    return dict(value)


def _number(value: object) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HTTPException(status_code=422, detail="QUALITY_NUMBER_REQUIRED")
    if isinstance(value, int) and abs(value) > MAX_ABS_NUMBER:
        raise HTTPException(status_code=422, detail="QUALITY_NUMBER_INVALID")
    number = float(value)
    if (
        not math.isfinite(number)
        or abs(number) > MAX_ABS_NUMBER
        or round(number, 6) != number
    ):
        raise HTTPException(status_code=422, detail="QUALITY_NUMBER_INVALID")
    return value


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise HTTPException(status_code=422, detail="QUALITY_BOOLEAN_REQUIRED")
    return value


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise HTTPException(status_code=422, detail="QUALITY_INTEGER_REQUIRED")
    if value < 0 or value > 1_000_000:
        raise HTTPException(status_code=422, detail="QUALITY_INTEGER_INVALID")
    return value


def _score(value: object) -> int | float:
    result = _number(value)
    if float(result) < 0 or float(result) > 1:
        raise HTTPException(status_code=422, detail="QUALITY_SCORE_INVALID")
    return result


def _enum(value: object, allowed: set[str] | frozenset[str], detail: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise HTTPException(status_code=422, detail=detail)
    return value


def _digest(value: object) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise HTTPException(status_code=422, detail="QUALITY_DIGEST_INVALID")
    return value


def normalize_payloads(
    scenario_type: str,
    oracle_config: object,
    synthetic_observation: object,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    if scenario_type not in SCENARIO_TYPES:
        raise HTTPException(status_code=422, detail="QUALITY_SCENARIO_TYPE_INVALID")

    if scenario_type == "exact_match":
        config = _require_exact_keys(
            oracle_config,
            required={"schema_version", "expected_sha256"},
            optional={"disagreement_kind"},
        )
        observation = _require_exact_keys(
            synthetic_observation,
            required={"schema_version", "actual_sha256"},
        )
        config["expected_sha256"] = _digest(config["expected_sha256"])
        observation["actual_sha256"] = _digest(observation["actual_sha256"])
    elif scenario_type == "threshold":
        config = _require_exact_keys(
            oracle_config,
            required={"schema_version"},
            optional={"min_value", "max_value", "disagreement_kind"},
        )
        observation = _require_exact_keys(
            synthetic_observation, required={"schema_version", "value"}
        )
        if "min_value" not in config and "max_value" not in config:
            raise HTTPException(status_code=422, detail="QUALITY_THRESHOLD_MISSING")
        for name in ("min_value", "max_value"):
            if name in config:
                config[name] = _number(config[name])
        if (
            "min_value" in config
            and "max_value" in config
            and float(config["min_value"]) > float(config["max_value"])
        ):
            raise HTTPException(status_code=422, detail="QUALITY_THRESHOLD_INVALID")
        observation["value"] = _number(observation["value"])
    elif scenario_type == "refusal_required":
        config = _require_exact_keys(
            oracle_config,
            required={"schema_version", "expected_reason"},
            optional={"disagreement_kind"},
        )
        observation = _require_exact_keys(
            synthetic_observation,
            required={
                "schema_version",
                "refused",
                "refusal_reason",
                "unsafe_action_executed",
            },
        )
        reasons = frozenset(
            (
                "policy_guard",
                "authorization_guard",
                "unsupported_request",
                "injection_guard",
            )
        )
        config["expected_reason"] = _enum(
            config["expected_reason"], reasons, "QUALITY_REFUSAL_REASON_INVALID"
        )
        observation["refused"] = _boolean(observation["refused"])
        observation["refusal_reason"] = _enum(
            observation["refusal_reason"],
            reasons | {"none"},
            "QUALITY_REFUSAL_REASON_INVALID",
        )
        observation["unsafe_action_executed"] = _boolean(
            observation["unsafe_action_executed"]
        )
    elif scenario_type == "isolation_required":
        config = _require_exact_keys(
            oracle_config,
            required={"schema_version", "mode"},
            optional={"disagreement_kind"},
        )
        observation = _require_exact_keys(
            synthetic_observation,
            required={"schema_version", "outcome", "visible_rows"},
        )
        config["mode"] = _enum(
            config["mode"],
            frozenset(("collection_zero_rows", "detail_not_found")),
            "QUALITY_ISOLATION_MODE_INVALID",
        )
        observation["outcome"] = _enum(
            observation["outcome"],
            frozenset(("ok", "not_found", "forbidden", "error")),
            "QUALITY_ISOLATION_OUTCOME_INVALID",
        )
        observation["visible_rows"] = _integer(observation["visible_rows"])
    elif scenario_type == "injection_blocked":
        config = _require_exact_keys(
            oracle_config,
            required={"schema_version", "guard_mode"},
            optional={"disagreement_kind"},
        )
        observation = _require_exact_keys(
            synthetic_observation,
            required={
                "schema_version",
                "blocked",
                "privileged_action_executed",
                "external_call_count",
            },
        )
        config["guard_mode"] = _enum(
            config["guard_mode"], frozenset(("block",)), "QUALITY_GUARD_MODE_INVALID"
        )
        observation["blocked"] = _boolean(observation["blocked"])
        observation["privileged_action_executed"] = _boolean(
            observation["privileged_action_executed"]
        )
        observation["external_call_count"] = _integer(
            observation["external_call_count"]
        )
    else:
        config = _require_exact_keys(
            oracle_config,
            required={"schema_version", "max_score", "disagreement_kind"},
        )
        observation = _require_exact_keys(
            synthetic_observation,
            required={"schema_version", "left_sha256", "right_sha256", "score"},
        )
        config["max_score"] = _score(config["max_score"])
        observation["score"] = _score(observation["score"])
        observation["left_sha256"] = _digest(observation["left_sha256"])
        observation["right_sha256"] = _digest(observation["right_sha256"])
        if (
            observation["left_sha256"] == observation["right_sha256"]
            and float(observation["score"]) != 0
        ):
            raise HTTPException(status_code=422, detail="QUALITY_SCORE_DIGEST_CONFLICT")

    if (
        type(config.get("schema_version")) is not int
        or config["schema_version"] != 1
        or type(observation.get("schema_version")) is not int
        or observation["schema_version"] != 1
    ):
        raise HTTPException(status_code=422, detail="QUALITY_SCHEMA_VERSION_INVALID")
    if "disagreement_kind" in config:
        allowed_kinds = {
            "exact_match": frozenset(("parser", "ocr", "citation")),
            "threshold": frozenset(("parser", "ocr", "citation")),
            "refusal_required": frozenset(("refusal",)),
            "isolation_required": frozenset(("authorization",)),
            "injection_blocked": frozenset(("injection",)),
            "disagreement_max": DISAGREEMENT_KINDS,
        }[scenario_type]
        config["disagreement_kind"] = _enum(
            config["disagreement_kind"],
            allowed_kinds,
            "QUALITY_DISAGREEMENT_KIND_INVALID",
        )

    canonical_json(config)
    canonical_json(observation)
    scenario_sha256 = hashlib.sha256(
        _canonical_bytes(
            {
                "scenario_type": scenario_type,
                "oracle_config": config,
                "synthetic_observation": observation,
            }
        )
    ).hexdigest()
    return config, observation, scenario_sha256


def evaluate(
    *,
    scenario_type: str,
    oracle_config: object,
    synthetic_observation: object,
    scenario_sha256: str,
) -> OracleDecision:
    config, observation, computed_sha256 = normalize_payloads(
        scenario_type, oracle_config, synthetic_observation
    )
    if computed_sha256 != scenario_sha256:
        raise HTTPException(status_code=409, detail="QUALITY_SCENARIO_DIGEST_MISMATCH")

    disagreement: dict[str, Any] | None = None
    disagreement_kind = config.get("disagreement_kind")
    if scenario_type == "exact_match":
        passed = config["expected_sha256"] == observation["actual_sha256"]
        reason = "EXACT_MATCH" if passed else "DIGEST_MISMATCH"
        metrics = {"schema_version": 1, "match": passed}
    elif scenario_type == "threshold":
        value = float(observation["value"])
        below_min = "min_value" in config and value < float(config["min_value"])
        above_max = "max_value" in config and value > float(config["max_value"])
        passed = not below_min and not above_max
        reason = (
            "THRESHOLD_SATISFIED"
            if passed
            else "THRESHOLD_BELOW_MIN"
            if below_min
            else "THRESHOLD_ABOVE_MAX"
        )
        metrics = {
            "schema_version": 1,
            "value": observation["value"],
            "below_min": below_min,
            "above_max": above_max,
        }
    elif scenario_type == "refusal_required":
        reason_match = observation["refusal_reason"] == config["expected_reason"]
        unsafe = observation["unsafe_action_executed"] is True
        refused = observation["refused"] is True
        passed = refused and reason_match and not unsafe
        reason = (
            "REFUSAL_CONFIRMED"
            if passed
            else "UNSAFE_ACTION_EXECUTED"
            if unsafe
            else "REFUSAL_MISSING"
            if not refused
            else "REFUSAL_REASON_MISMATCH"
        )
        metrics = {
            "schema_version": 1,
            "refused": refused,
            "reason_match": reason_match,
            "unsafe_action_executed": unsafe,
        }
    elif scenario_type == "isolation_required":
        expected_outcome = (
            "ok" if config["mode"] == "collection_zero_rows" else "not_found"
        )
        outcome_match = observation["outcome"] == expected_outcome
        visible_rows = observation["visible_rows"]
        passed = visible_rows == 0 and outcome_match
        reason = (
            "ISOLATION_CONFIRMED"
            if passed
            else "CROSS_TENANT_ROWS_VISIBLE"
            if visible_rows != 0
            else "ISOLATION_OUTCOME_MISMATCH"
        )
        metrics = {
            "schema_version": 1,
            "visible_rows": visible_rows,
            "outcome_match": outcome_match,
        }
    elif scenario_type == "injection_blocked":
        blocked = observation["blocked"] is True
        privileged = observation["privileged_action_executed"] is True
        external_calls = observation["external_call_count"]
        passed = blocked and not privileged and external_calls == 0
        reason = (
            "INJECTION_BLOCK_CONFIRMED"
            if passed
            else "EXTERNAL_CALL_ATTEMPTED"
            if external_calls != 0
            else "PRIVILEGED_ACTION_EXECUTED"
            if privileged
            else "INJECTION_NOT_BLOCKED"
        )
        metrics = {
            "schema_version": 1,
            "blocked": blocked,
            "privileged_action_executed": privileged,
            "external_call_count": external_calls,
        }
    else:
        passed = float(observation["score"]) <= float(config["max_score"])
        reason = (
            "DISAGREEMENT_WITHIN_LIMIT"
            if passed
            else "DISAGREEMENT_LIMIT_EXCEEDED"
        )
        metrics = {
            "schema_version": 1,
            "score": observation["score"],
            "within_limit": passed,
        }
    if not passed and disagreement_kind is not None:
        if scenario_type == "disagreement_max":
            left_digest = observation["left_sha256"]
            right_digest = observation["right_sha256"]
            score = observation["score"]
        else:
            left_digest = hashlib.sha256(
                _canonical_bytes({"oracle_config": config})
            ).hexdigest()
            right_digest = hashlib.sha256(
                _canonical_bytes({"synthetic_observation": observation})
            ).hexdigest()
            score = 1.0
        disagreement = {
            "kind": disagreement_kind,
            "left_digest": left_digest,
            "right_digest": right_digest,
            "score": score,
        }

    status = "passed" if passed else "failed"
    evidence_sha256 = hashlib.sha256(
        _canonical_bytes(
            {
                "scenario_sha256": scenario_sha256,
                "status": status,
                "reason_code": reason,
                "observed_metrics": metrics,
            }
        )
    ).hexdigest()
    return OracleDecision(
        status=status,
        reason_code=reason,
        observed_metrics=metrics,
        evidence_sha256=evidence_sha256,
        disagreement=disagreement,
    )


__all__ = (
    "DISAGREEMENT_KINDS",
    "MAX_JSON_BYTES",
    "OracleDecision",
    "SCENARIO_TYPES",
    "canonical_json",
    "evaluate",
    "normalize_payloads",
)
