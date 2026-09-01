"""Real PostgreSQL analysis-report authorization gate.

Fake objects implement the report generator only. Database, migration,
session_scope, RLS, repository, and service stay real.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import socket
import threading
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient

from infra.f1.analysis_report_postgres_integration import (
    PostgresIntegrationStack,
)
from platform_foundation.f1.auth import tenant_from_header
from platform_foundation.f1.api.routers.analysis_reports import router as analysis_report_router
from platform_foundation.f1.features.analysis_reports import health, service
from platform_foundation.f1.features.analysis_reports.contracts import (
    ENGINEERING_FLAG,
    HealthScoreContext,
    HealthSnapshotUnavailable,
    LOCAL_FLAG,
    SCHEMA_HEALTH,
    ReportNotFound,
    ReportTransitionInvalid,
    TEMPLATE_TITLE,
)


_MIGRATE = Path(__file__).resolve().parents[1] / "infra/f1/analysis-reports/migrate.py"
_SPEC = importlib.util.spec_from_file_location("analysis_report_migrate", _MIGRATE)
assert _SPEC is not None and _SPEC.loader is not None
_MIGRATE_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MIGRATE_MOD)
EXPECTED_RLS_TABLES = _MIGRATE_MOD.EXPECTED_RLS_TABLES
STACK: PostgresIntegrationStack | None = None
WORLD: Any = None
OUTBOUND = 0
_ORIG_CONNECT = socket.create_connection

_EXPLICIT_DIMENSION_SPECS = (
    ("material-completeness", "资料完整性", 15, 10),
    ("permits", "证照与批复", 20, 12),
    ("monitoring", "监测与台账", 20, 11),
    ("remediation", "整改闭环", 25, 8),
    ("expiry", "风险与到期", 10, 6),
    ("evidence", "证据可信度", 10, 7),
)
_EXPLICIT_SNAPSHOT_SCORE = sum(
    score for _key, _label, _maximum, score in _EXPLICIT_DIMENSION_SPECS
)
_APPROVAL_CHECKLIST = {
    "citation_traceable": True,
    "risks_complete": True,
    "usage_boundary": True,
}


class _ExplicitSnapshotScorer:
    """Test-only scorer for persistence and tamper contracts."""

    def score(self, context: HealthScoreContext) -> dict[str, object]:
        dimensions = [
            {
                "key": key,
                "label": label,
                "score": score,
                "max_score": maximum,
                "summary": f"{key}-integration-summary",
                "tone": "attention",
            }
            for key, label, maximum, score in _EXPLICIT_DIMENSION_SPECS
        ]
        return {
            "report_id": str(context.report_id),
            "version_id": str(context.version_id),
            "version_number": context.version_number,
            "report_title": context.report_title,
            "score": _EXPLICIT_SNAPSHOT_SCORE,
            "max_score": 100,
            "status_label": "测试快照",
            "assessed_on": context.assessed_on.isoformat().replace("+00:00", "Z"),
            "basis_label": "仅用于持久化契约测试",
            "evidence_mode": "evidence_local",
            "dimensions": dimensions,
            "priorities": [{"title": "验证快照契约", "level": "high"}],
            "boundary": health.HEALTH_BOUNDARY,
        }


def _run(coro):
    return asyncio.run(coro)


def _guard_connect(address, *args, **kwargs):
    global OUTBOUND
    host = address[0] if isinstance(address, tuple) else address
    if host in {"127.0.0.1", "localhost", "::1"}:
        return _ORIG_CONNECT(address, *args, **kwargs)
    OUTBOUND += 1
    raise AssertionError("ARK_OR_EXTERNAL_CALL")


def setUpModule() -> None:
    global STACK, WORLD
    socket.create_connection = _guard_connect
    STACK = PostgresIntegrationStack()
    try:
        STACK.start()
        WORLD = STACK.seed_world()
    except BaseException:
        try:
            STACK.dispose_runtime()
        finally:
            STACK.stop()
            STACK = None
            socket.create_connection = _ORIG_CONNECT
        raise


def tearDownModule() -> None:
    global STACK
    socket.create_connection = _ORIG_CONNECT
    if STACK is None:
        return
    try:
        STACK.dispose_runtime()
    finally:
        STACK.stop()
        try:
            if STACK.cleanup_status != "CLEAN":
                raise AssertionError(f"CLEANUP_NOT_CLEAN:{STACK.cleanup_status}")
            if STACK.dedicated_after != (0, 0, 0):
                raise AssertionError(f"DEDICATED_RESIDUAL:{STACK.dedicated_after}")
            if STACK.control_dir.exists():
                raise AssertionError("CONTROL_DIR_EXISTS")
            if STACK.shared_match != 1:
                raise AssertionError("SHARED_MISMATCH")
        finally:
            STACK = None


class AnalysisReportPostgresAuthzTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertIsNotNone(STACK)
        self.assertIsNotNone(WORLD)
        health.set_local_scorer(None)

    def tearDown(self) -> None:
        health.set_local_scorer(None)

    async def _generate_to_draft(
        self,
        tenant,
        client_account_id: uuid.UUID,
        report_id: uuid.UUID,
        request_id: uuid.UUID,
    ) -> dict[str, Any]:
        from platform_foundation.f1.features.analysis_reports import queue, worker

        with patch.object(queue, "enqueue_generation") as enqueue:
            queued = await service.generate_report(
                tenant,
                client_account_id,
                report_id,
                request_id,
            )
        self.assertEqual(queued["status"], "queued")
        enqueue.assert_called_once_with(
            uuid.UUID(queued["job_id"]), tenant.enterprise_id, tenant.sub
        )
        await worker._process_generation_job(
            uuid.UUID(queued["job_id"]),
            tenant.enterprise_id,
            tenant.sub,
        )
        finished = await service.job_status(tenant, uuid.UUID(queued["job_id"]))
        self.assertEqual(finished["status"], "draft")
        return {**queued, "status": "draft"}

    async def _publish(self, client_account_id: uuid.UUID) -> dict[str, Any]:
        created = await service.create_report(
            WORLD.provider_a, client_account_id, uuid.uuid4()
        )
        generated = await self._generate_to_draft(
            WORLD.provider_a,
            client_account_id,
            uuid.UUID(created["report_id"]),
            uuid.uuid4(),
        )
        version_id = uuid.UUID(generated["version_id"])
        await service.apply_transition(WORLD.provider_a, version_id, "submit")
        await service.apply_transition(
            WORLD.provider_a,
            version_id,
            "approve",
            checklist=_APPROVAL_CHECKLIST,
        )
        published = await service.apply_transition(
            WORLD.provider_a, version_id, "publish"
        )
        return {
            "report": created,
            "generated": generated,
            "published": published,
            "version_id": version_id,
            "report_id": uuid.UUID(created["report_id"]),
        }

    async def _publish_with_explicit_snapshot(
        self, client_account_id: uuid.UUID
    ) -> dict[str, Any]:
        health.set_local_scorer(_ExplicitSnapshotScorer())
        try:
            return await self._publish(client_account_id)
        finally:
            health.set_local_scorer(None)

    def test_dual_membership_realm_admin_cannot_manage_non_admin_enterprise(self) -> None:
        created = _run(
            service.create_report(
                WORLD.provider_a, WORLD.bound_client_id, uuid.uuid4()
            )
        )
        self.assertEqual(created["title"], TEMPLATE_TITLE)
        with self.assertRaises(ReportNotFound):
            _run(
                service.create_report(
                    WORLD.provider_a_on_b, WORLD.foreign_client_id, uuid.uuid4()
                )
            )
        with self.assertRaises(ReportNotFound):
            _run(
                service.list_client_reports(
                    WORLD.provider_a_on_b, WORLD.foreign_client_id
                )
            )

    def test_unbound_provider_create_and_generate_are_404(self) -> None:
        with self.assertRaises(ReportNotFound):
            _run(
                service.create_report(
                    WORLD.provider_a, WORLD.unbound_client_id, uuid.uuid4()
                )
            )
        with self.assertRaises(ReportNotFound):
            _run(
                service.generate_report(
                    WORLD.provider_a,
                    WORLD.unbound_client_id,
                    uuid.uuid4(),
                    uuid.uuid4(),
                )
            )

    def test_unbound_client_list_empty_and_detail_404(self) -> None:
        listed = _run(service.list_published(WORLD.stranger_c))
        self.assertEqual(listed["reports"], [])
        with self.assertRaises(ReportNotFound):
            _run(service.get_published(WORLD.stranger_c, uuid.uuid4()))

    def test_bound_create_generate_review_publish_client_readonly(self) -> None:
        payload = _run(self._publish(WORLD.bound_client_id))
        listed = _run(service.list_published(WORLD.client_b))
        self.assertIn(str(payload["report_id"]), [item["report_id"] for item in listed["reports"]])
        detail = _run(service.get_published(WORLD.client_b, payload["report_id"]))
        self.assertEqual(len(detail["sections"]), 7)
        self.assertGreaterEqual(len(detail["citations"]), 1)
        self.assertTrue(detail["artifact_ready"])
        with self.assertRaises(ReportTransitionInvalid):
            _run(
                service.generate_report(
                    WORLD.provider_a,
                    WORLD.bound_client_id,
                    payload["report_id"],
                    uuid.uuid4(),
                )
            )
        with self.assertRaises(ReportNotFound):
            _run(
                service.create_report(
                    WORLD.client_b, WORLD.bound_client_id, uuid.uuid4()
                )
            )

    def test_raw_sql_zero_before_publish_one_after_zero_after_revoke(self) -> None:
        created = _run(
            service.create_report(
                WORLD.provider_a, WORLD.race_client_id, uuid.uuid4()
            )
        )
        generated = _run(
            self._generate_to_draft(
                WORLD.provider_a,
                WORLD.race_client_id,
                uuid.UUID(created["report_id"]),
                uuid.uuid4(),
            )
        )
        report_id = uuid.UUID(created["report_id"])
        before = STACK.api_visible_report(
            WORLD.enterprise_c, WORLD.stranger_sub, report_id
        )
        self.assertEqual(before["analysis_report"], 0)
        self.assertEqual(before["analysis_report_version"], 0)
        self.assertEqual(before["analysis_report_section"], 0)
        self.assertEqual(before["analysis_report_citation"], 0)
        version_id = uuid.UUID(generated["version_id"])
        _run(service.apply_transition(WORLD.provider_a, version_id, "submit"))
        _run(
            service.apply_transition(
                WORLD.provider_a,
                version_id,
                "approve",
                checklist=_APPROVAL_CHECKLIST,
            )
        )
        _run(service.apply_transition(WORLD.provider_a, version_id, "publish"))
        after = STACK.api_visible_report(
            WORLD.enterprise_c, WORLD.stranger_sub, report_id
        )
        self.assertEqual(after["analysis_report"], 1)
        self.assertEqual(after["analysis_report_version"], 1)
        self.assertEqual(after["analysis_report_section"], 7)
        self.assertGreaterEqual(after["analysis_report_citation"], 1)
        STACK.set_binding_status(WORLD.race_client_id, "revoked")
        try:
            revoked = STACK.api_visible_report(
                WORLD.enterprise_c, WORLD.stranger_sub, report_id
            )
            self.assertEqual(revoked["analysis_report"], 0)
            self.assertEqual(revoked["analysis_report_version"], 0)
            self.assertEqual(revoked["analysis_report_section"], 0)
            self.assertEqual(revoked["analysis_report_citation"], 0)
        finally:
            STACK.set_binding_status(WORLD.race_client_id, "active")

    def test_foreign_tenant_cannot_see_report_version_citation(self) -> None:
        payload = _run(self._publish(WORLD.bound_client_id))
        other = STACK.api_counts(WORLD.enterprise_c, WORLD.stranger_sub)
        self.assertEqual(other["analysis_report"], 0)
        self.assertEqual(other["analysis_report_version"], 0)
        self.assertEqual(other["analysis_report_citation"], 0)
        listed = _run(service.list_published(WORLD.stranger_c))
        self.assertEqual(listed["reports"], [])
        with self.assertRaises(ReportNotFound):
            _run(service.get_published(WORLD.stranger_c, payload["report_id"]))

    def test_revoke_publish_race_does_not_leak_to_client(self) -> None:
        created = _run(
            service.create_report(
                WORLD.provider_a, WORLD.race_client_id, uuid.uuid4()
            )
        )
        generated = _run(
            self._generate_to_draft(
                WORLD.provider_a,
                WORLD.race_client_id,
                uuid.UUID(created["report_id"]),
                uuid.uuid4(),
            )
        )
        version_id = uuid.UUID(generated["version_id"])
        _run(service.apply_transition(WORLD.provider_a, version_id, "submit"))
        _run(
            service.apply_transition(
                WORLD.provider_a,
                version_id,
                "approve",
                checklist=_APPROVAL_CHECKLIST,
            )
        )
        errors: list[BaseException] = []
        barrier = threading.Barrier(2)

        def publish() -> None:
            barrier.wait()
            try:
                _run(service.apply_transition(WORLD.provider_a, version_id, "publish"))
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        def revoke() -> None:
            barrier.wait()
            STACK.set_binding_status(WORLD.race_client_id, "revoked")

        threads = [
            threading.Thread(target=publish),
            threading.Thread(target=revoke),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        visible = STACK.api_counts(WORLD.enterprise_c, WORLD.stranger_sub)
        self.assertEqual(visible["analysis_report"], 0)
        self.assertEqual(visible["analysis_report_version"], 0)
        self.assertEqual(visible["analysis_report_citation"], 0)
        self.assertTrue(
            all(isinstance(item, ReportNotFound) for item in errors) or visible["analysis_report"] == 0
        )

    def test_illegal_current_version_fk_rejected(self) -> None:
        report_a, version_b = self._two_reports_and_versions()
        with STACK._bootstrap() as connection:
            with self.assertRaises(Exception) as raised:
                with connection.transaction():
                    connection.execute(
                        "UPDATE f1.analysis_report SET current_version_id=%s "
                        "WHERE id=%s",
                        (version_b, report_a),
                    )
            self.assertIn("foreign key", str(raised.exception).lower())

    def test_illegal_job_version_fk_rejected(self) -> None:
        report_a, version_b = self._two_reports_and_versions()
        with STACK._bootstrap() as connection:
            with self.assertRaises(Exception) as raised:
                with connection.transaction():
                    connection.execute(
                        "INSERT INTO f1.analysis_report_generation_job "
                        "(id,enterprise_id,report_id,version_id,request_id,status,"
                        "source_fingerprint_sha256) VALUES "
                        "(%s,%s,%s,%s,%s,'queued',%s)",
                        (
                            uuid.uuid4(),
                            WORLD.enterprise_a,
                            report_a,
                            version_b,
                            uuid.uuid4(),
                            "a" * 64,
                        ),
                    )
            self.assertIn("foreign key", str(raised.exception).lower())

    def test_illegal_audit_version_fk_rejected(self) -> None:
        report_a, version_b = self._two_reports_and_versions()
        with STACK._bootstrap() as connection:
            with self.assertRaises(Exception) as raised:
                with connection.transaction():
                    connection.execute(
                        "INSERT INTO f1.analysis_report_audit_event "
                        "(id,enterprise_id,report_id,version_id,actor_user_id,"
                        "action,from_status,to_status) VALUES "
                        "(%s,%s,%s,%s,%s,'generate','empty','queued')",
                        (
                            uuid.uuid4(),
                            WORLD.enterprise_a,
                            report_a,
                            version_b,
                            WORLD.actor_a,
                        ),
                    )
            self.assertIn("foreign key", str(raised.exception).lower())

    def test_worker_has_no_table_privileges(self) -> None:
        privileges = STACK.worker_privileges()
        self.assertEqual(len(privileges), 8)
        for table, bits in privileges.items():
            with self.subTest(table=table):
                self.assertEqual(
                    bits,
                    {
                        "SELECT": False,
                        "INSERT": False,
                        "UPDATE": False,
                        "DELETE": False,
                    },
                )

    def test_public_has_no_table_privileges(self) -> None:
        privileges = STACK.public_privileges()
        self.assertEqual(len(privileges), 8)
        for table, bits in privileges.items():
            with self.subTest(table=table):
                self.assertEqual(
                    bits,
                    {
                        "SELECT": False,
                        "INSERT": False,
                        "UPDATE": False,
                        "DELETE": False,
                    },
                )

    def test_ark_and_external_calls_are_zero(self) -> None:
        self.assertEqual(OUTBOUND, 0)
        _run(self._publish(WORLD.bound_client_id))
        self.assertEqual(OUTBOUND, 0)

    def test_provider_can_read_and_withdraw_after_revoke_but_not_publish(self) -> None:
        payload = _run(self._publish(WORLD.bound_client_id))
        STACK.set_binding_status(WORLD.bound_client_id, "revoked")
        try:
            listed = _run(
                service.list_client_reports(
                    WORLD.provider_a, WORLD.bound_client_id
                )
            )
            self.assertGreaterEqual(len(listed["reports"]), 1)
            withdrawn = _run(
                service.apply_transition(
                    WORLD.provider_a, payload["version_id"], "withdraw"
                )
            )
            self.assertEqual(withdrawn["current_status"], "withdrawn")
            with self.assertRaises(ReportNotFound):
                _run(
                    service.create_report(
                        WORLD.provider_a, WORLD.bound_client_id, uuid.uuid4()
                    )
                )
            with self.assertRaises(ReportNotFound):
                _run(
                    service.generate_report(
                        WORLD.provider_a,
                        WORLD.bound_client_id,
                        payload["report_id"],
                        uuid.uuid4(),
                    )
                )
            client_listed = _run(service.list_published(WORLD.client_b))
            self.assertTrue(
                all(
                    item["report_id"] != str(payload["report_id"])
                    for item in client_listed["reports"]
                )
            )
        finally:
            STACK.set_binding_status(WORLD.bound_client_id, "active")

    def test_dedicated_stack_identity_is_isolated(self) -> None:
        self.assertEqual(urlparse(f"tcp://127.0.0.1:{STACK.host_port}").hostname, "127.0.0.1")
        self.assertNotEqual(STACK.project_name, "anhuan-f1")
        self.assertIn("anhuan-ar-pgint-", STACK.project_name)

    def test_health_catalog_is_f1_0024_and_47_force_rls(self) -> None:
        self.assertEqual(STACK.catalog_head(), "f1_0024")
        names = STACK.force_rls_names()
        self.assertEqual(len(EXPECTED_RLS_TABLES), 47)
        self.assertTrue(set(EXPECTED_RLS_TABLES).issubset(names))
        self.assertIn("analysis_report_health_snapshot", names)

    def test_publish_without_trusted_scorer_writes_no_snapshot(self) -> None:
        payload = _run(self._publish(WORLD.bound_client_id))
        snapshot = STACK.snapshot_row(payload["version_id"])
        self.assertIsNone(snapshot)
        self.assertEqual(STACK.snapshot_count(payload["version_id"]), 0)
        actions = STACK.audit_actions(payload["version_id"])
        self.assertIn("publish", actions)
        self.assertNotIn("health_snapshot_created", actions)
        envelope = _run(service.latest_health(WORLD.client_b))
        self.assertEqual(envelope["schema"], SCHEMA_HEALTH)
        self.assertIsNone(envelope["snapshot"])

    def test_illegal_scorer_rolls_back_to_approved(self) -> None:
        created, generated, version_id = self._approve_only(WORLD.bound_client_id)

        class IllegalScorer:
            def score(self, context: HealthScoreContext) -> dict[str, object]:
                snapshot = _ExplicitSnapshotScorer().score(context)
                broken = dict(snapshot)
                broken["score"] = 1
                return broken

        health.set_local_scorer(IllegalScorer())  # type: ignore[arg-type]
        try:
            with self.assertRaises(HealthSnapshotUnavailable):
                _run(service.apply_transition(WORLD.provider_a, version_id, "publish"))
        finally:
            health.set_local_scorer(None)
        self.assertEqual(STACK.version_status(version_id), "approved")
        self.assertEqual(STACK.snapshot_count(version_id), 0)
        self.assertNotIn("publish", STACK.audit_actions(version_id))
        self.assertNotIn("health_snapshot_created", STACK.audit_actions(version_id))

    def test_scorer_exception_rolls_back_to_approved(self) -> None:
        created, generated, version_id = self._approve_only(WORLD.bound_client_id)

        class BoomScorer:
            def score(self, context: object) -> dict[str, object]:
                raise RuntimeError("scorer-down")

        health.set_local_scorer(BoomScorer())  # type: ignore[arg-type]
        try:
            with self.assertRaises(Exception):
                _run(service.apply_transition(WORLD.provider_a, version_id, "publish"))
        finally:
            health.set_local_scorer(None)
        self.assertEqual(STACK.version_status(version_id), "approved")
        self.assertEqual(STACK.snapshot_count(version_id), 0)

    def test_insert_failure_rolls_back_to_approved(self) -> None:
        created, generated, version_id = self._approve_only(WORLD.bound_client_id)
        health.set_local_scorer(_ExplicitSnapshotScorer())
        with STACK._bootstrap() as connection:
            with connection.transaction():
                connection.execute(
                    "REVOKE INSERT ON f1.analysis_report_health_snapshot FROM f1_api"
                )
        try:
            with self.assertRaises(Exception):
                _run(service.apply_transition(WORLD.provider_a, version_id, "publish"))
            self.assertEqual(STACK.version_status(version_id), "approved")
            self.assertEqual(STACK.snapshot_count(version_id), 0)
        finally:
            try:
                with STACK._bootstrap() as connection:
                    with connection.transaction():
                        connection.execute(
                            "GRANT INSERT ON f1.analysis_report_health_snapshot TO f1_api"
                        )
            finally:
                health.set_local_scorer(None)

    def test_flags_off_do_not_write_or_read_existing_snapshot(self) -> None:
        first = _run(self._publish_with_explicit_snapshot(WORLD.bound_client_id))
        self.assertEqual(STACK.snapshot_count(first["version_id"]), 1)
        previous = {
            LOCAL_FLAG: os.environ.get(LOCAL_FLAG),
            ENGINEERING_FLAG: os.environ.get(ENGINEERING_FLAG),
        }
        try:
            os.environ[LOCAL_FLAG] = "0"
            empty = _run(service.latest_health(WORLD.client_b))
            self.assertIsNone(empty["snapshot"])
            os.environ[LOCAL_FLAG] = "1"
            os.environ[ENGINEERING_FLAG] = "1"
            created, generated, version_id = self._approve_only(WORLD.bound_client_id)
            os.environ[LOCAL_FLAG] = "0"
            published = _run(
                service.apply_transition(WORLD.provider_a, version_id, "publish")
            )
            self.assertEqual(published["current_status"], "published")
            self.assertEqual(STACK.snapshot_count(version_id), 0)
            os.environ[LOCAL_FLAG] = "1"
            os.environ[ENGINEERING_FLAG] = "1"
            latest = _run(service.latest_health(WORLD.client_b))
            self.assertIsNone(latest["snapshot"])
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_unscored_health_respects_role_binding_and_withdrawal(self) -> None:
        STACK.set_binding_client(
            provider_enterprise_id=WORLD.enterprise_a,
            audience_enterprise_id=WORLD.enterprise_c,
            client_account_id=WORLD.unbound_client_id,
        )
        STACK.set_binding_client(
            provider_enterprise_id=WORLD.enterprise_a,
            audience_enterprise_id=WORLD.enterprise_b,
            client_account_id=WORLD.race_client_id,
        )
        try:
            payload = _run(self._publish(WORLD.race_client_id))
            readable = _run(service.latest_health(WORLD.client_b))
            self.assertIsNone(readable["snapshot"])
            with self.assertRaises(ReportNotFound):
                _run(service.latest_health(WORLD.provider_a))
            stranger = _run(service.latest_health(WORLD.stranger_c))
            self.assertIsNone(stranger["snapshot"])
            STACK.set_binding_status(WORLD.race_client_id, "revoked")
            try:
                revoked = _run(service.latest_health(WORLD.client_b))
                self.assertIsNone(revoked["snapshot"])
            finally:
                STACK.set_binding_status(WORLD.race_client_id, "active")
            withdrawn = _run(
                service.apply_transition(
                    WORLD.provider_a, payload["version_id"], "withdraw"
                )
            )
            self.assertEqual(withdrawn["current_status"], "withdrawn")
            after = _run(service.latest_health(WORLD.client_b))
            self.assertIsNone(after["snapshot"])
        finally:
            STACK.set_binding_client(
                provider_enterprise_id=WORLD.enterprise_a,
                audience_enterprise_id=WORLD.enterprise_b,
                client_account_id=WORLD.bound_client_id,
            )
            STACK.set_binding_client(
                provider_enterprise_id=WORLD.enterprise_a,
                audience_enterprise_id=WORLD.enterprise_c,
                client_account_id=WORLD.race_client_id,
            )

    def test_sha_ok_assessed_on_wrong_is_503(self) -> None:
        payload = _run(self._publish_with_explicit_snapshot(WORLD.bound_client_id))
        row = STACK.snapshot_row(payload["version_id"])
        self.assertIsNotNone(row)
        assert row is not None
        original = dict(row["payload"])  # type: ignore[arg-type]
        mutated = dict(original)
        mutated["assessed_on"] = "2026-01-01T00:00:00Z"
        digest = health.payload_sha256(
            health.validate_snapshot(mutated, from_storage=True)
        )
        try:
            with STACK._bootstrap() as connection:
                with connection.transaction():
                    connection.execute(
                        "SELECT set_config('session_replication_role', 'replica', true)"
                    )
                    connection.execute(
                        "UPDATE f1.analysis_report_health_snapshot "
                        "SET payload = %s::jsonb, payload_sha256 = %s "
                        "WHERE version_id = %s",
                        (
                            health.canonical_dumps(mutated),
                            digest,
                            payload["version_id"],
                        ),
                    )
            with self.assertRaises(HealthSnapshotUnavailable):
                _run(service.latest_health(WORLD.client_b))
        finally:
            with STACK._bootstrap() as connection:
                with connection.transaction():
                    connection.execute(
                        "SELECT set_config('session_replication_role', 'replica', true)"
                    )
                    connection.execute(
                        "UPDATE f1.analysis_report_health_snapshot "
                        "SET payload = %s::jsonb, payload_sha256 = %s "
                        "WHERE version_id = %s",
                        (
                            health.canonical_dumps(original),
                            row["payload_sha256"],
                            payload["version_id"],
                        ),
                    )

    def test_sha_ok_identity_wrong_and_tamper_are_503(self) -> None:
        payload = _run(self._publish_with_explicit_snapshot(WORLD.bound_client_id))
        row = STACK.snapshot_row(payload["version_id"])
        self.assertIsNotNone(row)
        assert row is not None
        mutated = dict(row["payload"])  # type: ignore[arg-type]
        mutated["report_id"] = str(uuid.uuid4())
        digest = health.payload_sha256(health.validate_snapshot(mutated, from_storage=True))
        with STACK._bootstrap() as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT set_config('session_replication_role', 'replica', true)"
                )
                connection.execute(
                    "UPDATE f1.analysis_report_health_snapshot "
                    "SET payload = %s::jsonb, payload_sha256 = %s "
                    "WHERE version_id = %s",
                    (health.canonical_dumps(mutated), digest, payload["version_id"]),
                )
        with self.assertRaises(HealthSnapshotUnavailable):
            _run(service.latest_health(WORLD.client_b))
        with STACK._bootstrap() as connection:
            with connection.transaction():
                connection.execute(
                    "SELECT set_config('session_replication_role', 'replica', true)"
                )
                connection.execute(
                    "UPDATE f1.analysis_report_health_snapshot "
                    "SET payload = jsonb_set(payload, '{status_label}', '\"tampered\"') "
                    "WHERE version_id = %s",
                    (payload["version_id"],),
                )
        with self.assertRaises(HealthSnapshotUnavailable):
            _run(service.latest_health(WORLD.client_b))

    def test_snapshot_is_immutable(self) -> None:
        payload = _run(self._publish_with_explicit_snapshot(WORLD.bound_client_id))
        before = STACK.snapshot_row(payload["version_id"])
        self.assertIsNotNone(before)
        with STACK._bootstrap() as connection:
            with self.assertRaises(Exception) as raised_update:
                with connection.transaction():
                    connection.execute(
                        "UPDATE f1.analysis_report_health_snapshot SET score = 0 "
                        "WHERE version_id = %s",
                        (payload["version_id"],),
                    )
            self.assertIn("IMMUTABLE", str(raised_update.exception).upper())
            with self.assertRaises(Exception) as raised_delete:
                with connection.transaction():
                    connection.execute(
                        "DELETE FROM f1.analysis_report_health_snapshot "
                        "WHERE version_id = %s",
                        (payload["version_id"],),
                    )
            self.assertIn("IMMUTABLE", str(raised_delete.exception).upper())
        after = STACK.snapshot_row(payload["version_id"])
        self.assertEqual(before, after)

    def test_f1_api_select_insert_only_and_asgi_get_hits_route(self) -> None:
        bits = STACK.table_privileges("f1_api", "analysis_report_health_snapshot")
        self.assertEqual(
            bits,
            {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": False},
        )
        self.assertEqual(
            STACK.table_privileges("f1_worker", "analysis_report_health_snapshot"),
            {"SELECT": False, "INSERT": False, "UPDATE": False, "DELETE": False},
        )
        self.assertEqual(
            STACK.table_privileges("public", "analysis_report_health_snapshot"),
            {"SELECT": False, "INSERT": False, "UPDATE": False, "DELETE": False},
        )
        _run(self._publish(WORLD.bound_client_id))
        app = FastAPI()
        app.include_router(analysis_report_router, prefix="/api/v1/analysis-reports")
        app.dependency_overrides[tenant_from_header] = lambda: WORLD.client_b
        http = TestClient(app)
        response = http.get("/api/v1/analysis-reports/health/latest")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["schema"], SCHEMA_HEALTH)
        self.assertIsNone(body["snapshot"])
        app.dependency_overrides[tenant_from_header] = lambda: WORLD.provider_a
        forbidden = http.get("/api/v1/analysis-reports/health/latest")
        self.assertEqual(forbidden.status_code, 404)
        self.assertEqual(OUTBOUND, 0)

    def _approve_only(
        self, client_account_id: uuid.UUID
    ) -> tuple[dict[str, Any], dict[str, Any], uuid.UUID]:
        created = _run(
            service.create_report(
                WORLD.provider_a, client_account_id, uuid.uuid4()
            )
        )
        generated = _run(
            self._generate_to_draft(
                WORLD.provider_a,
                client_account_id,
                uuid.UUID(created["report_id"]),
                uuid.uuid4(),
            )
        )
        version_id = uuid.UUID(generated["version_id"])
        _run(service.apply_transition(WORLD.provider_a, version_id, "submit"))
        _run(
            service.apply_transition(
                WORLD.provider_a,
                version_id,
                "approve",
                checklist=_APPROVAL_CHECKLIST,
            )
        )
        return created, generated, version_id

    def _two_reports_and_versions(self) -> tuple[uuid.UUID, uuid.UUID]:
        first = _run(
            service.create_report(
                WORLD.provider_a, WORLD.bound_client_id, uuid.uuid4()
            )
        )
        second = _run(
            service.create_report(
                WORLD.provider_a, WORLD.race_client_id, uuid.uuid4()
            )
        )
        generated = _run(
            self._generate_to_draft(
                WORLD.provider_a,
                WORLD.race_client_id,
                uuid.UUID(second["report_id"]),
                uuid.uuid4(),
            )
        )
        return uuid.UUID(first["report_id"]), uuid.UUID(generated["version_id"])


if __name__ == "__main__":
    unittest.main()
