"""Opt-in GLM-backed analysis-report generation.

``F1_MATERIAL_ANALYSIS_REPORT_LLM=1`` switches report generation from the
deterministic extractive generator to a cloud chat model.  The prompt is
built only from the immutable frozen source units; the model must answer in
strict JSON and may cite only numbered evidence blocks it was given.  Every
model-authored body is bounded, the ``usage_boundary`` section is always the
fixed deterministic safety statement, the citations list is rebuilt from the
whitelisted evidence (never from model text), and any configuration,
transport, parse, or validation failure fails the generation with a fixed
reason — there is no silent fallback to the local generator.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Callable
from urllib.error import HTTPError, URLError

from ..material_intake.cloud_ocr import (
    CloudOcrConfig,
    _default_transport,
    _dialect_endpoint,
)
from .contracts import (
    SECTION_KEYS,
    SECTION_TITLES,
    TEMPLATE_ID,
    EligibleSource,
    EvidenceUnit,
    FrozenSourceSet,
    GeneratedCitation,
    GeneratedReport,
    GeneratedSection,
    GenerationFailed,
)


LLM_REPORT_MODE_ENV = "F1_MATERIAL_ANALYSIS_REPORT_LLM"
LLM_REPORT_MODEL_ENV = "F1_MATERIAL_ANALYSIS_REPORT_LLM_MODEL"
_MAX_EVIDENCE_BLOCKS = 60
_EVIDENCE_TEXT_CHARACTERS = 400
# f1_0017 caps citation excerpts at 320 characters.
_CITATION_EXCERPT_CHARACTERS = 320
_MODEL_BODY_CHARACTERS = 1_200
_MAX_CITATIONS = 12
_MIN_CITATIONS = 2
_ANTHROPIC_MAX_TOKENS = 4096

_USAGE_BOUNDARY_BODY = (
    "本报告由大语言模型基于已冻结的已发布材料文本辅助生成，"
    "生成过程受证据白名单约束，但模型归纳仍可能存在偏差或遗漏。"
    "它不是完整性保证、法定合规评价、执法结论或生产放行依据；"
    "引用证据不足以支撑结论时，必须由专业人员结合现场证据复核后采用。"
)

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_MODEL_SECTION_KEYS = (
    "source_scope",
    "status_summary",
    "key_findings",
    "risks_and_gaps",
    "remediation",
)

_INSTRUCTIONS = """你是环保托管运营平台的分析报告撰写助手。请只依据下方编号证据块撰写报告章节，规则：
1. 只能使用证据块中出现的信息，不得编造、推测或引入外部知识。
2. 输出严格的 JSON 对象（不要 markdown 代码块、不要注释），结构为：
   {"source_scope":"...","status_summary":"...","key_findings":"...","risks_and_gaps":"...","remediation":"...","citations":[编号, ...]}
