#!/usr/local/bin/python3
"""F0-E offline, single-unit PDF/JPEG OCR evidence executor.

stdin is a binary envelope: a four-byte big-endian header length, canonical
JSON header bytes, then exactly ``source_size`` source bytes. Source pixels and
recognized text exist only in memory. stdout is one bounded, body-free JSON
object containing hashes, counts, confidence aggregates, and bbox summaries.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import multiprocessing
import os
import platform
import re
import resource
import shutil
import stat
import sys
import unicodedata
from pathlib import Path


# The image installs only hash-locked wheels here. ``python -I`` deliberately
# ignores PYTHONPATH, so the fixed path is added explicitly after stdlib imports.
sys.path.insert(0, "/opt/python")


LOCK_PATH = "/opt/f0e/component-lock.json"
TEMP_ROOTS = ("/tmp", "/work")
ENVELOPE_SCHEMA = "f0e-envelope-v1"
RESULT_SCHEMA = "f0e-result-v1"

MAX_HEADER_BYTES = 4096
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_PDF_PAGES = 128
MAX_PAGES_PER_DOCUMENT_JOB = 16
UNITS_PER_EXECUTION = 1
MAX_PIXELS_PER_PAGE = 16_000_000
MAX_OCR_BLOCKS_PER_PAGE = 1024
MAX_OCR_CHARS_PER_PAGE = 1_000_000
MAX_OUTPUT_BYTES = 256 * 1024
RENDER_DPI = 250
TOTAL_TIMEOUT_SECONDS = 120
MANUAL_REVIEW_CONFIDENCE_FLOOR_PPM = 0
OCR_TEXT_NORMALIZATION_RULE = "ocr-text-nfc-lf-v1"
OCR_TEXT_NORMALIZATION_RULE_SHA256 = (
    "2bdd5fa88fb268bb8f2d3334f441699fb461f897a5b04d7680d6a7dfc310d3cc"
)

PACKAGE_VERSIONS = {
    "numpy": "2.4.6",
    "onnxruntime": "1.28.0",
    "opencv-python-headless": "5.0.0.93",
    "pypdfium2": "5.12.1",
    "rapidocr-onnxruntime": "1.4.4",
}

COMMON_HEADER_KEYS = {
    "schema",
    "document_type",
    "source_sha256",
    "source_size",
    "expected_total_pages",
    "page_no",
    "source_unit_id",
}
PDF_HEADER_KEYS = COMMON_HEADER_KEYS | {
    "media_box",
    "crop_box",
    "rotation_degrees",
}
JPEG_HEADER_KEYS = COMMON_HEADER_KEYS | {
    "image_width_px",
    "image_height_px",
}
BOX_KEYS = {"left", "bottom", "right", "top"}
THREE_DECIMAL_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)\.[0-9]{3}$")
LOWER_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class PublicError(Exception):
    """An error represented only by a stable, non-sensitive code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb", buffering=0) as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def _load_runtime_lock() -> dict:
    try:
        raw = Path(LOCK_PATH).read_bytes()
        if len(raw) > 64 * 1024:
            raise ValueError
        lock = json.loads(raw.decode("utf-8"))
        if not isinstance(lock, dict) or lock.get("schema") != "f0e-component-lock-v1":
            raise ValueError
        return lock
    except Exception as exc:
        raise PublicError("RUNTIME_INTEGRITY_FAILED") from exc


