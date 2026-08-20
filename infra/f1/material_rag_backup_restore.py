"""Dedicated material-RAG backup/restore machine gate.

Independent schema ``anhuan-material-rag-backup-v1``.  This is not a user
restore command.  Destructive actions only target PostgreSQL and MinIO data
volumes that carry the exact three labels.  Secrets, Ark keys, and RAGFlow
derivative volumes never enter the package.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import quote, urlparse

import psycopg

from infra.f1.local_backup import (
    BackupContractError,
    DATABASE_DUMP_NAME,
    MANIFEST_NAME,
    MINIO_DIRECTORY_NAME,
    _atomic_write_manifest,
    _canonical_json,
    _hash_regular_file,
    _read_small_regular_file,
    _reject_duplicate_fields,
    _require_exact_root,
    _tree_summary,
    _validated_root,
    _validate_identifier,
    _validate_nonnegative_integer,
)
from infra.f1.local_verify import P2_P7_TABLES
from infra.f1.material_rag_postgres_integration import (
    canonical_shared_fingerprint,
    _insert_lifecycle_document,
    FIXTURE_NS,
    LifecycleDoc,
    LifecycleWorld,
    PostgresIntegrationStack,
)

import importlib.util

_RESTORE_MAINTENANCE_PATH = (
    Path(__file__).resolve().parent / "material-rag" / "restore_maintenance.py"
)
_RESTORE_MAINTENANCE_SPEC = importlib.util.spec_from_file_location(
    "material_rag_restore_maintenance",
    _RESTORE_MAINTENANCE_PATH,
)
if _RESTORE_MAINTENANCE_SPEC is None or _RESTORE_MAINTENANCE_SPEC.loader is None:
    raise RuntimeError("RESTORE_MAINTENANCE_IMPORT")
_RESTORE_MAINTENANCE = importlib.util.module_from_spec(_RESTORE_MAINTENANCE_SPEC)
_RESTORE_MAINTENANCE_SPEC.loader.exec_module(_RESTORE_MAINTENANCE)
RestoreMaintenanceError = _RESTORE_MAINTENANCE.RestoreMaintenanceError
residual_counts = _RESTORE_MAINTENANCE.residual_counts
run_restore_maintenance = _RESTORE_MAINTENANCE.run_restore_maintenance

_RESTORE_RECOVERY_PATH = (
    Path(__file__).resolve().parent / "material-rag" / "restore_recovery.py"
)
_RESTORE_RECOVERY_SPEC = importlib.util.spec_from_file_location(
    "material_rag_restore_recovery",
    _RESTORE_RECOVERY_PATH,
)
if _RESTORE_RECOVERY_SPEC is None or _RESTORE_RECOVERY_SPEC.loader is None:
    raise RuntimeError("RESTORE_RECOVERY_IMPORT")
_RESTORE_RECOVERY = importlib.util.module_from_spec(_RESTORE_RECOVERY_SPEC)
_RESTORE_RECOVERY_SPEC.loader.exec_module(_RESTORE_RECOVERY)
RestoreRecoveryError = _RESTORE_RECOVERY.RestoreRecoveryError
JOURNAL_SCHEMA = _RESTORE_RECOVERY.JOURNAL_SCHEMA
abort_new_restore_resources = _RESTORE_RECOVERY.abort_new_restore_resources
advance_stage = _RESTORE_RECOVERY.advance_stage
maybe_crash = _RESTORE_RECOVERY.maybe_crash
new_labeled_resources = _RESTORE_RECOVERY.new_labeled_resources
prepare_empty_core = _RESTORE_RECOVERY.prepare_empty_core
read_journal = _RESTORE_RECOVERY.read_journal
recover_from_journal = _RESTORE_RECOVERY.recover_from_journal
volume_identity_id = _RESTORE_RECOVERY.volume_identity_id
write_journal = _RESTORE_RECOVERY.write_journal
ABORT_DELETE_KINDS = _RESTORE_RECOVERY.ABORT_DELETE_KINDS
RECOVERABLE_STAGES = _RESTORE_RECOVERY.RECOVERABLE_STAGES


ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "infra/f1/docker-compose.material-rag-backup-restore.yml"
DOCKER = Path("/Applications/Docker.app/Contents/Resources/bin/docker")
PYTHON = sys.executable
SCOPE = "material-rag-verification"
SCHEMA = "anhuan-material-rag-backup-v1"
CHECK_SCHEMA = "anhuan-material-rag-backup-restore-check-v3"
CHECK_SCHEMA_V2 = "anhuan-material-rag-backup-restore-check-v2"
CHECK_PAYLOAD_KEYS = (
    "schema",
    "f1_head",
    "business_table_count",
    "db_dump_size_positive",
    "minio_file_count",
    "minio_live_tree_match",
    "front_door_tamper_failures",
    "front_door_repair_ok",
    "destructive_started",
    "restore_ok",
    "maintenance_job",
    "maintenance_unit",
    "maintenance_live_lease",
    "maintenance_provisioning",
    "maintenance_deleted_secret",
    "maintenance_orphan",
    "rebuild_ok",
    "rebuild_old_job_reuse",
    "unreleased_enqueued",
    "revoked_enqueued",
    "cross_tenant_enqueued",
    "cross_tenant_visible",
    "cross_scope_visible",
    "post_restart_fresh_process",
    "post_restart_retrieval_ok",
    "restart_ok",
    "cleanup_label_rejection",
    "restore_failure_cleanup",
    "maintenance_failure_cleanup",
    "rebuild_failure_cleanup",
    "restart_failure_cleanup",
    "restore_mutation_observed",
    "maintenance_mutation_observed",
    "rebuild_mutation_observed",
    "restart_mutation_observed",
    "fail_cleanup_ok",
    "dedicated_c",
    "dedicated_v",
    "dedicated_n",
    "shared_fingerprint_match",
    "skipped",
    "same_count_swap_observed",
    "new_volume_count",
    "new_container_count",
    "deleted_count",
    "remaining_abort_id_count",
    "package_reverified",
    "rebuild_started",
    "retry_abort_id_reuse_count",
    "journal_stage_recovered",
)
CRASH_SCHEMA = "anhuan-material-rag-backup-restore-crash-check-v1"
CRASH_PAYLOAD_KEYS = (
    "schema",
    "f1_head",
    "hard_death_signal",
    "fresh_recovery_process",
    "tamper_rejected",
    "tamper_zero_delete",
    "tamper_reason_verified",
    "new_volume",
    "new_container",
    "deleted",
    "remaining",
    "fallback_cleanup_used",
    "stable_zero_observations",
    "package_reverified",
    "rebuild_started",
    "journal_recovered",
    "shared_match",
    "skipped",
    "dedicated_c",
    "dedicated_v",
    "dedicated_n",
)
CRASH_RECEIPT_SCHEMA = "anhuan-material-rag-crash-receipt-v1"
CRASH_RECEIPT_KEYS = (
    "schema",
    "f1_head",
    "scope",
    "project_id",
    "parent_project_id",
    "project_name",
    "control_dir",
    "journal_path",
    "package_path",
    "package_dump_sha256",
    "package_tree_sha256",
)
F1_HEAD = "f1_0015"
OK_TOKEN = "LOCAL_MATERIAL_RAG_BACKUP_RESTORE_OK"
CRASH_OK_TOKEN = "LOCAL_MATERIAL_RAG_CRASH_RECOVERY_OK"
POST_RESTART_PROBE = ROOT / "infra/f1/material-rag/post_restart_probe.py"
CRASH_PROBE = ROOT / "infra/f1/material-rag/crash_recovery_probe.py"
EVIDENCE_ROOT = Path(
    "/Users/lichenhao/Desktop/安环项目/artifacts/"
    "material-rag-backup-restore-runtime-20260819-v1"
)
MATERIAL_RAG_BACKUP_TABLES = P2_P7_TABLES + (
    "material_rag_scope_binding",
    "material_rag_unit",
    "material_rag_job",
)
if len(MATERIAL_RAG_BACKUP_TABLES) != 38:
    raise RuntimeError("MATERIAL_RAG_TABLE_CONTRACT")

_MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "f1_head",
        "project_id",
        "parent_project_id",
        "database",
        "scope",
        "db_dump_sha256",
        "db_dump_size",
        "business_table_count",
        "business_total_row_count",
        "business_nonempty_table_count",
        "business_count_sha256",
        "minio_tree_sha256",
        "minio_file_count",
        "minio_total_size",
        "created_at",
    }
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_TABLE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
FORBIDDEN_NAMES = frozenset(
    {
        ".minio.sys",
        "ark",
        "cache",
        "control",
        "egress",
        "elasticsearch",
        "f1_material_rag_key",
        "f1_material_rag_manifest_key",
        "mysql",
        "objectstore",
        "pgpass",
        "ragflow",
        "redis",
        "secrets",
    }
)
DATA_VOLUME_SUFFIXES = ("_br_postgres_data", "_br_minio_data")
DOCUMENTS_BUCKET = "anhuan-f1-documents"
QUARANTINE_BUCKET = "anhuan-f1-quarantine"
PREVIEW_BUCKET = "anhuan-f1-previews"


class BackupRestoreError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise BackupRestoreError(code)


def _confine_object_path(root: Path, object_key: str) -> Path:
    if not object_key or object_key.startswith("/") or "\\" in object_key:
        _fail("MINIO_OBJECT_KEY_INVALID")
    parts = object_key.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        _fail("MINIO_OBJECT_KEY_INVALID")
    target = root.joinpath(*parts)
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError:
        _fail("MINIO_OBJECT_KEY_INVALID")
    return target


def _hashed_object_name(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_object_tree_from_dir(root: str | os.PathLike[str]) -> list[dict[str, Any]]:
    base = Path(root)
    records: list[dict[str, Any]] = []
    pending = [base]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            _fail("MINIO_TREE_SCAN_FAILED")
        for entry in entries:
            path = Path(entry.path)
            if entry.is_symlink():
                _fail("PACKAGE_SYMLINK_REJECTED")
            if entry.is_dir(follow_symlinks=False):
                pending.append(path)
                continue
            if not entry.is_file(follow_symlinks=False):
                _fail("MINIO_ENTRY_TYPE_INVALID")
            relative = path.relative_to(base).as_posix()
            if "/" in relative:
                bucket, key = relative.split("/", 1)
            else:
                bucket, key = "", relative
            body = path.read_bytes()
            records.append(
                {
                    "body_sha256": hashlib.sha256(body).hexdigest(),
                    "bucket": _hashed_object_name(bucket),
                    "key": _hashed_object_name(key),
                    "size": len(body),
                }
            )
            del body
            del key
    records.sort(key=lambda item: (item["bucket"], item["key"], item["body_sha256"]))
    return records


def compare_object_trees(
    left: list[dict[str, Any]], right: list[dict[str, Any]]
) -> None:
    required = {"bucket", "key", "size", "body_sha256"}

    def index(tree: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
        mapping: dict[tuple[str, str], dict[str, Any]] = {}
        for record in tree:
            if set(record) != required:
                _fail("MINIO_TREE_RECORD_INVALID")
            token = (str(record["bucket"]), str(record["key"]))
            if token in mapping:
                _fail("MINIO_TREE_DUPLICATE")
            mapping[token] = record
        return mapping

    left_index = index(left)
    right_index = index(right)
    if set(left_index) != set(right_index):
        _fail("MINIO_TREE_MISMATCH")
    for token, record in left_index.items():
        other = right_index[token]
        if record["size"] != other["size"] or record["body_sha256"] != other["body_sha256"]:
            _fail("MINIO_BODY_SHA_MISMATCH")


def observe_stage_mutation(before: object, after: object) -> int:
    return 0 if before == after else 1


def validate_check_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != set(CHECK_PAYLOAD_KEYS):
        _fail("CHECK_PAYLOAD_KEYS_INVALID")
    if payload.get("schema") == CHECK_SCHEMA_V2:
        _fail("CHECK_PAYLOAD_SCHEMA_STALE")
    if payload.get("schema") != CHECK_SCHEMA:
        _fail("CHECK_PAYLOAD_HARDCODED")
    if payload.get("restart_ok") == 1 and payload.get("post_restart_fresh_process") != 1:
        _fail("CHECK_PAYLOAD_HARDCODED")
    if payload.get("restart_ok") == 1 and payload.get("post_restart_retrieval_ok") != 1:
        _fail("CHECK_PAYLOAD_HARDCODED")
    if payload.get("restore_ok") == 1 and payload.get("minio_live_tree_match") != 1:
        _fail("CHECK_PAYLOAD_HARDCODED")
    if payload.get("restore_ok") == 1 and (
        payload.get("cross_tenant_visible") != 0
        or payload.get("cross_scope_visible") != 0
    ):
        _fail("CHECK_PAYLOAD_HARDCODED")
    for cleanup_key, observed_key in (
        ("restore_failure_cleanup", "restore_mutation_observed"),
        ("maintenance_failure_cleanup", "maintenance_mutation_observed"),
        ("rebuild_failure_cleanup", "rebuild_mutation_observed"),
        ("restart_failure_cleanup", "restart_mutation_observed"),
    ):
        if payload.get(cleanup_key) == 1 and payload.get(observed_key) != 1:
            _fail("CHECK_PAYLOAD_HARDCODED")
    if payload.get("restore_failure_cleanup") == 1:
        if payload.get("same_count_swap_observed") != 1:
            _fail("CHECK_PAYLOAD_HARDCODED")
        if payload.get("new_volume_count") != 2:
            _fail("CHECK_PAYLOAD_HARDCODED")
        if payload.get("new_container_count") != 3:
            _fail("CHECK_PAYLOAD_HARDCODED")
        if payload.get("deleted_count") != 5:
            _fail("CHECK_PAYLOAD_HARDCODED")
        if payload.get("deleted_count") != (
            int(payload.get("new_volume_count") or -1)
            + int(payload.get("new_container_count") or -1)
        ):
            _fail("CHECK_PAYLOAD_HARDCODED")
        if payload.get("remaining_abort_id_count") != 0:
            _fail("CHECK_PAYLOAD_HARDCODED")
        if payload.get("package_reverified") != 1:
            _fail("CHECK_PAYLOAD_HARDCODED")
        if payload.get("rebuild_started") != 0:
            _fail("CHECK_PAYLOAD_HARDCODED")
        if payload.get("retry_abort_id_reuse_count") != 0:
            _fail("CHECK_PAYLOAD_HARDCODED")
        if payload.get("journal_stage_recovered") != 1:
            _fail("CHECK_PAYLOAD_HARDCODED")
    return payload


def validate_crash_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != set(CRASH_PAYLOAD_KEYS):
        _fail("CRASH_PAYLOAD_KEYS_INVALID")
    if payload.get("schema") != CRASH_SCHEMA:
        _fail("CRASH_PAYLOAD_HARDCODED")
    if payload.get("f1_head") != F1_HEAD:
        _fail("CRASH_PAYLOAD_HARDCODED")
    if payload.get("hard_death_signal") != 9:
        _fail("CRASH_PAYLOAD_HARDCODED")
    if payload.get("fresh_recovery_process") != 1:
        _fail("CRASH_PAYLOAD_HARDCODED")
    if payload.get("tamper_rejected") != 1:
        _fail("CRASH_PAYLOAD_HARDCODED")
    if payload.get("tamper_zero_delete") != 1:
        _fail("CRASH_PAYLOAD_HARDCODED")
    if payload.get("tamper_reason_verified") != 1:
        _fail("CRASH_PAYLOAD_HARDCODED")
    if payload.get("new_volume") != 2:
        _fail("CRASH_PAYLOAD_HARDCODED")
    if payload.get("new_container") != 3:
        _fail("CRASH_PAYLOAD_HARDCODED")
    if payload.get("deleted") != 5:
        _fail("CRASH_PAYLOAD_HARDCODED")
    if payload.get("deleted") != (
        int(payload.get("new_volume") or -1)
        + int(payload.get("new_container") or -1)
    ):
        _fail("CRASH_PAYLOAD_HARDCODED")
    if payload.get("remaining") != 0:
        _fail("CRASH_PAYLOAD_HARDCODED")
    if payload.get("fallback_cleanup_used") != 0:
        _fail("CRASH_PAYLOAD_HARDCODED")
    if payload.get("stable_zero_observations") != 2:
        _fail("CRASH_PAYLOAD_HARDCODED")
    if payload.get("package_reverified") != 1:
        _fail("CRASH_PAYLOAD_HARDCODED")
    if payload.get("rebuild_started") != 0:
        _fail("CRASH_PAYLOAD_HARDCODED")
    if payload.get("journal_recovered") != 1:
        _fail("CRASH_PAYLOAD_HARDCODED")
    if payload.get("shared_match") != 1:
        _fail("CRASH_PAYLOAD_HARDCODED")
    if payload.get("skipped") != 0:
        _fail("CRASH_PAYLOAD_HARDCODED")
    if (
        payload.get("dedicated_c") != 0
        or payload.get("dedicated_v") != 0
        or payload.get("dedicated_n") != 0
    ):
        _fail("CRASH_PAYLOAD_HARDCODED")
    return payload


def evaluate_tamper_probe(
    *,
    returncode: int,
    stdout: bytes,
    stderr: bytes,
    remaining_abort_ids: int,
) -> dict[str, int]:
    if (
        returncode == 2
        and stdout == b""
        and stderr == b"RESOURCE_LABEL_MISMATCH\n"
        and remaining_abort_ids == 5
    ):
        return {
            "tamper_rejected": 1,
            "tamper_reason_verified": 1,
            "tamper_zero_delete": 1,
        }
    _fail("CRASH_TAMPER_INVALID")
    raise AssertionError("unreachable")


def apply_post_stop_fallback(
    leftover: tuple[int, int, int],
    destroyers: Sequence[Callable[[], None]],
) -> int:
    if leftover == (0, 0, 0):
        return 0
    for destroy in destroyers:
        destroy()
    return 1


def reject_fallback_cleanup(fallback_cleanup_used: int) -> None:
    if fallback_cleanup_used != 0:
        _fail("CRASH_FALLBACK_CLEANUP_USED")


def observe_stable_zero(
    samples: Sequence[tuple[tuple[int, int, int], float]],
    *,
    min_gap: float = 0.5,
) -> int:
    if len(samples) < 2:
        _fail("CRASH_UNSTABLE_ZERO")
    previous, previous_at = samples[-2]
    current, current_at = samples[-1]
    if (
        previous != (0, 0, 0)
        or current != (0, 0, 0)
        or (current_at - previous_at) < min_gap
    ):
        _fail("CRASH_UNSTABLE_ZERO")
    return 2


def _write_closed_json(
    path: Path, document: Mapping[str, Any], keys: tuple[str, ...]
) -> None:
    if set(document) != set(keys):
        _fail("CRASH_RECEIPT_KEYS_INVALID")
    encoded = (
        json.dumps(
            dict(document),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    raw = encoded.lower()
    for token in (
        "dsn",
        "password",
        "secret",
        "token",
        "ark",
        "body",
        "content",
        "object_key",
        "minio_user",
    ):
        if token in raw:
            _fail("CRASH_RECEIPT_SECRET_REJECTED")
    parent = path.parent
    if not parent.is_dir():
        _fail("CRASH_RECEIPT_PARENT_MISSING")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    tmp = parent / (".receipt.tmp." + os.urandom(8).hex())
    descriptor = os.open(tmp, flags, 0o600)
    try:
        os.write(descriptor, encoded.encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _read_crash_receipt(path: Path) -> dict[str, Any]:
    if path.is_symlink() or path.parent.is_symlink():
        _fail("CRASH_RECEIPT_SYMLINK")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        _fail("CRASH_RECEIPT_LINK")
    if stat.S_IMODE(info.st_mode) != 0o600:
        _fail("CRASH_RECEIPT_MODE")
    document = json.loads(path.read_bytes().decode("ascii"))
    if not isinstance(document, dict) or set(document) != set(CRASH_RECEIPT_KEYS):
        _fail("CRASH_RECEIPT_KEYS_INVALID")
    if document.get("schema") != CRASH_RECEIPT_SCHEMA:
        _fail("CRASH_RECEIPT_SCHEMA")
    return document


def require_three_labels(
    labels: dict[str, Any],
    scope: str,
    project_id: str,
    parent_project_id: str,
) -> None:
    if (
        not isinstance(labels, dict)
        or labels.get("io.anhuan.scope") != scope
        or labels.get("io.anhuan.project-id") != project_id
        or labels.get("io.anhuan.parent-project-id") != parent_project_id
    ):
        _fail("RESOURCE_LABEL_MISMATCH")


def destroy_labeled_resources(
    inspects: list[dict[str, Any]],
    *,
    names: tuple[str, ...],
    scope: str,
    project_id: str,
    parent_project_id: str,
    destroyer: Any,
) -> None:
    found = {str(item.get("Name") or ""): item for item in inspects}
    verified: list[str] = []
    for name in names:
        item = found.get(name)
        if item is None:
            _fail("RESOURCE_LABEL_MISMATCH")
        require_three_labels(
            item.get("Labels") or {},
            scope,
            project_id,
            parent_project_id,
        )
        verified.append(name)
    destroyer.destructive_started = 1
    destroyer.destroy(tuple(verified))


def _translate(exc: BackupContractError) -> None:
    _fail(str(exc) or "BACKUP_CONTRACT_INVALID")


def _wrap_contract(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except BackupContractError as exc:
        _translate(exc)
        raise AssertionError("unreachable") from exc


def _scan_forbidden(root: Path) -> None:
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            _fail("PACKAGE_SCAN_FAILED")
        for entry in entries:
            name = entry.name
            if name.lower() in FORBIDDEN_NAMES or name == ".minio.sys":
                _fail("PACKAGE_FORBIDDEN_ENTRY")
            if entry.is_symlink():
                _fail("PACKAGE_SYMLINK_REJECTED")
            if entry.is_dir(follow_symlinks=False):
                pending.append(Path(entry.path))
            elif not entry.is_file(follow_symlinks=False):
                _fail("PACKAGE_ENTRY_TYPE_INVALID")


def _validate_snapshot(document: dict[str, Any]) -> dict[str, Any]:
    table_count = _wrap_contract(
        _validate_nonnegative_integer, document["table_count"], "BUSINESS_TABLE_COUNT"
    )
    total = _wrap_contract(
        _validate_nonnegative_integer,
        document["total_row_count"],
        "BUSINESS_TOTAL_ROW_COUNT",
    )
    nonempty = _wrap_contract(
        _validate_nonnegative_integer,
        document["nonempty_table_count"],
        "BUSINESS_NONEMPTY_TABLE_COUNT",
    )
    digest = document["count_sha256"]
    if (
        table_count != 38
        or nonempty <= 0
        or nonempty > table_count
        or total < nonempty
        or not isinstance(digest, str)
        or not _SHA256.fullmatch(digest)
    ):
        _fail("BUSINESS_SNAPSHOT_VALUE_INVALID")
    return {
        "table_count": table_count,
        "total_row_count": total,
        "nonempty_table_count": nonempty,
        "count_sha256": digest,
    }


def _validate_manifest(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != _MANIFEST_FIELDS:
        _fail("MANIFEST_SCHEMA_INVALID")
    if document["schema"] != SCHEMA:
        _fail("MANIFEST_SCHEMA_INVALID")
    if document["f1_head"] != F1_HEAD:
        _fail("F1_HEAD_MISMATCH")
    if document["scope"] != SCOPE:
        _fail("RESOURCE_LABEL_MISMATCH")
    project = _wrap_contract(_validate_identifier, document["project_id"], "MANIFEST_PROJECT_ID")
    parent = _wrap_contract(
        _validate_identifier, document["parent_project_id"], "MANIFEST_PARENT_PROJECT_ID"
    )
    database = _wrap_contract(
        _validate_identifier, document["database"], "MANIFEST_DATABASE"
    )
    for field in ("db_dump_sha256", "business_count_sha256", "minio_tree_sha256"):
        if not isinstance(document[field], str) or not _SHA256.fullmatch(document[field]):
            _fail("MANIFEST_DIGEST_INVALID")
    for field in (
        "db_dump_size",
        "business_table_count",
        "business_total_row_count",
        "business_nonempty_table_count",
        "minio_file_count",
        "minio_total_size",
    ):
        _wrap_contract(_validate_nonnegative_integer, document[field], "MANIFEST_COUNT")
    if document["db_dump_size"] <= 0:
        _fail("DATABASE_DUMP_EMPTY")
    if document["minio_file_count"] <= 0:
        _fail("MINIO_TREE_EMPTY")
    snapshot = _validate_snapshot(
        {
            "table_count": document["business_table_count"],
            "total_row_count": document["business_total_row_count"],
            "nonempty_table_count": document["business_nonempty_table_count"],
            "count_sha256": document["business_count_sha256"],
        }
    )
    if snapshot["table_count"] != 38:
        _fail("TABLE_CONTRACT_INVALID")
    created = document["created_at"]
    if not isinstance(created, str) or not _UTC_TIMESTAMP.fullmatch(created):
        _fail("MANIFEST_TIMESTAMP_INVALID")
    document["project_id"] = project
    document["parent_project_id"] = parent
    document["database"] = database
    return document


def create_manifest(
    stage_dir: str | os.PathLike[str],
    *,
    project_id: str,
    parent_project_id: str,
    database: str,
    scope: str,
    f1_head: str,
    business_snapshot: dict[str, Any],
) -> dict[str, Any]:
    if f1_head != F1_HEAD:
        _fail("F1_HEAD_MISMATCH")
    if scope != SCOPE:
        _fail("RESOURCE_LABEL_MISMATCH")
    root = _wrap_contract(_validated_root, stage_dir)
    project = _wrap_contract(_validate_identifier, project_id, "PROJECT_ID")
    parent = _wrap_contract(_validate_identifier, parent_project_id, "PARENT_PROJECT_ID")
    database_name = _wrap_contract(_validate_identifier, database, "DATABASE")
    business = _validate_snapshot(business_snapshot)
    _wrap_contract(_require_exact_root, root, {DATABASE_DUMP_NAME, MINIO_DIRECTORY_NAME})
    _scan_forbidden(root / MINIO_DIRECTORY_NAME)
    db_digest, db_size = _wrap_contract(
        _hash_regular_file, root / DATABASE_DUMP_NAME, "DATABASE_DUMP"
    )
    if db_size <= 0:
        _fail("DATABASE_DUMP_EMPTY")
    tree_digest, file_count, total_size = _wrap_contract(
        _tree_summary, root / MINIO_DIRECTORY_NAME
    )
    if file_count <= 0:
        _fail("MINIO_TREE_EMPTY")
    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "f1_head": F1_HEAD,
        "project_id": project,
        "parent_project_id": parent,
        "database": database_name,
        "scope": SCOPE,
        "db_dump_sha256": db_digest,
        "db_dump_size": db_size,
        "business_table_count": business["table_count"],
        "business_total_row_count": business["total_row_count"],
        "business_nonempty_table_count": business["nonempty_table_count"],
        "business_count_sha256": business["count_sha256"],
        "minio_tree_sha256": tree_digest,
        "minio_file_count": file_count,
        "minio_total_size": total_size,
        "created_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _validate_manifest(manifest)
    _wrap_contract(_atomic_write_manifest, root, _canonical_json(manifest))
    _wrap_contract(
        _require_exact_root,
        root,
        {DATABASE_DUMP_NAME, MINIO_DIRECTORY_NAME, MANIFEST_NAME},
    )
    return manifest


def verify_package(
    backup_dir: str | os.PathLike[str],
    *,
    expected_project_id: str,
    expected_parent_project_id: str,
    expected_database: str,
    expected_scope: str,
) -> dict[str, Any]:
    if expected_scope != SCOPE:
        _fail("RESOURCE_LABEL_MISMATCH")
    root = _wrap_contract(_validated_root, backup_dir)
    project = _wrap_contract(
        _validate_identifier, expected_project_id, "EXPECTED_PROJECT_ID"
    )
    parent = _wrap_contract(
        _validate_identifier, expected_parent_project_id, "EXPECTED_PARENT_PROJECT_ID"
    )
    database = _wrap_contract(
        _validate_identifier, expected_database, "EXPECTED_DATABASE"
    )
    _wrap_contract(
        _require_exact_root,
        root,
        {DATABASE_DUMP_NAME, MINIO_DIRECTORY_NAME, MANIFEST_NAME},
    )
    _scan_forbidden(root / MINIO_DIRECTORY_NAME)
    raw = _wrap_contract(_read_small_regular_file, root / MANIFEST_NAME, "MANIFEST")
    try:
        document = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_fields
        )
    except BackupContractError as exc:
        _translate(exc)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("MANIFEST_JSON_INVALID")
    manifest = _validate_manifest(document)
    if raw != _canonical_json(manifest):
        _fail("MANIFEST_NOT_CANONICAL")
    if manifest["project_id"] != project:
        _fail("MANIFEST_PROJECT_ID_MISMATCH")
    if manifest["parent_project_id"] != parent:
        _fail("MANIFEST_PARENT_PROJECT_ID_MISMATCH")
    if manifest["database"] != database:
        _fail("MANIFEST_DATABASE_MISMATCH")
    db_digest, db_size = _wrap_contract(
        _hash_regular_file, root / DATABASE_DUMP_NAME, "DATABASE_DUMP"
    )
    if db_digest != manifest["db_dump_sha256"] or db_size != manifest["db_dump_size"]:
        _fail("DATABASE_DUMP_MISMATCH")
    if db_size <= 0:
        _fail("DATABASE_DUMP_EMPTY")
    tree_digest, file_count, total_size = _wrap_contract(
        _tree_summary, root / MINIO_DIRECTORY_NAME
    )
    if (
        tree_digest != manifest["minio_tree_sha256"]
        or file_count != manifest["minio_file_count"]
        or total_size != manifest["minio_total_size"]
    ):
        _fail("MINIO_TREE_MISMATCH")
    if file_count <= 0:
        _fail("MINIO_TREE_EMPTY")
    return manifest


def selectable_data_volumes(
    inspects: list[dict[str, Any]],
    *,
    scope: str,
    project_id: str,
    parent_project_id: str,
) -> tuple[str, str]:
    selected: list[str] = []
    for item in inspects:
        labels = item.get("Labels") or {}
        name = str(item.get("Name") or "")
        item_scope = labels.get("io.anhuan.scope")
        if item_scope != scope:
            continue
        if (
            labels.get("io.anhuan.project-id") != project_id
            or labels.get("io.anhuan.parent-project-id") != parent_project_id
        ):
            _fail("RESOURCE_LABEL_MISMATCH")
        if name.endswith(DATA_VOLUME_SUFFIXES):
            selected.append(name)
    postgres = [name for name in selected if name.endswith("_br_postgres_data")]
    minio = [name for name in selected if name.endswith("_br_minio_data")]
    if len(postgres) != 1 or len(minio) != 1:
        _fail("RESOURCE_VOLUME_SET_INVALID")
    return postgres[0], minio[0]


@dataclass
class RestorePlan:
    manifest: dict[str, Any]
    postgres_volume: str
    minio_volume: str


def plan_restore(
    backup_dir: str | os.PathLike[str],
    *,
    expected_project_id: str,
    expected_parent_project_id: str,
    expected_database: str,
    expected_scope: str,
    volume_inspects: list[dict[str, Any]],
) -> RestorePlan:
    manifest = verify_package(
        backup_dir,
        expected_project_id=expected_project_id,
        expected_parent_project_id=expected_parent_project_id,
        expected_database=expected_database,
        expected_scope=expected_scope,
    )
    postgres_volume, minio_volume = selectable_data_volumes(
        volume_inspects,
        scope=expected_scope,
        project_id=expected_project_id,
        parent_project_id=expected_parent_project_id,
    )
    return RestorePlan(
        manifest=manifest,
        postgres_volume=postgres_volume,
        minio_volume=minio_volume,
    )


def guarded_restore(
    backup_dir: str | os.PathLike[str],
    *,
    expected_project_id: str,
    expected_parent_project_id: str,
    expected_database: str,
    expected_scope: str,
    volume_inspects: list[dict[str, Any]],
    destroyer: Any,
) -> RestorePlan:
    plan = plan_restore(
        backup_dir,
        expected_project_id=expected_project_id,
        expected_parent_project_id=expected_parent_project_id,
        expected_database=expected_database,
        expected_scope=expected_scope,
        volume_inspects=volume_inspects,
    )
    destroyer.destructive_started = 1
    destroyer.destroy((plan.postgres_volume, plan.minio_volume))
    return plan


def business_snapshot_from_connection(connection: psycopg.Connection) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for name in MATERIAL_RAG_BACKUP_TABLES:
        if not _TABLE_NAME.fullmatch(name):
            _fail("TABLE_NAME_INVALID")
        row = connection.execute(
            f'SELECT count(*)::bigint FROM f1."{name}"'
        ).fetchone()
        counts[name] = int(row[0]) if row else 0
    if set(counts) != set(MATERIAL_RAG_BACKUP_TABLES):
        _fail("TABLE_CONTRACT_INVALID")
    digest = hashlib.sha256(b"ANHUAN_BUSINESS_COUNTS_V1\x00")
    for name in sorted(counts):
        encoded = name.encode("ascii")
        digest.update(len(encoded).to_bytes(2, "big"))
        digest.update(encoded)
        digest.update(counts[name].to_bytes(8, "big"))
    nonempty = sum(value > 0 for value in counts.values())
    snapshot = {
        "table_count": len(counts),
        "total_row_count": sum(counts.values()),
        "nonempty_table_count": nonempty,
        "count_sha256": digest.hexdigest(),
    }
    return _validate_snapshot(snapshot)


def _write_secret(path: Path, value: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, value.encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.geteuid()
    ):
        _fail("SECRET_FILE_INVALID")


def _replace_secret(path: Path, value: str) -> None:
    if path.exists() or path.is_symlink():
        path.unlink()
    _write_secret(path, value)


def _docker_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        "HOME": os.environ.get("HOME", ""),
        "PATH": os.environ.get("PATH", ""),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    docker_host = os.environ.get("DOCKER_HOST", "").strip()
    if docker_host:
        env["DOCKER_HOST"] = docker_host
    docker_bin = str(DOCKER.parent)
    if docker_bin not in env["PATH"].split(":"):
        env["PATH"] = docker_bin + (":" + env["PATH"] if env["PATH"] else "")
    if extra:
        env.update(extra)
    return {key: value for key, value in env.items() if value}


def _run(
    arguments: list[str],
    *,
    environment: dict[str, str],
    timeout: int,
    cwd: Path = ROOT,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            arguments,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupRestoreError("PROCESS_FAILED") from exc


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    if port in {5432, 55432, 9000}:
        return _free_port()
    return port


def dedicated_counts() -> tuple[int, int, int]:
    def count(args: list[str]) -> int:
        raw = subprocess.check_output([str(DOCKER), *args], text=True).strip()
        return len([line for line in raw.splitlines() if line])

    containers = count(
        ["ps", "-a", "--filter", f"label=io.anhuan.scope={SCOPE}", "--format", "{{.ID}}"]
    )
    volumes = count(
        [
            "volume",
            "ls",
            "--filter",
            f"label=io.anhuan.scope={SCOPE}",
            "--format",
            "{{.Name}}",
        ]
    )
    networks = count(
        [
            "network",
            "ls",
            "--filter",
            f"label=io.anhuan.scope={SCOPE}",
            "--format",
            "{{.ID}}",
        ]
    )
    return containers, volumes, networks


class FakeLock:
    def acquire(self, *args: object, **kwargs: object) -> bool:
        return True

    def release(self) -> None:
        return None


class FakeRedis:
    @classmethod
    def from_url(cls, *args: object, **kwargs: object) -> FakeRedis:
        return cls()

    def lock(self, *args: object, **kwargs: object) -> FakeLock:
        return FakeLock()


class DeterministicRagFlow:
    def __init__(self) -> None:
        self.datasets: dict[str, dict[str, str]] = {}
        self.documents: dict[str, dict[str, dict[str, str]]] = {}
        self.chunks: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
        self.fail_next: str | None = None

    def handle(self, method: str, path: str, token: object, payload: Any = None):
        del token
        if self.fail_next == "connection":
            self.fail_next = None
            raise ConnectionError("MATERIAL_RAG_NETWORK_FAILED")
        parsed = urlparse(path)
        segs = [item for item in parsed.path.split("/") if item]
        if method == "GET" and segs == ["datasets"]:
            items = list(self.datasets.values())
            return 200, {"code": 0, "data": {"datasets": items, "total": len(items)}}
        if method == "POST" and segs == ["datasets"]:
            name = str((payload or {}).get("name") or "")
            dataset_id = hashlib.sha256(name.encode("ascii")).hexdigest()[:32]
            self.datasets[dataset_id] = {"id": dataset_id, "name": name}
            self.documents.setdefault(dataset_id, {})
            self.chunks.setdefault(dataset_id, {})
            return 200, {"code": 0, "data": {"id": dataset_id, "name": name}}
        if method == "DELETE" and segs == ["datasets"]:
            ids = list((payload or {}).get("ids") or [])
            removed = 0
            for dataset_id in ids:
                if dataset_id in self.datasets:
                    del self.datasets[dataset_id]
                    self.documents.pop(dataset_id, None)
                    self.chunks.pop(dataset_id, None)
                    removed += 1
            return 200, {"code": 0, "data": {"success_count": removed}}
        if len(segs) >= 3 and segs[0] == "datasets":
            dataset_id = segs[1]
            if method == "GET" and segs[2:] == ["documents"]:
                docs = list(self.documents.get(dataset_id, {}).values())
                return 200, {"code": 0, "data": {"docs": docs, "total": len(docs)}}
            if method == "POST" and segs[2:] == ["documents"]:
                name = str((payload or {}).get("name") or "")
                document_id = hashlib.sha256(
                    f"{dataset_id}:{name}".encode("ascii")
                ).hexdigest()[:32]
                self.documents.setdefault(dataset_id, {})[document_id] = {
                    "id": document_id,
                    "name": name,
                }
                self.chunks.setdefault(dataset_id, {}).setdefault(document_id, {})
                return 200, {"code": 0, "data": {"id": document_id, "name": name}}
            if method == "DELETE" and segs[2:] == ["documents"]:
                ids = list((payload or {}).get("ids") or [])
                removed = 0
                for document_id in ids:
                    if document_id in self.documents.get(dataset_id, {}):
                        self.chunks.get(dataset_id, {}).pop(document_id, {})
                        del self.documents[dataset_id][document_id]
                        removed += 1
                return 200, {"code": 0, "data": {"success_count": removed}}
            if len(segs) >= 4 and segs[2] == "documents":
                document_id = segs[3]
                if method == "GET" and (len(segs) == 5 and segs[4] == "chunks"):
                    items = list(
                        self.chunks.get(dataset_id, {}).get(document_id, {}).values()
                    )
                    return 200, {
                        "code": 0,
                        "data": {"chunks": items, "total": len(items)},
                    }
                if method == "GET" and len(segs) == 6 and segs[4] == "chunks":
                    chunk = (
                        self.chunks.get(dataset_id, {})
                        .get(document_id, {})
                        .get(segs[5], {})
                    )
                    return 200, {"code": 0, "data": chunk}
                if method == "POST" and segs[-1] == "chunks":
                    content = str((payload or {}).get("content") or "")
                    tags = list((payload or {}).get("tag_kwd") or [])
                    chunk_id = hashlib.sha256(
                        f"{dataset_id}:{document_id}:{content}".encode("utf-8")
                    ).hexdigest()[:32]
                    chunk = {
                        "id": chunk_id,
                        "chunk_id": chunk_id,
                        "content": content,
                        "tag_kwd": tags,
                    }
                    self.chunks.setdefault(dataset_id, {}).setdefault(document_id, {})[
                        chunk_id
                    ] = chunk
                    return 200, {"code": 0, "data": chunk}
        return 404, {"code": 404, "data": {}}


class BackupRestoreStack:
    def __init__(self) -> None:
        self.run_id = uuid.uuid4().hex
        self.project_id = uuid.uuid4().hex
        self.parent_project_id = uuid.uuid4().hex
        self.project_name = f"anhuan-mr-br-{self.run_id[:12]}"
        self.database = f"f1_mrbr_{self.run_id[:12]}"
        self.host_port = _free_port()
        self.minio_port = _free_port()
        self.control_dir = Path(f"/private/tmp/anhuan-mr-br-{self.run_id[:12]}")
        self.backup_dir = self.control_dir / "backup"
        self.package_dir = self.control_dir / "package"
        self.passwords = {
            "bootstrap": secrets.token_hex(24),
            "migration": secrets.token_hex(24),
            "runtime": secrets.token_hex(24),
            "worker": secrets.token_hex(24),
            "f1_api": secrets.token_hex(24),
            "f1_worker": secrets.token_hex(24),
            "minio_user": secrets.token_hex(8),
            "minio_password": secrets.token_hex(16),
            "material_key": secrets.token_hex(32),
            "manifest_key": secrets.token_hex(32),
        }
        self.secrets_dir: Path | None = None
        self.before_fingerprint = b""
        self.started = False
        self.destructive_started = 0
        self.rebuild_started = 0
        self.stage_mutation = {
            "maintenance": 0,
            "rebuild": 0,
            "restart": 0,
            "restore": 0,
        }
        self.cleanup_status = "NOT_STARTED"
        self.restore_abort_new_ids: tuple[str, ...] = ()
        self.retry_abort_id_reuse_count = 0
        self.restore_abort_metrics: dict[str, int] = {
            "same_count_swap_observed": -1,
            "new_volume_count": -1,
            "new_container_count": -1,
            "deleted_count": -1,
            "remaining_abort_id_count": -1,
            "package_reverified": 0,
            "rebuild_started": -1,
            "retry_abort_id_reuse_count": -1,
            "journal_stage_recovered": 0,
        }
        self.shared_match = 0
        self.dedicated_after = (-1, -1, -1)
        self.lifecycle_scope_ids: tuple[uuid.UUID, ...] = ()
        self.rag_fake = DeterministicRagFlow()
        self._redis_patch: Any = None
        self._rag_patch: Any = None
        self.world: LifecycleWorld | None = None

    @classmethod
    def attach_from_receipt(cls, receipt: Mapping[str, Any]) -> "BackupRestoreStack":
        stack = cls()
        stack.project_id = str(receipt["project_id"])
        stack.parent_project_id = str(receipt["parent_project_id"])
        stack.project_name = str(receipt["project_name"])
        stack.control_dir = Path(str(receipt["control_dir"]))
        stack.backup_dir = stack.control_dir / "backup"
        stack.package_dir = stack.control_dir / "package"
        secrets_dir = stack.control_dir / "secrets"
        if secrets_dir.is_dir():
            stack.secrets_dir = secrets_dir
        compose_env = stack.control_dir / "compose.env"
        if compose_env.is_file() and not compose_env.is_symlink():
            values: dict[str, str] = {}
            for line in compose_env.read_text(encoding="ascii").splitlines():
                if "=" in line:
                    key, value = line.split("=", 1)
                    values[key] = value
            database = values.get("LOCAL_MATERIAL_RAG_DATABASE")
            if database:
                stack.database = database
            host_port = values.get("LOCAL_MATERIAL_RAG_BR_HOST_PORT")
            if host_port:
                stack.host_port = int(host_port)
            minio_port = values.get("LOCAL_MATERIAL_RAG_BR_MINIO_PORT")
            if minio_port:
                stack.minio_port = int(minio_port)
        stack.started = True
        return stack

    def _compose_docker_env(self) -> dict[str, str]:
        if self.secrets_dir is None:
            _fail("SECRETS_DIR_MISSING")
        return _docker_env(
            {
                "LOCAL_MATERIAL_RAG_PROJECT_ID": self.project_id,
                "LOCAL_PARENT_PROJECT_ID": self.parent_project_id,
                "LOCAL_MATERIAL_RAG_DATABASE": self.database,
                "LOCAL_MATERIAL_RAG_BR_SECRETS_DIR": str(self.secrets_dir),
                "LOCAL_MATERIAL_RAG_BR_BACKUP_DIR": str(self.backup_dir),
                "LOCAL_MATERIAL_RAG_BR_HOST_PORT": str(self.host_port),
                "LOCAL_MATERIAL_RAG_BR_MINIO_PORT": str(self.minio_port),
                "LOCAL_UID": str(os.getuid()),
                "LOCAL_GID": str(os.getgid()),
            }
        )

    def runtime_env(self) -> dict[str, str]:
        if self.secrets_dir is None:
            _fail("SECRETS_DIR_MISSING")
        env = os.environ.copy()
        env.update(
            {
                "F1_PG_HOST": "127.0.0.1",
                "F1_PG_PORT": str(self.host_port),
                "F1_PG_DATABASE": self.database,
                "F1_SECRETS_DIR": str(self.secrets_dir),
                "F1_KEYCLOAK_REALM": "anhuan",
                "KEYCLOAK_URL": "http://material-rag.invalid",
                "F1_KEYCLOAK_ISSUER_URL": "http://material-rag.invalid/realms/anhuan",
                "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT),
                "F1_PROVIDER_SECRETS_DIR": str(self.secrets_dir),
                "MINIO_ENDPOINT": f"127.0.0.1:{self.minio_port}",
            }
        )
        return env

    def apply_env(self) -> None:
        os.environ.update(
            {
                "F1_PG_HOST": "127.0.0.1",
                "F1_PG_PORT": str(self.host_port),
                "F1_PG_DATABASE": self.database,
                "F1_SECRETS_DIR": str(self.secrets_dir or ""),
                "F1_PROVIDER_SECRETS_DIR": str(self.secrets_dir or ""),
                "F1_KEYCLOAK_REALM": "anhuan",
                "KEYCLOAK_URL": "http://material-rag.invalid",
                "F1_KEYCLOAK_ISSUER_URL": "http://material-rag.invalid/realms/anhuan",
                "MINIO_ENDPOINT": f"127.0.0.1:{self.minio_port}",
            }
        )

    def _write_secret_set(self) -> None:
        assert self.secrets_dir is not None
        mapping = {
            "f0d_bootstrap_password": self.passwords["bootstrap"],
            "f0d_migration_password": self.passwords["migration"],
            "f0d_runtime_password": self.passwords["runtime"],
            "f0d_worker_password": self.passwords["worker"],
            "f1_api_password": self.passwords["f1_api"],
            "f1_worker_password": self.passwords["f1_worker"],
            "minio_root_user": self.passwords["minio_user"],
            "minio_root_password": self.passwords["minio_password"],
            "f1_material_rag_key": self.passwords["material_key"],
            "f1_material_rag_manifest_key": self.passwords["manifest_key"],
            "ragflow_api_key": secrets.token_hex(16),
        }
        for name, value in mapping.items():
            target = self.secrets_dir / name
            if target.exists() or target.is_symlink():
                _replace_secret(target, value)
            else:
                _write_secret(target, value)
        pgpass = (
            f"postgres:5432:{self.database}:f0d_bootstrap:"
            f"{self.passwords['bootstrap']}\n"
        )
        _replace_secret(self.secrets_dir / "pgpass", pgpass.rstrip("\n"))
        os.chmod(self.secrets_dir / "pgpass", 0o600)

    def _write_dsns(self) -> None:
        assert self.secrets_dir is not None

        def dsn(user: str, password: str) -> str:
            return (
                f"postgresql://{user}:{quote(password, safe='')}"
                f"@127.0.0.1:{self.host_port}/{self.database}"
            )

        _replace_secret(
            self.secrets_dir / "f0d_migration_dsn",
            dsn("f0d_migration", self.passwords["migration"]),
        )
        _replace_secret(
            self.secrets_dir / "f1_bootstrap_dsn",
            dsn("f0d_bootstrap", self.passwords["bootstrap"]),
        )
        _replace_secret(
            self.secrets_dir / "f1_migration_dsn",
            dsn("f0d_migration", self.passwords["migration"]),
        )

    def _write_compose_env(self) -> Path:
        assert self.secrets_dir is not None
        compose_env = self.control_dir / "compose.env"
        payload = "\n".join(
            (
                f"LOCAL_MATERIAL_RAG_PROJECT_ID={self.project_id}",
                f"LOCAL_PARENT_PROJECT_ID={self.parent_project_id}",
                f"LOCAL_MATERIAL_RAG_DATABASE={self.database}",
                f"LOCAL_MATERIAL_RAG_BR_SECRETS_DIR={self.secrets_dir}",
                f"LOCAL_MATERIAL_RAG_BR_BACKUP_DIR={self.backup_dir}",
                f"LOCAL_MATERIAL_RAG_BR_HOST_PORT={self.host_port}",
                f"LOCAL_MATERIAL_RAG_BR_MINIO_PORT={self.minio_port}",
                f"LOCAL_UID={os.getuid()}",
                f"LOCAL_GID={os.getgid()}",
            )
        ) + "\n"
        if compose_env.exists() or compose_env.is_symlink():
            compose_env.unlink()
        _write_secret(compose_env, payload.rstrip("\n"))
        return compose_env

    def _compose(self, *args: str, timeout: int = 120) -> subprocess.CompletedProcess[bytes]:
        compose_env = self.control_dir / "compose.env"
        return _run(
            [
                str(DOCKER),
                "compose",
                "--progress",
                "quiet",
                "--project-name",
                self.project_name,
                "--env-file",
                str(compose_env),
                "-f",
                str(COMPOSE_FILE),
                *args,
            ],
            environment=self._compose_docker_env(),
            timeout=timeout,
        )

    def _bootstrap(self):
        return psycopg.connect(
            host="127.0.0.1",
            port=self.host_port,
            dbname=self.database,
            user="f0d_bootstrap",
            password=self.passwords["bootstrap"],
        )

    def _wait_postgres(self) -> None:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                with self._bootstrap() as connection:
                    identity = connection.execute(
                        "SELECT current_database(), current_user"
                    ).fetchone()
                if identity == (self.database, "f0d_bootstrap"):
                    return
            except psycopg.Error:
                time.sleep(0.5)
        self.stop()
        _fail("POSTGRES_READY_FAILED")

    def _wait_minio(self) -> None:
        from minio import Minio

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                client = Minio(
                    f"127.0.0.1:{self.minio_port}",
                    access_key=self.passwords["minio_user"],
                    secret_key=self.passwords["minio_password"],
                    secure=False,
                )
                client.list_buckets()
                return
            except Exception:
                time.sleep(0.5)
        self.stop()
        _fail("MINIO_READY_FAILED")

    def _minio_client(self):
        from minio import Minio

        return Minio(
            f"127.0.0.1:{self.minio_port}",
            access_key=self.passwords["minio_user"],
            secret_key=self.passwords["minio_password"],
            secure=False,
        )

    def _ensure_buckets(self) -> None:
        client = self._minio_client()
        for bucket in (DOCUMENTS_BUCKET, QUARANTINE_BUCKET, PREVIEW_BUCKET):
            if not client.bucket_exists(bucket):
                client.make_bucket(bucket)

    def install_fakes(self) -> None:
        from unittest.mock import patch

        from platform_foundation.f0j1.ragflow_client import RagFlowClient

        self._redis_patch = patch("redis.Redis.from_url", FakeRedis.from_url)
        self._rag_patch = patch.object(RagFlowClient, "_request", self.rag_fake.handle)
        self._redis_patch.start()
        self._rag_patch.start()

    def uninstall_fakes(self) -> None:
        if self._rag_patch is not None:
            self._rag_patch.stop()
            self._rag_patch = None
        if self._redis_patch is not None:
            self._redis_patch.stop()
            self._redis_patch = None

    def start(self) -> None:
        if dedicated_counts() != (0, 0, 0):
            _fail("DEDICATED_PREEXISTING")
        self.before_fingerprint = canonical_shared_fingerprint()
        self.control_dir.mkdir(mode=0o700)
        info = self.control_dir.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            _fail("CONTROL_DIR_INVALID")
        self.secrets_dir = self.control_dir / "secrets"
        for directory in (
            self.control_dir / "home",
            self.control_dir / "tmp",
            self.secrets_dir,
            self.backup_dir,
            self.package_dir,
        ):
            directory.mkdir(mode=0o700)
        self._write_secret_set()
        self._write_compose_env()
        up = self._compose(
            "up",
            "-d",
            "--force-recreate",
            "--wait",
            "--wait-timeout",
            "90",
            "secret-init",
            "postgres",
            "minio",
            timeout=120,
        )
        if up.returncode != 0:
            self.stop()
            _fail("COMPOSE_UP_FAILED")
        self.started = True
        self._wait_postgres()
        self._wait_minio()
        self._write_dsns()
        self.apply_env()
        self._migrate()
        self._seed_identities()
        self._ensure_buckets()

    def _migrate(self) -> None:
        migrated = _run(
            [PYTHON, "-B", "infra/f1/material-rag/migrate.py"],
            environment=self.runtime_env(),
            timeout=300,
        )
        if (
            migrated.returncode != 0
            or migrated.stdout.decode("ascii", "replace").strip()
            != "LOCAL_MATERIAL_RAG_MIGRATE_OK"
        ):
            self.stop()
            _fail("MIGRATE_FAILED")

    def _seed_identities(self) -> None:
        seeded = _run(
            [PYTHON, "-B", "infra/f1/material-rag/seed.py"],
            environment=self.runtime_env(),
            timeout=60,
        )
        if (
            seeded.returncode != 0
            or seeded.stdout.decode("ascii", "replace").strip()
            != "LOCAL_MATERIAL_RAG_SEED_OK"
        ):
            self.stop()
            _fail("SEED_FAILED")

    def seed_backup_world(self) -> LifecycleWorld:
        from infra.f1 import local_seed
        from platform_foundation.f1.auth import Tenant
        from platform_foundation.f1.database import session_scope
        from platform_foundation.f1.features.material_rag.security import (
            CLIENT_B_ISOLATION_CANARY_TEXT,
            PROVIDER_POLICY_CANARY_TEXT,
        )
        from platform_foundation.f1.features.p3.service import (
            _current_user_id,
            _resolve_knowledge_scope,
        )
        import asyncio

        tenant_a = Tenant(
            enterprise_id=local_seed.ENTERPRISE_A,
            sub="db906685-6906-4bc4-9d3a-9011975fd132",
            roles=("enterprise_admin",),
            role="enterprise_admin",
        )
        tenant_b = Tenant(
            enterprise_id=local_seed.ENTERPRISE_B,
            sub="ddc4e27e-ccde-4c89-958f-798fc8f30175",
            roles=("enterprise_admin",),
            role="enterprise_admin",
        )
        actor_a = local_seed._stable_id("profile", tenant_a.sub)
        actor_b = local_seed._stable_id("profile", tenant_b.sub)
        client_a_id = uuid.uuid5(FIXTURE_NS, "br-client-a")
        client_b_id = uuid.uuid5(FIXTURE_NS, "br-client-b")
        provider_sha = hashlib.sha256(
            PROVIDER_POLICY_CANARY_TEXT.encode("utf-8")
        ).hexdigest()
        client_sha = hashlib.sha256(
            CLIENT_B_ISOLATION_CANARY_TEXT.encode("utf-8")
        ).hexdigest()
        unreleased_bytes = b"life-unreleased-source"
        unreleased_sha = hashlib.sha256(unreleased_bytes).hexdigest()
        bodies = {
            provider_sha: PROVIDER_POLICY_CANARY_TEXT,
            client_sha: CLIENT_B_ISOLATION_CANARY_TEXT,
            unreleased_sha: unreleased_bytes.decode("ascii"),
        }
        with self._bootstrap() as connection:
            replica = connection.execute("SHOW session_replication_role").fetchone()
            if replica is None or replica[0] != "origin":
                _fail("REPLICA_ROLE_FORBIDDEN")
            connection.execute(
                "INSERT INTO f1.crm_account "
                "(id,enterprise_id,display_name,stage,created_by_user_id) "
                "VALUES (%s,%s,%s,'active',%s),(%s,%s,%s,'active',%s)",
                (
                    client_a_id,
                    local_seed.ENTERPRISE_A,
                    "BR Client A",
                    actor_a,
                    client_b_id,
                    local_seed.ENTERPRISE_B,
                    "BR Client B",
                    actor_b,
                ),
            )
            connection.commit()

        async def _seed() -> dict[str, LifecycleDoc]:
            docs: dict[str, LifecycleDoc] = {}
            async with session_scope(
                role="f1_api",
                enterprise_id=tenant_a.enterprise_id,
                sub=tenant_a.sub,
            ) as session:
                actor_id = await _current_user_id(session, tenant_a)
                provider = await _resolve_knowledge_scope(
                    session,
                    tenant_a,
                    kind="service_provider",
                    client_account_id=None,
                    actor_id=actor_id,
                )
                client = await _resolve_knowledge_scope(
                    session,
                    tenant_a,
                    kind="client",
                    client_account_id=client_a_id,
                    actor_id=actor_id,
                )
                docs["provider_a"] = await _insert_lifecycle_document(
                    session,
                    tenant=tenant_a,
                    actor_id=actor_id,
                    scope_id=provider["id"],
                    label="provider-a",
                    source_sha=provider_sha,
                    body=PROVIDER_POLICY_CANARY_TEXT,
                    released=True,
                    material_kind="policy",
                )
                docs["client_a"] = await _insert_lifecycle_document(
                    session,
                    tenant=tenant_a,
                    actor_id=actor_id,
                    scope_id=client["id"],
                    label="client-a",
                    source_sha=client_sha,
                    body=CLIENT_B_ISOLATION_CANARY_TEXT,
                    released=True,
                    material_kind="report",
                )
                docs["unreleased"] = await _insert_lifecycle_document(
                    session,
                    tenant=tenant_a,
                    actor_id=actor_id,
                    scope_id=client["id"],
                    label="unreleased",
                    source_sha=unreleased_sha,
                    body=unreleased_bytes.decode("ascii"),
                    released=False,
                    material_kind="report",
                )
                await session.commit()
            async with session_scope(
                role="f1_api",
                enterprise_id=tenant_b.enterprise_id,
                sub=tenant_b.sub,
            ) as session:
                actor_id = await _current_user_id(session, tenant_b)
                await _resolve_knowledge_scope(
                    session,
                    tenant_b,
                    kind="service_provider",
                    client_account_id=None,
                    actor_id=actor_id,
                )
                client = await _resolve_knowledge_scope(
                    session,
                    tenant_b,
                    kind="client",
                    client_account_id=client_b_id,
                    actor_id=actor_id,
                )
                docs["recovery"] = await _insert_lifecycle_document(
                    session,
                    tenant=tenant_b,
                    actor_id=actor_id,
                    scope_id=client["id"],
                    label="recovery",
                    source_sha=provider_sha,
                    body=PROVIDER_POLICY_CANARY_TEXT,
                    released=True,
                    material_kind="policy",
                )
                docs["revoke"] = await _insert_lifecycle_document(
                    session,
                    tenant=tenant_b,
                    actor_id=actor_id,
                    scope_id=client["id"],
                    label="revoke",
                    source_sha=client_sha,
                    body=CLIENT_B_ISOLATION_CANARY_TEXT,
                    released=True,
                    material_kind="report",
                )
                await session.commit()
            return docs

        docs = asyncio.run(_seed())
        self.lifecycle_scope_ids = tuple(
            sorted({doc.scope_id for doc in docs.values()}, key=str)
        )
        world = LifecycleWorld(
            tenant_a=tenant_a,
            tenant_b=tenant_b,
            docs=docs,
            bodies=bodies,
        )
        self.world = world
        return world

    def put_source_objects(self) -> None:
        client = self._minio_client()
        self._ensure_buckets()
        with self._bootstrap() as connection:
            rows = connection.execute(
                "SELECT object_key, content_sha256 FROM f1.upload_task"
            ).fetchall()
        if self.world is None:
            _fail("WORLD_MISSING")
        for object_key, content_sha in rows:
            body = self.world.bodies.get(str(content_sha))
            if body is None:
                _fail("SOURCE_BODY_MISSING")
            payload = body.encode("utf-8")
            if hashlib.sha256(payload).hexdigest() != str(content_sha):
                _fail("SOURCE_SHA_MISMATCH")
            client.put_object(
                DOCUMENTS_BUCKET,
                str(object_key),
                io.BytesIO(payload),
                length=len(payload),
                content_type="application/pdf",
            )

    def export_minio_tree(self, dest: Path) -> None:
        dest.mkdir(mode=0o700)
        os.chmod(dest, 0o700)
        client = self._minio_client()
        wrote = 0
        for bucket in (DOCUMENTS_BUCKET, QUARANTINE_BUCKET, PREVIEW_BUCKET):
            if not client.bucket_exists(bucket):
                continue
            bucket_dir = dest / bucket
            bucket_dir.mkdir(mode=0o700, exist_ok=True)
            os.chmod(bucket_dir, 0o700)
            for item in client.list_objects(bucket, recursive=True):
                object_key = str(item.object_name or "")
                if not object_key or object_key.endswith("/"):
                    continue
                target = _confine_object_path(bucket_dir, object_key)
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                cursor = target.parent
                while True:
                    os.chmod(cursor, 0o700)
                    if cursor == dest:
                        break
                    cursor = cursor.parent
                response = client.get_object(bucket, object_key)
                try:
                    payload = response.read()
                finally:
                    response.close()
                    response.release_conn()
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(target, flags, 0o600)
                try:
                    os.write(descriptor, payload)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.chmod(target, 0o600)
                wrote += 1
        for path in [dest, *dest.rglob("*")]:
            if path.is_dir():
                os.chmod(path, 0o700)
            elif path.is_file():
                os.chmod(path, 0o600)
        if wrote <= 0:
            _fail("MINIO_TREE_EMPTY")

    def document_count(self) -> int:
        with self._bootstrap() as connection:
            row = connection.execute("SELECT count(*) FROM f1.document").fetchone()
        return int(row[0]) if row else 0

    def minio_inventory_fingerprint(self) -> tuple[int, str]:
        client = self._minio_client()
        digest = hashlib.sha256(b"BR_MINIO_INVENTORY_V1\x00")
        count = 0
        for bucket in (DOCUMENTS_BUCKET, QUARANTINE_BUCKET, PREVIEW_BUCKET):
            if not client.bucket_exists(bucket):
                continue
            for item in client.list_objects(bucket, recursive=True):
                count += 1
                name = str(item.object_name or "")
                digest.update(len(name.encode("utf-8")).to_bytes(8, "big"))
                digest.update(name.encode("utf-8"))
                digest.update(int(item.size or 0).to_bytes(8, "big"))
        return count, digest.hexdigest()

    def canonical_live_object_tree(self) -> list[dict[str, Any]]:
        client = self._minio_client()
        records: list[dict[str, Any]] = []
        for bucket in (DOCUMENTS_BUCKET, QUARANTINE_BUCKET, PREVIEW_BUCKET):
            if not client.bucket_exists(bucket):
                continue
            for item in client.list_objects(bucket, recursive=True):
                object_key = str(item.object_name or "")
                if not object_key or object_key.endswith("/"):
                    continue
                response = client.get_object(bucket, object_key)
                try:
                    body = response.read()
                finally:
                    response.close()
                    response.release_conn()
                records.append(
                    {
                        "body_sha256": hashlib.sha256(body).hexdigest(),
                        "bucket": _hashed_object_name(bucket),
                        "key": _hashed_object_name(object_key),
                        "size": len(body),
                    }
                )
                del body
                del object_key
        records.sort(key=lambda item: (item["bucket"], item["key"], item["body_sha256"]))
        return records

    def prove_same_size_body_mismatch_is_red(self, package: Path) -> None:
        client = self._minio_client()
        chosen_bucket = ""
        chosen_key = ""
        original = b""
        for bucket in (DOCUMENTS_BUCKET, QUARANTINE_BUCKET, PREVIEW_BUCKET):
            if not client.bucket_exists(bucket):
                continue
            for item in client.list_objects(bucket, recursive=True):
                object_key = str(item.object_name or "")
                if not object_key or object_key.endswith("/"):
                    continue
                response = client.get_object(bucket, object_key)
                try:
                    original = response.read()
                finally:
                    response.close()
                    response.release_conn()
                if original:
                    chosen_bucket = bucket
                    chosen_key = object_key
                    break
            if chosen_key:
                break
        if not chosen_key or not original:
            _fail("MINIO_TAMPER_SOURCE_MISSING")
        mutated = bytes(byte ^ 0xFF for byte in original)
        if mutated == original:
            _fail("MINIO_TAMPER_NOOP")
        client.put_object(
            chosen_bucket,
            chosen_key,
            io.BytesIO(mutated),
            length=len(mutated),
        )
        package_tree = canonical_object_tree_from_dir(package / MINIO_DIRECTORY_NAME)
        try:
            compare_object_trees(package_tree, self.canonical_live_object_tree())
        except BackupRestoreError as exc:
            if exc.code != "MINIO_BODY_SHA_MISMATCH":
                raise
        else:
            _fail("MINIO_TAMPER_FALSE_GREEN")
        client.put_object(
            chosen_bucket,
            chosen_key,
            io.BytesIO(original),
            length=len(original),
        )
        compare_object_trees(package_tree, self.canonical_live_object_tree())
        del original
        del mutated
        del chosen_key

    def volume_inspects(self) -> list[dict[str, Any]]:
        raw = subprocess.check_output(
            [
                str(DOCKER),
                "volume",
                "ls",
                "--format",
                "{{.Name}}",
            ],
            text=True,
        )
        inspects: list[dict[str, Any]] = []
        for name in (line for line in raw.splitlines() if line):
            payload = json.loads(
                subprocess.check_output(
                    [str(DOCKER), "volume", "inspect", name], text=True
                )
            )[0]
            inspects.append(
                {
                    "Name": payload["Name"],
                    "Labels": payload.get("Labels") or {},
                    "Mountpoint": payload.get("Mountpoint") or "",
                    "CreatedAt": payload.get("CreatedAt") or "",
                }
            )
        return inspects

    def data_resource_fingerprint(self) -> tuple[str, str]:
        inspects = self.volume_inspects()
        postgres, minio = selectable_data_volumes(
            inspects,
            scope=SCOPE,
            project_id=self.project_id,
            parent_project_id=self.parent_project_id,
        )
        found = {item["Name"]: item for item in inspects}
        def token(name: str) -> str:
            item = found[name]
            return hashlib.sha256(
                f"{item['Name']}\0{item['CreatedAt']}\0{item['Mountpoint']}".encode(
                    "utf-8"
                )
            ).hexdigest()
        return token(postgres), token(minio)

    def alembic_head(self) -> str:
        with self._bootstrap() as connection:
            row = connection.execute(
                "SELECT string_agg(version_num, ',' ORDER BY version_num) "
                "FROM f1.alembic_version"
            ).fetchone()
        if row is None or row[0] != F1_HEAD:
            _fail("F1_HEAD_MISMATCH")
        return str(row[0])

    def snapshot_38(self) -> dict[str, Any]:
        with self._bootstrap() as connection:
            return business_snapshot_from_connection(connection)

    def create_package(self) -> Path:
        if self.backup_dir.exists():
            for child in self.backup_dir.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        dump = self._compose(
            "--profile",
            "ops",
            "run",
            "--rm",
            "--no-deps",
            "backup-db",
            timeout=120,
        )
        if dump.returncode != 0:
            _fail("BACKUP_DB_FAILED")
        dump_path = self.backup_dir / DATABASE_DUMP_NAME
        if dump_path.exists():
            os.chmod(dump_path, 0o600)
        minio_root = self.backup_dir / MINIO_DIRECTORY_NAME
        if minio_root.exists():
            shutil.rmtree(minio_root)
        self.export_minio_tree(minio_root)
        snapshot = self.snapshot_38()
        create_manifest(
            self.backup_dir,
            project_id=self.project_id,
            parent_project_id=self.parent_project_id,
            database=self.database,
            scope=SCOPE,
            f1_head=self.alembic_head(),
            business_snapshot=snapshot,
        )
        return self.backup_dir

    def _stage_package_for_ops(self, package: Path) -> None:
        if package.resolve() == self.backup_dir.resolve():
            return
        for child in self.backup_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        shutil.copy2(package / DATABASE_DUMP_NAME, self.backup_dir / DATABASE_DUMP_NAME)
        os.chmod(self.backup_dir / DATABASE_DUMP_NAME, 0o600)
        shutil.copytree(
            package / MINIO_DIRECTORY_NAME,
            self.backup_dir / MINIO_DIRECTORY_NAME,
        )
        shutil.copy2(package / MANIFEST_NAME, self.backup_dir / MANIFEST_NAME)
        os.chmod(self.backup_dir / MANIFEST_NAME, 0o600)
        os.chmod(self.backup_dir / MINIO_DIRECTORY_NAME, 0o700)
        for path in (self.backup_dir / MINIO_DIRECTORY_NAME).rglob("*"):
            if path.is_dir():
                os.chmod(path, 0o700)
            elif path.is_file():
                os.chmod(path, 0o600)

    def _recovery(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except RestoreRecoveryError as exc:
            raise BackupRestoreError(exc.code) from exc

    def _journal_path(self) -> Path:
        return self.control_dir / "restore.journal"

    def _write_restore_journal(
        self,
        stage: str,
        manifest: dict[str, Any],
        resources: list[dict[str, Any]],
    ) -> None:
        path = self._journal_path()
        records = [{"kind": item["kind"], "id": item["id"]} for item in resources]
        existing: dict[str, Any] | None = None
        if path.exists() or path.is_symlink():
            existing = dict(self._recovery(read_journal, path))
        if stage == "PREPARED":
            if existing is not None and existing["stage"] != "RECOVERED":
                _fail("JOURNAL_IN_PROGRESS")
            if path.exists() or path.is_symlink():
                path.unlink()
            existing = None
        if existing is None:
            if stage != "PREPARED":
                _fail("JOURNAL_STAGE_INVALID")
            document = {
                "f1_head": F1_HEAD,
                "package_dump_sha256": manifest["db_dump_sha256"],
                "package_tree_sha256": manifest["minio_tree_sha256"],
                "parent_project_id": self.parent_project_id,
                "project_id": self.project_id,
                "resources": records,
                "schema": JOURNAL_SCHEMA,
                "scope": SCOPE,
                "stage": stage,
            }
        else:
            document = existing
            if stage != "RECOVERED":
                self._recovery(advance_stage, document["stage"], stage)
            document["stage"] = stage
        self._recovery(write_journal, path, document)
        self._recovery(maybe_crash, stage)
        if (
            stage == "DB_RESTORED"
            and os.environ.get("MATERIAL_RAG_RESTORE_WAIT_AFTER", "").strip()
            == "DB_RESTORED"
        ):
            self._pause_after_journal(stage)

    def _write_crash_receipt(self) -> None:
        target = Path(os.environ["MATERIAL_RAG_CRASH_RECEIPT"])
        document = self._recovery(read_journal, self._journal_path())
        _write_closed_json(
            target,
            {
                "control_dir": str(self.control_dir),
                "f1_head": F1_HEAD,
                "journal_path": str(self._journal_path()),
                "package_dump_sha256": document["package_dump_sha256"],
                "package_path": str(self.backup_dir),
                "package_tree_sha256": document["package_tree_sha256"],
                "parent_project_id": self.parent_project_id,
                "project_id": self.project_id,
                "project_name": self.project_name,
                "schema": CRASH_RECEIPT_SCHEMA,
                "scope": SCOPE,
            },
            CRASH_RECEIPT_KEYS,
        )

    def _pause_after_journal(self, stage: str) -> None:
        self._write_crash_receipt()
        ready = Path(os.environ["MATERIAL_RAG_CRASH_READY"])
        _write_closed_json(
            ready,
            {"ready": 1, "stage": stage},
            ("ready", "stage"),
        )
        while True:
            time.sleep(0.2)

    def _core_label_payload(self, labels: dict[str, Any]) -> dict[str, Any]:
        return {
            "io.anhuan.parent-project-id": labels.get("io.anhuan.parent-project-id"),
            "io.anhuan.project-id": labels.get("io.anhuan.project-id"),
            "io.anhuan.scope": labels.get("io.anhuan.scope"),
        }

    def _labels_owned(self, labels: dict[str, Any]) -> bool:
        return (
            labels.get("io.anhuan.scope") == SCOPE
            and labels.get("io.anhuan.project-id") == self.project_id
            and labels.get("io.anhuan.parent-project-id") == self.parent_project_id
        )

    def _compose_project_filter(self) -> str:
        return f"label=com.docker.compose.project={self.project_name}"

    def _list_project_handles(self, kind: str) -> list[str]:
        args = {
            "container": [
                "ps",
                "-aq",
                "--no-trunc",
                "--filter",
                self._compose_project_filter(),
            ],
            "network": [
                "network",
                "ls",
                "-q",
                "--no-trunc",
                "--filter",
                self._compose_project_filter(),
            ],
            "volume": [
                "volume",
                "ls",
                "-q",
                "--filter",
                self._compose_project_filter(),
            ],
        }[kind]
        listed = subprocess.check_output([str(DOCKER), *args], text=True).strip()
        return [line.strip() for line in listed.splitlines() if line.strip()]

    def _identity_from_docker(
        self, kind: str, handle: str
    ) -> dict[str, Any] | None:
        try:
            if kind == "volume":
                payload = json.loads(
                    subprocess.check_output(
                        [str(DOCKER), "volume", "inspect", handle],
                        text=True,
                        stderr=subprocess.DEVNULL,
                    )
                )[0]
                labels = payload.get("Labels") or {}
                name = str(payload.get("Name") or handle)
                digest = volume_identity_id(
                    name,
                    str(payload.get("CreatedAt") or ""),
                    str(payload.get("Mountpoint") or ""),
                )
                return {
                    "handle": name,
                    "id": digest,
                    "kind": "volume",
                    "labels": self._core_label_payload(labels),
                }
            if kind == "container":
                payload = json.loads(
                    subprocess.check_output(
                        [str(DOCKER), "inspect", handle],
                        text=True,
                        stderr=subprocess.DEVNULL,
                    )
                )[0]
                labels = payload.get("Config", {}).get("Labels") or {}
                resource_id = str(payload.get("Id") or handle).lower().removeprefix(
                    "sha256:"
                )
                return {
                    "handle": resource_id,
                    "id": resource_id,
                    "kind": "container",
                    "labels": self._core_label_payload(labels),
                }
            payload = json.loads(
                subprocess.check_output(
                    [str(DOCKER), "network", "inspect", handle],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
            )[0]
            labels = payload.get("Labels") or {}
            resource_id = str(payload.get("Id") or handle).lower().removeprefix(
                "sha256:"
            )
            return {
                "handle": resource_id,
                "id": resource_id,
                "kind": "network",
                "labels": self._core_label_payload(labels),
            }
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, IndexError, KeyError):
            return None

    def capture_core_identities(
        self, *, also_handles: Sequence[Any] = ()
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for kind in ("container", "network", "volume"):
            for handle in self._list_project_handles(kind):
                item = self._identity_from_docker(kind, handle)
                if item is None:
                    continue
                key = (item["kind"], item["id"])
                if key in seen:
                    continue
                seen.add(key)
                items.append(item)
        for raw in also_handles:
            kind = str(raw.get("kind") or "")
            handle = str(raw.get("handle") or raw.get("id") or "")
            if kind not in {"container", "network", "volume"} or not handle:
                continue
            item = self._identity_from_docker(kind, handle)
            if item is None:
                continue
            key = (item["kind"], item["id"])
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
        return items

    def _destroy_data_volumes(self) -> None:
        inspects = self.volume_inspects()
        postgres, minio = selectable_data_volumes(
            inspects,
            scope=SCOPE,
            project_id=self.project_id,
            parent_project_id=self.parent_project_id,
        )
        found = {item["Name"]: item for item in inspects}
        for name in (postgres, minio):
            labels = found[name]["Labels"]
            if (
                labels.get("io.anhuan.scope") != SCOPE
                or labels.get("io.anhuan.project-id") != self.project_id
                or labels.get("io.anhuan.parent-project-id") != self.parent_project_id
            ):
                _fail("RESOURCE_LABEL_MISMATCH")
        stop = self._compose("stop", "postgres", "minio", timeout=60)
        if stop.returncode != 0:
            _fail("CORE_STOP_FAILED")
        removed_containers = self._compose("rm", "-f", "-s", "postgres", "minio", timeout=60)
        if removed_containers.returncode != 0:
            _fail("CORE_RM_FAILED")
        removed = _run(
            [str(DOCKER), "volume", "rm", "-f", postgres, minio],
            environment=_docker_env(),
            timeout=60,
        )
        if removed.returncode != 0:
            _fail("VOLUME_RM_FAILED")

    def rotate_restore_secrets(self) -> None:
        self.passwords["minio_user"] = secrets.token_hex(8)
        self.passwords["minio_password"] = secrets.token_hex(16)
        self.passwords["material_key"] = secrets.token_hex(32)
        self.passwords["manifest_key"] = secrets.token_hex(32)
        self._write_secret_set()
        self._write_dsns()

    def restore_package(
        self,
        package: Path,
        *,
        fail_before_minio: bool = False,
        inject_failure: bool = False,
    ) -> None:
        expected = {
            "expected_project_id": self.project_id,
            "expected_parent_project_id": self.parent_project_id,
            "expected_database": self.database,
            "expected_scope": SCOPE,
        }
        saved = self.capture_core_identities()
        self.retry_abort_id_reuse_count = len(
            set(self.restore_abort_new_ids)
            & {str(item.get("id")) for item in saved}
        )
        try:
            prepare_empty_core(
                live=saved,
                abort_new_ids=self.restore_abort_new_ids,
            )
        except RestoreRecoveryError as exc:
            raise BackupRestoreError(exc.code) from exc
        inspects = self.volume_inspects()
        owned_data = [
            item
            for item in inspects
            if self._labels_owned(item.get("Labels") or {})
            and str(item.get("Name") or "").endswith(DATA_VOLUME_SUFFIXES)
        ]
        if owned_data:
            plan = plan_restore(
                package,
                volume_inspects=inspects,
                **expected,
            )
            manifest = plan.manifest
            data_present = True
        else:
            manifest = verify_package(package, **expected)
            data_present = False
        self._write_restore_journal("PREPARED", manifest, saved)
        self.destructive_started = 1
        if data_present:
            volume_before = self.data_resource_fingerprint()
            self._destroy_data_volumes()
        else:
            volume_before = ("", "")
        self.rotate_restore_secrets()
        up = self._compose(
            "up",
            "-d",
            "--force-recreate",
            "--wait",
            "--wait-timeout",
            "90",
            "secret-init",
            "postgres",
            "minio",
            timeout=120,
        )
        if up.returncode != 0:
            _fail("RESTORE_COMPOSE_UP_FAILED")
        self._wait_postgres()
        self._wait_minio()
        self._write_dsns()
        self.apply_env()
        self._migrate()
        self._write_restore_journal("VOLUMES_REPLACED", manifest, saved)
        self._stage_package_for_ops(package)
        restored = self._compose(
            "--profile",
            "ops",
            "run",
            "--rm",
            "--no-deps",
            "restore-db",
            timeout=180,
        )
        if restored.returncode != 0:
            _fail("RESTORE_DB_FAILED")
        self._write_restore_journal("DB_RESTORED", manifest, saved)
        volume_after = self.data_resource_fingerprint()
        restore_observed = observe_stage_mutation(volume_before, volume_after)
        if inject_failure:
            self.stage_mutation["restore"] = restore_observed
            if restore_observed != 1:
                _fail("RESTORE_MUTATION_NOT_OBSERVED")
            live = self.capture_core_identities(also_handles=saved)
            destroyer = DockerIdentityDestroyer(self)

            def package_check() -> None:
                verify_package(package, **expected)

            new_items = new_labeled_resources(
                saved,
                live,
                scope=SCOPE,
                project_id=self.project_id,
                parent_project_id=self.parent_project_id,
            )
            try:
                abort_result = abort_new_restore_resources(
                    saved=saved,
                    live=live,
                    scope=SCOPE,
                    project_id=self.project_id,
                    parent_project_id=self.parent_project_id,
                    destroyer=destroyer,
                    package_check=package_check,
                )
            except RestoreRecoveryError as exc:
                raise BackupRestoreError(exc.code) from exc
            deleted_count = abort_result["deleted"]
            new_volume_count = abort_result["new_volume_count"]
            new_container_count = abort_result["new_container_count"]
            if deleted_count != new_volume_count + new_container_count:
                _fail("RESTORE_ABORT_COUNT_MISMATCH")
            remaining_abort_id_count = 0
            for item in new_items:
                if item["kind"] not in ABORT_DELETE_KINDS:
                    continue
                if destroyer.inspect(item["kind"], item["id"]) is not None:
                    remaining_abort_id_count += 1
            if remaining_abort_id_count != 0:
                _fail("RESTORE_ABORT_ID_REMAINS")
            verify_package(package, **expected)
            package_reverified = abort_result["package_reverified"]
            if package_reverified != 1:
                _fail("RESTORE_ABORT_PACKAGE_LOST")
            self.restore_abort_new_ids = tuple(
                item["id"]
                for item in new_items
                if item["kind"] in ABORT_DELETE_KINDS
            )
            self._write_restore_journal("RECOVERED", manifest, saved)
            recovered = self._recovery(read_journal, self._journal_path())
            journal_stage_recovered = int(recovered["stage"] == "RECOVERED")
            self.restore_abort_metrics = {
                "same_count_swap_observed": abort_result["same_count_swap_observed"],
                "new_volume_count": new_volume_count,
                "new_container_count": new_container_count,
                "deleted_count": deleted_count,
                "remaining_abort_id_count": remaining_abort_id_count,
                "package_reverified": package_reverified,
                "rebuild_started": abort_result["rebuild_started"],
                "retry_abort_id_reuse_count": self.retry_abort_id_reuse_count,
                "journal_stage_recovered": journal_stage_recovered,
            }
            _fail("RESTORE_INJECTED_FAILURE")
        if fail_before_minio:
            _fail("MINIO_RESTORE_INJECTED_FAILURE")
        self._replay_minio(package)
        self._wait_minio()
        self._write_restore_journal("MINIO_REPLAYED", manifest, saved)
        self._assert_restored_identity(package)
        self._write_restore_journal("RECOVERED", manifest, saved)

    def _replay_minio(self, package: Path) -> None:
        from minio import Minio

        self._ensure_buckets()
        client = self._minio_client()
        root = package / MINIO_DIRECTORY_NAME
        for path in sorted(root.rglob("*")):
            if path.is_dir():
                continue
            relative = path.relative_to(root).as_posix()
            parts = relative.split("/", 1)
            if len(parts) != 2:
                continue
            bucket, object_key = parts
            if bucket not in {DOCUMENTS_BUCKET, QUARANTINE_BUCKET, PREVIEW_BUCKET}:
                continue
            if not client.bucket_exists(bucket):
                client.make_bucket(bucket)
            data = path.read_bytes()
            client.put_object(
                bucket,
                object_key,
                io.BytesIO(data),
                length=len(data),
            )
        del Minio

    def _assert_restored_identity(self, package: Path) -> None:
        manifest = verify_package(
            package,
            expected_project_id=self.project_id,
            expected_parent_project_id=self.parent_project_id,
            expected_database=self.database,
            expected_scope=SCOPE,
        )
        if self.alembic_head() != F1_HEAD:
            _fail("F1_HEAD_MISMATCH")
        snapshot = self.snapshot_38()
        if (
            snapshot["table_count"] != 38
            or snapshot["count_sha256"] != manifest["business_count_sha256"]
            or snapshot["total_row_count"] != manifest["business_total_row_count"]
            or snapshot["nonempty_table_count"]
            != manifest["business_nonempty_table_count"]
        ):
            _fail("RESTORED_TABLE_CONTRACT_MISMATCH")
        tree = canonical_object_tree_from_dir(package / MINIO_DIRECTORY_NAME)
        compare_object_trees(tree, self.canonical_live_object_tree())
        if not tree:
            _fail("MINIO_TREE_EMPTY")

    def run_maintenance(self, *, inject_failure: bool = False) -> dict[str, Any]:
        with self._bootstrap() as connection:
            try:
                return run_restore_maintenance(
                    connection, inject_failure=inject_failure
                )
            except RestoreMaintenanceError as exc:
                self.stage_mutation["maintenance"] = int(
                    getattr(exc, "mutation_observed", 0) == 1
                )
                raise BackupRestoreError(exc.code) from exc

    def _core_running_count(self) -> int:
        completed = self._compose(
            "ps",
            "--status",
            "running",
            "--format",
            "{{.ID}}",
            "postgres",
            "minio",
            timeout=30,
        )
        if completed.returncode != 0:
            _fail("CORE_PS_FAILED")
        return len(
            [
                line
                for line in completed.stdout.decode("ascii", "replace").splitlines()
                if line.strip()
            ]
        )

    def restart_core(self, *, inject_failure: bool = False) -> None:
        before = self._core_running_count()
        stopped = self._compose("stop", "postgres", "minio", timeout=60)
        if stopped.returncode != 0:
            _fail("RESTART_STOP_FAILED")
        after_stop = self._core_running_count()
        restart_observed = observe_stage_mutation(before, after_stop)
        if inject_failure:
            self.stage_mutation["restart"] = restart_observed
            if restart_observed != 1:
                _fail("RESTART_MUTATION_NOT_OBSERVED")
            try:
                _fail("RESTART_INJECTED_FAILURE")
            finally:
                started = self._compose("start", "postgres", "minio", timeout=60)
                if started.returncode != 0:
                    _fail("RESTART_START_FAILED")
                wait = self._compose(
                    "up",
                    "-d",
                    "--wait",
                    "--wait-timeout",
                    "90",
                    "postgres",
                    "minio",
                    timeout=120,
                )
                if wait.returncode != 0:
                    _fail("RESTART_WAIT_FAILED")
                self._wait_postgres()
                self._wait_minio()
                self.apply_env()
            return
        started = self._compose(
            "start", "postgres", "minio", timeout=60
        )
        if started.returncode != 0:
            _fail("RESTART_START_FAILED")
        wait = self._compose(
            "up", "-d", "--wait", "--wait-timeout", "90", "postgres", "minio", timeout=120
        )
        if wait.returncode != 0:
            _fail("RESTART_WAIT_FAILED")
        self._wait_postgres()
        self._wait_minio()
        self.apply_env()

    def dispose_runtime(self) -> None:
        PostgresIntegrationStack.dispose_runtime(self)

    def revoke_release(self, task_id: uuid.UUID) -> int:
        return PostgresIntegrationStack.revoke_release(self, task_id)

    def expire_running_lease(self, job_id: uuid.UUID) -> int:
        return PostgresIntegrationStack.expire_running_lease(self, job_id)

    def job_ids(self) -> set[uuid.UUID]:
        with self._bootstrap() as connection:
            rows = connection.execute("SELECT id FROM f1.material_rag_job").fetchall()
        return {row[0] for row in rows}

    def job_status_summary(self) -> dict[str, int]:
        with self._bootstrap() as connection:
            rows = connection.execute(
                "SELECT "
                "count(*) FILTER (WHERE status='queued'), "
                "count(*) FILTER (WHERE status='retry_wait'), "
                "count(*) FILTER (WHERE status='done'), "
                "count(*) FILTER (WHERE status='failed'), "
                "count(*) FILTER ("
                "WHERE status='running' AND lease_until > statement_timestamp()"
                "), "
                "count(*) FILTER ("
                "WHERE status='running' AND lease_until <= statement_timestamp()"
                ") FROM f1.material_rag_job"
            ).fetchone()
        if rows is None:
            return {
                "queued": 0,
                "retry_wait": 0,
                "done": 0,
                "failed": 0,
                "running_live": 0,
                "running_expired": 0,
            }
        return {
            "queued": int(rows[0]),
            "retry_wait": int(rows[1]),
            "done": int(rows[2]),
            "failed": int(rows[3]),
            "running_live": int(rows[4]),
            "running_expired": int(rows[5]),
        }

    def _run_async(self, coro):
        import asyncio

        return asyncio.run(coro)

    def _units_for(self, claim, body: str):
        from platform_foundation.f1.features.material_rag.security import canonical_unit

        return (
            canonical_unit(
                enterprise_id=claim.enterprise_id,
                knowledge_scope_id=claim.knowledge_scope_id,
                document_record_id=claim.document_record_id,
                document_version_id=claim.document_version_id,
                source_sha256=claim.source_sha256,
                page_number=1,
                ordinal=1,
                parser_version="pgint1",
                text=body,
            ),
        )

    def _manifest_proof(self, claim, units):
        from platform_foundation.f1.features.material_rag.security import (
            create_synthetic_unit_manifest_proof,
        )

        return create_synthetic_unit_manifest_proof(claim=claim, units=units)

    def process_claim(self, claim, body: str) -> object:
        from platform_foundation.f1.features.material_rag.worker import (
            process_claimed_demo_job,
        )

        units = self._units_for(claim, body)
        proof = self._manifest_proof(claim, units)
        return self._run_async(
            process_claimed_demo_job(claim, units=units, manifest_proof=proof)
        )

    def enqueue_claim(
        self, tenant, version_id, action: str, key: str, worker_id: str, lease_seconds=300
    ):
        from platform_foundation.f1.features.material_rag.repository import (
            claim_job,
            enqueue_job,
        )

        job_id = self._run_async(
            enqueue_job(
                tenant,
                document_version_id=version_id,
                action=action,
                idempotency_key=key,
            )
        )
        claim = self._run_async(
            claim_job(job_id, worker_id=worker_id, lease_seconds=lease_seconds)
        )
        return job_id, claim

    def seed_six_job_classes(self) -> None:
        if self.world is None:
            _fail("WORLD_MISSING")
        doc = self.world.docs["recovery"]
        tenant = self.world.tenant_b
        body = self.world.body_for(doc.source_sha256)
        _, done_claim = self.enqueue_claim(
            tenant, doc.version_id, "index", "br-done", "worker-br-done"
        )
        done_outcome = self.process_claim(done_claim, body)
        if not done_outcome:
            kind = getattr(done_outcome, "kind", type(done_outcome).__name__)
            with self._bootstrap() as connection:
                row = connection.execute(
                    "SELECT status, error_reason FROM f1.material_rag_job WHERE id=%s",
                    (done_claim.id,),
                ).fetchone()
            status = "none" if row is None else str(row[0])
            reason = "none" if row is None or row[1] is None else str(row[1])
            if len(reason) > 80:
                reason = "REDACTED"
            _fail(f"DONE_JOB_PROCESS_FAILED:{kind}:{status}:{reason}")
        from platform_foundation.f1.features.material_rag.repository import enqueue_job

        queued = self._run_async(
            enqueue_job(
                tenant,
                document_version_id=doc.version_id,
                action="index",
                idempotency_key="br-queued",
            )
        )
        if queued is None:
            _fail("QUEUED_JOB_MISSING")
        _, retry_claim = self.enqueue_claim(
            tenant, doc.version_id, "index", "br-retry", "worker-br-retry"
        )
        self.rag_fake.fail_next = "connection"
        retry_outcome = self.process_claim(retry_claim, body)
        self.rag_fake.fail_next = None
        if getattr(retry_outcome, "kind", None) != "FINISH_TRUE":
            _fail("RETRY_JOB_PROCESS_FAILED")
        self.enqueue_claim(
            tenant, doc.version_id, "index", "br-live", "worker-br-live", lease_seconds=300
        )
        _, expired_claim = self.enqueue_claim(
            tenant,
            doc.version_id,
            "index",
            "br-expired",
            "worker-br-expired",
            lease_seconds=30,
        )
        if self.expire_running_lease(expired_claim.id) != 1:
            _fail("LEASE_EXPIRE_FAILED")
        revoke_doc = self.world.docs["revoke"]
        _, failed_claim = self.enqueue_claim(
            tenant,
            revoke_doc.version_id,
            "index",
            "br-failed",
            "worker-br-failed",
        )
        if self.revoke_release(revoke_doc.task_id) != 1:
            _fail("REVOKE_FAILED")
        failed_outcome = self.process_claim(
            failed_claim, self.world.body_for(revoke_doc.source_sha256)
        )
        if getattr(failed_outcome, "kind", None) != "FINISH_TRUE":
            _fail("FAILED_JOB_PROCESS_FAILED")
        summary = self.job_status_summary()
        for key in (
            "queued",
            "retry_wait",
            "running_live",
            "running_expired",
            "done",
            "failed",
        ):
            if summary[key] < 1:
                _fail("SIX_JOB_CLASSES_INCOMPLETE")

    def released_current_versions(self) -> list[tuple[object, uuid.UUID, str, str]]:
        if self.world is None:
            _fail("WORLD_MISSING")
        with self._bootstrap() as connection:
            rows = connection.execute(
                "SELECT version.id, version.enterprise_id, task.object_key, "
                "task.content_sha256 FROM f1.document_version AS version "
                "JOIN f1.document_record AS record ON record.id=version.document_record_id "
                "AND record.enterprise_id=version.enterprise_id "
                "JOIN f1.upload_task AS task ON task.id=version.upload_task_id "
                "AND task.enterprise_id=version.enterprise_id "
                "WHERE version.version_no=record.latest_version_no "
                "AND task.object_state='ready' AND task.scan_verdict='clean' "
                "AND task.preview_status='ready' AND task.processing_stage='ready' "
                "AND task.quarantine_status='released' AND task.released_at IS NOT NULL "
                "ORDER BY version.id"
            ).fetchall()
        tenants = {
            self.world.tenant_a.enterprise_id: self.world.tenant_a,
            self.world.tenant_b.enterprise_id: self.world.tenant_b,
        }
        result = []
        for version_id, enterprise_id, object_key, content_sha in rows:
            tenant = tenants.get(enterprise_id)
            if tenant is None:
                _fail("TENANT_MISSING")
            result.append((tenant, version_id, str(object_key), str(content_sha)))
        return result

    def rebuild_from_minio(
        self, old_job_ids: set[uuid.UUID], *, inject_failure: bool = False
    ) -> dict[str, int]:
        from platform_foundation.f1.features.material_rag.repository import (
            enqueue_job,
        )

        client = self._minio_client()
        rebuilt = 0
        reused = 0
        new_ids: set[uuid.UUID] = set()
        for tenant, version_id, object_key, content_sha in self.released_current_versions():
            try:
                response = client.get_object(DOCUMENTS_BUCKET, object_key)
                try:
                    payload = response.read()
                finally:
                    response.close()
                    response.release_conn()
            except Exception:
                _fail("RESTORED_OBJECT_MISSING")
            digest = hashlib.sha256(payload).hexdigest()
            if digest != content_sha:
                _fail("RESTORED_OBJECT_SHA_MISMATCH")
            body = payload.decode("utf-8")
            key = f"br-rebuild-{uuid.uuid4().hex}"
            before_ids = self.job_ids()
            job_id = self._run_async(
                enqueue_job(
                    tenant,
                    document_version_id=version_id,
                    action="rebuild",
                    idempotency_key=key,
                )
            )
            self.rebuild_started = 1
            if inject_failure:
                after_ids = self.job_ids()
                rebuild_observed = observe_stage_mutation(before_ids, after_ids)
                self.stage_mutation["rebuild"] = rebuild_observed
                if rebuild_observed != 1:
                    _fail("REBUILD_MUTATION_NOT_OBSERVED")
                try:
                    _fail("REBUILD_INJECTED_FAILURE")
                finally:
                    self._recover_rebuild_injection(before_ids)
            if job_id in old_job_ids:
                reused += 1
            new_ids.add(job_id)
            from platform_foundation.f1.features.material_rag.repository import claim_job

            claim = self._run_async(
                claim_job(job_id, worker_id=f"worker-rebuild-{rebuilt}")
            )
            if claim is None:
                _fail("REBUILD_CLAIM_FAILED")
            outcome = self.process_claim(claim, body)
            if not outcome:
                _fail("REBUILD_PROCESS_FAILED")
            rebuilt += 1
        return {
            "old_job_reuse": reused,
            "rebuilt": rebuilt,
            "unique_new_jobs": len(new_ids),
        }

    def _recover_rebuild_injection(self, baseline_ids: set[uuid.UUID]) -> None:
        extras = self.job_ids() - set(baseline_ids)
        if extras:
            with self._bootstrap() as connection:
                if not baseline_ids:
                    connection.execute("DELETE FROM f1.material_rag_job")
                else:
                    connection.execute(
                        "DELETE FROM f1.material_rag_job WHERE NOT (id = ANY(%s::uuid[]))",
                        (list(baseline_ids),),
                    )
                connection.commit()
        if self.job_ids() != set(baseline_ids):
            _fail("REBUILD_INJECTION_RESIDUAL")

    def isolation_probes(self) -> dict[str, int]:
        from platform_foundation.f1.features.material_rag.contracts import (
            MaterialRagIntegrityError,
        )
        from platform_foundation.f1.features.material_rag.repository import enqueue_job

        if self.world is None:
            _fail("WORLD_MISSING")

        def attempt(tenant, version_id, key: str) -> int:
            try:
                job_id = self._run_async(
                    enqueue_job(
                        tenant,
                        document_version_id=version_id,
                        action="rebuild",
                        idempotency_key=key,
                    )
                )
            except MaterialRagIntegrityError:
                return 0
            return 0 if job_id is None else 1

        enqueue = {
            "cross_tenant_enqueued": attempt(
                self.world.tenant_a,
                self.world.docs["recovery"].version_id,
                "br-cross-tenant",
            ),
            "revoked_enqueued": attempt(
                self.world.tenant_b,
                self.world.docs["revoke"].version_id,
                "br-revoked",
            ),
            "unreleased_enqueued": attempt(
                self.world.tenant_a,
                self.world.docs["unreleased"].version_id,
                "br-unreleased",
            ),
        }
        visibility = self.service_visibility_probes()
        return {**enqueue, **visibility}

    def _load_unit_rows(self) -> list[dict[str, Any]]:
        with self._bootstrap() as connection:
            rows = connection.execute(
                "SELECT unit.id, unit.enterprise_id, unit.knowledge_scope_id, "
                "unit.document_record_id, unit.document_version_id, "
                "unit.source_sha256, unit.page_number, unit.body_sha256, "
                "scope.scope_kind FROM f1.material_rag_unit AS unit "
                "JOIN f1.material_knowledge_scope AS scope "
                "ON scope.enterprise_id=unit.enterprise_id "
                "AND scope.id=unit.knowledge_scope_id"
            ).fetchall()
        records = []
        for row in rows:
            records.append(
                {
                    "body_sha256": str(row[7]),
                    "canonical_unit_id": row[0],
                    "document_record_id": row[3],
                    "document_version_id": row[4],
                    "enterprise_id": row[1],
                    "knowledge_scope_id": row[2],
                    "page_number": int(row[6]),
                    "scope_kind": str(row[8]),
                    "source_sha256": str(row[5]),
                }
            )
        return records

    def service_visibility_probes(self) -> dict[str, int]:
        from platform_foundation.f1.features.material_rag.ragflow_adapter import (
            RemoteCandidate,
        )
        from platform_foundation.f1.features.material_rag.security import (
            CLIENT_B_RETRIEVAL_QUERY_TEXT,
            PROVIDER_RETRIEVAL_QUERY_TEXT,
        )
        from platform_foundation.f1.features.material_rag.service import (
            MaterialRetrievalService,
            PostgresMaterialRagRepository,
        )

        if self.world is None:
            _fail("WORLD_MISSING")
        units = self._load_unit_rows()
        if not units:
            _fail("UNITS_MISSING")

        class InjectedTransport:
            def __init__(self) -> None:
                self.candidates: tuple[object, ...] = ()

            async def retrieve_candidates(self, query, datasets, limit):
                del query, datasets, limit
                return self.candidates

        transport = InjectedTransport()
        transport.candidates = tuple(
            RemoteCandidate(
                canonical_unit_id=item["canonical_unit_id"],
                knowledge_scope_id=item["knowledge_scope_id"],
                document_record_id=item["document_record_id"],
                document_version_id=item["document_version_id"],
                source_sha256=item["source_sha256"],
                page_number=item["page_number"],
                body_sha256=item["body_sha256"],
            )
            for item in units
        )
        service = MaterialRetrievalService(PostgresMaterialRagRepository(), transport)
        client_b_id = uuid.uuid5(FIXTURE_NS, "br-client-b")
        context_a = self._run_async(
            service.derive_retrieval_context(self.world.tenant_a, None)
        )
        result_a = self._run_async(
            service.retrieve_registered(
                PROVIDER_RETRIEVAL_QUERY_TEXT, self.world.tenant_a, context_a
            )
        )
        context_b = self._run_async(
            service.derive_retrieval_context(self.world.tenant_b, client_b_id)
        )
        result_b = self._run_async(
            service.retrieve_registered(
                CLIENT_B_RETRIEVAL_QUERY_TEXT, self.world.tenant_b, context_b
            )
        )
        if not result_a.evidence or not result_b.evidence:
            _fail("ISOLATION_RETRIEVAL_EMPTY")

        def visibility(result, tenant, context) -> tuple[int, int]:
            allowed = set(context._scope_ids)
            by_id = {item["canonical_unit_id"]: item for item in units}
            cross_tenant = 0
            cross_scope = 0
            for evidence in result.evidence:
                item = by_id.get(evidence.canonical_unit_id)
                if item is None:
                    cross_scope += 1
                    continue
                if item["enterprise_id"] != tenant.enterprise_id:
                    cross_tenant += 1
                if item["knowledge_scope_id"] not in allowed:
                    cross_scope += 1
                elif (
                    context.kind == "service_provider"
                    and item["scope_kind"] != "service_provider"
                ):
                    cross_scope += 1
            return cross_tenant, cross_scope

        tenant_a_cross, tenant_a_scope = visibility(
            result_a, self.world.tenant_a, context_a
        )
        tenant_b_cross, tenant_b_scope = visibility(
            result_b, self.world.tenant_b, context_b
        )
        return {
            "cross_scope_visible": tenant_a_scope + tenant_b_scope,
            "cross_tenant_visible": tenant_a_cross + tenant_b_cross,
        }

    def prove_cleanup_label_rejection(self) -> int:
        inspects = self.volume_inspects()
        postgres, minio = selectable_data_volumes(
            inspects,
            scope=SCOPE,
            project_id=self.project_id,
            parent_project_id=self.parent_project_id,
        )
        labels = {
            "io.anhuan.scope": SCOPE,
            "io.anhuan.project-id": self.project_id,
            "io.anhuan.parent-project-id": self.parent_project_id,
        }
        destroyer = FakeDestroyer()
        for field, value in (
            ("io.anhuan.scope", "other-scope"),
            ("io.anhuan.project-id", "c" * 32),
            ("io.anhuan.parent-project-id", "d" * 32),
        ):
            bad = dict(labels)
            bad[field] = value
            try:
                destroy_labeled_resources(
                    [
                        {"Name": postgres, "Labels": bad},
                        {"Name": minio, "Labels": dict(labels)},
                    ],
                    names=(postgres, minio),
                    scope=SCOPE,
                    project_id=self.project_id,
                    parent_project_id=self.parent_project_id,
                    destroyer=destroyer,
                )
            except BackupRestoreError as exc:
                if exc.code != "RESOURCE_LABEL_MISMATCH":
                    raise
                if destroyer.destructive_started != 0 or destroyer.destroyed:
                    _fail("CLEANUP_LABEL_DELETED")
            else:
                _fail("CLEANUP_LABEL_FALSE_GREEN")
        return 1

    def _label_filters(self) -> list[str]:
        return [
            f"label=io.anhuan.scope={SCOPE}",
            f"label=io.anhuan.project-id={self.project_id}",
            f"label=io.anhuan.parent-project-id={self.parent_project_id}",
        ]

    def _inspect_resource_labels(self, kind: str, resource_id: str) -> dict[str, Any]:
        if kind == "container":
            payload = json.loads(
                subprocess.check_output(
                    [str(DOCKER), "inspect", resource_id], text=True
                )
            )[0]
            return payload.get("Config", {}).get("Labels") or {}
        command = "volume" if kind == "volume" else "network"
        payload = json.loads(
            subprocess.check_output(
                [str(DOCKER), command, "inspect", resource_id], text=True
            )
        )[0]
        return payload.get("Labels") or {}

    def _destroy_labeled_leftovers(self, kind: str, destroy_args: list[str]) -> None:
        listed = subprocess.check_output(
            [str(DOCKER), *{
                "container": ["ps", "-aq"],
                "volume": ["volume", "ls", "-q"],
                "network": ["network", "ls", "-q"],
            }[kind], *[item for label in self._label_filters() for item in ("--filter", label)]],
            text=True,
        ).strip()
        identifiers = [line for line in listed.splitlines() if line]
        if not identifiers:
            return
        for resource_id in identifiers:
            require_three_labels(
                self._inspect_resource_labels(kind, resource_id),
                SCOPE,
                self.project_id,
                self.parent_project_id,
            )
        subprocess.run(
            [str(DOCKER), *destroy_args, *identifiers],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def run_fresh_process_probe(self) -> dict[str, int]:
        self.uninstall_fakes()
        self.rag_fake = DeterministicRagFlow()
        self.dispose_runtime()
        completed = _run(
            [PYTHON, "-B", str(POST_RESTART_PROBE)],
            environment=self.runtime_env(),
            timeout=120,
        )
        if completed.returncode != 0:
            _fail("POST_RESTART_PROBE_FAILED")
        raw = completed.stdout.decode("ascii", "replace").strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BackupRestoreError("POST_RESTART_PROBE_JSON") from exc
        if not isinstance(payload, dict):
            _fail("POST_RESTART_PROBE_JSON")
        if (
            payload.get("fresh_process") != 1
            or payload.get("minio_reconnect_ok") != 1
            or payload.get("retrieval_a_ok") != 1
            or payload.get("retrieval_b_ok") != 1
            or payload.get("cross_tenant_visible") != 0
            or payload.get("cross_scope_visible") != 0
        ):
            _fail("POST_RESTART_PROBE_INVALID")
        return {
            "cross_scope_visible": int(payload["cross_scope_visible"]),
            "cross_tenant_visible": int(payload["cross_tenant_visible"]),
            "post_restart_fresh_process": 1,
            "post_restart_retrieval_ok": 1,
        }

    def stop(self) -> None:
        compose_env = self.control_dir / "compose.env"
        if compose_env.exists():
            self._compose(
                "down",
                "--volumes",
                "--remove-orphans",
                "--timeout",
                "20",
                timeout=90,
            )
        self._destroy_labeled_leftovers("container", ["rm", "-f"])
        self._destroy_labeled_leftovers("volume", ["volume", "rm", "-f"])
        self._destroy_labeled_leftovers("network", ["network", "rm"])
        self.uninstall_fakes()
        if self.control_dir.exists():
            shutil.rmtree(self.control_dir)
        self.started = False
        self.dedicated_after = dedicated_counts()
        after = canonical_shared_fingerprint()
        self.shared_match = int(after == self.before_fingerprint)
        self.cleanup_status = (
            "CLEAN"
            if self.dedicated_after == (0, 0, 0)
            and self.shared_match == 1
            and not self.control_dir.exists()
            else "RESIDUAL"
        )


def _copy_package(source: Path, destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, symlinks=False)
    os.chmod(destination, 0o700)
    for path in [destination, *destination.rglob("*")]:
        if path.is_dir():
            os.chmod(path, 0o700)
        elif path.is_file():
            os.chmod(path, 0o600)
    return destination


def _rewrite_manifest(root: Path, updater: Any) -> None:
    raw = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    updater(raw)
    (root / MANIFEST_NAME).write_text(
        json.dumps(
            raw,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(root / MANIFEST_NAME, 0o600        )


class DockerIdentityDestroyer:
    def __init__(self, stack: BackupRestoreStack) -> None:
        self._stack = stack
        self.destructive_started = 0
        self.destroyed: tuple[tuple[str, str], ...] = ()
        self.rebuild_started = 0

    def inspect(self, kind: str, resource_id: str) -> dict[str, Any] | None:
        for item in self._stack.capture_core_identities():
            if item["kind"] == kind and item["id"] == resource_id:
                return item
        return None

    def list_project(
        self, scope: str, project_id: str, parent_project_id: str
    ) -> list[dict[str, Any]]:
        del scope, project_id, parent_project_id
        return self._stack.capture_core_identities()

    def list_labeled(
        self, scope: str, project_id: str, parent_project_id: str
    ) -> list[dict[str, Any]]:
        return self.list_project(scope, project_id, parent_project_id)

    def destroy(self, targets: tuple[tuple[str, str], ...]) -> None:
        self.destroyed = targets
        ordered = tuple(
            item for item in targets if item[0] == "container"
        ) + tuple(item for item in targets if item[0] == "volume")
        for kind, resource_id in ordered:
            current = self.inspect(kind, resource_id)
            if current is None:
                continue
            handle = str(current.get("handle") or resource_id)
            if kind == "container":
                command = [str(DOCKER), "rm", "-f", handle]
            elif kind == "volume":
                command = [str(DOCKER), "volume", "rm", "-f", handle]
            else:
                continue
            subprocess.run(
                command,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


class FakeDestroyer:
    def __init__(self) -> None:
        self.destructive_started = 0
        self.destroyed: tuple[str, ...] = ()
        self.db_canary = 12
        self.minio_tree = "b" * 64
        self.resource_ids = ("vol-pg-a", "vol-minio-a")

    def snapshot(self) -> tuple[object, ...]:
        return (
            self.destructive_started,
            self.destroyed,
            self.db_canary,
            self.minio_tree,
            self.resource_ids,
        )

    def destroy(self, names: tuple[str, ...]) -> None:
        self.destroyed = tuple(names)
        self.db_canary = 0
        self.minio_tree = "c" * 64
        self.resource_ids = ("vol-pg-b", "vol-minio-b")


def _canonical_check_payload(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"


def run_machine_gate() -> dict[str, Any]:
    stack = BackupRestoreStack()
    front_door_failures = 0
    front_door_repair_ok = 0
    rebuild = {"old_job_reuse": 0, "rebuilt": 0, "unique_new_jobs": 0}
    isolation = {
        "cross_tenant_enqueued": 0,
        "revoked_enqueued": 0,
        "unreleased_enqueued": 0,
    }
    maintenance_after = {
        "job": -1,
        "unit": -1,
        "live_lease": -1,
        "provisioning": -1,
        "deleted_secret": -1,
        "orphan": -1,
    }
    fail_cleanup_ok = 0
    restart_ok = 0
    restore_ok = 0
    try:
        stack.install_fakes()
        stack.start()
        stack.seed_backup_world()
        stack.put_source_objects()
        stack.seed_six_job_classes()
        old_jobs = stack.job_ids()
        db_canary = stack.document_count()
        minio_fp = stack.minio_inventory_fingerprint()
        volume_fp = stack.data_resource_fingerprint()
        package = stack.create_package()
        expected = {
            "expected_project_id": stack.project_id,
            "expected_parent_project_id": stack.parent_project_id,
            "expected_database": stack.database,
            "expected_scope": SCOPE,
        }
        inspects = stack.volume_inspects()

        def assert_canary() -> None:
            if stack.destructive_started != 0:
                _fail("DESTRUCTIVE_STARTED")
            if stack.document_count() != db_canary:
                _fail("DB_CANARY_CHANGED")
            if stack.minio_inventory_fingerprint() != minio_fp:
                _fail("MINIO_CANARY_CHANGED")
            if stack.data_resource_fingerprint() != volume_fp:
                _fail("RESOURCE_ID_CHANGED")

        def _tamper_minio(root: Path) -> None:
            extra = root / MINIO_DIRECTORY_NAME / "tamper.bin"
            extra.write_bytes(b"tamper")
            extra.chmod(0o600)

        tampers: list[tuple[str, Any, str]] = [
            (
                "dump",
                lambda root: (root / DATABASE_DUMP_NAME).write_bytes(
                    (root / DATABASE_DUMP_NAME).read_bytes() + b"\x00"
                ),
                "DATABASE_DUMP_MISMATCH",
            ),
            (
                "minio",
                _tamper_minio,
                "MINIO_TREE_MISMATCH",
            ),
            (
                "head",
                lambda root: _rewrite_manifest(
                    root, lambda doc: doc.__setitem__("f1_head", "f1_0014")
                ),
                "F1_HEAD_MISMATCH",
            ),
            (
                "tables",
                lambda root: _rewrite_manifest(
                    root, lambda doc: doc.__setitem__("business_table_count", 35)
                ),
                "BUSINESS_SNAPSHOT_VALUE_INVALID",
            ),
            (
                "labels",
                lambda root: _rewrite_manifest(
                    root,
                    lambda doc: doc.__setitem__(
                        "parent_project_id", "tampered-parent-id-0001"
                    ),
                ),
                "MANIFEST_PARENT_PROJECT_ID_MISMATCH",
            ),
        ]
        for name, mutator, code in tampers:
            copy = _copy_package(package, stack.package_dir / name)
            mutator(copy)
            for path in copy.rglob("*"):
                if path.is_dir():
                    os.chmod(path, 0o700)
                elif path.is_file():
                    os.chmod(path, 0o600)
            try:
                plan_restore(copy, volume_inspects=inspects, **expected)
            except BackupRestoreError as exc:
                if exc.code != code:
                    raise BackupRestoreError(f"FRONT_DOOR_CODE:{exc.code}") from exc
                front_door_failures += 1
            else:
                _fail("FRONT_DOOR_FALSE_GREEN")
            assert_canary()

        repaired = _copy_package(package, stack.package_dir / "repaired")
        plan_restore(repaired, volume_inspects=inspects, **expected)
        assert_canary()
        front_door_repair_ok = 1

        def capture_injected(code: str, action, stage: str) -> tuple[int, int]:
            dedicated_before = dedicated_counts()
            fingerprint = canonical_shared_fingerprint()
            try:
                action()
            except BackupRestoreError as exc:
                if exc.code != code:
                    raise
            else:
                _fail("INJECTED_FAILURE_FALSE_GREEN")
            shared_changed = canonical_shared_fingerprint() != fingerprint
            observed = int(stack.stage_mutation.get(stage, 0) == 1)
            if shared_changed:
                _fail("SHARED_FINGERPRINT_CHANGED")
            if observed != 1:
                _fail("MUTATION_NOT_OBSERVED")
            if stage == "restore":
                metrics = stack.restore_abort_metrics
                if (
                    metrics.get("deleted_count")
                    != metrics.get("new_volume_count", -1)
                    + metrics.get("new_container_count", -1)
                ):
                    _fail("RESTORE_ABORT_COUNT_MISMATCH")
                if metrics.get("remaining_abort_id_count") != 0:
                    _fail("RESTORE_ABORT_ID_REMAINS")
                if metrics.get("package_reverified") != 1:
                    _fail("RESTORE_ABORT_PACKAGE_LOST")
                if metrics.get("journal_stage_recovered") != 1:
                    _fail("JOURNAL_STAGE_INVALID")
                if metrics.get("rebuild_started") != 0:
                    _fail("JOURNAL_REBUILD_STARTED")
                leftover = [
                    item
                    for item in stack.capture_core_identities()
                    if item["id"] in stack.restore_abort_new_ids
                    and item["kind"] in ABORT_DELETE_KINDS
                ]
                if leftover:
                    _fail("RESTORE_ABORT_ID_REMAINS")
                cleanup = int(
                    metrics.get("same_count_swap_observed") == 1
                    and metrics.get("new_volume_count") == 2
                    and metrics.get("new_container_count") == 3
                    and metrics.get("deleted_count") == 5
                    and metrics.get("remaining_abort_id_count") == 0
                    and metrics.get("package_reverified") == 1
                    and metrics.get("rebuild_started") == 0
                    and metrics.get("journal_stage_recovered") == 1
                )
                if cleanup != 1:
                    _fail("INJECTED_FAILURE_NOT_CLEANED")
            else:
                leaked = dedicated_counts() != dedicated_before
                if leaked:
                    _fail("INJECTED_FAILURE_LEAK")
                cleanup = int(not leaked)
                if cleanup != 1:
                    _fail("INJECTED_FAILURE_NOT_CLEANED")
            return observed, cleanup

        restore_mutation_observed, restore_failure_cleanup = capture_injected(
            "RESTORE_INJECTED_FAILURE",
            lambda: stack.restore_package(package, inject_failure=True),
            "restore",
        )
        stack.restore_package(package)
        abort_metrics = dict(stack.restore_abort_metrics)
        abort_metrics["retry_abort_id_reuse_count"] = stack.retry_abort_id_reuse_count
        stack.prove_same_size_body_mismatch_is_red(package)
        minio_live_tree_match = 1
        restore_ok = 1
        cleanup_label_rejection = stack.prove_cleanup_label_rejection()
        maintenance_mutation_observed, maintenance_failure_cleanup = capture_injected(
            "MAINTENANCE_INJECTED_FAILURE",
            lambda: stack.run_maintenance(inject_failure=True),
            "maintenance",
        )
        maintained = stack.run_maintenance()
        maintenance_after = maintained["after"]
        rebuild_mutation_observed, rebuild_failure_cleanup = capture_injected(
            "REBUILD_INJECTED_FAILURE",
            lambda: stack.rebuild_from_minio(old_jobs, inject_failure=True),
            "rebuild",
        )
        rebuild = stack.rebuild_from_minio(old_jobs)
        isolation = stack.isolation_probes()
        head = stack.alembic_head()
        snap = stack.snapshot_38()
        minio_after = stack.canonical_live_object_tree()
        restart_mutation_observed, restart_failure_cleanup = capture_injected(
            "RESTART_INJECTED_FAILURE",
            lambda: stack.restart_core(inject_failure=True),
            "restart",
        )
        stack.restart_core()
        if (
            stack.alembic_head() != head
            or stack.snapshot_38() != snap
        ):
            _fail("RESTART_STATE_MISMATCH")
        compare_object_trees(minio_after, stack.canonical_live_object_tree())
        probe = stack.run_fresh_process_probe()
        post_restart_fresh_process = probe["post_restart_fresh_process"]
        post_restart_retrieval_ok = probe["post_restart_retrieval_ok"]
        if (
            probe["cross_tenant_visible"] != 0
            or probe["cross_scope_visible"] != 0
            or isolation.get("cross_tenant_visible", 1) != 0
            or isolation.get("cross_scope_visible", 1) != 0
        ):
            _fail("ISOLATION_VISIBLE")
        restart_ok = int(
            post_restart_fresh_process == 1 and post_restart_retrieval_ok == 1
        )
        injected_rebuild = stack.rebuild_started
        try:
            stack.restore_package(package, fail_before_minio=True)
        except BackupRestoreError as exc:
            if exc.code != "MINIO_RESTORE_INJECTED_FAILURE":
                raise
            if stack.rebuild_started != injected_rebuild:
                _fail("INJECTED_REBUILD_STARTED")
        else:
            _fail("INJECTED_FAILURE_FALSE_GREEN")
    finally:
        try:
            stack.dispose_runtime()
        finally:
            stack.stop()
    if stack.cleanup_status != "CLEAN":
        _fail("CLEANUP_RESIDUAL")
    fail_cleanup_ok = 1
    payload = {
        "schema": CHECK_SCHEMA,
        "f1_head": F1_HEAD,
        "business_table_count": 38,
        "db_dump_size_positive": 1,
        "minio_file_count": minio_fp[0],
        "minio_live_tree_match": minio_live_tree_match,
        "front_door_tamper_failures": front_door_failures,
        "front_door_repair_ok": front_door_repair_ok,
        "destructive_started": 1,
        "restore_ok": restore_ok,
        "maintenance_job": maintenance_after["job"],
        "maintenance_unit": maintenance_after["unit"],
        "maintenance_live_lease": maintenance_after["live_lease"],
        "maintenance_provisioning": maintenance_after["provisioning"],
        "maintenance_deleted_secret": maintenance_after["deleted_secret"],
        "maintenance_orphan": maintenance_after["orphan"],
        "rebuild_ok": int(
            rebuild["rebuilt"] >= 3 and rebuild["old_job_reuse"] == 0
        ),
        "rebuild_old_job_reuse": rebuild["old_job_reuse"],
        "unreleased_enqueued": isolation["unreleased_enqueued"],
        "revoked_enqueued": isolation["revoked_enqueued"],
        "cross_tenant_enqueued": isolation["cross_tenant_enqueued"],
        "cross_tenant_visible": isolation["cross_tenant_visible"],
        "cross_scope_visible": isolation["cross_scope_visible"],
        "post_restart_fresh_process": post_restart_fresh_process,
        "post_restart_retrieval_ok": post_restart_retrieval_ok,
        "restart_ok": restart_ok,
        "cleanup_label_rejection": cleanup_label_rejection,
        "restore_failure_cleanup": restore_failure_cleanup,
        "maintenance_failure_cleanup": maintenance_failure_cleanup,
        "rebuild_failure_cleanup": rebuild_failure_cleanup,
        "restart_failure_cleanup": restart_failure_cleanup,
        "restore_mutation_observed": restore_mutation_observed,
        "maintenance_mutation_observed": maintenance_mutation_observed,
        "rebuild_mutation_observed": rebuild_mutation_observed,
        "restart_mutation_observed": restart_mutation_observed,
        "fail_cleanup_ok": fail_cleanup_ok,
        "dedicated_c": stack.dedicated_after[0],
        "dedicated_v": stack.dedicated_after[1],
        "dedicated_n": stack.dedicated_after[2],
        "shared_fingerprint_match": stack.shared_match,
        "skipped": 0,
        "same_count_swap_observed": abort_metrics["same_count_swap_observed"],
        "new_volume_count": abort_metrics["new_volume_count"],
        "new_container_count": abort_metrics["new_container_count"],
        "deleted_count": abort_metrics["deleted_count"],
        "remaining_abort_id_count": abort_metrics["remaining_abort_id_count"],
        "package_reverified": abort_metrics["package_reverified"],
        "rebuild_started": abort_metrics["rebuild_started"],
        "retry_abort_id_reuse_count": abort_metrics["retry_abort_id_reuse_count"],
        "journal_stage_recovered": abort_metrics["journal_stage_recovered"],
    }
    validate_check_payload(payload)
    if (
        payload["front_door_tamper_failures"] != 5
        or payload["front_door_repair_ok"] != 1
        or payload["restore_ok"] != 1
        or payload["rebuild_ok"] != 1
        or payload["restart_ok"] != 1
        or payload["fail_cleanup_ok"] != 1
        or payload["minio_live_tree_match"] != 1
        or payload["post_restart_fresh_process"] != 1
        or payload["post_restart_retrieval_ok"] != 1
        or payload["cleanup_label_rejection"] != 1
        or payload["restore_failure_cleanup"] != 1
        or payload["maintenance_failure_cleanup"] != 1
        or payload["rebuild_failure_cleanup"] != 1
        or payload["restart_failure_cleanup"] != 1
        or payload["restore_mutation_observed"] != 1
        or payload["maintenance_mutation_observed"] != 1
        or payload["rebuild_mutation_observed"] != 1
        or payload["restart_mutation_observed"] != 1
        or payload["unreleased_enqueued"] != 0
        or payload["revoked_enqueued"] != 0
        or payload["cross_tenant_enqueued"] != 0
        or payload["cross_tenant_visible"] != 0
        or payload["cross_scope_visible"] != 0
        or payload["dedicated_c"] != 0
        or payload["dedicated_v"] != 0
        or payload["dedicated_n"] != 0
        or payload["shared_fingerprint_match"] != 1
        or payload["maintenance_job"] != 0
        or payload["maintenance_unit"] != 0
        or payload["maintenance_live_lease"] != 0
        or payload["maintenance_provisioning"] != 0
        or payload["maintenance_deleted_secret"] != 0
        or payload["maintenance_orphan"] != 0
        or payload["same_count_swap_observed"] != 1
        or payload["new_volume_count"] != 2
        or payload["new_container_count"] != 3
        or payload["deleted_count"] != 5
        or payload["remaining_abort_id_count"] != 0
        or payload["package_reverified"] != 1
        or payload["rebuild_started"] != 0
        or payload["retry_abort_id_reuse_count"] != 0
        or payload["journal_stage_recovered"] != 1
    ):
        _fail("GATE_AGGREGATE_INVALID")
    return payload


def _crash_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + str(ROOT)
    env.setdefault(
        "F1_KEYCLOAK_ISSUER_URL",
        "http://material-rag.invalid/realms/anhuan",
    )
    env.pop("MATERIAL_RAG_RESTORE_CRASH_AFTER", None)
    env.pop("MATERIAL_RAG_RESTORE_WAIT_AFTER", None)
    env.pop("MATERIAL_RAG_CRASH_READY", None)
    env.pop("MATERIAL_RAG_CRASH_RECEIPT", None)
    return env


def _precise_crash_cleanup(receipt_path: Path, before_fingerprint: bytes) -> None:
    if not receipt_path.exists():
        return
    receipt = _read_crash_receipt(receipt_path)
    stack = BackupRestoreStack.attach_from_receipt(receipt)
    stack.before_fingerprint = before_fingerprint
    journal_path = Path(receipt["journal_path"])
    package = Path(receipt["package_path"])
    if journal_path.exists() and not journal_path.is_symlink():
        document = read_journal(journal_path)
        if document["stage"] in RECOVERABLE_STAGES:
            live = stack.capture_core_identities()
            destroyer = DockerIdentityDestroyer(stack)
            manifest = json.loads(
                (package / "manifest.json").read_bytes().decode("ascii")
            )

            def package_check() -> None:
                verify_package(
                    package,
                    expected_project_id=str(receipt["project_id"]),
                    expected_parent_project_id=str(receipt["parent_project_id"]),
                    expected_database=str(manifest["database"]),
                    expected_scope=SCOPE,
                )

            recover_from_journal(
                journal_path,
                expected_scope=SCOPE,
                expected_project_id=str(receipt["project_id"]),
                expected_parent_project_id=str(receipt["parent_project_id"]),
                expected_dump_sha256=str(receipt["package_dump_sha256"]),
                expected_tree_sha256=str(receipt["package_tree_sha256"]),
                live=live,
                destroyer=destroyer,
                package_check=package_check,
            )
    stack.stop()


def run_crash_machine_gate() -> dict[str, Any]:
    if dedicated_counts() != (0, 0, 0):
        _fail("DEDICATED_PREEXISTING")
    before_fingerprint = canonical_shared_fingerprint()
    work = Path(f"/private/tmp/anhuan-mr-crash-{uuid.uuid4().hex[:12]}")
    work.mkdir(mode=0o700)
    ready = work / "ready.json"
    receipt_path = work / "receipt.json"
    child: subprocess.Popen[bytes] | None = None
    cleaned = 0
    try:
        env = _crash_subprocess_env()
        probe = str(ROOT / "infra/f1/material-rag/crash_recovery_probe.py")
        if "recover_from_journal" not in Path(probe).read_text(encoding="utf-8"):
            _fail("CRASH_PROBE_RECOVER_MISSING")
        child = subprocess.Popen(
            [
                PYTHON,
                "-B",
                probe,
                "child",
                "--ready",
                str(ready),
                "--receipt",
                str(receipt_path),
            ],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if child.poll() is not None:
                _fail("CRASH_CHILD_EXITED")
            if ready.exists() and receipt_path.exists():
                break
            time.sleep(0.1)
        else:
            _fail("CRASH_CHILD_READY_TIMEOUT")
        receipt = _read_crash_receipt(receipt_path)
        attached = BackupRestoreStack.attach_from_receipt(receipt)
        attached.before_fingerprint = before_fingerprint
        journal_path = Path(receipt["journal_path"])
        journal = read_journal(journal_path)
        if journal["stage"] != "DB_RESTORED":
            _fail("JOURNAL_STAGE_INVALID")
        live_before = attached.capture_core_identities()
        saved = [
            {"id": item["id"], "kind": item["kind"]} for item in journal["resources"]
        ]
        try:
            new_items = new_labeled_resources(
                saved,
                live_before,
                scope=SCOPE,
                project_id=str(receipt["project_id"]),
                parent_project_id=str(receipt["parent_project_id"]),
            )
        except RestoreRecoveryError as exc:
            raise BackupRestoreError(exc.code) from exc
        abort_new = [
            item for item in new_items if item["kind"] in ABORT_DELETE_KINDS
        ]
        new_volume = sum(1 for item in abort_new if item["kind"] == "volume")
        new_container = sum(1 for item in abort_new if item["kind"] == "container")
        if new_volume != 2 or new_container != 3:
            _fail("CRASH_NEW_RESOURCE_COUNT")
        package = Path(receipt["package_path"])
        manifest = json.loads((package / "manifest.json").read_bytes().decode("ascii"))
        expected = {
            "expected_project_id": str(receipt["project_id"]),
            "expected_parent_project_id": str(receipt["parent_project_id"]),
            "expected_database": str(manifest["database"]),
            "expected_scope": SCOPE,
        }
        verify_package(package, **expected)
        os.kill(child.pid, signal.SIGKILL)
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except OSError:
            pass
        child.wait(timeout=10)
        if child.returncode != -signal.SIGKILL:
            _fail("HARD_DEATH_NOT_SIGKILL")
        hard_death_signal = 9
        live_after_kill = attached.capture_core_identities()
        after_kill_ids = {
            (item["kind"], item["id"]) for item in live_after_kill
        }
        for item in abort_new:
            if (item["kind"], item["id"]) not in after_kill_ids:
                _fail("CRASH_FINALLY_RAN")
        verify_package(package, **expected)
        tamper = subprocess.Popen(
            [
                PYTHON,
                "-B",
                probe,
                "recover",
                "--receipt",
                str(receipt_path),
                "--tamper-labels",
            ],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        tamper_stdout, tamper_stderr = tamper.communicate(timeout=60)
        live_after_tamper = attached.capture_core_identities()
        tamper_ids = {(item["kind"], item["id"]) for item in live_after_tamper}
        remaining_after_tamper = sum(
            1 for item in abort_new if (item["kind"], item["id"]) in tamper_ids
        )
        evaluated = evaluate_tamper_probe(
            returncode=tamper.returncode,
            stdout=tamper_stdout or b"",
            stderr=tamper_stderr or b"",
            remaining_abort_ids=remaining_after_tamper,
        )
        tamper_rejected = evaluated["tamper_rejected"]
        tamper_reason_verified = evaluated["tamper_reason_verified"]
        tamper_zero_delete = evaluated["tamper_zero_delete"]
        recover = subprocess.Popen(
            [
                PYTHON,
                "-B",
                probe,
                "recover",
                "--receipt",
                str(receipt_path),
            ],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        recover.communicate(timeout=60)
        if recover.returncode != 0:
            _fail("CRASH_RECOVER_FAILED")
        if child.pid != recover.pid:
            fresh_recovery_process = 1
        else:
            _fail("CRASH_RECOVERY_PID_COLLISION")
        live_after = attached.capture_core_identities()
        remain_ids = {(item["kind"], item["id"]) for item in live_after}
        remaining = sum(
            1 for item in abort_new if (item["kind"], item["id"]) in remain_ids
        )
        if remaining != 0:
            _fail("RESTORE_ABORT_ID_REMAINS")
        deleted = new_volume + new_container
        recovered_doc = read_journal(journal_path)
        journal_recovered = int(recovered_doc["stage"] == "RECOVERED")
        if journal_recovered != 1:
            _fail("JOURNAL_STAGE_INVALID")
        verify_package(package, **expected)
        package_reverified = 1
        rebuild_started = 0
        attached.stop()
        first = dedicated_counts()
        first_at = time.monotonic()
        while time.monotonic() - first_at < 0.5:
            time.sleep(0.05)
        second = dedicated_counts()
        second_at = time.monotonic()
        leftover = first if first != (0, 0, 0) else second
        fallback_cleanup_used = apply_post_stop_fallback(
            leftover,
            (
                lambda: attached._destroy_labeled_leftovers("container", ["rm", "-f"]),
                lambda: attached._destroy_labeled_leftovers("volume", ["volume", "rm", "-f"]),
                lambda: attached._destroy_labeled_leftovers("network", ["network", "rm"]),
            ),
        )
        if fallback_cleanup_used == 1:
            attached.dedicated_after = dedicated_counts()
            attached.shared_match = int(
                canonical_shared_fingerprint() == before_fingerprint
            )
        reject_fallback_cleanup(fallback_cleanup_used)
        stable_zero_observations = observe_stable_zero(
            ((first, first_at), (second, second_at))
        )
        attached.dedicated_after = second
        attached.shared_match = int(
            canonical_shared_fingerprint() == before_fingerprint
        )
        if attached.dedicated_after != (0, 0, 0) or dedicated_counts() != (0, 0, 0):
            _fail("CLEANUP_RESIDUAL")
        cleaned = 1
        payload = {
            "schema": CRASH_SCHEMA,
            "f1_head": F1_HEAD,
            "hard_death_signal": hard_death_signal,
            "fresh_recovery_process": fresh_recovery_process,
            "tamper_rejected": tamper_rejected,
            "tamper_zero_delete": tamper_zero_delete,
            "tamper_reason_verified": tamper_reason_verified,
            "new_volume": new_volume,
            "new_container": new_container,
            "deleted": deleted,
            "remaining": remaining,
            "fallback_cleanup_used": fallback_cleanup_used,
            "stable_zero_observations": stable_zero_observations,
            "package_reverified": package_reverified,
            "rebuild_started": rebuild_started,
            "journal_recovered": journal_recovered,
            "shared_match": attached.shared_match,
            "skipped": 0,
            "dedicated_c": attached.dedicated_after[0],
            "dedicated_v": attached.dedicated_after[1],
            "dedicated_n": attached.dedicated_after[2],
        }
        return validate_crash_payload(payload)
    finally:
        if cleaned != 1:
            try:
                if child is not None and child.poll() is None:
                    os.kill(child.pid, signal.SIGKILL)
                    child.wait(timeout=10)
            except OSError:
                pass
            try:
                _precise_crash_cleanup(receipt_path, before_fingerprint)
            except Exception:
                pass
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
