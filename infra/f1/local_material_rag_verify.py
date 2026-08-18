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
from contextvars import ContextVar, Token


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


_INDEX_EVIDENCE_PREFIX = "LOCAL_MATERIAL_RAG_INDEX_EVIDENCE "
_INDEX_JOB_STATUSES = frozenset(
    {"done", "failed", "queued", "retry_wait", "running"}
)
_INDEX_PROBE_REASON_BASES = frozenset(
    {
        "CHUNK_ADD_FAILED",
        "CHUNK_DELETE_FAILED",
        "CHUNK_GET_FAILED",
        "CHUNK_LIST_FAILED",
        "DATASET_CREATE_FAILED",
        "DATASET_DELETE_FAILED",
        "DATASET_LIST_FAILED",
        "DOC_CREATE_FAILED",
        "DOC_DELETE_FAILED",
        "DOC_LIST_FAILED",
        "PROVIDER_ADD_FAILED",
        "RETRIEVAL_FAILED",
    }
)
_INDEX_PROBE_STATUS_SUFFIXES = frozenset(
    {
        "200",
        "400",
        "401",
        "403",
        "404",
        "409",
        "422",
        "500",
        "502",
        "503",
        "NONE",
    }
)
_INDEX_CHUNK_ADD_CODE_TOKENS = frozenset(
    {
        "CHUNK_ADD_CODE_NONE",
        "CHUNK_ADD_CODE_OTHER",
        *(
            f"CHUNK_ADD_CODE_{code}"
            for code in (
                1,
                2,
                3,
                4,
                5,
                100,
                101,
                102,
                103,
                104,
                108,
                109,
                400,
                401,
                403,
                404,
                409,
                422,
                500,
                501,
                502,
                503,
            )
        ),
    }
)
_INDEX_PROBE_STATUS_TOKENS = frozenset(
    f"{base}_{suffix}"
    for base in _INDEX_PROBE_REASON_BASES
    for suffix in _INDEX_PROBE_STATUS_SUFFIXES
)
_INDEX_REASON_TOKENS = frozenset(
    {
        "MATERIAL_RAG_BINDING_MISSING",
        "MATERIAL_RAG_DATASET_BINDING_CONFLICT",
        "MATERIAL_RAG_DATASET_BINDING_DELETING",
        "MATERIAL_RAG_DATASET_BINDING_INVALID",
        "MATERIAL_RAG_DATASET_FINALIZE_FAILED",
        "MATERIAL_RAG_DELETE_UNITS_FORBIDDEN",
        "MATERIAL_RAG_IDEMPOTENCY_CONFLICT",
        "MATERIAL_RAG_INTEGRITY_FAILED",
        "MATERIAL_RAG_JOB_ACTION_INVALID",
        "MATERIAL_RAG_LOCAL_FAILED",
        "MATERIAL_RAG_MANIFEST_INVALID",
        "MATERIAL_RAG_MANIFEST_REQUIRED",
        "MATERIAL_RAG_RELEASE_FENCE_FORBIDDEN",
        "MATERIAL_RAG_REMOTE_DATASET_DELETE_MISMATCH",
        "MATERIAL_RAG_REMOTE_DATASET_IDENTITY_INVALID",
        "MATERIAL_RAG_REMOTE_DATASET_NOT_EMPTY",
        "MATERIAL_RAG_SOURCE_NOT_AUTHORIZED",
        "MATERIAL_RAG_STORED_MANIFEST_MISMATCH",
        "MATERIAL_RAG_NETWORK_FAILED",
        "MATERIAL_RAG_PROBE_FAILED",
        "MATERIAL_RAG_PROVISION_FAILED",
        "MATERIAL_RAG_UNAVAILABLE",
        "MATERIAL_RAG_UNITS_MISSING",
        "MATERIAL_RAG_UNIT_JOB_MISMATCH",
        "MATERIAL_UNIT_IDENTITY_CONFLICT",
        "MATERIAL_VERSION_NOT_FOUND",
        "MATERIAL_VERSION_NOT_INDEXABLE",
    }
) | _INDEX_PROBE_STATUS_TOKENS | _INDEX_CHUNK_ADD_CODE_TOKENS
_INDEX_OUTCOMES = frozenset(
    {
        "CLAIM_NONE",
        "FINISH_EXCEPTION",
        "FINISH_FALSE",
        "FINISH_TRUE",
        "LEASE_LOST",
        "NONE",
    }
)
_INDEX_LEASE_SOURCES = frozenset(
    {
        "ADAPTER",
        "FINISH_DONE",
        "MUTATION_FENCE",
        "NONE",
        "RENEW",
        "SCOPE_LOCK",
        "UNKNOWN",
    }
)
_INDEX_PROCESS_OUTCOME: ContextVar[object | None] = ContextVar(
    "material_rag_index_process_outcome", default=None
)
_INDEX_SQLSTATE_RE = re.compile(r"^[A-Z0-9]{5}$")
_INDEX_EVIDENCE_CHECKPOINTS = frozenset(
    {
        "CANONICAL_UNITS_EMPTY",
        "CONFLICT_ACCEPTED",
        "CONFLICT_IDENTITY",
        "CONFLICT_MUTATED",
        "CONFLICT_PERSIST",
        "JOB_ROW_MISSING",
        "NONE",
        "PRIMARY_ATTEST_COUNTS",
        "PRIMARY_ATTEST_REMOTE",
        "PRIMARY_FINGERPRINT",
        "PRIMARY_JOB",
        "PRIMARY_PROCESS",
        "REMOTE_SNAPSHOT",
        "REMOTE_TAGS",
        "SNAPSHOT_EXIT",
        "SNAPSHOT_LOAD",
        "SNAPSHOT_OPEN",
        "REPLAY_COUNTS",
        "REPLAY_JOB",
        "REPLAY_PROCESS",
        "REPLAY_REMOTE",
        "SYNTHETIC_COUNTS",
        "SYNTHETIC_JOB",
        "SYNTHETIC_PROCESS",
        "SYNTHETIC_REMOTE",
        "SYNTHETIC_SCOPES",
        "UNKNOWN",
    }
)
_INTERNAL_EVIDENCE_PREFIX = "LOCAL_MATERIAL_RAG_INTERNAL_EVIDENCE "
_INTERNAL_EVIDENCE_PHASES = frozenset(
    {
        "ASSERT_RUNTIME",
        "DISPOSE_ENGINES",
        "FINAL_AUDIT",
        "IMPORT_SCANNER",
        "LOAD_FIXTURES",
        "PJ_CONTEXT_GUARDS",
        "PJ_DELETE",
        "PJ_FINAL_AUDIT",
        "PJ_IMPORT_INIT",
        "PJ_INDEX_REPLAY",
        "PJ_PRIMARY_ATTEST",
        "PJ_PRIMARY_INDEX",
        "PJ_REBUILD",
        "PJ_SCOPED_RETRIEVAL",
        "PJ_SCOPE_ISOLATION",
        "PJ_SYNTHETIC_INDEX",
        "PROVIDER_ATTESTATION",
        "SEED_DATABASE",
        "SETUP_UPLOAD",
        "STORAGE_ACTIVATE",
        "STORAGE_CLEANUP",
        "UNKNOWN",
    }
)
_INTERNAL_EVIDENCE_ERROR_CLASSES = frozenset(
    {
        "ASSERTION_ERROR",
        "ATTRIBUTE_ERROR",
        "CANCELLED_ERROR",
        "DB_DATA",
        "DB_INTEGRITY",
        "DB_INTERFACE",
        "DB_INTERNAL",
        "DB_INVALID_REQUEST",
        "DB_MISSING_GREENLET",
        "DB_NOT_SUPPORTED",
        "DB_OPERATIONAL",
        "DB_OTHER",
        "DB_PENDING_ROLLBACK",
        "DB_PROGRAMMING",
        "DB_STATEMENT",
        "EXCEPTION_GROUP",
        "IMPORT_ERROR",
        "INDEX_ERROR",
        "KEY_ERROR",
        "OS_ERROR",
        "OTHER",
        "RUNTIME_ERROR",
        "TIMEOUT",
        "TYPE_ERROR",
        "UNKNOWN",
        "VALUE_ERROR",
    }
)
_INTERNAL_EVIDENCE_SQLSTATE_RE = re.compile(r"\A[A-Z0-9]{5}\Z")
_INTERNAL_EVIDENCE_DB_TOKENS = frozenset(
    {
        "MATERIAL_RAG_BINDING_IDENTITY_IMMUTABLE",
        "MATERIAL_RAG_DOWNGRADE_DATA_PRESENT",
        "MATERIAL_RAG_JOB_CLAIM_INVALID",
        "MATERIAL_RAG_JOB_IDENTITY_IMMUTABLE",
        "MATERIAL_RAG_JOB_OUTCOME_INVALID",
        "MATERIAL_RAG_JOB_SOURCE_IDENTITY_INVALID",
        "MATERIAL_RAG_JOB_SOURCE_NOT_RELEASED",
        "MATERIAL_RAG_JOB_TRANSITION_INVALID",
        "MATERIAL_RAG_UNIT_IMMUTABLE",
        "MATERIAL_RAG_UNIT_SOURCE_NOT_RELEASED",
        "QA_CLAIM_INVALID",
        "QA_COMPLETE_INVALID",
        "QA_OUTCOME_STATE_INVALID",
        "TEXT_NUL",
    }
)
_INTERNAL_EVIDENCE_DB_MESSAGE_TOKENS = {
    "PostgreSQL text fields cannot contain NUL (0x00) bytes": "TEXT_NUL",
}
_DB_ERROR_CLASS_BY_NAME = {
    "MissingGreenlet": "DB_MISSING_GREENLET",
    "PendingRollbackError": "DB_PENDING_ROLLBACK",
    "InvalidRequestError": "DB_INVALID_REQUEST",
    "StatementError": "DB_STATEMENT",
    "DataError": "DB_DATA",
    "InterfaceError": "DB_INTERFACE",
    "InternalError": "DB_INTERNAL",
    "NotSupportedError": "DB_NOT_SUPPORTED",
    "OperationalError": "DB_OPERATIONAL",
    "IntegrityError": "DB_INTEGRITY",
    "ProgrammingError": "DB_PROGRAMMING",
}
_INTERNAL_PHASE: ContextVar[str] = ContextVar(
    "material_rag_internal_phase", default="UNKNOWN"
)
_INTERNAL_EVIDENCE_OPERATIONS = frozenset(
    {
        "CANDIDATE_VERIFY",
        "CONTEXT_DERIVE",
        "CRYPTO_PROBE",
        "DB_SNAPSHOT_EXIT",
        "DB_SNAPSHOT_LOAD",
        "DB_SNAPSHOT_OPEN",
        "EGRESS_AUDIT",
        "ENQUEUE_JOB",
        "FINAL_RESIDUE",
        "IMPORTS",
        "JOB_ROW",
        "LOAD_UNITS",
        "CLAIM_JOB",
        "CLAIMED_SESSION",
        "MUTATION_FENCE",
        "PERSIST_UNITS",
        "PROCESS_DEMO_JOB",
        "QA_COMPLETE",
        "QA_RESERVE",
        "REMOTE_SNAPSHOT",
        "RETRIEVAL",
        "RLS_CHECK",
        "SCOPE_LOCK",
        "UNIT_COUNTS",
        "UNKNOWN",
    }
)
_INTERNAL_OPERATION: ContextVar[str] = ContextVar(
    "material_rag_internal_operation", default="UNKNOWN"
)
_BASE_EXCEPTION_GROUP = getattr(
    __import__("builtins"), "BaseExceptionGroup", None
)


