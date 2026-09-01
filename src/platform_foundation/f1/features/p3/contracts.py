"""Body-safe public contracts for P3 controlled ingestion.

Only fixed reason codes may cross this boundary.  User-controlled names and
document bodies belong in authenticated API responses/DB rows; they must not
be interpolated into exceptions, audit events, traces, or logs.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Annotated, BinaryIO, Literal

from pydantic import BaseModel, ConfigDict, Field


RESOURCE_POLICY_VERSION = "p3-v1"
MAX_ATTEMPTS = 3
MAX_PREVIEW_BYTES = 256 * 1024
MAX_JPEG_PREVIEW_BYTES = 20 * 1024 * 1024
MAX_PREVIEW_CHARACTERS = 100_000
MAX_OOXML_ENTRIES = 2_048
MAX_OOXML_ENTRY_BYTES = 16 * 1024 * 1024
MAX_OOXML_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_OOXML_COMPRESSION_RATIO = 100
MAX_PDF_PAGES = 128
MAX_JPEG_PIXELS = 40_000_000
MAX_JPEG_EDGE = 10_000
MAX_VERSIONS_PER_DOCUMENT = 100
MAX_DOCX_PAGES = 128
MAX_XLSX_SHEETS = 32
MAX_XLSX_ROWS_PER_SHEET = 200
MAX_XLSX_COLUMNS = 50
SCAN_TIMEOUT_SECONDS = 60
PREVIEW_TIMEOUT_SECONDS = 30


@dataclass(frozen=True, slots=True)
class FormatLimit:
    kind: str
    content_type: str
    extensions: tuple[str, ...]
    max_bytes: int
    magic: bytes


ALLOWED_FORMATS: dict[str, FormatLimit] = {
    "pdf": FormatLimit(
        kind="pdf",
        content_type="application/pdf",
        extensions=(".pdf",),
        max_bytes=50 * 1024 * 1024,
        magic=b"%PDF-",
    ),
    "docx": FormatLimit(
        kind="docx",
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        extensions=(".docx",),
        max_bytes=25 * 1024 * 1024,
        magic=b"PK\x03\x04",
    ),
    "xlsx": FormatLimit(
        kind="xlsx",
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        extensions=(".xlsx",),
        max_bytes=25 * 1024 * 1024,
        magic=b"PK\x03\x04",
    ),
    "jpeg": FormatLimit(
        kind="jpeg",
        content_type="image/jpeg",
        extensions=(".jpg", ".jpeg"),
        max_bytes=20 * 1024 * 1024,
        magic=b"\xff\xd8\xff",
    ),
}

_BY_CONTENT_TYPE = {item.content_type: item for item in ALLOWED_FORMATS.values()}
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ProcessingStage(str, Enum):
    RECEIVED = "received"
    SCANNING = "scanning"
    VALIDATING = "validating"
    PREVIEWING = "previewing"
    READY = "ready"
    RETRY_WAIT = "retry_wait"
    REJECTED = "rejected"
    FAILED = "failed"


class QuarantineState(str, Enum):
    HELD = "held"
    RELEASED = "released"
    BLOCKED = "blocked"


class ScanStatus(str, Enum):
    QUEUED = "queued"
    SCANNING = "scanning"
    CLEAN = "clean"
    INFECTED = "infected"
    ERROR = "error"
    UNAVAILABLE = "unavailable"


class PreviewStatus(str, Enum):
    BLOCKED = "blocked"
    QUEUED = "queued"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class IngestionError(RuntimeError):
    """Controlled failure carrying only a fixed, body-free reason code."""

    def __init__(self, code: str, *, http_status: int = 422) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True, slots=True)
class UploadPreflight:
    kind: str
    display_filename: str
    content_type: str
    size: int
    content_sha256: str

    def __post_init__(self) -> None:
        if self.kind not in ALLOWED_FORMATS:
            raise ValueError("P3_FORMAT_NOT_ALLOWED")
        if not _SHA256_RE.fullmatch(self.content_sha256):
            raise ValueError("P3_SOURCE_SHA_INVALID")


AllowedAction = Literal[
    "create_document",
    "upload_version",
    "set_knowledge_scope",
    "process",
    "retry",
    "release",
    "reject",
]
PreviewKind = Literal["page_text", "sheet_grid", "image"]
DeclaredMaterialKind = Literal["policy", "report", "unknown"]
KnowledgeScopeKind = Literal["service_provider", "client"]
AutoPipelineStageStatus = Literal[
    "disabled", "pending", "running", "ready", "failed", "skipped"
]


class KnowledgeScopeOut(BaseModel):
    """Vendor-neutral knowledge namespace identity.

    ``id`` is the only namespace key exposed by the product API.  Physical
    dataset names or provider-specific identifiers never cross this boundary.
    """

    id: uuid.UUID
    kind: KnowledgeScopeKind
    client_account_id: uuid.UUID | None = None
    client_display_name: str | None = Field(default=None, max_length=200)


class KnowledgeScopeUpdateIn(BaseModel):
    kind: KnowledgeScopeKind
    client_account_id: uuid.UUID | None = None


class AllowedTypeOut(BaseModel):
    content_type: str
    extensions: list[str]
    preview_kind: PreviewKind
    max_file_bytes: int = Field(gt=0)


class CapabilityLimitsOut(BaseModel):
    max_file_bytes: int = max(item.max_bytes for item in ALLOWED_FORMATS.values())
    max_versions_per_document: int = MAX_VERSIONS_PER_DOCUMENT
    max_pdf_pages: int = MAX_PDF_PAGES
    max_docx_pages: int = MAX_DOCX_PAGES
    max_xlsx_sheets: int = MAX_XLSX_SHEETS
    max_xlsx_rows_per_sheet: int = MAX_XLSX_ROWS_PER_SHEET
    max_xlsx_columns: int = MAX_XLSX_COLUMNS
    max_image_pixels: int = MAX_JPEG_PIXELS


class ScannerCapabilityOut(BaseModel):
    mode: Literal["local"] = "local"
    state: Literal["ready", "degraded", "unavailable"]
    last_checked_at: datetime | None = None


class CapabilitiesOut(BaseModel):
    upload_enabled: bool = True
    disabled_reason_code: str | None = None
    allowed_types: list[AllowedTypeOut]
    limits: CapabilityLimitsOut = Field(default_factory=CapabilityLimitsOut)
    scanner: ScannerCapabilityOut


class VersionOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    version_number: int = Field(ge=1)
    original_filename: str = Field(min_length=1, max_length=255)
    content_type: str
    size_bytes: int = Field(ge=0)
    workflow_status: Literal["received", "processing", "ready", "blocked", "failed"]
    quarantine_status: Literal["held", "released", "blocked"]
    scan_status: Literal[
        "queued", "scanning", "clean", "infected", "error", "unavailable"
    ]
    preview_status: Literal["blocked", "queued", "generating", "ready", "failed"]
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{1,80}$")
    retryable: bool = False
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[AllowedAction] = Field(default_factory=list)


class DocumentSummaryOut(BaseModel):
    id: uuid.UUID
    display_name: str = Field(min_length=1, max_length=160)
    declared_material_kind: DeclaredMaterialKind = "unknown"
    knowledge_scope: KnowledgeScopeOut
    status: Literal["processing", "ready", "blocked", "failed"]
    version_count: int = Field(ge=0)
    latest_version: VersionOut | None
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[AllowedAction] = Field(default_factory=list)


class DocumentDetailOut(DocumentSummaryOut):
    versions: list[VersionOut]


class DocumentListOut(BaseModel):
    items: list[DocumentSummaryOut]
    next_cursor: str | None = None
    allowed_actions: list[AllowedAction] = Field(default_factory=list)


class AutoPipelineStageOut(BaseModel):
    status: AutoPipelineStageStatus
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{1,80}$")


class AutoPipelineOut(BaseModel):
    """Derived, body-free status for the local automatic PDF pipeline."""

    model_config = ConfigDict(serialize_by_alias=True)

    schema_version: Literal["anhuan-material-auto-pipeline-v1"] = Field(
        default="anhuan-material-auto-pipeline-v1",
        alias="schema",
    )
    version_id: uuid.UUID
    enabled: bool
    scope_kind: KnowledgeScopeKind
    ingestion: AutoPipelineStageOut
    analysis: AutoPipelineStageOut
    index: AutoPipelineStageOut
    report: AutoPipelineStageOut


class PreviewUnitOut(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    kind: Literal["page_text", "worksheet_grid", "image"]
    ordinal: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=128)
    width_px: int | None = Field(default=None, ge=1)
    height_px: int | None = Field(default=None, ge=1)
    row_count: int | None = Field(default=None, ge=0)
    column_count: int | None = Field(default=None, ge=0)


class PreviewManifestOut(BaseModel):
    version_id: uuid.UUID
    status: Literal["blocked", "generating", "ready", "failed"]
    kind: PreviewKind
    units: list[PreviewUnitOut] = Field(default_factory=list)
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{1,80}$")
    retryable: bool = False
    generated_at: datetime | None = None


class PageTextOut(BaseModel):
    lines: list[Annotated[str, Field(max_length=80)]] = Field(max_length=48)
    truncated: bool


WorksheetCell = str | int | float | bool | None


def validate_knowledge_scope_selection(
    kind: str, client_account_id: uuid.UUID | None
) -> tuple[KnowledgeScopeKind, uuid.UUID | None]:
    if kind not in {"service_provider", "client"}:
        raise IngestionError("P3_KNOWLEDGE_SCOPE_INVALID")
    if kind == "service_provider" and client_account_id is not None:
        raise IngestionError("P3_KNOWLEDGE_SCOPE_INVALID")
    if kind == "client" and client_account_id is None:
        raise IngestionError("P3_CLIENT_ACCOUNT_REQUIRED")
    return kind, client_account_id


class WorksheetGridOut(BaseModel):
    unit_id: str
    row_offset: int = Field(ge=0)
    total_rows: int = Field(ge=0)
    total_columns: int = Field(ge=0)
    rows: list[list[WorksheetCell]]
    truncated: bool


def capabilities(
    *,
    scanner_state: Literal["ready", "degraded", "unavailable"] = "degraded",
    scanner_checked_at: datetime | None = None,
) -> CapabilitiesOut:
    preview_kinds: dict[str, PreviewKind] = {
        "pdf": "page_text",
        "docx": "page_text",
        "xlsx": "sheet_grid",
        "jpeg": "image",
    }
    return CapabilitiesOut(
        allowed_types=[
            AllowedTypeOut(
                content_type=item.content_type,
                extensions=list(item.extensions),
                preview_kind=preview_kinds[item.kind],
                max_file_bytes=item.max_bytes,
            )
            for item in ALLOWED_FORMATS.values()
        ],
        scanner=ScannerCapabilityOut(
            state=scanner_state, last_checked_at=scanner_checked_at
        ),
    )


def normalize_display_filename(value: str | None) -> str:
    if not isinstance(value, str):
        raise IngestionError("P3_FILENAME_INVALID")
    normalized = unicodedata.normalize("NFC", value).strip()
    if (
        not normalized
        or len(normalized) > 255
        or "/" in normalized
        or "\\" in normalized
        or _CONTROL_RE.search(normalized)
    ):
        raise IngestionError("P3_FILENAME_INVALID")
    return normalized


def normalize_content_type(value: str | None) -> str:
    if not isinstance(value, str):
        raise IngestionError("P3_FORMAT_NOT_ALLOWED")
    return value.partition(";")[0].strip().lower()


def idempotency_key_sha256(value: str | None) -> str:
    if not isinstance(value, str):
        raise IngestionError("P3_IDEMPOTENCY_KEY_REQUIRED", http_status=400)
    normalized = value.strip()
    if not 8 <= len(normalized) <= 128 or _CONTROL_RE.search(normalized):
        raise IngestionError("P3_IDEMPOTENCY_KEY_INVALID", http_status=400)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def preflight_stream(
    file_obj: BinaryIO,
    *,
    filename: str | None,
    content_type: str | None,
) -> UploadPreflight:
    """Hash and minimally sniff one upload without writing an object."""
    display_filename = normalize_display_filename(filename)
    media_type = normalize_content_type(content_type)
    limit = _BY_CONTENT_TYPE.get(media_type)
    if limit is None:
        raise IngestionError("P3_FORMAT_NOT_ALLOWED")
    lowered = display_filename.casefold()
    if not any(lowered.endswith(ext) for ext in limit.extensions):
        raise IngestionError("P3_EXTENSION_MISMATCH")

    digest = hashlib.sha256()
    size = 0
    head = b""
    try:
        file_obj.seek(0)
        while True:
            chunk = file_obj.read(64 * 1024)
            if not chunk:
                break
            if not isinstance(chunk, bytes):
                raise IngestionError("P3_UPLOAD_READ_FAILED")
            size += len(chunk)
            if size > limit.max_bytes:
                raise IngestionError("P3_FILE_TOO_LARGE")
            digest.update(chunk)
            if len(head) < 16:
                head += chunk[: 16 - len(head)]
    except IngestionError:
        raise
    except Exception as error:
        raise IngestionError("P3_UPLOAD_READ_FAILED") from error
    try:
        file_obj.seek(0)
    except Exception as error:
        raise IngestionError("P3_UPLOAD_READ_FAILED") from error
    if size == 0:
        raise IngestionError("P3_EMPTY_FILE")
    if not head.startswith(limit.magic):
        raise IngestionError("P3_CONTAINER_MISMATCH")
    return UploadPreflight(
        kind=limit.kind,
        display_filename=display_filename,
        content_type=media_type,
        size=size,
        content_sha256=digest.hexdigest(),
    )


def collection_allowed_actions(role: str | None) -> list[AllowedAction]:
    if role in {"super_admin", "enterprise_admin", "plant_admin"}:
        return ["create_document"]
    return []


def document_allowed_actions(
    role: str | None, *, knowledge_scope_editable: bool = False
) -> list[AllowedAction]:
    if role in {"super_admin", "enterprise_admin", "plant_admin"}:
        actions: list[AllowedAction] = ["upload_version"]
        if knowledge_scope_editable:
            actions.append("set_knowledge_scope")
        return actions
    return []


def version_allowed_actions(
    role: str | None,
    *,
    workflow_status: str,
    scan_status: str,
    preview_status: str,
    quarantine_status: str,
    attempt: int,
    reason_code: str | None,
) -> list[AllowedAction]:
    if role not in {"super_admin", "enterprise_admin", "plant_admin"}:
        return []
    actions: list[AllowedAction] = []
    if (
        workflow_status == "ready"
        and scan_status == "clean"
        and preview_status == "ready"
        and quarantine_status in {"held", "released"}
    ):
        # ``process`` is idempotent for a ready version: it retries missing
        # material analysis and replays the local index/report coordinator.
        # Non-PDF versions no-op in the processor and advance to a skipped
        # pipeline status.
        actions.append("process")
        if quarantine_status == "held":
            actions.extend(("release", "reject"))
    elif workflow_status in {"blocked", "failed"} and attempt < MAX_ATTEMPTS:
        if reason_code in PUBLIC_RETRYABLE_REASON_CODES:
            actions.append("retry")
    elif (
        workflow_status == "received"
        and scan_status == "queued"
        and quarantine_status == "held"
    ):
        actions.extend(("process", "reject"))
    return actions


SCANNER_TRANSPORT_FAILURE_CODES = frozenset(
    {
        "P3_SCANNER_UNAVAILABLE",
        "P3_SCANNER_DNS_FAILED",
        "P3_SCANNER_REFUSED",
        "P3_SCANNER_TIMEOUT",
        "P3_SCANNER_CONNECT_REFUSED",
        "P3_SCANNER_CONNECT_RESET",
        "P3_SCANNER_CONNECT_PIPE",
        "P3_SCANNER_VERSION_REFUSED",
        "P3_SCANNER_VERSION_RESET",
        "P3_SCANNER_VERSION_PIPE",
        "P3_SCANNER_STREAM_REFUSED",
        "P3_SCANNER_STREAM_RESET",
        "P3_SCANNER_STREAM_PIPE",
    }
)
RETRYABLE_REASON_CODES = frozenset(
    {
        *SCANNER_TRANSPORT_FAILURE_CODES,
        "P3_PROCESSING_IN_PROGRESS",
        "P3_SCAN_PROTOCOL_ERROR",
        "P3_SOURCE_READ_FAILED",
        "P3_PREVIEW_TIMEOUT",
        "P3_PREVIEW_TEMPORARY_FAILURE",
        "P3_RELEASE_WRITE_FAILED",
        "OCR_DISABLED",
        "OCR_UNAVAILABLE",
        "OCR_PAGE_LIMIT",
        "OCR_OUTPUT_INSUFFICIENT",
        "OCR_REQUIRED",
        "MATERIAL_ANALYSIS_FAILED",
        "MATERIAL_SOURCE_READ_FAILED",
        "MATERIAL_ANALYSIS_PERSIST_FAILED",
    }
)

PUBLIC_REASON_CODES = {
    "P3_FILE_TOO_LARGE": "FILE_TOO_LARGE",
    "P3_FORMAT_NOT_ALLOWED": "FILE_TYPE_NOT_ALLOWED",
    "P3_EXTENSION_MISMATCH": "FILE_TYPE_NOT_ALLOWED",
    "P3_FILENAME_INVALID": "FILE_TYPE_NOT_ALLOWED",
    "P3_CONTAINER_MISMATCH": "CONTAINER_MISMATCH",
    "P3_EMPTY_FILE": "EMPTY_FILE",
    "P3_UPLOAD_READ_FAILED": "SOURCE_OBJECT_READ_FAILED",
    "P3_IDEMPOTENCY_KEY_CONFLICT": "IDEMPOTENCY_CONFLICT",
    "P3_IDEMPOTENCY_KEY_REQUIRED": "IDEMPOTENCY_KEY_REQUIRED",
    "P3_IDEMPOTENCY_KEY_INVALID": "IDEMPOTENCY_KEY_INVALID",
    "P3_RESERVATION_CONFLICT": "IDEMPOTENCY_CONFLICT",
    "P3_SOURCE_CLAIM_CONFLICT": "IDEMPOTENCY_CONFLICT",
    "P3_DOCUMENT_VERSION_LIMIT": "DOCUMENT_VERSION_LIMIT",
    "P3_DOCUMENT_NOT_FOUND": "NOT_FOUND",
    "P3_PREVIEW_UNIT_NOT_FOUND": "NOT_FOUND",
    "P3_PLANT_NOT_FOUND": "NOT_FOUND",
    "P3_MEMBERSHIP_NOT_FOUND": "NOT_FOUND",
    "P3_MANAGER_REQUIRED": "FORBIDDEN",
    "P3_TITLE_INVALID": "INVALID_REQUEST",
    "P3_MATERIAL_KIND_INVALID": "INVALID_REQUEST",
    "P3_CURSOR_INVALID": "INVALID_REQUEST",
    "P3_FILTER_INVALID": "INVALID_REQUEST",
    "P3_KNOWLEDGE_SCOPE_INVALID": "INVALID_REQUEST",
    "P3_CLIENT_ACCOUNT_REQUIRED": "INVALID_REQUEST",
    "P3_CLIENT_ACCOUNT_NOT_FOUND": "NOT_FOUND",
    "P3_KNOWLEDGE_SCOPE_LOCKED": "ILLEGAL_STATE_TRANSITION",
    "P3_KNOWLEDGE_SCOPE_CONFLICT": "ILLEGAL_STATE_TRANSITION",
    "MATERIAL_SCOPE_NOT_CONFIGURED": "MATERIAL_SCOPE_NOT_CONFIGURED",
    "P3_LIMIT_INVALID": "INVALID_REQUEST",
    "P3_ILLEGAL_STATE_TRANSITION": "ILLEGAL_STATE_TRANSITION",
    "P3_QUARANTINE_FINALIZE_CONFLICT": "ILLEGAL_STATE_TRANSITION",
    "P3_PROCESSING_IN_PROGRESS": "INGESTION_PROCESSING",
    "P3_SCANNER_UNAVAILABLE": "SCAN_ENGINE_UNAVAILABLE",
    "P3_SCANNER_DNS_FAILED": "SCAN_ENGINE_UNAVAILABLE",
    "P3_SCANNER_REFUSED": "SCAN_ENGINE_UNAVAILABLE",
    "P3_SCANNER_CONNECT_REFUSED": "SCAN_ENGINE_UNAVAILABLE",
    "P3_SCANNER_CONNECT_RESET": "SCAN_ENGINE_UNAVAILABLE",
    "P3_SCANNER_CONNECT_PIPE": "SCAN_ENGINE_UNAVAILABLE",
    "P3_SCANNER_VERSION_REFUSED": "SCAN_ENGINE_UNAVAILABLE",
    "P3_SCANNER_VERSION_RESET": "SCAN_ENGINE_UNAVAILABLE",
    "P3_SCANNER_VERSION_PIPE": "SCAN_ENGINE_UNAVAILABLE",
    "P3_SCANNER_STREAM_REFUSED": "SCAN_ENGINE_UNAVAILABLE",
    "P3_SCANNER_STREAM_RESET": "SCAN_ENGINE_UNAVAILABLE",
    "P3_SCANNER_STREAM_PIPE": "SCAN_ENGINE_UNAVAILABLE",
    "P3_SCANNER_TIMEOUT": "SCAN_ENGINE_UNAVAILABLE",
    "P3_SCAN_ENGINE_ERROR": "SCAN_ENGINE_UNAVAILABLE",
    "P3_SCAN_PROTOCOL_ERROR": "SCAN_ENGINE_UNAVAILABLE",
    "P3_SCANNER_TARGET_INVALID": "SCAN_ENGINE_UNAVAILABLE",
    "P3_SCAN_SIZE_INVALID": "PREVIEW_RESOURCE_LIMIT",
    "P3_SCAN_TIMEOUT_INVALID": "SCAN_ENGINE_UNAVAILABLE",
    "P3_MALWARE_DETECTED": "MALWARE_DETECTED",
    "P3_SOURCE_READ_FAILED": "SOURCE_OBJECT_READ_FAILED",
    "P3_SOURCE_IDENTITY_MISMATCH": "SOURCE_OBJECT_READ_FAILED",
    "P3_SOURCE_SHA_INVALID": "SOURCE_OBJECT_READ_FAILED",
    "P3_QUARANTINE_WRITE_FAILED": "SOURCE_OBJECT_STAT_FAILED",
    "P3_PREVIEW_TIMEOUT": "PREVIEW_FAILED",
    "P3_PREVIEW_TEMPORARY_FAILURE": "PREVIEW_FAILED",
    "P3_PREVIEW_OUTPUT_LIMIT": "PREVIEW_RESOURCE_LIMIT",
    "P3_PDF_PAGE_LIMIT": "PREVIEW_RESOURCE_LIMIT",
    "P3_DOCX_PAGE_LIMIT": "PREVIEW_RESOURCE_LIMIT",
    "P3_XLSX_SHEET_LIMIT": "PREVIEW_RESOURCE_LIMIT",
    "P3_OOXML_ENTRY_LIMIT": "PREVIEW_RESOURCE_LIMIT",
    "P3_OOXML_ENTRY_SIZE_LIMIT": "PREVIEW_RESOURCE_LIMIT",
    "P3_OOXML_EXPANDED_LIMIT": "PREVIEW_RESOURCE_LIMIT",
    "P3_OOXML_COMPRESSION_LIMIT": "PREVIEW_RESOURCE_LIMIT",
    "P3_JPEG_PIXEL_LIMIT": "PREVIEW_RESOURCE_LIMIT",
    "P3_PREVIEW_INVALID": "PREVIEW_FAILED",
    "P3_PREVIEW_RANGE_INVALID": "PREVIEW_FAILED",
    "P3_PDF_ENCRYPTED": "PREVIEW_FAILED",
    "P3_PDF_CORRUPT": "PREVIEW_FAILED",
    "P3_PDF_ACTIVE_CONTENT": "PREVIEW_FAILED",
    "P3_OOXML_CORRUPT": "PREVIEW_FAILED",
    "P3_OOXML_STRUCTURE_INVALID": "PREVIEW_FAILED",
    "P3_OOXML_UNSAFE_ENTRY": "PREVIEW_FAILED",
    "P3_OOXML_EXTERNAL_RELATIONSHIP": "PREVIEW_FAILED",
    "P3_XML_ACTIVE_CONTENT": "PREVIEW_FAILED",
    "P3_DOCX_CORRUPT": "PREVIEW_FAILED",
    "P3_XLSX_CORRUPT": "PREVIEW_FAILED",
    "P3_XLSX_STRUCTURE_INVALID": "PREVIEW_FAILED",
    "P3_JPEG_CORRUPT": "PREVIEW_FAILED",
    "P3_PREVIEW_CONTENT_TYPE_INVALID": "PREVIEW_FAILED",
    "P3_PREVIEW_IDENTITY_INVALID": "PREVIEW_FAILED",
    "P3_RELEASE_WRITE_FAILED": "SOURCE_OBJECT_STAT_FAILED",
    "OCR_DISABLED": "MATERIAL_ANALYSIS_RETRY_REQUIRED",
    "OCR_UNAVAILABLE": "MATERIAL_ANALYSIS_RETRY_REQUIRED",
    "OCR_PAGE_LIMIT": "MATERIAL_ANALYSIS_RETRY_REQUIRED",
    "OCR_OUTPUT_INSUFFICIENT": "MATERIAL_ANALYSIS_RETRY_REQUIRED",
    "OCR_REQUIRED": "MATERIAL_ANALYSIS_RETRY_REQUIRED",
    "MATERIAL_ANALYSIS_FAILED": "MATERIAL_ANALYSIS_RETRY_REQUIRED",
    "MATERIAL_SOURCE_READ_FAILED": "MATERIAL_ANALYSIS_RETRY_REQUIRED",
    "MATERIAL_ANALYSIS_PERSIST_FAILED": "MATERIAL_ANALYSIS_RETRY_REQUIRED",
    "MATERIAL_ANALYSIS_CONFIRMED_OCR_REVIEW_REQUIRED": (
        "MATERIAL_ANALYSIS_CONFIRMED_OCR_REVIEW_REQUIRED"
    ),
}
PUBLIC_REASON_CODE_ALLOWLIST = frozenset(PUBLIC_REASON_CODES.values())
PUBLIC_RETRYABLE_REASON_CODES = frozenset(
    PUBLIC_REASON_CODES[code]
    for code in RETRYABLE_REASON_CODES
)


def public_reason_code(code: str | None) -> str | None:
    if code is None:
        return None
    if code in PUBLIC_REASON_CODE_ALLOWLIST:
        return code
    return PUBLIC_REASON_CODES.get(code, "INGESTION_UNAVAILABLE")


def reason_is_retryable(code: str | None) -> bool:
    return public_reason_code(code) in PUBLIC_RETRYABLE_REASON_CODES


__all__ = (
    "ALLOWED_FORMATS",
    "AllowedTypeOut",
    "AutoPipelineOut",
    "AutoPipelineStageOut",
    "AutoPipelineStageStatus",
    "CapabilitiesOut",
    "CapabilityLimitsOut",
    "DocumentDetailOut",
    "DocumentListOut",
    "DocumentSummaryOut",
    "FormatLimit",
    "IngestionError",
    "KnowledgeScopeKind",
    "KnowledgeScopeOut",
    "KnowledgeScopeUpdateIn",
    "MAX_ATTEMPTS",
    "MAX_DOCX_PAGES",
    "MAX_JPEG_PIXELS",
    "MAX_JPEG_EDGE",
    "MAX_JPEG_PREVIEW_BYTES",
    "MAX_OOXML_COMPRESSION_RATIO",
    "MAX_OOXML_ENTRIES",
    "MAX_OOXML_ENTRY_BYTES",
    "MAX_OOXML_EXPANDED_BYTES",
    "MAX_PDF_PAGES",
    "MAX_PREVIEW_BYTES",
    "MAX_PREVIEW_CHARACTERS",
    "MAX_VERSIONS_PER_DOCUMENT",
    "MAX_XLSX_COLUMNS",
    "MAX_XLSX_ROWS_PER_SHEET",
    "MAX_XLSX_SHEETS",
    "PREVIEW_TIMEOUT_SECONDS",
    "PreviewManifestOut",
    "PageTextOut",
    "PreviewUnitOut",
    "ProcessingStage",
    "QuarantineState",
    "RESOURCE_POLICY_VERSION",
    "RETRYABLE_REASON_CODES",
    "SCANNER_TRANSPORT_FAILURE_CODES",
    "WorksheetGridOut",
    "SCAN_TIMEOUT_SECONDS",
    "ScanStatus",
    "ScannerCapabilityOut",
    "UploadPreflight",
    "VersionOut",
    "capabilities",
    "collection_allowed_actions",
    "document_allowed_actions",
    "idempotency_key_sha256",
    "normalize_content_type",
    "normalize_display_filename",
    "preflight_stream",
    "public_reason_code",
    "reason_is_retryable",
    "version_allowed_actions",
    "validate_knowledge_scope_selection",
)
