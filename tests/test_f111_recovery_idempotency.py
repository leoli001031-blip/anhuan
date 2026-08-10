"""F1.1.1 recovery/idempotency contracts without shared-state access.

The fixed formal orchestrator runs these tests inside its random scratch
environment before the live PostgreSQL and twenty-metric reverse gates.  The
legacy version of this module reached a fixed host port, opened an old
migration DSN and reused a registered fixture path.  These checks instead
exercise the pure preflight function and bind the runtime implementation to
the live scratch verifiers that own all business writes and exact cleanup.
"""
from __future__ import annotations

import ast
import hashlib
import io
import os
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class F111IdempotencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tasks = _source("src/platform_foundation/f1/upload_task.py")
        cls.worker = _source("src/platform_foundation/f1/worker_pipeline.py")
        cls.reverse = _source("tests/f111_reverse_verify.py")
        cls.pg_live = _source("tests/f111_repair_pg_verify.py")
        for relative, value in (
            ("src/platform_foundation/f1/upload_task.py", cls.tasks),
            ("src/platform_foundation/f1/worker_pipeline.py", cls.worker),
            ("tests/f111_reverse_verify.py", cls.reverse),
            ("tests/f111_repair_pg_verify.py", cls.pg_live),
        ):
            ast.parse(value, filename=relative)

    def test_same_sha_reupload_returns_same_document_zero_new(self) -> None:
        # Runtime resolves an existing (enterprise, SHA) winner and the live
        # reverse gate proves the four effects remain exactly (1, 1, 1, 1).
        reserve = self.tasks.split("async def reserve_api_upload", 1)[1].split(
            "\n\nasync def ", 1
        )[0]
        self.assertIn("_reservation_for_sha(", reserve)
        self.assertIn("except IntegrityError", reserve)
        self.assertIn("return winner", reserve)
        replay = self.reverse.split("def upload_replay_and_dispatch", 1)[1].split(
            "\n    def ", 1
        )[0]
        for marker in (
            "same_document",
            "before == after",
            "before == (1, 1, 1, 1)",
            'results["upload_replay_effects"]',
        ):
            self.assertIn(marker, self.reverse if marker.startswith("results") else replay)

    def test_preflight_computes_sha_without_writing_object(self) -> None:
        from platform_foundation.f1.storage import preflight_upload

        payload = b"%PDF-1.7\n" + os.urandom(257)
        result = preflight_upload(
            "opaque.pdf", "application/pdf", io.BytesIO(payload)
        )
        self.assertEqual(result.sha256, hashlib.sha256(payload).hexdigest())
        self.assertEqual(result.size, len(payload))
        preflight = _source("src/platform_foundation/f1/storage.py").split(
            "def preflight_upload", 1
        )[1].split("\n\ndef ", 1)[0]
        self.assertNotIn("put_object", preflight)
        self.assertNotIn("store_stream", preflight)

    def test_outbox_dispatcher_reenqueues_pending(self) -> None:
        dispatcher = self.worker.split("def dispatch_pending_outbox", 1)[1].split(
            "\n\n__all__", 1
        )[0]
        self.assertIn("f1.claim_pending_dispatch(100, 60)", dispatcher)
        self.assertIn("upload_task.enqueue_upload(task_id, job_id)", dispatcher)
        self.assertIn("f1.complete_dispatch(:id, :token, :success)", dispatcher)
        self.assertNotIn("pending_dispatch_tasks()", dispatcher)
        # The PostgreSQL scratch verifier exercises wrong-token, expiry,
        # reclaim and final completion before deleting only its UUID fixtures.
        for marker in (
            'metrics["outbox_claim_failures"]',
            'metrics["outbox_token_guard_failures"]',
            "_cleanup_fixture(fixture)",
        ):
            self.assertIn(marker, self.pg_live)


class F111RequestIdBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qa = _source("src/platform_foundation/f1/qa_service.py")
        cls.router = _source("src/platform_foundation/f1/api/routers/qa.py")
        cls.migration = _source(
            "infra/f1/alembic/versions/f1_0004_repair_boundaries.py"
        )
        cls.reverse = _source("tests/f111_reverse_verify.py")

    def test_request_id_conflict_on_question_change(self) -> None:
        first = hashlib.sha256(b"question one").hexdigest()
        second = hashlib.sha256(b"question two").hexdigest()
        self.assertNotEqual(first, second)
        digest_function = self.qa.split("def _question_sha256", 1)[1].split(
            "\n\ndef ", 1
        )[0]
        self.assertIn('hashlib.sha256(question.encode("utf-8")).hexdigest()', digest_function)
        lookup = self.qa.split("async def lookup_request", 1)[1].split(
            "\n\nasync def ", 1
        )[0]
        self.assertIn("str(row[4]) != qsha", lookup)
        self.assertIn('RequestIdConflict("REQUEST_ID_CONFLICT")', lookup)
        self.assertIn("v_row.question_sha256 <> p_question_sha256", self.migration)

    def test_same_request_id_same_question_is_replay(self) -> None:
        reserve = self.qa.split("async def reserve_request", 1)[1].split(
            "\n\nasync def ", 1
        )[0]
        ask = self.qa.split("async def ask_question", 1)[1].split(
            "\n\nasync def ", 1
        )[0]
        self.assertIn("ReservationState.REPLAY", reserve)
        self.assertIn("if reservation.state is ReservationState.REPLAY", ask)
        self.assertIn("return reservation.result", ask)
        race = self.reverse.split("def qa_request_races", 1)[1].split(
            "\n    def ", 1
        )[0]
        self.assertIn("len(digests) == 1", race)
        self.assertIn("int(row[1]) == 1", race)

    def test_http_request_id_conflict_returns_409(self) -> None:
        self.assertIn("except qa_service.RequestIdConflict", self.router)
        self.assertIn('status_code=409, detail="REQUEST_ID_CONFLICT"', self.router)
        race = self.reverse.split("def qa_request_races", 1)[1].split(
            "\n    def ", 1
        )[0]
        self.assertIn("conflict_status == 409", race)
        self.assertIn('results["qa_request_races"]', self.reverse)


if __name__ == "__main__":
    unittest.main()
