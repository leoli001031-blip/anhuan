"""Fail-closed contracts for F0-I canonical blocks and chunks.

Only metadata is printable.  Canonical body bytes remain in an owned mutable
buffer so callers can encrypt them and then wipe them deterministically.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import re
import unicodedata
import uuid


CANONICAL_TEXT_RULE = "UTF8_NFC_LF_V1"
CHUNK_RULE = "UNICODE_300_800_NO_OVERLAP_V1"
LEAF_RULE = "ORDERED_UTF8_SPAN_COVERAGE_V1"
MAX_CANONICAL_BYTES = 4 * 1024 * 1024
MAX_CANONICAL_CHARACTERS = 1_000_000
MAX_LEAF_BLOCKS = 4096
MAX_CHILD_CHUNKS = 4096

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_SAFE_NAMESPACE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{2,95}$")


class F0IError(RuntimeError):
    """A stable public reason code that never reflects private input."""

    _CODES = frozenset(
        {
            "ARTIFACT_GENERATION_FAILED",
            "CANONICAL_BODY_INVALID",
            "CANONICAL_BODY_LIMIT",
            "CANONICAL_CONTRACT_INVALID",
            "CANONICAL_RECONSTRUCTION_FAILED",
            "CHUNK_RULE_INVALID",
            "DATABASE_CONFIGURATION_INVALID",
            "DATABASE_OPERATION_FAILED",
            "GEOMETRY_INVALID",
            "HOST_LOCK_BUSY",
            "HOST_LOCK_INVALID",
            "KEYFILE_ALREADY_EXISTS",
            "KEYFILE_INVALID",
            "KEYFILE_NOT_AVAILABLE",
            "LOCK_INVALID",
            "LOCK_UNAVAILABLE",
            "PERSISTENCE_FAILED",
            "REPLAY_MISMATCH",
            "RESOURCE_LIMIT_EXCEEDED",
            "SOURCE_OBJECT_CHANGED",
            "SOURCE_OBJECT_INVALID",
            "STRUCTURE_LOCATION_INVALID",
            "STRUCTURE_PARSE_FAILED",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if code in self._CODES else "CANONICAL_CONTRACT_INVALID"
        super().__init__(self.code)

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"F0IError({self.code!r})"

    def to_dict(self) -> dict[str, str]:
        return {"error": "F0I_ERROR", "reason_code": self.code}


def require_sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise F0IError("CANONICAL_CONTRACT_INVALID")
    return value


def require_safe_code(value: object) -> str:
    if not isinstance(value, str) or _SAFE_CODE.fullmatch(value) is None:
        raise F0IError("CANONICAL_CONTRACT_INVALID")
    return value


def require_non_negative(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise F0IError("CANONICAL_CONTRACT_INVALID")
    return value


def require_positive(value: object) -> int:
    result = require_non_negative(value)
    if result == 0:
        raise F0IError("CANONICAL_CONTRACT_INVALID")
    return result


def canonical_json_bytes(value: object) -> bytes:
    """Serialize body-free metadata with one deterministic encoding."""

    try:
        return json.dumps(
            _json_value(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except F0IError:
        raise
    except (TypeError, ValueError, OverflowError, UnicodeError):
        raise F0IError("CANONICAL_CONTRACT_INVALID") from None


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def stable_uuid4(namespace: str, *values: object) -> uuid.UUID:
    """Return a deterministic UUID with RFC 4122 version/variant bits."""

    if not isinstance(namespace, str) or _SAFE_NAMESPACE.fullmatch(namespace) is None:
        raise F0IError("CANONICAL_CONTRACT_INVALID")
    raw = bytearray(
        hashlib.sha256(canonical_json_bytes([namespace, *values])).digest()[:16]
    )
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(raw))


def chain_sha256(
    namespace: str, previous_sha256: str | None, payload: object
) -> str:
    """Bind an ordered record to its predecessor without accepting raw bodies."""

    if not isinstance(namespace, str) or _SAFE_NAMESPACE.fullmatch(namespace) is None:
        raise F0IError("CANONICAL_CONTRACT_INVALID")
    if previous_sha256 is not None:
        require_sha256(previous_sha256)
    return canonical_sha256(
        {
            "namespace": namespace,
            "payload": payload,
            "previous_sha256": previous_sha256,
        }
    )


class SensitiveCanonicalBody:
    """Owned strict UTF-8/NFC/LF bytes with redacted display and zeroization."""

    __slots__ = ("_buffer", "_characters", "_nonblank", "_sha256", "_wiped")

    def __init__(
        self,
        value: bytes | bytearray | memoryview,
        *,
        maximum_bytes: int = MAX_CANONICAL_BYTES,
        maximum_characters: int = MAX_CANONICAL_CHARACTERS,
    ) -> None:
        if (
            isinstance(maximum_bytes, bool)
            or not isinstance(maximum_bytes, int)
            or maximum_bytes < 0
            or maximum_bytes > MAX_CANONICAL_BYTES
            or isinstance(maximum_characters, bool)
            or not isinstance(maximum_characters, int)
            or maximum_characters < 0
            or maximum_characters > MAX_CANONICAL_CHARACTERS
        ):
            raise F0IError("CANONICAL_CONTRACT_INVALID")
        try:
            view = memoryview(value).cast("B")
            if len(view) > maximum_bytes:
                raise F0IError("CANONICAL_BODY_LIMIT")
            buffer = bytearray(view)
        except F0IError:
            raise
        except (TypeError, ValueError):
            raise F0IError("CANONICAL_BODY_INVALID") from None
        try:
            text = buffer.decode("utf-8", errors="strict")
            if (
                "\r" in text
                or unicodedata.normalize("NFC", text) != text
                or len(text) > maximum_characters
            ):
                raise F0IError("CANONICAL_BODY_INVALID")
        except F0IError:
            buffer[:] = b"\0" * len(buffer)
            buffer.clear()
            raise
        except UnicodeError:
            buffer[:] = b"\0" * len(buffer)
            buffer.clear()
            raise F0IError("CANONICAL_BODY_INVALID") from None
        self._buffer = buffer
        self._characters = len(text)
        self._nonblank = sum(not character.isspace() for character in text)
        self._sha256 = hashlib.sha256(buffer).hexdigest()
        self._wiped = False

    def __enter__(self) -> SensitiveCanonicalBody:
        self._require_live()
        return self

    def __exit__(self, *_: object) -> None:
        self.wipe()

    def __repr__(self) -> str:
        state = "wiped" if self._wiped else "owned"
        return f"SensitiveCanonicalBody(state={state!r}, bytes={len(self._buffer)})"

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
        return CANONICAL_TEXT_RULE

    def view(self) -> memoryview:
        self._require_live()
        return memoryview(self._buffer).toreadonly()

    def slice(self, start_byte: int, end_byte: int) -> memoryview:
        self._require_live()
        start = require_non_negative(start_byte)
        end = require_non_negative(end_byte)
        if start > end or end > len(self._buffer):
            raise F0IError("CANONICAL_CONTRACT_INVALID")
        return memoryview(self._buffer)[start:end].toreadonly()

    def wipe(self) -> None:
        self._buffer[:] = b"\0" * len(self._buffer)
        self._buffer.clear()
        self._wiped = True

    def _require_live(self) -> None:
        if self._wiped:
            raise F0IError("CANONICAL_BODY_INVALID")


@dataclass(frozen=True, slots=True)
class IdentityBinding:
    """Immutable provenance inputs shared by every ID and chain in one unit."""

    tenant_id: uuid.UUID
    document_version_id: uuid.UUID
    source_processing_unit_id: uuid.UUID | None
    structure_unit_sha256: str | None
    source_version_sha256: str
    f0h_model_sha256: str
    f0h_configuration_sha256: str
    parsing_rule_sha256: str
    chunking_rule_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, uuid.UUID) or not isinstance(
            self.document_version_id, uuid.UUID
        ):
            raise F0IError("CANONICAL_CONTRACT_INVALID")
        if (self.source_processing_unit_id is None) == (
            self.structure_unit_sha256 is None
        ):
            raise F0IError("CANONICAL_CONTRACT_INVALID")
        if self.source_processing_unit_id is not None and not isinstance(
            self.source_processing_unit_id, uuid.UUID
        ):
            raise F0IError("CANONICAL_CONTRACT_INVALID")
        if self.structure_unit_sha256 is not None:
            require_sha256(self.structure_unit_sha256)
        for value in (
            self.source_version_sha256,
            self.f0h_model_sha256,
            self.f0h_configuration_sha256,
            self.parsing_rule_sha256,
            self.chunking_rule_sha256,
        ):
            require_sha256(value)

    def identity_payload(self) -> dict[str, object]:
        return {
            "tenant_id": str(self.tenant_id),
            "document_version_id": str(self.document_version_id),
            "processing_unit_kind": (
                "UPSTREAM_VISUAL"
                if self.source_processing_unit_id is not None
                else "NATIVE_STRUCTURE"
            ),
            "source_processing_unit_id": (
                str(self.source_processing_unit_id)
                if self.source_processing_unit_id is not None
                else None
            ),
            "structure_unit_sha256": self.structure_unit_sha256,
            "source_version_sha256": self.source_version_sha256,
            "f0h_model_sha256": self.f0h_model_sha256,
            "f0h_configuration_sha256": self.f0h_configuration_sha256,
            "parsing_rule_sha256": self.parsing_rule_sha256,
            "chunking_rule_sha256": self.chunking_rule_sha256,
        }


@dataclass(frozen=True, slots=True, repr=False)
class LeafInput:
    """One observed leaf and its body-free location evidence."""

    text: str = field(repr=False)
    block_kind: str
    locator_kind: str
    locator_sha256: str
    separator_after: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not isinstance(self.separator_after, str):
            raise F0IError("CANONICAL_CONTRACT_INVALID")
        require_safe_code(self.block_kind)
        require_safe_code(self.locator_kind)
        require_sha256(self.locator_sha256)

    @property
    def location_kind(self) -> str:
        return self.locator_kind

    @property
    def location_sha256(self) -> str:
        return self.locator_sha256

    def __repr__(self) -> str:
        return (
            "LeafInput(text=<redacted>, separator_after=<redacted>, "
            f"block_kind={self.block_kind!r}, locator_kind={self.locator_kind!r})"
        )


@dataclass(frozen=True, slots=True)
class Utf8Span:
    start_byte: int
    end_byte: int
    start_character: int
    end_character: int

    def __post_init__(self) -> None:
        for value in (
            self.start_byte,
            self.end_byte,
            self.start_character,
            self.end_character,
        ):
            require_non_negative(value)
        if self.start_byte > self.end_byte or self.start_character > self.end_character:
            raise F0IError("CANONICAL_CONTRACT_INVALID")

    @property
    def byte_count(self) -> int:
        return self.end_byte - self.start_byte

    @property
    def character_count(self) -> int:
        return self.end_character - self.start_character

    def to_dict(self) -> dict[str, int]:
        return {
            "start_byte": self.start_byte,
            "end_byte": self.end_byte,
            "start_character": self.start_character,
            "end_character": self.end_character,
        }


@dataclass(frozen=True, slots=True)
class BlockDraft:
    block_id: uuid.UUID
    ordinal: int
    block_kind: str
    locator_kind: str
    locator_sha256: str
    span: Utf8Span
    plaintext_sha256: str
    plaintext_bytes: int
    plaintext_characters: int
    previous_chain_sha256: str | None
    chain_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.block_id, uuid.UUID):
            raise F0IError("CANONICAL_CONTRACT_INVALID")
        require_positive(self.ordinal)
        require_safe_code(self.block_kind)
        require_safe_code(self.locator_kind)
        require_sha256(self.locator_sha256)
        require_sha256(self.plaintext_sha256)
        require_sha256(self.chain_sha256)
        require_non_negative(self.plaintext_bytes)
        require_non_negative(self.plaintext_characters)
        if self.previous_chain_sha256 is not None:
            require_sha256(self.previous_chain_sha256)
        if (
            self.plaintext_bytes != self.span.byte_count
            or self.plaintext_characters != self.span.character_count
        ):
            raise F0IError("CANONICAL_CONTRACT_INVALID")

    @property
    def location_kind(self) -> str:
        return self.locator_kind

    @property
    def location_sha256(self) -> str:
        return self.locator_sha256


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    chunk_id: uuid.UUID
    parent_chunk_id: uuid.UUID | None
    chunk_level: str
    ordinal: int
    is_tail: bool
    span: Utf8Span
    plaintext_sha256: str
    plaintext_bytes: int
    plaintext_characters: int
    previous_chain_sha256: str | None
    chain_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.chunk_id, uuid.UUID):
            raise F0IError("CANONICAL_CONTRACT_INVALID")
        require_safe_code(self.chunk_level)
        require_non_negative(self.ordinal)
        if not isinstance(self.is_tail, bool):
            raise F0IError("CANONICAL_CONTRACT_INVALID")
        require_sha256(self.plaintext_sha256)
        require_sha256(self.chain_sha256)
        require_non_negative(self.plaintext_bytes)
        require_non_negative(self.plaintext_characters)
        if self.previous_chain_sha256 is not None:
            require_sha256(self.previous_chain_sha256)
        if self.chunk_level == "PARENT":
            if self.parent_chunk_id is not None or self.ordinal != 0 or self.is_tail:
                raise F0IError("CANONICAL_CONTRACT_INVALID")
        elif self.chunk_level == "CHILD":
            if (
                not isinstance(self.parent_chunk_id, uuid.UUID)
                or self.ordinal <= 0
                or not isinstance(self.is_tail, bool)
            ):
                raise F0IError("CANONICAL_CONTRACT_INVALID")
        else:
            raise F0IError("CANONICAL_CONTRACT_INVALID")
        if (
            self.plaintext_bytes != self.span.byte_count
            or self.plaintext_characters != self.span.character_count
        ):
            raise F0IError("CANONICAL_CONTRACT_INVALID")


@dataclass(frozen=True, slots=True)
class ChunkBlockLinkDraft:
    link_id: uuid.UUID
    chunk_id: uuid.UUID
    block_id: uuid.UUID
    link_ordinal: int
    intersection_span: Utf8Span

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, uuid.UUID)
            for value in (self.link_id, self.chunk_id, self.block_id)
        ):
            raise F0IError("CANONICAL_CONTRACT_INVALID")
        require_positive(self.link_ordinal)


@dataclass(frozen=True, slots=True, repr=False)
class CanonicalUnitDraft:
    binding: IdentityBinding
    body: SensitiveCanonicalBody = field(repr=False)
    blocks: tuple[BlockDraft, ...]
    parent: ChunkDraft
    children: tuple[ChunkDraft, ...]
    links: tuple[ChunkBlockLinkDraft, ...]
    unit_chain_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.binding, IdentityBinding) or not isinstance(
            self.body, SensitiveCanonicalBody
        ):
            raise F0IError("CANONICAL_CONTRACT_INVALID")
        if (
            not isinstance(self.blocks, tuple)
            or not isinstance(self.children, tuple)
            or not isinstance(self.links, tuple)
            or not self.blocks
            or not self.children
            or self.parent.chunk_level != "PARENT"
            or any(child.chunk_level != "CHILD" for child in self.children)
        ):
            raise F0IError("CANONICAL_CONTRACT_INVALID")
        require_sha256(self.unit_chain_sha256)

    def __repr__(self) -> str:
        return (
            "CanonicalUnitDraft(body=<redacted>, "
            f"blocks={len(self.blocks)}, children={len(self.children)})"
        )

    @property
    def canonical_body_sha256(self) -> str:
        return self.body.sha256

    @property
    def canonical_body_bytes(self) -> int:
        return self.body.byte_count

    @property
    def canonical_body_characters(self) -> int:
        return self.body.character_count

    def wipe(self) -> None:
        self.body.wipe()


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise F0IError("CANONICAL_CONTRACT_INVALID")
            result[key] = _json_value(item)
        return result
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray, memoryview)
    ):
        return [_json_value(item) for item in value]
    raise F0IError("CANONICAL_CONTRACT_INVALID")


__all__ = (
    "BlockDraft",
    "CANONICAL_TEXT_RULE",
    "CHUNK_RULE",
    "CanonicalUnitDraft",
    "ChunkBlockLinkDraft",
    "ChunkDraft",
    "F0IError",
    "IdentityBinding",
    "LEAF_RULE",
    "LeafInput",
    "MAX_CANONICAL_BYTES",
    "MAX_CANONICAL_CHARACTERS",
    "MAX_CHILD_CHUNKS",
    "MAX_LEAF_BLOCKS",
    "SensitiveCanonicalBody",
    "Utf8Span",
    "canonical_json_bytes",
    "canonical_sha256",
    "chain_sha256",
    "require_non_negative",
    "require_positive",
    "require_safe_code",
    "require_sha256",
    "stable_uuid4",
)
