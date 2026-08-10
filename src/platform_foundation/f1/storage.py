"""F1 object storage: MinIO wrapper (S3-compatible).

Streamed upload (SHA-256 computed while streaming), download / presigned
URL with a file-type whitelist and a 100MiB size limit.  Credentials come
from the F1 secrets dir; never logged.  Object keys are opaque and are never
returned to the frontend.
"""
from __future__ import annotations

import hashlib
import io
import re
import uuid
from dataclasses import dataclass
from typing import Any, BinaryIO

from minio import Minio
from minio.error import S3Error

from .config import minio_endpoint as _minio_endpoint
from .secret_files import read_f1_secret_text

MINIO_ENDPOINT = _minio_endpoint()
BUCKET = "anhuan-f1-documents"
QUARANTINE_BUCKET = "anhuan-f1-quarantine"
PREVIEW_BUCKET = "anhuan-f1-previews"
MAX_SIZE_BYTES = 100 * 1024 * 1024  # 100 MiB (+1 rejected)

ALLOWED_TYPES = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}

# Container sniff signatures for the allowed MIME types (magic bytes).
_CONTAINER_SIGNATURES: dict[str, tuple[bytes, int]] = {
    "application/pdf": (b"%PDF-", 0),
    "image/jpeg": (b"\xff\xd8\xff", 0),
    "image/png": (b"\x89PNG\r\n\x1a\n", 0),
    "application/msword": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", 0),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (b"PK\x03\x04", 0),
    "application/vnd.ms-excel": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", 0),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (b"PK\x03\x04", 0),
    "application/vnd.ms-powerpoint": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", 0),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (b"PK\x03\x04", 0),
}


class StorageError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StoredObject:
    object_key: str
    etag: str
    size: int
    content_type: str
    sha256: str = ""


_OPAQUE_KEY_RE = re.compile(
    r"^[0-9a-f]{32}(?:\.pdf|\.doc|\.docx|\.xls|\.xlsx|\.ppt|\.pptx|\.jpg|\.png)$"
)
_PREVIEW_KEY_RE = re.compile(r"^[0-9a-f]{32}/[0-9a-f]{32}\.(?:json|jpg)$")
_PREVIEW_UNIT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def _client() -> Minio:
    user = read_f1_secret_text(
        "minio_root_user", file_env="F1_MINIO_ROOT_USER_FILE"
    )
    password = read_f1_secret_text(
        "minio_root_password", file_env="F1_MINIO_ROOT_PASSWORD_FILE"
    )
    return Minio(MINIO_ENDPOINT, access_key=user, secret_key=password, secure=False)


def validate_upload(filename: str, content_type: str, size: int) -> str:
    """Validate file type and size; returns the canonical extension."""
    if size <= 0:
        raise StorageError("EMPTY_FILE")
    if size > MAX_SIZE_BYTES:
        raise StorageError("FILE_TOO_LARGE")
    ext = ALLOWED_TYPES.get(content_type)
    if ext is None:
        raise StorageError("FILE_TYPE_NOT_ALLOWED")
    return ext


def _sniff_container(content_type: str, head: bytes) -> None:
    signature = _CONTAINER_SIGNATURES.get(content_type)
    if signature is None:
        return
    magic, offset = signature
    if not head.startswith(magic):
        raise StorageError("CONTAINER_MISMATCH")


@dataclass(frozen=True, slots=True)
class Preflight:
    """SHA-256 + size + content_type computed WITHOUT writing any object."""

    sha256: str
    size: int
    content_type: str


def preflight_upload(
    filename: str,
    content_type: str,
    file_obj: BinaryIO,
    max_size: int = MAX_SIZE_BYTES,
) -> Preflight:
    """Read the stream once, enforcing size/container and computing SHA-256.

    No object is written here; the caller checks idempotency by SHA before
    deciding whether to store a new opaque object.
    """
    digest = hashlib.sha256()
    total = 0
    head = b""
    while True:
        chunk = file_obj.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            raise StorageError("FILE_TOO_LARGE")
        digest.update(chunk)
        if len(head) < 16:
            head += chunk[: 16 - len(head)]
    if total <= 0:
        raise StorageError("EMPTY_FILE")
    ext = ALLOWED_TYPES.get(content_type)
    if ext is None:
        raise StorageError("FILE_TYPE_NOT_ALLOWED")
    _sniff_container(content_type, head)
    return Preflight(sha256=digest.hexdigest(), size=total, content_type=content_type)


