"""Explicitly configured cloud OCR adapter over the Ark vision chat API.

The adapter is opt-in through ``F1_MATERIAL_CLOUD_OCR_PROVIDER``
(``glm_vision`` for Zhipu GLM, ``ark_vision`` for VolcEngine Ark) and reads
its bearer key from a 0600 regular-file secret.  It is fail-closed
everywhere: a missing or wrongly permissioned key file, a missing model id, a
non-HTTPS base URL, a page whose image cannot be extracted as JPEG, and any
transport failure all produce fixed-code ``OCR_UNAVAILABLE`` page results, so
callers keep ``OCR_REQUIRED`` semantics instead of partial success.  The local
FIFO runtime remains the authoritative engine whenever it is enabled; the two
providers are never silently mixed in one process.

OCR plaintext exists only in returned in-memory values and is persisted by the
caller through the authenticated encrypted checkpoint envelope.  The API key is
read from a 0600 regular file and never appears in errors, logs, or request
echoes.  Page images are bounded, and request/response byte buffers are zeroed
after use.  Python ``str`` content returned by the API cannot be zeroed in
place; callers must treat it as customer-confidential, exactly like FIFO OCR
text.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import ssl
import stat
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from pypdf import PdfReader

from .ocr import (
    MAX_OCR_PAGE_TEXT_CHARACTERS,
    MAX_OCR_PAGES_PER_DOCUMENT,
    DEFAULT_OCR_PAGES_PER_DOCUMENT,
    DEFAULT_OCR_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_OCR_TOTAL_TIMEOUT_SECONDS,
    _MAX_OCR_TOTAL_TIMEOUT_SECONDS,
    OCR_PARSER_BACKEND,
    LocalOcrConfig,
    OcrPageResult,
    _normalize_embedded_text,
    _read_source,
    ocr_pdf_pages,
)
from ..p3.contracts import MAX_PDF_PAGES


CLOUD_OCR_PARSER_BACKEND = "cloud-vision-chat-1"
CLOUD_OCR_PROVIDERS = frozenset(("ark_vision", "glm_vision"))
_DEFAULT_CLOUD_OCR_BASE_URLS = {
    "ark_vision": "https://ark.cn-beijing.volces.com/api/plan/v3",
    "glm_vision": "https://open.bigmodel.cn/api/paas/v4",
}
DEFAULT_CLOUD_OCR_BASE_URL = _DEFAULT_CLOUD_OCR_BASE_URLS["ark_vision"]
CLOUD_OCR_TRANSPORT = Callable[
    [str, dict[str, str], bytearray, float], bytes
]
# Ark rejects request bodies beyond roughly 10 MiB; keep a stricter margin so
# the bounded page image size stays the only variable component.
_MAX_PAGE_IMAGE_BYTES = 6 * 1024 * 1024
MAX_CLOUD_OCR_RESPONSE_BYTES = 4 * 1024 * 1024
_TRUE_VALUES = frozenset(("1", "true", "yes", "on"))

_CLOUD_OCR_PROMPT = (
    "逐字转录图片中全部可见文字。保留原有的段落与行结构，"
    "不要添加注释、翻译、总结或任何图片中没有的内容。"
    "图片中没有文字时仅输出空字符串。"
)

CloudOcrCapabilityState = Literal["disabled", "unavailable", "ready"]


class _NoRedirectHandler(HTTPRedirectHandler):
    """Refuse redirects so page images can never leave the configured origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(newurl, code, "CLOUD_OCR_REDIRECT_REFUSED", headers, fp)


