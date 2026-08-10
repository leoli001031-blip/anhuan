from __future__ import annotations

import errno
import hashlib
import html
import json
import os
import re
import stat
import struct
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from xml.etree import ElementTree

from fixture_gate import ENVIRONMENT_DEMO_V01, ValidationFailure, verify_fixture_set
from fixture_gate.validator import (
    FixtureIdentity,
    ManifestEntry,
    _open_entry,
    _open_path_without_symlinks,
    _parse_manifest,
)
from platform_foundation.f0_isolation import load_frozen_f0_isolation


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FROZEN_F0_ISOLATION = load_frozen_f0_isolation()
REGISTERED_SOURCE_ROOT = (
    _FROZEN_F0_ISOLATION.fixture_source_root
    if _FROZEN_F0_ISOLATION is not None
    else Path("/Users/lichenhao/Desktop/环境demo")
)
REGISTERED_FIXTURE_ROOT = PROJECT_ROOT / "fixtures/environment-demo-seed/v0.1"
REGISTERED_CORE_MANIFEST = REGISTERED_FIXTURE_ROOT / "core-manifest.sha256"
REGISTERED_NEGATIVE_MANIFEST = REGISTERED_FIXTURE_ROOT / "negative-manifest.sha256"
REGISTERED_ARTIFACT_ROOT = PROJECT_ROOT / "artifacts/fixture-routing/v0.1"

_READ_CHUNK_BYTES = 1024 * 1024
_TAIL_BYTES = 16 * 1024
_PDF_HEADER = re.compile(rb"^%PDF-(1\.[0-9]|2\.0)(?:\r|\n|\s)")
_PDF_STARTXREF = re.compile(rb"startxref\s+([0-9]{1,20})\s+%%EOF\s*$", re.DOTALL)
_PDF_ENCRYPT_NAME = re.compile(
    rb"/(?:E|#45)(?:n|#6[eE])(?:c|#63)(?:r|#72)(?:y|#79)"
    rb"(?:p|#70)(?:t|#74)(?=$|[\x00\x09\x0a\x0c\x0d ()<>\[\]{}/%])"
)
_PDF_CLASSIC_XREF_HEADER = re.compile(
    rb"^xref[\x09\x0a\x0c\x0d ]+"
    rb"([0-9]{1,10})[ ]+([0-9]{1,10})[\x0a\x0d]+"
)
_PDF_CLASSIC_XREF_ENTRY = re.compile(
    rb"^[0-9]{10}[ ][0-9]{5}[ ][fn](?:[ \x0a\x0d])"
)
_OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
_ZIP_MAGIC = b"PK"
_CFB_FREE = 0xFFFFFFFF
_CFB_END_OF_CHAIN = 0xFFFFFFFE
_CFB_FAT_SECTOR = 0xFFFFFFFD
_CFB_DIFAT_SECTOR = 0xFFFFFFFC
_CFB_MACRO_NAMES = frozenset(
    {"macros", "vba", "_vba_project", "_vba_project_cur", "project", "projectwm"}
)
_CFB_ENCRYPTION_NAMES = frozenset({"encryptedpackage", "encryptioninfo"})
_CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_RELATIONSHIPS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_SMOKE_KEYS = frozenset(
    {
        ("core", 1),
        ("core", 4),
        ("core", 5),
        ("core", 10),
        ("core", 20),
        ("core", 21),
        ("core", 23),
        ("core", 24),
        ("negative", 1),
        ("negative", 2),
    }
)
_TYPE_ROUTES = {
    "PDF": "PDF_NATIVE_OR_OCR_PROBE",
    "JPEG": "IMAGE_OCR_REQUIRED",
    "DOC": "LEGACY_OFFICE_CONVERSION_REQUIRED",
    "DOCX": "DOCX_NATIVE",
    "XLSX": "XLSX_NATIVE",
}
_TYPE_EXTENSIONS = {
    "PDF": frozenset({".pdf"}),
    "JPEG": frozenset({".jpg", ".jpeg"}),
    "DOC": frozenset({".doc"}),
    "DOCX": frozenset({".docx"}),
    "XLSX": frozenset({".xlsx"}),
}
_OUTPUT_NAMES = frozenset({"smoke-plan.json", "route-plan.json", "status.html"})
_STAT_FIELDS = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")


class RouteFailure(Exception):
    """A fail-closed routing error whose public fields are path-free."""

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


@dataclass(frozen=True)
class ReadEvidence:
    sha256: str
    size_bytes: int
    head: bytes
    tail: bytes
    pdf_encrypt_marker: bool


@dataclass(frozen=True)
class CfbDirectoryEntry:
    name: str
    normalized_name: str
    object_type: int
    start_sector: int
    stream_size: int


def _from_validation(error: ValidationFailure) -> RouteFailure:
    return RouteFailure(error.code, group=error.group, line=error.line)


def _entry_failure(
    entry: ManifestEntry, code: str, *, document_id: str | None = None
) -> RouteFailure:
    return RouteFailure(
        code,
        group=entry.group,
        line=entry.line,
        document_id=document_id,
    )


def _stat_identity(file_stat: os.stat_result) -> tuple[int, ...]:
    return tuple(int(getattr(file_stat, field)) for field in _STAT_FIELDS)