3. 五个正文章节使用简体中文，每节不超过 400 字；key_findings 与 risks_and_gaps 应分点陈述（用「；」或换行分隔）。
4. citations 是你实际引用的证据块编号数组，必须包含 2 到 12 个不重复编号，且只能来自给定证据块编号。
5. source_scope 概括材料范围；status_summary 概括材料反映的现状；remediation 给出与证据对应的整改建议。"""


def llm_generation_enabled() -> bool:
    return os.environ.get(LLM_REPORT_MODE_ENV, "").strip() in {"1", "true", "on"}


@dataclass(frozen=True, slots=True)
class _EvidenceBlock:
    index: int
    source: EligibleSource
    unit: EvidenceUnit


def _evidence_blocks(frozen: FrozenSourceSet) -> list[_EvidenceBlock]:
    blocks: list[_EvidenceBlock] = []
    for source in frozen.sources:
        for unit in source.evidence_units:
            if len(blocks) >= _MAX_EVIDENCE_BLOCKS:
                return blocks
            blocks.append(
                _EvidenceBlock(
                    index=len(blocks) + 1, source=source, unit=unit
                )
            )
    return blocks


def _llm_settings() -> tuple[CloudOcrConfig, str]:
    config = CloudOcrConfig.from_environment()
    model = os.environ.get(LLM_REPORT_MODEL_ENV, "").strip() or (
        os.environ.get("F1_MATERIAL_CLOUD_OCR_MODEL", "").strip()
    )
    if (
        not config.enabled
        or not config.configuration_valid
        or not model
    ):
        raise GenerationFailed("REPORT_LLM_CONFIGURATION_INVALID")
    return config, model


def _chat_transport(
    config: CloudOcrConfig,
    model: str,
    prompt: str,
    *,
    transport: Callable[[str, dict[str, str], bytearray, float], bytes]
    | None = None,
) -> str:
    path_suffix, extra_headers = _dialect_endpoint(config.dialect)
    body: dict[str, object] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    if config.dialect == "anthropic":
        body["max_tokens"] = _ANTHROPIC_MAX_TOKENS
    payload = bytearray(
        json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )
    headers = {
        "Authorization": "Bearer " + _read_report_key(config),
        "Content-Type": "application/json",
        **extra_headers,
    }
    active = transport or _default_transport
    url = config.base_url.rstrip("/") + path_suffix
    try:
        raw = active(url, headers, payload, config.request_timeout_seconds)
    except (HTTPError, URLError, OSError, ValueError):
        raise GenerationFailed("REPORT_LLM_UNAVAILABLE") from None
    finally:
        payload[:] = b"\0" * len(payload)
        payload.clear()
    return _response_text(raw)


def _read_report_key(config: CloudOcrConfig) -> str:
    import stat as _stat

    path = config.api_key_file
    try:
        info = os.stat(path, follow_symlinks=False)  # type: ignore[arg-type]
        if (
            not _stat.S_ISREG(info.st_mode)
            or _stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise OSError("mode")
        with open(path, "rb") as handle:  # noqa: SIM115
            key = handle.read(4096).decode("ascii", "strict").strip()
    except (OSError, UnicodeDecodeError):
        raise GenerationFailed("REPORT_LLM_CONFIGURATION_INVALID") from None
    if not key or any(character.isspace() for character in key):
        raise GenerationFailed("REPORT_LLM_CONFIGURATION_INVALID")
    return key


def _response_text(raw: bytes) -> str:
    try:
        value = json.loads(raw.decode("utf-8", "strict"))
        if isinstance(value.get("content"), list):
            text = "\n".join(
                block.get("text", "")
                for block in value["content"]
                if isinstance(block, dict) and block.get("type") == "text"
            )
        else:
            text = value["choices"][0]["message"]["content"]
        if not isinstance(text, str):
            raise ValueError("text")
    except Exception:
        raise GenerationFailed("REPORT_LLM_RESPONSE_INVALID") from None
    return text


def _parse_model_output(text: str) -> tuple[dict[str, str], list[int]]:
    match = _JSON_OBJECT_RE.search(text)
    if match is None:
        raise GenerationFailed("REPORT_LLM_RESPONSE_INVALID")
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        raise GenerationFailed("REPORT_LLM_RESPONSE_INVALID") from None
    if not isinstance(value, dict):
        raise GenerationFailed("REPORT_LLM_RESPONSE_INVALID")
    bodies: dict[str, str] = {}
    for key in _MODEL_SECTION_KEYS:
        body = value.get(key)
        if (
            not isinstance(body, str)
            or not 1 <= len(body) <= _MODEL_BODY_CHARACTERS
            or body.strip() != body
        ):
            raise GenerationFailed("REPORT_LLM_OUTPUT_INVALID")
        bodies[key] = body
    if set(value) - set(_MODEL_SECTION_KEYS) - {"citations"}:
        raise GenerationFailed("REPORT_LLM_OUTPUT_INVALID")
    raw_citations = value.get("citations")
    if not isinstance(raw_citations, list):
        raise GenerationFailed("REPORT_LLM_OUTPUT_INVALID")
    indices: list[int] = []
    for item in raw_citations:
        if type(item) is not int or not 1 <= item <= _MAX_EVIDENCE_BLOCKS:
            raise GenerationFailed("REPORT_LLM_OUTPUT_INVALID")
        if item not in indices:
            indices.append(item)
    if not _MIN_CITATIONS <= len(indices) <= _MAX_CITATIONS:
        raise GenerationFailed("REPORT_LLM_OUTPUT_INVALID")
    return bodies, indices


class LlmReportGenerator:
    """Generate a report with a cloud chat model over frozen evidence only."""

    def __init__(
        self,
        *,
        transport: Callable[[str, dict[str, str], bytearray, float], bytes]
        | None = None,
    ) -> None:
        self._transport = transport

    def generate(self, frozen: FrozenSourceSet) -> GeneratedReport:
        if frozen.template_id != TEMPLATE_ID:
            raise GenerationFailed("REPORT_TEMPLATE_INVALID")
        if not frozen.sources:
            raise GenerationFailed("REPORT_SOURCES_EMPTY")
        blocks = _evidence_blocks(frozen)
        if len(blocks) < _MIN_CITATIONS:
            raise GenerationFailed("REPORT_SOURCES_EMPTY")

        evidence_lines: list[str] = []
        for block in blocks:
            text = block.unit.text.strip()
            if len(text) > _EVIDENCE_TEXT_CHARACTERS:
                text = text[:_EVIDENCE_TEXT_CHARACTERS]
            evidence_lines.append(
                f"[{block.index}] 《{block.source.document_name}》"
                f"v{block.source.version_number} "
                f"第{block.unit.page_number}页：{text}"
            )
        prompt = (
            _INSTRUCTIONS
            + "\n\n证据块：\n"
            + "\n".join(evidence_lines)
            + "\n\n请输出 JSON："
        )

        config, model = _llm_settings()
        text = _chat_transport(
            config, model, prompt, transport=self._transport
        )
        bodies, indices = _parse_model_output(text)
        by_index = {block.index: block for block in blocks}
        citations = tuple(
            GeneratedCitation(
                document_version_id=by_index[index].source.document_version_id,
                document_name=by_index[index].source.document_name,
                version_number=by_index[index].source.version_number,
                page_number=by_index[index].unit.page_number,
                excerpt=by_index[index].unit.text.strip()[
                    :_CITATION_EXCERPT_CHARACTERS
                ],
            )
            for index in indices
        )
        citation_bodies = "\n".join(
            f"[证据{number}] 《{citation.document_name}》"
            f"v{citation.version_number} 第{citation.page_number}页："
            f"{citation.excerpt}"
            for number, citation in enumerate(citations, start=1)
        )
        section_bodies = {
            **bodies,
            "citations": citation_bodies,
            "usage_boundary": _USAGE_BOUNDARY_BODY,
        }
        sections = tuple(
            GeneratedSection(
                key=key, title=SECTION_TITLES[key], body=section_bodies[key]
            )
            for key in SECTION_KEYS
        )
        return GeneratedReport(sections=sections, citations=citations)


__all__ = ("LlmReportGenerator", "llm_generation_enabled")
