"""Native extraction must agree with visible pixels; no cloud service is used."""
from __future__ import annotations

import hashlib
import io
import unittest

from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from platform_foundation.f1.features.material_intake.analyzer import analyze_pdf
from platform_foundation.f1.features.material_intake.ocr import (
    CLOUD_OCR_PARSER_BACKEND, OcrPageResult, extract_pdf_text_pages,
)
from platform_foundation.f1.features.material_intake.pdf_images import page_requires_visual_ocr
from platform_foundation.f1.features.material_intake.pdf_renderer import render_pdf_page

_TEXT = "SYNTHETIC SAFETY REPORT: Inspection completed on 2026-09-08."


def _pdf(operations: bytes, *, font_changes=None, page_box=None) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(620, 800)
    if page_box is not None:
        page.mediabox = page_box
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    if font_changes:
        font.update(font_changes)
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
        DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    content = DecodedStreamObject()
    content.set_data(operations)
    page[NameObject("/Contents")] = writer._add_object(content)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _text(prefix: str, suffix: str = "") -> bytes:
    return f"BT {prefix} ({_TEXT}) Tj {suffix} ET".encode()


class NativeVisibilityContracts(unittest.TestCase):
    def assert_requires_ocr_without_native_evidence(self, source, *, all_white=False):
        page = PdfReader(io.BytesIO(source)).pages[0]
        self.assertIn(_TEXT, page.extract_text())
        self.assertTrue(page_requires_visual_ocr(page))
        calls = []

        def insufficient_ocr(body, *, page_numbers, **_kwargs):
            self.assertEqual(body, source)
            self.assertEqual(tuple(page_numbers), (1,))
            calls.append(1)
            if all_white:
                with Image.open(io.BytesIO(render_pdf_page(body, 1))) as image:
                    self.assertEqual(image.convert("L").getextrema(), (255, 255))
            return (OcrPageResult(page_number=1, text="", status="insufficient_text",
                reason_code="OCR_OUTPUT_INSUFFICIENT", ocr_applied=False,
                character_count=0, parser_backend=CLOUD_OCR_PARSER_BACKEND),)

        extracted = extract_pdf_text_pages(source, ocr_pages=insufficient_ocr)[0]
        self.assertTrue(extracted.ocr_required)
        self.assertEqual(extracted.text, "")
        self.assertEqual(extracted.text_source, "none")
        analysis = analyze_pdf(io.BytesIO(source), expected_sha256=hashlib.sha256(source).hexdigest(),
            ocr_pages=insufficient_ocr, ocr_parser_backend=CLOUD_OCR_PARSER_BACKEND)
        self.assertTrue(analysis.pages[0].ocr_required)
        self.assertEqual(analysis.pages[0].text_character_count, 0)
        self.assertEqual(analysis.candidates, ())
        self.assertEqual(calls, [1, 1])

    def test_white_pages_never_expose_hidden_native_text(self):
        prefixes = {
            "outside_right": "/F1 9 Tf 1 0 0 1 900 500 Tm",
            "outside_below": "/F1 9 Tf 20 -100 Td",
            "zero_font": "/F1 0 Tf 20 500 Td",
            "negative_font": "/F1 -9 Tf -100 -100 Td",
            "tiny_font": "/F1 0.000001 Tf 20 500 Td",
            "zero_matrix": "/F1 9 Tf 0 0 0 0 20 500 Tm",
            "negative_matrix": "/F1 9 Tf -1 0 0 -1 -100 -100 Tm",
            "rotated_matrix": "/F1 9 Tf 0 1 -1 0 -100 500 Tm",
            "off_page_TD": "/F1 9 Tf 20 500 Td 900 0 TD",
            "off_page_Tstar": "/F1 9 Tf 20 500 Td 900 TL T*",
        }
        for name, prefix in prefixes.items():
            with self.subTest(case=name):
                self.assert_requires_ocr_without_native_evidence(_pdf(_text(prefix)), all_white=True)

    def test_string_width_and_spacing_cannot_cross_page_edges(self):
        for prefix in ("/F1 9 Tf 600 500 Td", "/F1 9 Tf 20 500 Td 30 Tc",
                       "/F1 9 Tf 20 500 Td 300 Tw", "/F1 9 Tf 20 799 Td",
                       "/F1 9 Tf 1 500 Td", "/F1 9 Tf 20 1 Td"):
            with self.subTest(prefix=prefix):
                self.assert_requires_ocr_without_native_evidence(_pdf(_text(prefix)))

    def test_TJ_adjustments_are_applied_to_the_next_text_position(self):
        for adjustment in (-100000, 100000):
            with self.subTest(adjustment=adjustment):
                source = _pdf(f"BT /F1 9 Tf 20 500 Td [{adjustment} ({_TEXT})] TJ ET".encode())
                self.assert_requires_ocr_without_native_evidence(source, all_white=True)

    def test_repositioned_or_backtracking_text_cannot_overprint_native_evidence(self):
        second_text = "DIFFERENT RESULT: Equipment failed inspection."
        programs = (
            _text("/F1 9 Tf 1 0 0 1 20 500 Tm", f"1 0 0 1 20 500 Tm ({second_text}) Tj"),
            _text("/F1 9 Tf 20 500 Td", f"0 0 Td ({second_text}) Tj"),
            _text("/F1 9 Tf 20 500 Td", f"20 0 Td ({second_text}) Tj"),
            _text("/F1 9 Tf 20 500 Td", f"0 -2 Td ({second_text}) Tj"),
            _text("/F1 9 Tf 20 500 Td") + _text("/F1 9 Tf 20 500 Td"),
            f"BT /F1 9 Tf 20 500 Td [({_TEXT}) 5000 ({second_text})] TJ ET".encode(),
        )
        for program in programs:
            with self.subTest(program=program):
                source = _pdf(program)
                with Image.open(io.BytesIO(render_pdf_page(source, 1))) as image:
                    self.assertLess(image.convert("L").getextrema()[0], 100)
                self.assert_requires_ocr_without_native_evidence(source)

    def test_ordinary_horizontal_native_operations_keep_the_fast_path(self):
        programs = (
            _text("/F1 9 Tf 20 740 Td"),
            _text("/F1 9 Tf 1.2 0 0 1 20 740 Tm"),
            _text("/F1 9 Tf 20 760 Td 0 -20 TD", f"T* ({_TEXT}) Tj"),
            _text("/F1 9 Tf 20 740 Td 14 TL", f"({_TEXT}) ' 0 0 ({_TEXT}) \""),
            _text("/F1 9 Tf 20 740 Td 0.5 Tc 1 Tw"),
            f"q BT /F1 9 Tf 20 740 Td [({_TEXT[:20]}) -100 ({_TEXT[20:]})] TJ ET Q".encode(),
            f"BT /F1 9 Tf 20 740 Td ({_TEXT[:20]}) Tj ({_TEXT[20:]}) Tj ET".encode(),
            _text("/F1 8 Tf 20 740 Td", f"1 0 0 1 350 740 Tm ({_TEXT}) Tj"),
            f"BT /F1 9 Tf ET BT 20 740 Td ({_TEXT}) Tj ET".encode(),
        )
        for program in programs:
            with self.subTest(program=program):
                source = _pdf(program)
                self.assertFalse(page_requires_visual_ocr(PdfReader(io.BytesIO(source)).pages[0]))
                with Image.open(io.BytesIO(render_pdf_page(source, 1))) as image:
                    self.assertLess(image.convert("L").getextrema()[0], 100)

                def unexpected_ocr(*_args, **_kwargs):
                    self.fail("Visible native text should not invoke OCR")

                page = extract_pdf_text_pages(source, ocr_pages=unexpected_ocr)[0]
                self.assertFalse(page.ocr_required)
                self.assertEqual(page.text_source, "pypdf")
                self.assertIn("SYNTHETIC SAFETY REPORT", page.text)
                analysis = analyze_pdf(io.BytesIO(source), expected_sha256=hashlib.sha256(source).hexdigest(),
                    ocr_pages=unexpected_ocr)
                self.assertFalse(analysis.pages[0].ocr_required)
                self.assertGreater(analysis.pages[0].text_character_count, 40)

    def test_unknown_fonts_encodings_and_malformed_state_require_ocr(self):
        for changes in (
            {NameObject("/Subtype"): NameObject("/Type3")},
            {NameObject("/BaseFont"): NameObject("/UnprovenFont")},
            {NameObject("/Encoding"): DictionaryObject()},
            {NameObject("/FontDescriptor"): DictionaryObject()},
            {NameObject("/ToUnicode"): DecodedStreamObject()},
        ):
            with self.subTest(font=changes):
                source = _pdf(_text("/F1 9 Tf 20 740 Td"), font_changes=changes)
                self.assertTrue(page_requires_visual_ocr(PdfReader(io.BytesIO(source)).pages[0]))
        for program in (
            _text("/F1 9 Tf 1 0.2 0 1 20 500 Tm"),
            _text("/F1 9 Tf 1 0 0.2 1 20 500 Tm"),
            _text("/F1 9 Tf 20 500 Td -10 Tc"),
            _text("/F1 9 Tf 20 500 Td", "Q"),
            b"Q", b"BT ET ET", b"q BT ET", b"BT /F1 9 Tf 1 0 0 1 20 Tm ET",
        ):
            with self.subTest(program=program):
                self.assertTrue(page_requires_visual_ocr(PdfReader(io.BytesIO(_pdf(program))).pages[0]))


if __name__ == "__main__":
    unittest.main()
