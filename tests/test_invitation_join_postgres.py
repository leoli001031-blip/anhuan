"""Invitation joining through HTTP and real isolated PostgreSQL.

Only verified OIDC claims are injected; token verification is not exercised.
The invitation JWT, ledger, RLS, membership, receipt and audit are real.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import unittest
import uuid
from unittest.mock import patch

os.environ.setdefault("F1_KEYCLOAK_ISSUER_URL", "http://material-rag.invalid/realms/anhuan")
from fastapi import FastAPI
from fastapi.testclient import TestClient
from infra.f1.analysis_report_postgres_integration import PostgresIntegrationStack
from platform_foundation.f1 import invitation
from platform_foundation.f1.auth import current_user, memberships_for_sub
from platform_foundation.f1.api.routers.invitation import router as invites
from platform_foundation.f1.api.routers.users import router as users

STACK = WORLD = KEY_ENV = None


def setUpModule():
    global STACK, WORLD, KEY_ENV
    STACK = PostgresIntegrationStack()
    print("JOIN_PROJECT=" + STACK.project_name, flush=True)
    try:
        STACK.start()
        WORLD = STACK.seed_world()
        path = STACK.secrets_dir / "join_invite_signing_key"
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
        KEY_ENV = patch.dict(os.environ, {"F1_INVITE_KEY_FILE": str(path)})
        KEY_ENV.start()
    except BaseException:
        STACK.dispose_runtime()
        STACK.stop()
        raise


def tearDownModule():
    if KEY_ENV:
        KEY_ENV.stop()
    if STACK:
        STACK.dispose_runtime()
        STACK.stop()
        if STACK.cleanup_status != "CLEAN" or STACK.dedicated_after != (0, 0, 0) or STACK.shared_match != 1:
            raise AssertionError("JOIN_CLEANUP_FAILED")
        print("JOIN_CLEANUP=CLEAN;SHARED_UNCHANGED=1", flush=True)


class InvitationJoinPostgresTests(unittest.TestCase):
    def setUp(self):
        self.sub = "join-" + uuid.uuid4().hex
        self.email = self.sub + "@example.invalid"
        self.app = FastAPI()
        self.app.include_router(invites, prefix="/v1/invitations")
        self.app.include_router(users, prefix="/v1/users")
        self.claims = {"sub": self.sub, "email": self.email, "email_verified": True, "roles": []}
        self.app.dependency_overrides[current_user] = lambda: self.claims
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()

    def issue(self, *, email=None):
        return asyncio.run(invitation.create_invite(WORLD.enterprise_a, email or self.email,
            "partner", user_sub=WORLD.provider_a.sub))

    def consume(self, token, **extra):
        return self.client.post("/v1/invitations/consume", json={"token": token, **extra},
            headers={"X-Enterprise-Id": str(WORLD.enterprise_b)})

    def counts(self, invite):
        with STACK._bootstrap() as connection:
            row = connection.execute("SELECT consumed_at IS NOT NULL,consumed_by_sub FROM f1.invite_jti WHERE jti=%s", (invite.jti,)).fetchone()
            audits = connection.execute("SELECT count(*) FROM f1.audit_log WHERE action='invite.consume' AND resource_id=%s", (invite.jti,)).fetchone()[0]
        return row, audits

    def test_new_user_joins_without_tenant_and_receives_authoritative_selection(self):
        self.assertEqual(self.client.get("/v1/users/me/enterprises").json(), [])
        invite = self.issue()
        response = self.consume(invite.token, keycloak_sub="ignored-other-user", email="ignored@example.invalid")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["enterprise_id"], str(WORLD.enterprise_a))
        memberships = self.client.get("/v1/users/me/enterprises").json()
        self.assertEqual([(m["enterprise_id"], m["role"]) for m in memberships], [(str(WORLD.enterprise_a), "partner")])
        self.assertEqual(asyncio.run(memberships_for_sub("ignored-other-user")), [])
        self.assertEqual(self.counts(invite), ((True, self.sub), 1))
        second = self.consume(invite.token)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.json()["detail"], "INVITE_ALREADY_USED")
        self.assertEqual(self.counts(invite), ((True, self.sub), 1))

    def test_wrong_email_is_rejected_without_consuming_invitation(self):
        invite = self.issue(email="other@example.invalid")
        response = self.consume(invite.token, email="other@example.invalid")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "INVITE_IDENTITY_MISMATCH")
        self.assertEqual(self.counts(invite), ((False, None), 0))
        self.assertEqual(self.client.get("/v1/users/me/enterprises").json(), [])

    def test_missing_oidc_email_requires_account_completion(self):
        invite = self.issue()
        self.claims.pop("email")
        response = self.consume(invite.token)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "OIDC_EMAIL_REQUIRED")
        self.assertEqual(self.counts(invite), ((False, None), 0))

    def test_invalid_signature_does_not_change_membership_or_ledger(self):
        invite = self.issue()
        header, payload, signature = invite.token.split(".")
        invalid = ".".join((header, payload, ("a" if signature[0] != "a" else "b") + signature[1:]))
        response = self.consume(invalid)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "INVALID_INVITE")
        self.assertEqual(self.counts(invite), ((False, None), 0))

    def test_unverified_email_cannot_accept_even_when_it_matches_invitation(self):
        invite = self.issue()
        for value in (False, None, "true"):
            self.claims["email_verified"] = value
            response = self.consume(invite.token)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["detail"], "OIDC_EMAIL_VERIFICATION_REQUIRED")
            self.assertEqual(self.counts(invite), ((False, None), 0))

    def test_concurrent_acceptance_creates_one_membership_and_one_audit(self):
        invite = self.issue()
        async def consume_twice():
            return await asyncio.gather(*(invitation.consume_invite(invite.token,
                user_sub=self.sub, oidc_email=self.email) for _ in range(2)), return_exceptions=True)
        results = asyncio.run(consume_twice())
        self.assertEqual(sum(isinstance(result, invitation.Invite) for result in results), 1)
        self.assertEqual([str(result) for result in results if isinstance(result, Exception)], ["INVITE_ALREADY_USED"])
        self.assertEqual(self.counts(invite), ((True, self.sub), 1))
        self.assertEqual(len(self.client.get("/v1/users/me/enterprises").json()), 1)

    def test_existing_member_role_is_preserved(self):
        invite = self.issue()
        self.assertEqual(self.consume(invite.token).status_code, 200)
        replacement = self.issue()
        response = self.consume(replacement.token)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "MEMBERSHIP_ALREADY_EXISTS")
        self.assertEqual(self.counts(replacement), ((False, None), 0))
        self.assertEqual(self.client.get("/v1/users/me/enterprises").json()[0]["role"], "partner")

    def test_invitation_still_requires_an_authenticated_user(self):
        invite = self.issue()
        self.app.dependency_overrides.clear()
        response = self.consume(invite.token)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.counts(invite), ((False, None), 0))
