"""Internal material-RAG restore abort cleanup and crash journal.

Not a user restore command.  Destruction only targets resources whose
exact ID and three labels close.  Journal records stages and IDs only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


class RestoreRecoveryError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


JOURNAL_SCHEMA = "anhuan-material-rag-restore-journal-v1"
JOURNAL_FIELDS = (
    "f1_head",
    "package_dump_sha256",
    "package_tree_sha256",
    "parent_project_id",
    "project_id",
    "resources",
    "schema",
    "scope",
    "stage",
)
STAGES = (
    "PREPARED",
    "VOLUMES_REPLACED",
    "DB_RESTORED",
    "MINIO_REPLAYED",
    "RECOVERED",
)
_FORWARD = {
    "PREPARED": "VOLUMES_REPLACED",
    "VOLUMES_REPLACED": "DB_RESTORED",
    "DB_RESTORED": "MINIO_REPLAYED",
    "MINIO_REPLAYED": "RECOVERED",
}
RECOVERABLE_STAGES = frozenset(
    {"PREPARED", "VOLUMES_REPLACED", "DB_RESTORED", "MINIO_REPLAYED"}
)
RESOURCE_KINDS = frozenset({"container", "network", "volume"})
ABORT_DELETE_KINDS = frozenset({"container", "volume"})
RESOURCE_RECORD_FIELDS = frozenset({"id", "kind"})
_ID = re.compile(r"^[0-9a-f]{12,128}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROJECT = re.compile(r"^[0-9a-f]{32}$")
_CRASH_ENV = "MATERIAL_RAG_RESTORE_CRASH_AFTER"
FORBIDDEN_JOURNAL_TOKENS = frozenset(
    {
        "dsn",
        "password",
        "secret",
        "token",
        "ark",
        "body",
        "object_key",
        "content",
        "minio_user",
    }
)


def _fail(code: str) -> None:
    raise RestoreRecoveryError(code)


def maybe_crash(stage: str) -> None:
    token = os.environ.get(_CRASH_ENV, "").strip()
    if token and token == stage:
        _fail("RESTORE_CRASH_INJECTED")


def _require_id(value: object) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        _fail("RESOURCE_ID_INVALID")
    if "/" in value or "\\" in value or ".." in value:
        _fail("RESOURCE_ID_INVALID")
    return value


def _require_kind(value: object) -> str:
    if value not in RESOURCE_KINDS:
        _fail("RESOURCE_KIND_INVALID")
    return str(value)


def _require_sha256(value: object) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        _fail("JOURNAL_DIGEST_INVALID")
    return value


def _require_project(value: object) -> str:
    if not isinstance(value, str) or not _PROJECT.fullmatch(value):
        _fail("JOURNAL_PROJECT_INVALID")
    return value


def count_only_same(before: Sequence[object], after: Sequence[object]) -> int:
    return int(len(before) == len(after))


def resource_key(kind: str, resource_id: str) -> tuple[str, str]:
    return (_require_kind(kind), _require_id(resource_id))


def volume_identity_id(name: str, created_at: str, mountpoint: str) -> str:
    if not isinstance(name, str) or not name:
        _fail("RESOURCE_ID_INVALID")
    if "/" in name or "\\" in name or ".." in name:
        _fail("RESOURCE_ID_INVALID")
    digest = hashlib.sha256(
        f"{name}\0{created_at}\0{mountpoint}".encode("utf-8")
    ).hexdigest()
    return _require_id(digest)


def normalize_resource(item: Mapping[str, Any]) -> dict[str, str]:
    if set(item) != RESOURCE_RECORD_FIELDS:
        _fail("RESOURCE_RECORD_INVALID")
    return {"id": _require_id(item["id"]), "kind": _require_kind(item["kind"])}


def labeled_identity(item: Mapping[str, Any]) -> dict[str, Any]:
    kind = _require_kind(item.get("kind"))
    resource_id = _require_id(item.get("id"))
    labels = item.get("labels")
    if not isinstance(labels, dict):
        _fail("RESOURCE_LABEL_MISMATCH")
    return {
        "id": resource_id,
        "kind": kind,
        "labels": {
            "io.anhuan.parent-project-id": labels.get("io.anhuan.parent-project-id"),
            "io.anhuan.project-id": labels.get("io.anhuan.project-id"),
            "io.anhuan.scope": labels.get("io.anhuan.scope"),
        },
    }


def labels_match(
    labels: Mapping[str, Any],
    *,
    scope: str,
    project_id: str,
    parent_project_id: str,
) -> bool:
    return (
        labels.get("io.anhuan.scope") == scope
        and labels.get("io.anhuan.project-id") == project_id
        and labels.get("io.anhuan.parent-project-id") == parent_project_id
    )


def accept_journal_lstat(info: os.stat_result) -> None:
    if stat.S_ISLNK(info.st_mode):
        _fail("JOURNAL_SYMLINK_REJECTED")
    if not stat.S_ISREG(info.st_mode):
        _fail("JOURNAL_NOT_REGULAR")
    if info.st_nlink != 1:
        _fail("JOURNAL_HARDLINK_REJECTED")
    if stat.S_IMODE(info.st_mode) != 0o600:
        _fail("JOURNAL_MODE_INVALID")
    if info.st_uid != os.geteuid():
        _fail("JOURNAL_OWNER_INVALID")


def _reject_link_path(path: Path) -> None:
    if path.is_symlink() or path.parent.is_symlink():
        _fail("JOURNAL_SYMLINK_REJECTED")


def write_journal(path: Path, document: Mapping[str, Any]) -> None:
    payload = validate_journal_document(document)
    parent = path.parent
    if not parent.is_dir():
        _fail("JOURNAL_PARENT_MISSING")
    parent_info = parent.lstat()
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        _fail("JOURNAL_PARENT_INVALID")
    if stat.S_IMODE(parent_info.st_mode) != 0o700:
        _fail("JOURNAL_PARENT_MODE_INVALID")
    _reject_link_path(path)
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("ascii")
    raw = encoded.decode("ascii").lower()
    if any(token in raw for token in FORBIDDEN_JOURNAL_TOKENS):
        _fail("JOURNAL_SECRET_REJECTED")
    if path.exists() or path.is_symlink():
        existing = path.lstat()
        accept_journal_lstat(existing)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    tmp = parent / (".journal.tmp." + os.urandom(8).hex())
    descriptor = os.open(tmp, flags, 0o600)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    accept_journal_lstat(path.lstat())


def read_journal(path: Path) -> dict[str, Any]:
    _reject_link_path(path)
    info = path.lstat()
    accept_journal_lstat(info)
    raw = path.read_bytes()
    try:
        document = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("JOURNAL_TRUNCATED")
    if not isinstance(document, dict):
        _fail("JOURNAL_TRUNCATED")
    return validate_journal_document(document)


def validate_journal_document(document: Mapping[str, Any]) -> dict[str, Any]:
    if set(document) != set(JOURNAL_FIELDS):
        _fail("JOURNAL_FIELDS_INVALID")
    if document.get("schema") != JOURNAL_SCHEMA:
        _fail("JOURNAL_SCHEMA_INVALID")
    stage = document.get("stage")
    if stage not in STAGES:
        _fail("JOURNAL_STAGE_INVALID")
    resources = document.get("resources")
    if not isinstance(resources, list):
        _fail("JOURNAL_RESOURCES_INVALID")
    normalized = [normalize_resource(item) for item in resources]
    seen: set[tuple[str, str]] = set()
    for item in normalized:
        key = (item["kind"], item["id"])
        if key in seen:
            _fail("JOURNAL_RESOURCE_DUPLICATE")
        seen.add(key)
    payload = {
        "f1_head": document.get("f1_head"),
        "package_dump_sha256": _require_sha256(document.get("package_dump_sha256")),
        "package_tree_sha256": _require_sha256(document.get("package_tree_sha256")),
        "parent_project_id": _require_project(document.get("parent_project_id")),
        "project_id": _require_project(document.get("project_id")),
        "resources": normalized,
        "schema": JOURNAL_SCHEMA,
        "scope": document.get("scope"),
        "stage": stage,
    }
    if payload["f1_head"] != "f1_0015":
        _fail("JOURNAL_F1_HEAD_INVALID")
    if payload["scope"] != "material-rag-verification":
        _fail("JOURNAL_SCOPE_INVALID")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).lower()
    if any(token in encoded for token in FORBIDDEN_JOURNAL_TOKENS):
        _fail("JOURNAL_SECRET_REJECTED")
    return payload


def advance_stage(current: str, nxt: str) -> str:
    if current not in STAGES or nxt not in STAGES:
        _fail("JOURNAL_STAGE_INVALID")
    if _FORWARD.get(current) != nxt:
        _fail("JOURNAL_STAGE_JUMP")
    return nxt


def new_labeled_resources(
    saved: Sequence[Mapping[str, Any]],
    live: Sequence[Mapping[str, Any]],
    *,
    scope: str,
    project_id: str,
    parent_project_id: str,
) -> list[dict[str, Any]]:
    saved_keys = {resource_key(item["kind"], item["id"]) for item in saved}
    ours: list[dict[str, Any]] = []
    for raw in live:
        item = labeled_identity(raw)
        if not labels_match(
            item["labels"],
            scope=scope,
            project_id=project_id,
            parent_project_id=parent_project_id,
        ):
            _fail("RESOURCE_LABEL_MISMATCH")
        ours.append(item)
    return [
        item
        for item in ours
        if resource_key(item["kind"], item["id"]) not in saved_keys
    ]


def abort_new_restore_resources(
    *,
    saved: Sequence[Mapping[str, Any]],
    live: Sequence[Mapping[str, Any]],
    scope: str,
    project_id: str,
    parent_project_id: str,
    destroyer: Any,
    package_check: Callable[[], None],
) -> dict[str, int]:
    new_items = new_labeled_resources(
        saved,
        live,
        scope=scope,
        project_id=project_id,
        parent_project_id=parent_project_id,
    )
    known = {resource_key(item["kind"], item["id"]) for item in saved}
    known.update(resource_key(item["kind"], item["id"]) for item in live)
    enumerator = getattr(destroyer, "list_project", None)
    if enumerator is None:
        enumerator = getattr(destroyer, "list_labeled", None)
    if enumerator is not None:
        listed = enumerator(scope, project_id, parent_project_id)
        for raw in listed:
            item = labeled_identity(raw)
            if not labels_match(
                item["labels"],
                scope=scope,
                project_id=project_id,
                parent_project_id=parent_project_id,
            ):
                _fail("RESOURCE_LABEL_MISMATCH")
            key = resource_key(item["kind"], item["id"])
            if key not in known and item["kind"] in ABORT_DELETE_KINDS:
                _fail("RESOURCE_UNEXPECTED_EXTRA")
    targets: list[tuple[str, str]] = []
    for item in new_items:
        kind = item["kind"]
        resource_id = item["id"]
        if kind not in ABORT_DELETE_KINDS:
            continue
        inspected = destroyer.inspect(kind, resource_id)
        if inspected is None:
            _fail("RESOURCE_ID_MISMATCH")
        current = labeled_identity(inspected)
        if current["id"] != resource_id or current["kind"] != kind:
            _fail("RESOURCE_ID_MISMATCH")
        if not labels_match(
            current["labels"],
            scope=scope,
            project_id=project_id,
            parent_project_id=parent_project_id,
        ):
            _fail("RESOURCE_LABEL_MISMATCH")
        targets.append((kind, resource_id))
    if getattr(destroyer, "destructive_started", 0) not in {0, 1}:
        _fail("DESTROYER_STATE_INVALID")
    destroyer.destructive_started = 1
    destroyer.destroy(tuple(targets))
    remaining = 0
    for kind, resource_id in targets:
        leftover = destroyer.inspect(kind, resource_id)
        if leftover is not None:
            _fail("RESTORE_ABORT_ID_REMAINS")
        remaining += 0
    package_check()
    saved_ids = {str(item["id"]) for item in saved}
    live_ids = {str(item["id"]) for item in live}
    return {
        "deleted": len(targets),
        "rebuild_started": 0,
        "same_count": count_only_same(saved, live),
        "same_count_swap_observed": int(
            count_only_same(saved, live) == 1 and saved_ids != live_ids
        ),
        "new_volume_count": sum(1 for kind, _resource_id in targets if kind == "volume"),
        "new_container_count": sum(
            1 for kind, _resource_id in targets if kind == "container"
        ),
        "remaining_abort_id_count": remaining,
        "package_reverified": 1,
    }


def prepare_empty_core(
    *,
    live: Sequence[Mapping[str, Any]],
    abort_new_ids: Sequence[str],
) -> dict[str, int]:
    leftover = set(abort_new_ids) & {str(item.get("id")) for item in live}
    if leftover:
        _fail("RESTORE_CORE_REUSED_ABORT_SCENE")
    return {"prepared_empty": 1, "rebuild_started": 0}


def recover_from_journal(
    path: Path,
    *,
    expected_scope: str,
    expected_project_id: str,
    expected_parent_project_id: str,
    expected_dump_sha256: str,
    expected_tree_sha256: str,
    live: Sequence[Mapping[str, Any]],
    destroyer: Any,
    package_check: Callable[[], None],
) -> dict[str, int]:
    document = read_journal(path)
    if document["project_id"] != expected_project_id:
        _fail("JOURNAL_PROJECT_MISMATCH")
    if document["parent_project_id"] != expected_parent_project_id:
        _fail("JOURNAL_PROJECT_MISMATCH")
    if document["scope"] != expected_scope:
        _fail("JOURNAL_SCOPE_INVALID")
    if document["package_dump_sha256"] != expected_dump_sha256:
        _fail("JOURNAL_PACKAGE_MISMATCH")
    if document["package_tree_sha256"] != expected_tree_sha256:
        _fail("JOURNAL_PACKAGE_MISMATCH")
    if document["stage"] not in RECOVERABLE_STAGES:
        _fail("JOURNAL_STAGE_INVALID")
    saved = [
        {
            "id": item["id"],
            "kind": item["kind"],
            "labels": {
                "io.anhuan.parent-project-id": expected_parent_project_id,
                "io.anhuan.project-id": expected_project_id,
                "io.anhuan.scope": expected_scope,
            },
        }
        for item in document["resources"]
        if item["kind"] in ABORT_DELETE_KINDS
    ]
    result = abort_new_restore_resources(
        saved=saved,
        live=live,
        scope=expected_scope,
        project_id=expected_project_id,
        parent_project_id=expected_parent_project_id,
        destroyer=destroyer,
        package_check=package_check,
    )
    if result["rebuild_started"] != 0:
        _fail("JOURNAL_REBUILD_STARTED")
    updated = dict(document)
    updated["stage"] = "RECOVERED"
    write_journal(path, updated)
    package_check()
    return result
