"""Body-free contracts for local OCR evidence execution.

F0-C candidate decisions are immutable inputs.  These contracts describe a
separate F0-E evidence route and deliberately have no field capable of carrying
source paths, database credentials, page images, or extracted text.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import uuid


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_SAFE_RULE = re.compile(r"^[A-Za-z0-9_.:/-]{1,96}$")
_SAFE_INPUT_VERSION = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")

CANDIDATE_DECISIONS = frozenset(
    {
        "NATIVE_CANDIDATE",
        "FULL_PAGE_OCR_REQUIRED",
        "MANUAL_REVIEW_REQUIRED",
    }
)
EVIDENCE_METHODS = frozenset(
    {
        "NATIVE_REFERENCE",
        "LOCAL_OCR",
        "MANUAL_REVIEW_REFERENCE",
        "DEFERRED_CONVERSION_REFERENCE",
        "NO_CONVERSION_EXECUTED",
    }
)
VISUAL_UNIT_KINDS = frozenset({"PAGE", "IMAGE"})


class F0EError(RuntimeError):
    """A stable, content-free F0-E failure."""

    _CODES = frozenset(
        {
            "CONTRACT_INVALID",
            "ROUTE_INVALID",
            "ROUTE_DUPLICATE",
            "SOURCE_OBJECT_INVALID",
            "SOURCE_OBJECT_CHANGED",
            "SOURCE_FD_CLOSED",
            "RUNNER_CONFIGURATION_INVALID",
            "RUNNER_INVOCATION_DENIED",
            "RUNNER_TIMEOUT",
            "RUNNER_OUTPUT_LIMIT",
            "RUNNER_OUTPUT_INVALID",
            "RUNNER_FAILED",
            "DATABASE_OPERATION_FAILED",
            "CONFIGURATION_NOT_AVAILABLE",
            "PLAN_NOT_AVAILABLE",
            "JOB_NOT_AVAILABLE",
            "JOB_LEASE_STALE",
            "EVIDENCE_MISMATCH",
            "REPLAY_MISMATCH",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            code = "CONTRACT_INVALID"
        self.code = code
        super().__init__(code)

    def __str__(self) -> str:
        return self.code

    def to_dict(self) -> dict[str, str]:
        return {"error": "F0E_ERROR", "reason_code": self.code}


def require_sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise F0EError("CONTRACT_INVALID")
    return value


def require_uuid(value: object) -> uuid.UUID:
    if not isinstance(value, uuid.UUID):
        raise F0EError("CONTRACT_INVALID")
    return value


def require_non_negative(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise F0EError("CONTRACT_INVALID")
    return value


def require_positive(value: object) -> int:
    result = require_non_negative(value)
    if result == 0:
        raise F0EError("CONTRACT_INVALID")
    return result


def require_reason_codes(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple) or not value:
        raise F0EError("CONTRACT_INVALID")
    for code in value:
        if not isinstance(code, str) or _SAFE_CODE.fullmatch(code) is None:
            raise F0EError("CONTRACT_INVALID")
    return value


def _require_optional_sha256(value: object) -> str | None:
    if value is None:
        return None
    return require_sha256(value)


def _require_optional_positive(value: object) -> int | None:
    if value is None:
        return None
    return require_positive(value)


def _require_box(
    value: object,
) -> tuple[str, str, str, str] | None:
    if value is None:
        return None
    if not isinstance(value, tuple) or len(value) != 4:
        raise F0EError("CONTRACT_INVALID")
    for coordinate in value:
        if not isinstance(coordinate, str):
            raise F0EError("CONTRACT_INVALID")
        try:
            float(coordinate)
        except ValueError:
            raise F0EError("CONTRACT_INVALID") from None
    return value


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    timeout_ms: int = 60_000
    maximum_stdout_bytes: int = 4 * 1024 * 1024
    maximum_stderr_bytes: int = 64 * 1024
    maximum_source_bytes: int = 64 * 1024 * 1024
    maximum_pages: int = 128
    maximum_selected_pages_per_run: int = 16
    units_per_execution: int = 1
    render_dpi: int = 250
    maximum_pixels: int = 16_000_000
    maximum_memory_bytes: int = 1024 * 1024 * 1024
    maximum_open_files: int = 32
    maximum_processes: int = 1
    concurrency: int = 1

    def __post_init__(self) -> None:
        require_positive(self.timeout_ms)
        require_positive(self.maximum_stdout_bytes)
        require_non_negative(self.maximum_stderr_bytes)
        require_positive(self.maximum_source_bytes)
        if not 1 <= require_positive(self.maximum_pages) <= 128:
            raise F0EError("CONTRACT_INVALID")
        if self.maximum_selected_pages_per_run != 16:
            raise F0EError("CONTRACT_INVALID")
        if self.units_per_execution != 1:
            raise F0EError("CONTRACT_INVALID")
        if self.render_dpi != 250:
            raise F0EError("CONTRACT_INVALID")
        require_positive(self.maximum_pixels)
        require_positive(self.maximum_memory_bytes)
        if not 8 <= require_positive(self.maximum_open_files) <= 256:
            raise F0EError("CONTRACT_INVALID")
        if self.maximum_processes != 1 or self.concurrency != 1:
            raise F0EError("CONTRACT_INVALID")


@dataclass(frozen=True, slots=True)
class SandboxProfile:
    renderer_sha256: str
    ocr_engine_sha256: str
    language_pack_sha256: str
    execution_profile_sha256: str
    normalization_rule: str = "ocr-text-nfc-lf-v1"
    normalization_rule_sha256: str = (
        "2bdd5fa88fb268bb8f2d3334f441699fb461f897a5b04d7680d6a7dfc310d3cc"
    )
    render_dpi: int = 250
    manual_review_confidence_floor_ppm: int = 0
    external_processing_policy: str = "DENY"
    raw_text_persisted: bool = False
    page_images_persisted: bool = False
    container_image_id: str | None = None

    def __post_init__(self) -> None:
        require_sha256(self.renderer_sha256)
        require_sha256(self.ocr_engine_sha256)
        require_sha256(self.language_pack_sha256)
        require_sha256(self.execution_profile_sha256)
        require_sha256(self.normalization_rule_sha256)
        if (
            not isinstance(self.normalization_rule, str)
            or _SAFE_RULE.fullmatch(self.normalization_rule) is None
            or self.render_dpi != 250
            or self.manual_review_confidence_floor_ppm != 0
            or self.external_processing_policy != "DENY"
            or self.raw_text_persisted is not False
            or self.page_images_persisted is not False
        ):
            raise F0EError("CONTRACT_INVALID")
        if self.container_image_id is not None and re.fullmatch(
            r"sha256:[0-9a-f]{64}", self.container_image_id
        ) is None:
            raise F0EError("CONTRACT_INVALID")


@dataclass(frozen=True, slots=True)
class ProcessingUnitRecord:
    processing_unit_id: uuid.UUID
    processing_plan_id: uuid.UUID
    source_unit_id: str
    unit_ordinal: int
    unit_kind: str
    page_no: int
    candidate_decision: str
    reason_codes: tuple[str, ...]
    evidence_sha256: str
    native_text_sha256: str | None = None
    native_characters: int = 0
    bad_character_ppm: int = 0
    rotation: int | None = None
    media_box: tuple[str, str, str, str] | None = None
    crop_box: tuple[str, str, str, str] | None = None
    width_px: int | None = None
    height_px: int | None = None
    expected_total_pages: int = 1

    def __post_init__(self) -> None:
        require_uuid(self.processing_unit_id)
        require_uuid(self.processing_plan_id)
        require_sha256(self.source_unit_id)
        require_positive(self.unit_ordinal)
        require_positive(self.page_no)
        total_pages = require_positive(self.expected_total_pages)
        require_reason_codes(self.reason_codes)
        require_sha256(self.evidence_sha256)
        _require_optional_sha256(self.native_text_sha256)
        require_non_negative(self.native_characters)
        bad_ppm = require_non_negative(self.bad_character_ppm)
        if (
            self.unit_kind not in VISUAL_UNIT_KINDS
            or self.candidate_decision not in CANDIDATE_DECISIONS
            or bad_ppm > 1_000_000
            or self.rotation not in {None, 0, 90, 180, 270}
            or total_pages > 128
            or self.page_no > total_pages
        ):
            raise F0EError("CONTRACT_INVALID")
        media = _require_box(self.media_box)
        crop = _require_box(self.crop_box)
        width = _require_optional_positive(self.width_px)
        height = _require_optional_positive(self.height_px)
        if self.unit_kind == "PAGE":
            if media is None or crop is None or self.rotation is None:
                raise F0EError("CONTRACT_INVALID")
            if width is not None or height is not None:
                raise F0EError("CONTRACT_INVALID")
        else:
            if width is None or height is None:
                raise F0EError("CONTRACT_INVALID")
            if media is not None or crop is not None or self.rotation is not None:
                raise F0EError("CONTRACT_INVALID")
            if total_pages != 1 or self.page_no != 1:
                raise F0EError("CONTRACT_INVALID")


@dataclass(frozen=True, slots=True)
class PageRoute:
    processing_unit_id: uuid.UUID
    processing_plan_id: uuid.UUID
    source_unit_id: str
    unit_ordinal: int
    unit_kind: str
    page_no: int
    candidate_decision: str
    evidence_method: str
    reason_codes: tuple[str, ...]
    source_evidence_sha256: str
    route_sha256: str
    native_text_sha256: str | None = None
    native_characters: int = 0
    native_bad_character_ppm: int = 0
    rotation: int | None = None
    media_box: tuple[str, str, str, str] | None = None
    crop_box: tuple[str, str, str, str] | None = None
    width_px: int | None = None
    height_px: int | None = None
    expected_total_pages: int = 1

    def __post_init__(self) -> None:
        require_uuid(self.processing_unit_id)
        require_uuid(self.processing_plan_id)
        require_sha256(self.source_unit_id)
        require_positive(self.unit_ordinal)
        require_positive(self.page_no)
        total_pages = require_positive(self.expected_total_pages)
        require_reason_codes(self.reason_codes)
        require_sha256(self.source_evidence_sha256)
        require_sha256(self.route_sha256)
        _require_optional_sha256(self.native_text_sha256)
        require_non_negative(self.native_characters)
        bad_ppm = require_non_negative(self.native_bad_character_ppm)
        if (
            self.unit_kind not in VISUAL_UNIT_KINDS
            or self.candidate_decision not in CANDIDATE_DECISIONS
            or self.evidence_method not in EVIDENCE_METHODS
            or bad_ppm > 1_000_000
            or total_pages > 128
            or self.page_no > total_pages
        ):
            raise F0EError("CONTRACT_INVALID")
        _require_box(self.media_box)
        _require_box(self.crop_box)
        _require_optional_positive(self.width_px)
        _require_optional_positive(self.height_px)


@dataclass(frozen=True, slots=True)
class DeferredDocumentRoute:
    processing_plan_id: uuid.UUID
    document_version_id: uuid.UUID
    reason_codes: tuple[str, ...]
    evidence_method: str
    route_sha256: str

    def __post_init__(self) -> None:
        require_uuid(self.processing_plan_id)
        require_uuid(self.document_version_id)
        require_reason_codes(self.reason_codes)
        require_sha256(self.route_sha256)
        if self.evidence_method != "NO_CONVERSION_EXECUTED":
            raise F0EError("CONTRACT_INVALID")


@dataclass(frozen=True, slots=True)
class NormalizedTextEvidence:
    text_sha256: str
    utf8_bytes: int
    characters: int
    non_blank_characters: int
    bad_character_ppm: int
    normalization_rule: str

    def __post_init__(self) -> None:
        require_sha256(self.text_sha256)
        require_non_negative(self.utf8_bytes)
        require_non_negative(self.characters)
        require_non_negative(self.non_blank_characters)
        bad_ppm = require_non_negative(self.bad_character_ppm)
        if bad_ppm > 1_000_000 or _SAFE_RULE.fullmatch(
            self.normalization_rule
        ) is None:
            raise F0EError("CONTRACT_INVALID")


@dataclass(frozen=True, slots=True)
class OcrPageEvidence:
    evidence_id: uuid.UUID
    processing_unit_id: uuid.UUID
    source_unit_id: str
    candidate_decision: str
    selected_route: str
    terminal_status: str
    source_evidence_sha256: str
    render_sha256: str | None
    output_sha256: str | None
    output_block_count: int | None
    output_character_count: int
    output_non_blank_characters: int
    mean_confidence_ppm: int | None
    bbox_summary_sha256: str | None
    reason_code: str
    execution_profile_sha256: str
    raw_text_persisted: bool = False
    page_image_persisted: bool = False

    def __post_init__(self) -> None:
        require_uuid(self.evidence_id)
        require_uuid(self.processing_unit_id)
        require_sha256(self.source_unit_id)
        require_sha256(self.source_evidence_sha256)
        _require_optional_sha256(self.render_sha256)
        _require_optional_sha256(self.output_sha256)
        if self.output_block_count is not None:
            require_non_negative(self.output_block_count)
        require_non_negative(self.output_character_count)
        require_non_negative(self.output_non_blank_characters)
        if self.output_non_blank_characters > self.output_character_count:
            raise F0EError("CONTRACT_INVALID")
        if self.mean_confidence_ppm is not None:
            confidence = require_non_negative(self.mean_confidence_ppm)
            if confidence > 1_000_000:
                raise F0EError("CONTRACT_INVALID")
        _require_optional_sha256(self.bbox_summary_sha256)
        require_sha256(self.execution_profile_sha256)
        if (
            self.candidate_decision not in CANDIDATE_DECISIONS
            or self.selected_route not in EVIDENCE_METHODS
            or self.terminal_status
            not in {
                "NATIVE_REFERENCE",
                "LOCAL_OCR_EVIDENCE",
                "MANUAL_REVIEW_REQUIRED",
            }
            or _SAFE_CODE.fullmatch(self.reason_code) is None
            or self.raw_text_persisted is not False
            or self.page_image_persisted is not False
        ):
            raise F0EError("CONTRACT_INVALID")
        if self.selected_route == "LOCAL_OCR":
            if self.candidate_decision != "FULL_PAGE_OCR_REQUIRED":
                raise F0EError("CONTRACT_INVALID")
            if (
                self.render_sha256 is None
                or self.output_sha256 is None
                or self.output_block_count is None
                or self.bbox_summary_sha256 is None
            ):
                raise F0EError("CONTRACT_INVALID")
            if self.terminal_status not in {
                "LOCAL_OCR_EVIDENCE",
                "MANUAL_REVIEW_REQUIRED",
            }:
                raise F0EError("CONTRACT_INVALID")
            if self.terminal_status == "MANUAL_REVIEW_REQUIRED":
                if (
                    self.reason_code != "LOCAL_OCR_EMPTY_REVIEW_REQUIRED"
                    or self.output_non_blank_characters != 0
                    or self.mean_confidence_ppm is not None
                ):
                    raise F0EError("CONTRACT_INVALID")
            elif (
                self.reason_code != "LOCAL_OCR_CANDIDATE_CAPTURED"
                or self.output_block_count is None
                or self.output_block_count <= 0
                or self.output_character_count <= 0
                or self.output_non_blank_characters <= 0
                or self.mean_confidence_ppm is None
            ):
                raise F0EError("CONTRACT_INVALID")
        elif self.selected_route == "NATIVE_REFERENCE":
            if self.candidate_decision != "NATIVE_CANDIDATE":
                raise F0EError("CONTRACT_INVALID")
            if self.terminal_status != "NATIVE_REFERENCE":
                raise F0EError("CONTRACT_INVALID")
            if (
                self.reason_code != "NATIVE_TEXT_REFERENCE_SELECTED"
                or self.render_sha256 is not None
                or self.output_sha256 is None
                or self.output_block_count != 0
                or self.output_non_blank_characters != self.output_character_count
                or self.mean_confidence_ppm is not None
                or self.bbox_summary_sha256 is not None
            ):
                raise F0EError("CONTRACT_INVALID")
        elif self.selected_route == "MANUAL_REVIEW_REFERENCE":
            if self.candidate_decision != "MANUAL_REVIEW_REQUIRED":
                raise F0EError("CONTRACT_INVALID")
        else:
            raise F0EError("CONTRACT_INVALID")

    @property
    def evidence_method(self) -> str:
        return self.selected_route

    @property
    def output_text_sha256(self) -> str | None:
        return self.output_sha256

    @property
    def output_characters(self) -> int:
        return self.output_character_count

    def to_finalize_payload(self) -> dict[str, object]:
        """Return exactly the migration function's body-free JSON contract."""

        if self.selected_route == "MANUAL_REVIEW_REFERENCE":
            raise F0EError("EVIDENCE_MISMATCH")
        return {
            "evidence_id": str(self.evidence_id),
            "processing_unit_id": str(self.processing_unit_id),
            "selected_route": self.selected_route,
            "terminal_status": self.terminal_status,
            "render_sha256": self.render_sha256,
            "output_sha256": self.output_sha256,
            "output_block_count": self.output_block_count,
            "output_character_count": self.output_character_count,
            "output_non_blank_character_count": self.output_non_blank_characters,
            "mean_confidence_ppm": self.mean_confidence_ppm,
            "bbox_summary_sha256": self.bbox_summary_sha256,
            "reason_code": self.reason_code,
        }


