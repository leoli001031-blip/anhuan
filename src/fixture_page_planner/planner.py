from __future__ import annotations

import errno
import hashlib
import html
import importlib.metadata
import json
import logging
import math
import os
import platform
import stat
import unicodedata
import warnings
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterator
from xml.etree import ElementTree

from fixture_gate import ENVIRONMENT_DEMO_V01, ValidationFailure
from fixture_gate.validator import (
    FixtureIdentity,
    ManifestEntry,
    _open_entry,
    _open_path_without_symlinks,
    _parse_manifest,
)
from fixture_router import RouteFailure, build_route_plan
from fixture_router.router import (
    PROJECT_ROOT,
    _detect_type,
    _document_id,
    _read_evidence,
    _selected_entries,
    _stat_identity,
)
RULE_VERSION = "native-page-rule/v1"
SCHEMA_VERSION = "fixture-native-page-plan/v1"
REGISTERED_ARTIFACT_ROOT = PROJECT_ROOT / "artifacts/fixture-native-plan/v0.1"
REGISTERED_SMOKE_ROUTE_PLAN = (
    PROJECT_ROOT / "artifacts/fixture-routing/v0.1/smoke-plan.json"
)
REGISTERED_FULL_ROUTE_PLAN = (
    PROJECT_ROOT / "artifacts/fixture-routing/v0.1/route-plan.json"
)

_EXPECTED_PYPDF_VERSION = "6.14.2"
_EXPECTED_PYPDF_LICENSE = "BSD-3-Clause"
_MIN_NATIVE_CHARACTERS = 20
_MAX_BAD_CHARACTER_PPM = 20_000
_MAX_INPUT_BYTES = 128 * 1024 * 1024
_MAX_ROUTE_PLAN_BYTES = 2 * 1024 * 1024
_MAX_PDF_PAGES = 2_000
_MAX_PAGE_TEXT_CHARACTERS = 5_000_000
_MAX_XML_BYTES = 64 * 1024 * 1024
_MAX_ZIP_MEMBERS = 2_000
_MAX_ZIP_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
_MAX_IMAGE_PIXELS = 100_000_000
_OUTPUT_NAMES = frozenset(
    {"smoke-plan.json", "full-plan.json", "status.html", "sbom.json"}
)
_STAT_FIELDS = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")

_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

class PlannerFailure(Exception):
    """A fail-closed planner error with path-free public fields."""

    def __init__(
        self,
        code: str,
        *,
        group: str | None = None,
        line: int | None = None,
        document_id: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.group = group
        self.line = line
        self.document_id = document_id

    def public_record(self) -> dict[str, object]:
        record: dict[str, object] = {"code": self.code}
        if self.group is not None:
            record["group"] = self.group
        if self.line is not None:
            record["line"] = self.line
        if self.document_id is not None:
            record["document_id"] = self.document_id
        return record


def _entry_failure(
    entry: ManifestEntry,
    code: str,
    *,
    document_id: str | None = None,
) -> PlannerFailure:
    return PlannerFailure(
        code,
        group=entry.group,
        line=entry.line,
        document_id=document_id,
    )


def _check_runtime() -> tuple[str, str]:
    try:
        version = importlib.metadata.version("pypdf")
        license_expression = (
            importlib.metadata.metadata("pypdf").get("License-Expression") or ""
        )
    except importlib.metadata.PackageNotFoundError as error:
        raise PlannerFailure("DEPENDENCY_MISMATCH") from error
    if (
        version != _EXPECTED_PYPDF_VERSION
        or license_expression != _EXPECTED_PYPDF_LICENSE
    ):
        raise PlannerFailure("DEPENDENCY_MISMATCH")
    return version, license_expression


def _json_bytes(value: dict[str, object]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _read_registered_bytes(path: Path, *, limit: int) -> bytes:
    try:
        descriptor = _open_path_without_symlinks(
            path,
            final_flags=os.O_RDONLY | getattr(os, "O_NONBLOCK", 0),
            unavailable_code="REGISTERED_INPUT_UNAVAILABLE",
            symlink_code="REGISTERED_INPUT_UNAVAILABLE",
        )
    except ValidationFailure as error:
        raise PlannerFailure("REGISTERED_INPUT_UNAVAILABLE") from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > limit
        ):
            raise PlannerFailure("REGISTERED_INPUT_UNAVAILABLE")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, min(1024 * 1024, limit + 1 - total)):
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise PlannerFailure("REGISTERED_INPUT_UNAVAILABLE")
        after = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(after) or after.st_nlink != 1:
            raise PlannerFailure("REGISTERED_INPUT_CHANGED")
        return b"".join(chunks)
    except PlannerFailure:
        raise
    except OSError as error:
        raise PlannerFailure("REGISTERED_INPUT_UNAVAILABLE") from error
    finally:
        os.close(descriptor)


@contextmanager
def _quiet_pypdf() -> Iterator[None]:
    logger = logging.getLogger("pypdf")
    old_disabled = logger.disabled
    old_handlers = list(logger.handlers)
    old_propagate = logger.propagate
    logger.disabled = True
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            yield
    finally:
        logger.disabled = old_disabled
        logger.handlers = old_handlers
        logger.propagate = old_propagate


def _fixed_decimal(value: object) -> str:
    numeric = float(value)
    if not math.isfinite(numeric) or abs(numeric) > 1_000_000:
        raise ValueError
    rendered = f"{numeric:.3f}"
    if rendered == "-0.000":
        return "0.000"
    return rendered


def _box_record(box: object) -> tuple[dict[str, str], bool]:
    try:
        left = float(box.left)
        bottom = float(box.bottom)
        right = float(box.right)
        top = float(box.top)
        record = {
            "left": _fixed_decimal(left),
            "bottom": _fixed_decimal(bottom),
            "right": _fixed_decimal(right),
            "top": _fixed_decimal(top),
        }
        abnormal = right <= left or top <= bottom
    except (AttributeError, TypeError, ValueError, OverflowError):
        record = {"left": "0.000", "bottom": "0.000", "right": "0.000", "top": "0.000"}
        abnormal = True
    return record, abnormal


