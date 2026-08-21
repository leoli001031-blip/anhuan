"""Real PostgreSQL analysis-report authorization gate.

Fake objects implement the report generator only. Database, migration,
session_scope, RLS, repository, and service stay real.
"""
from __future__ import annotations

import asyncio
import socket
import threading
import unittest
import uuid
from typing import Any
from urllib.parse import urlparse

from infra.f1.analysis_report_postgres_integration import (
    PostgresIntegrationStack,
)
from platform_foundation.f1.features.analysis_reports import service
from platform_foundation.f1.features.analysis_reports.contracts import (
    ReportNotFound,
    TEMPLATE_TITLE,
)


STACK: PostgresIntegrationStack | None = None
WORLD: Any = None
OUTBOUND = 0
_ORIG_CONNECT = socket.create_connection


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
        STACK = None


class AnalysisReportPostgresAuthzTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertIsNotNone(STACK)
        self.assertIsNotNone(WORLD)

    async def _publish(self, client_account_id: uuid.UUID) -> dict[str, Any]:
        created = await service.create_report(
            WORLD.provider_a, client_account_id, uuid.uuid4()
        )
        generated = await service.generate_report(
            WORLD.provider_a,
            client_account_id,
            uuid.UUID(created["report_id"]),
            uuid.uuid4(),
        )
        version_id = uuid.UUID(generated["version_id"])
        await service.apply_transition(WORLD.provider_a, version_id, "submit")
        await service.apply_transition(WORLD.provider_a, version_id, "approve")
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
            service.generate_report(
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
        _run(service.apply_transition(WORLD.provider_a, version_id, "approve"))
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
            service.generate_report(
                WORLD.provider_a,
                WORLD.race_client_id,
                uuid.UUID(created["report_id"]),
                uuid.uuid4(),
            )
        )
        version_id = uuid.UUID(generated["version_id"])
        _run(service.apply_transition(WORLD.provider_a, version_id, "submit"))
        _run(service.apply_transition(WORLD.provider_a, version_id, "approve"))
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
        self.assertEqual(len(privileges), 7)
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
        self.assertEqual(len(privileges), 7)
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
            service.generate_report(
                WORLD.provider_a,
                WORLD.race_client_id,
                uuid.UUID(second["report_id"]),
                uuid.uuid4(),
            )
        )
        return uuid.UUID(first["report_id"]), uuid.UUID(generated["version_id"])


if __name__ == "__main__":
    unittest.main()
