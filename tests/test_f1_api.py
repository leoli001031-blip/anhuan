"""F1 API tests against the formal random loopback scratch stack."""
from __future__ import annotations

import json
import unittest
import urllib.request
import urllib.error

from f11_support import (
    ENTERPRISE_A,
    ENTERPRISE_B,
    api,
    configure_formal_runtime,
    formal_api_base,
    get_token,
)


def setUpModule() -> None:
    configure_formal_runtime()


class F1ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.token = get_token()
        cls.tenant_a = {"X-Enterprise-Id": str(ENTERPRISE_A)}

    def test_healthz(self) -> None:
        req = urllib.request.Request(f"{formal_api_base()}/healthz")
        with urllib.request.urlopen(req, timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["status"], "ok")

    def test_unauthorized_401(self) -> None:
        req = urllib.request.Request(f"{formal_api_base()}/api/v1/enterprises")
        try:
            urllib.request.urlopen(req, timeout=10)
            self.fail("expected 401")
        except urllib.error.HTTPError as error:
            self.assertEqual(error.code, 401)

    def test_enterprises_list_requires_auth(self) -> None:
        status, body = api("GET", "/api/v1/enterprises", self.token, headers=self.tenant_a)
        self.assertEqual(status, 200)
        self.assertIsInstance(body, list)

    def test_create_enterprise_forbidden_for_partner(self) -> None:
        status, body = api(
            "POST",
            "/api/v1/enterprises",
            self.token,
            {"name": "TestCo", "license_no": "LIC-001"},
            headers=self.tenant_a,
        )
        self.assertEqual(status, 403)

    def test_users_me(self) -> None:
        status, body = api("GET", "/api/v1/users/me", self.token)
        self.assertEqual(status, 200)
        self.assertEqual(body["email"], "tester@fixture.invalid")

    def test_cross_tenant_access_is_404(self) -> None:
        status, body = api(
            "GET",
            "/api/v1/enterprises",
            self.token,
            headers={"X-Enterprise-Id": str(ENTERPRISE_B)},
        )
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
