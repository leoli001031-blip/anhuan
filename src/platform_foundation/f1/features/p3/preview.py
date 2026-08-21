"""Deterministic, low-fidelity and non-executing P3 preview builders."""
from __future__ import annotations

import hashlib
import io
import json
import re
import struct
import textwrap
import zipfile
from dataclasses import dataclass
from typing import BinaryIO
from xml.etree import ElementTree

from pypdf import PdfReader
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    IndirectObject,
    NameObject,
)

from .contracts import (
    MAX_DOCX_PAGES,
    MAX_JPEG_EDGE,
    MAX_JPEG_PIXELS,
    MAX_JPEG_PREVIEW_BYTES,
    MAX_OOXML_COMPRESSION_RATIO,
    MAX_OOXML_ENTRIES,
    MAX_OOXML_ENTRY_BYTES,
    MAX_OOXML_EXPANDED_BYTES,
    MAX_PDF_PAGES,
    MAX_PREVIEW_BYTES,
    MAX_PREVIEW_CHARACTERS,
    MAX_XLSX_COLUMNS,
    MAX_XLSX_ROWS_PER_SHEET,
    MAX_XLSX_SHEETS,
)


MAX_XLSX_ROWS = MAX_XLSX_ROWS_PER_SHEET
MAX_XLSX_CELLS = 10_000
MAX_PAGE_LINES = 48
MAX_PAGE_LINE_CHARACTERS = 80
_XML_FORBIDDEN = (b"<!DOCTYPE", b"<!ENTITY")
_PDF_ACTIVE_MARKERS = (
    b"/JavaScript",
    b"/JS",
    b"/Launch",
    b"/EmbeddedFile",
    b"/OpenAction",
)
_PDF_ACTIVE_DICTIONARY_KEYS = frozenset(
    {
        "/AA",
        "/EF",
        "/EmbeddedFile",
        "/EmbeddedFiles",
        "/JS",
        "/JavaScript",
        "/Launch",
        "/OpenAction",
    }
)
_PDF_DANGEROUS_ACTION_NAMES = frozenset(
    {
        "/GoToE",
        "/GoToR",
        "/ImportData",
        "/JS",
        "/JavaScript",
        "/Launch",
        "/Movie",
        "/Rendition",
        "/RichMedia",
        "/Sound",
        "/SubmitForm",
    }
)
_PDF_EMBEDDED_TYPES = frozenset({"/EmbeddedFile", "/FileSpec", "/Filespec"})
MAX_PDF_GRAPH_NODES = 32_768
_NESTED_ARCHIVE_RE = re.compile(
    r"\.(?:zip|7z|rar|tar|gz|bz2|xz|docx|xlsx|docm|xlsm)$", re.IGNORECASE
)


