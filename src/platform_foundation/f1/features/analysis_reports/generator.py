"""Deterministic fake generator. No Ark, keys, or network."""
from __future__ import annotations

import hashlib
import uuid

from .contracts import (
    FrozenSourceSet,
    GeneratedCitation,
    GeneratedReport,
    GeneratedSection,
    GenerationFailed,
    SECTION_KEYS,
    SECTION_TITLES,
    TEMPLATE_TITLE,
)

_EXCERPT = "企业应当建立安全生产责任制。"
_NAMESPACE = uuid.UUID("7c2e1a90-9f3d-4c1b-8a6e-0123456789ab")


class FakeDeterministicReportGenerator:
    def generate(self, frozen: FrozenSourceSet) -> GeneratedReport:
        if frozen.template_id != "enterprise-ehs-material-analysis-v1":
            raise GenerationFailed("REPORT_TEMPLATE_INVALID")
        if not frozen.sources:
            raise GenerationFailed("REPORT_SOURCES_EMPTY")
        kinds = {source.scope_kind for source in frozen.sources}
        if "service_provider" not in kinds:
            raise GenerationFailed("REPORT_PROVIDER_SOURCES_MISSING")
        if "client" not in kinds:
            raise GenerationFailed("REPORT_CLIENT_SOURCES_EMPTY")
        citations: list[GeneratedCitation] = []
        for source in frozen.sources:
            if source.page_number < 1:
                raise GenerationFailed("REPORT_CITATION_PAGE_INVALID")
            if len(source.source_sha256) != 64:
                raise GenerationFailed("REPORT_SOURCE_HASH_INVALID")
            citations.append(
                GeneratedCitation(
                    document_version_id=source.document_version_id,
                    document_name=source.document_name,
                    version_number=source.version_number,
                    page_number=source.page_number,
                    excerpt=_EXCERPT,
                )
            )
        if not citations:
            raise GenerationFailed("REPORT_CITATION_MISSING")
        provider_n = sum(1 for s in frozen.sources if s.scope_kind == "service_provider")
        client_n = sum(1 for s in frozen.sources if s.scope_kind == "client")
        digest = frozen.fingerprint_sha256[:12]
        bodies = {
            "source_scope": f"已纳入服务方共享资料 {provider_n} 份与本企业已发布资料 {client_n} 份。",
            "status_summary": f"企业已建立基础安环制度，现场执行记录不完整（{digest}）。",
            "key_findings": "危化品台账与现场标识存在不一致。",
            "risks_and_gaps": "应急预案未覆盖夜班值班场景。",
            "remediation": "30 日内完成台账核对并补齐应急预案附录。",
            "citations": "结论均绑定已发布文档版本页码，见 citations 列表。",
            "usage_boundary": "本报告由本地确定性夹具生成，不得作为执法或生产放行依据。",
        }
        sections = tuple(
            GeneratedSection(key=key, title=SECTION_TITLES[key], body=bodies[key])
            for key in SECTION_KEYS
        )
        if len(sections) != 7:
            raise GenerationFailed("REPORT_SCHEMA_INVALID")
        _ = TEMPLATE_TITLE
        _ = hashlib.sha256(digest.encode("utf-8")).hexdigest()
        _ = uuid.uuid5(_NAMESPACE, frozen.fingerprint_sha256)
        return GeneratedReport(sections=sections, citations=tuple(citations))
