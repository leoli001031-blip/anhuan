"""Native worker source binding and stale-result suppression, without Redis/PG."""
import asyncio
import hashlib
import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch

os.environ.setdefault("F1_KEYCLOAK_ISSUER_URL", "http://native.invalid/realms/anhuan")

from platform_foundation.f1.features.evidence import repository, worker
from platform_foundation.f1.features.evidence.docx_native import PARSER_VERSION, SUPPORT_PROFILE
from tests.test_native_evidence import package, p


def claim_for(raw):
    return repository.NativeClaim(*(uuid.uuid4() for _ in range(9)),
        hashlib.sha256(raw).hexdigest(), len(raw), "source-etag", "a" * 32 + ".docx",
        "native-test-actor", 1, PARSER_VERSION, SUPPORT_PROFILE, 1)


class NativeWorkerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.claim = claim_for(package(p("监测原件")))
        self.enabled = patch.object(repository, "native_extraction_enabled", return_value=True)
        self.enabled.start()
        self.addCleanup(self.enabled.stop)

    async def test_disabled_and_stale_work_do_not_read_any_source(self):
        with patch.object(repository, "read_claim", AsyncMock(return_value=None)) as read:
            with patch.object(repository, "native_extraction_enabled", return_value=False):
                self.assertEqual(await worker.run_job(self.claim.job_id, self.claim.lease_token), "DISABLED")
                read.assert_not_awaited()
            self.assertEqual(await worker.run_job(self.claim.job_id, self.claim.lease_token), "STALE")

    async def test_renew_failure_discards_finished_parse(self):
        with patch.object(repository, "read_claim", AsyncMock(return_value=self.claim)), \
             patch.object(worker, "_extract_payload", return_value={}), \
             patch.object(repository, "renew_lease", AsyncMock(return_value=False)), \
             patch.object(repository, "finalize", AsyncMock()) as finalize:
            self.assertEqual(await worker.run_job(self.claim.job_id, self.claim.lease_token), "STALE")
            finalize.assert_not_awaited()

    async def test_partial_result_is_finalized_once_without_retry(self):
        payload = {"coverage_state": "partial", "fragments": [], "debts": ["unsupported"]}
        with patch.object(repository, "read_claim", AsyncMock(return_value=self.claim)), \
             patch.object(worker, "_extract_payload", return_value=payload), \
             patch.object(repository, "renew_lease", AsyncMock(return_value=True)), \
             patch.object(repository, "finalize", AsyncMock(return_value=self.claim.revision_id)) as finalize, \
             patch.object(repository, "finish_failure", AsyncMock()) as fail:
            self.assertEqual(await worker.run_job(self.claim.job_id, self.claim.lease_token), "DONE")
            finalize.assert_awaited_once_with(self.claim, payload)
            fail.assert_not_awaited()

    async def test_heartbeat_lease_loss_stops_further_renewals(self):
        stop, lost = asyncio.Event(), asyncio.Event()
        with patch.object(repository, "renew_lease", AsyncMock(return_value=False)) as renew:
            await worker._heartbeat(self.claim, stop, lost, interval=0.001)
            self.assertTrue(lost.is_set())
            renew.assert_awaited_once()

    async def test_source_failure_has_bounded_reason_and_retry_class(self):
        for retryable, expected in [(True, "RETRY"), (False, "BLOCKED")]:
            with patch.object(repository, "read_claim", AsyncMock(return_value=self.claim)), \
                 patch.object(worker, "_extract_payload", side_effect=worker.NativeSourceError("NATIVE_SOURCE_INVALID", retryable=retryable)), \
                 patch.object(repository, "finish_failure", AsyncMock(return_value=True)) as fail:
                self.assertEqual(await worker.run_job(self.claim.job_id, self.claim.lease_token), expected)
                fail.assert_awaited_once_with(self.claim.job_id, self.claim.lease_token,
                    reason="NATIVE_SOURCE_INVALID", retry_seconds=30 if retryable else None)

    async def test_uncertain_finalize_does_not_try_another_result_write(self):
        with patch.object(repository, "read_claim", AsyncMock(return_value=self.claim)), \
             patch.object(worker, "_extract_payload", return_value={}), \
             patch.object(repository, "renew_lease", AsyncMock(return_value=True)), \
             patch.object(repository, "finalize", AsyncMock(side_effect=OSError("sensitive database details"))) as finalize, \
             patch.object(repository, "finish_failure", AsyncMock(return_value=False)) as fail:
            self.assertEqual(await worker.run_job(self.claim.job_id, self.claim.lease_token), "RETRY")
            finalize.assert_awaited_once()
            fail.assert_awaited_once_with(self.claim.job_id, self.claim.lease_token,
                reason="NATIVE_EXTRACTION_FAILED", retry_seconds=30)


class NativeSourceTests(unittest.TestCase):
    def test_verifies_same_bytes_before_parser_and_never_uses_preview(self):
        from platform_foundation.f1 import storage
        raw = package(p("完整源内容"))
        claim = claim_for(raw)
        with patch.object(storage, "read_released_native_source", return_value=raw) as read, \
             patch.object(worker, "extract_docx", return_value="parsed") as parse, \
             patch.object(worker, "build_docx_payload", return_value={}) as envelope:
            worker._extract_payload(claim)
            read.assert_called_once_with(claim.object_key, claim.source_sha256, len(raw))
            parse.assert_called_once_with(raw, expected_sha256=claim.source_sha256)
            envelope.assert_called_once()
        with patch.object(storage, "read_released_native_source", return_value=raw + b"changed"), \
             patch.object(worker, "extract_docx") as parse:
            with self.assertRaisesRegex(worker.NativeSourceError, "NATIVE_SOURCE_INVALID"):
                worker._extract_payload(claim)
            parse.assert_not_called()

    def test_release_copy_etag_can_differ_but_sha_and_size_must_match(self):
        from platform_foundation.f1 import storage
        from types import SimpleNamespace
        raw = b"verified original"
        sha = hashlib.sha256(raw).hexdigest()
        key = "a" * 32 + ".docx"
        with patch.object(storage, "_read_bucket_bytes", return_value=(SimpleNamespace(sha256=sha, size=len(raw), etag="new-copy-etag"), raw)) as read:
            self.assertEqual(storage.read_released_native_source(key, sha, len(raw)), raw)
            read.assert_called_once_with(storage.BUCKET, key, max_bytes=len(raw))
            with self.assertRaisesRegex(storage.StorageError, "SOURCE_IDENTITY_MISMATCH"):
                storage.read_released_native_source(key, "b" * 64, len(raw))


if __name__ == "__main__":
    unittest.main()
