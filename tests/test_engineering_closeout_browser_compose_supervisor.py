from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from infra.f1 import local_browser_compose_supervisor as supervisor


PROJECT_ID = "11111111-1111-4111-8111-111111111111"
COMPOSE_PROJECT = "anhuan-closeout-123456abcdef"
PROBE = "a" * 24


class FakeProcess:
    def __init__(self, returncode: int) -> None:
        self._returncode = returncode

    def wait(self) -> int:
        return self._returncode


class SupervisorFixture:
    def __init__(self, parent: Path) -> None:
        parent = parent.resolve()
        self.root = parent / "安环项目"
        self.root.mkdir(mode=0o700)
        self.state_directory = self.root / ".local"
        self.state_directory.mkdir(mode=0o700)
        self.home = self.state_directory / "home"
        self.home.mkdir(mode=0o700)
        (self.home / ".docker").mkdir(mode=0o700)
        self.temporary = self.state_directory / "tmp"
        self.temporary.mkdir(mode=0o700)
        self.control = self.temporary / f"pwa-update-{PROBE}"
        self.control.mkdir(mode=0o700)
        self.secrets = self.state_directory / "secrets"
        self.secrets.mkdir(mode=0o700)
        infrastructure = self.root / "infra" / "f1"
        infrastructure.mkdir(mode=0o700, parents=True)
        self.compose = infrastructure / "docker-compose.local.yml"
        self.compose.write_text("services:\n  web: {}\n", encoding="ascii")
        self.compose.chmod(0o600)
        self.docker = parent / "docker"
        self.docker.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        self.docker.chmod(0o700)
        self.state = self.state_directory / "state.json"
        self.state_payload = {
            "compose_project": COMPOSE_PROJECT,
            "database": "anhuan_closeout_" + "b" * 24,
            "project_id": PROJECT_ID,
            "runtime_image": "anhuan-closeout-runtime:123456abcdef",
            "schema": "anhuan-engineering-local-v1",
            "web_image": "anhuan-closeout-web:123456abcdef",
            "web_port": 54321,
        }
        self._write_state()
        self.env_file = self.state_directory / "compose.env"
        values = {
            "LOCAL_DATABASE": self.state_payload["database"],
            "LOCAL_GID": os.getegid(),
            "LOCAL_PROJECT_ID": PROJECT_ID,
            "LOCAL_RUNTIME_IMAGE": self.state_payload["runtime_image"],
            "LOCAL_SECRETS_DIR": str(self.secrets),
            "LOCAL_UID": os.geteuid(),
            "LOCAL_WEB_IMAGE": self.state_payload["web_image"],
            "LOCAL_WEB_ORIGIN": "http://127.0.0.1:54321",
            "LOCAL_WEB_PORT": 54321,
        }
        self.env_file.write_text(
            "".join(f"{key}={values[key]}\n" for key in sorted(values)),
            encoding="utf-8",
        )
        self.env_file.chmod(0o600)

    def _write_state(self) -> None:
        self.state.write_text(
            json.dumps(self.state_payload, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="ascii",
        )
        self.state.chmod(0o600)

    def argv(self, kind: str) -> list[str]:
        return [
            "--kind",
            kind,
            "--probe",
            PROBE,
            "--project-id",
            PROJECT_ID,
            "--compose-project",
            COMPOSE_PROJECT,
            "--state-file",
            str(self.state),
            "--compose-file",
            str(self.compose),
            "--env-file",
            str(self.env_file),
            "--control-directory",
            str(self.control),
            "--docker",
            str(self.docker),
        ]

    def environment(self, kind: str) -> dict[str, str]:
        base = {
            "DOCKER_CONFIG": str(self.home / ".docker"),
            "DOCKER_HOST": "unix:///var/run/docker.sock",
            "HOME": str(self.home),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "NO_PROXY": supervisor.LOCAL_NO_PROXY,
            "PATH": supervisor.LOCAL_PATH,
            "TMPDIR": str(self.temporary),
            "no_proxy": supervisor.LOCAL_NO_PROXY,
        }
        if kind == "build_b":
            base["LOCAL_PWA_UPDATE_PROBE"] = PROBE
            base["LOCAL_WEB_IMAGE"] = (
                f"{self.state_payload['web_image']}-pwa-update-{PROBE}"
            )
        elif kind == "swap_b":
            base["LOCAL_WEB_IMAGE"] = (
                f"{self.state_payload['web_image']}-pwa-update-{PROBE}"
            )
        else:
            base["LOCAL_WEB_IMAGE"] = str(self.state_payload["web_image"])
        return base


class EngineeringCloseoutBrowserComposeSupervisorTests(unittest.TestCase):
    def test_each_kind_builds_only_the_fixed_web_command_and_environment(self) -> None:
        tails = {
            "build_b": ["build", "web"],
            "swap_b": [
                "up",
                "-d",
                "--no-deps",
                "--force-recreate",
                "--wait",
                "--wait-timeout",
                "180",
                "web",
            ],
            "restore_a": [
                "up",
                "-d",
                "--no-deps",
                "--force-recreate",
                "--wait",
                "--wait-timeout",
                "180",
                "web",
            ],
        }
        for kind, tail in tails.items():
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as raw:
                fixture = SupervisorFixture(Path(raw))
                observed: dict[str, object] = {}

                def popen(arguments, **kwargs):
                    observed["arguments"] = arguments
                    observed["kwargs"] = kwargs
                    return FakeProcess(0)

                environment = fixture.environment(kind)
                environment["SHOULD_NOT_REACH_DOCKER"] = "secret"
                with mock.patch.object(
                    supervisor.subprocess, "Popen", side_effect=popen
                ):
                    self.assertEqual(
                        supervisor.main(fixture.argv(kind), environ=environment), 0
                    )

                prefix = [
                    str(fixture.docker),
                    "compose",
                    "--ansi",
                    "never",
                    "--project-name",
                    COMPOSE_PROJECT,
                    "--env-file",
                    str(fixture.env_file),
                    "-f",
                    str(fixture.compose),
                    "--profile",
                    "ops",
                ]
                self.assertEqual(observed["arguments"], prefix + tail)
                kwargs = observed["kwargs"]
                self.assertEqual(kwargs["cwd"], fixture.root)
                self.assertEqual(kwargs["env"], fixture.environment(kind))
                self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
                self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
                self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
                self.assertNotIn("start_new_session", kwargs)

                receipt = fixture.control / supervisor.RECEIPT_BASENAME
                document = json.loads(receipt.read_text(encoding="ascii"))
                self.assertEqual(set(document), supervisor.RECEIPT_FIELDS)
                self.assertEqual(document["kind"], kind)
                self.assertEqual(document["probe"], PROBE)
                self.assertEqual(document["project_id"], PROJECT_ID)
                self.assertEqual(document["compose_project"], COMPOSE_PROJECT)
                self.assertEqual(document["service"], "web")
                self.assertEqual(document["exit_code"], 0)
                self.assertEqual(
                    document["state_sha256"],
                    hashlib.sha256(fixture.state.read_bytes()).hexdigest(),
                )
                self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)
                raw_receipt = receipt.read_text(encoding="ascii")
                self.assertNotIn(str(fixture.root), raw_receipt)
                self.assertNotIn("secret", raw_receipt)
                self.assertEqual(
                    raw_receipt,
                    json.dumps(document, sort_keys=True, separators=(",", ":"))
                    + "\n",
                )

    def test_rejects_arbitrary_kind_extra_arguments_and_path_drift(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = SupervisorFixture(Path(raw))
            cases = [
                fixture.argv("shell"),
                fixture.argv("build_b") + ["--command", "rm"],
                [
                    *(fixture.argv("build_b")[:-2]),
                    "--docker",
                    str(fixture.docker),
                    "unexpected",
                ],
            ]
            drift = fixture.argv("build_b")
            drift[drift.index("--compose-file") + 1] = str(
                fixture.root / "other.yml"
            )
            cases.append(drift)
            for argv in cases:
                with self.subTest(argv=argv), mock.patch.object(
                    supervisor.subprocess, "Popen"
                ) as popen:
                    self.assertEqual(
                        supervisor.main(
                            argv, environ=fixture.environment("build_b")
                        ),
                        supervisor.CONTRACT_ERROR_EXIT,
                    )
                    popen.assert_not_called()

    def test_rejects_project_state_env_and_override_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = SupervisorFixture(Path(raw))
            wrong_project = fixture.argv("build_b")
            wrong_project[wrong_project.index("--project-id") + 1] = (
                "22222222-2222-4222-8222-222222222222"
            )
            wrong_override = fixture.environment("build_b")
            wrong_override["LOCAL_WEB_IMAGE"] = str(
                fixture.state_payload["web_image"]
            )
            bad_env = fixture.env_file.read_text(encoding="utf-8").replace(
                "LOCAL_WEB_PORT=54321", "LOCAL_WEB_PORT=54322"
            )
            cases = [
                (wrong_project, fixture.environment("build_b")),
                (fixture.argv("build_b"), wrong_override),
            ]
            for argv, environment in cases:
                with self.subTest(argv=argv), mock.patch.object(
                    supervisor.subprocess, "Popen"
                ) as popen:
                    self.assertEqual(
                        supervisor.main(argv, environ=environment),
                        supervisor.CONTRACT_ERROR_EXIT,
                    )
                    popen.assert_not_called()
            fixture.env_file.write_text(bad_env, encoding="utf-8")
            fixture.env_file.chmod(0o600)
            with mock.patch.object(supervisor.subprocess, "Popen") as popen:
                self.assertEqual(
                    supervisor.main(
                        fixture.argv("build_b"),
                        environ=fixture.environment("build_b"),
                    ),
                    supervisor.CONTRACT_ERROR_EXIT,
                )
                popen.assert_not_called()

    def test_existing_receipt_or_stage_refuses_to_run_compose(self) -> None:
        for basename in (
            supervisor.RECEIPT_BASENAME,
            supervisor.RECEIPT_STAGING_BASENAME,
        ):
            with self.subTest(basename=basename), tempfile.TemporaryDirectory() as raw:
                fixture = SupervisorFixture(Path(raw))
                path = fixture.control / basename
                path.write_bytes(b"foreign")
                path.chmod(0o600)
                with mock.patch.object(supervisor.subprocess, "Popen") as popen:
                    self.assertEqual(
                        supervisor.main(
                            fixture.argv("build_b"),
                            environ=fixture.environment("build_b"),
                        ),
                        supervisor.CONTRACT_ERROR_EXIT,
                    )
                    popen.assert_not_called()

    def test_nonzero_and_signaled_child_codes_are_fixed_in_receipt(self) -> None:
        for observed, expected in ((17, 17), (-15, 143), (999, 125)):
            with self.subTest(observed=observed), tempfile.TemporaryDirectory() as raw:
                fixture = SupervisorFixture(Path(raw))
                with mock.patch.object(
                    supervisor.subprocess,
                    "Popen",
                    return_value=FakeProcess(observed),
                ):
                    self.assertEqual(
                        supervisor.main(
                            fixture.argv("restore_a"),
                            environ=fixture.environment("restore_a"),
                        ),
                        0,
                    )
                receipt = json.loads(
                    (fixture.control / supervisor.RECEIPT_BASENAME).read_text(
                        encoding="ascii"
                    )
                )
                self.assertEqual(receipt["exit_code"], expected)

    def test_cli_is_quiet_on_contract_failure(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(supervisor.main(["--kind", "bad"], environ={}), 64)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")

    def test_supervisor_survives_sigkill_of_its_launching_parent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            fixture = SupervisorFixture(Path(raw))
            fixture.docker.write_text(
                f"#!{sys.executable}\n"
                "import os, pathlib, time\n"
                "root = pathlib.Path(os.environ['TMPDIR'])\n"
                "(root / 'fake-docker-started').write_text('started')\n"
                "while not (root / 'release-fake-docker').exists():\n"
                "    time.sleep(0.01)\n"
                "raise SystemExit(7)\n",
                encoding="utf-8",
            )
            fixture.docker.chmod(0o700)
            supervisor_pid_file = Path(raw).resolve() / "supervisor.pid"
            command = [
                sys.executable,
                str(Path(supervisor.__file__).resolve()),
                *fixture.argv("build_b"),
            ]
            helper_source = (
                "import json,pathlib,subprocess,sys,time\n"
                "command=json.loads(sys.argv[1])\n"
                "environment=json.loads(sys.argv[2])\n"
                "child=subprocess.Popen(command,env=environment,"
                "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,"
                "stderr=subprocess.DEVNULL,start_new_session=True)\n"
                "path=pathlib.Path(sys.argv[3])\n"
                "path.write_text(str(child.pid),encoding='ascii')\n"
                "path.chmod(0o600)\n"
                "time.sleep(60)\n"
            )
            helper = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    helper_source,
                    json.dumps(command),
                    json.dumps(fixture.environment("build_b")),
                    str(supervisor_pid_file),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            supervisor_pid: int | None = None
            receipt = fixture.control / supervisor.RECEIPT_BASENAME
            try:
                deadline = time.monotonic() + 5
                started = fixture.temporary / "fake-docker-started"
                while time.monotonic() < deadline:
                    if supervisor_pid_file.exists() and started.exists():
                        supervisor_pid = int(
                            supervisor_pid_file.read_text(encoding="ascii")
                        )
                        break
                    time.sleep(0.01)
                self.assertIsNotNone(supervisor_pid)
                os.kill(helper.pid, signal.SIGKILL)
                helper.wait(timeout=5)
                (fixture.temporary / "release-fake-docker").touch(mode=0o600)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not receipt.exists():
                    time.sleep(0.01)
                self.assertTrue(receipt.exists())
                document = json.loads(receipt.read_text(encoding="ascii"))
                self.assertEqual(document["exit_code"], 7)
                self.assertEqual(document["kind"], "build_b")
            finally:
                (fixture.temporary / "release-fake-docker").touch(
                    mode=0o600, exist_ok=True
                )
                if helper.poll() is None:
                    helper.kill()
                    helper.wait(timeout=5)
                if supervisor_pid is not None and not receipt.exists():
                    with contextlib.suppress(ProcessLookupError):
                        os.kill(supervisor_pid, signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
