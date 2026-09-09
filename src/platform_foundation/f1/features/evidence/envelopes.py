"""Encrypted native evidence envelopes; no PDF v1 identities are reused."""
from __future__ import annotations

import hashlib
import re
import uuid

from ..material_rag.security import decrypt_text, encrypt_text
from .contracts import FragmentIdentity, canonical_json, parse_locator
from .docx_native import DocxExtraction
from .formats import CONTRACTS

EXTRACTION_CONTRACT = 1
MAX_PAYLOAD_BYTES = 20_000_000


def build_native_payload(
    extraction,
    *,
    enterprise_id: uuid.UUID,
    knowledge_scope_id: uuid.UUID,
    document_record_id: uuid.UUID,
    document_version_id: uuid.UUID,
    revision_id: uuid.UUID,
    source_format: str,
) -> dict:
    """Freeze a whole extraction, including partial coverage, before DB I/O.

    Empty paragraphs retain their structural ordinal. Partial sources retain
    all reported debts but never contribute usable evidence fragments.
    """
    if not all(type(value) is uuid.UUID for value in (
        enterprise_id, knowledge_scope_id, document_record_id,
        document_version_id, revision_id,
    )):
        raise ValueError("NATIVE_EXTRACTION_IDENTITY_INVALID")
    if (
        type(extraction.expected_block_count) is not int
        or type(extraction.processed_block_count) is not int
        or not 0 <= extraction.expected_block_count <= 400_000
        or not 0 <= extraction.processed_block_count <= 20_000
        or len(extraction.debts) > 256
        or sum(len(block.text) for block in extraction.blocks) > 2_000_000
        or any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in (
            extraction.source_sha256, extraction.manifest_sha256,
        ))
        or
        CONTRACTS.get(source_format) != (extraction.parser_version, extraction.support_profile)
        or extraction.processed_block_count > extraction.expected_block_count
        or extraction.processed_block_count < 0
        or (not extraction.debts and (
            extraction.processed_block_count != extraction.expected_block_count
            or len(extraction.blocks) != extraction.processed_block_count
        ))
        or (extraction.debts and extraction.blocks)
    ):
        raise ValueError("NATIVE_EXTRACTION_COVERAGE_INVALID")
    fragments = []
    for ordinal, block in enumerate(extraction.blocks):
        if block.locator.source_format != source_format:
            raise ValueError("NATIVE_EXTRACTION_IDENTITY_INVALID")
        identity = FragmentIdentity(
            enterprise_id, knowledge_scope_id, document_record_id,
            document_version_id, revision_id, extraction.source_sha256,
            extraction.parser_version, EXTRACTION_CONTRACT, block.locator,
            ordinal, block.body_sha256,
        )
        ciphertext, aad_sha256 = encrypt_text(block.text, identity.aad())
        fragments.append({
            "id": str(identity.id), "ordinal": ordinal,
            "locator": block.locator.to_dict(),
            "locator_sha256": block.locator.sha256,
            "body_sha256": block.body_sha256,
            "token_sha256": block.token_sha256,
            "character_count": len(block.text),
            "has_nonblank_text": bool(block.text.strip()),
            "body_ciphertext_hex": ciphertext.hex(),
            "body_aad_sha256": aad_sha256,
        })
    payload = {
        "revision_id": str(revision_id),
        "source_sha256": extraction.source_sha256,
        "source_format": source_format,
        "parser_version": extraction.parser_version,
        "support_profile": extraction.support_profile,
        "extraction_contract": EXTRACTION_CONTRACT,
        "manifest_sha256": extraction.manifest_sha256,
        "coverage_state": extraction.coverage_state,
        "expected_block_count": extraction.expected_block_count,
        "processed_block_count": extraction.processed_block_count,
        "report_source_eligible": extraction.report_source_eligible,
        "debts": [{"reason_code": debt.reason_code, "part": debt.part,
                   "path": debt.path} for debt in extraction.debts],
        "fragments": fragments,
    }
    if source_format == "jpeg":
        payload["processing_identity"] = dict(extraction.processing_identity)
    # Reject non-JSON payloads before attempting a transaction.
    # Leave space under the SQL 24 MB bound for JSONB's display whitespace.
    if len(canonical_json(payload)) > MAX_PAYLOAD_BYTES:
        raise ValueError("NATIVE_EXTRACTION_PAYLOAD_LIMIT")
    return payload


def build_docx_payload(extraction: DocxExtraction, **identity) -> dict:
    return build_native_payload(extraction, source_format="docx", **identity)


def decrypt_fragment(row: dict) -> str:
    """Verify the complete v2 identity before exposing any plaintext."""
    identity = FragmentIdentity(
        *(uuid.UUID(str(row[key])) for key in (
            "enterprise_id", "knowledge_scope_id", "document_record_id",
            "document_version_id", "extraction_revision_id")),
        row["source_sha256"], row["parser_version"], row["extraction_contract"],
        parse_locator(row["locator"]), row["ordinal"], row["body_sha256"],
    )
    if identity.id != uuid.UUID(str(row["id"])) or identity.locator.sha256 != row["locator_sha256"]:
        raise ValueError("NATIVE_FRAGMENT_IDENTITY_INVALID")
    body = decrypt_text(bytes(row["body_ciphertext"]), identity.aad(), row["body_aad_sha256"])
    if (hashlib.sha256(body.encode("utf-8")).hexdigest() != row["body_sha256"]
        or len(body) != row["character_count"]
        or bool(body.strip()) != row["has_nonblank_text"]):
        raise ValueError("NATIVE_FRAGMENT_BODY_INVALID")
    return body
