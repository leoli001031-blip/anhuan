"""Bounded pypdf-only heuristics for human-reviewable material candidates."""
from __future__ import annotations

import hashlib
import io
import math
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from typing import BinaryIO

from pypdf import PdfReader

from ..p3.contracts import MAX_PDF_PAGES
from .contracts import FieldCandidate, MaterialAnalysisResult, PageClassification


_WHITESPACE_RE = re.compile(r"[\t\r\f\v ]+")
_REFERENCE_RE = re.compile(
    r"(?:[A-Za-z]{1,8}(?:/T)?\s*\d{2,8}(?:[.\-]\d{1,4})?"
    r"|[\u4e00-\u9fff]{1,10}[〔\[][12][0-9]{3}[〕\]][0-9]{1,6}号)"
)
_DATE_RE = re.compile(
    r"(?P<year>20[0-9]{2})\s*(?:年|[-/.])\s*"
    r"(?P<month>0?[1-9]|1[0-2])\s*(?:月|[-/.])\s*"
    r"(?P<day>0?[1-9]|[12][0-9]|3[01])\s*日?"
)
_PUBLISHER_LABEL_RE = re.compile(
    r"(?:发布机关|发布单位|制定机关|发文机关|颁布部门|发布部门)\s*[:：]\s*(.{2,80})"
)
_ORGANIZATION_SUFFIXES = (
    "人民代表大会",
    "人民政府",
    "国务院",
    "委员会",
    "管理局",
    "监督局",
    "应急管理部",
    "生态环境部",
    "卫生健康委员会",
)
_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "safety": ("安全生产", "事故", "隐患", "应急", "作业安全"),
    "health": ("职业健康", "职业病", "卫生", "健康", "劳动防护"),
    "environment": ("环境", "污染", "排放", "生态", "废水", "废气"),
    "fire": ("消防", "火灾", "防火", "灭火"),
    "chemical": ("危险化学品", "化学品", "危化", "易燃", "爆炸物"),
}
_REPORT_KEYWORDS = ("报告", "检查", "监测", "评估", "评价", "记录")


