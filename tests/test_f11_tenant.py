"""F1.1 tenant-boundary tests: RLS isolation, cross-tenant 404, idempotency."""
from __future__ import annotations

import asyncio
import unittest
import uuid

from sqlalchemy import text

from platform_foundation.f1.database import session_scope

from f11_support import (
    ENTERPRISE_A,
    ENTERPRISE_B,
    SUB_ADMIN,
    SUB_TENANT_B,
    api,
    configure_formal_runtime,
    create_document,
    get_token,
)


def setUpModule() -> None:
    configure_formal_runtime()


class F11TenantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.token_a = get_token("tenant-a")
        cls.token_b = get_token("tenant-b")

    def test_tenant_a_sees_only_own_enterprise(self) -> None:
        status, body = api(
            "GET",
            "/api/v1/enterprises",
            self.token_a,
            headers={"X-Enterprise-Id": str(ENTERPRISE_A)},
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["name"], "Tenant A")

    def test_tenant_b_cannot_see_tenant_a_documents(self) -> None:
        doc_a = create_document(ENTERPRISE_A, SUB_ADMIN)
        status, body = api(
            "GET",
            "/api/v1/documents",
            self.token_b,
            headers={"X-Enterprise-Id": str(ENTERPRISE_B)},
        )
        self.assertEqual(status, 200)
        ids = [d["id"] for d in body]
        self.assertNotIn(str(doc_a), ids)

    def test_cross_tenant_plant_write_rejected(self) -> None:
        async def _probe() -> bool:
            async with session_scope(role="f1_api", enterprise_id=ENTERPRISE_A, sub=SUB_ADMIN) as session:
                try:
                    await session.execute(
                        text(
                            "INSERT INTO f1.plant (id, enterprise_id, name) "
                            "VALUES (:id, :eid, 'x')"
                        ),
                        {"id": uuid.uuid4(), "eid": ENTERPRISE_B},
                    )
                    await session.commit()
                    return True
                except Exception:  # noqa: BLE001
                    return False

        self.assertFalse(asyncio.run(_probe()))

    def test_duplicate_upload_task_sha_is_idempotent(self) -> None:
        from platform_foundation.f1.upload_task import create_upload_task

        sha = uuid.uuid4().hex * 2
        document_id = create_document()
        first = asyncio.run(
            create_upload_task(
                enterprise_id=ENTERPRISE_A,
                document_id=document_id,
                object_key=f"t-{uuid.uuid4().hex}.pdf",
                content_sha256=sha,
                sub=SUB_ADMIN,
            )
        )
        second = asyncio.run(
            create_upload_task(
                enterprise_id=ENTERPRISE_A,
                document_id=document_id,
                object_key=f"t-{uuid.uuid4().hex}.pdf",
                content_sha256=sha,
                sub=SUB_ADMIN,
            )
        )
        self.assertEqual(first, second)

    def test_cross_tenant_same_sha_is_distinct(self) -> None:
        from platform_foundation.f1.upload_task import create_upload_task

        sha = uuid.uuid4().hex * 2
        a = asyncio.run(
            create_upload_task(
                enterprise_id=ENTERPRISE_A,
                document_id=create_document(ENTERPRISE_A),
                object_key=f"t-{uuid.uuid4().hex}.pdf",
                content_sha256=sha,
                sub=SUB_ADMIN,
            )
        )
        b = asyncio.run(
            create_upload_task(
                enterprise_id=ENTERPRISE_B,
                document_id=create_document(ENTERPRISE_B, SUB_TENANT_B),
                object_key=f"t-{uuid.uuid4().hex}.pdf",
                content_sha256=sha,
                sub=SUB_TENANT_B,
            )
        )
        self.assertNotEqual(a, b)

    def test_no_context_reads_zero_rows(self) -> None:
        async def _count() -> int:
            async with session_scope(role="f1_api") as session:
                row = (
                    await session.execute(
                        text("SELECT count(*) FROM f1.document")
                    )
                ).fetchone()
                return int(row[0])

        self.assertEqual(asyncio.run(_count()), 0)

    def test_pool_context_does_not_leak(self) -> None:
        # A tenant-A scoped session is committed; a fresh unscoped read must
        # not inherit the enterprise.
        from platform_foundation.f1.database import session_scope as scope

        async def _scoped() -> None:
            async with scope(role="f1_api", enterprise_id=ENTERPRISE_A, sub=SUB_ADMIN) as session:
                await session.execute(text("SELECT 1"))
                await session.commit()

        async def _unscoped() -> int:
            async with scope(role="f1_api") as session:
                row = (await session.execute(text("SELECT count(*) FROM f1.document"))).fetchone()
                return int(row[0])

        asyncio.run(_scoped())
        self.assertEqual(asyncio.run(_unscoped()), 0)

    def test_enterprise_selection_lists_membership(self) -> None:
        status, body = api("GET", "/api/v1/users/me/enterprises", self.token_a)
        self.assertEqual(status, 200)
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["role"], "enterprise_admin")

    def test_super_admin_creates_enterprise(self) -> None:
        token = get_token("admin@anhuan.local")
        status, body = api(
            "POST",
            "/api/v1/enterprises",
            token,
            {"name": "CreateTestCo", "license_no": "LIC-CREATE"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(body["name"], "CreateTestCo")


if __name__ == "__main__":
    unittest.main()