def store_stream(
    file_obj: BinaryIO,
    *,
    content_type: str,
    length: int,
    object_key: str | None = None,
) -> StoredObject:
    """Write a preflighted stream as a fresh opaque object; returns etag.

    Only the caller's own freshly-minted object is written here, so a failure
    can be compensated by comparing the returned etag.
    """
    ext = ALLOWED_TYPES[content_type]
    object_key = object_key or f"{uuid.uuid4().hex}{ext}"
    if not _OPAQUE_KEY_RE.fullmatch(object_key) or not object_key.endswith(ext):
        raise StorageError("OBJECT_KEY_INVALID")
    client = _client()
    if not client.bucket_exists(BUCKET):
        client.make_bucket(BUCKET)
    file_obj.seek(0)
    client.put_object(
        BUCKET,
        object_key,
        file_obj,
        length=length,
        content_type=content_type,
    )
    stat = client.stat_object(BUCKET, object_key)
    return StoredObject(
        object_key=object_key,
        etag=stat.etag,
        size=stat.size,
        content_type=stat.content_type,
        sha256="",
    )


def _ensure_bucket(client: Minio, bucket: str) -> None:
    if bucket not in {BUCKET, QUARANTINE_BUCKET, PREVIEW_BUCKET}:
        raise StorageError("BUCKET_INVALID")
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)


def _store_to_bucket(
    bucket: str,
    object_key: str,
    file_obj: BinaryIO,
    *,
    content_type: str,
    length: int,
) -> StoredObject:
    client = _client()
    _ensure_bucket(client, bucket)
    file_obj.seek(0)
    client.put_object(
        bucket,
        object_key,
        file_obj,
        length=length,
        content_type=content_type,
    )
    stat = client.stat_object(bucket, object_key)
    return StoredObject(
        object_key=object_key,
        etag=str(stat.etag),
        size=int(stat.size),
        content_type=str(stat.content_type or content_type),
    )


def store_quarantine_stream(
    file_obj: BinaryIO,
    *,
    content_type: str,
    length: int,
    object_key: str,
) -> StoredObject:
    """Store an exact P3 source in the isolated quarantine bucket."""
    if not _OPAQUE_KEY_RE.fullmatch(object_key):
        raise StorageError("OBJECT_KEY_INVALID")
    if content_type not in {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "image/jpeg",
    }:
        raise StorageError("FILE_TYPE_NOT_ALLOWED")
    if length <= 0 or length > 50 * 1024 * 1024:
        raise StorageError("FILE_TOO_LARGE")
    return _store_to_bucket(
        QUARANTINE_BUCKET,
        object_key,
        file_obj,
        content_type=content_type,
        length=length,
    )


def _read_bucket_bytes(
    bucket: str,
    object_key: str,
    *,
    max_bytes: int,
) -> tuple[StoredObject, bytes]:
    client = _client()
    try:
        stat = client.stat_object(bucket, object_key)
        response = client.get_object(bucket, object_key)
    except S3Error as error:
        raise StorageError("SOURCE_OBJECT_MISSING") from error
    except Exception as error:
        raise StorageError("SOURCE_OBJECT_STAT_FAILED") from error
    digest = hashlib.sha256()
    payload = bytearray()
    try:
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > max_bytes:
                raise StorageError("FILE_TOO_LARGE")
            digest.update(chunk)
    except StorageError:
        raise
    except Exception as error:
        raise StorageError("SOURCE_OBJECT_READ_FAILED") from error
    finally:
        response.close()
        response.release_conn()
    if len(payload) != int(stat.size):
        raise StorageError("SOURCE_SIZE_MISMATCH")
    return (
        StoredObject(
            object_key=object_key,
            etag=str(stat.etag),
            size=len(payload),
            content_type=str(stat.content_type or ""),
            sha256=digest.hexdigest(),
        ),
        bytes(payload),
    )


def verify_quarantine_object(
    object_key: str,
    *,
    expected_sha256: str,
    expected_size: int,
    expected_etag: str | None = None,
) -> StoredObject:
    if not _OPAQUE_KEY_RE.fullmatch(object_key):
        raise StorageError("OBJECT_KEY_INVALID")
    stored, _payload = _read_bucket_bytes(
        QUARANTINE_BUCKET, object_key, max_bytes=50 * 1024 * 1024
    )
    if stored.sha256 != expected_sha256 or stored.size != int(expected_size):
        raise StorageError("SOURCE_IDENTITY_MISMATCH")
    if expected_etag is not None and stored.etag != expected_etag:
        raise StorageError("SOURCE_ETAG_MISMATCH")
    return stored


def open_quarantine_source(
    object_key: str,
    expected_sha256: str,
    expected_size: int,
    expected_etag: str,
) -> BinaryIO:
    stored, payload = _read_bucket_bytes(
        QUARANTINE_BUCKET, object_key, max_bytes=50 * 1024 * 1024
    )
    if (
        stored.sha256 != expected_sha256
        or stored.size != int(expected_size)
        or stored.etag != expected_etag
    ):
        raise StorageError("SOURCE_IDENTITY_MISMATCH")
    return io.BytesIO(payload)


