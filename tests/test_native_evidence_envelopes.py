"""Actual AES-GCM round trips, identity substitution and partial coverage."""
import tempfile
import unittest
import uuid
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from platform_foundation.f1.features.evidence.envelopes import build_docx_payload, decrypt_fragment
from platform_foundation.f1.features.material_rag import security
from tests.test_native_evidence import package, p, extract


class NativeEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        key = Path(self.temporary.name) / "key"
        key.write_text("b3" * 32)
        key.chmod(0o600)
        self.patch = patch.object(security, "MATERIAL_RAG_KEY_FILE", key)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.identity = {name: uuid.uuid4() for name in (
            "enterprise_id", "knowledge_scope_id", "document_record_id",
            "document_version_id", "revision_id")}

    def _row(self, payload, ordinal=0):
        fragment = payload["fragments"][ordinal]
        return {**fragment, **{key: value for key, value in self.identity.items() if key != "revision_id"},
            "extraction_revision_id": self.identity["revision_id"],
            "source_sha256": payload["source_sha256"],
            "parser_version": payload["parser_version"],
            "extraction_contract": payload["extraction_contract"],
            "body_ciphertext": bytes.fromhex(fragment["body_ciphertext_hex"])}

    def test_encrypts_exact_text_and_empty_structural_slots(self):
        result = extract(package(p("排口 COD：42 mg/L") + "<w:p/>" + p("第二项")))
        payload = build_docx_payload(result, **self.identity)
        self.assertEqual(payload["coverage_state"], "complete")
        self.assertTrue(payload["report_source_eligible"])
        self.assertEqual([decrypt_fragment(self._row(payload, n)) for n in range(3)],
                         ["排口 COD：42 mg/L", "", "第二项"])
        self.assertNotIn("COD", repr(payload))
        self.assertEqual([f["ordinal"] for f in payload["fragments"]], [0, 1, 2])

    def test_ciphertext_replay_cannot_cross_identity_or_locator(self):
        payload = build_docx_payload(extract(package(p("秘密源数据"))), **self.identity)
        original = self._row(payload)
        mutations = {key: uuid.uuid4() for key in (
            "enterprise_id", "knowledge_scope_id", "document_record_id",
            "document_version_id", "extraction_revision_id")}
        mutations.update(source_sha256="a" * 64, parser_version="docx-native-2",
                         extraction_contract=2, ordinal=1, body_sha256="c" * 64,
                         locator={**original["locator"], "body_index": 2})
        for key, value in mutations.items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                decrypt_fragment({**original, key: value})

    def test_tampering_ciphertext_and_lengths_never_returns_body(self):
        row = self._row(build_docx_payload(extract(package(p("证据"))), **self.identity))
        ciphertext = bytearray(row["body_ciphertext"])
        ciphertext[-1] ^= 1
        from cryptography.exceptions import InvalidTag
        with self.assertRaises(InvalidTag):
            decrypt_fragment({**row, "body_ciphertext": bytes(ciphertext)})
        with self.assertRaisesRegex(ValueError, "NATIVE_FRAGMENT_BODY_INVALID"):
            decrypt_fragment({**row, "character_count": 1})
        with self.assertRaisesRegex(ValueError, "MATERIAL_RAG_AAD_MISMATCH"):
            decrypt_fragment({**row, "body_aad_sha256": "f" * 64})

    def test_partial_keeps_all_debts_and_no_fragments(self):
        result = extract(package(p("前文") + "<w:p><w:r><w:drawing/></w:r></w:p>"))
        payload = build_docx_payload(result, **self.identity)
        self.assertEqual(payload["coverage_state"], "partial")
        self.assertFalse(payload["report_source_eligible"])
        self.assertEqual(payload["fragments"], [])
        self.assertEqual(len(payload["debts"]), len(result.debts))
        self.assertEqual(payload["expected_block_count"], 2)
        self.assertEqual(payload["processed_block_count"], 2)

    def test_cannot_manufacture_complete_by_dropping_blocks_or_counts(self):
        result = extract(package(p("第一段") + p("第二段")))
        for modified in [replace(result, blocks=result.blocks[:1]),
                         replace(result, processed_block_count=1),
                         replace(result, expected_block_count=True)]:
            with self.assertRaisesRegex(ValueError, "NATIVE_EXTRACTION_COVERAGE_INVALID"):
                build_docx_payload(modified, **self.identity)
        empty = extract(package("<w:p/>"))
        with self.assertRaisesRegex(ValueError, "NATIVE_EXTRACTION_IDENTITY_INVALID"):
            build_docx_payload(empty, **{**self.identity, "enterprise_id": "fake"})

    def test_reencrypting_is_new_ciphertext_but_same_fragment_identity(self):
        result = extract(package(p("固定正文")))
        first, second = (build_docx_payload(result, **self.identity) for _ in range(2))
        self.assertEqual(first["fragments"][0]["id"], second["fragments"][0]["id"])
        self.assertNotEqual(first["fragments"][0]["body_ciphertext_hex"], second["fragments"][0]["body_ciphertext_hex"])
        next_revision = build_docx_payload(result, **{**self.identity, "revision_id": uuid.uuid4()})
        self.assertNotEqual(first["fragments"][0]["id"], next_revision["fragments"][0]["id"])

    def test_oversized_envelope_is_rejected_before_database_io(self):
        with patch("platform_foundation.f1.features.evidence.envelopes.MAX_PAYLOAD_BYTES", 100):
            with self.assertRaisesRegex(ValueError, "NATIVE_EXTRACTION_PAYLOAD_LIMIT"):
                build_docx_payload(extract(package(p("正文"))), **self.identity)


if __name__ == "__main__":
    unittest.main()
