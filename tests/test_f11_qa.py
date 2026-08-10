"""F1.1 QA tests: persistence, encryption, idempotency, tenant scope."""
from __future__ import annotations

import asyncio
import unittest
import uuid

from sqlalchemy import text

from platform_foundation.f1 import qa_service
from platform_foundation.f1.auth import Tenant
from platform_foundation.f1.database import session_scope

from f11_support import (
    ENTERPRISE_A,
    ENTERPRISE_B,
    SUB_ADMIN,
    SUB_TENANT_B,
    api,
    configure_formal_runtime,
    get_token,
)


def setUpModule() -> None:
    configure_formal_runtime()

class _Tenant:
    def __init__(self, eid, sub=SUB_ADMIN):
        self.enterprise_id = eid
        self.sub = sub
        self.roles = ("enterprise_admin",)


class F11QaTests(unittest.TestCase):
    def _tenant(self, eid, sub=SUB_ADMIN) -> Tenant:
        return Tenant(enterprise_id=eid, sub=sub, roles=("enterprise_admin",))

    def test_qa_synthetic_tenant_b_refuses(self) -> None:
        # Enterprise B has no indexed corpus -> data-driven refusal (NO_HITS).
        tenant = self._tenant(ENTERPRISE_B, SUB_TENANT_B)
        result = asyncio.run(qa_service.ask_question("test question", uuid.uuid4(), tenant))
        self.assertIsNotNone(result.refusal_reason)

    def test_qa_persists_refused_request(self) -> None:
        tenant = self._tenant(ENTERPRISE_B, SUB_TENANT_B)
        rid = uuid.uuid4()
        asyncio.run(qa_service.ask_question("persist question", rid, tenant))

        async def _check() -> str:
            async with session_scope(role="f1_api", enterprise_id=ENTERPRISE_B, sub=SUB_TENANT_B) as session:
                row = (
                    await session.execute(
                        text("SELECT status, refusal_reason FROM f1.qa_request WHERE request_id = :rid"),
                        {"rid": rid},
                    )
                ).fetchone()
                return f"{row[0]}:{row[1]}"

        status, reason = asyncio.run(_check()).split(":", 1)
        self.assertEqual(status, "refused")
        self.assertIsNotNone(reason)

    def test_qa_lookup_idempotent(self) -> None:
        tenant = self._tenant(ENTERPRISE_B, SUB_TENANT_B)
        rid = uuid.uuid4()
        asyncio.run(qa_service.ask_question("idem question", rid, tenant))
        # Lookup returns the stored refusal (idempotent replay).
        stored = asyncio.run(qa_service.lookup_request(rid, tenant))
        self.assertIsNotNone(stored.refusal_reason)

    def test_qa_enterprise_a_runs_chain(self) -> None:
        # Enterprise A has indexed fixtures: the chain must run and return
        # either a grounded answer with citations or a reason-coded refusal —
        # never crash and never fabricate.
        from platform_foundation.f1.qa_chain import run

        tenant = self._tenant(ENTERPRISE_A)
        result = asyncio.run(run("该企业废气治理采用什么方案？", tenant))
        self.assertIsInstance(result, qa_service.QaResult)
        if result.refusal_reason is None:
            self.assertTrue(result.answer)
            self.assertGreaterEqual(len(result.citations), 1)
        else:
            self.assertIn(
                result.refusal_reason,
                (
                    "NO_HITS",
                    "ALL_CANDIDATES_REJECTED",
                    "QA_CHAIN_UNAVAILABLE",
                    "MISSING_CITATION",
                    "INVALID_CITATION_FORMAT",
                    "FABRICATED_CITATION",
                    "INVALID_CITATION_PAGE",
                ),
            )

    def test_qa_request_api_requires_auth(self) -> None:
        status, _ = api("POST", "/api/v1/qa", "", {"question": "x"})
        self.assertEqual(status, 401)

    def test_qa_cross_tenant_is_404(self) -> None:
        token = get_token("tenant-a")
        status, _ = api(
            "POST",
            "/api/v1/qa",
            token,
            {"question": "x", "enterprise_id": str(ENTERPRISE_B)},
        )
        self.assertEqual(status, 404)

    def test_qa_empty_question_rejected(self) -> None:
        token = get_token("tenant-a")
        status, _ = api(
            "POST",
            "/api/v1/qa",
            token,
            {"question": "   "},
        )
        self.assertEqual(status, 422)

    def test_encryption_key_is_0600(self) -> None:
        import os
        from pathlib import Path

        path = qa_service._qa_key_path()
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertGreater(path.stat().st_size, 0)

    def test_no_plaintext_answer_stored(self) -> None:
        tenant = self._tenant(ENTERPRISE_A)
        asyncio.run(qa_service.ask_question("leak check", uuid.uuid4(), tenant))

        async def _leaks() -> int:
            async with session_scope(role="f1_api", enterprise_id=ENTERPRISE_A, sub=SUB_ADMIN) as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM f1.qa_request "
                            "WHERE status = 'done' AND response_encrypted IS NULL"
                        )
                    )
                ).fetchone()
                return int(row[0])

        self.assertEqual(asyncio.run(_leaks()), 0)

    def test_question_sha_is_64_hex(self) -> None:
        import hashlib

        sha = qa_service._question_sha256("some question")
        self.assertEqual(len(sha), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in sha))

    def test_lookup_missing_request_returns_none(self) -> None:
        tenant = self._tenant(ENTERPRISE_A)
        stored = asyncio.run(qa_service.lookup_request(uuid.uuid4(), tenant))
        self.assertIsNone(stored)


if __name__ == "__main__":
    unittest.main()
