"""Clean-checkout configuration contract for F1.1.1.

These tests use only random synthetic values.  They never connect to a live
service and never print secret material.
"""
from __future__ import annotations

import inspect
import hmac
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from infra.f1 import seed_f1
from platform_foundation.f1 import config

import f11_support


@contextmanager
def _environment(**values: str | None):
    previous = {name: os.environ.get(name) for name in values}
    try:
        for name, value in values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


class RuntimeConfigTests(unittest.TestCase):
    def test_random_clean_runner_values_override_every_endpoint(self) -> None:
        api_port = str(20000 + int.from_bytes(os.urandom(2), "big") % 30000)
        kc_port = str(20000 + int.from_bytes(os.urandom(2), "big") % 30000)
        pg_port = str(20000 + int.from_bytes(os.urandom(2), "big") % 30000)
        database = "f1_" + uuid.uuid4().hex
        with _environment(
            F1_API_BASE_URL=f"http://127.0.0.1:{api_port}",
            KEYCLOAK_URL=f"http://127.0.0.1:{kc_port}",
            F1_KEYCLOAK_ISSUER_URL=(
                f"http://127.0.0.1:{kc_port}/realms/anhuan"
            ),
            F1_PG_HOST="127.0.0.1",
            F1_PG_PORT=pg_port,
            F1_PG_DATABASE=database,
        ):
            self.assertEqual(config.api_base_url(), f"http://127.0.0.1:{api_port}")
            self.assertEqual(config.keycloak_url(), f"http://127.0.0.1:{kc_port}")
            self.assertEqual(
                config.keycloak_issuer_url(),
                f"http://127.0.0.1:{kc_port}/realms/anhuan",
            )
            self.assertEqual(config.pg_port(), pg_port)
            self.assertEqual(config.pg_database(), database)

    def test_keycloak_issuer_is_explicit_strict_and_realm_bound(self) -> None:
        with _environment(F1_KEYCLOAK_ISSUER_URL=None):
            with self.assertRaisesRegex(
                RuntimeError, "F1_KEYCLOAK_ISSUER_URL_REQUIRED"
            ):
                config.keycloak_issuer_url()

        invalid = (
            "http://127.0.0.1:8080",
            "http://127.0.0.1:8080/realms/other",
            "http://user@127.0.0.1:8080/realms/anhuan",
            "http://127.0.0.1:8080/realms/anhuan?next=1",
            "http://127.0.0.1:8080/realms/anhuan#fragment",
            "http://127.0.0.1:99999/realms/anhuan",
        )
        for value in invalid:
            with self.subTest(value=value), _environment(
                F1_KEYCLOAK_ISSUER_URL=value
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "F1_KEYCLOAK_ISSUER_URL_INVALID"
                ):
                    config.keycloak_issuer_url()

    def test_auth_separates_internal_jwks_from_public_issuer(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src/platform_foundation/f1/auth.py"
        ).read_text(encoding="utf-8")
        self.assertIn("_keycloak_url()", source)
        self.assertIn("_keycloak_issuer_url()", source)
        self.assertNotIn("http://127.0.0.1:8080/realms/anhuan", source)
        self.assertIn('issuer=ISSUER', source)
        self.assertIn("_valid_azp", source)
        self.assertIn("_valid_audience", source)

        internal = "http://keycloak:8080"
        external = "http://127.0.0.1:29408/realms/anhuan"
        environment = dict(os.environ)
        environment.update(
            {
                "KEYCLOAK_URL": internal,
                "F1_KEYCLOAK_ISSUER_URL": external,
            }
        )
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                (
                    "from platform_foundation.f1 import auth;"
                    f"assert auth.KEYCLOAK_URL == {internal!r};"
                    f"assert auth.ISSUER == {external!r};"
                    f"assert auth.JWKS_URL.startswith({internal!r});"
                    f"assert auth._valid_issuer({external!r});"
                    "assert not auth._valid_issuer('http://wrong.invalid/realms/anhuan');"
                    "assert not auth._valid_azp('wrong-client');"
                    "assert not auth._valid_audience(['account', 'wrong-client'])"
                ),
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_compose_requires_both_public_origins(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "infra/f1/docker-compose.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "F1_KEYCLOAK_ISSUER_URL=${F1_KEYCLOAK_ISSUER_URL:?"
            "F1_KEYCLOAK_ISSUER_URL_REQUIRED}",
            source,
        )
        self.assertIn(
            "F1_WEB_PUBLIC_ORIGIN=${F1_WEB_PUBLIC_ORIGIN:?"
            "F1_WEB_PUBLIC_ORIGIN_REQUIRED}",
            source,
        )

    def test_database_identifier_is_explicit_and_fail_closed(self) -> None:
        with _environment(F1_PG_DATABASE=None):
            with self.assertRaisesRegex(RuntimeError, "F1_PG_DATABASE_REQUIRED"):
                config.pg_database()
        with _environment(F1_PG_DATABASE="bad/name"):
            with self.assertRaisesRegex(RuntimeError, "F1_PG_DATABASE_INVALID"):
                config.pg_database()

    def test_runtime_database_consumes_f1_database_config(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "src/platform_foundation/f1/database.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("pg_database()", source)
        self.assertNotIn("ACCEPTANCE_DATABASE", source)

    def test_compose_requires_database_name_for_all_runtime_processes(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "infra/f1/docker-compose.yml").read_text(encoding="utf-8")
        self.assertEqual(source.count("F1_PG_DATABASE_REQUIRED"), 3)


class SupportSecretTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _secret(self, name: str, value: str, mode: int = 0o600) -> Path:
        path = self.directory / name
        path.write_text(value, encoding="ascii")
        path.chmod(mode)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
        return path

    def test_default_oidc_password_requires_secure_file(self) -> None:
        with _environment(F1_TEST_PASSWORD_FILE_TESTER=None, F1_SECRETS_DIR=None):
            with self.assertRaisesRegex(RuntimeError, "F1_TEST_PASSWORD_REQUIRED"):
                f11_support._test_password("tester")

        path = self._secret("random-password", uuid.uuid4().hex, 0o644)
        with _environment(F1_TEST_PASSWORD_FILE_TESTER=str(path)):
            with self.assertRaisesRegex(RuntimeError, "F1_TEST_PASSWORD_FILE_INVALID"):
                f11_support._test_password("tester")

    def test_each_oidc_identity_uses_a_distinct_secure_file_binding(self) -> None:
        first = self._secret("first", uuid.uuid4().hex)
        second = self._secret("second", uuid.uuid4().hex)
        with _environment(
            F1_TEST_PASSWORD_FILE_TENANT_A=str(first),
            F1_TEST_PASSWORD_FILE_TENANT_B=str(second),
        ):
            self.assertNotEqual(
                f11_support._test_password("tenant-a"),
                f11_support._test_password("tenant-b"),
            )

    def test_role_connection_uses_runtime_fields_not_migration_dsn(self) -> None:
        password = uuid.uuid4().hex
        secret = self._secret("f1_api_password", password)
        database = "f1_" + uuid.uuid4().hex
        port = str(20000 + int.from_bytes(os.urandom(2), "big") % 30000)
        sentinel = mock.Mock()
        with _environment(
            F1_SECRETS_DIR=str(self.directory),
            F1_PG_HOST="127.0.0.1",
            F1_PG_PORT=port,
            F1_PG_DATABASE=database,
        ), mock.patch("psycopg.connect", return_value=sentinel) as connect:
            self.assertIs(f11_support.role_conn("f1_api"), sentinel)
        self.assertEqual(connect.call_count, 1)
        kwargs = dict(connect.call_args.kwargs)
        connected_password = kwargs.pop("password")
        self.assertEqual(
            kwargs,
            {
                "host": "127.0.0.1",
                "port": port,
                "dbname": database,
                "user": "f1_api",
            },
        )
        self.assertTrue(hmac.compare_digest(connected_password, password))
        self.assertEqual(secret.stat().st_nlink, 1)

    def test_role_connection_accepts_explicit_secure_file_without_directory(self) -> None:
        password = uuid.uuid4().hex
        secret = self._secret("api-role-random", password)
        database = "f1_" + uuid.uuid4().hex
        sentinel = mock.Mock()
        with _environment(
            F1_SECRETS_DIR=None,
            F1_API_PASSWORD_FILE=str(secret),
            F1_PG_DATABASE=database,
        ), mock.patch("psycopg.connect", return_value=sentinel) as connect:
            self.assertIs(f11_support.role_conn("f1_api"), sentinel)
        self.assertTrue(
            hmac.compare_digest(connect.call_args.kwargs["password"], password)
        )
        self.assertEqual(connect.call_args.kwargs["dbname"], database)


class SeedConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_seed_uses_random_explicit_database_and_secure_role_file(self) -> None:
        password = uuid.uuid4().hex
        password_file = self.directory / "f1_api_password"
        password_file.write_text(password, encoding="ascii")
        password_file.chmod(0o600)
        database = "f1_" + uuid.uuid4().hex
        port = str(20000 + int.from_bytes(os.urandom(2), "big") % 30000)
        with _environment(
            F1_SECRETS_DIR=str(self.directory),
            F1_PG_HOST="127.0.0.1",
            F1_PG_PORT=port,
            F1_PG_DATABASE=database,
        ):
            connection = seed_f1._api_connection_kwargs()
        connected_password = connection.pop("password")
        self.assertEqual(
            connection,
            {
                "host": "127.0.0.1",
                "port": port,
                "dbname": database,
                "user": "f1_api",
            },
        )
        self.assertTrue(hmac.compare_digest(connected_password, password))

    def test_seed_connects_with_runtime_role_fields_only(self) -> None:
        source = inspect.getsource(seed_f1)
        self.assertNotIn("migration_dsn", source)
        self.assertNotIn("/private/tmp", source)
        self.assertNotRegex(source, r"(?i)(tester|tenant[ab]|admin)!20[0-9]{2}")
        self.assertIn("pg_database", source)
        self.assertIn("read_f1_secret_text", source)

    def test_support_has_no_fixed_credential_or_secret_path(self) -> None:
        source = Path(f11_support.__file__).read_text(encoding="utf-8")
        self.assertNotIn("/private/tmp", source)
        self.assertNotIn("migration_dsn", source)
        self.assertNotRegex(source, r"(?i)(tester|tenant[ab]|admin)!20[0-9]{2}")


if __name__ == "__main__":
    unittest.main()
