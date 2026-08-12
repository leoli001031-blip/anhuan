"""Fast, database-free contracts for the live local verifier."""
from __future__ import annotations

import ast
import dataclasses
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from infra.f1.local_verify import (
    EXPECTED_BINDINGS,
    EXPECTED_ENTERPRISES,
    EXPECTED_RUNTIME_ROLES,
    P2_P7_TABLES,
    Snapshot,
    VerificationCounts,
    VerificationError,
    render_success,
    verify_snapshot,
)


DATABASE = "anhuan_engineering_fixture"
ROOT = Path(__file__).resolve().parents[1]
LOCAL_COMPOSE = ROOT / "infra/f1/docker-compose.local.yml"
LOCALCTL = ROOT / "scripts/localctl"


def _load_localctl():
    loader = importlib.machinery.SourceFileLoader(
        "engineering_closeout_localctl_verify", str(LOCALCTL)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise AssertionError("localctl spec unavailable")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _compose_service(source: str, name: str) -> str:
    lines = source.splitlines()
    marker = f"  {name}:"
    try:
        start = lines.index(marker)
    except ValueError:
        raise AssertionError(f"missing compose service: {name}") from None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if re.fullmatch(r"  [a-z0-9_-]+:", lines[index]):
            end = index
            break
        if lines[index] and not lines[index].startswith(" "):
            end = index
            break
    return "\n".join(lines[start:end])


def _function_source(path: Path, name: str) -> str:
    tree = ast.parse(_source(path), filename=str(path))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"missing function: {path}:{name}")


def _valid_snapshot() -> Snapshot:
    return Snapshot(
        identity=("f0d_bootstrap", "f0d_bootstrap", DATABASE),
        f0_heads=("f0d_0006",),
        f1_heads=("f1_0011",),
        rls_rows=tuple((name, True, True) for name in P2_P7_TABLES),
        runtime_roles=EXPECTED_RUNTIME_ROLES,
        runtime_role_memberships=0,
        enterprises=EXPECTED_ENTERPRISES,
        bindings=EXPECTED_BINDINGS,
    )


def _replace(snapshot: Snapshot, **changes: object) -> Snapshot:
    return dataclasses.replace(snapshot, **changes)


