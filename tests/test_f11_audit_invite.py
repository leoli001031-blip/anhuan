"""F1.1 audit + invitation tests: append-only audit, single-use invite."""
from __future__ import annotations

import asyncio
import unittest
import uuid

from sqlalchemy import text

from platform_foundation.f1 import invitation
from platform_foundation.f1.audit import log_event
from platform_foundation.f1.database import session_scope

from f11_support import (
    ENTERPRISE_A,
    ENTERPRISE_B,
    SUB_ADMIN,
    SUB_TENANT_B,
    api,
    configure_formal_runtime,
    get_token,
)


def setUpModule() -> None:
    configure_formal_runtime()


class F11AuditInviteTests(unittest.TestCase):
    def test_audit_event_written_with_enterprise(self) -> None:
        async def _write() -> None:
            await log_event(
                ENTERPRISE_A, SUB_ADMIN, "test.write", "test",
                str(uuid.uuid4()), "success",
            )

        asyncio.run(_write())

        async def _count() -> int:
            async with session_scope(role="f1_api", enterprise_id=ENTERPRISE_A, sub=SUB_ADMIN) as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM f1.audit_log "
                            "WHERE action = 'test.write' AND enterprise_id = :eid"
                        ),
                        {"eid": ENTERPRISE_A},
                    )
                ).fetchone()
                return int(row[0])

        self.assertGreaterEqual(asyncio.run(_count()), 1)

    def test_audit_scope_does_not_leak_tenant(self) -> None:
        async def _write_b() -> None:
            await log_event(
                ENTERPRISE_B, SUB_TENANT_B, "test.write.b", "test",
                str(uuid.uuid4()), "success",
            )

        asyncio.run(_write_b())

        async def _count_a() -> int:
            async with session_scope(role="f1_api", enterprise_id=ENTERPRISE_A, sub=SUB_ADMIN) as session:
                row = (
                    await session.execute(
                        text("SELECT count(*) FROM f1.audit_log WHERE action = 'test.write.b'")
                    )
                ).fetchone()
                return int(row[0])

        self.assertEqual(asyncio.run(_count_a()), 0)

    def test_invite_create_and_validate(self) -> None:
        invite = asyncio.run(
            invitation.create_invite(
                ENTERPRISE_A, "inv@example.com", "partner", user_sub=SUB_ADMIN
            )
        )
        claims = invitation.validate_invite(invite.token)
        self.assertEqual(claims["role"], "partner")
        self.assertEqual(claims["enterprise_id"], str(ENTERPRISE_A))

    def test_invite_consume_is_single_use(self) -> None:
        invite = asyncio.run(
            invitation.create_invite(
                ENTERPRISE_A, "once@example.com", "auditor", user_sub=SUB_ADMIN
            )
        )
        invitee_sub = str(uuid.uuid4())
        asyncio.run(
            invitation.consume_invite(
                invite.token, user_sub=invitee_sub, oidc_email="once@example.com"
            )
        )
        with self.assertRaises(invitation.InvitationError):
            asyncio.run(
                invitation.consume_invite(
                    invite.token, user_sub=invitee_sub, oidc_email="once@example.com"
                )
            )

    def test_invite_invalid_role_rejected(self) -> None:
        with self.assertRaises(invitation.InvitationError):
            asyncio.run(
                invitation.create_invite(
                    ENTERPRISE_A, "x@example.com", "super_admin", user_sub=SUB_ADMIN
                )
            )

    def test_invite_api_requires_tenant(self) -> None:
        token = get_token("tenant-a")
        status, _ = api(
            "POST",
            "/api/v1/invitations",
            token,
            {"email": "n@example.com", "role": "partner"},
        )
        self.assertEqual(status, 201)


if __name__ == "__main__":
    unittest.main()
