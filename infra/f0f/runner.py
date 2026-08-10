#!/usr/local/bin/python3
"""F0-F private single-page OCR body executor.

stdin keeps the frozen F0-E binary envelope. stdout is a single bounded,
canonical JSON response intended only for a caller-owned in-memory pipe. A
successful response contains normalized OCR block text; failures contain only
stable public error codes. The caller must never inherit, log, or forward the
private stdout stream.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import sys
import unicodedata
from pathlib import Path


F0E_RUNNER_PATH = "/opt/f0e/runner.py"
F0E_COMPONENT_LOCK_PATH = "/opt/f0e/component-lock.json"
F0F_COMPONENT_LOCK_PATH = "/opt/f0f/component-lock.json"

RESULT_SCHEMA = "f0f-body-result-v1"
MAX_PRIVATE_OUTPUT_BYTES = 8 * 1024 * 1024
FROZEN_F0E_IMAGE_ID = (
    "sha256:afff23f8e469f76e8b94159ccd5a1a4345c12a9c72c95ad150acf51c8c86085a"
)
FROZEN_F0E_PROFILE_SHA256 = (
    "8b79ddd2e30708f15f72493bbba937a005ffe61d39c0da9baade89e264b674b6"
)


class PublicError(Exception):
    """An error represented by one non-sensitive stable code."""

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


def _load_json(path: str, schema: str, limit: int = 128 * 1024) -> dict:
    try:
        raw = Path(path).read_bytes()
        if not 1 <= len(raw) <= limit:
            raise ValueError
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict) or value.get("schema") != schema:
            raise ValueError
        return value
    except Exception as exc:
        raise PublicError("RUNTIME_INTEGRITY_FAILED") from exc


def _load_f0e_module(expected_sha256: str):
    try:
        if _sha256_file(F0E_RUNNER_PATH) != expected_sha256:
            raise ValueError
        spec = importlib.util.spec_from_file_location("_frozen_f0e_runner", F0E_RUNNER_PATH)
        if spec is None or spec.loader is None:
            raise ValueError
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception as exc:
        raise PublicError("RUNTIME_INTEGRITY_FAILED") from exc


def _expected_profile(base) -> dict:
    return {
        "base_image_id": FROZEN_F0E_IMAGE_ID,
        "base_profile_sha256": FROZEN_F0E_PROFILE_SHA256,
        "cap_drop_all": True,
        "concurrency": 1,
        "container_name_pattern": "^anhuan-f0f-[0-9a-f]{32}$",
        "cpu_limit_millis": 1000,
        "input_envelope_schema": base.ENVELOPE_SCHEMA,
        "ipc_mode": "none",
        "log_driver": "none",
        "max_header_bytes": base.MAX_HEADER_BYTES,
        "max_ocr_blocks_per_page": base.MAX_OCR_BLOCKS_PER_PAGE,
        "max_ocr_chars_per_page": base.MAX_OCR_CHARS_PER_PAGE,
        "max_pdf_pages": base.MAX_PDF_PAGES,
        "max_pixels_per_page": base.MAX_PIXELS_PER_PAGE,
        "max_private_output_bytes": MAX_PRIVATE_OUTPUT_BYTES,
        "max_source_bytes": base.MAX_SOURCE_BYTES,
        "network_mode": "none",
        "no_new_privileges": True,
        "output_schema": RESULT_SCHEMA,
        "pids_limit": 64,
        "pull_policy": "never",
        "read_only_rootfs": True,
        "render_dpi": base.RENDER_DPI,
        "run_as_user": "65532:65532",
        "seccomp_sha256": "d96b278b73bb348b90ee3c4d50f108c038b1f9636d6792a5da82f27c85905c68",
        "shm_bytes": 67_108_864,
        "timeout_cleanup": "KILL_CLI_THEN_DOCKER_KILL_RM_INSPECT_NOT_FOUND_V1",
        "timeout_seconds": base.TOTAL_TIMEOUT_SECONDS,
        "tmpfs_tmp_bytes": 16_777_216,
        "tmpfs_work_bytes": 268_435_456,
        "units_per_execution": 1,
    }


def _verify_runtime(base, lock: dict) -> None:
    try:
        base_lock = base._load_runtime_lock()
        base._verify_runtime(base_lock)
        if base_lock["profile_sha256"] != FROZEN_F0E_PROFILE_SHA256:
            raise ValueError
        if lock["base"]["image_id"] != FROZEN_F0E_IMAGE_ID:
            raise ValueError
        if lock["base"]["profile_sha256"] != FROZEN_F0E_PROFILE_SHA256:
            raise ValueError
        if _sha256_file(F0E_COMPONENT_LOCK_PATH) != lock["integrity"][
            "base_component_lock_sha256"
        ]:
            raise ValueError
        if _sha256_file(F0E_RUNNER_PATH) != lock["integrity"][
            "base_runner_sha256"
        ]:
            raise ValueError
        if _sha256_file(__file__) != lock["integrity"]["runner_sha256"]:
            raise ValueError
        profile = _expected_profile(base)
        if lock["profile"] != profile:
            raise ValueError
        if lock["profile_sha256"] != _sha256_bytes(_canonical_json(profile)):
            raise ValueError
        if lock["output"] != {
            "block_keys": ["bbox", "confidence_ppm", "index", "text"],
            "normalization_rule": base.OCR_TEXT_NORMALIZATION_RULE,
            "normalization_rule_sha256": base.OCR_TEXT_NORMALIZATION_RULE_SHA256,
            "schema": RESULT_SCHEMA,
        }:
            raise ValueError
        if lock["ocr"] != {
            "engine": "rapidocr-onnxruntime",
            "engine_version": base_lock["ocr"]["rapidocr_version"],
            "f0e_normalization_rule": base.OCR_TEXT_NORMALIZATION_RULE,
            "f0e_normalization_rule_sha256": base.OCR_TEXT_NORMALIZATION_RULE_SHA256,
            "model_bundle_sha256": base_lock["ocr"]["model_bundle_sha256"],
        }:
            raise ValueError
    except PublicError:
        raise
    except Exception as exc:
        raise PublicError("RUNTIME_INTEGRITY_FAILED") from exc


def _translate_base_error(base, operation):
    try:
        return operation()
    except base.PublicError as exc:
        raise PublicError(exc.code) from None


def _normalize_result_blocks(base, result: object, width: int, height: int) -> list[dict]:
    if result is None:
        result = []
    if not isinstance(result, list) or len(result) > base.MAX_OCR_BLOCKS_PER_PAGE:
        raise PublicError("OCR_RESULT_INVALID")
    blocks = []
    total_chars = 0
    for index, item in enumerate(result):
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            raise PublicError("OCR_RESULT_INVALID")
        box, text, score = item[0], item[1], item[2]
        if not isinstance(text, str):
            raise PublicError("OCR_RESULT_INVALID")
        normalized = unicodedata.normalize(
            "NFC", text.replace("\r\n", "\n").replace("\r", "\n")
        )
        try:
            normalized.encode("utf-8", "strict")
        except UnicodeError as exc:
            raise PublicError("OCR_RESULT_INVALID") from exc
        total_chars += len(normalized)
        if total_chars > base.MAX_OCR_CHARS_PER_PAGE:
            raise PublicError("OCR_RESULT_INVALID")
        try:
            rounded = base._rounded_box(box, width, height)
        except base.PublicError as exc:
            raise PublicError(exc.code) from None
        try:
            confidence = float(score)
        except (TypeError, ValueError) as exc:
            raise PublicError("OCR_RESULT_INVALID") from exc
        if not math.isfinite(confidence):
            raise PublicError("OCR_RESULT_INVALID")
        confidence_ppm = min(1_000_000, max(0, int(round(confidence * 1_000_000))))
        blocks.append(
            {
                "bbox": rounded,
                "confidence_ppm": confidence_ppm,
                "index": index,
                "text": normalized,
            }
        )
    return blocks


def _process_unit(base, source: bytes, header: dict) -> dict:
    try:
        import cv2
        import numpy as np
        import pypdfium2 as pdfium
        from rapidocr_onnxruntime import RapidOCR
    except Exception as exc:
        raise PublicError("RUNTIME_COMPONENT_LOAD_FAILED") from exc

    if header["document_type"] == "PDF":
        try:
            image, render_meta = base._render_pdf(source, header, pdfium)
        except base.PublicError as exc:
            raise PublicError(exc.code) from None
    else:
        try:
            image, render_meta = base._decode_jpeg(source, header, cv2, np)
        except base.PublicError as exc:
            raise PublicError(exc.code) from None
    if image.ndim != 3 or image.shape[2] != 3:
        raise PublicError("RENDER_FORMAT_INVALID")
    height_px, width_px = int(image.shape[0]), int(image.shape[1])
    if width_px * height_px > base.MAX_PIXELS_PER_PAGE:
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
    try:
        summary = base._summarize_ocr(result, width_px, height_px)
    except base.PublicError as exc:
        raise PublicError(exc.code) from None
    blocks = _normalize_result_blocks(base, result, width_px, height_px)
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
        "blocks": blocks,
    }


def _worker(send_pipe, base, source: bytes, header: dict) -> None:
    try:
        base._silence_worker()
        base._apply_worker_limits()
        send_pipe.send(("ok", _process_unit(base, source, header)))
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


def _run_worker(base, source: bytes, header: dict) -> dict:
    context = base.multiprocessing.get_context("fork")
    receive_pipe, send_pipe = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(send_pipe, base, source, header))
    process.daemon = False
    process.start()
    send_pipe.close()
    try:
        if not receive_pipe.poll(base.TOTAL_TIMEOUT_SECONDS):
            process.kill()
            process.join(5)
            raise PublicError("OCR_TIMEOUT")
        try:
            status_value, payload = receive_pipe.recv()
        except Exception as exc:
            raise PublicError("OCR_WORKER_FAILED") from exc
    finally:
        receive_pipe.close()
    process.join(5)
    if process.is_alive():
        process.kill()
        process.join(5)
        raise PublicError("OCR_WORKER_FAILED")
    if status_value != "ok":
        if not isinstance(payload, str) or not payload.isupper():
            raise PublicError("OCR_WORKER_FAILED")
        raise PublicError(payload)
    if process.exitcode != 0 or not isinstance(payload, dict):
        raise PublicError("OCR_WORKER_FAILED")
    return payload


def _success(base_lock: dict, lock: dict, header: dict, evidence: dict) -> dict:
    model_digest = hashlib.sha256(b"F0E_MODEL_BUNDLE_V1\0")
    for model in base_lock["ocr"]["models"]:
        model_digest.update(model["filename"].encode("ascii"))
        model_digest.update(bytes.fromhex(model["sha256"]))
    renderer = (
        {
            "name": "pypdfium2",
            "version": base_lock["renderer"]["pypdfium2_version"],
            "pdfium_version": base_lock["renderer"]["pdfium_version"],
        }
        if header["document_type"] == "PDF"
        else {"name": "opencv-imdecode", "version": "5.0.0.93"}
    )
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
        "raw_text_emitted": True,
        "raw_text_persisted": False,
        "ocr_executed": True,
        "profile_sha256": lock["profile_sha256"],
        "renderer": renderer,
        "ocr_engine": {
            "name": "rapidocr-onnxruntime",
            "version": base_lock["ocr"]["rapidocr_version"],
            "onnxruntime_version": base_lock["ocr"]["onnxruntime_version"],
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
    if len(encoded) > MAX_PRIVATE_OUTPUT_BYTES:
        encoded = _canonical_json(_error("PRIVATE_OUTPUT_LIMIT_EXCEEDED")) + b"\n"
        exit_code = 2
    offset = 0
    try:
        while offset < len(encoded):
            written = os.write(1, encoded[offset:])
            if written < 1:
                raise OSError
            offset += written
    except OSError:
        exit_code = 2
    raise SystemExit(exit_code)


def main() -> None:
    base = None
    try:
        lock = _load_json(F0F_COMPONENT_LOCK_PATH, "f0f-component-lock-v1")
        base = _load_f0e_module(lock["integrity"]["base_runner_sha256"])
        _verify_runtime(base, lock)
        _translate_base_error(base, base._clean_temp_roots)
        header, source = _translate_base_error(base, base._read_envelope)
        evidence = _run_worker(base, source, header)
        del source
        _translate_base_error(base, base._clean_temp_roots)
        base_lock = base._load_runtime_lock()
        _emit(_success(base_lock, lock, header, evidence), 0)
    except PublicError as exc:
        if base is not None:
            try:
                base._clean_temp_roots()
            except Exception:
                exc = PublicError("TEMP_CLEANUP_FAILED")
        _emit(_error(exc.code), 2)
    except Exception:
        if base is not None:
            try:
                base._clean_temp_roots()
            except Exception:
                pass
        _emit(_error("INTERNAL_ERROR"), 2)


if __name__ == "__main__":
    main()