def _is_db_error_module(module: str) -> bool:
    return (
        module == "sqlalchemy"
        or module.startswith("sqlalchemy.")
        or module == "psycopg"
        or module.startswith("psycopg.")
        or module == "psycopg2"
        or module.startswith("psycopg2.")
    )


def _unwrap_internal_errors(error: BaseException) -> tuple[BaseException, ...]:
    seen: set[int] = set()
    ordered: list[BaseException] = []
    stack = [error]
    while stack:
        current = stack.pop()
        ident = id(current)
        if ident in seen:
            continue
        seen.add(ident)
        ordered.append(current)
        orig = getattr(current, "orig", None)
        if isinstance(orig, BaseException):
            stack.append(orig)
        cause = current.__cause__
        if isinstance(cause, BaseException):
            stack.append(cause)
        context = current.__context__
        if isinstance(context, BaseException):
            stack.append(context)
    return tuple(ordered)


def _safe_sqlstate(error: BaseException | None) -> str:
    if error is None:
        return "NONE"
    for current in _unwrap_internal_errors(error):
        for attr in ("sqlstate", "pgcode"):
            value = getattr(current, attr, None)
            if isinstance(value, str) and _INTERNAL_EVIDENCE_SQLSTATE_RE.fullmatch(
                value
            ):
                return value
        diag = getattr(current, "diag", None)
        if diag is not None:
            value = getattr(diag, "sqlstate", None)
            if isinstance(value, str) and _INTERNAL_EVIDENCE_SQLSTATE_RE.fullmatch(
                value
            ):
                return value
    return "NONE"


def _token_from_db_text(value: str) -> str | None:
    mapped = _INTERNAL_EVIDENCE_DB_MESSAGE_TOKENS.get(value)
    if mapped is not None:
        return mapped
    if value in _INTERNAL_EVIDENCE_DB_TOKENS:
        return value
    return None


def _safe_db_token(error: BaseException | None) -> str:
    if error is None:
        return "NONE"
    for current in _unwrap_internal_errors(error):
        diag = getattr(current, "diag", None)
        if diag is not None:
            primary = getattr(diag, "message_primary", None)
            if isinstance(primary, str):
                token = _token_from_db_text(primary)
                if token is not None:
                    return token
        args = getattr(current, "args", ())
        if args and isinstance(args[0], str):
            token = _token_from_db_text(args[0])
            if token is not None:
                return token
    return "NONE"


def _classify_db_error(error: BaseException) -> str | None:
    mapped: str | None = None
    db_hit = False
    for cls in type(error).mro():
        module = getattr(cls, "__module__", "") or ""
        if not _is_db_error_module(module):
            continue
        db_hit = True
        candidate = _DB_ERROR_CLASS_BY_NAME.get(cls.__name__)
        if candidate is not None:
            mapped = candidate
            break
    if mapped is not None:
        return mapped
    if db_hit:
        return "DB_OTHER"
    return None


def _classify_internal_error(error: BaseException) -> str:
    db_class = _classify_db_error(error)
    if db_class is not None:
        return db_class
    if _BASE_EXCEPTION_GROUP is not None and isinstance(
        error, _BASE_EXCEPTION_GROUP
    ):
        return "EXCEPTION_GROUP"
    if isinstance(error, asyncio.CancelledError):
        return "CANCELLED_ERROR"
    if isinstance(error, TimeoutError):
        return "TIMEOUT"
    if isinstance(error, AssertionError):
        return "ASSERTION_ERROR"
    if isinstance(error, TypeError):
        return "TYPE_ERROR"
    if isinstance(error, ValueError):
        return "VALUE_ERROR"
    if isinstance(error, IndexError):
        return "INDEX_ERROR"
    if isinstance(error, KeyError):
        return "KEY_ERROR"
    if isinstance(error, AttributeError):
        return "ATTRIBUTE_ERROR"
    if isinstance(error, ImportError):
        return "IMPORT_ERROR"
    if isinstance(error, OSError):
        return "OS_ERROR"
    if isinstance(error, RuntimeError):
        return "RUNTIME_ERROR"
    return "OTHER"


def _enter_internal_phase(phase: str) -> None:
    _INTERNAL_PHASE.set(
        phase if phase in _INTERNAL_EVIDENCE_PHASES else "UNKNOWN"
    )


def _enter_internal_operation(operation: str) -> Token[str]:
    return _INTERNAL_OPERATION.set(
        operation if operation in _INTERNAL_EVIDENCE_OPERATIONS else "UNKNOWN"
    )


