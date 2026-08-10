"""Fail-closed governance contracts for the local fixture foundation.

This module intentionally contains no approval or mutation API.  It models the
only readiness state that F0-D is allowed to expose: registered local fixtures
may enter the local foundation, while real-customer use, Acceptance Gold,
external processing, professional publication, UAT, and production stay
closed.

The snapshot and denial errors contain only fixed vocabulary.  Caller-provided
content, filenames, and paths are never incorporated into messages or exported
data.
"""

from __future__ import annotations

from enum import Enum
from typing import Final, final


class DataScope(str, Enum):
    """The only data scope authorized in F0-D."""

    LOCAL_FIXTURE = "LOCAL_FIXTURE"


class PilotContextState(str, Enum):
    """Real customer, facility, region, and industry context readiness."""

    UNCONFIRMED = "UNCONFIRMED"


class RegionIndustryState(str, Enum):
    """Whether region and industry applicability has been confirmed."""

    UNCONFIRMED = "UNCONFIRMED"


class BenchmarkTier(str, Enum):
    """Benchmark status.  Fixture evidence is not Gold."""

    NONE = "NONE"


class ExternalProcessingPolicy(str, Enum):
    """External OCR, LLM, embedding, notification, and similar processing."""

    DENY = "DENY"


class ProfessionalAuthority(str, Enum):
    """Authority to publish professional environmental conclusions."""

    UNASSIGNED = "UNASSIGNED"


class UploadIntent(str, Enum):
    """Upload intents recognized by the closed foundation."""

    REGISTERED_LOCAL_FIXTURE = "REGISTERED_LOCAL_FIXTURE"
    REAL_CUSTOMER = "REAL_CUSTOMER"


class GovernanceOperation(str, Enum):
    """Fixed operation vocabulary used in safe denial responses."""

    FIXTURE_UPLOAD = "FIXTURE_UPLOAD"
    REAL_CUSTOMER_UPLOAD = "REAL_CUSTOMER_UPLOAD"
    ACCEPTANCE_GOLD_PROMOTION = "ACCEPTANCE_GOLD_PROMOTION"
    EXTERNAL_PROCESSING = "EXTERNAL_PROCESSING"
    PROFESSIONAL_PUBLICATION = "PROFESSIONAL_PUBLICATION"
    UAT_ENTRY = "UAT_ENTRY"
    PRODUCTION_ENTRY = "PRODUCTION_ENTRY"


class GovernanceReasonCode(str, Enum):
    """Stable, machine-readable governance denial reasons."""

    FIXTURE_REGISTRATION_REQUIRED = "GOV_FIXTURE_REGISTRATION_REQUIRED"
    REAL_CUSTOMER_CONTEXT_UNCONFIRMED = (
        "GOV_REAL_CUSTOMER_CONTEXT_UNCONFIRMED"
    )
    REGION_INDUSTRY_UNCONFIRMED = "GOV_REGION_INDUSTRY_UNCONFIRMED"
    ACCEPTANCE_GOLD_UNAVAILABLE = "GOV_ACCEPTANCE_GOLD_UNAVAILABLE"
    EXTERNAL_PROCESSING_DENIED = "GOV_EXTERNAL_PROCESSING_DENIED"
    PROFESSIONAL_AUTHORITY_UNASSIGNED = (
        "GOV_PROFESSIONAL_AUTHORITY_UNASSIGNED"
    )
    UAT_NOT_AUTHORIZED = "GOV_UAT_NOT_AUTHORIZED"
    PRODUCTION_NOT_AUTHORIZED = "GOV_PRODUCTION_NOT_AUTHORIZED"


