"""Deterministic, downloadable HTML artifacts for immutable report versions."""
from __future__ import annotations

import hashlib
import html
from dataclasses import dataclass
from typing import Any

from .contracts import SECTION_KEYS, TEMPLATE_TITLE


class ReportArtifactInvalid(RuntimeError):
    """The stored version cannot be rendered as a complete report artifact."""


@dataclass(frozen=True, slots=True)
class ReportArtifact:
    body: bytes
    filename: str
    sha256: str


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReportArtifactInvalid("REPORT_ARTIFACT_CONTENT_INVALID")
    return value.strip()


def _integer(value: object, *, minimum: int = 1) -> int:
    if isinstance(value, bool):
        raise ReportArtifactInvalid("REPORT_ARTIFACT_CONTENT_INVALID")
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ReportArtifactInvalid("REPORT_ARTIFACT_CONTENT_INVALID") from None
    if number < minimum:
        raise ReportArtifactInvalid("REPORT_ARTIFACT_CONTENT_INVALID")
    return number


def render_html_artifact(payload: dict[str, Any]) -> ReportArtifact:
    """Render one complete stored version without network or template fallback."""
    version_number = _integer(payload.get("version_number"))
    sections = payload.get("sections")
    citations = payload.get("citations")
    if not isinstance(sections, list) or not isinstance(citations, list):
        raise ReportArtifactInvalid("REPORT_ARTIFACT_CONTENT_INVALID")
    section_keys = [item.get("key") for item in sections if isinstance(item, dict)]
    if len(sections) != len(SECTION_KEYS) or set(section_keys) != set(SECTION_KEYS):
        raise ReportArtifactInvalid("REPORT_ARTIFACT_SECTIONS_INCOMPLETE")
    if not citations:
        raise ReportArtifactInvalid("REPORT_ARTIFACT_CITATIONS_REQUIRED")

    section_html: list[str] = []
    for item in sections:
        if not isinstance(item, dict):
            raise ReportArtifactInvalid("REPORT_ARTIFACT_CONTENT_INVALID")
        key = _text(item.get("key"))
        if key not in SECTION_KEYS:
            raise ReportArtifactInvalid("REPORT_ARTIFACT_SECTIONS_INCOMPLETE")
        title = html.escape(_text(item.get("title")))
        body = html.escape(_text(item.get("body")))
        section_html.append(
            f'<section id="{html.escape(key, quote=True)}"><h2>{title}</h2>'
            f'<div class="report-body">{body}</div></section>'
        )

    citation_html: list[str] = []
    for index, item in enumerate(citations, start=1):
        if not isinstance(item, dict):
            raise ReportArtifactInvalid("REPORT_ARTIFACT_CONTENT_INVALID")
        name = html.escape(_text(item.get("document_name")))
        excerpt = html.escape(_text(item.get("excerpt")))
        document_version = html.escape(_text(item.get("document_version_id")))
        source_version = _integer(item.get("version_number"))
        page = _integer(item.get("page_number"))
        citation_html.append(
            "<li>"
            f"<strong>[{index}] {name}</strong>"
            f"<span>第 {source_version} 版 · 第 {page} 页</span>"
            f"<blockquote>{excerpt}</blockquote>"
            f'<code data-document-version="{document_version}">{document_version}</code>'
            "</li>"
        )

    title = html.escape(TEMPLATE_TITLE)
    body = (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{title}·第 {version_number} 版</title>"
        "<style>"
        "@page{size:A4;margin:18mm 16mm}"
        "*{box-sizing:border-box}body{margin:0;color:#18322b;background:#fff;"
        "font:14px/1.75 -apple-system,BlinkMacSystemFont,'PingFang SC','Microsoft YaHei',sans-serif}"
        "main{max-width:800px;margin:0 auto;padding:40px 32px}"
        "header{padding-bottom:22px;border-bottom:2px solid #2b7562}"
        "h1{font-size:28px;margin:0 0 8px}h2{font-size:19px;margin:30px 0 10px}"
        ".meta,li span{display:block;color:#587069;font-size:12px}"
        ".report-body{white-space:pre-wrap}ol{padding-left:24px}li{margin:0 0 18px}"
        "blockquote{margin:7px 0;padding:9px 12px;border-left:3px solid #8fb9ad;background:#f5faf8}"
        "code{font-size:10px;color:#61726d;overflow-wrap:anywhere}"
        "@media print{main{padding:0}section,li{break-inside:avoid}}"
        "</style></head><body><main>"
        f"<header><h1>{title}</h1><div class=\"meta\">第 {version_number} 版"
        " · 仅基于报告内已列引用证据</div></header>"
        + "".join(section_html)
        + "<section id=\"artifact-citations\"><h2>引用索引</h2><ol>"
        + "".join(citation_html)
        + "</ol></section></main></body></html>"
    ).encode("utf-8")
    return ReportArtifact(
        body=body,
        filename=f"a-eco-analysis-report-v{version_number}.html",
        sha256=hashlib.sha256(body).hexdigest(),
    )


__all__ = (
    "ReportArtifact",
    "ReportArtifactInvalid",
    "render_html_artifact",
)
