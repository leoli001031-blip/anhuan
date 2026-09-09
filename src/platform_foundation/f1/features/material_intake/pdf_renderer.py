"""Bounded, process-isolated PDFium rendering of the visible whole page.

The worker receives PDF bytes on stdin, never a path or cloud credential.
PDFium is not thread-safe, so no native renderer is loaded into API workers.
The pinned non-V8 wheel renders annotations and AcroForm appearances, but
XFA is deliberately rejected. No PDF/image plaintext is written to disk.
"""
from __future__ import annotations

import io
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys

MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_PAGES = 128
MAX_PIXELS = 8_000_000
MAX_EDGE = 4096
MAX_IMAGE_BYTES = 6 * 1024 * 1024
PAGE_TIMEOUT_SECONDS = 20.0
TOTAL_RENDER_TIMEOUT_SECONDS = 120.0
RENDER_DPI = 200
PYPDFIUM_VERSION = "5.13.0"
PDFIUM_VERSION = (153, 0, 7999, 0)


class PdfRenderError(RuntimeError):
    """Only fixed codes cross the process boundary."""


def render_pdf_page(source: bytes, page_number: int, *, required_page: int | None = None,
                    timeout: float = PAGE_TIMEOUT_SECONDS) -> bytes:
    if not 8 <= len(source) <= MAX_SOURCE_BYTES:
        raise PdfRenderError("OCR_SOURCE_LIMIT")
    if type(page_number) is not int or not 1 <= page_number <= MAX_PAGES:
        raise PdfRenderError("OCR_PAGE_INVALID")
    required_page = page_number if required_page is None else required_page
    if type(required_page) is not int or not page_number <= required_page <= MAX_PAGES:
        raise PdfRenderError("OCR_PAGE_INVALID")
    if not math.isfinite(timeout) or timeout <= 0:
        raise PdfRenderError("PDF_RENDER_TIMEOUT")
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", str(Path(__file__).resolve()), str(page_number), str(required_page)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
            close_fds=True, start_new_session=True,
        )
        output, _ = process.communicate(source, timeout=min(timeout, PAGE_TIMEOUT_SECONDS))
    except subprocess.TimeoutExpired:
        raise PdfRenderError("PDF_RENDER_TIMEOUT") from None
    except OSError:
        raise PdfRenderError("PDF_RENDER_UNAVAILABLE") from None
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        if process is not None:
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()
    if process.returncode != 0 or len(output) > MAX_IMAGE_BYTES + 256:
        raise PdfRenderError("PDF_RENDER_UNAVAILABLE")
    try:
        header, image = output.split(b"\n", 1)
        info = json.loads(header)
        if info.get("error") in {"OCR_SOURCE_INVALID", "OCR_SOURCE_ENCRYPTED", "OCR_PAGE_INVALID", "PDF_RENDER_UNSUPPORTED"}:
            raise PdfRenderError(info["error"])
        width, height = info["width"], info["height"]
        if (type(width) is not int or type(height) is not int
                or not 1 <= width <= MAX_EDGE or not 1 <= height <= MAX_EDGE
                or width * height > MAX_PIXELS or not 1 <= len(image) <= MAX_IMAGE_BYTES
                or not image.startswith(b"\xff\xd8") or not image.endswith(b"\xff\xd9")):
            raise ValueError
        return image
    except (ValueError, KeyError, TypeError):
        raise PdfRenderError("PDF_RENDER_UNAVAILABLE") from None