@dataclass(frozen=True, slots=True)
class OcrRunEnvelope:
    run_id: uuid.UUID
    processing_plan_id: uuid.UUID
    configuration_id: uuid.UUID
    input_version: str
    status: str
    page_evidence: tuple[OcrPageEvidence, ...]
    deferred_documents: tuple[DeferredDocumentRoute, ...] = ()
    raw_text_persisted: bool = False
    page_images_persisted: bool = False

    def __post_init__(self) -> None:
        require_uuid(self.run_id)
        require_uuid(self.processing_plan_id)
        require_uuid(self.configuration_id)
        if (
            not isinstance(self.input_version, str)
            or _SAFE_INPUT_VERSION.fullmatch(self.input_version) is None
        ):
            raise F0EError("CONTRACT_INVALID")
        if (
            self.status
            not in {
                "CANDIDATE_EVIDENCE_RECORDED",
                "DEFERRED_CONVERSION_REQUIRED",
            }
            or not isinstance(self.page_evidence, tuple)
            or not isinstance(self.deferred_documents, tuple)
            or self.raw_text_persisted is not False
            or self.page_images_persisted is not False
        ):
            raise F0EError("CONTRACT_INVALID")
        if any(not isinstance(item, OcrPageEvidence) for item in self.page_evidence):
            raise F0EError("CONTRACT_INVALID")
        if any(
            not isinstance(item, DeferredDocumentRoute)
            for item in self.deferred_documents
        ):
            raise F0EError("CONTRACT_INVALID")
        if len({item.processing_unit_id for item in self.page_evidence}) != len(
            self.page_evidence
        ):
            raise F0EError("CONTRACT_INVALID")
        if self.status == "CANDIDATE_EVIDENCE_RECORDED":
            if not self.page_evidence or self.deferred_documents:
                raise F0EError("CONTRACT_INVALID")
        elif self.page_evidence or len(self.deferred_documents) != 1:
            raise F0EError("CONTRACT_INVALID")


__all__ = (
    "CANDIDATE_DECISIONS",
    "EVIDENCE_METHODS",
    "VISUAL_UNIT_KINDS",
    "DeferredDocumentRoute",
    "F0EError",
    "NormalizedTextEvidence",
    "OcrPageEvidence",
    "OcrRunEnvelope",
    "PageRoute",
    "ProcessingUnitRecord",
    "ResourceLimits",
    "SandboxProfile",
    "require_non_negative",
    "require_positive",
    "require_reason_codes",
    "require_sha256",
    "require_uuid",
)
