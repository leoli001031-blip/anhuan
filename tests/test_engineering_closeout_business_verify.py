"""Database-free contracts for the real P2-P7 local business verifier."""
from __future__ import annotations

import contextlib
import inspect
import io
import json
import os
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock

from infra.f1.local_business_verify import (
    BusinessVerificationCounts,
    BusinessVerificationError,
    ScratchAdapter,
    _map_smoke_failure,
    _metric_failure_reason,
    actors_from_local_seed,
    main,
    render_success,
    rewrite_dsn_database,
    scratch_database_name,
    temporary_scratch_environment,
)
import infra.f1.local_business_verify as business_verify


SOURCE_DATABASE = "anhuan_closeout_0123456789abcdef01234567"
SCRATCH_DATABASE = "anhuan_business_0123456789ab_aaaaaaaaaaaaaaaa"
ROOT = Path(__file__).resolve().parents[1]


class EngineeringCloseoutBusinessVerifierTests(unittest.TestCase):
    def test_scratch_database_is_random_and_project_bound(self) -> None:
        first = scratch_database_name(SOURCE_DATABASE, "a" * 32)
        second = scratch_database_name(SOURCE_DATABASE, "b" * 32)
        self.assertEqual(first, SCRATCH_DATABASE)
        self.assertNotEqual(first, second)
        for invalid in (
            "postgres",
            "anhuan_closeout_0123",
            "shared_0123456789abcdef01234567",
        ):
            with self.subTest(source=invalid):
                with self.assertRaisesRegex(
                    BusinessVerificationError,
                    "^LOCAL_BUSINESS_SOURCE_INVALID$",
                ):
                    scratch_database_name(invalid, "a" * 32)

    def test_dsn_rewrite_changes_only_the_database(self) -> None:
        source = (
            "postgresql://f0d_bootstrap:encoded%2Fsecret@postgres:5432/"
            + SOURCE_DATABASE
        )
        rewritten = rewrite_dsn_database(
            source,
            source_database=SOURCE_DATABASE,
            scratch_database=SCRATCH_DATABASE,
            expected_user="f0d_bootstrap",
        )
        self.assertEqual(
            rewritten,
            "postgresql://f0d_bootstrap:encoded%2Fsecret@postgres:5432/"
            + SCRATCH_DATABASE,
        )
        self.assertNotIn("?", rewritten)

        with self.assertRaisesRegex(
            BusinessVerificationError,
            "^LOCAL_BUSINESS_SOURCE_INVALID$",
        ):
            rewrite_dsn_database(
                source.replace("f0d_bootstrap", "f1_api"),
                source_database=SOURCE_DATABASE,
                scratch_database=SCRATCH_DATABASE,
                expected_user="f0d_bootstrap",
            )

    def test_actor_map_preserves_every_required_local_seed_role(self) -> None:
        enterprise_a = uuid.UUID("20000000-0000-4000-8000-00000000000a")
        enterprise_b = uuid.UUID("20000000-0000-4000-8000-00000000000b")
        definitions = (
            ("admin-a", enterprise_a, "super_admin"),
            ("admin-b", enterprise_b, "super_admin"),
            ("enterprise", enterprise_a, "enterprise_admin"),
            ("employee", enterprise_a, "plant_admin"),
            ("consultant", enterprise_a, "auditor"),
            ("partner", enterprise_a, "partner"),
            ("tenant-b", enterprise_b, "enterprise_admin"),
        )
        bindings = tuple(
            types.SimpleNamespace(
                name=name,
                enterprise_id=enterprise_id,
                sub=f"sub-{name}",
                role=role,
            )
            for name, enterprise_id, role in definitions
        )
        fake_seed = types.ModuleType("infra.f1.local_seed")
        fake_seed.BINDINGS = bindings
        fake_seed._stable_id = lambda kind, value: uuid.uuid5(
            uuid.NAMESPACE_URL, f"{kind}:{value}"
        )
        with mock.patch.dict(
            sys.modules, {"infra.f1.local_seed": fake_seed}
        ):
            actors = actors_from_local_seed()
        self.assertEqual(
            set(actors),
            {"admin", "enterprise", "employee", "consultant", "partner", "tenant_b"},
        )
        self.assertEqual(actors["admin"]["role"], "super_admin")
        self.assertEqual(actors["enterprise"]["role"], "enterprise_admin")
        self.assertEqual(actors["employee"]["role"], "plant_admin")
        self.assertEqual(actors["consultant"]["role"], "auditor")
        self.assertEqual(actors["partner"]["role"], "partner")
        self.assertNotEqual(
            actors["admin"]["enterprise_id"],
            actors["tenant_b"]["enterprise_id"],
        )

    def test_scratch_environment_is_exact_and_fully_restored(self) -> None:
        with tempfile.TemporaryDirectory() as raw_directory:
            directory = Path(raw_directory)
            adapter = ScratchAdapter(
                run_id="a" * 32,
                database=SCRATCH_DATABASE,
                host="postgres",
                port=5432,
                secret_directory=directory,
                bootstrap_dsn=(
                    "postgresql://f0d_bootstrap:secret@postgres:5432/"
                    + SCRATCH_DATABASE
                ),
                api_password="api-secret",
            )
            original_database = os.environ.get("F1_PG_DATABASE")
            original_secrets = os.environ.get("F1_SECRETS_DIR")
            with temporary_scratch_environment(
                adapter,
                f0_migration_dsn=(
                    "postgresql://f0d_migration:secret@postgres:5432/"
                    + SCRATCH_DATABASE
                ),
            ):
                self.assertEqual(os.environ["F1_PG_DATABASE"], SCRATCH_DATABASE)
                self.assertEqual(os.environ["F1_SECRETS_DIR"], str(directory))
                self.assertEqual(os.environ["F1_PG_HOST"], "postgres")
                self.assertEqual(os.environ["F1_PG_PORT"], "5432")
            self.assertEqual(os.environ.get("F1_PG_DATABASE"), original_database)
            self.assertEqual(os.environ.get("F1_SECRETS_DIR"), original_secrets)

    def test_success_output_is_fixed_integer_metrics_only(self) -> None:
        metrics, tag = render_success(BusinessVerificationCounts())
        decoded = json.loads(metrics)
        self.assertEqual(tag, "LOCAL_BUSINESS_VERIFY_OK")
        self.assertTrue(decoded)
        self.assertTrue(all(type(value) is int for value in decoded.values()))
        self.assertEqual(decoded["cross_tenant_api_leak_count"], 0)
        self.assertEqual(decoded["scratch_database_residual_count"], 0)
        self.assertEqual(decoded["source_business_row_delta_count"], 0)
        self.assertEqual(decoded["illegal_state_transition_409_count"], 1)
        self.assertEqual(decoded["illegal_transition_business_delta_count"], 0)
        self.assertEqual(decoded["illegal_transition_audit_delta_count"], 0)
        self.assertEqual(decoded["illegal_transition_timeline_delta_count"], 0)
        self.assertEqual(decoded["illegal_transition_notification_delta_count"], 0)
        self.assertEqual(decoded["application_engine_restart_count"], 1)
        self.assertEqual(decoded["post_restart_business_read_count"], 5)
        self.assertEqual(
            decoded["post_restart_cross_tenant_detail_leak_count"], 0
        )
        self.assertEqual(
            decoded["post_restart_cross_tenant_list_leak_count"], 0
        )
        for forbidden in (
            SOURCE_DATABASE,
            SCRATCH_DATABASE,
            "postgresql://",
            "password",
            "fixture.invalid",
        ):
            self.assertNotIn(forbidden, metrics)

    def test_smoke_failures_are_reduced_to_fixed_aggregate_reasons(self) -> None:
        expectations = {
            "CROSS_TENANT_DETAIL_RED": "LOCAL_BUSINESS_TENANT_BOUNDARY_FAILED",
            "RLS_WRITE_RED": "LOCAL_BUSINESS_TENANT_BOUNDARY_FAILED",
            "TIMELINE_GAP_RED": "LOCAL_BUSINESS_EVIDENCE_FAILED",
            "NOTIFICATION_LINK_RED": "LOCAL_BUSINESS_EVIDENCE_FAILED",
            "WAVE2_REVIEW_RED": "LOCAL_BUSINESS_P2_FAILED",
            "P6_RUN_STATE_RED": "LOCAL_BUSINESS_P4_P7_FAILED",
        }
        for code, expected in expectations.items():
            with self.subTest(code=code):
                self.assertEqual(
                    _map_smoke_failure(code, p2_stage=code.startswith("WAVE")),
                    expected,
                )

        clean = types.SimpleNamespace(
            METRICS={"external_calls": 0, "rls_write_leaks": 0}
        )
        leaked = types.SimpleNamespace(
            METRICS={"external_calls": 0, "rls_write_leaks": 1}
        )
        networked = types.SimpleNamespace(
            METRICS={"external_calls": 1, "rls_write_leaks": 0}
        )
        self.assertIsNone(_metric_failure_reason(clean, clean))
        self.assertEqual(
            _metric_failure_reason(leaked, clean),
            "LOCAL_BUSINESS_TENANT_BOUNDARY_FAILED",
        )
        self.assertEqual(
            _metric_failure_reason(clean, networked),
            "LOCAL_BUSINESS_EXTERNAL_CALL_DETECTED",
        )

    def test_runner_reuses_real_contracts_without_starting_docker(self) -> None:
        source = inspect.getsource(business_verify)
        execution = inspect.getsource(business_verify._run_smoke_contracts)
        migration = inspect.getsource(business_verify._migrate_seed_and_verify)
        cleanup = inspect.getsource(business_verify._drop_scratch_database)

        self.assertIn("from tests import p2_real_pg_api_smoke as p2", source)
        self.assertIn("from tests import p4_p7_real_api_smoke as p4_p7", source)
        self.assertIn("await p2._api_smoke(adapter, actors)", execution)
        self.assertIn("p2._direct_rls_and_evidence", execution)
        self.assertIn("await p4_p7._api_smoke(adapter, actors)", execution)
        self.assertIn("p4_p7._direct_rls_and_audit", execution)
        self.assertIn("local_migrate.migrate()", migration)
        self.assertIn("local_seed.main()", migration)
        self.assertIn(
            "CREATE DATABASE {} OWNER f0d_migration TEMPLATE template0",
            source,
        )
        self.assertIn("COMMENT ON DATABASE {} IS {}", source)
        self.assertIn('sql.SQL("DROP DATABASE {} WITH (FORCE)")', cleanup)
        self.assertIn("_source_business_counts(configuration)", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("docker", source.lower())

    def test_real_illegal_transition_and_engine_restart_contracts_are_bounded(
        self,
    ) -> None:
        illegal = inspect.getsource(
            business_verify._verify_illegal_transition_rollback
        )
        snapshot = inspect.getsource(
            business_verify._illegal_transition_snapshot
        )
        restart = inspect.getsource(
            business_verify._verify_engine_restart_persistence
        )
        app_builder = inspect.getsource(
            business_verify._build_business_probe_app
        )

        self.assertIn("/api/v1/service-cases/{case_id}/close", illegal)
        self.assertIn("response.status_code != 409", illegal)
        self.assertIn("SERVICE_CASE_NOT_CLOSABLE", illegal)
        self.assertIn("if after != before", illegal)
        service_cases_source = (
            ROOT
            / "src/platform_foundation/f1/api/routers/service_cases.py"
        ).read_text(encoding="utf-8")
        close_source = service_cases_source.split(
            '@router.post("/{case_id}/close"', 1
        )[1].split("\n\n@router.", 1)[0]
        self.assertNotIn("FOR UPDATE", close_source)
        self.assertIn(
            "WHERE id = :case_id AND status = 'completed'", close_source
        )
        self.assertIn(
            "app.dependency_overrides[service_cases.tenant_from_header]",
            app_builder,
        )
        self.assertIn("await auth.current_tenant", app_builder)
        for table in (
            "f1.service_case",
            "f1.audit_log",
            "f1.business_timeline",
            "f1.in_app_notification",
        ):
            self.assertIn(table, snapshot)

        self.assertIn("await dispose_database_engines()", restart)
        self.assertIn("if database._engines or database._factories", restart)
        self.assertIn("new_engine is old_engine", restart)
        for path in (
            "/api/v1/service-cases",
            "/api/v1/views-reports/crm/accounts",
            "/api/v1/policy-workflow/sources",
            "/api/v1/automated-quality/suites",
            "/api/v1/local-rehearsal/plans",
        ):
            self.assertIn(path, restart)
        self.assertIn("response.status_code != 404", restart)
        self.assertIn("_response_has_empty_items", restart)

    def test_failure_surface_never_renders_exception_or_traceback(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        error = BusinessVerificationError("LOCAL_BUSINESS_P2_FAILED")
        with mock.patch.object(business_verify, "run", side_effect=error):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(main(), 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "LOCAL_BUSINESS_P2_FAILED\n")
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
