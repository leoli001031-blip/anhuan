"""One-shot verifier for the four explicitly authorized Demo PDFs.

The verifier is intentionally part of the isolated ``material-rag`` Compose
project.  It has no browser surface, no shared-stack connection, no Ark key,
and no route that accepts arbitrary query text.  Source names and text bodies
are kept in memory and are never written to stdout, stderr, or RAGFlow
metadata.
"""
from __future__ import annotations

import asyncio
import contextlib
import errno
import hashlib
import io
import importlib.metadata
import json
import math
import os
import re
import select
import stat
import sys
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

CORE_MANIFEST_SHA256 = (
    "e9425d59207461b9ff87c601016932653b8cd3522b7d70ede877ebb5ce6316ae"
)
NATIVE_PLAN_SHA256 = (
    "08c8a3e972950cd2be88f86a4f79d9727c39f81e12a321c50cb3a46581534436"
)
PYPDF_VERSION = "6.14.2"
NATIVE_PARSER_VERSION = "pypdf-6.14.2"
OCR_PARSER_VERSION = "f0h-ppocrv6-3.9.2"
EMPLOYEE_SUB = "3247dddb-69bc-4ad1-841c-8fc338b603ce"
CONSULTANT_SUB = "7e9978c7-106f-4221-a6d7-79e8104a659b"
TENANT_B_SUB = "ddc4e27e-ccde-4c89-958f-798fc8f30175"
MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_FIXTURE_CONTRACT_BYTES = 16 * 1024 * 1024
MAX_OCR_ENVELOPE_BYTES = 64 * 1024 * 1024
MAX_OCR_RESPONSE_BYTES = 8 * 1024 * 1024
OCR_DEADLINE_SECONDS = 140.0
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_LINE_RE = re.compile(r"([0-9a-f]{64})  (.+)\Z")
SAFE_BOX_RE = re.compile(r"^-?[0-9]{1,9}\.[0-9]{3}$")
OPAQUE_OBJECT_RE = re.compile(r"^[0-9a-f]{32}\.pdf$")
PREVIEW_OBJECT_RE = re.compile(r"^[0-9a-f]{32}/[0-9a-f]{32}\.(?:json|jpg)$")
ARK_AUTHORIZATION_FILE = Path(
    "/run/material-rag-authorization/body-sha256.json"
)
ARK_AUTHORIZATION_OWNER = 65532
ARK_AUTHORIZATION_SCHEMA = "anhuan-material-rag-body-authorization-v1"
ARK_AUTHORIZATION_DUMMY_NAMESPACE = uuid.UUID(
    "8ba2113c-b682-4f6e-a056-1b3b647e22f1"
)
ARK_AUTHORIZATION_DUMMY_ENTERPRISE_ID = uuid.UUID(
    "8ba2113c-b682-4f6e-a056-1b3b647e2201"
)
ARK_AUTHORIZATION_DUMMY_SCOPE_ID = uuid.UUID(
    "8ba2113c-b682-4f6e-a056-1b3b647e2202"
)

F0H_BUNDLE = SimpleNamespace(
    execution_profile_sha256=(
        "9c320d4d978fe2d0d5a69f6411b58744a203600909d1171a49aed700de8a4440"
    ),
    configuration_sha256=(
        "30b615e7c21b144d12434df5cd1d867317eeef341b80ef3adadef49a5bec626f"
    ),
    model_bundle_sha256=(
        "eb97addf62fa9cb149229a9c061e8d91fda3c8976c1a9a894b2c88ae65ba888f"
    ),
    rapidocr_version="3.9.2",
    ocr_family="PP-OCRv6",
    onnxruntime_version="1.28.0",
)


@dataclass(frozen=True, slots=True)
class FixtureSpec:
    line: int
    source_sha256: str
    page_count: int
    ocr_pages: frozenset[int]

    @property
    def path(self) -> Path:
        root = Path(os.environ.get("F1_MATERIAL_RAG_DEMO_ROOT", "/demo"))
        return root / f"{self.source_sha256}.pdf"


FIXTURES = (
    FixtureSpec(
        1,
        "e64cb41465eaf3fc550dbc881c06d687275a8d2b6850f34c703c111a4a3cfc46",
        49,
        frozenset(),
    ),
    FixtureSpec(
        2,
        "ab242c22f92e73d519c5e5485df7027ad33812e96324943b6591171d0e41fc07",
        5,
        frozenset(range(1, 6)),
    ),
    FixtureSpec(
        19,
        "12f20a5a1edf14eb18a77553740b8ab18e49dd7b2c95dcfc3ce22954ea206860",
        65,
        frozenset({2}),
    ),
    FixtureSpec(
        21,
        "973e6ac91e95489a6b8311a9ca61a1a734b6f3ef08f3b3b6d4713d4b04c4dd0e",
        17,
        frozenset(),
    ),
)
EXPECTED_SOURCE_SHA256 = frozenset(spec.source_sha256 for spec in FIXTURES)

FAILURE_REASONS = frozenset(
    {
        "LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED",
        "LOCAL_MATERIAL_RAG_CLEANUP_FAILED",
        "LOCAL_MATERIAL_RAG_DELETE_FAILED",
        "LOCAL_MATERIAL_RAG_EGRESS_AUDIT_FAILED",
        "LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED",
        "LOCAL_MATERIAL_RAG_INDEX_FAILED",
        "LOCAL_MATERIAL_RAG_INTERNAL_ERROR",
        "LOCAL_MATERIAL_RAG_OCR_FAILED",
        "LOCAL_MATERIAL_RAG_P3_CRM_FAILED",
        "LOCAL_MATERIAL_RAG_P3_FAILED",
        "LOCAL_MATERIAL_RAG_P3_PREVIEW_FAILED",
        "LOCAL_MATERIAL_RAG_P3_RELEASE_FAILED",
        "LOCAL_MATERIAL_RAG_P3_SCAN_CONNECT_FAILED",
        "LOCAL_MATERIAL_RAG_P3_SCAN_CONNECT_PIPE_FAILED",
        "LOCAL_MATERIAL_RAG_P3_SCAN_CONNECT_REFUSED_FAILED",
        "LOCAL_MATERIAL_RAG_P3_SCAN_CONNECT_RESET_FAILED",
        "LOCAL_MATERIAL_RAG_P3_SCAN_DNS_FAILED",
        "LOCAL_MATERIAL_RAG_P3_SCAN_ENGINE_FAILED",
        "LOCAL_MATERIAL_RAG_P3_SCAN_ERROR_FAILED",
        "LOCAL_MATERIAL_RAG_P3_SCAN_FAILED",
        "LOCAL_MATERIAL_RAG_P3_SCAN_INCOMPLETE_FAILED",
        "LOCAL_MATERIAL_RAG_P3_SCAN_INFECTED_FAILED",
        "LOCAL_MATERIAL_RAG_P3_SCAN_PROTOCOL_FAILED",
        "LOCAL_MATERIAL_RAG_P3_SCAN_REFUSED_FAILED",
        "LOCAL_MATERIAL_RAG_P3_SCAN_STREAM_PIPE_FAILED",
        "LOCAL_MATERIAL_RAG_P3_SCAN_STREAM_REFUSED_FAILED",
        "LOCAL_MATERIAL_RAG_P3_SCAN_STREAM_RESET_FAILED",
        "LOCAL_MATERIAL_RAG_P3_SCAN_TARGET_FAILED",
        "LOCAL_MATERIAL_RAG_P3_SCAN_TIMEOUT_FAILED",
        "LOCAL_MATERIAL_RAG_P3_SCAN_UNAVAILABLE_FAILED",
        "LOCAL_MATERIAL_RAG_P3_SCAN_VERSION_PIPE_FAILED",
        "LOCAL_MATERIAL_RAG_P3_SCAN_VERSION_REFUSED_FAILED",
        "LOCAL_MATERIAL_RAG_P3_SCAN_VERSION_RESET_FAILED",
        "LOCAL_MATERIAL_RAG_P3_UPLOAD_FAILED",
        "LOCAL_MATERIAL_RAG_P3_UPLOAD_HTTP_FAILED",
        "LOCAL_MATERIAL_RAG_REBUILD_FAILED",
        "LOCAL_MATERIAL_RAG_RETRIEVAL_FAILED",
        "LOCAL_MATERIAL_RAG_RLS_FAILED",
        "LOCAL_MATERIAL_RAG_SEED_FAILED",
        "LOCAL_MATERIAL_RAG_SOURCE_FAILED",
        "LOCAL_MATERIAL_RAG_STORAGE_FAILED",
    }
)


class MaterialRagVerifyError(RuntimeError):
    """A fixed, body-free verification failure."""

    def __init__(
        self, reason: str, *, evidence: dict[str, object] | None = None
    ) -> None:
        safe = reason if reason in FAILURE_REASONS else "LOCAL_MATERIAL_RAG_INTERNAL_ERROR"
        self.reason = safe
        self.evidence = evidence if isinstance(evidence, dict) else None
        super().__init__(safe)


_P3_PREVIEW_EVIDENCE_LINES = frozenset({1, 2, 19, 21})
_P3_PREVIEW_EVIDENCE_STATUSES = frozenset(
    {"blocked", "failed", "generating", "queued", "ready"}
)
_P3_PREVIEW_EVIDENCE_REASON_RE = re.compile(r"P3_[A-Z0-9_]{1,64}\Z")


def _print_p3_preview_evidence(evidence: dict[str, object] | None) -> None:
    if not isinstance(evidence, dict):
        return
    fixture_line = evidence.get("fixture_line")
    if fixture_line not in _P3_PREVIEW_EVIDENCE_LINES:
        fixture_line = 0
    preview_status = evidence.get("preview_status")
    if preview_status not in _P3_PREVIEW_EVIDENCE_STATUSES:
        preview_status = "unknown"
    error_reason = evidence.get("error_reason")
    if not isinstance(error_reason, str) or (
        _P3_PREVIEW_EVIDENCE_REASON_RE.fullmatch(error_reason) is None
    ):
        error_reason = "OTHER" if error_reason else "MISSING"
    print(
        "LOCAL_MATERIAL_RAG_P3_PREVIEW_EVIDENCE "
        + json.dumps(
            {
                "error_reason": error_reason,
                "fixture_line": fixture_line,
                "preview_status": preview_status,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ),
        file=sys.stderr,
        flush=True,
    )


_P3_SCAN_FAILURE_BY_CODE = {
    "P3_SCANNER_UNAVAILABLE": "LOCAL_MATERIAL_RAG_P3_SCAN_CONNECT_FAILED",
    "P3_SCANNER_DNS_FAILED": "LOCAL_MATERIAL_RAG_P3_SCAN_DNS_FAILED",
    "P3_SCANNER_REFUSED": "LOCAL_MATERIAL_RAG_P3_SCAN_REFUSED_FAILED",
    "P3_SCANNER_CONNECT_REFUSED": "LOCAL_MATERIAL_RAG_P3_SCAN_CONNECT_REFUSED_FAILED",
    "P3_SCANNER_CONNECT_RESET": "LOCAL_MATERIAL_RAG_P3_SCAN_CONNECT_RESET_FAILED",
    "P3_SCANNER_CONNECT_PIPE": "LOCAL_MATERIAL_RAG_P3_SCAN_CONNECT_PIPE_FAILED",
    "P3_SCANNER_VERSION_REFUSED": "LOCAL_MATERIAL_RAG_P3_SCAN_VERSION_REFUSED_FAILED",
    "P3_SCANNER_VERSION_RESET": "LOCAL_MATERIAL_RAG_P3_SCAN_VERSION_RESET_FAILED",
    "P3_SCANNER_VERSION_PIPE": "LOCAL_MATERIAL_RAG_P3_SCAN_VERSION_PIPE_FAILED",
    "P3_SCANNER_STREAM_REFUSED": "LOCAL_MATERIAL_RAG_P3_SCAN_STREAM_REFUSED_FAILED",
    "P3_SCANNER_STREAM_RESET": "LOCAL_MATERIAL_RAG_P3_SCAN_STREAM_RESET_FAILED",
    "P3_SCANNER_STREAM_PIPE": "LOCAL_MATERIAL_RAG_P3_SCAN_STREAM_PIPE_FAILED",
    "P3_SCANNER_TIMEOUT": "LOCAL_MATERIAL_RAG_P3_SCAN_TIMEOUT_FAILED",
    "P3_SCAN_PROTOCOL_ERROR": "LOCAL_MATERIAL_RAG_P3_SCAN_PROTOCOL_FAILED",
    "P3_SCAN_ENGINE_ERROR": "LOCAL_MATERIAL_RAG_P3_SCAN_ENGINE_FAILED",
    "P3_SCANNER_TARGET_INVALID": "LOCAL_MATERIAL_RAG_P3_SCAN_TARGET_FAILED",
}


def _p3_scan_failure_reason(latest: object, *, internal: str | None = None) -> str:
    if internal in _P3_SCAN_FAILURE_BY_CODE:
        return _P3_SCAN_FAILURE_BY_CODE[internal]
    payload = latest if isinstance(latest, dict) else {}
    status = payload.get("scan_status")
    reason = payload.get("reason_code")
    if status == "infected" or reason == "MALWARE_DETECTED":
        return "LOCAL_MATERIAL_RAG_P3_SCAN_INFECTED_FAILED"
    if status == "unavailable" or reason == "SCAN_ENGINE_UNAVAILABLE":
        return "LOCAL_MATERIAL_RAG_P3_SCAN_UNAVAILABLE_FAILED"
    if status in {"queued", "scanning"}:
        return "LOCAL_MATERIAL_RAG_P3_SCAN_INCOMPLETE_FAILED"
    if status == "error" or reason == "SOURCE_OBJECT_READ_FAILED":
        return "LOCAL_MATERIAL_RAG_P3_SCAN_ERROR_FAILED"
    return "LOCAL_MATERIAL_RAG_P3_SCAN_FAILED"


def _preflight_scanner() -> None:
    from platform_foundation.f1.features.p3.scanner import ScanFailure, scanner_version

    started = time.monotonic()
    last: ScanFailure | None = None
    while time.monotonic() - started < 60:
        try:
            scanner_version(timeout_seconds=10)
            return
        except ScanFailure as error:
            last = error
            if error.code not in {
                "P3_SCANNER_DNS_FAILED",
                "P3_SCANNER_REFUSED",
                "P3_SCANNER_CONNECT_REFUSED",
                "P3_SCANNER_CONNECT_RESET",
                "P3_SCANNER_CONNECT_PIPE",
                "P3_SCANNER_VERSION_REFUSED",
                "P3_SCANNER_VERSION_RESET",
                "P3_SCANNER_VERSION_PIPE",
                "P3_SCANNER_TIMEOUT",
                "P3_SCANNER_UNAVAILABLE",
                "P3_SCAN_PROTOCOL_ERROR",
            }:
                break
            time.sleep(2)
    raise MaterialRagVerifyError(
        _P3_SCAN_FAILURE_BY_CODE.get(
            last.code if last is not None else "",
            "LOCAL_MATERIAL_RAG_P3_SCAN_UNAVAILABLE_FAILED",
        )
    ) from None


class _DiscardText:
    def write(self, value: str) -> int:
        return len(value)

    def flush(self) -> None:
        return None


@dataclass(frozen=True, slots=True, repr=False)
class ParsedPage:
    page_number: int
    parser_version: str
    text: str
    ocr_applied: bool
    table_candidate: bool = False
    two_column_candidate: bool = False


def _layout_hints(
    fragments: Iterable[tuple[float, float, str]],
    *,
    width: float,
    height: float,
    text: str,
) -> tuple[bool, bool]:
    """Return body-free, uncalibrated table/two-column geometry signals."""

    if (
        not math.isfinite(width)
        or not math.isfinite(height)
        or width <= 1
        or height <= 1
    ):
        return False, False
    material = tuple(
        (x, y, value)
        for x, y, value in fragments
        if math.isfinite(x) and math.isfinite(y) and value.strip()
    )
    row_height = max(4.0, height / 132.0)
    rows: dict[int, list[float]] = {}
    for x, y, _value in material:
        rows.setdefault(int(round(y / row_height)), []).append(x)
    grid_rows = 0
    for xs in rows.values():
        separated: list[float] = []
        for value in sorted(xs):
            if not separated or value - separated[-1] >= width * 0.08:
                separated.append(value)
        if len(separated) >= 3:
            grid_rows += 1
    table_candidate = grid_rows >= 3 or len(re.findall(r"\S+\s{2,}\S+", text)) >= 3

    left_y = [y for x, y, value in material if x < width * 0.45 and len(value) >= 2]
    right_y = [y for x, y, value in material if x > width * 0.55 and len(value) >= 2]
    overlap = 0.0
    if left_y and right_y:
        overlap = max(
            0.0,
            min(max(left_y), max(right_y)) - max(min(left_y), min(right_y)),
        ) / height
    two_column_candidate = (
        len(left_y) >= 5 and len(right_y) >= 5 and overlap >= 0.2
    )
    return table_candidate, two_column_candidate


@dataclass(frozen=True, slots=True, repr=False)
class FixtureDocument:
    spec: FixtureSpec
    body: bytes
    pages: tuple[ParsedPage, ...]


@dataclass(frozen=True, slots=True, repr=False)
class UploadedDocument:
    spec: FixtureSpec
    document_record_id: uuid.UUID
    document_version_id: uuid.UUID
    upload_task_id: uuid.UUID
    knowledge_scope_id: uuid.UUID
    object_key: str


@dataclass(frozen=True, slots=True, repr=False)
class SyntheticDocument:
    label: str
    document_record_id: uuid.UUID
    document_version_id: uuid.UUID
    upload_task_id: uuid.UUID
    knowledge_scope_id: uuid.UUID
    source_sha256: str
    body: object


@dataclass(frozen=True, slots=True, repr=False)
class ProductSetup:
    provider_scope_id: uuid.UUID
    client_a_account_id: uuid.UUID
    client_a_scope_id: uuid.UUID
    client_b_account_id: uuid.UUID
    client_b_scope_id: uuid.UUID
    uploads: tuple[UploadedDocument, ...]
    synthetic_documents: tuple[SyntheticDocument, ...]
    held_enqueue_rejection_count: int
    pre_release_remote_zero_snapshot_count: int
    premature_index_count: int
    manual_report_classification_preserved_count: int
    cross_tenant_api_visible_count: int


@dataclass(frozen=True, slots=True)
class RagRun:
    unit_count: int
    synthetic_unit_count: int
    duplicate_unit_count: int
    unit_identity_conflict_rejection_count: int
    index_job_count: int
    index_replay_job_count: int
    synthetic_index_job_count: int
    citation_count: int
    provider_retrieval_hit_count: int
    client_a_scoped_retrieval_hit_count: int
    client_b_retrieval_hit_count: int
    cross_scope_sibling_delete_proof_count: int
    sibling_scope_delete_leak_count: int
    forwarded_embedding_request_count: int
    client_a_indexed_remote_chunk_count: int
    remote_dataset_residual_count: int
    ready_binding_residual_count: int
    binding_secret_residual_count: int


@dataclass(frozen=True, slots=True, repr=False)
class RemoteChunkSnapshot:
    """Body-free exact identity of one hydrated remote chunk."""

    remote_chunk_id: str
    canonical_unit_id: uuid.UUID
    knowledge_scope_id: uuid.UUID
    document_record_id: uuid.UUID
    document_version_id: uuid.UUID
    source_sha256: str
    page_number: int
    body_sha256: str
    content_sha256: str

    def semantic_fingerprint(self) -> tuple[object, ...]:
        return (
            self.canonical_unit_id,
            self.knowledge_scope_id,
            self.document_record_id,
            self.document_version_id,
            self.source_sha256,
            self.page_number,
            self.body_sha256,
            self.content_sha256,
        )


@dataclass(frozen=True, slots=True, repr=False)
class RemoteDocumentSnapshot:
    """Exact physical and logical identity of one remote version document."""

    remote_document_id: str
    document_name: str
    chunks: tuple[RemoteChunkSnapshot, ...]

    def semantic_fingerprint(self) -> tuple[object, ...]:
        return (
            self.document_name,
            tuple(chunk.semantic_fingerprint() for chunk in self.chunks),
        )


@dataclass(frozen=True, slots=True, repr=False)
class RemoteScopeSnapshot:
    """Exact scope dataset snapshot; repr never exposes adapter identities."""

    dataset_ref: str
    dataset_name: str
    documents: tuple[RemoteDocumentSnapshot, ...]

    @property
    def document_count(self) -> int:
        return len(self.documents)

    @property
    def chunk_count(self) -> int:
        return sum(len(document.chunks) for document in self.documents)

    def semantic_fingerprint(self) -> tuple[object, ...]:
        return (
            self.dataset_ref,
            self.dataset_name,
            tuple(document.semantic_fingerprint() for document in self.documents),
        )


@dataclass(frozen=True, slots=True)
class MaterialRagVerificationCounts:
    demo_pdf_count: int
    uploaded_version_count: int
    synthetic_canary_version_count: int
    clean_scan_count: int
    preview_ready_count: int
    released_version_count: int
    held_enqueue_rejection_count: int
    pre_release_remote_zero_snapshot_count: int
    premature_index_count: int
    manual_report_classification_preserved_count: int
    page_count: int
    native_page_count: int
    ocr_page_count: int
    local_ocr_execution_count: int
    ocr_external_call_count: int
    provider_scope_count: int
    client_scope_count: int
    canonical_unit_count: int
    synthetic_canonical_unit_count: int
    external_text_safety_failure_count: int
    duplicate_unit_count: int
    unit_identity_conflict_rejection_count: int
    index_job_count: int
    index_replay_job_count: int
    synthetic_index_job_count: int
    indexed_version_count: int
    client_a_indexed_remote_document_count: int
    client_a_indexed_remote_chunk_count: int
    provider_indexed_remote_document_count: int
    client_b_indexed_remote_document_count: int
    provider_retrieval_hit_count: int
    client_a_scoped_retrieval_hit_count: int
    client_b_retrieval_hit_count: int
    authorized_retrieval_count: int
    citation_count: int
    pre_index_provider_empty_scope_refusal_count: int
    pre_index_client_b_no_hit_count: int
    pre_index_empty_scope_egress_count: int
    freeform_query_rejection_count: int
    context_idempotency_conflict_count: int
    wrong_context_aad_rejection_count: int
    forged_candidate_rejection_count: int
    cross_tenant_api_visible_count: int
    unauthorized_rls_visible_count: int
    synthetic_scope_unauthorized_rls_visible_count: int
    rebuild_job_count: int
    rebuild_mismatch_count: int
    delete_job_count: int
    cross_scope_sibling_delete_proof_count: int
    sibling_scope_delete_leak_count: int
    stale_candidate_leak_count: int
    remote_document_residual_count: int
    remote_chunk_residual_count: int
    remote_dataset_residual_count: int
    ready_binding_residual_count: int
    binding_secret_residual_count: int
    delete_residual_count: int
    external_llm_call_count: int
    egress_rejected_request_count: int
    egress_forwarded_embedding_request_count: int
    object_residual_count: int
    bucket_residual_count: int


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
    )


