"""Bounded pypdf heuristics with optional local OCR for reviewable candidates."""
from __future__ import annotations

import hashlib
import io
import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date
from typing import BinaryIO, Callable, Iterable

from pypdf import PdfReader

from ..p3.contracts import MAX_PDF_PAGES
from .contracts import (
    FieldCandidate,
    MaterialAnalysisResult,
    MaterialKind,
    PageClassification,
)
from .ocr import (
    LocalOcrError,
    MAX_OCR_SOURCE_BYTES,
    OCR_PARSER_BACKEND,
    OCR_PARSER_BACKENDS,
    OcrPageResult,
    ocr_pdf_pages,
)


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
_REPORT_ROUTING_KEYWORDS = (
    "检测报告",
    "监测报告",
    "评估报告",
    "评价报告",
    "检查报告",
    "审计报告",
    "验收报告",
    "调查报告",
    "月度报告",
    "年度报告",
    "月报",
    "年报",
    "LDAR",
)
_POLICY_ROUTING_KEYWORDS = (
    "法律",
    "法规",
    "条例",
    "规定",
    "办法",
    "标准",
    "指南",
    "通知",
    "规范",
    "规程",
    "导则",
    "实施细则",
    "指导意见",
)


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
    *,
    embedded_text: str | None = None,
    ocr_result: OcrPageResult | None = None,
) -> PageClassification:
    embedded = text if embedded_text is None else embedded_text
    embedded_character_count = min(100_000, len(re.sub(r"\s+", "", embedded)))
    character_count = min(100_000, len(re.sub(r"\s+", "", text)))
    if embedded_character_count < 20:
        primary_kind = "scanned"
        text_confidence = _ppm(embedded_character_count / 40.0)
        scan_confidence = _ppm(0.92 - embedded_character_count / 100.0)
    elif embedded_character_count < 120:
        primary_kind = "mixed"
        text_confidence = _ppm(
            0.45 + min(embedded_character_count, 120) / 400.0
        )
        scan_confidence = _ppm(
            0.65 - min(embedded_character_count, 120) / 400.0
        )
    else:
        primary_kind = "text"
        text_confidence = _ppm(
            min(0.95, 0.68 + embedded_character_count / 8_000.0)
        )
        scan_confidence = _ppm(
            max(0.02, 0.25 - embedded_character_count / 10_000.0)
        )
    if ocr_result is not None and ocr_result.ocr_applied:
        # F0-H exposes an uncalibrated model score.  It is useful as a routing
        # hint, but never promoted to measured document accuracy.
        text_confidence = min(
            800_000,
            ocr_result.confidence_mean_ppm
            if ocr_result.confidence_mean_ppm is not None
            else 550_000,
        )
    table, table_confidence, columns, column_confidence = _geometry_hints(
        page, fragments
    )
    if not table and len(re.findall(r"\S+\s{2,}\S+", text)) >= 3:
        table = True
        table_confidence = 420_000
    if ocr_result is not None and ocr_result.ocr_applied:
        if ocr_result.table_candidate:
            table = True
            table_confidence = max(table_confidence, 520_000)
        if ocr_result.two_column_candidate:
            columns = True
            column_confidence = max(column_confidence, 520_000)
    ocr_required = embedded_character_count < 40 and not (
        ocr_result is not None and ocr_result.ocr_applied
    )
    reason_codes: list[str] = []
    if ocr_required:
        reason_codes.append("OCR_REQUIRED")
    if ocr_result is not None:
        reason_codes.append(ocr_result.reason_code)
    return PageClassification(
        page_number=page_number,
        primary_kind=primary_kind,
        ocr_required=ocr_required,
        table_candidate=table,
        two_column_candidate=columns,
        text_character_count=character_count,
        text_confidence_ppm=text_confidence,
        scan_confidence_ppm=scan_confidence,
        table_confidence_ppm=table_confidence,
        two_column_confidence_ppm=column_confidence,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
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


def _suggest_material_kind(
    pages: list[tuple[int, str]],
    classifications: list[PageClassification],
) -> tuple[MaterialKind, int]:
    """Return a deterministic routing hint, never an authoritative type.

    Report evidence intentionally wins over policy words because business
    reports often quote regulations and standards.  A PDF with no usable text
    remains unknown and is routed to human review instead of being guessed.
    """
    if not pages or not any(
        page.text_character_count >= 40 for page in classifications
    ):
        return "unknown", 0

    title_item = _title_candidate(pages)
    title = title_item[1] if title_item is not None else ""
    routing_text = "\n".join(
        "\n".join(text.splitlines()[:12]) for _, text in pages[:2]
    )
    title_folded = title.casefold()
    text_folded = routing_text.casefold()

    report_hits = sum(
        text_folded.count(keyword.casefold())
        for keyword in _REPORT_ROUTING_KEYWORDS
    )
    if report_hits:
        title_bonus = 100_000 if any(
            keyword.casefold() in title_folded
            for keyword in _REPORT_ROUTING_KEYWORDS
        ) else 0
        return "report", min(900_000, 650_000 + title_bonus + report_hits * 20_000)

    policy_hits = sum(
        text_folded.count(keyword.casefold())
        for keyword in _POLICY_ROUTING_KEYWORDS
    )
    if policy_hits:
        title_bonus = 100_000 if any(
            keyword.casefold() in title_folded
            for keyword in _POLICY_ROUTING_KEYWORDS
        ) else 0
        return "policy", min(880_000, 600_000 + title_bonus + policy_hits * 20_000)
    return "unknown", 0


def analyze_pdf(
    file_obj: BinaryIO,
    *,
    expected_sha256: str,
    ocr_checkpoints: Iterable[OcrPageResult] = (),
    ocr_checkpoint_callback: Callable[[OcrPageResult], None] | None = None,
    ocr_pages: Callable[..., tuple[OcrPageResult, ...]] | None = None,
    ocr_parser_backend: str = OCR_PARSER_BACKEND,
) -> MaterialAnalysisResult:
    """Analyze one already-scanned PDF without changing its P3 state."""
    if ocr_parser_backend not in OCR_PARSER_BACKENDS:
        raise MaterialAnalysisFailure("MATERIAL_OCR_ENGINE_INVALID")
    try:
        file_obj.seek(0)
        raw = file_obj.read(MAX_OCR_SOURCE_BYTES + 1)
        file_obj.seek(0)
    except Exception as error:
        raise MaterialAnalysisFailure("MATERIAL_SOURCE_READ_FAILED") from error
    if not isinstance(raw, bytes) or not 8 <= len(raw) <= MAX_OCR_SOURCE_BYTES:
        raise MaterialAnalysisFailure("MATERIAL_SOURCE_SIZE_LIMIT")
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

    extracted_pages: list[
        tuple[int, object, str, list[tuple[float, float, str]]]
    ] = []
    try:
        for page_number, page in enumerate(reader.pages, start=1):
            text, fragments = _page_fragments(page)
            extracted_pages.append((page_number, page, text, fragments))
    except MaterialAnalysisFailure as error:
        raise MaterialAnalysisFailure(
            error.code, page_count=len(reader.pages)
        ) from error

    ocr_page_numbers = tuple(
        page_number
        for page_number, _page, text, _fragments in extracted_pages
        if len(re.sub(r"\s+", "", text)) < 40
    )
    ocr_results: dict[int, OcrPageResult] = {}
    try:
        for item in ocr_checkpoints:
            if (
                item.page_number not in ocr_page_numbers
                or item.page_number in ocr_results
                or not item.ocr_applied
                or item.status != "applied"
                or item.reason_code != "OCR_APPLIED"
                or item.parser_backend != ocr_parser_backend
                or item.source_unit_id is None
            ):
                raise MaterialAnalysisFailure("MATERIAL_OCR_CHECKPOINT_INVALID")
            ocr_results[item.page_number] = item
    except TypeError as error:
        raise MaterialAnalysisFailure("MATERIAL_OCR_CHECKPOINT_INVALID") from error
    if ocr_page_numbers:
        remaining_ocr_pages = tuple(
            page_number
            for page_number in ocr_page_numbers
            if page_number not in ocr_results
        )
        try:
            ocr_results.update(
                {
                    item.page_number: item
                    for item in (ocr_pages or ocr_pdf_pages)(
                        raw,
                        page_numbers=remaining_ocr_pages,
                        expected_sha256=expected_sha256,
                        completed_page_callback=ocr_checkpoint_callback,
                    )
                }
            )
        except LocalOcrError as error:
            if error.code == "MATERIAL_ANALYSIS_PERSIST_FAILED":
                raise MaterialAnalysisFailure(
                    error.code, page_count=len(reader.pages)
                ) from error
            # The authoritative pypdf path remains available.  A missing or
            # rejected optional runtime must never be presented as OCR success.
            ocr_results.update(
                {
                    page_number: OcrPageResult(
                        page_number=page_number,
                        text="",
                        status="unavailable",
                        reason_code="OCR_UNAVAILABLE",
                        ocr_applied=False,
                        character_count=0,
                    )
                    for page_number in remaining_ocr_pages
                }
            )

    page_outputs: list[PageClassification] = []
    page_texts: list[tuple[int, str]] = []
    applied_ocr_pages: set[int] = set()
    for page_number, page, embedded_text, fragments in extracted_pages:
        ocr_result = ocr_results.get(page_number)
        effective_text = embedded_text
        if ocr_result is not None and ocr_result.ocr_applied:
            effective_text = _normalize_text(ocr_result.text)
        if ocr_result is not None and ocr_result.ocr_applied:
            applied_ocr_pages.add(page_number)
        page_texts.append((page_number, effective_text))
        page_outputs.append(
            _page_classification(
                page_number,
                page,
                effective_text,
                fragments,
                embedded_text=embedded_text,
                ocr_result=ocr_result,
            )
        )

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
    nonempty_pages = [(number, text) for number, text in page_texts if text]
    if any(page.text_character_count >= 40 for page in page_outputs):
        if nonempty_pages:
            candidates = _field_candidates(nonempty_pages)
            if applied_ocr_pages:
                # The database producer remains ``pypdf_heuristic`` by design:
                # it identifies the candidate rule set.  The confidence basis
                # records that the rule consumed validated local OCR text.
                candidates = tuple(
                    replace(
                        candidate,
                        confidence_basis=f"ocr_heuristic.{candidate.field_name}",
                    )
                    if candidate.page_number in applied_ocr_pages
                    else candidate
                    for candidate in candidates
                )
    suggested_kind, suggested_kind_confidence_ppm = _suggest_material_kind(
        nonempty_pages, page_outputs
    )
    return MaterialAnalysisResult(
        document_profile=profile,
        pages=tuple(page_outputs),
        candidates=candidates,
        suggested_kind=suggested_kind,
        suggested_kind_confidence_ppm=suggested_kind_confidence_ppm,
    )


__all__ = ("MaterialAnalysisFailure", "analyze_pdf")