def _text_metrics(text: str) -> tuple[int, int, str]:
    if len(text) > _MAX_PAGE_TEXT_CHARACTERS:
        raise PlannerFailure("PAGE_TEXT_LIMIT_EXCEEDED")
    native_characters = 0
    bad_characters = 0
    for character in text:
        category = unicodedata.category(character)
        invalid_format = category in {"Cc", "Cf", "Co", "Cs", "Cn"}
        if not character.isspace() and not invalid_format:
            native_characters += 1
        if character == "\ufffd" or (
            invalid_format and character not in "\t\n\r"
        ):
            bad_characters += 1
    bad_character_ppm = (
        0 if not text else (bad_characters * 1_000_000) // len(text)
    )
    return (
        native_characters,
        bad_character_ppm,
        hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _classify_page(
    *,
    native_characters: int,
    bad_character_ppm: int,
    geometry_abnormal: bool,
    hidden_text: bool = False,
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if hidden_text:
        reasons.append("HIDDEN_NATIVE_TEXT")
    if geometry_abnormal:
        reasons.append("GEOMETRY_ABNORMAL")
    if bad_character_ppm > _MAX_BAD_CHARACTER_PPM:
        reasons.append("BAD_NATIVE_TEXT_RATIO")
    if reasons:
        return "MANUAL_REVIEW_REQUIRED", reasons
    if native_characters < _MIN_NATIVE_CHARACTERS:
        return "FULL_PAGE_OCR_REQUIRED", ["LOW_NATIVE_TEXT"]
    return "NATIVE_CANDIDATE", ["NATIVE_TEXT_THRESHOLD_MET"]


def _page_id(document_id: str, page_no: int) -> str:
    material = "\0".join((RULE_VERSION, document_id, str(page_no)))
    return hashlib.sha256(material.encode("ascii")).hexdigest()


def _parse_pdf(handle: BinaryIO, document_id: str) -> dict[str, object]:
    try:
        from pypdf import PdfReader

        handle.seek(0)
        with _quiet_pypdf():
            reader = PdfReader(
                handle,
                strict=True,
                password=None,
                root_object_recovery_limit=10_000,
            )
            if reader.is_encrypted:
                raise PlannerFailure("ENCRYPTED_INPUT")
            page_count = len(reader.pages)
            if page_count < 1 or page_count > _MAX_PDF_PAGES:
                raise PlannerFailure("PDF_PAGE_LIMIT_EXCEEDED")
            pages: list[dict[str, object]] = []
            for page_index, page in enumerate(reader.pages, start=1):
                media_box, media_abnormal = _box_record(page.mediabox)
                crop_box, crop_abnormal = _box_record(page.cropbox)
                raw_rotation = page.get("/Rotate", 0)
                try:
                    rotation = int(raw_rotation or 0)
                    rotation_abnormal = rotation not in {0, 90, 180, 270}
                except (TypeError, ValueError, OverflowError):
                    rotation = 0
                    rotation_abnormal = True
                visibility = {
                    "render_mode": 0,
                    "render_stack": [],
                    "inside": 0,
                    "outside": 0,
                    "hidden": 0,
                    "abnormal": False,
                }

                crop_left = float(page.cropbox.left)
                crop_bottom = float(page.cropbox.bottom)
                crop_right = float(page.cropbox.right)
                crop_top = float(page.cropbox.top)

                def visit_operand(
                    operator: bytes,
                    operands: list[object],
                    _cm: list[float],
                    _tm: list[float],
                ) -> None:
                    if (
                        operator in {b"Tj", b"TJ", b"'", b'"'}
                        and visibility["render_mode"] in {3, 7}
                    ):
                        visibility["hidden"] = 1
                    if operator == b"q":
                        visibility["render_stack"].append(visibility["render_mode"])
                    elif operator == b"Q":
                        if visibility["render_stack"]:
                            visibility["render_mode"] = visibility["render_stack"].pop()
                        else:
                            visibility["abnormal"] = True
                    elif operator == b"Tr" and operands:
                        try:
                            mode = int(operands[0])
                        except (TypeError, ValueError, OverflowError):
                            visibility["abnormal"] = True
                            return
                        if mode not in range(8):
                            visibility["abnormal"] = True
                        else:
                            visibility["render_mode"] = mode

                def visit_text(
                    chunk: str,
                    cm: list[float],
                    tm: list[float],
                    _font: dict[str, object] | None,
                    _font_size: float,
                ) -> None:
                    count = sum(1 for character in chunk if not character.isspace())
                    if not count:
                        return
                    if visibility["render_mode"] in {3, 7}:
                        visibility["hidden"] += count
                        return
                    try:
                        x = float(tm[4] * cm[0] + tm[5] * cm[2] + cm[4])
                        y = float(tm[4] * cm[1] + tm[5] * cm[3] + cm[5])
                    except (IndexError, TypeError, ValueError, OverflowError):
                        visibility["abnormal"] = True
                        return
                    key = (
                        "inside"
                        if crop_left <= x <= crop_right and crop_bottom <= y <= crop_top
                        else "outside"
                    )
                    visibility[key] += count

                text = page.extract_text(
                    extraction_mode="plain",
                    visitor_operand_before=visit_operand,
                    visitor_text=visit_text,
                ) or ""
                native_characters, bad_character_ppm, text_digest = _text_metrics(text)
                if visibility["outside"] and not visibility["inside"]:
                    native_characters = 0
                decision, reasons = _classify_page(
                    native_characters=native_characters,
                    bad_character_ppm=bad_character_ppm,
                    geometry_abnormal=(
                        media_abnormal
                        or crop_abnormal
                        or rotation_abnormal
                        or bool(visibility["abnormal"])
                    ),
                    hidden_text=bool(visibility["hidden"]),
                )
                pages.append(
                    {
                        "page_id": _page_id(document_id, page_index),
                        "page_no": page_index,
                        "media_box": media_box,
                        "crop_box": crop_box,
                        "rotation": rotation,
                        "native_characters": native_characters,
                        "bad_character_ppm": bad_character_ppm,
                        "native_text_sha256": text_digest,
                        "decision": decision,
                        "reason_codes": reasons,
                    }
                )
                del text
    except PlannerFailure:
        raise
    except Exception as error:
        raise PlannerFailure("PDF_PARSE_FAILED") from error
    finally:
        handle.seek(0)
    return {"parse_status": "NATIVE_PROBE_COMPLETE", "page_count": page_count, "pages": pages}


def _read_zip_member(
    archive: zipfile.ZipFile,
    name: str,
    *,
    limit: int = _MAX_XML_BYTES,
) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as error:
        raise PlannerFailure("OOXML_STRUCTURE_MISSING") from error
    if info.is_dir() or info.file_size > limit:
        raise PlannerFailure("OOXML_RESOURCE_LIMIT_EXCEEDED")
    if info.file_size > max(1, info.compress_size) * 200:
        raise PlannerFailure("OOXML_RESOURCE_LIMIT_EXCEEDED")
    try:
        data = archive.read(info)
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as error:
        raise PlannerFailure("OOXML_PARSE_FAILED") from error
    if len(data) != info.file_size:
        raise PlannerFailure("OOXML_PARSE_FAILED")
    return data


def _safe_xml(data: bytes, code: str) -> ElementTree.Element:
    lowered = data.lower()
    if b"\x00" in data or b"<!doctype" in lowered or b"<!entity" in lowered:
        raise PlannerFailure(code)
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise PlannerFailure(code) from error


def _open_ooxml(handle: BinaryIO) -> zipfile.ZipFile:
    archive: zipfile.ZipFile | None = None
    try:
        handle.seek(0)
        archive = zipfile.ZipFile(handle, mode="r")
        infos = archive.infolist()
        if not infos or len(infos) > _MAX_ZIP_MEMBERS:
            raise PlannerFailure("OOXML_RESOURCE_LIMIT_EXCEEDED")
        total = sum(info.file_size for info in infos)
        if total > _MAX_ZIP_UNCOMPRESSED_BYTES:
            raise PlannerFailure("OOXML_RESOURCE_LIMIT_EXCEEDED")
        return archive
    except PlannerFailure:
        if archive is not None:
            archive.close()
        raise
    except (OSError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        if archive is not None:
            archive.close()
        raise PlannerFailure("OOXML_PARSE_FAILED") from error


def _xml_text_digest(element: ElementTree.Element, text_tag: str) -> tuple[int, str]:
    text = "".join(node.text or "" for node in element.iter(text_tag))
    if len(text) > _MAX_PAGE_TEXT_CHARACTERS:
        raise PlannerFailure("OOXML_RESOURCE_LIMIT_EXCEEDED")
    count = sum(1 for character in text if not character.isspace())
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    del text
    return count, digest


def _parse_docx(handle: BinaryIO) -> dict[str, object]:
    archive = _open_ooxml(handle)
    try:
        document_data = _read_zip_member(archive, "word/document.xml")
        root = _safe_xml(document_data, "DOCX_XML_INVALID")
        body = root.find(f"{{{_WORD_NS}}}body")
        if body is None:
            raise PlannerFailure("DOCX_STRUCTURE_MISSING")
        paragraph_tag = f"{{{_WORD_NS}}}p"
        table_tag = f"{{{_WORD_NS}}}tbl"
        row_tag = f"{{{_WORD_NS}}}tr"
        cell_tag = f"{{{_WORD_NS}}}tc"
        text_tag = f"{{{_WORD_NS}}}t"
        paragraphs = sum(1 for _ in root.iter(paragraph_tag))
        tables = sum(1 for _ in root.iter(table_tag))
        rows = sum(1 for _ in root.iter(row_tag))
        cells = sum(1 for _ in root.iter(cell_tag))
        ordered_blocks: list[dict[str, object]] = []
        for index, child in enumerate(list(body), start=1):
            if child.tag not in {paragraph_tag, table_tag}:
                continue
            count, digest = _xml_text_digest(child, text_tag)
            block: dict[str, object] = {
                "block_index": index,
                "kind": "PARAGRAPH" if child.tag == paragraph_tag else "TABLE",
                "native_characters": count,
                "native_text_sha256": digest,
            }
            if child.tag == table_tag:
                block["rows"] = sum(1 for _ in child.iter(row_tag))
                block["cells"] = sum(1 for _ in child.iter(cell_tag))
            ordered_blocks.append(block)
        return {
            "parse_status": "NATIVE_STRUCTURE_PROBE_COMPLETE",
            "structure_summary": {
                "paragraphs": paragraphs,
                "tables": tables,
                "rows": rows,
                "cells": cells,
                "ordered_blocks": len(ordered_blocks),
            },
            "structure_anchors": ordered_blocks,
        }
    finally:
        archive.close()
        handle.seek(0)


def _resolve_ooxml_target(source_part: str, target: str) -> str:
    if not target or "\\" in target or ":" in target.split("/", 1)[0]:
        raise PlannerFailure("XLSX_RELATIONSHIP_INVALID")
    if target.startswith("/"):
        parts: list[str] = []
        target_parts = target[1:].split("/")
    else:
        parts = list(PurePosixPath(source_part).parent.parts)
        target_parts = target.split("/")
    for part in target_parts:
        if part in {"", "."}:
            raise PlannerFailure("XLSX_RELATIONSHIP_INVALID")
        if part == "..":
            if not parts:
                raise PlannerFailure("XLSX_RELATIONSHIP_INVALID")
            parts.pop()
        else:
            parts.append(part)
    return PurePosixPath(*parts).as_posix()


def _parse_xlsx(handle: BinaryIO, document_id: str) -> dict[str, object]:
    archive = _open_ooxml(handle)
    try:
        workbook_data = _read_zip_member(archive, "xl/workbook.xml")
        relationships_data = _read_zip_member(
            archive, "xl/_rels/workbook.xml.rels", limit=2 * 1024 * 1024
        )
        workbook = _safe_xml(workbook_data, "XLSX_XML_INVALID")
        relationships = _safe_xml(relationships_data, "XLSX_XML_INVALID")
        relation_map: dict[str, tuple[str, str]] = {}
        for relation in relationships:
            if relation.tag != f"{{{_PACKAGE_REL_NS}}}Relationship":
                raise PlannerFailure("XLSX_RELATIONSHIP_INVALID")
            relation_id = relation.attrib.get("Id", "")
            relation_type = relation.attrib.get("Type", "")
            target = relation.attrib.get("Target", "")
            if (
                not relation_id
                or not relation_type
                or relation_id in relation_map
                or relation.attrib.get("TargetMode", "").casefold() == "external"
            ):
                raise PlannerFailure("XLSX_RELATIONSHIP_INVALID")
            relation_map[relation_id] = (
                relation_type,
                _resolve_ooxml_target("xl/workbook.xml", target),
            )
        sheets_parent = workbook.find(f"{{{_SHEET_NS}}}sheets")
        if sheets_parent is None:
            raise PlannerFailure("XLSX_STRUCTURE_MISSING")
        sheet_records: list[dict[str, object]] = []
        total_cells = 0
        total_formulas = 0
        total_value_cells = 0
        total_formula_cached_values = 0
        names = set(archive.namelist())
        for sheet_index, sheet in enumerate(list(sheets_parent), start=1):
            if sheet.tag != f"{{{_SHEET_NS}}}sheet":
                raise PlannerFailure("XLSX_STRUCTURE_MISSING")
            relation_id = sheet.attrib.get(f"{{{_REL_NS}}}id", "")
            relation_type, target = relation_map.get(relation_id, ("", ""))
            if not relation_type.endswith("/worksheet") or target not in names:
                raise PlannerFailure("XLSX_RELATIONSHIP_INVALID")
            sheet_data = _read_zip_member(archive, target)
            sheet_root = _safe_xml(sheet_data, "XLSX_XML_INVALID")
            cell_tag = f"{{{_SHEET_NS}}}c"
            formula_tag = f"{{{_SHEET_NS}}}f"
            value_tag = f"{{{_SHEET_NS}}}v"
            cells = list(sheet_root.iter(cell_tag))
            formula_count = sum(
                1 for cell in cells if cell.find(formula_tag) is not None
            )
            value_cell_count = sum(
                1 for cell in cells if cell.find(value_tag) is not None
            )
            formula_cached_value_count = sum(
                1
                for cell in cells
                if cell.find(formula_tag) is not None
                and cell.find(value_tag) is not None
            )
            total_cells += len(cells)
            total_formulas += formula_count
            total_value_cells += value_cell_count
            total_formula_cached_values += formula_cached_value_count
            sheet_records.append(
                {
                    "sheet_index": sheet_index,
                    "sheet_id": hashlib.sha256(
                        f"{document_id}\0{sheet_index}\0{target}".encode("utf-8")
                    ).hexdigest(),
                    "cell_count": len(cells),
                    "formula_count": formula_count,
                    "value_cell_count": value_cell_count,
                    "formula_cached_value_count": formula_cached_value_count,
                    "content_sha256": hashlib.sha256(sheet_data).hexdigest(),
                }
            )
        return {
            "parse_status": "NATIVE_STRUCTURE_PROBE_COMPLETE",
            "structure_summary": {
                "sheets": len(sheet_records),
                "cells": total_cells,
                "formulas": total_formulas,
                "value_cells": total_value_cells,
                "formula_cached_values": total_formula_cached_values,
            },
            "structure_anchors": sheet_records,
        }
    finally:
        archive.close()
        handle.seek(0)


def _jpeg_dimensions(handle: BinaryIO) -> tuple[int, int]:
    handle.seek(0)
    data = handle.read(_MAX_INPUT_BYTES + 1)
    handle.seek(0)
    if len(data) > _MAX_INPUT_BYTES or not data.startswith(b"\xff\xd8"):
        raise PlannerFailure("JPEG_PARSE_FAILED")
    index = 2
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while index < len(data):
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            break
        marker = data[index]
        index += 1
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > len(data):
            break
        segment_length = int.from_bytes(data[index : index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            break
        if marker in sof_markers:
            if segment_length < 7:
                break
            height = int.from_bytes(data[index + 3 : index + 5], "big")
            width = int.from_bytes(data[index + 5 : index + 7], "big")
            if (
                width < 1
                or height < 1
                or width * height > _MAX_IMAGE_PIXELS
            ):
                raise PlannerFailure("JPEG_RESOURCE_LIMIT_EXCEEDED")
            return width, height
        if marker == 0xDA:
            break
        index += segment_length
    raise PlannerFailure("JPEG_PARSE_FAILED")


def _assert_route_entry(
    actual: dict[str, object], expected: dict[str, object]
) -> None:
    keys = (
        "group",
        "line",
        "document_id",
        "type",
        "route",
        "corpus_role",
        "enterprise_fact_allowed",
        "current_regulation_allowed",
        "search_publish_allowed",
    )
    if any(actual.get(key) != expected.get(key) for key in keys):
        raise PlannerFailure("ROUTE_ENTRY_MISMATCH")


def _plan_entry(
    root_descriptor: int,
    entry: ManifestEntry,
    route_entry: dict[str, object],
    *,
    fixture_set_id: str,
    fixture_version: str,
) -> dict[str, object]:
    document_id: str | None = None
    try:
        descriptor = _open_entry(root_descriptor, entry)
    except ValidationFailure as error:
        raise _entry_failure(entry, error.code) from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > _MAX_INPUT_BYTES
        ):
            raise _entry_failure(entry, "UNSAFE_SOURCE_ENTRY")
        try:
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                evidence = _read_evidence(handle)
                if evidence.sha256 != entry.expected_sha256:
                    raise _entry_failure(entry, "HASH_MISMATCH")
                if evidence.size_bytes != before.st_size:
                    raise _entry_failure(entry, "SOURCE_CHANGED_DURING_PLAN")
                document_id = _document_id(
                    fixture_set_id, fixture_version, entry, evidence.sha256
                )
                try:
                    document_type = _detect_type(handle, evidence, entry)
                except RouteFailure as error:
                    raise _entry_failure(
                        entry, error.code, document_id=document_id
                    ) from error
                expected_type = str(route_entry.get("type"))
                expected_route = str(route_entry.get("route"))
                candidate = {
                    "group": entry.group,
                    "line": entry.line,
                    "document_id": document_id,
                    "type": document_type,
                    "route": expected_route,
                    "corpus_role": route_entry.get("corpus_role"),
                    "enterprise_fact_allowed": route_entry.get("enterprise_fact_allowed"),
                    "current_regulation_allowed": route_entry.get("current_regulation_allowed"),
                    "search_publish_allowed": route_entry.get("search_publish_allowed"),
                }
                _assert_route_entry(candidate, route_entry)
                if document_type != expected_type:
                    raise _entry_failure(
                        entry, "ROUTE_ENTRY_MISMATCH", document_id=document_id
                    )
                if document_type == "PDF":
                    parsed = _parse_pdf(handle, document_id)
                elif document_type == "DOCX":
                    parsed = _parse_docx(handle)
                elif document_type == "XLSX":
                    parsed = _parse_xlsx(handle, document_id)
                elif document_type == "JPEG":
                    width, height = _jpeg_dimensions(handle)
                    parsed = {
                        "parse_status": "OCR_CANDIDATE_PLANNED",
                        "page_count": 1,
                        "pages": [
                            {
                                "page_id": _page_id(document_id, 1),
                                "page_no": 1,
                                "width_px": width,
                                "height_px": height,
                                "decision": "FULL_PAGE_OCR_REQUIRED",
                                "reason_codes": ["IMAGE_INPUT"],
                            }
                        ],
                    }
                elif document_type == "DOC":
                    parsed = {"parse_status": "DEFERRED_CONVERSION_REQUIRED"}
                else:
                    raise _entry_failure(
                        entry, "UNSUPPORTED_ROUTE_TYPE", document_id=document_id
                    )
        except PlannerFailure:
            raise
        except OSError as error:
            raise _entry_failure(
                entry, "SOURCE_UNREADABLE", document_id=document_id
            ) from error
        after = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(after) or after.st_nlink != 1:
            raise _entry_failure(
                entry, "SOURCE_CHANGED_DURING_PLAN", document_id=document_id
            )
    finally:
        os.close(descriptor)

    result = dict(route_entry)
    result.update(parsed)
    return result


def _summarize(entries: list[dict[str, object]]) -> dict[str, object]:
    types = {name: 0 for name in ("PDF", "DOC", "DOCX", "JPEG", "XLSX")}
    groups = {"core": 0, "negative": 0}
    decisions = {
        "NATIVE_CANDIDATE": 0,
        "FULL_PAGE_OCR_REQUIRED": 0,
        "MANUAL_REVIEW_REQUIRED": 0,
    }
    pdf_pages = 0
    visual_units = 0
    doc_deferred = 0
    docx_summary = {"paragraphs": 0, "tables": 0, "rows": 0, "cells": 0}
    xlsx_summary = {
        "sheets": 0,
        "cells": 0,
        "formulas": 0,
        "value_cells": 0,
        "formula_cached_values": 0,
    }
    jpeg_summary = {"documents": 0, "visual_units": 0}
    for entry in entries:
        document_type = str(entry["type"])
        types[document_type] += 1
        groups[str(entry["group"])] += 1
        if document_type == "PDF":
            pdf_pages += int(entry["page_count"])
        if document_type in {"PDF", "JPEG"}:
            pages = entry["pages"]
            visual_units += len(pages)
            for page in pages:
                decisions[str(page["decision"])] += 1
        if document_type == "DOC":
            doc_deferred += 1
        elif document_type == "DOCX":
            structure = entry["structure_summary"]
            for key in docx_summary:
                docx_summary[key] += int(structure[key])
        elif document_type == "XLSX":
            structure = entry["structure_summary"]
            for key in xlsx_summary:
                xlsx_summary[key] += int(structure[key])
        elif document_type == "JPEG":
            jpeg_summary["documents"] += 1
            jpeg_summary["visual_units"] += 1
            page = entry["pages"][0]
            jpeg_summary["width_px"] = int(page["width_px"])
            jpeg_summary["height_px"] = int(page["height_px"])
    return {
        "documents": len(entries),
        "groups": groups,
        "types": types,
        "pdf_documents": types["PDF"],
        "pdf_pages": pdf_pages,
        "visual_units": visual_units,
        "native_candidates": decisions["NATIVE_CANDIDATE"],
        "ocr_required": decisions["FULL_PAGE_OCR_REQUIRED"],
        "manual_review_required": decisions["MANUAL_REVIEW_REQUIRED"],
        "decisions": decisions,
        "doc_deferred": doc_deferred,
        "docx": docx_summary,
        "xlsx": xlsx_summary,
        "jpeg": jpeg_summary,
        "errors": 0,
    }


def build_page_plan(
    *,
    source_root: Path,
    core_manifest: Path,
    negative_manifest: Path,
    route_plan_path: Path,
    profile: str,
    expected_identity: FixtureIdentity | None = None,
) -> dict[str, object]:
    if profile not in {"smoke", "full"}:
        raise PlannerFailure("INVALID_PROFILE")
    pypdf_version, pypdf_license = _check_runtime()
    try:
        live_route_plan = build_route_plan(
            source_root=source_root,
            core_manifest=core_manifest,
            negative_manifest=negative_manifest,
            profile=profile,
            expected_identity=expected_identity,
        )
    except RouteFailure as error:
        raise PlannerFailure(
            error.code,
            group=error.group,
            line=error.line,
            document_id=error.document_id,
        ) from error
    registered_route_bytes = _read_registered_bytes(
        route_plan_path, limit=_MAX_ROUTE_PLAN_BYTES
    )
    live_route_bytes = _json_bytes(live_route_plan)
    if registered_route_bytes != live_route_bytes:
        raise PlannerFailure("ROUTE_PLAN_MISMATCH")

    try:
        seen_paths: set[str] = set()
        core_entries, core_digest = _parse_manifest(core_manifest, "core", seen_paths)
        negative_entries, negative_digest = _parse_manifest(
            negative_manifest, "negative", seen_paths
        )
    except ValidationFailure as error:
        raise PlannerFailure(error.code, group=error.group, line=error.line) from error
    identity = expected_identity or ENVIRONMENT_DEMO_V01
    if expected_identity is not None and (
        core_digest != identity.core_manifest_sha256
        or negative_digest != identity.negative_manifest_sha256
    ):
        raise PlannerFailure("MANIFEST_IDENTITY_MISMATCH")
    manifest_entries = _selected_entries(core_entries + negative_entries, profile)
    route_entries = live_route_plan["entries"]
    if len(manifest_entries) != len(route_entries):
        raise PlannerFailure("ROUTE_ENTRY_MISMATCH")
    for manifest_entry, route_entry in zip(manifest_entries, route_entries):
        if (manifest_entry.group, manifest_entry.line) != (
            route_entry.get("group"),
            route_entry.get("line"),
        ):
            raise PlannerFailure("ROUTE_ENTRY_MISMATCH")

    try:
        root_descriptor = _open_path_without_symlinks(
            source_root,
            final_flags=os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            unavailable_code="SOURCE_ROOT_UNAVAILABLE",
            symlink_code="SOURCE_ROOT_SYMLINK",
        )
    except ValidationFailure as error:
        raise PlannerFailure(error.code) from error
    try:
        planned = [
            _plan_entry(
                root_descriptor,
                manifest_entry,
                route_entry,
                fixture_set_id=str(live_route_plan["fixture_set_id"]),
                fixture_version=str(live_route_plan["fixture_version"]),
            )
            for manifest_entry, route_entry in zip(manifest_entries, route_entries)
        ]
    finally:
        os.close(root_descriptor)

    return {
        "schema_version": SCHEMA_VERSION,
        "fixture_set_id": live_route_plan["fixture_set_id"],
        "fixture_version": live_route_plan["fixture_version"],
        "profile": profile,
        "labels": ["FIXTURE_ONLY", "PIPELINE_REGRESSION_ONLY"],
        "benchmark_tier": "NONE",
        "claim_scope": "PIPELINE_REGRESSION_ONLY",
        "policy": dict(live_route_plan["policy"]),
        "input_route_plan_sha256": hashlib.sha256(registered_route_bytes).hexdigest(),
        "parser": {
            "name": "pypdf",
            "version": pypdf_version,
            "license_expression": pypdf_license,
            "strict": True,
        },
        "rule_version": RULE_VERSION,
        "ocr_executed": False,
        "raw_text_persisted": False,
        "page_images_persisted": False,
        "external_processing": "DENY",
        "summary": _summarize(planned),
        "entries": planned,
    }


def _schema_dict(value: object, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
    return value


def _schema_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
    return value


def _is_nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _strict_equal(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return set(actual) == set(expected) and all(
            _strict_equal(actual[key], expected[key]) for key in actual
        )
    if isinstance(actual, (list, tuple)):
        return len(actual) == len(expected) and all(
            _strict_equal(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


def _validate_box(value: object) -> bool:
    box = _schema_dict(value, {"left", "bottom", "right", "top"})
    coordinates: dict[str, float] = {}
    for key in ("left", "bottom", "right", "top"):
        coordinate = box[key]
        if not isinstance(coordinate, str):
            raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
        try:
            if _fixed_decimal(coordinate) != coordinate:
                raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
            coordinates[key] = float(coordinate)
        except (TypeError, ValueError, OverflowError):
            raise PlannerFailure("UNSAFE_PLAN_SCHEMA") from None
    return (
        coordinates["right"] <= coordinates["left"]
        or coordinates["top"] <= coordinates["bottom"]
    )


def _validate_pdf_page(
    value: object,
    *,
    document_id: str,
    expected_page_no: int,
) -> None:
    page = _schema_dict(
        value,
        {
            "page_id", "page_no", "media_box", "crop_box", "rotation",
            "native_characters", "bad_character_ppm", "native_text_sha256",
            "decision", "reason_codes",
        },
    )
    if (
        type(page["page_no"]) is not int
        or page["page_no"] != expected_page_no
        or page["page_id"] != _page_id(document_id, expected_page_no)
        or not _is_sha256(page["page_id"])
        or type(page["rotation"]) is not int
        or page["rotation"] not in {0, 90, 180, 270}
        or not _is_nonnegative_int(page["native_characters"])
        or page["native_characters"] > _MAX_PAGE_TEXT_CHARACTERS
        or not _is_nonnegative_int(page["bad_character_ppm"])
        or page["bad_character_ppm"] > 1_000_000
        or not _is_sha256(page["native_text_sha256"])
    ):
        raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
    media_abnormal = _validate_box(page["media_box"])
    crop_abnormal = _validate_box(page["crop_box"])
    reasons = _schema_list(page["reason_codes"])
    decision = page["decision"]
    native_characters = page["native_characters"]
    bad_character_ppm = page["bad_character_ppm"]
    if decision == "NATIVE_CANDIDATE":
        if (
            reasons != ["NATIVE_TEXT_THRESHOLD_MET"]
            or native_characters < _MIN_NATIVE_CHARACTERS
            or bad_character_ppm > _MAX_BAD_CHARACTER_PPM
            or media_abnormal
            or crop_abnormal
        ):
            raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
    elif decision == "FULL_PAGE_OCR_REQUIRED":
        if (
            reasons != ["LOW_NATIVE_TEXT"]
            or native_characters >= _MIN_NATIVE_CHARACTERS
            or bad_character_ppm > _MAX_BAD_CHARACTER_PPM
            or media_abnormal
            or crop_abnormal
        ):
            raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
    elif decision == "MANUAL_REVIEW_REQUIRED":
        canonical = [
            "HIDDEN_NATIVE_TEXT",
            "GEOMETRY_ABNORMAL",
            "BAD_NATIVE_TEXT_RATIO",
        ]
        if (
            not reasons
            or reasons != [reason for reason in canonical if reason in reasons]
            or ((bad_character_ppm > _MAX_BAD_CHARACTER_PPM)
                != ("BAD_NATIVE_TEXT_RATIO" in reasons))
            or ((media_abnormal or crop_abnormal)
                and "GEOMETRY_ABNORMAL" not in reasons)
        ):
            raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
    else:
        raise PlannerFailure("UNSAFE_PLAN_SCHEMA")


def _validate_jpeg_page(
    value: object,
    *,
    document_id: str,
) -> None:
    page = _schema_dict(
        value,
        {"page_id", "page_no", "width_px", "height_px", "decision", "reason_codes"},
    )
    if (
        type(page["page_no"]) is not int
        or page["page_no"] != 1
        or page["page_id"] != _page_id(document_id, 1)
        or not _is_sha256(page["page_id"])
        or not _is_nonnegative_int(page["width_px"])
        or not _is_nonnegative_int(page["height_px"])
        or page["width_px"] < 1
        or page["height_px"] < 1
        or page["width_px"] * page["height_px"] > _MAX_IMAGE_PIXELS
        or page["decision"] != "FULL_PAGE_OCR_REQUIRED"
        or page["reason_codes"] != ["IMAGE_INPUT"]
    ):
        raise PlannerFailure("UNSAFE_PLAN_SCHEMA")


def _validate_docx_entry(entry: dict[str, object]) -> None:
    summary = _schema_dict(
        entry["structure_summary"],
        {"paragraphs", "tables", "rows", "cells", "ordered_blocks"},
    )
    if any(
        not _is_nonnegative_int(summary[key])
        or summary[key] > _MAX_XML_BYTES
        for key in summary
    ):
        raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
    anchors = _schema_list(entry["structure_anchors"])
    if summary["ordered_blocks"] != len(anchors):
        raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
    previous_index = 0
    paragraph_anchors = 0
    table_anchors = 0
    table_rows = 0
    table_cells = 0
    for value in anchors:
        if not isinstance(value, dict):
            raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
        kind = value.get("kind")
        expected_keys = {
            "block_index", "kind", "native_characters", "native_text_sha256"
        }
        if kind == "TABLE":
            expected_keys |= {"rows", "cells"}
        elif kind != "PARAGRAPH":
            raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
        anchor = _schema_dict(value, expected_keys)
        if (
            type(anchor["block_index"]) is not int
            or anchor["block_index"] <= previous_index
            or anchor["block_index"] > _MAX_XML_BYTES
            or not _is_nonnegative_int(anchor["native_characters"])
            or anchor["native_characters"] > _MAX_PAGE_TEXT_CHARACTERS
            or not _is_sha256(anchor["native_text_sha256"])
        ):
            raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
        previous_index = anchor["block_index"]
        if kind == "PARAGRAPH":
            paragraph_anchors += 1
        else:
            if (
                not _is_nonnegative_int(anchor["rows"])
                or not _is_nonnegative_int(anchor["cells"])
                or anchor["rows"] > _MAX_XML_BYTES
                or anchor["cells"] > _MAX_XML_BYTES
            ):
                raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
            table_anchors += 1
            table_rows += anchor["rows"]
            table_cells += anchor["cells"]
    if (
        summary["paragraphs"] < paragraph_anchors
        or summary["tables"] < table_anchors
        or summary["rows"] != table_rows
        or summary["cells"] != table_cells
    ):
        raise PlannerFailure("UNSAFE_PLAN_SCHEMA")


def _validate_xlsx_entry(entry: dict[str, object]) -> None:
    numeric_keys = {
        "sheets", "cells", "formulas", "value_cells", "formula_cached_values"
    }
    summary = _schema_dict(entry["structure_summary"], numeric_keys)
    if (
        any(not _is_nonnegative_int(summary[key]) for key in numeric_keys)
        or summary["sheets"] > _MAX_ZIP_MEMBERS
        or any(
            summary[key] > _MAX_ZIP_UNCOMPRESSED_BYTES
            for key in numeric_keys - {"sheets"}
        )
    ):
        raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
    anchors = _schema_list(entry["structure_anchors"])
    if summary["sheets"] != len(anchors):
        raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
    totals = {
        "cells": 0,
        "formulas": 0,
        "value_cells": 0,
        "formula_cached_values": 0,
    }
    for sheet_index, value in enumerate(anchors, start=1):
        anchor = _schema_dict(
            value,
            {
                "sheet_index", "sheet_id", "cell_count", "formula_count",
                "value_cell_count", "formula_cached_value_count", "content_sha256",
            },
        )
        if (
            type(anchor["sheet_index"]) is not int
            or anchor["sheet_index"] != sheet_index
            or anchor["sheet_index"] > _MAX_ZIP_MEMBERS
            or not _is_sha256(anchor["sheet_id"])
            or not _is_sha256(anchor["content_sha256"])
            or any(
                not _is_nonnegative_int(anchor[key])
                for key in (
                    "cell_count", "formula_count", "value_cell_count",
                    "formula_cached_value_count",
                )
            )
            or anchor["formula_count"] > anchor["cell_count"]
            or anchor["value_cell_count"] > anchor["cell_count"]
            or anchor["formula_cached_value_count"] > anchor["formula_count"]
            or anchor["formula_cached_value_count"] > anchor["value_cell_count"]
            or any(
                anchor[key] > _MAX_XML_BYTES
                for key in (
                    "cell_count", "formula_count", "value_cell_count",
                    "formula_cached_value_count",
                )
            )
        ):
            raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
        totals["cells"] += anchor["cell_count"]
        totals["formulas"] += anchor["formula_count"]
        totals["value_cells"] += anchor["value_cell_count"]
        totals["formula_cached_values"] += anchor["formula_cached_value_count"]
    if any(summary[key] != totals[key] for key in totals):
        raise PlannerFailure("UNSAFE_PLAN_SCHEMA")


def _validate_plan_entry(value: object) -> None:
    if not isinstance(value, dict):
        raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
    document_type = value.get("type")
    route_contracts = {
        "PDF": ("PDF_NATIVE_OR_OCR_PROBE", "NATIVE_PROBE_COMPLETE"),
        "DOC": ("LEGACY_OFFICE_CONVERSION_REQUIRED", "DEFERRED_CONVERSION_REQUIRED"),
        "DOCX": ("DOCX_NATIVE", "NATIVE_STRUCTURE_PROBE_COMPLETE"),
        "JPEG": ("IMAGE_OCR_REQUIRED", "OCR_CANDIDATE_PLANNED"),
        "XLSX": ("XLSX_NATIVE", "NATIVE_STRUCTURE_PROBE_COMPLETE"),
    }
    if not isinstance(document_type, str) or document_type not in route_contracts:
        raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
    base_keys = {
        "group", "line", "document_id", "type", "route", "corpus_role",
        "enterprise_fact_allowed", "current_regulation_allowed",
        "search_publish_allowed", "parse_status",
    }
    extra_keys = {
        "PDF": {"page_count", "pages"},
        "JPEG": {"page_count", "pages"},
        "DOCX": {"structure_summary", "structure_anchors"},
        "XLSX": {"structure_summary", "structure_anchors"},
        "DOC": set(),
    }
    entry = _schema_dict(value, base_keys | extra_keys[document_type])
    group = entry["group"]
    gate_contracts = {
        "core": ("CORE_FIXTURE", True, False, False),
        "negative": ("NEGATIVE_TEST_ONLY", False, False, False),
    }
    line_limits = {"core": 24, "negative": 2}
    if (
        not isinstance(group, str)
        or group not in gate_contracts
        or type(entry["line"]) is not int
        or entry["line"] < 1
        or entry["line"] > line_limits[group]
        or not _is_sha256(entry["document_id"])
        or (entry["route"], entry["parse_status"])
        != route_contracts[document_type]
        or not _strict_equal(
            (
                entry["corpus_role"],
                entry["enterprise_fact_allowed"],
                entry["current_regulation_allowed"],
                entry["search_publish_allowed"],
            ),
            gate_contracts[group],
        )
    ):
        raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
    document_id = entry["document_id"]
    if document_type == "PDF":
        pages = _schema_list(entry["pages"])
        if (
            type(entry["page_count"]) is not int
            or entry["page_count"] != len(pages)
            or not 1 <= entry["page_count"] <= _MAX_PDF_PAGES
        ):
            raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
        for page_no, page in enumerate(pages, start=1):
            _validate_pdf_page(page, document_id=document_id, expected_page_no=page_no)
    elif document_type == "JPEG":
        pages = _schema_list(entry["pages"])
        if (
            type(entry["page_count"]) is not int
            or entry["page_count"] != 1
            or len(pages) != 1
        ):
            raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
        _validate_jpeg_page(pages[0], document_id=document_id)
    elif document_type == "DOCX":
        _validate_docx_entry(entry)
    elif document_type == "XLSX":
        _validate_xlsx_entry(entry)


def _validate_plan_for_write(plan: dict[str, object]) -> None:
    expected_top_level = {
        "schema_version", "fixture_set_id", "fixture_version", "profile",
        "labels", "benchmark_tier", "claim_scope", "policy",
        "input_route_plan_sha256", "parser", "rule_version", "ocr_executed",
        "raw_text_persisted", "page_images_persisted", "external_processing",
        "summary", "entries",
    }
    if set(plan) != expected_top_level:
        raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
    expected_policy = {
        "external_processing": "DENY",
        "model_training": "DENY",
        "production_use": "DENY",
        "public_display": "DENY",
    }
    expected_parser = {
        "name": "pypdf",
        "version": _EXPECTED_PYPDF_VERSION,
        "license_expression": _EXPECTED_PYPDF_LICENSE,
        "strict": True,
    }
    if (
        plan.get("schema_version") != SCHEMA_VERSION
        or plan.get("fixture_set_id") != "environment-demo-seed"
        or plan.get("fixture_version") != "v0.1"
        or plan.get("profile") not in {"smoke", "full"}
        or not _strict_equal(
            plan.get("labels"), ["FIXTURE_ONLY", "PIPELINE_REGRESSION_ONLY"]
        )
        or plan.get("benchmark_tier") != "NONE"
        or plan.get("claim_scope") != "PIPELINE_REGRESSION_ONLY"
        or not _strict_equal(plan.get("policy"), expected_policy)
        or not _strict_equal(plan.get("parser"), expected_parser)
        or not _is_sha256(plan.get("input_route_plan_sha256"))
        or plan.get("rule_version") != RULE_VERSION
        or plan.get("ocr_executed") is not False
        or plan.get("raw_text_persisted") is not False
        or plan.get("page_images_persisted") is not False
        or plan.get("external_processing") != "DENY"
        or not isinstance(plan.get("summary"), dict)
        or not isinstance(plan.get("entries"), list)
    ):
        raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
    summary = plan["summary"]
    entries = plan["entries"]
    if len(entries) > 26:
        raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
    try:
        document_ids: set[str] = set()
        entry_locations: set[tuple[str, int]] = set()
        for entry in entries:
            _validate_plan_entry(entry)
            document_id = entry["document_id"]
            entry_location = (entry["group"], entry["line"])
            if document_id in document_ids or entry_location in entry_locations:
                raise PlannerFailure("UNSAFE_PLAN_SCHEMA")
            document_ids.add(document_id)
            entry_locations.add(entry_location)
        expected_summary = _summarize(entries)
    except (KeyError, TypeError, ValueError, OverflowError):
        raise PlannerFailure("UNSAFE_PLAN_SCHEMA") from None
    if not _strict_equal(summary, expected_summary):
        raise PlannerFailure("UNSAFE_PLAN_SCHEMA")


def _open_output_directory(directory: Path) -> int:
    if ".." in directory.parts:
        raise PlannerFailure("OUTPUT_WRITE_FAILED")
    absolute = directory if directory.is_absolute() else Path.cwd() / directory
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        current = os.open(absolute.anchor, flags)
        for part in absolute.parts[1:]:
            try:
                following = os.open(part, flags, dir_fd=current)
            except FileNotFoundError:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=current)
                except FileExistsError:
                    pass
                following = os.open(part, flags, dir_fd=current)
            os.close(current)
            current = following
        directory_stat = os.fstat(current)
        if (
            not stat.S_ISDIR(directory_stat.st_mode)
            or stat.S_IMODE(directory_stat.st_mode) != 0o700
            or directory_stat.st_uid != os.getuid()
        ):
            raise PlannerFailure("OUTPUT_WRITE_FAILED")
        return current
    except PlannerFailure:
        try:
            os.close(current)
        except (UnboundLocalError, OSError):
            pass
        raise
    except OSError as error:
        try:
            os.close(current)
        except (UnboundLocalError, OSError):
            pass
        raise PlannerFailure("OUTPUT_WRITE_FAILED") from error


def _existing_output_matches(directory: int, name: str, expected: bytes) -> bool:
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                dir_fd=directory,
            )
        except FileNotFoundError:
            return False
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.getuid()
            or before.st_size != len(expected)
        ):
            raise PlannerFailure("OUTPUT_WRITE_FAILED")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            _stat_identity(before) != _stat_identity(after)
            or after.st_nlink != 1
            or stat.S_IMODE(after.st_mode) != 0o600
            or after.st_uid != os.getuid()
            or b"".join(chunks) != expected
        ):
            raise PlannerFailure("OUTPUT_WRITE_FAILED")
        return True
    except PlannerFailure:
        raise
    except OSError as error:
        raise PlannerFailure("OUTPUT_WRITE_FAILED") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _remove_created_output(
    directory: int, name: str, identity: tuple[int, int]
) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            dir_fd=directory,
        )
        current = os.fstat(descriptor)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or (current.st_dev, current.st_ino) != identity
        ):
            raise PlannerFailure("OUTPUT_ROLLBACK_FAILED")
        os.close(descriptor)
        descriptor = None
        os.unlink(name, dir_fd=directory)
    except PlannerFailure:
        raise
    except OSError as error:
        raise PlannerFailure("OUTPUT_ROLLBACK_FAILED") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _create_output(directory: int, name: str, content: bytes) -> tuple[int, int]:
    descriptor: int | None = None
    created_identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            0o600,
            dir_fd=directory,
        )
        before = os.fstat(descriptor)
        created_identity = (before.st_dev, before.st_ino)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.getuid()
        ):
            raise PlannerFailure("OUTPUT_WRITE_FAILED")
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise PlannerFailure("OUTPUT_WRITE_FAILED")
            view = view[written:]
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or not stat.S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or stat.S_IMODE(after.st_mode) != 0o600
            or after.st_uid != os.getuid()
            or after.st_size != len(content)
        ):
            raise PlannerFailure("OUTPUT_WRITE_FAILED")
        return created_identity
    except PlannerFailure as error:
        if descriptor is not None:
            os.close(descriptor)
            descriptor = None
        if created_identity is not None:
            _remove_created_output(directory, name, created_identity)
        raise error
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
            descriptor = None
        if created_identity is not None:
            _remove_created_output(directory, name, created_identity)
        raise PlannerFailure("OUTPUT_WRITE_FAILED") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_output_batch(root: Path, outputs: dict[str, bytes]) -> None:
    if not outputs or any(name not in _OUTPUT_NAMES for name in outputs):
        raise PlannerFailure("OUTPUT_WRITE_FAILED")
    directory = _open_output_directory(root)
    created: list[tuple[str, tuple[int, int]]] = []
    try:
        missing: list[str] = []
        for name, content in outputs.items():
            if not _existing_output_matches(directory, name, content):
                missing.append(name)
        for name in missing:
            created.append((name, _create_output(directory, name, outputs[name])))
        os.fsync(directory)
    except PlannerFailure as error:
        for name, identity in reversed(created):
            _remove_created_output(directory, name, identity)
        raise error
    except OSError as error:
        for name, identity in reversed(created):
            _remove_created_output(directory, name, identity)
        raise PlannerFailure("OUTPUT_WRITE_FAILED") from error
    finally:
        os.close(directory)


