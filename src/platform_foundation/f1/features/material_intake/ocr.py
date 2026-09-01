"""Optional, bounded client for the existing offline F0-H OCR FIFO.

The adapter is deliberately opt-in.  It never falls back to an in-process or
network OCR provider: when the private FIFO runtime is disabled, absent, busy,
or returns a result that fails the frozen F0-H contract, callers receive an
``OCR_UNAVAILABLE`` result and must retain ``OCR_REQUIRED``.

OCR plaintext exists only in returned in-memory values.  Callers may persist it
only through the authenticated encrypted checkpoint envelope; this module never
writes a body itself.  Request envelopes and raw responses are zeroed before
return, and no body, filename, path, or subprocess/runtime error is logged.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import io
import json
import math
import os
import re
import select
import stat
import threading
import time
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable, Literal

from pypdf import PdfReader

from ....f0h.contracts import F0HError, canonical_json_bytes, validate_private_result
from ..p3.contracts import MAX_PDF_PAGES


MAX_OCR_SOURCE_BYTES = 50 * 1024 * 1024
MAX_OCR_ENVELOPE_BYTES = 64 * 1024 * 1024
MAX_OCR_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_OCR_PAGE_TEXT_CHARACTERS = 100_000
MAX_OCR_CHECKPOINT_TEXT_BYTES = 400_000
# P3 accepts PDFs with as many as 128 pages.  OCR remains bounded by the
# document deadline, but the page selector must not turn otherwise-valid long
# scanned PDFs into a permanent configuration dead end.
MAX_OCR_PAGES_PER_DOCUMENT = MAX_PDF_PAGES
DEFAULT_OCR_PAGES_PER_DOCUMENT = MAX_PDF_PAGES
DEFAULT_OCR_REQUEST_TIMEOUT_SECONDS = 140.0
DEFAULT_OCR_TOTAL_TIMEOUT_SECONDS = 300.0
_MAX_OCR_TOTAL_TIMEOUT_SECONDS = 600.0
_HEADER_LIMIT = 4_096
_TRUE_VALUES = frozenset(("1", "true", "yes", "on"))
_FALSE_VALUES = frozenset(("", "0", "false", "no", "off"))
_SAFE_COORDINATE_RE = re.compile(r"^-?[0-9]{1,9}\.[0-9]{3}$")
_OCR_CALL_LOCK = threading.Lock()
OCR_PARSER_BACKEND = "f0h-ppocrv6-3.9.2"
# Closed set of checkpoint parser-backend identities.  The private FIFO
# runtime stays the audited default; the Ark vision cloud adapter is the
# only additional member and requires its own explicit opt-in.
OCR_PARSER_BACKENDS = frozenset(
    {"f0h-ppocrv6-3.9.2", "cloud-vision-chat-1"}
)

RETRYABLE_OCR_REASON_CODES = frozenset(
    {
        "OCR_DISABLED",
        "OCR_UNAVAILABLE",
        "OCR_PAGE_LIMIT",
        "OCR_OUTPUT_INSUFFICIENT",
        "OCR_REQUIRED",
    }
)

# Exact identity required by ``validate_private_result`` and by the immutable
# F0-H runtime currently mounted behind ``infra/f1/material-rag/ocr_server.py``.
_F0H_BUNDLE = SimpleNamespace(
    execution_profile_sha256=(
        "9c320d4d978fe2d0d5a69f6411b58744a203600909d1171a49aed700de8a4440"
    ),
    configuration_sha256=(
        "30b615e7c21b144d12434df5cd1d867317eeef341b80ef3adadef49a5bec626f"
    ),
    model_bundle_sha256=(
        "eb97addf62fa9cb149229a9c061e8d91fda3c8976c1a9a894b2c88ae65ba888f"
    ),
    rapidocr_version="3.9.2",
    ocr_family="PP-OCRv6",
    onnxruntime_version="1.28.0",
)

OcrCapabilityState = Literal["disabled", "unavailable", "ready"]
OcrPageStatus = Literal[
    "disabled",
    "unavailable",
    "page_limit",
    "insufficient_text",
    "applied",
]
PdfTextSource = Literal["pypdf", "f0h", "none"]


class LocalOcrError(RuntimeError):
    """A fixed-code error which never includes source or OCR body content."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class LocalOcrConfig:
    enabled: bool = False
    request_fifo: Path = Path("/run/material-rag-ocr/request.fifo")
    response_fifo: Path = Path("/run/material-rag-ocr/response.fifo")
    ready_file: Path = Path("/run/material-rag-ocr/ready")
    max_pages: int = DEFAULT_OCR_PAGES_PER_DOCUMENT
    request_timeout_seconds: float = DEFAULT_OCR_REQUEST_TIMEOUT_SECONDS
    total_timeout_seconds: float = DEFAULT_OCR_TOTAL_TIMEOUT_SECONDS
    configuration_valid: bool = True

    def __post_init__(self) -> None:
        paths = (self.request_fifo, self.response_fifo, self.ready_file)
        if (
            any(not isinstance(path, Path) or not path.is_absolute() for path in paths)
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
            raise ValueError("MATERIAL_OCR_CONFIGURATION_INVALID")

    @classmethod
    def from_environment(cls) -> "LocalOcrConfig":
        raw_enabled = os.environ.get("F1_MATERIAL_OCR_ENABLED", "").strip().lower()
        configuration_valid = raw_enabled in _TRUE_VALUES | _FALSE_VALUES
        enabled = raw_enabled in _TRUE_VALUES

        request = _environment_path(
            "F1_MATERIAL_OCR_REQUEST_FIFO",
            "F1_MATERIAL_RAG_OCR_REQUEST_FIFO",
            "/run/material-rag-ocr/request.fifo",
        )
        response = _environment_path(
            "F1_MATERIAL_OCR_RESPONSE_FIFO",
            "F1_MATERIAL_RAG_OCR_RESPONSE_FIFO",
            "/run/material-rag-ocr/response.fifo",
        )
        ready = _environment_path(
            "F1_MATERIAL_OCR_READY_FILE",
            "F1_MATERIAL_RAG_OCR_READY_FILE",
            "/run/material-rag-ocr/ready",
        )
        max_pages, max_pages_valid = _bounded_environment_int(
            "F1_MATERIAL_OCR_MAX_PAGES",
            DEFAULT_OCR_PAGES_PER_DOCUMENT,
            minimum=1,
            maximum=MAX_OCR_PAGES_PER_DOCUMENT,
        )
        request_timeout, request_timeout_valid = _bounded_environment_float(
            "F1_MATERIAL_OCR_REQUEST_TIMEOUT_SECONDS",
            DEFAULT_OCR_REQUEST_TIMEOUT_SECONDS,
            minimum=1.0,
            maximum=DEFAULT_OCR_REQUEST_TIMEOUT_SECONDS,
        )
        total_timeout, total_timeout_valid = _bounded_environment_float(
            "F1_MATERIAL_OCR_TOTAL_TIMEOUT_SECONDS",
            DEFAULT_OCR_TOTAL_TIMEOUT_SECONDS,
            minimum=request_timeout,
            maximum=_MAX_OCR_TOTAL_TIMEOUT_SECONDS,
        )
        configuration_valid = configuration_valid and all(
            (
                request is not None,
                response is not None,
                ready is not None,
                max_pages_valid,
                request_timeout_valid,
                total_timeout_valid,
            )
        )
        return cls(
            enabled=enabled,
            request_fifo=request or Path("/invalid/material-ocr-request"),
            response_fifo=response or Path("/invalid/material-ocr-response"),
            ready_file=ready or Path("/invalid/material-ocr-ready"),
            max_pages=max_pages,
            request_timeout_seconds=request_timeout,
            total_timeout_seconds=total_timeout,
            configuration_valid=configuration_valid,
        )


@dataclass(frozen=True, slots=True)
class LocalOcrCapability:
    state: OcrCapabilityState
    reason_code: str | None
    backend: str = "f0h-ppocrv6-3.9.2"


@dataclass(frozen=True, slots=True, repr=False)
class OcrPageResult:
    """One private OCR page result.

    ``repr=False`` reduces accidental body disclosure in exception or debug
    output.  Consumers must still treat ``text`` as customer-confidential.
    """

    page_number: int
    text: str
    status: OcrPageStatus
    reason_code: str
    ocr_applied: bool
    character_count: int
    confidence_mean_ppm: int | None = None
    table_candidate: bool = False
    two_column_candidate: bool = False
    source_unit_id: str | None = None
    parser_backend: str = "f0h-ppocrv6-3.9.2"


@dataclass(frozen=True, slots=True, repr=False)
class PdfPageTextResult:
    """Effective page text with explicit pypdf/OCR provenance."""

    page_number: int
    text: str
    text_source: PdfTextSource
    embedded_character_count: int
    ocr_applied: bool
    ocr_required: bool
    ocr_status: OcrPageStatus | Literal["not_required"]
    reason_codes: tuple[str, ...]
    confidence_mean_ppm: int | None = None
    table_candidate: bool = False
    two_column_candidate: bool = False
    source_unit_id: str | None = None
    parser_backend: str = "pypdf-6.14.2"


def ocr_checkpoint_aad(
    *,
    enterprise_id: uuid.UUID,
    document_version_id: uuid.UUID,
    source_sha256: str,
    expected_page_count: int,
    page_number: int,
    parser_backend: str,
    source_unit_id: str,
    body_sha256: str,
    character_count: int,
    confidence_mean_ppm: int | None,
    table_candidate: bool,
    two_column_candidate: bool,
) -> bytes:
    """Bind one encrypted page body to its complete OCR source identity."""

    if (
        not isinstance(enterprise_id, uuid.UUID)
        or not isinstance(document_version_id, uuid.UUID)
        or re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None
        or not 1 <= expected_page_count <= MAX_PDF_PAGES
        or not 1 <= page_number <= expected_page_count
        or parser_backend not in OCR_PARSER_BACKENDS
        or re.fullmatch(r"[0-9a-f]{64}", source_unit_id) is None
        or re.fullmatch(r"[0-9a-f]{64}", body_sha256) is None
        or type(character_count) is not int
        or not 40 <= character_count <= MAX_OCR_PAGE_TEXT_CHARACTERS
        or (
            confidence_mean_ppm is not None
            and (
                type(confidence_mean_ppm) is not int
                or not 0 <= confidence_mean_ppm <= 1_000_000
            )
        )
        or type(table_candidate) is not bool
        or type(two_column_candidate) is not bool
    ):
        raise LocalOcrError("OCR_CHECKPOINT_IDENTITY_INVALID")
    return (
        "f1.material-ocr.checkpoint.v1\x00"
        f"{enterprise_id}\x00{document_version_id}\x00{source_sha256}\x00"
        f"{expected_page_count}\x00{page_number}\x00{parser_backend}\x00"
        f"{source_unit_id}\x00{body_sha256}\x00{character_count}\x00"
        f"{confidence_mean_ppm if confidence_mean_ppm is not None else '-'}\x00"
        f"{int(table_candidate)}\x00{int(two_column_candidate)}"
    ).encode("ascii")


def _environment_path(primary: str, legacy: str, default: str) -> Path | None:
    value = os.environ.get(primary) or os.environ.get(legacy) or default
    try:
        path = Path(value)
    except (TypeError, ValueError):
        return None
    if not path.is_absolute() or "\x00" in str(path):
        return None
    return path


def _bounded_environment_int(
    name: str, default: int, *, minimum: int, maximum: int
) -> tuple[int, bool]:
    raw = os.environ.get(name)
    if raw is None:
        return default, True
    try:
        value = int(raw, 10)
    except (TypeError, ValueError):
        return default, False
    if not minimum <= value <= maximum:
        return default, False
    return value, True


def _bounded_environment_float(
    name: str, default: float, *, minimum: float, maximum: float
) -> tuple[float, bool]:
    raw = os.environ.get(name)
    if raw is None:
        return default, True
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default, False
    if not math.isfinite(value) or not minimum <= value <= maximum:
        return default, False
    return value, True


def local_ocr_capability(
    config: LocalOcrConfig | None = None,
) -> LocalOcrCapability:
    active = config or LocalOcrConfig.from_environment()
    if not active.configuration_valid:
        return LocalOcrCapability("unavailable", "OCR_UNAVAILABLE")
    if not active.enabled:
        return LocalOcrCapability("disabled", "OCR_DISABLED")
    try:
        paths = (active.request_fifo, active.response_fifo, active.ready_file)
        if len({path.parent for path in paths}) != 1:
            raise OSError
        directory = paths[0].parent.lstat()
        request = paths[0].lstat()
        response = paths[1].lstat()
        ready = paths[2].lstat()
        owners = {directory.st_uid, request.st_uid, response.st_uid, ready.st_uid}
        if (
            not stat.S_ISDIR(directory.st_mode)
            or stat.S_ISLNK(directory.st_mode)
            or stat.S_IMODE(directory.st_mode) != 0o700
            or len(owners) != 1
            or not stat.S_ISFIFO(request.st_mode)
            or not stat.S_ISFIFO(response.st_mode)
            or not stat.S_ISREG(ready.st_mode)
            or any(item.st_nlink != 1 for item in (request, response, ready))
            or any(
                stat.S_IMODE(item.st_mode) != 0o600
                for item in (request, response, ready)
            )
        ):
            raise OSError
    except (OSError, ValueError):
        return LocalOcrCapability("unavailable", "OCR_UNAVAILABLE")
    return LocalOcrCapability("ready", None)


def _read_source(
    source: bytes | bytearray | memoryview | str | os.PathLike[str],
) -> bytes:
    if isinstance(source, (bytes, bytearray, memoryview)):
        body = bytes(source)
        if not 8 <= len(body) <= MAX_OCR_SOURCE_BYTES:
            raise LocalOcrError("OCR_SOURCE_LIMIT")
        return body
    path = Path(source)
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 8 <= before.st_size <= MAX_OCR_SOURCE_BYTES
        ):
            raise OSError
        output = bytearray()
        try:
            while len(output) <= MAX_OCR_SOURCE_BYTES:
                chunk = os.read(
                    descriptor,
                    min(1024 * 1024, MAX_OCR_SOURCE_BYTES + 1 - len(output)),
                )
                if not chunk:
                    break
                output.extend(chunk)
            after = os.fstat(descriptor)
            if (
                len(output) > MAX_OCR_SOURCE_BYTES
                or before.st_dev != after.st_dev
                or before.st_ino != after.st_ino
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
            ):
                raise OSError
            return bytes(output)
        finally:
            output[:] = b"\0" * len(output)
            output.clear()
    except (OSError, TypeError, ValueError):
        raise LocalOcrError("OCR_SOURCE_INVALID") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _coordinate(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise LocalOcrError("OCR_PAGE_GEOMETRY_INVALID") from None
    rendered = f"{number:.3f}"
    if (
        not math.isfinite(number)
        or abs(number) > 1_000_000
        or _SAFE_COORDINATE_RE.fullmatch(rendered) is None
    ):
        raise LocalOcrError("OCR_PAGE_GEOMETRY_INVALID")
    return rendered


def _box(page_box: object) -> dict[str, str]:
    try:
        values = (
            _coordinate(page_box.left),
            _coordinate(page_box.bottom),
            _coordinate(page_box.right),
            _coordinate(page_box.top),
        )
    except AttributeError:
        raise LocalOcrError("OCR_PAGE_GEOMETRY_INVALID") from None
    if Decimal(values[0]) >= Decimal(values[2]) or Decimal(values[1]) >= Decimal(
        values[3]
    ):
        raise LocalOcrError("OCR_PAGE_GEOMETRY_INVALID")
    return dict(zip(("left", "bottom", "right", "top"), values, strict=True))


def _page_header(
    *,
    page: object,
    page_number: int,
    page_count: int,
    source_sha256: str,
    source_size: int,
) -> dict[str, object]:
    media_box = _box(page.mediabox)
    crop_box = _box(page.cropbox)
    try:
        rotation = int(page.get("/Rotate", 0) or 0) % 360
    except (AttributeError, TypeError, ValueError):
        raise LocalOcrError("OCR_PAGE_GEOMETRY_INVALID") from None
    if rotation not in {0, 90, 180, 270}:
        raise LocalOcrError("OCR_PAGE_GEOMETRY_INVALID")
    source_unit_material = {
        "schema": "material-ocr-source-unit-v1",
        "source_sha256": source_sha256,
        "page_no": page_number,
        "media_box": media_box,
        "crop_box": crop_box,
        "rotation_degrees": rotation,
    }
    source_unit_id = hashlib.sha256(
        canonical_json_bytes(source_unit_material)
    ).hexdigest()
    return {
        "schema": "f0e-envelope-v1",
        "document_type": "PDF",
        "source_sha256": source_sha256,
        "source_size": source_size,
        "expected_total_pages": page_count,
        "page_no": page_number,
        "source_unit_id": source_unit_id,
        "media_box": media_box,
        "crop_box": crop_box,
        "rotation_degrees": rotation,
    }


def _open_fifo_writer(path: Path, deadline: float) -> int:
    while time.monotonic() < deadline:
        descriptor = -1
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY
                | os.O_NONBLOCK
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            info = os.fstat(descriptor)
            if (
                not stat.S_ISFIFO(info.st_mode)
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                os.close(descriptor)
                break
            return descriptor
        except OSError as error:
            if descriptor >= 0:
                os.close(descriptor)
            if error.errno not in {errno.ENXIO, errno.EAGAIN, errno.EINTR}:
                break
            time.sleep(0.02)
    raise LocalOcrError("OCR_UNAVAILABLE")


def _acquire_interprocess_lock(path: Path, deadline: float) -> int:
    """Exclusively lock the existing private ready file without modifying it."""

    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise LocalOcrError("OCR_UNAVAILABLE")
        while time.monotonic() < deadline:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked_descriptor = descriptor
                descriptor = -1
                return locked_descriptor
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EINTR}:
                    raise LocalOcrError("OCR_UNAVAILABLE") from None
                time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
        raise LocalOcrError("OCR_UNAVAILABLE")
    except (OSError, ValueError):
        raise LocalOcrError("OCR_UNAVAILABLE") from None
    finally:
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(descriptor)


def _release_interprocess_lock(descriptor: int) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _write_nonblocking(descriptor: int, body: bytearray, deadline: float) -> None:
    view = memoryview(body)
    offset = 0
    try:
        while offset < len(view):
            if time.monotonic() >= deadline:
                raise LocalOcrError("OCR_UNAVAILABLE")
            _readable, writable, _errors = select.select(
                [], [descriptor], [], min(0.25, max(0.0, deadline - time.monotonic()))
            )
            if not writable:
                continue
            try:
                written = os.write(descriptor, view[offset : offset + 65_536])
            except BlockingIOError:
                continue
            if written < 1:
                raise LocalOcrError("OCR_UNAVAILABLE")
            offset += written
    finally:
        view.release()


def _read_nonblocking(descriptor: int, size: int, deadline: float) -> bytearray:
    if not 0 <= size <= MAX_OCR_RESPONSE_BYTES:
        raise LocalOcrError("OCR_UNAVAILABLE")
    output = bytearray()
    try:
        while len(output) < size:
            if time.monotonic() >= deadline:
                raise LocalOcrError("OCR_UNAVAILABLE")
            readable, _writable, _errors = select.select(
                [descriptor],
                [],
                [],
                min(0.25, max(0.0, deadline - time.monotonic())),
            )
            if not readable:
                continue
            try:
                chunk = os.read(descriptor, min(65_536, size - len(output)))
            except BlockingIOError:
                continue
            if not chunk:
                time.sleep(0.01)
                continue
            output.extend(chunk)
        return output
    except (LocalOcrError, OSError):
        output[:] = b"\0" * len(output)
        output.clear()
        raise


def _layout_hints(
    blocks: list[object], *, width: float, height: float, text: str
) -> tuple[bool, bool]:
    fragments: list[tuple[float, float, str]] = []
    for block in blocks:
        try:
            item = dict(block)  # type: ignore[arg-type]
            points = list(item["bbox"])
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
            fragments.append(
                (
                    (min(xs) + max(xs)) / 2.0,
                    (min(ys) + max(ys)) / 2.0,
                    str(item["text"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            raise LocalOcrError("OCR_UNAVAILABLE") from None
    rows: dict[int, list[float]] = {}
    row_height = max(4.0, height / 132.0)
    for x, y, value in fragments:
        if value.strip():
            rows.setdefault(int(round(y / row_height)), []).append(x)
    grid_rows = 0
    for xs in rows.values():
        separated: list[float] = []
        for value in sorted(xs):
            if not separated or value - separated[-1] >= width * 0.08:
                separated.append(value)
        if len(separated) >= 3:
            grid_rows += 1
    table = grid_rows >= 3 or len(re.findall(r"\S+\s{2,}\S+", text)) >= 3
    left_y = [y for x, y, value in fragments if x < width * 0.45 and len(value) >= 2]
    right_y = [y for x, y, value in fragments if x > width * 0.55 and len(value) >= 2]
    overlap = 0.0
    if left_y and right_y:
        overlap = max(
            0.0,
            min(max(left_y), max(right_y)) - max(min(left_y), min(right_y)),
        ) / height
    columns = len(left_y) >= 5 and len(right_y) >= 5 and overlap >= 0.2
    return table, columns


def _normalize_ocr_text(blocks: list[object]) -> str:
    values: list[str] = []
    for block in blocks:
        try:
            value = str(dict(block)["text"])  # type: ignore[arg-type]
        except (KeyError, TypeError, ValueError):
            raise LocalOcrError("OCR_UNAVAILABLE") from None
        value = unicodedata.normalize(
            "NFC", value.replace("\r\n", "\n").replace("\r", "\n")
        ).strip()
        if value:
            values.append(value)
    output = "\n".join(values).strip()
    if len(output) > MAX_OCR_PAGE_TEXT_CHARACTERS:
        raise LocalOcrError("OCR_OUTPUT_LIMIT")
    return output


def _normalize_embedded_text(value: str) -> str:
    normalized = unicodedata.normalize(
        "NFC", value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    )
    lines = [
        re.sub(r"[\t\f\v ]+", " ", line).strip()
        for line in normalized.splitlines()
    ]
    output = "\n".join(line for line in lines if line)
    if len(output) > MAX_OCR_PAGE_TEXT_CHARACTERS:
        raise LocalOcrError("PDF_TEXT_PAGE_LIMIT")
    return output


def _request_page(
    body: bytes,
    header: dict[str, object],
    *,
    config: LocalOcrConfig,
    deadline: float,
) -> OcrPageResult:
    page_number = int(header["page_no"])
    source_unit_id = str(header["source_unit_id"])
    header_body = canonical_json_bytes(header)
    if not 1 <= len(header_body) <= _HEADER_LIMIT:
        raise LocalOcrError("OCR_UNAVAILABLE")
    envelope = bytearray()
    framed = bytearray()
    raw = bytearray()
    try:
        envelope.extend(len(header_body).to_bytes(4, "big"))
        envelope.extend(header_body)
        envelope.extend(body)
        if len(envelope) > MAX_OCR_ENVELOPE_BYTES:
            raise LocalOcrError("OCR_SOURCE_LIMIT")
        framed.extend(len(envelope).to_bytes(4, "big"))
        framed.extend(envelope)
        request_descriptor = _open_fifo_writer(config.request_fifo, deadline)
        response_descriptor = -1
        try:
            response_descriptor = os.open(
                config.response_fifo,
                os.O_RDONLY
                | os.O_NONBLOCK
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            response_info = os.fstat(response_descriptor)
            if (
                not stat.S_ISFIFO(response_info.st_mode)
                or response_info.st_nlink != 1
                or stat.S_IMODE(response_info.st_mode) != 0o600
            ):
                raise LocalOcrError("OCR_UNAVAILABLE")
            _write_nonblocking(request_descriptor, framed, deadline)
            os.close(request_descriptor)
            request_descriptor = -1
            size_body = _read_nonblocking(response_descriptor, 4, deadline)
            size = int.from_bytes(size_body, "big")
            size_body[:] = b"\0" * len(size_body)
            if not 1 <= size <= MAX_OCR_RESPONSE_BYTES:
                raise LocalOcrError("OCR_UNAVAILABLE")
            raw = _read_nonblocking(response_descriptor, size, deadline)
        except OSError:
            raise LocalOcrError("OCR_UNAVAILABLE") from None
        finally:
            if request_descriptor >= 0:
                os.close(request_descriptor)
            if response_descriptor >= 0:
                os.close(response_descriptor)
        try:
            value = json.loads(bytes(raw).decode("ascii", "strict"))
            if canonical_json_bytes(value) != bytes(raw):
                raise F0HError("RUNNER_OUTPUT_INVALID")
            validated = validate_private_result(
                value,
                _F0H_BUNDLE,
                expected={
                    "document_type": "PDF",
                    "source_sha256": header["source_sha256"],
                    "page_no": page_number,
                    "expected_total_pages": header["expected_total_pages"],
                    "source_unit_id": source_unit_id,
                    "external_calls": 0,
                    "external_processing": "DENY",
                },
            )
            blocks = validated["blocks"]
            if not isinstance(blocks, list):
                raise F0HError("RUNNER_OUTPUT_INVALID")
            text = _normalize_ocr_text(blocks)
            characters = sum(not character.isspace() for character in text)
            if characters < 40:
                return OcrPageResult(
                    page_number=page_number,
                    text=text,
                    status="insufficient_text",
                    reason_code="OCR_OUTPUT_INSUFFICIENT",
                    ocr_applied=False,
                    character_count=characters,
                    source_unit_id=source_unit_id,
                )
            width = float(validated["render_width_px"])
            height = float(validated["render_height_px"])
            table, columns = _layout_hints(
                blocks, width=width, height=height, text=text
            )
            confidence = validated.get("confidence_mean_ppm")
            return OcrPageResult(
                page_number=page_number,
                text=text,
                status="applied",
                reason_code="OCR_APPLIED",
                ocr_applied=True,
                character_count=characters,
                confidence_mean_ppm=(
                    int(confidence) if isinstance(confidence, int) else None
                ),
                table_candidate=table,
                two_column_candidate=columns,
                source_unit_id=source_unit_id,
            )
        except (
            F0HError,
            KeyError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            raise LocalOcrError("OCR_UNAVAILABLE") from None
    finally:
        envelope[:] = b"\0" * len(envelope)
        envelope.clear()
        framed[:] = b"\0" * len(framed)
        framed.clear()
        raw[:] = b"\0" * len(raw)
        raw.clear()


def _fallback_result(
    page_number: int, *, state: OcrCapabilityState, reason_code: str
) -> OcrPageResult:
    return OcrPageResult(
        page_number=page_number,
        text="",
        status="disabled" if state == "disabled" else "unavailable",
        reason_code=reason_code,
        ocr_applied=False,
        character_count=0,
    )


def ocr_pdf_pages(
    source: bytes | bytearray | memoryview | str | os.PathLike[str],
    *,
    page_numbers: Iterable[int],
    expected_sha256: str | None = None,
    config: LocalOcrConfig | None = None,
    completed_page_callback: Callable[[OcrPageResult], None] | None = None,
) -> tuple[OcrPageResult, ...]:
    """OCR selected PDF pages through the private F0-H FIFO.

    The function is deterministic and side-effect-free outside the bounded
    private FIFO exchange.  It accepts bytes or a securely re-read regular
    file path and returns one ordered result per unique requested page.
    """

    active = config or LocalOcrConfig.from_environment()
    try:
        requested = tuple(sorted(set(page_numbers)))
    except (TypeError, ValueError):
        raise LocalOcrError("OCR_PAGE_INVALID") from None
    if any(
        type(page) is not int or not 1 <= page <= MAX_PDF_PAGES
        for page in requested
    ):
        raise LocalOcrError("OCR_PAGE_INVALID")
    if not requested:
        return ()
    capability = local_ocr_capability(active)
    if capability.state != "ready":
        return tuple(
            _fallback_result(
                page,
                state=capability.state,
                reason_code=capability.reason_code or "OCR_UNAVAILABLE",
            )
            for page in requested
        )

    body = _read_source(source)
    source_sha256 = hashlib.sha256(body).hexdigest()
    if expected_sha256 is not None and source_sha256 != expected_sha256:
        raise LocalOcrError("OCR_SOURCE_IDENTITY_MISMATCH")
    try:
        reader = PdfReader(io.BytesIO(body), strict=True)
    except Exception:
        raise LocalOcrError("OCR_SOURCE_INVALID") from None
    if reader.is_encrypted:
        raise LocalOcrError("OCR_SOURCE_ENCRYPTED")
    page_count = len(reader.pages)
    if not 1 <= page_count <= MAX_PDF_PAGES or any(
        page > page_count for page in requested
    ):
        raise LocalOcrError("OCR_PAGE_INVALID")

    results: list[OcrPageResult] = []
    document_deadline = time.monotonic() + active.total_timeout_seconds
    acquired = _OCR_CALL_LOCK.acquire(
        timeout=min(2.0, max(0.0, active.total_timeout_seconds))
    )
    if not acquired:
        return tuple(
            _fallback_result(
                page, state="unavailable", reason_code="OCR_UNAVAILABLE"
            )
            for page in requested
        )
    interprocess_lock = -1
    try:
        try:
            interprocess_lock = _acquire_interprocess_lock(
                active.ready_file, document_deadline
            )
        except LocalOcrError:
            return tuple(
                _fallback_result(
                    page, state="unavailable", reason_code="OCR_UNAVAILABLE"
                )
                for page in requested
            )
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
                    )
                )
                continue
            remaining = document_deadline - time.monotonic()
            if remaining <= 0:
                results.append(
                    _fallback_result(
                        page_number,
                        state="unavailable",
                        reason_code="OCR_UNAVAILABLE",
                    )
                )
                continue
            try:
                header = _page_header(
                    page=reader.pages[page_number - 1],
                    page_number=page_number,
                    page_count=page_count,
                    source_sha256=source_sha256,
                    source_size=len(body),
                )
                result = _request_page(
                    body,
                    header,
                    config=active,
                    deadline=min(
                        document_deadline,
                        time.monotonic() + active.request_timeout_seconds,
                    ),
                )
            except Exception:
                result = _fallback_result(
                    page_number,
                    state="unavailable",
                    reason_code="OCR_UNAVAILABLE",
                )
            results.append(result)
            if result.ocr_applied and completed_page_callback is not None:
                # Keep this outside the OCR exception boundary: a failed
                # durable checkpoint write must stop analysis, not silently
                # pretend that the page can be resumed after a process crash.
                completed_page_callback(result)
    finally:
        if interprocess_lock >= 0:
            _release_interprocess_lock(interprocess_lock)
        _OCR_CALL_LOCK.release()
    return tuple(results)


def extract_pdf_text_pages(
    source: bytes | bytearray | memoryview | str | os.PathLike[str],
    *,
    expected_sha256: str | None = None,
    config: LocalOcrConfig | None = None,
    ocr_threshold_characters: int = 40,
) -> tuple[PdfPageTextResult, ...]:
    """Return bounded effective text for every PDF page.

    Native pypdf text is authoritative and remains the fallback.  Only pages
    below ``ocr_threshold_characters`` are sent to the optional F0-H adapter;
    invalid, insufficient, or unavailable OCR is never marked as applied.
    """

    if not 1 <= ocr_threshold_characters <= 1_000:
        raise LocalOcrError("OCR_CONFIGURATION_INVALID")
    body = _read_source(source)
    source_sha256 = hashlib.sha256(body).hexdigest()
    if expected_sha256 is not None and source_sha256 != expected_sha256:
        raise LocalOcrError("OCR_SOURCE_IDENTITY_MISMATCH")
    try:
        reader = PdfReader(io.BytesIO(body), strict=True)
    except Exception:
        raise LocalOcrError("OCR_SOURCE_INVALID") from None
    if reader.is_encrypted:
        raise LocalOcrError("OCR_SOURCE_ENCRYPTED")
    page_count = len(reader.pages)
    if not 1 <= page_count <= MAX_PDF_PAGES:
        raise LocalOcrError("OCR_PAGE_INVALID")

    embedded_pages: list[tuple[int, str, int]] = []
    try:
        for page_number, page in enumerate(reader.pages, start=1):
            text = _normalize_embedded_text(page.extract_text() or "")
            character_count = sum(not character.isspace() for character in text)
            embedded_pages.append((page_number, text, character_count))
    except LocalOcrError:
        raise
    except Exception:
        raise LocalOcrError("PDF_TEXT_EXTRACTION_FAILED") from None

    targets = tuple(
        page_number
        for page_number, _text, character_count in embedded_pages
        if character_count < ocr_threshold_characters
    )
    ocr_by_page: dict[int, OcrPageResult] = {}
    if targets:
        try:
            ocr_by_page = {
                item.page_number: item
                for item in ocr_pdf_pages(
                    body,
                    page_numbers=targets,
                    expected_sha256=source_sha256,
                    config=config,
                )
            }
        except LocalOcrError:
            ocr_by_page = {}

    results: list[PdfPageTextResult] = []
    for page_number, embedded_text, embedded_count in embedded_pages:
        if embedded_count >= ocr_threshold_characters:
            results.append(
                PdfPageTextResult(
                    page_number=page_number,
                    text=embedded_text,
                    text_source="pypdf" if embedded_text else "none",
                    embedded_character_count=embedded_count,
                    ocr_applied=False,
                    ocr_required=False,
                    ocr_status="not_required",
                    reason_codes=(),
                )
            )
            continue
        ocr = ocr_by_page.get(page_number)
        if ocr is not None and ocr.ocr_applied:
            results.append(
                PdfPageTextResult(
                    page_number=page_number,
                    text=ocr.text,
                    text_source="f0h",
                    embedded_character_count=embedded_count,
                    ocr_applied=True,
                    ocr_required=False,
                    ocr_status=ocr.status,
                    reason_codes=("OCR_APPLIED",),
                    confidence_mean_ppm=ocr.confidence_mean_ppm,
                    table_candidate=ocr.table_candidate,
                    two_column_candidate=ocr.two_column_candidate,
                    source_unit_id=ocr.source_unit_id,
                    parser_backend=ocr.parser_backend,
                )
            )
            continue
        reason = ocr.reason_code if ocr is not None else "OCR_UNAVAILABLE"
        status: OcrPageStatus = ocr.status if ocr is not None else "unavailable"
        results.append(
            PdfPageTextResult(
                page_number=page_number,
                text=embedded_text,
                text_source="pypdf" if embedded_text else "none",
                embedded_character_count=embedded_count,
                ocr_applied=False,
                ocr_required=True,
                ocr_status=status,
                reason_codes=("OCR_REQUIRED", reason),
                source_unit_id=ocr.source_unit_id if ocr is not None else None,
            )
        )
    return tuple(results)


__all__ = (
    "LocalOcrCapability",
    "LocalOcrConfig",
    "LocalOcrError",
    "MAX_OCR_CHECKPOINT_TEXT_BYTES",
    "MAX_OCR_PAGE_TEXT_CHARACTERS",
    "MAX_OCR_SOURCE_BYTES",
    "RETRYABLE_OCR_REASON_CODES",
    "OcrPageResult",
    "OCR_PARSER_BACKEND",
    "OCR_PARSER_BACKENDS",
    "PdfPageTextResult",
    "extract_pdf_text_pages",
    "local_ocr_capability",
    "ocr_checkpoint_aad",
    "ocr_pdf_pages",
)
