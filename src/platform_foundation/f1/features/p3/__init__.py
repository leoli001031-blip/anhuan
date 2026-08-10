"""P3 controlled-ingestion feature contracts.

The package intentionally has no import-time access to PostgreSQL, MinIO,
Redis, ClamAV, OCR, or RAGFlow.  Runtime integrations live behind explicit
service calls so importing the API never starts processing or external work.
"""

from .contracts import (
    ALLOWED_FORMATS,
    MAX_ATTEMPTS,
    RESOURCE_POLICY_VERSION,
    CapabilitiesOut,
    CapabilityLimitsOut,
    DocumentDetailOut,
    DocumentListOut,
    DocumentSummaryOut,
    IngestionError,
    PageTextOut,
    PreviewManifestOut,
    PreviewUnitOut,
    UploadPreflight,
    VersionOut,
    WorksheetGridOut,
    preflight_stream,
)

__all__ = (
    "ALLOWED_FORMATS",
    "MAX_ATTEMPTS",
    "RESOURCE_POLICY_VERSION",
    "CapabilitiesOut",
    "CapabilityLimitsOut",
    "DocumentDetailOut",
    "DocumentListOut",
    "DocumentSummaryOut",
    "IngestionError",
    "PageTextOut",
    "PreviewManifestOut",
    "PreviewUnitOut",
    "UploadPreflight",
    "VersionOut",
    "WorksheetGridOut",
    "preflight_stream",
)