class LocalVerifierContracts(unittest.TestCase):
    def test_existing_state_commands_revalidate_the_complete_secret_set(self) -> None:
        localctl = _load_localctl()
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw) / "secrets"
            directory.mkdir(mode=0o700)
            with mock.patch.object(localctl, "SECRETS_DIR", directory):
                for name in localctl.ALL_SECRET_NAMES:
                    body = b"x" * (32 if name == "f0i_key" else 24)
                    path = directory / name
                    path.write_bytes(body)
                    path.chmod(0o600)
                localctl._validate_secret_set()
                rejected = directory / "f1_api_password"
                rejected.chmod(0o644)
                with self.assertRaises(localctl.LocalError) as raised:
                    localctl._validate_secret_set()
                self.assertEqual(
                    str(raised.exception), "LOCAL_FILE_PERMISSIONS_INVALID"
                )

        main = _function_source(LOCALCTL, "main")
        self.assertIn("_validate_existing_layout()", main)
        self.assertIn("arguments.command not in {'stop', 'reset'}", main)

    def test_health_requires_body_free_readiness_and_exact_catalog(self) -> None:
        health = _function_source(LOCALCTL, "_health")
        self.assertIn("_api_readiness_ready", health)
        self.assertIn("_catalog_ready", health)
        self.assertIn("'database_catalog'", health)
        catalog = _function_source(LOCALCTL, "_catalog_ready")
        self.assertIn("EXPECTED_VERIFICATION_METRICS", catalog)
        self.assertIn("LOCAL_VERIFY_OK", catalog)

    def test_compose_exposes_a_bootstrap_only_one_shot_verifier(self) -> None:
        compose = _source(LOCAL_COMPOSE)
        service = _compose_service(compose, "verifier")
        self.assertIn(
            'command: ["python", "-B", "/app/infra/f1/local_verify.py"]',
            service,
        )
        self.assertIn("dockerfile: infra/f1/local.Dockerfile", service)
        self.assertIn("environment: *runtime_environment", service)
        self.assertIn("- seed_secrets:/run/secrets/f1:ro", service)
        self.assertIn("postgres:", service)
        self.assertIn("condition: service_healthy", service)
        self.assertIn("profiles: [ops]", service)
        self.assertIn('restart: "no"', service)
        self.assertNotIn("ports:", service)
        self.assertNotIn("api_secrets", service)
        self.assertNotIn("worker_secrets", service)

    def test_localctl_verify_runs_one_shot_and_whitelists_its_output(self) -> None:
        verify = _function_source(LOCALCTL, "_verify")
        self.assertIn("_sync_secrets(state)", verify)
        self.assertIn("'build', 'verifier'", verify)
        self.assertIn("'up', '-d', '--wait'", verify)
        self.assertIn("'postgres'", verify)
        self.assertIn("_compose_contract(state, 'verifier'", verify)
        self.assertIn("LOCAL_VERIFY_OK", verify)
        self.assertIn("LOCAL_VERIFY_OUTPUT_INVALID", verify)
        self.assertIn("json.loads", verify)
        self.assertNotIn("result.stderr", verify)
        self.assertIn("_compose_contract(state, 'business-verifier'", verify)
        self.assertIn("LOCAL_BUSINESS_VERIFY_OK", verify)
        self.assertIn("LOCAL_BUSINESS_OUTPUT_INVALID", verify)
        self.assertIn("_compose_contract(state, 'ingestion-verifier'", verify)
        self.assertIn("LOCAL_INGESTION_VERIFY_OK", verify)
        self.assertIn("LOCAL_INGESTION_OUTPUT_INVALID", verify)

    def test_verifier_failure_reason_is_explicitly_allowlisted(self) -> None:
        localctl = _load_localctl()
        allowed = localctl.VERIFIER_FAILURE_REASONS["business-verifier"]
        warning = "warning with https://example.invalid/private\n"
        reason = "LOCAL_BUSINESS_RESTART_PERSISTENCE_FAILED"
        self.assertEqual(
            localctl._contract_failure_reason(warning + reason + "\n", allowed),
            reason,
        )
        self.assertIsNone(
            localctl._contract_failure_reason(
                warning + "LOCAL_BUSINESS_UNKNOWN_FAILURE\n", allowed
            )
        )
        self.assertIsNone(
            localctl._contract_failure_reason(
                reason + "\n" + reason + "\n", allowed
            )
        )
        self.assertEqual(
            set(localctl.VERIFIER_FAILURE_REASONS),
            {
                "targeted-tests",
                "verifier",
                "migration-atomicity",
                "business-verifier",
                "ingestion-verifier",
            },
        )

        main = _function_source(LOCALCTL, "main")
        self.assertIn("arguments.command == 'verify'", main)
        self.assertIn("_verify(state)", main)

    def test_localctl_browser_verify_uses_real_b_image_and_restores_a(self) -> None:
        browser = _function_source(LOCALCTL, "_browser_verify")
        for token in (
            "--pwa-update-control",
            "PWA_BROWSER_READY",
            "PWA_IMAGE_READY",
            "MINIO_FAULT_READY",
            "MINIO_503_OBSERVED",
            "CLAMD_FAULT_READY",
            "CLAMD_UNAVAILABLE_OBSERVED",
            "_orchestrate_browser_dependency_fault",
            "_assert_web_uses_image(state, image_b)",
            "_write_browser_recovery_journal(state, probe)",
            "_update_browser_recovery_pid(state, probe, browser.pid)",
            "start_new_session=True",
            "_recover_browser_recovery_journal(state)",
            "_runtime_log_metrics(state)",
            "print(log_tag)",
        ):
            self.assertIn(token, browser)
        self.assertIn(
            "LOCAL_PWA_OS_BLOCKED_BY_BROWSER_AUTOMATION_BOUNDARY",
            browser,
        )
        self.assertNotIn("--install-pwa", browser)
        self.assertNotIn("_orchestrate_browser_pwa_offline_reopen", browser)
        supervisor_command = _function_source(
            LOCALCTL, "_browser_compose_supervisor_command"
        )
        self.assertIn("LOCAL_PWA_UPDATE_PROBE", supervisor_command)
        supervisor = _source(
            ROOT / "infra/f1/local_browser_compose_supervisor.py"
        )
        self.assertIn('"--force-recreate"', supervisor)
        supervisor_run = _function_source(
            ROOT / "infra/f1/local_browser_compose_supervisor.py", "_run"
        )
        self.assertIn("process.wait()", supervisor_run)
        self.assertLess(
            supervisor_run.index("process.wait()"),
            supervisor_run.index("_write_receipt("),
        )
        self.assertLess(
            browser.index("_write_browser_recovery_journal(state, probe)"),
            browser.index("LOCAL_PWA_IMAGE_ALREADY_EXISTS"),
        )
        self.assertNotIn("--headed", browser)
        self.assertLess(
            browser.index("service='minio'"), browser.index("service='clamd'")
        )
        self.assertLess(
            browser.index("service='clamd'"), browser.index("PWA_BROWSER_READY")
        )
        output = _function_source(LOCALCTL, "_browser_summary_contract")
        self.assertIn("PWA_OS_INSTALL_NOT_TESTED", output)
        self.assertIn("PWA_WAITING_UPDATE_PASSED", output)
        compose = _source(LOCAL_COMPOSE)
        web = _compose_service(compose, "web")
        self.assertIn("ANHUAN_PWA_UPDATE_PROBE", web)

        main = _function_source(LOCALCTL, "main")
        self.assertIn("arguments.command == 'browser-verify'", main)
        self.assertIn("_browser_verify(state, stage=arguments.stage)", main)
        self.assertLess(
            main.index("_recover_browser_recovery_journal(state)"),
            main.index("_recover_reverse_journal(state)"),
        )

    def test_localctl_browser_verify_stages_are_explicit_and_isolated(self) -> None:
        localctl = _load_localctl()
        parser = localctl._parser()
        self.assertEqual(
            parser.parse_args(["browser-verify"]).stage,
            "all",
        )
        self.assertEqual(
            {
                parser.parse_args(
                    ["browser-verify", "--stage", stage]
                ).stage
                for stage in localctl.BROWSER_STAGES
            },
            localctl.BROWSER_STAGES,
        )
        with self.assertRaises(SystemExit):
            parser.parse_args(["browser-verify", "--stage", "unknown"])

        browser = _function_source(LOCALCTL, "_browser_verify")
        self.assertIn("{'all', 'pwa-update'}", browser)
        self.assertIn("{'all', 'faults'}", browser)
        self.assertIn("'--stage'", browser)
        self.assertIn("stage=stage", browser)
        self.assertIn(
            "LOCAL_PWA_OS_BLOCKED_BY_BROWSER_AUTOMATION_BOUNDARY",
            browser,
        )

    def test_browser_stage_summaries_require_exact_stage_tags(self) -> None:
        localctl = _load_localctl()
        self.assertNotIn("pwa-os", localctl.BROWSER_STAGE_TAGS)
        for stage in sorted(localctl.BROWSER_STAGE_TAGS):
            exact, ranged_key, ranged_minimum = (
                localctl._browser_summary_contract(stage)
            )
            summary = dict(exact)
            if ranged_key is not None:
                summary[ranged_key] = ranged_minimum
            stdout = (
                json.dumps(summary, sort_keys=True, separators=(",", ":"))
                + "\n"
                + localctl.BROWSER_STAGE_TAGS[stage]
                + "\n"
            )
            self.assertEqual(
                localctl._validate_browser_summary(
                    stdout, 0, stage=stage
                ),
                summary,
            )
            wrong_stage = next(
                candidate
                for candidate in localctl.BROWSER_STAGES
                if candidate != stage
            )
            with self.assertRaisesRegex(
                localctl.LocalError,
                "LOCAL_BROWSER_OUTPUT_SHAPE_INVALID",
            ):
                localctl._validate_browser_summary(
                    stdout, 0, stage=wrong_stage
                )

    def test_every_browser_control_signal_is_read_from_a_private_file(self) -> None:
        localctl = _load_localctl()
        self.assertEqual(len(localctl.BROWSER_CONTROL_SIGNALS), 14)
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw) / "control"
            directory.mkdir(mode=0o700)
            for name in localctl.BROWSER_CONTROL_SIGNALS:
                localctl._exclusive_write(
                    directory / name, localctl.PWA_CONTROL_SIGNAL
                )
                self.assertTrue(localctl._control_signal_present(directory, name))
            with self.assertRaisesRegex(
                localctl.LocalError, "LOCAL_PWA_CONTROL_SIGNAL_INVALID"
            ):
                localctl._control_signal_present(directory, "untrusted")

    def test_pwa_offline_reopen_stops_and_starts_exact_b_container(self) -> None:
        localctl = _load_localctl()
        container_id = "a" * 64
        running = {
            "State": {
                "Running": True,
                "Status": "running",
                "Health": {"Status": "healthy"},
            }
        }
        stopped = {"State": {"Running": False, "Status": "exited"}}
        state = {"web_image": "anhuan-closeout-web:123456abcdef"}
        browser = mock.Mock()
        with tempfile.TemporaryDirectory() as raw:
            control = Path(raw)
            with (
                mock.patch.object(
                    localctl,
                    "_wait_for_control_signal",
                ) as wait_signal,
                mock.patch.object(
                    localctl,
                    "_web_container_payload",
                    side_effect=[
                        (container_id, running),
                        (container_id, stopped),
                        (container_id, running),
                    ],
                ),
                mock.patch.object(localctl, "_docker", return_value="docker"),
                mock.patch.object(localctl, "_run") as run,
                mock.patch.object(localctl, "_exclusive_write") as write_signal,
                mock.patch.object(localctl, "_assert_web_uses_image") as image,
            ):
                localctl._orchestrate_browser_pwa_offline_reopen(
                    state,
                    control,
                    browser,
                    image_b=str(state["web_image"]),
                )
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["docker", "stop", "--time", "30", container_id],
                ["docker", "start", container_id],
            ],
        )
        self.assertEqual(
            [call.args[1] for call in wait_signal.call_args_list],
            [localctl.PWA_OS_OFFLINE_READY, localctl.PWA_OS_OFFLINE_OBSERVED],
        )
        self.assertEqual(
            [call.args[0].name for call in write_signal.call_args_list],
            [localctl.PWA_OS_WEB_STOPPED, localctl.PWA_OS_WEB_RESTORED],
        )
        image.assert_called_once_with(state, state["web_image"])

    def test_browser_fault_artifact_cleanup_is_exact_and_private(self) -> None:
        localctl = _load_localctl()
        with tempfile.TemporaryDirectory() as raw:
            temporary_root = Path(raw) / "tmp"
            temporary_root.mkdir(mode=0o700)
            probe = "a" * 24
            control = temporary_root / f"pwa-update-{probe}"
            control.mkdir(mode=0o700)
            artifact_directory = temporary_root / f"anhuan-minio-fault-{probe}"
            artifact_directory.mkdir(mode=0o700)
            artifact = artifact_directory / localctl.MINIO_FAULT_ARTIFACT_NAME
            artifact.write_bytes(b"%PDF-1.4\n" + b"x" * 64)
            artifact.chmod(0o600)
            with mock.patch.object(localctl, "TMP_DIR", temporary_root):
                localctl._cleanup_browser_fault_artifact(control)
            self.assertFalse(artifact_directory.exists())
            self.assertTrue(control.is_dir())

            artifact_directory.mkdir(mode=0o700)
            residual = artifact_directory / "unexpected"
            residual.write_bytes(b"x")
            residual.chmod(0o600)
            with mock.patch.object(localctl, "TMP_DIR", temporary_root):
                with self.assertRaisesRegex(
                    localctl.LocalError, "LOCAL_BROWSER_FAULT_ARTIFACT_INVALID"
                ):
                    localctl._cleanup_browser_fault_artifact(control)
            self.assertTrue(residual.exists())

    def test_browser_recovery_journal_and_profile_are_probe_bound(self) -> None:
        localctl = _load_localctl()
        state = {
            "project_id": "11111111-1111-4111-8111-111111111111",
            "compose_project": "anhuan-closeout-123456abcdef",
            "web_image": "anhuan-closeout-web:123456abcdef",
        }
        probe = "c" * 24
        with tempfile.TemporaryDirectory() as raw:
            temporary_root = Path(raw) / "tmp"
            temporary_root.mkdir(mode=0o700)
            journal = Path(raw) / "browser-recovery.json"
            profile = temporary_root / f"anhuan-engineering-browser-{probe}"
            profile.mkdir(mode=0o700)
            (profile / "Preferences").write_bytes(b"local-profile")
            with (
                mock.patch.object(localctl, "TMP_DIR", temporary_root),
                mock.patch.object(localctl, "BROWSER_RECOVERY_JOURNAL", journal),
            ):
                localctl._write_browser_recovery_journal(state, probe)
                initial = localctl._read_browser_recovery_journal(state)
                self.assertIsNone(initial["browser_pid"])
                localctl._update_browser_recovery_pid(state, probe, 12345)
                updated = localctl._read_browser_recovery_journal(state)
                self.assertEqual(updated["browser_pid"], 12345)
                localctl._cleanup_browser_profile(
                    temporary_root / f"pwa-update-{probe}"
                )
                self.assertFalse(profile.exists())
                localctl._remove_browser_recovery_journal()
                self.assertFalse(journal.exists())

    def test_browser_recovery_removes_only_exact_probe_pwa_shim(self) -> None:
        localctl = _load_localctl()
        state = {"web_port": 12345}
        probe = "b" * 24
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            home = root / "home"
            home.mkdir(mode=0o700)
            temporary_root = root / "tmp"
            temporary_root.mkdir(mode=0o700)
            control = temporary_root / f"pwa-update-{probe}"
            control.mkdir(mode=0o700)
            applications = home / "Applications/Chrome Apps.localized"
            applications.mkdir(parents=True, mode=0o755)
            bundle = applications / "Anhuan Internal.app"
            contents = bundle / "Contents"
            contents.mkdir(parents=True, mode=0o755)
            shortcut_id = "a" * 32
            (contents / "Info.plist").write_bytes(
                localctl.plistlib.dumps(
                    {
                        "CFBundleIdentifier": f"com.google.Chrome.app.{shortcut_id}",
                        "CrAppModeShortcutID": shortcut_id,
                        "CrAppModeShortcutURL": (
                            "http://127.0.0.1:12345/internal-app"
                        ),
                        "CrAppModeUserDataDir": str(
                            temporary_root
                            / f"anhuan-engineering-browser-{probe}"
                            / "Default/Web Applications"
                            / f"_crx_{shortcut_id}"
                        ),
                    }
                )
            )
            with (
                mock.patch.object(localctl, "TMP_DIR", temporary_root),
                mock.patch.object(localctl.sys, "platform", "darwin"),
                mock.patch.object(
                    localctl.pwd,
                    "getpwuid",
                    return_value=mock.Mock(pw_dir=str(home)),
                ),
            ):
                localctl._cleanup_browser_pwa_shim(state, control)
            self.assertFalse(bundle.exists())
            self.assertTrue(applications.is_dir())

    def test_browser_recovery_reconciles_interrupted_staging(self) -> None:
        localctl = _load_localctl()
        state = {
            "project_id": "33333333-3333-4333-8333-333333333333",
            "compose_project": "anhuan-closeout-123456abcdef",
            "web_image": "anhuan-closeout-web:123456abcdef",
        }
        probe = "f" * 24
        with tempfile.TemporaryDirectory() as raw:
            journal = Path(raw) / "browser-recovery.json"
            staging = journal.with_name(journal.name + ".new")
            with mock.patch.object(localctl, "BROWSER_RECOVERY_JOURNAL", journal):
                localctl._write_browser_recovery_journal(state, probe)
                promoted = (
                    json.dumps(
                        localctl._browser_recovery_payload(state, probe, 12345),
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode("ascii")
                localctl._exclusive_write(staging, promoted)
                observed = localctl._read_browser_recovery_journal(state)
                self.assertEqual(observed["browser_pid"], 12345)
                self.assertFalse(staging.exists())

                localctl._exclusive_write(staging, b"{")
                observed = localctl._read_browser_recovery_journal(state)
                self.assertEqual(observed["browser_pid"], 12345)
                self.assertFalse(staging.exists())

                journal.unlink()
                localctl._exclusive_write(staging, b"")
                self.assertIsNone(localctl._read_browser_recovery_journal(state))
                self.assertFalse(staging.exists())

                foreign = dict(
                    localctl._browser_recovery_payload(state, probe, None)
                )
                foreign["project_id"] = (
                    "44444444-4444-4444-8444-444444444444"
                )
                localctl._exclusive_write(
                    staging,
                    (
                        json.dumps(
                            foreign,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode("ascii"),
                )
                with self.assertRaisesRegex(
                    localctl.LocalError,
                    "LOCAL_BROWSER_RECOVERY_INVALID",
                ):
                    localctl._read_browser_recovery_journal(state)
                self.assertTrue(staging.exists())

    def test_browser_recovery_removes_exact_partial_signal_and_pdf(self) -> None:
        localctl = _load_localctl()
        with tempfile.TemporaryDirectory() as raw:
            temporary_root = Path(raw) / "tmp"
            temporary_root.mkdir(mode=0o700)
            probe = "9" * 24
            control = temporary_root / f"pwa-update-{probe}"
            control.mkdir(mode=0o700)
            partial_signal = control / localctl.MINIO_FAULT_READY
            partial_signal.write_bytes(b"re")
            partial_signal.chmod(0o600)
            artifact_directory = temporary_root / f"anhuan-minio-fault-{probe}"
            artifact_directory.mkdir(mode=0o700)
            partial_pdf = (
                artifact_directory / localctl.MINIO_FAULT_ARTIFACT_NAME
            )
            partial_pdf.write_bytes(b"")
            partial_pdf.chmod(0o600)
            with mock.patch.object(localctl, "TMP_DIR", temporary_root):
                localctl._cleanup_browser_fault_artifact(control)
                localctl._cleanup_control_directory(control)
            self.assertFalse(artifact_directory.exists())
            self.assertFalse(control.exists())

    def test_browser_process_identity_requires_own_probe_process_group(self) -> None:
        localctl = _load_localctl()
        probe = "d" * 24
        control = localctl.TMP_DIR / f"pwa-update-{probe}"
        payload = {"browser_pid": 43210}
        command = (
            f"{localctl.PWA_BROWSER_RUNNER} http://127.0.0.1:1234 "
            f"--pwa-update-control {control}"
        )
        observed = subprocess.CompletedProcess(
            [], 0, f"{os.geteuid()} 43210 43210 node {command}\n", ""
        )
        with mock.patch.object(localctl, "_run", return_value=observed):
            self.assertEqual(
                localctl._browser_process_candidate(payload, control), 43210
            )
        wrong_group = subprocess.CompletedProcess(
            [], 0, f"{os.geteuid()} 43210 7 node {command}\n", ""
        )
        with mock.patch.object(localctl, "_run", return_value=wrong_group):
            with self.assertRaisesRegex(
                localctl.LocalError, "LOCAL_BROWSER_PROCESS_IDENTITY_INVALID"
            ):
                localctl._browser_process_candidate(payload, control)

        profile = localctl.TMP_DIR / f"anhuan-engineering-browser-{probe}"
        orphan_chrome = subprocess.CompletedProcess(
            [],
            0,
            f"{os.geteuid()} 50000 43210 /Applications/Google Chrome "
            f"--user-data-dir={profile}\n",
            "",
        )
        with mock.patch.object(localctl, "_run", return_value=orphan_chrome):
            self.assertEqual(
                localctl._browser_process_candidate(payload, control), 43210
            )
        untrusted_orphan = subprocess.CompletedProcess(
            [], 0, f"{os.geteuid()} 50000 43210 unrelated-helper\n", ""
        )
        with mock.patch.object(localctl, "_run", return_value=untrusted_orphan):
            with self.assertRaisesRegex(
                localctl.LocalError, "LOCAL_BROWSER_PROCESS_IDENTITY_INVALID"
            ):
                localctl._browser_process_candidate(payload, control)

    def test_browser_active_compose_identity_is_project_and_group_bound(self) -> None:
        localctl = _load_localctl()
        payload = {
            "active_command_kind": "build_b",
            "active_command_pid": 54321,
            "probe": "a" * 24,
            "project_id": "11111111-1111-4111-8111-111111111111",
            "compose_project": "anhuan-closeout-123456abcdef",
        }
        supervisor_command = (
            f"python {localctl.BROWSER_COMPOSE_SUPERVISOR} "
            "--kind build_b --probe "
            f"{payload['probe']} --project-id {payload['project_id']} "
            f"--compose-project {payload['compose_project']} "
            f"--state-file {localctl.STATE_FILE} "
            f"--compose-file {localctl.COMPOSE_FILE} "
            f"--env-file {localctl.ENV_FILE} --control-directory "
            f"{localctl._browser_control_directory(payload['probe'])}"
        )
        child_command = (
            "docker compose --ansi never --project-name "
            f"{payload['compose_project']} --env-file {localctl.ENV_FILE} "
            f"-f {localctl.COMPOSE_FILE} --profile ops build web"
        )
        observed = subprocess.CompletedProcess(
            [],
            0,
            f"{os.geteuid()} 54321 54321 54321 {supervisor_command}\n"
            f"{os.geteuid()} 60000 54321 54321 {child_command}\n",
            "",
        )
        with mock.patch.object(localctl, "_run", return_value=observed):
            self.assertEqual(
                localctl._browser_active_command_candidate(payload), 54321
            )
        trusted_orphan = subprocess.CompletedProcess(
            [],
            0,
            f"{os.geteuid()} 60000 54321 54321 {child_command}\n",
            "",
        )
        with mock.patch.object(
            localctl, "_run", return_value=trusted_orphan
        ):
            self.assertEqual(
                localctl._browser_active_command_candidate(payload), 54321
            )
        untrusted_orphan = subprocess.CompletedProcess(
            [], 0, f"{os.geteuid()} 60000 54321 54321 helper\n", ""
        )
        with mock.patch.object(
            localctl, "_run", return_value=untrusted_orphan
        ):
            with self.assertRaisesRegex(
                localctl.LocalError, "LOCAL_BROWSER_COMMAND_IDENTITY_INVALID"
            ):
                localctl._browser_active_command_candidate(payload)

    def test_browser_recovery_finalizer_removes_only_recorded_probe(self) -> None:
        localctl = _load_localctl()
        state = {
            "project_id": "22222222-2222-4222-8222-222222222222",
            "compose_project": "anhuan-closeout-123456abcdef",
            "web_image": "anhuan-closeout-web:123456abcdef",
            "web_port": 12345,
        }
        probe = "e" * 24
        with tempfile.TemporaryDirectory() as raw:
            temporary_root = Path(raw) / "tmp"
            temporary_root.mkdir(mode=0o700)
            journal = Path(raw) / "browser-recovery.json"
            control = temporary_root / f"pwa-update-{probe}"
            control.mkdir(mode=0o700)
            artifact_directory = temporary_root / f"anhuan-minio-fault-{probe}"
            artifact_directory.mkdir(mode=0o700)
            artifact = artifact_directory / localctl.MINIO_FAULT_ARTIFACT_NAME
            artifact.write_bytes(b"%PDF-1.4\n" + b"x" * 64)
            artifact.chmod(0o600)
            profile = temporary_root / f"anhuan-engineering-browser-{probe}"
            profile.mkdir(mode=0o700)
            (profile / "Preferences").write_bytes(b"profile")
            with (
                mock.patch.object(localctl, "TMP_DIR", temporary_root),
                mock.patch.object(localctl, "BROWSER_RECOVERY_JOURNAL", journal),
                mock.patch.object(
                    localctl, "_terminate_recovered_browser_process"
                ) as terminate,
                mock.patch.object(localctl, "_assert_resource_labels") as labels,
                mock.patch.object(
                    localctl, "_cleanup_browser_pwa_shim"
                ) as shim,
                mock.patch.object(localctl, "_assert_web_uses_image") as web,
                mock.patch.object(
                    localctl, "_inspect_probe_image", return_value=None
                ) as image,
                mock.patch.object(localctl.time, "sleep"),
            ):
                localctl._write_browser_recovery_journal(state, probe)
                localctl._recover_browser_recovery_journal(state)
            terminate.assert_called_once()
            labels.assert_called_once_with(state)
            self.assertEqual(
                web.call_args_list,
                [mock.call(state, state["web_image"])] * 2,
            )
            self.assertEqual(image.call_count, 2)
            shim.assert_called_once_with(state, control)
            self.assertFalse(journal.exists())
            self.assertFalse(control.exists())
            self.assertFalse(artifact_directory.exists())
            self.assertFalse(profile.exists())

        browser = _function_source(LOCALCTL, "_browser_verify")
        self.assertLess(
            browser.index("if primary_error is not None"),
            browser.index("if cleanup_error is not None"),
        )

    def test_browser_summary_requires_real_fault_and_recovery_evidence(self) -> None:
        localctl = _load_localctl()
        summary = {
            "identities_authenticated": 3,
            "admin_pages_visited": 17,
            "admin_api_responses": 20,
            "admin_api_non_2xx": 0,
            "consultant_pages_visited": 2,
            "enterprise_pages_visited": 2,
            "role_api_non_2xx": 0,
            "role_allowed_action_ui_checks": 2,
            "cross_tenant_404_ui_count": 1,
            "illegal_state_409_ui_count": 1,
            "expected_fault_api_non_2xx": 3,
            "service_unavailable_503_ui_count": 1,
            "service_unavailable_503_ui_status": "PASSED",
            "clamd_unavailable_ui_count": 1,
            "clamd_recovery_ui_count": 1,
            "tenant_header_changes": 1,
            "tenant_state_clears": 1,
            "pwa_registrations": 1,
            "pwa_controlled_clients": 1,
            "pwa_owned_caches": 2,
            "pwa_sensitive_cache_entries": 0,
            "pwa_installability_errors": 0,
            "pwa_offline_shell": 1,
            "pwa_installations": 0,
            "pwa_os_offline_reopens": 0,
            "pwa_os_online_launches": 0,
            "pwa_os_shim_residuals": 0,
            "pwa_os_shims_created": 0,
            "pwa_os_install_status": "PWA_OS_INSTALL_NOT_TESTED",
            "pwa_os_uninstall_probe": 0,
            "pwa_os_uninstallations": 0,
            "pwa_waiting_updates": 1,
            "pwa_controller_changes": 1,
            "pwa_old_caches_removed": 2,
            "pwa_new_caches": 2,
            "pwa_sentinel_caches_preserved": 1,
            "pwa_login_states_preserved": 1,
            "pwa_update_status": "PWA_WAITING_UPDATE_PASSED",
            "pwa_apply_clicks": 1,
            "stage": "all",
        }
        stdout = (
            json.dumps(summary, sort_keys=True, separators=(",", ":"))
            + "\nLOCAL_BROWSER_VERIFY_OK\n"
        )
        self.assertEqual(localctl._validate_browser_summary(stdout, 0), summary)

        old = dict(summary)
        old["service_unavailable_503_ui_count"] = 0
        old["service_unavailable_503_ui_status"] = (
            "NOT_TESTED_ORCHESTRATION_REQUIRED"
        )
        with self.assertRaisesRegex(
            localctl.LocalError, "LOCAL_BROWSER_OUTPUT_INVALID"
        ):
            localctl._validate_browser_summary(
                json.dumps(old, sort_keys=True, separators=(",", ":"))
                + "\nLOCAL_BROWSER_VERIFY_OK\n",
                0,
            )

    def test_browser_failure_reason_is_allowlisted_and_never_echoes_noise(self) -> None:
        localctl = _load_localctl()
        warning = "ExperimentalWarning: ignored https://secret.invalid/path\n"
        parsed = localctl._browser_failure(
            warning
            + "LOCAL_BROWSER_VERIFY_FAILED PWA_WAITING_UPDATE_MISSING\n"
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(str(parsed), "PWA_WAITING_UPDATE_MISSING")
        self.assertIsNone(localctl._browser_failure(warning))

        rejected = localctl._browser_failure(
            "LOCAL_BROWSER_VERIFY_FAILED LEAK_https_secret_invalid\n"
        )
        self.assertIsNotNone(rejected)
        self.assertEqual(
            str(rejected), "LOCAL_BROWSER_FAILURE_REASON_NOT_ALLOWED"
        )
        ambiguous = localctl._browser_failure(
            "LOCAL_BROWSER_VERIFY_FAILED PWA_WAITING_UPDATE_MISSING\n"
            "LOCAL_BROWSER_VERIFY_FAILED PWA_UPDATE_ACTIVATION_INVALID\n"
        )
        self.assertEqual(
            str(ambiguous), "LOCAL_BROWSER_FAILURE_REASON_AMBIGUOUS"
        )

    def test_compose_exposes_project_bound_business_verifier(self) -> None:
        compose = _source(LOCAL_COMPOSE)
        service = _compose_service(compose, "business-verifier")
        self.assertIn(
            'command: ["python", "-B", "/app/infra/f1/local_business_verify.py"]',
            service,
        )
        self.assertIn("environment: *runtime_environment", service)
        self.assertIn("- migrator_secrets:/run/secrets/f1:ro", service)
        self.assertIn("- ../../tests:/app/tests:ro", service)
        self.assertIn("condition: service_healthy", service)
        self.assertIn("profiles: [ops]", service)
        self.assertNotIn("ports:", service)

    def test_compose_exposes_real_p3_ingestion_verifier(self) -> None:
        compose = _source(LOCAL_COMPOSE)
        service = _compose_service(compose, "ingestion-verifier")
        self.assertIn(
            'command: ["python", "-B", "/app/infra/f1/local_ingestion_verify.py"]',
            service,
        )
        self.assertIn("F1_MINIO_ROOT_USER_FILE: /run/secrets/api/minio_root_user", service)
        self.assertIn("- migrator_secrets:/run/secrets/f1:ro", service)
        self.assertIn("- api_secrets:/run/secrets/api:ro", service)
        for dependency in ("postgres:", "minio:", "clamd:"):
            self.assertIn(dependency, service)
        self.assertIn("profiles: [ops]", service)
        self.assertNotIn("ports:", service)

    def test_success_requires_the_complete_exact_contract(self) -> None:
        self.assertEqual(len(P2_P7_TABLES), 34)
        self.assertEqual(len(EXPECTED_RUNTIME_ROLES), 2)
        self.assertEqual(len(EXPECTED_ENTERPRISES), 2)
        self.assertEqual(len(EXPECTED_BINDINGS), 7)

        counts = verify_snapshot(_valid_snapshot(), expected_database=DATABASE)
        self.assertEqual(counts, VerificationCounts())

    def test_success_output_is_fixed_counts_only(self) -> None:
        metrics, tag = render_success(VerificationCounts())
        decoded = json.loads(metrics)
        self.assertEqual(tag, "LOCAL_VERIFY_OK")
        self.assertTrue(decoded)
        self.assertTrue(all(type(value) is int for value in decoded.values()))
        self.assertEqual(decoded["rls_table_count"], 34)
        self.assertEqual(decoded["runtime_role_membership_count"], 0)
        for forbidden in (
            "fixture.invalid",
            "Local Enterprise",
            "f0d_bootstrap",
            "20000000-0000",
        ):
            self.assertNotIn(forbidden, metrics)

    def test_each_migration_head_must_be_the_only_row(self) -> None:
        for changes in (
            {"f0_heads": ("f0d_0006", "unexpected")},
            {"f1_heads": ()},
            {"f1_heads": ("f1_0009",)},
        ):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(
                    VerificationError, "^LOCAL_VERIFY_HEAD_MISMATCH$"
                ):
                    verify_snapshot(
                        _replace(_valid_snapshot(), **changes),
                        expected_database=DATABASE,
                    )

    def test_all_business_tables_must_enable_and_force_rls(self) -> None:
        rows = list(_valid_snapshot().rls_rows)
        rows[-1] = (rows[-1][0], True, False)
        with self.assertRaisesRegex(
            VerificationError, "^LOCAL_VERIFY_RLS_MISMATCH$"
        ):
            verify_snapshot(
                _replace(_valid_snapshot(), rls_rows=tuple(rows)),
                expected_database=DATABASE,
            )

    def test_runtime_roles_require_exact_flags_and_zero_memberships(self) -> None:
        roles = list(EXPECTED_RUNTIME_ROLES)
        unsafe = list(roles[0])
        unsafe[7] = True
        roles[0] = tuple(unsafe)
        with self.assertRaisesRegex(
            VerificationError, "^LOCAL_VERIFY_RUNTIME_ROLE_MISMATCH$"
        ):
            verify_snapshot(
                _replace(_valid_snapshot(), runtime_roles=tuple(roles)),
                expected_database=DATABASE,
            )

        with self.assertRaisesRegex(
            VerificationError, "^LOCAL_VERIFY_ROLE_MEMBERSHIP_MISMATCH$"
        ):
            verify_snapshot(
                _replace(_valid_snapshot(), runtime_role_memberships=1),
                expected_database=DATABASE,
            )

    def test_seed_enterprises_and_every_role_binding_are_exact(self) -> None:
        with self.assertRaisesRegex(
            VerificationError, "^LOCAL_VERIFY_SEED_ENTERPRISE_MISMATCH$"
        ):
            verify_snapshot(
                _replace(_valid_snapshot(), enterprises=EXPECTED_ENTERPRISES[:1]),
                expected_database=DATABASE,
            )

        changed = list(EXPECTED_BINDINGS)
        changed[-1] = (*changed[-1][:-1], "auditor")
        with self.assertRaisesRegex(
            VerificationError, "^LOCAL_VERIFY_SEED_BINDING_MISMATCH$"
        ):
            verify_snapshot(
                _replace(_valid_snapshot(), bindings=tuple(changed)),
                expected_database=DATABASE,
            )

    def test_bootstrap_identity_is_not_inferred_from_connectivity(self) -> None:
        with self.assertRaisesRegex(
            VerificationError, "^LOCAL_VERIFY_DATABASE_IDENTITY_MISMATCH$"
        ):
            verify_snapshot(
                _replace(
                    _valid_snapshot(),
                    identity=("f1_api", "f1_api", DATABASE),
                ),
                expected_database=DATABASE,
            )


if __name__ == "__main__":
    unittest.main()