_SAFE_MESSAGES: Final[dict[GovernanceReasonCode, str]] = {
    GovernanceReasonCode.FIXTURE_REGISTRATION_REQUIRED: (
        "a registered local fixture identity is required"
    ),
    GovernanceReasonCode.REAL_CUSTOMER_CONTEXT_UNCONFIRMED: (
        "real customer context is not authorized"
    ),
    GovernanceReasonCode.REGION_INDUSTRY_UNCONFIRMED: (
        "region and industry context is not confirmed"
    ),
    GovernanceReasonCode.ACCEPTANCE_GOLD_UNAVAILABLE: (
        "Acceptance Gold promotion is not authorized"
    ),
    GovernanceReasonCode.EXTERNAL_PROCESSING_DENIED: (
        "external processing is denied"
    ),
    GovernanceReasonCode.PROFESSIONAL_AUTHORITY_UNASSIGNED: (
        "professional publication authority is unassigned"
    ),
    GovernanceReasonCode.UAT_NOT_AUTHORIZED: "UAT is not authorized",
    GovernanceReasonCode.PRODUCTION_NOT_AUTHORIZED: (
        "production use is not authorized"
    ),
}


@final
class GovernanceDenied(PermissionError):
    """A sanitized, stable fail-closed governance error."""

    __slots__ = ("operation", "reason_code")

    def __init__(
        self,
        operation: GovernanceOperation,
        reason_code: GovernanceReasonCode,
    ) -> None:
        self.operation = operation
        self.reason_code = reason_code
        super().__init__(
            f"{operation.value} denied: {reason_code.value}: "
            f"{_SAFE_MESSAGES[reason_code]}"
        )

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-serializable error without caller-provided values."""

        return {
            "error": "GOVERNANCE_DENIED",
            "operation": self.operation.value,
            "reason_code": self.reason_code.value,
            "message": _SAFE_MESSAGES[self.reason_code],
        }


@final
class ClosedReadinessSnapshot:
    """Immutable-by-construction F0-D readiness snapshot.

    The class has no instance attributes and accepts no constructor arguments,
    so callers cannot create a variant with an open gate.  ``to_dict`` returns
    a fresh serialization copy; mutating that copy never changes the snapshot.
    """

    __slots__ = ()

    @property
    def data_scope(self) -> DataScope:
        return DataScope.LOCAL_FIXTURE

    @property
    def pilot_context(self) -> PilotContextState:
        return PilotContextState.UNCONFIRMED

    @property
    def region_industry(self) -> RegionIndustryState:
        return RegionIndustryState.UNCONFIRMED

    @property
    def benchmark_tier(self) -> BenchmarkTier:
        return BenchmarkTier.NONE

    @property
    def external_processing_policy(self) -> ExternalProcessingPolicy:
        return ExternalProcessingPolicy.DENY

    @property
    def professional_authority(self) -> ProfessionalAuthority:
        return ProfessionalAuthority.UNASSIGNED

    @property
    def uat_allowed(self) -> bool:
        return False

    @property
    def production_allowed(self) -> bool:
        return False

    @property
    def blockers(self) -> tuple[GovernanceReasonCode, ...]:
        return (
            GovernanceReasonCode.REAL_CUSTOMER_CONTEXT_UNCONFIRMED,
            GovernanceReasonCode.REGION_INDUSTRY_UNCONFIRMED,
            GovernanceReasonCode.ACCEPTANCE_GOLD_UNAVAILABLE,
            GovernanceReasonCode.EXTERNAL_PROCESSING_DENIED,
            GovernanceReasonCode.PROFESSIONAL_AUTHORITY_UNASSIGNED,
            GovernanceReasonCode.UAT_NOT_AUTHORIZED,
            GovernanceReasonCode.PRODUCTION_NOT_AUTHORIZED,
        )

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-serializable readiness representation."""

        return {
            "schema": "platform-foundation-readiness-v1",
            "data_scope": self.data_scope.value,
            "pilot_context": self.pilot_context.value,
            "region_industry": self.region_industry.value,
            "benchmark_tier": self.benchmark_tier.value,
            "external_processing_policy": (
                self.external_processing_policy.value
            ),
            "professional_authority": self.professional_authority.value,
            "uat_allowed": self.uat_allowed,
            "production_allowed": self.production_allowed,
            "blockers": [code.value for code in self.blockers],
        }