class _InternalEvidenceBuffer:
    def __init__(self) -> None:
        self._item: dict[str, object] | None = None

    def clear(self) -> None:
        self._item = None

    def record(
        self,
        error_class: str,
        phase: str,
        primary_preserved: bool,
        source: BaseException | None = None,
    ) -> None:
        if self._item is not None:
            return
        sqlstate = _safe_sqlstate(source)
        db_token = _safe_db_token(source)
        if (
            error_class not in _INTERNAL_EVIDENCE_ERROR_CLASSES
            or phase not in _INTERNAL_EVIDENCE_PHASES
            or type(primary_preserved) is not bool
            or (
                sqlstate != "NONE"
                and _INTERNAL_EVIDENCE_SQLSTATE_RE.fullmatch(sqlstate) is None
            )
            or (db_token != "NONE" and db_token not in _INTERNAL_EVIDENCE_DB_TOKENS)
        ):
            return
        operation = _INTERNAL_OPERATION.get()
        if operation not in _INTERNAL_EVIDENCE_OPERATIONS:
            operation = "UNKNOWN"
        self._item = {
            "db_token": db_token,
            "error_class": error_class,
            "operation": operation,
            "phase": phase,
            "primary_preserved": primary_preserved,
            "sqlstate": sqlstate,
        }

    def mark_primary_preserved(self) -> None:
        if self._item is None:
            return
        self._item["primary_preserved"] = True

    def emit_for_reason(self, reason: str) -> None:
        if reason != "LOCAL_MATERIAL_RAG_INTERNAL_ERROR" or self._item is None:
            return
        line = _INTERNAL_EVIDENCE_PREFIX + json.dumps(
            self._item,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(line.encode("utf-8")) > 1024:
            return
        print(line, file=sys.stderr, flush=True)


_INTERNAL_EVIDENCE = _InternalEvidenceBuffer()


def _index_sqlstate(value: object) -> str:
    if value == "NONE":
        return "NONE"
    if isinstance(value, str) and _INDEX_SQLSTATE_RE.fullmatch(value):
        return value
    return "NONE"


def _index_bool(value: object) -> bool:
    return value is True


def _index_payload(
    *,
    checkpoint: str,
    job_status: str,
    operation: str,
    phase: str,
    reason_token: str,
    outcome: str,
    lease_source: str,
    lease_present: bool,
    lease_live: bool,
    token_match: bool,
    finish_sqlstate: str,
) -> dict[str, object]:
    return {
        "checkpoint": checkpoint,
        "finish_sqlstate": finish_sqlstate,
        "job_status": job_status,
        "lease_live": lease_live,
        "lease_present": lease_present,
        "lease_source": lease_source,
        "operation": operation,
        "outcome": outcome,
        "phase": phase,
        "reason_token": reason_token,
        "token_match": token_match,
    }


class _IndexEvidenceBuffer:
    def __init__(self) -> None:
        self._item: dict[str, object] | None = None

    def clear(self) -> None:
        self._item = None

    def record(
        self,
        checkpoint: str,
        *,
        job_status: str = "NONE",
        reason_token: str = "NONE",
        outcome: str = "NONE",
        lease_source: str = "NONE",
        lease_present: bool = False,
        lease_live: bool = False,
        token_match: bool = False,
        finish_sqlstate: str = "NONE",
    ) -> None:
        if self._item is not None:
            return
        if checkpoint not in _INDEX_EVIDENCE_CHECKPOINTS:
            checkpoint = "UNKNOWN"
        if job_status not in _INDEX_JOB_STATUSES and job_status != "NONE":
            job_status = "NONE"
        if reason_token not in _INDEX_REASON_TOKENS and reason_token != "NONE":
            reason_token = "NONE"
        if outcome not in _INDEX_OUTCOMES:
            outcome = "NONE"
        if lease_source not in _INDEX_LEASE_SOURCES:
            lease_source = "NONE"
        operation = _INTERNAL_OPERATION.get()
        if operation not in _INTERNAL_EVIDENCE_OPERATIONS:
            operation = "UNKNOWN"
        phase = _INTERNAL_PHASE.get()
        if phase not in _INTERNAL_EVIDENCE_PHASES:
            phase = "UNKNOWN"
        self._item = _index_payload(
            checkpoint=checkpoint,
            job_status=job_status,
            operation=operation,
            phase=phase,
            reason_token=reason_token,
            outcome=outcome,
            lease_source=lease_source,
            lease_present=_index_bool(lease_present),
            lease_live=_index_bool(lease_live),
            token_match=_index_bool(token_match),
            finish_sqlstate=_index_sqlstate(finish_sqlstate),
        )

    def emit_for_reason(self, reason: str) -> None:
        if reason != "LOCAL_MATERIAL_RAG_INDEX_FAILED":
            return
        item = self._item
        if item is None:
            operation = _INTERNAL_OPERATION.get()
            if operation not in _INTERNAL_EVIDENCE_OPERATIONS:
                operation = "UNKNOWN"
            phase = _INTERNAL_PHASE.get()
            if phase not in _INTERNAL_EVIDENCE_PHASES:
                phase = "UNKNOWN"
            item = _index_payload(
                checkpoint="NONE",
                job_status="NONE",
                operation=operation,
                phase=phase,
                reason_token="NONE",
                outcome="NONE",
                lease_source="NONE",
                lease_present=False,
                lease_live=False,
                token_match=False,
                finish_sqlstate="NONE",
            )
        line = _INDEX_EVIDENCE_PREFIX + json.dumps(
            item,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(line.encode("utf-8")) > 1024:
            return
        print(line, file=sys.stderr, flush=True)


_INDEX_EVIDENCE = _IndexEvidenceBuffer()

_EGRESS_EVIDENCE_PREFIX = "LOCAL_MATERIAL_RAG_EGRESS_EVIDENCE "
_EGRESS_COUNTER_KEYS = (
    "authorized_embedding_request_count",
    "forwarded_embedding_request_count",
    "rejected_json_count",
    "rejected_model_count",
    "rejected_non_text_input_count",
    "rejected_path_count",
    "rejected_request_count",
    "rejected_unauthorized_text_count",
    "upstream_2xx_count",
    "upstream_4xx_count",
    "upstream_5xx_count",
)
_EGRESS_EVIDENCE_KEYS = frozenset(("audit_status",) + _EGRESS_COUNTER_KEYS)
_EGRESS_AUDIT_STATUSES = frozenset({"INVALID", "MISSING", "READY", "UNAVAILABLE"})


def _index_failure_egress_payload() -> dict[str, object]:
    zeros: dict[str, object] = {key: 0 for key in _EGRESS_COUNTER_KEYS}
    missing = dict(zeros)
    missing["audit_status"] = "MISSING"
    invalid = dict(zeros)
    invalid["audit_status"] = "INVALID"
    unavailable = dict(zeros)
    unavailable["audit_status"] = "UNAVAILABLE"
    raw_path = os.environ.get(
        "F1_MATERIAL_RAG_EGRESS_AUDIT_FILE",
        "/run/material-rag-egress/audit.json",
    )
    if not isinstance(raw_path, str) or not raw_path:
        return missing
    try:
        path = Path(raw_path)
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            return missing
    except OSError:
        return unavailable
    try:
        body = _read_regular_unhashed(path, maximum_bytes=4096)
    except MaterialRagVerifyError:
        return invalid
    except Exception:
        return unavailable
    try:
        value = json.loads(body.decode("ascii", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return invalid
    if not isinstance(value, dict):
        return invalid
    payload: dict[str, object] = {"audit_status": "READY"}
    for key in _EGRESS_COUNTER_KEYS:
        raw = value.get(key)
        if type(raw) is not int or raw < 0:
            return invalid
        payload[key] = raw
    return payload


class _EgressEvidenceBuffer:
    def clear(self) -> None:
        return

    def emit_for_reason(self, reason: str) -> None:
        if reason != "LOCAL_MATERIAL_RAG_INDEX_FAILED":
            return
        line = _EGRESS_EVIDENCE_PREFIX + json.dumps(
            _index_failure_egress_payload(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(line.encode("utf-8")) > 1024:
            return
        print(line, file=sys.stderr, flush=True)


_EGRESS_EVIDENCE = _EgressEvidenceBuffer()

_RETRIEVAL_EVIDENCE_PREFIX = "LOCAL_MATERIAL_RAG_RETRIEVAL_EVIDENCE "
_RETRIEVAL_EVIDENCE_KEYS = frozenset(
    {
        "checkpoint",
        "citation_mismatch_count",
        "client_a_hit_count",
        "client_a_overlap",
        "client_a_refusal",
        "client_a_refusal_token",
        "client_b_hit_count",
        "client_b_refusal",
        "client_b_refusal_token",
        "cross_ab",
        "cross_ba",
        "fragment_hit_count",
        "provider_hit_count",
        "provider_refusal",
        "provider_refusal_token",
    }
)
_RETRIEVAL_EVIDENCE_CHECKPOINTS = frozenset(
    {
        "CITATION_MATCH",
        "DEMO_FRAGMENT",
        "EXPECTED_COUNT",
        "NONE",
        "SCOPED_SET",
        "UNKNOWN",
    }
)
_RETRIEVAL_REFUSAL_TOKENS = frozenset(
    {
        "NONE",
        "NO_HITS",
        "NOT_CONFIGURED",
        "REJECTED",
        "UNAVAILABLE",
        "UNKNOWN",
    }
)
_RETRIEVAL_REFUSAL_REASON_TO_TOKEN = {
    "ALL_CANDIDATES_REJECTED": "REJECTED",
    "MATERIAL_RAG_NOT_CONFIGURED": "NOT_CONFIGURED",
    "MATERIAL_RAG_UNAVAILABLE": "UNAVAILABLE",
    "NO_HITS": "NO_HITS",
}


def _retrieval_count(value: object) -> int:
    if type(value) is not int or value < 0:
        return 0
    if value > 999:
        return 999
    return value


def _retrieval_flag(value: object) -> int:
    return 1 if value is True else 0


def _retrieval_refusal_token(reason: object) -> str:
    if reason is None:
        return "NONE"
    if not isinstance(reason, str):
        return "UNKNOWN"
    token = _RETRIEVAL_REFUSAL_REASON_TO_TOKEN.get(reason)
    if token in _RETRIEVAL_REFUSAL_TOKENS:
        return token
    return "UNKNOWN"


def _retrieval_payload(
    *,
    checkpoint: str,
    provider_hit_count: int,
    client_a_hit_count: int,
    client_b_hit_count: int,
    provider_refusal: int,
    client_a_refusal: int,
    client_b_refusal: int,
    client_a_overlap: int,
    cross_ab: int,
    cross_ba: int,
    fragment_hit_count: int,
    citation_mismatch_count: int,
    provider_refusal_token: str,
    client_a_refusal_token: str,
    client_b_refusal_token: str,
) -> dict[str, object]:
    return {
        "checkpoint": checkpoint,
        "citation_mismatch_count": citation_mismatch_count,
        "client_a_hit_count": client_a_hit_count,
        "client_a_overlap": client_a_overlap,
        "client_a_refusal": client_a_refusal,
        "client_a_refusal_token": client_a_refusal_token,
        "client_b_hit_count": client_b_hit_count,
        "client_b_refusal": client_b_refusal,
        "client_b_refusal_token": client_b_refusal_token,
        "cross_ab": cross_ab,
        "cross_ba": cross_ba,
        "fragment_hit_count": fragment_hit_count,
        "provider_hit_count": provider_hit_count,
        "provider_refusal": provider_refusal,
        "provider_refusal_token": provider_refusal_token,
    }


class _RetrievalEvidenceBuffer:
    def __init__(self) -> None:
        self._item: dict[str, object] | None = None

    def clear(self) -> None:
        self._item = None

    def record(
        self,
        checkpoint: str,
        *,
        provider_hit_count: object = 0,
        client_a_hit_count: object = 0,
        client_b_hit_count: object = 0,
        provider_refusal: object = False,
        client_a_refusal: object = False,
        client_b_refusal: object = False,
        client_a_overlap: object = False,
        cross_ab: object = False,
        cross_ba: object = False,
        fragment_hit_count: object = 0,
        citation_mismatch_count: object = 0,
        provider_refusal_token: object = "NONE",
        client_a_refusal_token: object = "NONE",
        client_b_refusal_token: object = "NONE",
    ) -> None:
        if self._item is not None:
            return
        if checkpoint not in _RETRIEVAL_EVIDENCE_CHECKPOINTS:
            checkpoint = "UNKNOWN"
        provider_token = (
            provider_refusal_token
            if provider_refusal_token in _RETRIEVAL_REFUSAL_TOKENS
            else "UNKNOWN"
        )
        client_a_token = (
            client_a_refusal_token
            if client_a_refusal_token in _RETRIEVAL_REFUSAL_TOKENS
            else "UNKNOWN"
        )
        client_b_token = (
            client_b_refusal_token
            if client_b_refusal_token in _RETRIEVAL_REFUSAL_TOKENS
            else "UNKNOWN"
        )
        self._item = _retrieval_payload(
            checkpoint=checkpoint,
            provider_hit_count=_retrieval_count(provider_hit_count),
            client_a_hit_count=_retrieval_count(client_a_hit_count),
            client_b_hit_count=_retrieval_count(client_b_hit_count),
            provider_refusal=_retrieval_flag(provider_refusal),
            client_a_refusal=_retrieval_flag(client_a_refusal),
            client_b_refusal=_retrieval_flag(client_b_refusal),
            client_a_overlap=_retrieval_flag(client_a_overlap),
            cross_ab=_retrieval_flag(cross_ab),
            cross_ba=_retrieval_flag(cross_ba),
            fragment_hit_count=_retrieval_count(fragment_hit_count),
            citation_mismatch_count=_retrieval_count(citation_mismatch_count),
            provider_refusal_token=str(provider_token),
            client_a_refusal_token=str(client_a_token),
            client_b_refusal_token=str(client_b_token),
        )

    def emit_for_reason(self, reason: str) -> None:
        if reason != "LOCAL_MATERIAL_RAG_RETRIEVAL_FAILED":
            return
        item = self._item
        if item is None:
            item = _retrieval_payload(
                checkpoint="NONE",
                provider_hit_count=0,
                client_a_hit_count=0,
                client_b_hit_count=0,
                provider_refusal=0,
                client_a_refusal=0,
                client_b_refusal=0,
                client_a_overlap=0,
                cross_ab=0,
                cross_ba=0,
                fragment_hit_count=0,
                citation_mismatch_count=0,
                provider_refusal_token="NONE",
                client_a_refusal_token="NONE",
                client_b_refusal_token="NONE",
            )
        line = _RETRIEVAL_EVIDENCE_PREFIX + json.dumps(
            item,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(line.encode("utf-8")) > 1024:
            return
        print(line, file=sys.stderr, flush=True)


_RETRIEVAL_EVIDENCE = _RetrievalEvidenceBuffer()

_REBUILD_EVIDENCE_PREFIX = "LOCAL_MATERIAL_RAG_REBUILD_EVIDENCE "
_REBUILD_EVIDENCE_KEYS = frozenset(
    {
        "checkpoint",
        "chunk_count",
        "document_count",
        "fingerprint_match",
        "job_status",
        "manifest_match",
        "outcome",
        "reason_token",
        "unit_count_match",
    }
)
_REBUILD_EVIDENCE_CHECKPOINTS = frozenset(
    {
        "FINGERPRINT",
        "JOB_ROW",
        "NONE",
        "PROCESS",
        "REMOTE_SNAPSHOT",
        "REMOTE_TAGS",
        "UNKNOWN",
    }
)
_REBUILD_OUTCOMES = frozenset(
    {
        "CLAIM_NONE",
        "FINISH_EXCEPTION",
        "FINISH_FALSE",
        "FINISH_TRUE",
        "LEASE_LOST",
        "NONE",
        "SUCCESS",
    }
)
_REBUILD_JOB_STATUSES = _INDEX_JOB_STATUSES | {"NONE"}
_REBUILD_REASON_TOKENS = _INDEX_REASON_TOKENS | frozenset(
    {
        "MATERIAL_RAG_RELEASE_FENCE_REQUIRED",
        "MATERIAL_RAG_REMOTE_BODY_MISMATCH",
        "MATERIAL_RAG_REMOTE_CHUNK_INVALID",
        "MATERIAL_RAG_REMOTE_COUNT_MISMATCH",
        "MATERIAL_RAG_REMOTE_DELETE_MISMATCH",
        "MATERIAL_RAG_REMOTE_DOCUMENT_AMBIGUOUS",
        "MATERIAL_RAG_REMOTE_DOCUMENT_INVALID",
        "MATERIAL_RAG_REMOTE_EXTRA_UNIT",
        "MATERIAL_RAG_REMOTE_IDENTITY_INVALID",
        "MATERIAL_RAG_UNIT_DUPLICATE",
        "MATERIAL_RAG_UNIT_SCOPE_MISMATCH",
        "NONE",
    }
)


def _rebuild_count(value: object) -> int:
    if type(value) is not int or value < 0:
        return 0
    if value > 999:
        return 999
    return value


def _rebuild_flag(value: object) -> int:
    return 1 if value is True or value == 1 else 0


def _rebuild_payload(
    *,
    checkpoint: str,
    chunk_count: int,
    document_count: int,
    fingerprint_match: int,
    job_status: str,
    manifest_match: int,
    outcome: str,
    reason_token: str,
    unit_count_match: int,
) -> dict[str, object]:
    return {
        "checkpoint": checkpoint,
        "chunk_count": chunk_count,
        "document_count": document_count,
        "fingerprint_match": fingerprint_match,
        "job_status": job_status,
        "manifest_match": manifest_match,
        "outcome": outcome,
        "reason_token": reason_token,
        "unit_count_match": unit_count_match,
    }


class _RebuildEvidenceBuffer:
    def __init__(self) -> None:
        self._item: dict[str, object] | None = None

    def clear(self) -> None:
        self._item = None

    def record(
        self,
        checkpoint: str,
        *,
        chunk_count: object = 0,
        document_count: object = 0,
        fingerprint_match: object = 0,
        job_status: object = "NONE",
        manifest_match: object = 0,
        outcome: object = "NONE",
        reason_token: object = "NONE",
        unit_count_match: object = 0,
    ) -> None:
        if self._item is not None:
            return
        if checkpoint not in _REBUILD_EVIDENCE_CHECKPOINTS:
            checkpoint = "UNKNOWN"
        if job_status not in _REBUILD_JOB_STATUSES:
            job_status = "NONE"
        if outcome not in _REBUILD_OUTCOMES:
            outcome = "NONE"
        if reason_token not in _REBUILD_REASON_TOKENS:
            reason_token = "NONE"
        self._item = _rebuild_payload(
            checkpoint=checkpoint,
            chunk_count=_rebuild_count(chunk_count),
            document_count=_rebuild_count(document_count),
            fingerprint_match=_rebuild_flag(fingerprint_match),
            job_status=str(job_status),
            manifest_match=_rebuild_flag(manifest_match),
            outcome=str(outcome),
            reason_token=str(reason_token),
            unit_count_match=_rebuild_flag(unit_count_match),
        )

    def emit_for_reason(self, reason: str) -> None:
        if reason != "LOCAL_MATERIAL_RAG_REBUILD_FAILED":
            return
        item = self._item
        if item is None:
            item = _rebuild_payload(
                checkpoint="NONE",
                chunk_count=0,
                document_count=0,
                fingerprint_match=0,
                job_status="NONE",
                manifest_match=0,
                outcome="NONE",
                reason_token="NONE",
                unit_count_match=0,
            )
        line = _REBUILD_EVIDENCE_PREFIX + json.dumps(
            item,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(line.encode("utf-8")) > 1024:
            return
        print(line, file=sys.stderr, flush=True)


_REBUILD_EVIDENCE = _RebuildEvidenceBuffer()


def _fail_index(checkpoint: str) -> None:
    _INDEX_EVIDENCE.record(checkpoint)
    raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")


def _fail_retrieval(checkpoint: str, **counts: object) -> None:
    _RETRIEVAL_EVIDENCE.record(checkpoint, **counts)
    raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_RETRIEVAL_FAILED")


def _fail_fixed(reason: str, checkpoint: str) -> None:
    if reason == "LOCAL_MATERIAL_RAG_INDEX_FAILED":
        _INDEX_EVIDENCE.record(checkpoint)
    elif reason == "LOCAL_MATERIAL_RAG_REBUILD_FAILED":
        rebuild_checkpoint = (
            checkpoint if checkpoint in _REBUILD_EVIDENCE_CHECKPOINTS else "UNKNOWN"
        )
        _REBUILD_EVIDENCE.record(rebuild_checkpoint)
    raise MaterialRagVerifyError(reason)


async def _record_index_job(
    checkpoint: str,
    job_id: uuid.UUID,
    *,
    outcome: object | None = None,
) -> None:
    job_status = "NONE"
    reason_token = "NONE"
    lease_present = False
    lease_live = False
    try:
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
                        "SELECT status,error_reason,"
                        "(lease_token IS NOT NULL) AS lease_present,"
                        "(lease_token IS NOT NULL AND "
                        "lease_until > clock_timestamp()) AS lease_live "
                        "FROM f1.material_rag_job WHERE id=:id"
                    ),
                    {"id": job_id},
                )
            ).mappings().one_or_none()
        if row is not None:
            status = row["status"]
            if status in _INDEX_JOB_STATUSES:
                job_status = status
            raw_reason = row["error_reason"]
            if isinstance(raw_reason, str) and raw_reason in _INDEX_REASON_TOKENS:
                reason_token = raw_reason
            lease_present = row["lease_present"] is True
            lease_live = row["lease_live"] is True
    except Exception:
        pass
    outcome_kind = getattr(outcome, "kind", "NONE")
    if outcome_kind not in _INDEX_OUTCOMES:
        outcome_kind = "NONE"
    lease_source = getattr(outcome, "lease_source", "NONE")
    if lease_source not in _INDEX_LEASE_SOURCES:
        lease_source = "NONE"
    _INDEX_EVIDENCE.record(
        checkpoint,
        job_status=job_status,
        reason_token=reason_token,
        outcome=outcome_kind,
        lease_source=lease_source,
        lease_present=lease_present,
        lease_live=lease_live,
        token_match=getattr(outcome, "token_match", False) is True,
        finish_sqlstate=_index_sqlstate(getattr(outcome, "finish_sqlstate", "NONE")),
    )


async def _raise_index_failed(
    job_id: uuid.UUID,
    checkpoint: str,
    *,
    outcome: object | None = None,
) -> None:
    if outcome is None:
        outcome = _INDEX_PROCESS_OUTCOME.get()
    await _record_index_job(checkpoint, job_id, outcome=outcome)
    raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INDEX_FAILED")


async def _fail_rebuild(
    checkpoint: str,
    job_id: uuid.UUID | None = None,
    *,
    outcome: object | None = None,
    document_count: object = 0,
    chunk_count: object = 0,
    fingerprint_match: object = False,
    manifest_match: object = False,
    unit_count_match: object = False,
) -> None:
    job_status = "NONE"
    reason_token = "NONE"
    if job_id is not None:
        try:
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
                            "SELECT status,error_reason "
                            "FROM f1.material_rag_job WHERE id=:id"
                        ),
                        {"id": job_id},
                    )
                ).mappings().one_or_none()
            if row is not None:
                status = row["status"]
                if status in _REBUILD_JOB_STATUSES:
                    job_status = status
                raw_reason = row["error_reason"]
                if isinstance(raw_reason, str) and raw_reason in _REBUILD_REASON_TOKENS:
                    reason_token = raw_reason
        except Exception:
            pass
    outcome_kind = getattr(outcome, "kind", "NONE")
    if outcome_kind not in _REBUILD_OUTCOMES:
        outcome_kind = "NONE"
    _REBUILD_EVIDENCE.record(
        checkpoint,
        job_status=job_status,
        reason_token=reason_token,
        outcome=outcome_kind,
        document_count=document_count,
        chunk_count=chunk_count,
        fingerprint_match=fingerprint_match,
        manifest_match=manifest_match,
        unit_count_match=unit_count_match,
    )
    raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_REBUILD_FAILED")


@contextlib.contextmanager
def _internal_phase(phase: str):
    resolved = phase if phase in _INTERNAL_EVIDENCE_PHASES else "UNKNOWN"
    token = _INTERNAL_PHASE.set(resolved)
    try:
        yield
    except BaseException as error:
        if not isinstance(error, MaterialRagVerifyError):
            _INTERNAL_EVIDENCE.record(
                _classify_internal_error(error),
                _INTERNAL_PHASE.get(),
                True,
                source=error,
            )
        raise
    else:
        _INTERNAL_PHASE.reset(token)


def _preserve_primary_error(
    primary: BaseException | None, overlay: BaseException
) -> BaseException:
    if primary is not None:
        _INTERNAL_EVIDENCE.mark_primary_preserved()
        return primary
    _INTERNAL_EVIDENCE.record(
        _classify_internal_error(overlay),
        "DISPOSE_ENGINES",
        False,
        source=overlay,
    )
    return MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INTERNAL_ERROR")


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
    from platform_foundation.f1.features.p3.scanner import (
        ScanFailure,
        clear_scanner_evidence,
        scanner_version,
    )

    started = time.monotonic()
    last: ScanFailure | None = None
    while time.monotonic() - started < 60:
        try:
            scanner_version(timeout_seconds=10)
            clear_scanner_evidence()
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


_SCANNER_EVIDENCE_CODE_TO_REASON = {
    "P3_SCAN_ENGINE_ERROR": "LOCAL_MATERIAL_RAG_P3_SCAN_ENGINE_FAILED",
    "P3_SCAN_PROTOCOL_ERROR": "LOCAL_MATERIAL_RAG_P3_SCAN_PROTOCOL_FAILED",
    "P3_SCANNER_CONNECT_PIPE": "LOCAL_MATERIAL_RAG_P3_SCAN_CONNECT_PIPE_FAILED",
    "P3_SCANNER_CONNECT_REFUSED": "LOCAL_MATERIAL_RAG_P3_SCAN_CONNECT_REFUSED_FAILED",
    "P3_SCANNER_CONNECT_RESET": "LOCAL_MATERIAL_RAG_P3_SCAN_CONNECT_RESET_FAILED",
    "P3_SCANNER_DNS_FAILED": "LOCAL_MATERIAL_RAG_P3_SCAN_DNS_FAILED",
    "P3_SCANNER_STREAM_PIPE": "LOCAL_MATERIAL_RAG_P3_SCAN_STREAM_PIPE_FAILED",
    "P3_SCANNER_STREAM_REFUSED": "LOCAL_MATERIAL_RAG_P3_SCAN_STREAM_REFUSED_FAILED",
    "P3_SCANNER_STREAM_RESET": "LOCAL_MATERIAL_RAG_P3_SCAN_STREAM_RESET_FAILED",
    "P3_SCANNER_TARGET_INVALID": "LOCAL_MATERIAL_RAG_P3_SCAN_TARGET_FAILED",
    "P3_SCANNER_TIMEOUT": "LOCAL_MATERIAL_RAG_P3_SCAN_TIMEOUT_FAILED",
    "P3_SCANNER_UNAVAILABLE": "LOCAL_MATERIAL_RAG_P3_SCAN_CONNECT_FAILED",
    "P3_SCANNER_VERSION_PIPE": "LOCAL_MATERIAL_RAG_P3_SCAN_VERSION_PIPE_FAILED",
    "P3_SCANNER_VERSION_REFUSED": "LOCAL_MATERIAL_RAG_P3_SCAN_VERSION_REFUSED_FAILED",
    "P3_SCANNER_VERSION_RESET": "LOCAL_MATERIAL_RAG_P3_SCAN_VERSION_RESET_FAILED",
}
_P3_SCAN_EVIDENCE_REASONS = frozenset(_SCANNER_EVIDENCE_CODE_TO_REASON.values())
_SCANNER_EVIDENCE_PREFIX = "LOCAL_MATERIAL_RAG_SCANNER_EVIDENCE "
_SCANNER_EVIDENCE_KEYS = (
    "attempt_count",
    "operation",
    "phase",
    "response_class",
    "scan_code",
)
_SCANNER_EVIDENCE_OPERATIONS = frozenset({"INSTREAM", "VERSION"})
_SCANNER_EVIDENCE_PHASES = frozenset({"CONNECT", "PARSE", "RECV", "RESOLVE", "SEND"})
_SCANNER_EVIDENCE_RESPONSE_CLASSES = frozenset(
    {"EMPTY", "ENGINE_ERROR", "FORMAT_MISMATCH", "NOT_APPLICABLE", "OVERSIZE"}
)
_SCANNER_EVIDENCE_SCAN_CODES = frozenset(_SCANNER_EVIDENCE_CODE_TO_REASON)
_MAX_SCANNER_ATTEMPT_COUNT = 64
_MAX_SCANNER_EVIDENCE_BYTES = 1024


def _canonicalize_scanner_evidence(payload: object) -> dict[str, object] | None:
    if not isinstance(payload, dict) or set(payload) != set(_SCANNER_EVIDENCE_KEYS):
        return None
    attempt_count = payload.get("attempt_count")
    operation = payload.get("operation")
    phase = payload.get("phase")
    response_class = payload.get("response_class")
    scan_code = payload.get("scan_code")
    if (
        type(attempt_count) is not int
        or not 1 <= attempt_count <= _MAX_SCANNER_ATTEMPT_COUNT
        or operation not in _SCANNER_EVIDENCE_OPERATIONS
        or phase not in _SCANNER_EVIDENCE_PHASES
        or response_class not in _SCANNER_EVIDENCE_RESPONSE_CLASSES
        or scan_code not in _SCANNER_EVIDENCE_SCAN_CODES
    ):
        return None
    return {
        "attempt_count": attempt_count,
        "operation": operation,
        "phase": phase,
        "response_class": response_class,
        "scan_code": scan_code,
    }


def _scanner_evidence_line(payload: dict[str, object]) -> str | None:
    line = _SCANNER_EVIDENCE_PREFIX + json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(line.encode("utf-8")) > _MAX_SCANNER_EVIDENCE_BYTES:
        return None
    return line


class _ScannerEvidenceBuffer:
    def __init__(self) -> None:
        self._group: list[dict[str, object]] = []

    def clear(self) -> None:
        self._group = []

    def record(self, payload: object) -> None:
        canonical = _canonicalize_scanner_evidence(payload)
        if canonical is None:
            return
        if self._group and self._group[-1]["operation"] != canonical["operation"]:
            self._group = []
        self._group.append(canonical)

    def __call__(self, payload: object) -> None:
        self.record(payload)

    def emit_for_reason(self, reason: str) -> None:
        if reason not in _P3_SCAN_EVIDENCE_REASONS or not self._group:
            return
        payload = dict(self._group[-1])
        payload["attempt_count"] = min(len(self._group), _MAX_SCANNER_ATTEMPT_COUNT)
        if _SCANNER_EVIDENCE_CODE_TO_REASON.get(payload["scan_code"]) != reason:
            return
        line = _scanner_evidence_line(payload)
        if line is None:
            return
        print(line, file=sys.stderr, flush=True)


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
        _fail_index("CANONICAL_UNITS_EMPTY")
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
    _enter_internal_operation("JOB_ROW")
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
        _fail_index("JOB_ROW_MISSING")
    return row


async def _unit_counts(scope_id: uuid.UUID) -> tuple[int, int]:
    _enter_internal_operation("UNIT_COUNTS")
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
    _enter_internal_operation("LOAD_UNITS")
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
    version_ids: Iterable[uuid.UUID],
) -> tuple[tuple[object, ...], ...]:
    """Return durable fingerprints for caller-supplied versions only.

    Scope-wide SELECT is intentionally absent. Extra or unknown rows are
    still caught by `_unit_counts` totals. Each known version uses its own
    API session, matching `_load_version_units`.
    """

    from infra.f1 import local_seed
    from platform_foundation.f1.database import session_scope
    from platform_foundation.f1.features.material_rag.repository import (
        load_units_for_version,
    )

    known = tuple(sorted(set(version_ids)))
    fingerprints: list[tuple[object, ...]] = []
    operation_token = _enter_internal_operation("DB_SNAPSHOT_OPEN")
    try:
        for version_id in known:
            _enter_internal_operation("DB_SNAPSHOT_LOAD")
            async with session_scope(
                role="f1_api",
                enterprise_id=local_seed.ENTERPRISE_A,
                sub=local_seed.ADMIN_SUB,
            ) as session:
                loaded = await load_units_for_version(
                    session,
                    enterprise_id=local_seed.ENTERPRISE_A,
                    knowledge_scope_id=scope_id,
                    document_version_id=version_id,
                )
                fingerprints.extend(_unit_fingerprint(unit) for unit in loaded)
                await session.rollback()
        _enter_internal_operation("DB_SNAPSHOT_EXIT")
        return tuple(sorted(fingerprints))
    except MaterialRagVerifyError:
        raise
    except BaseException as error:
        mapped = None
        for current_error in _unwrap_internal_errors(error):
            mapped = _classify_db_error(current_error)
            if mapped is not None:
                break
        if mapped is None:
            raise
        sqlstate = _safe_sqlstate(error)
        current = _INTERNAL_OPERATION.get()
        if current == "DB_SNAPSHOT_LOAD":
            _INDEX_EVIDENCE.record("SNAPSHOT_LOAD", finish_sqlstate=sqlstate)
            _fail_index("SNAPSHOT_LOAD")
        if current == "DB_SNAPSHOT_EXIT":
            _INDEX_EVIDENCE.record("SNAPSHOT_EXIT", finish_sqlstate=sqlstate)
            _fail_index("SNAPSHOT_EXIT")
        _INDEX_EVIDENCE.record("SNAPSHOT_OPEN", finish_sqlstate=sqlstate)
        _fail_index("SNAPSHOT_OPEN")
    finally:
        _INTERNAL_OPERATION.reset(operation_token)


async def _action_job_count(scope_id: uuid.UUID, action: str) -> int:
    _enter_internal_operation("JOB_ROW")
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
    claim: object,
    original: object,
    version_ids: Iterable[uuid.UUID],
) -> int:
    """Exercise the real persistence conflict path and prove no row changed."""

    from platform_foundation.f1.features.material_rag import worker as material_rag_worker
    from platform_foundation.f1.features.material_rag.contracts import (
        MaterialRagIntegrityError,
        SensitiveText,
    )

    scope_id = claim.knowledge_scope_id  # type: ignore[attr-defined]
    before = await _scope_unit_db_snapshot(scope_id, version_ids)
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
        _fail_index("CONFLICT_IDENTITY")
    persist_canonical_units = material_rag_worker.persist_canonical_units
    with material_rag_worker.live_scope_job_lock(claim):
        with material_rag_worker.live_source_mutation_fence(claim):
            async with material_rag_worker.claimed_session(claim) as session:
                _enter_internal_operation("PERSIST_UNITS")
                try:
                    await persist_canonical_units(session, (conflict,))
                except MaterialRagIntegrityError as error:
                    await session.rollback()
                    if str(error) != "MATERIAL_UNIT_IDENTITY_CONFLICT":
                        _fail_index("CONFLICT_PERSIST")
                else:
                    await session.rollback()
                    _fail_index("CONFLICT_ACCEPTED")
    if await _scope_unit_db_snapshot(scope_id, version_ids) != before:
        _fail_index("CONFLICT_MUTATED")
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
    _enter_internal_operation("EGRESS_AUDIT")
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


