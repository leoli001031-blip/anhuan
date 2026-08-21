"""Database-free contracts for the same-stack P3 ingestion verifier."""
from __future__ import annotations

import dataclasses
import contextlib
import inspect
import io
import json
import os
import stat
import tempfile
import unittest
import uuid
from unittest import mock

import infra.f1.local_ingestion_verify as ingestion
from infra.f1.local_ingestion_verify import (
    ADMIN_SUB,
    EXPECTED_AUDIT_ACTIONS,
    DataObservation,
    IngestionVerificationCounts,
    IngestionVerifyError,
    TaskObjectIdentity,
    render_success,
    resource_names,
    rewrite_dsn_database,
    scratch_database_name,
    verify_data_observation,
)


SOURCE_DATABASE = "anhuan_closeout_0123456789abcdef01234567"
NONCE_A = "a" * 32
NONCE_B = "b" * 32
SHA256 = "c" * 64
TASK_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def _valid_observation() -> DataObservation:
    return DataObservation(
        task_id=TASK_ID,
        object_key=f"{TASK_ID.hex}.pdf",
        content_sha256=SHA256,
        source_size=512,
        source_etag="opaque-etag",
        status="done",
        object_state="ready",
        processing_stage="ready",
        quarantine_status="released",
        scan_verdict="clean",
        scanner_engine="clamav",
        scanner_version="1.4.6",
        signature_version="fixture-signature",
        preview_status="ready",
        preview_kind="page_text",
        preview_sha256="d" * 64,
        preview_unit_count=1,
        released=True,
        audit_actions=EXPECTED_AUDIT_ACTIONS,
    )


