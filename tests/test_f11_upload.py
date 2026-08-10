"""F1.1 upload-flow tests: streaming gate, SHA registration, worker recovery."""
from __future__ import annotations

import asyncio
import unittest
import uuid

from platform_foundation.f1 import indexing
from platform_foundation.f1.upload_task import (
    create_upload_task,
    get_task,
)

from f11_support import (
    ENTERPRISE_A,
    ENTERPRISE_B,
    SUB_ADMIN,
    SUB_TENANT_B,
    configure_formal_runtime,
    create_document,
    registered_fixture_sha,
)


def setUpModule() -> None:
    configure_formal_runtime()


class F11UploadTests(unittest.TestCase):
    def test_unregistered_sha_fails_gate(self) -> None:
        task_id = asyncio.run(
            create_upload_task(
                enterprise_id=ENTERPRISE_A,
                document_id=create_document(),
                object_key=f"u-{uuid.uuid4().hex}.pdf",
                content_sha256=uuid.uuid4().hex * 2,
                sub=SUB_ADMIN,
            )
        )
        asyncio.run(indexing.process_upload(task_id, ENTERPRISE_A))
        record = asyncio.run(
            get_task(task_id, enterprise_id=ENTERPRISE_A, sub=SUB_ADMIN, role="f1_api")
        )
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["error_reason"], "FIXTURE_ONLY_UNREGISTERED")

    def test_synthetic_tenant_b_has_no_fixtures(self) -> None:
        task_id = asyncio.run(
            create_upload_task(
                enterprise_id=ENTERPRISE_B,
                document_id=create_document(ENTERPRISE_B, SUB_TENANT_B),
                object_key=f"u-{uuid.uuid4().hex}.pdf",
                content_sha256=uuid.uuid4().hex * 2,
                sub=SUB_TENANT_B,
            )
        )
        asyncio.run(indexing.process_upload(task_id, ENTERPRISE_B))
        record = asyncio.run(
            get_task(task_id, enterprise_id=ENTERPRISE_B, sub=SUB_TENANT_B, role="f1_api")
        )
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["error_reason"], "FIXTURE_ONLY_UNREGISTERED")

    def test_registered_sha_reaches_indexing_state(self) -> None:
        # The first registered fixture SHA (PDF, 114 chunks) is registered for
        # tenant A; the gate must accept it and move to indexing (the RAGFlow
        # write itself is gated on a valid embedding key, so the task may end
        # failed with INDEXING_FAILED — but never FIXTURE_ONLY_UNREGISTERED).
        sha = registered_fixture_sha()
        task_id = asyncio.run(
            create_upload_task(
                enterprise_id=ENTERPRISE_A,
                document_id=create_document(),
                object_key=f"u-{uuid.uuid4().hex}.pdf",
                content_sha256=sha,
                sub=SUB_ADMIN,
            )
        )
        asyncio.run(indexing.process_upload(task_id, ENTERPRISE_A))
        record = asyncio.run(
            get_task(task_id, enterprise_id=ENTERPRISE_A, sub=SUB_ADMIN, role="f1_api")
        )
        self.assertNotEqual(record["error_reason"], "FIXTURE_ONLY_UNREGISTERED")

    def test_outbox_dispatched_event_exists(self) -> None:
        from sqlalchemy import text

        from platform_foundation.f1.database import session_scope

        task_id = asyncio.run(
            create_upload_task(
                enterprise_id=ENTERPRISE_A,
                document_id=create_document(),
                object_key=f"u-{uuid.uuid4().hex}.pdf",
                content_sha256=uuid.uuid4().hex * 2,
                sub=SUB_ADMIN,
            )
        )

        async def _count() -> int:
            async with session_scope(role="f1_api", enterprise_id=ENTERPRISE_A, sub=SUB_ADMIN) as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM f1.outbox "
                            "WHERE task_id = :id AND event_type = 'upload.dispatched'"
                        ),
                        {"id": task_id},
                    )
                ).fetchone()
                return int(row[0])

        self.assertEqual(asyncio.run(_count()), 1)

    def test_worker_pipeline_no_crash_on_duplicate_delivery(self) -> None:
        from platform_foundation.f1.worker_pipeline import process_task

        task_id = asyncio.run(
            create_upload_task(
                enterprise_id=ENTERPRISE_A,
                document_id=create_document(),
                object_key=f"u-{uuid.uuid4().hex}.pdf",
                content_sha256=uuid.uuid4().hex * 2,
                sub=SUB_ADMIN,
            )
        )
        # Two deliveries must not raise.
        process_task(str(task_id))
        process_task(str(task_id))

    def test_worker_lease_claim_updates_attempt(self) -> None:
        from platform_foundation.f1.upload_task import claim_upload_task

        task_id = asyncio.run(
            create_upload_task(
                enterprise_id=ENTERPRISE_A,
                document_id=create_document(),
                object_key=f"u-{uuid.uuid4().hex}.pdf",
                content_sha256=uuid.uuid4().hex * 2,
                sub=SUB_ADMIN,
            )
        )
        claimed = asyncio.run(claim_upload_task(task_id))
        self.assertIsNotNone(claimed)
        record = asyncio.run(
            get_task(task_id, enterprise_id=ENTERPRISE_A, sub=SUB_ADMIN, role="f1_api")
        )
        self.assertEqual(claimed.task_id, task_id)
        self.assertIsNotNone(claimed.lease_token)
        self.assertEqual(record["status"], "scanning")
        self.assertGreaterEqual(record["attempt"], 1)

    def test_upload_rejects_container_mismatch(self) -> None:
        import io

        from platform_foundation.f1.storage import StorageError, stream_upload

        with self.assertRaises(StorageError) as ctx:
            stream_upload("fake.pdf", "application/pdf", io.BytesIO(b"not-a-pdf"))
        self.assertEqual(str(ctx.exception), "CONTAINER_MISMATCH")

    def test_upload_rejects_unsupported_mime(self) -> None:
        import io

        from platform_foundation.f1.storage import StorageError, stream_upload

        with self.assertRaises(StorageError) as ctx:
            stream_upload("x.exe", "application/x-msdownload", io.BytesIO(b"data"))
        self.assertEqual(str(ctx.exception), "FILE_TYPE_NOT_ALLOWED")

    def test_upload_rejects_oversize(self) -> None:
        import io

        from platform_foundation.f1.storage import MAX_SIZE_BYTES, StorageError, stream_upload

        with self.assertRaises(StorageError) as ctx:
            stream_upload(
                "big.pdf", "application/pdf", io.BytesIO(b"%PDF-" + b"x" * (MAX_SIZE_BYTES + 1))
            )
        self.assertEqual(str(ctx.exception), "FILE_TOO_LARGE")

    def test_upload_streaming_computes_sha(self) -> None:
        import io

        from platform_foundation.f1.storage import stream_upload

        payload = b"%PDF-1.4\n" + b"x" * 1000
        stored = stream_upload("s.pdf", "application/pdf", io.BytesIO(payload))

        def remove() -> None:
            from platform_foundation.f1 import storage

            storage._client().remove_object(storage.BUCKET, stored.object_key)
            self.assertFalse(storage.object_exists(stored.object_key))

        self.addCleanup(remove)
        self.assertEqual(stored.size, len(payload))
        self.assertEqual(len(stored.sha256), 64)


if __name__ == "__main__":
    unittest.main()
