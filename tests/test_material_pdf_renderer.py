"""Independent pixel/layout oracles over actual PDFs and real PDFium workers.

Transport is injected; model recognition accuracy is explicitly not tested.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import subprocess
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject, RectangleObject

from tests.test_material_cloud_ocr import _Env, _complete_chat, _jpeg
from platform_foundation.f1.features.material_intake.cloud_ocr import CloudOcrConfig, CloudOcrError, cloud_ocr_pdf_pages
from platform_foundation.f1.features.material_intake.cloud_ocr_transport import bounded_https_exchange
from platform_foundation.f1.features.material_intake.pdf_renderer import MAX_PIXELS, MAX_SOURCE_BYTES, PdfRenderError, render_pdf_page
from platform_foundation.f1.features.material_intake.ocr import extract_pdf_text_pages
from platform_foundation.f1.features.material_intake.analyzer import analyze_pdf
from platform_foundation.f1.features.material_intake.ocr import CLOUD_OCR_PARSER_BACKEND

_TEXT = "Visible page: left red cell, right blue cell. Synthetic OCR response with more than forty characters."
_HIDDEN = "THIS NATIVE TEXT IS COMPLETELY COVERED AND MUST NEVER BE APPENDED TO OCR RESULTS."


def layout_pdf(kind="normal", *, dimensions=(200, 100), pages=1):
    writer = PdfWriter()
    image = Image.new("RGB", (100, 50), "red")
    image.paste("blue", (50, 0, 100, 50))
    data = io.BytesIO(); image.save(data, "JPEG", quality=95, subsampling=0); image.close()
    stream = DecodedStreamObject(); stream.set_data(data.getvalue())
    stream.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
        NameObject("/Width"): NumberObject(100), NameObject("/Height"): NumberObject(50),
        NameObject("/BitsPerComponent"): NumberObject(8), NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
        NameObject("/Filter"): NameObject("/DCTDecode")})
    resources = DictionaryObject({NameObject("/XObject"): DictionaryObject({NameObject("/Image"): writer._add_object(stream)})})
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    resources[NameObject("/Font")] = DictionaryObject({NameObject("/F1"): writer._add_object(font)})
    for _ in range(pages):
        page = writer.add_blank_page(*dimensions)
        page[NameObject("/Resources")] = resources
        ops = b"q 200 0 0 100 0 0 cm /Image Do Q\n"
        if kind == "clip":
            ops = b"0 0 100 100 re W n\n" + ops
        elif kind == "form":
            form = DecodedStreamObject(); form.set_data(ops)
            form.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                NameObject("/BBox"): RectangleObject((0, 0, 200, 100)), NameObject("/Resources"): resources})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): DictionaryObject({NameObject("/Form"): writer._add_object(form)})})
            ops = b"/Form Do\n"
        elif kind == "inline":
            ops = b"q 200 0 0 100 0 0 cm BI /W 2 /H 1 /CS /RGB /BPC 8 ID \xff\x00\x00\x00\x00\xff\nEI Q\n"
        elif kind in ("multiple_images", "mixed_filters"):
            import zlib
            xobjects = DictionaryObject()
            for name, color in (("/Left", (255, 0, 0)), ("/Right", (0, 0, 255))):
                pixels = bytes(color) * 64 * 32
                flate = kind == "mixed_filters" and name == "/Right"
                obj = DecodedStreamObject(); obj.set_data(zlib.compress(pixels) if flate else _jpeg(color))
                obj.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                    NameObject("/Width"): NumberObject(64), NameObject("/Height"): NumberObject(32),
                    NameObject("/BitsPerComponent"): NumberObject(8), NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                    NameObject("/Filter"): NameObject("/FlateDecode" if flate else "/DCTDecode")})
                xobjects[NameObject(name)] = writer._add_object(obj)
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): xobjects})
            ops = b"q 100 0 0 100 0 0 cm /Left Do Q q 100 0 0 100 100 0 cm /Right Do Q\n"
        elif kind == "overlay":
            ops += b"1 g 100 0 100 100 re f\n"
        elif kind == "covered_text":
            ops = f"BT /F1 5 Tf 1 45 Td ({_HIDDEN}) Tj ET\n1 g 0 0 200 100 re f\n".encode()
        elif kind == "table":
            ops = b"1 0 0 rg 0 50 100 50 re f 0 0 1 rg 100 50 100 50 re f 0 1 0 rg 0 0 100 50 re f 1 1 0 rg 100 0 100 50 re f 0 G 2 w 100 0 m 100 100 l S 0 50 m 200 50 l S\n"
        contents = DecodedStreamObject(); contents.set_data(ops)
        page[NameObject("/Contents")] = writer._add_object(contents)
        if kind == "crop":
            page.cropbox = RectangleObject((100, 0, 200, 100))
        elif kind == "rotate":
            page[NameObject("/Rotate")] = NumberObject(90)
        elif kind == "annotation":
            appearance = DecodedStreamObject(); appearance.set_data(b"0 1 0 rg 0 0 100 50 re f")
            appearance.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"), NameObject("/BBox"): RectangleObject((0, 0, 100, 50))})
            annot = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Square"),
                NameObject("/Rect"): RectangleObject((50, 25, 150, 75)), NameObject("/AP"): DictionaryObject({NameObject("/N"): writer._add_object(appearance)})})
            page[NameObject("/Annots")] = ArrayObject([writer._add_object(annot)])
    output = io.BytesIO(); writer.write(output); return output.getvalue()


def request_image(payload):
    content = json.loads(payload)["messages"][0]["content"]
    images = [block for block in content if block["type"] == "image_url"]
    if len(images) != 1:
        raise AssertionError("exactly one whole-page image required")
    return Image.open(io.BytesIO(base64.b64decode(images[0]["image_url"]["url"].split(",", 1)[1])))


class PdfRendererPixelContracts(unittest.TestCase):
    def color(self, image, x, y, expected):
        pixel = image.getpixel((int(image.width*x), int(image.height*y)))
        for actual, target in zip(pixel, expected):
            self.assertLess(abs(actual - target), 18, (pixel, expected))

    def test_render_and_transport_agree_with_independent_visible_layout(self):
        _Env(self).enable()
        for kind in ("normal", "crop", "rotate", "clip", "form", "inline", "multiple_images", "mixed_filters", "overlay", "table", "annotation"):
            with self.subTest(kind=kind):
                source = layout_pdf(kind)
                calls = []
                def transport(_url, _headers, payload, _timeout):
                    with request_image(payload) as image:
                        calls.append(image.size)
                        if kind == "crop":
                            self.assertEqual(image.size, (278, 278)); self.color(image, .25, .5, (0, 0, 255)); self.color(image, .75, .5, (0, 0, 255))
                        elif kind == "rotate":
                            self.assertEqual(image.size, (278, 556)); self.color(image, .5, .25, (255, 0, 0)); self.color(image, .5, .75, (0, 0, 255))
                        elif kind == "table":
                            for x, y, color in ((.25,.25,(255,0,0)),(.75,.25,(0,0,255)),(.25,.75,(0,255,0)),(.75,.75,(255,255,0)),(.5,.5,(0,0,0))):
                                self.color(image, x, y, color)
                        elif kind == "annotation":
                            self.color(image, .5, .5, (0,255,0)); self.color(image, .1, .5, (255,0,0))
                        else:
                            self.assertEqual(image.size, (556,278)); self.color(image,.25,.5,(255,0,0))
                            self.color(image,.75,.5,(255,255,255) if kind in ("clip","overlay") else (0,0,255))
                    return _complete_chat(_TEXT)
                checkpoints = []
                result = cloud_ocr_pdf_pages(source, page_numbers=[1], transport=transport, completed_page_callback=checkpoints.append)
                self.assertEqual(len(calls), 1); self.assertEqual(len(checkpoints), 1)
                self.assertTrue(result[0].ocr_applied); self.assertEqual(result[0].text, _TEXT)

    def test_covered_native_text_routes_to_visible_page_and_is_not_appended(self):
        _Env(self).enable(); source = layout_pdf("covered_text"); seen=[]
        self.assertIn(_HIDDEN, PdfReader(io.BytesIO(source)).pages[0].extract_text())
        def transport(_url,_headers,payload,_timeout):
            with request_image(payload) as image:
                self.assertEqual(image.convert("L").getextrema(), (255,255))
            seen.append(1); return _complete_chat("")
        def engine(*args,**kwargs):
            kwargs.pop("config",None); return cloud_ocr_pdf_pages(*args,transport=transport,**kwargs)
        pages=extract_pdf_text_pages(source,ocr_pages=engine)
        self.assertEqual(seen,[1]); self.assertTrue(pages[0].ocr_required)
        self.assertNotIn(_HIDDEN,pages[0].text)
        analysis=analyze_pdf(io.BytesIO(source),expected_sha256=hashlib.sha256(source).hexdigest(),ocr_pages=engine,ocr_parser_backend=CLOUD_OCR_PARSER_BACKEND)
        self.assertTrue(analysis.pages[0].ocr_required)
        self.assertEqual(analysis.pages[0].text_character_count,0)
        self.assertEqual(analysis.candidates,())

    def test_large_page_downscales_before_bitmap_allocation(self):
        with Image.open(io.BytesIO(render_pdf_page(layout_pdf(dimensions=(14000,14000)),1))) as image:
            self.assertLessEqual(image.width*image.height,MAX_PIXELS)
            self.assertLessEqual(max(image.size),4096)

    def test_real_worker_timeout_is_bounded(self):
        started=time.monotonic()
        with self.assertRaisesRegex(PdfRenderError,"PDF_RENDER_TIMEOUT"):
            render_pdf_page(layout_pdf(),1,timeout=.000001)
        self.assertLess(time.monotonic()-started,2)

    def test_https_worker_has_a_killable_whole_request_deadline(self):
        actual_popen = subprocess.Popen
        with patch('platform_foundation.f1.features.material_intake.cloud_ocr_transport.subprocess.Popen', wraps=actual_popen) as popen:
            started = time.monotonic()
            with self.assertRaisesRegex(OSError, "OCR_UNAVAILABLE"):
                bounded_https_exchange("https://example.invalid/", {}, bytearray(b"{}"), .000001)
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(popen.call_args.kwargs["env"], {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})

    def test_pdf_worker_does_not_inherit_cloud_secrets_or_python_path(self):
        actual_popen = subprocess.Popen
        with patch.dict(os.environ, {"F1_MATERIAL_CLOUD_OCR_API_KEY_FILE": "/synthetic/secret", "PYTHONPATH": "/synthetic/module"}):
            with patch('platform_foundation.f1.features.material_intake.pdf_renderer.subprocess.Popen', wraps=actual_popen) as popen:
                render_pdf_page(layout_pdf(), 1)
                self.assertEqual(popen.call_args.kwargs["env"], {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})

    def test_encrypted_even_empty_password_document_is_rejected(self):
        for password in ("", "synthetic-password"):
            writer=PdfWriter(); writer.add_blank_page(200,100); writer.encrypt(password)
            output=io.BytesIO(); writer.write(output)
            with self.assertRaisesRegex(PdfRenderError, "OCR_SOURCE_ENCRYPTED"):
                render_pdf_page(output.getvalue(),1)

    def test_late_transport_result_never_checkpoints(self):
        _Env(self).enable(); checkpoints=[]
        config=replace(CloudOcrConfig.from_environment(),request_timeout_seconds=1,total_timeout_seconds=1)
        def late(*_):
            time.sleep(1.05); return _complete_chat(_TEXT)
        result=cloud_ocr_pdf_pages(layout_pdf(),page_numbers=[1],config=config,transport=late,completed_page_callback=checkpoints.append)
        self.assertFalse(result[0].ocr_applied);self.assertEqual(checkpoints,[])

    def test_source_page_count_and_geometry_limits_never_checkpoint(self):
        _Env(self).enable(); checkpoints=[]; calls=[]
        def transport(*_):
            calls.append(1); return _complete_chat(_TEXT)
        with self.assertRaisesRegex(PdfRenderError,"OCR_SOURCE_LIMIT"):
            render_pdf_page(b"0"*(MAX_SOURCE_BYTES+1),1)
        with self.assertRaisesRegex(CloudOcrError,"OCR_PAGE_INVALID"):
            cloud_ocr_pdf_pages(layout_pdf(pages=129),page_numbers=[1],transport=transport,completed_page_callback=checkpoints.append)
        with self.assertRaisesRegex(CloudOcrError,"OCR_PAGE_INVALID"):
            cloud_ocr_pdf_pages(layout_pdf(),page_numbers=[1,2],transport=transport,completed_page_callback=checkpoints.append)
        result=cloud_ocr_pdf_pages(layout_pdf(dimensions=(15000,100)),page_numbers=[1],transport=transport,completed_page_callback=checkpoints.append)
        self.assertFalse(result[0].ocr_applied); self.assertEqual(checkpoints,[]); self.assertEqual(calls,[])

    def test_render_timeout_never_checkpoints(self):
        _Env(self).enable(); checkpoints=[];calls=[]
        with patch('platform_foundation.f1.features.material_intake.cloud_ocr.PAGE_TIMEOUT_SECONDS',.000001):
            result=cloud_ocr_pdf_pages(layout_pdf(),page_numbers=[1],transport=lambda *_:calls.append(1),completed_page_callback=checkpoints.append)
        self.assertFalse(result[0].ocr_applied);self.assertEqual(calls,[]);self.assertEqual(checkpoints,[])


if __name__ == "__main__":
    unittest.main()
