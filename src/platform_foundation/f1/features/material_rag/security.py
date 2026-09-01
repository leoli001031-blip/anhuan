"""Canonicalization, PII filtering, and at-rest envelopes for material RAG."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import time
import unicodedata
import uuid
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Literal

from .contracts import (
    CanonicalUnit,
    DemoUnitManifestProof,
    MaterialRagIntegrityError,
    MaterialRagJobClaim,
    SHA256_RE,
    SensitiveText,
)


MATERIAL_RAG_KEY_FILE: Path | None = None
MATERIAL_RAG_MANIFEST_KEY_FILE: Path | None = None
AUTHORIZED_DEMO_SOURCE_SHA256 = frozenset(
    {
        "e64cb41465eaf3fc550dbc881c06d687275a8d2b6850f34c703c111a4a3cfc46",
        "ab242c22f92e73d519c5e5485df7027ad33812e96324943b6591171d0e41fc07",
        "12f20a5a1edf14eb18a77553740b8ab18e49dd7b2c95dcfc3ce22954ea206860",
        "973e6ac91e95489a6b8311a9ca61a1a734b6f3ef08f3b3b6d4713d4b04c4dd0e",
    }
)
PROVIDER_POLICY_CANARY_TEXT = (
    "环保服务共享政策要求：现场服务前核对适用法规、作业边界和应急联系人职责，"
    "发现条件变化时暂停作业并记录复核结论。"
)
CLIENT_B_ISOLATION_CANARY_TEXT = (
    "客户乙验证材料：装置检修前完成能量隔离、气体检测和作业许可复核，"
    "复工前再次确认隔离状态与现场条件。"
)
PROVIDER_RETRIEVAL_QUERY_TEXT = "现场服务前需要复核哪些共享政策要求？"
CLIENT_A_RETRIEVAL_QUERY_TEXT = "这份客户甲材料中有哪些需要现场复核的要求？"
CLIENT_B_RETRIEVAL_QUERY_TEXT = "客户乙装置检修复工前需要确认什么？"
SYNTHETIC_AUTHORIZED_SOURCE_SHA256 = frozenset(
    hashlib.sha256(value.encode("utf-8")).hexdigest()
    for value in (PROVIDER_POLICY_CANARY_TEXT, CLIENT_B_ISOLATION_CANARY_TEXT)
)
AUTHORIZED_MATERIAL_RAG_SOURCE_SHA256 = (
    AUTHORIZED_DEMO_SOURCE_SHA256 | SYNTHETIC_AUTHORIZED_SOURCE_SHA256
)
_REMOTE_DOCUMENT_NAME_BY_SOURCE_SHA256 = MappingProxyType(
    {
        "e64cb41465eaf3fc550dbc881c06d687275a8d2b6850f34c703c111a4a3cfc46":
            "MATERIAL_RAG_DEMO_01",
        "ab242c22f92e73d519c5e5485df7027ad33812e96324943b6591171d0e41fc07":
            "MATERIAL_RAG_DEMO_02",
        "12f20a5a1edf14eb18a77553740b8ab18e49dd7b2c95dcfc3ce22954ea206860":
            "MATERIAL_RAG_DEMO_19",
        "973e6ac91e95489a6b8311a9ca61a1a734b6f3ef08f3b3b6d4713d4b04c4dd0e":
            "MATERIAL_RAG_DEMO_21",
        hashlib.sha256(PROVIDER_POLICY_CANARY_TEXT.encode("utf-8")).hexdigest():
            "MATERIAL_RAG_PROVIDER_POLICY_CANARY",
        hashlib.sha256(CLIENT_B_ISOLATION_CANARY_TEXT.encode("utf-8")).hexdigest():
            "MATERIAL_RAG_CLIENT_B_ISOLATION_CANARY",
    }
)
SYNTHETIC_AUTHORIZED_EMBEDDING_TEXTS = (
    PROVIDER_POLICY_CANARY_TEXT,
    CLIENT_B_ISOLATION_CANARY_TEXT,
    PROVIDER_RETRIEVAL_QUERY_TEXT,
    CLIENT_A_RETRIEVAL_QUERY_TEXT,
    CLIENT_B_RETRIEVAL_QUERY_TEXT,
    *_REMOTE_DOCUMENT_NAME_BY_SOURCE_SHA256.values(),
)
_MAGIC = b"F1MR1"
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Z0-9._%+-]{1,64}@[A-Z0-9.-]{1,253}\.[A-Z]{2,63}(?![\w.-])",
    re.IGNORECASE,
)
_MOBILE_RE = re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d(?:[-\s]?\d){8}(?!\d)")
_LANDLINE_RE = re.compile(
    r"(?<!\d)(?:\+?86[-\s]?)?(?:\(?0\d{2,3}\)?[-\s]?)?"
    r"\d{7,8}(?:[-\s](?:转|ext\.?)?\s*\d{1,6})?(?!\d)",
    re.IGNORECASE,
)
_LABEL_RE = re.compile(
    r"(?im)^(?P<prefix>\s*(?:联系人|联系方式|联系电话|手机(?:号码)?|电话|邮箱|"
    r"电子邮箱|经办人|负责人|签名|签字|签章|盖章|印章|contact(?: person)?|"
    r"phone|telephone|mobile|e-?mail|responsible person|signatory|signature|"
    r"seal|stamp)\s*(?:[:：]|为|是)?)\s*.*$"
)
_INLINE_PERSON_RE = re.compile(
    r"(?P<prefix>(?:联系人|经办人|负责人|contact(?: person)?|"
    r"responsible person|signatory)\s*(?:[:：]|为|是)\s*)"
    r"(?:[\u3400-\u9fff·]{2,12}|[A-Z][A-Za-z'.-]*(?:\s+[A-Z][A-Za-z'.-]*){0,4})",
    re.IGNORECASE,
)
_SIGNATURE_CUE_RE = re.compile(
    r"签名|签字|签章|盖章|印章|公章|signatory|signature|seal|stamp",
    re.IGNORECASE,
)
_REDACTED = "[REDACTED]"
_UNIT_NAMESPACE = uuid.UUID("677865af-80f0-4d52-828b-23259656bb8d")
MAX_UNIT_CHARACTERS = 1_600
MAX_DEMO_SOURCE_BYTES = 64 * 1024 * 1024
MAX_DEMO_CANONICAL_BYTES = 128 * 1024 * 1024
MAX_DEMO_UNITS = 100_000
_MANIFEST_DOMAIN = b"f1.material-rag.demo-unit-manifest.v1\x00"


def _key_path() -> Path:
    if MATERIAL_RAG_KEY_FILE is not None:
        path = MATERIAL_RAG_KEY_FILE
    else:
        raw = os.environ.get("F1_MATERIAL_RAG_KEY_FILE", "").strip()
        if not raw:
            secrets_dir = os.environ.get("F1_SECRETS_DIR", "").strip()
            if secrets_dir:
                raw = str(Path(secrets_dir) / "f1_material_rag_key")
        if not raw:
            raise RuntimeError("MATERIAL_RAG_KEY_UNAVAILABLE")
        path = Path(raw)
    if not path.is_absolute() or path.is_symlink():
        raise RuntimeError("MATERIAL_RAG_KEY_INVALID")
    info = path.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.geteuid()
    ):
        raise RuntimeError("MATERIAL_RAG_KEY_PERMISSIONS")
    return path


def _key_bytes() -> bytes:
    raw = _key_path().read_text(encoding="ascii").strip()
    key = bytes.fromhex(raw) if len(raw) == 64 else raw.encode("utf-8")
    if len(key) not in (16, 24, 32):
        raise RuntimeError("MATERIAL_RAG_KEY_INVALID_LENGTH")
    return key


def _manifest_key_path() -> Path:
    if MATERIAL_RAG_MANIFEST_KEY_FILE is not None:
        path = MATERIAL_RAG_MANIFEST_KEY_FILE
    else:
        raw = os.environ.get("F1_MATERIAL_RAG_MANIFEST_KEY_FILE", "").strip()
        if not raw:
            secrets_dir = os.environ.get("F1_SECRETS_DIR", "").strip()
            if secrets_dir:
                raw = str(Path(secrets_dir) / "f1_material_rag_manifest_key")
        if not raw:
            raise RuntimeError("MATERIAL_RAG_MANIFEST_KEY_UNAVAILABLE")
        path = Path(raw)
    if not path.is_absolute() or path.is_symlink():
        raise RuntimeError("MATERIAL_RAG_MANIFEST_KEY_INVALID")
    info = path.stat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.geteuid()
    ):
        raise RuntimeError("MATERIAL_RAG_MANIFEST_KEY_PERMISSIONS")
    return path


def _manifest_key_bytes() -> bytes:
    raw = _manifest_key_path().read_text(encoding="ascii").strip()
    key = bytes.fromhex(raw) if len(raw) == 64 else raw.encode("utf-8")
    if len(key) != 32:
        raise RuntimeError("MATERIAL_RAG_MANIFEST_KEY_INVALID_LENGTH")
    if hmac.compare_digest(key, _key_bytes()):
        raise RuntimeError("MATERIAL_RAG_MANIFEST_KEY_REUSE_FORBIDDEN")
    return key


def _canonical_unit_identity(unit: CanonicalUnit) -> uuid.UUID:
    identity = (
        f"{unit.enterprise_id}\x00{unit.knowledge_scope_id}\x00"
        f"{unit.document_record_id}\x00{unit.document_version_id}\x00"
        f"{unit.source_sha256}\x00{unit.page_number}\x00{unit.ordinal}\x00"
        f"{unit.parser_version}"
    )
    return uuid.uuid5(_UNIT_NAMESPACE, identity)


def _ordered_manifest_units(
    units: Iterable[CanonicalUnit],
) -> tuple[CanonicalUnit, ...]:
    materialized = tuple(units)
    if not materialized or len(materialized) > MAX_DEMO_UNITS:
        raise MaterialRagIntegrityError("MATERIAL_RAG_MANIFEST_UNITS_MISSING")
    expected_order = tuple(
        sorted(materialized, key=lambda value: (value.page_number, value.ordinal, value.id.hex))
    )
    if materialized != expected_order or len({unit.id for unit in materialized}) != len(
        materialized
    ):
        raise MaterialRagIntegrityError("MATERIAL_RAG_MANIFEST_ORDER_INVALID")
    total_bytes = 0
    for unit in materialized:
        if unit.id != _canonical_unit_identity(unit):
            raise MaterialRagIntegrityError("MATERIAL_RAG_UNIT_IDENTITY_INVALID")
        body = unit.body.reveal()
        if len(body) > MAX_UNIT_CHARACTERS:
            raise MaterialRagIntegrityError("MATERIAL_RAG_UNIT_BODY_TOO_LARGE")
        total_bytes += len(body.encode("utf-8"))
        if total_bytes > MAX_DEMO_CANONICAL_BYTES:
            raise MaterialRagIntegrityError("MATERIAL_RAG_MANIFEST_TOO_LARGE")
        assert_external_text_safe(body)
    return materialized


def _manifest_payload(
    *,
    claim: MaterialRagJobClaim,
    issued_at_epoch: int,
    expires_at_epoch: int,
    units: Iterable[CanonicalUnit],
) -> tuple[bytes, tuple[CanonicalUnit, ...]]:
    ordered = _ordered_manifest_units(units)
    first = ordered[0]
    if claim.action not in {"index", "rebuild"} or claim.attempt < 1:
        raise MaterialRagIntegrityError("MATERIAL_RAG_MANIFEST_JOB_INVALID")
    if any(
        unit.enterprise_id != first.enterprise_id
        or unit.knowledge_scope_id != first.knowledge_scope_id
        or unit.document_record_id != first.document_record_id
        or unit.document_version_id != first.document_version_id
        or unit.source_sha256 != first.source_sha256
        for unit in ordered
    ):
        raise MaterialRagIntegrityError("MATERIAL_RAG_MANIFEST_IDENTITY_INVALID")
    payload = json.dumps(
        {
            "action": claim.action,
            "attempt": claim.attempt,
            "document_record_id": str(first.document_record_id),
            "document_version_id": str(first.document_version_id),
            "enterprise_id": str(first.enterprise_id),
            "expires_at_epoch": expires_at_epoch,
            "issued_at_epoch": issued_at_epoch,
            "job_id": str(claim.id),
            "knowledge_scope_id": str(first.knowledge_scope_id),
            "lease_token": str(claim.lease_token),
            "schema_version": 1,
            "source_sha256": first.source_sha256,
            "unit_count": len(ordered),
            "units": [
                {
                    "body_sha256": unit.body_sha256,
                    "id": str(unit.id),
                    "ocr_applied": unit.ocr_applied,
                    "ordinal": unit.ordinal,
                    "page_number": unit.page_number,
                    "parser_version": unit.parser_version,
                    "table_candidate": unit.table_candidate,
                    "two_column_candidate": unit.two_column_candidate,
                }
                for unit in ordered
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return payload, ordered


def _hash_readonly_demo_source(source_path: Path) -> str:
    if not isinstance(source_path, Path) or not source_path.is_absolute():
        raise MaterialRagIntegrityError("MATERIAL_RAG_SOURCE_PATH_INVALID")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source_path, flags)
    except OSError as error:
        raise MaterialRagIntegrityError("MATERIAL_RAG_SOURCE_OPEN_FAILED") from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > MAX_DEMO_SOURCE_BYTES
        ):
            raise MaterialRagIntegrityError("MATERIAL_RAG_SOURCE_FILE_INVALID")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if identity_before != identity_after:
            raise MaterialRagIntegrityError("MATERIAL_RAG_SOURCE_CHANGED")
    finally:
        os.close(descriptor)
    value = digest.hexdigest()
    if value not in AUTHORIZED_DEMO_SOURCE_SHA256:
        raise MaterialRagIntegrityError("MATERIAL_RAG_SOURCE_NOT_AUTHORIZED")
    return value


def create_demo_unit_manifest_proof(
    *,
    source_path: Path,
    claim: MaterialRagJobClaim,
    units: Iterable[CanonicalUnit],
    ttl_seconds: int = 300,
) -> DemoUnitManifestProof:
    """Attest units only after hashing one immutable, allowlisted Demo PDF.

    This function belongs only in the isolated verifier/worker process.  Its
    dedicated key must not be mounted into the product API or RAGFlow.
    """
    if not 30 <= ttl_seconds <= 900:
        raise ValueError("MATERIAL_RAG_MANIFEST_TTL_INVALID")
    issued = int(time.time())
    expires = issued + ttl_seconds
    payload, ordered = _manifest_payload(
        claim=claim,
        issued_at_epoch=issued,
        expires_at_epoch=expires,
        units=units,
    )
    source_sha = _hash_readonly_demo_source(source_path)
    first = ordered[0]
    if (
        claim.action not in {"index", "rebuild"}
        or first.enterprise_id != claim.enterprise_id
        or first.knowledge_scope_id != claim.knowledge_scope_id
        or first.document_record_id != claim.document_record_id
        or first.document_version_id != claim.document_version_id
        or claim.source_sha256 != source_sha
        or any(unit.source_sha256 != source_sha for unit in ordered)
    ):
        raise MaterialRagIntegrityError("MATERIAL_RAG_SOURCE_MANIFEST_MISMATCH")
    manifest_sha = hashlib.sha256(payload).hexdigest()
    signature = hmac.new(
        _manifest_key_bytes(), _MANIFEST_DOMAIN + payload, hashlib.sha256
    ).hexdigest()
    return DemoUnitManifestProof(
        schema_version=1,
        job_id=claim.id,
        action=claim.action,
        attempt=claim.attempt,
        source_sha256=source_sha,
        issued_at_epoch=issued,
        expires_at_epoch=expires,
        manifest_sha256=manifest_sha,
        signature_hex=signature,
    )


def create_synthetic_unit_manifest_proof(
    *,
    claim: MaterialRagJobClaim,
    units: Iterable[CanonicalUnit],
    ttl_seconds: int = 300,
) -> DemoUnitManifestProof:
    """Attest one fixed, PII-free verifier canary without a source file.

    The source identity is the SHA-256 of the exact fixed canary body.  This
    path cannot sign arbitrary generated text: both the source hash and the
    canonical body must match one of the two module constants above.
    """

    if not 30 <= ttl_seconds <= 900:
        raise ValueError("MATERIAL_RAG_MANIFEST_TTL_INVALID")
    issued = int(time.time())
    expires = issued + ttl_seconds
    payload, ordered = _manifest_payload(
        claim=claim,
        issued_at_epoch=issued,
        expires_at_epoch=expires,
        units=units,
    )
    expected_by_source = {
        hashlib.sha256(value.encode("utf-8")).hexdigest(): redact_external_text(value)
        for value in (PROVIDER_POLICY_CANARY_TEXT, CLIENT_B_ISOLATION_CANARY_TEXT)
    }
    expected_body = expected_by_source.get(claim.source_sha256)
    first = ordered[0]
    if (
        claim.action not in {"index", "rebuild"}
        or expected_body is None
        or len(ordered) != 1
        or first.enterprise_id != claim.enterprise_id
        or first.knowledge_scope_id != claim.knowledge_scope_id
        or first.document_record_id != claim.document_record_id
        or first.document_version_id != claim.document_version_id
        or first.source_sha256 != claim.source_sha256
        or first.body.reveal() != expected_body
        or first.body_sha256
        != hashlib.sha256(expected_body.encode("utf-8")).hexdigest()
    ):
        raise MaterialRagIntegrityError("MATERIAL_RAG_SOURCE_MANIFEST_MISMATCH")
    manifest_sha = hashlib.sha256(payload).hexdigest()
    signature = hmac.new(
        _manifest_key_bytes(), _MANIFEST_DOMAIN + payload, hashlib.sha256
    ).hexdigest()
    return DemoUnitManifestProof(
        schema_version=1,
        job_id=claim.id,
        action=claim.action,
        attempt=claim.attempt,
        source_sha256=claim.source_sha256,
        issued_at_epoch=issued,
        expires_at_epoch=expires,
        manifest_sha256=manifest_sha,
        signature_hex=signature,
    )


def create_released_unit_manifest_proof(
    *,
    claim: MaterialRagJobClaim,
    units: Iterable[CanonicalUnit],
    ttl_seconds: int = 300,
) -> DemoUnitManifestProof:
    """Attest units parsed from the exact released source bound to ``claim``.

    The caller must first open the source through the identity-checking P3
    storage reader.  The worker subsequently re-proves the live released state
    under the same lease before persistence and every remote mutation.
    """
    if not 30 <= ttl_seconds <= 900:
        raise ValueError("MATERIAL_RAG_MANIFEST_TTL_INVALID")
    issued = int(time.time())
    expires = issued + ttl_seconds
    payload, ordered = _manifest_payload(
        claim=claim,
        issued_at_epoch=issued,
        expires_at_epoch=expires,
        units=units,
    )
    first = ordered[0]
    if (
        claim.action not in {"index", "rebuild"}
        or first.enterprise_id != claim.enterprise_id
        or first.knowledge_scope_id != claim.knowledge_scope_id
        or first.document_record_id != claim.document_record_id
        or first.document_version_id != claim.document_version_id
        or first.source_sha256 != claim.source_sha256
        or any(unit.source_sha256 != claim.source_sha256 for unit in ordered)
    ):
        raise MaterialRagIntegrityError("MATERIAL_RAG_SOURCE_MANIFEST_MISMATCH")
    manifest_sha = hashlib.sha256(payload).hexdigest()
    signature = hmac.new(
        _manifest_key_bytes(), _MANIFEST_DOMAIN + payload, hashlib.sha256
    ).hexdigest()
    return DemoUnitManifestProof(
        schema_version=1,
        job_id=claim.id,
        action=claim.action,
        attempt=claim.attempt,
        source_sha256=claim.source_sha256,
        issued_at_epoch=issued,
        expires_at_epoch=expires,
        manifest_sha256=manifest_sha,
        signature_hex=signature,
    )


def verify_demo_unit_manifest_proof(
    claim: MaterialRagJobClaim,
    units: Iterable[CanonicalUnit],
    proof: DemoUnitManifestProof,
) -> tuple[CanonicalUnit, ...]:
    """Verify one proof in constant time before persistence or egress."""
    now = int(time.time())
    if (
        not isinstance(proof, DemoUnitManifestProof)
        or proof.job_id != claim.id
        or proof.action != claim.action
        or proof.attempt != claim.attempt
        or proof.source_sha256 != claim.source_sha256
        or proof.issued_at_epoch > now + 5
        or proof.expires_at_epoch < now
        or proof.expires_at_epoch - proof.issued_at_epoch > 900
    ):
        raise MaterialRagIntegrityError("MATERIAL_RAG_MANIFEST_INVALID")
    payload, ordered = _manifest_payload(
        claim=claim,
        issued_at_epoch=proof.issued_at_epoch,
        expires_at_epoch=proof.expires_at_epoch,
        units=units,
    )
    first = ordered[0]
    if (
        first.enterprise_id != claim.enterprise_id
        or first.knowledge_scope_id != claim.knowledge_scope_id
        or first.document_record_id != claim.document_record_id
        or first.document_version_id != claim.document_version_id
        or first.source_sha256 != claim.source_sha256
    ):
        raise MaterialRagIntegrityError("MATERIAL_RAG_MANIFEST_IDENTITY_INVALID")
    manifest_sha = hashlib.sha256(payload).hexdigest()
    signature = hmac.new(
        _manifest_key_bytes(), _MANIFEST_DOMAIN + payload, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(proof.manifest_sha256, manifest_sha) or not hmac.compare_digest(
        proof.signature_hex, signature
    ):
        raise MaterialRagIntegrityError("MATERIAL_RAG_MANIFEST_INVALID")
    return ordered


# Compatibility name retained for verifier callers while the production
# worker uses the source-neutral name.
verify_unit_manifest_proof = verify_demo_unit_manifest_proof


def normalize_text(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("MATERIAL_UNIT_BODY_INVALID")
    normalized = unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    normalized = _CONTROL_RE.sub(" ", normalized)
    lines = [re.sub(r"[ \t\f\v]+", " ", line).strip() for line in normalized.split("\n")]
    collapsed: list[str] = []
    for line in lines:
        if line or (collapsed and collapsed[-1]):
            collapsed.append(line)
    return "\n".join(collapsed).strip()


def redact_external_text(value: str) -> str:
    """Return canonical text safe to send for embedding.

    Redaction happens before hashing or persistence, so the indexed identity
    always describes the filtered body rather than its sensitive precursor.
    """
    text = normalize_text(value)
    text = _LABEL_RE.sub(lambda match: f"{match.group('prefix')}{_REDACTED}", text)
    text = _INLINE_PERSON_RE.sub(lambda match: f"{match.group('prefix')}{_REDACTED}", text)
    text = _EMAIL_RE.sub(_REDACTED, text)
    text = _MOBILE_RE.sub(_REDACTED, text)
    text = _LANDLINE_RE.sub(_REDACTED, text)
    text = "\n".join(
        _REDACTED if _SIGNATURE_CUE_RE.search(line) and _REDACTED not in line else line
        for line in text.splitlines()
    )
    text = normalize_text(text)
    if not text:
        raise ValueError("MATERIAL_UNIT_EMPTY_AFTER_REDACTION")
    assert_external_text_safe(text)
    return text


def assert_external_text_safe(value: str) -> None:
    """Fail closed if a supported PII pattern survived filtering."""
    if _EMAIL_RE.search(value) or _MOBILE_RE.search(value) or _LANDLINE_RE.search(value):
        raise ValueError("MATERIAL_UNIT_PII_REMAINS")
    for line in value.splitlines():
        if _LABEL_RE.fullmatch(line) and _REDACTED not in line:
            raise ValueError("MATERIAL_UNIT_PII_REMAINS")
        if _SIGNATURE_CUE_RE.search(line) and _REDACTED not in line:
            raise ValueError("MATERIAL_UNIT_PII_REMAINS")


def canonical_unit(
    *,
    enterprise_id: uuid.UUID,
    knowledge_scope_id: uuid.UUID,
    document_record_id: uuid.UUID,
    document_version_id: uuid.UUID,
    source_sha256: str,
    page_number: int,
    ordinal: int,
    parser_version: str,
    text: str,
    ocr_applied: bool = False,
    table_candidate: bool = False,
    two_column_candidate: bool = False,
) -> CanonicalUnit:
    filtered = redact_external_text(text)
    body_sha = hashlib.sha256(filtered.encode("utf-8")).hexdigest()
    identity = (
        f"{enterprise_id}\x00{knowledge_scope_id}\x00{document_record_id}\x00"
        f"{document_version_id}\x00{source_sha256}\x00{page_number}\x00"
        f"{ordinal}\x00{parser_version}"
    )
    return CanonicalUnit(
        id=uuid.uuid5(_UNIT_NAMESPACE, identity),
        enterprise_id=enterprise_id,
        knowledge_scope_id=knowledge_scope_id,
        document_record_id=document_record_id,
        document_version_id=document_version_id,
        source_sha256=source_sha256,
        page_number=page_number,
        ordinal=ordinal,
        parser_version=parser_version,
        body=SensitiveText(filtered),
        body_sha256=body_sha,
        ocr_applied=ocr_applied,
        table_candidate=table_candidate,
        two_column_candidate=two_column_candidate,
    )


def canonical_page_units(
    *,
    enterprise_id: uuid.UUID,
    knowledge_scope_id: uuid.UUID,
    document_record_id: uuid.UUID,
    document_version_id: uuid.UUID,
    source_sha256: str,
    page_number: int,
    parser_version: str,
    text: str,
    ocr_applied: bool = False,
    table_candidate: bool = False,
    two_column_candidate: bool = False,
) -> tuple[CanonicalUnit, ...]:
    """Redact a whole page before deterministic bounded splitting.

    Whole-page filtering is important: splitting first could place two halves
    of an email or telephone in different chunks and evade the PII patterns.
    """
    filtered_page = redact_external_text(text)
    paragraphs = [value.strip() for value in filtered_page.split("\n") if value.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        pieces = [
            paragraph[offset : offset + MAX_UNIT_CHARACTERS]
            for offset in range(0, len(paragraph), MAX_UNIT_CHARACTERS)
        ]
        for piece in pieces:
            candidate = f"{current}\n{piece}" if current else piece
            if len(candidate) <= MAX_UNIT_CHARACTERS:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return tuple(
        canonical_unit(
            enterprise_id=enterprise_id,
            knowledge_scope_id=knowledge_scope_id,
            document_record_id=document_record_id,
            document_version_id=document_version_id,
            source_sha256=source_sha256,
            page_number=page_number,
            ordinal=ordinal,
            parser_version=parser_version,
            text=chunk,
            ocr_applied=ocr_applied,
            table_candidate=table_candidate,
            two_column_candidate=two_column_candidate,
        )
        for ordinal, chunk in enumerate(chunks, start=1)
    )


def remote_document_name(
    source_sha256: str,
    document_version_id: uuid.UUID | None = None,
) -> str:
    """Return an externally embeddable opaque title for a source.

    RAGFlow embeds the remote document name together with every chunk body, so
    the value contains no original filename.  Historical verifier sources keep
    their frozen names.  Ordinary sources include a hash of the source/version
    identity so equal files in the same scope cannot alias one remote document.
    """

    if not isinstance(source_sha256, str) or not SHA256_RE.fullmatch(source_sha256):
        raise MaterialRagIntegrityError("MATERIAL_SOURCE_SHA_INVALID")
    frozen = _REMOTE_DOCUMENT_NAME_BY_SOURCE_SHA256.get(source_sha256)
    if frozen is not None:
        return frozen
    identity = source_sha256
    if document_version_id is not None:
        if not isinstance(document_version_id, uuid.UUID):
            raise MaterialRagIntegrityError("MATERIAL_VERSION_ID_INVALID")
        identity = hashlib.sha256(
            f"{source_sha256}\x00{document_version_id}".encode("ascii")
        ).hexdigest()
    return f"MATERIAL_RAG_SOURCE_{identity}"


def unit_aad(unit: CanonicalUnit) -> bytes:
    return unit_aad_for_identity(
        enterprise_id=unit.enterprise_id,
        knowledge_scope_id=unit.knowledge_scope_id,
        unit_id=unit.id,
        document_record_id=unit.document_record_id,
        document_version_id=unit.document_version_id,
        source_sha256=unit.source_sha256,
        page_number=unit.page_number,
        ordinal=unit.ordinal,
        parser_version=unit.parser_version,
        body_sha256=unit.body_sha256,
    )


def unit_aad_for_identity(
    *,
    enterprise_id: uuid.UUID,
    knowledge_scope_id: uuid.UUID,
    unit_id: uuid.UUID,
    document_record_id: uuid.UUID,
    document_version_id: uuid.UUID,
    source_sha256: str,
    page_number: int,
    ordinal: int,
    parser_version: str,
    body_sha256: str,
) -> bytes:
    return (
        "f1.material-rag.unit.v1\x00"
        f"{enterprise_id}\x00{knowledge_scope_id}\x00{unit_id}\x00"
        f"{document_record_id}\x00{document_version_id}\x00{source_sha256}\x00"
        f"{page_number}\x00{ordinal}\x00{parser_version}\x00{body_sha256}"
    ).encode("ascii")


def dataset_ref_aad(
    *, enterprise_id: uuid.UUID, knowledge_scope_id: uuid.UUID, binding_id: uuid.UUID
) -> bytes:
    return (
        "f1.material-rag.binding.v1\x00"
        f"{enterprise_id}\x00{knowledge_scope_id}\x00{binding_id}"
    ).encode("ascii")


def encrypt_text(value: str, aad: bytes) -> tuple[bytes, str]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    encrypted = AESGCM(_key_bytes()).encrypt(nonce, value.encode("utf-8"), aad)
    return _MAGIC + nonce + encrypted, hashlib.sha256(aad).hexdigest()


def decrypt_text(ciphertext: bytes, aad: bytes, expected_aad_sha256: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if hashlib.sha256(aad).hexdigest() != expected_aad_sha256:
        raise ValueError("MATERIAL_RAG_AAD_MISMATCH")
    payload = bytes(ciphertext)
    if not payload.startswith(_MAGIC) or len(payload) <= len(_MAGIC) + 12:
        raise ValueError("MATERIAL_RAG_CIPHERTEXT_INVALID")
    payload = payload[len(_MAGIC) :]
    value = AESGCM(_key_bytes()).decrypt(payload[:12], payload[12:], aad).decode("utf-8")
    return value


__all__ = (
    "MATERIAL_RAG_KEY_FILE",
    "MATERIAL_RAG_MANIFEST_KEY_FILE",
    "AUTHORIZED_DEMO_SOURCE_SHA256",
    "AUTHORIZED_MATERIAL_RAG_SOURCE_SHA256",
    "CLIENT_A_RETRIEVAL_QUERY_TEXT",
    "CLIENT_B_ISOLATION_CANARY_TEXT",
    "CLIENT_B_RETRIEVAL_QUERY_TEXT",
    "PROVIDER_POLICY_CANARY_TEXT",
    "PROVIDER_RETRIEVAL_QUERY_TEXT",
    "remote_document_name",
    "SYNTHETIC_AUTHORIZED_EMBEDDING_TEXTS",
    "SYNTHETIC_AUTHORIZED_SOURCE_SHA256",
    "assert_external_text_safe",
    "canonical_unit",
    "canonical_page_units",
    "create_demo_unit_manifest_proof",
    "create_released_unit_manifest_proof",
    "create_synthetic_unit_manifest_proof",
    "dataset_ref_aad",
    "decrypt_text",
    "encrypt_text",
    "normalize_text",
    "redact_external_text",
    "unit_aad",
    "unit_aad_for_identity",
    "verify_demo_unit_manifest_proof",
    "verify_unit_manifest_proof",
)