class EngineeringCloseoutIngestionVerifierTests(unittest.TestCase):
    def test_scratch_and_buckets_are_random_and_project_bound(self) -> None:
        scratch_a = scratch_database_name(SOURCE_DATABASE, NONCE_A)
        scratch_b = scratch_database_name(SOURCE_DATABASE, NONCE_B)
        self.assertEqual(
            scratch_a,
            "anhuan_ingest_0123456789ab_aaaaaaaaaaaaaaaa",
        )
        self.assertNotEqual(scratch_a, scratch_b)

        resources_a = resource_names(SOURCE_DATABASE, NONCE_A)
        resources_b = resource_names(SOURCE_DATABASE, NONCE_B)
        self.assertEqual(len(set(resources_a.buckets)), 3)
        self.assertTrue(
            all(name.startswith("anhuan-ingest-0123456789ab-") for name in resources_a.buckets)
        )
        self.assertTrue(set(resources_a.buckets).isdisjoint(resources_b.buckets))

        for invalid in (
            "postgres",
            "anhuan_closeout_short",
            "shared_0123456789abcdef01234567",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(
                    IngestionVerifyError,
                    "^LOCAL_INGESTION_SOURCE_INVALID$",
                ):
                    scratch_database_name(invalid, NONCE_A)

    def test_dsn_rewrite_changes_only_the_database(self) -> None:
        source = (
            "postgresql://f0d_bootstrap:encoded%2Fsecret@postgres:5432/"
            + SOURCE_DATABASE
        )
        scratch = scratch_database_name(SOURCE_DATABASE, NONCE_A)
        rewritten = rewrite_dsn_database(
            source,
            database=scratch,
            expected_user="f0d_bootstrap",
        )
        self.assertEqual(
            rewritten,
            "postgresql://f0d_bootstrap:encoded%2Fsecret@postgres:5432/"
            + scratch,
        )
        self.assertNotIn("?", rewritten)
        with self.assertRaisesRegex(
            IngestionVerifyError,
            "^LOCAL_INGESTION_SOURCE_INVALID$",
        ):
            rewrite_dsn_database(
                source.replace("@postgres:", "@external.invalid:"),
                database=scratch,
                expected_user="f0d_bootstrap",
            )

    def test_final_data_requires_sha_state_scanner_preview_and_audit(self) -> None:
        verify_data_observation(
            _valid_observation(),
            expected_sha256=SHA256,
            expected_size=512,
        )
        mutations = (
            {"content_sha256": "e" * 64},
            {"quarantine_status": "held"},
            {"scan_verdict": "unavailable"},
            {"scanner_engine": ""},
            {"preview_status": "blocked"},
            {"released": False},
            {"audit_actions": frozenset(EXPECTED_AUDIT_ACTIONS - {"document.version.release"})},
        )
        for changes in mutations:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(
                    IngestionVerifyError,
                    "^LOCAL_INGESTION_DATA_IDENTITY_MISMATCH$",
                ):
                    verify_data_observation(
                        dataclasses.replace(_valid_observation(), **changes),
                        expected_sha256=SHA256,
                        expected_size=512,
                    )

    def test_object_cleanup_accepts_only_exact_run_owned_keys(self) -> None:
        resources = resource_names(SOURCE_DATABASE, NONCE_A)
        identity = TaskObjectIdentity(
            task_id=TASK_ID,
            object_key=f"{TASK_ID.hex}.pdf",
        )
        identities = (identity,)
        ingestion._validate_bucket_object(
            resources.quarantine_bucket,
            identity.object_key,
            resources,
            identities,
        )
        ingestion._validate_bucket_object(
            resources.released_bucket,
            identity.object_key,
            resources,
            identities,
        )
        ingestion._validate_bucket_object(
            resources.preview_bucket,
            f"{TASK_ID.hex}/{'b' * 32}.json",
            resources,
            identities,
        )
        for bucket, object_name in (
            (resources.quarantine_bucket, f"{'f' * 32}.pdf"),
            (resources.preview_bucket, f"{'f' * 32}/{'b' * 32}.json"),
            (resources.preview_bucket, f"{TASK_ID.hex}/foreign.bin"),
        ):
            with self.subTest(bucket=bucket, object_name=object_name):
                with self.assertRaisesRegex(
                    IngestionVerifyError,
                    "^LOCAL_INGESTION_OBJECT_CLEANUP_FAILED$",
                ):
                    ingestion._validate_bucket_object(
                        bucket,
                        object_name,
                        resources,
                        identities,
                    )

    def test_runner_has_no_container_control_or_core_scanner_override(self) -> None:
        source = inspect.getsource(ingestion)
        api_source = inspect.getsource(ingestion._api_smoke)
        execute_source = inspect.getsource(ingestion._execute_scratch_probe)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("docker", source.lower())
        self.assertNotIn("processor.scan_stream", source)
        self.assertNotIn("scan_stream =", source)
        self.assertEqual(api_source.count("dependency_overrides["), 1)
        self.assertIn("dependency_overrides[auth.current_user]", api_source)
        self.assertIn('scanner_parameters["host"].default', execute_source)
        self.assertIn("EXPECTED_CLAMD_HOST", execute_source)
        self.assertIn('scanner_parameters["port"].default', execute_source)
        self.assertIn("EXPECTED_CLAMD_PORT", execute_source)
        self.assertIn("EXPECTED_MINIO_ENDPOINT", execute_source)

    def test_real_fault_chains_are_bounded_and_recover_through_product_paths(self) -> None:
        api_source = inspect.getsource(ingestion._api_smoke)
        self.assertIn("_temporary_wrong_minio_password(secret_directory)", api_source)
        self.assertIn('"SOURCE_OBJECT_STAT_FAILED"', api_source)
        self.assertIn("document_id != failed_document_id", api_source)
        self.assertIn("version_id != failed_version_id", api_source)
        self.assertIn("correct_client.bucket_exists(bucket)", api_source)
        self.assertIn("_assert_upload_write_failure(", api_source)
        self.assertIn("_bound_unlistened_loopback()", api_source)
        self.assertIn('scanner_host="127.0.0.1"', api_source)
        self.assertIn("scanner_port=unavailable_port", api_source)
        self.assertIn("_assert_scanner_retry_wait(", api_source)
        self.assertIn(
            'f"/api/v1/ingestion/versions/{version_id}/retry"',
            api_source,
        )
        self.assertNotIn("mock.patch", api_source)

        upload_state = inspect.getsource(ingestion._assert_upload_write_failure)
        self.assertIn('"write_failed"', upload_state)
        self.assertIn('"blocked"', upload_state)
        self.assertIn("idempotency_key_sha256=%s", upload_state)
        scanner_state = inspect.getsource(ingestion._assert_scanner_retry_wait)
        self.assertIn('"retry_wait"', scanner_state)
        self.assertIn('"held"', scanner_state)
        self.assertIn('"unavailable"', scanner_state)
        self.assertIn('"P3_SCANNER_UNAVAILABLE"', scanner_state)

    def test_wrong_minio_secret_is_private_temporary_and_restored(self) -> None:
        environment_name = "F1_MINIO_ROOT_PASSWORD_FILE"
        original = os.environ.get(environment_name)
        try:
            os.environ[environment_name] = "synthetic-original-secret-file"
            with tempfile.TemporaryDirectory() as raw_directory:
                directory = ingestion.Path(raw_directory)
                os.chmod(directory, 0o700)
                path = directory / "minio_fault_password"
                with ingestion._temporary_wrong_minio_password(directory):
                    self.assertNotEqual(
                        os.environ[environment_name],
                        "synthetic-original-secret-file",
                    )
                    info = path.lstat()
                    self.assertTrue(stat.S_ISREG(info.st_mode))
                    self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
                self.assertEqual(
                    os.environ[environment_name],
                    "synthetic-original-secret-file",
                )
                self.assertFalse(path.exists())
        finally:
            if original is None:
                os.environ.pop(environment_name, None)
            else:
                os.environ[environment_name] = original

    def test_bound_loopback_port_is_reserved_without_a_listener(self) -> None:
        source = inspect.getsource(ingestion._bound_unlistened_loopback)
        self.assertIn('reservation.bind(("127.0.0.1", 0))', source)
        self.assertIn("reservation.getsockname()[1]", source)
        self.assertIn("reservation.close()", source)
        self.assertNotIn(".listen(", source)

    def test_release_gate_is_checked_before_real_processing(self) -> None:
        api_source = inspect.getsource(ingestion._api_smoke)
        first_release = api_source.index(
            'f"/api/v1/ingestion/versions/{version_id}/release"'
        )
        process = api_source.index("await process_controlled_ingestion(")
        self.assertLess(first_release, process)
        before_process = api_source[first_release:process]
        self.assertIn("409", before_process)

    def test_database_and_object_cleanup_are_exact_and_fail_closed(self) -> None:
        drop = inspect.getsource(ingestion._drop_scratch_database)
        buckets = inspect.getsource(ingestion._cleanup_buckets)
        self.assertIn('sql.SQL("DROP DATABASE {} WITH (FORCE)")', drop)
        self.assertIn("sql.Identifier(scratch_database)", drop)
        self.assertNotIn("DROP DATABASE IF EXISTS", drop)
        self.assertIn("client.remove_object(bucket, object_name)", buckets)
        self.assertIn("client.remove_bucket(bucket)", buckets)
        self.assertNotIn("remove_objects", buckets)
        self.assertNotIn("list_buckets", buckets)

    def test_success_output_is_fixed_integer_metrics_only(self) -> None:
        metrics, tag = render_success(IngestionVerificationCounts())
        decoded = json.loads(metrics)
        self.assertEqual(tag, "LOCAL_INGESTION_VERIFY_OK")
        self.assertTrue(decoded)
        self.assertTrue(all(type(value) is int for value in decoded.values()))
        self.assertEqual(decoded["uploaded_version_count"], 1)
        self.assertEqual(decoded["minio_write_failure_count"], 1)
        self.assertEqual(decoded["idempotent_upload_recovery_count"], 1)
        self.assertEqual(decoded["scanner_unavailable_count"], 1)
        self.assertEqual(decoded["scanner_retry_recovery_count"], 1)
        self.assertEqual(decoded["audit_action_count"], 5)
        self.assertEqual(decoded["cross_tenant_api_visible_count"], 0)
        self.assertEqual(decoded["object_residual_count"], 0)
        for forbidden in (
            SOURCE_DATABASE,
            "postgresql://",
            "local-ingestion.pdf",
            "anhuan-ingest-",
            ADMIN_SUB,
            "password",
        ):
            self.assertNotIn(forbidden, metrics)

    def test_command_surface_emits_only_whitelisted_shapes(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(
                ingestion,
                "run",
                return_value=IngestionVerificationCounts(),
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(ingestion.main(), 0)
        self.assertEqual(stderr.getvalue(), "")
        lines = stdout.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(type(value) is int for value in json.loads(lines[0]).values()))
        self.assertEqual(lines[1], "LOCAL_INGESTION_VERIFY_OK")

        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(
                ingestion,
                "run",
                side_effect=IngestionVerifyError(
                    "LOCAL_INGESTION_PROCESS_FAILED"
                ),
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(ingestion.main(), 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(
            stderr.getvalue(),
            "LOCAL_INGESTION_PROCESS_FAILED\n",
        )

        execute_source = inspect.getsource(ingestion._execute_scratch_probe)
        self.assertIn("contextlib.redirect_stdout(sink)", execute_source)
        self.assertIn("contextlib.redirect_stderr(sink)", execute_source)

    def test_unknown_failures_collapse_to_one_internal_code(self) -> None:
        error = IngestionVerifyError("credential-value-or-path")
        self.assertEqual(error.reason, "LOCAL_INGESTION_INTERNAL_ERROR")
        self.assertEqual(str(error), "LOCAL_INGESTION_INTERNAL_ERROR")


if __name__ == "__main__":
    unittest.main()
