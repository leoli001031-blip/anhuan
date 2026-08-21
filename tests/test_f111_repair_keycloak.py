"""F1.1.1 clean-Keycloak and collision-free Compose contracts."""
from __future__ import annotations

import importlib
import json
import os
import stat
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
REALM = ROOT / "infra/f1/keycloak/realm-import.json"
LOCAL_REALM = ROOT / "infra/f1/keycloak/realm-local.json"
COMPOSE = ROOT / "infra/f1/docker-compose.yml"
SEED = ROOT / "infra/f1/seed_f1.py"

EXPECTED_IDS = {
    "admin@anhuan.local": "d561ffe2-3be8-40cc-a87e-598dd7d84758",
    "tester": "f1f70ce5-465f-489c-a89d-974a63216ab4",
    "tenant-a": "db906685-6906-4bc4-9d3a-9011975fd132",
    "tenant-b": "ddc4e27e-ccde-4c89-958f-798fc8f30175",
    "invitee": "6f735662-672f-4aeb-9234-9a3390392f33",
    "auditor": "7e9978c7-106f-4221-a6d7-79e8104a659b",
}


class RealmContractTests(unittest.TestCase):
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

    def test_realm_has_six_deterministic_bodyless_identities(self) -> None:
        realm = json.loads(REALM.read_text(encoding="utf-8"))
        users = {user["username"]: user for user in realm["users"]}
        self.assertEqual(set(users), set(EXPECTED_IDS))
        for username, expected_id in EXPECTED_IDS.items():
            user = users[username]
            self.assertEqual(user["id"], expected_id)
            self.assertFalse(user.get("credentials"))
            self.assertTrue(user["email"].endswith("@fixture.invalid"))

    def test_realm_tracks_no_credential_or_key_material(self) -> None:
        realm = json.loads(REALM.read_text(encoding="utf-8"))
        self.assertEqual(sum(bool(user.get("credentials")) for user in realm["users"]), 0)
        self.assertEqual(sum(bool(client.get("secret")) for client in realm["clients"]), 0)
        rendered = json.dumps(realm, sort_keys=True)
        self.assertNotIn('"privateKey"', rendered)
        key_providers = realm.get("components", {}).get(
            "org.keycloak.keys.KeyProvider", []
        )
        self.assertEqual(key_providers, [])


class ProvisionerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = importlib.import_module(
            "platform_foundation.f1.keycloak_provision"
        )

    def test_each_user_has_an_independent_secret_file(self) -> None:
        bindings = self.module.IDENTITIES
        self.assertEqual({item.username for item in bindings}, set(EXPECTED_IDS))
        self.assertEqual(len({item.password_file for item in bindings}), 6)
        self.assertEqual(
            {item.user_id for item in bindings}, set(EXPECTED_IDS.values())
        )

    def test_secret_reader_rejects_non_0600_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            bad = directory / "bad"
            bad.write_text("not-a-real-secret", encoding="ascii")
            bad.chmod(0o644)
            with self.assertRaisesRegex(
                self.module.ProvisionError, "SECRET_FILE_INVALID"
            ):
                self.module._read_secret(directory, "bad")
            good = directory / "good"
            good.write_text("also-not-a-real-secret", encoding="ascii")
            good.chmod(0o600)
            link = directory / "link"
            link.symlink_to(good)
            with self.assertRaisesRegex(
                self.module.ProvisionError, "SECRET_FILE_INVALID"
            ):
                self.module._read_secret(directory, "link")
            self.assertEqual(stat.S_IMODE(good.stat().st_mode), 0o600)

    def test_failure_surface_is_reason_code_only(self) -> None:
        secret = "sentinel-do-not-print"
        with mock.patch.object(
            self.module, "provision", side_effect=self.module.ProvisionError("IDP_USER_MISMATCH")
        ), mock.patch.dict(os.environ, {"F1_SECRETS_DIR": "/run/secrets/f1"}), mock.patch(
            "builtins.print"
        ) as output:
            self.assertEqual(self.module.main(), 1)
        rendered = " ".join(str(arg) for call in output.call_args_list for arg in call.args)
        self.assertEqual(rendered, "IDP_USER_MISMATCH")
        self.assertNotIn(secret, rendered)

    def test_web_origin_is_explicit_and_strict(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                self.module.ProvisionError, "WEB_PUBLIC_ORIGIN_REQUIRED"
            ):
                self.module._web_public_origin()

        invalid = (
            "https://fixture.invalid/path",
            "https://user@fixture.invalid",
            "https://fixture.invalid?next=1",
            "https://fixture.invalid#fragment",
            "https://fixture.invalid:99999",
            "file:///tmp/web",
        )
        for value in invalid:
            with self.subTest(value=value), mock.patch.dict(
                os.environ, {"F1_WEB_PUBLIC_ORIGIN": value}, clear=True
            ):
                with self.assertRaisesRegex(
                    self.module.ProvisionError, "WEB_PUBLIC_ORIGIN_INVALID"
                ):
                    self.module._web_public_origin()

        with mock.patch.dict(
            os.environ,
            {"F1_WEB_PUBLIC_ORIGIN": "http://127.0.0.1:29417/"},
            clear=True,
        ):
            self.assertEqual(
                self.module._web_public_origin(), "http://127.0.0.1:29417"
            )

    def test_admin_rest_flow_keeps_tokens_and_passwords_out_of_urls(self) -> None:
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

        calls = []
        token = "opaque-admin-token"

        public_origin = "http://127.0.0.1:29417"

        def opener(request, *, timeout):
            self.assertEqual(timeout, 15)
            calls.append(request)
            if request.full_url.endswith("/protocol/openid-connect/token"):
                return Response(200, json.dumps({"access_token": token}).encode())
            if request.full_url.endswith("/clients?clientId=anhuan-web"):
                return Response(
                    200,
                    json.dumps(
                        [
                            {
                                "id": "165f1619-51c5-4dfc-9bb7-b81f691c4731",
                                "clientId": "anhuan-web",
                                "enabled": True,
                                "publicClient": True,
                                "bearerOnly": False,
                                "standardFlowEnabled": True,
                                "protocol": "openid-connect",
                                "redirectUris": ["http://stale.invalid/*"],
                                "webOrigins": ["http://stale.invalid"],
                            }
                        ]
                    ).encode(),
                )
            if request.get_method() == "GET":
                user_id = request.full_url.rsplit("/", 1)[1]
                identity = next(item for item in self.module.IDENTITIES if item.user_id == user_id)
                return Response(
                    200,
                    json.dumps(
                        {
                            "id": identity.user_id,
                            "username": identity.username,
                            "email": identity.email,
                            "firstName": identity.first_name,
                            "lastName": identity.last_name,
                        }
                    ).encode(),
                )
            return Response(204)

        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            values = {"keycloak_admin_password": "A" * 32}
            for index, identity in enumerate(self.module.IDENTITIES, start=1):
                values[identity.password_file] = f"{index:02d}" + "x" * 30
            for name, value in values.items():
                path = directory / name
                path.write_text(value, encoding="ascii")
                path.chmod(0o600)
            with mock.patch.dict(
                os.environ,
                {
                    "F1_SECRETS_DIR": str(directory),
                    "KEYCLOAK_URL": "http://keycloak:8080",
                    "F1_KEYCLOAK_REALM": "anhuan",
                    "F1_WEB_PUBLIC_ORIGIN": public_origin,
                },
                clear=False,
            ):
                self.module.provision(opener=opener)

        self.assertEqual(len(calls), 15)
        urls = " ".join(request.full_url for request in calls)
        self.assertNotIn(token, urls)
        for value in values.values():
            self.assertNotIn(value, urls)
        token_form = urllib.parse.parse_qs(calls[0].data.decode())
        self.assertEqual(token_form["password"], [values["keycloak_admin_password"]])
        reset_bodies = [
            json.loads(request.data)
            for request in calls
            if request.get_method() == "PUT"
            and request.full_url.endswith("/reset-password")
        ]
        self.assertEqual(
            {body["value"] for body in reset_bodies},
            {values[item.password_file] for item in self.module.IDENTITIES},
        )
        client_updates = [
            json.loads(request.data)
            for request in calls
            if request.get_method() == "PUT"
            and not request.full_url.endswith("/reset-password")
        ]
        self.assertEqual(len(client_updates), 1)
        self.assertEqual(
            client_updates[0]["redirectUris"],
            [public_origin, public_origin + "/*"],
        )
        self.assertEqual(client_updates[0]["webOrigins"], [public_origin])

    def test_missing_runtime_profile_is_repaired_before_password_reset(self) -> None:
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

        rejected_calls = []

        def rejecting_opener(request, *, timeout):
            self.assertEqual(timeout, 15)
            rejected_calls.append(request)
            if request.get_method() == "GET":
                return Response(200, user_without_profile)
            return Response(500)

        with self.assertRaises(self.module.ProvisionError) as raised:
            self.module._verify_and_set_password(
                "http://keycloak:8080",
                "anhuan",
                identity,
                password,
                token,
                opener=rejecting_opener,
            )
        rendered_error = str(raised.exception)
        self.assertEqual(rendered_error, "IDP_HTTP_REJECTED")
        self.assertNotIn(password, rendered_error)
        self.assertNotIn(token, rendered_error)
        self.assertEqual(
            [request.get_method() for request in rejected_calls],
            ["GET", "PUT"],
        )
        rejected_urls = " ".join(request.full_url for request in rejected_calls)
        self.assertNotIn(password, rejected_urls)
        self.assertNotIn(token, rejected_urls)

    def test_web_client_identity_mismatch_fails_before_any_update(self) -> None:
        class Response:
            def __init__(self, body: bytes) -> None:
                self.status = 200
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _amount: int = -1) -> bytes:
                return self.body

        requests = []

        def opener(request, *, timeout):
            self.assertEqual(timeout, 15)
            requests.append(request)
            if request.full_url.endswith("/protocol/openid-connect/token"):
                return Response(b'{"access_token":"opaque"}')
            return Response(
                json.dumps(
                    [
                        {
                            "id": "165f1619-51c5-4dfc-9bb7-b81f691c4731",
                            "clientId": "another-client",
                            "enabled": True,
                            "publicClient": True,
                            "bearerOnly": False,
                            "standardFlowEnabled": True,
                            "protocol": "openid-connect",
                        }
                    ]
                ).encode()
            )

        with self.assertRaisesRegex(
            self.module.ProvisionError, "IDP_WEB_CLIENT_MISMATCH"
        ):
            self.module._configure_web_client(
                "http://keycloak:8080",
                "anhuan",
                "http://127.0.0.1:29417",
                "opaque",
                opener=opener,
            )
        self.assertEqual([request.get_method() for request in requests], ["GET"])


