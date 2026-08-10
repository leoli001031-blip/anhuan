"""Strict in-memory native, PP-OCRv6 and OOXML evidence extraction.

No function accepts a filesystem path.  Callers hand in the already verified
registered descriptor and receive body-redacted leaf/location records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
import re
from typing import BinaryIO, Mapping
import xml.etree.ElementTree as ElementTree

from fixture_page_planner.planner import (
    _PACKAGE_REL_NS,
    _REL_NS,
    _SHEET_NS,
    _WORD_NS,
    _open_ooxml,
    _parse_docx,
    _parse_xlsx,
    _read_zip_member,
    _resolve_ooxml_target,
    _safe_xml,
    _strict_equal,
)

from ..f0f.native import _quiet_pypdf
from ..f0h.fixture_reader import RegisteredSource
from .contracts import F0IError, LeafInput, canonical_sha256
from .structures import (
    DocxLocation,
    GeometryEvidence,
    PageGeometryEvidence,
    XlsxLocation,
    docx_paragraph_location,
    docx_table_cell_location,
    native_geometry,
    ocr_bbox_to_ppm,
    page_geometry,
    pdf_table_status,
    structure_unit_sha256,
    xlsx_cell_location,
    xlsx_sheet_location,
)


_MAX_PDF_PAGES = 128
_MAX_PAGE_TEXT_CHARACTERS = 2_000_000
_CELL_REFERENCE = re.compile(r"^([A-Z]{1,3})([1-9][0-9]{0,6})$")


@dataclass(frozen=True, slots=True)
class LeafObservation:
    leaf: LeafInput
    geometry: GeometryEvidence | None = None
    docx_location: DocxLocation | None = None
    xlsx_location: XlsxLocation | None = None
    confidence_ppm: int | None = None
    xlsx_cell_type: str | None = None
    formula_observed: bool = False
    cached_value_observed: bool = False

    def __post_init__(self) -> None:
        locations = sum(
            item is not None
            for item in (self.geometry, self.docx_location, self.xlsx_location)
        )
        if not isinstance(self.leaf, LeafInput) or locations != 1:
            raise F0IError("CANONICAL_CONTRACT_INVALID")
        if self.confidence_ppm is not None and (
            isinstance(self.confidence_ppm, bool)
            or not isinstance(self.confidence_ppm, int)
            or not 0 <= self.confidence_ppm <= 1_000_000
        ):
            raise F0IError("CANONICAL_CONTRACT_INVALID")
        if self.geometry is None and self.confidence_ppm is not None:
            raise F0IError("CANONICAL_CONTRACT_INVALID")
        if self.xlsx_location is None and (
            self.xlsx_cell_type is not None
            or self.formula_observed
            or self.cached_value_observed
        ):
            raise F0IError("CANONICAL_CONTRACT_INVALID")

    @property
    def location_record(self) -> dict[str, object]:
        if self.geometry is not None:
            return self.geometry.to_record()
        if self.docx_location is not None:
            return self.docx_location.to_record()
        if self.xlsx_location is not None:
            return self.xlsx_location.to_record()
        raise F0IError("CANONICAL_CONTRACT_INVALID")


@dataclass(frozen=True, slots=True)
class UnitObservation:
    unit_kind: str
    unit_ordinal: int
    structure_unit_sha256: str | None
    source_output_sha256: str
    source_evidence_sha256: str
    leaves: tuple[LeafObservation, ...]
    page_geometry: PageGeometryEvidence | None = None
    image_width_px: int | None = None
    image_height_px: int | None = None
    render_width_px: int | None = None
    render_height_px: int | None = None
    render_origin: str | None = None
    renderer_sha256: str | None = None
    table_status: str = "UNRESOLVED"
    table_reason_code: str = "PDF_TABLE_MODEL_NOT_IN_SCOPE"
    structure_summary: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            self.unit_kind not in {
                "PDF_PAGE",
                "JPEG_IMAGE",
                "DOCX_SECTION",
                "XLSX_SHEET",
            }
            or isinstance(self.unit_ordinal, bool)
            or not isinstance(self.unit_ordinal, int)
            or self.unit_ordinal <= 0
            or not self.leaves
        ):
            raise F0IError("CANONICAL_CONTRACT_INVALID")
        for value in (self.source_output_sha256, self.source_evidence_sha256):
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise F0IError("CANONICAL_CONTRACT_INVALID")
        if self.structure_unit_sha256 is not None and re.fullmatch(
            r"[0-9a-f]{64}", self.structure_unit_sha256
        ) is None:
            raise F0IError("CANONICAL_CONTRACT_INVALID")


def extract_native_pdf_page(
    source: RegisteredSource,
    entry: Mapping[str, object],
    page: Mapping[str, object],
) -> UnitObservation:
    return extract_native_pdf_pages(source, entry, (page,))[0]


def extract_native_pdf_pages(
    source: RegisteredSource,
    entry: Mapping[str, object],
    pages: tuple[Mapping[str, object], ...],
) -> tuple[UnitObservation, ...]:
    if (
        not isinstance(source, RegisteredSource)
        or entry.get("type") != "PDF"
        or not isinstance(pages, tuple)
        or not pages
        or any(page.get("decision") != "NATIVE_CANDIDATE" for page in pages)
    ):
        raise F0IError("SOURCE_OBJECT_INVALID")
    expected_pages = _positive(entry.get("page_count"))
    duplicate = -1
    handle: BinaryIO | None = None
    try:
        source.reverify()
        duplicate = os.dup(source.fileno())
        handle = os.fdopen(duplicate, "rb", closefd=True)
        duplicate = -1
        from pypdf import PdfReader

        with _quiet_pypdf():
            reader = PdfReader(
                handle,
                strict=True,
                password=None,
                root_object_recovery_limit=10_000,
            )
            if reader.is_encrypted or not 1 <= len(reader.pages) <= _MAX_PDF_PAGES:
                raise F0IError("STRUCTURE_PARSE_FAILED")
            if len(reader.pages) != expected_pages:
                raise F0IError("SOURCE_OBJECT_CHANGED")
            observations: list[UnitObservation] = []
            seen: set[int] = set()
            for page in pages:
                page_no = _positive(page.get("page_no"))
                if page_no > len(reader.pages) or page_no in seen:
                    raise F0IError("SOURCE_OBJECT_CHANGED")
                seen.add(page_no)
                text = (
                    reader.pages[page_no - 1].extract_text(extraction_mode="plain")
                    or ""
                )
                observations.append(_native_observation(page, text))
                del text
        source.reverify()
        return tuple(observations)
    except F0IError:
        raise
    except Exception:
        raise F0IError("STRUCTURE_PARSE_FAILED") from None
    finally:
        if handle is not None:
            handle.close()
        if duplicate >= 0:
            os.close(duplicate)


def _native_observation(
    page: Mapping[str, object], text: str
) -> UnitObservation:
    page_no = _positive(page.get("page_no"))
    if len(text) > _MAX_PAGE_TEXT_CHARACTERS:
        raise F0IError("RESOURCE_LIMIT_EXCEEDED")
    encoded = text.encode("utf-8", errors="strict")
    native_characters = sum(
        not character.isspace() and not _ignored_native_character(character)
        for character in text
    )
    if (
        hashlib.sha256(encoded).hexdigest() != page.get("native_text_sha256")
        or native_characters != page.get("native_characters")
    ):
        raise F0IError("SOURCE_OBJECT_CHANGED")
    rotation = _rotation(page.get("rotation"))
    location = native_geometry(page_rotation=rotation)
    leaf = LeafObservation(
        leaf=LeafInput(
            text=text,
            block_kind="NATIVE_PAGE_TEXT",
            locator_kind=location.location_kind,
            locator_sha256=location.location_sha256,
        ),
        geometry=location,
    )
    geometry = page_geometry(
        media_box=page.get("media_box"),
        crop_box=page.get("crop_box"),
        rotation=rotation,
    )
    table = pdf_table_status()
    output_sha256 = hashlib.sha256(encoded).hexdigest()
    evidence_sha256 = canonical_sha256(
        {
            "geometry_sha256": geometry.geometry_sha256,
            "location_sha256": location.location_sha256,
            "output_sha256": output_sha256,
            "source_unit_id": page.get("page_id"),
        }
    )
    return UnitObservation(
        unit_kind="PDF_PAGE",
        unit_ordinal=page_no,
        structure_unit_sha256=None,
        source_output_sha256=output_sha256,
        source_evidence_sha256=evidence_sha256,
        leaves=(leaf,),
        page_geometry=geometry,
        table_status=table["table_status"],
        table_reason_code=table["table_reason_code"],
    )


def observation_from_ocr_result(
    entry: Mapping[str, object],
    page: Mapping[str, object],
    result: Mapping[str, object],
) -> UnitObservation:
    document_type = str(entry.get("type"))
    if (
        document_type not in {"PDF", "JPEG"}
        or page.get("decision") != "FULL_PAGE_OCR_REQUIRED"
        or result.get("document_type") != document_type
        or result.get("source_unit_id") != page.get("page_id")
    ):
        raise F0IError("REPLAY_MISMATCH")
    _validate_renderer(result, document_type)
    width = _positive(result.get("render_width_px"))
    height = _positive(result.get("render_height_px"))
    rotation = (
        None if document_type == "JPEG" else _rotation(page.get("rotation"))
    )
    raw_blocks = result.get("blocks")
    if not isinstance(raw_blocks, list) or len(raw_blocks) > 1024:
        raise F0IError("REPLAY_MISMATCH")
    leaves: list[LeafObservation] = []
    for index, raw in enumerate(raw_blocks):
        if not isinstance(raw, dict) or raw.get("index") != index:
            raise F0IError("REPLAY_MISMATCH")
        geometry = ocr_bbox_to_ppm(
            raw.get("bbox"),
            render_width_px=width,
            render_height_px=height,
            page_rotation=rotation,
        )
        confidence = raw.get("confidence_ppm")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, int)
            or not 0 <= confidence <= 1_000_000
            or not isinstance(raw.get("text"), str)
        ):
            raise F0IError("REPLAY_MISMATCH")
        leaves.append(
            LeafObservation(
                leaf=LeafInput(
                    text=str(raw["text"]),
                    block_kind="OCR_TEXT_BLOCK",
                    locator_kind=geometry.location_kind,
                    locator_sha256=geometry.location_sha256,
                    separator_after="\n" if index + 1 < len(raw_blocks) else "",
                ),
                geometry=geometry,
                confidence_ppm=confidence,
            )
        )
    if not leaves:
        from .structures import ocr_geometry_unavailable

        geometry = ocr_geometry_unavailable(page_rotation=rotation)
        leaves.append(
            LeafObservation(
                leaf=LeafInput(
                    text="",
                    block_kind="OCR_EMPTY_PAGE",
                    locator_kind=geometry.location_kind,
                    locator_sha256=geometry.location_sha256,
                ),
                geometry=geometry,
            )
        )
    page_record = (
        page_geometry(
            media_box=page.get("media_box"),
            crop_box=page.get("crop_box"),
            rotation=rotation,
        )
        if document_type == "PDF"
        else None
    )
    body_free = {key: value for key, value in result.items() if key != "blocks"}
    table = pdf_table_status()
    return UnitObservation(
        unit_kind="PDF_PAGE" if document_type == "PDF" else "JPEG_IMAGE",
        unit_ordinal=_positive(page.get("page_no")),
        structure_unit_sha256=None,
        source_output_sha256=str(result.get("ocr_text_sha256")),
        source_evidence_sha256=canonical_sha256(body_free),
        leaves=tuple(leaves),
        page_geometry=page_record,
        image_width_px=(
            _positive(page.get("width_px")) if document_type == "JPEG" else None
        ),
        image_height_px=(
            _positive(page.get("height_px")) if document_type == "JPEG" else None
        ),
        render_width_px=width,
        render_height_px=height,
        render_origin=str(result.get("render_origin")),
        renderer_sha256=canonical_sha256(result.get("renderer")),
        table_status=(
            table["table_status"] if document_type == "PDF" else "NOT_APPLICABLE"
        ),
        table_reason_code=(
            table["table_reason_code"] if document_type == "PDF" else "IMAGE_INPUT"
        ),
    )


def extract_docx_section(
    source: RegisteredSource,
    entry: Mapping[str, object],
    *,
    source_version_sha256: str,
    source_plan_sha256: str,
) -> UnitObservation:
    if not isinstance(source, RegisteredSource) or entry.get("type") != "DOCX":
        raise F0IError("SOURCE_OBJECT_INVALID")
    handle = _duplicate_handle(source)
    try:
        planned = _parse_docx(handle)
        expected = {
            "parse_status": entry.get("parse_status"),
            "structure_summary": entry.get("structure_summary"),
            "structure_anchors": entry.get("structure_anchors"),
        }
        if not _strict_equal(planned, expected):
            raise F0IError("SOURCE_OBJECT_CHANGED")
        archive = _open_ooxml(handle)
        try:
            root = _safe_xml(
                _read_zip_member(archive, "word/document.xml"),
                "DOCX_XML_INVALID",
            )
        finally:
            archive.close()
        body = root.find(f"{{{_WORD_NS}}}body")
        if body is None:
            raise F0IError("STRUCTURE_PARSE_FAILED")
        paragraph_tag = f"{{{_WORD_NS}}}p"
        table_tag = f"{{{_WORD_NS}}}tbl"
        row_tag = f"{{{_WORD_NS}}}tr"
        cell_tag = f"{{{_WORD_NS}}}tc"
        text_tag = f"{{{_WORD_NS}}}t"
        observed: list[tuple[str, DocxLocation]] = []
        paragraph_ordinal = 0
        table_ordinal = 0
        for block_ordinal, child in enumerate(list(body), start=1):
            if child.tag == paragraph_tag:
                paragraph_ordinal += 1
                location = docx_paragraph_location(
                    structure_ordinal=1,
                    block_ordinal=block_ordinal,
                    paragraph_ordinal=paragraph_ordinal,
                )
                observed.append((_xml_text(child, text_tag), location))
            elif child.tag == table_tag:
                table_ordinal += 1
                for row_ordinal, row in enumerate(child.iter(row_tag), start=1):
                    cells = [node for node in list(row) if node.tag == cell_tag]
                    for cell_ordinal, cell in enumerate(cells, start=1):
                        paragraph_ordinal += sum(
                            1 for _ in cell.iter(paragraph_tag)
                        )
                        location = docx_table_cell_location(
                            structure_ordinal=1,
                            block_ordinal=block_ordinal,
                            table_ordinal=table_ordinal,
                            row_ordinal=row_ordinal,
                            cell_ordinal=cell_ordinal,
                        )
                        observed.append((_xml_text(cell, text_tag), location))
        summary = dict(entry.get("structure_summary", {}))
        if (
            len(observed) != int(summary.get("paragraphs", -1))
            or table_ordinal != int(summary.get("tables", -1))
        ):
            raise F0IError("SOURCE_OBJECT_CHANGED")
        leaves = tuple(
            LeafObservation(
                leaf=LeafInput(
                    text=text,
                    block_kind=location.location_kind,
                    locator_kind=location.location_kind,
                    locator_sha256=location.location_sha256,
                    separator_after="\n" if index + 1 < len(observed) else "",
                ),
                docx_location=location,
            )
            for index, (text, location) in enumerate(observed)
        )
        anchor_sha256 = canonical_sha256(
            {
                "anchors": entry.get("structure_anchors"),
                "summary": entry.get("structure_summary"),
            }
        )
        unit_id = structure_unit_sha256(
            source_version_sha256=source_version_sha256,
            source_plan_sha256=source_plan_sha256,
            structure_anchor_sha256=anchor_sha256,
            unit_kind="DOCX_SECTION",
            unit_ordinal=1,
        )
        source.reverify()
        return UnitObservation(
            unit_kind="DOCX_SECTION",
            unit_ordinal=1,
            structure_unit_sha256=unit_id,
            source_output_sha256=anchor_sha256,
            source_evidence_sha256=canonical_sha256(
                [item.location_record for item in leaves]
            ),
            leaves=leaves,
            table_status="OBSERVED_DOCX_XML",
            table_reason_code="DOCX_XML_STRUCTURE",
            structure_summary={key: int(value) for key, value in summary.items()},
        )
    except F0IError:
        raise
    except Exception:
        raise F0IError("STRUCTURE_PARSE_FAILED") from None
    finally:
        handle.close()


def extract_xlsx_sheets(
    source: RegisteredSource,
    entry: Mapping[str, object],
    *,
    source_version_sha256: str,
    source_plan_sha256: str,
) -> tuple[UnitObservation, ...]:
    if not isinstance(source, RegisteredSource) or entry.get("type") != "XLSX":
        raise F0IError("SOURCE_OBJECT_INVALID")
    handle = _duplicate_handle(source)
    try:
        document_id = str(entry.get("document_id"))
        planned = _parse_xlsx(handle, document_id)
        expected = {
            "parse_status": entry.get("parse_status"),
            "structure_summary": entry.get("structure_summary"),
            "structure_anchors": entry.get("structure_anchors"),
        }
        if not _strict_equal(planned, expected):
            raise F0IError("SOURCE_OBJECT_CHANGED")
        archive = _open_ooxml(handle)
        try:
            workbook = _safe_xml(
                _read_zip_member(archive, "xl/workbook.xml"), "XLSX_XML_INVALID"
            )
            relationships = _safe_xml(
                _read_zip_member(
                    archive,
                    "xl/_rels/workbook.xml.rels",
                    limit=2 * 1024 * 1024,
                ),
                "XLSX_XML_INVALID",
            )
            relation_map: dict[str, tuple[str, str]] = {}
            for relation in relationships:
                relation_id = relation.attrib.get("Id", "")
                relation_type = relation.attrib.get("Type", "")
                target = relation.attrib.get("Target", "")
                if (
                    relation.tag != f"{{{_PACKAGE_REL_NS}}}Relationship"
                    or not relation_id
                    or relation_id in relation_map
                    or relation.attrib.get("TargetMode", "").casefold() == "external"
                ):
                    raise F0IError("STRUCTURE_PARSE_FAILED")
                relation_map[relation_id] = (
                    relation_type,
                    _resolve_ooxml_target("xl/workbook.xml", target),
                )
            shared = _shared_strings(archive)
            sheets_parent = workbook.find(f"{{{_SHEET_NS}}}sheets")
            anchors = entry.get("structure_anchors")
            if sheets_parent is None or not isinstance(anchors, list):
                raise F0IError("STRUCTURE_PARSE_FAILED")
            units: list[UnitObservation] = []
            names = set(archive.namelist())
            for sheet_ordinal, sheet in enumerate(list(sheets_parent), start=1):
                if sheet_ordinal > len(anchors) or not isinstance(anchors[sheet_ordinal - 1], dict):
                    raise F0IError("SOURCE_OBJECT_CHANGED")
                anchor = anchors[sheet_ordinal - 1]
                relation_id = sheet.attrib.get(f"{{{_REL_NS}}}id", "")
                relation_type, target = relation_map.get(relation_id, ("", ""))
                if not relation_type.endswith("/worksheet") or target not in names:
                    raise F0IError("STRUCTURE_PARSE_FAILED")
                sheet_data = _read_zip_member(archive, target)
                if hashlib.sha256(sheet_data).hexdigest() != anchor.get("content_sha256"):
                    raise F0IError("SOURCE_OBJECT_CHANGED")
                sheet_root = _safe_xml(sheet_data, "XLSX_XML_INVALID")
                cell_tag = f"{{{_SHEET_NS}}}c"
                formula_tag = f"{{{_SHEET_NS}}}f"
                value_tag = f"{{{_SHEET_NS}}}v"
                cells = list(sheet_root.iter(cell_tag))
                observations: list[LeafObservation] = []
                for structure_ordinal, cell in enumerate(cells, start=1):
                    row_ordinal, column_ordinal = _cell_coordinates(
                        cell.attrib.get("r", "")
                    )
                    cell_type = cell.attrib.get("t", "")
                    formula = cell.find(formula_tag)
                    cached = cell.find(value_tag)
                    text = _cell_text(cell, cell_type, shared, formula_tag, value_tag)
                    location = xlsx_cell_location(
                        structure_ordinal=structure_ordinal,
                        sheet_ordinal=sheet_ordinal,
                        row_ordinal=row_ordinal,
                        column_ordinal=column_ordinal,
                    )
                    observations.append(
                        LeafObservation(
                            leaf=LeafInput(
                                text=text,
                                block_kind="XLSX_CELL",
                                locator_kind=location.location_kind,
                                locator_sha256=location.location_sha256,
                                separator_after="\n" if structure_ordinal < len(cells) else "",
                            ),
                            xlsx_location=location,
                            xlsx_cell_type=(cell_type or "UNSPECIFIED"),
                            formula_observed=formula is not None,
                            cached_value_observed=cached is not None,
                        )
                    )
                if not observations:
                    location = xlsx_sheet_location(
                        structure_ordinal=1,
                        sheet_ordinal=sheet_ordinal,
                    )
                    observations.append(
                        LeafObservation(
                            leaf=LeafInput(
                                text="",
                                block_kind="XLSX_SHEET",
                                locator_kind=location.location_kind,
                                locator_sha256=location.location_sha256,
                            ),
                            xlsx_location=location,
                        )
                    )
                if (
                    len(cells) != int(anchor.get("cell_count", -1))
                    or sum(item.formula_observed for item in observations)
                    != int(anchor.get("formula_count", -1))
                    or sum(item.cached_value_observed for item in observations)
                    != int(anchor.get("value_cell_count", -1))
                ):
                    raise F0IError("SOURCE_OBJECT_CHANGED")
                anchor_sha256 = canonical_sha256(anchor)
                unit_id = structure_unit_sha256(
                    source_version_sha256=source_version_sha256,
                    source_plan_sha256=source_plan_sha256,
                    structure_anchor_sha256=anchor_sha256,
                    unit_kind="XLSX_SHEET",
                    unit_ordinal=sheet_ordinal,
                )
                units.append(
                    UnitObservation(
                        unit_kind="XLSX_SHEET",
                        unit_ordinal=sheet_ordinal,
                        structure_unit_sha256=unit_id,
                        source_output_sha256=str(anchor["content_sha256"]),
                        source_evidence_sha256=canonical_sha256(
                            [item.location_record for item in observations]
                        ),
                        leaves=tuple(observations),
                        table_status="OBSERVED_XLSX_CELL_XML",
                        table_reason_code="XLSX_CELL_XML_STRUCTURE",
                        structure_summary={
                            "cells": len(cells),
                            "formulas": sum(item.formula_observed for item in observations),
                            "value_cells": sum(
                                item.cached_value_observed for item in observations
                            ),
                        },
                    )
                )
            source.reverify()
            return tuple(units)
        finally:
            archive.close()
    except F0IError:
        raise
    except Exception:
        raise F0IError("STRUCTURE_PARSE_FAILED") from None
    finally:
        handle.close()


def _duplicate_handle(source: RegisteredSource) -> BinaryIO:
    try:
        source.reverify()
        descriptor = os.dup(source.fileno())
        return os.fdopen(descriptor, "rb", closefd=True)
    except F0IError:
        raise
    except OSError:
        raise F0IError("SOURCE_OBJECT_CHANGED") from None


def _xml_text(element: ElementTree.Element, text_tag: str) -> str:
    text = "".join(node.text or "" for node in element.iter(text_tag))
    if len(text) > _MAX_PAGE_TEXT_CHARACTERS:
        raise F0IError("RESOURCE_LIMIT_EXCEEDED")
    return text


def _shared_strings(archive: object) -> tuple[str, ...]:
    names = set(archive.namelist())  # type: ignore[attr-defined]
    if "xl/sharedStrings.xml" not in names:
        return ()
    root = _safe_xml(
        _read_zip_member(archive, "xl/sharedStrings.xml"),  # type: ignore[arg-type]
        "XLSX_XML_INVALID",
    )
    item_tag = f"{{{_SHEET_NS}}}si"
    text_tag = f"{{{_SHEET_NS}}}t"
    return tuple(
        "".join(node.text or "" for node in item.iter(text_tag))
        for item in root.iter(item_tag)
    )


def _cell_text(
    cell: ElementTree.Element,
    cell_type: str,
    shared: tuple[str, ...],
    formula_tag: str,
    value_tag: str,
) -> str:
    formula = cell.find(formula_tag)
    value = cell.find(value_tag)
    raw_value = value.text or "" if value is not None else ""
    if cell_type == "s":
        if value is None or not raw_value.isascii() or not raw_value.isdigit():
            raise F0IError("STRUCTURE_PARSE_FAILED")
        index = int(raw_value)
        if not 0 <= index < len(shared):
            raise F0IError("STRUCTURE_PARSE_FAILED")
        observed_value = shared[index]
    elif cell_type == "inlineStr":
        observed_value = "".join(
            node.text or ""
            for node in cell.iter(f"{{{_SHEET_NS}}}t")
        )
    else:
        observed_value = raw_value
    if formula is None:
        return observed_value
    literal = formula.text or ""
    return "=" + literal + ("\n" + observed_value if value is not None else "")


def _cell_coordinates(reference: str) -> tuple[int, int]:
    match = _CELL_REFERENCE.fullmatch(reference)
    if match is None:
        raise F0IError("STRUCTURE_LOCATION_INVALID")
    column = 0
    for character in match.group(1):
        column = column * 26 + ord(character) - ord("A") + 1
    row = int(match.group(2))
    return row, column


def _validate_renderer(result: Mapping[str, object], document_type: str) -> None:
    renderer = result.get("renderer")
    if document_type == "PDF":
        expected = {
            "name": "pypdfium2",
            "version": "5.12.1",
            "pdfium_version": "152.0.7947.0",
        }
        origin = "PDFIUM_250_DPI"
        dpi: object = 250
    else:
        expected = {"name": "opencv-imdecode", "version": "5.0.0.93"}
        origin = "JPEG_DECODED_SOURCE_PIXELS"
        dpi = None
    if (
        renderer != expected
        or result.get("render_origin") != origin
        or result.get("render_dpi") != dpi
    ):
        raise F0IError("REPLAY_MISMATCH")


def _positive(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise F0IError("CANONICAL_CONTRACT_INVALID")
    return value


def _rotation(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in {0, 90, 180, 270}:
        raise F0IError("GEOMETRY_INVALID")
    return value


def _ignored_native_character(character: str) -> bool:
    import unicodedata

    return unicodedata.category(character) in {"Cc", "Cf", "Co", "Cs", "Cn"}


__all__ = (
    "LeafObservation",
    "UnitObservation",
    "extract_docx_section",
    "extract_native_pdf_page",
    "extract_native_pdf_pages",
    "extract_xlsx_sheets",
    "observation_from_ocr_result",
)
