"""Closeout-only contracts for the isolated Keycloak fixture."""
from __future__ import annotations

import importlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_REALM = ROOT / "infra/f1/keycloak/realm-local.json"


class LocalKeycloakProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = importlib.import_module(
            "platform_foundation.f1.keycloak_provision"
        )

    def test_local_realm_gives_every_synthetic_user_a_nonempty_profile(self) -> None:
        realm = json.loads(LOCAL_REALM.read_text(encoding="utf-8"))
        users = realm["users"]
        self.assertTrue(users)
        for user in users:
            with self.subTest(username=user["username"]):
                self.assertIsInstance(user.get("firstName"), str)
                self.assertTrue(user["firstName"].strip())
                self.assertIsInstance(user.get("lastName"), str)
                self.assertTrue(user["lastName"].strip())

    def test_local_mode_repairs_profile_before_password_reset(self) -> None:
        class Response:
            def __init__(self, status: int, body: bytes = b"") -> None:
                self.status = status
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _amount: int = -1) -> bytes:
                return self.body

        identity = self.module.IDENTITIES[0]
        password = "fixture-profile-password-value"
        token = "opaque-profile-admin-token"
        user_without_profile = json.dumps(
            {
                "id": identity.user_id,
                "username": identity.username,
                "email": identity.email,
            }
        ).encode()
        calls = []

        def opener(request, *, timeout):
            self.assertEqual(timeout, 15)
            calls.append(request)
            if request.get_method() == "GET":
                return Response(200, user_without_profile)
            return Response(204)

        self.module._verify_and_set_password(
            "http://keycloak:8080",
            "anhuan",
            identity,
            password,
            token,
            opener=opener,
            ensure_profile=True,
        )

        self.assertEqual(
            [request.get_method() for request in calls],
            ["GET", "PUT", "PUT"],
        )
        self.assertEqual(calls[1].full_url, calls[0].full_url)
        self.assertEqual(calls[2].full_url, calls[0].full_url + "/reset-password")
        profile_update = json.loads(calls[1].data)
        self.assertEqual(profile_update["firstName"], identity.first_name)
        self.assertEqual(profile_update["lastName"], identity.last_name)
        self.assertNotIn("password", profile_update)
        self.assertNotIn(token, calls[1].data.decode())
        self.assertEqual(
            json.loads(calls[2].data),
            {"type": "password", "value": password, "temporary": False},
        )
        rendered_urls = " ".join(request.full_url for request in calls)
        self.assertNotIn(password, rendered_urls)
        self.assertNotIn(token, rendered_urls)


if __name__ == "__main__":
    unittest.main()
