"""Validated, body-free hand-off from the frozen F0-C native plan."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .catalog import CatalogEntry


NATIVE_PLAN_SHA256 = (
    "08c8a3e972950cd2be88f86a4f79d9727c39f81e12a321c50cb3a46581534436"
)
NATIVE_PLAN_SCHEMA = "fixture-native-page-plan/v1"
NATIVE_RULE_VERSION = "native-page-rule/v1"
_DECISIONS = frozenset(
    {"NATIVE_CANDIDATE", "FULL_PAGE_OCR_REQUIRED", "MANUAL_REVIEW_REQUIRED"}
)


class EvidenceError(RuntimeError):
    """Stable validation error that contains no source content."""

    def __init__(self, code: str = "F0C_EVIDENCE_INVALID") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ProcessingUnit:
    source_unit_id: str
    ordinal: int
    kind: str
    page_no: int
    decision: str
    reason_codes: tuple[str, ...]
    native_text_sha256: str | None
    native_characters: int
    bad_character_ppm: int
    rotation: int | None
    media_box: tuple[str, str, str, str] | None
    crop_box: tuple[str, str, str, str] | None
    width_px: int | None
    height_px: int | None
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class ProcessingEvidence:
    source_document_id: str
    parse_status: str
    visual_units: int
    native_candidates: int
    ocr_candidates: int
    manual_review_candidates: int
    doc_deferred: int
    units: tuple[ProcessingUnit, ...]


def _hex_digest(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise EvidenceError()
    return value


def processing_evidence(entry: CatalogEntry) -> ProcessingEvidence:
    plan = entry.plan
    if (
        plan.get("document_id") != entry.document_id
        or plan.get("group") != entry.group
        or plan.get("line") != entry.line
        or plan.get("type") != entry.document_type
        or plan.get("corpus_role") != entry.corpus_role
        or plan.get("search_publish_allowed") is not False
        or plan.get("current_regulation_allowed") is not False
    ):
        raise EvidenceError()
    parse_status = plan.get("parse_status")
    if not isinstance(parse_status, str) or not parse_status:
        raise EvidenceError()

    raw_pages = plan.get("pages", [])
    if not isinstance(raw_pages, list):
        raise EvidenceError()
    units: list[ProcessingUnit] = []
    for ordinal, raw in enumerate(raw_pages, start=1):
        if not isinstance(raw, dict):
            raise EvidenceError()
        page_no = raw.get("page_no")
        page_id = _hex_digest(raw.get("page_id"))
        decision = raw.get("decision")
        reasons = raw.get("reason_codes")
        if (
            page_no != ordinal
            or decision not in _DECISIONS
            or not isinstance(reasons, list)
            or not reasons
            or any(not isinstance(reason, str) or not reason for reason in reasons)
        ):
            raise EvidenceError()
        native_hash: str | None = None
        native_characters = 0
        bad_character_ppm = 0
        rotation: int | None = None
        media_box: tuple[str, str, str, str] | None = None
        crop_box: tuple[str, str, str, str] | None = None
        width_px: int | None = None
        height_px: int | None = None
        if entry.document_type == "PDF":
            native_hash = _hex_digest(raw.get("native_text_sha256"))
            native_characters = raw.get("native_characters")  # type: ignore[assignment]
            bad_character_ppm = raw.get("bad_character_ppm")  # type: ignore[assignment]
            if (
                not isinstance(native_characters, int)
                or native_characters < 0
                or not isinstance(bad_character_ppm, int)
                or not 0 <= bad_character_ppm <= 1_000_000
            ):
                raise EvidenceError()
            rotation = raw.get("rotation")  # type: ignore[assignment]
            raw_media = raw.get("media_box")
            raw_crop = raw.get("crop_box")
            if rotation not in {0, 90, 180, 270}:
                raise EvidenceError()
            media_box = _box(raw_media)
            crop_box = _box(raw_crop)
        elif entry.document_type != "JPEG":
            raise EvidenceError()
        else:
            width_px = raw.get("width_px")  # type: ignore[assignment]
            height_px = raw.get("height_px")  # type: ignore[assignment]
            if (
                not isinstance(width_px, int)
                or width_px <= 0
                or not isinstance(height_px, int)
                or height_px <= 0
            ):
                raise EvidenceError()
        evidence_sha256 = hashlib.sha256(
            json.dumps(
                raw, ensure_ascii=True, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        units.append(
            ProcessingUnit(
                source_unit_id=page_id,
                ordinal=ordinal,
                kind="PAGE" if entry.document_type == "PDF" else "IMAGE",
                page_no=page_no,
                decision=str(decision),
                reason_codes=tuple(reasons),
                native_text_sha256=native_hash,
                native_characters=native_characters,
                bad_character_ppm=bad_character_ppm,
                rotation=rotation,
                media_box=media_box,
                crop_box=crop_box,
                width_px=width_px,
                height_px=height_px,
                evidence_sha256=evidence_sha256,
            )
        )

    expected_page_count = plan.get("page_count", 0)
    if not isinstance(expected_page_count, int) or expected_page_count != len(units):
        raise EvidenceError()
    doc_deferred = 1 if entry.document_type == "DOC" else 0
    if entry.document_type == "DOC" and parse_status != "DEFERRED_CONVERSION_REQUIRED":
        raise EvidenceError()
    native = sum(unit.decision == "NATIVE_CANDIDATE" for unit in units)
    ocr = sum(unit.decision == "FULL_PAGE_OCR_REQUIRED" for unit in units)
    manual = sum(unit.decision == "MANUAL_REVIEW_REQUIRED" for unit in units)
    if native + ocr + manual != len(units):
        raise EvidenceError()
    return ProcessingEvidence(
        source_document_id=entry.document_id,
        parse_status=parse_status,
        visual_units=len(units),
        native_candidates=native,
        ocr_candidates=ocr,
        manual_review_candidates=manual,
        doc_deferred=doc_deferred,
        units=tuple(units),
    )


def _box(value: object) -> tuple[str, str, str, str]:
    if not isinstance(value, dict):
        raise EvidenceError()
    ordered: list[str] = []
    for key in ("left", "bottom", "right", "top"):
        coordinate = value.get(key)
        if not isinstance(coordinate, str):
            raise EvidenceError()
        try:
            float(coordinate)
        except ValueError:
            raise EvidenceError() from None
        ordered.append(coordinate)
    return (ordered[0], ordered[1], ordered[2], ordered[3])


def aggregate_evidence(entries: tuple[CatalogEntry, ...]) -> dict[str, int]:
    evidence = [processing_evidence(entry) for entry in entries]
    return {
        "documents": len(evidence),
        "visual_units": sum(item.visual_units for item in evidence),
        "native_candidates": sum(item.native_candidates for item in evidence),
        "ocr_candidates": sum(item.ocr_candidates for item in evidence),
        "manual_review_candidates": sum(
            item.manual_review_candidates for item in evidence
        ),
        "doc_deferred": sum(item.doc_deferred for item in evidence),
    }


__all__ = (
    "EvidenceError",
    "NATIVE_PLAN_SCHEMA",
    "NATIVE_PLAN_SHA256",
    "NATIVE_RULE_VERSION",
    "ProcessingEvidence",
    "ProcessingUnit",
    "aggregate_evidence",
    "processing_evidence",
)