CLOSED_READINESS: Final[ClosedReadinessSnapshot] = ClosedReadinessSnapshot()


def closed_readiness_snapshot() -> ClosedReadinessSnapshot:
    """Return the process-independent closed readiness singleton."""

    return CLOSED_READINESS


def require_registered_fixture_upload(intent: UploadIntent) -> None:
    """Allow only the already-verified registered-fixture ingestion path.

    The caller must pass the enum member itself.  Raw strings and all other
    values fail closed, preventing request data from being treated as trusted
    registration evidence.  The fixture verifier remains responsible for
    establishing the registered identity before invoking this guard.
    """

    if intent is UploadIntent.REGISTERED_LOCAL_FIXTURE:
        return
    if intent is UploadIntent.REAL_CUSTOMER:
        raise GovernanceDenied(
            GovernanceOperation.REAL_CUSTOMER_UPLOAD,
            GovernanceReasonCode.REAL_CUSTOMER_CONTEXT_UNCONFIRMED,
        )
    raise GovernanceDenied(
        GovernanceOperation.FIXTURE_UPLOAD,
        GovernanceReasonCode.FIXTURE_REGISTRATION_REQUIRED,
    )


def require_real_customer_upload() -> None:
    """Reject every real-customer upload while pilot context is unconfirmed."""

    raise GovernanceDenied(
        GovernanceOperation.REAL_CUSTOMER_UPLOAD,
        GovernanceReasonCode.REAL_CUSTOMER_CONTEXT_UNCONFIRMED,
    )


def require_acceptance_gold_promotion() -> None:
    """Reject promotion of Fixture evidence to Acceptance Gold."""

    raise GovernanceDenied(
        GovernanceOperation.ACCEPTANCE_GOLD_PROMOTION,
        GovernanceReasonCode.ACCEPTANCE_GOLD_UNAVAILABLE,
    )


def require_external_processing() -> None:
    """Reject cloud OCR, LLM, embedding, and every other external provider."""

    raise GovernanceDenied(
        GovernanceOperation.EXTERNAL_PROCESSING,
        GovernanceReasonCode.EXTERNAL_PROCESSING_DENIED,
    )


def require_professional_publication() -> None:
    """Reject publication of a professional environmental conclusion."""

    raise GovernanceDenied(
        GovernanceOperation.PROFESSIONAL_PUBLICATION,
        GovernanceReasonCode.PROFESSIONAL_AUTHORITY_UNASSIGNED,
    )


def require_uat_entry() -> None:
    """Reject use of the local Fixture foundation as customer UAT."""

    raise GovernanceDenied(
        GovernanceOperation.UAT_ENTRY,
        GovernanceReasonCode.UAT_NOT_AUTHORIZED,
    )


def require_production_entry() -> None:
    """Reject use of the local Fixture foundation in production."""

    raise GovernanceDenied(
        GovernanceOperation.PRODUCTION_ENTRY,
        GovernanceReasonCode.PRODUCTION_NOT_AUTHORIZED,
    )


__all__ = (
    "BenchmarkTier",
    "CLOSED_READINESS",
    "ClosedReadinessSnapshot",
    "DataScope",
    "ExternalProcessingPolicy",
    "GovernanceDenied",
    "GovernanceOperation",
    "GovernanceReasonCode",
    "PilotContextState",
    "ProfessionalAuthority",
    "RegionIndustryState",
    "UploadIntent",
    "closed_readiness_snapshot",
    "require_acceptance_gold_promotion",
    "require_external_processing",
    "require_production_entry",
    "require_professional_publication",
    "require_real_customer_upload",
    "require_registered_fixture_upload",
    "require_uat_entry",
)