def _preview_object_key(task_id: uuid.UUID, unit_id: str, content_type: str) -> str:
    if not isinstance(task_id, uuid.UUID) or _PREVIEW_UNIT_ID_RE.fullmatch(unit_id) is None:
        raise StorageError("OBJECT_KEY_INVALID")
    if content_type not in {"application/json", "image/jpeg"}:
        raise StorageError("PREVIEW_CONTENT_TYPE_INVALID")
    try:
        unit_uuid = uuid.UUID(unit_id)
    except (TypeError, ValueError):
        unit_uuid = uuid.uuid5(task_id, str(unit_id))
    extension = ".jpg" if content_type == "image/jpeg" else ".json"
    return f"{task_id.hex}/{unit_uuid.hex}{extension}"


def read_ingestion_preview_artifact(
    *,
    task_id: uuid.UUID,
    unit_id: str,
    content_type: str,
    expected_sha256: str,
    expected_size: int,
) -> bytes:
    """Read one deterministic P3 preview artifact by its validated identity."""
    return read_ingestion_preview_unit(
        _preview_object_key(task_id, unit_id, content_type),
        expected_sha256,
        expected_size,
    )


def read_ingestion_preview_manifest(
    *, task_id: uuid.UUID, expected_sha256: str
) -> bytes:
    """Read the bounded canonical manifest that authenticates all units."""
    object_key = _preview_object_key(task_id, "manifest", "application/json")
    stored, payload = _read_bucket_bytes(
        PREVIEW_BUCKET, object_key, max_bytes=256 * 1024
    )
    if stored.sha256 != expected_sha256:
        raise StorageError("PREVIEW_IDENTITY_MISMATCH")
    return payload


def store_ingestion_preview_unit(
    *,
    task_id: uuid.UUID,
    unit_id: str,
    content: bytes,
    content_type: str,
) -> StoredObject:
    if content_type not in {"application/json", "image/jpeg"}:
        raise StorageError("PREVIEW_CONTENT_TYPE_INVALID")
    maximum = 20 * 1024 * 1024 if content_type == "image/jpeg" else 256 * 1024
    if not content or len(content) > maximum:
        raise StorageError("PREVIEW_SIZE_INVALID")
    object_key = _preview_object_key(task_id, unit_id, content_type)
    if not _PREVIEW_KEY_RE.fullmatch(object_key):
        raise StorageError("OBJECT_KEY_INVALID")
    return _store_to_bucket(
        PREVIEW_BUCKET,
        object_key,
        io.BytesIO(content),
        content_type=content_type,
        length=len(content),
    )


def read_ingestion_preview_unit(
    object_key: str,
    expected_sha256: str,
    expected_size: int,
) -> bytes:
    if not _PREVIEW_KEY_RE.fullmatch(object_key):
        raise StorageError("OBJECT_KEY_INVALID")
    maximum = 20 * 1024 * 1024 if object_key.endswith(".jpg") else 256 * 1024
    stored, payload = _read_bucket_bytes(PREVIEW_BUCKET, object_key, max_bytes=maximum)
    if stored.sha256 != expected_sha256 or stored.size != int(expected_size):
        raise StorageError("PREVIEW_IDENTITY_MISMATCH")
    return payload


def release_ingestion_object(
    *,
    task_id: uuid.UUID,
    object_key: str,
    expected_sha256: str,
    expected_size: int,
    expected_etag: str,
) -> bool:
    """Idempotently copy an already-scanned source into the internal bucket."""
    if object_key != opaque_object_key(task_id, _content_type_for_key(object_key)):
        raise StorageError("OBJECT_KEY_INVALID")
    quarantined = verify_quarantine_object(
        object_key,
        expected_sha256=expected_sha256,
        expected_size=expected_size,
        expected_etag=expected_etag,
    )
    client = _client()
    _ensure_bucket(client, BUCKET)
    try:
        existing, _ = _read_bucket_bytes(BUCKET, object_key, max_bytes=MAX_SIZE_BYTES)
    except StorageError as error:
        if str(error) != "SOURCE_OBJECT_MISSING":
            raise
    else:
        return existing.sha256 == expected_sha256 and existing.size == expected_size
    source = client.get_object(QUARANTINE_BUCKET, object_key)
    try:
        client.put_object(
            BUCKET,
            object_key,
            source,
            length=quarantined.size,
            content_type=quarantined.content_type,
        )
    finally:
        source.close()
        source.release_conn()
    released, _ = _read_bucket_bytes(BUCKET, object_key, max_bytes=MAX_SIZE_BYTES)
    return released.sha256 == expected_sha256 and released.size == expected_size


