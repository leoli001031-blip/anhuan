"""Fast, database-free contracts for the live local verifier."""
from __future__ import annotations

import ast
import dataclasses
import importlib.machinery
import importlib.util
import json
import re
import unittest
from pathlib import Path

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
        f1_heads=("f1_0010",),
        rls_rows=tuple((name, True, True) for name in P2_P7_TABLES),
        runtime_roles=EXPECTED_RUNTIME_ROLES,
        runtime_role_memberships=0,
        enterprises=EXPECTED_ENTERPRISES,
        bindings=EXPECTED_BINDINGS,
    )


def _replace(snapshot: Snapshot, **changes: object) -> Snapshot:
    return dataclasses.replace(snapshot, **changes)


class LocalVerifierContracts(unittest.TestCase):
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
            "LOCAL_PWA_UPDATE_PROBE",
            "--pwa-update-control",
            "PWA_BROWSER_READY",
            "PWA_IMAGE_READY",
            "--force-recreate",
            "_assert_web_uses_image(state, image_b)",
            "_assert_web_uses_image(state, image_a)",
            "'image', 'rm', image_b",
            "_cleanup_control_directory(control_directory)",
            "_runtime_log_metrics(state)",
            "print(log_tag)",
        ):
            self.assertIn(token, browser)
        self.assertNotIn("--headed", browser)
        output = _function_source(LOCALCTL, "_validate_browser_summary")
        self.assertIn("PWA_OS_INSTALL_NOT_TESTED", output)
        self.assertIn("PWA_WAITING_UPDATE_PASSED", output)
        compose = _source(LOCAL_COMPOSE)
        web = _compose_service(compose, "web")
        self.assertIn("ANHUAN_PWA_UPDATE_PROBE", web)

        main = _function_source(LOCALCTL, "main")
        self.assertIn("arguments.command == 'browser-verify'", main)
        self.assertIn("_browser_verify(state)", main)

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
        self.assertEqual(len(P2_P7_TABLES), 31)
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
        self.assertEqual(decoded["rls_table_count"], 31)
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