class MaterialAnalysisFailure(RuntimeError):
    def __init__(self, code: str, *, page_count: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.page_count = page_count


def _normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value).replace("\x00", "")
    lines: list[str] = []
    for raw_line in value.splitlines():
        line = _WHITESPACE_RE.sub(" ", raw_line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _evidence(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value.replace("\n", " ")).strip()[:240]


def _ppm(value: float) -> int:
    return max(0, min(1_000_000, int(round(value * 1_000_000))))


def _page_fragments(page) -> tuple[str, list[tuple[float, float, str]]]:
    fragments: list[tuple[float, float, str]] = []

    def visitor(text, _cm, tm, _font_dict, _font_size) -> None:
        normalized = _normalize_text(str(text or ""))
        if not normalized:
            return
        try:
            x = float(tm[4])
            y = float(tm[5])
        except (IndexError, TypeError, ValueError):
            return
        if math.isfinite(x) and math.isfinite(y):
            fragments.append((x, y, normalized[:2_000]))

    try:
        extracted = page.extract_text(visitor_text=visitor) or ""
    except Exception as error:
        raise MaterialAnalysisFailure("MATERIAL_PDF_PARSE_FAILED") from error
    return _normalize_text(extracted), fragments


def _geometry_hints(
    page, fragments: list[tuple[float, float, str]]
) -> tuple[bool, int, bool, int]:
    try:
        width = abs(float(page.mediabox.width))
        height = abs(float(page.mediabox.height))
    except (TypeError, ValueError):
        return False, 0, False, 0
    if width <= 1 or height <= 1:
        return False, 0, False, 0

    rows: dict[int, list[float]] = defaultdict(list)
    for x, y, text in fragments:
        if text.strip():
            rows[int(round(y / 6.0))].append(x)
    grid_rows = 0
    for xs in rows.values():
        separated: list[float] = []
        for value in sorted(xs):
            if not separated or value - separated[-1] >= width * 0.08:
                separated.append(value)
        if len(separated) >= 3:
            grid_rows += 1
    table_detected = grid_rows >= 3
    table_confidence = _ppm(min(0.9, 0.45 + grid_rows * 0.08)) if table_detected else 0

    left_y = [y for x, y, text in fragments if x < width * 0.45 and len(text) >= 2]
    right_y = [y for x, y, text in fragments if x > width * 0.55 and len(text) >= 2]
    overlap = 0.0
    if left_y and right_y:
        overlap = max(
            0.0,
            min(max(left_y), max(right_y)) - max(min(left_y), min(right_y)),
        ) / height
    two_column_detected = len(left_y) >= 5 and len(right_y) >= 5 and overlap >= 0.2
    two_column_confidence = (
        _ppm(min(0.9, 0.55 + min(len(left_y), len(right_y)) * 0.015))
        if two_column_detected
        else 0
    )
    return (
        table_detected,
        table_confidence,
        two_column_detected,
        two_column_confidence,
    )


def _page_classification(
    page_number: int,
    page,
    text: str,
    fragments: list[tuple[float, float, str]],
) -> PageClassification:
    character_count = min(100_000, len(re.sub(r"\s+", "", text)))
    if character_count < 20:
        primary_kind = "scanned"
        text_confidence = _ppm(character_count / 40.0)
        scan_confidence = _ppm(0.92 - character_count / 100.0)
    elif character_count < 120:
        primary_kind = "mixed"
        text_confidence = _ppm(0.45 + min(character_count, 120) / 400.0)
        scan_confidence = _ppm(0.65 - min(character_count, 120) / 400.0)
    else:
        primary_kind = "text"
        text_confidence = _ppm(min(0.95, 0.68 + character_count / 8_000.0))
        scan_confidence = _ppm(max(0.02, 0.25 - character_count / 10_000.0))
    table, table_confidence, columns, column_confidence = _geometry_hints(
        page, fragments
    )
    if not table and len(re.findall(r"\S+\s{2,}\S+", text)) >= 3:
        table = True
        table_confidence = 420_000
    return PageClassification(
        page_number=page_number,
        primary_kind=primary_kind,
        ocr_required=character_count < 40,
        table_candidate=table,
        two_column_candidate=columns,
        text_character_count=character_count,
        text_confidence_ppm=text_confidence,
        scan_confidence_ppm=scan_confidence,
        table_confidence_ppm=table_confidence,
        two_column_confidence_ppm=column_confidence,
        reason_codes=("OCR_REQUIRED",) if character_count < 40 else (),
    )


def _candidate(
    field_name: str,
    value: str,
    *,
    page_number: int,
    evidence: str,
    confidence_ppm: int,
) -> FieldCandidate | None:
    # PPM values are uncalibrated hints.  This fixed producer-side floor only
    # suppresses weak guesses; it must not be presented as measured accuracy.
    if confidence_ppm < 500_000:
        return None
    normalized = _normalize_text(value).strip()
    evidence_text = _evidence(evidence)
    if not normalized or not evidence_text:
        return None
    return FieldCandidate(
        field_name=field_name,
        candidate_value=normalized[:4_000],
        page_number=page_number,
        evidence_snippet=evidence_text,
        confidence_ppm=confidence_ppm,
        confidence_basis=f"heuristic.{field_name}",
    )


def _title_candidate(pages: list[tuple[int, str]]) -> tuple[int, str] | None:
    best: tuple[int, int, str] | None = None
    for page_number, page_text in pages[:3]:
        for position, line in enumerate(page_text.splitlines()[:20]):
            candidate = line.strip(" -—·•\t")
            if not 4 <= len(candidate) <= 120:
                continue
            if _DATE_RE.fullmatch(candidate) or _REFERENCE_RE.fullmatch(candidate):
                continue
            score = 100 - position * 2
            if any(word in candidate for word in ("法", "条例", "规定", "办法", "标准", "指南", "通知")):
                score += 30
            if best is None or score > best[0]:
                best = (score, page_number, candidate)
    return (best[1], best[2]) if best else None


def _source_type(title: str) -> tuple[str, int] | None:
    if title.endswith("法") or "中华人民共和国" in title and "法" in title:
        return "law", 760_000
    if "标准" in title or re.search(r"\b(?:GB|HJ|AQ|DB)\s*/?T?\s*\d", title, re.I):
        return "standard", 720_000
    if any(word in title for word in ("指南", "导则", "指导意见")):
        return "guidance", 690_000
    if any(word in title for word in ("条例", "规定", "办法", "细则", "通知")):
        return "regulation", 650_000
    return None


def _publisher_candidate(pages: list[tuple[int, str]]) -> tuple[int, str, str] | None:
    for page_number, text in pages[:4]:
        match = _PUBLISHER_LABEL_RE.search(text)
        if match:
            return page_number, match.group(1).splitlines()[0][:200], match.group(0)
        for line in text.splitlines()[:40]:
            if 2 <= len(line) <= 80 and line.endswith(_ORGANIZATION_SUFFIXES):
                return page_number, line, line
    return None


def _date_candidates(pages: list[tuple[int, str]]) -> list[FieldCandidate]:
    output: list[FieldCandidate] = []
    seen: set[str] = set()
    for page_number, text in pages:
        for match in _DATE_RE.finditer(text):
            try:
                parsed = date(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                ).isoformat()
            except ValueError:
                continue
            context = text[max(0, match.start() - 30) : match.end() + 30]
            if any(word in context for word in ("施行", "生效", "实施")):
                field = "effective_from"
                confidence = 700_000
            elif any(word in context for word in ("发布", "公布", "印发", "通过")):
                field = "issued_on"
                confidence = 650_000
            else:
                field = "issued_on"
                confidence = 420_000
            if field in seen:
                continue
            candidate = _candidate(
                field,
                parsed,
                page_number=page_number,
                evidence=context,
                confidence_ppm=confidence,
            )
            if candidate:
                output.append(candidate)
                seen.add(field)
        if len(seen) >= 2:
            break
    return output


def _domain_candidate(pages: list[tuple[int, str]]) -> tuple[int, str, str, int]:
    scores: Counter[str] = Counter()
    first_evidence: dict[str, tuple[int, str]] = {}
    for page_number, text in pages:
        for domain, keywords in _DOMAIN_KEYWORDS.items():
            score = sum(text.count(keyword) for keyword in keywords)
            if score:
                scores[domain] += score
                first_evidence.setdefault(domain, (page_number, text[:240]))
    if not scores:
        page_number, text = pages[0]
        return page_number, "general", text[:240], 300_000
    domain, score = scores.most_common(1)[0]
    page_number, evidence = first_evidence[domain]
    return page_number, domain, evidence, min(800_000, 420_000 + score * 50_000)


def _field_candidates(pages: list[tuple[int, str]]) -> tuple[FieldCandidate, ...]:
    output: list[FieldCandidate] = []
    title_item = _title_candidate(pages)
    title = ""
    if title_item:
        page_number, title = title_item
        for field_name in ("source_title", "version_title"):
            candidate = _candidate(
                field_name,
                title,
                page_number=page_number,
                evidence=title,
                confidence_ppm=680_000,
            )
            if candidate:
                output.append(candidate)
        typed = _source_type(title)
        if typed:
            value, confidence = typed
            candidate = _candidate(
                "source_type",
                value,
                page_number=page_number,
                evidence=title,
                confidence_ppm=confidence,
            )
            if candidate:
                output.append(candidate)

    publisher = _publisher_candidate(pages)
    if publisher:
        page_number, value, evidence = publisher
        candidate = _candidate(
            "publisher",
            value,
            page_number=page_number,
            evidence=evidence,
            confidence_ppm=620_000,
        )
        if candidate:
            output.append(candidate)

    for page_number, text in pages[:5]:
        reference = _REFERENCE_RE.search(text)
        if reference:
            candidate = _candidate(
                "source_reference",
                reference.group(0),
                page_number=page_number,
                evidence=text[max(0, reference.start() - 20) : reference.end() + 20],
                confidence_ppm=740_000,
            )
            if candidate:
                output.append(candidate)
            break

    page_number, domain, domain_evidence, confidence = _domain_candidate(pages)
    candidate = _candidate(
        "domain",
        domain,
        page_number=page_number,
        evidence=domain_evidence,
        confidence_ppm=confidence,
    )
    if candidate:
        output.append(candidate)

    report_evidence = next(
        (
            (page_number, text)
            for page_number, text in pages[:5]
            if any(keyword in text for keyword in _REPORT_KEYWORDS)
        ),
        None,
    )
    if report_evidence is not None:
        report_page, report_text = report_evidence
        if title_item and any(keyword in title for keyword in _REPORT_KEYWORDS):
            candidate = _candidate(
                "report_title",
                title,
                page_number=title_item[0],
                evidence=title,
                confidence_ppm=680_000,
            )
            if candidate:
                output.append(candidate)
        report_date = _DATE_RE.search(report_text)
        if report_date:
            try:
                parsed_report_date = date(
                    int(report_date.group("year")),
                    int(report_date.group("month")),
                    int(report_date.group("day")),
                ).isoformat()
            except ValueError:
                parsed_report_date = ""
            candidate = _candidate(
                "report_date",
                parsed_report_date,
                page_number=report_page,
                evidence=report_text[
                    max(0, report_date.start() - 30) : report_date.end() + 30
                ],
                confidence_ppm=600_000,
            )
            if candidate:
                output.append(candidate)
    output.extend(_date_candidates(pages))

    summary_lines: list[str] = []
    summary_page = pages[0][0]
    for page_number, text in pages[:3]:
        for line in text.splitlines():
            if line != title and len(line) >= 8:
                if not summary_lines:
                    summary_page = page_number
                summary_lines.append(line)
                if len("\n".join(summary_lines)) >= 600:
                    break
        if len("\n".join(summary_lines)) >= 600:
            break
    summary = "\n".join(summary_lines)[:1_000]
    candidate = _candidate(
        "summary",
        summary,
        page_number=summary_page,
        evidence=summary,
        confidence_ppm=300_000,
    )
    if candidate:
        output.append(candidate)
    if report_evidence is not None:
        candidate = _candidate(
            "report_summary",
            summary,
            page_number=summary_page,
            evidence=summary,
            confidence_ppm=520_000,
        )
        if candidate:
            output.append(candidate)
    return tuple(output)


def analyze_pdf(
    file_obj: BinaryIO,
    *,
    expected_sha256: str,
) -> MaterialAnalysisResult:
    """Analyze one already-scanned PDF without changing its P3 state."""
    try:
        file_obj.seek(0)
        raw = file_obj.read()
        file_obj.seek(0)
    except Exception as error:
        raise MaterialAnalysisFailure("MATERIAL_SOURCE_READ_FAILED") from error
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise MaterialAnalysisFailure("MATERIAL_SOURCE_IDENTITY_MISMATCH")
    try:
        reader = PdfReader(io.BytesIO(raw), strict=True)
    except Exception as error:
        raise MaterialAnalysisFailure("MATERIAL_PDF_PARSE_FAILED") from error
    if reader.is_encrypted:
        raise MaterialAnalysisFailure("MATERIAL_PDF_ENCRYPTED")
    if not 1 <= len(reader.pages) <= MAX_PDF_PAGES:
        raise MaterialAnalysisFailure("MATERIAL_PDF_PAGE_LIMIT")

    page_outputs: list[PageClassification] = []
    page_texts: list[tuple[int, str]] = []
    try:
        for page_number, page in enumerate(reader.pages, start=1):
            text, fragments = _page_fragments(page)
            page_texts.append((page_number, text))
            page_outputs.append(
                _page_classification(page_number, page, text, fragments)
            )
    except MaterialAnalysisFailure as error:
        raise MaterialAnalysisFailure(
            error.code, page_count=len(reader.pages)
        ) from error

    kinds = {page.primary_kind for page in page_outputs}
    if kinds == {"scanned"}:
        profile = "scanned"
    elif len(kinds) > 1:
        profile = "mixed"
    elif sum(page.two_column_candidate for page in page_outputs) > len(page_outputs) / 2:
        profile = "two_column"
    elif sum(page.table_candidate for page in page_outputs) > len(page_outputs) / 2:
        profile = "table"
    else:
        profile = next(iter(kinds), "unknown")

    candidates = ()
    if any(page.text_character_count >= 40 for page in page_outputs):
        nonempty_pages = [(number, text) for number, text in page_texts if text]
        if nonempty_pages:
            candidates = _field_candidates(nonempty_pages)
    return MaterialAnalysisResult(
        document_profile=profile,
        pages=tuple(page_outputs),
        candidates=candidates,
    )


__all__ = ("MaterialAnalysisFailure", "analyze_pdf")