def _expected_profile() -> dict:
    return {
        "cap_drop_all": True,
        "concurrency": 1,
        "container_name_pattern": "^anhuan-f0e-[0-9a-f]{32}$",
        "cpu_limit_millis": 1000,
        "ipc_mode": "none",
        "log_driver": "none",
        "manual_review_confidence_floor_ppm": MANUAL_REVIEW_CONFIDENCE_FLOOR_PPM,
        "memory_limit_bytes": 1_073_741_824,
        "memory_swap_limit_bytes": 1_073_741_824,
        "max_header_bytes": MAX_HEADER_BYTES,
        "max_ocr_blocks_per_page": MAX_OCR_BLOCKS_PER_PAGE,
        "max_ocr_chars_per_page": MAX_OCR_CHARS_PER_PAGE,
        "max_output_bytes": MAX_OUTPUT_BYTES,
        "max_pages_per_document_job": MAX_PAGES_PER_DOCUMENT_JOB,
        "max_pdf_pages": MAX_PDF_PAGES,
        "max_pixels_per_page": MAX_PIXELS_PER_PAGE,
        "max_source_bytes": MAX_SOURCE_BYTES,
        "network_mode": "none",
        "no_new_privileges": True,
        "pids_limit": 64,
        "pull_policy": "never",
        "read_only_rootfs": True,
        "render_dpi": RENDER_DPI,
        "run_as_user": "65532:65532",
        "seccomp_sha256": "69744374970bce06b93756aa93c18e9a4a4abe95ff8f35fe7fbf67a79223092c",
        "shm_bytes": 67_108_864,
        "timeout_cleanup": "KILL_CLI_THEN_DOCKER_KILL_RM_INSPECT_NOT_FOUND_V1",
        "timeout_seconds": TOTAL_TIMEOUT_SECONDS,
        "tmpfs_tmp_bytes": 16_777_216,
        "tmpfs_work_bytes": 268_435_456,
        "units_per_execution": UNITS_PER_EXECUTION,
    }


def _verify_runtime(lock: dict) -> None:
    try:
        if platform.system() != "Linux" or platform.machine() != "aarch64":
            raise ValueError
        if sys.version_info[:3] != (3, 11, 9):
            raise ValueError
        for package, expected in PACKAGE_VERSIONS.items():
            if importlib.metadata.version(package) != expected:
                raise ValueError

        profile = _expected_profile()
        profile_sha256 = _sha256_bytes(_canonical_json(profile))
        if lock["profile"] != profile or lock["profile_sha256"] != profile_sha256:
            raise ValueError
        if (
            lock["ocr"]["normalization_rule"] != OCR_TEXT_NORMALIZATION_RULE
            or lock["ocr"]["normalization_rule_sha256"]
            != OCR_TEXT_NORMALIZATION_RULE_SHA256
            or _sha256_bytes(OCR_TEXT_NORMALIZATION_RULE.encode("ascii"))
            != OCR_TEXT_NORMALIZATION_RULE_SHA256
        ):
            raise ValueError

        integrity = lock["integrity"]
        checks = {
            "/opt/f0e/requirements.lock": integrity["requirements_lock_sha256"],
            "/opt/f0e/runner.py": integrity["runner_sha256"],
            "/opt/python/pypdfium2_raw/libpdfium.so": integrity["libpdfium_sha256"],
            "/opt/python/rapidocr_onnxruntime/config.yaml": integrity[
                "rapidocr_config_sha256"
            ],
        }
        for model in lock["ocr"]["models"]:
            checks[f"/opt/python/rapidocr_onnxruntime/models/{model['filename']}"] = model[
                "sha256"
            ]
        for path, expected in checks.items():
            if _sha256_file(path) != expected:
                raise ValueError
    except Exception as exc:
        raise PublicError("RUNTIME_INTEGRITY_FAILED") from exc


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PublicError("ENVELOPE_HEADER_INVALID")
        result[key] = value
    return result


