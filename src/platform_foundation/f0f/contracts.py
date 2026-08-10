"""Redacted contracts for controlled Fixture body evidence.

The body boundary is deliberately different from F0-E's body-free evidence
contracts.  A :class:`CanonicalBody` owns a mutable byte buffer, never renders
its contents in ``repr``/``str``/exceptions, and can be wiped deterministically.
Only code inside the F0-F persistence boundary may obtain its memory view.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
import unicodedata
import uuid


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_SAFE_RULE = re.compile(r"^[A-Za-z0-9_.:/-]{1,96}$")


class F0FError(RuntimeError):
    """A stable failure whose message cannot contain source or body data."""

    _CODES = frozenset(
        {
            "BODY_CONFIGURATION_INVALID",
            "BODY_CONTRACT_INVALID",
            "BODY_DECRYPTION_FAILED",
            "BODY_EVIDENCE_MISMATCH",
            "BODY_LIMIT_EXCEEDED",
            "BODY_NORMALIZATION_FAILED",
            "BODY_PERSISTENCE_FAILED",
            "BODY_REPLAY_MISMATCH",
            "DATABASE_OPERATION_FAILED",
            "GOLD_OPERATION_DENIED",
            "KEYFILE_ALREADY_EXISTS",
            "KEYFILE_INVALID",
            "KEYFILE_NOT_AVAILABLE",
            "NATIVE_PARSE_FAILED",
            "NATIVE_TEXT_MISMATCH",
            "JOB_LEASE_STALE",
            "JOB_NOT_AVAILABLE",
            "RUNNER_CONFIGURATION_INVALID",
            "RUNNER_FAILED",
            "RUNNER_INVOCATION_DENIED",
            "RUNNER_OUTPUT_INVALID",
            "RUNNER_OUTPUT_LIMIT",
            "RUNNER_TIMEOUT",
            "SOURCE_OBJECT_CHANGED",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if code in self._CODES else "BODY_CONTRACT_INVALID"
        super().__init__(self.code)

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"F0FError({self.code!r})"

    def to_dict(self) -> dict[str, str]:
        return {"error": "F0F_ERROR", "reason_code": self.code}


def require_sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise F0FError("BODY_CONTRACT_INVALID")
    return value


def _positive(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise F0FError("BODY_CONTRACT_INVALID")
    return value


def _non_negative(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise F0FError("BODY_CONTRACT_INVALID")
    return value


class CanonicalBody:
    """Owned canonical bytes with redacted display and explicit zeroization."""

    __slots__ = (
        "_buffer",
        "_sha256",
        "_characters",
        "_nonblank",
        "_rule",
        "_wiped",
    )

    def __init__(
        self,
        value: bytes | bytearray | memoryview,
        *,
        characters: int,
        nonblank_characters: int,
        normalization_rule: str,
        maximum_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        try:
            view = memoryview(value).cast("B")
            if len(view) > maximum_bytes:
                raise F0FError("BODY_LIMIT_EXCEEDED")
            buffer = bytearray(view)
        except F0FError:
            raise
        except (TypeError, ValueError):
            raise F0FError("BODY_CONTRACT_INVALID") from None
        if (
            _SAFE_RULE.fullmatch(normalization_rule) is None
            or _non_negative(characters) < _non_negative(nonblank_characters)
        ):
            buffer[:] = b""
            raise F0FError("BODY_CONTRACT_INVALID")
        self._buffer = buffer
        self._sha256 = hashlib.sha256(buffer).hexdigest()
        self._characters = characters
        self._nonblank = nonblank_characters
        self._rule = normalization_rule
        self._wiped = False

    def __enter__(self) -> CanonicalBody:
        if self._wiped:
            raise F0FError("BODY_CONTRACT_INVALID")
        return self

    def __exit__(self, *_: object) -> None:
        self.wipe()

    def __repr__(self) -> str:
        state = "wiped" if self._wiped else "owned"
        return f"CanonicalBody(state={state!r}, bytes={len(self._buffer)})"

    __str__ = __repr__

    @property
    def sha256(self) -> str:
        return self._sha256

    @property
    def byte_count(self) -> int:
        return len(self._buffer)

    @property
    def character_count(self) -> int:
        return self._characters

    @property
    def nonblank_character_count(self) -> int:
        return self._nonblank

    @property
    def normalization_rule(self) -> str:
        return self._rule

    def view(self) -> memoryview:
        if self._wiped:
            raise F0FError("BODY_CONTRACT_INVALID")
        return memoryview(self._buffer).toreadonly()

    def wipe(self) -> None:
        self._buffer[:] = b"\0" * len(self._buffer)
        self._buffer.clear()
        self._wiped = True


def native_body(text: str) -> CanonicalBody:
    """Canonicalize after the caller has proven the raw F0-C identity."""

    if not isinstance(text, str):
        raise F0FError("BODY_CONTRACT_INVALID")
    try:
        normalized = unicodedata.normalize(
            "NFC", text.replace("\r\n", "\n").replace("\r", "\n")
        )
        encoded = normalized.encode("utf-8", errors="strict")
    except UnicodeError:
        raise F0FError("BODY_NORMALIZATION_FAILED") from None
    return CanonicalBody(
        encoded,
        characters=len(normalized),
        nonblank_characters=sum(not char.isspace() for char in normalized),
        normalization_rule="UTF8_NFC_LF_V1",
    )


def ocr_body(block_texts: tuple[str, ...]) -> CanonicalBody:
    """Build a deterministic review body without changing block order."""

    if not isinstance(block_texts, tuple) or len(block_texts) > 4096:
        raise F0FError("BODY_CONTRACT_INVALID")
    normalized: list[str] = []
    for text in block_texts:
        if not isinstance(text, str):
            raise F0FError("BODY_CONTRACT_INVALID")
        normalized.append(
            unicodedata.normalize(
                "NFC", text.replace("\r\n", "\n").replace("\r", "\n")
            )
        )
    joined = "\n".join(normalized)
    try:
        encoded = joined.encode("utf-8", errors="strict")
    except UnicodeError:
        raise F0FError("BODY_NORMALIZATION_FAILED") from None
    return CanonicalBody(
        encoded,
        characters=len(joined),
        nonblank_characters=sum(not char.isspace() for char in joined),
        normalization_rule="UTF8_NFC_LF_V1",
    )


@dataclass(frozen=True, slots=True)
class BodyConfiguration:
    configuration_id: uuid.UUID
    configuration_sha256: str
    key_fingerprint_sha256: str
    f0e_execution_profile_sha256: str
    runner_image_id: str
    normalization_profile_sha256: str
    maximum_body_bytes: int = 4 * 1024 * 1024
    external_processing: str = "DENY"
    benchmark_tier: str = "NONE"
    production_allowed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.configuration_id, uuid.UUID):
            raise F0FError("BODY_CONFIGURATION_INVALID")
        for value in (
            self.configuration_sha256,
            self.key_fingerprint_sha256,
            self.f0e_execution_profile_sha256,
            self.normalization_profile_sha256,
        ):
            require_sha256(value)
        if (
            re.fullmatch(r"sha256:[0-9a-f]{64}", self.runner_image_id) is None
            or self.maximum_body_bytes != 4 * 1024 * 1024
            or self.external_processing != "DENY"
            or self.benchmark_tier != "NONE"
            or self.production_allowed is not False
        ):
            raise F0FError("BODY_CONFIGURATION_INVALID")


@dataclass(frozen=True, slots=True)
class PageBodyMetadata:
    body_evidence_id: uuid.UUID
    processing_unit_id: uuid.UUID
    processing_plan_id: uuid.UUID
    document_version_id: uuid.UUID
    source_unit_id: str
    selected_route: str
    plaintext_sha256: str
    plaintext_bytes: int
    plaintext_characters: int
    plaintext_nonblank_characters: int
    normalization_rule: str
    source_plan_sha256: str
    source_evidence_sha256: str
    f0e_page_evidence_sha256: str
    f0e_execution_profile_sha256: str
    configuration_sha256: str
    ciphertext_sha256: str | None = None

    def __post_init__(self) -> None:
        for value in (
            self.body_evidence_id,
            self.processing_unit_id,
            self.processing_plan_id,
            self.document_version_id,
        ):
            if not isinstance(value, uuid.UUID):
                raise F0FError("BODY_CONTRACT_INVALID")
        for value in (
            self.source_unit_id,
            self.plaintext_sha256,
            self.source_plan_sha256,
            self.source_evidence_sha256,
            self.f0e_page_evidence_sha256,
            self.f0e_execution_profile_sha256,
            self.configuration_sha256,
        ):
            require_sha256(value)
        if self.ciphertext_sha256 is not None:
            require_sha256(self.ciphertext_sha256)
        if (
            self.selected_route not in {"NATIVE_REFERENCE", "LOCAL_OCR"}
            or _SAFE_RULE.fullmatch(self.normalization_rule) is None
            or _non_negative(self.plaintext_bytes) > 4 * 1024 * 1024
            or _non_negative(self.plaintext_characters)
            < _non_negative(self.plaintext_nonblank_characters)
        ):
            raise F0FError("BODY_CONTRACT_INVALID")


@dataclass(frozen=True, slots=True, repr=False)
class OcrBlock:
    index: int
    text: str = field(repr=False)
    bbox: tuple[tuple[int, int], tuple[int, int], tuple[int, int], tuple[int, int]]
    confidence_ppm: int

    def __post_init__(self) -> None:
        if _non_negative(self.index) > 4095 or not isinstance(self.text, str):
            raise F0FError("RUNNER_OUTPUT_INVALID")
        confidence = _non_negative(self.confidence_ppm)
        if confidence > 1_000_000 or len(self.bbox) != 4:
            raise F0FError("RUNNER_OUTPUT_INVALID")
        for point in self.bbox:
            if (
                not isinstance(point, tuple)
                or len(point) != 2
                or any(isinstance(value, bool) or not isinstance(value, int) for value in point)
            ):
                raise F0FError("RUNNER_OUTPUT_INVALID")

    def __repr__(self) -> str:
        return (
            f"OcrBlock(index={self.index}, text=<redacted>, "
            f"bbox={self.bbox!r}, confidence_ppm={self.confidence_ppm})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class OcrBodyResult:
    body: CanonicalBody = field(repr=False)
    blocks: tuple[OcrBlock, ...] = field(repr=False)
    render_sha256: str
    f0e_text_sequence_sha256: str
    f0e_bbox_sequence_sha256: str
    block_count: int
    character_count: int
    nonblank_character_count: int
    mean_confidence_ppm: int | None
    render_width_px: int
    render_height_px: int

    def __post_init__(self) -> None:
        if not isinstance(self.body, CanonicalBody) or not isinstance(self.blocks, tuple):
            raise F0FError("BODY_CONTRACT_INVALID")
        for digest in (
            self.render_sha256,
            self.f0e_text_sequence_sha256,
            self.f0e_bbox_sequence_sha256,
        ):
            require_sha256(digest)
        if (
            _non_negative(self.block_count) != len(self.blocks)
            or _non_negative(self.character_count) < _non_negative(self.nonblank_character_count)
            or _positive(self.render_width_px) * _positive(self.render_height_px) > 16_000_000
            or tuple(block.index for block in self.blocks) != tuple(range(len(self.blocks)))
        ):
            raise F0FError("BODY_CONTRACT_INVALID")
        if self.mean_confidence_ppm is not None and not 0 <= self.mean_confidence_ppm <= 1_000_000:
            raise F0FError("BODY_CONTRACT_INVALID")

    def __repr__(self) -> str:
        return (
            "OcrBodyResult(body=<redacted>, blocks=<redacted>, "
            f"block_count={self.block_count})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class BoundPageBody:
    """A body cryptographically bound to one immutable F0-E page output."""

    page_evidence_id: uuid.UUID
    selected_route: str
    source_output_sha256: str
    source_page_evidence_sha256: str
    body: CanonicalBody = field(repr=False)
    ocr_block_byte_lengths: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.page_evidence_id, uuid.UUID)
            or not isinstance(self.body, CanonicalBody)
        ):
            raise F0FError("BODY_CONTRACT_INVALID")
        require_sha256(self.source_output_sha256)
        require_sha256(self.source_page_evidence_sha256)
        if self.selected_route == "NATIVE_REFERENCE":
            if (
                self.ocr_block_byte_lengths is not None
                or self.body.sha256 != self.source_output_sha256
            ):
                raise F0FError("BODY_EVIDENCE_MISMATCH")
            return
        if self.selected_route != "LOCAL_OCR" or not isinstance(
            self.ocr_block_byte_lengths, tuple
        ):
            raise F0FError("BODY_CONTRACT_INVALID")
        lengths = self.ocr_block_byte_lengths
        if (
            len(lengths) > 4096
            or any(
                isinstance(length, bool)
                or not isinstance(length, int)
                or length < 0
                for length in lengths
            )
            or _ocr_sequence_sha256(self.body.view(), lengths)
            != self.source_output_sha256
        ):
            raise F0FError("BODY_EVIDENCE_MISMATCH")

    def __repr__(self) -> str:
        return (
            "BoundPageBody(body=<redacted>, "
            f"selected_route={self.selected_route!r})"
        )

    def wipe(self) -> None:
        self.body.wipe()


def _ocr_sequence_sha256(
    body: bytes | bytearray | memoryview, lengths: tuple[int, ...]
) -> str:
    view = memoryview(body).cast("B")
    digest = hashlib.sha256(b"F0E_TEXT_SEQUENCE_V1\0ocr-text-nfc-lf-v1\0")
    offset = 0
    for index, length in enumerate(lengths):
        end = offset + length
        if end > len(view):
            raise F0FError("BODY_EVIDENCE_MISMATCH")
        digest.update(index.to_bytes(4, "big"))
        digest.update(length.to_bytes(8, "big"))
        digest.update(view[offset:end])
        offset = end
        if index + 1 < len(lengths):
            if offset >= len(view) or view[offset] != 10:
                raise F0FError("BODY_EVIDENCE_MISMATCH")
            offset += 1
    if offset != len(view):
        raise F0FError("BODY_EVIDENCE_MISMATCH")
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class AnnotationCandidate:
    queue_id: uuid.UUID
    processing_unit_id: uuid.UUID
    selected_route: str
    queue_ordinal: int
    status: str = "ANNOTATION_REQUIRED"
    benchmark_tier: str = "NONE"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.queue_id, uuid.UUID)
            or not isinstance(self.processing_unit_id, uuid.UUID)
            or self.selected_route not in {"NATIVE_REFERENCE", "LOCAL_OCR"}
            or not 1 <= _positive(self.queue_ordinal) <= 15
            or self.status != "ANNOTATION_REQUIRED"
            or self.benchmark_tier != "NONE"
        ):
            raise F0FError("BODY_CONTRACT_INVALID")


__all__ = (
    "AnnotationCandidate",
    "BodyConfiguration",
    "BoundPageBody",
    "CanonicalBody",
    "F0FError",
    "OcrBlock",
    "OcrBodyResult",
    "PageBodyMetadata",
    "native_body",
    "ocr_body",
    "require_sha256",
)
