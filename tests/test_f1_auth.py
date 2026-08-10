"""F1 identity tests: Keycloak OIDC token validation + RBAC + tenant scope.

Requires the formal random scratch stack with its synthetic ``tester``
identity (partner+auditor, bound to tenant A).  No secrets in assertions.
"""
from __future__ import annotations

import asyncio
import base64
import json
import unittest
import urllib.parse
import urllib.request
import uuid

from fastapi import HTTPException

from platform_foundation.f1 import auth

from f11_support import (
    ENTERPRISE_A,
    ENTERPRISE_B,
    SUB_TESTER,
    configure_formal_runtime,
    get_token,
)


def setUpModule() -> None:
    configure_formal_runtime()

class _Credentials:
    def __init__(self, token: str) -> None:
        self.credentials = token


class F1AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.token = get_token()

    def test_jwks_fetch(self) -> None:
        jwks = auth._fetch_jwks()
        self.assertGreater(len(jwks["keys"]), 0)
        self.assertIn("kid", jwks["keys"][0])

    def test_valid_token_returns_claims_with_roles(self) -> None:
        user = asyncio.run(auth.current_user(_Credentials(self.token)))
        self.assertEqual(user["email"], "tester@fixture.invalid")
        self.assertTrue({"partner", "auditor"}.issubset(user["roles"]))
        self.assertEqual(user["iss"], auth.ISSUER)
        self.assertEqual(user["azp"], "anhuan-web")

    def test_missing_token_401(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(auth.current_user(None))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_invalid_token_401(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(auth.current_user(_Credentials("not.a.jwt")))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_require_role_enforces_403_and_allows(self) -> None:
        user = {"sub": "x", "roles": ["partner", "auditor"]}
        checker = auth.require_role("super_admin")
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(checker(user))
        self.assertEqual(ctx.exception.status_code, 403)
        checker2 = auth.require_role("partner")
        result = asyncio.run(checker2(user))
        self.assertEqual(result, user)

    def test_memberships_resolve_tenant_a(self) -> None:
        memberships = asyncio.run(auth.memberships_for_sub(SUB_TESTER))
        self.assertEqual(len(memberships), 1)
        self.assertEqual(uuid.UUID(memberships[0]["enterprise_id"]), ENTERPRISE_A)
        self.assertEqual(memberships[0]["role"], "partner")

    def test_current_tenant_allows_own_enterprise(self) -> None:
        user = {"sub": SUB_TESTER, "roles": ["partner", "auditor"]}
        tenant = asyncio.run(auth.current_tenant(user, ENTERPRISE_A))
        self.assertEqual(tenant.enterprise_id, ENTERPRISE_A)
        self.assertEqual(tenant.sub, SUB_TESTER)

    def test_current_tenant_cross_enterprise_is_404(self) -> None:
        user = {"sub": SUB_TESTER, "roles": ["partner", "auditor"]}
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(auth.current_tenant(user, ENTERPRISE_B))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_current_tenant_no_membership_is_404(self) -> None:
        user = {"sub": str(uuid.uuid4()), "roles": ["partner"]}
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(auth.current_tenant(user, ENTERPRISE_A))
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