def _read_evidence(handle: BinaryIO) -> ReadEvidence:
    handle.seek(0)
    digest = hashlib.sha256()
    head = bytearray()
    tail = b""
    marker_overlap = b""
    pdf_encrypt_marker = False
    size_bytes = 0
    while chunk := handle.read(_READ_CHUNK_BYTES):
        digest.update(chunk)
        size_bytes += len(chunk)
        if len(head) < 64:
            head.extend(chunk[: 64 - len(head)])
        tail = (tail + chunk)[-_TAIL_BYTES:]
        marker_window = marker_overlap + chunk
        if _PDF_ENCRYPT_NAME.search(marker_window) is not None:
            pdf_encrypt_marker = True
        marker_overlap = marker_window[-64:]
    handle.seek(0)
    return ReadEvidence(
        sha256=digest.hexdigest(),
        size_bytes=size_bytes,
        head=bytes(head),
        tail=tail,
        pdf_encrypt_marker=pdf_encrypt_marker,
    )


def _read_limited_zip_member(
    archive: zipfile.ZipFile, info: zipfile.ZipInfo, *, limit: int = 1024 * 1024
) -> bytes:
    if info.file_size > limit:
        raise RouteFailure("OOXML_METADATA_TOO_LARGE")
    if info.file_size and info.compress_size == 0:
        raise RouteFailure("OOXML_SUSPICIOUS_COMPRESSION")
    if info.file_size > max(1, info.compress_size) * 200:
        raise RouteFailure("OOXML_SUSPICIOUS_COMPRESSION")
    return archive.read(info)


def _validate_zip_name(name: str) -> str:
    if not name or "\\" in name:
        raise RouteFailure("INVALID_OOXML_MEMBER")
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise RouteFailure("INVALID_OOXML_MEMBER")
    candidate = name[:-1] if name.endswith("/") else name
    path = PurePosixPath(candidate)
    if not candidate or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RouteFailure("INVALID_OOXML_MEMBER")
    return unicodedata.normalize("NFC", path.as_posix()).casefold()


def _xml_root(data: bytes, code: str) -> ElementTree.Element:
    lowered = data.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise RouteFailure(code)
    try:
        return ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise RouteFailure(code) from error


def _resolve_relationship_target(
    relationships_name: str, target: str, names: dict[str, zipfile.ZipInfo]
) -> str:
    lowered = target.casefold()
    if (
        not target
        or target.startswith("/")
        or "\\" in target
        or "?" in target
        or "#" in target
        or any(ord(character) < 32 or ord(character) == 127 for character in target)
        or any(token in lowered for token in ("%2e", "%2f", "%5c", "%25"))
        or ":" in target.split("/", 1)[0]
    ):
        raise RouteFailure("INVALID_OOXML_RELATIONSHIPS")

    if relationships_name == "_rels/.rels":
        base_parts: list[str] = []
    else:
        relationship_path = PurePosixPath(relationships_name)
        parts = relationship_path.parts
        if len(parts) < 3 or parts[-2] != "_rels" or not parts[-1].endswith(".rels"):
            raise RouteFailure("INVALID_OOXML_RELATIONSHIPS")
        source_name = parts[-1][: -len(".rels")]
        if not source_name:
            raise RouteFailure("INVALID_OOXML_RELATIONSHIPS")
        base_parts = list(parts[:-2])

    resolved_parts = base_parts
    for part in target.split("/"):
        if part in {"", "."}:
            raise RouteFailure("INVALID_OOXML_RELATIONSHIPS")
        if part == "..":
            if not resolved_parts:
                raise RouteFailure("INVALID_OOXML_RELATIONSHIPS")
            resolved_parts.pop()
        else:
            resolved_parts.append(part)
    resolved = PurePosixPath(*resolved_parts).as_posix()
    if resolved not in names or names[resolved].is_dir():
        raise RouteFailure("INVALID_OOXML_RELATIONSHIPS")
    return resolved


def _parse_relationships(
    data: bytes,
    relationships_name: str,
    names: dict[str, zipfile.ZipInfo],
) -> list[tuple[str, str]]:
    root = _xml_root(data, "INVALID_OOXML_RELATIONSHIPS")
    relationship_tag = f"{{{_RELATIONSHIPS_NS}}}Relationship"
    if root.tag != f"{{{_RELATIONSHIPS_NS}}}Relationships":
        raise RouteFailure("INVALID_OOXML_RELATIONSHIPS")
    relationships: list[tuple[str, str]] = []
    for relationship in list(root):
        if relationship.tag != relationship_tag:
            raise RouteFailure("INVALID_OOXML_RELATIONSHIPS")
        target_mode = relationship.attrib.get("TargetMode", "")
        if target_mode.casefold() == "external":
            raise RouteFailure("EXTERNAL_OOXML_RELATIONSHIP")
        if target_mode.casefold() not in {"", "internal"}:
            raise RouteFailure("INVALID_OOXML_RELATIONSHIPS")
        relationship_type = relationship.attrib.get("Type", "")
        target = relationship.attrib.get("Target", "")
        if not relationship_type:
            raise RouteFailure("INVALID_OOXML_RELATIONSHIPS")
        lowered_type = relationship_type.casefold()
        if "macro" in lowered_type or "vba" in lowered_type:
            raise RouteFailure("MACRO_ENABLED_INPUT")
        relationships.append(
            (
                relationship_type,
                _resolve_relationship_target(relationships_name, target, names),
            )
        )
    return relationships


