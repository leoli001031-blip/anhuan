"""Redacted contracts for the local Fixture annotation workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
import unicodedata
import uuid


_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_LABEL_BYTES = 4 * 1024 * 1024


class F0GError(RuntimeError):
    """A fixed-code error which cannot echo a body, label, key or token."""

    _CODES = frozenset(
        {
            "ANNOTATION_ACCEPTANCE_MISMATCH",
            "ANNOTATION_ADJUDICATION_DENIED",
            "ANNOTATION_ASSIGNMENT_DENIED",
            "ANNOTATION_BODY_INVALID",
            "ANNOTATION_CONTRACT_INVALID",
            "ANNOTATION_DATABASE_FAILED",
            "ANNOTATION_LABEL_INVALID",
            "ANNOTATION_PREPARE_FAILED",
            "ANNOTATION_STATE_INVALID",
            "ANNOTATION_UNAVAILABLE",
            "LOCAL_ONLY_REQUIRED",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if code in self._CODES else "ANNOTATION_CONTRACT_INVALID"
        super().__init__(self.code)

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"F0GError({self.code!r})"

    def to_dict(self) -> dict[str, str]:
        return {"error": "F0G_ERROR", "reason_code": self.code}


@dataclass(frozen=True, slots=True)
class AssignmentMetadata:
    assignment_id: uuid.UUID
    queue_id: uuid.UUID
    assignment_role: str
    label_slot: int | None
    selection_ordinal: int
    guideline_version: str
    guideline_sha256: str
    assignment_status: str
    own_label_submitted: bool
    labels_submitted: int | None
    adjudication_recorded: bool

    def __post_init__(self) -> None:
        if (
            self.assignment_role not in {"ANNOTATOR_ONE", "ANNOTATOR_TWO", "ADJUDICATOR"}
            or self.label_slot not in {None, 1, 2}
            or (self.assignment_role == "ANNOTATOR_ONE" and self.label_slot != 1)
            or (self.assignment_role == "ANNOTATOR_TWO" and self.label_slot != 2)
            or (self.assignment_role == "ADJUDICATOR" and self.label_slot is not None)
            or self.selection_ordinal <= 0
            or not self.guideline_version
            or _SHA256.fullmatch(self.guideline_sha256) is None
            or _SAFE_CODE.fullmatch(self.assignment_status) is None
            or (
                self.assignment_role == "ADJUDICATOR"
                and (
                    self.labels_submitted is None
                    or not 0 <= self.labels_submitted <= 2
                    or self.own_label_submitted
                )
            )
            or (
                self.assignment_role != "ADJUDICATOR"
                and self.labels_submitted is not None
            )
        ):
            raise F0GError("ANNOTATION_CONTRACT_INVALID")

    @property
    def adjudication_ready(self) -> bool:
        return (
            self.assignment_role == "ADJUDICATOR"
            and self.assignment_status == "ADJUDICATION_READY"
            and self.labels_submitted == 2
            and not self.adjudication_recorded
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "assignment_id": str(self.assignment_id),
            "queue_id": str(self.queue_id),
            "assignment_role": self.assignment_role,
            "label_slot": self.label_slot,
            "selection_ordinal": self.selection_ordinal,
            "guideline_version": self.guideline_version,
            "guideline_sha256": self.guideline_sha256,
            "assignment_status": self.assignment_status,
            "own_label_submitted": self.own_label_submitted,
            "labels_submitted": self.labels_submitted,
            "adjudication_recorded": self.adjudication_recorded,
            "adjudication_ready": self.adjudication_ready,
        }


@dataclass(frozen=True, slots=True)
class LabelMetadata:
    label_id: uuid.UUID
    label_ordinal: int
    label_sha256: str
    label_size_bytes: int

    def __post_init__(self) -> None:
        if (
            self.label_ordinal not in {1, 2}
            or _SHA256.fullmatch(self.label_sha256) is None
            or not 0 <= self.label_size_bytes <= MAX_LABEL_BYTES
        ):
            raise F0GError("ANNOTATION_CONTRACT_INVALID")

    def to_dict(self) -> dict[str, object]:
        return {
            "label_id": str(self.label_id),
            "label_ordinal": self.label_ordinal,
            "label_sha256": self.label_sha256,
            "label_size_bytes": self.label_size_bytes,
        }


class SensitiveBytes:
    """Owned response/request bytes with redacted display and explicit wiping."""

    __slots__ = ("_buffer", "_sha256", "_wiped")

    def __init__(self, value: bytes | bytearray | memoryview, *, maximum: int) -> None:
        try:
            view = memoryview(value).cast("B")
            if len(view) > maximum:
                raise F0GError("ANNOTATION_BODY_INVALID")
            self._buffer = bytearray(view)
        except F0GError:
            raise
        except (TypeError, ValueError):
            raise F0GError("ANNOTATION_BODY_INVALID") from None
        self._sha256 = hashlib.sha256(self._buffer).hexdigest()
        self._wiped = False

    def __enter__(self) -> SensitiveBytes:
        if self._wiped:
            raise F0GError("ANNOTATION_BODY_INVALID")
        return self

    def __exit__(self, *_: object) -> None:
        self.wipe()

    def __repr__(self) -> str:
        state = "wiped" if self._wiped else "owned"
        return f"SensitiveBytes(state={state!r}, bytes={len(self._buffer)})"

    __str__ = __repr__

    @property
    def sha256(self) -> str:
        return self._sha256

    @property
    def byte_count(self) -> int:
        return len(self._buffer)

    def view(self) -> memoryview:
        if self._wiped:
            raise F0GError("ANNOTATION_BODY_INVALID")
        return memoryview(self._buffer).toreadonly()

    def wipe(self) -> None:
        self._buffer[:] = b"\0" * len(self._buffer)
        self._buffer.clear()
        self._wiped = True


class CanonicalLabel(SensitiveBytes):
    """Strict UTF-8/NFC/LF label accepted only when already canonical."""

    def __init__(self, value: bytes | bytearray | memoryview) -> None:
        try:
            super().__init__(value, maximum=MAX_LABEL_BYTES)
        except F0GError:
            raise F0GError("ANNOTATION_LABEL_INVALID") from None
        try:
            decoded = self.view().tobytes().decode("utf-8", errors="strict")
            canonical = unicodedata.normalize(
                "NFC", decoded.replace("\r\n", "\n").replace("\r", "\n")
            ).encode("utf-8", errors="strict")
        except (UnicodeDecodeError, UnicodeEncodeError):
            self.wipe()
            raise F0GError("ANNOTATION_LABEL_INVALID") from None
        if canonical != self.view():
            self.wipe()
            raise F0GError("ANNOTATION_LABEL_INVALID")


@dataclass(frozen=True, slots=True)
class FixtureActorSession:
    role: str
    actor_id: uuid.UUID
    session_id: uuid.UUID
    token: str = field(repr=False)

    def __post_init__(self) -> None:
        if self.role not in {"ANNOTATOR_ONE", "ANNOTATOR_TWO", "ADJUDICATOR"}:
            raise F0GError("ANNOTATION_CONTRACT_INVALID")
        if not 32 <= len(self.token) <= 256:
            raise F0GError("ANNOTATION_CONTRACT_INVALID")


__all__ = (
    "AssignmentMetadata",
    "CanonicalLabel",
    "F0GError",
    "FixtureActorSession",
    "LabelMetadata",
    "MAX_LABEL_BYTES",
    "SensitiveBytes",
)
