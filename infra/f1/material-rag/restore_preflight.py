"""Read-only operator preflight for a closed material-RAG backup package.

This command never applies a restore.  ``ready_to_apply`` is always 0.
``destructive_started`` is always 0.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from infra.f1.local_backup import _canonical_json
from infra.f1.material_rag_backup_restore import (
    BackupRestoreError,
    F1_HEAD,
    SCOPE,
    plan_restore,
)
from infra.f1.migrate_f1 import F1_MATERIAL_RAG_MIGRATE_TARGET


SCHEMA = "anhuan-material-rag-restore-preflight-v1"
PACKAGE_F1_HEAD = F1_HEAD
TARGET_F1_HEAD = F1_MATERIAL_RAG_MIGRATE_TARGET
BACKUP_ID_RE = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}\Z")
PAYLOAD_KEYS = (
    "backup_id",
    "business_table_count",
    "destructive_started",
    "identity",
    "manifest_sha256",
    "migration",
    "minio_file_count",
    "package_f1_head",
    "plan_sha256",
    "ready_to_apply",
    "schema",
    "target_f1_head",
    "volume_count",
)
OK_TOKEN = "LOCAL_MATERIAL_RAG_RESTORE_PREFLIGHT_OK"


class RestorePreflightError(RuntimeError):
    pass


def _fail(token: str) -> None:
    raise RestorePreflightError(token)


def _lstat(path: Path) -> os.stat_result:
    try:
        return os.lstat(path)
    except OSError:
        _fail("PREFLIGHT_PATH_MISSING")
        raise


def _assert_dir(path: Path, *, uid: int, mode: int) -> None:
    info = _lstat(path)
    if stat.S_ISLNK(info.st_mode):
        _fail("PREFLIGHT_LINK_FORBIDDEN")
    if not stat.S_ISDIR(info.st_mode):
        _fail("PREFLIGHT_DIR_INVALID")
    if info.st_uid != uid:
        _fail("PREFLIGHT_OWNER_MISMATCH")
    if stat.S_IMODE(info.st_mode) != mode:
        _fail("PREFLIGHT_MODE_INVALID")


def _assert_file(path: Path, *, uid: int, mode: int) -> None:
    info = _lstat(path)
    if stat.S_ISLNK(info.st_mode):
        _fail("PREFLIGHT_LINK_FORBIDDEN")
    if not stat.S_ISREG(info.st_mode):
        _fail("PREFLIGHT_FILE_INVALID")
    if info.st_uid != uid:
        _fail("PREFLIGHT_OWNER_MISMATCH")
    if stat.S_IMODE(info.st_mode) != mode:
        _fail("PREFLIGHT_MODE_INVALID")
    if info.st_nlink != 1:
        _fail("PREFLIGHT_NLINK_INVALID")


def _walk_tree(root: Path, *, uid: int) -> None:
    _assert_dir(root, uid=uid, mode=0o700)
    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        here = Path(current)
        if here != root:
            _assert_dir(here, uid=uid, mode=0o700)
        for name in dirnames:
            child = here / name
            if child.is_symlink():
                _fail("PREFLIGHT_LINK_FORBIDDEN")
        for name in filenames:
            _assert_file(here / name, uid=uid, mode=0o600)


def _read_manifest(root: Path) -> dict[str, Any]:
    raw = (root / "manifest.json").read_bytes()
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("MANIFEST_JSON_INVALID")
        raise
    if not isinstance(document, dict):
        _fail("MANIFEST_SCHEMA_INVALID")
    return document


def run_preflight(
    *,
    backup_id: str,
    store: Path,
    volume_inspects: list[dict[str, Any]],
    uid: int | None = None,
) -> dict[str, Any]:
    if BACKUP_ID_RE.fullmatch(backup_id) is None:
        _fail("BACKUP_ID_INVALID")
    owner = os.geteuid() if uid is None else uid
    store_path = store if store.is_absolute() else store.resolve()
    _assert_dir(store_path, uid=owner, mode=0o700)
    package = store_path / backup_id
    _walk_tree(package, uid=owner)
    document = _read_manifest(package)
    expected_project = str(document.get("project_id") or "")
    expected_parent = str(document.get("parent_project_id") or "")
    expected_database = str(document.get("database") or "")
    expected_scope = str(document.get("scope") or "")
    if expected_scope != SCOPE:
        _fail("RESOURCE_LABEL_MISMATCH")
    if str(document.get("f1_head") or "") != PACKAGE_F1_HEAD:
        _fail("PACKAGE_F1_HEAD_MISMATCH")
    if TARGET_F1_HEAD != "f1_0016":
        _fail("TARGET_F1_HEAD_MISMATCH")
    try:
        plan = plan_restore(
            package,
            expected_project_id=expected_project,
            expected_parent_project_id=expected_parent,
            expected_database=expected_database,
            expected_scope=expected_scope,
            volume_inspects=volume_inspects,
        )
    except BackupRestoreError as error:
        raise RestorePreflightError(str(error)) from error
    if int(plan.manifest["business_table_count"]) != 38:
        _fail("TABLE_CONTRACT_INVALID")
    migration = {
        "apply": 0,
        "package_f1_head": PACKAGE_F1_HEAD,
        "target_f1_head": TARGET_F1_HEAD,
    }
    identity = {
        "database": expected_database,
        "parent_project_id": expected_parent,
        "project_id": expected_project,
        "scope": expected_scope,
    }
    plan_body = {
        "identity": identity,
        "migration": migration,
        "minio_volume": plan.minio_volume,
        "postgres_volume": plan.postgres_volume,
        "schema": SCHEMA,
    }
    plan_sha256 = hashlib.sha256(_canonical_json(plan_body)).hexdigest()
    manifest_sha256 = hashlib.sha256(_canonical_json(plan.manifest)).hexdigest()
    payload = {
        "backup_id": backup_id,
        "business_table_count": 38,
        "destructive_started": 0,
        "identity": identity,
        "manifest_sha256": manifest_sha256,
        "migration": migration,
        "minio_file_count": int(plan.manifest["minio_file_count"]),
        "package_f1_head": PACKAGE_F1_HEAD,
        "plan_sha256": plan_sha256,
        "ready_to_apply": 0,
        "schema": SCHEMA,
        "target_f1_head": TARGET_F1_HEAD,
        "volume_count": 2,
    }
    if tuple(sorted(payload)) != tuple(sorted(PAYLOAD_KEYS)):
        _fail("PREFLIGHT_PAYLOAD_INVALID")
    encoded = _canonical_json(payload)
    decoded = json.loads(encoded.decode("utf-8"))
    if decoded != payload:
        _fail("PREFLIGHT_PAYLOAD_INVALID")
    if decoded["destructive_started"] != 0 or decoded["ready_to_apply"] != 0:
        _fail("PREFLIGHT_APPLY_FORBIDDEN")
    if decoded["migration"]["apply"] != 0:
        _fail("PREFLIGHT_APPLY_FORBIDDEN")
    return decoded


def dump_payload(payload: dict[str, Any]) -> str:
    return _canonical_json(payload).decode("utf-8")
