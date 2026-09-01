"""Vendor-neutral contracts for material RAG.

The public contracts deliberately contain no RAGFlow dataset/document/chunk
identifiers.  Those values are adapter details and must not cross the product
API boundary.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Literal


ScopeKind = Literal["service_provider", "client"]
JobAction = Literal["index", "rebuild", "delete"]
JobStatus = Literal["queued", "running", "retry_wait", "done", "failed"]

REFUSE_NOT_CONFIGURED = "MATERIAL_RAG_NOT_CONFIGURED"
REFUSE_UNAVAILABLE = "MATERIAL_RAG_UNAVAILABLE"
REFUSE_NO_HITS = "NO_HITS"
REFUSE_REJECTED = "ALL_CANDIDATES_REJECTED"
REFUSE_CONTEXT_INVALID = "MATERIAL_CONTEXT_INVALID"

PARSER_VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class MaterialRagError(RuntimeError):
    """Base class whose messages are fixed, body-free reason codes."""


class MaterialRagContextNotFound(MaterialRagError):
    """The requested product context does not exist."""


class MaterialRagForbidden(MaterialRagError):
    """The requested context is deliberately hidden from this actor."""


class MaterialRagUnavailable(MaterialRagError):
    """The local retrieval or remote embedding adapter is unavailable."""


class MaterialRagRequestConflict(MaterialRagError):
    """The same request was reused with a different tenant, client, or query."""


class MaterialRagIntegrityError(MaterialRagError):
    """Persisted and adapter identities could not be reconciled."""


class MaterialRagLeaseLost(MaterialRagError):
    """A stale worker attempted to continue after losing its lease."""


class SensitiveText:
    """Canonical text whose repr/str never discloses the body."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError("MATERIAL_UNIT_BODY_INVALID")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SensitiveText(<redacted>)"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class CanonicalUnit:
    id: uuid.UUID
    enterprise_id: uuid.UUID
    knowledge_scope_id: uuid.UUID
    document_record_id: uuid.UUID
    document_version_id: uuid.UUID
    source_sha256: str
    page_number: int
    ordinal: int
    parser_version: str
    body: SensitiveText
    body_sha256: str
    ocr_applied: bool = False
    table_candidate: bool = False
    two_column_candidate: bool = False

    def __post_init__(self) -> None:
        if not SHA256_RE.fullmatch(self.source_sha256):
            raise ValueError("MATERIAL_SOURCE_SHA_INVALID")
        if not SHA256_RE.fullmatch(self.body_sha256):
            raise ValueError("MATERIAL_BODY_SHA_INVALID")
        if not PARSER_VERSION_RE.fullmatch(self.parser_version):
            raise ValueError("MATERIAL_PARSER_VERSION_INVALID")
        if self.page_number < 1 or self.ordinal < 0:
            raise ValueError("MATERIAL_UNIT_POSITION_INVALID")
        actual = hashlib.sha256(self.body.reveal().encode("utf-8")).hexdigest()
        if actual != self.body_sha256:
            raise ValueError("MATERIAL_BODY_SHA_MISMATCH")


