"""Transactional local-fixture upload, outbox, job, and F0-C attachment flow."""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from dataclasses import dataclass
from typing import AsyncIterable, Iterable

from .auth import SessionContext
from .catalog import CatalogEntry, load_catalog, open_catalog_source
from .database import DatabaseConfig, role_transaction, tenant_transaction
from .evidence import (
    NATIVE_PLAN_SCHEMA,
    NATIVE_PLAN_SHA256,
    NATIVE_RULE_VERSION,
    ProcessingEvidence,
    processing_evidence,
)
from .governance import UploadIntent, require_registered_fixture_upload
from .vault import LocalFixtureVault, StoredObject, VaultError


_SAFE_KEY = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")
_SAFE_WORKER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_EXPECTED_GATES = {
    "REAL_CUSTOMER_CONTEXT": "REAL_CUSTOMER_UNCONFIRMED",
    "REGION_INDUSTRY_SCOPE": "REGION_INDUSTRY_UNCONFIRMED",
    "ACCEPTANCE_GOLD": "ACCEPTANCE_GOLD_UNAUTHORIZED",
    "EXTERNAL_PROCESSING": "EXTERNAL_PROCESSING_DENY",
    "PROFESSIONAL_RESPONSIBILITY": "PROFESSIONAL_RESPONSIBILITY_UNCONFIRMED",
}