class ComposeAndSeedContractTests(unittest.TestCase):
    def test_provisioner_gates_api_and_never_places_secret_in_argv(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")
        self.assertIn("keycloak-provisioner:", source)
        self.assertIn("platform_foundation.f1.keycloak_provision", source)
        self.assertIn("condition: service_completed_successfully", source)
        self.assertNotIn("oidc_tester=", source)
        self.assertNotIn("KEYCLOAK_ADMIN_PASSWORD=${", source)

    def test_all_host_ports_and_database_port_are_overrideable(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")
        names = {
            "F1_KEYCLOAK_HOST_PORT",
            "F1_MINIO_API_HOST_PORT",
            "F1_MINIO_CONSOLE_HOST_PORT",
            "F1_REDIS_HOST_PORT",
            "F1_PROMETHEUS_HOST_PORT",
            "F1_GRAFANA_HOST_PORT",
            "F1_JAEGER_UI_HOST_PORT",
            "F1_JAEGER_OTLP_GRPC_HOST_PORT",
            "F1_JAEGER_OTLP_HTTP_HOST_PORT",
            "F1_RAGFLOW_API_HOST_PORT",
            "F1_RAGFLOW_HTTP_HOST_PORT",
            "F1_API_HOST_PORT",
            "F1_WEB_HOST_PORT",
        }
        for name in names:
            self.assertIn("${" + name, source)
        self.assertNotIn('"127.0.0.1:8080:8080"', source)
        self.assertEqual(source.count("F1_PG_PORT=${F1_PG_PORT:?F1_PG_PORT_REQUIRED}"), 3)

    def test_otel_logs_are_ephemeral_and_bounded(self) -> None:
        source = COMPOSE.read_text(encoding="utf-8")
        self.assertNotIn("./otel/logs:/var/log/otel:rw", source)
        self.assertIn("/var/log/otel", source)
        self.assertIn("noexec", source)
        self.assertIn("nosuid", source)
        self.assertIn("size=", source)

    def test_seed_uses_exact_realm_ids_without_passwords(self) -> None:
        source = SEED.read_text(encoding="utf-8")
        for expected in EXPECTED_IDS.values():
            self.assertIn(expected, source)
        self.assertNotIn("password =", source)
        self.assertNotIn("@localhost", source)
        self.assertNotIn("@anhuan.local\"", source)


if __name__ == "__main__":
    unittest.main()
