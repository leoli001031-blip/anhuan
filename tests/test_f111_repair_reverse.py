"""Static anti-fake-green contract for the F1.1.1 reverse verifier.

These tests deliberately do not contact the acceptance stack.  They keep the
verifier honest before the live run is allowed: no pre-clean, no shared-stack
control, opaque run-scoped resources, fail-closed dependencies, exact metrics,
and exact post-clean snapshots.
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from tests import f111_reverse_verify as reverse


ROOT = Path(__file__).resolve().parents[1]
REVERSE = ROOT / "tests/f111_reverse_verify.py"
REPAIR_COMPOSE = ROOT / "infra/f1/docker-compose.repair.yml"

EXPECTED_METRICS = (
    "valid_http_e2e",
    "membership_mint",
    "invite_double_consume",
    "stale_lease_commit",
    "duplicate_dispatch",
    "upload_replay_effects",
    "enqueue_recovery",
    "worker_restart",
    "ragflow_recovery",
    "qa_request_races",
    "citation_crosswires",
    "tenant_crosswires",
    "audit_gaps",
    "object_orphans_delta",
    "rq_orphans_delta",
    "index_duplicates",
    "preclean_mutations",
    "new_plaintext_leaks",
    "upstream_mutations",
    "scratch_residuals",
)


class RepairComposeContractTests(unittest.TestCase):
    def test_unique_local_images_and_bounded_logs(self) -> None:
        source = REPAIR_COMPOSE.read_text(encoding="utf-8")
        self.assertIn("F111_REVERSE_PROJECT_REQUIRED", source)
        self.assertIn("anhuan-f111-repair-api:${F111_REVERSE_PROJECT", source)
        self.assertIn("anhuan-f111-repair-worker:${F111_REVERSE_PROJECT", source)
        self.assertIn("anhuan-f111-repair-web:${F111_REVERSE_PROJECT", source)
        self.assertIn("driver: local", source)
        self.assertIn('max-size: "10m"', source)
        self.assertNotIn("container_name:", source)


def _source() -> str:
    return REVERSE.read_text(encoding="utf-8")


def _tree() -> ast.Module:
    return ast.parse(_source())


def _constant(name: str):
    for node in _tree().body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                return ast.literal_eval(node.value)
    raise AssertionError(f"missing constant: {name}")


def _function_source(name: str) -> str:
    source = _source()
    for node in _tree().body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"missing function: {name}")


class ReverseMetricContractTests(unittest.TestCase):
    def test_exact_twenty_metric_names_and_order(self) -> None:
        self.assertEqual(tuple(_constant("METRICS")), EXPECTED_METRICS)

    def test_metric_line_formatter_is_centralized(self) -> None:
        source = _source()
        self.assertIn("def format_metric_line(", source)
        self.assertEqual(source.count('print(format_metric_line('), 1)

    def test_main_initializes_every_metric_nonzero(self) -> None:
        source = _function_source("main")
        self.assertRegex(source, r"dict\.fromkeys\(METRICS,\s*1\)")

    def test_unexpected_exception_cannot_exit_zero(self) -> None:
        source = _function_source("main")
        self.assertIn("except Exception", source)
        self.assertIn("return 2", source)


class ReverseIdentityAndAuditOutcomeTests(unittest.TestCase):
    def test_invite_consume_probe_supplies_ignored_forged_identity_fields(self) -> None:
        project = "anhuan-f111-repair-0123456789abcdef0123456789abcdef"
        payload = reverse.invite_consume_probe_payload("opaque-token", project)
        self.assertEqual(payload["token"], "opaque-token")
        self.assertTrue(payload["keycloak_sub"].startswith("f111-forged-"))
        self.assertTrue(payload["email"].endswith("@fixture.invalid"))

    def test_audit_gate_requires_auditor_200_and_enterprise_admin_403(self) -> None:
        self.assertEqual(
            reverse.audit_gate_failure_count(
                auditor_status=200,
                enterprise_admin_status=403,
                observed_role="enterprise_admin",
            ),
            0,
        )
        self.assertEqual(
            reverse.audit_gate_failure_count(
                auditor_status=403,
                enterprise_admin_status=403,
                observed_role="enterprise_admin",
            ),
            1,
        )
        self.assertEqual(
            reverse.audit_gate_failure_count(
                auditor_status=200,
                enterprise_admin_status=200,
                observed_role="enterprise_admin",
            ),
            1,
        )
        self.assertEqual(
            reverse.audit_gate_failure_count(
                auditor_status=200,
                enterprise_admin_status=403,
                observed_role="plant_admin",
            ),
            1,
        )


class ReverseNoPrecleanTests(unittest.TestCase):
    def test_no_delete_or_truncate_before_first_snapshot(self) -> None:
        source = _function_source("main")
        prefix = source[: source.index("before = verifier.snapshot(")]
        self.assertNotRegex(prefix.upper(), r"\b(?:DELETE|TRUNCATE)\b")

    def test_verifier_construction_before_snapshot_is_read_only(self) -> None:
        verifier = next(
            node
            for node in _tree().body
            if isinstance(node, ast.ClassDef) and node.name == "Verifier"
        )
        init = next(
            node
            for node in verifier.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        segment = ast.get_source_segment(_source(), init) or ""
        for forbidden in (
            ".cleanup(",
            ".remove_",
            ".pause(",
            ".unpause(",
            ".signal_worker(",
            ".ensure_worker(",
            "DELETE ",
            "TRUNCATE ",
        ):
            self.assertNotIn(forbidden, segment)
        self.assertIn("validate_isolation()", segment)
        self.assertIn("validate_database_scope", segment)

    def test_no_bootstrap_or_bypassrls_connection(self) -> None:
        source = _source().lower()
        self.assertNotIn("_bootstrap_pg", source)
        self.assertNotIn("bypassrls", source)
        self.assertNotIn("bootstrap-local", source)

    def test_preclean_guard_scans_before_business_actions(self) -> None:
        source = _function_source("main")
        self.assertLess(source.index("preclean_guard"), source.index("run_probes"))

    def test_finally_cannot_cleanup_when_baseline_binding_never_completed(self) -> None:
        source = _source()
        main = _function_source("main")
        self.assertIn("and verifier.cleanup_authorized", main)
        self.assertIn("self.cleanup_authorized = False", source)
        self.assertIn("self.cleanup_authorized = True", source)
        self.assertIn("CLEANUP_BEFORE_BASELINE_FORBIDDEN", source)

    def test_no_hardcoded_shared_compose_project(self) -> None:
        source = _source()
        self.assertNotRegex(source, r"[\"']anhuan-f1[\"']")
        self.assertIn("class ScratchServiceController", source)
        self.assertIn('"docker", "compose", "--env-file", "/dev/null"', source)


class ReverseRunScopeTests(unittest.TestCase):
    def test_run_id_is_random_and_has_required_prefix(self) -> None:
        source = _source()
        self.assertIn("anhuan-f111-repair-", source)
        self.assertRegex(source, r"uuid\.uuid4\(\)")

    def test_refuses_non_scratch_compose_project(self) -> None:
        source = _source()
        self.assertIn("SCRATCH_PROJECT_PREFIX", source)
        self.assertIn("UNSAFE_STACK_SCOPE", source)
        self.assertIn('effective.get("name") != self.config.project', source)
        self.assertIn("UNSAFE_DOCKER_SCOPE", source)
        self.assertIn('parsed_docker_host.scheme != "unix"', source)

    def test_database_name_is_derived_from_random_project(self) -> None:
        source = _source()
        self.assertIn("def scratch_database_name(", source)
        self.assertIn('return "f111_repair_" + self.project.removeprefix(', source)
        self.assertNotIn("expected_database_name", source)

    def test_control_and_worker_roles_must_share_only_the_scratch_database(self) -> None:
        source = _source()
        self.assertIn("def validate_database_scope(", source)
        self.assertIn("current_database()", source)
        self.assertIn("inet_server_port()", source)
        self.assertIn("(expected, True, expected_port)", source)
        self.assertIn("(expected, False, expected_port)", source)
        self.assertIn("UNSAFE_DATABASE_SCOPE", source)

    def test_database_scope_gate_runs_before_first_snapshot(self) -> None:
        verifier = next(
            node
            for node in _tree().body
            if isinstance(node, ast.ClassDef) and node.name == "Verifier"
        )
        init = next(
            node
            for node in verifier.body
            if isinstance(node, ast.FunctionDef) and node.name == "__init__"
        )
        segment = ast.get_source_segment(_source(), init) or ""
        self.assertIn(
            "self.control.validate_database_scope(self.services.database_port)", segment
        )
        main = _function_source("main")
        self.assertLess(main.index("Verifier(config, registry)"), main.index("verifier.snapshot()"))

    def test_every_remote_endpoint_is_bound_to_its_scratch_compose_port(self) -> None:
        source = _source()
        for service, target in (
            ("api", 8001),
            ("keycloak", 8080),
            ("minio", 9000),
            ("redis", 6379),
            ("ragflow", 9380),
            ("jaeger", 16686),
        ):
            self.assertIn(f'published_port("{service}", {target})', source)
        self.assertIn("UNSAFE_DEPENDENCY_SCOPE", source)

    def test_fault_injection_targets_real_compose_service_names(self) -> None:
        source = _source()
        self.assertIn('self.services.pause("ragflow")', source)
        self.assertNotIn('self.services.pause("ragflow-server")', source)

    def test_worker_is_held_on_ragflow_before_sigkill_lease_attack(self) -> None:
        source = _source()
        tree = _tree()
        probe = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "ProbeSuite"
        )
        method = next(
            node
            for node in probe.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "worker_restart_and_stale_lease"
        )
        segment = ast.get_source_segment(source, method) or ""
        self.assertLess(segment.index('pause("ragflow")'), segment.index("wait_for_lease"))
        self.assertIn('unpause("ragflow")', segment)
        self.assertIn("ensure_worker()", segment)

    def test_stale_lease_probe_cannot_treat_generic_acl_error_as_cas_success(self) -> None:
        source = _source()
        self.assertIn("STALE_LEASE_PROBE_SQL_ERROR", source)
        self.assertNotIn('sqlstate", "")) in {"42501", "P0001"}', source)

    def test_duplicate_dispatch_requires_one_row_one_job_one_claim_attempt(self) -> None:
        source = _source()
        self.assertIn("coalesce(max(dispatch_attempt), 0)", source)
        self.assertIn("dispatch_count == 1", source)
        self.assertIn("distinct_jobs == 1", source)
        self.assertIn("dispatch_attempts == 1", source)
        self.assertIn("before == (1, 1, 1, 1)", source)
        self.assertIn("def rq_payload_is_task_only(", source)
        self.assertIn("tuple(job.args) == (task_id,)", source)
        self.assertIn("dict(job.kwargs) == {}", source)
        self.assertIn("def run_dispatch_duplicate_count(", source)
        self.assertIn(
            'results["duplicate_dispatch"] += self.control.run_dispatch_duplicate_count()',
            source,
        )

    def test_runtime_services_are_pinned_to_internal_scratch_dependencies(self) -> None:
        source = _source()
        self.assertIn('for service_name in ("api", "worker", "dispatcher"):', source)
        for binding in (
            'environment.get("F1_PG_DATABASE") != expected_database',
            'environment.get("F1_PG_HOST") != "host.docker.internal"',
            'environment.get("REDIS_URL") != "redis://redis:6379/0"',
            'environment.get("MINIO_ENDPOINT") != "minio:9000"',
            'environment.get("RAGFLOW_BASE_URL") != "http://ragflow:80"',
            'api_environment.get("KEYCLOAK_URL") != "http://keycloak:8080"',
            'api_environment.get("OTEL_EXPORTER_OTLP_ENDPOINT") != "jaeger:4317"',
        ):
            self.assertIn(binding, source)
        self.assertIn("UNSAFE_RUNTIME_SCOPE", source)

    def test_resource_registry_tracks_object_etag_and_remote_ids(self) -> None:
        source = _source()
        self.assertIn("class ResourceRegistry", source)
        for field in ("object_etags", "rq_job_ids", "ragflow_document_ids", "db_ids"):
            self.assertIn(field, source)

    def test_cleanup_identifiers_are_validated_before_registry_entry(self) -> None:
        source = _source()
        self.assertIn("RUN_RESOURCE_IDENTITY_INVALID", source)
        self.assertIn('object_key.startswith(task_uuid.hex)', source)
        self.assertIn('job_id != f"f1-upload-{task_uuid}"', source)
        self.assertIn('f"f1-indexing-{task_id}"', source)
        self.assertIn("def _is_remote_id(", source)
        self.assertIn("RAGFLOW_ID_INVALID", source)

    def test_cleanup_is_guarded_by_run_id_and_etag(self) -> None:
        source = _function_source("cleanup")
        self.assertIn("registry.run_id", source)
        self.assertIn("expected_etag", source)
        self.assertIn(
            'if registry.db_ids.get("document") or registry.db_ids.get("upload_task")',
            source,
        )
        self.assertNotIn("except Exception: pass", source.replace("\n", " "))

    def test_fixed_fixture_path_and_seed_credentials_are_absent(self) -> None:
        source = _source()
        self.assertNotIn("/private/tmp/anhuan-f0", source)
        self.assertNotRegex(source, r"KC_USER_[AB]\s*=")
        self.assertNotRegex(source, r"postgresql://[^\s]+:[^\s]+@")


class ReverseSnapshotTests(unittest.TestCase):
    def test_snapshot_covers_all_five_external_planes(self) -> None:
        source = _function_source("snapshot")
        for plane in ("database", "minio", "rq", "ragflow", "audit"):
            self.assertIn(f'"{plane}"', source)
        self.assertIn('"services": services.state_digest()', source)

    def test_cleanup_registry_rejects_every_preexisting_database_identity(self) -> None:
        source = _source()
        self.assertIn("IDENTITY_COLUMNS = (", source)
        self.assertIn("database_identities=control.database_identities()", source)
        self.assertIn("self.control.bind_baseline_identities(baseline)", source)
        self.assertIn("def require_current_run_identity(", source)
        self.assertIn("PREEXISTING_RESOURCE_REUSE", source)
        for table in ("invite_jti", "enterprise_user", "user_profile", "audit_log"):
            self.assertIn(f'self.require_current_run_identity("{table}"', source)
        self.assertIn('self.control.require_current_run_identity("qa_request"', source)
        for table in ("document", "upload_task", "outbox"):
            self.assertIn(f'("{table}", row[', source)

    def test_ragflow_cleanup_baseline_is_the_same_inventory_used_in_snapshot(self) -> None:
        source = _source()
        self.assertIn("ragflow_inventory = control.ragflow_inventory()", source)
        self.assertIn("ragflow_inventory_state=ragflow_inventory", source)
        self.assertIn(
            "self.baseline_ragflow_inventory = dict(baseline.ragflow_inventory_state)",
            source,
        )

    def test_legacy_is_separate_from_current_run_delta(self) -> None:
        source = _source()
        self.assertIn("legacy_object_orphans", source)
        self.assertIn("legacy_rq_orphans", source)
        self.assertIn("current_run_delta", source)

    def test_rq_snapshot_includes_unregistered_raw_job_hashes(self) -> None:
        source = _source()
        self.assertIn("registry.get_job_ids(cleanup=False)", source)
        self.assertNotIn("registry.get_job_ids()", source)
        self.assertIn('redis.scan_iter(match="rq:job:*", count=100)', source)
        self.assertIn('("rq:execution:*", "rq:executions:*")', source)
        self.assertIn("def rq_execution_inventory(", source)
        self.assertIn('"executions": sorted(self.rq_execution_inventory())', source)
        self.assertIn('redis.scan_iter(match="f1-*", count=100)', source)
        self.assertIn('"f1_keys": sorted(self.redis_aux_inventory())', source)
        self.assertIn("RQ_KEY_INVALID", source)

    def test_after_cleanup_requires_exact_snapshot_equality(self) -> None:
        source = _function_source("main")
        self.assertIn("after_cleanup != before", source)

    def test_dependency_unreachable_is_a_failure_code(self) -> None:
        source = _source()
        self.assertIn("DEPENDENCY_UNREACHABLE", source)
        self.assertNotRegex(source, r"DEPENDENCY_UNREACHABLE[^\n]+return\s+0")

    def test_only_http_helpers_are_used_for_business_mutations(self) -> None:
        source = _source()
        for forbidden in (
            "invitation.create_invite(",
            "reserve_api_upload(",
            "reserve_request(",
            "process_upload(",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("class HttpBusinessClient", source)

    def test_cross_tenant_business_endpoints_require_uniform_not_found(self) -> None:
        source = _source()
        self.assertIn("status_read == 404", source)
        self.assertIn("status_write == 404", source)
        self.assertIn("cross_status == 404", source)
        self.assertIn("docs_status == 404", source)
        self.assertNotIn("status_write in {403, 404}", source)

    def test_valid_http_answer_requires_exact_inline_verified_citation_set(self) -> None:
        source = _source()
        self.assertIn("def _answer_reference_ids(", source)
        self.assertIn("citation_ids == _answer_reference_ids(answer)", source)
        self.assertIn("ANSWER_CITATION_INVALID", source)

    def test_output_has_no_dynamic_error_or_snapshot_payload(self) -> None:
        source = _source()
        self.assertNotIn("sys.stderr.write", source)
        self.assertNotIn("traceback.print", source)
        self.assertNotRegex(source, r"print\([^\n]*(?:error|before|after|snapshot)")

    def test_leak_scan_requires_external_secret_and_pii_canaries(self) -> None:
        source = _source()
        self.assertIn('secrets.read("leak_canaries"', source)
        self.assertIn("self.config.leak_canaries", source)
        self.assertIn("self.http.token_canaries()", source)
        self.assertIn('str(ROOT).encode("utf-8")', source)
        self.assertIn('str(fixture.location).encode("utf-8")', source)
        self.assertIn('fixture.location.name.encode("utf-8")', source)
        self.assertIn("json.dumps(decoded, ensure_ascii=True)", source)
        self.assertIn('urllib.parse.quote(decoded, safe="")', source)
        self.assertIn("SELECT row_to_json(a)::text FROM f1.audit_log", source)


if __name__ == "__main__":
    unittest.main()