def _worker() -> None:
    import resource
    # Apply before loading PDFium or handling any untrusted bytes. Linux is
    # the production target and enforces DATA/AS. macOS does not provide these
    # limits reliably; host tests retain CPU/pixel/wall-clock caps only.
    resource.setrlimit(resource.RLIMIT_CPU, (15, 16))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_DATA, (512 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024,) * 2)
    from importlib.metadata import version
    if version("pypdfium2") != PYPDFIUM_VERSION:
        raise RuntimeError
    import pypdfium2 as pdfium
    if pdfium.PDFIUM_INFO.flags or pdfium.PDFIUM_INFO.api_tag != PDFIUM_VERSION:
        # The wheel lock fixes this exact non-JavaScript native build.
        raise RuntimeError
    source = sys.stdin.buffer.read(MAX_SOURCE_BYTES + 1)
    if not 8 <= len(source) <= MAX_SOURCE_BYTES:
        raise RuntimeError
    try:
        document = pdfium.PdfDocument(source)
    except pdfium.PdfiumError as error:
        code = "OCR_SOURCE_ENCRYPTED" if error.err_code == pdfium.raw.FPDF_ERR_PASSWORD else "OCR_SOURCE_INVALID"
        sys.stdout.buffer.write(json.dumps({"error": code}).encode() + b"\n")
        return
    with document:
        if pdfium.raw.FPDF_GetSecurityHandlerRevision(document) >= 0:
            sys.stdout.buffer.write(b'{"error":"OCR_SOURCE_ENCRYPTED"}\n')
            return
        if document.get_formtype() in (pdfium.raw.FORMTYPE_XFA_FULL, pdfium.raw.FORMTYPE_XFA_FOREGROUND):
            sys.stdout.buffer.write(b'{"error":"PDF_RENDER_UNSUPPORTED"}\n')
            return
        document.init_forms()
        count = len(document)
        page_number, required_page = int(sys.argv[1]), int(sys.argv[2])
        if not 1 <= page_number <= required_page <= count <= MAX_PAGES:
            sys.stdout.buffer.write(b'{"error":"OCR_PAGE_INVALID"}\n')
            return
        page = document[page_number - 1]
        # PDFium can silently omit an undecodable painted image while still
        # returning a page bitmap. Require each painted image to decode first;
        # unused XObject resources are deliberately absent from this walk.
        image_count = 0
        for index, obj in enumerate(page.get_objects(max_depth=16)):
            if index >= 50_000 or (obj.type == pdfium.raw.FPDF_PAGEOBJ_FORM and obj.level >= 15):
                raise RuntimeError
            if obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE:
                image_count += 1
                if image_count > 128:
                    raise RuntimeError
                image_width, image_height = obj.get_px_size()
                if (min(image_width, image_height) < 1
                        or image_width * image_height > 64_000_000
                        or max(image_width, image_height) > 16384):
                    raise RuntimeError
                bitmap = obj.get_bitmap()
                try:
                    if bitmap.width * bitmap.height > 64_000_000 or max(bitmap.width, bitmap.height) > 16384:
                        raise RuntimeError
                finally:
                    bitmap.close()
        width, height = page.get_size()  # Visible CropBox/MediaBox intersection and /Rotate.
        if not all(math.isfinite(v) and 1 <= v <= 14400 for v in (width, height)):
            raise RuntimeError
        # Subtract one pixel before ceil in PDFium to keep both bounds strict.
        scale = min(RENDER_DPI / 72, (MAX_EDGE - 1) / max(width, height),
                    math.sqrt((MAX_PIXELS - 2 * MAX_EDGE) / (width * height)))
        bitmap = page.render(scale=scale, draw_annots=True, may_draw_forms=True)
        try:
            with bitmap.to_pil() as original:
                with original.convert("RGB") as rgb:
                    if rgb.width * rgb.height > MAX_PIXELS or max(rgb.size) > MAX_EDGE:
                        raise RuntimeError
                    output = io.BytesIO()
                    rgb.save(output, format="JPEG", quality=90, subsampling=0)
                    data = output.getvalue()
                    if not 1 <= len(data) <= MAX_IMAGE_BYTES:
                        raise RuntimeError
                    header = json.dumps({"width": rgb.width, "height": rgb.height}, separators=(",", ":")).encode()
                    sys.stdout.buffer.write(header + b"\n" + data)
        finally:
            bitmap.close()
            page.close()


if __name__ == "__main__":
    try:
        _worker()
    except BaseException:
        # Never emit PDF parser errors, source text, filenames or tracebacks.
        sys.exit(1)
