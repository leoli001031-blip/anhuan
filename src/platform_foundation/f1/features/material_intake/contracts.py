"""Public and internal contracts for assisted material intake.

Confidence values are deliberately named ``*_ppm``.  They are deterministic,
uncalibrated hints in the inclusive range 0..1_000_000, not measured accuracy.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from ..p3.contracts import KnowledgeScopeOut


MATERIAL_ANALYSIS_VERSION = "material-v1"
MATERIAL_PARSER_BACKEND = "pypdf_heuristic"
MATERIAL_INTAKE_BOUNDARIES = (
    "MACHINE_CANDIDATE_ONLY",
    "CONFIDENCE_UNCALIBRATED",
    "HUMAN_CONFIRMATION_REQUIRED",
    "OCR_ROUTING_ONLY",
    "PDF_INSPECTOR_RUNTIME_DISABLED",
    "NOT_LEGAL_ADVICE",
    "NOT_PRODUCTION",
)

MAX_EVIDENCE_CHARACTERS = 240
MAX_CANDIDATE_VALUE_CHARACTERS = 4_000
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

AnalysisStatus = Literal["ready", "failed", "confirmed"]
PageKind = Literal["text", "scanned", "mixed", "unknown"]
MaterialKind = Literal["policy", "report", "unknown"]
ClassificationSource = Literal[
    "upload_selection", "machine_pending", "human_review"
]
CandidateProducer = Literal["pypdf_heuristic", "pdf_inspector_shadow"]
MaterialAllowedAction = Literal[
    "set_material_kind",
    "confirm_policy_draft",
    "view_policy_source",
    "view_policy_version",
]
PolicySourceType = Literal[
    "law", "regulation", "standard", "guidance", "internal"
]
PolicyDomain = Literal[
    "safety", "health", "environment", "fire", "chemical", "general"
]
EffectStatus = Literal["unknown", "not_effective", "effective", "expired"]

MATERIAL_FIELD_NAMES = frozenset(
    {
        "source_title",
        "publisher",
        "source_type",
        "jurisdiction",
        "source_reference",
        "version_title",
        "domain",
        "effect_status",
        "issued_on",
        "effective_from",
        "effective_to",
        "summary",
        "report_title",
        "report_date",
        "report_summary",
    }
)


@dataclass(frozen=True, slots=True)
class PageClassification:
    page_number: int
    primary_kind: PageKind
    ocr_required: bool
    table_candidate: bool
    two_column_candidate: bool
    text_character_count: int
    text_confidence_ppm: int
    scan_confidence_ppm: int
    table_confidence_ppm: int
    two_column_confidence_ppm: int
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FieldCandidate:
    field_name: str
    candidate_value: str
    page_number: int
    evidence_snippet: str
    confidence_ppm: int
    confidence_basis: str
    calibrated: bool = False
    producer: CandidateProducer = "pypdf_heuristic"

    def __post_init__(self) -> None:
        if self.field_name not in MATERIAL_FIELD_NAMES:
            raise ValueError("MATERIAL_FIELD_NAME_INVALID")
        if not self.candidate_value or len(self.candidate_value) > MAX_CANDIDATE_VALUE_CHARACTERS:
            raise ValueError("MATERIAL_CANDIDATE_VALUE_INVALID")
        if not self.evidence_snippet or len(self.evidence_snippet) > MAX_EVIDENCE_CHARACTERS:
            raise ValueError("MATERIAL_CANDIDATE_EVIDENCE_INVALID")
        if _CONTROL_RE.search(self.candidate_value) or _CONTROL_RE.search(self.evidence_snippet):
            raise ValueError("MATERIAL_CANDIDATE_VALUE_INVALID")
        if not re.fullmatch(r"[a-z0-9_.-]{1,80}", self.confidence_basis):
            raise ValueError("MATERIAL_CONFIDENCE_BASIS_INVALID")
        if self.calibrated:
            raise ValueError("MATERIAL_CONFIDENCE_MUST_BE_UNCALIBRATED")
        if self.page_number < 1 or not 0 <= self.confidence_ppm <= 1_000_000:
            raise ValueError("MATERIAL_CANDIDATE_CONFIDENCE_INVALID")


@dataclass(frozen=True, slots=True)
class MaterialAnalysisResult:
    document_profile: str
    pages: tuple[PageClassification, ...]
    candidates: tuple[FieldCandidate, ...]
    suggested_kind: MaterialKind
    suggested_kind_confidence_ppm: int
    analysis_version: str = MATERIAL_ANALYSIS_VERSION
    parser_backend: str = MATERIAL_PARSER_BACKEND

    def __post_init__(self) -> None:
        if not 0 <= self.suggested_kind_confidence_ppm <= 1_000_000:
            raise ValueError("MATERIAL_KIND_CONFIDENCE_INVALID")


class MaterialPageOut(BaseModel):
    page_number: int = Field(ge=1, le=128)
    primary_kind: PageKind
    ocr_required: bool
    table_candidate: bool
    two_column_candidate: bool
    text_character_count: int = Field(ge=0, le=100_000)
    text_confidence_ppm: int = Field(ge=0, le=1_000_000)
    scan_confidence_ppm: int = Field(ge=0, le=1_000_000)
    table_confidence_ppm: int = Field(ge=0, le=1_000_000)
    two_column_confidence_ppm: int = Field(ge=0, le=1_000_000)
    reason_codes: list[str] = Field(default_factory=list)


class MaterialCandidateOut(BaseModel):
    id: uuid.UUID
    field_name: str
    candidate_value: str
    page_number: int = Field(ge=1, le=128)
    evidence_snippet: str
    confidence_ppm: int = Field(ge=0, le=1_000_000)
    confidence_basis: str = Field(pattern=r"^[a-z0-9_.-]{1,80}$")
    calibrated: Literal[False] = False
    producer: CandidateProducer


class MaterialAnalysisOut(BaseModel):
    id: uuid.UUID
    document_version_id: uuid.UUID
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    analysis_version: str
    parser_backend: str
    document_profile: str
    status: AnalysisStatus
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{1,80}$")
    shadow_status: Literal["disabled", "unavailable", "ready", "failed"] = "disabled"
    suggested_kind: MaterialKind
    suggested_kind_confidence_ppm: int = Field(ge=0, le=1_000_000)
    resolved_kind: MaterialKind
    classification_source: ClassificationSource
    classification_by_user_id: uuid.UUID | None = None
    classification_at: datetime | None = None
    knowledge_scope: KnowledgeScopeOut
    page_count: int = Field(ge=1, le=128)
    candidate_count: int = Field(ge=0, le=100)
    pages: list[MaterialPageOut] = Field(default_factory=list)
    candidates: list[MaterialCandidateOut] = Field(default_factory=list)
    policy_source_id: uuid.UUID | None = None
    policy_version_id: uuid.UUID | None = None
    confirmed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[MaterialAllowedAction] = Field(default_factory=list)
    boundaries: list[str] = Field(default_factory=lambda: list(MATERIAL_INTAKE_BOUNDARIES))


class ConfirmPolicySourceIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    publisher: str = Field(min_length=1, max_length=200)
    source_type: PolicySourceType
    jurisdiction: str = Field(min_length=1, max_length=120)
    source_reference: str = Field(min_length=1, max_length=500)


class ConfirmPolicyVersionIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    domain: PolicyDomain
    effect_status: EffectStatus = "unknown"
    issued_on: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    summary: str = Field(min_length=1, max_length=4_000)


class ConfirmPolicyDraftIn(BaseModel):
    source: ConfirmPolicySourceIn
    version: ConfirmPolicyVersionIn


class SetMaterialKindIn(BaseModel):
    material_kind: MaterialKind


def material_allowed_actions(
    *,
    role: str | None,
    status: str,
    document_released: bool,
    source_id: object | None,
    version_id: object | None,
    resolved_kind: str,
    classification_source: str,
    classification_by_user_id: object | None,
    classification_at: object | None,
    knowledge_scope_kind: str,
) -> list[MaterialAllowedAction]:
    if status == "confirmed" and source_id is not None and version_id is not None:
        return ["view_policy_source", "view_policy_version"]
    actions: list[MaterialAllowedAction] = []
    if (
        status == "ready"
        and source_id is None
        and version_id is None
        and role in {"super_admin", "enterprise_admin", "plant_admin"}
    ):
        actions.append("set_material_kind")
    if (
        status == "ready"
        and document_released
        and role in {"super_admin", "enterprise_admin"}
        and resolved_kind == "policy"
        and classification_source in {"upload_selection", "human_review"}
        and classification_by_user_id is not None
        and classification_at is not None
        and knowledge_scope_kind == "service_provider"
    ):
        actions.append("confirm_policy_draft")
    return actions


__all__ = (
    "ConfirmPolicyDraftIn",
    "ClassificationSource",
    "EffectStatus",
    "FieldCandidate",
    "MATERIAL_ANALYSIS_VERSION",
    "MATERIAL_FIELD_NAMES",
    "MATERIAL_INTAKE_BOUNDARIES",
    "MATERIAL_PARSER_BACKEND",
    "MaterialKind",
    "MaterialAnalysisOut",
    "MaterialAnalysisResult",
    "MaterialCandidateOut",
    "MaterialPageOut",
    "PageClassification",
    "SetMaterialKindIn",
    "material_allowed_actions",
)
