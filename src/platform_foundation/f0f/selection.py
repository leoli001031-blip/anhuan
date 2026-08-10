"""Deterministic Fixture-only annotation queue selection."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
import uuid

from ..f0e.hashing import stable_uuid4
from .contracts import AnnotationCandidate, F0FError


def select_annotation_candidates(
    rows: Iterable[Mapping[str, object]],
) -> tuple[AnnotationCandidate, ...]:
    """Select 10 OCR pages round-robin by document and 5 native documents.

    Only rows explicitly labelled ``core`` are eligible.  The function derives
    counts from the supplied evidence rows; no corpus totals are embedded.
    """

    ocr: dict[str, list[tuple[str, uuid.UUID]]] = defaultdict(list)
    native: dict[str, list[tuple[str, uuid.UUID]]] = defaultdict(list)
    seen_units: set[uuid.UUID] = set()
    try:
        for row in rows:
            if row.get("source_group") != "core":
                continue
            unit_id = _uuid(row.get("processing_unit_id"))
            if unit_id in seen_units:
                raise F0FError("BODY_EVIDENCE_MISMATCH")
            seen_units.add(unit_id)
            document_id = _sha(row.get("source_document_id"))
            source_unit_id = _sha(row.get("source_unit_id"))
            route = row.get("selected_route")
            if route == "LOCAL_OCR":
                ocr[document_id].append((source_unit_id, unit_id))
            elif route == "NATIVE_REFERENCE":
                native[document_id].append((source_unit_id, unit_id))
            else:
                raise F0FError("BODY_EVIDENCE_MISMATCH")
    except F0FError:
        raise
    except (AttributeError, TypeError, ValueError):
        raise F0FError("BODY_CONTRACT_INVALID") from None

    for values in (*ocr.values(), *native.values()):
        values.sort(key=lambda item: (item[0], str(item[1])))
    ocr_queues = {
        document_id: deque(values) for document_id, values in sorted(ocr.items())
    }
    selected: list[tuple[uuid.UUID, str]] = []
    while len(selected) < 10 and any(ocr_queues.values()):
        for document_id in sorted(ocr_queues):
            queue = ocr_queues[document_id]
            if queue and len(selected) < 10:
                _, unit_id = queue.popleft()
                selected.append((unit_id, "LOCAL_OCR"))
    if len(selected) != 10 or len(ocr) != 7:
        raise F0FError("BODY_REPLAY_MISMATCH")

    for document_id in sorted(native)[:5]:
        _, unit_id = native[document_id][0]
        selected.append((unit_id, "NATIVE_REFERENCE"))
    if len(selected) != 15:
        raise F0FError("BODY_REPLAY_MISMATCH")

    return tuple(
        AnnotationCandidate(
            queue_id=stable_uuid4("f0f-annotation-queue", unit_id),
            processing_unit_id=unit_id,
            selected_route=route,
            queue_ordinal=ordinal,
        )
        for ordinal, (unit_id, route) in enumerate(selected, start=1)
    )


def _uuid(value: object) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str):
        raise F0FError("BODY_CONTRACT_INVALID")
    try:
        return uuid.UUID(value)
    except ValueError:
        raise F0FError("BODY_CONTRACT_INVALID") from None


def _sha(value: object) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise F0FError("BODY_CONTRACT_INVALID")
    try:
        int(value, 16)
    except ValueError:
        raise F0FError("BODY_CONTRACT_INVALID") from None
    if value != value.lower():
        raise F0FError("BODY_CONTRACT_INVALID")
    return value


__all__ = ("select_annotation_candidates",)
