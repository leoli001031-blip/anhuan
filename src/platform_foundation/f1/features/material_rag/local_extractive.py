"""Pure, bounded lexical ranking over already-authorized canonical units."""
from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
import uuid
from collections.abc import Sequence
from typing import Protocol

from .contracts import MaterialEvidence, ScopeKind


MAX_LOCAL_CANDIDATES = 256
_ASCII_TERM_RE = re.compile(r"[a-z0-9]+")
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


class LocalExtractiveIntegrityError(ValueError):
    """One DB-authorized unit failed its canonical body contract."""


class LocalReleasedUnit(Protocol):
    canonical_unit_id: uuid.UUID
    document_record_id: uuid.UUID
    document_version_id: uuid.UUID
    document_name: str
    version_number: int
    source_sha256: str
    page_number: int
    body_sha256: str
    body: str
    scope_kind: ScopeKind


def _normalized(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _features(value: str) -> frozenset[str]:
    normalized = _normalized(value)
    features = {
        term for term in _ASCII_TERM_RE.findall(normalized) if len(term) >= 2
    }
    for run in _CJK_RUN_RE.findall(normalized):
        if len(run) == 1:
            features.add(run)
            continue
        for width in (2, 3):
            features.update(
                run[index : index + width]
                for index in range(0, len(run) - width + 1)
            )
    return frozenset(features)


def _overlap_score(query: frozenset[str], value: str, *, weight: int) -> int:
    return weight * sum(len(item) ** 2 for item in query.intersection(_features(value)))


def rank_local_evidence(
    query: str,
    records: Sequence[LocalReleasedUnit],
    *,
    limit: int,
) -> tuple[MaterialEvidence, ...]:
    """Return deterministic extractive citations without network or generation.

    Authorization, release state, and AEAD verification happen before this pure
    boundary.  The body digest is checked again here so ranking can never use a
    plaintext that is inconsistent with the persisted canonical identity.
    """
    if not isinstance(query, str) or not query.strip() or not 1 <= limit <= 20:
        raise ValueError("MATERIAL_LOCAL_QUERY_INVALID")
    if len(records) > MAX_LOCAL_CANDIDATES:
        raise LocalExtractiveIntegrityError("MATERIAL_LOCAL_CANDIDATE_LIMIT")
    query_text = _normalized(query)
    query_features = _features(query)
    if not query_features:
        return ()

    ranked: list[tuple[int, str, int, str, MaterialEvidence]] = []
    for record in records:
        try:
            body = record.body
            actual_body_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
            if not hmac.compare_digest(actual_body_sha, record.body_sha256):
                raise LocalExtractiveIntegrityError("MATERIAL_LOCAL_BODY_HASH_MISMATCH")
            compact_body = " ".join(body.split())
            if not compact_body:
                raise LocalExtractiveIntegrityError("MATERIAL_LOCAL_BODY_EMPTY")
            score = _overlap_score(query_features, compact_body, weight=4)
            score += _overlap_score(query_features, record.document_name, weight=2)
            normalized_body = _normalized(compact_body)
            normalized_title = _normalized(record.document_name)
            if query_text in normalized_body:
                score += 10_000
            elif query_text in normalized_title:
                score += 5_000
            if score <= 0:
                continue
            evidence = MaterialEvidence(
                canonical_unit_id=record.canonical_unit_id,
                document_record_id=record.document_record_id,
                document_version_id=record.document_version_id,
                document_name=record.document_name,
                version_number=record.version_number,
                source_sha256=record.source_sha256,
                page_number=record.page_number,
                body_sha256=record.body_sha256,
                snippet=compact_body[:320],
                scope_kind=record.scope_kind,
            )
        except LocalExtractiveIntegrityError:
            raise
        except (AttributeError, TypeError, ValueError, UnicodeError):
            raise LocalExtractiveIntegrityError("MATERIAL_LOCAL_UNIT_INVALID") from None
        ranked.append(
            (
                -score,
                str(record.document_version_id),
                record.page_number,
                str(record.canonical_unit_id),
                evidence,
            )
        )
    ranked.sort(key=lambda item: item[:4])
    return tuple(item[4] for item in ranked[:limit])


__all__ = (
    "LocalExtractiveIntegrityError",
    "MAX_LOCAL_CANDIDATES",
    "rank_local_evidence",
)
