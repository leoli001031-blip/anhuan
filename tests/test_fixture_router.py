from __future__ import annotations

import hashlib
import io
import json
import os
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from fixture_router import RouteFailure, build_route_plan, write_route_outputs


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_source(root: Path, relative_path: str, data: bytes) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _write_manifest(path: Path, entries: list[tuple[str, bytes]]) -> None:
    lines = [f"{_digest(data)}  {relative_path}" for relative_path, data in entries]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _valid_pdf(marker: bytes = b"") -> bytes:
    prefix = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
        + marker
        + b"\n"
    )
    xref_offset = len(prefix)
    return prefix + (
        b"xref\n0 2\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"trailer\n<< /Size 2 /Root 1 0 R >>\n"
        b"startxref\n"
        + str(xref_offset).encode("ascii")
        + b"\n%%EOF\n"
    )


def _valid_jpeg() -> bytes:
    return (
        b"\xff\xd8"
        b"\xff\xe0\x00\x07JFIF\x00"
        b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
        b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"
        b"\x00\xff\xd9"
    )


def _directory_entry(
    name: str,
    object_type: int,
    *,
    start_sector: int = 0xFFFFFFFE,
    stream_size: int = 0,
) -> bytes:
    entry = bytearray(128)
    encoded_name = name.encode("utf-16le") + b"\x00\x00"
    entry[: len(encoded_name)] = encoded_name
    struct.pack_into("<H", entry, 64, len(encoded_name))
    entry[66] = object_type
    entry[67] = 1
    struct.pack_into("<III", entry, 68, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF)
    struct.pack_into("<I", entry, 116, start_sector)
    struct.pack_into("<Q", entry, 120, stream_size)
    return bytes(entry)


def _valid_doc(
    *,
    include_word_document: bool = True,
    marker_name: str | None = None,
    stream_size: int = 4108,
    fib_ident: int = 0xA5EC,
    fib_flags: int = 0x0014,
) -> bytes:
    header = bytearray(512)
    header[:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    struct.pack_into("<H", header, 24, 0x003E)
    struct.pack_into("<H", header, 26, 3)
    struct.pack_into("<H", header, 28, 0xFFFE)
    struct.pack_into("<H", header, 30, 9)
    struct.pack_into("<H", header, 32, 6)
    struct.pack_into("<I", header, 40, 0)
    struct.pack_into("<I", header, 44, 1)
    struct.pack_into("<I", header, 48, 1)
    struct.pack_into("<I", header, 56, 4096)
    struct.pack_into("<I", header, 60, 0xFFFFFFFE)
    struct.pack_into("<I", header, 64, 0)
    struct.pack_into("<I", header, 68, 0xFFFFFFFE)
    struct.pack_into("<I", header, 72, 0)
    difat = [0] + [0xFFFFFFFF] * 108
    struct.pack_into("<109I", header, 76, *difat)

    stream_sector_count = (
        (stream_size + 511) // 512 if include_word_document else 0
    )
    fat = [0xFFFFFFFD, 0xFFFFFFFE]
    for index in range(stream_sector_count):
        sector_id = 2 + index
        fat.append(
            0xFFFFFFFE if index == stream_sector_count - 1 else sector_id + 1
        )
    fat.extend([0xFFFFFFFF] * (128 - len(fat)))
    fat_sector = struct.pack("<128I", *fat)
    directory = bytearray(512)
    directory[:128] = _directory_entry("Root Entry", 5)
    if include_word_document:
        directory[128:256] = _directory_entry(
            "WordDocument", 2, start_sector=2, stream_size=stream_size
        )
    if marker_name is not None:
        directory[256:384] = _directory_entry(marker_name, 1)
    stream = bytearray(stream_size if include_word_document else 0)
    if include_word_document:
        if stream_size < 12:
            raise ValueError("stream_size must hold FibBase")
        struct.pack_into("<H", stream, 0, fib_ident)
        struct.pack_into("<H", stream, 2, 0x00C1)
        struct.pack_into("<H", stream, 10, fib_flags)
    return bytes(header) + fat_sector + bytes(directory) + bytes(stream)


def _corrupt_zip_member(data: bytes, member_name: str) -> bytes:
    mutable = bytearray(data)
    with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
        info = archive.getinfo(member_name)
        offset = info.header_offset
        name_length = struct.unpack_from("<H", data, offset + 26)[0]
        extra_length = struct.unpack_from("<H", data, offset + 28)[0]
        payload_offset = offset + 30 + name_length + extra_length + max(
            0, info.compress_size // 2
        )
        mutable[payload_offset] ^= 0x01
    return bytes(mutable)


def _ooxml(
    document_type: str,
    *,
    macro: bool = False,
    external: bool = False,
    extra_members: list[tuple[str, bytes]] | None = None,
    content_types_namespace: str = "http://schemas.openxmlformats.org/package/2006/content-types",
    relationships_namespace: str = "http://schemas.openxmlformats.org/package/2006/relationships",
    root_target: str | None = None,
) -> bytes:
    if document_type == "DOCX":
        main_part = "word/document.xml"
        content_type = (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document.main+xml"
        )
    else:
        main_part = "xl/workbook.xml"
        content_type = (
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet.main+xml"
        )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Types xmlns="{content_types_namespace}">'
        f'<Override PartName="/{main_part}" ContentType="{content_type}"/>'
        "</Types>"
    )
    target_mode = ' TargetMode="External"' if external else ""
    relationship_target = root_target or main_part
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Relationships xmlns="{relationships_namespace}">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        f'Target="{relationship_target}"{target_mode}/>'
        "</Relationships>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", relationships)
        archive.writestr(main_part, "<document/>")
        if macro:
            archive.writestr("word/vbaProject.bin", b"macro")
        for name, data in extra_members or []:
            archive.writestr(name, data)
    return buffer.getvalue()


class FixtureRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.source = self.base / "source"
        self.source.mkdir()
        self.core_manifest = self.base / "core.sha256"
        self.negative_manifest = self.base / "negative.sha256"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_case(
        self,
        source: Path,
        core_manifest: Path,
        negative_manifest: Path,
        core: list[tuple[str, bytes]],
        negative: list[tuple[str, bytes]],
    ) -> None:
        source.mkdir(parents=True, exist_ok=True)
        for relative_path, data in core + negative:
            _write_source(source, relative_path, data)
        _write_manifest(core_manifest, core)
        _write_manifest(negative_manifest, negative)

    def _build(
        self,
        core: list[tuple[str, bytes]],
        negative: list[tuple[str, bytes]] | None = None,
        *,
        profile: str = "full",
    ) -> dict[str, object]:
        negative = negative or [("negative.pdf", _valid_pdf(b"negative"))]
        self._write_case(
            self.source,
            self.core_manifest,
            self.negative_manifest,
            core,
            negative,
        )
        return build_route_plan(
            source_root=self.source,
            core_manifest=self.core_manifest,
            negative_manifest=self.negative_manifest,
            profile=profile,
        )

    def _assert_failure(
        self,
        expected_code: str,
        core: list[tuple[str, bytes]],
        negative: list[tuple[str, bytes]] | None = None,
    ) -> RouteFailure:
        with self.assertRaises(RouteFailure) as context:
            self._build(core, negative)
        self.assertEqual(context.exception.code, expected_code)
        return context.exception

    def test_pdf_routes_to_native_or_ocr_probe(self) -> None:
        plan = self._build([("sample.pdf", _valid_pdf())])
        self.assertEqual(plan["entries"][0]["type"], "PDF")
        self.assertEqual(plan["entries"][0]["route"], "PDF_NATIVE_OR_OCR_PROBE")

    def test_jpeg_routes_to_ocr_required(self) -> None:
        plan = self._build([("sample.jpg", _valid_jpeg())])
        self.assertEqual(plan["entries"][0]["type"], "JPEG")
        self.assertEqual(plan["entries"][0]["route"], "IMAGE_OCR_REQUIRED")

    def test_legacy_doc_routes_to_conversion_required(self) -> None:
        plan = self._build([("sample.doc", _valid_doc())])
        self.assertEqual(plan["entries"][0]["type"], "DOC")
        self.assertEqual(
            plan["entries"][0]["route"], "LEGACY_OFFICE_CONVERSION_REQUIRED"
        )

    def test_sector_aligned_legacy_doc_is_accepted(self) -> None:
        plan = self._build([("sample.doc", _valid_doc(stream_size=4096))])
        self.assertEqual(plan["entries"][0]["type"], "DOC")

    def test_docx_routes_to_native(self) -> None:
        plan = self._build([("sample.docx", _ooxml("DOCX"))])
        self.assertEqual(plan["entries"][0]["type"], "DOCX")
        self.assertEqual(plan["entries"][0]["route"], "DOCX_NATIVE")

    def test_xlsx_routes_to_native(self) -> None:
        plan = self._build([("sample.xlsx", _ooxml("XLSX"))])
        self.assertEqual(plan["entries"][0]["type"], "XLSX")
        self.assertEqual(plan["entries"][0]["route"], "XLSX_NATIVE")

    def test_smoke_profile_selects_exact_ten_registered_lines(self) -> None:
        core = [(f"core-{line}.pdf", _valid_pdf(str(line).encode())) for line in range(1, 25)]
        negative = [
            (f"negative-{line}.pdf", _valid_pdf(f"n{line}".encode()))
            for line in range(1, 3)
        ]
        plan = self._build(core, negative, profile="smoke")
        actual = [(entry["group"], entry["line"]) for entry in plan["entries"]]
        self.assertEqual(
            actual,
            [
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
            ],
        )

    def test_extension_mismatch_is_rejected(self) -> None:
        self._assert_failure("EXTENSION_TYPE_MISMATCH", [("sample.docx", _valid_pdf())])

    def test_truncated_pdf_is_rejected(self) -> None:
        self._assert_failure("TRUNCATED_PDF", [("sample.pdf", _valid_pdf()[:-6])])

    def test_invalid_pdf_xref_is_rejected(self) -> None:
        data = _valid_pdf()
        start = data.rfind(b"startxref\n") + len(b"startxref\n")
        end = data.index(b"\n", start)
        data = data[:start] + b"1" + data[end:]
        self._assert_failure("INVALID_PDF_XREF", [("sample.pdf", data)])

    def test_encrypted_pdf_marker_is_rejected(self) -> None:
        self._assert_failure(
            "ENCRYPTED_INPUT", [("sample.pdf", _valid_pdf(b"/Encrypt 2 0 R"))]
        )

    def test_hex_escaped_encrypted_pdf_name_is_rejected(self) -> None:
        self._assert_failure(
            "ENCRYPTED_INPUT", [("sample.pdf", _valid_pdf(b"/Encr#79pt 2 0 R"))]
        )

    def test_fake_classic_xref_is_rejected(self) -> None:
        prefix = b"%PDF-1.4\nxref NOT_A_TABLE\n"
        data = prefix + b"startxref\n9\n%%EOF\n"
        self._assert_failure("INVALID_PDF_XREF", [("sample.pdf", data)])

    def test_oversized_startxref_is_controlled_failure(self) -> None:
        data = b"%PDF-1.4\nxref\n0 1\n0000000000 65535 f \nstartxref\n" + (
            b"9" * 5000
        ) + b"\n%%EOF\n"
        with self.assertRaises(RouteFailure):
            self._build([("sample.pdf", data)])

    def test_bad_ooxml_container_is_rejected(self) -> None:
        self._assert_failure("INVALID_OOXML_CONTAINER", [("sample.docx", b"PK broken")])

    def test_ooxml_main_part_crc_failure_is_rejected(self) -> None:
        data = _corrupt_zip_member(_ooxml("DOCX"), "word/document.xml")
        self._assert_failure("INVALID_OOXML_CONTAINER", [("sample.docx", data)])

    def test_ooxml_wrong_content_types_namespace_is_rejected(self) -> None:
        self._assert_failure(
            "INVALID_CONTENT_TYPES",
            [
                (
                    "sample.docx",
                    _ooxml("DOCX", content_types_namespace="urn:not-content-types"),
                )
            ],
        )

    def test_ooxml_wrong_relationship_namespace_is_rejected(self) -> None:
        self._assert_failure(
            "INVALID_OOXML_RELATIONSHIPS",
            [
                (
                    "sample.xlsx",
                    _ooxml("XLSX", relationships_namespace="urn:not-relationships"),
                )
            ],
        )

    def test_macro_enabled_ooxml_is_rejected(self) -> None:
        self._assert_failure(
            "MACRO_ENABLED_INPUT", [("sample.docx", _ooxml("DOCX", macro=True))]
        )

    def test_external_ooxml_relationship_is_rejected(self) -> None:
        self._assert_failure(
            "EXTERNAL_OOXML_RELATIONSHIP",
            [("sample.xlsx", _ooxml("XLSX", external=True))],
        )

    def test_ooxml_relationship_cannot_escape_package_root(self) -> None:
        self._assert_failure(
            "INVALID_OOXML_RELATIONSHIPS",
            [
                (
                    "sample.xlsx",
                    _ooxml("XLSX", root_target="../../../../outside"),
                )
            ],
        )

    def test_xlm_macro_member_is_rejected(self) -> None:
        self._assert_failure(
            "MACRO_ENABLED_INPUT",
            [
                (
                    "sample.xlsx",
                    _ooxml(
                        "XLSX",
                        extra_members=[("xl/macrosheets/sheet1.xml", b"<sheet/>")],
                    ),
                )
            ],
        )

    def test_ooxml_traversal_member_is_rejected(self) -> None:
        self._assert_failure(
            "INVALID_OOXML_MEMBER",
            [("sample.docx", _ooxml("DOCX", extra_members=[("../outside.xml", b"x")]))],
        )

    def test_ooxml_normalized_duplicate_is_rejected(self) -> None:
        self._assert_failure(
            "DUPLICATE_OOXML_MEMBER",
            [
                (
                    "sample.docx",
                    _ooxml("DOCX", extra_members=[("Word/Document.xml", b"duplicate")]),
                )
            ],
        )

    def test_legacy_doc_missing_word_stream_is_rejected(self) -> None:
        self._assert_failure(
            "INVALID_CFB_DOCUMENT",
            [("sample.doc", _valid_doc(include_word_document=False))],
        )

    def test_legacy_doc_macro_storage_is_rejected(self) -> None:
        self._assert_failure(
            "MACRO_ENABLED_INPUT",
            [("sample.doc", _valid_doc(marker_name="Macros"))],
        )

    def test_legacy_doc_fib_encryption_flag_is_rejected(self) -> None:
        self._assert_failure(
            "ENCRYPTED_INPUT",
            [("sample.doc", _valid_doc(fib_flags=0x0114))],
        )

    def test_legacy_doc_encryption_storage_is_case_insensitive(self) -> None:
        self._assert_failure(
            "ENCRYPTED_INPUT",
            [("sample.doc", _valid_doc(marker_name="encryptioninfo"))],
        )

    def test_legacy_doc_invalid_fib_is_rejected(self) -> None:
        self._assert_failure(
            "INVALID_CFB_DOCUMENT",
            [("sample.doc", _valid_doc(fib_ident=0x0000))],
        )

    def test_legacy_doc_arbitrary_trailing_bytes_are_rejected(self) -> None:
        self._assert_failure(
            "INVALID_CFB_CONTAINER", [("sample.doc", _valid_doc() + b"appended")]
        )

    def test_legacy_doc_partial_sector_cannot_be_padded_afterward(self) -> None:
        data = _valid_doc()
        padding = (-len(data)) % 512
        self.assertGreater(padding, 0)
        self._assert_failure(
            "INVALID_CFB_CONTAINER", [("sample.doc", data + b"x" * padding)]
        )

    def test_legacy_doc_unclaimed_full_sector_is_rejected(self) -> None:
        data = _valid_doc()
        padding = (-len(data)) % 512
        self._assert_failure(
            "INVALID_CFB_CONTAINER",
            [("sample.doc", data + b"x" * (padding + 512))],
        )

    def test_legacy_doc_fat_chain_loop_is_rejected(self) -> None:
        data = bytearray(_valid_doc())
        struct.pack_into("<I", data, 512 + 2 * 4, 2)
        self._assert_failure("INVALID_CFB_CONTAINER", [("sample.doc", bytes(data))])

    def test_truncated_jpeg_is_rejected(self) -> None:
        self._assert_failure("TRUNCATED_JPEG", [("sample.jpg", _valid_jpeg()[:-2])])

    def test_jpeg_zero_height_is_rejected(self) -> None:
        data = bytearray(_valid_jpeg())
        sof = data.index(b"\xff\xc0")
        data[sof + 5 : sof + 7] = b"\x00\x00"
        self._assert_failure("INVALID_JPEG_STRUCTURE", [("sample.jpg", bytes(data))])

    def test_unknown_format_is_rejected(self) -> None:
        self._assert_failure("UNKNOWN_FORMAT", [("sample.bin", b"unknown")])

    def test_negative_role_closes_all_three_gates(self) -> None:
        plan = self._build(
            [("core.pdf", _valid_pdf(b"core"))],
            [("negative.xlsx", _ooxml("XLSX"))],
        )
        negative = plan["entries"][1]
        self.assertEqual(negative["corpus_role"], "NEGATIVE_TEST_ONLY")
        self.assertFalse(negative["enterprise_fact_allowed"])
        self.assertFalse(negative["current_regulation_allowed"])
        self.assertFalse(negative["search_publish_allowed"])

    def test_document_id_is_stable_across_source_roots(self) -> None:
        data = _valid_pdf(b"stable")
        first = self._build([("nested/sample.pdf", data)])
        second_base = self.base / "second"
        second_source = second_base / "source"
        second_core = second_base / "core.sha256"
        second_negative = second_base / "negative.sha256"
        self._write_case(
            second_source,
            second_core,
            second_negative,
            [("nested/sample.pdf", data)],
            [("negative.pdf", _valid_pdf(b"negative"))],
        )
        second = build_route_plan(
            source_root=second_source,
            core_manifest=second_core,
            negative_manifest=second_negative,
            profile="full",
        )
        self.assertEqual(
            first["entries"][0]["document_id"], second["entries"][0]["document_id"]
        )

    def test_serialized_plan_contains_no_path_or_filename(self) -> None:
        secret_name = "机密公司/许可证联系人电话.pdf"
        plan = self._build([(secret_name, _valid_pdf())])
        serialized = json.dumps(plan, ensure_ascii=False)
        self.assertNotIn(secret_name, serialized)
        self.assertNotIn(str(self.source), serialized)
        self.assertNotIn("机密公司", serialized)

    def test_route_plan_is_deterministic(self) -> None:
        first = self._build([("sample.docx", _ooxml("DOCX"))])
        second = build_route_plan(
            source_root=self.source,
            core_manifest=self.core_manifest,
            negative_manifest=self.negative_manifest,
            profile="full",
        )
        self.assertEqual(
            json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True)
        )

    def test_symlink_source_entry_is_rejected(self) -> None:
        target = _write_source(self.source, "target.pdf", _valid_pdf())
        (self.source / "link.pdf").symlink_to(target)
        negative = [("negative.pdf", _valid_pdf(b"negative"))]
        _write_source(self.source, negative[0][0], negative[0][1])
        _write_manifest(self.core_manifest, [("link.pdf", target.read_bytes())])
        _write_manifest(self.negative_manifest, negative)
        with self.assertRaises(RouteFailure) as context:
            build_route_plan(
                source_root=self.source,
                core_manifest=self.core_manifest,
                negative_manifest=self.negative_manifest,
                profile="full",
            )
        self.assertEqual(context.exception.code, "SYMLINK_REJECTED")

    def test_fifo_source_entry_is_rejected_without_blocking(self) -> None:
        fifo = self.source / "sample.pdf"
        os.mkfifo(fifo)
        negative = [("negative.pdf", _valid_pdf(b"negative"))]
        _write_source(self.source, negative[0][0], negative[0][1])
        self.core_manifest.write_text(f"{'0' * 64}  sample.pdf\n", encoding="utf-8")
        _write_manifest(self.negative_manifest, negative)
        with self.assertRaises(RouteFailure) as context:
            build_route_plan(
                source_root=self.source,
                core_manifest=self.core_manifest,
                negative_manifest=self.negative_manifest,
                profile="full",
            )
        self.assertEqual(context.exception.code, "NOT_REGULAR_FILE")

    def test_output_symlink_is_rejected_without_touching_target(self) -> None:
        output = self.base / "output-symlink"
        output.mkdir()
        victim = self.base / "victim-symlink.txt"
        victim.write_bytes(b"original")
        (output / "smoke-plan.json").symlink_to(victim)
        with self.assertRaises(RouteFailure):
            write_route_outputs({"profile": "smoke"}, artifact_root=output)
        self.assertEqual(victim.read_bytes(), b"original")

    def test_output_hardlink_is_rejected_without_touching_target(self) -> None:
        output = self.base / "output-hardlink"
        output.mkdir()
        victim = self.base / "victim-hardlink.txt"
        victim.write_bytes(b"original")
        os.link(victim, output / "smoke-plan.json")
        with self.assertRaises(RouteFailure):
            write_route_outputs({"profile": "smoke"}, artifact_root=output)
        self.assertEqual(victim.read_bytes(), b"original")

    def test_output_fifo_is_rejected_without_blocking(self) -> None:
        output = self.base / "output-fifo"
        output.mkdir()
        os.mkfifo(output / "smoke-plan.json")
        with self.assertRaises(RouteFailure):
            write_route_outputs({"profile": "smoke"}, artifact_root=output)

    def test_conflicting_regular_output_is_not_overwritten(self) -> None:
        output = self.base / "output-conflict"
        output.mkdir()
        target = output / "smoke-plan.json"
        target.write_bytes(b"existing")
        with self.assertRaises(RouteFailure):
            write_route_outputs({"profile": "smoke"}, artifact_root=output)
        self.assertEqual(target.read_bytes(), b"existing")

    def test_output_root_symlink_is_rejected(self) -> None:
        real_output = self.base / "real-output"
        real_output.mkdir()
        alias = self.base / "output-alias"
        alias.symlink_to(real_output, target_is_directory=True)
        with self.assertRaises(RouteFailure):
            write_route_outputs({"profile": "smoke"}, artifact_root=alias)
        self.assertEqual(list(real_output.iterdir()), [])

    def test_status_page_is_offline_aggregate_only(self) -> None:
        plan = self._build(
            [("private/source.pdf", _valid_pdf())],
            [("private/negative.xlsx", _ooxml("XLSX"))],
        )
        output = self.base / "status-output"
        write_route_outputs(plan, artifact_root=output)
        page = (output / "status.html").read_text(encoding="utf-8")
        self.assertIn("FIXTURE_ONLY", page)
        self.assertIn("2 份", page)
        self.assertNotIn("source.pdf", page)
        self.assertNotIn("http://", page)
        self.assertNotIn("https://", page)


if __name__ == "__main__":
    unittest.main()