def _inspect_ooxml(handle: BinaryIO) -> str:
    try:
        with zipfile.ZipFile(handle, mode="r") as archive:
            infos = archive.infolist()
            if not infos or len(infos) > 2000:
                raise RouteFailure("INVALID_OOXML_CONTAINER")

            names: dict[str, zipfile.ZipInfo] = {}
            normalized_names: set[str] = set()
            total_uncompressed = 0
            for info in infos:
                normalized = _validate_zip_name(info.filename)
                if info.filename in names or normalized in normalized_names:
                    raise RouteFailure("DUPLICATE_OOXML_MEMBER")
                names[info.filename] = info
                normalized_names.add(normalized)
                if info.flag_bits & 0x1:
                    raise RouteFailure("ENCRYPTED_INPUT")
                total_uncompressed += info.file_size
                if info.file_size > 128 * 1024 * 1024:
                    raise RouteFailure("OOXML_MEMBER_TOO_LARGE")
                if info.file_size > max(1, info.compress_size) * 200:
                    raise RouteFailure("OOXML_SUSPICIOUS_COMPRESSION")
            if total_uncompressed > 512 * 1024 * 1024:
                raise RouteFailure("OOXML_CONTAINER_TOO_LARGE")

            required = {"[Content_Types].xml", "_rels/.rels"}
            if not required.issubset(names):
                raise RouteFailure("INVALID_OOXML_CONTAINER")
            if any(
                "vba" in info.filename.casefold() or "macro" in info.filename.casefold()
                for info in infos
            ):
                raise RouteFailure("MACRO_ENABLED_INPUT")

            content_types_data = _read_limited_zip_member(
                archive, names["[Content_Types].xml"]
            )
            if b"macro" in content_types_data.lower() or b"vba" in content_types_data.lower():
                raise RouteFailure("MACRO_ENABLED_INPUT")
            content_types = _xml_root(content_types_data, "INVALID_CONTENT_TYPES")
            if content_types.tag != f"{{{_CONTENT_TYPES_NS}}}Types":
                raise RouteFailure("INVALID_CONTENT_TYPES")

            main_parts: list[tuple[str, str]] = []
            default_tag = f"{{{_CONTENT_TYPES_NS}}}Default"
            override_tag = f"{{{_CONTENT_TYPES_NS}}}Override"
            for element in list(content_types):
                if element.tag == default_tag:
                    continue
                if element.tag != override_tag:
                    raise RouteFailure("INVALID_CONTENT_TYPES")
                raw_part_name = element.attrib.get("PartName", "")
                if not raw_part_name.startswith("/"):
                    raise RouteFailure("INVALID_CONTENT_TYPES")
                part_name = raw_part_name[1:]
                content_type = element.attrib.get("ContentType", "")
                if not content_type or part_name not in names or names[part_name].is_dir():
                    raise RouteFailure("INVALID_CONTENT_TYPES")
                lowered_type = content_type.casefold()
                if "macro" in lowered_type or "vba" in lowered_type:
                    raise RouteFailure("MACRO_ENABLED_INPUT")
                if content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml":
                    main_parts.append(("DOCX", part_name))
                elif content_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml":
                    main_parts.append(("XLSX", part_name))
            if len(main_parts) != 1:
                raise RouteFailure("AMBIGUOUS_OOXML_MAIN_PART")
            document_type, main_part = main_parts[0]
            expected_part = "word/document.xml" if document_type == "DOCX" else "xl/workbook.xml"
            if main_part != expected_part or main_part not in names:
                raise RouteFailure("INVALID_OOXML_MAIN_PART")

            root_relationships = _parse_relationships(
                _read_limited_zip_member(archive, names["_rels/.rels"]),
                "_rels/.rels",
                names,
            )
            office_targets: list[str] = []
            for relationship_type, target in root_relationships:
                if relationship_type.endswith("/officeDocument"):
                    office_targets.append(target)
            if office_targets != [main_part]:
                raise RouteFailure("INVALID_OOXML_RELATIONSHIPS")

            for name, info in names.items():
                if not name.casefold().endswith(".rels") or name == "_rels/.rels":
                    continue
                _parse_relationships(
                    _read_limited_zip_member(archive, info, limit=2 * 1024 * 1024),
                    name,
                    names,
                )

            # Read every member only as an integrity stream.  The bytes are
            # discarded; this validates ZIP decompression and CRC without
            # extracting or interpreting document content.
            for info in infos:
                if info.is_dir():
                    continue
                with archive.open(info, "r") as member:
                    while member.read(_READ_CHUNK_BYTES):
                        pass
            return document_type
    except RouteFailure:
        raise
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        KeyError,
        RuntimeError,
        NotImplementedError,
        EOFError,
        ValueError,
        OSError,
    ) as error:
        raise RouteFailure("INVALID_OOXML_CONTAINER") from error
    finally:
        handle.seek(0)


def _validate_pdf(handle: BinaryIO, evidence: ReadEvidence) -> None:
    if _PDF_HEADER.match(evidence.head) is None:
        raise RouteFailure("INVALID_PDF_HEADER")
    tail_match = _PDF_STARTXREF.search(evidence.tail)
    if tail_match is None:
        raise RouteFailure("TRUNCATED_PDF")
    if evidence.pdf_encrypt_marker:
        raise RouteFailure("ENCRYPTED_INPUT")
    xref_offset = int(tail_match.group(1))
    if xref_offset < 0 or xref_offset >= evidence.size_bytes:
        raise RouteFailure("INVALID_PDF_XREF")
    handle.seek(xref_offset)
    xref_prefix = handle.read(4096)
    handle.seek(0)
    stripped = xref_prefix.lstrip()
    classic = _PDF_CLASSIC_XREF_HEADER.match(stripped)
    if classic is not None:
        entry_count = int(classic.group(2))
        remainder = stripped[classic.end() :]
        if entry_count > 0 and _PDF_CLASSIC_XREF_ENTRY.match(remainder):
            return
        if (
            entry_count == 0
            and re.match(rb"^trailer\s*<<", remainder)
            and re.search(rb"/Size\s*[0-9]+\b", remainder)
        ):
            return
    if (
        re.match(rb"[0-9]+\s+[0-9]+\s+obj\b", stripped)
        and re.search(rb"/Type\s*/XRef\b", stripped)
        and re.search(rb"/Size\s+[0-9]+\b", stripped)
        and re.search(rb"\bstream(?:\r\n|\r|\n)", stripped)
    ):
        return
    raise RouteFailure("INVALID_PDF_XREF")


