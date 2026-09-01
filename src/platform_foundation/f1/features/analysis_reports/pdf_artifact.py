"""Deterministic, downloadable PDF artifacts for immutable report versions.

The PDF mirrors the HTML artifact contract: the same stored-version payload,
the same completeness validation, the same fixed reason codes, and a
deterministic output (fixed creation date, no embedded timestamps).  The
bundled OFL-licensed Noto Sans SC font is the only accepted CJK face; a
missing or unreadable font fails closed instead of substituting glyphs.
"""
from __future__ import annotations

import hashlib
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fpdf import FPDF, FPDFException

from .artifact import ReportArtifact, ReportArtifactInvalid, _integer, _text
from .contracts import SECTION_KEYS, SECTION_TITLES, TEMPLATE_TITLE


_REPO_ROOT = Path(__file__).resolve().parents[5]
_FONT_CANDIDATES = (
    Path(os.environ.get("F1_REPORT_PDF_FONT", "") or "/nonexistent"),
    _REPO_ROOT / "assets/fonts/NotoSansSC-Regular.otf",
    Path("/app/assets/fonts/NotoSansSC-Regular.otf"),
)
_FIXED_CREATION_DATE = datetime(2024, 1, 1, tzinfo=timezone.utc)
_MAX_PDF_BYTES = 4 * 1024 * 1024


def _font_path() -> Path:
    override = os.environ.get("F1_REPORT_PDF_FONT", "").strip()
    candidates = (Path(override),) if override else _FONT_CANDIDATES
    for candidate in candidates:
        try:
            info = candidate.stat()
            link = candidate.lstat()
        except OSError:
            continue
        if (
            stat.S_ISREG(info.st_mode)
            and not stat.S_ISLNK(link.st_mode)
            and 1_000_000 <= info.st_size <= 64 * 1024 * 1024
        ):
            return candidate
    raise ReportArtifactInvalid("REPORT_PDF_FONT_UNAVAILABLE")


class _ReportPDF(FPDF):
    def footer(self) -> None:  # noqa: D102
        self.set_y(-14)
        self.set_font("noto", "", 9)
        self.set_text_color(88, 112, 105)
        self.cell(0, 8, f"第 {self.page_no()} 页 · 仅基于报告内已列引用证据", align="C")


def render_pdf_artifact(payload: dict[str, Any]) -> ReportArtifact:
    """Render one complete stored version as a deterministic A4 PDF."""

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

    font = _font_path()
    pdf = _ReportPDF(format="A4")
    pdf.set_margins(16, 16, 16)
    pdf.set_auto_page_break(auto=True, margin=18)
    try:
        pdf.add_font("noto", "", str(font))
    except (FPDFException, OSError, ValueError):
        raise ReportArtifactInvalid("REPORT_PDF_FONT_UNAVAILABLE") from None
    if hasattr(pdf, "set_creation_date"):
        pdf.set_creation_date(_FIXED_CREATION_DATE)
    pdf.add_page()

    pdf.set_font("noto", "", 22)
    pdf.set_text_color(24, 50, 43)
    pdf.cell(0, 14, TEMPLATE_TITLE, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("noto", "", 10)
    pdf.set_text_color(88, 112, 105)
    pdf.cell(0, 8, f"第 {version_number} 版", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_draw_color(43, 117, 98)
    pdf.set_line_width(0.6)
    pdf.line(16, pdf.get_y(), 194, pdf.get_y())
    pdf.ln(6)

    for item in sections:
        if not isinstance(item, dict):
            raise ReportArtifactInvalid("REPORT_ARTIFACT_CONTENT_INVALID")
        key = _text(item.get("key"))
        if key not in SECTION_KEYS:
            raise ReportArtifactInvalid("REPORT_ARTIFACT_SECTIONS_INCOMPLETE")
        title = _text(item.get("title")) or SECTION_TITLES[key]
        body = _text(item.get("body"))
        pdf.set_font("noto", "", 14)
        pdf.set_text_color(24, 50, 43)
        pdf.multi_cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("noto", "", 11)
        pdf.set_text_color(38, 56, 50)
        pdf.multi_cell(0, 7.5, body, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(4)

    pdf.set_font("noto", "", 14)
    pdf.set_text_color(24, 50, 43)
    pdf.multi_cell(0, 10, "引用索引", new_x="LMARGIN", new_y="NEXT")
    for index, item in enumerate(citations, start=1):
        if not isinstance(item, dict):
            raise ReportArtifactInvalid("REPORT_ARTIFACT_CONTENT_INVALID")
        name = _text(item.get("document_name"))
        excerpt = _text(item.get("excerpt"))
        source_version = _integer(item.get("version_number"))
        page = _integer(item.get("page_number"))
        pdf.set_font("noto", "", 11)
        pdf.set_text_color(24, 50, 43)
        pdf.multi_cell(
            0,
            7.5,
            f"[{index}] {name} 第 {source_version} 版 · 第 {page} 页",
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.set_font("noto", "", 10)
        pdf.set_text_color(88, 112, 105)
        pdf.multi_cell(0, 6.5, excerpt, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    try:
        body = bytes(pdf.output())
    except (FPDFException, OSError, ValueError):
        raise ReportArtifactInvalid("REPORT_PDF_RENDER_FAILED") from None
    if not 1_000 <= len(body) <= _MAX_PDF_BYTES or body[:5] != b"%PDF-":
        raise ReportArtifactInvalid("REPORT_PDF_RENDER_FAILED")
    return ReportArtifact(
        body=body,
        filename=f"a-eco-analysis-report-v{version_number}.pdf",
        sha256=hashlib.sha256(body).hexdigest(),
    )


__all__ = ("render_pdf_artifact",)