class CloudOcrError(RuntimeError):
    """A fixed-code error which never includes key, image, or text content."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CloudOcrConfig:
    provider: str = ""
    api_key_file: Path | None = None
    model: str = ""
    base_url: str = DEFAULT_CLOUD_OCR_BASE_URL
    dialect: str = "chat"
    max_pages: int = DEFAULT_OCR_PAGES_PER_DOCUMENT
    request_timeout_seconds: float = DEFAULT_OCR_REQUEST_TIMEOUT_SECONDS
    total_timeout_seconds: float = DEFAULT_OCR_TOTAL_TIMEOUT_SECONDS
    configuration_valid: bool = True

    def __post_init__(self) -> None:
        if (
            self.provider not in ("",) + tuple(CLOUD_OCR_PROVIDERS)
            or self.dialect not in ("chat", "anthropic")
            or (
                self.provider == ""
                and (
                    self.api_key_file is not None
                    or self.model != ""
                    or self.base_url != DEFAULT_CLOUD_OCR_BASE_URL
                    or self.dialect != "chat"
                )
            )
            or (
                self.api_key_file is not None
                and (
                    not isinstance(self.api_key_file, Path)
                    or not self.api_key_file.is_absolute()
                )
            )
            or not 1 <= self.max_pages <= MAX_OCR_PAGES_PER_DOCUMENT
            or not math.isfinite(self.request_timeout_seconds)
            or not 1.0
            <= self.request_timeout_seconds
            <= DEFAULT_OCR_REQUEST_TIMEOUT_SECONDS
            or not math.isfinite(self.total_timeout_seconds)
            or not self.request_timeout_seconds
            <= self.total_timeout_seconds
            <= _MAX_OCR_TOTAL_TIMEOUT_SECONDS
        ):
            raise ValueError("MATERIAL_CLOUD_OCR_CONFIGURATION_INVALID")

    @property
    def enabled(self) -> bool:
        return self.provider in CLOUD_OCR_PROVIDERS

    @classmethod
    def from_environment(cls) -> "CloudOcrConfig":
        provider = os.environ.get("F1_MATERIAL_CLOUD_OCR_PROVIDER", "").strip()
        raw_key_file = os.environ.get(
            "F1_MATERIAL_CLOUD_OCR_API_KEY_FILE", ""
        ).strip()
        model = os.environ.get("F1_MATERIAL_CLOUD_OCR_MODEL", "").strip()
        dialect = os.environ.get(
            "F1_MATERIAL_CLOUD_OCR_DIALECT", "chat"
        ).strip()
        provider_default_base = _DEFAULT_CLOUD_OCR_BASE_URLS.get(
            provider, DEFAULT_CLOUD_OCR_BASE_URL
        )
        if provider == "glm_vision" and dialect == "anthropic":
            provider_default_base = (
                "https://open.bigmodel.cn/api/anthropic"
            )
        base_url = os.environ.get(
            "F1_MATERIAL_CLOUD_OCR_BASE_URL", provider_default_base
        ).strip()

        parsed_limits = _bounded_environment_limits()
        configuration_valid = (
            provider in ("",) + tuple(CLOUD_OCR_PROVIDERS)
            and dialect in ("chat", "anthropic")
            and (provider == "" or dialect == "chat" or provider == "glm_vision")
            and (raw_key_file.startswith("/") or raw_key_file == "")
            and (provider == "" or raw_key_file != "")
            and base_url.startswith(("https://", "http://"))
            and parsed_limits is not None
        )
        if not configuration_valid:
            return cls(
                provider="glm_vision",
                configuration_valid=False,
            )
        return cls(
            provider=provider,
            api_key_file=Path(raw_key_file) if raw_key_file else None,
            model=model,
            base_url=base_url,
            dialect=dialect,
            max_pages=parsed_limits[0],
            request_timeout_seconds=parsed_limits[1],
            total_timeout_seconds=parsed_limits[2],
        )


def _bounded_environment_limits() -> tuple[int, float, float] | None:
    def bounded_int(name: str) -> int | None:
        raw = os.environ.get(name, "").strip()
        if raw == "":
            return DEFAULT_OCR_PAGES_PER_DOCUMENT
        try:
            value = int(raw, 10)
        except ValueError:
            return None
        if not 1 <= value <= MAX_PDF_PAGES:
            return None
        return value

    def bounded_float(name: str, default: float) -> float | None:
        raw = os.environ.get(name, "").strip()
        if raw == "":
            return default
        try:
            value = float(raw)
        except ValueError:
            return None
        if not math.isfinite(value) or value <= 0:
            return None
        return value

    max_pages = bounded_int("F1_MATERIAL_CLOUD_OCR_MAX_PAGES")
    request_timeout = bounded_float(
        "F1_MATERIAL_CLOUD_OCR_REQUEST_TIMEOUT_SECONDS",
        DEFAULT_OCR_REQUEST_TIMEOUT_SECONDS,
    )
    total_timeout = bounded_float(
        "F1_MATERIAL_CLOUD_OCR_TOTAL_TIMEOUT_SECONDS",
        DEFAULT_OCR_TOTAL_TIMEOUT_SECONDS,
    )
    if max_pages is None or request_timeout is None or total_timeout is None:
        return None
    if not (
        request_timeout <= total_timeout <= _MAX_OCR_TOTAL_TIMEOUT_SECONDS
        and 1.0 <= request_timeout <= DEFAULT_OCR_REQUEST_TIMEOUT_SECONDS
    ):
        return None
    return max_pages, request_timeout, total_timeout


@dataclass(frozen=True, slots=True)
class CloudOcrCapability:
    state: CloudOcrCapabilityState
    reason_code: str | None
    backend: str = CLOUD_OCR_PARSER_BACKEND


def _read_api_key(path: Path) -> str:
    info = os.stat(path, follow_symlinks=False)
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise CloudOcrError("CLOUD_OCR_API_KEY_FILE_INVALID")
    with open(path, "rb", opener=lambda name, flags: os.open(  # noqa: S108
        name, flags | getattr(os, "O_NOFOLLOW", 0)
    )) as handle:
        raw = handle.read(4096)
    key = raw.decode("ascii", "strict").strip()
    if not key or any(character.isspace() for character in key):
        raise CloudOcrError("CLOUD_OCR_API_KEY_FILE_INVALID")
    return key


def cloud_ocr_capability(
    config: CloudOcrConfig | None = None,
) -> CloudOcrCapability:
    active = config or CloudOcrConfig.from_environment()
    if not active.configuration_valid:
        return CloudOcrCapability(
            state="unavailable",
            reason_code="MATERIAL_CLOUD_OCR_CONFIGURATION_INVALID",
        )
    if not active.enabled:
        return CloudOcrCapability(state="disabled", reason_code="OCR_DISABLED")
    if active.api_key_file is None:
        return CloudOcrCapability(
            state="unavailable",
            reason_code="CLOUD_OCR_API_KEY_FILE_REQUIRED",
        )
    try:
        _read_api_key(active.api_key_file)
    except CloudOcrError as error:
        return CloudOcrCapability(
            state="unavailable", reason_code=error.code
        )
    except OSError:
        return CloudOcrCapability(
            state="unavailable",
            reason_code="CLOUD_OCR_API_KEY_FILE_REQUIRED",
        )
    if not active.model:
        return CloudOcrCapability(
            state="unavailable",
            reason_code="CLOUD_OCR_MODEL_REQUIRED",
        )
    if not active.base_url.startswith("https://"):
        return CloudOcrCapability(
            state="unavailable",
            reason_code="CLOUD_OCR_BASE_URL_INVALID",
        )
    return CloudOcrCapability(state="ready", reason_code=None)


def _cloud_fallback_result(
    page_number: int, *, state: str, reason_code: str
) -> OcrPageResult:
    return OcrPageResult(
        page_number=page_number,
        text="",
        status="unavailable" if state == "unavailable" else "disabled",
        reason_code=reason_code,
        ocr_applied=False,
        character_count=0,
        parser_backend=CLOUD_OCR_PARSER_BACKEND,
    )


def _tls_context() -> ssl.SSLContext:
    """A verifying TLS context rooted in certifi when the bundle is present.

    Some host Pythons ship without system CA roots; the pinned ``certifi``
    dependency provides them.  Verification is never disabled.
    """
    context = ssl.create_default_context()
    try:
        import certifi
    except ImportError:
        return context
    try:
        return ssl.create_default_context(cafile=certifi.where())
    except (OSError, ssl.SSLError):
        return context


def _default_transport(
    url: str, headers: dict[str, str], body: bytearray, timeout: float
) -> bytes:
    request = Request(
        url,
        data=bytes(body),
        headers=headers,
        method="POST",
    )
    opener = build_opener(
        _NoRedirectHandler,
        ProxyHandler({}),
        HTTPSHandler(context=_tls_context()),
    )
    with opener.open(request, timeout=timeout) as response:  # noqa: S310
        if response.status != 200:
            raise CloudOcrError("OCR_UNAVAILABLE")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_CLOUD_OCR_RESPONSE_BYTES:
                raise CloudOcrError("OCR_UNAVAILABLE")
            chunks.append(chunk)
    return b"".join(chunks)


def _largest_page_jpeg(reader: PdfReader, page: object) -> bytes | None:
    """Return the page's largest DCTDecode image without decoding it.

    ``DCTDecode`` streams are JPEG bytes verbatim, so no imaging dependency is
    required.  Every other filter (Flate bitmaps, CCITT, JPX) is deliberately
    unsupported: those pages fail closed with ``OCR_UNAVAILABLE`` instead of
    silently degrading.
    """
    try:
        resources = page["/Resources"]
        if resources is None:
            return None
        xobjects = resources.get("/XObject")
        if xobjects is None:
            return None
        best: bytes | None = None
        best_area = 0
        for reference in xobjects.values():
            image = (
                reference.get_object()
                if hasattr(reference, "get_object")
                else reference
            )
            try:
                if image.get("/Subtype") != "/Image":
                    continue
                filters = image.get("/Filter")
                if isinstance(filters, list):
                    normalized = tuple(
                        str(item) for item in filters
                    )
                else:
                    normalized = (str(filters),)
                if normalized != ("/DCTDecode",):
                    continue
                width = int(image.get("/Width", 0))
                height = int(image.get("/Height", 0))
                if width <= 0 or height <= 0:
                    continue
                data = image.get_data()
            except Exception:
                continue
            if not isinstance(data, bytes) or not 1 <= len(data) <= _MAX_PAGE_IMAGE_BYTES:
                continue
            area = width * height
            if area > best_area:
                best_area = area
                best = data
        return best
    except Exception:
        return None


def _chat_response_text(raw: bytes, dialect: str = "chat") -> str:
    try:
        value = json.loads(raw.decode("utf-8", "strict"))
        if dialect == "anthropic":
            blocks = value["content"]
            if not isinstance(blocks, list) or not blocks:
                raise ValueError("content")
            text = "\n".join(
                block["text"]
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            )
            if not isinstance(text, str):
                raise ValueError("text")
        else:
            choices = value["choices"]
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError("choices")
            message = choices[0]["message"]
            content = message["content"]
            if not isinstance(content, str):
                raise ValueError("content")
            text = content
    except Exception:
        raise CloudOcrError("OCR_UNAVAILABLE") from None
    return _normalize_embedded_text(text)


def _dialect_endpoint(dialect: str) -> tuple[str, dict[str, str]]:
    if dialect == "anthropic":
        return "/v1/messages", {"anthropic-version": "2023-06-01"}
    return "/chat/completions", {}


def _build_page_request(
    *, dialect: str, model: str, image: bytes
) -> bytearray:
    """Build the bounded per-page request body for the configured dialect."""

    encoded = base64.b64encode(image).decode("ascii")
    if dialect == "anthropic":
        payload = bytearray(
            json.dumps(
                {
                    "model": model,
                    "max_tokens": 4096,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/jpeg",
                                        "data": encoded,
                                    },
                                },
                                {"type": "text", "text": _CLOUD_OCR_PROMPT},
                            ],
                        }
                    ],
                },
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("ascii")
        )
        return payload
    payload = bytearray(
        json.dumps(
            {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": "data:image/jpeg;base64," + encoded
                                },
                            },
                            {"type": "text", "text": _CLOUD_OCR_PROMPT},
                        ],
                    }
                ],
            },
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
    )
    return payload


def _source_unit_id(
    source_sha256: str, page_number: int, backend: str
) -> str:
    return hashlib.sha256(
        f"{source_sha256}:{page_number}:{backend}".encode("ascii")
    ).hexdigest()


def cloud_ocr_pdf_pages(
    source: bytes | bytearray | memoryview | str | os.PathLike[str],
    *,
    page_numbers: Iterable[int],
    expected_sha256: str | None = None,
    config: CloudOcrConfig | None = None,
    transport: CLOUD_OCR_TRANSPORT | None = None,
    completed_page_callback: Callable[[OcrPageResult], None] | None = None,
) -> tuple[OcrPageResult, ...]:
    """OCR selected PDF pages through the explicitly configured cloud engine.

    Mirrors :func:`material_intake.ocr.ocr_pdf_pages`: deterministic page
    ordering, bounded source handling, per-page fail-closed fallbacks, and the
    durable checkpoint callback only for successfully applied pages.
    """
    active = config or CloudOcrConfig.from_environment()
    active_transport = transport or _default_transport
    try:
        requested = tuple(sorted(set(page_numbers)))
    except (TypeError, ValueError):
        raise CloudOcrError("OCR_PAGE_INVALID") from None
    if any(
        type(page) is not int or not 1 <= page <= MAX_PDF_PAGES
        for page in requested
    ):
        raise CloudOcrError("OCR_PAGE_INVALID")
    if not requested:
        return ()
    capability = cloud_ocr_capability(active)
    if capability.state != "ready":
        return tuple(
            _cloud_fallback_result(
                page,
                state="unavailable"
                if capability.state == "unavailable"
                else "disabled",
                reason_code=capability.reason_code or "OCR_UNAVAILABLE",
            )
            for page in requested
        )

    body = _read_source(source)
    source_sha256 = hashlib.sha256(body).hexdigest()
    if expected_sha256 is not None and source_sha256 != expected_sha256:
        raise CloudOcrError("OCR_SOURCE_IDENTITY_MISMATCH")
    try:
        reader = PdfReader(io.BytesIO(body), strict=True)
    except Exception:
        raise CloudOcrError("OCR_SOURCE_INVALID") from None
    if reader.is_encrypted:
        raise CloudOcrError("OCR_SOURCE_ENCRYPTED")
    page_count = len(reader.pages)
    if not 1 <= page_count <= MAX_PDF_PAGES or any(
        page > page_count for page in requested
    ):
        raise CloudOcrError("OCR_PAGE_INVALID")

    try:
        api_key = _read_api_key(active.api_key_file)  # type: ignore[arg-type]
    except CloudOcrError:
        return tuple(
            _cloud_fallback_result(
                page, state="unavailable", reason_code="OCR_UNAVAILABLE"
            )
            for page in requested
        )

    path_suffix, extra_headers = _dialect_endpoint(active.dialect)
    url = active.base_url.rstrip("/") + path_suffix
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        **extra_headers,
    }
    document_deadline = time.monotonic() + active.total_timeout_seconds
    results: list[OcrPageResult] = []
    for index, page_number in enumerate(requested):
        if index >= active.max_pages:
            results.append(
                OcrPageResult(
                    page_number=page_number,
                    text="",
                    status="page_limit",
                    reason_code="OCR_PAGE_LIMIT",
                    ocr_applied=False,
                    character_count=0,
                    parser_backend=CLOUD_OCR_PARSER_BACKEND,
                )
            )
            continue
        remaining = document_deadline - time.monotonic()
        if remaining <= 0:
            results.append(
                _cloud_fallback_result(
                    page_number,
                    state="unavailable",
                    reason_code="OCR_UNAVAILABLE",
                )
            )
            continue
        image = _largest_page_jpeg(reader, reader.pages[page_number - 1])
        result: OcrPageResult | None = None
        if image is not None:
            payload = _build_page_request(
                dialect=active.dialect, model=active.model, image=image
            )
            try:
                raw = active_transport(
                    url,
                    headers,
                    payload,
                    min(
                        remaining,
                        active.request_timeout_seconds,
                    ),
                )
                if not 1 <= len(raw) <= MAX_CLOUD_OCR_RESPONSE_BYTES:
                    raise CloudOcrError("OCR_UNAVAILABLE")
                text = _chat_response_text(raw, active.dialect)
            except (CloudOcrError, HTTPError, URLError, OSError, ValueError):
                result = None
            else:
                characters = sum(
                    not character.isspace() for character in text
                )
                if characters > MAX_OCR_PAGE_TEXT_CHARACTERS:
                    result = None
                elif characters < 40:
                    result = OcrPageResult(
                        page_number=page_number,
                        text=text,
                        status="insufficient_text",
                        reason_code="OCR_OUTPUT_INSUFFICIENT",
                        ocr_applied=False,
                        character_count=characters,
                        parser_backend=CLOUD_OCR_PARSER_BACKEND,
                    )
                else:
                    result = OcrPageResult(
                        page_number=page_number,
                        text=text,
                        status="applied",
                        reason_code="OCR_APPLIED",
                        ocr_applied=True,
                        character_count=characters,
                        parser_backend=CLOUD_OCR_PARSER_BACKEND,
                        source_unit_id=_source_unit_id(
                            source_sha256, page_number, CLOUD_OCR_PARSER_BACKEND
                        ),
                    )
            finally:
                payload[:] = b"\0" * len(payload)
                payload.clear()
        if result is None:
            result = _cloud_fallback_result(
                page_number,
                state="unavailable",
                reason_code="OCR_UNAVAILABLE",
            )
        results.append(result)
        if result.ocr_applied and completed_page_callback is not None:
            # Mirrors the FIFO contract: a failed durable checkpoint write must
            # stop analysis rather than pretend the page can be resumed.
            completed_page_callback(result)
    return tuple(results)


@dataclass(frozen=True, slots=True)
class OcrEngine:
    """The single OCR engine selected for this process at call time."""

    backend: str
    pages: Callable[..., tuple[OcrPageResult, ...]]


def resolve_ocr_engine() -> OcrEngine:
    """Pick the OCR engine without ever mixing providers.

    The audited local FIFO runtime wins whenever it is enabled.  The cloud
    adapter is used only when the FIFO is disabled and the cloud provider is
    explicitly configured; a not-ready cloud capability still selects the
    cloud engine, whose page results fail closed with ``OCR_UNAVAILABLE``.
    """
    cloud = CloudOcrConfig.from_environment()
    local = LocalOcrConfig.from_environment()
    if local.enabled or not cloud.enabled:
        return OcrEngine(
            backend=OCR_PARSER_BACKEND, pages=ocr_pdf_pages
        )
    return OcrEngine(
        backend=CLOUD_OCR_PARSER_BACKEND, pages=cloud_ocr_pdf_pages
    )


__all__ = (
    "CLOUD_OCR_PARSER_BACKEND",
    "CLOUD_OCR_PROVIDERS",
    "CloudOcrCapability",
    "CloudOcrConfig",
    "CloudOcrError",
    "OcrEngine",
    "cloud_ocr_capability",
    "cloud_ocr_pdf_pages",
    "resolve_ocr_engine",
)
