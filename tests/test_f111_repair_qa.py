"""F1.1.1 repair QA contracts using only static and temporary samples."""
from __future__ import annotations

import hashlib
import inspect
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from cryptography.exceptions import InvalidTag

from platform_foundation.f1 import citation, qa_chain, qa_service


class _TemporaryQaKey(unittest.TestCase):
    def setUp(self) -> None:
        self._original_key_file = qa_service.QA_KEY_FILE
        self._temporary = tempfile.TemporaryDirectory()
        key_file = Path(self._temporary.name) / "qa.key"
        key_file.write_text(os.urandom(32).hex(), encoding="ascii")
        key_file.chmod(0o600)
        qa_service.QA_KEY_FILE = key_file

    def tearDown(self) -> None:
        qa_service.QA_KEY_FILE = self._original_key_file
        self._temporary.cleanup()


class QaAadTests(_TemporaryQaKey):
    def _identities(self) -> tuple[uuid.UUID, uuid.UUID, str]:
        return uuid.uuid4(), uuid.uuid4(), hashlib.sha256(os.urandom(24)).hexdigest()

    def test_ciphertext_round_trip_requires_bound_aad(self) -> None:
        request_id, enterprise_id, question_sha = self._identities()
        aad = qa_service._aad(request_id, enterprise_id, question_sha)
        encrypted = qa_service._encrypt("opaque-sample", aad)
        self.assertNotIn(b"opaque-sample", encrypted)
        self.assertEqual(qa_service._decrypt(encrypted, aad), "opaque-sample")

    def test_request_identity_change_fails_authentication(self) -> None:
        request_id, enterprise_id, question_sha = self._identities()
        encrypted = qa_service._encrypt(
            "opaque-sample", qa_service._aad(request_id, enterprise_id, question_sha)
        )
        with self.assertRaises(InvalidTag):
            qa_service._decrypt(
                encrypted,
                qa_service._aad(uuid.uuid4(), enterprise_id, question_sha),
            )

    def test_tenant_identity_change_fails_authentication(self) -> None:
        request_id, enterprise_id, question_sha = self._identities()
        encrypted = qa_service._encrypt(
            "opaque-sample", qa_service._aad(request_id, enterprise_id, question_sha)
        )
        with self.assertRaises(InvalidTag):
            qa_service._decrypt(
                encrypted,
                qa_service._aad(request_id, uuid.uuid4(), question_sha),
            )

    def test_question_identity_change_fails_authentication(self) -> None:
        request_id, enterprise_id, question_sha = self._identities()
        encrypted = qa_service._encrypt(
            "opaque-sample", qa_service._aad(request_id, enterprise_id, question_sha)
        )
        with self.assertRaises(InvalidTag):
            qa_service._decrypt(
                encrypted,
                qa_service._aad(
                    request_id,
                    enterprise_id,
                    hashlib.sha256(os.urandom(24)).hexdigest(),
                ),
            )

    def test_unversioned_ciphertext_is_not_accepted_as_new(self) -> None:
        request_id, enterprise_id, question_sha = self._identities()
        with self.assertRaises(ValueError):
            qa_service._decrypt(
                os.urandom(48), qa_service._aad(request_id, enterprise_id, question_sha)
            )


class QaOutcomeStateTests(unittest.TestCase):
    def test_encrypted_payload_serialization_is_deterministic(self) -> None:
        citation_row = {"pages": [2], "chunk_id": str(uuid.uuid4())}
        first = qa_service.QaResult("opaque", [citation_row])
        second = qa_service.QaResult("opaque", [dict(reversed(list(citation_row.items())))])
        self.assertEqual(
            qa_service._canonical_payload(first), qa_service._canonical_payload(second)
        )

    def test_done_requires_nonempty_citations(self) -> None:
        with self.assertRaises(qa_service.QaOutcomeInvalid):
            qa_service._validate_outcome(qa_service.QaResult("opaque", []))

    def test_refusal_forbids_citations(self) -> None:
        with self.assertRaises(qa_service.QaOutcomeInvalid):
            qa_service._validate_outcome(
                qa_service.QaResult(None, [{"chunk_id": str(uuid.uuid4())}], "REFUSED")
            )

    def test_answer_and_refusal_are_mutually_exclusive(self) -> None:
        with self.assertRaises(qa_service.QaOutcomeInvalid):
            qa_service._validate_outcome(
                qa_service.QaResult("opaque", [{"chunk_id": str(uuid.uuid4())}], "REFUSED")
            )

    def test_valid_terminal_states_are_distinct(self) -> None:
        done = qa_service.QaResult("opaque", [{"chunk_id": str(uuid.uuid4())}])
        refused = qa_service.QaResult(None, [], "REFUSED")
        self.assertEqual(qa_service._validate_outcome(done), "done")
        self.assertEqual(qa_service._validate_outcome(refused), "refused")