class PreviewFailure(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class PreviewUnitArtifact:
    id: str
    kind: str
    ordinal: int
    label: str
    sha256: str
    content_type: str
    content: bytes
    grid: list[list[str]] | None = None
    width_px: int | None = None
    height_px: int | None = None
    row_count: int | None = None
    column_count: int | None = None

    def __post_init__(self) -> None:
        if self.content_type not in {"application/json", "image/jpeg"}:
            raise ValueError("P3_PREVIEW_CONTENT_TYPE_INVALID")
        if not self.content or hashlib.sha256(self.content).hexdigest() != self.sha256:
            raise ValueError("P3_PREVIEW_IDENTITY_INVALID")


@dataclass(frozen=True, slots=True)
class PreviewResult:
    kind: str
    payload: dict
    sha256: str
    unit_count: int
    units: tuple[PreviewUnitArtifact, ...]


def build_preview(kind: str, file_obj: BinaryIO) -> PreviewResult:
    try:
        file_obj.seek(0)
        if kind == "pdf":
            source_payload = _pdf_preview(file_obj)
            preview_kind = "page_text"
            units = _page_units(source_payload["pages"])
        elif kind == "docx":
            source_payload = _docx_preview(file_obj)
            preview_kind = "page_text"
            units = _page_units(source_payload["pages"])
        elif kind == "xlsx":
            source_payload = _xlsx_preview(file_obj)
            preview_kind = "sheet_grid"
            units = _sheet_units(source_payload["sheets"])
        elif kind == "jpeg":
            source_payload, sanitized_jpeg = _jpeg_preview(file_obj)
            preview_kind = "image"
            units = _image_units(source_payload, sanitized_jpeg)
        else:
            raise PreviewFailure("P3_FORMAT_NOT_ALLOWED")
        source_characters = sum(len(value) for value in _walk_strings(source_payload))
        if source_characters > MAX_PREVIEW_CHARACTERS:
            raise PreviewFailure("P3_PREVIEW_OUTPUT_LIMIT")
        payload = {
            "kind": preview_kind,
            "units": [_unit_manifest(unit) for unit in units],
        }
        encoded = _canonical_preview(payload)
        if preview_kind == "image":
            if (
                len(units) != 1
                or _unit_size(units[0]) > MAX_JPEG_PREVIEW_BYTES
            ):
                raise PreviewFailure("P3_PREVIEW_OUTPUT_LIMIT")
        elif len(encoded) + sum(_unit_size(unit) for unit in units) > MAX_PREVIEW_BYTES:
            raise PreviewFailure("P3_PREVIEW_OUTPUT_LIMIT")
        result = PreviewResult(
            kind=preview_kind,
            payload=payload,
            sha256=hashlib.sha256(encoded).hexdigest(),
            unit_count=len(units),
            units=units,
        )
    except PreviewFailure:
        raise
    except Exception as error:
        raise PreviewFailure("P3_PREVIEW_INVALID") from error
    try:
        file_obj.seek(0)
    except Exception as error:
        raise PreviewFailure("P3_SOURCE_READ_FAILED", retryable=True) from error
    return result


def _unit_manifest(unit: PreviewUnitArtifact) -> dict[str, object]:
    return {
        "id": unit.id,
        "kind": unit.kind,
        "ordinal": unit.ordinal,
        "label": unit.label,
        "content_type": unit.content_type,
        "size_bytes": len(unit.content),
        "width_px": unit.width_px,
        "height_px": unit.height_px,
        "row_count": unit.row_count,
        "column_count": unit.column_count,
        "sha256": unit.sha256,
    }


def _unit_size(unit: PreviewUnitArtifact) -> int:
    return len(unit.content)


def _page_units(pages: list[dict[str, object]]) -> tuple[PreviewUnitArtifact, ...]:
    units: list[PreviewUnitArtifact] = []
    for ordinal, page in enumerate(pages, start=1):
        page_payload = _page_text_payload(
            str(page.get("text") or ""),
            source_truncated=bool(page.get("truncated")),
        )
        encoded = json.dumps(
            page_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        units.append(
            PreviewUnitArtifact(
                id=f"unit-{ordinal:04d}",
                kind="page_text",
                ordinal=ordinal,
                label=f"第 {ordinal} 页",
                sha256=hashlib.sha256(encoded).hexdigest(),
                content_type="application/json",
                content=encoded,
            )
        )
    return tuple(units)


def _sheet_units(sheets: list[dict[str, object]]) -> tuple[PreviewUnitArtifact, ...]:
    units: list[PreviewUnitArtifact] = []
    for ordinal, sheet in enumerate(sheets, start=1):
        rows = sheet.get("rows")
        if not isinstance(rows, list) or any(not isinstance(row, list) for row in rows):
            raise PreviewFailure("P3_PREVIEW_INVALID")
        normalized_rows = [[str(cell) for cell in row] for row in rows]
        encoded = json.dumps(
            normalized_rows, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        units.append(
            PreviewUnitArtifact(
                id=f"unit-{ordinal:04d}",
                kind="worksheet_grid",
                ordinal=ordinal,
                label=f"工作表 {ordinal}",
                sha256=hashlib.sha256(encoded).hexdigest(),
                content_type="application/json",
                content=encoded,
                grid=normalized_rows,
                row_count=len(normalized_rows),
                column_count=max((len(row) for row in normalized_rows), default=0),
            )
        )
    return tuple(units)


def _image_units(
    payload: dict[str, int], sanitized_jpeg: bytes
) -> tuple[PreviewUnitArtifact, ...]:
    return (
        PreviewUnitArtifact(
            id="unit-0001",
            kind="image",
            ordinal=1,
            label="安全图像预览",
            sha256=hashlib.sha256(sanitized_jpeg).hexdigest(),
            content_type="image/jpeg",
            content=sanitized_jpeg,
            width_px=payload["image_width"],
            height_px=payload["image_height"],
        ),
    )


def _page_text_payload(
    value: str, *, source_truncated: bool = False
) -> dict[str, object]:
    wrapped: list[str] = []
    for source_line in value.splitlines():
        wrapped.extend(
            textwrap.wrap(
                source_line,
                width=MAX_PAGE_LINE_CHARACTERS,
                replace_whitespace=True,
                drop_whitespace=True,
                break_long_words=True,
                break_on_hyphens=False,
            )
        )
    truncated = source_truncated or len(wrapped) > MAX_PAGE_LINES
    return {"lines": wrapped[:MAX_PAGE_LINES], "truncated": truncated}


def _canonical_preview(payload: dict) -> bytes:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > MAX_PREVIEW_BYTES:
        raise PreviewFailure("P3_PREVIEW_OUTPUT_LIMIT")
    text_characters = sum(
        len(value)
        for value in _walk_strings(payload)
    )
    if text_characters > MAX_PREVIEW_CHARACTERS:
        raise PreviewFailure("P3_PREVIEW_OUTPUT_LIMIT")
    return encoded


def _walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def _pdf_name_token(value: object) -> str | None:
    current = value
    if isinstance(current, IndirectObject):
        try:
            current = current.get_object()
        except Exception as error:
            raise PreviewFailure("P3_PDF_CORRUPT") from error
    if not isinstance(current, NameObject):
        return None
    token = str(current)
    if not token.startswith("/") or not 2 <= len(token) <= 64:
        return None
    return token


def _pdf_dictionary_keys(dictionary: DictionaryObject) -> frozenset[str]:
    return frozenset(str(key) for key in dictionary.keys())


def _reject_active_pdf_dictionary(dictionary: DictionaryObject) -> None:
    keys = _pdf_dictionary_keys(dictionary)
    if keys & _PDF_ACTIVE_DICTIONARY_KEYS:
        raise PreviewFailure("P3_PDF_ACTIVE_CONTENT")
    type_name = _pdf_name_token(dictionary.get("/Type"))
    if type_name in _PDF_EMBEDDED_TYPES:
        raise PreviewFailure("P3_PDF_ACTIVE_CONTENT")
    subtype = _pdf_name_token(dictionary.get("/Subtype"))
    if subtype == "/EmbeddedFile":
        raise PreviewFailure("P3_PDF_ACTIVE_CONTENT")
    action = _pdf_name_token(dictionary.get("/S"))
    if action in _PDF_DANGEROUS_ACTION_NAMES:
        raise PreviewFailure("P3_PDF_ACTIVE_CONTENT")


def _pdf_walk_children(value: object) -> tuple[object, ...]:
    if isinstance(value, IndirectObject):
        return (value,)
    if isinstance(value, ArrayObject):
        return tuple(value)
    if isinstance(value, DictionaryObject):
        return tuple(value.values())
    return ()


def _reject_active_pdf_graph(reader: PdfReader) -> None:
    seen_indirect: set[tuple[int, int]] = set()
    seen_objects: set[int] = set()
    visits = 0
    stack: list[object] = [reader.trailer, reader.root_object, *reader.pages]
    while stack:
        current = stack.pop()
        if current is None:
            continue
        if isinstance(current, IndirectObject):
            identity = (int(current.idnum), int(current.generation))
            if identity in seen_indirect:
                continue
            seen_indirect.add(identity)
            try:
                current = current.get_object()
            except Exception as error:
                raise PreviewFailure("P3_PDF_CORRUPT") from error
        if not isinstance(current, (ArrayObject, DictionaryObject)):
            continue
        object_id = id(current)
        if object_id in seen_objects:
            continue
        seen_objects.add(object_id)
        visits += 1
        if visits > MAX_PDF_GRAPH_NODES:
            raise PreviewFailure("P3_PDF_CORRUPT")
        if isinstance(current, DictionaryObject):
            _reject_active_pdf_dictionary(current)
        stack.extend(_pdf_walk_children(current))


def _pdf_preview(file_obj: BinaryIO) -> dict:
    raw = file_obj.read()
    if any(marker in raw for marker in _PDF_ACTIVE_MARKERS):
        raise PreviewFailure("P3_PDF_ACTIVE_CONTENT")
    try:
        reader = PdfReader(io.BytesIO(raw), strict=True)
    except Exception as error:
        raise PreviewFailure("P3_PDF_CORRUPT") from error
    if reader.is_encrypted:
        raise PreviewFailure("P3_PDF_ENCRYPTED")
    try:
        _reject_active_pdf_graph(reader)
    except PreviewFailure:
        raise
    except Exception as error:
        raise PreviewFailure("P3_PDF_CORRUPT") from error
    page_count = len(reader.pages)
    if not 1 <= page_count <= MAX_PDF_PAGES:
        raise PreviewFailure("P3_PDF_PAGE_LIMIT")
    pages: list[dict[str, object]] = []
    remaining = MAX_PREVIEW_CHARACTERS
    for index, page in enumerate(reader.pages, start=1):
        if remaining == 0:
            pages.append({"page": index, "text": "", "truncated": True})
            continue
        try:
            text = page.extract_text() or ""
        except Exception as error:
            raise PreviewFailure("P3_PDF_CORRUPT") from error
        normalized = _normalize_text(text)
        truncated = len(normalized) > remaining
        if truncated:
            normalized = normalized[:remaining]
        remaining -= len(normalized)
        pages.append(
            {"page": index, "text": normalized, "truncated": truncated}
        )
    return {"pages": pages}


def _safe_zip(file_obj: BinaryIO, required: tuple[str, ...]) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(file_obj)
    except Exception as error:
        raise PreviewFailure("P3_OOXML_CORRUPT") from error
    infos = archive.infolist()
    if not infos or len(infos) > MAX_OOXML_ENTRIES:
        archive.close()
        raise PreviewFailure("P3_OOXML_ENTRY_LIMIT")
    names = {info.filename for info in infos}
    if any(name not in names for name in required):
        archive.close()
        raise PreviewFailure("P3_OOXML_STRUCTURE_INVALID")
    expanded = 0
    for info in infos:
        name = info.filename
        parts = name.replace("\\", "/").split("/")
        if (
            not name
            or name.startswith(("/", "\\"))
            or ".." in parts
            or "\\" in name
            or _NESTED_ARCHIVE_RE.search(name)
            or "vbaProject.bin" in name
            or "/embeddings/" in f"/{name}"
        ):
            archive.close()
            raise PreviewFailure("P3_OOXML_UNSAFE_ENTRY")
        if info.file_size > MAX_OOXML_ENTRY_BYTES:
            archive.close()
            raise PreviewFailure("P3_OOXML_ENTRY_SIZE_LIMIT")
        expanded += info.file_size
        if expanded > MAX_OOXML_EXPANDED_BYTES:
            archive.close()
            raise PreviewFailure("P3_OOXML_EXPANDED_LIMIT")
        if info.file_size > 0 and info.compress_size == 0:
            archive.close()
            raise PreviewFailure("P3_OOXML_COMPRESSION_LIMIT")
        if info.compress_size > 0 and info.file_size > (
            info.compress_size * MAX_OOXML_COMPRESSION_RATIO
        ):
            archive.close()
            raise PreviewFailure("P3_OOXML_COMPRESSION_LIMIT")
    for name in names:
        if not name.endswith((".xml", ".rels")):
            continue
        body = _read_zip_member(archive, name)
        upper = body.upper()
        if any(marker in upper for marker in _XML_FORBIDDEN):
            archive.close()
            raise PreviewFailure("P3_XML_ACTIVE_CONTENT")
        if name.endswith(".rels"):
            try:
                rel_root = ElementTree.fromstring(body)
            except Exception as error:
                archive.close()
                raise PreviewFailure("P3_OOXML_CORRUPT") from error
            if any(
                str(node.attrib.get("TargetMode", "")).casefold() == "external"
                for node in rel_root.iter()
            ):
                archive.close()
                raise PreviewFailure("P3_OOXML_EXTERNAL_RELATIONSHIP")
    return archive


def _read_zip_member(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        with archive.open(name) as handle:
            body = handle.read(MAX_OOXML_ENTRY_BYTES + 1)
    except Exception as error:
        raise PreviewFailure("P3_OOXML_CORRUPT") from error
    if len(body) > MAX_OOXML_ENTRY_BYTES:
        raise PreviewFailure("P3_OOXML_ENTRY_SIZE_LIMIT")
    return body


def _docx_preview(file_obj: BinaryIO) -> dict:
    archive = _safe_zip(file_obj, ("[Content_Types].xml", "word/document.xml"))
    try:
        body = _read_zip_member(archive, "word/document.xml")
        root = ElementTree.fromstring(body)
    except PreviewFailure:
        raise
    except Exception as error:
        raise PreviewFailure("P3_DOCX_CORRUPT") from error
    finally:
        archive.close()
    paragraphs: list[str] = []
    for paragraph in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
        text = "".join(
            node.text or ""
            for node in paragraph.iter(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
            )
        )
        normalized = _normalize_text(text)
        if normalized:
            paragraphs.append(normalized)
    pages: list[dict[str, object]] = []
    current: list[str] = []
    current_chars = 0
    remaining = MAX_PREVIEW_CHARACTERS
    source_truncated = False
    for paragraph in paragraphs:
        if remaining == 0:
            source_truncated = True
            break
        if current and current_chars + len(paragraph) + 1 > 4_000:
            pages.append(
                {
                    "page": len(pages) + 1,
                    "text": "\n".join(current),
                    "truncated": False,
                }
            )
            current = []
            current_chars = 0
        accepted = paragraph[:remaining]
        if len(accepted) < len(paragraph):
            source_truncated = True
        if accepted:
            current.append(accepted)
            current_chars += len(accepted) + 1
            remaining -= len(accepted)
        if source_truncated:
            break
    if current or not pages:
        pages.append(
            {
                "page": len(pages) + 1,
                "text": "\n".join(current),
                "truncated": source_truncated,
            }
        )
    elif source_truncated:
        pages[-1]["truncated"] = True
    if len(pages) > MAX_DOCX_PAGES:
        raise PreviewFailure("P3_DOCX_PAGE_LIMIT")
    return {"pages": pages}


def _xlsx_preview(file_obj: BinaryIO) -> dict:
    archive = _safe_zip(file_obj, ("[Content_Types].xml", "xl/workbook.xml"))
    try:
        shared = _xlsx_shared_strings(archive)
        sheet_names = sorted(
            name
            for name in archive.namelist()
            if re.fullmatch(r"xl/worksheets/sheet[0-9]+\.xml", name)
        )
        if not sheet_names:
            raise PreviewFailure("P3_XLSX_STRUCTURE_INVALID")
        if len(sheet_names) > MAX_XLSX_SHEETS:
            raise PreviewFailure("P3_XLSX_SHEET_LIMIT")
        sheets: list[dict[str, object]] = []
        total_cells = 0
        for sheet_index, name in enumerate(sheet_names, start=1):
            root = ElementTree.fromstring(_read_zip_member(archive, name))
            rows: list[list[str]] = []
            for row_node in root.iter(
                "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"
            ):
                if len(rows) >= MAX_XLSX_ROWS or total_cells >= MAX_XLSX_CELLS:
                    break
                row: list[str] = []
                for cell in row_node.iter(
                    "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"
                ):
                    if len(row) >= MAX_XLSX_COLUMNS or total_cells >= MAX_XLSX_CELLS:
                        break
                    row.append(_xlsx_cell_value(cell, shared))
                    total_cells += 1
                rows.append(row)
            sheets.append({"sheet": sheet_index, "rows": rows})
            if total_cells >= MAX_XLSX_CELLS:
                break
    except PreviewFailure:
        raise
    except Exception as error:
        raise PreviewFailure("P3_XLSX_CORRUPT") from error
    finally:
        archive.close()
    return {"sheets": sheets}


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    name = "xl/sharedStrings.xml"
    if name not in archive.namelist():
        return []
    try:
        root = ElementTree.fromstring(_read_zip_member(archive, name))
    except Exception as error:
        raise PreviewFailure("P3_XLSX_CORRUPT") from error
    values: list[str] = []
    for item in root.iter(
        "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si"
    ):
        value = "".join(
            node.text or ""
            for node in item.iter(
                "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"
            )
        )
        values.append(_normalize_text(value))
        if len(values) >= MAX_XLSX_CELLS:
            break
    return values


def _xlsx_cell_value(cell, shared: list[str]) -> str:
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    if cell.find(f"{namespace}f") is not None:
        return "[FORMULA]"
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return _normalize_text(
            "".join(node.text or "" for node in cell.iter(f"{namespace}t"))
        )
    value_node = cell.find(f"{namespace}v")
    value = value_node.text if value_node is not None and value_node.text else ""
    if cell_type == "s" and value.isdigit():
        index = int(value)
        return shared[index] if 0 <= index < len(shared) else ""
    return _normalize_text(value)


def _jpeg_preview(file_obj: BinaryIO) -> tuple[dict[str, int], bytes]:
    data = file_obj.read()
    width, height = _jpeg_dimensions(data)
    if (
        width <= 0
        or height <= 0
        or width > MAX_JPEG_EDGE
        or height > MAX_JPEG_EDGE
        or width * height > MAX_JPEG_PIXELS
    ):
        raise PreviewFailure("P3_JPEG_PIXEL_LIMIT")
    sanitized = _strip_jpeg_metadata(data)
    sanitized_width, sanitized_height = _jpeg_dimensions(sanitized)
    if (sanitized_width, sanitized_height) != (width, height):
        raise PreviewFailure("P3_JPEG_CORRUPT")
    return {"image_width": width, "image_height": height}, sanitized


def _strip_jpeg_metadata(data: bytes) -> bytes:
    """Remove APP0-APP15 and COM segments without decoding pixel data."""
    if not data.startswith(b"\xff\xd8"):
        raise PreviewFailure("P3_JPEG_CORRUPT")
    output = bytearray(b"\xff\xd8")
    position = 2
    in_scan = False
    found_eoi = False
    while position < len(data):
        if in_scan:
            scan_start = position
            while position < len(data):
                if data[position] != 0xFF:
                    position += 1
                    continue
                marker_position = position
                position += 1
                while position < len(data) and data[position] == 0xFF:
                    position += 1
                if position >= len(data):
                    raise PreviewFailure("P3_JPEG_CORRUPT")
                marker = data[position]
                if marker == 0x00 or 0xD0 <= marker <= 0xD7:
                    position += 1
                    continue
                output.extend(data[scan_start:marker_position])
                position = marker_position
                in_scan = False
                break
            if in_scan:
                raise PreviewFailure("P3_JPEG_CORRUPT")
            continue

        marker_start = position
        if data[position] != 0xFF:
            raise PreviewFailure("P3_JPEG_CORRUPT")
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            raise PreviewFailure("P3_JPEG_CORRUPT")
        marker = data[position]
        position += 1
        if marker == 0xD9:
            output.extend(b"\xff\xd9")
            found_eoi = True
            break
        if marker in {0xD8, 0x01} or 0xD0 <= marker <= 0xD7:
            output.extend(data[marker_start:position])
            continue
        if position + 2 > len(data):
            raise PreviewFailure("P3_JPEG_CORRUPT")
        segment_length = struct.unpack(">H", data[position : position + 2])[0]
        segment_end = position + segment_length
        if segment_length < 2 or segment_end > len(data):
            raise PreviewFailure("P3_JPEG_CORRUPT")
        is_metadata = 0xE0 <= marker <= 0xEF or marker == 0xFE
        if not is_metadata:
            output.extend(data[marker_start:segment_end])
        position = segment_end
        if marker == 0xDA:
            in_scan = True
    if not found_eoi:
        raise PreviewFailure("P3_JPEG_CORRUPT")
    return bytes(output)


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if (
        len(data) < 4
        or not data.startswith(b"\xff\xd8\xff")
        or not data.endswith(b"\xff\xd9")
    ):
        raise PreviewFailure("P3_JPEG_CORRUPT")
    position = 2
    sof_markers = {
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    }
    while position + 4 <= len(data):
        if data[position] != 0xFF:
            position += 1
            continue
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            break
        marker = data[position]
        position += 1
        if marker in (0xD8, 0xD9):
            continue
        if marker == 0xDA:
            break
        if position + 2 > len(data):
            break
        length = struct.unpack(">H", data[position : position + 2])[0]
        if length < 2 or position + length > len(data):
            raise PreviewFailure("P3_JPEG_CORRUPT")
        if marker in sof_markers:
            if length < 7:
                raise PreviewFailure("P3_JPEG_CORRUPT")
            height, width = struct.unpack(">HH", data[position + 3 : position + 7])
            return width, height
        position += length
    raise PreviewFailure("P3_JPEG_CORRUPT")


def _normalize_text(value: str) -> str:
    return "\n".join(
        line.strip()
        for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if line.strip()
    )


__all__ = (
    "PreviewFailure",
    "PreviewResult",
    "PreviewUnitArtifact",
    "build_preview",
)
