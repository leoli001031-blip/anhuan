from __future__ import annotations

import os
import stat
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from platform_foundation.f1 import secret_files


@contextmanager
def configured(**values: str | None):
    prior = {name: os.environ.get(name) for name in values}
    try:
        for name, value in values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        yield
    finally:
        for name, value in prior.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def secure_file(root: Path, name: str, value: bytes) -> Path:
    path = root / name
    path.write_bytes(value)
    path.chmod(0o600)
    return path


class SecretFileContractTests(unittest.TestCase):
    def test_f1_directory_secret_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            secure_file(root, "minio_root_user", b"opaque-user\n")
            with configured(
                F1_SECRETS_DIR=raw,
                F1_MINIO_ROOT_USER_FILE=None,
            ):
                self.assertEqual(
                    secret_files.read_f1_secret_text(
                        "minio_root_user", file_env="F1_MINIO_ROOT_USER_FILE"
                    ),
                    "opaque-user",
                )

    def test_explicit_file_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            directory_value = secure_file(root, "minio_root_user", b"directory")
            explicit_value = secure_file(root, "explicit", b"explicit")
            self.assertNotEqual(directory_value, explicit_value)
            with configured(
                F1_SECRETS_DIR=raw,
                F1_MINIO_ROOT_USER_FILE=str(explicit_value),
            ):
                self.assertEqual(
                    secret_files.read_f1_secret_text(
                        "minio_root_user", file_env="F1_MINIO_ROOT_USER_FILE"
                    ),
                    "explicit",
                )

    def test_provider_directory_secret_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            secure_file(root, "ragflow_api_key", b"opaque-provider")
            with configured(
                F1_PROVIDER_SECRETS_DIR=raw,
                F1_RAGFLOW_API_KEY_FILE=None,
            ):
                self.assertEqual(
                    secret_files.read_provider_secret_text(
                        "ragflow_api_key", file_env="F1_RAGFLOW_API_KEY_FILE"
                    ),
                    "opaque-provider",
                )

    def test_missing_configuration_fails_closed(self) -> None:
        with configured(
            F1_SECRETS_DIR=None,
            F1_MINIO_ROOT_USER_FILE=None,
        ):
            with self.assertRaisesRegex(
                secret_files.SecretFileError, "F1_RUNTIME_SECRET_UNAVAILABLE"
            ):
                secret_files.read_f1_secret_text(
                    "minio_root_user", file_env="F1_MINIO_ROOT_USER_FILE"
                )

    def test_relative_file_is_rejected(self) -> None:
        with configured(F1_F0I_KEY_FILE="relative-key"):
            with self.assertRaisesRegex(
                secret_files.SecretFileError, "F1_F0I_KEY_UNAVAILABLE_PATH_INVALID"
            ):
                secret_files.read_f0i_key()

    def test_f0i_key_requires_exact_32_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = secure_file(Path(raw), "key", b"k" * 32)
            with configured(F1_F0I_KEY_FILE=str(path)):
                self.assertEqual(secret_files.read_f0i_key(), b"k" * 32)
            path.write_bytes(b"k" * 31)
            path.chmod(0o600)
            with configured(F1_F0I_KEY_FILE=str(path)):
                with self.assertRaises(secret_files.SecretFileError):
                    secret_files.read_f0i_key()

    def test_group_or_world_permissions_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = secure_file(Path(raw), "key", b"k" * 32)
            path.chmod(0o640)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
            with configured(F1_F0I_KEY_FILE=str(path)):
                with self.assertRaisesRegex(
                    secret_files.SecretFileError, "PERMISSIONS_INVALID"
                ):
                    secret_files.read_f0i_key()

    def test_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = secure_file(root, "target", b"k" * 32)
            link = root / "link"
            link.symlink_to(target)
            with configured(F1_F0I_KEY_FILE=str(link)):
                with self.assertRaises(secret_files.SecretFileError):
                    secret_files.read_f0i_key()

    def test_hardlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = secure_file(root, "target", b"k" * 32)
            link = root / "link"
            os.link(target, link)
            with configured(F1_F0I_KEY_FILE=str(link)):
                with self.assertRaisesRegex(
                    secret_files.SecretFileError, "PERMISSIONS_INVALID"
                ):
                    secret_files.read_f0i_key()


class RuntimeWiringContractTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_f1_runtime_modules_do_not_embed_developer_secret_paths(self) -> None:
        names = (
            "storage.py",
            "ragflow_provision.py",
            "indexing.py",
            "citation.py",
            "qa_chain.py",
            "llm_client.py",
        )
        base = self.ROOT / "src/platform_foundation/f1"
        for name in names:
            source = (base / name).read_text(encoding="utf-8")
            self.assertNotIn("/private/tmp/", source, name)
            self.assertNotIn("anhuan-f0j1-secrets", source, name)

    def test_compose_mounts_explicit_provider_and_f0i_sources(self) -> None:
        source = (self.ROOT / "infra/f1/docker-compose.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("F1_PROVIDER_SECRETS_DIR_REQUIRED", source)
        self.assertIn("F1_F0I_KEY_FILE_REQUIRED", source)
        self.assertIn("F1_PROVIDER_SECRETS_DIR=/run/secrets/provider", source)
        self.assertIn("F1_F0I_KEY_FILE=/run/secrets/f0i/key", source)
        self.assertNotIn("anhuan-f0j1-secrets:/private/tmp", source)

    def test_runtime_services_never_mount_whole_secret_directories(self) -> None:
        source = (self.ROOT / "infra/f1/docker-compose.yml").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "${F1_SECRETS_DIR:?F1_SECRETS_DIR_REQUIRED}:/run/secrets/f1:ro",
            "${F1_PROVIDER_SECRETS_DIR:?F1_PROVIDER_SECRETS_DIR_REQUIRED}:/run/secrets/provider:ro",
        ):
            self.assertNotIn(forbidden, source)

    def test_api_worker_dispatcher_mount_only_their_runtime_db_password(self) -> None:
        source = (self.ROOT / "infra/f1/docker-compose.yml").read_text(
            encoding="utf-8"
        )

        def service_block(name: str, following: str) -> str:
            return source.split(f"  {name}:\n", 1)[1].split(
                f"  {following}:\n", 1
            )[0]

        api = service_block("api", "worker")
        worker = service_block("worker", "dispatcher")
        dispatcher = service_block("dispatcher", "web")
        provisioner = service_block("keycloak-provisioner", "minio")

        self.assertIn("/f1_api_password:/run/secrets/f1/f1_api_password:ro", api)
        self.assertNotIn("f1_worker_password", api)
        self.assertIn("/f1_worker_password:/run/secrets/f1/f1_worker_password:ro", worker)
        self.assertNotIn("f1_api_password", worker)
        self.assertIn("/f1_worker_password:/run/secrets/f1/f1_worker_password:ro", dispatcher)
        self.assertNotIn("f1_api_password", dispatcher)
        for block in (api, worker, dispatcher, provisioner):
            self.assertNotIn("f1_bootstrap_dsn", block)
            self.assertNotIn("f1_migration_dsn", block)

    def test_provider_keys_follow_least_privilege(self) -> None:
        source = (self.ROOT / "infra/f1/docker-compose.yml").read_text(
            encoding="utf-8"
        )
        api = source.split("  api:\n", 1)[1].split("  worker:\n", 1)[0]
        worker = source.split("  worker:\n", 1)[1].split("  dispatcher:\n", 1)[0]
        for name in ("deepseek_api_key", "ragflow_api_key", "ark_api_key"):
            self.assertIn(f"/{name}:/run/secrets/provider/{name}:ro", api)
        self.assertNotIn("deepseek_api_key", worker)
        for name in ("ragflow_api_key", "ark_api_key"):
            self.assertIn(f"/{name}:/run/secrets/provider/{name}:ro", worker)

    def test_worker_tenant_sessions_include_task_and_lease(self) -> None:
        base = self.ROOT / "src/platform_foundation/f1"
        indexing = (base / "indexing.py").read_text(encoding="utf-8")
        pipeline = (base / "worker_pipeline.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(indexing.count("task_id=task_id"), 2)
        self.assertGreaterEqual(indexing.count("lease_token=lease_token"), 2)
        self.assertIn("task_id=claim.task_id", pipeline)
        self.assertIn("lease_token=claim.lease_token", pipeline)


if __name__ == "__main__":
    unittest.main()
