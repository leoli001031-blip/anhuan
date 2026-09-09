"""Whole-image OCR evidence with original/render/model identities kept separate."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import re
import time
from urllib.error import HTTPError, URLError

from .contracts import ImageLocator, canonical_json
from .docx_native import CoverageDebt
from ..material_intake.jpeg_renderer import JpegRenderError, RenderedJpeg, render_jpeg
from ..material_intake.cloud_ocr import (
    CloudOcrConfig, CloudOcrError, MAX_CLOUD_OCR_RESPONSE_BYTES,
    _CLOUD_OCR_PROMPT, _build_page_request, _chat_response_text,
    _default_transport, _dialect_endpoint, _read_api_key, cloud_ocr_capability,
)
from ..material_intake.ocr import LocalOcrConfig

PARSER_VERSION = 'jpeg-ocr-1'
SUPPORT_PROFILE = 'jpeg-oriented-rgb-whole-image-1'
OCR_BACKEND = 'cloud-vision-image-1'


class JpegExtractionError(ValueError):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class JpegBlock:
    locator: ImageLocator
    text: str = field(repr=False)
    token_sha256: str

    @property
    def body_sha256(self):
        return hashlib.sha256(self.text.encode()).hexdigest()

    def __repr__(self):
        return f'JpegBlock(locator={self.locator!r}, text=<redacted>)'


@dataclass(frozen=True, slots=True)
class JpegExtraction:
    source_sha256: str
    manifest_sha256: str
    blocks: tuple[JpegBlock, ...]
    debts: tuple[CoverageDebt, ...]
    expected_block_count: int
    processed_block_count: int
    processing_identity: dict
    retryable: bool = False
    parser_version: str = PARSER_VERSION
    support_profile: str = SUPPORT_PROFILE

    @property
    def coverage_state(self):
        return 'partial' if self.debts else 'complete'

    @property
    def report_source_eligible(self):
        return not self.debts and any(block.text.strip() for block in self.blocks)


def image_processing_identity(active, rendered):
    """No key, key path, endpoint URL or source body in the public identity."""
    return {'backend': OCR_BACKEND, 'provider': active.provider, 'model': active.model,
        'dialect': active.dialect, 'endpoint_sha256': hashlib.sha256(active.base_url.encode()).hexdigest(),
        'prompt_sha256': hashlib.sha256(_CLOUD_OCR_PROMPT.encode()).hexdigest(),
        'renderer': rendered.renderer_version, 'image_sha256': rendered.image_sha256,
        'pixel_sha256': rendered.pixel_sha256}


def _ocr_image(rendered, active, transport, source_sha256):
    from ... import ocr_cache
    from ..material_intake import jpeg_renderer, cloud_ocr
    if LocalOcrConfig.from_environment().enabled:
        # The existing FIFO contract accepts PDF pages only. Never switch an
        # explicitly selected local engine to a remote engine for this format.
        return '', 'JPEG_LOCAL_OCR_UNSUPPORTED', False
    if not isinstance(active.model, str) or len(active.model) > 200 or (active.model and re.fullmatch(r'[A-Za-z0-9_.:/-]+', active.model) is None):
        return '', 'OCR_UNAVAILABLE', False
    capability = cloud_ocr_capability(active)
    if capability.state != 'ready':
        return '', 'OCR_DISABLED' if capability.state == 'disabled' else 'OCR_UNAVAILABLE', False
    payload = bytearray()
    try:
        key = _read_api_key(active.api_key_file)
        path, extra = _dialect_endpoint(active.dialect)
        headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json', **extra}
        deadline = time.monotonic() + active.request_timeout_seconds
        payload = _build_page_request(dialect=active.dialect, model=active.model, image=rendered.image)
        ticket = ocr_cache.Ticket(source_sha256, 1, ocr_cache.cloud_identity(
            active, payload, OCR_BACKEND, {'image': image_processing_identity(active, rendered),
                'code': ocr_cache.code_identity(__file__, jpeg_renderer.__file__, cloud_ocr.__file__)}))
        cached = ticket.load()
        if cached is not None:
            if time.monotonic() >= deadline:
                raise CloudOcrError('OCR_UNAVAILABLE')
            return ocr_cache.cached_text(cached, minimum=1), None, False
        raw = transport(active.base_url.rstrip('/') + path, headers, payload, active.request_timeout_seconds)
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_CLOUD_OCR_RESPONSE_BYTES or time.monotonic() >= deadline:
            raise CloudOcrError('OCR_UNAVAILABLE')
        # Do not silently delete control characters or discard tool/image
        # response blocks before validating the complete text response.
        value = json.loads(raw)
        if active.dialect == 'anthropic':
            entries = value['content']
            if not isinstance(entries, list) or any(not isinstance(item, dict) or item.get('type') != 'text' or not isinstance(item.get('text'), str) for item in entries):
                raise ValueError
            original = '\n'.join(item['text'] for item in entries)
        else:
            original = value['choices'][0]['message']['content']
        if not isinstance(original, str) or any(ord(char) < 32 and char not in '\r\n\t' for char in original):
            raise ValueError
        text = _chat_response_text(raw, active.dialect)
        if not text.strip():
            return '', 'OCR_OUTPUT_INSUFFICIENT', False
        # Small labels can be valid images: no PDF-specific 40-character rule.
        text = ocr_cache.cached_text(ticket.save({'text': text}), minimum=1)
        if time.monotonic() >= deadline:
            raise CloudOcrError('OCR_UNAVAILABLE')
        return text, None, False
    except (CloudOcrError, ocr_cache.OcrCacheError, HTTPError, URLError, OSError, ValueError, KeyError, TypeError, IndexError):
        return '', 'OCR_UNAVAILABLE', True
    finally:
        payload[:] = b'\0' * len(payload)
        payload.clear()


def extract_jpeg(source: bytes, *, expected_sha256: str,
                 config: CloudOcrConfig | None = None, transport=None):
    try:
        rendered = render_jpeg(source, expected_sha256=expected_sha256)
    except JpegRenderError as exc:
        raise JpegExtractionError(str(exc)) from None
    active = config or CloudOcrConfig.from_environment()
    text, reason, retryable = _ocr_image(rendered, active, transport or _default_transport, expected_sha256)
    identity = image_processing_identity(active, rendered)
    locator = ImageLocator(rendered.source_width, rendered.source_height,
        rendered.width, rendered.height, rendered.image_sha256, rendered.exif_orientation)
    token = {'source_sha256': expected_sha256, 'locator': locator.to_dict(),
        'processing_identity': identity, 'text': text}
    blocks = () if reason else (JpegBlock(locator, text, hashlib.sha256(canonical_json(token)).hexdigest()),)
    debts = (CoverageDebt(reason, 'image', '/whole-image'),) if reason else ()
    manifest = {'source_sha256': expected_sha256, 'parser_version': PARSER_VERSION,
        'support_profile': SUPPORT_PROFILE, 'processing_identity': identity,
        'locator': locator.to_dict(), 'body_sha256': hashlib.sha256(text.encode()).hexdigest(),
        'debt': reason}
    return JpegExtraction(expected_sha256, hashlib.sha256(canonical_json(manifest)).hexdigest(),
        blocks, debts, 1, 1, identity, retryable)
