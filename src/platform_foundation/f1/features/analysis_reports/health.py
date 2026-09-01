"""Health snapshot contract, hasher, and evidence-safe local scorer.

Independent of the report generator. HTTP never includes payload_sha256.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

from .contracts import (
    SCHEMA_HEALTH,
    HealthScoreContext,
    HealthScorerPort,
    HealthSnapshotUnavailable,
)

ENVELOPE_KEYS = ("schema", "snapshot")
SNAPSHOT_KEYS = (
    "report_id",
    "version_id",
    "version_number",
    "report_title",
    "score",
    "max_score",
    "status_label",
    "assessed_on",
    "basis_label",
    "evidence_mode",
    "dimensions",
    "priorities",
    "boundary",
)
DIMENSION_OBJECT_KEYS = ("key", "label", "score", "max_score", "summary", "tone")
PRIORITY_OBJECT_KEYS = ("title", "level")
DIMENSION_SPECS: tuple[tuple[str, str, int], ...] = (
    ("material-completeness", "资料完整性", 15),
    ("permits", "证照与批复", 20),
    ("monitoring", "监测与台账", 20),
    ("remediation", "整改闭环", 25),
    ("expiry", "风险与到期", 10),
    ("evidence", "证据可信度", 10),
)
TONES = frozenset({"positive", "attention", "priority"})
PRIORITY_LEVELS = frozenset({"high", "medium"})
EVIDENCE_MODES = frozenset({"evidence_local"})
HEALTH_BOUNDARY = (
    "该健康度用于资料管理与改善优先级参考，不替代法定合规评价、执法结论或生产放行。"
)
_LEAK_TOKENS = (
    "provider",
    "client",
    "binding",
    "scope",
    "dataset",
    "chunk",
    "sha",
    "lease",
    "request_id",
    "request-id",
)


def empty_envelope() -> dict[str, Any]:
    return {"schema": SCHEMA_HEALTH, "snapshot": None}


def canonical_dumps(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def payload_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_dumps(payload).encode("utf-8")).hexdigest()


def _closed(data: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    if list(data.keys()) != list(keys):
        raise HealthSnapshotUnavailable()
    return {key: data[key] for key in keys}


def _walk_leaks(value: Any) -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in _LEAK_TOKENS):
                raise HealthSnapshotUnavailable()
            _walk_leaks(inner)
    elif isinstance(value, list):
        for item in value:
            _walk_leaks(item)


def _require_uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise HealthSnapshotUnavailable()
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise HealthSnapshotUnavailable() from exc
    if str(parsed) != value:
        raise HealthSnapshotUnavailable()
    return value


def _require_iso(value: Any) -> str:
    if not isinstance(value, str) or len(value) < 20:
        raise HealthSnapshotUnavailable()
    stamp = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError as exc:
        raise HealthSnapshotUnavailable() from exc
    if parsed.tzinfo is None:
        raise HealthSnapshotUnavailable()
    date_part = value[:10]
    try:
        calendar_day = date(int(date_part[0:4]), int(date_part[5:7]), int(date_part[8:10]))
    except ValueError as exc:
        raise HealthSnapshotUnavailable() from exc
    if parsed.date() != calendar_day:
        raise HealthSnapshotUnavailable()
    return value


def _require_int(value: Any, minimum: int, maximum: int) -> int:
    if type(value) is not int or isinstance(value, bool):
        raise HealthSnapshotUnavailable()
    if value < minimum or value > maximum:
        raise HealthSnapshotUnavailable()
    return value


def _require_text(value: Any) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise HealthSnapshotUnavailable()
    return value


def _canonicalize_stored(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    if set(snapshot.keys()) != set(SNAPSHOT_KEYS) or len(snapshot) != len(SNAPSHOT_KEYS):
        raise HealthSnapshotUnavailable()
    dimensions = snapshot.get("dimensions")
    if not isinstance(dimensions, list):
        raise HealthSnapshotUnavailable()
    ordered_dims: list[dict[str, Any]] = []
    for item in dimensions:
        if (
            not isinstance(item, Mapping)
            or set(item.keys()) != set(DIMENSION_OBJECT_KEYS)
            or len(item) != len(DIMENSION_OBJECT_KEYS)
        ):
            raise HealthSnapshotUnavailable()
        ordered_dims.append({key: item[key] for key in DIMENSION_OBJECT_KEYS})
    priorities = snapshot.get("priorities")
    if not isinstance(priorities, list):
        raise HealthSnapshotUnavailable()
    ordered_prios: list[dict[str, Any]] = []
    for item in priorities:
        if (
            not isinstance(item, Mapping)
            or set(item.keys()) != set(PRIORITY_OBJECT_KEYS)
            or len(item) != len(PRIORITY_OBJECT_KEYS)
        ):
            raise HealthSnapshotUnavailable()
        ordered_prios.append({key: item[key] for key in PRIORITY_OBJECT_KEYS})
    ordered = {key: snapshot[key] for key in SNAPSHOT_KEYS}
    ordered["dimensions"] = ordered_dims
    ordered["priorities"] = ordered_prios
    return ordered


def validate_snapshot(
    snapshot: Mapping[str, Any], *, from_storage: bool = False
) -> dict[str, Any]:
    payload = _canonicalize_stored(snapshot) if from_storage else snapshot
    ordered = _closed(payload, SNAPSHOT_KEYS)
    _walk_leaks(ordered)
    score = _require_int(ordered["score"], 0, 100)
    max_score = _require_int(ordered["max_score"], 100, 100)
    if ordered["evidence_mode"] not in EVIDENCE_MODES:
        raise HealthSnapshotUnavailable()
    if not isinstance(ordered["dimensions"], list):
        raise HealthSnapshotUnavailable()
    if len(ordered["dimensions"]) != len(DIMENSION_SPECS):
        raise HealthSnapshotUnavailable()
    dimensions = []
    total = 0
    for index, spec in enumerate(DIMENSION_SPECS):
        key, label, dim_max = spec
        raw = ordered["dimensions"][index]
        if not isinstance(raw, Mapping):
            raise HealthSnapshotUnavailable()
        dim = _closed(raw, DIMENSION_OBJECT_KEYS)
        if dim["key"] != key or dim["label"] != label:
            raise HealthSnapshotUnavailable()
        dim_score = _require_int(dim["score"], 0, dim_max)
        _require_int(dim["max_score"], dim_max, dim_max)
        if dim["tone"] not in TONES:
            raise HealthSnapshotUnavailable()
        total += dim_score
        dimensions.append(
            {
                "key": key,
                "label": label,
                "score": dim_score,
                "max_score": dim_max,
                "summary": _require_text(dim["summary"]),
                "tone": dim["tone"],
            }
        )
    if total != score:
        raise HealthSnapshotUnavailable()
    if not isinstance(ordered["priorities"], list):
        raise HealthSnapshotUnavailable()
    if not 1 <= len(ordered["priorities"]) <= 3:
        raise HealthSnapshotUnavailable()
    priorities = []
    for raw in ordered["priorities"]:
        if not isinstance(raw, Mapping):
            raise HealthSnapshotUnavailable()
        item = _closed(raw, PRIORITY_OBJECT_KEYS)
        if item["level"] not in PRIORITY_LEVELS:
            raise HealthSnapshotUnavailable()
        priorities.append(
            {"title": _require_text(item["title"]), "level": item["level"]}
        )
    return {
        "report_id": _require_uuid(ordered["report_id"]),
        "version_id": _require_uuid(ordered["version_id"]),
        "version_number": _require_int(ordered["version_number"], 1, 1_000_000),
        "report_title": _require_text(ordered["report_title"]),
        "score": score,
        "max_score": max_score,
        "status_label": _require_text(ordered["status_label"]),
        "assessed_on": _require_iso(ordered["assessed_on"]),
        "basis_label": _require_text(ordered["basis_label"]),
        "evidence_mode": ordered["evidence_mode"],
        "dimensions": dimensions,
        "priorities": priorities,
        "boundary": _require_text(ordered["boundary"]),
    }


def http_envelope(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    if snapshot is None:
        return empty_envelope()
    return {"schema": SCHEMA_HEALTH, "snapshot": validate_snapshot(snapshot)}


def as_iso(value: datetime) -> str:
    """Return the UTC form used in persisted snapshot identity checks."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class EvidenceAwareHealthScorer:
    """Decline to score until an evidence-backed rubric exists.

    A list of released documents or keyword matches cannot establish a legal,
    operational, or safety health score. Returning ``None`` makes the public
    health envelope explicitly unscored instead of manufacturing a number.
    ``context`` is accepted for the stable port but does not contain enough
    evidence to make a score defensible.
    """

    def score(self, context: HealthScoreContext) -> dict[str, object] | None:
        _ = context
        return None


_LOCAL_SCORER: HealthScorerPort = EvidenceAwareHealthScorer()


def local_scorer() -> HealthScorerPort:
    return _LOCAL_SCORER


def set_local_scorer(scorer: HealthScorerPort | None) -> None:
    global _LOCAL_SCORER
    _LOCAL_SCORER = scorer or EvidenceAwareHealthScorer()