def _validate_jpeg(handle: BinaryIO, size_bytes: int, tail: bytes) -> None:
    handle.seek(0)
    if handle.read(2) != b"\xff\xd8":
        raise RouteFailure("INVALID_JPEG_HEADER")
    found_sof = False
    found_sos = False
    in_entropy = False
    restart_markers = set(range(0xD0, 0xD8))
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while handle.tell() < size_bytes:
        marker_prefix = handle.read(1)
        if in_entropy and marker_prefix != b"\xff":
            continue
        if marker_prefix != b"\xff":
            raise RouteFailure("INVALID_JPEG_STRUCTURE")
        marker = handle.read(1)
        while marker == b"\xff":
            marker = handle.read(1)
        if not marker:
            raise RouteFailure("TRUNCATED_JPEG")
        marker_value = marker[0]
        if in_entropy and (marker_value == 0x00 or marker_value in restart_markers):
            continue
        in_entropy = False
        if marker_value == 0xD9:
            if not found_sof or not found_sos or handle.tell() != size_bytes:
                raise RouteFailure("INVALID_JPEG_STRUCTURE")
            handle.seek(0)
            return
        if marker_value in restart_markers or marker_value in {0x00, 0x01, 0xD8}:
            raise RouteFailure("INVALID_JPEG_STRUCTURE")
        length_bytes = handle.read(2)
        if len(length_bytes) != 2:
            raise RouteFailure("TRUNCATED_JPEG")
        segment_length = int.from_bytes(length_bytes, "big")
        if segment_length < 2 or handle.tell() + segment_length - 2 > size_bytes:
            raise RouteFailure("INVALID_JPEG_STRUCTURE")
        payload = handle.read(segment_length - 2)
        if len(payload) != segment_length - 2:
            raise RouteFailure("TRUNCATED_JPEG")
        if marker_value in sof_markers:
            if len(payload) < 6:
                raise RouteFailure("INVALID_JPEG_STRUCTURE")
            precision = payload[0]
            height = int.from_bytes(payload[1:3], "big")
            width = int.from_bytes(payload[3:5], "big")
            components = payload[5]
            if (
                precision not in {8, 12}
                or height == 0
                or width == 0
                or components not in range(1, 5)
                or len(payload) != 6 + 3 * components
            ):
                raise RouteFailure("INVALID_JPEG_STRUCTURE")
            component_ids: set[int] = set()
            for index in range(components):
                component_id, sampling, table = payload[6 + index * 3 : 9 + index * 3]
                horizontal = sampling >> 4
                vertical = sampling & 0x0F
                if (
                    component_id == 0
                    or component_id in component_ids
                    or horizontal not in range(1, 5)
                    or vertical not in range(1, 5)
                    or table > 3
                ):
                    raise RouteFailure("INVALID_JPEG_STRUCTURE")
                component_ids.add(component_id)
            found_sof = True
        if marker_value == 0xDA:
            if not found_sof or len(payload) < 4:
                raise RouteFailure("INVALID_JPEG_STRUCTURE")
            scan_components = payload[0]
            if (
                scan_components not in range(1, 5)
                or len(payload) != 1 + 2 * scan_components + 3
            ):
                raise RouteFailure("INVALID_JPEG_STRUCTURE")
            for index in range(scan_components):
                selector = payload[2 + index * 2]
                if selector >> 4 > 3 or selector & 0x0F > 3:
                    raise RouteFailure("INVALID_JPEG_STRUCTURE")
            spectral_start, spectral_end, approximation = payload[-3:]
            if (
                spectral_start > spectral_end
                or spectral_end > 63
                or approximation >> 4 > 13
                or approximation & 0x0F > 13
            ):
                raise RouteFailure("INVALID_JPEG_STRUCTURE")
            found_sos = True
            in_entropy = True
    handle.seek(0)
    if not found_sof or not found_sos or not tail.endswith(b"\xff\xd9"):
        raise RouteFailure("TRUNCATED_JPEG")
    raise RouteFailure("INVALID_JPEG_STRUCTURE")


def _cfb_sector(handle: BinaryIO, sector_id: int, sector_size: int, size_bytes: int) -> bytes:
    if sector_id < 0:
        raise RouteFailure("INVALID_CFB_CONTAINER")
    offset = (sector_id + 1) * sector_size
    if offset < 512 or offset + sector_size > size_bytes:
        raise RouteFailure("INVALID_CFB_CONTAINER")
    handle.seek(offset)
    data = handle.read(sector_size)
    if len(data) != sector_size:
        raise RouteFailure("INVALID_CFB_CONTAINER")
    return data


