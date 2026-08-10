"""F0-F controlled Fixture body evidence and annotation-pending boundary."""

from .contracts import (
    AnnotationCandidate,
    BodyConfiguration,
    BoundPageBody,
    CanonicalBody,
    F0FError,
    OcrBlock,
    OcrBodyResult,
    PageBodyMetadata,
)
from .keyfile import LocalFixtureKey, create_keyfile, load_keyfile
from .native import extract_native_page
from .selection import select_annotation_candidates
from .runtime_config import RuntimeBundle, load_runtime_bundle
from .service import (
    BodyConfigurationRecord,
    BodyPageSource,
    ControlledBodyExecution,
    ControlledBodyService,
    bind_native_body,
    bind_ocr_body,
)

__all__ = (
    "AnnotationCandidate",
    "BodyConfiguration",
    "BodyConfigurationRecord",
    "BodyPageSource",
    "BoundPageBody",
    "CanonicalBody",
    "ControlledBodyExecution",
    "ControlledBodyService",
    "F0FError",
    "LocalFixtureKey",
    "OcrBlock",
    "OcrBodyResult",
    "PageBodyMetadata",
    "RuntimeBundle",
    "create_keyfile",
    "bind_native_body",
    "bind_ocr_body",
    "extract_native_page",
    "load_keyfile",
    "load_runtime_bundle",
    "select_annotation_candidates",
)