def _content_type_for_key(object_key: str) -> str:
    extension = object_key.rsplit(".", 1)[-1]
    mapping = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "jpg": "image/jpeg",
    }
    try:
        return mapping[extension]
    except KeyError as error:
        raise StorageError("OBJECT_KEY_INVALID") from error


def opaque_object_key(task_id: uuid.UUID, content_type: str) -> str:
    """Return the stable, non-user-controlled object key for an upload task."""
    try:
        extension = ALLOWED_TYPES[content_type]
    except KeyError as error:
        raise StorageError("FILE_TYPE_NOT_ALLOWED") from error
    return f"{task_id.hex}{extension}"


def stat_and_hash_object(object_key: str) -> StoredObject:
    """Read back an object and return its authoritative size, etag and SHA-256.

    This is deliberately a full streamed read.  The worker must bind the bytes
    it is about to represent in RAGFlow to the SHA reserved by the API; a MinIO
    stat alone cannot prove that binding.
    """
    if not _OPAQUE_KEY_RE.fullmatch(object_key):
        raise StorageError("OBJECT_KEY_INVALID")
    client = _client()
    try:
        stat = client.stat_object(BUCKET, object_key)
        response = client.get_object(BUCKET, object_key)
    except S3Error as error:
        raise StorageError("SOURCE_OBJECT_MISSING") from error
    except Exception as error:
        raise StorageError("SOURCE_OBJECT_STAT_FAILED") from error
    digest = hashlib.sha256()
    total = 0
    try:
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_SIZE_BYTES:
                raise StorageError("FILE_TOO_LARGE")
            digest.update(chunk)
    except StorageError:
        raise
    except Exception as error:  # fixed reason; never expose endpoint/object data
        raise StorageError("SOURCE_OBJECT_READ_FAILED") from error
    finally:
        response.close()
        response.release_conn()
    if total != int(stat.size):
        raise StorageError("SOURCE_SIZE_MISMATCH")
    return StoredObject(
        object_key=object_key,
        etag=str(stat.etag),
        size=total,
        content_type=str(stat.content_type or ""),
        sha256=digest.hexdigest(),
    )


def verify_stored_object(
    object_key: str,
    *,
    expected_sha256: str,
    expected_size: int | None,
    expected_etag: str | None = None,
) -> StoredObject:
    """Fail closed unless the stored bytes match the reserved source identity."""
    stored = stat_and_hash_object(object_key)
    if expected_size is not None and stored.size != int(expected_size):
        raise StorageError("SOURCE_SIZE_MISMATCH")
    if stored.sha256 != expected_sha256:
        raise StorageError("SOURCE_HASH_MISMATCH")
    if expected_etag is not None and stored.etag != expected_etag:
        raise StorageError("SOURCE_ETAG_MISMATCH")
    return stored


def stream_upload(
    filename: str,
    content_type: str,
    file_obj: BinaryIO,
    max_size: int = MAX_SIZE_BYTES,
) -> StoredObject:
    """Legacy one-shot: preflight then store (kept for tests/convenience)."""
    pre = preflight_upload(filename, content_type, file_obj, max_size=max_size)
    stored = store_stream(file_obj, content_type=pre.content_type, length=pre.size)
    return StoredObject(
        object_key=stored.object_key,
        etag=stored.etag,
        size=stored.size,
        content_type=stored.content_type,
        sha256=pre.sha256,
    )


def upload_bytes(filename: str, content_type: str, data: bytes) -> StoredObject:
    """Upload raw bytes to MinIO (test/dev convenience wrapper)."""
    return stream_upload(filename, content_type, io.BytesIO(data))


def download_bytes(object_key: str) -> bytes:
    """Download an object's bytes (dev path; presigned URL for production)."""
    client = _client()
    try:
        response = client.get_object(BUCKET, object_key)
    except S3Error as error:
        raise StorageError("OBJECT_NOT_FOUND") from error
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def presigned_url(object_key: str, expires_seconds: int = 3600) -> str:
    """Presigned GET URL (enabled for production; dev may use download_bytes)."""
    from datetime import timedelta

    client = _client()
    return client.presigned_get_object(
        BUCKET, object_key, expires=timedelta(seconds=expires_seconds)
    )


def object_exists(object_key: str) -> bool:
    client = _client()
    try:
        client.stat_object(BUCKET, object_key)
        return True
    except S3Error:
        return False


__all__ = (
    "StoredObject",
    "Preflight",
    "StorageError",
    "validate_upload",
    "preflight_upload",
    "store_stream",
    "store_quarantine_stream",
    "verify_quarantine_object",
    "open_quarantine_source",
    "store_ingestion_preview_unit",
    "read_ingestion_preview_unit",
    "release_ingestion_object",
    "opaque_object_key",
    "stat_and_hash_object",
    "verify_stored_object",
    "stream_upload",
    "upload_bytes",
    "download_bytes",
    "presigned_url",
    "object_exists",
)
