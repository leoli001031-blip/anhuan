"""Actual PDF painting operations + injected model transport; no cloud calls."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("F1_KEYCLOAK_ISSUER_URL", "http://material-rag.invalid/realms/anhuan")

from PIL import Image
from pypdf import PdfReader, PdfWriter
from tests.test_material_cloud_ocr import _jpeg
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from platform_foundation.f1.features.material_intake.analyzer import analyze_pdf
from platform_foundation.f1.features.material_intake.cloud_ocr import CloudOcrConfig, cloud_ocr_pdf_pages
from platform_foundation.f1.features.material_intake.ocr import CLOUD_OCR_PARSER_BACKEND, extract_pdf_text_pages

_A = _jpeg("red", (64, 64))
_B = _jpeg("blue", (64, 64))
_TEXT_A = "Outfall A COD is 42 mg/L and within the recorded limit. END OF PAGE A."
_TEXT_B = "Outfall B COD is 242 mg/L and exceeds the recorded limit. END OF PAGE B."
_HEADER = "SYNTHETIC MONTHLY MONITORING REPORT. Values are in the table below."


def _pdf(*, pages=2, header=False, extra=b"", draw=True) -> bytes:
    writer = PdfWriter()
    images = DictionaryObject()
    for name, data in (("/ImA", _A), ("/ImB", _B)):
        stream = DecodedStreamObject()
        stream.set_data(data)
        stream.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                       NameObject("/Width"): NumberObject(64), NameObject("/Height"): NumberObject(64),
                       NameObject("/BitsPerComponent"): NumberObject(8), NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                       NameObject("/Filter"): NameObject("/DCTDecode")})
        images[NameObject(name)] = writer._add_object(stream)
    resources = DictionaryObject({NameObject("/XObject"): images})
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    resources[NameObject("/Font")] = DictionaryObject({NameObject("/F1"): writer._add_object(font)})
    shared = writer._add_object(resources)
    for number in range(pages):
        page = writer.add_blank_page(620, 800)
        page[NameObject("/Resources")] = shared
        operations = (f"BT /F1 9 Tf 20 740 Td ({_HEADER}) Tj ET\n".encode() if header else b"")
        operations += extra
        if draw:
            operations += f"q 500 0 0 500 20 100 cm /Im{'A' if number == 0 else 'B'} Do Q\n".encode()
        stream = DecodedStreamObject()
        stream.set_data(operations)
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class MaterialLayoutRegressions(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="material-layout-")
        self.addCleanup(directory.cleanup)
        key = Path(directory.name) / "key"
        key.write_text("synthetic-unused-secret")
        key.chmod(0o600)
        self.config = CloudOcrConfig(provider="glm_vision", model="test", api_key_file=key, base_url="https://example.invalid/v1")
        self.sent = []

    def transport(self, _url, _headers, payload, _timeout):
        image = base64.b64decode(json.loads(payload)["messages"][0]["content"][0]["image_url"]["url"].split(",", 1)[1])
        self.sent.append(image)
        pixels = Image.open(io.BytesIO(image))
        color = pixels.getpixel((int(pixels.width * .4), int(pixels.height * .5)))
        text = _TEXT_A if color[0] > 200 else _TEXT_B
        if pixels.crop((0, int(pixels.height * .04), pixels.width, int(pixels.height * .1))).convert("L").getextrema()[0] < 100:
            text = _HEADER + "\n" + text
        return json.dumps({"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}).encode()

    def engine(self, *args, **kwargs):
        kwargs.pop("config", None)
        return cloud_ocr_pdf_pages(*args, config=self.config, transport=self.transport, **kwargs)

    def test_shared_resources_do_not_cross_contaminate_page_evidence(self):
        source = _pdf()
        reader = PdfReader(io.BytesIO(source))
        self.assertEqual(set(reader.pages[0]["/Resources"]["/XObject"]), {"/ImA", "/ImB"})
        pages = self.engine(source, page_numbers=(1, 2))
        self.assertEqual(len(self.sent), 2)
        self.assertNotEqual(self.sent[0], self.sent[1])
        self.assertEqual([page.text for page in pages], [_TEXT_A, _TEXT_B])
        self.assertTrue(all(page.ocr_applied for page in pages))

    def test_native_header_cannot_hide_scan_from_analysis_or_index(self):
        source = _pdf(pages=1, header=True)
        sha = hashlib.sha256(source).hexdigest()
        result = analyze_pdf(io.BytesIO(source), expected_sha256=sha, ocr_pages=self.engine, ocr_parser_backend=CLOUD_OCR_PARSER_BACKEND)
        pages = extract_pdf_text_pages(source, expected_sha256=sha, ocr_pages=self.engine)
        self.assertEqual(len(self.sent), 2)
        self.assertFalse(result.pages[0].ocr_required)
        self.assertIn("OCR_APPLIED", result.pages[0].reason_codes)
        self.assertEqual(pages[0].text.count(_HEADER), 1)
        self.assertIn(_TEXT_A, pages[0].text)
        self.assertTrue(pages[0].ocr_applied)

    def test_unused_resource_does_not_force_ocr(self):
        source = _pdf(pages=1, header=True, draw=False)
        pages = extract_pdf_text_pages(source, ocr_pages=self.engine)
        self.assertEqual(self.sent, [])
        self.assertFalse(pages[0].ocr_required)
        self.assertEqual(pages[0].text.count(_HEADER), 1)

    def test_native_header_cannot_make_empty_image_ocr_successful(self):
        checkpoints = []
        pages = cloud_ocr_pdf_pages(_pdf(pages=1, header=True), page_numbers=(1,), config=self.config,
            transport=lambda *_: json.dumps({"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}).encode(),
            completed_page_callback=checkpoints.append)
        self.assertFalse(pages[0].ocr_applied)
        self.assertEqual(checkpoints, [])

    def test_truncated_and_missing_completion_metadata_never_checkpoint(self):
        source = _pdf(pages=1)
        for dialect, stop in (("chat", "length"), ("chat", None), ("anthropic", "max_tokens"), ("anthropic", None)):
            with self.subTest(dialect=dialect, stop=stop):
                response = ({"choices": [{"message": {"content": _TEXT_A}, "finish_reason": stop}]}
                            if dialect == "chat" else {"content": [{"type": "text", "text": _TEXT_A}], "stop_reason": stop})
                checkpoints = []
                pages = cloud_ocr_pdf_pages(source, page_numbers=(1,), config=replace(self.config, dialect=dialect),
                    transport=lambda *_: json.dumps(response).encode(), completed_page_callback=checkpoints.append)
                self.assertFalse(pages[0].ocr_applied)
                self.assertEqual(pages[0].reason_code, "OCR_UNAVAILABLE")
                self.assertEqual(checkpoints, [])
