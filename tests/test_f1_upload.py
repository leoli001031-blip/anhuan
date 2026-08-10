"""F1 upload task tests: DB-backed pipeline + idempotency.

Requires PostgreSQL (f1 schema), Redis :6379.  No secrets in assertions.
"""
from __future__ import annotations

import asyncio
import unittest
import uuid

from platform_foundation.f1 import upload_task

from f11_support import (
    ENTERPRISE_A,
    SUB_ADMIN,
    configure_formal_runtime,
    create_document,
)


def setUpModule() -> None:
    configure_formal_runtime()


class F1UploadTests(unittest.TestCase):
    def _create(
        self, sha: str | None = None, document_id: uuid.UUID | None = None
    ) -> uuid.UUID:
        return asyncio.run(
            upload_task.create_upload_task(
                enterprise_id=ENTERPRISE_A,
                document_id=document_id or create_document(),
                object_key=f"obj-{uuid.uuid4().hex}.pdf",
                content_sha256=sha or (uuid.uuid4().hex * 2),
                sub=SUB_ADMIN,
            )
        )

    def test_create_task_persists_pending(self) -> None:
        task_id = self._create()
        record = asyncio.run(
            upload_task.get_task(task_id, enterprise_id=ENTERPRISE_A, sub=SUB_ADMIN)
        )
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "pending")
        self.assertTrue(record["task_id"])

    def test_duplicate_sha_is_idempotent(self) -> None:
        sha = uuid.uuid4().hex * 2
        document_id = create_document()
        first = self._create(sha, document_id)
        second = self._create(sha, document_id)
        self.assertEqual(first, second)

    def test_list_tasks_scoped_to_enterprise(self) -> None:
        self._create()
        tasks = asyncio.run(upload_task.list_tasks(enterprise_id=ENTERPRISE_A, sub=SUB_ADMIN))
        self.assertIsInstance(tasks, list)
        self.assertGreaterEqual(len(tasks), 1)

    def test_get_missing_task_returns_none(self) -> None:
        record = asyncio.run(
            upload_task.get_task(uuid.uuid4(), enterprise_id=ENTERPRISE_A, sub=SUB_ADMIN)
        )
        self.assertIsNone(record)

    def test_outbox_event_created_with_task(self) -> None:
        from sqlalchemy import text

        from platform_foundation.f1.database import session_scope

        task_id = self._create()

        async def _check() -> int:
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

        self.assertEqual(asyncio.run(_check()), 1)


if __name__ == "__main__":
    unittest.main()