def _validate_legacy_doc(handle: BinaryIO, size_bytes: int) -> None:
    handle.seek(0)
    header = handle.read(512)
    if len(header) != 512 or not header.startswith(_OLE_MAGIC):
        raise RouteFailure("INVALID_CFB_CONTAINER")
    major_version = struct.unpack_from("<H", header, 26)[0]
    byte_order = struct.unpack_from("<H", header, 28)[0]
    sector_shift = struct.unpack_from("<H", header, 30)[0]
    mini_sector_shift = struct.unpack_from("<H", header, 32)[0]
    number_of_directory_sectors = struct.unpack_from("<I", header, 40)[0]
    number_of_fat_sectors = struct.unpack_from("<I", header, 44)[0]
    first_directory_sector = struct.unpack_from("<I", header, 48)[0]
    mini_stream_cutoff = struct.unpack_from("<I", header, 56)[0]
    first_mini_fat_sector = struct.unpack_from("<I", header, 60)[0]
    number_of_mini_fat_sectors = struct.unpack_from("<I", header, 64)[0]
    first_difat_sector = struct.unpack_from("<I", header, 68)[0]
    number_of_difat_sectors = struct.unpack_from("<I", header, 72)[0]
    expected_shift = 9 if major_version == 3 else 12 if major_version == 4 else -1
    if (
        byte_order != 0xFFFE
        or sector_shift != expected_shift
        or mini_sector_shift != 6
        or mini_stream_cutoff != 4096
        or number_of_fat_sectors == 0
        or number_of_difat_sectors != 0
        or first_difat_sector != _CFB_END_OF_CHAIN
        or (major_version == 3 and number_of_directory_sectors != 0)
    ):
        raise RouteFailure("INVALID_CFB_CONTAINER")
    sector_size = 1 << sector_shift
    if size_bytes < sector_size * 2:
        raise RouteFailure("INVALID_CFB_CONTAINER")

    payload_bytes = size_bytes - sector_size
    complete_sector_count, partial_sector_bytes = divmod(payload_bytes, sector_size)
    physical_sector_count = complete_sector_count + bool(partial_sector_bytes)
    partial_sector_id = complete_sector_count if partial_sector_bytes else None

    fat_sector_ids = [
        sector_id
        for sector_id in struct.unpack_from("<109I", header, 76)
        if sector_id != _CFB_FREE
    ]
    if (
        len(fat_sector_ids) != number_of_fat_sectors
        or len(set(fat_sector_ids)) != len(fat_sector_ids)
        or any(sector_id >= complete_sector_count for sector_id in fat_sector_ids)
    ):
        raise RouteFailure("INVALID_CFB_CONTAINER")
    fat_entries: list[int] = []
    for sector_id in fat_sector_ids:
        sector = _cfb_sector(handle, sector_id, sector_size, size_bytes)
        fat_entries.extend(struct.unpack(f"<{sector_size // 4}I", sector))
    if physical_sector_count > len(fat_entries) or any(
        fat_entries[sector_id] != _CFB_FAT_SECTOR for sector_id in fat_sector_ids
    ):
        raise RouteFailure("INVALID_CFB_CONTAINER")

    directory_bytes = bytearray()
    directory_sectors: set[int] = set()
    sector_id = first_directory_sector
    while sector_id != _CFB_END_OF_CHAIN:
        if (
            sector_id in directory_sectors
            or sector_id in fat_sector_ids
            or sector_id >= complete_sector_count
            or len(directory_sectors) >= physical_sector_count
        ):
            raise RouteFailure("INVALID_CFB_CONTAINER")
        directory_sectors.add(sector_id)
        directory_bytes.extend(_cfb_sector(handle, sector_id, sector_size, size_bytes))
        sector_id = fat_entries[sector_id]
    if major_version == 4 and len(directory_sectors) != number_of_directory_sectors:
        raise RouteFailure("INVALID_CFB_CONTAINER")

    mini_fat_sectors: set[int] = set()
    if number_of_mini_fat_sectors == 0:
        if first_mini_fat_sector not in {_CFB_END_OF_CHAIN, _CFB_FREE}:
            raise RouteFailure("INVALID_CFB_CONTAINER")
    else:
        sector_id = first_mini_fat_sector
        for _ in range(number_of_mini_fat_sectors):
            if (
                sector_id in mini_fat_sectors
                or sector_id in fat_sector_ids
                or sector_id in directory_sectors
                or sector_id >= complete_sector_count
            ):
                raise RouteFailure("INVALID_CFB_CONTAINER")
            mini_fat_sectors.add(sector_id)
            _cfb_sector(handle, sector_id, sector_size, size_bytes)
            sector_id = fat_entries[sector_id]
        if sector_id != _CFB_END_OF_CHAIN:
            raise RouteFailure("INVALID_CFB_CONTAINER")

    entries: dict[str, CfbDirectoryEntry] = {}
    for offset in range(0, len(directory_bytes), 128):
        entry = directory_bytes[offset : offset + 128]
        if len(entry) != 128:
            raise RouteFailure("INVALID_CFB_CONTAINER")
        name_length = struct.unpack_from("<H", entry, 64)[0]
        object_type = entry[66]
        if object_type == 0:
            continue
        if (
            object_type not in {1, 2, 5}
            or name_length < 2
            or name_length > 64
            or name_length % 2
            or entry[name_length - 2 : name_length] != b"\x00\x00"
        ):
            raise RouteFailure("INVALID_CFB_CONTAINER")
        try:
            name = bytes(entry[: name_length - 2]).decode("utf-16le")
        except UnicodeError as error:
            raise RouteFailure("INVALID_CFB_CONTAINER") from error
        normalized_name = unicodedata.normalize("NFC", name).casefold()
        if not normalized_name or normalized_name in entries:
            raise RouteFailure("INVALID_CFB_CONTAINER")
        start_sector = struct.unpack_from("<I", entry, 116)[0]
        stream_size = struct.unpack_from("<Q", entry, 120)[0]
        if major_version == 3 and stream_size >> 32:
            raise RouteFailure("INVALID_CFB_CONTAINER")
        entries[normalized_name] = CfbDirectoryEntry(
            name=name,
            normalized_name=normalized_name,
            object_type=object_type,
            start_sector=start_sector,
            stream_size=stream_size,
        )

    if any(name in entries for name in _CFB_ENCRYPTION_NAMES):
        raise RouteFailure("ENCRYPTED_INPUT")
    if any(name in entries for name in _CFB_MACRO_NAMES):
        raise RouteFailure("MACRO_ENABLED_INPUT")

    root_entry = entries.get("root entry")
    word_entry = entries.get("worddocument")
    if (
        root_entry is None
        or root_entry.object_type != 5
        or word_entry is None
        or word_entry.object_type != 2
        or word_entry.stream_size < mini_stream_cutoff
    ):
        raise RouteFailure("INVALID_CFB_DOCUMENT")

    metadata_sectors = set(fat_sector_ids) | directory_sectors | mini_fat_sectors
    sector_owners: dict[int, str] = {}
    stream_chains: dict[str, list[int]] = {}
    for cfb_entry in entries.values():
        uses_regular_fat = (
            cfb_entry.object_type == 5 and cfb_entry.stream_size > 0
        ) or (
            cfb_entry.object_type == 2
            and cfb_entry.stream_size >= mini_stream_cutoff
        )
        if not uses_regular_fat:
            continue
        expected_sectors = (cfb_entry.stream_size + sector_size - 1) // sector_size
        if expected_sectors == 0:
            raise RouteFailure("INVALID_CFB_CONTAINER")
        chain: list[int] = []
        sector_id = cfb_entry.start_sector
        for index in range(expected_sectors):
            if (
                sector_id >= physical_sector_count
                or sector_id in metadata_sectors
                or sector_id in sector_owners
                or (sector_id == partial_sector_id and index != expected_sectors - 1)
            ):
                raise RouteFailure("INVALID_CFB_CONTAINER")
            sector_owners[sector_id] = cfb_entry.normalized_name
            chain.append(sector_id)
            next_sector = fat_entries[sector_id]
            if index == expected_sectors - 1:
                if next_sector != _CFB_END_OF_CHAIN:
                    raise RouteFailure("INVALID_CFB_CONTAINER")
            elif next_sector in {
                _CFB_FREE,
                _CFB_END_OF_CHAIN,
                _CFB_FAT_SECTOR,
                _CFB_DIFAT_SECTOR,
            }:
                raise RouteFailure("INVALID_CFB_CONTAINER")
            sector_id = next_sector
        if chain[-1] == partial_sector_id:
            declared_tail = cfb_entry.stream_size - (expected_sectors - 1) * sector_size
            if declared_tail != partial_sector_bytes:
                raise RouteFailure("INVALID_CFB_CONTAINER")
        stream_chains[cfb_entry.normalized_name] = chain

    if partial_sector_id is not None and partial_sector_id not in sector_owners:
        raise RouteFailure("INVALID_CFB_CONTAINER")
    accounted_sectors = metadata_sectors | set(sector_owners)
    if accounted_sectors != set(range(physical_sector_count)):
        raise RouteFailure("INVALID_CFB_CONTAINER")
    if partial_sector_id is None and physical_sector_count:
        final_owner = sector_owners.get(physical_sector_count - 1)
        if (
            final_owner is not None
            and entries[final_owner].stream_size % sector_size != 0
        ):
            raise RouteFailure("INVALID_CFB_CONTAINER")

    word_chain = stream_chains.get("worddocument")
    if not word_chain:
        raise RouteFailure("INVALID_CFB_DOCUMENT")
    fib = bytearray()
    remaining = min(12, word_entry.stream_size)
    for word_sector in word_chain:
        if remaining == 0:
            break
        offset = (word_sector + 1) * sector_size
        available = min(sector_size, size_bytes - offset, remaining)
        handle.seek(offset)
        chunk = handle.read(available)
        if len(chunk) != available:
            raise RouteFailure("INVALID_CFB_CONTAINER")
        fib.extend(chunk)
        remaining -= available
    handle.seek(0)
    if len(fib) < 12 or struct.unpack_from("<H", fib, 0)[0] != 0xA5EC:
        raise RouteFailure("INVALID_CFB_DOCUMENT")
    flags = struct.unpack_from("<H", fib, 10)[0]
    if flags & 0x8100:
        raise RouteFailure("ENCRYPTED_INPUT")