class CitationEvidenceTests(unittest.TestCase):
    def _row(self, *, pages=(1,), body=b"\x01\x02", body_sha=None):
        chunk_id = uuid.uuid4()
        digest = body_sha or hashlib.sha256(body).hexdigest()
        return (
            chunk_id,
            uuid.uuid4(),
            uuid.uuid4(),
            pages,
            digest,
            body,
        )

    def test_body_hash_and_pages_are_reobserved(self) -> None:
        row = self._row(pages=(1, 3))
        result = citation._validated_rows([row], {row[0]})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].pages, (1, 3))
        self.assertEqual(result[0].body_sha256, hashlib.sha256(row[5]).hexdigest())

    def test_hash_mismatch_is_rejected(self) -> None:
        row = self._row(body_sha="0" * 64)
        self.assertEqual(citation._validated_rows([row], {row[0]}), [])

    def test_empty_body_is_rejected(self) -> None:
        row = self._row(body=b"")
        self.assertEqual(citation._validated_rows([row], {row[0]}), [])

    def test_missing_or_invalid_pages_are_rejected(self) -> None:
        missing = self._row(pages=())
        invalid = self._row(pages=(0,))
        self.assertEqual(citation._validated_rows([missing], {missing[0]}), [])
        self.assertEqual(citation._validated_rows([invalid], {invalid[0]}), [])

    def test_unrequested_and_duplicate_chunks_are_rejected(self) -> None:
        row = self._row()
        self.assertEqual(citation._validated_rows([row], {uuid.uuid4()}), [])
        self.assertEqual(len(citation._validated_rows([row, row], {row[0]})), 1)


class ActualCitationIntersectionTests(unittest.TestCase):
    def _evidence(self, pages=(2, 4)) -> citation.VerifiedCitation:
        body = b"\x03\x04"
        return citation.VerifiedCitation(
            uuid.uuid4(),
            uuid.uuid4(),
            uuid.uuid4(),
            pages,
            hashlib.sha256(body).hexdigest(),
            body,
        )

    def test_response_contains_only_actual_llm_reference(self) -> None:
        selected = self._evidence()
        unused = self._evidence()
        answer = f"opaque [chunk_id={selected.chunk_id}, pages=[2]]"
        actual, reason = qa_chain._actual_citations(answer, [selected, unused])
        self.assertIsNone(reason)
        self.assertEqual([item["chunk_id"] for item in actual], [str(selected.chunk_id)])
        self.assertEqual(actual[0]["pages"], [2])

    def test_nonempty_reference_is_required(self) -> None:
        actual, reason = qa_chain._actual_citations("opaque", [self._evidence()])
        self.assertEqual(actual, [])
        self.assertEqual(reason, "MISSING_CITATION")

    def test_unverified_chunk_is_rejected(self) -> None:
        evidence = self._evidence()
        answer = f"opaque [chunk_id={uuid.uuid4()}, pages=[2]]"
        actual, reason = qa_chain._actual_citations(answer, [evidence])
        self.assertEqual(actual, [])
        self.assertEqual(reason, "FABRICATED_CITATION")

    def test_unverified_page_is_rejected(self) -> None:
        evidence = self._evidence()
        answer = f"opaque [chunk_id={evidence.chunk_id}, pages=[9]]"
        actual, reason = qa_chain._actual_citations(answer, [evidence])
        self.assertEqual(actual, [])
        self.assertEqual(reason, "INVALID_CITATION_PAGE")

    def test_bare_chunk_reference_is_rejected(self) -> None:
        evidence = self._evidence()
        answer = f"opaque chunk_id={evidence.chunk_id}"
        actual, reason = qa_chain._actual_citations(answer, [evidence])
        self.assertEqual(actual, [])
        self.assertEqual(reason, "MISSING_CITATION")


class QaAtomicContractSourceTests(unittest.TestCase):
    def test_reservation_states_are_exact(self) -> None:
        self.assertEqual(
            {state.value for state in qa_service.ReservationState},
            {"CLAIMED", "REPLAY", "IN_PROGRESS", "CONFLICT"},
        )

    def test_reserve_contract_uses_definer_claim_and_attempt(self) -> None:
        source = inspect.getsource(qa_service.reserve_request)
        self.assertIn("f1.claim_qa_request", source)
        self.assertIn("ReservationState", source)
        self.assertNotIn("INSERT INTO f1.qa_request", source)
        self.assertNotIn("UPDATE f1.qa_request", source)

    def test_external_chain_runs_only_after_reservation(self) -> None:
        source = inspect.getsource(qa_service.ask_question)
        self.assertLess(source.index("reserve_request"), source.index("qa_chain.run"))
        self.assertIn("ReservationState.CLAIMED", source)

    def test_completion_cas_and_audit_share_one_transaction(self) -> None:
        source = inspect.getsource(qa_service.complete_request)
        self.assertEqual(source.count("session_scope("), 1)
        self.assertIn("f1.complete_qa_request", source)
        self.assertNotIn("UPDATE f1.qa_request", source)
        self.assertNotIn("INSERT INTO f1.audit_log", source)
        self.assertLess(source.index("f1.complete_qa_request"), source.index("session.commit"))

    def test_router_has_no_lookup_then_run_toctou(self) -> None:
        from platform_foundation.f1.api.routers import qa as qa_router

        source = inspect.getsource(qa_router.ask)
        self.assertNotIn("lookup_request", source)
        self.assertIn("RequestIdConflict", source)
        self.assertIn("RequestInProgress", source)
        self.assertIn("RequestOwnershipLost", source)


if __name__ == "__main__":
    unittest.main()