def _read_exact(stream, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise PublicError("ENVELOPE_TRUNCATED")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _validate_box(box: object) -> None:
    if not isinstance(box, dict) or set(box) != BOX_KEYS:
        raise PublicError("ENVELOPE_HEADER_INVALID")
    for value in box.values():
        if (
            not isinstance(value, str)
            or not THREE_DECIMAL_RE.fullmatch(value)
            or abs(float(value)) > 1_000_000
        ):
            raise PublicError("ENVELOPE_HEADER_INVALID")
    if float(box["left"]) >= float(box["right"]) or float(box["bottom"]) >= float(
        box["top"]
    ):
        raise PublicError("ENVELOPE_HEADER_INVALID")


def _validate_header(header: object) -> dict:
    if not isinstance(header, dict):
        raise PublicError("ENVELOPE_HEADER_INVALID")
    document_type = header.get("document_type")
    expected_keys = PDF_HEADER_KEYS if document_type == "PDF" else JPEG_HEADER_KEYS
    if document_type not in {"PDF", "JPEG"} or set(header) != expected_keys:
        raise PublicError("ENVELOPE_HEADER_INVALID")
    if header["schema"] != ENVELOPE_SCHEMA:
        raise PublicError("ENVELOPE_HEADER_INVALID")
    if not isinstance(header["source_sha256"], str) or not LOWER_HEX_RE.fullmatch(
        header["source_sha256"]
    ):
        raise PublicError("ENVELOPE_HEADER_INVALID")
    if not isinstance(header["source_unit_id"], str) or not LOWER_HEX_RE.fullmatch(
        header["source_unit_id"]
    ):
        raise PublicError("ENVELOPE_HEADER_INVALID")
    if (
        type(header["source_size"]) is not int
        or not 8 <= header["source_size"] <= MAX_SOURCE_BYTES
    ):
        raise PublicError("ENVELOPE_HEADER_INVALID")
    if (
        type(header["expected_total_pages"]) is not int
        or not 1 <= header["expected_total_pages"] <= MAX_PDF_PAGES
        or type(header["page_no"]) is not int
        or not 1 <= header["page_no"] <= header["expected_total_pages"]
    ):
        raise PublicError("ENVELOPE_HEADER_INVALID")

    if document_type == "PDF":
        _validate_box(header["media_box"])
        _validate_box(header["crop_box"])
        if header["rotation_degrees"] not in {0, 90, 180, 270}:
            raise PublicError("ENVELOPE_HEADER_INVALID")
    else:
        if header["expected_total_pages"] != 1 or header["page_no"] != 1:
            raise PublicError("ENVELOPE_HEADER_INVALID")
        width = header["image_width_px"]
        height = header["image_height_px"]
        if (
            type(width) is not int
            or type(height) is not int
            or width < 1
            or height < 1
            or width * height > MAX_PIXELS_PER_PAGE
        ):
            raise PublicError("ENVELOPE_HEADER_INVALID")
    return header


def _read_envelope() -> tuple[dict, bytes]:
    stream = sys.stdin.buffer
    prefix = _read_exact(stream, 4)
    header_size = int.from_bytes(prefix, "big")
    if not 1 <= header_size <= MAX_HEADER_BYTES:
        raise PublicError("ENVELOPE_HEADER_LIMIT")
    header_raw = _read_exact(stream, header_size)
    try:
        header = json.loads(
            header_raw.decode("ascii"),
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except PublicError:
        raise
    except Exception as exc:
        raise PublicError("ENVELOPE_HEADER_INVALID") from exc
    header = _validate_header(header)
    if _canonical_json(header) != header_raw:
        raise PublicError("ENVELOPE_NOT_CANONICAL")
    source = _read_exact(stream, header["source_size"])
    if stream.read(1):
        raise PublicError("ENVELOPE_TRAILING_BYTES")
    if _sha256_bytes(source) != header["source_sha256"]:
        raise PublicError("SOURCE_HASH_MISMATCH")
    return header, source


def _clean_temp_roots() -> None:
    try:
        for root in TEMP_ROOTS:
            root_stat = os.lstat(root)
            if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
                raise OSError
            with os.scandir(root) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        shutil.rmtree(entry.path)
                    else:
                        os.unlink(entry.path)
    except Exception as exc:
        raise PublicError("TEMP_CLEANUP_FAILED") from exc


def _apply_worker_limits() -> None:
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    resource.setrlimit(
        resource.RLIMIT_CPU, (TOTAL_TIMEOUT_SECONDS - 5, TOTAL_TIMEOUT_SECONDS)
    )


def _silence_worker() -> None:
    null_fd = os.open("/dev/null", os.O_WRONLY | os.O_CLOEXEC)
    try:
        os.dup2(null_fd, 1)
        os.dup2(null_fd, 2)
    finally:
        if null_fd > 2:
            os.close(null_fd)


def _format_box(values: object) -> dict:
    try:
        left, bottom, right, top = values
        numeric = [float(left), float(bottom), float(right), float(top)]
    except Exception as exc:
        raise PublicError("PDF_GEOMETRY_INVALID") from exc
    if not all(math.isfinite(value) for value in numeric):
        raise PublicError("PDF_GEOMETRY_INVALID")
    return {
        "left": f"{numeric[0]:.3f}",
        "bottom": f"{numeric[1]:.3f}",
        "right": f"{numeric[2]:.3f}",
        "top": f"{numeric[3]:.3f}",
    }


def _rounded_box(box: object, width: int, height: int) -> list[list[int]]:
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise PublicError("OCR_RESULT_INVALID")
    rounded = []
    for point in box:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise PublicError("OCR_RESULT_INVALID")
        try:
            x = float(point[0])
            y = float(point[1])
        except (TypeError, ValueError) as exc:
            raise PublicError("OCR_RESULT_INVALID") from exc
        if not math.isfinite(x) or not math.isfinite(y):
            raise PublicError("OCR_RESULT_INVALID")
        rounded.append(
            [
                min(width, max(0, int(round(x)))),
                min(height, max(0, int(round(y)))),
            ]
        )
    return rounded


def _summarize_ocr(result: object, width: int, height: int) -> dict:
    if result is None:
        result = []
    if not isinstance(result, list) or len(result) > MAX_OCR_BLOCKS_PER_PAGE:
        raise PublicError("OCR_RESULT_INVALID")

    text_digest = hashlib.sha256(
        b"F0E_TEXT_SEQUENCE_V1\0"
        + OCR_TEXT_NORMALIZATION_RULE.encode("ascii")
        + b"\0"
    )
    box_digest = hashlib.sha256(b"F0E_BOX_SEQUENCE_V1\0")
    char_count = 0
    nonblank_count = 0
    confidence_values = []
    union = None

    for index, item in enumerate(result):
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            raise PublicError("OCR_RESULT_INVALID")
        box, text, score = item[0], item[1], item[2]
        if not isinstance(text, str):
            raise PublicError("OCR_RESULT_INVALID")
        normalized_text = unicodedata.normalize(
            "NFC", text.replace("\r\n", "\n").replace("\r", "\n")
        )
        encoded = normalized_text.encode("utf-8", "strict")
        char_count += len(normalized_text)
        nonblank_count += sum(
            not character.isspace() for character in normalized_text
        )
        if char_count > MAX_OCR_CHARS_PER_PAGE:
            raise PublicError("OCR_RESULT_INVALID")
        text_digest.update(index.to_bytes(4, "big"))
        text_digest.update(len(encoded).to_bytes(8, "big"))
        text_digest.update(encoded)

        rounded = _rounded_box(box, width, height)
        canonical_box = _canonical_json(rounded)
        box_digest.update(index.to_bytes(4, "big"))
        box_digest.update(len(canonical_box).to_bytes(4, "big"))
        box_digest.update(canonical_box)
        xs = [point[0] for point in rounded]
        ys = [point[1] for point in rounded]
        current = [min(xs), min(ys), max(xs), max(ys)]
        if union is None:
            union = current
        else:
            union = [
                min(union[0], current[0]),
                min(union[1], current[1]),
                max(union[2], current[2]),
                max(union[3], current[3]),
            ]

        try:
            confidence = float(score)
        except (TypeError, ValueError) as exc:
            raise PublicError("OCR_RESULT_INVALID") from exc
        if not math.isfinite(confidence):
            raise PublicError("OCR_RESULT_INVALID")
        confidence_values.append(
            min(1_000_000, max(0, int(round(confidence * 1_000_000))))
        )

    if confidence_values and nonblank_count > 0:
        confidence_min = min(confidence_values)
        confidence_mean = (
            sum(confidence_values) + len(confidence_values) // 2
        ) // len(confidence_values)
    else:
        confidence_min = None
        confidence_mean = None
    return {
        "bbox_coordinate_space": "RENDERED_PIXEL_TOP_LEFT_V1",
        "bbox_sha256": box_digest.hexdigest(),
        "bbox_union_px": union,
        "confidence_mean_ppm": confidence_mean,
        "confidence_min_ppm": confidence_min,
        "ocr_block_count": len(result),
        "ocr_char_count": char_count,
        "ocr_nonblank_char_count": nonblank_count,
        "ocr_text_sha256": text_digest.hexdigest(),
        "normalization_rule": OCR_TEXT_NORMALIZATION_RULE,
        "normalization_rule_sha256": OCR_TEXT_NORMALIZATION_RULE_SHA256,
    }


def _jpeg_dimensions(source: bytes) -> tuple[int, int]:
    if len(source) < 4 or source[:2] != b"\xff\xd8":
        raise PublicError("JPEG_HEADER_INVALID")
    position = 2
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while position < len(source):
        if source[position] != 0xFF:
            raise PublicError("JPEG_HEADER_INVALID")
        while position < len(source) and source[position] == 0xFF:
            position += 1
        if position >= len(source):
            break
        marker = source[position]
        position += 1
        if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
            continue
        if position + 2 > len(source):
            break
        segment_length = int.from_bytes(source[position : position + 2], "big")
        if segment_length < 2 or position + segment_length > len(source):
            raise PublicError("JPEG_HEADER_INVALID")
        if marker in sof_markers:
            if segment_length < 8:
                raise PublicError("JPEG_HEADER_INVALID")
            height = int.from_bytes(source[position + 3 : position + 5], "big")
            width = int.from_bytes(source[position + 5 : position + 7], "big")
            if width < 1 or height < 1:
                raise PublicError("JPEG_HEADER_INVALID")
            return width, height
        if marker == 0xDA:
            break
        position += segment_length
    raise PublicError("JPEG_HEADER_INVALID")


def _render_pdf(source: bytes, header: dict, pdfium) -> tuple[object, dict]:
    try:
        document = pdfium.PdfDocument(source)
        actual_pages = len(document)
    except Exception as exc:
        raise PublicError("PDF_PARSE_FAILED") from exc
    try:
        if actual_pages < 1 or actual_pages > MAX_PDF_PAGES:
            raise PublicError("PDF_PAGE_LIMIT")
        if actual_pages != header["expected_total_pages"]:
            raise PublicError("PDF_PAGE_COUNT_MISMATCH")
        page = document[header["page_no"] - 1]
        bitmap = None
        try:
            media_box = _format_box(page.get_mediabox())
            crop_box = _format_box(page.get_cropbox())
            rotation = page.get_rotation()
            if (
                media_box != header["media_box"]
                or crop_box != header["crop_box"]
                or rotation != header["rotation_degrees"]
            ):
                raise PublicError("PDF_GEOMETRY_MISMATCH")
            width_pt, height_pt = page.get_size()
            if (
                not math.isfinite(width_pt)
                or not math.isfinite(height_pt)
                or width_pt <= 0
                or height_pt <= 0
            ):
                raise PublicError("PDF_GEOMETRY_INVALID")
            width_px = math.ceil(width_pt * RENDER_DPI / 72)
            height_px = math.ceil(height_pt * RENDER_DPI / 72)
            if width_px * height_px > MAX_PIXELS_PER_PAGE:
                raise PublicError("PIXEL_LIMIT_EXCEEDED")
            bitmap = page.render(
                scale=RENDER_DPI / 72,
                may_draw_forms=False,
                fill_color=(255, 255, 255, 255),
                draw_annots=False,
                optimize_mode="print",
            )
            image = bitmap.to_numpy().copy()
        except PublicError:
            raise
        except Exception as exc:
            raise PublicError("PDF_RENDER_FAILED") from exc
        finally:
            if bitmap is not None:
                bitmap.close()
            page.close()
    finally:
        document.close()
    return image, {
        "render_origin": "PDFIUM_250_DPI",
        "render_dpi": RENDER_DPI,
    }


def _decode_jpeg(source: bytes, header: dict, cv2, np) -> tuple[object, dict]:
    width, height = _jpeg_dimensions(source)
    if (
        width != header["image_width_px"]
        or height != header["image_height_px"]
        or width * height > MAX_PIXELS_PER_PAGE
    ):
        raise PublicError("JPEG_GEOMETRY_MISMATCH")
    try:
        image = cv2.imdecode(np.frombuffer(source, dtype=np.uint8), cv2.IMREAD_COLOR)
    except Exception as exc:
        raise PublicError("JPEG_DECODE_FAILED") from exc
    if image is None or image.ndim != 3 or image.shape != (height, width, 3):
        raise PublicError("JPEG_DECODE_FAILED")
    return image, {
        "render_origin": "JPEG_DECODED_SOURCE_PIXELS",
        "render_dpi": None,
    }


def _process_unit(source: bytes, header: dict) -> dict:
    try:
        import cv2
        import numpy as np
        import pypdfium2 as pdfium
        from rapidocr_onnxruntime import RapidOCR
    except Exception as exc:
        raise PublicError("RUNTIME_COMPONENT_LOAD_FAILED") from exc

    if header["document_type"] == "PDF":
        image, render_meta = _render_pdf(source, header, pdfium)
    else:
        image, render_meta = _decode_jpeg(source, header, cv2, np)
    if image.ndim != 3 or image.shape[2] != 3:
        raise PublicError("RENDER_FORMAT_INVALID")
    height_px, width_px = int(image.shape[0]), int(image.shape[1])
    if width_px * height_px > MAX_PIXELS_PER_PAGE:
        raise PublicError("PIXEL_LIMIT_EXCEEDED")
    image_bytes = image.tobytes(order="C")
    render_sha256 = _sha256_bytes(
        b"F0E_BGR24_V1\0"
        + width_px.to_bytes(4, "big")
        + height_px.to_bytes(4, "big")
        + image_bytes
    )
    del image_bytes
    try:
        ocr = RapidOCR(
            intra_op_num_threads=1,
            inter_op_num_threads=1,
            print_verbose=False,
            max_side_len=2000,
            return_word_box=False,
        )
        result, _elapsed = ocr(image)
    except Exception as exc:
        raise PublicError("OCR_INFERENCE_FAILED") from exc
    summary = _summarize_ocr(result, width_px, height_px)
    del image
    if summary["ocr_nonblank_char_count"] == 0:
        decision = "MANUAL_REVIEW_REQUIRED"
        reason_codes = ["EMPTY_OCR_OUTPUT"]
    else:
        decision = "OCR_EVIDENCE_CAPTURED_NOT_VALIDATED"
        reason_codes = ["OCR_OUTPUT_HASHED", "CONFIDENCE_NOT_CALIBRATED"]
    return {
        "decision": decision,
        "reason_codes": reason_codes,
        **render_meta,
        "render_width_px": width_px,
        "render_height_px": height_px,
        "render_pixel_format": "BGR24",
        "render_sha256": render_sha256,
        **summary,
    }


def _worker(send_pipe, source: bytes, header: dict) -> None:
    try:
        _silence_worker()
        _apply_worker_limits()
        send_pipe.send(("ok", _process_unit(source, header)))
    except PublicError as exc:
        try:
            send_pipe.send(("error", exc.code))
        except Exception:
            pass
    except BaseException:
        try:
            send_pipe.send(("error", "OCR_WORKER_FAILED"))
        except Exception:
            pass
    finally:
        send_pipe.close()


def _run_worker(source: bytes, header: dict) -> dict:
    context = multiprocessing.get_context("fork")
    receive_pipe, send_pipe = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(send_pipe, source, header))
    process.daemon = False
    process.start()
    send_pipe.close()
    process.join(TOTAL_TIMEOUT_SECONDS)
    if process.is_alive():
        process.kill()
        process.join(5)
        receive_pipe.close()
        raise PublicError("OCR_TIMEOUT")
    try:
        if not receive_pipe.poll():
            raise PublicError("OCR_WORKER_FAILED")
        status_value, payload = receive_pipe.recv()
    except PublicError:
        raise
    except Exception as exc:
        raise PublicError("OCR_WORKER_FAILED") from exc
    finally:
        receive_pipe.close()
    if status_value != "ok":
        if not isinstance(payload, str) or not payload.isupper():
            raise PublicError("OCR_WORKER_FAILED")
        raise PublicError(payload)
    if not isinstance(payload, dict):
        raise PublicError("OCR_WORKER_FAILED")
    return payload


def _success(lock: dict, header: dict, evidence: dict) -> dict:
    model_digest = hashlib.sha256(b"F0E_MODEL_BUNDLE_V1\0")
    for model in lock["ocr"]["models"]:
        model_digest.update(model["filename"].encode("ascii"))
        model_digest.update(bytes.fromhex(model["sha256"]))
    if header["document_type"] == "PDF":
        renderer = {
            "name": "pypdfium2",
            "version": lock["renderer"]["pypdfium2_version"],
            "pdfium_version": lock["renderer"]["pdfium_version"],
        }
    else:
        renderer = {"name": "opencv-imdecode", "version": "5.0.0.93"}
    return {
        "schema": RESULT_SCHEMA,
        "status": "SUCCESS",
        "source_unit_id": header["source_unit_id"],
        "document_type": header["document_type"],
        "source_sha256": header["source_sha256"],
        "page_no": header["page_no"],
        "expected_total_pages": header["expected_total_pages"],
        "fixture_label": "FIXTURE_ONLY",
        "benchmark_tier": "NONE",
        "accuracy_claimed": False,
        "gold_status": "NOT_EVALUATED",
        "professional_status": "NOT_REVIEWED",
        "external_processing": "DENY",
        "external_calls": 0,
        "raw_text_emitted": False,
        "raw_text_persisted": False,
        "ocr_executed": True,
        "profile_sha256": lock["profile_sha256"],
        "renderer": renderer,
        "ocr_engine": {
            "name": "rapidocr-onnxruntime",
            "version": lock["ocr"]["rapidocr_version"],
            "onnxruntime_version": lock["ocr"]["onnxruntime_version"],
            "model_bundle_sha256": model_digest.hexdigest(),
        },
        **evidence,
        "temp_residuals": 0,
    }


def _error(code: str) -> dict:
    return {
        "schema": RESULT_SCHEMA,
        "status": "ERROR",
        "error_code": code,
        "fixture_label": "FIXTURE_ONLY",
        "benchmark_tier": "NONE",
        "accuracy_claimed": False,
        "external_processing": "DENY",
        "external_calls": 0,
        "raw_text_emitted": False,
        "raw_text_persisted": False,
    }


def _emit(payload: dict, exit_code: int) -> None:
    encoded = _canonical_json(payload) + b"\n"
    if len(encoded) > MAX_OUTPUT_BYTES:
        encoded = _canonical_json(_error("OUTPUT_LIMIT_EXCEEDED")) + b"\n"
        exit_code = 2
    try:
        os.write(1, encoded)
    except OSError:
        exit_code = 2
    raise SystemExit(exit_code)


def main() -> None:
    try:
        _clean_temp_roots()
        lock = _load_runtime_lock()
        _verify_runtime(lock)
        header, source = _read_envelope()
        evidence = _run_worker(source, header)
        del source
        _clean_temp_roots()
        _emit(_success(lock, header, evidence), 0)
    except PublicError as exc:
        try:
            _clean_temp_roots()
        except PublicError:
            exc = PublicError("TEMP_CLEANUP_FAILED")
        _emit(_error(exc.code), 2)
    except Exception:
        try:
            _clean_temp_roots()
        except PublicError:
            pass
        _emit(_error("INTERNAL_ERROR"), 2)


if __name__ == "__main__":
    main()
