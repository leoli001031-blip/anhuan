"""F1 invitation tests: one-time JWT invite links with jti single-use."""
from __future__ import annotations

import asyncio
import time
import unittest
import uuid

from jose import jwt

from platform_foundation.f1 import invitation

from f11_support import ENTERPRISE_A, SUB_ADMIN, configure_formal_runtime


def setUpModule() -> None:
    configure_formal_runtime()


class F1InvitationTests(unittest.TestCase):
    def test_create_invite_valid_and_persisted(self) -> None:
        invite = asyncio.run(
            invitation.create_invite(
                ENTERPRISE_A, "user@example.com", "partner", user_sub=SUB_ADMIN
            )
        )
        self.assertTrue(invite.token)
        self.assertTrue(invite.jti)
        claims = invitation.validate_invite(invite.token)
        self.assertEqual(claims["email"], "user@example.com")
        self.assertEqual(claims["role"], "partner")

    def test_invalid_role_rejected(self) -> None:
        with self.assertRaises(invitation.InvitationError) as ctx:
            asyncio.run(
                invitation.create_invite(
                    ENTERPRISE_A, "u@e.com", "super_admin", user_sub=SUB_ADMIN
                )
            )
        self.assertEqual(str(ctx.exception), "INVALID_ROLE")

    def test_invalid_token_rejected(self) -> None:
        with self.assertRaises(invitation.InvitationError) as ctx:
            invitation.validate_invite("not.a.jwt")
        self.assertEqual(str(ctx.exception), "INVALID_INVITE")

    def test_expired_invite_rejected(self) -> None:
        payload = {
            "sub": "invite",
            "jti": uuid.uuid4().hex,
            "email": "u@e.com",
            "enterprise_id": str(ENTERPRISE_A),
            "role": "partner",
            "exp": int(time.time()) - 10,
        }
        token = jwt.encode(
            payload, invitation._load_invite_key(), algorithm=invitation.ALGORITHM
        )
        with self.assertRaises(invitation.InvitationError) as ctx:
            invitation.validate_invite(token)
        self.assertEqual(str(ctx.exception), "INVALID_INVITE")

    def test_consume_is_single_use(self) -> None:
        invite = asyncio.run(
            invitation.create_invite(
                ENTERPRISE_A, "u2@example.com", "partner", user_sub=SUB_ADMIN
            )
        )
        invitee_sub = str(uuid.uuid4())
        consumed = asyncio.run(
            invitation.consume_invite(
                invite.token, user_sub=invitee_sub, oidc_email="u2@example.com"
            )
        )
        self.assertEqual(consumed.jti, invite.jti)
        with self.assertRaises(invitation.InvitationError) as ctx:
            asyncio.run(
                invitation.consume_invite(
                    invite.token, user_sub=invitee_sub, oidc_email="u2@example.com"
                )
            )
        self.assertEqual(str(ctx.exception), "INVITE_ALREADY_USED")

    def test_ttl_is_24h(self) -> None:
        self.assertEqual(invitation.INVITE_TTL_HOURS, 24)


if __name__ == "__main__":
    unittest.main()
