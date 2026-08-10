"""Private, aggregate-only manifests for local engineering backups.

The manifest deliberately contains no object names or relative paths.  The
MinIO tree digest still commits to every directory name, file name, file size,
and file digest through an unambiguous, sorted binary encoding.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA = "anhuan-engineering-backup-v1"
DATABASE_DUMP_NAME = "database.dump"
MINIO_DIRECTORY_NAME = "minio-data"
MANIFEST_NAME = "manifest.json"

_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "project_id",
        "database",
        "db_dump_sha256",
        "db_dump_size",
        "minio_tree_sha256",
        "minio_file_count",
        "minio_total_size",
        "created_at",
    }
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_TREE_DOMAIN = b"ANHUAN_MINIO_TREE_V1\x00"


class BackupContractError(RuntimeError):
    """A reason-code-only failure from the local backup contract."""


def _fail(code: str) -> None:
    raise BackupContractError(code)


def _validated_root(raw_path: str | os.PathLike[str]) -> Path:
    path = Path(raw_path)
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        _fail("BACKUP_PATH_TRAVERSAL")
    _validate_directory(path, "BACKUP_ROOT")
    return path


def _lstat(path: Path, prefix: str) -> os.stat_result:
    try:
        return path.lstat()
    except FileNotFoundError:
        _fail(f"{prefix}_MISSING")
    except OSError:
        _fail(f"{prefix}_UNAVAILABLE")
    raise AssertionError("unreachable")


def _validate_owner(item: os.stat_result, prefix: str) -> None:
    if item.st_uid != os.geteuid():
        _fail(f"{prefix}_OWNER_INVALID")


def _validate_directory(path: Path, prefix: str) -> os.stat_result:
    item = _lstat(path, prefix)
    if stat.S_ISLNK(item.st_mode):
        _fail(f"{prefix}_SYMLINK_REJECTED")
    if not stat.S_ISDIR(item.st_mode):
        _fail(f"{prefix}_TYPE_INVALID")
    _validate_owner(item, prefix)
    if stat.S_IMODE(item.st_mode) != 0o700:
        _fail(f"{prefix}_MODE_INVALID")
    return item


def _validate_regular_stat(item: os.stat_result, prefix: str) -> None:
    if stat.S_ISLNK(item.st_mode):
        _fail(f"{prefix}_SYMLINK_REJECTED")
    if not stat.S_ISREG(item.st_mode):
        _fail(f"{prefix}_TYPE_INVALID")
    _validate_owner(item, prefix)
    if stat.S_IMODE(item.st_mode) != 0o600:
        _fail(f"{prefix}_MODE_INVALID")
    if item.st_nlink != 1:
        _fail(f"{prefix}_HARDLINK_REJECTED")


def _hash_regular_file(path: Path, prefix: str) -> tuple[str, int]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        _fail(f"{prefix}_MISSING")
    except OSError:
        _fail(f"{prefix}_OPEN_REJECTED")

    try:
        before = os.fstat(descriptor)
        _validate_regular_stat(before, prefix)
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
            before.st_mode,
            before.st_uid,
            before.st_nlink,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
            after.st_mode,
            after.st_uid,
            after.st_nlink,
        )
        if identity_before != identity_after:
            _fail(f"{prefix}_CHANGED_DURING_READ")
        return digest.hexdigest(), before.st_size
    finally:
        os.close(descriptor)


def _read_small_regular_file(path: Path, prefix: str) -> bytes:
    """Read a bounded control file without following a last-component link."""

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        _fail(f"{prefix}_MISSING")
    except OSError:
        _fail(f"{prefix}_OPEN_REJECTED")

    try:
        before = os.fstat(descriptor)
        _validate_regular_stat(before, prefix)
        if before.st_size > 64 * 1024:
            _fail(f"{prefix}_SIZE_INVALID")
        body = bytearray()
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > 64 * 1024:
                _fail(f"{prefix}_SIZE_INVALID")
        after = os.fstat(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
            before.st_mode,
            before.st_uid,
            before.st_nlink,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
            after.st_mode,
            after.st_uid,
            after.st_nlink,
        )
        if identity_before != identity_after:
            _fail(f"{prefix}_CHANGED_DURING_READ")
        return bytes(body)
    finally:
        os.close(descriptor)


def _safe_relative_posix(root: Path, candidate: Path) -> str:
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        _fail("MINIO_PATH_TRAVERSAL")
    posix = relative.as_posix()
    parsed = PurePosixPath(posix)
    if not posix or parsed.is_absolute() or any(
        part in {"", ".", ".."} for part in parsed.parts
    ):
        _fail("MINIO_PATH_TRAVERSAL")
    return posix


def _tree_summary(root: Path) -> tuple[str, int, int]:
    _validate_directory(root, "MINIO_ROOT")
    records: list[tuple[str, str, int, str]] = []
    pending = [root]

    while pending:
        directory = pending.pop()
        _validate_directory(directory, "MINIO_DIRECTORY")
        try:
            entries = list(os.scandir(directory))
        except OSError:
            _fail("MINIO_DIRECTORY_UNAVAILABLE")
        for entry in entries:
            candidate = Path(entry.path)
            relative = _safe_relative_posix(root, candidate)
            item = _lstat(candidate, "MINIO_ENTRY")
            if stat.S_ISLNK(item.st_mode):
                _fail("MINIO_ENTRY_SYMLINK_REJECTED")
            if stat.S_ISDIR(item.st_mode):
                _validate_directory(candidate, "MINIO_DIRECTORY")
                records.append((relative, "directory", 0, ""))
                pending.append(candidate)
            elif stat.S_ISREG(item.st_mode):
                digest, size = _hash_regular_file(candidate, "MINIO_FILE")
                records.append((relative, "file", size, digest))
            else:
                _fail("MINIO_ENTRY_TYPE_INVALID")

    tree_digest = hashlib.sha256(_TREE_DOMAIN)
    file_count = 0
    total_size = 0
    for relative, kind, size, content_sha256 in sorted(records):
        path_bytes = relative.encode("utf-8")
        tree_digest.update(b"D" if kind == "directory" else b"F")
        tree_digest.update(len(path_bytes).to_bytes(8, "big"))
        tree_digest.update(path_bytes)
        if kind == "file":
            file_count += 1
            total_size += size
            tree_digest.update(size.to_bytes(8, "big"))
            tree_digest.update(bytes.fromhex(content_sha256))

    return tree_digest.hexdigest(), file_count, total_size


def _validate_identifier(value: Any, prefix: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        _fail(f"{prefix}_INVALID")
    return value


def _validate_nonnegative_integer(value: Any, prefix: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(f"{prefix}_INVALID")
    return value


def _validate_manifest(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != _MANIFEST_FIELDS:
        _fail("MANIFEST_SCHEMA_INVALID")
    if document["schema"] != SCHEMA:
        _fail("MANIFEST_SCHEMA_INVALID")
    _validate_identifier(document["project_id"], "MANIFEST_PROJECT_ID")
    _validate_identifier(document["database"], "MANIFEST_DATABASE")
    for field in ("db_dump_sha256", "minio_tree_sha256"):
        if not isinstance(document[field], str) or not _SHA256.fullmatch(
            document[field]
        ):
            _fail("MANIFEST_DIGEST_INVALID")
    for field in ("db_dump_size", "minio_file_count", "minio_total_size"):
        _validate_nonnegative_integer(document[field], "MANIFEST_COUNT")
    created_at = document["created_at"]
    if not isinstance(created_at, str) or not _UTC_TIMESTAMP.fullmatch(created_at):
        _fail("MANIFEST_CREATED_AT_INVALID")
    try:
        datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        _fail("MANIFEST_CREATED_AT_INVALID")
    return document


def _canonical_json(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _root_names(root: Path) -> set[str]:
    try:
        return {entry.name for entry in os.scandir(root)}
    except OSError:
        _fail("BACKUP_ROOT_UNAVAILABLE")
    raise AssertionError("unreachable")


def _require_exact_root(root: Path, expected: set[str]) -> None:
    observed = _root_names(root)
    if expected - observed:
        _fail("BACKUP_ENTRY_MISSING")
    if observed - expected:
        _fail("BACKUP_ENTRY_EXTRA")


def _atomic_write_manifest(root: Path, payload: bytes) -> None:
    destination = root / MANIFEST_NAME
    if destination.exists() or destination.is_symlink():
        _fail("MANIFEST_ALREADY_EXISTS")
    temporary = root / f".manifest-{secrets.token_hex(16)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(temporary, flags, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail("MANIFEST_WRITE_FAILED")
            view = view[written:]
        os.fsync(descriptor)
        item = os.fstat(descriptor)
        _validate_regular_stat(item, "MANIFEST_TEMPORARY")
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, destination)
        _hash_regular_file(destination, "MANIFEST")
        directory_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            directory_flags |= os.O_DIRECTORY
        directory_descriptor = os.open(root, directory_flags)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BackupContractError:
        raise
    except OSError:
        _fail("MANIFEST_WRITE_FAILED")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def create_manifest(
    stage_dir: str | os.PathLike[str], project_id: str, database: str
) -> dict[str, Any]:
    """Validate a private backup stage and atomically create its manifest."""

    root = _validated_root(stage_dir)
    project = _validate_identifier(project_id, "PROJECT_ID")
    database_name = _validate_identifier(database, "DATABASE")
    _require_exact_root(root, {DATABASE_DUMP_NAME, MINIO_DIRECTORY_NAME})

    db_digest, db_size = _hash_regular_file(
        root / DATABASE_DUMP_NAME, "DATABASE_DUMP"
    )
    tree_digest, file_count, total_size = _tree_summary(
        root / MINIO_DIRECTORY_NAME
    )
    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "project_id": project,
        "database": database_name,
        "db_dump_sha256": db_digest,
        "db_dump_size": db_size,
        "minio_tree_sha256": tree_digest,
        "minio_file_count": file_count,
        "minio_total_size": total_size,
        "created_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _validate_manifest(manifest)
    _atomic_write_manifest(root, _canonical_json(manifest))
    _require_exact_root(
        root, {DATABASE_DUMP_NAME, MINIO_DIRECTORY_NAME, MANIFEST_NAME}
    )
    return manifest


def _reject_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            _fail("MANIFEST_DUPLICATE_FIELD")
        document[key] = value
    return document


def verify_backup(
    backup_dir: str | os.PathLike[str],
    expected_project_id: str,
    expected_database: str,
) -> dict[str, Any]:
    """Fail closed unless a backup exactly satisfies the private contract."""

    root = _validated_root(backup_dir)
    project = _validate_identifier(expected_project_id, "EXPECTED_PROJECT_ID")
    database = _validate_identifier(expected_database, "EXPECTED_DATABASE")
    _require_exact_root(
        root, {DATABASE_DUMP_NAME, MINIO_DIRECTORY_NAME, MANIFEST_NAME}
    )

    manifest_path = root / MANIFEST_NAME
    raw = _read_small_regular_file(manifest_path, "MANIFEST")
    try:
        document = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_fields
        )
    except BackupContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("MANIFEST_JSON_INVALID")
    manifest = _validate_manifest(document)
    if raw != _canonical_json(manifest):
        _fail("MANIFEST_NOT_CANONICAL")
    if manifest["project_id"] != project:
        _fail("MANIFEST_PROJECT_ID_MISMATCH")
    if manifest["database"] != database:
        _fail("MANIFEST_DATABASE_MISMATCH")

    db_digest, db_size = _hash_regular_file(
        root / DATABASE_DUMP_NAME, "DATABASE_DUMP"
    )
    if db_digest != manifest["db_dump_sha256"] or db_size != manifest["db_dump_size"]:
        _fail("DATABASE_DUMP_MISMATCH")
    tree_digest, file_count, total_size = _tree_summary(
        root / MINIO_DIRECTORY_NAME
    )
    if (
        tree_digest != manifest["minio_tree_sha256"]
        or file_count != manifest["minio_file_count"]
        or total_size != manifest["minio_total_size"]
    ):
        _fail("MINIO_TREE_MISMATCH")
    return manifest


__all__ = [
    "BackupContractError",
    "SCHEMA",
    "create_manifest",
    "verify_backup",
]