class RetrievalContext:
    """Resolved product context with private scope identities.

    Only ``kind``, ``client_account_id`` and ``context_sha256`` are intended
    for product-layer use.  The adapter identities stay private even in repr.
    """

    __slots__ = (
        "enterprise_id",
        "kind",
        "client_account_id",
        "context_sha256",
        "_scope_ids",
        "_audience_bound",
    )

    def __init__(
        self,
        *,
        enterprise_id: uuid.UUID,
        kind: ScopeKind,
        client_account_id: uuid.UUID | None,
        scope_ids: tuple[uuid.UUID, ...],
        audience_bound: bool = False,
    ) -> None:
        if kind == "service_provider" and client_account_id is not None:
            raise ValueError("MATERIAL_CONTEXT_INVALID")
        if kind == "client" and client_account_id is None:
            raise ValueError("MATERIAL_CONTEXT_INVALID")
        expected_count = 1 if kind == "service_provider" else 2
        if len(scope_ids) != expected_count or len(set(scope_ids)) != expected_count:
            raise ValueError("MATERIAL_CONTEXT_INVALID")
        self.enterprise_id = enterprise_id
        self.kind = kind
        self.client_account_id = client_account_id
        self._scope_ids = scope_ids
        self._audience_bound = audience_bound is True
        identity = {
                "client_account_id": str(client_account_id) if client_account_id else None,
                "enterprise_id": str(enterprise_id),
                "kind": kind,
                "scope_ids": [str(value) for value in scope_ids],
                "version": 1,
            }
        if self._audience_bound:
            identity["audience_bound"] = True
            identity["version"] = 2
        payload = json.dumps(
            identity,
            sort_keys=True,
            separators=(",", ":"),
        )
        self.context_sha256 = hashlib.sha256(payload.encode("ascii")).hexdigest()

    def __repr__(self) -> str:
        return (
            "RetrievalContext("
            f"kind={self.kind!r}, client_account_id={self.client_account_id!r}, "
            f"context_sha256={self.context_sha256!r}, scopes=<redacted>)"
        )


@dataclass(frozen=True, slots=True, repr=False)
class MaterialEvidence:
    canonical_unit_id: uuid.UUID
    document_record_id: uuid.UUID
    document_version_id: uuid.UUID
    document_name: str
    version_number: int
    source_sha256: str
    page_number: int
    body_sha256: str
    snippet: str
    scope_kind: ScopeKind

    def __post_init__(self) -> None:
        if not SHA256_RE.fullmatch(self.source_sha256):
            raise ValueError("MATERIAL_SOURCE_SHA_INVALID")
        if not SHA256_RE.fullmatch(self.body_sha256):
            raise ValueError("MATERIAL_BODY_SHA_INVALID")
        if (
            not self.document_name
            or len(self.document_name) > 200
            or self.version_number < 1
            or self.page_number < 1
            or not self.snippet
            or len(self.snippet) > 320
        ):
            raise ValueError("MATERIAL_EVIDENCE_INVALID")

    def __repr__(self) -> str:
        return (
            "MaterialEvidence("
            f"canonical_unit_id={self.canonical_unit_id!r}, "
            f"document_record_id={self.document_record_id!r}, "
            f"document_version_id={self.document_version_id!r}, "
            "document_name=<redacted>, "
            f"version_number={self.version_number!r}, "
            f"page_number={self.page_number!r}, scope_kind={self.scope_kind!r}, "
            "snippet=<redacted>)"
        )

    def to_citation_dict(self) -> dict[str, object]:
        """Return the public, vendor-neutral citation fields."""
        return {
            "canonical_unit_id": str(self.canonical_unit_id),
            "document_record_id": str(self.document_record_id),
            "document_version_id": str(self.document_version_id),
            "document_name": self.document_name,
            "version_number": self.version_number,
            "source_sha256": self.source_sha256,
            "page_number": self.page_number,
            "body_sha256": self.body_sha256,
            "snippet": self.snippet,
        }


@dataclass(frozen=True, slots=True)
class MaterialRetrievalResult:
    evidence: tuple[MaterialEvidence, ...]
    refusal_reason: str | None = None

    def __post_init__(self) -> None:
        if bool(self.evidence) == bool(self.refusal_reason):
            raise ValueError("MATERIAL_RETRIEVAL_OUTCOME_INVALID")


