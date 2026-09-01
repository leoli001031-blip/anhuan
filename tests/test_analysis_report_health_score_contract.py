"""Health-snapshot backend contracts. No Docker, no PostgreSQL."""
from __future__ import annotations

import os
import unittest
import uuid
from datetime import datetime, timezone

from platform_foundation.f1.api.routers.analysis_reports import _map_error
from platform_foundation.f1.auth import Tenant
from platform_foundation.f1.features.analysis_reports import health
from platform_foundation.f1.features.analysis_reports.contracts import (
    ENGINEERING_FLAG,
    HealthScoreContext,
    HealthSnapshotUnavailable,
    LOCAL_FLAG,
    SCHEMA_HEALTH,
)
from platform_foundation.f1.features.analysis_reports.service import (
    generation_enabled,
    product_role_for,
)


REPORT_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
VERSION_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

DIMENSION_SPECS = (
    ("material-completeness", "资料完整性", 15, 10),
    ("permits", "证照与批复", 20, 12),
    ("monitoring", "监测与台账", 20, 11),
    ("remediation", "整改闭环", 25, 8),
    ("expiry", "风险与到期", 10, 6),
    ("evidence", "证据可信度", 10, 7),
)
VALID_SNAPSHOT_SCORE = sum(score for _key, _label, _cap, score in DIMENSION_SPECS)


def _dimension(key: str, label: str, max_score: int, score: int) -> dict[str, object]:
    return {
        "key": key,
        "label": label,
        "score": score,
        "max_score": max_score,
        "summary": f"{key}-summary",
        "tone": "attention",
    }


def valid_snapshot() -> dict[str, object]:
    dimensions = [
        _dimension(key, label, cap, score)
        for key, label, cap, score in DIMENSION_SPECS
    ]
    return {
        "report_id": REPORT_ID,
        "version_id": VERSION_ID,
        "version_number": 1,
        "report_title": "企业安环资料分析报告",
        "score": VALID_SNAPSHOT_SCORE,
        "max_score": 100,
        "status_label": "需重点改善",
        "assessed_on": "2026-08-23T00:00:00Z",
        "basis_label": "基于已发布材料与本次分析报告",
        "evidence_mode": "evidence_local",
        "dimensions": dimensions,
        "priorities": [{"title": "补齐整改闭环材料", "level": "high"}],
        "boundary": "边界说明",
    }


