"""Static recovery-contract checks that never touch shared DB or services."""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class RecoverySchemaContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.migration = source(
            "infra/f1/alembic/versions/f1_0004_repair_boundaries.py"
        )

    def test_linear_successor(self) -> None:
        self.assertIn('down_revision: str | None = "f1_0003"', self.migration)

    def test_upload_owner_columns(self) -> None:
        for name in (
            "object_state", "source_etag", "source_size", "lease_token",
            "lease_owner", "lease_acquired_at", "next_attempt_at",
        ):
            self.assertIn(name, self.migration)

    def test_outbox_owner_columns(self) -> None:
        for name in (
            "rq_job_id", "dispatch_token", "dispatch_lease_until",
            "dispatch_attempt",
        ):
            self.assertIn(name, self.migration)

    def test_claim_functions_are_security_definer(self) -> None:
        for name in (
            "claim_upload_task", "renew_upload_lease",
            "claim_pending_dispatch", "complete_dispatch",
        ):
            pattern = rf"CREATE FUNCTION f1\.{name}\([\s\S]+?SECURITY DEFINER"
            self.assertRegex(self.migration, pattern)

    def test_claim_functions_revoke_public(self) -> None:
        for signature in (
            "f1.claim_upload_task(uuid,text,integer)",
            "f1.renew_upload_lease(uuid,uuid,integer)",
            "f1.claim_pending_dispatch(integer,integer)",
            "f1.complete_dispatch(uuid,uuid,boolean)",
        ):
            self.assertIn(signature, self.migration)
        self.assertGreaterEqual(self.migration.count("REVOKE ALL ON FUNCTION"), 4)


class UploadRecoveryRuntimeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.storage = source("src/platform_foundation/f1/storage.py")
        cls.tasks = source("src/platform_foundation/f1/upload_task.py")
        cls.worker = source("src/platform_foundation/f1/worker_pipeline.py")
        cls.router = source("src/platform_foundation/f1/api/routers/documents.py")

    def test_all_edited_modules_parse(self) -> None:
        for path in (
            "src/platform_foundation/f1/storage.py",
            "src/platform_foundation/f1/upload_task.py",
            "src/platform_foundation/f1/worker_pipeline.py",
            "src/platform_foundation/f1/indexing.py",
            "src/platform_foundation/f1/ragflow_provision.py",
            "src/platform_foundation/f1/api/routers/documents.py",
        ):
            ast.parse(source(path), filename=path)

    def test_opaque_key_is_task_bound_not_filename_bound(self) -> None:
        function = self.storage.split("def opaque_object_key", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("task_id.hex", function)
        self.assertNotIn("filename", function)

    def test_store_accepts_pre_registered_key(self) -> None:
        self.assertIn("object_key: str | None = None", self.storage)
        self.assertIn("OBJECT_KEY_INVALID", self.storage)

    def test_store_readback_hash_is_mandatory(self) -> None:
        self.assertIn("def stat_and_hash_object", self.storage)
        self.assertIn("def verify_stored_object", self.storage)
        self.assertIn("SOURCE_HASH_MISMATCH", self.storage)
        self.assertIn("SOURCE_SIZE_MISMATCH", self.storage)

    def test_reserve_is_not_ready(self) -> None:
        function = self.tasks.split("async def reserve_api_upload", 1)[1].split(
            "\n\nasync def ", 1
        )[0]
        self.assertIn("'reserved'", function)
        self.assertNotIn("object_state, 'ready'", function)

    def test_same_sha_integrity_race_resolves_winner(self) -> None:
        self.assertIn("except IntegrityError", self.tasks)
        self.assertIn("_reservation_for_sha", self.tasks)
        self.assertIn("UPLOAD_RESERVATION_CONFLICT", self.tasks)

    def test_finalize_ready_and_audit_share_transaction(self) -> None:
        function = self.tasks.split("async def finalize_upload_object", 1)[1].split(
            "\n\nasync def ", 1
        )[0]
        self.assertIn("object_state='ready'", function)
        self.assertIn("INSERT INTO f1.audit_log", function)
        self.assertEqual(function.count("await session.commit()"), 1)

    def test_router_order_is_reserve_store_verify_finalize_enqueue(self) -> None:
        positions = [
            self.router.index(token)
            for token in (
                "reserve_api_upload(", "store_stream(", "verify_stored_object(",
                "finalize_upload_object(", "enqueue_upload(",
            )
        ]
        self.assertEqual(positions, sorted(positions))

    def test_deterministic_rq_identity(self) -> None:
        self.assertIn('return f"f1-upload-{task_id}"', self.tasks)
        self.assertIn("job_id=stable_job_id", self.tasks)

    def test_dispatcher_uses_claim_and_completion_cas(self) -> None:
        self.assertIn("f1.claim_pending_dispatch(100, 60)", self.worker)
        self.assertIn("f1.complete_dispatch(:id, :token, :success)", self.worker)
        self.assertNotIn("pending_dispatch_tasks()", self.worker)

    def test_compose_runs_a_periodic_dispatcher_with_its_own_health(self) -> None:
        compose = source("infra/f1/docker-compose.yml")
        dispatcher = source("src/platform_foundation/f1/dispatcher.py")
        self.assertIn("  dispatcher:", compose)
        self.assertIn("platform_foundation.f1.dispatcher", compose)
        self.assertIn("f1-dispatcher-heartbeat", compose)
        self.assertIn("dispatch_pending_outbox()", dispatcher)
        self.assertIn("_STOP.wait(interval)", dispatcher)

    def test_worker_claims_before_source_read(self) -> None:
        claim = self.worker.index("claim_upload_task(task_id)")
        verify = self.worker.index("verify_stored_object(")
        index = self.worker.index("indexing.process_upload(")
        self.assertLess(claim, verify)
        self.assertLess(verify, index)

    def test_worker_binds_exact_claim_context(self) -> None:
        self.assertIn("task_id=claim.task_id", self.worker)
        self.assertIn("lease_token=claim.lease_token", self.worker)
        self.assertGreaterEqual(self.worker.count("claim.lease_token"), 3)


class RagflowReconcileContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.indexing = source("src/platform_foundation/f1/indexing.py")
        cls.provision = source("src/platform_foundation/f1/ragflow_provision.py")

    def test_reconcile_identity_binds_chunk_and_body_sha(self) -> None:
        self.assertIn('f"chunk_id={chunk_id}"', self.indexing)
        self.assertIn('f"body_sha256={chunk[\'body_sha256\']}"', self.indexing)
        self.assertIn("_canonical_remote_chunks", self.indexing)

    def test_reconcile_rejects_duplicate_or_conflicting_remote(self) -> None:
        self.assertIn("chunk_id in canonical", self.indexing)
        self.assertIn("RAGFLOW_RECONCILE_MISMATCH", self.indexing)

    def test_stale_owner_is_checked_before_external_chunk_add(self) -> None:
        add_position = self.indexing.index("client.add_chunk(")
        guard_position = self.indexing.rfind("_guard_lease(lease_guard)", 0, add_position)
        self.assertGreaterEqual(guard_position, 0)

    def test_index_failure_is_retryable(self) -> None:
        self.assertIn('outcome = "retry"', self.indexing)
        self.assertIn('reason = "RAGFLOW_UNAVAILABLE"', self.indexing)
        self.assertIn("next_attempt_at", self.indexing)

    def test_dataset_and_scope_creation_use_cross_process_lock(self) -> None:
        self.assertIn("def ragflow_lock", self.provision)
        self.assertIn("with ragflow_lock(name)", self.provision)
        self.assertIn('with ragflow_lock(f"scope-{scope_id.hex}")', self.indexing)

    def test_plaintext_decode_is_strict(self) -> None:
        self.assertIn('decode("utf-8", errors="strict")', self.indexing)
        self.assertNotIn('decode("utf-8", errors="replace")', self.indexing)


if __name__ == "__main__":
    unittest.main()
