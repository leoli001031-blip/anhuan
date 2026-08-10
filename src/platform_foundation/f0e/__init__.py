"""F0-E local candidate-evidence execution package."""

from .contracts import (
    DeferredDocumentRoute,
    F0EError,
    NormalizedTextEvidence,
    OcrPageEvidence,
    OcrRunEnvelope,
    PageRoute,
    ProcessingUnitRecord,
    ResourceLimits,
    SandboxProfile,
)
from .replay import (
    ReplayAggregate,
    aggregate_replay,
    assemble_run_envelope,
    verify_replay,
)
from .routing import (
    build_deferred_route,
    build_page_routes,
    native_reference_evidence,
)
from .service import LocalOcrExecution, LocalOcrService
from .supervisor import FixedArgvSandboxSupervisor, SandboxSupervisor, docker_argv
from .vault_adapter import VerifiedSourceFd, open_verified_source


__all__ = (
    "DeferredDocumentRoute",
    "F0EError",
    "FixedArgvSandboxSupervisor",
    "LocalOcrExecution",
    "LocalOcrService",
    "NormalizedTextEvidence",
    "OcrPageEvidence",
    "OcrRunEnvelope",
    "PageRoute",
    "ProcessingUnitRecord",
    "ReplayAggregate",
    "ResourceLimits",
    "SandboxProfile",
    "SandboxSupervisor",
    "VerifiedSourceFd",
    "aggregate_replay",
    "assemble_run_envelope",
    "build_deferred_route",
    "build_page_routes",
    "docker_argv",
    "native_reference_evidence",
    "open_verified_source",
    "verify_replay",
)