class HealthScoreContractTests(unittest.TestCase):
    def test_empty_envelope_exact_key_order(self) -> None:
        envelope = health.empty_envelope()
        self.assertEqual(list(envelope.keys()), ["schema", "snapshot"])
        self.assertEqual(envelope["schema"], SCHEMA_HEALTH)
        self.assertIsNone(envelope["snapshot"])

    def test_http_envelope_exact_key_order(self) -> None:
        envelope = health.http_envelope(valid_snapshot())
        self.assertEqual(list(envelope.keys()), ["schema", "snapshot"])
        self.assertEqual(list(envelope["snapshot"].keys()), list(health.SNAPSHOT_KEYS))

    def test_validate_snapshot_accepts_closed_set(self) -> None:
        snapshot = health.validate_snapshot(valid_snapshot())
        self.assertEqual(snapshot["score"], VALID_SNAPSHOT_SCORE)
        self.assertEqual(snapshot["evidence_mode"], "evidence_local")

    def test_validate_snapshot_rejects_reordered_snapshot_keys(self) -> None:
        items = list(valid_snapshot().items())
        reordered = dict(items[1:] + items[:1])
        self.assertEqual(set(reordered), set(valid_snapshot()))
        self.assertNotEqual(list(reordered.keys()), list(health.SNAPSHOT_KEYS))
        with self.assertRaises(HealthSnapshotUnavailable):
            health.validate_snapshot(reordered)

    def test_validate_snapshot_rejects_reordered_dimension_object_keys(self) -> None:
        snapshot = valid_snapshot()
        first = dict(snapshot["dimensions"][0])  # type: ignore[index]
        items = list(first.items())
        snapshot["dimensions"][0] = dict(items[1:] + items[:1])  # type: ignore[index]
        with self.assertRaises(HealthSnapshotUnavailable):
            health.validate_snapshot(snapshot)

    def test_dimension_closed_set_order_and_caps(self) -> None:
        snapshot = health.validate_snapshot(valid_snapshot())
        keys_and_caps = [(item["key"], item["max_score"]) for item in snapshot["dimensions"]]
        self.assertEqual(
            keys_and_caps,
            [(key, cap) for key, _label, cap, _score in DIMENSION_SPECS],
        )

    def test_score_equals_dimension_sum_and_caps(self) -> None:
        snapshot = valid_snapshot()
        snapshot["score"] = VALID_SNAPSHOT_SCORE - 1
        with self.assertRaises(HealthSnapshotUnavailable):
            health.validate_snapshot(snapshot)
        snapshot = valid_snapshot()
        snapshot["dimensions"][0]["score"] = 16  # type: ignore[index]
        snapshot["score"] = VALID_SNAPSHOT_SCORE + 4
        with self.assertRaises(HealthSnapshotUnavailable):
            health.validate_snapshot(snapshot)

    def test_rejects_leak_keys(self) -> None:
        snapshot = valid_snapshot()
        snapshot["provider"] = "hidden"
        with self.assertRaises(HealthSnapshotUnavailable):
            health.validate_snapshot(snapshot)

    def test_rejects_nonexistent_calendar_date(self) -> None:
        snapshot = valid_snapshot()
        snapshot["assessed_on"] = "2026-02-30T00:00:00Z"
        with self.assertRaises(HealthSnapshotUnavailable):
            health.validate_snapshot(snapshot)

    def test_rejects_non_canonical_uuid(self) -> None:
        snapshot = valid_snapshot()
        snapshot["report_id"] = REPORT_ID.upper()
        with self.assertRaises(HealthSnapshotUnavailable):
            health.validate_snapshot(snapshot)

    def test_evidence_aware_scorer_declines_without_a_trusted_rubric(self) -> None:
        context = HealthScoreContext(
            report_id=uuid.UUID(REPORT_ID),
            version_id=uuid.UUID(VERSION_ID),
            version_number=2,
            report_title="t",
            assessed_on=datetime(2026, 8, 23, tzinfo=timezone.utc),
        )
        snapshot = health.EvidenceAwareHealthScorer().score(context)
        self.assertIsNone(snapshot)
        self.assertEqual(health.http_envelope(snapshot), health.empty_envelope())

    def test_payload_sha256_is_stable_and_hex(self) -> None:
        snapshot = health.validate_snapshot(valid_snapshot())
        digest = health.payload_sha256(snapshot)
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, health.payload_sha256(snapshot))
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_generation_enabled_requires_both_flags(self) -> None:
        previous = {
            LOCAL_FLAG: os.environ.get(LOCAL_FLAG),
            ENGINEERING_FLAG: os.environ.get(ENGINEERING_FLAG),
        }
        try:
            os.environ[LOCAL_FLAG] = "1"
            os.environ[ENGINEERING_FLAG] = "1"
            self.assertTrue(generation_enabled())
            os.environ[LOCAL_FLAG] = "0"
            self.assertFalse(generation_enabled())
            os.environ[LOCAL_FLAG] = "1"
            os.environ[ENGINEERING_FLAG] = "0"
            self.assertFalse(generation_enabled())
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_product_role_provider_vs_client(self) -> None:
        provider = Tenant(
            enterprise_id=uuid.uuid4(),
            sub="provider",
            roles=("super_admin",),
            role="super_admin",
        )
        client = Tenant(
            enterprise_id=uuid.uuid4(),
            sub="client",
            roles=(),
            role="partner",
        )
        self.assertEqual(product_role_for(provider), "provider_admin")
        self.assertEqual(product_role_for(client), "client_user")

    def test_health_unavailable_maps_to_http_503(self) -> None:
        mapped = _map_error(HealthSnapshotUnavailable())
        self.assertEqual(mapped.status_code, 503)
        self.assertEqual(mapped.detail, "HEALTH_SNAPSHOT_UNAVAILABLE")

    def test_rejects_wrong_dimension_order(self) -> None:
        snapshot = valid_snapshot()
        snapshot["dimensions"] = list(reversed(snapshot["dimensions"]))  # type: ignore[arg-type]
        with self.assertRaises(HealthSnapshotUnavailable):
            health.validate_snapshot(snapshot)


if __name__ == "__main__":
    unittest.main()
