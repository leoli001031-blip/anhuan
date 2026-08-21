"""Frozen analysis-report value contracts. Field names match API v1."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

SCHEMA_SESSION = "anhuan-analysis-report-session-v1"
SCHEMA_PUBLISHED_LIST = "anhuan-analysis-report-published-list-v1"
SCHEMA_PUBLISHED_DETAIL = "anhuan-analysis-report-published-detail-v1"
SCHEMA_GENERATION = "anhuan-analysis-report-generation-v1"
SCHEMA_JOB = "anhuan-analysis-report-job-v1"
SCHEMA_HISTORY = "anhuan-analysis-report-version-history-v1"
SCHEMA_PROVIDER_LIST = "anhuan-analysis-report-provider-list-v1"
SCHEMA_DRAFT = "anhuan-analysis-report-draft-v1"

TEMPLATE_ID = "enterprise-ehs-material-analysis-v1"
TEMPLATE_TITLE = "企业安环资料分析报告"

SECTION_KEYS = (
    "source_scope",
    "status_summary",
    "key_findings",
    "risks_and_gaps",
    "remediation",
    "citations",
    "usage_boundary",
)
SECTION_TITLES = {
    "source_scope": "资料范围",
    "status_summary": "现状摘要",
    "key_findings": "主要发现",
    "risks_and_gaps": "风险与缺口",
    "remediation": "整改建议",
    "citations": "引用证据",
    "usage_boundary": "使用边界",
}

ProductRole = Literal["provider_admin", "client_user"]
VersionStatus = Literal[
    "queued",
    "generating",
    "draft",
    "review_pending",
    "changes_requested",
    "approved",
    "published",
    "superseded",
    "withdrawn",
    "failed",
]
JobStatus = Literal["queued", "generating", "draft", "failed"]

PROVIDER_CAPABILITIES = (
    "list_client_reports",
    "create_report",
    "generate",
    "review",
    "publish",
    "withdraw",
)
CLIENT_CAPABILITIES = ("list_published", "read_published")
FORBIDDEN_CLIENT_IDENTITY_KEYS = frozenset(
    ("client_account_id", "tenant_id", "enterprise_id", "knowledge_scope_id")
)
FORBIDDEN_RESPONSE_KEYS = frozenset(
    (
        "dataset_id",
        "chunk_id",
        "knowledge_scope_id",
        "lease_token",
        "object_key",
        "ragflow_dataset_id",
        "ragflow_document_id",
        "binding_id",
        "audience_enterprise_id",
        "provider_enterprise_id",
    )
)

LOCAL_FLAG = "F1_MATERIAL_ANALYSIS_REPORT_LOCAL"
ENGINEERING_FLAG = "F1_LOCAL_ENGINEERING"

PROVIDER_MEMBER_ROLES = frozenset(("super_admin", "enterprise_admin"))


class ReportNotFound(Exception):
    pass


class RequestIdConflict(Exception):
    pass


class ReportTransitionInvalid(Exception):
    pass


class GenerationDisabled(Exception):
    pass


class GenerationFailed(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class EligibleSource:
    document_version_id: uuid.UUID
    document_name: str
    version_number: int
    source_sha256: str
    scope_kind: str
    page_number: int


@dataclass(frozen=True, slots=True)
class FrozenSourceSet:
    enterprise_id: uuid.UUID
    client_account_id: uuid.UUID
    template_id: str
    fingerprint_sha256: str
    sources: tuple[EligibleSource, ...]


@dataclass(frozen=True, slots=True)
class GeneratedSection:
    key: str
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class GeneratedCitation:
    document_version_id: uuid.UUID
    document_name: str
    version_number: int
    page_number: int
    excerpt: str


@dataclass(frozen=True, slots=True)
class GeneratedReport:
    sections: tuple[GeneratedSection, ...]
    citations: tuple[GeneratedCitation, ...]


class ReportGeneratorPort(Protocol):
    def generate(self, frozen: FrozenSourceSet) -> GeneratedReport:
        """Return structured JSON or raise GenerationFailed. Never returns stale."""


__all__ = (
    "SCHEMA_SESSION",
    "SCHEMA_PUBLISHED_LIST",
    "SCHEMA_PUBLISHED_DETAIL",
    "SCHEMA_GENERATION",
    "SCHEMA_JOB",
    "SCHEMA_HISTORY",
    "SCHEMA_PROVIDER_LIST",
    "SCHEMA_DRAFT",
    "TEMPLATE_ID",
    "TEMPLATE_TITLE",
    "SECTION_KEYS",
    "SECTION_TITLES",
    "ProductRole",
    "VersionStatus",
    "JobStatus",
    "PROVIDER_CAPABILITIES",
    "CLIENT_CAPABILITIES",
    "FORBIDDEN_CLIENT_IDENTITY_KEYS",
    "FORBIDDEN_RESPONSE_KEYS",
    "LOCAL_FLAG",
    "ENGINEERING_FLAG",
    "PROVIDER_MEMBER_ROLES",
    "ReportNotFound",
    "RequestIdConflict",
    "ReportTransitionInvalid",
    "GenerationDisabled",
    "GenerationFailed",
    "EligibleSource",
    "FrozenSourceSet",
    "GeneratedSection",
    "GeneratedCitation",
    "GeneratedReport",
    "ReportGeneratorPort",
)
