from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_localctl():
    path = ROOT / "scripts/localctl"
    loader = importlib.machinery.SourceFileLoader(
        "engineering_closeout_reverse_localctl", str(path)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise AssertionError("localctl import unavailable")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


localctl = load_localctl()


class FakeResponse:
    def __init__(self, status: int, payload: dict[str, object]):
        self.status = status
        self.headers = {"Cache-Control": "no-store"}
        self._body = json.dumps(payload, separators=(",", ":")).encode()

    def read(self, _limit: int) -> bytes:
        return self._body

    def close(self) -> None:
        return None


class EngineeringCloseoutReverseBoundaryTests(unittest.TestCase):
    STATE = {
        "compose_project": "anhuan-closeout-123456abcdef",
        "project_id": "d30c42bc-d5a3-48db-a40b-ab88748055f8",
        "web_port": 54321,
    }

    def test_readiness_observation_accepts_body_free_dependency_503(self) -> None:
        payload = {
            "status": "unavailable",
            "components": {
                "clamd": True,
                "database": True,
                "minio": False,
                "oidc": True,
                "redis": True,
            },
        }
        with mock.patch.object(
            localctl.urllib.request,
            "urlopen",
            return_value=FakeResponse(503, payload),
        ):
            self.assertEqual(
                localctl._api_readiness_observation("http://127.0.0.1/readyz"),
                (503, payload),
            )
            self.assertFalse(
                localctl._api_readiness_ready("http://127.0.0.1/readyz")
            )

    def test_secret_permission_probe_always_restores_0600(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp_dir = Path(raw) / "tmp"
            tmp_dir.mkdir(mode=0o700)
            secrets_dir = Path(raw) / "secrets"
            secrets_dir.mkdir(mode=0o700)
            for name in localctl.ALL_SECRET_NAMES:
                body = b"x" * (32 if name == "f0i_key" else 16)
                path = secrets_dir / name
                path.write_bytes(body)
                path.chmod(0o600)
            output = io.StringIO()
            with (
                mock.patch.object(localctl, "SECRETS_DIR", secrets_dir),
                mock.patch.object(localctl, "TMP_DIR", tmp_dir),
                mock.patch.object(localctl, "_health"),
                mock.patch.object(
                    localctl,
                    "_probe_dependency_outage",
                    side_effect=[
                        {"minio_health_red_count": 1, "minio_recovery_count": 1},
                        {"clamd_health_red_count": 1, "clamd_recovery_count": 1},
                    ],
                ),
                contextlib.redirect_stdout(output),
            ):
                localctl._dependency_verify(self.STATE)
            mode = stat.S_IMODE((secrets_dir / "f1_qa_key").stat().st_mode)
            self.assertEqual(mode, 0o600)
            self.assertEqual(list(tmp_dir.iterdir()), [])
            lines = output.getvalue().splitlines()
            self.assertEqual(lines[1], "LOCAL_DEPENDENCY_BOUNDARIES_OK")
            self.assertEqual(json.loads(lines[0])["secret_0644_rejection_count"], 1)

    def test_dependency_probe_stops_and_restarts_only_exact_container_id(self) -> None:
        container_id = "a" * 64
        commands: list[list[str]] = []

        def run(arguments, **_kwargs):
            commands.append(arguments)
            return mock.Mock(returncode=0, stdout="", stderr="")

        payload = {
            "status": "unavailable",
            "components": {
                "clamd": True,
                "database": True,
                "minio": False,
                "oidc": True,
                "redis": True,
            },
        }
        with tempfile.TemporaryDirectory() as raw:
            journal = Path(raw) / "reverse-recovery.json"
            with (
                mock.patch.object(localctl, "REVERSE_JOURNAL", journal),
                mock.patch.object(
                    localctl,
                    "_service_container_payload",
                    return_value=(container_id, {}),
                ),
                mock.patch.object(
                    localctl, "_docker", return_value="/trusted/docker"
                ),
                mock.patch.object(localctl, "_run", side_effect=run),
                mock.patch.object(
                    localctl,
                    "_health",
                    side_effect=localctl.LocalError("LOCAL_HEALTH_RED"),
                ),
                mock.patch.object(
                    localctl,
                    "_api_readiness_observation",
                    return_value=(503, payload),
                ),
                mock.patch.object(
                    localctl, "_core_containers_ready", return_value=False
                ),
                mock.patch.object(localctl, "_wait_dependency_recovered"),
            ):
                observed = localctl._probe_dependency_outage(self.STATE, "minio")
            self.assertFalse(journal.exists())
        self.assertEqual(observed["minio_health_red_count"], 1)
        self.assertEqual(
            commands,
            [
                ["/trusted/docker", "stop", "--time", "30", container_id],
                ["/trusted/docker", "start", container_id],
            ],
        )

    def test_dependency_journal_recovers_exact_id_and_rejects_drift(self) -> None:
        expected_id = "a" * 64
        payload = {
            "kind": "dependency",
            "project_id": self.STATE["project_id"],
            "compose_project": self.STATE["compose_project"],
            "service": "clamd",
            "container_id": expected_id,
        }
        with tempfile.TemporaryDirectory() as raw:
            journal = Path(raw) / "reverse-recovery.json"
            commands: list[list[str]] = []

            def run(arguments, **_kwargs):
                commands.append(arguments)
                return mock.Mock(returncode=0, stdout="", stderr="")

            with (
                mock.patch.object(localctl, "REVERSE_JOURNAL", journal),
                mock.patch.object(
                    localctl,
                    "_service_container_payload",
                    return_value=(expected_id, {}),
                ),
                mock.patch.object(localctl, "_docker", return_value="/trusted/docker"),
                mock.patch.object(localctl, "_run", side_effect=run),
                mock.patch.object(localctl, "_wait_dependency_recovered"),
            ):
                localctl._write_reverse_journal(payload)
                localctl._recover_reverse_journal(self.STATE)
            self.assertEqual(
                commands, [["/trusted/docker", "start", expected_id]]
            )
            self.assertFalse(journal.exists())

            commands.clear()
            with (
                mock.patch.object(localctl, "REVERSE_JOURNAL", journal),
                mock.patch.object(
                    localctl,
                    "_service_container_payload",
                    return_value=("b" * 64, {}),
                ),
                mock.patch.object(localctl, "_run", side_effect=run),
            ):
                localctl._write_reverse_journal(payload)
                with self.assertRaisesRegex(
                    localctl.LocalError,
                    "LOCAL_REVERSE_RECOVERY_IDENTITY_MISMATCH",
                ):
                    localctl._recover_reverse_journal(self.STATE)
            self.assertEqual(commands, [])
            self.assertTrue(journal.exists())

    def test_volume_only_sentinel_journal_is_cleaned(self) -> None:
        nonce = "b" * 24
        volume_name = f"anhuan-foreign-sentinel-{nonce}"
        image_id = "sha256:" + "c" * 64
        payload = {
            "kind": "sentinel",
            "project_id": self.STATE["project_id"],
            "compose_project": self.STATE["compose_project"],
            "nonce": nonce,
            "container_name": volume_name,
            "volume_name": volume_name,
            "image_id": image_id,
        }
        commands: list[list[str]] = []

        def run(arguments, **_kwargs):
            commands.append(arguments)
            if arguments[1] == "inspect":
                return mock.Mock(returncode=1, stdout="", stderr="")
            if arguments[1:3] == ["volume", "inspect"]:
                body = [{"Labels": {
                    "io.anhuan.reverse-sentinel": nonce,
                    "io.anhuan.scope": "foreign-sentinel",
                }}]
                return mock.Mock(returncode=0, stdout=json.dumps(body), stderr="")
            if arguments[1:3] == ["volume", "rm"]:
                return mock.Mock(returncode=0, stdout=volume_name + "\n", stderr="")
            if arguments[1:4] in (["ps", "-a", "-q"], ["volume", "ls", "-q"]):
                return mock.Mock(returncode=0, stdout="", stderr="")
            raise AssertionError(arguments)

        with tempfile.TemporaryDirectory() as raw:
            journal = Path(raw) / "reverse-recovery.json"
            with (
                mock.patch.object(localctl, "REVERSE_JOURNAL", journal),
                mock.patch.object(localctl, "_docker", return_value="/trusted/docker"),
                mock.patch.object(localctl, "_run", side_effect=run),
            ):
                localctl._write_reverse_journal(payload)
                localctl._recover_reverse_journal(self.STATE)
            self.assertFalse(journal.exists())
        self.assertIn(
            ["/trusted/docker", "volume", "rm", volume_name], commands
        )

    def test_sentinel_cleanup_attempts_volume_after_container_failure(self) -> None:
        nonce = "d" * 24
        container_id = "e" * 64
        name = f"anhuan-foreign-sentinel-{nonce}"
        image_id = "sha256:" + "f" * 64
        payload = {
            "kind": "sentinel",
            "project_id": self.STATE["project_id"],
            "compose_project": self.STATE["compose_project"],
            "nonce": nonce,
            "container_name": name,
            "volume_name": name,
            "image_id": image_id,
        }
        commands: list[list[str]] = []

        def run(arguments, **_kwargs):
            commands.append(arguments)
            if arguments[1] == "inspect":
                body = [{
                    "Id": container_id,
                    "Image": image_id,
                    "Config": {"Labels": {
                        "io.anhuan.reverse-sentinel": nonce,
                        "io.anhuan.scope": "foreign-sentinel",
                    }},
                    "Mounts": [{
                        "Type": "volume", "Name": name, "Destination": "/sentinel"
                    }],
                }]
                return mock.Mock(returncode=0, stdout=json.dumps(body), stderr="")
            if arguments[1:3] == ["volume", "inspect"]:
                body = [{"Labels": {
                    "io.anhuan.reverse-sentinel": nonce,
                    "io.anhuan.scope": "foreign-sentinel",
                }}]
                return mock.Mock(returncode=0, stdout=json.dumps(body), stderr="")
            if arguments[1:3] == ["rm", "-f"]:
                return mock.Mock(returncode=1, stdout="", stderr="")
            if arguments[1:3] == ["volume", "rm"]:
                return mock.Mock(returncode=1, stdout="", stderr="")
            if arguments[1:4] == ["ps", "-a", "-q"]:
                return mock.Mock(returncode=0, stdout=container_id + "\n", stderr="")
            if arguments[1:4] == ["volume", "ls", "-q"]:
                return mock.Mock(returncode=0, stdout=name + "\n", stderr="")
            raise AssertionError(arguments)

        with tempfile.TemporaryDirectory() as raw:
            journal = Path(raw) / "reverse-recovery.json"
            with (
                mock.patch.object(localctl, "REVERSE_JOURNAL", journal),
                mock.patch.object(localctl, "_docker", return_value="/trusted/docker"),
                mock.patch.object(localctl, "_run", side_effect=run),
            ):
                localctl._write_reverse_journal(payload)
                with self.assertRaisesRegex(
                    localctl.LocalError,
                    "LOCAL_REVERSE_SENTINEL_CLEANUP_FAILED",
                ):
                    localctl._recover_reverse_journal(self.STATE)
            self.assertTrue(journal.exists())
        self.assertIn(["/trusted/docker", "rm", "-f", container_id], commands)
        self.assertIn(["/trusted/docker", "volume", "rm", name], commands)

    def test_wrong_mode_journal_fails_without_docker_action(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            journal = Path(raw) / "reverse-recovery.json"
            journal.write_text("{}", encoding="ascii")
            journal.chmod(0o644)
            with (
                mock.patch.object(localctl, "REVERSE_JOURNAL", journal),
                mock.patch.object(localctl, "_run") as run,
            ):
                with self.assertRaisesRegex(
                    localctl.LocalError, "LOCAL_FILE_PERMISSIONS_INVALID"
                ):
                    localctl._recover_reverse_journal(self.STATE)
            run.assert_not_called()

    def test_foreign_sentinel_survives_project_reset_then_is_cleaned(self) -> None:
        nonce = "b" * 24
        container_id = "c" * 64
        image_id = localctl.REVERSE_SENTINEL_IMAGE_ID
        volume_name = f"anhuan-foreign-sentinel-{nonce}"
        sentinel_state = dict(self.STATE)
        state = {"container": False, "volume": False, "reset": 0}

        def run(arguments, **_kwargs):
            if arguments[1:3] == ["image", "inspect"]:
                payload = [{
                    "Id": image_id,
                    "RepoDigests": [
                        localctl.REVERSE_SENTINEL_IMAGE.replace(
                            "python:3.11-slim@", "python@"
                        )
                    ],
                    "Config": {"Labels": None, "Volumes": None},
                }]
                return mock.Mock(
                    returncode=0, stdout=json.dumps(payload), stderr=""
                )
            if arguments[1:4] == ["ps", "-a", "-q"]:
                return mock.Mock(
                    returncode=0,
                    stdout=(container_id + "\n") if state["container"] else "",
                    stderr="",
                )
            if arguments[1:4] == ["volume", "ls", "-q"]:
                return mock.Mock(
                    returncode=0,
                    stdout=(volume_name + "\n") if state["volume"] else "",
                    stderr="",
                )
            if arguments[1:3] == ["volume", "create"]:
                state["volume"] = True
                return mock.Mock(returncode=0, stdout=volume_name + "\n", stderr="")
            if arguments[1] == "create":
                state["container"] = True
                return mock.Mock(returncode=0, stdout=container_id + "\n", stderr="")
            if arguments[1:3] == ["rm", "-f"]:
                state["container"] = False
                return mock.Mock(returncode=0, stdout=container_id + "\n", stderr="")
            if arguments[1:3] == ["volume", "rm"]:
                state["volume"] = False
                return mock.Mock(returncode=0, stdout=volume_name + "\n", stderr="")
            if arguments[1] == "inspect":
                if not state["container"]:
                    return mock.Mock(returncode=1, stdout="", stderr="")
                payload = [{
                    "Id": container_id,
                    "Image": image_id,
                    "Config": {"Labels": {
                        "io.anhuan.reverse-sentinel": nonce,
                        "io.anhuan.scope": "foreign-sentinel",
                    }},
                    "Mounts": [{
                        "Type": "volume",
                        "Name": volume_name,
                        "Destination": "/sentinel",
                    }],
                }]
                return mock.Mock(returncode=0, stdout=json.dumps(payload), stderr="")
            if arguments[1:3] == ["volume", "inspect"]:
                if not state["volume"]:
                    return mock.Mock(returncode=1, stdout="", stderr="")
                payload = [{"Labels": {
                    "io.anhuan.reverse-sentinel": nonce,
                    "io.anhuan.scope": "foreign-sentinel",
                }}]
                return mock.Mock(returncode=0, stdout=json.dumps(payload), stderr="")
            raise AssertionError(arguments)

        def reset(_state, *, confirmed):
            self.assertTrue(confirmed)
            state["reset"] += 1

        output = io.StringIO()
        with tempfile.TemporaryDirectory() as raw:
            journal = Path(raw) / "reverse-recovery.json"
            with (
                mock.patch.object(localctl, "REVERSE_JOURNAL", journal),
                mock.patch.object(localctl, "_assert_resource_labels"),
                mock.patch.object(localctl, "_run", side_effect=run),
                mock.patch.object(localctl, "_docker", return_value="/trusted/docker"),
                mock.patch.object(localctl.secrets, "token_hex", return_value=nonce),
                mock.patch.object(localctl, "_reset", side_effect=reset),
                contextlib.redirect_stdout(output),
            ):
                localctl._reset_with_foreign_sentinel(
                    sentinel_state, confirmed=True
                )
            self.assertFalse(journal.exists())
        self.assertEqual(state, {"container": False, "volume": False, "reset": 1})
        lines = output.getvalue().splitlines()
        self.assertEqual(lines[1], "LOCAL_FOREIGN_SENTINEL_OK")
        self.assertEqual(json.loads(lines[0])["foreign_cleanup_residual_count"], 0)

    def test_cli_keeps_destructive_sentinel_proof_explicit(self) -> None:
        source = (ROOT / "scripts/localctl").read_text(encoding="utf-8")
        self.assertIn('subparsers.add_parser("dependency-verify")', source)
        self.assertIn('reset.add_argument("--prove-foreign-sentinel"', source)
        self.assertIn("_reset_with_foreign_sentinel", source)
        self.assertNotIn("docker system prune", source)


if __name__ == "__main__":
    unittest.main()
