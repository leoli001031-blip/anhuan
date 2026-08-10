#!/usr/local/bin/python3
"""F0-H offline PP-OCRv6-small single-unit executor.

The input remains the frozen ``f0e-envelope-v1`` binary envelope.  The fixed
``body`` mode emits ``f0f-body-result-v1`` into a private, bounded pipe; the
fixed ``evidence`` mode projects the same execution to body-free
``f0e-result-v1``.  No source path, environment secret, page image, or body is
written to disk or stderr.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import unicodedata


F0E_RUNNER_PATH = "/opt/f0e/runner.py"
F0E_COMPONENT_LOCK_PATH = "/opt/f0e/component-lock.json"
F0H_COMPONENT_LOCK_PATH = "/opt/f0h/component-lock.json"
F0H_REQUIREMENTS_PATH = "/opt/f0h/requirements.lock"
F0H_PYTHON = "/opt/f0h/python"
F0H_WHEELS = "/opt/f0h/wheels"
F0H_MODELS = f"{F0H_PYTHON}/rapidocr/models"

BODY_SCHEMA = "f0f-body-result-v1"
EVIDENCE_SCHEMA = "f0e-result-v1"
MAX_PRIVATE_OUTPUT_BYTES = 8 * 1024 * 1024
FROZEN_F0E_IMAGE_ID = (
    "sha256:afff23f8e469f76e8b94159ccd5a1a4345c12a9c72c95ad150acf51c8c86085a"
)
FROZEN_F0E_PROFILE_SHA256 = (
    "8b79ddd2e30708f15f72493bbba937a005ffe61d39c0da9baade89e264b674b6"
)
NORMALIZATION_RULE = "ocr-text-nfc-lf-v1"
NORMALIZATION_RULE_SHA256 = (
    "2bdd5fa88fb268bb8f2d3334f441699fb461f897a5b04d7680d6a7dfc310d3cc"
)
DET_MODEL = f"{F0H_MODELS}/PP-OCRv6_det_small.onnx"
REC_MODEL = f"{F0H_MODELS}/PP-OCRv6_rec_small.onnx"
CLS_MODEL = f"{F0H_MODELS}/ch_ppocr_mobile_v2.0_cls_mobile.onnx"

PACKAGE_VERSIONS = {
    "antlr4-python3-runtime": "4.9.3",
    "certifi": "2026.7.22",
    "charset-normalizer": "3.4.9",
    "colorlog": "6.12.0",
    "idna": "3.18",
    "omegaconf": "2.3.1",
    "rapidocr": "3.9.2",
    "requests": "2.34.2",
    "urllib3": "2.7.0",
}

CONFIGURATION = {
    "classifier_model_path": CLS_MODEL,
    "detector_model_path": DET_MODEL,
    "engine_type": "onnxruntime",
    "inter_op_num_threads": 1,
    "intra_op_num_threads": 1,
    "language": "ch",
    "max_side_len": 2000,
    "model_root_dir": F0H_MODELS,
    "model_type": "small",
    "ocr_version": "PP-OCRv6",
    "recognizer_model_path": REC_MODEL,
    "return_word_box": False,
}


class PublicError(Exception):
    """An error represented by one stable non-sensitive code."""

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
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")


def _load_json(path: str, schema: str, limit: int = 256 * 1024) -> dict:
    try:
        raw = Path(path).read_bytes()
        if not 1 <= len(raw) <= limit:
            raise ValueError
        value = json.loads(raw.decode("ascii", errors="strict"))
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
        sys.path.insert(0, F0H_PYTHON)
        return module
    except Exception as exc:
        raise PublicError("RUNTIME_INTEGRITY_FAILED") from exc


def _expected_profile(seccomp_sha256: str) -> dict:
    return {
        "base_image_id": FROZEN_F0E_IMAGE_ID,
        "base_profile_sha256": FROZEN_F0E_PROFILE_SHA256,
        "cap_drop_all": True,
        "concurrency": 1,
        "container_name_pattern": "^anhuan-f0h-[0-9a-f]{32}$",
        "cpu_limit_millis": 1000,
        "input_envelope_schema": "f0e-envelope-v1",
        "ipc_mode": "none",
        "log_driver": "none",
        "max_header_bytes": 4096,
        "max_ocr_blocks_per_page": 1024,
        "max_ocr_chars_per_page": 1_000_000,
        "max_pdf_pages": 128,
        "max_pixels_per_page": 16_000_000,
        "max_private_output_bytes": MAX_PRIVATE_OUTPUT_BYTES,
        "max_source_bytes": 64 * 1024 * 1024,
        "network_mode": "none",
        "no_new_privileges": True,
        "output_schemas": [EVIDENCE_SCHEMA, BODY_SCHEMA],
        "pids_limit": 64,
        "provider": "ppocrv6-small",
        "pull_policy": "never",
        "read_only_rootfs": True,
        "render_dpi": 250,
        "run_as_user": "65532:65532",
        "runtime_downloads": False,
        "seccomp_sha256": seccomp_sha256,
        "shm_bytes": 67_108_864,
        "timeout_cleanup": "KILL_CLI_THEN_DOCKER_KILL_RM_INSPECT_NOT_FOUND_V1",
        "timeout_seconds": 120,
        "tmpfs_tmp_bytes": 16_777_216,
        "tmpfs_work_bytes": 268_435_456,
        "units_per_execution": 1,
    }


def _verify_runtime(base, lock: dict) -> None:
    try:
        base_lock = base._load_runtime_lock()
        base._verify_runtime(base_lock)
        if (
            lock.get("candidate_status") != "LOCAL_PPOCRV6_RUNTIME_ONLY"
            or lock["base"]["image_id"] != FROZEN_F0E_IMAGE_ID
            or lock["base"]["profile_sha256"] != FROZEN_F0E_PROFILE_SHA256
            or _sha256_file(F0E_COMPONENT_LOCK_PATH)
            != lock["integrity"]["base_component_lock_sha256"]
            or _sha256_file(F0E_RUNNER_PATH)
            != lock["integrity"]["base_runner_sha256"]
            or _sha256_file(__file__) != lock["integrity"]["runner_sha256"]
            or _sha256_file(F0H_REQUIREMENTS_PATH)
            != lock["integrity"]["requirements_lock_sha256"]
            or lock.get("configuration") != CONFIGURATION
            or lock.get("configuration_sha256")
            != _sha256_bytes(_canonical_json(CONFIGURATION))
        ):
            raise ValueError
        profile = _expected_profile(lock["profile"]["seccomp_sha256"])
        if (
            lock.get("profile") != profile
            or lock.get("profile_sha256") != _sha256_bytes(_canonical_json(profile))
        ):
            raise ValueError
        for package, expected in PACKAGE_VERSIONS.items():
            if importlib.metadata.version(package) != expected:
                raise ValueError
        for item in lock["dependencies"]["runtime"]:
            wheel_path = f"{F0H_WHEELS}/{item['filename']}"
            if _sha256_file(wheel_path) != item["sha256"]:
                raise ValueError
        for item in lock["models"]:
            if _sha256_file(item["installed_path"]) != item["sha256"]:
                raise ValueError
        metadata_paths = {
            f"{F0H_PYTHON}/rapidocr/config.yaml": lock["metadata"]["config_sha256"],
            f"{F0H_PYTHON}/rapidocr/default_models.yaml": lock["metadata"][
                "default_models_sha256"
            ],
        }
        for path, expected in metadata_paths.items():
            if _sha256_file(path) != expected:
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


def _adapt_output(output: object) -> list[list[object]]:
    boxes = getattr(output, "boxes", None)
    texts = getattr(output, "txts", None)
    scores = getattr(output, "scores", None)
    if boxes is None and texts is None and scores is None:
        return []
    try:
        if boxes is None or texts is None or scores is None:
            raise ValueError
        if not (len(boxes) == len(texts) == len(scores)):
            raise ValueError
        if len(texts) > 1024:
            raise ValueError
        adapted: list[list[object]] = []
        for index in range(len(texts)):
            raw_box = boxes[index]
            if hasattr(raw_box, "tolist"):
                raw_box = raw_box.tolist()
            points = list(raw_box)
            if len(points) != 4:
                raise ValueError
            box = []
            for raw_point in points:
                point = list(raw_point)
                if len(point) != 2:
                    raise ValueError
                box.append([float(point[0]), float(point[1])])
            adapted.append([box, texts[index], scores[index]])
        return adapted
    except Exception as exc:
        raise PublicError("OCR_RESULT_INVALID") from exc


def _normalize_result_blocks(base, result: object, width: int, height: int) -> list[dict]:
    if not isinstance(result, list) or len(result) > base.MAX_OCR_BLOCKS_PER_PAGE:
        raise PublicError("OCR_RESULT_INVALID")
    blocks: list[dict] = []
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
        total_chars += len(normalized)
        if total_chars > base.MAX_OCR_CHARS_PER_PAGE:
            raise PublicError("OCR_RESULT_INVALID")
        try:
            rounded = base._rounded_box(box, width, height)
            confidence = float(score)
        except base.PublicError as exc:
            raise PublicError(exc.code) from None
        except (TypeError, ValueError) as exc:
            raise PublicError("OCR_RESULT_INVALID") from exc
        if not math.isfinite(confidence):
            raise PublicError("OCR_RESULT_INVALID")
        blocks.append(
            {
                "bbox": rounded,
                "confidence_ppm": min(
                    1_000_000, max(0, int(round(confidence * 1_000_000)))
                ),
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
        from rapidocr import RapidOCR
        from rapidocr.utils.typings import EngineType, ModelType, OCRVersion
    except Exception as exc:
        raise PublicError("RUNTIME_COMPONENT_LOAD_FAILED") from exc

    if header["document_type"] == "PDF":
        image, render_meta = _translate_base_error(
            base, lambda: base._render_pdf(source, header, pdfium)
        )
    else:
        image, render_meta = _translate_base_error(
            base, lambda: base._decode_jpeg(source, header, cv2, np)
        )
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
    params = {
        "Global.log_level": "critical",
        "Global.max_side_len": CONFIGURATION["max_side_len"],
        "Global.model_root_dir": CONFIGURATION["model_root_dir"],
        "Global.return_word_box": CONFIGURATION["return_word_box"],
        "EngineConfig.onnxruntime.intra_op_num_threads": 1,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        "Det.engine_type": EngineType.ONNXRUNTIME,
        "Det.model_type": ModelType.SMALL,
        "Det.ocr_version": OCRVersion.PPOCRV6,
        "Det.model_path": DET_MODEL,
        "Cls.model_path": CLS_MODEL,
        "Rec.engine_type": EngineType.ONNXRUNTIME,
        "Rec.model_type": ModelType.SMALL,
        "Rec.ocr_version": OCRVersion.PPOCRV6,
        "Rec.model_path": REC_MODEL,
    }
    try:
        ocr = RapidOCR(params=params)
        if (
            str(ocr.cfg.Det.model_path) != DET_MODEL
            or str(ocr.cfg.Rec.model_path) != REC_MODEL
            or str(ocr.cfg.Cls.model_path) != CLS_MODEL
            or ocr.cfg.Det.ocr_version.value != "PP-OCRv6"
            or ocr.cfg.Rec.ocr_version.value != "PP-OCRv6"
            or ocr.cfg.Det.model_type.value != "small"
            or ocr.cfg.Rec.model_type.value != "small"
            or ocr.cfg.EngineConfig.onnxruntime.intra_op_num_threads != 1
            or ocr.cfg.EngineConfig.onnxruntime.inter_op_num_threads != 1
        ):
            raise ValueError
        output = ocr(image)
        adapted = _adapt_output(output)
    except Exception as exc:
        raise PublicError("OCR_INFERENCE_FAILED") from exc
    try:
        summary = base._summarize_ocr(adapted, width_px, height_px)
    except base.PublicError as exc:
        raise PublicError(exc.code) from None
    blocks = _normalize_result_blocks(base, adapted, width_px, height_px)
    del adapted
    del output
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
        except BaseException:
            os._exit(2)
    except BaseException:
        try:
            send_pipe.send(("error", "OCR_WORKER_FAILED"))
        except BaseException:
            os._exit(2)
    finally:
        try:
            send_pipe.close()
        except BaseException:
            os._exit(2)


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


def _success(base_lock: dict, lock: dict, header: dict, evidence: dict, mode: str) -> dict:
    renderer = (
        {
            "name": "pypdfium2",
            "version": base_lock["renderer"]["pypdfium2_version"],
            "pdfium_version": base_lock["renderer"]["pdfium_version"],
        }
        if header["document_type"] == "PDF"
        else {"name": "opencv-imdecode", "version": "5.0.0.93"}
    )
    blocks = evidence.pop("blocks")
    result = {
        "schema": BODY_SCHEMA if mode == "body" else EVIDENCE_SCHEMA,
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
        "raw_text_emitted": mode == "body",
        "raw_text_persisted": False,
        "ocr_executed": True,
        "profile_sha256": lock["profile_sha256"],
        "renderer": renderer,
        "ocr_engine": {
            "config_sha256": lock["configuration_sha256"],
            "model_bundle_sha256": lock["model_bundle_sha256"],
            "model_type": "small",
            "name": "rapidocr",
            "ocr_version": "PP-OCRv6",
            "onnxruntime_version": base_lock["ocr"]["onnxruntime_version"],
            "provider": "ppocrv6-small",
            "runtime_profile_sha256": lock["profile_sha256"],
            "version": "3.9.2",
        },
        **evidence,
        "temp_residuals": 0,
    }
    if mode == "body":
        result["blocks"] = blocks
    return result


def _error(code: str, mode: str) -> dict:
    return {
        "schema": BODY_SCHEMA if mode == "body" else EVIDENCE_SCHEMA,
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


def _emit(payload: dict, exit_code: int, mode: str) -> None:
    encoded = _canonical_json(payload) + b"\n"
    if len(encoded) > MAX_PRIVATE_OUTPUT_BYTES:
        encoded = _canonical_json(_error("PRIVATE_OUTPUT_LIMIT_EXCEEDED", mode)) + b"\n"
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
    mode = sys.argv[1] if len(sys.argv) == 2 else "body"
    if mode not in {"body", "evidence"}:
        mode = "body"
        _emit(_error("MODE_INVALID", mode), 2, mode)
    base = None
    try:
        lock = _load_json(F0H_COMPONENT_LOCK_PATH, "f0h-component-lock-v1")
        base = _load_f0e_module(lock["integrity"]["base_runner_sha256"])
        _verify_runtime(base, lock)
        _translate_base_error(base, base._clean_temp_roots)
        header, source = _translate_base_error(base, base._read_envelope)
        evidence = _run_worker(base, source, header)
        del source
        _translate_base_error(base, base._clean_temp_roots)
        base_lock = base._load_runtime_lock()
        _emit(_success(base_lock, lock, header, evidence, mode), 0, mode)
    except PublicError as exc:
        if base is not None:
            try:
                base._clean_temp_roots()
            except Exception:
                exc = PublicError("TEMP_CLEANUP_FAILED")
        _emit(_error(exc.code, mode), 2, mode)
    except Exception:
        error_code = "INTERNAL_ERROR"
        if base is not None:
            try:
                base._clean_temp_roots()
            except Exception:
                error_code = "TEMP_CLEANUP_FAILED"
        _emit(_error(error_code, mode), 2, mode)


if __name__ == "__main__":
    main()