@dataclass(frozen=True, slots=True, repr=False)
class MaterialExtractiveAnswer:
    """A local-only answer copied from verified canonical evidence."""

    answer: str | None
    evidence: tuple[MaterialEvidence, ...]
    refusal_reason: str | None = None

    def __post_init__(self) -> None:
        if self.refusal_reason:
            if self.answer is not None or self.evidence:
                raise ValueError("MATERIAL_ANSWER_OUTCOME_INVALID")
            return
        if not self.answer or not self.evidence:
            raise ValueError("MATERIAL_ANSWER_OUTCOME_INVALID")

    def citation_dicts(self) -> list[dict[str, object]]:
        return [item.to_citation_dict() for item in self.evidence]

    def __repr__(self) -> str:
        return (
            "MaterialExtractiveAnswer("
            f"answer={'<redacted>' if self.answer is not None else None}, "
            f"evidence_count={len(self.evidence)}, "
            f"refusal_reason={self.refusal_reason!r})"
        )


@dataclass(frozen=True, slots=True)
class MaterialRagJobClaim:
    id: uuid.UUID
    enterprise_id: uuid.UUID
    knowledge_scope_id: uuid.UUID
    document_record_id: uuid.UUID
    document_version_id: uuid.UUID
    upload_task_id: uuid.UUID
    source_sha256: str
    action: JobAction
    lease_token: uuid.UUID
    attempt: int

    def __post_init__(self) -> None:
        if self.attempt < 1:
            raise ValueError("MATERIAL_JOB_ATTEMPT_INVALID")
        if not SHA256_RE.fullmatch(self.source_sha256):
            raise ValueError("MATERIAL_SOURCE_SHA_INVALID")


@dataclass(frozen=True, slots=True, repr=False)
class DemoUnitManifestProof:
    """Short-lived verifier attestation for one claimed Demo indexing job.

    The signature is deliberately opaque in repr and never crosses the API or
    RAGFlow boundary.  It binds a locally verified source file and an ordered
    canonical-unit manifest to one durable job attempt.
    """

    schema_version: int
    job_id: uuid.UUID
    action: Literal["index", "rebuild"]
    attempt: int
    source_sha256: str
    issued_at_epoch: int
    expires_at_epoch: int
    manifest_sha256: str
    signature_hex: str

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("MATERIAL_RAG_MANIFEST_VERSION_INVALID")
        if self.action not in {"index", "rebuild"} or self.attempt < 1:
            raise ValueError("MATERIAL_RAG_MANIFEST_JOB_INVALID")
        if not SHA256_RE.fullmatch(self.source_sha256):
            raise ValueError("MATERIAL_SOURCE_SHA_INVALID")
        if not SHA256_RE.fullmatch(self.manifest_sha256):
            raise ValueError("MATERIAL_RAG_MANIFEST_SHA_INVALID")
        if not SHA256_RE.fullmatch(self.signature_hex):
            raise ValueError("MATERIAL_RAG_MANIFEST_SIGNATURE_INVALID")
        if self.issued_at_epoch < 1 or self.expires_at_epoch <= self.issued_at_epoch:
            raise ValueError("MATERIAL_RAG_MANIFEST_TIME_INVALID")

    def __repr__(self) -> str:
        return (
            "DemoUnitManifestProof("
            f"job_id={self.job_id!r}, action={self.action!r}, "
            f"attempt={self.attempt!r}, source_sha256={self.source_sha256!r}, "
            f"manifest_sha256={self.manifest_sha256!r}, signature=<redacted>)"
        )


__all__ = (
    "CanonicalUnit",
    "DemoUnitManifestProof",
    "JobAction",
    "JobStatus",
    "MaterialEvidence",
    "MaterialExtractiveAnswer",
    "MaterialRagContextNotFound",
    "MaterialRagError",
    "MaterialRagForbidden",
    "MaterialRagIntegrityError",
    "MaterialRagJobClaim",
    "MaterialRagLeaseLost",
    "MaterialRagRequestConflict",
    "MaterialRagUnavailable",
    "MaterialRetrievalResult",
    "REFUSE_CONTEXT_INVALID",
    "REFUSE_NO_HITS",
    "REFUSE_NOT_CONFIGURED",
    "REFUSE_REJECTED",
    "REFUSE_UNAVAILABLE",
    "RetrievalContext",
    "ScopeKind",
    "SensitiveText",
)
