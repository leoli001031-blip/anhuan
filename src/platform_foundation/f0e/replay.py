"""Deterministic replay aggregation for F0-E routes and terminal evidence."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import uuid

from .contracts import (
    DeferredDocumentRoute,
    F0EError,
    OcrPageEvidence,
    OcrRunEnvelope,
    PageRoute,
)
from .hashing import canonical_sha256, stable_uuid4


@dataclass(frozen=True, slots=True)
class ReplayAggregate:
    processing_plans: int
    visual_units: int
    native_references: int
    local_ocr_routes: int
    manual_review_source_routes: int
    deferred_documents: int
    native_terminal_evidence: int
    local_ocr_terminal_evidence: int
    manual_review_terminal_evidence: int
    replay_sha256: str

    def counts(self) -> dict[str, int]:
        return {
            "processing_plans": self.processing_plans,
            "visual_units": self.visual_units,
            "native_references": self.native_references,
            "local_ocr_routes": self.local_ocr_routes,
            "manual_review_source_routes": self.manual_review_source_routes,
            "deferred_documents": self.deferred_documents,
            "native_terminal_evidence": self.native_terminal_evidence,
            "local_ocr_terminal_evidence": self.local_ocr_terminal_evidence,
            "manual_review_terminal_evidence": self.manual_review_terminal_evidence,
        }


FULL_ROUTE_EXPECTATION = {
    "processing_plans": 24,
    "visual_units": 249,
    "native_references": 225,
    "local_ocr_routes": 24,
    "manual_review_source_routes": 0,
    "deferred_documents": 2,
}


def aggregate_replay(
    routes: Iterable[PageRoute],
    deferred: Iterable[DeferredDocumentRoute],
    evidence: Iterable[OcrPageEvidence] = (),
) -> ReplayAggregate:
    try:
        ordered_routes = tuple(
            sorted(
                tuple(routes),
                key=lambda item: (
                    str(item.processing_plan_id),
                    item.unit_ordinal,
                    str(item.processing_unit_id),
                ),
            )
        )
        ordered_deferred = tuple(
            sorted(tuple(deferred), key=lambda item: str(item.processing_plan_id))
        )
        ordered_evidence = tuple(
            sorted(tuple(evidence), key=lambda item: str(item.processing_unit_id))
        )
    except (TypeError, AttributeError):
        raise F0EError("REPLAY_MISMATCH") from None
    if any(not isinstance(item, PageRoute) for item in ordered_routes) or any(
        not isinstance(item, DeferredDocumentRoute) for item in ordered_deferred
    ) or any(not isinstance(item, OcrPageEvidence) for item in ordered_evidence):
        raise F0EError("REPLAY_MISMATCH")

    unit_ids = [item.processing_unit_id for item in ordered_routes]
    source_ids = [item.source_unit_id for item in ordered_routes]
    deferred_plans = [item.processing_plan_id for item in ordered_deferred]
    route_plans = {item.processing_plan_id for item in ordered_routes}
    if (
        len(set(unit_ids)) != len(unit_ids)
        or len(set(source_ids)) != len(source_ids)
        or len(set(deferred_plans)) != len(deferred_plans)
        or route_plans.intersection(deferred_plans)
    ):
        raise F0EError("REPLAY_MISMATCH")
    if sum(
        item.evidence_method
        in {"NATIVE_REFERENCE", "LOCAL_OCR", "MANUAL_REVIEW_REFERENCE"}
        for item in ordered_routes
    ) != len(ordered_routes):
        raise F0EError("REPLAY_MISMATCH")

    evidence_by_unit = {item.processing_unit_id: item for item in ordered_evidence}
    if len(evidence_by_unit) != len(ordered_evidence):
        raise F0EError("REPLAY_MISMATCH")
    route_by_unit = {item.processing_unit_id: item for item in ordered_routes}
    for unit_id, item in evidence_by_unit.items():
        route = route_by_unit.get(unit_id)
        if route is None or (
            item.source_unit_id != route.source_unit_id
            or item.candidate_decision != route.candidate_decision
            or item.selected_route != route.evidence_method
            or item.source_evidence_sha256 != route.source_evidence_sha256
        ):
            raise F0EError("REPLAY_MISMATCH")

    plans = route_plans.union(deferred_plans)
    material = {
        "deferred": [item.route_sha256 for item in ordered_deferred],
        "evidence": [
            {
                "evidence_id": str(item.evidence_id),
                "terminal_status": item.terminal_status,
                "payload": item.to_finalize_payload(),
            }
            for item in ordered_evidence
        ],
        "routes": [item.route_sha256 for item in ordered_routes],
    }
    return ReplayAggregate(
        processing_plans=len(plans),
        visual_units=len(ordered_routes),
        native_references=sum(
            item.evidence_method == "NATIVE_REFERENCE" for item in ordered_routes
        ),
        local_ocr_routes=sum(
            item.evidence_method == "LOCAL_OCR" for item in ordered_routes
        ),
        manual_review_source_routes=sum(
            item.evidence_method == "MANUAL_REVIEW_REFERENCE"
            for item in ordered_routes
        ),
        deferred_documents=len(ordered_deferred),
        native_terminal_evidence=sum(
            item.terminal_status == "NATIVE_REFERENCE" for item in ordered_evidence
        ),
        local_ocr_terminal_evidence=sum(
            item.terminal_status == "LOCAL_OCR_EVIDENCE"
            for item in ordered_evidence
        ),
        manual_review_terminal_evidence=sum(
            item.terminal_status == "MANUAL_REVIEW_REQUIRED"
            for item in ordered_evidence
        ),
        replay_sha256=canonical_sha256(material),
    )


def verify_replay(
    aggregate: ReplayAggregate,
    expected: Mapping[str, int] | None = None,
) -> ReplayAggregate:
    if not isinstance(aggregate, ReplayAggregate):
        raise F0EError("REPLAY_MISMATCH")
    target = FULL_ROUTE_EXPECTATION if expected is None else expected
    actual = aggregate.counts()
    try:
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or actual.get(key) != value
            for key, value in target.items()
        ):
            raise F0EError("REPLAY_MISMATCH")
    except (AttributeError, TypeError):
        raise F0EError("REPLAY_MISMATCH") from None
    if (
        aggregate.native_terminal_evidence != aggregate.native_references
        or aggregate.local_ocr_terminal_evidence
        + aggregate.manual_review_terminal_evidence
        != aggregate.local_ocr_routes
        or aggregate.native_terminal_evidence
        + aggregate.local_ocr_terminal_evidence
        + aggregate.manual_review_terminal_evidence
        != aggregate.visual_units
    ):
        raise F0EError("REPLAY_MISMATCH")
    return aggregate


def assemble_run_envelope(
    execution: object,
    ocr_evidence: Iterable[OcrPageEvidence],
    *,
    run_id: uuid.UUID | None = None,
) -> OcrRunEnvelope:
    """Combine native references and local results without mutating F0-C."""

    required = (
        "processing_plan_id",
        "configuration",
        "input_version",
        "routes",
        "deferred_document",
        "native_evidence",
    )
    if any(not hasattr(execution, name) for name in required):
        raise F0EError("REPLAY_MISMATCH")
    identifier = (
        stable_uuid4(
            "local-ocr-run",
            execution.lease.job_id,
            execution.lease.generation,
            execution.lease.token,
        )
        if run_id is None
        else run_id
    )
    if not isinstance(identifier, uuid.UUID):
        raise F0EError("REPLAY_MISMATCH")
    if execution.deferred_document is not None:
        supplied = tuple(ocr_evidence)
        if supplied or execution.routes:
            raise F0EError("REPLAY_MISMATCH")
        return OcrRunEnvelope(
            run_id=identifier,
            processing_plan_id=execution.processing_plan_id,
            configuration_id=execution.configuration.configuration_id,
            input_version=execution.input_version,
            status="DEFERRED_CONVERSION_REQUIRED",
            page_evidence=(),
            deferred_documents=(execution.deferred_document,),
        )
    supplied = tuple(ocr_evidence)
    expected_ocr_ids = {
        route.processing_unit_id
        for route in execution.routes
        if route.evidence_method == "LOCAL_OCR"
    }
    if (
        len({item.processing_unit_id for item in supplied}) != len(supplied)
        or {item.processing_unit_id for item in supplied} != expected_ocr_ids
    ):
        raise F0EError("REPLAY_MISMATCH")
    combined = tuple(execution.native_evidence) + supplied
    order = {
        route.processing_unit_id: route.unit_ordinal for route in execution.routes
    }
    combined = tuple(sorted(combined, key=lambda item: order[item.processing_unit_id]))
    return OcrRunEnvelope(
        run_id=identifier,
        processing_plan_id=execution.processing_plan_id,
        configuration_id=execution.configuration.configuration_id,
        input_version=execution.input_version,
        status="CANDIDATE_EVIDENCE_RECORDED",
        page_evidence=combined,
    )


__all__ = (
    "FULL_ROUTE_EXPECTATION",
    "ReplayAggregate",
    "aggregate_replay",
    "assemble_run_envelope",
    "verify_replay",
)
