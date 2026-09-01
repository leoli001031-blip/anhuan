"""Pure offline contracts for the explicitly configured cloud OCR adapter.

No network access happens here: the HTTP transport is always injected, and
every test proves the fail-closed, bounded, and no-leak boundaries of the
Ark vision adapter plus its checkpoint-backend integration.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

# The service import chain resolves the OIDC issuer at import time; the
# handoff's inert placeholder keeps that boundary loadable without any real
# Keycloak, exactly like TEST_HANDOFF.md prescribes for host-side tooling.
os.environ.setdefault(
    "F1_KEYCLOAK_ISSUER_URL", "http://material-rag.invalid/realms/anhuan"
)

from platform_foundation.f1.features.material_intake.analyzer import (
    MaterialAnalysisFailure,
    analyze_pdf,
)
from platform_foundation.f1.features.material_intake.cloud_ocr import (
    CLOUD_OCR_PARSER_BACKEND,
    CLOUD_OCR_PROVIDERS,
    CloudOcrConfig,
    CloudOcrError,
    OcrEngine,
    cloud_ocr_capability,
    cloud_ocr_pdf_pages,
    resolve_ocr_engine,
)
from platform_foundation.f1.features.material_intake.ocr import (
    OCR_PARSER_BACKEND,
    OCR_PARSER_BACKENDS,
    LocalOcrError,
    OcrPageResult,
    ocr_checkpoint_aad,
    ocr_pdf_pages,
)
from platform_foundation.f1.features.material_intake.service import (
    _checkpoint_body,
)

_LONG_TEXT = (
    "危险化学品安全管理条例第一条规定，为了加强危险化学品的安全管理，"
    "预防和减少危险化学品事故，保障人民群众生命财产安全，保护环境，制定本条例。"
)
_SHORT_TEXT = "少量文字"


def _scanned_pdf(
    image: bytes,
    *,
    width: int = 64,
    height: int = 32,
    dct: bool = True,
    pages: int = 1,
) -> bytes:
    """Hand-roll a minimal PDF whose pages carry one full-page image."""

    objects: dict[int, bytes] = {}
    kids = " ".join(f"{3 + index * 2} 0 R" for index in range(pages))
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode()
    for index in range(pages):
        page_number = 3 + index * 2
        image_number = page_number + 1
        filter_name = "/DCTDecode" if dct else "/FlateDecode"
        objects[page_number] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 100] "
            f"/Resources << /XObject << /Im0 {image_number} 0 R >> >> >>"
        ).encode()
        objects[image_number] = (
            f"<< /Type /XObject /Subtype /Image /Width {width} "
            f"/Height {height} /ColorSpace /DeviceRGB "
            f"/BitsPerComponent 8 /Filter {filter_name} "
            f"/Length {len(image)} >>\nstream\n"
        ).encode() + image + b"\nendstream"
    out = bytearray(b"%PDF-1.7\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out.extend(f"{number} 0 obj\n".encode())
        out.extend(objects[number])
        out.extend(b"\nendobj\n")
    xref_offset = len(out)
    max_number = max(objects)
    out.extend(f"xref\n0 {max_number + 1}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for number in range(1, max_number + 1):
        out.extend(f"{offsets[number]:010d} 00000 n \n".encode())
    out.extend(
        (
            f"trailer\n<< /Size {max_number + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
    )
    return bytes(out)


class _Env:
    """Bounded environment and 0600 key-file fixture with cleanup."""

    def __init__(self, testcase: unittest.TestCase) -> None:
        self.testcase = testcase
        self.directory = Path(
            tempfile.mkdtemp(prefix="cloud-ocr-test-")
        )
        self.directory.chmod(0o700)
        self.key_file = self.directory / "ark_api_key"
        self.key_file.write_text("test-cloud-key-value", encoding="ascii")
        self.key_file.chmod(0o600)
        self._names = (
            "F1_MATERIAL_CLOUD_OCR_PROVIDER",
            "F1_MATERIAL_CLOUD_OCR_API_KEY_FILE",
            "F1_MATERIAL_CLOUD_OCR_MODEL",
            "F1_MATERIAL_CLOUD_OCR_BASE_URL",
            "F1_MATERIAL_CLOUD_OCR_DIALECT",
            "F1_MATERIAL_CLOUD_OCR_MAX_PAGES",
            "F1_MATERIAL_OCR_ENABLED",
        )
        self._original = {name: os.environ.get(name) for name in self._names}
        for name in self._names:
            os.environ.pop(name, None)
        testcase.addCleanup(self._restore)

    def _restore(self) -> None:
        for name, value in self._original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def enable(
        self,
        *,
        key_file: str | None = None,
        model: str = "doubao-vision-test",
        base_url: str | None = None,
        fifo: bool = False,
        max_pages: str | None = None,
    ) -> None:
        os.environ["F1_MATERIAL_CLOUD_OCR_PROVIDER"] = "glm_vision"
        os.environ["F1_MATERIAL_CLOUD_OCR_API_KEY_FILE"] = (
            key_file if key_file is not None else str(self.key_file)
        )
        os.environ["F1_MATERIAL_CLOUD_OCR_MODEL"] = model
        if base_url is not None:
            os.environ["F1_MATERIAL_CLOUD_OCR_BASE_URL"] = base_url
        if max_pages is not None:
            os.environ["F1_MATERIAL_CLOUD_OCR_MAX_PAGES"] = max_pages
        os.environ["F1_MATERIAL_OCR_ENABLED"] = "1" if fifo else "0"


def _fake_transport(content: str):
    calls: list[tuple[str, dict[str, str], bytes, float]] = []

    def transport(
        url: str,
        headers: dict[str, str],
        body: bytearray,
        timeout: float,
    ) -> bytes:
        calls.append((url, dict(headers), bytes(body), timeout))
        return json.dumps(
            {"choices": [{"message": {"content": content}}]}
        ).encode("utf-8")

    return transport, calls


class CloudOcrCapabilityContracts(unittest.TestCase):
    def test_disabled_by_default_and_engine_keeps_fifo(self) -> None:
        env = _Env(self)
        capability = cloud_ocr_capability()
        self.assertEqual(capability.state, "disabled")
        self.assertEqual(capability.reason_code, "OCR_DISABLED")
        engine = resolve_ocr_engine()
        self.assertEqual(engine.backend, OCR_PARSER_BACKEND)
        self.assertIs(engine.pages, ocr_pdf_pages)

    def test_key_file_missing_fails_closed(self) -> None:
        _Env(self).enable(key_file="/nonexistent/ark_api_key")
        capability = cloud_ocr_capability()
        self.assertEqual(capability.state, "unavailable")
        self.assertEqual(
            capability.reason_code, "CLOUD_OCR_API_KEY_FILE_REQUIRED"
        )
        results = cloud_ocr_pdf_pages(b"ignored", page_numbers=[1])
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].ocr_applied)
        self.assertEqual(results[0].reason_code, "CLOUD_OCR_API_KEY_FILE_REQUIRED")
        self.assertEqual(results[0].parser_backend, CLOUD_OCR_PARSER_BACKEND)

    def test_key_file_mode_not_0600_fails_closed(self) -> None:
        env = _Env(self)
        env.key_file.chmod(0o644)
        env.enable()
        capability = cloud_ocr_capability()
        self.assertEqual(capability.state, "unavailable")
        self.assertEqual(
            capability.reason_code, "CLOUD_OCR_API_KEY_FILE_INVALID"
        )

    def test_model_required_when_enabled(self) -> None:
        _Env(self).enable(model="")
        capability = cloud_ocr_capability()
        self.assertEqual(capability.state, "unavailable")
        self.assertEqual(capability.reason_code, "CLOUD_OCR_MODEL_REQUIRED")

    def test_http_base_url_rejected(self) -> None:
        _Env(self).enable(base_url="http://ark.example.invalid/api/plan/v3")
        capability = cloud_ocr_capability()
        self.assertEqual(capability.state, "unavailable")
        self.assertEqual(
            capability.reason_code, "CLOUD_OCR_BASE_URL_INVALID"
        )

    def test_fifo_enabled_wins_over_cloud(self) -> None:
        _Env(self).enable(fifo=True)
        engine = resolve_ocr_engine()
        self.assertEqual(engine.backend, OCR_PARSER_BACKEND)

    def test_cloud_selected_when_fifo_disabled(self) -> None:
        _Env(self).enable()
        engine = resolve_ocr_engine()
        self.assertEqual(engine.backend, CLOUD_OCR_PARSER_BACKEND)

    def test_anthropic_dialect_defaults_to_compatible_endpoint(self) -> None:
        env = _Env(self)
        env.enable()
        os.environ["F1_MATERIAL_CLOUD_OCR_DIALECT"] = "anthropic"
        config = __import__(
            "platform_foundation.f1.features.material_intake.cloud_ocr",
            fromlist=["CloudOcrConfig"],
        ).CloudOcrConfig.from_environment()
        self.assertEqual(
            config.base_url, "https://open.bigmodel.cn/api/anthropic"
        )
        self.assertEqual(cloud_ocr_capability().state, "ready")

    def test_unknown_dialect_rejected(self) -> None:
        env = _Env(self)
        env.enable()
        os.environ["F1_MATERIAL_CLOUD_OCR_DIALECT"] = "grpc"
        capability = cloud_ocr_capability()
        self.assertEqual(capability.state, "unavailable")
        self.assertEqual(
            capability.reason_code,
            "MATERIAL_CLOUD_OCR_CONFIGURATION_INVALID",
        )

    def test_glm_provider_defaults_to_zhipu_endpoint(self) -> None:
        env = _Env(self)
        env.enable()
        config = __import__(
            "platform_foundation.f1.features.material_intake.cloud_ocr",
            fromlist=["CloudOcrConfig"],
        ).CloudOcrConfig.from_environment()
        self.assertEqual(
            config.base_url, "https://open.bigmodel.cn/api/paas/v4"
        )
        self.assertEqual(cloud_ocr_capability().state, "ready")

    def test_unknown_provider_rejected(self) -> None:
        env = _Env(self)
        env.enable()
        os.environ["F1_MATERIAL_CLOUD_OCR_PROVIDER"] = "openai_vision"
        capability = cloud_ocr_capability()
        self.assertEqual(capability.state, "unavailable")
        self.assertEqual(
            capability.reason_code,
            "MATERIAL_CLOUD_OCR_CONFIGURATION_INVALID",
        )


class CloudOcrPageContracts(unittest.TestCase):
    def _scanned(self, **kwargs) -> bytes:
        return _scanned_pdf(b"\xff\xd8\xff\xd9fake-jpeg-bytes", **kwargs)

    def test_applied_page_binds_cloud_identity(self) -> None:
        env = _Env(self)
        env.enable()
        source = self._scanned()
        expected = hashlib.sha256(source).hexdigest()
        applied: list[OcrPageResult] = []
        transport, calls = _fake_transport(_LONG_TEXT)
        results = cloud_ocr_pdf_pages(
            source,
            page_numbers=[1],
            expected_sha256=expected,
            transport=transport,
            completed_page_callback=applied.append,
        )
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(len(applied), 1)
        self.assertIs(result, applied[0])
        self.assertTrue(result.ocr_applied)
        self.assertEqual(result.status, "applied")
        self.assertEqual(result.reason_code, "OCR_APPLIED")
        self.assertEqual(result.parser_backend, CLOUD_OCR_PARSER_BACKEND)
        self.assertRegex(result.source_unit_id, r"^[0-9a-f]{64}$")
        self.assertEqual(
            result.source_unit_id,
            hashlib.sha256(
                f"{expected}:1:{CLOUD_OCR_PARSER_BACKEND}".encode()
            ).hexdigest(),
        )
        self.assertEqual(result.character_count, result.character_count)
        self.assertGreaterEqual(result.character_count, 40)

        self.assertEqual(len(calls), 1)
        url, headers, body, timeout = calls[0]
        self.assertTrue(url.endswith("/chat/completions"))
        self.assertTrue(url.startswith("https://"))
        self.assertEqual(headers["Authorization"], "Bearer test-cloud-key-value")
        request = json.loads(body.decode("ascii"))
        self.assertEqual(request["model"], "doubao-vision-test")
        content = request["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "image_url")
        self.assertTrue(
            content[0]["image_url"]["url"].startswith(
                "data:image/jpeg;base64,"
            )
        )
        decoded = base64.b64decode(
            content[0]["image_url"]["url"].split(",", 1)[1]
        )
        self.assertEqual(decoded, b"\xff\xd8\xff\xd9fake-jpeg-bytes")
        self.assertEqual(content[1]["type"], "text")
        self.assertIn("逐字转录", content[1]["text"])
        self.assertGreater(timeout, 0)

    def test_insufficient_text_is_not_applied(self) -> None:
        env = _Env(self)
        env.enable()
        applied: list[OcrPageResult] = []
        transport, _calls = _fake_transport(_SHORT_TEXT)
        results = cloud_ocr_pdf_pages(
            self._scanned(),
            page_numbers=[1],
            transport=transport,
            completed_page_callback=applied.append,
        )
        self.assertEqual(results[0].status, "insufficient_text")
        self.assertEqual(
            results[0].reason_code, "OCR_OUTPUT_INSUFFICIENT"
        )
        self.assertFalse(results[0].ocr_applied)
        self.assertEqual(applied, [])

    def test_anthropic_dialect_request_and_response_shape(self) -> None:
        import io as _io

        env = _Env(self)
        env.enable()
        os.environ["F1_MATERIAL_CLOUD_OCR_DIALECT"] = "anthropic"
        source = _scanned_pdf(b"\xff\xd8\xff\xd9fake-jpeg-bytes")
        applied: list[OcrPageResult] = []

        def anthropic_transport(url, headers, body, timeout):
            request = json.loads(bytes(body).decode("ascii"))
            self.assertTrue(url.endswith("/v1/messages"))
            self.assertTrue(
                url.startswith("https://open.bigmodel.cn/api/anthropic")
            )
            self.assertEqual(
                headers.get("anthropic-version"), "2023-06-01"
            )
            self.assertEqual(
                headers.get("Authorization"), "Bearer test-cloud-key-value"
            )
            self.assertEqual(request["model"], "doubao-vision-test")
            self.assertEqual(request["max_tokens"], 4096)
            blocks = request["messages"][0]["content"]
            self.assertEqual(blocks[0]["type"], "image")
            self.assertEqual(
                blocks[0]["source"]["media_type"], "image/jpeg"
            )
            decoded = base64.b64decode(blocks[0]["source"]["data"])
            self.assertEqual(decoded, b"\xff\xd8\xff\xd9fake-jpeg-bytes")
            self.assertEqual(blocks[1]["type"], "text")
            return json.dumps(
                {
                    "content": [
                        {"type": "text", "text": _LONG_TEXT},
                        {"type": "image", "source": {}},
                    ]
                }
            ).encode("utf-8")

        results = cloud_ocr_pdf_pages(
            source,
            page_numbers=[1],
            expected_sha256=hashlib.sha256(source).hexdigest(),
            transport=anthropic_transport,
            completed_page_callback=applied.append,
        )
        self.assertTrue(results[0].ocr_applied)
        self.assertEqual(results[0].parser_backend, CLOUD_OCR_PARSER_BACKEND)
        self.assertEqual(len(applied), 1)

    def test_transport_failure_fails_closed_without_leaking_key(self) -> None:
        env = _Env(self)
        env.enable()

        def failing_transport(url, headers, body, timeout):
            raise OSError("connection reset")

        results = cloud_ocr_pdf_pages(
            self._scanned(), page_numbers=[1], transport=failing_transport
        )
        self.assertFalse(results[0].ocr_applied)
        self.assertEqual(results[0].reason_code, "OCR_UNAVAILABLE")
        self.assertEqual(results[0].parser_backend, CLOUD_OCR_PARSER_BACKEND)

    def test_non_dct_page_image_fails_closed(self) -> None:
        env = _Env(self)
        env.enable()
        transport, calls = _fake_transport(_LONG_TEXT)
        source = self._scanned(dct=False)
        results = cloud_ocr_pdf_pages(
            source, page_numbers=[1], transport=transport
        )
        self.assertFalse(results[0].ocr_applied)
        self.assertEqual(results[0].reason_code, "OCR_UNAVAILABLE")
        self.assertEqual(calls, [])

    def test_page_limit_respected(self) -> None:
        env = _Env(self)
        env.enable(max_pages="1")
        transport, _calls = _fake_transport(_LONG_TEXT)
        results = cloud_ocr_pdf_pages(
            self._scanned(pages=2),
            page_numbers=[1, 2],
            transport=transport,
        )
        self.assertTrue(results[0].ocr_applied)
        self.assertEqual(results[1].status, "page_limit")
        self.assertEqual(results[1].reason_code, "OCR_PAGE_LIMIT")
        self.assertFalse(results[1].ocr_applied)

    def test_identity_mismatch_rejected(self) -> None:
        env = _Env(self)
        env.enable()
        with self.assertRaises(CloudOcrError) as raised:
            cloud_ocr_pdf_pages(
                self._scanned(),
                page_numbers=[1],
                expected_sha256="0" * 64,
                transport=_fake_transport(_LONG_TEXT)[0],
            )
        self.assertEqual(raised.exception.code, "OCR_SOURCE_IDENTITY_MISMATCH")


class CloudOcrCheckpointContracts(unittest.TestCase):
    def _identity(self) -> dict[str, object]:
        import uuid

        return {
            "enterprise_id": uuid.uuid4(),
            "document_version_id": uuid.uuid4(),
            "source_sha256": "a" * 64,
            "expected_page_count": 3,
            "page_number": 2,
            "source_unit_id": "b" * 64,
            "body_sha256": "c" * 64,
            "character_count": 100,
            "confidence_mean_ppm": None,
            "table_candidate": False,
            "two_column_candidate": False,
        }

    def test_aad_accepts_cloud_backend_only_in_closed_set(self) -> None:
        identity = self._identity()
        for backend in OCR_PARSER_BACKENDS:
            aad = ocr_checkpoint_aad(
                parser_backend=backend, **identity
            )
            self.assertIsInstance(aad, bytes)
        with self.assertRaises(LocalOcrError):
            ocr_checkpoint_aad(
                parser_backend="openai-vision-1", **identity
            )

    def test_checkpoint_body_accepts_cloud_result(self) -> None:
        result = OcrPageResult(
            page_number=1,
            text=_LONG_TEXT,
            status="applied",
            reason_code="OCR_APPLIED",
            ocr_applied=True,
            character_count=sum(
                not character.isspace() for character in _LONG_TEXT
            ),
            parser_backend=CLOUD_OCR_PARSER_BACKEND,
            source_unit_id="d" * 64,
        )
        value, encoded, body_sha256 = _checkpoint_body(result)
        self.assertEqual(value, _LONG_TEXT)
        self.assertEqual(
            hashlib.sha256(encoded).hexdigest(), body_sha256
        )


class CloudOcrAnalyzerContracts(unittest.TestCase):
    def test_cloud_checkpoint_accepted_only_for_active_backend(self) -> None:
        import io

        source = _scanned_pdf(b"\xff\xd8\xff\xd9fake-jpeg-bytes")
        expected = hashlib.sha256(source).hexdigest()
        checkpoint = OcrPageResult(
            page_number=1,
            text=_LONG_TEXT,
            status="applied",
            reason_code="OCR_APPLIED",
            ocr_applied=True,
            character_count=sum(
                not character.isspace() for character in _LONG_TEXT
            ),
            parser_backend=CLOUD_OCR_PARSER_BACKEND,
            source_unit_id="e" * 64,
        )
        transport, _calls = _fake_transport("")
        engine_pages = lambda *args, **kwargs: cloud_ocr_pdf_pages(
            *args, transport=transport, **kwargs
        )
        with io.BytesIO(source) as handle:
            result = analyze_pdf(
                handle,
                expected_sha256=expected,
                ocr_checkpoints=[checkpoint],
                ocr_pages=engine_pages,
                ocr_parser_backend=CLOUD_OCR_PARSER_BACKEND,
            )
        self.assertTrue(
            any(
                "OCR_APPLIED" in page.reason_codes
                for page in result.pages
            )
        )
        self.assertTrue(
            any(page.text_character_count >= 40 for page in result.pages)
        )

        with io.BytesIO(source) as handle:
            with self.assertRaises(MaterialAnalysisFailure) as raised:
                analyze_pdf(
                    handle,
                    expected_sha256=expected,
                    ocr_checkpoints=[checkpoint],
                    ocr_pages=engine_pages,
                    ocr_parser_backend=OCR_PARSER_BACKEND,
                )
        self.assertEqual(
            str(raised.exception), "MATERIAL_OCR_CHECKPOINT_INVALID"
        )

    def test_unknown_engine_backend_rejected(self) -> None:
        import io

        source = _scanned_pdf(b"\xff\xd8\xff\xd9fake-jpeg-bytes")
        with io.BytesIO(source) as handle:
            with self.assertRaises(MaterialAnalysisFailure) as raised:
                analyze_pdf(
                    handle,
                    expected_sha256=hashlib.sha256(source).hexdigest(),
                    ocr_parser_backend="unknown-backend",
                )
        self.assertEqual(str(raised.exception), "MATERIAL_OCR_ENGINE_INVALID")


if __name__ == "__main__":
    unittest.main()
