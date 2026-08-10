"""Strict F0-H protocol adapter and body-evidence validation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import math
import re
import unicodedata


NORMALIZATION_RULE = "ocr-text-nfc-lf-v1"
NORMALIZATION_RULE_SHA256 = (
    "2bdd5fa88fb268bb8f2d3334f441699fb461f897a5b04d7680d6a7dfc310d3cc"
)
MAX_BLOCKS = 1024
MAX_CHARACTERS = 1_000_000
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_PUBLIC_CODES = frozenset(
    {
        "ARTIFACT_GENERATION_FAILED",
        "CONTRACT_INVALID",
        "REPLAY_MISMATCH",
        "RUNNER_CONFIGURATION_INVALID",
        "RUNNER_FAILED",
        "RUNNER_INVOCATION_DENIED",
        "RUNNER_OUTPUT_INVALID",
        "RUNNER_OUTPUT_LIMIT",
        "RUNNER_TIMEOUT",
        "SOURCE_OBJECT_CHANGED",
        "SOURCE_OBJECT_INVALID",
    }
)

_RESULT_KEYS = frozenset(
    {
        "accuracy_claimed",
        "bbox_coordinate_space",
        "bbox_sha256",
        "bbox_union_px",
        "benchmark_tier",
        "blocks",
        "confidence_mean_ppm",
        "confidence_min_ppm",
        "decision",
        "document_type",
        "expected_total_pages",
        "external_calls",
        "external_processing",
        "fixture_label",
        "gold_status",
        "normalization_rule",
        "normalization_rule_sha256",
        "ocr_block_count",
        "ocr_char_count",
        "ocr_engine",
        "ocr_executed",
        "ocr_nonblank_char_count",
        "ocr_text_sha256",
        "page_no",
        "professional_status",
        "profile_sha256",
        "raw_text_emitted",
        "raw_text_persisted",
        "reason_codes",
        "render_dpi",
        "render_height_px",
        "render_origin",
        "render_pixel_format",
        "render_sha256",
        "render_width_px",
        "renderer",
        "schema",
        "source_sha256",
        "source_unit_id",
        "status",
        "temp_residuals",
    }
)


class F0HError(Exception):
    """A fail-closed error whose message never reflects private input."""

    __slots__ = ("code",)

    def __init__(self, code: str):
        safe = code if code in _PUBLIC_CODES else "CONTRACT_INVALID"
        self.code = safe
        super().__init__(safe)

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code}


def canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        raise F0HError("CONTRACT_INVALID") from None


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def adapt_output_parts(
    boxes: object, texts: object, scores: object
) -> tuple[dict[str, object], ...]:
    """Adapt RapidOCROutput parts to the frozen F0-F block semantics."""

    if boxes is None and texts is None and scores is None:
        return ()
    if boxes is None or texts is None or scores is None:
        raise F0HError("RUNNER_OUTPUT_INVALID")
    try:
        box_items = tuple(boxes)  # type: ignore[arg-type]
        text_items = tuple(texts)  # type: ignore[arg-type]
        score_items = tuple(scores)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise F0HError("RUNNER_OUTPUT_INVALID") from None
    if (
        len(box_items) != len(text_items)
        or len(text_items) != len(score_items)
        or len(text_items) > MAX_BLOCKS
    ):
        raise F0HError("RUNNER_OUTPUT_INVALID")

    blocks: list[dict[str, object]] = []
    total_characters = 0
    for index, (raw_box, raw_text, raw_score) in enumerate(
        zip(box_items, text_items, score_items, strict=True)
    ):
        if not isinstance(raw_text, str):
            raise F0HError("RUNNER_OUTPUT_INVALID")
        text = unicodedata.normalize(
            "NFC", raw_text.replace("\r\n", "\n").replace("\r", "\n")
        )
        try:
            text.encode("utf-8", errors="strict")
        except UnicodeError:
            raise F0HError("RUNNER_OUTPUT_INVALID") from None
        total_characters += len(text)
        if total_characters > MAX_CHARACTERS:
            raise F0HError("RUNNER_OUTPUT_INVALID")
        try:
            points = tuple(raw_box)  # type: ignore[arg-type]
        except TypeError:
            raise F0HError("RUNNER_OUTPUT_INVALID") from None
        if len(points) != 4:
            raise F0HError("RUNNER_OUTPUT_INVALID")
        bbox: list[list[int]] = []
        for raw_point in points:
            try:
                point = tuple(raw_point)  # type: ignore[arg-type]
            except TypeError:
                raise F0HError("RUNNER_OUTPUT_INVALID") from None
            if len(point) != 2:
                raise F0HError("RUNNER_OUTPUT_INVALID")
            coordinates: list[int] = []
            for raw_coordinate in point:
                if isinstance(raw_coordinate, bool) or not isinstance(
                    raw_coordinate, (int, float)
                ):
                    raise F0HError("RUNNER_OUTPUT_INVALID")
                coordinate = float(raw_coordinate)
                if not math.isfinite(coordinate) or abs(coordinate) > 1_000_000:
                    raise F0HError("RUNNER_OUTPUT_INVALID")
                coordinates.append(int(round(coordinate)))
            bbox.append(coordinates)
        if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
            raise F0HError("RUNNER_OUTPUT_INVALID")
        score = float(raw_score)
        if not math.isfinite(score) or not 0 <= score <= 1:
            raise F0HError("RUNNER_OUTPUT_INVALID")
        blocks.append(
            {
                "bbox": bbox,
                "confidence_ppm": int(round(score * 1_000_000)),
                "index": index,
                "text": text,
            }
        )
    return tuple(blocks)


def _validated_blocks(value: object) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list) or len(value) > MAX_BLOCKS:
        raise F0HError("RUNNER_OUTPUT_INVALID")
    boxes: list[object] = []
    texts: list[object] = []
    scores: list[float] = []
    for expected_index, block in enumerate(value):
        if not isinstance(block, dict) or set(block) != {
            "bbox",
            "confidence_ppm",
            "index",
            "text",
        }:
            raise F0HError("RUNNER_OUTPUT_INVALID")
        confidence = block.get("confidence_ppm")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, int)
            or not 0 <= confidence <= 1_000_000
            or block.get("index") != expected_index
        ):
            raise F0HError("RUNNER_OUTPUT_INVALID")
        boxes.append(block.get("bbox"))
        texts.append(block.get("text"))
        scores.append(confidence / 1_000_000)
    return adapt_output_parts(boxes, texts, scores)


def summarize_blocks(blocks: Sequence[Mapping[str, object]]) -> dict[str, object]:
    try:
        material = [dict(block) for block in blocks]
    except (TypeError, ValueError):
        raise F0HError("RUNNER_OUTPUT_INVALID") from None
    normalized = _validated_blocks(material)
    text_digest = hashlib.sha256(
        b"F0E_TEXT_SEQUENCE_V1\0" + NORMALIZATION_RULE.encode("ascii") + b"\0"
    )
    bbox_digest = hashlib.sha256(b"F0E_BOX_SEQUENCE_V1\0")
    character_count = 0
    nonblank_count = 0
    confidences: list[int] = []
    bbox_union: list[int] | None = None
    for block in normalized:
        index = int(block["index"])
        text = str(block["text"])
        encoded = text.encode("utf-8", errors="strict")
        character_count += len(text)
        nonblank_count += sum(not character.isspace() for character in text)
        text_digest.update(index.to_bytes(4, "big"))
        text_digest.update(len(encoded).to_bytes(8, "big"))
        text_digest.update(encoded)
        bbox = block["bbox"]
        canonical_bbox = canonical_json_bytes(bbox)
        bbox_digest.update(index.to_bytes(4, "big"))
        bbox_digest.update(len(canonical_bbox).to_bytes(4, "big"))
        bbox_digest.update(canonical_bbox)
        points = list(bbox)  # type: ignore[arg-type]
        xs = [int(point[0]) for point in points]
        ys = [int(point[1]) for point in points]
        current = [min(xs), min(ys), max(xs), max(ys)]
        if current[0] >= current[2] or current[1] >= current[3]:
            raise F0HError("RUNNER_OUTPUT_INVALID")
        if bbox_union is None:
            bbox_union = current
        else:
            bbox_union = [
                min(bbox_union[0], current[0]),
                min(bbox_union[1], current[1]),
                max(bbox_union[2], current[2]),
                max(bbox_union[3], current[3]),
            ]
        confidences.append(int(block["confidence_ppm"]))
    if confidences and nonblank_count > 0:
        minimum: int | None = min(confidences)
        mean: int | None = (sum(confidences) + len(confidences) // 2) // len(
            confidences
        )
    else:
        minimum = None
        mean = None
    return {
        "bbox_coordinate_space": "RENDERED_PIXEL_TOP_LEFT_V1",
        "bbox_sha256": bbox_digest.hexdigest(),
        "bbox_union_px": bbox_union,
        "confidence_mean_ppm": mean,
        "confidence_min_ppm": minimum,
        "normalization_rule": NORMALIZATION_RULE,
        "normalization_rule_sha256": NORMALIZATION_RULE_SHA256,
        "ocr_block_count": len(normalized),
        "ocr_char_count": character_count,
        "ocr_nonblank_char_count": nonblank_count,
        "ocr_text_sha256": text_digest.hexdigest(),
    }


def _engine_identity(bundle: object) -> dict[str, object]:
    try:
        return {
            "config_sha256": bundle.configuration_sha256,
            "model_bundle_sha256": bundle.model_bundle_sha256,
            "model_type": "small",
            "name": "rapidocr",
            "ocr_version": "PP-OCRv6",
            "onnxruntime_version": bundle.onnxruntime_version,
            "provider": "ppocrv6-small",
            "runtime_profile_sha256": bundle.execution_profile_sha256,
            "version": bundle.rapidocr_version,
        }
    except AttributeError:
        raise F0HError("RUNNER_CONFIGURATION_INVALID") from None


def validate_private_result(
    result: object,
    bundle: object,
    expected: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Validate a private body result and recompute every aggregate from blocks."""

    if not isinstance(result, dict) or frozenset(result) != _RESULT_KEYS:
        raise F0HError("RUNNER_OUTPUT_INVALID")
    fixed = {
        "schema": "f0f-body-result-v1",
        "status": "SUCCESS",
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
        "profile_sha256": getattr(bundle, "execution_profile_sha256", None),
        "normalization_rule": NORMALIZATION_RULE,
        "normalization_rule_sha256": NORMALIZATION_RULE_SHA256,
        "render_pixel_format": "BGR24",
        "bbox_coordinate_space": "RENDERED_PIXEL_TOP_LEFT_V1",
        "temp_residuals": 0,
        "ocr_engine": _engine_identity(bundle),
    }
    if any(result.get(key) != value for key, value in fixed.items()):
        raise F0HError("RUNNER_OUTPUT_INVALID")
    if expected is not None and any(
        result.get(key) != value for key, value in expected.items()
    ):
        raise F0HError("RUNNER_OUTPUT_INVALID")
    if result.get("document_type") not in {"PDF", "JPEG"}:
        raise F0HError("RUNNER_OUTPUT_INVALID")
    for key in ("source_sha256", "source_unit_id", "render_sha256"):
        if SHA256_RE.fullmatch(str(result.get(key, ""))) is None:
            raise F0HError("RUNNER_OUTPUT_INVALID")
    dimensions = (result.get("render_width_px"), result.get("render_height_px"))
    if any(isinstance(value, bool) or not isinstance(value, int) for value in dimensions):
        raise F0HError("RUNNER_OUTPUT_INVALID")
    width, height = int(dimensions[0]), int(dimensions[1])
    if width < 1 or height < 1 or width * height > 16_000_000:
        raise F0HError("RUNNER_OUTPUT_INVALID")
    blocks = _validated_blocks(result["blocks"])
    for block in blocks:
        for point in block["bbox"]:  # type: ignore[union-attr]
            x, y = point
            if not 0 <= x <= width or not 0 <= y <= height:
                raise F0HError("RUNNER_OUTPUT_INVALID")
    summary = summarize_blocks(blocks)
    if any(result.get(key) != value for key, value in summary.items()):
        raise F0HError("RUNNER_OUTPUT_INVALID")
    empty = summary["ocr_nonblank_char_count"] == 0
    if empty:
        if result.get("decision") != "MANUAL_REVIEW_REQUIRED" or result.get(
            "reason_codes"
        ) != ["EMPTY_OCR_OUTPUT"]:
            raise F0HError("RUNNER_OUTPUT_INVALID")
    elif result.get("decision") != "OCR_EVIDENCE_CAPTURED_NOT_VALIDATED" or result.get(
        "reason_codes"
    ) != ["OCR_OUTPUT_HASHED", "CONFIDENCE_NOT_CALIBRATED"]:
        raise F0HError("RUNNER_OUTPUT_INVALID")
    return result


__all__ = (
    "F0HError",
    "NORMALIZATION_RULE",
    "NORMALIZATION_RULE_SHA256",
    "adapt_output_parts",
    "canonical_json_bytes",
    "canonical_sha256",
    "summarize_blocks",
    "validate_private_result",
)