def _read_regular(
    path: Path, *, expected_sha256: str, maximum_bytes: int
) -> bytes:
    if not path.is_absolute() or path.is_symlink():
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise MaterialRagVerifyError(
            "LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED"
        ) from None
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or not 1 <= before.st_size <= maximum_bytes
        ):
            raise MaterialRagVerifyError(
                "LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED"
            )
        body = bytearray()
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            digest.update(chunk)
            if len(body) > maximum_bytes:
                raise MaterialRagVerifyError(
                    "LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED"
                )
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        _identity(before) != _identity(after)
        or digest.hexdigest() != expected_sha256
    ):
        body[:] = b"\0" * len(body)
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED")
    return bytes(body)


def _manifest_path() -> Path:
    raw = os.environ.get("F1_MATERIAL_RAG_CORE_MANIFEST_FILE", "")
    return Path(raw) if raw else Path("/run/material-rag-fixtures/core-manifest.sha256")


def _native_plan_path() -> Path:
    raw = os.environ.get("F1_MATERIAL_RAG_NATIVE_PLAN_FILE", "")
    return Path(raw) if raw else Path("/run/material-rag-fixtures/full-plan.json")


def _load_fixture_contracts() -> dict[int, Mapping[str, Any]]:
    manifest_body = _read_regular(
        _manifest_path(),
        expected_sha256=CORE_MANIFEST_SHA256,
        maximum_bytes=1024 * 1024,
    )
    try:
        manifest_lines = manifest_body.decode("utf-8", "strict").splitlines()
    except UnicodeDecodeError:
        raise MaterialRagVerifyError(
            "LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED"
        ) from None
    if len(manifest_lines) != 24:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED")
    manifest_entries: list[tuple[str, str]] = []
    for line in manifest_lines:
        match = MANIFEST_LINE_RE.fullmatch(line)
        if match is None:
            raise MaterialRagVerifyError(
                "LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED"
            )
        relative = PurePosixPath(match.group(2))
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise MaterialRagVerifyError(
                "LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED"
            )
        manifest_entries.append((match.group(1), relative.as_posix()))
    if (
        len({value[0] for value in manifest_entries}) != 24
        or len({value[1] for value in manifest_entries}) != 24
        or any(
            manifest_entries[spec.line - 1][0] != spec.source_sha256
            for spec in FIXTURES
        )
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED")

    plan_body = _read_regular(
        _native_plan_path(),
        expected_sha256=NATIVE_PLAN_SHA256,
        maximum_bytes=MAX_FIXTURE_CONTRACT_BYTES,
    )
    try:
        plan = json.loads(plan_body.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MaterialRagVerifyError(
            "LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED"
        ) from None
    if not isinstance(plan, dict) or any(
        plan.get(key) != value
        for key, value in {
            "schema_version": "fixture-native-page-plan/v1",
            "fixture_set_id": "environment-demo-seed",
            "fixture_version": "v0.1",
            "rule_version": "native-page-rule/v1",
            "profile": "full",
            "external_processing": "DENY",
            "ocr_executed": False,
            "raw_text_persisted": False,
            "page_images_persisted": False,
            "benchmark_tier": "NONE",
            "claim_scope": "PIPELINE_REGRESSION_ONLY",
        }.items()
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED")
    if plan.get("parser") != {
        "license_expression": "BSD-3-Clause",
        "name": "pypdf",
        "strict": True,
        "version": PYPDF_VERSION,
    } or plan.get("policy") != {
        "external_processing": "DENY",
        "model_training": "DENY",
        "production_use": "DENY",
        "public_display": "DENY",
    }:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED")
    entries = plan.get("entries")
    if not isinstance(entries, list) or len(entries) != 26:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED")
    selected = _validate_selected_plan_entries(entries)
    return selected


def _valid_box(value: object) -> bool:
    return isinstance(value, dict) and set(value) == {
        "left",
        "bottom",
        "right",
        "top",
    } and all(
        isinstance(value[key], str) and SAFE_BOX_RE.fullmatch(value[key]) is not None
        for key in ("left", "bottom", "right", "top")
    )


def _validate_selected_plan_entries(
    entries: Iterable[object],
) -> dict[int, Mapping[str, Any]]:
    expected_lines = {spec.line for spec in FIXTURES}
    selected = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("group") == "core"
        and entry.get("line") in expected_lines
    ]
    if len(selected) != len(FIXTURES):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED")
    by_line: dict[int, Mapping[str, Any]] = {}
    for raw in selected:
        line = raw.get("line")
        if type(line) is not int or line in by_line:
            raise MaterialRagVerifyError(
                "LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED"
            )
        by_line[line] = raw
    if set(by_line) != expected_lines:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED")
    for spec in FIXTURES:
        entry = by_line[spec.line]
        pages = entry.get("pages")
        if (
            entry.get("type") != "PDF"
            or entry.get("route") != "PDF_NATIVE_OR_OCR_PROBE"
            or entry.get("parse_status") != "NATIVE_PROBE_COMPLETE"
            or entry.get("page_count") != spec.page_count
            or not isinstance(entry.get("document_id"), str)
            or SHA256_RE.fullmatch(str(entry.get("document_id"))) is None
            or not isinstance(pages, list)
            or len(pages) != spec.page_count
        ):
            raise MaterialRagVerifyError(
                "LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED"
            )
        for page_number, page in enumerate(pages, start=1):
            expected_decision = (
                "FULL_PAGE_OCR_REQUIRED"
                if page_number in spec.ocr_pages
                else "NATIVE_CANDIDATE"
            )
            expected_reasons = (
                ["LOW_NATIVE_TEXT"]
                if page_number in spec.ocr_pages
                else ["NATIVE_TEXT_THRESHOLD_MET"]
            )
            if (
                not isinstance(page, dict)
                or page.get("page_no") != page_number
                or page.get("decision") != expected_decision
                or page.get("reason_codes") != expected_reasons
                or not isinstance(page.get("page_id"), str)
                or SHA256_RE.fullmatch(str(page.get("page_id"))) is None
                or not isinstance(page.get("native_text_sha256"), str)
                or SHA256_RE.fullmatch(str(page.get("native_text_sha256"))) is None
                or type(page.get("native_characters")) is not int
                or int(page.get("native_characters", -1)) < 0
                or type(page.get("bad_character_ppm")) is not int
                or not 0 <= int(page.get("bad_character_ppm", -1)) <= 1_000_000
                or type(page.get("rotation")) is not int
                or page.get("rotation") not in {0, 90, 180, 270}
                or not _valid_box(page.get("media_box"))
                or not _valid_box(page.get("crop_box"))
            ):
                raise MaterialRagVerifyError(
                    "LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED"
                )
    return by_line


def _read_demo_source(spec: FixtureSpec) -> bytes:
    root = Path(os.environ.get("F1_MATERIAL_RAG_DEMO_ROOT", "/demo"))
    if not root.is_absolute() or root.is_symlink() or spec.path.parent != root:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_SOURCE_FAILED")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    try:
        root_descriptor = os.open(root, directory_flags)
    except OSError:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_SOURCE_FAILED") from None
    descriptor = -1
    try:
        root_info = os.fstat(root_descriptor)
        if not stat.S_ISDIR(root_info.st_mode):
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_SOURCE_FAILED")
        descriptor = os.open(
            f"{spec.source_sha256}.pdf",
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root_descriptor,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or not 8 <= before.st_size <= MAX_SOURCE_BYTES
        ):
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_SOURCE_FAILED")
        body = bytearray()
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, MAX_SOURCE_BYTES + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            digest.update(chunk)
            if len(body) > MAX_SOURCE_BYTES:
                raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_SOURCE_FAILED")
        after = os.fstat(descriptor)
    except OSError:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_SOURCE_FAILED") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(root_descriptor)
    if (
        _identity(before) != _identity(after)
        or digest.hexdigest() != spec.source_sha256
        or not body.startswith(b"%PDF-")
    ):
        body[:] = b"\0" * len(body)
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_SOURCE_FAILED")
    return bytes(body)


def _native_text_metrics(value: str) -> tuple[int, int, str]:
    import unicodedata

    native = 0
    bad = 0
    for character in value:
        category = unicodedata.category(character)
        invalid = category in {"Cc", "Cf", "Co", "Cs", "Cn"}
        if not character.isspace() and not invalid:
            native += 1
        if character == "\ufffd" or (invalid and character not in "\t\n\r"):
            bad += 1
    ppm = 0 if not value else (bad * 1_000_000) // len(value)
    return native, ppm, hashlib.sha256(value.encode("utf-8")).hexdigest()


def _fifo_path(environment_name: str, fallback: str) -> Path:
    return Path(os.environ.get(environment_name, fallback))


def _validate_ocr_ipc() -> tuple[Path, Path]:
    request = _fifo_path(
        "F1_MATERIAL_RAG_OCR_REQUEST_FIFO", "/run/material-rag-ocr/request.fifo"
    )
    response = _fifo_path(
        "F1_MATERIAL_RAG_OCR_RESPONSE_FIFO", "/run/material-rag-ocr/response.fifo"
    )
    ready = _fifo_path(
        "F1_MATERIAL_RAG_OCR_READY_FILE", "/run/material-rag-ocr/ready"
    )
    try:
        request_info = request.lstat()
        response_info = response.lstat()
        ready_info = ready.lstat()
    except OSError:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_OCR_FAILED") from None
    if (
        not stat.S_ISFIFO(request_info.st_mode)
        or not stat.S_ISFIFO(response_info.st_mode)
        or not stat.S_ISREG(ready_info.st_mode)
        or request_info.st_nlink != 1
        or response_info.st_nlink != 1
        or ready_info.st_nlink != 1
        or stat.S_IMODE(request_info.st_mode) != 0o600
        or stat.S_IMODE(response_info.st_mode) != 0o600
        or stat.S_IMODE(ready_info.st_mode) != 0o600
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_OCR_FAILED")
    return request, response


def _open_fifo_writer(path: Path, deadline: float) -> int:
    while time.monotonic() < deadline:
        try:
            return os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as error:
            if error.errno not in {errno.ENXIO, errno.EAGAIN, errno.EINTR}:
                break
            time.sleep(0.02)
    raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_OCR_FAILED")


def _write_nonblocking(descriptor: int, body: bytearray, deadline: float) -> None:
    offset = 0
    while offset < len(body):
        if time.monotonic() >= deadline:
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_OCR_FAILED")
        _readable, writable, _errors = select.select(
            [], [descriptor], [], min(0.25, max(0.0, deadline - time.monotonic()))
        )
        if not writable:
            continue
        try:
            written = os.write(descriptor, memoryview(body)[offset : offset + 65536])
        except BlockingIOError:
            continue
        if written < 1:
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_OCR_FAILED")
        offset += written


def _read_nonblocking(descriptor: int, size: int, deadline: float) -> bytes:
    if size < 0 or size > MAX_OCR_RESPONSE_BYTES:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_OCR_FAILED")
    output = bytearray()
    while len(output) < size:
        if time.monotonic() >= deadline:
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_OCR_FAILED")
        readable, _writable, _errors = select.select(
            [descriptor], [], [], min(0.25, max(0.0, deadline - time.monotonic()))
        )
        if not readable:
            continue
        try:
            chunk = os.read(descriptor, min(65536, size - len(output)))
        except BlockingIOError:
            continue
        if not chunk:
            time.sleep(0.01)
            continue
        output.extend(chunk)
    return bytes(output)


def _ocr_page(
    spec: FixtureSpec, body: bytes, plan_page: Mapping[str, Any]
) -> tuple[str, bool, bool]:
    from platform_foundation.f0h.contracts import (
        F0HError,
        canonical_json_bytes,
        validate_private_result,
    )

    request_path, response_path = _validate_ocr_ipc()
    header = {
        "schema": "f0e-envelope-v1",
        "document_type": "PDF",
        "source_sha256": spec.source_sha256,
        "source_size": len(body),
        "expected_total_pages": spec.page_count,
        "page_no": plan_page["page_no"],
        "source_unit_id": plan_page["page_id"],
        "media_box": plan_page["media_box"],
        "crop_box": plan_page["crop_box"],
        "rotation_degrees": plan_page["rotation"],
    }
    header_body = canonical_json_bytes(header)
    envelope = bytearray()
    envelope.extend(len(header_body).to_bytes(4, "big"))
    envelope.extend(header_body)
    envelope.extend(body)
    if len(envelope) > MAX_OCR_ENVELOPE_BYTES:
        envelope[:] = b"\0" * len(envelope)
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_OCR_FAILED")
    framed = bytearray(len(envelope).to_bytes(4, "big"))
    framed.extend(envelope)
    envelope[:] = b"\0" * len(envelope)
    deadline = time.monotonic() + OCR_DEADLINE_SECONDS
    request_descriptor = _open_fifo_writer(request_path, deadline)
    response_descriptor = -1
    try:
        response_descriptor = os.open(response_path, os.O_RDONLY | os.O_NONBLOCK)
        _write_nonblocking(request_descriptor, framed, deadline)
        os.close(request_descriptor)
        request_descriptor = -1
        size = int.from_bytes(_read_nonblocking(response_descriptor, 4, deadline), "big")
        if not 1 <= size <= MAX_OCR_RESPONSE_BYTES:
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_OCR_FAILED")
        raw = _read_nonblocking(response_descriptor, size, deadline)
    except OSError:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_OCR_FAILED") from None
    finally:
        framed[:] = b"\0" * len(framed)
        if request_descriptor >= 0:
            os.close(request_descriptor)
        if response_descriptor >= 0:
            os.close(response_descriptor)
    try:
        result = json.loads(raw.decode("ascii", "strict"))
        if canonical_json_bytes(result) != raw:
            raise F0HError("RUNNER_OUTPUT_INVALID")
        validated = validate_private_result(
            result,
            F0H_BUNDLE,
            expected={
                "document_type": "PDF",
                "source_sha256": spec.source_sha256,
                "page_no": plan_page["page_no"],
                "expected_total_pages": spec.page_count,
                "source_unit_id": plan_page["page_id"],
                "external_calls": 0,
                "external_processing": "DENY",
            },
        )
        blocks = validated["blocks"]
        if (
            not isinstance(blocks, list)
            or not blocks
            or int(validated["ocr_nonblank_char_count"]) < 1
        ):
            raise F0HError("RUNNER_OUTPUT_INVALID")
        text_value = "\n".join(
            str(block["text"]) for block in blocks if str(block["text"]).strip()
        ).strip()
        if not text_value:
            raise F0HError("RUNNER_OUTPUT_INVALID")
        fragments: list[tuple[float, float, str]] = []
        for block in blocks:
            points = list(block["bbox"])
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
            fragments.append(
                (
                    (min(xs) + max(xs)) / 2.0,
                    (min(ys) + max(ys)) / 2.0,
                    str(block["text"]),
                )
            )
        table_candidate, two_column_candidate = _layout_hints(
            fragments,
            width=float(validated["render_width_px"]),
            height=float(validated["render_height_px"]),
            text=text_value,
        )
        return text_value, table_candidate, two_column_candidate
    except (F0HError, KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_OCR_FAILED") from None


def _parse_fixture(
    spec: FixtureSpec, body: bytes, plan: Mapping[str, Any]
) -> FixtureDocument:
    try:
        if importlib.metadata.version("pypdf") != PYPDF_VERSION:
            raise ValueError
        from pypdf import PdfReader

        reader = PdfReader(
            io.BytesIO(body),
            strict=True,
            password=None,
            root_object_recovery_limit=10_000,
        )
        if reader.is_encrypted or len(reader.pages) != spec.page_count:
            raise ValueError
        planned_pages = plan["pages"]
        pages: list[ParsedPage] = []
        for page_number, page in enumerate(reader.pages, start=1):
            route = planned_pages[page_number - 1]
            native_fragments: list[tuple[float, float, str]] = []

            def visitor_text(
                value: str,
                _cm: object,
                tm: object,
                _font: object,
                _size: object,
            ) -> None:
                try:
                    coordinates = list(tm)  # type: ignore[arg-type]
                    x = float(coordinates[4])
                    y = float(coordinates[5])
                except (IndexError, TypeError, ValueError):
                    return
                if value.strip() and math.isfinite(x) and math.isfinite(y):
                    native_fragments.append((x, y, value[:2_000]))

            native_text = (
                page.extract_text(
                    extraction_mode="plain", visitor_text=visitor_text
                )
                or ""
            )
            native_count, bad_ppm, native_sha = _native_text_metrics(native_text)
            if (
                native_count != route["native_characters"]
                or bad_ppm != route["bad_character_ppm"]
                or native_sha != route["native_text_sha256"]
            ):
                raise ValueError
            if page_number in spec.ocr_pages:
                (
                    page_text,
                    table_candidate,
                    two_column_candidate,
                ) = _ocr_page(spec, body, route)
                parser_version = OCR_PARSER_VERSION
                ocr_applied = True
            else:
                if not native_text.strip():
                    raise ValueError
                page_text = native_text
                parser_version = NATIVE_PARSER_VERSION
                ocr_applied = False
                table_candidate, two_column_candidate = _layout_hints(
                    native_fragments,
                    width=abs(float(page.mediabox.width)),
                    height=abs(float(page.mediabox.height)),
                    text=page_text,
                )
            pages.append(
                ParsedPage(
                    page_number=page_number,
                    parser_version=parser_version,
                    text=page_text,
                    ocr_applied=ocr_applied,
                    table_candidate=table_candidate,
                    two_column_candidate=two_column_candidate,
                )
            )
        return FixtureDocument(spec=spec, body=body, pages=tuple(pages))
    except MaterialRagVerifyError:
        raise
    except BaseException:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_SOURCE_FAILED") from None


def _seed_database() -> None:
    try:
        import importlib.util

        seed_path = ROOT / "infra" / "f1" / "material-rag" / "seed.py"
        spec = importlib.util.spec_from_file_location(
            "anhuan_material_rag_seed", seed_path
        )
        if spec is None or spec.loader is None:
            raise ValueError
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.seed()
    except BaseException:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_SEED_FAILED") from None


def _uuid_field(value: Mapping[str, Any], field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value[field]))
    except (KeyError, TypeError, ValueError):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_P3_FAILED") from None


def _response_json(response: object) -> dict[str, Any]:
    try:
        value = response.json()  # type: ignore[attr-defined]
    except BaseException:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_P3_FAILED") from None
    if not isinstance(value, dict):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_P3_FAILED")
    return value


async def _ensure_empty_scopes(
    client_b_account_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID]:
    from infra.f1 import local_seed
    from platform_foundation.f1.auth import Tenant
    from platform_foundation.f1.database import session_scope
    from platform_foundation.f1.features.p3.service import (
        _current_user_id,
        _resolve_knowledge_scope,
    )

    tenant = Tenant(
        enterprise_id=local_seed.ENTERPRISE_A,
        sub=local_seed.ADMIN_SUB,
        roles=(),
        role="super_admin",
    )
    async with session_scope(
        role="f1_api", enterprise_id=tenant.enterprise_id, sub=tenant.sub
    ) as session:
        actor_id = await _current_user_id(session, tenant)
        provider = await _resolve_knowledge_scope(
            session,
            tenant,
            kind="service_provider",
            client_account_id=None,
            actor_id=actor_id,
        )
        client_b = await _resolve_knowledge_scope(
            session,
            tenant,
            kind="client",
            client_account_id=client_b_account_id,
            actor_id=actor_id,
        )
        await session.commit()
    return provider["id"], client_b["id"]


async def _create_synthetic_documents(
    *, provider_scope_id: uuid.UUID, client_b_scope_id: uuid.UUID
) -> tuple[SyntheticDocument, ...]:
    """Persist two fixed released canaries without creating external source files."""

    from infra.f1 import local_seed
    from sqlalchemy import text
    from platform_foundation.f1.database import session_scope
    from platform_foundation.f1.features.material_rag.contracts import SensitiveText
    from platform_foundation.f1.features.material_rag.security import (
        CLIENT_B_ISOLATION_CANARY_TEXT,
        PROVIDER_POLICY_CANARY_TEXT,
        assert_external_text_safe,
        redact_external_text,
    )

    namespace = uuid.UUID("b27c8c08-1c40-48fd-9db8-cf7a7b350cc7")
    actor_id = local_seed._stable_id("profile", local_seed.ADMIN_SUB)
    definitions = (
        ("provider-policy", provider_scope_id, PROVIDER_POLICY_CANARY_TEXT, "policy"),
        ("client-b-isolation", client_b_scope_id, CLIENT_B_ISOLATION_CANARY_TEXT, "report"),
    )
    created: list[SyntheticDocument] = []
    async with session_scope(
        role="f1_api",
        enterprise_id=local_seed.ENTERPRISE_A,
        sub=local_seed.ADMIN_SUB,
    ) as session:
        for label, scope_id, raw_body, material_kind in definitions:
            filtered = redact_external_text(raw_body)
            assert_external_text_safe(filtered)
            if filtered != raw_body or not 1 <= len(filtered) <= 1_600:
                raise MaterialRagVerifyError(
                    "LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED"
                )
            source_sha = hashlib.sha256(filtered.encode("utf-8")).hexdigest()
            record_id = uuid.uuid5(namespace, f"{scope_id}:record:{label}")
            version_id = uuid.uuid5(namespace, f"{scope_id}:version:{label}")
            task_id = uuid.uuid5(namespace, f"{scope_id}:task:{label}")
            source_document_id = uuid.uuid5(
                namespace, f"{scope_id}:source-document:{label}"
            )
            object_key = f"{task_id.hex}.pdf"
            idempotency_sha = hashlib.sha256(
                f"material-rag-synthetic-v1\x00{scope_id}\x00{label}".encode("ascii")
            ).hexdigest()
            await session.execute(
                text(
                    "INSERT INTO f1.document (id,enterprise_id,knowledge_scope_id,"
                    "object_key,filename,size,content_type,status) VALUES "
                    "(:id,:enterprise_id,:scope_id,:object_key,:filename,:size,"
                    "'application/pdf','done')"
                ),
                {
                    "id": source_document_id,
                    "enterprise_id": local_seed.ENTERPRISE_A,
                    "scope_id": scope_id,
                    "object_key": object_key,
                    "filename": f"MATERIAL_RAG_{label.upper()}.pdf",
                    "size": len(filtered.encode("utf-8")),
                },
            )
            await session.execute(
                text(
                    "INSERT INTO f1.upload_task (id,enterprise_id,document_id,object_key,"
                    "content_sha256,status,object_state,source_size,pipeline_kind,"
                    "processing_stage,quarantine_status,scan_verdict,preview_kind,"
                    "preview_status,preview_sha256,preview_unit_count,"
                    "resource_policy_version,released_at) VALUES "
                    "(:id,:enterprise_id,:document_id,:object_key,:source_sha,'done',"
                    "'ready',:source_size,'controlled_ingestion','ready','released',"
                    "'clean','page_text','ready',:source_sha,1,'p3-v1',"
                    "statement_timestamp())"
                ),
                {
                    "id": task_id,
                    "enterprise_id": local_seed.ENTERPRISE_A,
                    "document_id": source_document_id,
                    "object_key": object_key,
                    "source_sha": source_sha,
                    "source_size": len(filtered.encode("utf-8")),
                },
            )
            await session.execute(
                text(
                    "INSERT INTO f1.document_record (id,enterprise_id,title,status,"
                    "declared_material_kind,knowledge_scope_id,scope_selection_source,"
                    "scope_selected_by_user_id,scope_selected_at,latest_version_no,"
                    "created_by_user_id) VALUES "
                    "(:id,:enterprise_id,:title,'active',:material_kind,:scope_id,"
                    "'upload_selection',:actor_id,statement_timestamp(),1,:actor_id)"
                ),
                {
                    "id": record_id,
                    "enterprise_id": local_seed.ENTERPRISE_A,
                    "title": f"MATERIAL_RAG_{label.upper()}",
                    "material_kind": material_kind,
                    "scope_id": scope_id,
                    "actor_id": actor_id,
                },
            )
            await session.execute(
                text(
                    "INSERT INTO f1.document_version (id,enterprise_id,document_record_id,"
                    "version_no,source_document_id,upload_task_id,display_filename,"
                    "idempotency_key_sha256,created_by_user_id) VALUES "
                    "(:id,:enterprise_id,:record_id,1,:source_document_id,:task_id,"
                    ":display_filename,:idempotency_sha,:actor_id)"
                ),
                {
                    "id": version_id,
                    "enterprise_id": local_seed.ENTERPRISE_A,
                    "record_id": record_id,
                    "source_document_id": source_document_id,
                    "task_id": task_id,
                    "display_filename": f"MATERIAL_RAG_{label.upper()}.pdf",
                    "idempotency_sha": idempotency_sha,
                    "actor_id": actor_id,
                },
            )
            created.append(
                SyntheticDocument(
                    label=label,
                    document_record_id=record_id,
                    document_version_id=version_id,
                    upload_task_id=task_id,
                    knowledge_scope_id=scope_id,
                    source_sha256=source_sha,
                    body=SensitiveText(filtered),
                )
            )
        await session.commit()
    return tuple(created)


async def _pre_release_remote_counts() -> tuple[int, int, int]:
    """Exhaustively prove no physical RAG state exists while a source is held."""

    from platform_foundation.f0j1.ragflow_client import RagFlowClient
    from platform_foundation.f1.config import ragflow_base_url
    from platform_foundation.f1.ragflow_provision import ragflow_token

    def inspect() -> tuple[int, int, int]:
        client = RagFlowClient(base_url=ragflow_base_url())
        token = ragflow_token()
        datasets = client.list_all_datasets(token)
        document_count = 0
        chunk_count = 0
        for dataset in datasets:
            dataset_id = str(dataset.get("id") or "")
            if not dataset_id:
                raise MaterialRagVerifyError(
                    "LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED"
                )
            documents = client.list_all_documents(token, dataset_id)
            document_count += len(documents)
            for document in documents:
                document_id = str(document.get("id") or "")
                if not document_id:
                    raise MaterialRagVerifyError(
                        "LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED"
                    )
                chunk_count += len(
                    client.list_chunks(token, dataset_id, document_id)
                )
        return len(datasets), document_count, chunk_count

    return await asyncio.to_thread(inspect)


async def _version_identity(
    version_id: uuid.UUID, expected_sha256: str
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, str]:
    from infra.f1 import local_seed
    from sqlalchemy import text
    from platform_foundation.f1.database import session_scope

    async with session_scope(
        role="f1_api", enterprise_id=local_seed.ENTERPRISE_A, sub=local_seed.ADMIN_SUB
    ) as session:
        row = (
            await session.execute(
                text(
                    "SELECT version.document_record_id,version.upload_task_id,"
                    "record.knowledge_scope_id,task.content_sha256,task.object_key "
                    "FROM f1.document_version AS version "
                    "JOIN f1.document_record AS record ON "
                    "record.enterprise_id=version.enterprise_id "
                    "AND record.id=version.document_record_id "
                    "JOIN f1.upload_task AS task ON "
                    "task.enterprise_id=version.enterprise_id "
                    "AND task.id=version.upload_task_id "
                    "WHERE version.enterprise_id=:enterprise_id "
                    "AND version.id=:version_id"
                ),
                {
                    "enterprise_id": local_seed.ENTERPRISE_A,
                    "version_id": version_id,
                },
            )
        ).one_or_none()
    if (
        row is None
        or str(row[3]) != expected_sha256
        or OPAQUE_OBJECT_RE.fullmatch(str(row[4])) is None
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_P3_FAILED")
    return row[0], row[1], row[2], str(row[4])


async def _upload_task_error_reason(version_id: uuid.UUID) -> str | None:
    from infra.f1 import local_seed
    from sqlalchemy import text
    from platform_foundation.f1.database import session_scope

    async with session_scope(
        role="f1_api", enterprise_id=local_seed.ENTERPRISE_A, sub=local_seed.ADMIN_SUB
    ) as session:
        row = (
            await session.execute(
                text(
                    "SELECT task.error_reason "
                    "FROM f1.document_version AS version "
                    "JOIN f1.upload_task AS task ON "
                    "task.enterprise_id=version.enterprise_id "
                    "AND task.id=version.upload_task_id "
                    "WHERE version.enterprise_id=:enterprise_id "
                    "AND version.id=:version_id"
                ),
                {
                    "enterprise_id": local_seed.ENTERPRISE_A,
                    "version_id": version_id,
                },
            )
        ).one_or_none()
    if row is None:
        return None
    value = row[0]
    return str(value) if value else None


async def _setup_and_upload(
    fixtures: tuple[FixtureDocument, ...],
) -> ProductSetup:
    import httpx
    from fastapi import FastAPI, Header, HTTPException
    from infra.f1 import local_seed
    from platform_foundation.f1 import auth
    from platform_foundation.f1.api.routers import (
        p3_controlled_ingestion,
        p4_views_reports,
    )
    from platform_foundation.f1.auth import Tenant
    from platform_foundation.f1.features.material_rag.contracts import (
        MaterialRagIntegrityError,
    )
    from platform_foundation.f1.features.material_rag.repository import enqueue_job

    _preflight_scanner()
    actors = {
        "admin": (local_seed.ADMIN_SUB, local_seed.ENTERPRISE_A),
        "employee": (EMPLOYEE_SUB, local_seed.ENTERPRISE_A),
        "consultant": (CONSULTANT_SUB, local_seed.ENTERPRISE_A),
        "tenant_b": (TENANT_B_SUB, local_seed.ENTERPRISE_B),
    }
    app = FastAPI()
    app.include_router(p3_controlled_ingestion.router, prefix="/api/v1/ingestion")
    app.include_router(p4_views_reports.router, prefix="/api/v1/views-reports")

    async def identity(
        x_local_material_rag_actor: str | None = Header(default=None),
    ) -> dict[str, object]:
        actor = actors.get(x_local_material_rag_actor or "")
        if actor is None:
            raise HTTPException(status_code=401, detail="LOCAL_IDENTITY_REQUIRED")
        return {"sub": actor[0], "roles": ()}

    app.dependency_overrides[auth.current_user] = identity
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
    uploads: list[UploadedDocument] = []
    held_rejections = 0
    pre_release_remote_zero_snapshots = 0
    premature_index_count = 0
    manual_report_preserved = 0
    cross_tenant_visible = 0
    employee_tenant = Tenant(
        enterprise_id=local_seed.ENTERPRISE_A,
        sub=EMPLOYEE_SUB,
        roles=(),
        role="plant_admin",
    )
    async with httpx.AsyncClient(
        transport=transport, base_url="http://material-rag.invalid"
    ) as client:
        async def request(
            actor_name: str,
            method: str,
            path: str,
            expected_status: int,
            **kwargs: object,
        ) -> object:
            actor = actors[actor_name]
            headers = {
                "X-Local-Material-Rag-Actor": actor_name,
                "X-Enterprise-Id": str(actor[1]),
                **dict(kwargs.pop("headers", {})),
            }
            response = await client.request(
                method, path, headers=headers, **kwargs  # type: ignore[arg-type]
            )
            if response.status_code != expected_status:
                if path.startswith("/api/v1/views-reports/"):
                    raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_P3_CRM_FAILED")
                if path == "/api/v1/ingestion/documents" and method == "POST":
                    raise MaterialRagVerifyError(
                        "LOCAL_MATERIAL_RAG_P3_UPLOAD_HTTP_FAILED"
                    )
                if path.endswith("/release") and method == "POST":
                    raise MaterialRagVerifyError(
                        "LOCAL_MATERIAL_RAG_P3_RELEASE_FAILED"
                    )
                raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_P3_FAILED")
            return response

        employee_user_id = local_seed._stable_id("profile", EMPLOYEE_SUB)
        admin_user_id = local_seed._stable_id("profile", local_seed.ADMIN_SUB)
        account_a = _response_json(
            await request(
                "admin",
                "POST",
                "/api/v1/views-reports/crm/accounts",
                201,
                json={
                    "display_name": "MATERIAL_RAG_CLIENT_A",
                    "stage": "active",
                    "owner_user_id": str(employee_user_id),
                },
            )
        )
        account_b = _response_json(
            await request(
                "admin",
                "POST",
                "/api/v1/views-reports/crm/accounts",
                201,
                json={
                    "display_name": "MATERIAL_RAG_CLIENT_B",
                    "stage": "active",
                    "owner_user_id": str(admin_user_id),
                },
            )
        )
        client_a_account_id = _uuid_field(account_a, "id")
        client_b_account_id = _uuid_field(account_b, "id")
        provider_scope_id, client_b_scope_id = await _ensure_empty_scopes(
            client_b_account_id
        )
        client_a_scope_id: uuid.UUID | None = None

        for fixture in fixtures:
            declared_kind = "report" if fixture.spec.line == 1 else "unknown"
            uploaded = _response_json(
                await request(
                    "employee",
                    "POST",
                    "/api/v1/ingestion/documents",
                    202,
                    headers={
                        "Idempotency-Key": (
                            "material-rag-upload-v1-" + fixture.spec.source_sha256
                        )
                    },
                    data={
                        "display_name": f"DEMO_MATERIAL_{fixture.spec.line:02d}",
                        "declared_material_kind": declared_kind,
                        "knowledge_scope_kind": "client",
                        "client_account_id": str(client_a_account_id),
                    },
                    files={
                        "file": (
                            f"{fixture.spec.source_sha256}.pdf",
                            fixture.body,
                            "application/pdf",
                        )
                    },
                )
            )
            document_id = _uuid_field(uploaded, "id")
            scope = uploaded.get("knowledge_scope")
            latest = uploaded.get("latest_version")
            if (
                uploaded.get("declared_material_kind") != declared_kind
                or not isinstance(scope, dict)
                or scope.get("kind") != "client"
                or scope.get("client_account_id") != str(client_a_account_id)
                or not isinstance(latest, dict)
            ):
                raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_P3_UPLOAD_FAILED")
            if latest.get("scan_status") != "clean":
                version_id = _uuid_field(latest, "id")
                internal = await _upload_task_error_reason(version_id)
                raise MaterialRagVerifyError(
                    _p3_scan_failure_reason(latest, internal=internal)
                )
            if latest.get("preview_status") != "ready":
                version_id = _uuid_field(latest, "id")
                internal = await _upload_task_error_reason(version_id)
                raise MaterialRagVerifyError(
                    "LOCAL_MATERIAL_RAG_P3_PREVIEW_FAILED",
                    evidence={
                        "error_reason": internal,
                        "fixture_line": fixture.spec.line,
                        "preview_status": latest.get("preview_status"),
                    },
                )
            if (
                latest.get("workflow_status") != "ready"
                or latest.get("quarantine_status") != "held"
            ):
                raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_P3_UPLOAD_FAILED")
            version_id = _uuid_field(latest, "id")
            scope_id = _uuid_field(scope, "id")
            if client_a_scope_id is None:
                client_a_scope_id = scope_id
            elif client_a_scope_id != scope_id:
                raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_P3_FAILED")
            try:
                await enqueue_job(
                    employee_tenant,
                    document_version_id=version_id,
                    action="index",
                    idempotency_key=(
                        "material-rag-held-v1-" + fixture.spec.source_sha256
                    ),
                )
            except MaterialRagIntegrityError as error:
                if str(error) != "MATERIAL_VERSION_NOT_INDEXABLE":
                    raise MaterialRagVerifyError(
                        "LOCAL_MATERIAL_RAG_P3_FAILED"
                    ) from None
                held_rejections += 1
            else:
                raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_P3_FAILED")
            remote_counts = await _pre_release_remote_counts()
            premature_index_count += sum(remote_counts)
            if remote_counts != (0, 0, 0):
                raise MaterialRagVerifyError(
                    "LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED"
                )
            pre_release_remote_zero_snapshots += 1
            analysis = _response_json(
                await request(
                    "employee",
                    "GET",
                    f"/api/v1/ingestion/versions/{version_id}/material-intake",
                    200,
                )
            )
            if declared_kind == "report":
                if (
                    analysis.get("resolved_kind") != "report"
                    or analysis.get("classification_source") != "upload_selection"
                    or not analysis.get("classification_by_user_id")
                    or not analysis.get("classification_at")
                ):
                    raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_P3_FAILED")
                manual_report_preserved += 1
            elif (
                analysis.get("resolved_kind") != "unknown"
                or analysis.get("classification_source") != "machine_pending"
                or analysis.get("classification_by_user_id") is not None
                or analysis.get("classification_at") is not None
            ):
                raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_P3_FAILED")
            released = _response_json(
                await request(
                    "employee",
                    "POST",
                    f"/api/v1/ingestion/versions/{version_id}/release",
                    200,
                )
            )
            if (
                released.get("workflow_status") != "ready"
                or released.get("scan_status") != "clean"
                or released.get("preview_status") != "ready"
                or released.get("quarantine_status") != "released"
            ):
                raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_P3_RELEASE_FAILED")
            await request(
                "admin", "GET", f"/api/v1/ingestion/documents/{document_id}", 200
            )
            await request(
                "consultant",
                "GET",
                f"/api/v1/ingestion/documents/{document_id}",
                403,
            )
            tenant_b_response = await request(
                "tenant_b",
                "GET",
                f"/api/v1/ingestion/documents/{document_id}",
                404,
            )
            if getattr(tenant_b_response, "status_code", 0) == 200:
                cross_tenant_visible += 1
            record_id, task_id, persisted_scope_id, object_key = await _version_identity(
                version_id, fixture.spec.source_sha256
            )
            if record_id != document_id or persisted_scope_id != scope_id:
                raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_P3_FAILED")
            uploads.append(
                UploadedDocument(
                    spec=fixture.spec,
                    document_record_id=record_id,
                    document_version_id=version_id,
                    upload_task_id=task_id,
                    knowledge_scope_id=scope_id,
                    object_key=object_key,
                )
            )
    if (
        client_a_scope_id is None
        or held_rejections != 4
        or pre_release_remote_zero_snapshots != 4
        or premature_index_count != 0
        or manual_report_preserved != 1
        or len(uploads) != 4
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_P3_FAILED")
    synthetic_documents = await _create_synthetic_documents(
        provider_scope_id=provider_scope_id,
        client_b_scope_id=client_b_scope_id,
    )
    if len(synthetic_documents) != 2:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_P3_FAILED")
    return ProductSetup(
        provider_scope_id=provider_scope_id,
        client_a_account_id=client_a_account_id,
        client_a_scope_id=client_a_scope_id,
        client_b_account_id=client_b_account_id,
        client_b_scope_id=client_b_scope_id,
        uploads=tuple(uploads),
        synthetic_documents=synthetic_documents,
        held_enqueue_rejection_count=held_rejections,
        pre_release_remote_zero_snapshot_count=pre_release_remote_zero_snapshots,
        premature_index_count=premature_index_count,
        manual_report_classification_preserved_count=manual_report_preserved,
        cross_tenant_api_visible_count=cross_tenant_visible,
    )


def _canonical_units_for_claim(
    claim: object, fixture: FixtureDocument
) -> tuple[object, ...]:
    from platform_foundation.f1.features.material_rag.security import (
        canonical_page_units,
    )

    units: list[object] = []
    for page in fixture.pages:
        units.extend(
            canonical_page_units(
                enterprise_id=claim.enterprise_id,  # type: ignore[attr-defined]
                knowledge_scope_id=claim.knowledge_scope_id,  # type: ignore[attr-defined]
                document_record_id=claim.document_record_id,  # type: ignore[attr-defined]
                document_version_id=claim.document_version_id,  # type: ignore[attr-defined]
                source_sha256=claim.source_sha256,  # type: ignore[attr-defined]
                page_number=page.page_number,
                parser_version=page.parser_version,
                text=page.text,
                ocr_applied=page.ocr_applied,
                table_candidate=page.table_candidate,
                two_column_candidate=page.two_column_candidate,
            )
        )
    if not units:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")
    return tuple(units)


def _authorization_body_sha256(
    fixtures: tuple[FixtureDocument, ...],
) -> tuple[str, ...]:
    """Build the relay allowlist with the worker's exact canonicalizer.

    Dummy identities are stable and deliberately have no bearing on the
    filtered body or its SHA-256.  All page text takes the same
    ``canonical_page_units`` path used by the normal index worker.
    """

    from platform_foundation.f1.features.material_rag.security import (
        SYNTHETIC_AUTHORIZED_EMBEDDING_TEXTS,
        assert_external_text_safe,
        canonical_page_units,
    )

    if (
        len(fixtures) != len(FIXTURES)
        or tuple(fixture.spec for fixture in fixtures) != FIXTURES
        or sum(len(fixture.pages) for fixture in fixtures) != 136
        or sum(
            int(page.ocr_applied)
            for fixture in fixtures
            for page in fixture.pages
        )
        != 6
    ):
        raise MaterialRagVerifyError(
            "LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED"
        )

    body_hashes: list[str] = []
    try:
        for position, fixture in enumerate(fixtures, start=1):
            record_id = uuid.uuid5(
                ARK_AUTHORIZATION_DUMMY_NAMESPACE,
                f"document-record:{position}",
            )
            version_id = uuid.uuid5(
                ARK_AUTHORIZATION_DUMMY_NAMESPACE,
                f"document-version:{position}",
            )
            for page in fixture.pages:
                units = canonical_page_units(
                    enterprise_id=ARK_AUTHORIZATION_DUMMY_ENTERPRISE_ID,
                    knowledge_scope_id=ARK_AUTHORIZATION_DUMMY_SCOPE_ID,
                    document_record_id=record_id,
                    document_version_id=version_id,
                    source_sha256=fixture.spec.source_sha256,
                    page_number=page.page_number,
                    parser_version=page.parser_version,
                    text=page.text,
                    ocr_applied=page.ocr_applied,
                    table_candidate=page.table_candidate,
                    two_column_candidate=page.two_column_candidate,
                )
                if not units:
                    raise ValueError("MATERIAL_AUTHORIZATION_UNIT_MISSING")
                body_hashes.extend(unit.body_sha256 for unit in units)
        for text_value in SYNTHETIC_AUTHORIZED_EMBEDDING_TEXTS:
            assert_external_text_safe(text_value)
            if not 1 <= len(text_value) <= 1_600:
                raise ValueError("MATERIAL_AUTHORIZATION_TEXT_INVALID")
            body_hashes.append(
                hashlib.sha256(text_value.encode("utf-8")).hexdigest()
            )
    except MaterialRagVerifyError:
        raise
    except BaseException:
        raise MaterialRagVerifyError(
            "LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED"
        ) from None

    ordered = tuple(sorted(set(body_hashes)))
    if (
        not ordered
        or len(ordered) > 100_000
        or any(SHA256_RE.fullmatch(value) is None for value in ordered)
        or ordered != tuple(sorted(ordered))
        or len(set(ordered)) != len(ordered)
    ):
        raise MaterialRagVerifyError(
            "LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED"
        )
    return ordered


def _authorization_payload(body_sha256: tuple[str, ...]) -> bytes:
    if (
        not body_sha256
        or body_sha256 != tuple(sorted(body_sha256))
        or len(set(body_sha256)) != len(body_sha256)
        or any(SHA256_RE.fullmatch(value) is None for value in body_sha256)
    ):
        raise MaterialRagVerifyError(
            "LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED"
        )
    return json.dumps(
        {
            "body_sha256": list(body_sha256),
            "schema": ARK_AUTHORIZATION_SCHEMA,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _authorization_path() -> Path:
    raw = os.environ.get(
        "F1_MATERIAL_RAG_ARK_AUTHORIZATION_FILE",
        str(ARK_AUTHORIZATION_FILE),
    )
    path = Path(raw)
    if path != ARK_AUTHORIZATION_FILE or not path.is_absolute():
        raise MaterialRagVerifyError(
            "LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED"
        )
    return path


def _write_authorization_file(body: bytes) -> None:
    """Atomically replace the private, relay-readable authorization file."""

    path = _authorization_path()
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | os.O_NOFOLLOW
    )
    directory_descriptor = -1
    temporary_descriptor = -1
    temporary_name = f".body-sha256.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    try:
        directory_descriptor = os.open(path.parent, flags)
        directory_info = os.fstat(directory_descriptor)
        if (
            not stat.S_ISDIR(directory_info.st_mode)
            or directory_info.st_uid != ARK_AUTHORIZATION_OWNER
            or directory_info.st_gid != ARK_AUTHORIZATION_OWNER
            or stat.S_IMODE(directory_info.st_mode) != 0o700
        ):
            raise ValueError("MATERIAL_AUTHORIZATION_DIRECTORY_INVALID")

        temporary_descriptor = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_descriptor,
        )
        os.fchmod(temporary_descriptor, 0o600)
        view = memoryview(body)
        while view:
            written = os.write(temporary_descriptor, view)
            if written < 1:
                raise OSError(errno.EIO, "authorization write failed")
            view = view[written:]
        os.fsync(temporary_descriptor)
        temporary_info = os.fstat(temporary_descriptor)
        if (
            not stat.S_ISREG(temporary_info.st_mode)
            or temporary_info.st_nlink != 1
            or temporary_info.st_uid != ARK_AUTHORIZATION_OWNER
            or temporary_info.st_gid != ARK_AUTHORIZATION_OWNER
            or stat.S_IMODE(temporary_info.st_mode) != 0o600
            or temporary_info.st_size != len(body)
        ):
            raise ValueError("MATERIAL_AUTHORIZATION_FILE_INVALID")
        os.close(temporary_descriptor)
        temporary_descriptor = -1

        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        os.fsync(directory_descriptor)

        final_descriptor = os.open(
            path.name,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | os.O_NOFOLLOW,
            dir_fd=directory_descriptor,
        )
        try:
            before = os.fstat(final_descriptor)
            actual = os.read(final_descriptor, len(body) + 1)
            after = os.fstat(final_descriptor)
        finally:
            os.close(final_descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != ARK_AUTHORIZATION_OWNER
            or before.st_gid != ARK_AUTHORIZATION_OWNER
            or stat.S_IMODE(before.st_mode) != 0o600
            or not stat.S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or after.st_uid != ARK_AUTHORIZATION_OWNER
            or after.st_gid != ARK_AUTHORIZATION_OWNER
            or stat.S_IMODE(after.st_mode) != 0o600
            or _identity(before) != _identity(after)
            or actual != body
        ):
            raise ValueError("MATERIAL_AUTHORIZATION_FILE_INVALID")
    except BaseException:
        raise MaterialRagVerifyError(
            "LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED"
        ) from None
    finally:
        if temporary_descriptor >= 0:
            os.close(temporary_descriptor)
        if directory_descriptor >= 0:
            try:
                os.unlink(
                    temporary_name,
                    dir_fd=directory_descriptor,
                )
            except FileNotFoundError:
                pass
            except OSError:
                pass
            os.close(directory_descriptor)


def write_authorization() -> None:
    """Parse only the four approved Demo PDFs and publish body hashes."""

    _assert_runtime_authorization()
    plans = _load_fixture_contracts()
    fixtures = tuple(
        _parse_fixture(spec, _read_demo_source(spec), plans[spec.line])
        for spec in FIXTURES
    )
    body_sha256 = _authorization_body_sha256(fixtures)
    _write_authorization_file(_authorization_payload(body_sha256))


async def _job_row(job_id: uuid.UUID) -> Mapping[str, Any]:
    from infra.f1 import local_seed
    from sqlalchemy import text
    from platform_foundation.f1.database import session_scope

    async with session_scope(
        role="f1_api",
        enterprise_id=local_seed.ENTERPRISE_A,
        sub=local_seed.ADMIN_SUB,
    ) as session:
        row = (
            await session.execute(
                text(
                    "SELECT status,result_manifest_sha256,indexed_unit_count,action "
                    "FROM f1.material_rag_job WHERE id=:id"
                ),
                {"id": job_id},
            )
        ).mappings().one_or_none()
    if row is None:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")
    return row


async def _unit_counts(scope_id: uuid.UUID) -> tuple[int, int]:
    from infra.f1 import local_seed
    from sqlalchemy import text
    from platform_foundation.f1.database import session_scope

    async with session_scope(
        role="f1_api", enterprise_id=local_seed.ENTERPRISE_A, sub=EMPLOYEE_SUB
    ) as session:
        row = (
            await session.execute(
                text(
                    "SELECT count(*),count(DISTINCT id) "
                    "FROM f1.material_rag_unit WHERE knowledge_scope_id=:scope_id"
                ),
                {"scope_id": scope_id},
            )
        ).one()
    return int(row[0]), int(row[1])


async def _load_version_units(upload: object) -> tuple[object, ...]:
    from infra.f1 import local_seed
    from platform_foundation.f1.database import session_scope
    from platform_foundation.f1.features.material_rag.repository import (
        load_units_for_version,
    )

    async with session_scope(
        role="f1_api",
        enterprise_id=local_seed.ENTERPRISE_A,
        sub=local_seed.ADMIN_SUB,
    ) as session:
        return await load_units_for_version(
            session,
            enterprise_id=local_seed.ENTERPRISE_A,
            knowledge_scope_id=upload.knowledge_scope_id,  # type: ignore[attr-defined]
            document_version_id=upload.document_version_id,  # type: ignore[attr-defined]
        )


def _unit_fingerprint(unit: object) -> tuple[object, ...]:
    """Return every durable canonical-unit identity field, without its body."""

    return (
        unit.id,  # type: ignore[attr-defined]
        unit.enterprise_id,  # type: ignore[attr-defined]
        unit.knowledge_scope_id,  # type: ignore[attr-defined]
        unit.document_record_id,  # type: ignore[attr-defined]
        unit.document_version_id,  # type: ignore[attr-defined]
        unit.source_sha256,  # type: ignore[attr-defined]
        unit.page_number,  # type: ignore[attr-defined]
        unit.ordinal,  # type: ignore[attr-defined]
        unit.parser_version,  # type: ignore[attr-defined]
        unit.body_sha256,  # type: ignore[attr-defined]
        unit.ocr_applied,  # type: ignore[attr-defined]
        unit.table_candidate,  # type: ignore[attr-defined]
        unit.two_column_candidate,  # type: ignore[attr-defined]
    )


async def _scope_unit_db_snapshot(
    scope_id: uuid.UUID,
) -> tuple[tuple[object, ...], ...]:
    """Return every stored unit column so replay/conflict checks are exact."""

    from infra.f1 import local_seed
    from sqlalchemy import text
    from platform_foundation.f1.database import session_scope

    async with session_scope(
        role="f1_api", enterprise_id=local_seed.ENTERPRISE_A, sub=EMPLOYEE_SUB
    ) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id,enterprise_id,knowledge_scope_id,document_record_id,"
                    "document_version_id,source_sha256,page_number,ordinal,"
                    "parser_version,ocr_applied,table_candidate,two_column_candidate,"
                    "body_ciphertext,body_sha256,body_aad_sha256,created_at "
                    "FROM f1.material_rag_unit WHERE knowledge_scope_id=:scope_id "
                    "ORDER BY id"
                ),
                {"scope_id": scope_id},
            )
        ).all()
    return tuple(tuple(row) for row in rows)


async def _action_job_count(scope_id: uuid.UUID, action: str) -> int:
    from infra.f1 import local_seed
    from sqlalchemy import text
    from platform_foundation.f1.database import session_scope

    async with session_scope(
        role="f1_api",
        enterprise_id=local_seed.ENTERPRISE_A,
        sub=local_seed.ADMIN_SUB,
    ) as session:
        value = (
            await session.execute(
                text(
                    "SELECT count(*) FROM f1.material_rag_job "
                    "WHERE knowledge_scope_id=:scope_id AND action=:action"
                ),
                {"scope_id": scope_id, "action": action},
            )
        ).scalar_one()
    return int(value)


async def _prove_unit_identity_conflict_rejected(
    scope_id: uuid.UUID, original: object
) -> int:
    """Exercise the real persistence conflict path and prove no row changed."""

    from infra.f1 import local_seed
    from platform_foundation.f1.database import session_scope
    from platform_foundation.f1.features.material_rag.contracts import (
        MaterialRagIntegrityError,
        SensitiveText,
    )
    from platform_foundation.f1.features.material_rag.repository import (
        persist_canonical_units,
    )

    before = await _scope_unit_db_snapshot(scope_id)
    conflict_body = original.body.reveal() + "\nMATERIAL_RAG_BODY_CONFLICT_PROBE"  # type: ignore[attr-defined]
    conflict = replace(
        original,
        body=SensitiveText(conflict_body),
        body_sha256=hashlib.sha256(conflict_body.encode("utf-8")).hexdigest(),
    )
    if (
        conflict.id != original.id  # type: ignore[attr-defined]
        or conflict.body_sha256 == original.body_sha256  # type: ignore[attr-defined]
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")
    async with session_scope(
        role="f1_api", enterprise_id=local_seed.ENTERPRISE_A, sub=EMPLOYEE_SUB
    ) as session:
        try:
            await persist_canonical_units(session, (conflict,))
        except MaterialRagIntegrityError as error:
            await session.rollback()
            if str(error) != "MATERIAL_UNIT_IDENTITY_CONFLICT":
                raise MaterialRagVerifyError(
                    "LOCAL_MATERIAL_RAG_INDEX_FAILED"
                ) from None
        else:
            await session.rollback()
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")
    if await _scope_unit_db_snapshot(scope_id) != before:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")
    return 1


def _egress_audit() -> dict[str, int]:
    path = Path(
        os.environ.get(
            "F1_MATERIAL_RAG_EGRESS_AUDIT_FILE",
            "/run/material-rag-egress/audit.json",
        )
    )
    body = _read_regular_unhashed(path, maximum_bytes=4096)
    try:
        value = json.loads(body.decode("ascii", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_EGRESS_AUDIT_FAILED") from None
    fixed = {
        "allowed_method": "POST",
        "allowed_model": "doubao-embedding-vision",
        "allowed_path": "/api/plan/v3/embeddings/multimodal",
        "allowed_upstream_authority": "ark.cn-beijing.volces.com:443",
        "schema": "anhuan-material-rag-ark-relay-audit-v2",
    }
    counter_keys = {
        "aborted_embedding_request_count",
        "authorized_embedding_request_count",
        "external_llm_call_count",
        "external_ocr_call_count",
        "forwarded_embedding_request_count",
        "forwarded_non_embedding_request_count",
        "inflight_embedding_request_count",
        "input_text_count",
        "process_start_count",
        "rejected_content_type_count",
        "rejected_json_count",
        "rejected_method_count",
        "rejected_model_count",
        "rejected_non_text_input_count",
        "rejected_path_count",
        "rejected_request_count",
        "rejected_unauthorized_text_count",
        "upstream_2xx_count",
        "upstream_4xx_count",
        "upstream_5xx_count",
        "upstream_request_byte_count",
        "upstream_response_byte_count",
    }
    if (
        not isinstance(value, dict)
        or set(value) != set(fixed).union(counter_keys)
        or any(value.get(key) != expected for key, expected in fixed.items())
        or any(
            type(value.get(key)) is not int or int(value[key]) < 0
            for key in counter_keys
        )
        or value["forwarded_non_embedding_request_count"] != 0
        or value["external_llm_call_count"] != 0
        or value["external_ocr_call_count"] != 0
        or value["inflight_embedding_request_count"] != 0
        or value["aborted_embedding_request_count"] != 0
        or value["authorized_embedding_request_count"]
        != value["forwarded_embedding_request_count"]
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_EGRESS_AUDIT_FAILED")
    return {key: int(value[key]) for key in counter_keys}


async def _stable_egress_audit(*, require_traffic: bool) -> dict[str, int]:
    """Wait for the aggregate proxy file to stop changing.

    The relay commits counters around each upstream embedding call.  A short
    stable window is part of the no-egress assertion for empty scopes.
    """
    previous: dict[str, int] | None = None
    stable_samples = 0
    for _ in range(60):
        current = _egress_audit()
        traffic_ready = (
            not require_traffic
            or (
                current["forwarded_embedding_request_count"] > 0
                and current["upstream_2xx_count"] > 0
                and current["upstream_request_byte_count"] > 0
                and current["upstream_response_byte_count"] > 0
            )
        )
        if current == previous and traffic_ready:
            stable_samples += 1
            if stable_samples >= 2:
                return current
        else:
            stable_samples = 0
        previous = current
        await asyncio.sleep(0.25)
    raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_EGRESS_AUDIT_FAILED")


def _read_regular_unhashed(path: Path, *, maximum_bytes: int) -> bytes:
    if not path.is_absolute() or path.is_symlink():
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_EGRESS_AUDIT_FAILED")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_EGRESS_AUDIT_FAILED") from None
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or not 1 <= before.st_size <= maximum_bytes
        ):
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_EGRESS_AUDIT_FAILED")
        body = os.read(descriptor, maximum_bytes + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if len(body) != before.st_size or _identity(before) != _identity(after):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_EGRESS_AUDIT_FAILED")
    return body


def _strict_remote_tags(value: object, failure_reason: str) -> dict[str, str]:
    raw_tags = value if isinstance(value, list) else [value] if value else []
    result: dict[str, str] = {}
    for raw in raw_tags:
        key, separator, item = str(raw).partition("=")
        if not separator or not key or key in result:
            raise MaterialRagVerifyError(failure_reason)
        result[key] = item
    if set(result) != {
        "canonical_unit_id",
        "knowledge_scope_id",
        "document_record_id",
        "document_version_id",
        "source_sha256",
        "page_number",
        "body_sha256",
    }:
        raise MaterialRagVerifyError(failure_reason)
    return result


async def _remote_snapshot(
    setup: ProductSetup,
    expected_by_version: Mapping[uuid.UUID, tuple[object, ...]],
    *,
    knowledge_scope_id: uuid.UUID | None = None,
    failure_reason: str,
) -> RemoteScopeSnapshot:
    """Hydrate and reconcile every remote dataset/document/chunk identity."""

    from infra.f1 import local_seed
    from platform_foundation.f0j1.ragflow_client import RagFlowClient
    from platform_foundation.f1.config import ragflow_base_url
    from platform_foundation.f1.database import session_scope
    from platform_foundation.f1.features.material_rag.repository import (
        load_dataset_binding,
    )
    from platform_foundation.f1.ragflow_provision import ragflow_token

    target_scope_id = knowledge_scope_id or setup.client_a_scope_id
    async with session_scope(
        role="f1_api", enterprise_id=local_seed.ENTERPRISE_A, sub=local_seed.ADMIN_SUB
    ) as session:
        bindings = {
            scope_id: await load_dataset_binding(
                session,
                enterprise_id=local_seed.ENTERPRISE_A,
                knowledge_scope_id=scope_id,
            )
            for scope_id in (
                setup.provider_scope_id,
                setup.client_a_scope_id,
                setup.client_b_scope_id,
            )
        }
    target_binding = bindings.get(target_scope_id)
    if target_binding is None:
        raise MaterialRagVerifyError(failure_reason)

    def inspect() -> RemoteScopeSnapshot:
        client = RagFlowClient(base_url=ragflow_base_url())
        token = ragflow_token()
        datasets = client.list_all_datasets(token)
        expected_datasets = {
            f"f1-material-{scope_id.hex}": binding.dataset_ref
            for scope_id, binding in bindings.items()
            if binding is not None
        }
        if knowledge_scope_id is None and set(expected_datasets) != {
            f"f1-material-{setup.client_a_scope_id.hex}"
        }:
            raise MaterialRagVerifyError(failure_reason)
        actual_datasets = {
            str(item.get("name") or ""): str(item.get("id") or "")
            for item in datasets
        }
        expected_name = f"f1-material-{target_scope_id.hex}"
        if (
            len(actual_datasets) != len(datasets)
            or actual_datasets != expected_datasets
            or target_binding.dataset_ref != expected_datasets.get(expected_name)
        ):
            raise MaterialRagVerifyError(
                "LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED"
            )
        documents = client.list_all_documents(token, target_binding.dataset_ref)
        from platform_foundation.f1.features.material_rag.security import (
            remote_document_name,
        )

        expected_documents: dict[str, uuid.UUID] = {}
        for version_id, units in expected_by_version.items():
            source_sha256_values = {
                unit.source_sha256  # type: ignore[attr-defined]
                for unit in units
            }
            if len(source_sha256_values) != 1:
                raise MaterialRagVerifyError(failure_reason)
            document_name = remote_document_name(source_sha256_values.pop())
            if document_name in expected_documents:
                raise MaterialRagVerifyError(failure_reason)
            expected_documents[document_name] = version_id
        document_names = [str(item.get("name") or "") for item in documents]
        if (
            len(set(document_names)) != len(documents)
            or set(document_names) != set(expected_documents)
        ):
            raise MaterialRagVerifyError(failure_reason)
        snapshots: list[RemoteDocumentSnapshot] = []
        remote_document_ids: set[str] = set()
        remote_chunk_ids: set[str] = set()
        seen_unit_ids: set[uuid.UUID] = set()
        for document in documents:
            document_name = str(document.get("name") or "")
            document_id = str(document.get("id") or "")
            if not document_id or document_id in remote_document_ids:
                raise MaterialRagVerifyError(failure_reason)
            remote_document_ids.add(document_id)
            version_id = expected_documents[document_name]
            expected_units = {
                unit.id: unit  # type: ignore[attr-defined]
                for unit in expected_by_version[version_id]
            }
            if len(expected_units) != len(expected_by_version[version_id]):
                raise MaterialRagVerifyError(failure_reason)
            chunk_snapshots: list[RemoteChunkSnapshot] = []
            listed_chunks = client.list_chunks(
                token, target_binding.dataset_ref, document_id
            )
            for listed in listed_chunks:
                chunk_id = str(listed.get("id") or listed.get("chunk_id") or "")
                if not chunk_id or chunk_id in remote_chunk_ids:
                    raise MaterialRagVerifyError(failure_reason)
                remote_chunk_ids.add(chunk_id)
                detail = client.get_chunk(
                    token, target_binding.dataset_ref, document_id, chunk_id
                )
                detail_id = str(detail.get("id") or detail.get("chunk_id") or "")
                if detail_id and detail_id != chunk_id:
                    raise MaterialRagVerifyError(failure_reason)
                tags = _strict_remote_tags(detail.get("tag_kwd"), failure_reason)
                content = detail.get("content")
                if not isinstance(content, str) or not content:
                    raise MaterialRagVerifyError(failure_reason)
                content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
                try:
                    snapshot = RemoteChunkSnapshot(
                        remote_chunk_id=chunk_id,
                        canonical_unit_id=uuid.UUID(tags["canonical_unit_id"]),
                        knowledge_scope_id=uuid.UUID(tags["knowledge_scope_id"]),
                        document_record_id=uuid.UUID(tags["document_record_id"]),
                        document_version_id=uuid.UUID(tags["document_version_id"]),
                        source_sha256=tags["source_sha256"],
                        page_number=int(tags["page_number"]),
                        body_sha256=tags["body_sha256"],
                        content_sha256=content_sha256,
                    )
                except (TypeError, ValueError):
                    raise MaterialRagVerifyError(
                        failure_reason
                    ) from None
                expected = expected_units.get(snapshot.canonical_unit_id)
                if (
                    expected is None
                    or snapshot.canonical_unit_id in seen_unit_ids
                    or snapshot.knowledge_scope_id
                    != expected.knowledge_scope_id  # type: ignore[attr-defined]
                    or snapshot.document_record_id
                    != expected.document_record_id  # type: ignore[attr-defined]
                    or snapshot.document_version_id
                    != expected.document_version_id  # type: ignore[attr-defined]
                    or snapshot.document_version_id != version_id
                    or snapshot.source_sha256
                    != expected.source_sha256  # type: ignore[attr-defined]
                    or snapshot.page_number
                    != expected.page_number  # type: ignore[attr-defined]
                    or snapshot.body_sha256
                    != expected.body_sha256  # type: ignore[attr-defined]
                    or snapshot.content_sha256 != snapshot.body_sha256
                ):
                    raise MaterialRagVerifyError(failure_reason)
                seen_unit_ids.add(snapshot.canonical_unit_id)
                chunk_snapshots.append(snapshot)
            if {item.canonical_unit_id for item in chunk_snapshots} != set(
                expected_units
            ):
                raise MaterialRagVerifyError(failure_reason)
            snapshots.append(
                RemoteDocumentSnapshot(
                    remote_document_id=document_id,
                    document_name=document_name,
                    chunks=tuple(
                        sorted(
                            chunk_snapshots,
                            key=lambda item: item.canonical_unit_id.hex,
                        )
                    ),
                )
            )
        expected_unit_ids = {
            unit.id  # type: ignore[attr-defined]
            for units in expected_by_version.values()
            for unit in units
        }
        if seen_unit_ids != expected_unit_ids:
            raise MaterialRagVerifyError(failure_reason)
        return RemoteScopeSnapshot(
            dataset_ref=target_binding.dataset_ref,
            dataset_name=expected_name,
            documents=tuple(sorted(snapshots, key=lambda item: item.document_name)),
        )

    return await asyncio.to_thread(inspect)


async def _final_scope_residue(
    setup: ProductSetup, *, deleted_dataset_refs: frozenset[str]
) -> tuple[int, int, int]:
    """Inspect remote datasets and local binding secrets before stack teardown."""
    from infra.f1 import local_seed
    from sqlalchemy import text
    from platform_foundation.f0j1.ragflow_client import RagFlowClient
    from platform_foundation.f1.config import ragflow_base_url
    from platform_foundation.f1.database import session_scope
    from platform_foundation.f1.ragflow_provision import ragflow_token

    async with session_scope(
        role="f1_api",
        enterprise_id=local_seed.ENTERPRISE_A,
        sub=local_seed.ADMIN_SUB,
    ) as session:
        row = (
            await session.execute(
                text(
                    "SELECT count(*),"
                    "count(*) FILTER (WHERE status='deleted'),"
                    "count(*) FILTER (WHERE status='ready'),"
                    "count(*) FILTER (WHERE dataset_ref_ciphertext IS NOT NULL "
                    "OR dataset_ref_sha256 IS NOT NULL "
                    "OR dataset_ref_aad_sha256 IS NOT NULL) "
                    "FROM f1.material_rag_scope_binding"
                )
            )
        ).one()
    binding_count = int(row[0])
    deleted_binding_count = int(row[1])
    ready_binding_count = int(row[2])
    secret_residual_count = int(row[3])

    def inspect_remote() -> int:
        datasets = RagFlowClient(base_url=ragflow_base_url()).list_all_datasets(
            ragflow_token()
        )
        expected_names = {
            f"f1-material-{scope_id.hex}"
            for scope_id in (
                setup.provider_scope_id,
                setup.client_a_scope_id,
                setup.client_b_scope_id,
            )
        }
        if any(
            item.get("id") in deleted_dataset_refs
            or item.get("name") in expected_names
            for item in datasets
        ):
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_DELETE_FAILED")
        return len(datasets)

    remote_dataset_count = await asyncio.to_thread(inspect_remote)
    if (
        binding_count != 3
        or deleted_binding_count != 3
        or ready_binding_count != 0
        or secret_residual_count != 0
        or remote_dataset_count != 0
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_DELETE_FAILED")
    return remote_dataset_count, ready_binding_count, secret_residual_count


async def _rls_visible_counts(
    *, enterprise_id: uuid.UUID, sub: str, scope_id: uuid.UUID
) -> tuple[int, int, int]:
    from sqlalchemy import text
    from platform_foundation.f1.database import session_scope

    async with session_scope(
        role="f1_api", enterprise_id=enterprise_id, sub=sub
    ) as session:
        row = (
            await session.execute(
                text(
                    "SELECT "
                    "(SELECT count(*) FROM f1.material_rag_scope_binding "
                    "WHERE knowledge_scope_id=:scope_id),"
                    "(SELECT count(*) FROM f1.material_rag_unit "
                    "WHERE knowledge_scope_id=:scope_id),"
                    "(SELECT count(*) FROM f1.material_rag_job "
                    "WHERE knowledge_scope_id=:scope_id)"
                ),
                {"scope_id": scope_id},
            )
        ).one()
    return int(row[0]), int(row[1]), int(row[2])


async def _verify_rls(
    setup: ProductSetup, unit_count: int, *, job_count: int = 4
) -> None:
    from infra.f1 import local_seed

    admin = await _rls_visible_counts(
        enterprise_id=local_seed.ENTERPRISE_A,
        sub=local_seed.ADMIN_SUB,
        scope_id=setup.client_a_scope_id,
    )
    owner = await _rls_visible_counts(
        enterprise_id=local_seed.ENTERPRISE_A,
        sub=EMPLOYEE_SUB,
        scope_id=setup.client_a_scope_id,
    )
    non_owner = await _rls_visible_counts(
        enterprise_id=local_seed.ENTERPRISE_A,
        sub=CONSULTANT_SUB,
        scope_id=setup.client_a_scope_id,
    )
    cross_tenant = await _rls_visible_counts(
        enterprise_id=local_seed.ENTERPRISE_B,
        sub=TENANT_B_SUB,
        scope_id=setup.client_a_scope_id,
    )
    owner_client_b = await _rls_visible_counts(
        enterprise_id=local_seed.ENTERPRISE_A,
        sub=EMPLOYEE_SUB,
        scope_id=setup.client_b_scope_id,
    )
    if (
        admin != (1, unit_count, job_count)
        or owner != (1, unit_count, job_count)
        or non_owner != (0, 0, 0)
        or cross_tenant != (0, 0, 0)
        or owner_client_b != (0, 0, 0)
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_RLS_FAILED")


async def _process_jobs(
    fixtures: tuple[FixtureDocument, ...], setup: ProductSetup
) -> RagRun:
    from infra.f1 import local_seed
    from platform_foundation.f1.auth import Tenant
    from platform_foundation.f1.features.material_rag.contracts import (
        MaterialRagContextNotFound,
        MaterialRagUnavailable,
        REFUSE_NO_HITS,
        REFUSE_NOT_CONFIGURED,
    )
    from platform_foundation.f1.features.material_rag.ragflow_adapter import (
        RemoteCandidate,
    )
    from platform_foundation.f1.features.material_rag.repository import enqueue_job
    from platform_foundation.f1.features.material_rag.security import (
        CLIENT_A_RETRIEVAL_QUERY_TEXT,
        CLIENT_B_RETRIEVAL_QUERY_TEXT,
        PROVIDER_RETRIEVAL_QUERY_TEXT,
        assert_external_text_safe,
        canonical_page_units,
        create_demo_unit_manifest_proof,
        create_synthetic_unit_manifest_proof,
        remote_document_name,
    )
    from platform_foundation.f1.features.material_rag.service import (
        derive_retrieval_context,
        retrieve_authorized_demo_fragment,
        retrieve_registered_verifier_query,
        run_verified_retrieval,
        verify_remote_candidates,
    )
    from platform_foundation.f1.features.material_rag.worker import process_demo_job
    from platform_foundation.f1.qa_service import (
        QaResult,
        ReservationState,
        _aad,
        _decrypt,
        _encrypt,
        _question_sha256,
        complete_request,
        reserve_request,
    )
    from cryptography.exceptions import InvalidTag

    fixture_by_sha = {fixture.spec.source_sha256: fixture for fixture in fixtures}
    employee = Tenant(
        enterprise_id=local_seed.ENTERPRISE_A,
        sub=EMPLOYEE_SUB,
        roles=(),
        role="plant_admin",
    )
    admin = Tenant(
        enterprise_id=local_seed.ENTERPRISE_A,
        sub=local_seed.ADMIN_SUB,
        roles=(),
        role="super_admin",
    )
    tenant_b = Tenant(
        enterprise_id=local_seed.ENTERPRISE_B,
        sub=TENANT_B_SUB,
        roles=(),
        role="enterprise_admin",
    )
    index_manifests: dict[uuid.UUID, str] = {}
    persisted_by_version: dict[uuid.UUID, tuple[object, ...]] = {}

    for upload in setup.uploads:
        fixture = fixture_by_sha[upload.spec.source_sha256]
        captured: dict[str, tuple[object, ...]] = {}

        def prepare(claim: object) -> tuple[Iterable[object], object]:
            units = _canonical_units_for_claim(claim, fixture)
            for unit in units:
                assert_external_text_safe(unit.body.reveal())  # type: ignore[attr-defined]
            proof = create_demo_unit_manifest_proof(
                source_path=upload.spec.path,
                claim=claim,
                units=units,
            )
            captured["units"] = units
            return units, proof

        job_id = await enqueue_job(
            employee,
            document_version_id=upload.document_version_id,
            action="index",
            idempotency_key="material-rag-index-v1-" + upload.spec.source_sha256,
        )
        if not await process_demo_job(
            job_id, worker_id="material-rag-verifier", prepare=prepare
        ):
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")
        row = await _job_row(job_id)
        units = captured.get("units")
        if (
            units is None
            or row["status"] != "done"
            or row["action"] != "index"
            or int(row["indexed_unit_count"]) != len(units)
            or not isinstance(row["result_manifest_sha256"], str)
            or SHA256_RE.fullmatch(row["result_manifest_sha256"]) is None
        ):
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")
        index_manifests[upload.document_version_id] = row["result_manifest_sha256"]
        persisted = await _load_version_units(upload)
        if (
            tuple(_unit_fingerprint(unit) for unit in persisted)
            != tuple(_unit_fingerprint(unit) for unit in units)
        ):
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")
        persisted_by_version[upload.document_version_id] = persisted

    unit_count, distinct_count = await _unit_counts(setup.client_a_scope_id)
    expected_unit_count = sum(len(value) for value in persisted_by_version.values())
    if unit_count != expected_unit_count or distinct_count != unit_count:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")
    indexed_remote = await _remote_snapshot(
        setup,
        persisted_by_version,
        failure_reason="LOCAL_MATERIAL_RAG_INDEX_FAILED",
    )
    documents = indexed_remote.document_count
    chunks = indexed_remote.chunk_count
    provider_documents = 0
    client_b_documents = 0
    if documents != 4 or chunks != unit_count:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")
    await _verify_rls(setup, unit_count)

    # A distinct durable index job is the real replay boundary.  Reusing the
    # first idempotency key would only return its completed row and would not
    # exercise canonical persistence or RAGFlow reconciliation a second time.
    pre_replay_db = await _scope_unit_db_snapshot(setup.client_a_scope_id)
    index_replay_job_count = 0
    for upload in setup.uploads:
        fixture = fixture_by_sha[upload.spec.source_sha256]

        def prepare_replay(claim: object) -> tuple[Iterable[object], object]:
            units = _canonical_units_for_claim(claim, fixture)
            proof = create_demo_unit_manifest_proof(
                source_path=upload.spec.path,
                claim=claim,
                units=units,
            )
            return units, proof

        replay_job_id = await enqueue_job(
            employee,
            document_version_id=upload.document_version_id,
            action="index",
            idempotency_key=(
                "material-rag-index-replay-v1-" + upload.spec.source_sha256
            ),
        )
        if not await process_demo_job(
            replay_job_id,
            worker_id="material-rag-verifier",
            prepare=prepare_replay,
        ):
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")
        replay_row = await _job_row(replay_job_id)
        expected_units = persisted_by_version[upload.document_version_id]
        replayed_units = await _load_version_units(upload)
        if (
            replay_row["status"] != "done"
            or replay_row["action"] != "index"
            or int(replay_row["indexed_unit_count"]) != len(expected_units)
            or replay_row["result_manifest_sha256"]
            != index_manifests[upload.document_version_id]
            or tuple(_unit_fingerprint(unit) for unit in replayed_units)
            != tuple(_unit_fingerprint(unit) for unit in expected_units)
            or await _scope_unit_db_snapshot(setup.client_a_scope_id)
            != pre_replay_db
        ):
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")
        replayed_remote = await _remote_snapshot(
            setup,
            persisted_by_version,
            failure_reason="LOCAL_MATERIAL_RAG_INDEX_FAILED",
        )
        if replayed_remote != indexed_remote:
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")
        index_replay_job_count += 1

    replayed_count, replayed_distinct = await _unit_counts(setup.client_a_scope_id)
    duplicate_unit_count = replayed_count - replayed_distinct
    index_job_count = await _action_job_count(setup.client_a_scope_id, "index")
    if (
        index_replay_job_count != 4
        or replayed_count != unit_count
        or duplicate_unit_count != 0
        or index_job_count != 4 + index_replay_job_count
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")
    await _verify_rls(setup, unit_count, job_count=index_job_count)
    unit_identity_conflict_rejection_count = (
        await _prove_unit_identity_conflict_rejected(
            setup.client_a_scope_id,
            persisted_by_version[setup.uploads[0].document_version_id][0],
        )
    )

    client_a_context = await derive_retrieval_context(
        employee, setup.client_a_account_id
    )
    client_b_context = await derive_retrieval_context(admin, setup.client_b_account_id)
    provider_context = await derive_retrieval_context(admin, None)
    try:
        await derive_retrieval_context(tenant_b, setup.client_a_account_id)
    except MaterialRagContextNotFound:
        pass
    else:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_RLS_FAILED")

    query_unit = persisted_by_version[setup.uploads[0].document_version_id][0]
    audit_before_empty = await _stable_egress_audit(require_traffic=False)
    context_probe_id = uuid.uuid5(
        uuid.UUID("71ac74c5-c73b-482e-87d4-1a0364f493bd"),
        client_a_context.context_sha256,
    )
    context_probe_question = "MATERIAL_RAG_CONTEXT_BINDING_PROBE"
    context_probe = await reserve_request(
        context_probe_id,
        employee,
        context_probe_question,
        query_context_sha256=client_a_context.context_sha256,
    )
    if (
        context_probe.state is not ReservationState.CLAIMED
        or context_probe.owner_token is None
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED")
    await complete_request(
        context_probe_id,
        employee,
        context_probe_question,
        context_probe.owner_token,
        QaResult(
            None,
            [],
            "MATERIAL_QUERY_EXTERNAL_PROCESSING_NOT_AUTHORIZED",
            str(context_probe_id),
        ),
        query_context_sha256=client_a_context.context_sha256,
    )
    conflicting_reservation = await reserve_request(
        context_probe_id,
        admin,
        context_probe_question,
        query_context_sha256=client_b_context.context_sha256,
    )
    if conflicting_reservation.state is not ReservationState.CONFLICT:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED")
    question_sha = _question_sha256(context_probe_question)
    protected = _encrypt(
        '{"answer":"context-bound","citations":[]}',
        _aad(
            context_probe_id,
            employee.enterprise_id,
            question_sha,
            client_a_context.context_sha256,
        ),
    )
    try:
        _decrypt(
            protected,
            _aad(
                context_probe_id,
                employee.enterprise_id,
                question_sha,
                client_b_context.context_sha256,
            ),
        )
    except InvalidTag:
        pass
    else:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED")
    provider_result = await retrieve_authorized_demo_fragment(
        query_unit.body.reveal(),  # type: ignore[attr-defined]
        admin,
        provider_context,
        query_source_sha256=query_unit.source_sha256,  # type: ignore[attr-defined]
    )
    client_b_result = await retrieve_authorized_demo_fragment(
        query_unit.body.reveal(),  # type: ignore[attr-defined]
        admin,
        client_b_context,
        query_source_sha256=query_unit.source_sha256,  # type: ignore[attr-defined]
    )
    try:
        await run_verified_retrieval(
            "MATERIAL_RAG_FREEFORM_QUERY_MUST_NOT_LEAVE",
            employee,
            client_a_context,
        )
    except MaterialRagUnavailable as error:
        if str(error) != "MATERIAL_QUERY_EXTERNAL_PROCESSING_NOT_AUTHORIZED":
            raise MaterialRagVerifyError(
                "LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED"
            ) from None
    else:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED")
    audit_after_empty = await _stable_egress_audit(require_traffic=False)
    if (
        provider_result.refusal_reason != REFUSE_NOT_CONFIGURED
        or client_b_result.refusal_reason != REFUSE_NO_HITS
        or audit_after_empty != audit_before_empty
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED")

    synthetic_by_scope: dict[uuid.UUID, tuple[object, ...]] = {}
    synthetic_index_job_count = 0
    for synthetic in setup.synthetic_documents:
        captured_synthetic: dict[str, tuple[object, ...]] = {}

        def prepare_synthetic(claim: object) -> tuple[Iterable[object], object]:
            units = canonical_page_units(
                enterprise_id=claim.enterprise_id,  # type: ignore[attr-defined]
                knowledge_scope_id=claim.knowledge_scope_id,  # type: ignore[attr-defined]
                document_record_id=claim.document_record_id,  # type: ignore[attr-defined]
                document_version_id=claim.document_version_id,  # type: ignore[attr-defined]
                source_sha256=claim.source_sha256,  # type: ignore[attr-defined]
                page_number=1,
                parser_version="synthetic-canary-v1",
                text=synthetic.body.reveal(),  # type: ignore[attr-defined]
            )
            if len(units) != 1:
                raise ValueError("MATERIAL_SYNTHETIC_UNIT_INVALID")
            proof = create_synthetic_unit_manifest_proof(
                claim=claim,  # type: ignore[arg-type]
                units=units,
            )
            captured_synthetic["units"] = units
            return units, proof

        synthetic_job_id = await enqueue_job(
            admin,
            document_version_id=synthetic.document_version_id,
            action="index",
            idempotency_key=f"material-rag-synthetic-index-v1-{synthetic.label}",
        )
        if not await process_demo_job(
            synthetic_job_id,
            worker_id="material-rag-verifier",
            prepare=prepare_synthetic,
        ):
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")
        synthetic_row = await _job_row(synthetic_job_id)
        synthetic_units = captured_synthetic.get("units")
        persisted_synthetic = await _load_version_units(synthetic)
        if (
            synthetic_units is None
            or synthetic_row["status"] != "done"
            or synthetic_row["action"] != "index"
            or int(synthetic_row["indexed_unit_count"]) != 1
            or tuple(_unit_fingerprint(unit) for unit in persisted_synthetic)
            != tuple(_unit_fingerprint(unit) for unit in synthetic_units)
        ):
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")
        synthetic_by_scope[synthetic.knowledge_scope_id] = persisted_synthetic
        synthetic_index_job_count += 1
    if (
        synthetic_index_job_count != 2
        or set(synthetic_by_scope)
        != {setup.provider_scope_id, setup.client_b_scope_id}
        or await _action_job_count(setup.provider_scope_id, "index") != 1
        or await _action_job_count(setup.client_b_scope_id, "index") != 1
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")

    provider_synthetic = setup.synthetic_documents[0]
    client_b_synthetic = setup.synthetic_documents[1]
    if (
        provider_synthetic.knowledge_scope_id != setup.provider_scope_id
        or client_b_synthetic.knowledge_scope_id != setup.client_b_scope_id
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")
    provider_sibling_snapshot = await _remote_snapshot(
        setup,
        {provider_synthetic.document_version_id: synthetic_by_scope[setup.provider_scope_id]},
        knowledge_scope_id=setup.provider_scope_id,
        failure_reason="LOCAL_MATERIAL_RAG_INDEX_FAILED",
    )
    client_b_sibling_snapshot = await _remote_snapshot(
        setup,
        {client_b_synthetic.document_version_id: synthetic_by_scope[setup.client_b_scope_id]},
        knowledge_scope_id=setup.client_b_scope_id,
        failure_reason="LOCAL_MATERIAL_RAG_INDEX_FAILED",
    )
    if (
        provider_sibling_snapshot.document_count != 1
        or provider_sibling_snapshot.chunk_count != 1
        or client_b_sibling_snapshot.document_count != 1
        or client_b_sibling_snapshot.chunk_count != 1
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")
    if (
        await _rls_visible_counts(
            enterprise_id=local_seed.ENTERPRISE_A,
            sub=local_seed.ADMIN_SUB,
            scope_id=setup.client_b_scope_id,
        )
        != (1, 1, 1)
        or await _rls_visible_counts(
            enterprise_id=local_seed.ENTERPRISE_A,
            sub=EMPLOYEE_SUB,
            scope_id=setup.client_b_scope_id,
        )
        != (0, 0, 0)
        or await _rls_visible_counts(
            enterprise_id=local_seed.ENTERPRISE_B,
            sub=TENANT_B_SUB,
            scope_id=setup.client_b_scope_id,
        )
        != (0, 0, 0)
        or await _rls_visible_counts(
            enterprise_id=local_seed.ENTERPRISE_A,
            sub=EMPLOYEE_SUB,
            scope_id=setup.provider_scope_id,
        )
        != (1, 1, 1)
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_RLS_FAILED")

    provider_scoped_result = await retrieve_registered_verifier_query(
        PROVIDER_RETRIEVAL_QUERY_TEXT,
        admin,
        provider_context,
        limit=20,
    )
    client_a_scoped_result = await retrieve_registered_verifier_query(
        CLIENT_A_RETRIEVAL_QUERY_TEXT,
        employee,
        client_a_context,
        limit=20,
    )
    client_b_scoped_result = await retrieve_registered_verifier_query(
        CLIENT_B_RETRIEVAL_QUERY_TEXT,
        admin,
        client_b_context,
        limit=20,
    )
    provider_unit_ids = {
        unit.id for unit in synthetic_by_scope[setup.provider_scope_id]  # type: ignore[attr-defined]
    }
    client_b_unit_ids = {
        unit.id for unit in synthetic_by_scope[setup.client_b_scope_id]  # type: ignore[attr-defined]
    }
    client_a_unit_ids = {
        unit.id  # type: ignore[attr-defined]
        for units in persisted_by_version.values()
        for unit in units
    }
    provider_result_ids = {
        evidence.canonical_unit_id for evidence in provider_scoped_result.evidence
    }
    client_a_result_ids = {
        evidence.canonical_unit_id for evidence in client_a_scoped_result.evidence
    }
    client_b_result_ids = {
        evidence.canonical_unit_id for evidence in client_b_scoped_result.evidence
    }
    if (
        provider_scoped_result.refusal_reason is not None
        or not provider_unit_ids.issubset(provider_result_ids)
        or not provider_result_ids.issubset(provider_unit_ids)
        or client_a_scoped_result.refusal_reason is not None
        or not (client_a_result_ids & client_a_unit_ids)
        or not client_a_result_ids.issubset(provider_unit_ids | client_a_unit_ids)
        or client_b_scoped_result.refusal_reason is not None
        or not client_b_unit_ids.issubset(client_b_result_ids)
        or not client_b_result_ids.issubset(provider_unit_ids | client_b_unit_ids)
        or bool(client_a_result_ids & client_b_unit_ids)
        or bool(client_b_result_ids & client_a_unit_ids)
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_RETRIEVAL_FAILED")
    provider_retrieval_hit_count = 1
    client_a_scoped_retrieval_hit_count = 1
    client_b_retrieval_hit_count = 1

    candidate_identity = {
        "canonical_unit_id": query_unit.id,  # type: ignore[attr-defined]
        "knowledge_scope_id": query_unit.knowledge_scope_id,  # type: ignore[attr-defined]
        "document_record_id": query_unit.document_record_id,  # type: ignore[attr-defined]
        "document_version_id": query_unit.document_version_id,  # type: ignore[attr-defined]
        "source_sha256": query_unit.source_sha256,  # type: ignore[attr-defined]
        "page_number": query_unit.page_number,  # type: ignore[attr-defined]
        "body_sha256": query_unit.body_sha256,  # type: ignore[attr-defined]
    }
    forged_candidates = (
        RemoteCandidate(**{**candidate_identity, "knowledge_scope_id": setup.client_b_scope_id}),
        RemoteCandidate(
            **{
                **candidate_identity,
                "page_number": int(candidate_identity["page_number"]) + 1,
            }
        ),
        RemoteCandidate(
            **{
                **candidate_identity,
                "document_version_id": uuid.UUID(
                    int=(
                        candidate_identity["document_version_id"].int ^ 1  # type: ignore[union-attr]
                    )
                ),
            }
        ),
        RemoteCandidate(
            **{
                **candidate_identity,
                "body_sha256": (
                    "0" * 64
                    if candidate_identity["body_sha256"] != "0" * 64
                    else "1" * 64
                ),
            }
        ),
    )
    for forged in forged_candidates:
        if await verify_remote_candidates((forged,), employee, client_a_context):
            raise MaterialRagVerifyError(
                "LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED"
            )

    citation_count = sum(
        len(result.evidence)
        for result in (
            provider_scoped_result,
            client_a_scoped_result,
            client_b_scoped_result,
        )
    )
    expected_document_names = {
        upload.document_record_id: f"DEMO_MATERIAL_{upload.spec.line:02d}"
        for upload in setup.uploads
    }
    expected_evidence = {
        unit.id: (  # type: ignore[attr-defined]
            unit.document_record_id,  # type: ignore[attr-defined]
            unit.document_version_id,  # type: ignore[attr-defined]
            expected_document_names[unit.document_record_id],  # type: ignore[attr-defined]
            1,
            unit.source_sha256,  # type: ignore[attr-defined]
            unit.page_number,  # type: ignore[attr-defined]
            unit.body_sha256,  # type: ignore[attr-defined]
            "client",
            " ".join(unit.body.reveal().split())[:320],  # type: ignore[attr-defined]
        )
        for units in persisted_by_version.values()
        for unit in units
    }
    if len(expected_evidence) != unit_count:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_RETRIEVAL_FAILED")
    for upload in setup.uploads:
        exact = persisted_by_version[upload.document_version_id][0]
        result = None
        for attempt in range(6):
            result = await retrieve_authorized_demo_fragment(
                exact.body.reveal(),  # type: ignore[attr-defined]
                employee,
                client_a_context,
                query_source_sha256=exact.source_sha256,  # type: ignore[attr-defined]
                limit=12,
            )
            if result.evidence:
                break
            if attempt < 5:
                await asyncio.sleep(3)
        if result is None or not result.evidence or result.refusal_reason is not None:
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_RETRIEVAL_FAILED")
        if not any(
            evidence.canonical_unit_id == exact.id  # type: ignore[attr-defined]
            for evidence in result.evidence
        ):
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_RETRIEVAL_FAILED")
        for evidence in result.evidence:
            expected = expected_evidence.get(evidence.canonical_unit_id)
            actual = (
                evidence.document_record_id,
                evidence.document_version_id,
                evidence.document_name,
                evidence.version_number,
                evidence.source_sha256,
                evidence.page_number,
                evidence.body_sha256,
                evidence.scope_kind,
                evidence.snippet,
            )
            if expected is None or actual != expected:
                raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_RETRIEVAL_FAILED")
        citation_count += len(result.evidence)

    audit_after_retrieval = await _stable_egress_audit(require_traffic=True)
    if (
        audit_after_retrieval["forwarded_embedding_request_count"] < 1
        or audit_after_retrieval["upstream_request_byte_count"] < 1
        or audit_after_retrieval["upstream_response_byte_count"] < 1
        or audit_after_retrieval["upstream_2xx_count"] < 1
        or audit_after_retrieval["rejected_request_count"] != 0
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_EGRESS_AUDIT_FAILED")

    for upload in setup.uploads:
        fixture = fixture_by_sha[upload.spec.source_sha256]

        def prepare_rebuild(claim: object) -> tuple[Iterable[object], object]:
            units = _canonical_units_for_claim(claim, fixture)
            proof = create_demo_unit_manifest_proof(
                source_path=upload.spec.path,
                claim=claim,
                units=units,
            )
            return units, proof

        job_id = await enqueue_job(
            employee,
            document_version_id=upload.document_version_id,
            action="rebuild",
            idempotency_key="material-rag-rebuild-v1-" + upload.spec.source_sha256,
        )
        if not await process_demo_job(
            job_id, worker_id="material-rag-verifier", prepare=prepare_rebuild
        ):
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_REBUILD_FAILED")
        row = await _job_row(job_id)
        if (
            row["status"] != "done"
            or row["action"] != "rebuild"
            or row["result_manifest_sha256"]
            != index_manifests[upload.document_version_id]
        ):
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_REBUILD_FAILED")
    rebuilt_count, rebuilt_distinct = await _unit_counts(setup.client_a_scope_id)
    rebuilt_remote = await _remote_snapshot(
        setup,
        persisted_by_version,
        failure_reason="LOCAL_MATERIAL_RAG_REBUILD_FAILED",
    )
    rebuilt_documents = rebuilt_remote.document_count
    rebuilt_chunks = rebuilt_remote.chunk_count
    if (
        rebuilt_count != unit_count
        or rebuilt_distinct != unit_count
        or rebuilt_documents != 4
        or rebuilt_chunks != unit_count
        or rebuilt_remote.semantic_fingerprint()
        != indexed_remote.semantic_fingerprint()
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_REBUILD_FAILED")

    stale = RemoteCandidate(
        canonical_unit_id=query_unit.id,  # type: ignore[attr-defined]
        knowledge_scope_id=query_unit.knowledge_scope_id,  # type: ignore[attr-defined]
        document_record_id=query_unit.document_record_id,  # type: ignore[attr-defined]
        document_version_id=query_unit.document_version_id,  # type: ignore[attr-defined]
        source_sha256=query_unit.source_sha256,  # type: ignore[attr-defined]
        page_number=query_unit.page_number,  # type: ignore[attr-defined]
        body_sha256=query_unit.body_sha256,  # type: ignore[attr-defined]
    )
    remaining_units = unit_count
    remaining_by_version = dict(persisted_by_version)
    prior_remote = rebuilt_remote
    remote_dataset_residual_count = -1
    ready_binding_residual_count = -1
    binding_secret_residual_count = -1
    cross_scope_sibling_delete_proof_count = 0
    sibling_scope_delete_leak_count = 0
    synthetic_delete_job_count = 0
    for deletion_index, upload in enumerate(setup.uploads, start=1):
        removed_units = len(persisted_by_version[upload.document_version_id])
        job_id = await enqueue_job(
            employee,
            document_version_id=upload.document_version_id,
            action="delete",
            idempotency_key="material-rag-delete-v1-" + upload.spec.source_sha256,
        )
        if not await process_demo_job(job_id, worker_id="material-rag-verifier"):
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_DELETE_FAILED")
        row = await _job_row(job_id)
        if (
            row["status"] != "done"
            or row["action"] != "delete"
            or int(row["indexed_unit_count"]) != 0
        ):
            raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_DELETE_FAILED")
        remaining_units -= removed_units
        del remaining_by_version[upload.document_version_id]
        expected_siblings = RemoteScopeSnapshot(
            dataset_ref=prior_remote.dataset_ref,
            dataset_name=prior_remote.dataset_name,
            documents=tuple(
                document
                for document in prior_remote.documents
                if document.document_name
                != remote_document_name(upload.spec.source_sha256)
            ),
        )
        if remaining_by_version:
            remaining_remote = await _remote_snapshot(
                setup,
                remaining_by_version,
                failure_reason="LOCAL_MATERIAL_RAG_DELETE_FAILED",
            )
            remaining_documents = remaining_remote.document_count
            remaining_chunks = remaining_remote.chunk_count
            if (
                remaining_documents != 4 - deletion_index
                or remaining_chunks != remaining_units
                or remaining_remote != expected_siblings
            ):
                raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_DELETE_FAILED")
            prior_remote = remaining_remote
            if deletion_index == 1:
                provider_after_client_delete = await _remote_snapshot(
                    setup,
                    {
                        provider_synthetic.document_version_id: synthetic_by_scope[
                            setup.provider_scope_id
                        ]
                    },
                    knowledge_scope_id=setup.provider_scope_id,
                    failure_reason="LOCAL_MATERIAL_RAG_DELETE_FAILED",
                )
                client_b_after_client_delete = await _remote_snapshot(
                    setup,
                    {
                        client_b_synthetic.document_version_id: synthetic_by_scope[
                            setup.client_b_scope_id
                        ]
                    },
                    knowledge_scope_id=setup.client_b_scope_id,
                    failure_reason="LOCAL_MATERIAL_RAG_DELETE_FAILED",
                )
                if (
                    provider_after_client_delete != provider_sibling_snapshot
                    or client_b_after_client_delete != client_b_sibling_snapshot
                ):
                    sibling_scope_delete_leak_count += 1
                    raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_DELETE_FAILED")
                cross_scope_sibling_delete_proof_count += 1
                for synthetic in setup.synthetic_documents:
                    synthetic_delete_id = await enqueue_job(
                        admin,
                        document_version_id=synthetic.document_version_id,
                        action="delete",
                        idempotency_key=(
                            f"material-rag-synthetic-delete-v1-{synthetic.label}"
                        ),
                    )
                    if not await process_demo_job(
                        synthetic_delete_id,
                        worker_id="material-rag-verifier",
                    ):
                        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_DELETE_FAILED")
                    synthetic_delete_row = await _job_row(synthetic_delete_id)
                    if (
                        synthetic_delete_row["status"] != "done"
                        or synthetic_delete_row["action"] != "delete"
                        or int(synthetic_delete_row["indexed_unit_count"]) != 0
                    ):
                        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_DELETE_FAILED")
                    synthetic_delete_job_count += 1
        else:
            if expected_siblings.documents:
                raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_DELETE_FAILED")
            (
                remote_dataset_residual_count,
                ready_binding_residual_count,
                binding_secret_residual_count,
            ) = await _final_scope_residue(
                setup,
                deleted_dataset_refs=frozenset(
                    (
                        prior_remote.dataset_ref,
                        provider_sibling_snapshot.dataset_ref,
                        client_b_sibling_snapshot.dataset_ref,
                    )
                ),
            )
    final_count, final_distinct = await _unit_counts(setup.client_a_scope_id)
    if final_count != 0 or final_distinct != 0:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_DELETE_FAILED")
    if (
        remote_dataset_residual_count != 0
        or ready_binding_residual_count != 0
        or binding_secret_residual_count != 0
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_DELETE_FAILED")
    if await verify_remote_candidates((stale,), employee, client_a_context):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_DELETE_FAILED")
    if (
        cross_scope_sibling_delete_proof_count != 1
        or sibling_scope_delete_leak_count != 0
        or synthetic_delete_job_count != 2
        or await _action_job_count(setup.client_a_scope_id, "delete") != 4
        or await _action_job_count(setup.provider_scope_id, "delete") != 1
        or await _action_job_count(setup.client_b_scope_id, "delete") != 1
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_DELETE_FAILED")

    final_audit = await _stable_egress_audit(require_traffic=True)
    if (
        final_audit["forwarded_embedding_request_count"] < 1
        or final_audit["upstream_request_byte_count"] < 1
        or final_audit["upstream_response_byte_count"] < 1
        or final_audit["upstream_2xx_count"] < 1
        or final_audit["rejected_request_count"] != 0
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_EGRESS_AUDIT_FAILED")

    return RagRun(
        unit_count=unit_count,
        synthetic_unit_count=sum(len(value) for value in synthetic_by_scope.values()),
        duplicate_unit_count=duplicate_unit_count,
        unit_identity_conflict_rejection_count=(
            unit_identity_conflict_rejection_count
        ),
        index_job_count=index_job_count + synthetic_index_job_count,
        index_replay_job_count=index_replay_job_count,
        synthetic_index_job_count=synthetic_index_job_count,
        citation_count=citation_count,
        provider_retrieval_hit_count=provider_retrieval_hit_count,
        client_a_scoped_retrieval_hit_count=client_a_scoped_retrieval_hit_count,
        client_b_retrieval_hit_count=client_b_retrieval_hit_count,
        cross_scope_sibling_delete_proof_count=(
            cross_scope_sibling_delete_proof_count
        ),
        sibling_scope_delete_leak_count=sibling_scope_delete_leak_count,
        forwarded_embedding_request_count=final_audit[
            "forwarded_embedding_request_count"
        ],
        client_a_indexed_remote_chunk_count=chunks,
        remote_dataset_residual_count=remote_dataset_residual_count,
        ready_binding_residual_count=ready_binding_residual_count,
        binding_secret_residual_count=binding_secret_residual_count,
    )


def _activate_storage_namespace() -> tuple[object, tuple[str, str, str]]:
    from platform_foundation.f1 import storage

    suffix = uuid.uuid4().hex[:20]
    buckets = (
        f"anhuan-rag-q-{suffix}",
        f"anhuan-rag-p-{suffix}",
        f"anhuan-rag-r-{suffix}",
    )
    storage.QUARANTINE_BUCKET, storage.PREVIEW_BUCKET, storage.BUCKET = buckets
    try:
        client = storage._client()
        if any(client.bucket_exists(bucket) for bucket in buckets):
            raise ValueError
    except BaseException:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_STORAGE_FAILED") from None
    return storage, buckets


def _cleanup_storage(storage: object, buckets: tuple[str, str, str]) -> None:
    try:
        client = storage._client()
        for bucket in buckets:
            if not client.bucket_exists(bucket):
                continue
            names = tuple(
                str(item.object_name)
                for item in client.list_objects(bucket, recursive=True)
            )
            for name in names:
                if bucket in {buckets[0], buckets[2]}:
                    valid = OPAQUE_OBJECT_RE.fullmatch(name) is not None
                else:
                    valid = PREVIEW_OBJECT_RE.fullmatch(name) is not None
                if not valid:
                    raise ValueError
            for name in sorted(names):
                client.remove_object(bucket, name)
            if tuple(client.list_objects(bucket, recursive=True)):
                raise ValueError
            client.remove_bucket(bucket)
            if client.bucket_exists(bucket):
                raise ValueError
    except BaseException:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_CLEANUP_FAILED") from None


async def _dispose_engines() -> None:
    from platform_foundation.f1 import database

    for engine in tuple(database._engines.values()):
        await engine.dispose()
    database._engines.clear()
    database._factories.clear()


async def _run_async(
    fixtures: tuple[FixtureDocument, ...],
) -> tuple[ProductSetup, RagRun]:
    try:
        setup = await _setup_and_upload(fixtures)
        rag_run = await _process_jobs(fixtures, setup)
        return setup, rag_run
    finally:
        await _dispose_engines()


def _assert_runtime_authorization() -> None:
    if (
        os.environ.get("F1_EXTERNAL_LLM_ENABLED") != "false"
        or os.environ.get("F1_EXTERNAL_OCR_ENABLED") != "false"
        or os.environ.get("F1_EXTERNAL_PIPELINES_ENABLED") != "true"
        or os.environ.get("F1_ARK_API_KEY_FILE")
        or os.environ.get("ARK_API_KEY")
    ):
        raise MaterialRagVerifyError(
            "LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED"
        )


def _assert_embedding_only_provider_attestation() -> None:
    raw_path = os.environ.get("F1_MATERIAL_RAG_PROVIDER_ATTESTATION_FILE", "")
    path = Path(raw_path)
    if (
        not raw_path
        or not path.is_absolute()
        or path.name != "provider-attestation.json"
        or path.is_symlink()
    ):
        raise MaterialRagVerifyError(
            "LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED"
        )
    expected = {
        "configured_base_url": "http://material-rag-egress-proxy:8080/api/plan/v3",
        "external_llm_call_count": 0,
        "external_llm_enabled": False,
        "instance": "material-rag-ark",
        "model": "doubao-embedding-vision",
        "model_types": ["embedding"],
        "provider": "VolcEngine",
        "schema": "anhuan-material-rag-provider-attestation-v2",
        "upstream_authority": "ark.cn-beijing.volces.com:443",
        "upstream_embedding_path": "/api/plan/v3/embeddings/multimodal",
    }
    expected_body = (
        json.dumps(expected, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError:
        raise MaterialRagVerifyError(
            "LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED"
        ) from None
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size != len(expected_body)
        ):
            raise MaterialRagVerifyError(
                "LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED"
            )
        body = os.read(descriptor, len(expected_body) + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        _identity(before) != _identity(after)
        or body != expected_body
        or hashlib.sha256(body).digest()
        != hashlib.sha256(expected_body).digest()
    ):
        raise MaterialRagVerifyError(
            "LOCAL_MATERIAL_RAG_AUTHORIZATION_BOUNDARY_FAILED"
        )


def run() -> MaterialRagVerificationCounts:
    _assert_runtime_authorization()
    _assert_embedding_only_provider_attestation()
    plans = _load_fixture_contracts()
    fixtures = tuple(
        _parse_fixture(spec, _read_demo_source(spec), plans[spec.line])
        for spec in FIXTURES
    )
    if (
        sum(len(fixture.pages) for fixture in fixtures) != 136
        or sum(
            int(page.ocr_applied)
            for fixture in fixtures
            for page in fixture.pages
        )
        != 6
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_FIXTURE_CONTRACT_FAILED")
    _seed_database()
    storage, buckets = _activate_storage_namespace()
    pending: MaterialRagVerifyError | None = None
    setup: ProductSetup | None = None
    rag_run: RagRun | None = None
    try:
        setup, rag_run = asyncio.run(_run_async(fixtures))
    except MaterialRagVerifyError as error:
        pending = error
    except BaseException:
        pending = MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INTERNAL_ERROR")
    try:
        _cleanup_storage(storage, buckets)
    except MaterialRagVerifyError as error:
        pending = error
    if pending is not None:
        raise pending
    if setup is None or rag_run is None:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INTERNAL_ERROR")
    final_audit = _egress_audit()
    if (
        final_audit["rejected_request_count"] != 0
        or final_audit["forwarded_embedding_request_count"]
        != rag_run.forwarded_embedding_request_count
    ):
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_EGRESS_AUDIT_FAILED")
    return MaterialRagVerificationCounts(
        demo_pdf_count=4,
        uploaded_version_count=4,
        synthetic_canary_version_count=2,
        clean_scan_count=4,
        preview_ready_count=4,
        released_version_count=4,
        held_enqueue_rejection_count=setup.held_enqueue_rejection_count,
        pre_release_remote_zero_snapshot_count=(
            setup.pre_release_remote_zero_snapshot_count
        ),
        premature_index_count=setup.premature_index_count,
        manual_report_classification_preserved_count=(
            setup.manual_report_classification_preserved_count
        ),
        page_count=136,
        native_page_count=130,
        ocr_page_count=6,
        local_ocr_execution_count=6,
        ocr_external_call_count=0,
        provider_scope_count=1,
        client_scope_count=2,
        canonical_unit_count=rag_run.unit_count,
        synthetic_canonical_unit_count=rag_run.synthetic_unit_count,
        external_text_safety_failure_count=0,
        duplicate_unit_count=rag_run.duplicate_unit_count,
        unit_identity_conflict_rejection_count=(
            rag_run.unit_identity_conflict_rejection_count
        ),
        index_job_count=rag_run.index_job_count,
        index_replay_job_count=rag_run.index_replay_job_count,
        synthetic_index_job_count=rag_run.synthetic_index_job_count,
        indexed_version_count=6,
        client_a_indexed_remote_document_count=4,
        client_a_indexed_remote_chunk_count=rag_run.client_a_indexed_remote_chunk_count,
        provider_indexed_remote_document_count=1,
        client_b_indexed_remote_document_count=1,
        provider_retrieval_hit_count=rag_run.provider_retrieval_hit_count,
        client_a_scoped_retrieval_hit_count=(
            rag_run.client_a_scoped_retrieval_hit_count
        ),
        client_b_retrieval_hit_count=rag_run.client_b_retrieval_hit_count,
        authorized_retrieval_count=7,
        citation_count=rag_run.citation_count,
        pre_index_provider_empty_scope_refusal_count=1,
        pre_index_client_b_no_hit_count=1,
        pre_index_empty_scope_egress_count=0,
        freeform_query_rejection_count=1,
        context_idempotency_conflict_count=1,
        wrong_context_aad_rejection_count=1,
        forged_candidate_rejection_count=4,
        cross_tenant_api_visible_count=setup.cross_tenant_api_visible_count,
        unauthorized_rls_visible_count=0,
        synthetic_scope_unauthorized_rls_visible_count=0,
        rebuild_job_count=4,
        rebuild_mismatch_count=0,
        delete_job_count=6,
        cross_scope_sibling_delete_proof_count=(
            rag_run.cross_scope_sibling_delete_proof_count
        ),
        sibling_scope_delete_leak_count=rag_run.sibling_scope_delete_leak_count,
        stale_candidate_leak_count=0,
        remote_document_residual_count=0,
        remote_chunk_residual_count=0,
        remote_dataset_residual_count=rag_run.remote_dataset_residual_count,
        ready_binding_residual_count=rag_run.ready_binding_residual_count,
        binding_secret_residual_count=rag_run.binding_secret_residual_count,
        delete_residual_count=0,
        # The endpoint-aware relay admits only the exact embedding route,
        # model and pre-authorized sanitized body hashes.  Its persistent
        # counters prove that no LLM or external OCR route was forwarded.
        external_llm_call_count=0,
        egress_rejected_request_count=final_audit["rejected_request_count"],
        egress_forwarded_embedding_request_count=(
            rag_run.forwarded_embedding_request_count
        ),
        object_residual_count=0,
        bucket_residual_count=0,
    )


def main() -> int:
    if sys.argv[1:] == ["--write-authorization"]:
        try:
            with (
                contextlib.redirect_stdout(_DiscardText()),
                contextlib.redirect_stderr(_DiscardText()),
            ):
                write_authorization()
        except BaseException as error:
            reason = (
                error.reason
                if isinstance(error, MaterialRagVerifyError)
                else "LOCAL_MATERIAL_RAG_INTERNAL_ERROR"
            )
            print(reason, file=sys.stderr)
            return 1
        print("LOCAL_MATERIAL_RAG_AUTHORIZATION_OK")
        return 0
    try:
        with (
            contextlib.redirect_stdout(_DiscardText()),
            contextlib.redirect_stderr(_DiscardText()),
        ):
            counts = run()
    except BaseException as error:
        reason = (
            error.reason
            if isinstance(error, MaterialRagVerifyError)
            else "LOCAL_MATERIAL_RAG_INTERNAL_ERROR"
        )
        if isinstance(error, MaterialRagVerifyError):
            _print_p3_preview_evidence(error.evidence)
        print(reason, file=sys.stderr)
        return 1
    print(json.dumps(asdict(counts), sort_keys=True, separators=(",", ":")))
    print("LOCAL_MATERIAL_RAG_VERIFY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