class PlatformError(RuntimeError):
    """A stable, path-free and body-free application failure."""

    _CODES = frozenset(
        {
            "SOURCE_NOT_REGISTERED",
            "UPLOAD_NOT_FOUND",
            "UPLOAD_STATE_INVALID",
            "CONTENT_IDENTITY_MISMATCH",
            "CONTENT_TOO_LARGE",
            "IDEMPOTENCY_KEY_INVALID",
            "IDEMPOTENCY_CONFLICT",
            "UPLOAD_PERSIST_FAILED",
            "FINALIZE_FAILED",
            "OUTBOX_RELAY_FAILED",
            "JOB_NOT_AVAILABLE",
            "JOB_LEASE_STALE",
            "JOB_PROCESSING_FAILED",
            "F0C_IDENTITY_MISMATCH",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            code = "UPLOAD_PERSIST_FAILED"
        self.code = code
        super().__init__(code)

    def to_dict(self) -> dict[str, str]:
        return {"error": "PLATFORM_ERROR", "reason_code": self.code}


@dataclass(frozen=True, slots=True)
class UploadResult:
    upload_id: uuid.UUID
    source_id: uuid.UUID
    status: str
    captured_sha256: str | None = None
    captured_size: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "upload_id": str(self.upload_id),
            "source_id": str(self.source_id),
            "status": self.status,
            "captured_sha256": self.captured_sha256,
            "captured_size": self.captured_size,
        }


@dataclass(frozen=True, slots=True)
class CompletionResult:
    upload_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID
    object_id: uuid.UUID
    status: str = "FIXTURE_STORED"

    def to_dict(self) -> dict[str, str]:
        return {
            "upload_id": str(self.upload_id),
            "document_id": str(self.document_id),
            "version_id": str(self.version_id),
            "object_id": str(self.object_id),
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class JobLease:
    job_id: uuid.UUID
    generation: int
    token: uuid.UUID
    worker_id: str


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_uuid4(value: str) -> uuid.UUID:
    raw = bytearray(hashlib.sha256(value.encode("utf-8")).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(raw))


def _idempotency_hash(value: str) -> str:
    if not isinstance(value, str) or _SAFE_KEY.fullmatch(value) is None:
        raise PlatformError("IDEMPOTENCY_KEY_INVALID")
    return _sha256(value)


def _box_values(box: tuple[str, str, str, str] | None) -> tuple[object, ...]:
    if box is None:
        return (None, None, None, None)
    return box


class PlatformService:
    def __init__(
        self,
        config: DatabaseConfig,
        vault: LocalFixtureVault,
        *,
        catalog: tuple[CatalogEntry, ...] | None = None,
    ) -> None:
        self.config = config
        self.vault = vault
        entries = load_catalog("full") if catalog is None else catalog
        self._catalog = tuple(entries)
        self._catalog_by_document = {entry.document_id: entry for entry in entries}
        if len(self._catalog_by_document) != len(entries):
            raise PlatformError("F0C_IDENTITY_MISMATCH")

    def create_upload(
        self,
        context: SessionContext,
        source_id: uuid.UUID,
        idempotency_key: str,
    ) -> UploadResult:
        require_registered_fixture_upload(UploadIntent.REGISTERED_LOCAL_FIXTURE)
        key_hash = _idempotency_hash(idempotency_key)
        request_hash = _sha256(f"CREATE_UPLOAD\0{source_id}")
        try:
            with tenant_transaction(
                self.config, "f0d_runtime", context
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT id,source_document_id,expected_sha256,expected_size_bytes "
                        "FROM f0d.fixture_source_registry WHERE id=%s",
                        (source_id,),
                    )
                    source = cursor.fetchone()
                    if source is None:
                        raise PlatformError("SOURCE_NOT_REGISTERED")
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                        (str(source["source_document_id"]),),
                    )
                    cursor.execute(
                        "SELECT request_sha256,status,response_reference_id "
                        "FROM f0d.idempotency_record "
                        "WHERE enterprise_id=%s AND actor_id=%s AND method='POST' "
                        "AND route_code='CREATE_UPLOAD' AND idempotency_key_sha256=%s",
                        (context.enterprise_id, context.actor_id, key_hash),
                    )
                    existing_idem = cursor.fetchone()
                    if existing_idem is not None:
                        if existing_idem["request_sha256"] != request_hash:
                            raise PlatformError("IDEMPOTENCY_CONFLICT")
                        reference = existing_idem["response_reference_id"]
                        if reference is None:
                            raise PlatformError("UPLOAD_PERSIST_FAILED")
                        return self._upload_result(cursor, context, reference)

                    cursor.execute(
                        "SELECT id FROM f0d.upload_session "
                        "WHERE enterprise_id=%s AND source_document_id=%s "
                        "AND status <> 'REJECTED' ORDER BY created_at LIMIT 1",
                        (context.enterprise_id, source["source_document_id"]),
                    )
                    existing_upload = cursor.fetchone()
                    if existing_upload is not None:
                        upload_id = existing_upload["id"]
                    else:
                        cursor.execute(
                            "SELECT count(*) AS count FROM f0d.upload_session "
                            "WHERE enterprise_id=%s AND source_document_id=%s "
                            "AND status='REJECTED'",
                            (context.enterprise_id, source["source_document_id"]),
                        )
                        rejected_count = int(cursor.fetchone()["count"])
                        upload_id = _stable_uuid4(
                            f"upload:{context.enterprise_id}:"
                            f"{source['source_document_id']}:attempt:{rejected_count}"
                        )
                    if existing_upload is None:
                        cursor.execute(
                            "INSERT INTO f0d.upload_session("
                            "id,enterprise_id,actor_id,source_document_id,expected_sha256,"
                            "expected_size_bytes,quarantine_object_key) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                            (
                                upload_id,
                                context.enterprise_id,
                                context.actor_id,
                                source["source_document_id"],
                                source["expected_sha256"],
                                source["expected_size_bytes"],
                                _stable_uuid4(
                                    f"reserved-stage:{context.enterprise_id}:"
                                    f"{upload_id}"
                                ).hex,
                            ),
                        )
                    idem_id = _stable_uuid4(
                        f"idem:{context.enterprise_id}:{context.actor_id}:"
                        f"CREATE_UPLOAD:{key_hash}"
                    )
                    cursor.execute(
                        "INSERT INTO f0d.idempotency_record("
                        "id,enterprise_id,actor_id,method,route_code,"
                        "idempotency_key_sha256,request_sha256,status,response_status,"
                        "response_reference_id,completed_at) "
                        "VALUES (%s,%s,%s,'POST','CREATE_UPLOAD',%s,%s,'COMPLETED',"
                        "201,%s,statement_timestamp())",
                        (
                            idem_id,
                            context.enterprise_id,
                            context.actor_id,
                            key_hash,
                            request_hash,
                            upload_id,
                        ),
                    )
                    return self._upload_result(cursor, context, upload_id)
        except PlatformError:
            raise
        except Exception:
            raise PlatformError("UPLOAD_PERSIST_FAILED") from None

    def _upload_result(
        self, cursor: object, context: SessionContext, upload_id: uuid.UUID
    ) -> UploadResult:
        cursor.execute(  # type: ignore[attr-defined]
            "SELECT u.id,u.status,u.captured_sha256,u.captured_size_bytes,r.id AS source_id "
            "FROM f0d.upload_session u JOIN f0d.fixture_source_registry r "
            "ON r.enterprise_id=u.enterprise_id "
            "AND r.source_document_id=u.source_document_id "
            "WHERE u.enterprise_id=%s AND u.id=%s",
            (context.enterprise_id, upload_id),
        )
        record = cursor.fetchone()  # type: ignore[attr-defined]
        if record is None:
            raise PlatformError("UPLOAD_NOT_FOUND")
        return UploadResult(
            upload_id=record["id"],
            source_id=record["source_id"],
            status=record["status"],
            captured_sha256=record["captured_sha256"],
            captured_size=record["captured_size_bytes"],
        )

    def store_catalog_content(
        self, context: SessionContext, upload_id: uuid.UUID
    ) -> UploadResult:
        upload = self._load_upload(context, upload_id, "f0d_runtime")
        if upload["status"] in {"CONTENT_STORED", "COMPLETED"}:
            return self.get_upload(context, upload_id)
        if upload["status"] != "PENDING":
            raise PlatformError("UPLOAD_STATE_INVALID")
        entry = self._catalog_by_document.get(str(upload["source_document_id"]))
        if entry is None:
            raise PlatformError("F0C_IDENTITY_MISMATCH")
        recovered = self._recover_reserved_stage(context, upload_id, upload)
        if recovered is not None:
            return self._persist_staged(context, upload_id, recovered)
        with open_catalog_source(entry) as source_fd:
            staged = self.vault.stage_fd(
                source_fd, stage_id=str(upload["quarantine_object_key"])
            )
        return self._persist_staged(context, upload_id, staged)

    def store_content_chunks(
        self,
        context: SessionContext,
        upload_id: uuid.UUID,
        chunks: Iterable[bytes | bytearray | memoryview],
    ) -> UploadResult:
        upload = self._load_upload(context, upload_id, "f0d_runtime")
        if upload["status"] in {"CONTENT_STORED", "COMPLETED"}:
            return self.get_upload(context, upload_id)
        if upload["status"] != "PENDING":
            raise PlatformError("UPLOAD_STATE_INVALID")
        recovered = self._recover_reserved_stage(context, upload_id, upload)
        if recovered is not None:
            return self._persist_staged(context, upload_id, recovered)
        staged = self.vault.stage_chunks(
            chunks, stage_id=str(upload["quarantine_object_key"])
        )
        return self._persist_staged(context, upload_id, staged)

    async def store_content_stream(
        self,
        context: SessionContext,
        upload_id: uuid.UUID,
        chunks: AsyncIterable[bytes | bytearray | memoryview],
        *,
        maximum_size: int,
    ) -> UploadResult:
        upload = self._load_upload(context, upload_id, "f0d_runtime")
        if upload["status"] in {"CONTENT_STORED", "COMPLETED"}:
            return self.get_upload(context, upload_id)
        if upload["status"] != "PENDING":
            raise PlatformError("UPLOAD_STATE_INVALID")
        recovered = self._recover_reserved_stage(context, upload_id, upload)
        if recovered is not None:
            return self._persist_staged(context, upload_id, recovered)
        try:
            staged = await self.vault.stage_async_chunks(
                chunks,
                maximum_size=maximum_size,
                stage_id=str(upload["quarantine_object_key"]),
            )
        except VaultError as error:
            if error.code == "CONTENT_TOO_LARGE":
                raise PlatformError("CONTENT_TOO_LARGE") from None
            raise PlatformError("UPLOAD_PERSIST_FAILED") from None
        return self._persist_staged(context, upload_id, staged)

    def _recover_reserved_stage(
        self,
        context: SessionContext,
        upload_id: uuid.UUID,
        upload: dict[str, object],
    ) -> object | None:
        try:
            return self.vault.recover_stage(
                str(upload["quarantine_object_key"]),
                str(upload["expected_sha256"]),
                int(upload["expected_size_bytes"]),
            )
        except VaultError as error:
            if error.code == "OBJECT_NOT_FOUND":
                return None
            if error.code in {"STAGING_SIZE_MISMATCH", "STAGING_HASH_MISMATCH"}:
                try:
                    discarded = self.vault.discard_named_stage(
                        str(upload["quarantine_object_key"])
                    )
                except VaultError:
                    discarded = False
                if not discarded:
                    raise PlatformError("UPLOAD_PERSIST_FAILED") from None
                try:
                    with tenant_transaction(
                        self.config, "f0d_runtime", context
                    ) as connection:
                        connection.execute(
                            "UPDATE f0d.upload_session SET status='REJECTED',"
                            "rejection_code='CONTENT_IDENTITY_MISMATCH' "
                            "WHERE enterprise_id=%s AND id=%s AND status='PENDING'",
                            (context.enterprise_id, upload_id),
                        )
                except Exception:
                    raise PlatformError("UPLOAD_PERSIST_FAILED") from None
                raise PlatformError("CONTENT_IDENTITY_MISMATCH")
            raise PlatformError("UPLOAD_PERSIST_FAILED") from None

    def _persist_staged(
        self, context: SessionContext, upload_id: uuid.UUID, staged: object
    ) -> UploadResult:
        upload = self._load_upload(context, upload_id, "f0d_runtime")
        if (
            staged.sha256 != upload["expected_sha256"]  # type: ignore[attr-defined]
            or staged.size != upload["expected_size_bytes"]  # type: ignore[attr-defined]
        ):
            self.vault.discard(staged)  # type: ignore[arg-type]
            try:
                with tenant_transaction(
                    self.config,
                    "f0d_runtime",
                    context,
                ) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "UPDATE f0d.upload_session SET status='REJECTED',"
                            "rejection_code='CONTENT_IDENTITY_MISMATCH' "
                            "WHERE enterprise_id=%s AND id=%s AND status='PENDING'",
                            (context.enterprise_id, upload_id),
                        )
            except Exception:
                raise PlatformError("UPLOAD_PERSIST_FAILED") from None
            raise PlatformError("CONTENT_IDENTITY_MISMATCH")
        try:
            with tenant_transaction(
                self.config, "f0d_runtime", context
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE f0d.upload_session SET status='CONTENT_STORED',"
                        "quarantine_object_key=%s,captured_sha256=%s,"
                        "captured_size_bytes=%s "
                        "WHERE enterprise_id=%s AND id=%s AND status='PENDING'",
                        (
                            staged.stage_id,  # type: ignore[attr-defined]
                            staged.sha256,  # type: ignore[attr-defined]
                            staged.size,  # type: ignore[attr-defined]
                            context.enterprise_id,
                            upload_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        self.vault.discard(staged)  # type: ignore[arg-type]
                        return self._upload_result(cursor, context, upload_id)
                    return self._upload_result(cursor, context, upload_id)
        except PlatformError:
            raise
        except Exception:
            try:
                self.vault.discard(staged)  # type: ignore[arg-type]
            except VaultError:
                pass
            raise PlatformError("UPLOAD_PERSIST_FAILED") from None

    def get_upload(
        self, context: SessionContext, upload_id: uuid.UUID
    ) -> UploadResult:
        try:
            with tenant_transaction(
                self.config, "f0d_runtime", context
            ) as connection:
                with connection.cursor() as cursor:
                    return self._upload_result(cursor, context, upload_id)
        except PlatformError:
            raise
        except Exception:
            raise PlatformError("UPLOAD_NOT_FOUND") from None

    def _load_upload(
        self, context: SessionContext, upload_id: uuid.UUID, role: str
    ) -> dict[str, object]:
        selected_role = "f0d_worker" if role == "f0d_worker" else "f0d_runtime"
        try:
            with tenant_transaction(
                self.config,
                selected_role,  # type: ignore[arg-type]
                context,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT id,status,source_document_id,expected_sha256,"
                        "expected_size_bytes,quarantine_object_key,captured_sha256,"
                        "captured_size_bytes FROM f0d.upload_session "
                        "WHERE enterprise_id=%s AND id=%s",
                        (context.enterprise_id, upload_id),
                    )
                    record = cursor.fetchone()
        except Exception:
            raise PlatformError("UPLOAD_NOT_FOUND") from None
        if record is None:
            raise PlatformError("UPLOAD_NOT_FOUND")
        return record

    def complete_upload(
        self,
        context: SessionContext,
        upload_id: uuid.UUID,
        idempotency_key: str,
    ) -> CompletionResult:
        key_hash = _idempotency_hash(idempotency_key)
        upload = self._load_upload(context, upload_id, "f0d_worker")
        if upload["status"] == "COMPLETED":
            return self._completion_result(
                context, upload_id, completion_key_hash=key_hash
            )
        if upload["status"] != "CONTENT_STORED":
            raise PlatformError("UPLOAD_STATE_INVALID")
        if (
            upload["captured_sha256"] != upload["expected_sha256"]
            or upload["captured_size_bytes"] != upload["expected_size_bytes"]
        ):
            raise PlatformError("CONTENT_IDENTITY_MISMATCH")
        object_key = _stable_uuid4(
            f"object:{context.enterprise_id}:{upload_id}"
        ).hex
        stored = self._ensure_promoted(upload, object_key)
        try:
            return self._finalize_transaction(
                context, upload_id, upload, stored, key_hash
            )
        except PlatformError:
            raise
        except Exception:
            raise PlatformError("FINALIZE_FAILED") from None

    def _ensure_promoted(
        self, upload: dict[str, object], object_key: str
    ) -> StoredObject:
        expected_hash = str(upload["expected_sha256"])
        expected_size = int(upload["expected_size_bytes"])
        try:
            stored = self.vault.verify(object_key, expected_hash, expected_size)
        except VaultError:
            pass
        else:
            self._discard_reserved_stage_if_present(upload)
            return stored
        try:
            staged = self.vault.recover_stage(
                str(upload["quarantine_object_key"]), expected_hash, expected_size
            )
            return self.vault.promote_as(staged, object_key)
        except VaultError:
            # Another concurrent complete can expose the create-only final link
            # for a few milliseconds while its staging hardlink is being
            # fsync'd and removed.  Never overwrite it; wait only for that same
            # immutable identity to become singly linked and verifiable.
            for _ in range(20):
                try:
                    stored = self.vault.verify(
                        object_key, expected_hash, expected_size
                    )
                except VaultError:
                    time.sleep(0.005)
                else:
                    self._discard_reserved_stage_if_present(upload)
                    return stored
            raise PlatformError("FINALIZE_FAILED") from None

    def _discard_reserved_stage_if_present(
        self, upload: dict[str, object]
    ) -> None:
        try:
            staged = self.vault.recover_stage(
                str(upload["quarantine_object_key"]),
                str(upload["expected_sha256"]),
                int(upload["expected_size_bytes"]),
            )
        except VaultError as error:
            if error.code == "OBJECT_NOT_FOUND":
                return
            raise PlatformError("FINALIZE_FAILED") from None
        if not self.vault.discard(staged):
            raise PlatformError("FINALIZE_FAILED")

    def _finalize_transaction(
        self,
        context: SessionContext,
        upload_id: uuid.UUID,
        upload: dict[str, object],
        stored: StoredObject,
        key_hash: str,
    ) -> CompletionResult:
        source_document_id = str(upload["source_document_id"])
        blob_id = _stable_uuid4(f"blob:{context.enterprise_id}:{upload_id}")
        document_id = _stable_uuid4(
            f"document:{context.enterprise_id}:{source_document_id}"
        )
        version_id = _stable_uuid4(f"version:{context.enterprise_id}:{upload_id}")
        object_version_id = _stable_uuid4(
            f"object-version:{context.enterprise_id}:{upload_id}"
        )
        outbox_key = _sha256(
            f"DOCUMENT_VERSION_STORED\0{context.enterprise_id}\0{version_id}"
        )
        with tenant_transaction(
            self.config, "f0d_worker", context
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT status FROM f0d.upload_session "
                    "WHERE enterprise_id=%s AND id=%s FOR UPDATE",
                    (context.enterprise_id, upload_id),
                )
                current = cursor.fetchone()
                if current is None:
                    raise PlatformError("UPLOAD_NOT_FOUND")
                if current["status"] == "COMPLETED":
                    request_hash, idem = self._completion_idempotency_record(
                        cursor, context, upload_id, key_hash
                    )
                    result = self._completion_result_cursor(
                        cursor, context, upload_id
                    )
                    self._bind_completed_idempotency(
                        cursor,
                        context,
                        key_hash,
                        request_hash,
                        idem,
                        result,
                    )
                    return result
                if current["status"] != "CONTENT_STORED":
                    raise PlatformError("UPLOAD_STATE_INVALID")
                request_hash, idem = self._completion_idempotency_record(
                    cursor, context, upload_id, key_hash
                )
                if idem is None:
                    cursor.execute(
                        "INSERT INTO f0d.idempotency_record("
                        "id,enterprise_id,actor_id,method,route_code,"
                        "idempotency_key_sha256,request_sha256) "
                        "VALUES (%s,%s,%s,'POST','COMPLETE_UPLOAD',%s,%s)",
                        (
                            _stable_uuid4(
                                f"idem:{context.enterprise_id}:{context.actor_id}:"
                                f"COMPLETE_UPLOAD:{key_hash}"
                            ),
                            context.enterprise_id,
                            context.actor_id,
                            key_hash,
                            request_hash,
                        ),
                    )
                cursor.execute(
                    "INSERT INTO f0d.object_blob("
                    "id,enterprise_id,upload_session_id,object_key,object_version_id,"
                    "sha256,size_bytes) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        blob_id,
                        context.enterprise_id,
                        upload_id,
                        stored.object_id,
                        object_version_id,
                        stored.sha256,
                        stored.size,
                    ),
                )
                cursor.execute(
                    "INSERT INTO f0d.document(id,enterprise_id,source_document_id) "
                    "VALUES (%s,%s,%s) ON CONFLICT (enterprise_id,source_document_id) "
                    "DO NOTHING",
                    (document_id, context.enterprise_id, source_document_id),
                )
                cursor.execute(
                    "SELECT id FROM f0d.document WHERE enterprise_id=%s "
                    "AND source_document_id=%s",
                    (context.enterprise_id, source_document_id),
                )
                existing_document = cursor.fetchone()
                if existing_document is None or existing_document["id"] != document_id:
                    raise PlatformError("FINALIZE_FAILED")
                cursor.execute(
                    "INSERT INTO f0d.document_version("
                    "id,enterprise_id,document_id,object_blob_id,upload_session_id,"
                    "source_document_id,version_no) "
                    "VALUES (%s,%s,%s,%s,%s,%s,1)",
                    (
                        version_id,
                        context.enterprise_id,
                        document_id,
                        blob_id,
                        upload_id,
                        source_document_id,
                    ),
                )
                cursor.execute(
                    "UPDATE f0d.upload_session SET status='COMPLETED',"
                    "completed_at=statement_timestamp() "
                    "WHERE enterprise_id=%s AND id=%s AND status='CONTENT_STORED'",
                    (context.enterprise_id, upload_id),
                )
                if cursor.rowcount != 1:
                    raise PlatformError("FINALIZE_FAILED")
                audit_id = _stable_uuid4(
                    f"audit:UPLOAD_COMPLETED:{context.enterprise_id}:{upload_id}"
                )
                cursor.execute(
                    "INSERT INTO f0d.audit_event("
                    "id,enterprise_id,actor_id,event_code,target_type,target_id,"
                    "correlation_id,outcome_code) "
                    "VALUES (%s,%s,%s,'UPLOAD_COMPLETED','UPLOAD_SESSION',%s,%s,'SUCCESS')",
                    (
                        audit_id,
                        context.enterprise_id,
                        context.actor_id,
                        upload_id,
                        upload_id,
                    ),
                )
                outbox_id = _stable_uuid4(
                    f"outbox:DOCUMENT_VERSION_STORED:{context.enterprise_id}:{version_id}"
                )
                cursor.execute(
                    "INSERT INTO f0d.outbox_event("
                    "id,enterprise_id,event_type,document_version_id,idempotency_key) "
                    "VALUES (%s,%s,'DOCUMENT_VERSION_STORED',%s,%s)",
                    (
                        outbox_id,
                        context.enterprise_id,
                        version_id,
                        outbox_key,
                    ),
                )
                cursor.execute(
                    "UPDATE f0d.idempotency_record SET status='COMPLETED',"
                    "response_status=200,response_reference_id=%s,"
                    "completed_at=statement_timestamp() "
                    "WHERE enterprise_id=%s AND actor_id=%s AND method='POST' "
                    "AND route_code='COMPLETE_UPLOAD' AND idempotency_key_sha256=%s "
                    "AND request_sha256=%s AND status='IN_PROGRESS'",
                    (
                        version_id,
                        context.enterprise_id,
                        context.actor_id,
                        key_hash,
                        request_hash,
                    ),
                )
                if cursor.rowcount != 1:
                    raise PlatformError("FINALIZE_FAILED")
                return CompletionResult(
                    upload_id=upload_id,
                    document_id=document_id,
                    version_id=version_id,
                    object_id=blob_id,
                )

    def _completion_idempotency_record(
        self,
        cursor: object,
        context: SessionContext,
        upload_id: uuid.UUID,
        key_hash: str,
    ) -> tuple[str, dict[str, object] | None]:
        request_hash = _sha256(f"COMPLETE_UPLOAD\0{upload_id}")
        cursor.execute(  # type: ignore[attr-defined]
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (
                f"{context.enterprise_id}:{context.actor_id}:"
                f"COMPLETE_UPLOAD:{key_hash}",
            ),
        )
        cursor.execute(  # type: ignore[attr-defined]
            "SELECT request_sha256,status,response_reference_id "
            "FROM f0d.idempotency_record WHERE enterprise_id=%s "
            "AND actor_id=%s AND method='POST' "
            "AND route_code='COMPLETE_UPLOAD' AND idempotency_key_sha256=%s",
            (context.enterprise_id, context.actor_id, key_hash),
        )
        record = cursor.fetchone()  # type: ignore[attr-defined]
        if record is not None and record["request_sha256"] != request_hash:
            raise PlatformError("IDEMPOTENCY_CONFLICT")
        return request_hash, record

    def _bind_completed_idempotency(
        self,
        cursor: object,
        context: SessionContext,
        key_hash: str,
        request_hash: str,
        record: dict[str, object] | None,
        result: CompletionResult,
    ) -> None:
        if record is not None:
            if (
                record["status"] != "COMPLETED"
                or record["response_reference_id"] != result.version_id
            ):
                raise PlatformError("FINALIZE_FAILED")
            return
        cursor.execute(  # type: ignore[attr-defined]
            "INSERT INTO f0d.idempotency_record("
            "id,enterprise_id,actor_id,method,route_code,"
            "idempotency_key_sha256,request_sha256,status,response_status,"
            "response_reference_id,completed_at) "
            "VALUES (%s,%s,%s,'POST','COMPLETE_UPLOAD',%s,%s,'COMPLETED',"
            "200,%s,statement_timestamp())",
            (
                _stable_uuid4(
                    f"idem:{context.enterprise_id}:{context.actor_id}:"
                    f"COMPLETE_UPLOAD:{key_hash}"
                ),
                context.enterprise_id,
                context.actor_id,
                key_hash,
                request_hash,
                result.version_id,
            ),
        )

    def _completion_result(
        self,
        context: SessionContext,
        upload_id: uuid.UUID,
        *,
        completion_key_hash: str | None = None,
    ) -> CompletionResult:
        try:
            with tenant_transaction(
                self.config, "f0d_worker", context
            ) as connection:
                with connection.cursor() as cursor:
                    request_hash: str | None = None
                    idem: dict[str, object] | None = None
                    if completion_key_hash is not None:
                        request_hash, idem = self._completion_idempotency_record(
                            cursor, context, upload_id, completion_key_hash
                        )
                    result = self._completion_result_cursor(
                        cursor, context, upload_id
                    )
                    if completion_key_hash is not None and request_hash is not None:
                        self._bind_completed_idempotency(
                            cursor,
                            context,
                            completion_key_hash,
                            request_hash,
                            idem,
                            result,
                        )
                    return result
        except PlatformError:
            raise
        except Exception:
            raise PlatformError("FINALIZE_FAILED") from None

    def _completion_result_cursor(
        self, cursor: object, context: SessionContext, upload_id: uuid.UUID
    ) -> CompletionResult:
        cursor.execute(  # type: ignore[attr-defined]
            "SELECT d.id AS document_id,v.id AS version_id,b.id AS object_id "
            "FROM f0d.object_blob b JOIN f0d.document_version v "
            "ON v.enterprise_id=b.enterprise_id AND v.object_blob_id=b.id "
            "JOIN f0d.document d ON d.enterprise_id=v.enterprise_id AND d.id=v.document_id "
            "WHERE b.enterprise_id=%s AND b.upload_session_id=%s",
            (context.enterprise_id, upload_id),
        )
        record = cursor.fetchone()  # type: ignore[attr-defined]
        if record is None:
            raise PlatformError("FINALIZE_FAILED")
        return CompletionResult(
            upload_id=upload_id,
            document_id=record["document_id"],
            version_id=record["version_id"],
            object_id=record["object_id"],
        )

    def relay_once(self, context: SessionContext) -> uuid.UUID | None:
        try:
            with tenant_transaction(
                self.config, "f0d_worker", context
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT id,document_version_id FROM f0d.outbox_event "
                        "WHERE enterprise_id=%s AND status='PENDING' "
                        "AND event_type='DOCUMENT_VERSION_STORED' "
                        "AND available_at<=statement_timestamp() "
                        "ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1",
                        (context.enterprise_id,),
                    )
                    event = cursor.fetchone()
                    if event is None:
                        return None
                    job_key = _sha256(
                        f"ATTACH_NATIVE_PLAN\0{context.enterprise_id}\0"
                        f"{event['document_version_id']}"
                    )
                    job_id = _stable_uuid4(
                        f"job:ATTACH_NATIVE_PLAN:{context.enterprise_id}:"
                        f"{event['document_version_id']}"
                    )
                    cursor.execute(
                        "INSERT INTO f0d.job("
                        "id,enterprise_id,kind,document_version_id,idempotency_key,"
                        "input_version,trace_id) "
                        "VALUES (%s,%s,'ATTACH_NATIVE_PLAN',%s,%s,%s,%s)",
                        (
                            job_id,
                            context.enterprise_id,
                            event["document_version_id"],
                            job_key,
                            NATIVE_PLAN_SHA256,
                            _stable_uuid4(f"trace:{job_id}"),
                        ),
                    )
                    cursor.execute(
                        "UPDATE f0d.outbox_event SET status='PUBLISHED',"
                        "attempts=attempts+1,published_at=statement_timestamp() "
                        "WHERE enterprise_id=%s AND id=%s AND status='PENDING'",
                        (context.enterprise_id, event["id"]),
                    )
                    if cursor.rowcount != 1:
                        raise PlatformError("OUTBOX_RELAY_FAILED")
                    return job_id
        except PlatformError:
            raise
        except Exception:
            raise PlatformError("OUTBOX_RELAY_FAILED") from None

    def claim_job(
        self, context: SessionContext, worker_id: str
    ) -> JobLease | None:
        if _SAFE_WORKER.fullmatch(worker_id) is None:
            raise PlatformError("JOB_PROCESSING_FAILED")
        try:
            with tenant_transaction(
                self.config, "f0d_worker", context
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT id FROM f0d.job WHERE enterprise_id=%s "
                        "AND kind='ATTACH_NATIVE_PLAN' "
                        "AND ((status='PENDING' AND run_after<=statement_timestamp()) "
                        "OR (status='RUNNING' AND lease_until<statement_timestamp())) "
                        "ORDER BY priority,created_at,id FOR UPDATE SKIP LOCKED LIMIT 1",
                        (context.enterprise_id,),
                    )
                    job = cursor.fetchone()
                    if job is None:
                        return None
                    token = uuid.uuid4()
                    cursor.execute(
                        "UPDATE f0d.job SET status='RUNNING',attempts=attempts+1,"
                        "lease_owner=%s,lease_until=statement_timestamp()+interval '5 minutes',"
                        "lease_generation=lease_generation+1,lease_token=%s,"
                        "heartbeat_at=statement_timestamp(),error_code=NULL "
                        "WHERE enterprise_id=%s AND id=%s "
                        "RETURNING lease_generation",
                        (
                            worker_id,
                            token,
                            context.enterprise_id,
                            job["id"],
                        ),
                    )
                    claimed = cursor.fetchone()
                    if claimed is None:
                        raise PlatformError("JOB_PROCESSING_FAILED")
                    return JobLease(
                        job_id=job["id"],
                        generation=int(claimed["lease_generation"]),
                        token=token,
                        worker_id=worker_id,
                    )
        except PlatformError:
            raise
        except Exception:
            raise PlatformError("JOB_PROCESSING_FAILED") from None

    def finish_job(
        self, context: SessionContext, lease: JobLease
    ) -> uuid.UUID:
        job_input = self._job_input(context, lease)
        source_document_id = str(job_input["source_document_id"])
        entry = self._catalog_by_document.get(source_document_id)
        if entry is None:
            raise PlatformError("F0C_IDENTITY_MISMATCH")
        if (
            str(job_input["sha256"]) != entry.expected_sha256
            or int(job_input["size_bytes"]) != entry.expected_size
            or str(job_input["captured_sha256"]) != entry.expected_sha256
            or int(job_input["captured_size_bytes"]) != entry.expected_size
        ):
            raise PlatformError("F0C_IDENTITY_MISMATCH")
        evidence = processing_evidence(entry)
        try:
            self.vault.verify(
                str(job_input["object_key"]),
                str(job_input["sha256"]),
                int(job_input["size_bytes"]),
            )
        except VaultError:
            raise PlatformError("JOB_PROCESSING_FAILED") from None
        return self._attach_plan(context, lease, job_input, entry, evidence)

    def _job_input(
        self, context: SessionContext, lease: JobLease
    ) -> dict[str, object]:
        try:
            with tenant_transaction(
                self.config, "f0d_worker", context
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT j.status,j.lease_generation,j.lease_token,"
                        "j.lease_until>statement_timestamp() AS lease_valid,"
                        "j.document_version_id,v.source_document_id,b.object_key,"
                        "b.sha256,b.size_bytes,u.captured_sha256,u.captured_size_bytes "
                        "FROM f0d.job j JOIN f0d.document_version v "
                        "ON v.enterprise_id=j.enterprise_id AND v.id=j.document_version_id "
                        "JOIN f0d.object_blob b ON b.enterprise_id=v.enterprise_id "
                        "AND b.id=v.object_blob_id AND b.upload_session_id=v.upload_session_id "
                        "JOIN f0d.upload_session u ON u.enterprise_id=v.enterprise_id "
                        "AND u.id=v.upload_session_id "
                        "AND u.source_document_id=v.source_document_id "
                        "AND u.expected_sha256=b.sha256 AND u.expected_size_bytes=b.size_bytes "
                        "JOIN f0d.fixture_source_registry r ON r.enterprise_id=u.enterprise_id "
                        "AND r.source_document_id=u.source_document_id "
                        "AND r.expected_sha256=u.expected_sha256 "
                        "AND r.expected_size_bytes=u.expected_size_bytes "
                        "WHERE j.enterprise_id=%s AND j.id=%s AND u.status='COMPLETED' "
                        "AND u.captured_sha256=u.expected_sha256 "
                        "AND u.captured_size_bytes=u.expected_size_bytes",
                        (context.enterprise_id, lease.job_id),
                    )
                    record = cursor.fetchone()
        except Exception:
            raise PlatformError("JOB_PROCESSING_FAILED") from None
        if record is None:
            raise PlatformError("JOB_NOT_AVAILABLE")
        if (
            record["lease_generation"] != lease.generation
            or record["lease_token"] != lease.token
            or record["status"] not in {"RUNNING", "SUCCEEDED"}
            or (record["status"] == "RUNNING" and not record["lease_valid"])
        ):
            raise PlatformError("JOB_LEASE_STALE")
        return record

    def _attach_plan(
        self,
        context: SessionContext,
        lease: JobLease,
        job_input: dict[str, object],
        entry: CatalogEntry,
        evidence: ProcessingEvidence,
    ) -> uuid.UUID:
        version_id = job_input["document_version_id"]
        plan_id = _stable_uuid4(f"plan:{context.enterprise_id}:{version_id}")
        try:
            with tenant_transaction(
                self.config, "f0d_worker", context
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT status,lease_generation,lease_token,"
                        "lease_until>statement_timestamp() AS lease_valid "
                        "FROM f0d.job WHERE enterprise_id=%s AND id=%s FOR UPDATE",
                        (context.enterprise_id, lease.job_id),
                    )
                    current = cursor.fetchone()
                    if current is None:
                        raise PlatformError("JOB_NOT_AVAILABLE")
                    if (
                        current["lease_generation"] != lease.generation
                        or current["lease_token"] != lease.token
                    ):
                        raise PlatformError("JOB_LEASE_STALE")
                    if current["status"] == "SUCCEEDED":
                        return plan_id
                    if current["status"] != "RUNNING" or not current["lease_valid"]:
                        raise PlatformError("JOB_LEASE_STALE")
                    cursor.execute(
                        "INSERT INTO f0d.document_processing_plan("
                        "id,enterprise_id,document_version_id,source_document_id,"
                        "source_plan_sha256,source_schema_version,source_rule_version,"
                        "page_count,visual_unit_count,native_candidate_count,"
                        "ocr_required_count,manual_review_count,deferred_conversion) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (
                            plan_id,
                            context.enterprise_id,
                            version_id,
                            entry.document_id,
                            NATIVE_PLAN_SHA256,
                            NATIVE_PLAN_SCHEMA,
                            NATIVE_RULE_VERSION,
                            evidence.visual_units,
                            evidence.visual_units,
                            evidence.native_candidates,
                            evidence.ocr_candidates,
                            evidence.manual_review_candidates,
                            evidence.doc_deferred == 1,
                        ),
                    )
                    for unit in evidence.units:
                        media = _box_values(unit.media_box)
                        crop = _box_values(unit.crop_box)
                        unit_id = _stable_uuid4(
                            f"unit:{context.enterprise_id}:{plan_id}:"
                            f"{unit.source_unit_id}"
                        )
                        cursor.execute(
                            "INSERT INTO f0d.document_processing_unit("
                            "id,enterprise_id,processing_plan_id,source_unit_id,"
                            "unit_ordinal,unit_kind,page_no,candidate_decision,reason_codes,"
                            "native_characters,bad_character_ppm,native_text_sha256,rotation,"
                            "media_left,media_bottom,media_right,media_top,"
                            "crop_left,crop_bottom,crop_right,crop_top,width_px,height_px,"
                            "evidence_sha256) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (
                                unit_id,
                                context.enterprise_id,
                                plan_id,
                                unit.source_unit_id,
                                unit.ordinal,
                                unit.kind,
                                unit.page_no,
                                unit.decision,
                                list(unit.reason_codes),
                                unit.native_characters,
                                unit.bad_character_ppm,
                                unit.native_text_sha256,
                                unit.rotation,
                                *media,
                                *crop,
                                unit.width_px,
                                unit.height_px,
                                unit.evidence_sha256,
                            ),
                        )
                    cursor.execute(
                        "UPDATE f0d.job SET status='SUCCEEDED',finished_at=statement_timestamp(),"
                        "progress_done=%s,progress_total=%s,lease_owner=NULL,lease_until=NULL,"
                        "heartbeat_at=NULL "
                        "WHERE enterprise_id=%s AND id=%s AND status='RUNNING' "
                        "AND lease_generation=%s AND lease_token=%s "
                        "AND lease_until>statement_timestamp()",
                        (
                            evidence.visual_units,
                            evidence.visual_units,
                            context.enterprise_id,
                            lease.job_id,
                            lease.generation,
                            lease.token,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise PlatformError("JOB_LEASE_STALE")
                    audit_id = _stable_uuid4(
                        f"audit:FIXTURE_PLAN_ATTACHED:{context.enterprise_id}:{plan_id}"
                    )
                    cursor.execute(
                        "INSERT INTO f0d.audit_event("
                        "id,enterprise_id,actor_id,event_code,target_type,target_id,"
                        "correlation_id,outcome_code) "
                        "VALUES (%s,%s,%s,'FIXTURE_PLAN_ATTACHED','PROCESSING_PLAN',"
                        "%s,%s,'SUCCESS')",
                        (
                            audit_id,
                            context.enterprise_id,
                            context.actor_id,
                            plan_id,
                            lease.job_id,
                        ),
                    )
                    return plan_id
        except PlatformError:
            raise
        except Exception:
            raise PlatformError("JOB_PROCESSING_FAILED") from None

    def process_once(
        self, context: SessionContext, worker_id: str = "fixture-worker-1"
    ) -> uuid.UUID | None:
        lease = self.claim_job(context, worker_id)
        if lease is None:
            return None
        return self.finish_job(context, lease)

    def list_documents(self, context: SessionContext) -> list[dict[str, object]]:
        with tenant_transaction(
            self.config, "f0d_runtime", context
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT d.id,v.id AS version_id,v.lifecycle_status,"
                    "p.visual_unit_count,p.native_candidate_count,p.ocr_required_count,"
                    "p.deferred_conversion FROM f0d.document d "
                    "JOIN f0d.document_version v ON v.enterprise_id=d.enterprise_id "
                    "AND v.document_id=d.id LEFT JOIN f0d.document_processing_plan p "
                    "ON p.enterprise_id=v.enterprise_id AND p.document_version_id=v.id "
                    "WHERE d.enterprise_id=%s ORDER BY d.id",
                    (context.enterprise_id,),
                )
                rows = cursor.fetchall()
        return [
            {
                "document_id": str(row["id"]),
                "version_id": str(row["version_id"]),
                "status": row["lifecycle_status"],
                "visual_units": row["visual_unit_count"],
                "native_candidates": row["native_candidate_count"],
                "ocr_candidates": row["ocr_required_count"],
                "deferred_conversion": row["deferred_conversion"],
            }
            for row in rows
        ]

    def list_jobs(self, context: SessionContext) -> list[dict[str, object]]:
        with tenant_transaction(
            self.config, "f0d_runtime", context
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id,kind,status,attempts,lease_generation,"
                    "progress_done,progress_total,error_code FROM f0d.job "
                    "WHERE enterprise_id=%s ORDER BY created_at,id",
                    (context.enterprise_id,),
                )
                rows = cursor.fetchall()
        return [
            {
                "job_id": str(row["id"]),
                "kind": row["kind"],
                "status": row["status"],
                "attempts": row["attempts"],
                "lease_generation": row["lease_generation"],
                "progress_done": row["progress_done"],
                "progress_total": row["progress_total"],
                "error_code": row["error_code"],
            }
            for row in rows
        ]

    def stats(self, context: SessionContext) -> dict[str, int]:
        with tenant_transaction(
            self.config, "f0d_runtime", context
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT (SELECT count(*) FROM f0d.upload_session) AS uploads,"
                    "(SELECT count(*) FROM f0d.object_blob) AS blobs,"
                    "(SELECT COALESCE(sum(size_bytes),0) FROM f0d.object_blob) AS bytes,"
                    "(SELECT count(*) FROM f0d.document_version) AS versions,"
                    "(SELECT count(*) FROM f0d.document_processing_plan) AS plans,"
                    "(SELECT count(*) FROM f0d.document_processing_unit) AS units,"
                    "(SELECT count(*) FROM f0d.document_processing_unit "
                    "WHERE candidate_decision='NATIVE_CANDIDATE') AS native,"
                    "(SELECT count(*) FROM f0d.document_processing_unit "
                    "WHERE candidate_decision='FULL_PAGE_OCR_REQUIRED') AS ocr,"
                    "(SELECT count(*) FROM f0d.document_processing_plan "
                    "WHERE deferred_conversion) AS deferred,"
                    "(SELECT count(*) FROM f0d.job WHERE status='SUCCEEDED') AS jobs_succeeded,"
                    "(SELECT count(*) FROM f0d.audit_event) AS audit_events"
                )
                record = cursor.fetchone()
        if record is None:
            raise PlatformError("UPLOAD_PERSIST_FAILED")
        return {key: int(value) for key, value in record.items()}

    def readiness(self) -> dict[str, object]:
        from .governance import closed_readiness_snapshot

        integrity = "VALID"
        rows: list[dict[str, object]] = []
        try:
            with role_transaction(self.config, "f0d_runtime") as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT code,status,reason_code FROM f0d.capability_gate ORDER BY code"
                    )
                    rows = cursor.fetchall()
            observed = {
                str(row["code"]): (str(row["status"]), str(row["reason_code"]))
                for row in rows
            }
            if observed != {
                code: ("CLOSED", reason) for code, reason in _EXPECTED_GATES.items()
            }:
                integrity = "INVALID"
        except Exception:
            integrity = "UNAVAILABLE"
        return {
            **closed_readiness_snapshot().to_dict(),
            "gate_store_integrity": integrity,
            "gate_count": len(rows) if integrity == "VALID" else 0,
        }


__all__ = (
    "CompletionResult",
    "JobLease",
    "PlatformError",
    "PlatformService",
    "UploadResult",
)