def render_status_html(plan: dict[str, object]) -> str:
    summary = plan["summary"]
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Fixture 原生解析计划</title><style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#f4f7f5;color:#17352b;margin:0;padding:32px}}
main{{max-width:880px;margin:auto}}header,section{{background:#fff;border-radius:16px;padding:24px;margin-bottom:18px}}
header{{background:#143f33;color:#fff}}.tag{{color:#17352b;background:#d7f36b;padding:6px 10px;border-radius:999px;font-weight:700}}
dl{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}div{{background:#edf4f0;padding:14px;border-radius:10px}}dt{{font-size:13px}}dd{{font-size:26px;margin:8px 0 0}}.deny{{color:#9f2f2f;font-weight:700}}
</style></head><body><main><header><span class="tag">FIXTURE_ONLY</span>
<h1>页级原生解析证据与 OCR 待办</h1><p>NATIVE CANDIDATE 不是正确或 READY。</p></header>
<section><dl><div><dt>资料</dt><dd>{int(summary['documents'])}</dd></div>
<div><dt>视觉单元</dt><dd>{int(summary['visual_units'])}</dd></div>
<div><dt>原生候选</dt><dd>{int(summary['native_candidates'])}</dd></div>
<div><dt>OCR 待办</dt><dd>{int(summary['ocr_required'])}</dd></div>
<div><dt>人工复核</dt><dd>{int(summary['manual_review_required'])}</dd></div>
<div><dt>DOC 延后</dt><dd>{int(summary['doc_deferred'])}</dd></div></dl></section>
<section><p class="deny">外部处理 DENY · OCR 未执行 · 正文未落盘 · 负样本三道权限闸门保持关闭。</p></section>
</main></body></html>
"""


def _sbom() -> dict[str, object]:
    version, license_expression = _check_runtime()
    return {
        "schema_version": "fixture-native-plan-sbom/v1",
        "components": [
            {
                "name": "Python",
                "version": platform.python_version(),
                "kind": "runtime",
                "license_expression": "PSF-2.0",
            },
            {
                "name": "pypdf",
                "version": version,
                "kind": "library",
                "license_expression": license_expression,
            },
        ],
        "external_binaries": 0,
        "network_dependencies": 0,
    }


def write_page_outputs(
    plan: dict[str, object], *, artifact_root: Path = REGISTERED_ARTIFACT_ROOT
) -> None:
    _validate_plan_for_write(plan)
    profile = str(plan.get("profile"))
    if profile == "smoke":
        _write_output_batch(
            artifact_root, {"smoke-plan.json": _json_bytes(plan)}
        )
        return
    if profile == "full":
        _write_output_batch(
            artifact_root,
            {
                "full-plan.json": _json_bytes(plan),
                "status.html": render_status_html(plan).encode("utf-8"),
                "sbom.json": _json_bytes(_sbom()),
            },
        )
        return
    raise PlannerFailure("INVALID_PROFILE")
