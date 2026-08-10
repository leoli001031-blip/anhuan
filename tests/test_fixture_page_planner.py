from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    RectangleObject,
)

from fixture_page_planner import (
    PlannerFailure,
    RULE_VERSION,
    build_page_plan,
    render_status_html,
    write_page_outputs,
)
from fixture_page_planner import planner as planner_module
from fixture_page_planner.planner import (
    _classify_page,
    _parse_pdf,
    _text_metrics,
)
from fixture_router import build_route_plan
from tests.test_fixture_router import _valid_doc


def _pdf_bytes(
    texts: str | list[str],
    *,
    rotation: int = 0,
    crop: tuple[int, int, int, int] | None = None,
    encrypted: bool = False,
    render_mode: int = 0,
    reset_render_mode: bool = False,
) -> bytes:
    if isinstance(texts, str):
        texts = [texts]
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    for text in texts:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject(
                    {NameObject("/F1"): font_reference}
                )
            }
        )
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = DecodedStreamObject()
        reset = " 0 Tr" if reset_render_mode else ""
        stream.set_data(
            f"BT /F1 12 Tf {render_mode} Tr 72 700 Td ({escaped}) Tj{reset} ET".encode(
                "ascii"
            )
        )
        page[NameObject("/Contents")] = writer._add_object(stream)
        if rotation:
            page.rotate(rotation)
        if crop is not None:
            page.cropbox = RectangleObject(crop)
    if encrypted:
        writer.encrypt("fixture-password", algorithm="RC4-128")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _zip_bytes(members: list[tuple[str, bytes]]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return output.getvalue()


def _docx_bytes(document_xml: bytes | None = None) -> bytes:
    content_types = b'''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>'''
    relationships = b'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''
    if document_xml is None:
        document_xml = b'''<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:r><w:t>PRIVATE_DOCX_ALPHA</w:t></w:r></w:p>
<w:tbl><w:tr><w:tc><w:p><w:r><w:t>PRIVATE_DOCX_BETA</w:t></w:r></w:p></w:tc>
<w:tc><w:p><w:r><w:t>PRIVATE_DOCX_GAMMA</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
</w:body></w:document>'''
    return _zip_bytes(
        [
            ("[Content_Types].xml", content_types),
            ("_rels/.rels", relationships),
            ("word/document.xml", document_xml),
        ]
    )


def _xlsx_bytes() -> bytes:
    content_types = b'''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>'''
    root_relationships = b'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''
    workbook = b'''<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="PRIVATE_SHEET_NAME" sheetId="1" r:id="rId1"/></sheets></workbook>'''
    workbook_relationships = b'''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>'''
    worksheet = b'''<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1">
<c r="A1"><v>PRIVATE_CELL_VALUE</v></c><c r="B1"><f>2+3</f><v>5</v></c>
</row></sheetData></worksheet>'''
    return _zip_bytes(
        [
            ("[Content_Types].xml", content_types),
            ("_rels/.rels", root_relationships),
            ("xl/workbook.xml", workbook),
            ("xl/_rels/workbook.xml.rels", workbook_relationships),
            ("xl/worksheets/sheet1.xml", worksheet),
        ]
    )


def _jpeg_bytes(width: int = 640, height: int = 480) -> bytes:
    return (
        b"\xff\xd8"
        b"\xff\xe0\x00\x07JFIF\x00"
        + b"\xff\xc0\x00\x0b\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x01\x01\x11\x00"
        + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"
        + b"\x00\xff\xd9"
    )


def _serialize(value: dict[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _safe_output_plan(profile: str) -> dict[str, object]:
    return {
        "schema_version": "fixture-native-page-plan/v1",
        "fixture_set_id": "environment-demo-seed",
        "fixture_version": "v0.1",
        "profile": profile,
        "labels": ["FIXTURE_ONLY", "PIPELINE_REGRESSION_ONLY"],
        "benchmark_tier": "NONE",
        "claim_scope": "PIPELINE_REGRESSION_ONLY",
        "policy": {
            "external_processing": "DENY",
            "model_training": "DENY",
            "production_use": "DENY",
            "public_display": "DENY",
        },
        "input_route_plan_sha256": "0" * 64,
        "parser": {
            "name": "pypdf",
            "version": "6.14.2",
            "license_expression": "BSD-3-Clause",
            "strict": True,
        },
        "rule_version": "native-page-rule/v1",
        "ocr_executed": False,
        "raw_text_persisted": False,
        "page_images_persisted": False,
        "external_processing": "DENY",
        "summary": {
            "documents": 0,
            "groups": {"core": 0, "negative": 0},
            "types": {"PDF": 0, "DOC": 0, "DOCX": 0, "JPEG": 0, "XLSX": 0},
            "pdf_documents": 0,
            "pdf_pages": 0,
            "visual_units": 0,
            "native_candidates": 0,
            "ocr_required": 0,
            "manual_review_required": 0,
            "decisions": {
                "NATIVE_CANDIDATE": 0,
                "FULL_PAGE_OCR_REQUIRED": 0,
                "MANUAL_REVIEW_REQUIRED": 0,
            },
            "doc_deferred": 0,
            "docx": {"paragraphs": 0, "tables": 0, "rows": 0, "cells": 0},
            "xlsx": {
                "sheets": 0,
                "cells": 0,
                "formulas": 0,
                "value_cells": 0,
                "formula_cached_values": 0,
            },
            "jpeg": {"documents": 0, "visual_units": 0},
            "errors": 0,
        },
        "entries": [],
    }


class FixturePagePlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.case_number = 0

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _case(
        self,
        core: list[tuple[str, bytes]],
        negative: list[tuple[str, bytes]] | None = None,
    ) -> tuple[Path, Path, Path, Path]:
        self.case_number += 1
        case_root = self.base / f"case-{self.case_number}"
        source = case_root / "source"
        source.mkdir(parents=True)
        negative = negative or [("negative.pdf", _pdf_bytes("N" * 20))]
        for relative_path, data in core + negative:
            destination = source / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        core_manifest = case_root / "core.sha256"
        negative_manifest = case_root / "negative.sha256"
        for manifest, entries in (
            (core_manifest, core),
            (negative_manifest, negative),
        ):
            manifest.write_text(
                "".join(
                    f"{hashlib.sha256(data).hexdigest()}  {relative_path}\n"
                    for relative_path, data in entries
                ),
                encoding="utf-8",
            )
        route = build_route_plan(
            source_root=source,
            core_manifest=core_manifest,
            negative_manifest=negative_manifest,
            profile="full",
        )
        route_path = case_root / "route-plan.json"
        route_path.write_bytes(_serialize(route))
        return source, core_manifest, negative_manifest, route_path

    def _plan(
        self,
        core: list[tuple[str, bytes]],
        negative: list[tuple[str, bytes]] | None = None,
    ) -> tuple[dict[str, object], tuple[Path, Path, Path, Path]]:
        inputs = self._case(core, negative)
        plan = build_page_plan(
            source_root=inputs[0],
            core_manifest=inputs[1],
            negative_manifest=inputs[2],
            route_plan_path=inputs[3],
            profile="full",
        )
        return plan, inputs

    def test_rule_version_is_frozen(self) -> None:
        self.assertEqual(RULE_VERSION, "native-page-rule/v1")

    def test_nineteen_nonspace_characters_require_ocr(self) -> None:
        plan, _ = self._plan([("nineteen.pdf", _pdf_bytes("A" * 19))])
        page = plan["entries"][0]["pages"][0]
        self.assertEqual(page["native_characters"], 19)
        self.assertEqual(page["decision"], "FULL_PAGE_OCR_REQUIRED")

    def test_twenty_nonspace_characters_are_only_native_candidate(self) -> None:
        plan, _ = self._plan([("twenty.pdf", _pdf_bytes("A" * 20))])
        page = plan["entries"][0]["pages"][0]
        self.assertEqual(page["native_characters"], 20)
        self.assertEqual(page["decision"], "NATIVE_CANDIDATE")

    def test_whitespace_does_not_raise_effective_character_count(self) -> None:
        plan, _ = self._plan([("space.pdf", _pdf_bytes("A" * 19 + "   "))])
        page = plan["entries"][0]["pages"][0]
        self.assertEqual(page["native_characters"], 19)
        self.assertEqual(page["decision"], "FULL_PAGE_OCR_REQUIRED")

    def test_blank_page_requires_ocr(self) -> None:
        plan, _ = self._plan([("blank.pdf", _pdf_bytes(""))])
        page = plan["entries"][0]["pages"][0]
        self.assertEqual(page["native_characters"], 0)
        self.assertEqual(page["decision"], "FULL_PAGE_OCR_REQUIRED")

    def test_bad_character_ratio_above_two_percent_requires_review(self) -> None:
        count, bad_ppm, digest = _text_metrics("A" * 39 + "\ufffd")
        decision, reasons = _classify_page(
            native_characters=count,
            bad_character_ppm=bad_ppm,
            geometry_abnormal=False,
        )
        self.assertEqual(len(digest), 64)
        self.assertEqual(decision, "MANUAL_REVIEW_REQUIRED")
        self.assertEqual(reasons, ["BAD_NATIVE_TEXT_RATIO"])

    def test_geometry_abnormality_requires_review(self) -> None:
        decision, reasons = _classify_page(
            native_characters=20,
            bad_character_ppm=0,
            geometry_abnormal=True,
        )
        self.assertEqual(decision, "MANUAL_REVIEW_REQUIRED")
        self.assertEqual(reasons, ["GEOMETRY_ABNORMAL"])

    def test_rotation_and_crop_box_are_canonical(self) -> None:
        plan, _ = self._plan(
            [("geometry.pdf", _pdf_bytes("A" * 20, rotation=90, crop=(50, 100, 550, 700)))]
        )
        page = plan["entries"][0]["pages"][0]
        self.assertEqual(page["rotation"], 90)
        self.assertEqual(
            page["crop_box"],
            {"left": "50.000", "bottom": "100.000", "right": "550.000", "top": "700.000"},
        )

    def test_text_entirely_outside_crop_box_requires_ocr(self) -> None:
        plan, _ = self._plan(
            [("outside.pdf", _pdf_bytes("A" * 20, crop=(0, 0, 100, 100)))]
        )
        page = plan["entries"][0]["pages"][0]
        self.assertEqual(page["native_characters"], 0)
        self.assertEqual(page["decision"], "FULL_PAGE_OCR_REQUIRED")

    def test_hidden_text_rendering_mode_requires_manual_review(self) -> None:
        plan, _ = self._plan(
            [("hidden.pdf", _pdf_bytes("A" * 20, render_mode=3))]
        )
        page = plan["entries"][0]["pages"][0]
        self.assertEqual(page["decision"], "MANUAL_REVIEW_REQUIRED")
        self.assertIn("HIDDEN_NATIVE_TEXT", page["reason_codes"])

    def test_hidden_text_is_detected_before_render_mode_reset(self) -> None:
        for render_mode in (3, 7):
            with self.subTest(render_mode=render_mode):
                plan, _ = self._plan(
                    [
                        (
                            f"hidden-reset-{render_mode}.pdf",
                            _pdf_bytes(
                                "A" * 20,
                                render_mode=render_mode,
                                reset_render_mode=True,
                            ),
                        )
                    ]
                )
                page = plan["entries"][0]["pages"][0]
                self.assertEqual(page["decision"], "MANUAL_REVIEW_REQUIRED")
                self.assertIn("HIDDEN_NATIVE_TEXT", page["reason_codes"])

    def test_zero_width_format_characters_are_not_effective_text(self) -> None:
        count, bad_ppm, _digest = _text_metrics("\u200b" * 20)
        self.assertEqual(count, 0)
        self.assertGreater(bad_ppm, 20_000)

    def test_each_pdf_page_gets_an_independent_decision(self) -> None:
        plan, _ = self._plan(
            [("mixed.pdf", _pdf_bytes(["A" * 19, "B" * 20, ""]))]
        )
        pages = plan["entries"][0]["pages"]
        self.assertEqual([page["page_no"] for page in pages], [1, 2, 3])
        self.assertEqual(
            [page["decision"] for page in pages],
            ["FULL_PAGE_OCR_REQUIRED", "NATIVE_CANDIDATE", "FULL_PAGE_OCR_REQUIRED"],
        )

    def test_text_digest_changes_with_real_parsed_text(self) -> None:
        first, _ = self._plan([("first.pdf", _pdf_bytes("A" * 20))])
        second, _ = self._plan([("second.pdf", _pdf_bytes("B" * 20))])
        first_page = first["entries"][0]["pages"][0]
        second_page = second["entries"][0]["pages"][0]
        self.assertNotEqual(first_page["native_text_sha256"], second_page["native_text_sha256"])

    def test_same_inputs_produce_identical_plan(self) -> None:
        inputs = self._case([("stable.pdf", _pdf_bytes("S" * 20))])
        arguments = {
            "source_root": inputs[0],
            "core_manifest": inputs[1],
            "negative_manifest": inputs[2],
            "route_plan_path": inputs[3],
            "profile": "full",
        }
        self.assertEqual(build_page_plan(**arguments), build_page_plan(**arguments))

    def test_corrupt_pdf_is_controlled_failure(self) -> None:
        with self.assertRaises(PlannerFailure) as caught:
            _parse_pdf(io.BytesIO(_pdf_bytes("A" * 20)[:-12]), "0" * 64)
        self.assertEqual(caught.exception.code, "PDF_PARSE_FAILED")

    def test_encrypted_pdf_is_rejected_without_password_attempt(self) -> None:
        with self.assertRaises(PlannerFailure) as caught:
            _parse_pdf(io.BytesIO(_pdf_bytes("A" * 20, encrypted=True)), "0" * 64)
        self.assertEqual(caught.exception.code, "ENCRYPTED_INPUT")

    def test_page_text_resource_limit_is_fail_closed(self) -> None:
        with self.assertRaises(PlannerFailure) as caught:
            _text_metrics("A" * 5_000_001)
        self.assertEqual(caught.exception.code, "PAGE_TEXT_LIMIT_EXCEEDED")

    def test_registered_route_plan_tampering_is_rejected(self) -> None:
        inputs = self._case([("route.pdf", _pdf_bytes("A" * 20))])
        tampered = json.loads(inputs[3].read_text(encoding="utf-8"))
        tampered["entries"][0]["document_id"] = "f" * 64
        inputs[3].write_bytes(_serialize(tampered))
        with self.assertRaises(PlannerFailure) as caught:
            build_page_plan(
                source_root=inputs[0],
                core_manifest=inputs[1],
                negative_manifest=inputs[2],
                route_plan_path=inputs[3],
                profile="full",
            )
        self.assertEqual(caught.exception.code, "ROUTE_PLAN_MISMATCH")

    def test_registered_route_identity_tampering_is_rejected(self) -> None:
        inputs = self._case([("identity.pdf", _pdf_bytes("A" * 20))])
        tampered = json.loads(inputs[3].read_text(encoding="utf-8"))
        tampered["fixture_version"] = "v9"
        inputs[3].write_bytes(_serialize(tampered))
        with self.assertRaises(PlannerFailure):
            build_page_plan(
                source_root=inputs[0],
                core_manifest=inputs[1],
                negative_manifest=inputs[2],
                route_plan_path=inputs[3],
                profile="full",
            )

    def test_negative_entry_keeps_all_three_gates_closed(self) -> None:
        plan, _ = self._plan(
            [("core.pdf", _pdf_bytes("C" * 20))],
            [("negative.pdf", _pdf_bytes("N" * 20))],
        )
        negative = plan["entries"][1]
        self.assertEqual(negative["corpus_role"], "NEGATIVE_TEST_ONLY")
        self.assertFalse(negative["enterprise_fact_allowed"])
        self.assertFalse(negative["current_regulation_allowed"])
        self.assertFalse(negative["search_publish_allowed"])

    def test_body_filename_and_absolute_path_do_not_persist(self) -> None:
        body = "PRIVATE_BODY synthetic.person@example.invalid 13800138000"
        plan, inputs = self._plan(
            [("PRIVATE_FILENAME.pdf", _pdf_bytes(body))]
        )
        serialized = _serialize(plan).decode("ascii")
        self.assertNotIn("PRIVATE_BODY", serialized)
        self.assertNotIn("PRIVATE_FILENAME", serialized)
        self.assertNotIn("example.invalid", serialized)
        self.assertNotIn("13800138000", serialized)
        self.assertNotIn(str(inputs[0]), serialized)

    def test_docx_preserves_structure_order_without_page_numbers(self) -> None:
        plan, _ = self._plan([("structure.docx", _docx_bytes())])
        entry = plan["entries"][0]
        self.assertEqual(
            entry["structure_summary"],
            {"paragraphs": 3, "tables": 1, "rows": 1, "cells": 2, "ordered_blocks": 2},
        )
        self.assertEqual(
            [block["kind"] for block in entry["structure_anchors"]],
            ["PARAGRAPH", "TABLE"],
        )
        self.assertNotIn("page_no", _serialize(entry).decode("ascii"))

    def test_malformed_docx_xml_is_controlled_failure(self) -> None:
        inputs = self._case([("broken.docx", _docx_bytes(b"<document>"))])
        with self.assertRaises(PlannerFailure) as caught:
            build_page_plan(
                source_root=inputs[0],
                core_manifest=inputs[1],
                negative_manifest=inputs[2],
                route_plan_path=inputs[3],
                profile="full",
            )
        self.assertEqual(caught.exception.code, "DOCX_XML_INVALID")

    def test_docx_entity_declaration_is_rejected_before_xml_parse(self) -> None:
        xml = b'''<!DOCTYPE x [<!ENTITY secret "PRIVATE_ENTITY">]><x/>'''
        inputs = self._case([("entity.docx", _docx_bytes(xml))])
        with self.assertRaises(PlannerFailure) as caught:
            build_page_plan(
                source_root=inputs[0],
                core_manifest=inputs[1],
                negative_manifest=inputs[2],
                route_plan_path=inputs[3],
                profile="full",
            )
        self.assertEqual(caught.exception.code, "DOCX_XML_INVALID")

    def test_utf16_docx_entity_declaration_is_rejected_before_xml_parse(self) -> None:
        xml = '''<?xml version="1.0" encoding="utf-16"?>
<!DOCTYPE x [<!ENTITY secret "PRIVATE_ENTITY">]><x>&secret;</x>'''.encode("utf-16")
        inputs = self._case([("utf16-entity.docx", _docx_bytes(xml))])
        with self.assertRaises(PlannerFailure) as caught:
            build_page_plan(
                source_root=inputs[0],
                core_manifest=inputs[1],
                negative_manifest=inputs[2],
                route_plan_path=inputs[3],
                profile="full",
            )
        self.assertEqual(caught.exception.code, "DOCX_XML_INVALID")

    def test_xlsx_counts_cells_formulas_and_cache_without_values(self) -> None:
        plan, _ = self._plan([("structure.xlsx", _xlsx_bytes())])
        entry = plan["entries"][0]
        self.assertEqual(
            entry["structure_summary"],
            {
                "sheets": 1,
                "cells": 2,
                "formulas": 1,
                "value_cells": 2,
                "formula_cached_values": 1,
            },
        )
        serialized = _serialize(entry).decode("ascii")
        self.assertNotIn("PRIVATE_SHEET_NAME", serialized)
        self.assertNotIn("PRIVATE_CELL_VALUE", serialized)
        self.assertNotIn("page_no", serialized)

    def test_jpeg_is_one_ocr_visual_unit_with_dimensions(self) -> None:
        plan, _ = self._plan([("image.jpg", _jpeg_bytes(640, 480))])
        entry = plan["entries"][0]
        self.assertEqual(entry["page_count"], 1)
        self.assertEqual(entry["pages"][0]["width_px"], 640)
        self.assertEqual(entry["pages"][0]["height_px"], 480)
        self.assertEqual(entry["pages"][0]["decision"], "FULL_PAGE_OCR_REQUIRED")

    def test_legacy_doc_is_deferred_and_has_no_pages(self) -> None:
        plan, _ = self._plan([("legacy.doc", _valid_doc())])
        entry = plan["entries"][0]
        self.assertEqual(entry["parse_status"], "DEFERRED_CONVERSION_REQUIRED")
        self.assertNotIn("pages", entry)

    def test_source_symlink_is_rejected(self) -> None:
        payload = _pdf_bytes("A" * 20)
        inputs = self._case([("linked.pdf", payload)])
        target = inputs[0].parent / "target.pdf"
        target.write_bytes(payload)
        source_file = inputs[0] / "linked.pdf"
        source_file.unlink()
        source_file.symlink_to(target)
        with self.assertRaises(PlannerFailure):
            build_page_plan(
                source_root=inputs[0], core_manifest=inputs[1], negative_manifest=inputs[2],
                route_plan_path=inputs[3], profile="full"
            )

    def test_source_hardlink_is_rejected(self) -> None:
        inputs = self._case([("linked.pdf", _pdf_bytes("A" * 20))])
        os.link(inputs[0] / "linked.pdf", inputs[0].parent / "second-link.pdf")
        with self.assertRaises(PlannerFailure) as caught:
            build_page_plan(
                source_root=inputs[0], core_manifest=inputs[1], negative_manifest=inputs[2],
                route_plan_path=inputs[3], profile="full"
            )
        self.assertEqual(caught.exception.code, "UNSAFE_SOURCE_ENTRY")

    def test_source_fifo_is_rejected_without_blocking(self) -> None:
        inputs = self._case([("pipe.pdf", _pdf_bytes("A" * 20))])
        source_file = inputs[0] / "pipe.pdf"
        source_file.unlink()
        os.mkfifo(source_file, 0o600)
        with self.assertRaises(PlannerFailure):
            build_page_plan(
                source_root=inputs[0], core_manifest=inputs[1], negative_manifest=inputs[2],
                route_plan_path=inputs[3], profile="full"
            )

    def test_output_is_mode_0600_and_idempotent(self) -> None:
        root = self.base / "artifacts"
        plan = _safe_output_plan("smoke")
        write_page_outputs(plan, artifact_root=root)
        first = (root / "smoke-plan.json").read_bytes()
        write_page_outputs(plan, artifact_root=root)
        self.assertEqual((root / "smoke-plan.json").read_bytes(), first)
        self.assertEqual((root / "smoke-plan.json").stat().st_mode & 0o777, 0o600)

    def test_conflicting_output_is_not_overwritten(self) -> None:
        root = self.base / "conflict"
        root.mkdir(mode=0o700)
        target = root / "smoke-plan.json"
        target.write_bytes(b"KEEP")
        with self.assertRaises(PlannerFailure):
            write_page_outputs(_safe_output_plan("smoke"), artifact_root=root)
        self.assertEqual(target.read_bytes(), b"KEEP")

    def test_output_symlink_is_rejected_without_touching_target(self) -> None:
        root = self.base / "symlink-output"
        root.mkdir(mode=0o700)
        target = self.base / "keep-symlink"
        target.write_bytes(b"KEEP")
        (root / "smoke-plan.json").symlink_to(target)
        with self.assertRaises(PlannerFailure):
            write_page_outputs(_safe_output_plan("smoke"), artifact_root=root)
        self.assertEqual(target.read_bytes(), b"KEEP")

    def test_output_hardlink_is_rejected_without_touching_target(self) -> None:
        root = self.base / "hardlink-output"
        root.mkdir(mode=0o700)
        target = self.base / "keep-hardlink"
        target.write_bytes(b"KEEP")
        os.link(target, root / "smoke-plan.json")
        with self.assertRaises(PlannerFailure):
            write_page_outputs(_safe_output_plan("smoke"), artifact_root=root)
        self.assertEqual(target.read_bytes(), b"KEEP")

    def test_output_fifo_is_rejected_without_blocking(self) -> None:
        root = self.base / "fifo-output"
        root.mkdir(mode=0o700)
        os.mkfifo(root / "smoke-plan.json", 0o600)
        with self.assertRaises(PlannerFailure):
            write_page_outputs(_safe_output_plan("smoke"), artifact_root=root)

    def test_output_root_symlink_is_rejected(self) -> None:
        real_root = self.base / "real-output"
        real_root.mkdir()
        linked_root = self.base / "linked-output"
        linked_root.symlink_to(real_root, target_is_directory=True)
        with self.assertRaises(PlannerFailure):
            write_page_outputs(_safe_output_plan("smoke"), artifact_root=linked_root)
        self.assertEqual(list(real_root.iterdir()), [])

    def test_full_output_is_exact_fixed_set_and_status_is_aggregate(self) -> None:
        root = self.base / "full-output"
        plan = _safe_output_plan("full")
        write_page_outputs(plan, artifact_root=root)
        self.assertEqual(
            {path.name for path in root.iterdir()},
            {"full-plan.json", "status.html", "sbom.json"},
        )
        status = (root / "status.html").read_text(encoding="utf-8")
        self.assertNotIn("<script", status.casefold())
        sbom = (root / "sbom.json").read_text(encoding="utf-8")
        self.assertNotIn("/Users/", sbom)
        self.assertNotIn("Location", sbom)

    def test_writer_rejects_body_path_and_extra_key(self) -> None:
        root = self.base / "unsafe-plan"
        plan = _safe_output_plan("smoke")
        plan["raw_body"] = "PRIVATE_BODY /Users/private 13800138000"
        with self.assertRaises(PlannerFailure) as caught:
            write_page_outputs(plan, artifact_root=root)
        self.assertEqual(caught.exception.code, "UNSAFE_PLAN_SCHEMA")
        self.assertFalse(root.exists())

    def test_writer_rejects_valid_key_in_wrong_schema_level(self) -> None:
        root = self.base / "misplaced-plan-key"
        plan = _safe_output_plan("smoke")
        plan["summary"]["line"] = 13800138000
        with self.assertRaises(PlannerFailure) as caught:
            write_page_outputs(plan, artifact_root=root)
        self.assertEqual(caught.exception.code, "UNSAFE_PLAN_SCHEMA")
        self.assertFalse(root.exists())

    def test_writer_rejects_out_of_range_manifest_line(self) -> None:
        root = self.base / "out-of-range-line"
        plan, _ = self._plan([("line.pdf", _pdf_bytes("A" * 20))])
        plan["fixture_set_id"] = "environment-demo-seed"
        plan["fixture_version"] = "v0.1"
        plan["entries"][0]["line"] = 13800138000
        with self.assertRaises(PlannerFailure) as caught:
            write_page_outputs(plan, artifact_root=root)
        self.assertEqual(caught.exception.code, "UNSAFE_PLAN_SCHEMA")
        self.assertFalse(root.exists())

    def test_writer_rejects_out_of_range_native_character_count(self) -> None:
        root = self.base / "out-of-range-character-count"
        plan, _ = self._plan([("count.pdf", _pdf_bytes("A" * 20))])
        plan["fixture_set_id"] = "environment-demo-seed"
        plan["fixture_version"] = "v0.1"
        plan["entries"][0]["pages"][0]["native_characters"] = 13800138000
        with self.assertRaises(PlannerFailure) as caught:
            write_page_outputs(plan, artifact_root=root)
        self.assertEqual(caught.exception.code, "UNSAFE_PLAN_SCHEMA")
        self.assertFalse(root.exists())

    def test_writer_rejects_cached_formula_without_value_cell(self) -> None:
        root = self.base / "invalid-formula-cache"
        plan, _ = self._plan([("cache.xlsx", _xlsx_bytes())])
        plan["fixture_set_id"] = "environment-demo-seed"
        plan["fixture_version"] = "v0.1"
        entry = plan["entries"][0]
        entry["structure_summary"]["value_cells"] = 0
        entry["structure_anchors"][0]["value_cell_count"] = 0
        plan["summary"]["xlsx"]["value_cells"] = 0
        with self.assertRaises(PlannerFailure) as caught:
            write_page_outputs(plan, artifact_root=root)
        self.assertEqual(caught.exception.code, "UNSAFE_PLAN_SCHEMA")
        self.assertFalse(root.exists())

    def test_idempotent_output_rejects_wide_file_permissions(self) -> None:
        root = self.base / "wide-file"
        root.mkdir(mode=0o700)
        plan = _safe_output_plan("smoke")
        target = root / "smoke-plan.json"
        target.write_bytes(_serialize(plan))
        target.chmod(0o644)
        with self.assertRaises(PlannerFailure):
            write_page_outputs(plan, artifact_root=root)
        self.assertEqual(target.stat().st_mode & 0o777, 0o644)

    def test_writer_rejects_wide_directory_permissions(self) -> None:
        root = self.base / "wide-directory"
        root.mkdir(mode=0o755)
        with self.assertRaises(PlannerFailure):
            write_page_outputs(_safe_output_plan("smoke"), artifact_root=root)
        self.assertEqual(list(root.iterdir()), [])

    def test_full_batch_failure_rolls_back_files_created_by_that_call(self) -> None:
        root = self.base / "rollback"
        plan = _safe_output_plan("full")
        original = planner_module._create_output
        calls = 0

        def fail_second(directory: int, name: str, content: bytes) -> tuple[int, int]:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise PlannerFailure("OUTPUT_WRITE_FAILED")
            return original(directory, name, content)

        with mock.patch.object(planner_module, "_create_output", side_effect=fail_second):
            with self.assertRaises(PlannerFailure):
                write_page_outputs(plan, artifact_root=root)
        self.assertEqual(list(root.iterdir()), [])

    def test_short_write_rolls_back_incomplete_output(self) -> None:
        root = self.base / "short-write"
        plan = _safe_output_plan("smoke")
        with mock.patch.object(planner_module.os, "write", side_effect=(2, 0)):
            with self.assertRaises(PlannerFailure):
                write_page_outputs(plan, artifact_root=root)
        self.assertEqual(list(root.iterdir()), [])

    def test_summary_is_a_complete_visual_unit_partition(self) -> None:
        plan, _ = self._plan(
            [("mixed.pdf", _pdf_bytes(["A" * 19, "B" * 20])), ("image.jpg", _jpeg_bytes())]
        )
        summary = plan["summary"]
        self.assertEqual(
            summary["native_candidates"]
            + summary["ocr_required"]
            + summary["manual_review_required"],
            summary["visual_units"],
        )

    def test_page_ids_are_stable_lowercase_sha256(self) -> None:
        plan, _ = self._plan([("page-id.pdf", _pdf_bytes(["A" * 20, "B" * 20]))])
        page_ids = [page["page_id"] for page in plan["entries"][0]["pages"]]
        self.assertEqual(len(set(page_ids)), 2)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", value) for value in page_ids))

    def test_new_package_imports_only_standard_library_project_and_pypdf(self) -> None:
        source = Path(planner_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".", 1)[0])
        allowed = {
            "__future__", "contextlib", "errno", "fixture_gate", "fixture_router",
            "hashlib", "html", "importlib", "json", "logging", "math", "os",
            "pathlib", "platform", "pypdf", "stat", "typing", "unicodedata",
            "warnings", "xml", "zipfile",
        }
        self.assertEqual(roots - allowed, set())

    def test_failure_public_record_never_contains_exception_or_path(self) -> None:
        error = PlannerFailure("FIXED_CODE", group="core", line=1, document_id="a" * 64)
        serialized = json.dumps(error.public_record(), sort_keys=True)
        self.assertEqual(set(error.public_record()), {"code", "group", "line", "document_id"})
        self.assertNotIn("/", serialized)
        self.assertNotIn("traceback", serialized.casefold())

    def test_rendered_status_never_contains_page_records(self) -> None:
        plan = {
            "summary": {
                "documents": 1,
                "visual_units": 1,
                "native_candidates": 1,
                "ocr_required": 0,
                "manual_review_required": 0,
                "doc_deferred": 0,
            },
            "entries": [{"native_text_sha256": "b" * 64}],
        }
        status = render_status_html(plan)
        self.assertNotIn("b" * 64, status)
        self.assertNotIn("native_text", status)


if __name__ == "__main__":
    unittest.main()
