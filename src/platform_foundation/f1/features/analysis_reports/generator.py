"""Local extractive report generation from frozen released material evidence.

The generator is deliberately deterministic and has no model, key, or network
dependency. It only summarizes text carried by the frozen source set; it does
not invent a compliance conclusion when the material is silent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .contracts import (
    EligibleSource,
    FrozenSourceSet,
    GeneratedCitation,
    GeneratedReport,
    GeneratedSection,
    GenerationFailed,
    SECTION_KEYS,
    SECTION_TITLES,
    TEMPLATE_ID,
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SPACE = re.compile(r"\s+")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？!?;；])\s*|[\r\n]+")
_MAX_EXCERPT_CHARS = 220
_MAX_CITATIONS = 16

# Terms classify source text; they never create facts absent from an excerpt.
_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("证照与批复", ("许可证", "证照", "批复", "环评", "验收")),
    ("监测与台账", ("监测", "检测", "台账", "记录", "运行日志")),
    ("应急管理", ("应急", "预案", "演练", "事故", "值班")),
    ("隐患与整改", ("隐患", "整改", "闭环", "排查", "缺项")),
    ("危化品管理", ("危化品", "化学品", "存储", "库存", "标识")),
    ("污染防治", ("废水", "废气", "固废", "危废", "排放", "污染")),
    ("责任与培训", ("责任制", "责任人", "培训", "考核", "制度")),
)
_RISK_TERMS = (
    "未完成",
    "未覆盖",
    "未更新",
    "未取得",
    "未闭环",
    "未签字",
    "不一致",
    "不完整",
    "不符合",
    "缺失",
    "缺少",
    "缺项",
    "异常",
    "超标",
    "存在隐患",
    "发现隐患",
    "重大隐患",
    "即将到期",
    "已过期",
    "待整改",
    "待补充",
    "待核实",
)


@dataclass(frozen=True, slots=True)
class _EvidenceSentence:
    source: EligibleSource
    page_number: int
    ordinal: int
    text: str
    topics: tuple[str, ...]
    risk_signal: bool


def _clean_excerpt(value: str) -> str:
    """Return a bounded textual excerpt without adding source claims."""
    cleaned = _SPACE.sub(" ", value).strip()
    if len(cleaned) <= _MAX_EXCERPT_CHARS:
        return cleaned
    return cleaned[: _MAX_EXCERPT_CHARS - 1].rstrip() + "…"


def _sentence_parts(value: str) -> Iterable[str]:
    for raw in _SENTENCE_BOUNDARY.split(value):
        excerpt = _clean_excerpt(raw)
        if len(excerpt) >= 4 and any(character.isalnum() for character in excerpt):
            yield excerpt


def _classify(text: str) -> tuple[tuple[str, ...], bool]:
    topics = tuple(
        label for label, terms in _TOPICS if any(term in text for term in terms)
    )
    return topics, any(term in text for term in _RISK_TERMS)


def _evidence_sentences(frozen: FrozenSourceSet) -> list[_EvidenceSentence]:
    sentences: list[_EvidenceSentence] = []
    for source in frozen.sources:
        if source.page_number < 1:
            raise GenerationFailed("REPORT_CITATION_PAGE_INVALID")
        if not _HEX64.fullmatch(source.source_sha256):
            raise GenerationFailed("REPORT_SOURCE_HASH_INVALID")

        source_sentences: list[_EvidenceSentence] = []
        evidence_units = getattr(source, "evidence_units", ())
        for unit in sorted(
            evidence_units,
            key=lambda item: (int(item.page_number), int(item.ordinal)),
        ):
            if unit.page_number < 1 or unit.ordinal < 1:
                raise GenerationFailed("REPORT_EVIDENCE_POSITION_INVALID")
            if not _HEX64.fullmatch(unit.body_sha256):
                raise GenerationFailed("REPORT_EVIDENCE_HASH_INVALID")
            if not isinstance(unit.text, str):
                raise GenerationFailed("REPORT_SOURCE_EVIDENCE_INVALID")
            for part in _sentence_parts(unit.text):
                topics, risk_signal = _classify(part)
                source_sentences.append(
                    _EvidenceSentence(
                        source=source,
                        page_number=unit.page_number,
                        ordinal=unit.ordinal,
                        text=part,
                        topics=topics,
                        risk_signal=risk_signal,
                    )
                )
        if not source_sentences:
            raise GenerationFailed("REPORT_SOURCE_EVIDENCE_MISSING")
        sentences.extend(source_sentences)
    return sentences


def _source_order(sentences: list[_EvidenceSentence]) -> list[EligibleSource]:
    sources: list[EligibleSource] = []
    seen: set[object] = set()
    for sentence in sentences:
        source_id = sentence.source.document_version_id
        if source_id not in seen:
            sources.append(sentence.source)
            seen.add(source_id)
    return sources


def _select_citations(
    sentences: list[_EvidenceSentence],
) -> list[_EvidenceSentence]:
    """Choose diverse real excerpts while preserving deterministic ordering."""
    selected: list[_EvidenceSentence] = []
    selected_keys: set[tuple[object, int, str]] = set()
    sources = _source_order(sentences)
    citation_limit = len(sources) + _MAX_CITATIONS

    def add(candidate: _EvidenceSentence) -> None:
        source_id = candidate.source.document_version_id
        key = (source_id, candidate.page_number, candidate.text)
        if key not in selected_keys and len(selected) < citation_limit:
            selected.append(candidate)
            selected_keys.add(key)

    # Every frozen document contributes at least one real excerpt.
    for source in sources:
        add(next(item for item in sentences if item.source is source))
    # Risk-bearing and domain-bearing excerpts are the most useful evidence.
    for candidate in sentences:
        if candidate.risk_signal:
            add(candidate)
    for candidate in sentences:
        if candidate.topics:
            add(candidate)
    for candidate in sentences:
        add(candidate)
    return selected


def _citation_number(
    selected: list[_EvidenceSentence], candidate: _EvidenceSentence
) -> int:
    source_id = candidate.source.document_version_id
    for index, item in enumerate(selected, start=1):
        if (
            item.source.document_version_id == source_id
            and item.page_number == candidate.page_number
            and item.text == candidate.text
        ):
            return index
    raise GenerationFailed("REPORT_CITATION_LINK_MISSING")


def _findings_body(selected: list[_EvidenceSentence]) -> str:
    topic_items = [item for item in selected if item.topics]
    chosen = topic_items[:5] or selected[:3]
    return "\n".join(
        f"- [证据{_citation_number(selected, item)}] {item.text}" for item in chosen
    )


def _risks_body(selected: list[_EvidenceSentence]) -> str:
    risks = [item for item in selected if item.risk_signal]
    if not risks:
        return (
            "证据不足：已冻结材料中未检出明确的风险或缺口表述。"
            "这不代表已证实无风险，仍需结合现场与待补材料核实。"
        )
    return "\n".join(
        ["以下仅是触发进一步核实的材料原文，不等于已确认的现场风险："]
        + [
            f"- [证据{_citation_number(selected, item)}] {item.text}"
            for item in risks[:5]
        ]
    )


def _remediation_body(selected: list[_EvidenceSentence]) -> str:
    risks = [item for item in selected if item.risk_signal]
    if not risks:
        return (
            "当前证据不足以支持具体整改结论。建议先补充未覆盖的原始记录，"
            "并由负责人结合现场状况进行人工核实。"
        )
    lines = []
    for item in risks[:3]:
        number = _citation_number(selected, item)
        topic = item.topics[0] if item.topics else "该事项"
        lines.append(
            f"- 核实【{topic}】原文所述状态（见证据{number}）；"
            "若确认尚未闭环，再补充责任人、期限和完成佐证。"
        )
    return "\n".join(lines)


class EvidenceDrivenReportGenerator:
    """Generate an extractive report from immutable released-unit text."""

    def generate(self, frozen: FrozenSourceSet) -> GeneratedReport:
        if frozen.template_id != TEMPLATE_ID:
            raise GenerationFailed("REPORT_TEMPLATE_INVALID")
        if not _HEX64.fullmatch(frozen.fingerprint_sha256):
            raise GenerationFailed("REPORT_FINGERPRINT_INVALID")
        if not frozen.sources:
            raise GenerationFailed("REPORT_SOURCES_EMPTY")
        kinds = {source.scope_kind for source in frozen.sources}
        if "service_provider" not in kinds:
            raise GenerationFailed("REPORT_PROVIDER_SOURCES_MISSING")
        if "client" not in kinds:
            raise GenerationFailed("REPORT_CLIENT_SOURCES_EMPTY")

        sentences = _evidence_sentences(frozen)
        selected = _select_citations(sentences)
        if not selected:
            raise GenerationFailed("REPORT_CITATION_MISSING")

        citations = tuple(
            GeneratedCitation(
                document_version_id=item.source.document_version_id,
                document_name=item.source.document_name,
                version_number=item.source.version_number,
                page_number=item.page_number,
                excerpt=item.text,
            )
            for item in selected
        )
        provider_sources = [s for s in frozen.sources if s.scope_kind == "service_provider"]
        client_sources = [s for s in frozen.sources if s.scope_kind == "client"]
        pages = {(item.source.document_version_id, item.page_number) for item in sentences}
        topic_labels = [
            label
            for label, _terms in _TOPICS
            if any(label in item.topics for item in sentences)
        ]
        topic_summary = "、".join(topic_labels) if topic_labels else "未形成可靠的主题归类"
        source_names = "、".join(
            f"《{source.document_name}》v{source.version_number}"
            for source in frozen.sources
        )
        bodies = {
            "source_scope": (
                f"本次仅分析已冻结的服务方材料 {len(provider_sources)} 份、"
                f"本企业材料 {len(client_sources)} 份，共 {len(pages)} 个有文本证据的页面。"
                f"材料范围：{source_names}。"
            ),
            "status_summary": (
                f"从 {len(sentences)} 条可提取文本中识别到的材料主题为：{topic_summary}。"
                "本摘要只描述材料覆盖情况，不将未出现的内容推断为不存在。"
            ),
            "key_findings": _findings_body(selected),
            "risks_and_gaps": _risks_body(selected),
            "remediation": _remediation_body(selected),
            "citations": "\n".join(
                f"[证据{index}] 《{citation.document_name}》v{citation.version_number} "
                f"第{citation.page_number}页：{citation.excerpt}"
                for index, citation in enumerate(citations, start=1)
            ),
            "usage_boundary": (
                "本报告是对已发布材料文本的本地规则式提取，未调用外部模型。"
                "它不是完整性保证、法定合规评价、执法结论或生产放行依据；"
                "原文证据不足时必须由人员和现场证据补齐。"
            ),
        }
        sections = tuple(
            GeneratedSection(key=key, title=SECTION_TITLES[key], body=bodies[key])
            for key in SECTION_KEYS
        )
        return GeneratedReport(sections=sections, citations=citations)
