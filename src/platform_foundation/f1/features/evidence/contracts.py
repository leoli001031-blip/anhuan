"""Typed native source locations. Never reinterpret a non-PDF block as a page.

This is a new v2 identity domain. Existing PDF v1 unit IDs and AAD stay intact.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass
from typing import ClassVar


def _positive(value: object, maximum: int = 1_000_000) -> bool:
    return type(value) is int and 1 <= value <= maximum


def _sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


class _Locator:
    kind: ClassVar[str]
    source_format: ClassVar[str]

    def to_dict(self) -> dict:
        return {"schema_version": 2, "kind": self.kind, **asdict(self)}

    @property
    def sha256(self) -> str:
        return hashlib.sha256(canonical_json(self.to_dict())).hexdigest()


@dataclass(frozen=True, slots=True)
class PdfPageLocator(_Locator):
    page_number: int
    kind: ClassVar[str] = "pdf_page"
    source_format: ClassVar[str] = "pdf"

    def __post_init__(self):
        if not _positive(self.page_number):
            raise ValueError("EVIDENCE_LOCATOR_INVALID")


@dataclass(frozen=True, slots=True)
class DocxBlockLocator(_Locator):
    # body_index is the 1-based position among the body's XML children.
    # cell_index is the physical tc ordinal; grid_column is the actual grid.
    body_index: int
    row_index: int | None = None
    cell_index: int | None = None
    grid_column: int | None = None
    grid_span: int | None = None
    paragraph_index: int | None = None
    part: str = "word/document.xml"
    kind: ClassVar[str] = "docx_block"
    source_format: ClassVar[str] = "docx"

    def __post_init__(self):
        positions = (self.row_index, self.cell_index, self.grid_column, self.grid_span, self.paragraph_index)
        if self.part != "word/document.xml" or not _positive(self.body_index):
            raise ValueError("EVIDENCE_LOCATOR_INVALID")
        if not (all(v is None for v in positions) or all(_positive(v) for v in positions)):
            raise ValueError("EVIDENCE_LOCATOR_INVALID")
        if self.cell_index is not None and (self.grid_column < self.cell_index or self.grid_column + self.grid_span > 1_000_001):
            raise ValueError("EVIDENCE_LOCATOR_INVALID")


def _cell(address: str) -> tuple[int, int]:
    found = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,6})", address)
    if found is None:
        raise ValueError("EVIDENCE_LOCATOR_INVALID")
    column = 0
    for char in found[1]:
        column = column * 26 + ord(char) - ord("A") + 1
    row = int(found[2])
    if column > 16_384 or row > 1_048_576:
        raise ValueError("EVIDENCE_LOCATOR_INVALID")
    return row, column


@dataclass(frozen=True, slots=True)
class XlsxCellsLocator(_Locator):
    sheet_id: int
    sheet_name: str
    part: str
    cell_range: str
    kind: ClassVar[str] = "xlsx_cells"
    source_format: ClassVar[str] = "xlsx"

    def __post_init__(self):
        if not _positive(self.sheet_id, 2**32 - 1) or not isinstance(self.sheet_name, str) or not self.sheet_name or len(self.sheet_name) > 31:
            raise ValueError("EVIDENCE_LOCATOR_INVALID")
        if any(char in self.sheet_name for char in "\\/?*:[]") or self.sheet_name.startswith("'") or self.sheet_name.endswith("'") or any(ord(char) < 32 for char in self.sheet_name):
            raise ValueError("EVIDENCE_LOCATOR_INVALID")
        if not isinstance(self.part, str) or re.fullmatch(r"xl/worksheets/[A-Za-z0-9_-]+\.xml", self.part) is None:
            raise ValueError("EVIDENCE_LOCATOR_INVALID")
        if not isinstance(self.cell_range, str):
            raise ValueError("EVIDENCE_LOCATOR_INVALID")
        addresses = self.cell_range.split(":")
        if len(addresses) not in (1, 2):
            raise ValueError("EVIDENCE_LOCATOR_INVALID")
        start, end = _cell(addresses[0]), _cell(addresses[-1])
        if start[0] > end[0] or start[1] > end[1] or (len(addresses) == 2 and start == end):
            raise ValueError("EVIDENCE_LOCATOR_INVALID")


@dataclass(frozen=True, slots=True)
class ImageLocator(_Locator):
    source_width: int
    source_height: int
    rendered_width: int
    rendered_height: int
    rendered_sha256: str
    exif_orientation: int
    kind: ClassVar[str] = "image"
    source_format: ClassVar[str] = "jpeg"

    def __post_init__(self):
        if not all(_positive(v, 10_000) for v in (self.source_width, self.source_height, self.rendered_width, self.rendered_height)):
            raise ValueError("EVIDENCE_LOCATOR_INVALID")
        if self.source_width * self.source_height > 40_000_000 or self.rendered_width * self.rendered_height > 40_000_000:
            raise ValueError("EVIDENCE_LOCATOR_INVALID")
        if not _positive(self.exif_orientation, 8) or not _sha(self.rendered_sha256):
            raise ValueError("EVIDENCE_LOCATOR_INVALID")


SourceLocatorV2 = PdfPageLocator | DocxBlockLocator | XlsxCellsLocator | ImageLocator
_KINDS = {cls.kind: cls for cls in (PdfPageLocator, DocxBlockLocator, XlsxCellsLocator, ImageLocator)}


def parse_locator(value: object) -> SourceLocatorV2:
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value.get("schema_version") != 2:
        raise ValueError("EVIDENCE_LOCATOR_INVALID")
    kind = value.get("kind")
    cls = _KINDS.get(kind) if isinstance(kind, str) else None
    if cls is None:
        raise ValueError("EVIDENCE_LOCATOR_INVALID")
    try:
        result = cls(**{key: val for key, val in value.items() if key not in {"schema_version", "kind"}})
    except (TypeError, ValueError):
        raise ValueError("EVIDENCE_LOCATOR_INVALID") from None
    if result.to_dict() != value:
        raise ValueError("EVIDENCE_LOCATOR_NONCANONICAL")
    return result


def format_location(locator: SourceLocatorV2) -> str:
    if type(locator) is PdfPageLocator:
        return f"第 {locator.page_number} 页"
    if type(locator) is DocxBlockLocator:
        if locator.row_index is None:
            return f"正文块 {locator.body_index} · 段落"
        return f"正文块 {locator.body_index} · 表格第 {locator.row_index} 行第 {locator.grid_column} 列 · 段落 {locator.paragraph_index}"
    if type(locator) is XlsxCellsLocator:
        return f"{locator.sheet_name} · {locator.cell_range}"
    if type(locator) is ImageLocator:
        return "图像全文"
    raise ValueError("EVIDENCE_LOCATOR_INVALID")


@dataclass(frozen=True, slots=True)
class FragmentIdentity:
    enterprise_id: uuid.UUID
    knowledge_scope_id: uuid.UUID
    document_record_id: uuid.UUID
    document_version_id: uuid.UUID
    extraction_revision_id: uuid.UUID
    source_sha256: str
    parser_version: str
    extraction_contract: int
    locator: SourceLocatorV2
    ordinal: int
    body_sha256: str

    def __post_init__(self):
        if not all(type(value) is uuid.UUID for value in (self.enterprise_id, self.knowledge_scope_id, self.document_record_id, self.document_version_id, self.extraction_revision_id)):
            raise ValueError("EVIDENCE_IDENTITY_INVALID")
        if not _sha(self.source_sha256) or not _sha(self.body_sha256) or not _positive(self.extraction_contract):
            raise ValueError("EVIDENCE_IDENTITY_INVALID")
        if not isinstance(self.parser_version, str) or re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,79}", self.parser_version) is None:
            raise ValueError("EVIDENCE_IDENTITY_INVALID")
        if type(self.locator) not in _KINDS.values() or type(self.ordinal) is not int or not 0 <= self.ordinal < 1_000_000:
            raise ValueError("EVIDENCE_IDENTITY_INVALID")

    def aad(self) -> bytes:
        identity = {key: str(getattr(self, key)) for key in ("enterprise_id", "knowledge_scope_id", "document_record_id", "document_version_id", "extraction_revision_id")}
        identity.update(schema_version=2, source_sha256=self.source_sha256,
            source_format=self.locator.source_format, parser_version=self.parser_version,
            extraction_contract=self.extraction_contract, locator=self.locator.to_dict(),
            locator_sha256=self.locator.sha256, ordinal=self.ordinal, body_sha256=self.body_sha256)
        return b"anhuan.material.evidence.v2\x00" + canonical_json(identity)

    @property
    def id(self) -> uuid.UUID:
        return uuid.uuid5(uuid.UUID("ca6db2f5-1514-5576-b72f-bad8971ee7a4"), self.aad().decode("ascii"))
