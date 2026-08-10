"""Build a single evidence route for every frozen F0-C visual unit."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import uuid

from .contracts import (
    DeferredDocumentRoute,
    F0EError,
    PageRoute,
    ProcessingUnitRecord,
    OcrPageEvidence,
    SandboxProfile,
)
from .hashing import canonical_sha256, stable_uuid4


_ROUTE_BY_DECISION = {
    "NATIVE_CANDIDATE": "NATIVE_REFERENCE",
    "FULL_PAGE_OCR_REQUIRED": "LOCAL_OCR",
    "MANUAL_REVIEW_REQUIRED": "MANUAL_REVIEW_REFERENCE",
}


def processing_unit_from_row(row: Mapping[str, object]) -> ProcessingUnitRecord:
    try:
        media = _box_from_row(row, "media")
        crop = _box_from_row(row, "crop")
        return ProcessingUnitRecord(
            processing_unit_id=_uuid(row["id"]),
            processing_plan_id=_uuid(row["processing_plan_id"]),
            source_unit_id=str(row["source_unit_id"]),
            unit_ordinal=_integer(row["unit_ordinal"]),
            unit_kind=str(row["unit_kind"]),
            page_no=_integer(row["page_no"]),
            candidate_decision=str(row["candidate_decision"]),
            reason_codes=_reason_codes(row["reason_codes"]),
            evidence_sha256=str(row["evidence_sha256"]),
            native_text_sha256=_optional_text(row.get("native_text_sha256")),
            native_characters=_integer(row.get("native_characters", 0)),
            bad_character_ppm=_integer(row.get("bad_character_ppm", 0)),
            rotation=_optional_integer(row.get("rotation")),
            media_box=media,
            crop_box=crop,
            width_px=_optional_integer(row.get("width_px")),
            height_px=_optional_integer(row.get("height_px")),
            expected_total_pages=_integer(row.get("expected_total_pages", 1)),
        )
    except (KeyError, TypeError, ValueError, F0EError):
        raise F0EError("ROUTE_INVALID") from None


def build_page_routes(
    units: Iterable[ProcessingUnitRecord],
) -> tuple[PageRoute, ...]:
    try:
        ordered = sorted(tuple(units), key=lambda unit: unit.unit_ordinal)
    except (TypeError, AttributeError):
        raise F0EError("ROUTE_INVALID") from None
    if not ordered:
        return ()

    plan_id = ordered[0].processing_plan_id
    seen_unit_ids: set[uuid.UUID] = set()
    seen_source_ids: set[str] = set()
    seen_ordinals: set[int] = set()
    routes: list[PageRoute] = []
    for unit in ordered:
        if (
            not isinstance(unit, ProcessingUnitRecord)
            or unit.processing_plan_id != plan_id
            or unit.processing_unit_id in seen_unit_ids
            or unit.source_unit_id in seen_source_ids
            or unit.unit_ordinal in seen_ordinals
        ):
            raise F0EError("ROUTE_DUPLICATE")
        seen_unit_ids.add(unit.processing_unit_id)
        seen_source_ids.add(unit.source_unit_id)
        seen_ordinals.add(unit.unit_ordinal)

        method = _ROUTE_BY_DECISION.get(unit.candidate_decision)
        if method is None:
            raise F0EError("ROUTE_INVALID")
        material = {
            "candidate_decision": unit.candidate_decision,
            "crop_box": unit.crop_box,
            "evidence_method": method,
            "height_px": unit.height_px,
            "media_box": unit.media_box,
            "native_bad_character_ppm": unit.bad_character_ppm,
            "native_characters": unit.native_characters,
            "native_text_sha256": unit.native_text_sha256,
            "page_no": unit.page_no,
            "processing_plan_id": str(unit.processing_plan_id),
            "processing_unit_id": str(unit.processing_unit_id),
            "reason_codes": unit.reason_codes,
            "rotation": unit.rotation,
            "source_evidence_sha256": unit.evidence_sha256,
            "source_unit_id": unit.source_unit_id,
            "unit_kind": unit.unit_kind,
            "unit_ordinal": unit.unit_ordinal,
            "width_px": unit.width_px,
            "expected_total_pages": unit.expected_total_pages,
        }
        routes.append(
            PageRoute(
                processing_unit_id=unit.processing_unit_id,
                processing_plan_id=unit.processing_plan_id,
                source_unit_id=unit.source_unit_id,
                unit_ordinal=unit.unit_ordinal,
                unit_kind=unit.unit_kind,
                page_no=unit.page_no,
                candidate_decision=unit.candidate_decision,
                evidence_method=method,
                reason_codes=unit.reason_codes,
                source_evidence_sha256=unit.evidence_sha256,
                route_sha256=canonical_sha256(material),
                native_text_sha256=unit.native_text_sha256,
                native_characters=unit.native_characters,
                native_bad_character_ppm=unit.bad_character_ppm,
                rotation=unit.rotation,
                media_box=unit.media_box,
                crop_box=unit.crop_box,
                width_px=unit.width_px,
                height_px=unit.height_px,
                expected_total_pages=unit.expected_total_pages,
            )
        )
    return tuple(routes)


def build_deferred_route(
    processing_plan_id: uuid.UUID,
    document_version_id: uuid.UUID,
    reason_codes: tuple[str, ...] = ("DEFERRED_CONVERSION_REQUIRED",),
) -> DeferredDocumentRoute:
    material = {
        "document_version_id": str(document_version_id),
        "evidence_method": "NO_CONVERSION_EXECUTED",
        "processing_plan_id": str(processing_plan_id),
        "reason_codes": reason_codes,
    }
    return DeferredDocumentRoute(
        processing_plan_id=processing_plan_id,
        document_version_id=document_version_id,
        reason_codes=reason_codes,
        evidence_method="NO_CONVERSION_EXECUTED",
        route_sha256=canonical_sha256(material),
    )


def native_reference_evidence(
    route: PageRoute, profile: SandboxProfile
) -> OcrPageEvidence:
    if (
        not isinstance(route, PageRoute)
        or not isinstance(profile, SandboxProfile)
        or route.evidence_method != "NATIVE_REFERENCE"
        or route.candidate_decision != "NATIVE_CANDIDATE"
        or route.native_text_sha256 is None
        or route.native_text_sha256 == "0" * 64
    ):
        raise F0EError("EVIDENCE_MISMATCH")
    return OcrPageEvidence(
        evidence_id=stable_uuid4(
            "page-evidence",
            route.processing_plan_id,
            route.processing_unit_id,
            profile.execution_profile_sha256,
            route.native_text_sha256,
        ),
        processing_unit_id=route.processing_unit_id,
        source_unit_id=route.source_unit_id,
        candidate_decision=route.candidate_decision,
        selected_route="NATIVE_REFERENCE",
        terminal_status="NATIVE_REFERENCE",
        source_evidence_sha256=route.source_evidence_sha256,
        render_sha256=None,
        output_sha256=route.native_text_sha256,
        output_block_count=0,
        output_character_count=route.native_characters,
        output_non_blank_characters=route.native_characters,
        mean_confidence_ppm=None,
        bbox_summary_sha256=None,
        reason_code="NATIVE_TEXT_REFERENCE_SELECTED",
        execution_profile_sha256=profile.execution_profile_sha256,
    )


def _uuid(value: object) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str):
        raise ValueError
    return uuid.UUID(value)


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError
    return value


def _optional_integer(value: object) -> int | None:
    if value is None:
        return None
    return _integer(value)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError
    return value


def _reason_codes(value: object) -> tuple[str, ...]:
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    raise ValueError


def _box_from_row(
    row: Mapping[str, object], prefix: str
) -> tuple[str, str, str, str] | None:
    values = tuple(
        row.get(f"{prefix}_{coordinate}")
        for coordinate in ("left", "bottom", "right", "top")
    )
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise ValueError
    return tuple(str(value) for value in values)  # type: ignore[return-value]


__all__ = (
    "build_deferred_route",
    "build_page_routes",
    "native_reference_evidence",
    "processing_unit_from_row",
)
