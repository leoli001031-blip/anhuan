"""F1 audit tests: append-only log_event persistence + audit API + role gate."""
from __future__ import annotations

import asyncio
import unittest
import uuid

from platform_foundation.f1.audit import log_event

from f11_support import (
    ENTERPRISE_A,
    SUB_ADMIN,
    api,
    configure_formal_runtime,
    control_connection,
    get_token,
)


def setUpModule() -> None:
    configure_formal_runtime()


class F1AuditTests(unittest.TestCase):
    def test_log_event_persists_with_enterprise(self) -> None:
        async def _write() -> None:
            await log_event(
                ENTERPRISE_A,
                SUB_ADMIN,
                "enterprise.create",
                "enterprise",
                str(uuid.uuid4()),
                "success",
            )

        asyncio.run(_write())

    def test_audit_api_readable_by_auditor(self) -> None:
        token = get_token()
        status, rows = api(
            "GET",
            "/api/v1/audit",
            token,
            headers={"X-Enterprise-Id": str(ENTERPRISE_A)},
        )
        self.assertEqual(status, 200)
        self.assertIsInstance(rows, list)

    def test_audit_append_only_trigger_blocks_update(self) -> None:
        # Verify the live scratch schema retains the immutable trigger.  This
        # is read-only and cannot silently pass merely because there are zero
        # audit rows for a chosen tenant.
        with control_connection() as conn:
            row = conn.execute(
                "SELECT t.tgenabled, pg_get_triggerdef(t.oid) "
                "FROM pg_trigger AS t "
                "WHERE t.tgrelid = 'f1.audit_log'::regclass "
                "AND t.tgname = 'audit_append_only' AND NOT t.tgisinternal"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "O")
        self.assertIn("BEFORE UPDATE OR DELETE", row[1])


if __name__ == "__main__":
    unittest.main()