def _detect_type(handle: BinaryIO, evidence: ReadEvidence, entry: ManifestEntry) -> str:
    if evidence.head.startswith(b"%PDF-"):
        _validate_pdf(handle, evidence)
        document_type = "PDF"
    elif evidence.head.startswith(b"\xff\xd8\xff"):
        _validate_jpeg(handle, evidence.size_bytes, evidence.tail)
        document_type = "JPEG"
    elif evidence.head.startswith(_OLE_MAGIC):
        _validate_legacy_doc(handle, evidence.size_bytes)
        document_type = "DOC"
    elif evidence.head.startswith(_ZIP_MAGIC):
        document_type = _inspect_ooxml(handle)
    else:
        raise _entry_failure(entry, "UNKNOWN_FORMAT")

    suffix = PurePosixPath(entry.relative_path).suffix.casefold()
    if suffix not in _TYPE_EXTENSIONS[document_type]:
        raise _entry_failure(entry, "EXTENSION_TYPE_MISMATCH")
    return document_type


def _document_id(
    fixture_set_id: str,
    fixture_version: str,
    entry: ManifestEntry,
    file_sha256: str,
) -> str:
    material = "\0".join(
        (
            fixture_set_id,
            fixture_version,
            entry.group,
            str(entry.line),
            file_sha256,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _route_entry(
    root_descriptor: int,
    entry: ManifestEntry,
    *,
    fixture_set_id: str,
    fixture_version: str,
) -> dict[str, object]:
    try:
        descriptor = _open_entry(root_descriptor, entry)
    except ValidationFailure as error:
        raise _from_validation(error) from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise _entry_failure(entry, "NOT_REGULAR_FILE")
        try:
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                evidence = _read_evidence(handle)
                if evidence.sha256 != entry.expected_sha256:
                    raise _entry_failure(entry, "HASH_MISMATCH")
                if evidence.size_bytes != before.st_size:
                    raise _entry_failure(entry, "FILE_CHANGED_DURING_ROUTE")
                document_id = _document_id(
                    fixture_set_id, fixture_version, entry, evidence.sha256
                )
                try:
                    document_type = _detect_type(handle, evidence, entry)
                except RouteFailure as error:
                    if error.group is None:
                        raise _entry_failure(
                            entry, error.code, document_id=document_id
                        ) from error
                    raise
        except OSError as error:
            raise _entry_failure(entry, "FILE_UNREADABLE") from error
        after = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(after):
            raise _entry_failure(
                entry, "FILE_CHANGED_DURING_ROUTE", document_id=document_id
            )
    finally:
        os.close(descriptor)

    negative = entry.group == "negative"
    return {
        "group": entry.group,
        "line": entry.line,
        "document_id": document_id,
        "type": document_type,
        "route": _TYPE_ROUTES[document_type],
        "corpus_role": "NEGATIVE_TEST_ONLY" if negative else "CORE_FIXTURE",
        "enterprise_fact_allowed": not negative,
        "current_regulation_allowed": False,
        "search_publish_allowed": False,
    }


def _selected_entries(entries: list[ManifestEntry], profile: str) -> list[ManifestEntry]:
    if profile == "full":
        return entries
    if profile != "smoke":
        raise RouteFailure("INVALID_PROFILE")
    selected = [entry for entry in entries if (entry.group, entry.line) in _SMOKE_KEYS]
    if len(selected) != len(_SMOKE_KEYS):
        raise RouteFailure("SMOKE_PROFILE_INCOMPLETE")
    return selected


def build_route_plan(
    *,
    source_root: Path,
    core_manifest: Path,
    negative_manifest: Path,
    profile: str,
    expected_identity: FixtureIdentity | None = None,
) -> dict[str, object]:
    try:
        gate_audit = verify_fixture_set(
            source_root=source_root,
            core_manifest=core_manifest,
            negative_manifest=negative_manifest,
            expected_identity=expected_identity,
        )
        seen_paths: set[str] = set()
        core_entries, core_digest = _parse_manifest(core_manifest, "core", seen_paths)
        negative_entries, negative_digest = _parse_manifest(
            negative_manifest, "negative", seen_paths
        )
    except ValidationFailure as error:
        raise _from_validation(error) from error

    gate_manifest = gate_audit["manifest_sha256"]
    if core_digest != gate_manifest["core"] or negative_digest != gate_manifest["negative"]:
        raise RouteFailure("MANIFEST_CHANGED_DURING_ROUTE")
    if expected_identity is not None and (
        core_digest != expected_identity.core_manifest_sha256
        or negative_digest != expected_identity.negative_manifest_sha256
    ):
        raise RouteFailure("MANIFEST_IDENTITY_MISMATCH")

    entries = _selected_entries(core_entries + negative_entries, profile)
    try:
        root_descriptor = _open_path_without_symlinks(
            source_root,
            final_flags=os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            unavailable_code="SOURCE_ROOT_UNAVAILABLE",
            symlink_code="SOURCE_ROOT_SYMLINK",
        )
    except ValidationFailure as error:
        raise _from_validation(error) from error
    try:
        routed = [
            _route_entry(
                root_descriptor,
                entry,
                fixture_set_id=str(gate_audit["fixture_set_id"]),
                fixture_version=str(gate_audit["fixture_version"]),
            )
            for entry in entries
        ]
    finally:
        os.close(root_descriptor)

    type_summary = {document_type: 0 for document_type in _TYPE_ROUTES}
    route_summary = {route: 0 for route in _TYPE_ROUTES.values()}
    group_summary = {"core": 0, "negative": 0}
    for entry in routed:
        type_summary[str(entry["type"])] += 1
        route_summary[str(entry["route"])] += 1
        group_summary[str(entry["group"])] += 1

    return {
        "schema_version": "fixture-routing-plan/v1",
        "fixture_set_id": gate_audit["fixture_set_id"],
        "fixture_version": gate_audit["fixture_version"],
        "profile": profile,
        "labels": ["FIXTURE_ONLY", "PIPELINE_REGRESSION_ONLY"],
        "policy": {
            "external_processing": "DENY",
            "model_training": "DENY",
            "production_use": "DENY",
            "public_display": "DENY",
        },
        "summary": {
            "total": len(routed),
            "groups": group_summary,
            "types": type_summary,
            "routes": route_summary,
        },
        "entries": routed,
    }


def _open_output_directory(directory: Path) -> int:
    if ".." in directory.parts:
        raise RouteFailure("OUTPUT_WRITE_FAILED")
    absolute = directory if directory.is_absolute() else Path.cwd() / directory
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        current_descriptor = os.open(absolute.anchor, directory_flags)
    except OSError as error:
        raise RouteFailure("OUTPUT_WRITE_FAILED") from error
    try:
        for part in absolute.parts[1:]:
            try:
                next_descriptor = os.open(
                    part, directory_flags, dir_fd=current_descriptor
                )
            except FileNotFoundError:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=current_descriptor)
                except FileExistsError:
                    pass
                next_descriptor = os.open(
                    part, directory_flags, dir_fd=current_descriptor
                )
            os.close(current_descriptor)
            current_descriptor = next_descriptor
        return current_descriptor
    except OSError as error:
        os.close(current_descriptor)
        raise RouteFailure("OUTPUT_WRITE_FAILED") from error


def _existing_output_matches(
    directory_descriptor: int, filename: str, expected: bytes
) -> bool:
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(
                filename,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                dir_fd=directory_descriptor,
            )
        except FileNotFoundError:
            return False
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size != len(expected)
        ):
            raise RouteFailure("OUTPUT_WRITE_FAILED")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, _READ_CHUNK_BYTES):
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            _stat_identity(before) != _stat_identity(after)
            or after.st_nlink != 1
            or b"".join(chunks) != expected
        ):
            raise RouteFailure("OUTPUT_WRITE_FAILED")
        return True
    except RouteFailure:
        raise
    except OSError as error:
        raise RouteFailure("OUTPUT_WRITE_FAILED") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _create_output(
    directory_descriptor: int, filename: str, content: bytes
) -> None:
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(
                filename,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
                0o600,
                dir_fd=directory_descriptor,
            )
        except OSError as error:
            if error.errno == errno.EEXIST and _existing_output_matches(
                directory_descriptor, filename, content
            ):
                return
            raise
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise RouteFailure("OUTPUT_WRITE_FAILED")
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise RouteFailure("OUTPUT_WRITE_FAILED")
            view = view[written:]
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or not stat.S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or after.st_size != len(content)
        ):
            raise RouteFailure("OUTPUT_WRITE_FAILED")
    except RouteFailure:
        raise
    except OSError as error:
        raise RouteFailure("OUTPUT_WRITE_FAILED") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_output_batch(artifact_root: Path, outputs: dict[str, str]) -> None:
    if not outputs or any(filename not in _OUTPUT_NAMES for filename in outputs):
        raise RouteFailure("OUTPUT_WRITE_FAILED")
    encoded = {
        filename: content.encode("utf-8") for filename, content in outputs.items()
    }
    directory_descriptor = _open_output_directory(artifact_root)
    try:
        missing = [
            filename
            for filename, content in encoded.items()
            if not _existing_output_matches(directory_descriptor, filename, content)
        ]
        for filename in missing:
            _create_output(directory_descriptor, filename, encoded[filename])
        os.fsync(directory_descriptor)
    except RouteFailure:
        raise
    except OSError as error:
        raise RouteFailure("OUTPUT_WRITE_FAILED") from error
    finally:
        os.close(directory_descriptor)


