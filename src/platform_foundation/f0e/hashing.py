"""Deterministic, body-free hashing helpers for F0-E."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Mapping, Sequence
import uuid

from .contracts import F0EError, NormalizedTextEvidence


def canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        raise F0EError("CONTRACT_INVALID") from None


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def digest_fields(*values: object) -> str:
    return canonical_sha256(list(values))


def stable_uuid4(*values: object) -> uuid.UUID:
    normalized = [str(value) if isinstance(value, uuid.UUID) else value for value in values]
    raw = bytearray(hashlib.sha256(canonical_json_bytes(normalized)).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(raw))


def normalize_text_evidence(
    raw_utf8: bytes | bytearray | memoryview,
    *,
    normalization_rule: str = "ocr-text-nfc-lf-v1",
) -> NormalizedTextEvidence:
    """Return only normalized text metadata; normalized body never escapes."""

    if normalization_rule != "ocr-text-nfc-lf-v1":
        raise F0EError("RUNNER_CONFIGURATION_INVALID")
    try:
        raw_view = memoryview(raw_utf8)
        if not raw_view.contiguous:
            raw_view = memoryview(bytes(raw_view))
        raw_view = raw_view.cast("B")
        decoded = raw_view.tobytes().decode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeDecodeError):
        raise F0EError("RUNNER_OUTPUT_INVALID") from None

    normalized = unicodedata.normalize("NFC", decoded.replace("\r\n", "\n").replace("\r", "\n"))
    encoded = normalized.encode("utf-8")
    characters = len(normalized)
    non_blank = sum(not character.isspace() for character in normalized)
    bad = sum(
        character == "\ufffd"
        or (unicodedata.category(character) == "Cc" and character not in "\n\t")
        for character in normalized
    )
    bad_ppm = 0 if characters == 0 else bad * 1_000_000 // characters
    return NormalizedTextEvidence(
        text_sha256=hashlib.sha256(encoded).hexdigest(),
        utf8_bytes=len(encoded),
        characters=characters,
        non_blank_characters=non_blank,
        bad_character_ppm=bad_ppm,
        normalization_rule=normalization_rule,
    )


def body_free_mapping(value: Mapping[str, object]) -> dict[str, object]:
    """Reject common body/path/credential keys at an adapter boundary."""

    forbidden = {
        "body",
        "content",
        "dsn",
        "page_image",
        "path",
        "raw_text",
        "source_path",
        "text",
    }
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str) or key.lower() in forbidden:
            raise F0EError("CONTRACT_INVALID")
        if isinstance(item, (bytes, bytearray, memoryview)):
            raise F0EError("CONTRACT_INVALID")
        result[key] = item
    return result


def stable_reason_code(codes: Sequence[str]) -> str:
    if not codes:
        raise F0EError("CONTRACT_INVALID")
    return "+".join(sorted(set(codes)))


__all__ = (
    "body_free_mapping",
    "canonical_json_bytes",
    "canonical_sha256",
    "digest_fields",
    "normalize_text_evidence",
    "stable_uuid4",
    "stable_reason_code",
)