def _chunk_detail_content(detail: object) -> str | None:
    if not isinstance(detail, dict):
        return None
    candidates: list[object] = [detail]
    nested = detail.get("chunk")
    if isinstance(nested, dict):
        candidates.append(nested)
    for item in candidates:
        for key in ("content", "content_with_weight"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _chunk_detail_tags(detail: object, failure_reason: str) -> dict[str, str]:
    if not isinstance(detail, dict):
        _fail_fixed(failure_reason, "REMOTE_TAGS")
    raw = detail.get("tag_kwd")
    if raw in (None, [], ""):
        nested = detail.get("chunk")
        if isinstance(nested, dict):
            raw = nested.get("tag_kwd")
    return _strict_remote_tags(raw, failure_reason)


def _strict_remote_tags(value: object, failure_reason: str) -> dict[str, str]:
    raw_tags = value if isinstance(value, list) else [value] if value else []
    result: dict[str, str] = {}
    for raw in raw_tags:
        key, separator, item = str(raw).partition("=")
        if not separator or not key or key in result:
            _fail_fixed(failure_reason, "REMOTE_TAGS")
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
        _fail_fixed(failure_reason, "REMOTE_TAGS")
    return result


async def _remote_snapshot(
    setup: ProductSetup,
    expected_by_version: Mapping[uuid.UUID, tuple[object, ...]],
    *,
    knowledge_scope_id: uuid.UUID | None = None,
    failure_reason: str,
) -> RemoteScopeSnapshot:
    """Hydrate and reconcile every remote dataset/document/chunk identity."""

    _enter_internal_operation("REMOTE_SNAPSHOT")
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
        _fail_fixed(failure_reason, "REMOTE_SNAPSHOT")

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
            _fail_fixed(failure_reason, "REMOTE_SNAPSHOT")
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
                _fail_fixed(failure_reason, "REMOTE_SNAPSHOT")
            document_name = remote_document_name(source_sha256_values.pop())
            if document_name in expected_documents:
                _fail_fixed(failure_reason, "REMOTE_SNAPSHOT")
            expected_documents[document_name] = version_id
        document_names = [str(item.get("name") or "") for item in documents]
        if (
            len(set(document_names)) != len(documents)
            or set(document_names) != set(expected_documents)
        ):
            _fail_fixed(failure_reason, "REMOTE_SNAPSHOT")
        snapshots: list[RemoteDocumentSnapshot] = []
        remote_document_ids: set[str] = set()
        remote_chunk_ids: set[str] = set()
        seen_unit_ids: set[uuid.UUID] = set()
        for document in documents:
            document_name = str(document.get("name") or "")
            document_id = str(document.get("id") or "")
            if not document_id or document_id in remote_document_ids:
                _fail_fixed(failure_reason, "REMOTE_SNAPSHOT")
            remote_document_ids.add(document_id)
            version_id = expected_documents[document_name]
            expected_units = {
                unit.id: unit  # type: ignore[attr-defined]
                for unit in expected_by_version[version_id]
            }
            if len(expected_units) != len(expected_by_version[version_id]):
                _fail_fixed(failure_reason, "REMOTE_SNAPSHOT")
            chunk_snapshots: list[RemoteChunkSnapshot] = []
            listed_chunks = client.list_chunks(
                token, target_binding.dataset_ref, document_id
            )
            for listed in listed_chunks:
                chunk_id = str(listed.get("id") or listed.get("chunk_id") or "")
                if not chunk_id or chunk_id in remote_chunk_ids:
                    _fail_fixed(failure_reason, "REMOTE_SNAPSHOT")
                remote_chunk_ids.add(chunk_id)
                detail = client.get_chunk(
                    token, target_binding.dataset_ref, document_id, chunk_id
                )
                detail_id = str(detail.get("id") or detail.get("chunk_id") or "")
                if detail_id and detail_id != chunk_id:
                    nested = detail.get("chunk")
                    if isinstance(nested, dict):
                        detail_id = str(
                            nested.get("id") or nested.get("chunk_id") or ""
                        )
                if detail_id and detail_id != chunk_id:
                    _fail_fixed(failure_reason, "REMOTE_SNAPSHOT")
                tags = _chunk_detail_tags(detail, failure_reason)
                content = _chunk_detail_content(detail)
                if not isinstance(content, str) or not content:
                    _fail_fixed(failure_reason, "REMOTE_SNAPSHOT")
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
                    if failure_reason == "LOCAL_MATERIAL_RAG_INDEX_FAILED":
                        _INDEX_EVIDENCE.record("REMOTE_SNAPSHOT")
                    elif failure_reason == "LOCAL_MATERIAL_RAG_REBUILD_FAILED":
                        _REBUILD_EVIDENCE.record("REMOTE_SNAPSHOT")
                    raise MaterialRagVerifyError(failure_reason) from None
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
                    _fail_fixed(failure_reason, "REMOTE_SNAPSHOT")
                seen_unit_ids.add(snapshot.canonical_unit_id)
                chunk_snapshots.append(snapshot)
            if {item.canonical_unit_id for item in chunk_snapshots} != set(
                expected_units
            ):
                _fail_fixed(failure_reason, "REMOTE_SNAPSHOT")
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
            _fail_fixed(failure_reason, "REMOTE_SNAPSHOT")
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
    _enter_internal_operation("FINAL_RESIDUE")
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
    _enter_internal_operation("RLS_CHECK")
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
    _enter_internal_operation("RLS_CHECK")
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
    _enter_internal_phase("PJ_IMPORT_INIT")
    _enter_internal_operation("IMPORTS")
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
    from platform_foundation.f1.features.material_rag import worker as material_rag_worker
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

    _enqueue_job = enqueue_job
    _process_demo_job = process_demo_job
    _process_claimed_demo_job = material_rag_worker.process_claimed_demo_job
    _claim_demo_job = material_rag_worker.claim_demo_job
    _claimed_session = material_rag_worker.claimed_session
    _live_scope_job_lock = material_rag_worker.live_scope_job_lock
    _live_source_mutation_fence = material_rag_worker.live_source_mutation_fence
    _persist_canonical_units = material_rag_worker.persist_canonical_units
    _derive_retrieval_context = derive_retrieval_context
    _reserve_request = reserve_request
    _complete_request = complete_request
    _encrypt_impl = _encrypt
    _decrypt_impl = _decrypt
    _retrieve_authorized_demo_fragment = retrieve_authorized_demo_fragment
    _retrieve_registered_verifier_query = retrieve_registered_verifier_query
    _run_verified_retrieval = run_verified_retrieval
    _verify_remote_candidates = verify_remote_candidates

    async def enqueue_job(*args, **kwargs):
        _enter_internal_operation("ENQUEUE_JOB")
        return await _enqueue_job(*args, **kwargs)

    async def process_demo_job(*args, **kwargs):
        _enter_internal_operation("PROCESS_DEMO_JOB")
        return await _process_demo_job(*args, **kwargs)

    async def process_claimed_demo_job(*args, **kwargs):
        _enter_internal_operation("PROCESS_DEMO_JOB")
        return await _process_claimed_demo_job(*args, **kwargs)

    async def claim_demo_job(*args, **kwargs):
        _enter_internal_operation("CLAIM_JOB")
        return await _claim_demo_job(*args, **kwargs)

    @contextlib.asynccontextmanager
    async def claimed_session(*args, **kwargs):
        _enter_internal_operation("CLAIMED_SESSION")
        async with _claimed_session(*args, **kwargs) as session:
            yield session

    @contextlib.contextmanager
    def live_scope_job_lock(*args, **kwargs):
        _enter_internal_operation("SCOPE_LOCK")
        with _live_scope_job_lock(*args, **kwargs) as locked:
            yield locked

    @contextlib.contextmanager
    def live_source_mutation_fence(*args, **kwargs):
        _enter_internal_operation("MUTATION_FENCE")
        with _live_source_mutation_fence(*args, **kwargs) as fenced:
            yield fenced

    async def persist_canonical_units(*args, **kwargs):
        _enter_internal_operation("PERSIST_UNITS")
        return await _persist_canonical_units(*args, **kwargs)

    material_rag_worker.claim_demo_job = claim_demo_job
    material_rag_worker.process_claimed_demo_job = process_claimed_demo_job
    material_rag_worker.claimed_session = claimed_session
    material_rag_worker.live_scope_job_lock = live_scope_job_lock
    material_rag_worker.live_source_mutation_fence = live_source_mutation_fence
    material_rag_worker.persist_canonical_units = persist_canonical_units

    async def derive_retrieval_context(*args, **kwargs):
        _enter_internal_operation("CONTEXT_DERIVE")
        return await _derive_retrieval_context(*args, **kwargs)

    async def reserve_request(*args, **kwargs):
        _enter_internal_operation("QA_RESERVE")
        return await _reserve_request(*args, **kwargs)

    async def complete_request(*args, **kwargs):
        _enter_internal_operation("QA_COMPLETE")
        return await _complete_request(*args, **kwargs)

    def _encrypt(*args, **kwargs):
        _enter_internal_operation("CRYPTO_PROBE")
        return _encrypt_impl(*args, **kwargs)

    def _decrypt(*args, **kwargs):
        _enter_internal_operation("CRYPTO_PROBE")
        return _decrypt_impl(*args, **kwargs)

    async def retrieve_authorized_demo_fragment(*args, **kwargs):
        _enter_internal_operation("RETRIEVAL")
        return await _retrieve_authorized_demo_fragment(*args, **kwargs)

    async def retrieve_registered_verifier_query(*args, **kwargs):
        _enter_internal_operation("RETRIEVAL")
        return await _retrieve_registered_verifier_query(*args, **kwargs)

    async def run_verified_retrieval(*args, **kwargs):
        _enter_internal_operation("RETRIEVAL")
        return await _run_verified_retrieval(*args, **kwargs)

    async def verify_remote_candidates(*args, **kwargs):
        _enter_internal_operation("CANDIDATE_VERIFY")
        return await _verify_remote_candidates(*args, **kwargs)

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

    _enter_internal_phase("PJ_PRIMARY_INDEX")
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
        processed = await process_demo_job(
            job_id, worker_id="material-rag-verifier", prepare=prepare
        )
        if not processed:
            _INDEX_PROCESS_OUTCOME.set(processed)
            await _raise_index_failed(job_id, "PRIMARY_PROCESS")
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
            await _raise_index_failed(job_id, "PRIMARY_JOB")
        index_manifests[upload.document_version_id] = row["result_manifest_sha256"]
        persisted = await _load_version_units(upload)
        if (
            tuple(_unit_fingerprint(unit) for unit in persisted)
            != tuple(_unit_fingerprint(unit) for unit in units)
        ):
            await _raise_index_failed(job_id, "PRIMARY_FINGERPRINT")
        persisted_by_version[upload.document_version_id] = persisted

    _enter_internal_phase("PJ_PRIMARY_ATTEST")
    unit_count, distinct_count = await _unit_counts(setup.client_a_scope_id)
    expected_unit_count = sum(len(value) for value in persisted_by_version.values())
    if unit_count != expected_unit_count or distinct_count != unit_count:
        _fail_index("PRIMARY_ATTEST_COUNTS")
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
        _fail_index("PRIMARY_ATTEST_REMOTE")
    await _verify_rls(setup, unit_count)

    # A distinct durable index job is the real replay boundary.  Reusing the
    # first idempotency key would only return its completed row and would not
    # exercise canonical persistence or RAGFlow reconciliation a second time.
    _enter_internal_phase("PJ_INDEX_REPLAY")
    known_versions = tuple(sorted(persisted_by_version))
    pre_replay_db = await _scope_unit_db_snapshot(setup.client_a_scope_id, known_versions)
    index_replay_job_count = 0
    unit_identity_conflict_rejection_count = 0
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
        claim = await claim_demo_job(
            replay_job_id,
            worker_id="material-rag-verifier",
        )
        if claim is None:
            await _raise_index_failed(
                replay_job_id, "REPLAY_CLAIM", outcome=claim
            )
        units, proof = prepare_replay(claim)
        if index_replay_job_count == 0:
            unit_identity_conflict_rejection_count = (
                await _prove_unit_identity_conflict_rejected(
                    claim,
                    persisted_by_version[upload.document_version_id][0],
                    known_versions,
                )
            )
        processed = await process_claimed_demo_job(
            claim,
            units=units,
            manifest_proof=proof,
        )
        if not processed:
            await _raise_index_failed(
                replay_job_id, "REPLAY_PROCESS", outcome=processed
            )
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
            or await _scope_unit_db_snapshot(setup.client_a_scope_id, known_versions)
            != pre_replay_db
        ):
            await _raise_index_failed(replay_job_id, "REPLAY_JOB")
        replayed_remote = await _remote_snapshot(
            setup,
            persisted_by_version,
            failure_reason="LOCAL_MATERIAL_RAG_INDEX_FAILED",
        )
        if replayed_remote != indexed_remote:
            await _raise_index_failed(replay_job_id, "REPLAY_REMOTE")
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
        _fail_index("REPLAY_COUNTS")
    await _verify_rls(setup, unit_count, job_count=index_job_count)

    _enter_internal_phase("PJ_CONTEXT_GUARDS")
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

    _enter_internal_phase("PJ_SYNTHETIC_INDEX")
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
        processed = await process_demo_job(
            synthetic_job_id,
            worker_id="material-rag-verifier",
            prepare=prepare_synthetic,
        )
        if not processed:
            await _raise_index_failed(
                synthetic_job_id, "SYNTHETIC_PROCESS", outcome=processed
            )
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
            await _raise_index_failed(synthetic_job_id, "SYNTHETIC_JOB")
        synthetic_by_scope[synthetic.knowledge_scope_id] = persisted_synthetic
        synthetic_index_job_count += 1
    if (
        synthetic_index_job_count != 2
        or set(synthetic_by_scope)
        != {setup.provider_scope_id, setup.client_b_scope_id}
        or await _action_job_count(setup.provider_scope_id, "index") != 1
        or await _action_job_count(setup.client_b_scope_id, "index") != 1
    ):
        _fail_index("SYNTHETIC_COUNTS")

    _enter_internal_phase("PJ_SCOPE_ISOLATION")
    provider_synthetic = setup.synthetic_documents[0]
    client_b_synthetic = setup.synthetic_documents[1]
    if (
        provider_synthetic.knowledge_scope_id != setup.provider_scope_id
        or client_b_synthetic.knowledge_scope_id != setup.client_b_scope_id
    ):
        _fail_index("SYNTHETIC_SCOPES")
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
        _fail_index("SYNTHETIC_REMOTE")
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

    _enter_internal_phase("PJ_SCOPED_RETRIEVAL")
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
    scoped_counts = {
        "provider_hit_count": len(provider_result_ids),
        "client_a_hit_count": len(client_a_result_ids),
        "client_b_hit_count": len(client_b_result_ids),
        "provider_refusal": provider_scoped_result.refusal_reason is not None,
        "client_a_refusal": client_a_scoped_result.refusal_reason is not None,
        "client_b_refusal": client_b_scoped_result.refusal_reason is not None,
        "client_a_overlap": bool(client_a_result_ids & client_a_unit_ids),
        "cross_ab": bool(client_a_result_ids & client_b_unit_ids),
        "cross_ba": bool(client_b_result_ids & client_a_unit_ids),
        "provider_refusal_token": _retrieval_refusal_token(
            provider_scoped_result.refusal_reason
        ),
        "client_a_refusal_token": _retrieval_refusal_token(
            client_a_scoped_result.refusal_reason
        ),
        "client_b_refusal_token": _retrieval_refusal_token(
            client_b_scoped_result.refusal_reason
        ),
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
        _fail_retrieval("SCOPED_SET", **scoped_counts)
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
        _fail_retrieval("EXPECTED_COUNT", **scoped_counts)
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
            _fail_retrieval(
                "DEMO_FRAGMENT",
                **scoped_counts,
                fragment_hit_count=0 if result is None else len(result.evidence),
                client_a_refusal=result is not None
                and result.refusal_reason is not None,
            )
        if not any(
            evidence.canonical_unit_id == exact.id  # type: ignore[attr-defined]
            for evidence in result.evidence
        ):
            _fail_retrieval(
                "DEMO_FRAGMENT",
                **scoped_counts,
                fragment_hit_count=len(result.evidence),
            )
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
                _fail_retrieval(
                    "CITATION_MATCH",
                    **scoped_counts,
                    fragment_hit_count=len(result.evidence),
                    citation_mismatch_count=1,
                )
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

    _enter_internal_phase("PJ_REBUILD")
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
        processed = await process_demo_job(
            job_id, worker_id="material-rag-verifier", prepare=prepare_rebuild
        )
        if not processed:
            await _fail_rebuild("PROCESS", job_id, outcome=processed)
        row = await _job_row(job_id)
        manifest_match = (
            row["result_manifest_sha256"]
            == index_manifests[upload.document_version_id]
        )
        if (
            row["status"] != "done"
            or row["action"] != "rebuild"
            or not manifest_match
        ):
            await _fail_rebuild("JOB_ROW", job_id, outcome=processed, manifest_match=manifest_match)
    rebuilt_count, rebuilt_distinct = await _unit_counts(setup.client_a_scope_id)
    rebuilt_remote = await _remote_snapshot(
        setup,
        persisted_by_version,
        knowledge_scope_id=setup.client_a_scope_id,
        failure_reason="LOCAL_MATERIAL_RAG_REBUILD_FAILED",
    )
    rebuilt_documents = rebuilt_remote.document_count
    rebuilt_chunks = rebuilt_remote.chunk_count
    fingerprint_match = (
        rebuilt_remote.semantic_fingerprint()
        == indexed_remote.semantic_fingerprint()
    )
    unit_count_match = rebuilt_count == unit_count and rebuilt_distinct == unit_count
    if (
        not unit_count_match
        or rebuilt_documents != 4
        or rebuilt_chunks != unit_count
        or not fingerprint_match
    ):
        await _fail_rebuild("FINGERPRINT",
            document_count=rebuilt_documents,
            chunk_count=rebuilt_chunks,
            fingerprint_match=fingerprint_match,
            manifest_match=True,
            unit_count_match=unit_count_match,
        )

    _enter_internal_phase("PJ_DELETE")
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
                knowledge_scope_id=setup.client_a_scope_id,
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

    _enter_internal_phase("PJ_FINAL_AUDIT")
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
    primary: BaseException | None = None
    try:
        with _internal_phase("SETUP_UPLOAD"):
            setup = await _setup_and_upload(fixtures)
        rag_run = await _process_jobs(fixtures, setup)
        return setup, rag_run
    except BaseException as error:
        primary = error
        if not isinstance(error, MaterialRagVerifyError):
            _INTERNAL_EVIDENCE.record(
                _classify_internal_error(error),
                _INTERNAL_PHASE.get(),
                True,
                source=error,
            )
        raise
    finally:
        try:
            await _dispose_engines()
        except BaseException as overlay:
            kept = _preserve_primary_error(primary, overlay)
            if primary is None:
                raise kept from None


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
    with _internal_phase("ASSERT_RUNTIME"):
        _assert_runtime_authorization()
    with _internal_phase("PROVIDER_ATTESTATION"):
        _assert_embedding_only_provider_attestation()
    with _internal_phase("LOAD_FIXTURES"):
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
    with _internal_phase("SEED_DATABASE"):
        _seed_database()
    with _internal_phase("STORAGE_ACTIVATE"):
        storage, buckets = _activate_storage_namespace()
    pending: MaterialRagVerifyError | None = None
    setup: ProductSetup | None = None
    rag_run: RagRun | None = None
    try:
        setup, rag_run = asyncio.run(_run_async(fixtures))
    except MaterialRagVerifyError as error:
        pending = error
    except BaseException as error:
        _INTERNAL_EVIDENCE.record(
            _classify_internal_error(error),
            _INTERNAL_PHASE.get(),
            True,
            source=error,
        )
        pending = MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INTERNAL_ERROR")
    try:
        with _internal_phase("STORAGE_CLEANUP"):
            _cleanup_storage(storage, buckets)
    except MaterialRagVerifyError as error:
        if pending is None:
            pending = error
    if pending is not None:
        raise pending
    if setup is None or rag_run is None:
        raise MaterialRagVerifyError("LOCAL_MATERIAL_RAG_INTERNAL_ERROR")
    with _internal_phase("FINAL_AUDIT"):
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
    buffer = _ScannerEvidenceBuffer()
    _INTERNAL_EVIDENCE.clear()
    _INDEX_EVIDENCE.clear()
    _EGRESS_EVIDENCE.clear()
    _RETRIEVAL_EVIDENCE.clear()
    _REBUILD_EVIDENCE.clear()
    try:
        from platform_foundation.f1.features.p3.scanner import scanner_evidence_sink
    except Exception:
        _INTERNAL_EVIDENCE.record("IMPORT_ERROR", "IMPORT_SCANNER", True)
        print("LOCAL_MATERIAL_RAG_INTERNAL_ERROR", file=sys.stderr)
        _INTERNAL_EVIDENCE.emit_for_reason("LOCAL_MATERIAL_RAG_INTERNAL_ERROR")
        return 1
    try:
        with scanner_evidence_sink(buffer):
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
        if not isinstance(error, MaterialRagVerifyError):
            _INTERNAL_EVIDENCE.record(
                _classify_internal_error(error),
                _INTERNAL_PHASE.get(),
                True,
                source=error,
            )
        if isinstance(error, MaterialRagVerifyError):
            _print_p3_preview_evidence(error.evidence)
        print(reason, file=sys.stderr)
        buffer.emit_for_reason(reason)
        _INTERNAL_EVIDENCE.emit_for_reason(reason)
        _INDEX_EVIDENCE.emit_for_reason(reason)
        _EGRESS_EVIDENCE.emit_for_reason(reason)
        _RETRIEVAL_EVIDENCE.emit_for_reason(reason)
        _REBUILD_EVIDENCE.emit_for_reason(reason)
        return 1
    print(json.dumps(asdict(counts), sort_keys=True, separators=(",", ":")))
    print("LOCAL_MATERIAL_RAG_VERIFY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