def render_status_html(plan: dict[str, object]) -> str:
    summary = plan["summary"]
    types = summary["types"]
    routes = summary["routes"]
    type_cards = "".join(
        f'<li><strong>{html.escape(name)}</strong><span>{int(count)}</span></li>'
        for name, count in types.items()
    )
    route_rows = "".join(
        f'<tr><td>{html.escape(name)}</td><td>{int(count)}</td></tr>'
        for name, count in routes.items()
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
  <title>Fixture 路由状态</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:#f4f7f5; color:#17352b; }}
    body {{ margin:0; padding:40px; }} main {{ max-width:960px; margin:auto; }}
    header {{ background:#143f33; color:#fff; padding:32px; border-radius:18px; }}
    .tag {{ display:inline-block; padding:6px 10px; background:#d7f36b; color:#17352b; border-radius:999px; font-weight:700; }}
    h1 {{ margin:18px 0 8px; }} .meta {{ color:#cfe1da; }}
    section {{ margin-top:22px; background:#fff; padding:24px; border-radius:18px; box-shadow:0 8px 30px rgba(20,63,51,.08); }}
    ul {{ list-style:none; padding:0; display:grid; grid-template-columns:repeat(5,1fr); gap:12px; }}
    li {{ background:#edf4f0; padding:18px; border-radius:12px; }} li span {{ display:block; margin-top:10px; font-size:28px; }}
    table {{ width:100%; border-collapse:collapse; }} td,th {{ padding:12px; border-bottom:1px solid #dde8e2; text-align:left; }}
    .deny {{ color:#9f2f2f; font-weight:700; }} footer {{ margin-top:20px; color:#557269; }}
    @media (max-width:720px) {{ body {{ padding:18px; }} ul {{ grid-template-columns:repeat(2,1fr); }} }}
  </style>
</head>
<body><main>
  <header><span class="tag">FIXTURE_ONLY</span><h1>本地资料路由状态</h1>
    <div class="meta">PIPELINE_REGRESSION_ONLY · {int(summary['total'])} 份 · 外部处理 <b>DENY</b></div></header>
  <section><h2>格式聚合</h2><ul>{type_cards}</ul></section>
  <section><h2>处理路线</h2><table><thead><tr><th>路线</th><th>数量</th></tr></thead><tbody>{route_rows}</tbody></table></section>
  <section><h2>负样本边界</h2><p class="deny">企业事实、现行法规和搜索发布三道闸门全部关闭。</p></section>
  <footer>离线只读状态页 · 不包含文件名、正文或源路径</footer>
</main></body></html>
"""


def write_route_outputs(
    plan: dict[str, object], *, artifact_root: Path = REGISTERED_ARTIFACT_ROOT
) -> None:
    profile = str(plan.get("profile"))
    json_text = json.dumps(plan, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if profile == "smoke":
        _write_output_batch(artifact_root, {"smoke-plan.json": json_text})
        return
    if profile == "full":
        _write_output_batch(
            artifact_root,
            {
                "route-plan.json": json_text,
                "status.html": render_status_html(plan),
            },
        )
        return
    raise RouteFailure("INVALID_PROFILE")
