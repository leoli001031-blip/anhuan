"""Real isolated JPEG decode; OCR transports are deterministic offline fakes."""
from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageCms

from platform_foundation.f1.features.evidence.jpeg_native import JpegExtractionError, extract_jpeg
from platform_foundation.f1.features.material_intake.jpeg_renderer import JpegRenderError, render_jpeg
from platform_foundation.f1.features.material_intake.cloud_ocr import CloudOcrConfig
from platform_foundation.f1.features.p3.preview import build_preview


def jpeg(orientation=1, *, size=(80, 40), profile=None):
    image = Image.new('RGB', size, 'white')
    # Fixed four corners: red / green / blue / yellow; each quadrant is uniform.
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
    for box, color in zip([(0, 0, size[0] // 2, size[1] // 2), (size[0] // 2, 0, size[0], size[1] // 2), (0, size[1] // 2, size[0] // 2, size[1]), (size[0] // 2, size[1] // 2, size[0], size[1])], colors):
        image.paste(color, box)
    exif = Image.Exif(); exif[274] = orientation; exif[270] = 'synthetic-private-description'
    output = io.BytesIO(); image.save(output, 'JPEG', quality=95, subsampling=0, exif=exif, icc_profile=profile)
    return output.getvalue()


def render(raw):
    return render_jpeg(raw, expected_sha256=hashlib.sha256(raw).hexdigest())


def complete(text='42 mg/L'):
    return json.dumps({'choices': [{'message': {'content': text}, 'finish_reason': 'stop'}]}).encode()


class JpegNativeEvidence(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='native-jpeg-test-')
        self.addCleanup(self.directory.cleanup)
        self.key = Path(self.directory.name) / 'key'
        self.key.write_text('synthetic-test-key'); self.key.chmod(0o600)
        self.config = CloudOcrConfig(provider='glm_vision', api_key_file=self.key, model='synthetic-model', base_url='https://example.invalid')
        self.env = patch.dict(os.environ, {'F1_MATERIAL_OCR_ENABLED': '0'})
        self.env.start(); self.addCleanup(self.env.stop)

    def extract(self, raw=None, response=None, config=None, transport=None):
        raw = jpeg() if raw is None else raw
        fake = transport or Mock(return_value=complete() if response is None else response)
        result = extract_jpeg(raw, expected_sha256=hashlib.sha256(raw).hexdigest(), config=config or self.config, transport=fake)
        return result, fake

    def test_all_eight_orientations_match_fixed_corner_expectations(self):
        expected = {1: 'RGBY', 2: 'GRYB', 3: 'YBGR', 4: 'BYRG', 5: 'RBGY', 6: 'BRYG', 7: 'YGBR', 8: 'GYRB'}
        colors = {'R': (255, 0, 0), 'G': (0, 255, 0), 'B': (0, 0, 255), 'Y': (255, 255, 0)}
        for orientation, order in expected.items():
            with self.subTest(orientation=orientation):
                result = render(jpeg(orientation))
                self.assertEqual((result.source_width, result.source_height), (80, 40))
                self.assertEqual((result.width, result.height), (40, 80) if orientation >= 5 else (80, 40))
                self.assertEqual(result.exif_orientation, orientation)
                with Image.open(io.BytesIO(result.image)) as image:
                    points = [(3, 3), (image.width - 4, 3), (3, image.height - 4), (image.width - 4, image.height - 4)]
                    for point, key in zip(points, order):
                        self.assertTrue(all(abs(a - b) <= 5 for a, b in zip(image.getpixel(point), colors[key])))
                    self.assertFalse(image.getexif())
                    pixel_bytes = b'anhuan.material.image.rgb.v1\0' + image.width.to_bytes(4, 'big') + image.height.to_bytes(4, 'big') + image.tobytes()
                    self.assertEqual(result.pixel_sha256, hashlib.sha256(pixel_bytes).hexdigest())
                self.assertNotIn(b'synthetic-private-description', result.image)
                self.assertEqual(result.image_sha256, hashlib.sha256(result.image).hexdigest())

    def test_display_preview_and_ocr_use_identical_oriented_image_bytes(self):
        raw = jpeg(6)
        preview = build_preview('jpeg', io.BytesIO(raw))
        sent = []
        def transport(url, headers, payload, timeout):
            request = json.loads(payload)
            sent.append(base64.b64decode(request['messages'][0]['content'][0]['image_url']['url'].split(',', 1)[1]))
            self.assertEqual(url, 'https://example.invalid/chat/completions')
            return complete()
        result, _ = self.extract(raw, transport=transport)
        self.assertTrue(result.report_source_eligible)
        self.assertEqual(sent, [preview.units[0].content])
        self.assertEqual((preview.units[0].width_px, preview.units[0].height_px), (40, 80))
        locator = result.blocks[0].locator
        self.assertEqual(locator.rendered_sha256, hashlib.sha256(sent[0]).hexdigest())
        self.assertNotIn('page_number', locator.to_dict())

    def test_large_image_is_bounded_without_cropping_and_grayscale_is_supported(self):
        result = render(jpeg(size=(5000, 100)))
        self.assertEqual((result.source_width, result.source_height), (5000, 100))
        self.assertEqual(result.width, 4096)
        self.assertLessEqual(result.width * result.height, 8_000_000)
        output = io.BytesIO(); Image.new('L', (10, 20), 127).save(output, 'JPEG')
        self.assertEqual((render(output.getvalue()).width, render(output.getvalue()).height), (10, 20))

    def test_valid_srgb_profile_is_applied_then_removed_invalid_profile_rejected(self):
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
        result = render(jpeg(profile=profile))
        with Image.open(io.BytesIO(result.image)) as image:
            self.assertNotIn('icc_profile', image.info)
            self.assertTrue(image.getpixel((3, 3))[0] > 245)
        with self.assertRaisesRegex(JpegRenderError, 'JPEG_COLOR_PROFILE_UNRESOLVED'):
            render(jpeg(profile=b'invalid-profile'))

    def test_short_image_text_is_valid_and_ocr_identity_contains_no_secret(self):
        result, transport = self.extract(response=complete('42'))
        self.assertEqual(result.coverage_state, 'complete')
        self.assertEqual(result.blocks[0].text, '42')
        self.assertEqual((result.expected_block_count, result.processed_block_count), (1, 1))
        self.assertEqual(result.processing_identity['model'], 'synthetic-model')
        self.assertNotIn(str(self.key), repr(result))
        self.assertNotIn('synthetic-test-key', repr(result))
        self.assertNotIn('42', repr(result.blocks[0]).split('text=')[1])
        self.assertEqual(transport.call_count, 1)

    def test_truncated_or_empty_response_never_produces_usable_fragment(self):
        for response in [b'{broken', complete(''), complete('123\x00'),
            json.dumps({'choices': [{'message': {'content': 'partial text'}, 'finish_reason': 'length'}]}).encode(),
            json.dumps({'choices': [{'message': {'content': 'partial text'}}]}).encode(),
            complete('x' * 100_001)]:
            result, _ = self.extract(response=response)
            self.assertEqual(result.coverage_state, 'partial')
            self.assertFalse(result.report_source_eligible)
            self.assertEqual(result.blocks, ())

    def test_transport_timeout_or_error_is_retryable_and_payload_is_cleared(self):
        captured = []
        def error(url, headers, payload, timeout):
            captured.append(payload)
            raise OSError('must not escape with body')
        result, _ = self.extract(transport=error)
        self.assertTrue(result.retryable)
        self.assertEqual(result.debts[0].reason_code, 'OCR_UNAVAILABLE')
        self.assertEqual(captured, [bytearray()])

    def test_local_engine_selection_never_silently_sends_image_to_cloud(self):
        with patch.dict(os.environ, {'F1_MATERIAL_OCR_ENABLED': '1'}):
            result, transport = self.extract()
        self.assertEqual(result.debts[0].reason_code, 'JPEG_LOCAL_OCR_UNSUPPORTED')
        transport.assert_not_called()

    def test_disabled_missing_or_bad_key_never_calls_transport(self):
        for config in [CloudOcrConfig(), replace(self.config, api_key_file=Path(self.directory.name) / 'missing')]:
            result, transport = self.extract(config=config)
            self.assertEqual(result.coverage_state, 'partial')
            transport.assert_not_called()
        self.key.chmod(0o644)
        result, transport = self.extract()
        self.assertEqual(result.coverage_state, 'partial')
        transport.assert_not_called()

    def test_anthropic_completion_and_tool_or_truncated_blocks(self):
        config = replace(self.config, dialect='anthropic')
        response = json.dumps({'content': [{'type': 'text', 'text': '42 mg/L'}], 'stop_reason': 'end_turn'}).encode()
        result, transport = self.extract(config=config, response=response)
        self.assertTrue(result.report_source_eligible)
        self.assertEqual(transport.call_args[0][0], 'https://example.invalid/v1/messages')
        for value in [{'content': [{'type': 'text', 'text': '42'}], 'stop_reason': 'max_tokens'},
            {'content': [{'type': 'text', 'text': '42'}, {'type': 'tool_use'}], 'stop_reason': 'end_turn'}]:
            result, _ = self.extract(config=config, response=json.dumps(value).encode())
            self.assertEqual(result.blocks, ())

    def test_changed_model_changes_identity_even_for_same_text_and_pixels(self):
        raw = jpeg()
        first, _ = self.extract(raw)
        second, _ = self.extract(raw, config=replace(self.config, model='other-model'))
        self.assertEqual(first.blocks[0].text, second.blocks[0].text)
        self.assertEqual(first.blocks[0].locator, second.blocks[0].locator)
        self.assertNotEqual(first.manifest_sha256, second.manifest_sha256)
        self.assertNotEqual(first.blocks[0].token_sha256, second.blocks[0].token_sha256)

    def test_bad_orientation_truncated_stream_hash_and_resource_failure_are_rejected(self):
        for raw in [jpeg(0), jpeg(9)]:
            with self.assertRaisesRegex(JpegRenderError, 'JPEG_ORIENTATION_INVALID'):
                render(raw)
        with self.assertRaisesRegex(JpegRenderError, 'JPEG_SOURCE_INVALID'):
            render(jpeg()[:-30])
        with self.assertRaisesRegex(JpegRenderError, 'JPEG_SOURCE_SHA_MISMATCH'):
            render_jpeg(jpeg(), expected_sha256='a' * 64)
        with self.assertRaisesRegex(JpegRenderError, 'JPEG_RENDER_TIMEOUT'):
            render_jpeg(jpeg(), expected_sha256=hashlib.sha256(jpeg()).hexdigest(), timeout=0.00001)
        with self.assertRaises(JpegExtractionError):
            self.extract(b'not jpeg')


if __name__ == '__main__':
    unittest.main()
