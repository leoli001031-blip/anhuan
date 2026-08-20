"""Dedicated material-RAG backup/restore machine-gate tests.

Contract tests do not start Docker.  The live test drives one dedicated
PostgreSQL+MinIO cycle through the isolated check path.
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import inspect
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from infra.f1 import local_backup
from infra.f1.material_rag_backup_restore import (
    CHECK_SCHEMA,
    F1_HEAD,
    SCHEMA,
    SCOPE,
    BackupRestoreError,
    FakeDestroyer,
    MATERIAL_RAG_BACKUP_TABLES,
    _confine_object_path,
    create_manifest,
    guarded_restore,
    plan_restore,
    run_machine_gate,
    selectable_data_volumes,
    verify_package,
)


ROOT = Path(__file__).resolve().parents[1]
PROJECT_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
PARENT_ID = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
DATABASE = "f1_mrbr_testdb1"
BUSINESS_SNAPSHOT = {
    "table_count": 38,
    "total_row_count": 10,
    "nonempty_table_count": 4,
    "count_sha256": "a" * 64,
}


def load_restore_recovery():
    path = ROOT / "infra/f1/material-rag/restore_recovery.py"
    loader = importlib.machinery.SourceFileLoader(
        "material_rag_restore_recovery_tests", str(path)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise AssertionError("restore_recovery import unavailable")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def owned_labels() -> dict[str, str]:
    return {
        "io.anhuan.parent-project-id": PARENT_ID,
        "io.anhuan.project-id": PROJECT_ID,
        "io.anhuan.scope": SCOPE,
    }


def hid(tag: str) -> str:
    return hashlib.sha256(tag.encode("ascii")).hexdigest()


def labeled(kind: str, resource_id: str, labels: dict[str, str] | None = None, **extra: object) -> dict[str, object]:
    item: dict[str, object] = {
        "handle": resource_id,
        "id": resource_id,
        "kind": kind,
        "labels": dict(labels or owned_labels()),
    }
    item.update(extra)
    return item


class IdentityWorld:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self._items = {(str(item["kind"]), str(item["id"])): dict(item) for item in items}
        self.destroyed: tuple[tuple[str, str], ...] = ()
        self.destructive_started = 0
        self.rebuild_started = 0
        self.inspect_overrides: dict[tuple[str, str], dict[str, object] | None] = {}

    def inspect(self, kind: str, resource_id: str):
        key = (kind, resource_id)
        if key in self.inspect_overrides:
            return self.inspect_overrides[key]
        return self._items.get(key)

    def list_project(
        self, scope: str, project_id: str, parent_project_id: str
    ) -> list[dict[str, object]]:
        del scope, project_id, parent_project_id
        return [item for item in self._items.values() if not item.get("foreign")]

    def list_labeled(
        self, scope: str, project_id: str, parent_project_id: str
    ) -> list[dict[str, object]]:
        return self.list_project(scope, project_id, parent_project_id)

    def destroy(self, targets: tuple[tuple[str, str], ...]) -> None:
        self.destroyed = targets
        for kind, resource_id in targets:
            self._items.pop((kind, resource_id), None)


def journal_document(
    *,
    stage: str = "PREPARED",
    dump: str,
    tree: str,
    resources: list[dict[str, str]],
    project_id: str = PROJECT_ID,
    parent_id: str = PARENT_ID,
) -> dict[str, object]:
    recovery = load_restore_recovery()
    return {
        "f1_head": F1_HEAD,
        "package_dump_sha256": dump,
        "package_tree_sha256": tree,
        "parent_project_id": parent_id,
        "project_id": project_id,
        "resources": resources,
        "schema": recovery.JOURNAL_SCHEMA,
        "scope": SCOPE,
        "stage": stage,
    }


def load_localctl():
    path = ROOT / "scripts/localctl"
    loader = importlib.machinery.SourceFileLoader(
        "material_rag_backup_restore_localctl", str(path)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise AssertionError("localctl import unavailable")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def private_directory(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    path.chmod(0o700)
    return path


def private_file(path: Path, body: bytes) -> Path:
    path.write_bytes(body)
    path.chmod(0o600)
    return path


def backup_stage(parent: Path, name: str = "backup") -> Path:
    root = private_directory(parent / name)
    private_file(root / "database.dump", b"postgres-dump\x00material-rag-v1")
    minio = private_directory(root / "minio-data")
    bucket = private_directory(minio / "anhuan-f1-documents")
    private_file(bucket / "aabbccddeeff00112233445566778899.pdf", b"%PDF-1.4\nbody")
    private_file(minio / "opaque-object-two", b"second-object")
    return root


def expected() -> dict[str, str]:
    return {
        "expected_project_id": PROJECT_ID,
        "expected_parent_project_id": PARENT_ID,
        "expected_database": DATABASE,
        "expected_scope": SCOPE,
    }


def matching_inspects(
    *, parent_id: str = PARENT_ID, project_id: str = PROJECT_ID
) -> list[dict[str, object]]:
    labels = {
        "io.anhuan.scope": SCOPE,
        "io.anhuan.project-id": project_id,
        "io.anhuan.parent-project-id": parent_id,
    }
    return [
        {"Name": "anhuan-mr-br-abc_br_postgres_data", "Labels": dict(labels)},
        {"Name": "anhuan-mr-br-abc_br_minio_data", "Labels": dict(labels)},
        {"Name": "anhuan-mr-br-abc_br_postgres_secrets", "Labels": dict(labels)},
        {
            "Name": "anhuan-f1_postgres_data",
            "Labels": {"com.docker.compose.project": "anhuan-f1"},
        },
    ]


def write_valid_package(parent: Path) -> Path:
    root = backup_stage(parent)
    create_manifest(
        root,
        project_id=PROJECT_ID,
        parent_project_id=PARENT_ID,
        database=DATABASE,
        scope=SCOPE,
        f1_head=F1_HEAD,
        business_snapshot=BUSINESS_SNAPSHOT,
    )
    return root


def rewrite_manifest(root: Path, updater) -> None:
    path = root / "manifest.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    updater(document)
    path.write_text(
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


class MaterialRagBackupContractTests(unittest.TestCase):
    def test_schema_requires_f1_0015_and_38_tables(self) -> None:
        self.assertEqual(len(MATERIAL_RAG_BACKUP_TABLES), 38)
        with tempfile.TemporaryDirectory() as raw:
            root = backup_stage(Path(raw))
            with self.assertRaises(BackupRestoreError) as raised:
                create_manifest(
                    root,
                    project_id=PROJECT_ID,
                    parent_project_id=PARENT_ID,
                    database=DATABASE,
                    scope=SCOPE,
                    f1_head="f1_0014",
                    business_snapshot=BUSINESS_SNAPSHOT,
                )
            self.assertEqual(raised.exception.code, "F1_HEAD_MISMATCH")
            with self.assertRaises(BackupRestoreError) as raised:
                create_manifest(
                    root,
                    project_id=PROJECT_ID,
                    parent_project_id=PARENT_ID,
                    database=DATABASE,
                    scope=SCOPE,
                    f1_head=F1_HEAD,
                    business_snapshot={
                        **BUSINESS_SNAPSHOT,
                        "table_count": 35,
                    },
                )
            self.assertEqual(
                raised.exception.code, "BUSINESS_SNAPSHOT_VALUE_INVALID"
            )

    def test_rejects_empty_dump(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = backup_stage(Path(raw))
            (root / "database.dump").write_bytes(b"")
            (root / "database.dump").chmod(0o600)
            with self.assertRaises(BackupRestoreError) as raised:
                create_manifest(
                    root,
                    project_id=PROJECT_ID,
                    parent_project_id=PARENT_ID,
                    database=DATABASE,
                    scope=SCOPE,
                    f1_head=F1_HEAD,
                    business_snapshot=BUSINESS_SNAPSHOT,
                )
            self.assertEqual(raised.exception.code, "DATABASE_DUMP_EMPTY")

    def test_rejects_symlink_and_hardlink(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = backup_stage(Path(raw))
            target = root / "minio-data" / "opaque-object-two"
            linked = root / "minio-data" / "linked-object"
            os.symlink(target.name, linked)
            with self.assertRaises(BackupRestoreError) as raised:
                create_manifest(
                    root,
                    project_id=PROJECT_ID,
                    parent_project_id=PARENT_ID,
                    database=DATABASE,
                    scope=SCOPE,
                    f1_head=F1_HEAD,
                    business_snapshot=BUSINESS_SNAPSHOT,
                )
            self.assertIn("SYMLINK", raised.exception.code)
            linked.unlink()
            os.link(target, linked)
            with self.assertRaises(BackupRestoreError) as raised:
                create_manifest(
                    root,
                    project_id=PROJECT_ID,
                    parent_project_id=PARENT_ID,
                    database=DATABASE,
                    scope=SCOPE,
                    f1_head=F1_HEAD,
                    business_snapshot=BUSINESS_SNAPSHOT,
                )
            self.assertIn("HARDLINK", raised.exception.code)

    def test_minio_object_key_is_confined(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "bucket"
            root.mkdir(mode=0o700)
            confined = _confine_object_path(
                root, "aabbccddeeff00112233445566778899.pdf"
            )
            self.assertEqual(confined.parent, root)
            nested = _confine_object_path(root, "nested/object.bin")
            self.assertEqual(nested.parent, root / "nested")
            with self.assertRaises(BackupRestoreError) as raised:
                _confine_object_path(root, "../escape.pdf")
            self.assertEqual(raised.exception.code, "MINIO_OBJECT_KEY_INVALID")
            with self.assertRaises(BackupRestoreError) as raised:
                _confine_object_path(root, "/abs.pdf")
            self.assertEqual(raised.exception.code, "MINIO_OBJECT_KEY_INVALID")

    def test_tamper_dump_fails_before_destructive(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = write_valid_package(Path(raw))
            (root / "database.dump").write_bytes(
                (root / "database.dump").read_bytes() + b"\x00"
            )
            (root / "database.dump").chmod(0o600)
            destroyer = FakeDestroyer()
            before = destroyer.snapshot()
            with self.assertRaises(BackupRestoreError) as raised:
                guarded_restore(
                    root,
                    volume_inspects=matching_inspects(),
                    destroyer=destroyer,
                    **expected(),
                )
            self.assertEqual(raised.exception.code, "DATABASE_DUMP_MISMATCH")
            self.assertEqual(destroyer.snapshot(), before)
            self.assertEqual(destroyer.destructive_started, 0)

    def test_tamper_minio_tree_fails_before_destructive(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = write_valid_package(Path(raw))
            extra = root / "minio-data" / "tamper.bin"
            extra.write_bytes(b"tamper")
            extra.chmod(0o600)
            destroyer = FakeDestroyer()
            before = destroyer.snapshot()
            with self.assertRaises(BackupRestoreError) as raised:
                guarded_restore(
                    root,
                    volume_inspects=matching_inspects(),
                    destroyer=destroyer,
                    **expected(),
                )
            self.assertEqual(raised.exception.code, "MINIO_TREE_MISMATCH")
            self.assertEqual(destroyer.snapshot(), before)

    def test_tamper_f1_head_fails_before_destructive(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = write_valid_package(Path(raw))
            rewrite_manifest(root, lambda doc: doc.__setitem__("f1_head", "f1_0014"))
            destroyer = FakeDestroyer()
            before = destroyer.snapshot()
            with self.assertRaises(BackupRestoreError) as raised:
                guarded_restore(
                    root,
                    volume_inspects=matching_inspects(),
                    destroyer=destroyer,
                    **expected(),
                )
            self.assertEqual(raised.exception.code, "F1_HEAD_MISMATCH")
            self.assertEqual(destroyer.snapshot(), before)

    def test_tamper_table_contract_fails_before_destructive(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = write_valid_package(Path(raw))
            rewrite_manifest(
                root, lambda doc: doc.__setitem__("business_table_count", 35)
            )
            destroyer = FakeDestroyer()
            before = destroyer.snapshot()
            with self.assertRaises(BackupRestoreError) as raised:
                guarded_restore(
                    root,
                    volume_inspects=matching_inspects(),
                    destroyer=destroyer,
                    **expected(),
                )
            self.assertEqual(
                raised.exception.code, "BUSINESS_SNAPSHOT_VALUE_INVALID"
            )
            self.assertEqual(destroyer.snapshot(), before)

    def test_tamper_resource_labels_fails_before_destructive(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = write_valid_package(Path(raw))
            destroyer = FakeDestroyer()
            before = destroyer.snapshot()
            with self.assertRaises(BackupRestoreError) as raised:
                guarded_restore(
                    root,
                    volume_inspects=matching_inspects(parent_id="ccccccccccccdddddddddddddddddddd"),
                    destroyer=destroyer,
                    **expected(),
                )
            self.assertEqual(raised.exception.code, "RESOURCE_LABEL_MISMATCH")
            self.assertEqual(destroyer.snapshot(), before)

    def test_verify_restore_green_after_repair(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = write_valid_package(Path(raw))
            (root / "database.dump").write_bytes(
                (root / "database.dump").read_bytes() + b"\x00"
            )
            (root / "database.dump").chmod(0o600)
            destroyer = FakeDestroyer()
            with self.assertRaises(BackupRestoreError):
                guarded_restore(
                    root,
                    volume_inspects=matching_inspects(),
                    destroyer=destroyer,
                    **expected(),
                )
            self.assertEqual(destroyer.destructive_started, 0)
            original = Path(raw) / "original"
            repaired = write_valid_package(original.parent / "repaired")
            manifest = verify_package(repaired, **expected())
            self.assertEqual(manifest["schema"], SCHEMA)
            self.assertEqual(manifest["f1_head"], F1_HEAD)
            self.assertEqual(manifest["business_table_count"], 38)
            guarded_restore(
                repaired,
                volume_inspects=matching_inspects(),
                destroyer=destroyer,
                **expected(),
            )
            self.assertEqual(destroyer.destructive_started, 1)
            self.assertEqual(destroyer.destroyed[0].endswith("_br_postgres_data"), True)
            self.assertEqual(destroyer.destroyed[1].endswith("_br_minio_data"), True)
            del original

    def test_package_excludes_secret_and_ragflow_names(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = backup_stage(Path(raw))
            forbidden = private_directory(root / "minio-data" / "ragflow")
            private_file(forbidden / "cache.bin", b"nope")
            with self.assertRaises(BackupRestoreError) as raised:
                create_manifest(
                    root,
                    project_id=PROJECT_ID,
                    parent_project_id=PARENT_ID,
                    database=DATABASE,
                    scope=SCOPE,
                    f1_head=F1_HEAD,
                    business_snapshot=BUSINESS_SNAPSHOT,
                )
            self.assertEqual(raised.exception.code, "PACKAGE_FORBIDDEN_ENTRY")

    def test_create_and_verify_canonical_manifest_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = write_valid_package(Path(raw))
            manifest = verify_package(root, **expected())
            self.assertEqual(manifest["schema"], SCHEMA)
            self.assertEqual(manifest["scope"], SCOPE)
            self.assertGreater(manifest["db_dump_size"], 0)
            self.assertGreater(manifest["minio_file_count"], 0)
            raw_manifest = (root / "manifest.json").read_bytes()
            self.assertEqual(stat.S_IMODE((root / "manifest.json").stat().st_mode), 0o600)
            self.assertTrue(raw_manifest.endswith(b"\n"))
            self.assertEqual(set(manifest), set(json.loads(raw_manifest)))

    def test_localctl_parser_isolates_subcommand(self) -> None:
        localctl = load_localctl()
        parser = localctl._parser()
        parsed = parser.parse_args(["material-rag-backup-restore-check"])
        self.assertEqual(parsed.command, "material-rag-backup-restore-check")
        backup = parser.parse_args(["backup"])
        self.assertEqual(backup.command, "backup")
        restore = parser.parse_args(["restore", "--confirm-local-data"])
        self.assertEqual(restore.command, "restore")
        self.assertTrue(restore.confirm_local_data)

    def test_default_35_contract_rejects_38(self) -> None:
        valid_35 = {
            "table_count": 35,
            "total_row_count": 2,
            "nonempty_table_count": 2,
            "count_sha256": "a" * 64,
        }
        self.assertEqual(
            local_backup._validate_business_snapshot(valid_35)["table_count"], 35
        )
        with self.assertRaises(local_backup.BackupContractError) as raised:
            local_backup._validate_business_snapshot(
                {
                    "table_count": 38,
                    "total_row_count": 2,
                    "nonempty_table_count": 2,
                    "count_sha256": "a" * 64,
                }
            )
        self.assertEqual(str(raised.exception), "BUSINESS_SNAPSHOT_VALUE_INVALID")

    def test_maintenance_sql_is_delete_not_failed_update(self) -> None:
        source = (
            ROOT / "infra/f1/material-rag/restore_maintenance.py"
        ).read_text(encoding="utf-8")
        self.assertIn("DELETE FROM f1.material_rag_job", source)
        self.assertIn("DELETE FROM f1.material_rag_unit", source)
        self.assertIn("status='deleted'", source)
        self.assertNotIn("UPDATE f1.material_rag_job SET status='failed'", source)
        self.assertNotIn("session_replication_role','replica'", source)
        self.assertNotIn("BYPASSRLS", source)
        self.assertNotIn("SECURITY DEFINER", source)

    def test_volume_selector_requires_three_labels(self) -> None:
        postgres, minio = selectable_data_volumes(
            matching_inspects(),
            scope=SCOPE,
            project_id=PROJECT_ID,
            parent_project_id=PARENT_ID,
        )
        self.assertTrue(postgres.endswith("_br_postgres_data"))
        self.assertTrue(minio.endswith("_br_minio_data"))
        with self.assertRaises(BackupRestoreError) as raised:
            selectable_data_volumes(
                matching_inspects(parent_id="ccccccccccccdddddddddddddddddddd"),
                scope=SCOPE,
                project_id=PROJECT_ID,
                parent_project_id=PARENT_ID,
            )
        self.assertEqual(raised.exception.code, "RESOURCE_LABEL_MISMATCH")

    def test_compose_avoids_default_35_restore(self) -> None:
        compose = (
            ROOT / "infra/f1/docker-compose.material-rag-backup-restore.yml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("LOCAL_BACKUP_TABLE_COUNT", compose)
        self.assertNotIn("DROP TABLE IF EXISTS f1.material_knowledge_scope", compose)
        self.assertIn("pg_dump --format=custom", compose)
        self.assertIn("pg_restore --clean --if-exists --exit-on-error", compose)
        self.assertIn("DELETE FROM f1.material_rag_job", compose)
        self.assertIn("secret-init", compose)
        self.assertIn("postgres:18.3-bookworm@sha256:80630f83", compose)
        self.assertIn("minio/minio:RELEASE.2024-07-29T22-14-52Z", compose)
        self.assertNotIn("ragflow", compose.lower())
        self.assertNotIn("elasticsearch", compose.lower())

    def test_check_schema_is_v3_and_payload_keyset_is_closed(self) -> None:
        from infra.f1.material_rag_backup_restore import CHECK_PAYLOAD_KEYS

        self.assertEqual(CHECK_SCHEMA, "anhuan-material-rag-backup-restore-check-v3")
        required = {
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
        }
        self.assertEqual(set(CHECK_PAYLOAD_KEYS), required)
        payload = self._valid_payload()
        payload["schema"] = "anhuan-material-rag-backup-restore-check-v2"
        from infra.f1.material_rag_backup_restore import validate_check_payload

        with self.assertRaises(BackupRestoreError) as raised:
            validate_check_payload(payload)
        self.assertEqual(raised.exception.code, "CHECK_PAYLOAD_SCHEMA_STALE")

    def _valid_payload(self) -> dict:
        from infra.f1.material_rag_backup_restore import CHECK_PAYLOAD_KEYS

        payload = {key: 0 for key in CHECK_PAYLOAD_KEYS} | {
            "schema": "anhuan-material-rag-backup-restore-check-v3",
            "f1_head": F1_HEAD,
            "business_table_count": 38,
            "db_dump_size_positive": 1,
            "minio_file_count": 5,
            "minio_live_tree_match": 1,
            "front_door_tamper_failures": 5,
            "front_door_repair_ok": 1,
            "destructive_started": 1,
            "restore_ok": 1,
            "rebuild_ok": 1,
            "restart_ok": 1,
            "post_restart_fresh_process": 1,
            "post_restart_retrieval_ok": 1,
            "cleanup_label_rejection": 1,
            "restore_failure_cleanup": 1,
            "maintenance_failure_cleanup": 1,
            "rebuild_failure_cleanup": 1,
            "restart_failure_cleanup": 1,
            "fail_cleanup_ok": 1,
            "shared_fingerprint_match": 1,
            "same_count_swap_observed": 1,
            "new_volume_count": 2,
            "new_container_count": 3,
            "deleted_count": 5,
            "remaining_abort_id_count": 0,
            "package_reverified": 1,
            "rebuild_started": 0,
            "retry_abort_id_reuse_count": 0,
            "journal_stage_recovered": 1,
        }
        observed = {
            "restore_mutation_observed": 1,
            "maintenance_mutation_observed": 1,
            "rebuild_mutation_observed": 1,
            "restart_mutation_observed": 1,
        }
        if set(observed) <= set(payload):
            payload.update(observed)
        return payload

    def test_same_count_and_size_different_body_sha_is_red(self) -> None:
        from infra.f1.material_rag_backup_restore import (
            canonical_object_tree_from_dir,
            compare_object_trees,
        )

        with tempfile.TemporaryDirectory() as raw:
            left = Path(raw) / "left"
            right = Path(raw) / "right"
            for root, body in ((left, b"AAAA"), (right, b"BBBB")):
                bucket = private_directory(root / "anhuan-f1-documents")
                private_file(bucket / "same-size.bin", body)
            left_tree = canonical_object_tree_from_dir(left)
            right_tree = canonical_object_tree_from_dir(right)
            self.assertEqual(len(left_tree), 1)
            self.assertEqual(left_tree[0]["size"], right_tree[0]["size"])
            self.assertEqual(left_tree[0]["bucket"], right_tree[0]["bucket"])
            self.assertNotEqual(left_tree[0]["body_sha256"], right_tree[0]["body_sha256"])
            self.assertNotIn("same-size.bin", repr(left_tree))
            self.assertNotIn("AAAA", repr(left_tree))
            with self.assertRaises(BackupRestoreError) as raised:
                compare_object_trees(left_tree, right_tree)
            self.assertEqual(raised.exception.code, "MINIO_BODY_SHA_MISMATCH")
            compare_object_trees(left_tree, left_tree)

    def test_live_tree_records_require_bucket_key_size_and_body_sha(self) -> None:
        from infra.f1.material_rag_backup_restore import canonical_object_tree_from_dir

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "tree"
            bucket = private_directory(root / "anhuan-f1-documents")
            private_file(bucket / "unit.bin", b"body-one")
            tree = canonical_object_tree_from_dir(root)
            self.assertEqual(set(tree[0]), {"bucket", "key", "size", "body_sha256"})
            self.assertEqual(tree[0]["size"], 8)
            self.assertEqual(len(tree[0]["body_sha256"]), 64)

    def test_validate_check_payload_rejects_missing_and_extra_keys(self) -> None:
        from infra.f1.material_rag_backup_restore import validate_check_payload

        payload = self._valid_payload()
        validate_check_payload(payload)
        missing = dict(payload)
        del missing["minio_live_tree_match"]
        with self.assertRaises(BackupRestoreError) as raised:
            validate_check_payload(missing)
        self.assertEqual(raised.exception.code, "CHECK_PAYLOAD_KEYS_INVALID")
        extra = dict(payload)
        extra["bonus"] = 1
        with self.assertRaises(BackupRestoreError) as raised:
            validate_check_payload(extra)
        self.assertEqual(raised.exception.code, "CHECK_PAYLOAD_KEYS_INVALID")

    def test_validate_check_payload_rejects_hardcoded_restart_without_fresh_process(
        self,
    ) -> None:
        from infra.f1.material_rag_backup_restore import validate_check_payload

        payload = self._valid_payload()
        payload["post_restart_fresh_process"] = 0
        payload["restart_ok"] = 1
        with self.assertRaises(BackupRestoreError) as raised:
            validate_check_payload(payload)
        self.assertEqual(raised.exception.code, "CHECK_PAYLOAD_HARDCODED")
        payload = self._valid_payload()
        payload["minio_live_tree_match"] = 0
        payload["restore_ok"] = 1
        with self.assertRaises(BackupRestoreError) as raised:
            validate_check_payload(payload)
        self.assertEqual(raised.exception.code, "CHECK_PAYLOAD_HARDCODED")

    def test_localctl_rejects_missing_extra_and_hardcoded_check_payload(self) -> None:
        localctl = load_localctl()
        validator = localctl._validate_material_rag_backup_restore_payload
        payload = self._valid_payload()
        validator(payload)
        with self.assertRaises(localctl.LocalError):
            validator({k: payload[k] for k in payload if k != "cross_tenant_visible"})
        extra = dict(payload)
        extra["token"] = "x"
        with self.assertRaises(localctl.LocalError):
            validator(extra)
        hardcoded = dict(payload)
        hardcoded["post_restart_fresh_process"] = 0
        with self.assertRaises(localctl.LocalError):
            validator(hardcoded)

    def test_cleanup_refuses_each_wrong_label_without_deleting(self) -> None:
        from infra.f1.material_rag_backup_restore import (
            destroy_labeled_resources,
            require_three_labels,
        )

        labels = {
            "io.anhuan.scope": SCOPE,
            "io.anhuan.project-id": PROJECT_ID,
            "io.anhuan.parent-project-id": PARENT_ID,
        }
        destroyer = FakeDestroyer()
        inspects = [
            {"Name": "anhuan-mr-br-abc_br_postgres_data", "Labels": dict(labels)},
            {"Name": "anhuan-mr-br-abc_br_minio_data", "Labels": dict(labels)},
        ]
        destroy_labeled_resources(
            inspects,
            names=("anhuan-mr-br-abc_br_postgres_data", "anhuan-mr-br-abc_br_minio_data"),
            scope=SCOPE,
            project_id=PROJECT_ID,
            parent_project_id=PARENT_ID,
            destroyer=destroyer,
        )
        self.assertEqual(destroyer.destructive_started, 1)
        after_ok = destroyer.db_canary
        for field, value in (
            ("io.anhuan.scope", "other-scope"),
            ("io.anhuan.project-id", "cccccccccccccccccccccccccccccccc"),
            ("io.anhuan.parent-project-id", "dddddddddddddddddddddddddddddddd"),
        ):
            destroyer.destructive_started = 0
            destroyer.destroyed = ()
            bad = dict(labels)
            bad[field] = value
            inspects = [
                {"Name": "anhuan-mr-br-abc_br_postgres_data", "Labels": bad},
                {"Name": "anhuan-mr-br-abc_br_minio_data", "Labels": dict(labels)},
            ]
            with self.assertRaises(BackupRestoreError) as raised:
                destroy_labeled_resources(
                    inspects,
                    names=(
                        "anhuan-mr-br-abc_br_postgres_data",
                        "anhuan-mr-br-abc_br_minio_data",
                    ),
                    scope=SCOPE,
                    project_id=PROJECT_ID,
                    parent_project_id=PARENT_ID,
                    destroyer=destroyer,
                )
            self.assertEqual(raised.exception.code, "RESOURCE_LABEL_MISMATCH")
            self.assertEqual(destroyer.destructive_started, 0)
            self.assertEqual(destroyer.destroyed, ())
        require_three_labels(labels, SCOPE, PROJECT_ID, PARENT_ID)
        with self.assertRaises(BackupRestoreError):
            require_three_labels(
                {**labels, "io.anhuan.scope": "x"},
                SCOPE,
                PROJECT_ID,
                PARENT_ID,
            )
        self.assertEqual(after_ok, destroyer.db_canary)

    def test_post_restart_probe_is_fresh_subprocess_entrypoint(self) -> None:
        source = (
            ROOT / "infra/f1/material-rag/post_restart_probe.py"
        ).read_text(encoding="utf-8")
        self.assertIn('if __name__ == "__main__"', source)
        self.assertIn("PostgresMaterialRagRepository", source)
        self.assertIn("MaterialRetrievalService", source)
        gate = (ROOT / "infra/f1/material_rag_backup_restore.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("post_restart_probe.py", gate)
        self.assertIn("subprocess", gate)
        self.assertNotIn("BackupRestoreStack", source)
        self.assertNotIn("rag_fake", source)

    def test_isolation_payload_requires_service_visibility_not_enqueue_only(self) -> None:
        payload = self._valid_payload()
        self.assertIn("cross_tenant_visible", payload)
        self.assertIn("cross_scope_visible", payload)
        self.assertEqual(payload["cross_tenant_visible"], 0)
        self.assertEqual(payload["cross_scope_visible"], 0)
        source = (ROOT / "infra/f1/material_rag_backup_restore.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("cross_tenant_visible", source)
        self.assertIn("retrieve_registered", source)
        self.assertIn("RemoteCandidate", source)

    def test_four_phase_failure_cleanup_keys_are_required(self) -> None:
        payload = self._valid_payload()
        for key in (
            "restore_failure_cleanup",
            "maintenance_failure_cleanup",
            "rebuild_failure_cleanup",
            "restart_failure_cleanup",
            "cleanup_label_rejection",
        ):
            self.assertEqual(payload[key], 1, key)
        source = (ROOT / "infra/f1/material_rag_backup_restore.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("RESTORE_INJECTED_FAILURE", source)
        self.assertIn("MAINTENANCE_INJECTED_FAILURE", source)
        self.assertIn("REBUILD_INJECTED_FAILURE", source)
        self.assertIn("RESTART_INJECTED_FAILURE", source)

    def test_restore_injects_after_volume_or_raw_db_restore(self) -> None:
        from infra.f1.material_rag_backup_restore import BackupRestoreStack

        source = inspect.getsource(BackupRestoreStack.restore_package)
        inject_at = source.index('"RESTORE_INJECTED_FAILURE"')
        db_at = source.index('"restore-db"')
        abort_at = source.index("abort_result = abort_new_restore_resources(")
        recovered_at = source.index('"RECOVERED"', abort_at)
        verify_at = source.index("verify_package(", abort_at)
        self.assertLess(source.index("_destroy_data_volumes("), inject_at)
        self.assertLess(db_at, abort_at)
        self.assertLess(abort_at, verify_at)
        self.assertLess(verify_at, recovered_at)
        self.assertLess(recovered_at, inject_at)
        self.assertGreater(inject_at, source.index("plan_restore("))
        self.assertLess(source.index("capture_core_identities("), source.index("_destroy_data_volumes("))
        self.assertIn("prepare_empty_core(", source)
        self.assertIn("abort_result[", source)
        self.assertIn('abort_result["deleted"]', source)
        self.assertIn('abort_result["new_volume_count"]', source)
        self.assertIn('abort_result["new_container_count"]', source)
        self.assertIn('abort_result["same_count_swap_observed"]', source)
        self.assertIn('abort_result["package_reverified"]', source)
        self.assertIn('abort_result["rebuild_started"]', source)
        capture = inspect.getsource(BackupRestoreStack.capture_core_identities)
        handles = inspect.getsource(BackupRestoreStack._list_project_handles)
        project_filter = inspect.getsource(BackupRestoreStack._compose_project_filter)
        self.assertIn("_list_project_handles", capture)
        self.assertIn("also_handles", capture)
        self.assertNotIn("_labels_owned", capture)
        self.assertIn("com.docker.compose.project=", project_filter)
        self.assertIn("self._compose_project_filter()", handles)
        identity_src = inspect.getsource(BackupRestoreStack._identity_from_docker)
        self.assertIn("stderr=subprocess.DEVNULL", identity_src)
        abort_src = (
            ROOT / "infra/f1/material-rag/restore_recovery.py"
        ).read_text(encoding="utf-8")
        mismatch_at = abort_src.index("RESOURCE_LABEL_MISMATCH")
        extra_at = abort_src.index("RESOURCE_UNEXPECTED_EXTRA")
        destroy_at = abort_src.index("destroyer.destroy(")
        self.assertLess(mismatch_at, destroy_at)
        self.assertLess(extra_at, destroy_at)
        gate = inspect.getsource(
            __import__(
                "infra.f1.material_rag_backup_restore", fromlist=["run_machine_gate"]
            ).run_machine_gate
        )
        self.assertIn("restore_abort_metrics", gate)
        self.assertIn("RESTORE_ABORT_ID_REMAINS", gate)
        restore_at = gate.index('if stage == "restore":')
        else_at = gate.index("else:", restore_at)
        restore_only = gate[restore_at:else_at]
        self.assertNotIn("dedicated_counts() != dedicated_before", restore_only)
        self.assertIn("restore_abort_metrics", restore_only)
        self.assertNotIn("restore_abort_ids_removed = 1", source)

    def test_maintenance_injects_after_first_delete_and_rolls_back_snapshot(
        self,
    ) -> None:
        from infra.f1.material_rag_backup_restore import (
            RestoreMaintenanceError,
            run_restore_maintenance,
        )

        source = (
            ROOT / "infra/f1/material-rag/restore_maintenance.py"
        ).read_text(encoding="utf-8")
        self.assertIn("inject_failure", source)
        self.assertIn("MAINTENANCE_INJECTED_FAILURE", source)
        self.assertLess(
            source.index("DELETE FROM f1.material_rag_job"),
            source.index("MAINTENANCE_INJECTED_FAILURE"),
        )
        connection = _MaintenanceSnapshotConnection()
        before = connection.logical_snapshot()
        with self.assertRaises(RestoreMaintenanceError) as raised:
            run_restore_maintenance(connection, inject_failure=True)
        self.assertEqual(raised.exception.code, "MAINTENANCE_INJECTED_FAILURE")
        self.assertEqual(getattr(raised.exception, "mutation_observed", 0), 1)
        self.assertGreaterEqual(connection.job_deletes, 1)
        self.assertTrue(connection.rolled_back)
        self.assertFalse(connection.committed)
        self.assertEqual(connection.logical_snapshot(), before)

    def test_rebuild_injects_after_first_new_job_or_remote_mutation(self) -> None:
        from infra.f1.material_rag_backup_restore import BackupRestoreStack

        source = inspect.getsource(BackupRestoreStack.rebuild_from_minio)
        inject_at = source.index('"REBUILD_INJECTED_FAILURE"')
        enqueue_at = source.index("enqueue_job(")
        self.assertLess(enqueue_at, inject_at)
        self.assertNotEqual(source.find("observe_stage_mutation("), -1)

    def test_restart_injects_after_core_stop(self) -> None:
        from infra.f1.material_rag_backup_restore import BackupRestoreStack

        source = inspect.getsource(BackupRestoreStack.restart_core)
        inject_at = source.index('"RESTART_INJECTED_FAILURE"')
        stop_at = source.index('"stop"')
        self.assertLess(stop_at, inject_at)
        self.assertNotEqual(source.find("observe_stage_mutation("), -1)

    def test_cleanup_without_mutation_observed_is_hardcoded(self) -> None:
        from infra.f1.material_rag_backup_restore import (
            CHECK_PAYLOAD_KEYS,
            validate_check_payload,
        )

        for key in (
            "restore_mutation_observed",
            "maintenance_mutation_observed",
            "rebuild_mutation_observed",
            "restart_mutation_observed",
        ):
            self.assertIn(key, CHECK_PAYLOAD_KEYS)
        localctl = load_localctl()
        validator = localctl._validate_material_rag_backup_restore_payload
        pairs = (
            ("restore_failure_cleanup", "restore_mutation_observed"),
            ("maintenance_failure_cleanup", "maintenance_mutation_observed"),
            ("rebuild_failure_cleanup", "rebuild_mutation_observed"),
            ("restart_failure_cleanup", "restart_mutation_observed"),
        )
        for cleanup_key, observed_key in pairs:
            payload = self._valid_payload()
            payload[cleanup_key] = 1
            payload[observed_key] = 0
            with self.assertRaises(BackupRestoreError) as raised:
                validate_check_payload(payload)
            self.assertEqual(
                raised.exception.code, "CHECK_PAYLOAD_HARDCODED", cleanup_key
            )
            with self.assertRaises(localctl.LocalError) as local_raised:
                validator(payload)
            self.assertEqual(str(local_raised.exception), "CHECK_PAYLOAD_HARDCODED")

    def _abort_fixture(self, parent: Path):
        recovery = load_restore_recovery()
        package = write_valid_package(parent)
        old_pg = labeled("volume", hid("old-pg"))
        old_mn = labeled("volume", hid("old-mn"))
        old_ct = labeled("container", hid("old-ct"))
        old_net = labeled("network", hid("old-net"))
        new_pg = labeled("volume", hid("new-pg"))
        new_mn = labeled("volume", hid("new-mn"))
        new_ct = labeled("container", hid("new-ct"))
        shared = labeled(
            "volume",
            hid("shared"),
            {
                "io.anhuan.parent-project-id": "c" * 32,
                "io.anhuan.project-id": "d" * 32,
                "io.anhuan.scope": "anhuan-f1",
            },
            foreign=True,
        )
        saved = [old_pg, old_mn, old_ct, old_net]
        live = [new_pg, new_mn, new_ct, old_net]
        world = IdentityWorld([new_pg, new_mn, new_ct, old_net, shared])
        return recovery, package, saved, live, world, new_pg, new_mn, new_ct, shared

    def test_same_count_volume_swap_is_not_cleanup_success(self) -> None:
        recovery = load_restore_recovery()
        with tempfile.TemporaryDirectory() as raw:
            package = write_valid_package(Path(raw))
            saved = [labeled("volume", hid("old-pg")), labeled("volume", hid("old-mn"))]
            live = [labeled("volume", hid("new-pg")), labeled("volume", hid("new-mn"))]
            world = IdentityWorld(live)
            self.assertEqual(recovery.count_only_same(saved, live), 1)
            result = recovery.abort_new_restore_resources(
                saved=saved,
                live=live,
                scope=SCOPE,
                project_id=PROJECT_ID,
                parent_project_id=PARENT_ID,
                destroyer=world,
                package_check=lambda: verify_package(package, **expected()),
            )
            self.assertEqual(result["same_count"], 1)
            self.assertEqual(result["same_count_swap_observed"], 1)
            self.assertEqual(result["new_volume_count"], 2)
            self.assertEqual(result["new_container_count"], 0)
            self.assertEqual(result["deleted"], 2)
            self.assertEqual(result["deleted"], result["new_volume_count"] + result["new_container_count"])
            self.assertIsNone(world.inspect("volume", hid("new-pg")))
            self.assertIsNone(world.inspect("volume", hid("new-mn")))
            verify_package(package, **expected())

    def test_restore_abort_deletes_exact_new_ids_after_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            recovery, package, saved, live, world, new_pg, new_mn, new_ct, shared = (
                self._abort_fixture(Path(raw))
            )
            result = recovery.abort_new_restore_resources(
                saved=saved,
                live=live,
                scope=SCOPE,
                project_id=PROJECT_ID,
                parent_project_id=PARENT_ID,
                destroyer=world,
                package_check=lambda: verify_package(package, **expected()),
            )
            self.assertEqual(result["rebuild_started"], 0)
            self.assertEqual(result["new_volume_count"], 2)
            self.assertEqual(result["new_container_count"], 1)
            self.assertEqual(result["deleted"], 3)
            self.assertEqual(
                result["deleted"],
                result["new_volume_count"] + result["new_container_count"],
            )
            self.assertEqual(result["remaining_abort_id_count"], 0)
            self.assertEqual(result["package_reverified"], 1)
            self.assertEqual(world.rebuild_started, 0)
            self.assertIsNone(world.inspect("volume", new_pg["id"]))
            self.assertIsNone(world.inspect("volume", new_mn["id"]))
            self.assertIsNone(world.inspect("container", new_ct["id"]))
            self.assertIsNotNone(world.inspect("network", hid("old-net")))
            self.assertIsNotNone(world.inspect("volume", shared["id"]))
            self.assertEqual(
                set(world.destroyed),
                {
                    ("volume", new_pg["id"]),
                    ("volume", new_mn["id"]),
                    ("container", new_ct["id"]),
                },
            )
            verify_package(package, **expected())

    def test_restore_abort_wrong_label_refuses_zero_delete(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            recovery, package, saved, live, world, new_pg, new_mn, new_ct, shared = (
                self._abort_fixture(Path(raw))
            )
            bad = dict(new_pg)
            bad["labels"] = {**owned_labels(), "io.anhuan.scope": "other-scope"}
            world.inspect_overrides[("volume", new_pg["id"])] = bad
            with self.assertRaises(recovery.RestoreRecoveryError) as raised:
                recovery.abort_new_restore_resources(
                    saved=saved,
                    live=live,
                    scope=SCOPE,
                    project_id=PROJECT_ID,
                    parent_project_id=PARENT_ID,
                    destroyer=world,
                    package_check=lambda: verify_package(package, **expected()),
                )
            self.assertEqual(raised.exception.code, "RESOURCE_LABEL_MISMATCH")
            self.assertEqual(world.destructive_started, 0)
            self.assertEqual(world.destroyed, ())
            self.assertIsNotNone(world.inspect("volume", new_pg["id"]))
            self.assertIsNotNone(world.inspect("volume", new_mn["id"]))
            self.assertIsNotNone(world.inspect("container", new_ct["id"]))
            self.assertIsNotNone(world.inspect("volume", shared["id"]))

    def test_project_enum_wrong_label_is_visible_and_zero_delete(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            recovery, package, saved, live, world, new_pg, new_mn, new_ct, shared = (
                self._abort_fixture(Path(raw))
            )
            wrong = labeled(
                "volume",
                hid("wrong-label-vol"),
                {**owned_labels(), "io.anhuan.scope": "other-scope"},
            )
            world._items[("volume", wrong["id"])] = wrong
            with self.assertRaises(recovery.RestoreRecoveryError) as raised:
                recovery.abort_new_restore_resources(
                    saved=saved,
                    live=live,
                    scope=SCOPE,
                    project_id=PROJECT_ID,
                    parent_project_id=PARENT_ID,
                    destroyer=world,
                    package_check=lambda: verify_package(package, **expected()),
                )
            self.assertEqual(raised.exception.code, "RESOURCE_LABEL_MISMATCH")
            self.assertEqual(world.destroyed, ())
            self.assertEqual(world.destructive_started, 0)
            self.assertIsNotNone(world.inspect("volume", new_pg["id"]))
            self.assertIsNotNone(world.inspect("volume", new_mn["id"]))
            self.assertIsNotNone(world.inspect("container", new_ct["id"]))
            self.assertIsNotNone(world.inspect("volume", wrong["id"]))
            self.assertIsNotNone(world.inspect("volume", shared["id"]))
            verify_package(package, **expected())

    def test_restore_abort_wrong_id_refuses_zero_delete(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            recovery, package, saved, live, world, new_pg, new_mn, new_ct, shared = (
                self._abort_fixture(Path(raw))
            )
            swapped = dict(new_pg)
            swapped["id"] = hid("other-id")
            world.inspect_overrides[("volume", new_pg["id"])] = swapped
            with self.assertRaises(recovery.RestoreRecoveryError) as raised:
                recovery.abort_new_restore_resources(
                    saved=saved,
                    live=live,
                    scope=SCOPE,
                    project_id=PROJECT_ID,
                    parent_project_id=PARENT_ID,
                    destroyer=world,
                    package_check=lambda: verify_package(package, **expected()),
                )
            self.assertEqual(raised.exception.code, "RESOURCE_ID_MISMATCH")
            self.assertEqual(world.destroyed, ())
            self.assertIsNotNone(world.inspect("volume", new_pg["id"]))
            self.assertIsNotNone(world.inspect("volume", new_mn["id"]))

    def test_restore_abort_extra_labeled_resource_refuses_zero_delete(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            recovery, package, saved, live, world, new_pg, new_mn, new_ct, shared = (
                self._abort_fixture(Path(raw))
            )
            extra = labeled("volume", hid("extra-vol"))
            world._items[("volume", extra["id"])] = extra
            with self.assertRaises(recovery.RestoreRecoveryError) as raised:
                recovery.abort_new_restore_resources(
                    saved=saved,
                    live=live,
                    scope=SCOPE,
                    project_id=PROJECT_ID,
                    parent_project_id=PARENT_ID,
                    destroyer=world,
                    package_check=lambda: verify_package(package, **expected()),
                )
            self.assertEqual(raised.exception.code, "RESOURCE_UNEXPECTED_EXTRA")
            self.assertEqual(world.destroyed, ())
            self.assertIsNotNone(world.inspect("volume", extra["id"]))
            self.assertIsNotNone(world.inspect("volume", new_pg["id"]))

    def test_restore_abort_keeps_verifiable_package(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            recovery, package, saved, live, world, *_rest = self._abort_fixture(Path(raw))
            recovery.abort_new_restore_resources(
                saved=saved,
                live=live,
                scope=SCOPE,
                project_id=PROJECT_ID,
                parent_project_id=PARENT_ID,
                destroyer=world,
                package_check=lambda: verify_package(package, **expected()),
            )
            verify_package(package, **expected())
            self.assertTrue((package / "database.dump").is_file())
            self.assertTrue((package / "manifest.json").is_file())

    def test_next_restore_prepares_empty_core_not_abort_scene(self) -> None:
        recovery = load_restore_recovery()
        leftover = hid("new-pg")
        with self.assertRaises(recovery.RestoreRecoveryError) as reused:
            recovery.prepare_empty_core(
                live=[labeled("volume", leftover)],
                abort_new_ids=(leftover,),
            )
        self.assertEqual(reused.exception.code, "RESTORE_CORE_REUSED_ABORT_SCENE")
        prepared = recovery.prepare_empty_core(
            live=[labeled("network", hid("old-net"))],
            abort_new_ids=(leftover,),
        )
        self.assertEqual(prepared["prepared_empty"], 1)
        self.assertEqual(prepared["rebuild_started"], 0)

    def test_journal_mode_owner_and_rejects_symlink_hardlink(self) -> None:
        recovery = load_restore_recovery()
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw) / "journal-dir"
            parent.mkdir(mode=0o700)
            parent.chmod(0o700)
            package = write_valid_package(Path(raw) / "pkg-root")
            manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
            path = parent / "restore.journal"
            recovery.write_journal(
                path,
                journal_document(
                    dump=manifest["db_dump_sha256"],
                    tree=manifest["minio_tree_sha256"],
                    resources=[{"kind": "volume", "id": hid("old-pg")}],
                ),
            )
            info = path.lstat()
            self.assertTrue(stat.S_ISREG(info.st_mode))
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
            self.assertEqual(info.st_nlink, 1)
            self.assertEqual(info.st_uid, os.geteuid())
            self.assertEqual(stat.S_IMODE(parent.lstat().st_mode), 0o700)
            linked = parent / "restore.link"
            os.symlink(path, linked)
            with self.assertRaises(recovery.RestoreRecoveryError) as raised:
                recovery.read_journal(linked)
            self.assertEqual(raised.exception.code, "JOURNAL_SYMLINK_REJECTED")
            hard = parent / "restore.hard"
            os.link(path, hard)
            with self.assertRaises(recovery.RestoreRecoveryError) as hard_raised:
                recovery.read_journal(path)
            self.assertEqual(hard_raised.exception.code, "JOURNAL_HARDLINK_REJECTED")
            os.unlink(hard)
            path.chmod(0o644)
            with self.assertRaises(recovery.RestoreRecoveryError) as mode_raised:
                recovery.read_journal(path)
            self.assertEqual(mode_raised.exception.code, "JOURNAL_MODE_INVALID")
            class OtherOwner:
                st_mode = stat.S_IFREG | 0o600
                st_nlink = 1
                st_uid = os.geteuid() + 1
            with self.assertRaises(recovery.RestoreRecoveryError) as owner_raised:
                recovery.accept_journal_lstat(OtherOwner())
            self.assertEqual(owner_raised.exception.code, "JOURNAL_OWNER_INVALID")

    def test_journal_field_closed_set_and_rejects_secrets(self) -> None:
        recovery = load_restore_recovery()
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            parent.chmod(0o700)
            package = write_valid_package(parent / "pkg")
            manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
            document = journal_document(
                dump=manifest["db_dump_sha256"],
                tree=manifest["minio_tree_sha256"],
                resources=[{"kind": "volume", "id": hid("old-pg")}],
            )
            encoded = json.dumps(document, sort_keys=True).lower()
            self.assertNotIn("password", encoded)
            self.assertNotIn("dsn", encoded)
            self.assertNotIn("/users/", encoded)
            self.assertNotIn("/private/", encoded)
            extra = dict(document)
            extra["token"] = "x"
            with self.assertRaises(recovery.RestoreRecoveryError) as raised:
                recovery.validate_journal_document(extra)
            self.assertEqual(raised.exception.code, "JOURNAL_FIELDS_INVALID")
            secret = dict(document)
            secret["f1_head"] = "secret"
            with self.assertRaises(recovery.RestoreRecoveryError) as secret_raised:
                recovery.validate_journal_document(secret)
            self.assertIn(
                secret_raised.exception.code,
                {"JOURNAL_SECRET_REJECTED", "JOURNAL_F1_HEAD_INVALID"},
            )

    def test_journal_legal_stage_recovery_zero_rebuild(self) -> None:
        recovery = load_restore_recovery()
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw) / "journal-dir"
            parent.mkdir(mode=0o700)
            parent.chmod(0o700)
            package = write_valid_package(Path(raw) / "pkg")
            manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
            path = parent / "restore.journal"
            old = labeled("volume", hid("old-pg"))
            new = labeled("volume", hid("new-pg"))
            recovery.write_journal(
                path,
                journal_document(
                    dump=manifest["db_dump_sha256"],
                    tree=manifest["minio_tree_sha256"],
                    resources=[{"kind": "volume", "id": old["id"]}],
                ),
            )
            self.assertEqual(
                recovery.advance_stage("PREPARED", "VOLUMES_REPLACED"),
                "VOLUMES_REPLACED",
            )
            with self.assertRaises(recovery.RestoreRecoveryError) as jumped:
                recovery.advance_stage("PREPARED", "DB_RESTORED")
            self.assertEqual(jumped.exception.code, "JOURNAL_STAGE_JUMP")
            world = IdentityWorld([new])
            result = recovery.recover_from_journal(
                path,
                expected_scope=SCOPE,
                expected_project_id=PROJECT_ID,
                expected_parent_project_id=PARENT_ID,
                expected_dump_sha256=manifest["db_dump_sha256"],
                expected_tree_sha256=manifest["minio_tree_sha256"],
                live=[new],
                destroyer=world,
                package_check=lambda: verify_package(package, **expected()),
            )
            self.assertEqual(result["rebuild_started"], 0)
            self.assertIsNone(world.inspect("volume", new["id"]))
            self.assertEqual(recovery.read_journal(path)["stage"], "RECOVERED")
            verify_package(package, **expected())

    def test_journal_fail_closed_truncation_unknown_field_wrong_project(self) -> None:
        recovery = load_restore_recovery()
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            parent.chmod(0o700)
            package = write_valid_package(parent / "pkg")
            manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
            path = parent / "restore.journal"
            old = labeled("volume", hid("old-pg"))
            new = labeled("volume", hid("new-pg"))
            recovery.write_journal(
                path,
                journal_document(
                    dump=manifest["db_dump_sha256"],
                    tree=manifest["minio_tree_sha256"],
                    resources=[{"kind": "volume", "id": old["id"]}],
                ),
            )
            truncated = parent / "truncated.journal"
            truncated.write_bytes(b'{"schema":')
            truncated.chmod(0o600)
            with self.assertRaises(recovery.RestoreRecoveryError) as trunc:
                recovery.read_journal(truncated)
            self.assertEqual(trunc.exception.code, "JOURNAL_TRUNCATED")
            world = IdentityWorld([new])
            with self.assertRaises(recovery.RestoreRecoveryError) as project:
                recovery.recover_from_journal(
                    path,
                    expected_scope=SCOPE,
                    expected_project_id="c" * 32,
                    expected_parent_project_id=PARENT_ID,
                    expected_dump_sha256=manifest["db_dump_sha256"],
                    expected_tree_sha256=manifest["minio_tree_sha256"],
                    live=[new],
                    destroyer=world,
                    package_check=lambda: verify_package(package, **expected()),
                )
            self.assertEqual(project.exception.code, "JOURNAL_PROJECT_MISMATCH")
            self.assertEqual(world.destroyed, ())
            self.assertIsNotNone(world.inspect("volume", new["id"]))
            extra = json.loads(path.read_text(encoding="ascii"))
            extra["unknown"] = 1
            path.write_text(json.dumps(extra), encoding="ascii")
            path.chmod(0o600)
            with self.assertRaises(recovery.RestoreRecoveryError) as unknown:
                recovery.read_journal(path)
            self.assertEqual(unknown.exception.code, "JOURNAL_FIELDS_INVALID")

    def test_journal_wrong_label_and_mode_zero_delete(self) -> None:
        recovery = load_restore_recovery()
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            parent.chmod(0o700)
            package = write_valid_package(parent / "pkg")
            manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
            path = parent / "restore.journal"
            old = labeled("volume", hid("old-pg"))
            new = labeled("volume", hid("new-pg"))
            recovery.write_journal(
                path,
                journal_document(
                    dump=manifest["db_dump_sha256"],
                    tree=manifest["minio_tree_sha256"],
                    resources=[{"kind": "volume", "id": old["id"]}],
                ),
            )
            world = IdentityWorld([new])
            bad = dict(new)
            bad["labels"] = {**owned_labels(), "io.anhuan.project-id": "c" * 32}
            world.inspect_overrides[("volume", new["id"])] = bad
            with self.assertRaises(recovery.RestoreRecoveryError) as raised:
                recovery.recover_from_journal(
                    path,
                    expected_scope=SCOPE,
                    expected_project_id=PROJECT_ID,
                    expected_parent_project_id=PARENT_ID,
                    expected_dump_sha256=manifest["db_dump_sha256"],
                    expected_tree_sha256=manifest["minio_tree_sha256"],
                    live=[new],
                    destroyer=world,
                    package_check=lambda: verify_package(package, **expected()),
                )
            self.assertEqual(raised.exception.code, "RESOURCE_LABEL_MISMATCH")
            self.assertEqual(world.destroyed, ())
            self.assertIsNotNone(world.inspect("volume", new["id"]))

    def test_fresh_process_journal_recovery_zero_rebuild(self) -> None:
        recovery = load_restore_recovery()
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw) / "journal-dir"
            parent.mkdir(mode=0o700)
            parent.chmod(0o700)
            package = write_valid_package(Path(raw) / "pkg")
            manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
            path = parent / "restore.journal"
            old_id = hid("old-pg")
            new_id = hid("new-pg")
            recovery.write_journal(
                path,
                journal_document(
                    stage="DB_RESTORED",
                    dump=manifest["db_dump_sha256"],
                    tree=manifest["minio_tree_sha256"],
                    resources=[{"kind": "volume", "id": old_id}],
                ),
            )
            script = r"""
import json, os, sys
from pathlib import Path
sys.path[:0] = os.environ["PYTHONPATH"].split(os.pathsep)
import importlib.machinery, importlib.util, hashlib
root = Path(os.environ["REPO_ROOT"])
loader = importlib.machinery.SourceFileLoader("rr", str(root / "infra/f1/material-rag/restore_recovery.py"))
spec = importlib.util.spec_from_loader(loader.name, loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)
from infra.f1.material_rag_backup_restore import verify_package
class World:
    def __init__(self, item):
        self._item = item
        self.destroyed = ()
        self.destructive_started = 0
        self.rebuild_started = 0
    def inspect(self, kind, resource_id):
        if self._item and self._item["kind"]==kind and self._item["id"]==resource_id:
            return self._item
        return None
    def list_labeled(self, scope, project_id, parent_project_id):
        return [self._item] if self._item else []
    def destroy(self, targets):
        self.destroyed = targets
        self._item = None
labels = {
    "io.anhuan.parent-project-id": os.environ["PARENT_ID"],
    "io.anhuan.project-id": os.environ["PROJECT_ID"],
    "io.anhuan.scope": os.environ["SCOPE"],
}
live = {"id": os.environ["NEW_ID"], "kind": "volume", "labels": labels, "handle": os.environ["NEW_ID"]}
world = World(live)
result = mod.recover_from_journal(
    Path(os.environ["JOURNAL"]),
    expected_scope=os.environ["SCOPE"],
    expected_project_id=os.environ["PROJECT_ID"],
    expected_parent_project_id=os.environ["PARENT_ID"],
    expected_dump_sha256=os.environ["DUMP"],
    expected_tree_sha256=os.environ["TREE"],
    live=[live],
    destroyer=world,
    package_check=lambda: verify_package(Path(os.environ["PACKAGE"]), expected_project_id=os.environ["PROJECT_ID"], expected_parent_project_id=os.environ["PARENT_ID"], expected_database=os.environ["DATABASE"], expected_scope=os.environ["SCOPE"]),
)
print(json.dumps({"deleted": result["deleted"], "rebuild_started": result["rebuild_started"], "gone": world.inspect("volume", os.environ["NEW_ID"]) is None}))
"""
            env = os.environ.copy()
            env.update(
                {
                    "PYTHONPATH": f"{ROOT / 'src'}{os.pathsep}{ROOT}",
                    "REPO_ROOT": str(ROOT),
                    "JOURNAL": str(path),
                    "PACKAGE": str(package),
                    "PROJECT_ID": PROJECT_ID,
                    "PARENT_ID": PARENT_ID,
                    "SCOPE": SCOPE,
                    "DATABASE": DATABASE,
                    "DUMP": manifest["db_dump_sha256"],
                    "TREE": manifest["minio_tree_sha256"],
                    "NEW_ID": new_id,
                    "F1_KEYCLOAK_ISSUER_URL": "http://material-rag.invalid/realms/anhuan",
                }
            )
            completed = subprocess.run(
                [sys.executable, "-B", "-c", script],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout.strip().splitlines()[-1])
            self.assertEqual(payload["rebuild_started"], 0)
            self.assertTrue(payload["gone"])
            self.assertEqual(payload["deleted"], 1)
            self.assertEqual(recovery.read_journal(path)["stage"], "RECOVERED")
            crash_key = "MATERIAL_RAG_RESTORE_CRASH_AFTER"
            previous = os.environ.get(crash_key)
            os.environ[crash_key] = "DB_RESTORED"
            try:
                with self.assertRaises(recovery.RestoreRecoveryError) as crashed:
                    recovery.maybe_crash("DB_RESTORED")
                self.assertEqual(crashed.exception.code, "RESTORE_CRASH_INJECTED")
            finally:
                if previous is None:
                    os.environ.pop(crash_key, None)
                else:
                    os.environ[crash_key] = previous

    def _parser_commands(self, parser) -> set[str]:
        for action in parser._actions:
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict) and "backup" in choices:
                return set(choices)
        raise AssertionError("localctl subparsers missing")

    def _valid_crash_payload(self) -> dict:
        from infra.f1.material_rag_backup_restore import CRASH_PAYLOAD_KEYS, CRASH_SCHEMA

        return {key: 0 for key in CRASH_PAYLOAD_KEYS} | {
            "schema": CRASH_SCHEMA,
            "f1_head": F1_HEAD,
            "hard_death_signal": 9,
            "fresh_recovery_process": 1,
            "tamper_rejected": 1,
            "tamper_zero_delete": 1,
            "tamper_reason_verified": 1,
            "new_volume": 2,
            "new_container": 3,
            "deleted": 5,
            "remaining": 0,
            "fallback_cleanup_used": 0,
            "stable_zero_observations": 2,
            "package_reverified": 1,
            "rebuild_started": 0,
            "journal_recovered": 1,
            "shared_match": 1,
            "skipped": 0,
            "dedicated_c": 0,
            "dedicated_v": 0,
            "dedicated_n": 0,
        }

    def test_crash_check_parser_is_isolated_from_user_restore(self) -> None:
        localctl = load_localctl()
        parser = localctl._parser()
        commands = self._parser_commands(parser)
        self.assertIn("material-rag-backup-restore-crash-check", commands)
        self.assertIn("material-rag-backup-restore-check", commands)
        self.assertIn("restore", commands)
        parsed = parser.parse_args(["material-rag-backup-restore-crash-check"])
        self.assertEqual(parsed.command, "material-rag-backup-restore-crash-check")
        check = parser.parse_args(["material-rag-backup-restore-check"])
        self.assertEqual(check.command, "material-rag-backup-restore-check")
        restore = parser.parse_args(["restore", "--confirm-local-data"])
        self.assertEqual(restore.command, "restore")
        self.assertTrue(restore.confirm_local_data)

    def test_exception_and_finally_cleanup_are_not_hard_death(self) -> None:
        recovery = load_restore_recovery()
        crash_key = "MATERIAL_RAG_RESTORE_CRASH_AFTER"
        previous = os.environ.get(crash_key)
        os.environ[crash_key] = "DB_RESTORED"
        try:
            with self.assertRaises(recovery.RestoreRecoveryError) as raised:
                recovery.maybe_crash("DB_RESTORED")
            self.assertEqual(raised.exception.code, "RESTORE_CRASH_INJECTED")
        finally:
            if previous is None:
                os.environ.pop(crash_key, None)
            else:
                os.environ[crash_key] = previous
        gate = (ROOT / "infra/f1/material_rag_backup_restore.py").read_text(
            encoding="utf-8"
        )
        probe = (
            ROOT / "infra/f1/material-rag/crash_recovery_probe.py"
        ).read_text(encoding="utf-8")
        self.assertIn("signal.SIGKILL", gate)
        self.assertIn("os.kill(", gate)
        self.assertIn("run_crash_machine_gate", gate)
        self.assertNotIn("RESTORE_CRASH_INJECTED", probe)
        self.assertIn("finally:", probe)
        self.assertIn("stack.stop()", probe)
        pause = inspect.getsource(
            __import__(
                "infra.f1.material_rag_backup_restore",
                fromlist=["BackupRestoreStack"],
            ).BackupRestoreStack._write_restore_journal
        )
        self.assertIn("MATERIAL_RAG_RESTORE_WAIT_AFTER", pause)
        self.assertLess(pause.index("write_journal"), pause.index("WAIT_AFTER"))

    def test_crash_and_recovery_must_use_distinct_pids(self) -> None:
        probe_path = ROOT / "infra/f1/material-rag/crash_recovery_probe.py"
        self.assertTrue(probe_path.is_file())
        probe = probe_path.read_text(encoding="utf-8")
        gate = inspect.getsource(
            __import__(
                "infra.f1.material_rag_backup_restore",
                fromlist=["run_crash_machine_gate"],
            ).run_crash_machine_gate
        )
        self.assertIn('if __name__ == "__main__"', probe)
        self.assertIn("crash_recovery_probe.py", gate)
        self.assertIn("subprocess.Popen", gate)
        self.assertIn('"recover"', gate)
        self.assertIn("fresh_recovery_process", gate)
        self.assertIn("child.pid", gate)
        self.assertIn("recover.pid", gate)
        self.assertIn("!=", gate)

    def test_sigkill_requires_journal_stage_db_restored(self) -> None:
        gate = inspect.getsource(
            __import__(
                "infra.f1.material_rag_backup_restore",
                fromlist=["run_crash_machine_gate"],
            ).run_crash_machine_gate
        )
        self.assertIn('"DB_RESTORED"', gate)
        self.assertIn("signal.SIGKILL", gate)
        self.assertLess(gate.index('"DB_RESTORED"'), gate.index("SIGKILL"))
        self.assertIn("hard_death_signal", gate)
        journal = inspect.getsource(
            __import__(
                "infra.f1.material_rag_backup_restore",
                fromlist=["BackupRestoreStack"],
            ).BackupRestoreStack._write_restore_journal
        )
        self.assertIn('"DB_RESTORED"', journal)
        self.assertIn("MATERIAL_RAG_RESTORE_WAIT_AFTER", journal)

    def test_wrong_project_or_label_crash_recovery_zero_delete(self) -> None:
        recovery = load_restore_recovery()
        probe = (
            ROOT / "infra/f1/material-rag/crash_recovery_probe.py"
        ).read_text(encoding="utf-8")
        gate = inspect.getsource(
            __import__(
                "infra.f1.material_rag_backup_restore",
                fromlist=["run_crash_machine_gate"],
            ).run_crash_machine_gate
        )
        self.assertIn("tamper", probe)
        self.assertIn("RESOURCE_LABEL_MISMATCH", probe)
        self.assertIn("tamper_zero_delete", gate)
        self.assertIn("tamper_rejected", gate)
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            parent.chmod(0o700)
            package = write_valid_package(parent / "pkg")
            manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
            path = parent / "restore.journal"
            old = labeled("volume", hid("old-pg"))
            new_pg = labeled("volume", hid("new-pg"))
            new_mn = labeled("volume", hid("new-mn"))
            new_a = labeled("container", hid("new-ct-a"))
            new_b = labeled("container", hid("new-ct-b"))
            new_c = labeled("container", hid("new-ct-c"))
            recovery.write_journal(
                path,
                journal_document(
                    stage="DB_RESTORED",
                    dump=manifest["db_dump_sha256"],
                    tree=manifest["minio_tree_sha256"],
                    resources=[{"kind": "volume", "id": old["id"]}],
                ),
            )
            live = [new_pg, new_mn, new_a, new_b, new_c]
            world = IdentityWorld(list(live))
            with self.assertRaises(recovery.RestoreRecoveryError) as project:
                recovery.recover_from_journal(
                    path,
                    expected_scope=SCOPE,
                    expected_project_id="c" * 32,
                    expected_parent_project_id=PARENT_ID,
                    expected_dump_sha256=manifest["db_dump_sha256"],
                    expected_tree_sha256=manifest["minio_tree_sha256"],
                    live=live,
                    destroyer=world,
                    package_check=lambda: verify_package(package, **expected()),
                )
            self.assertEqual(project.exception.code, "JOURNAL_PROJECT_MISMATCH")
            self.assertEqual(world.destroyed, ())
            for item in live:
                self.assertIsNotNone(world.inspect(item["kind"], item["id"]))
            bad = dict(new_pg)
            bad["labels"] = {**owned_labels(), "io.anhuan.project-id": "c" * 32}
            world.inspect_overrides[("volume", new_pg["id"])] = bad
            labeled_live = [bad, new_mn, new_a, new_b, new_c]
            with self.assertRaises(recovery.RestoreRecoveryError) as labels:
                recovery.recover_from_journal(
                    path,
                    expected_scope=SCOPE,
                    expected_project_id=PROJECT_ID,
                    expected_parent_project_id=PARENT_ID,
                    expected_dump_sha256=manifest["db_dump_sha256"],
                    expected_tree_sha256=manifest["minio_tree_sha256"],
                    live=labeled_live,
                    destroyer=world,
                    package_check=lambda: verify_package(package, **expected()),
                )
            self.assertEqual(labels.exception.code, "RESOURCE_LABEL_MISMATCH")
            self.assertEqual(world.destroyed, ())
            for item in live:
                self.assertIsNotNone(world.inspect(item["kind"], item["id"]))

    def test_post_stop_leftover_fallback_must_fail_gate(self) -> None:
        from infra.f1.material_rag_backup_restore import (
            CRASH_PAYLOAD_KEYS,
            apply_post_stop_fallback,
            observe_stable_zero,
            reject_fallback_cleanup,
            run_crash_machine_gate,
            validate_crash_payload,
        )

        invoked: list[str] = []

        def destroy_container() -> None:
            invoked.append("container")

        def destroy_volume() -> None:
            invoked.append("volume")

        def destroy_network() -> None:
            invoked.append("network")

        destroyers = (destroy_container, destroy_volume, destroy_network)
        used = apply_post_stop_fallback((1, 0, 0), destroyers)
        self.assertEqual(used, 1)
        self.assertEqual(invoked, ["container", "volume", "network"])
        with self.assertRaises(BackupRestoreError) as raised:
            reject_fallback_cleanup(used)
        self.assertEqual(raised.exception.code, "CRASH_FALLBACK_CLEANUP_USED")
        invoked.clear()
        self.assertEqual(apply_post_stop_fallback((0, 0, 0), destroyers), 0)
        self.assertEqual(invoked, [])
        reject_fallback_cleanup(0)
        self.assertEqual(
            observe_stable_zero((((0, 0, 0), 0.0), ((0, 0, 0), 0.5))),
            2,
        )
        with self.assertRaises(BackupRestoreError) as gap:
            observe_stable_zero((((0, 0, 0), 0.0), ((0, 0, 0), 0.49)))
        self.assertEqual(gap.exception.code, "CRASH_UNSTABLE_ZERO")
        with self.assertRaises(BackupRestoreError) as leftover:
            observe_stable_zero((((1, 0, 0), 0.0), ((0, 0, 0), 1.0)))
        self.assertEqual(leftover.exception.code, "CRASH_UNSTABLE_ZERO")
        self.assertIn("stable_zero_observations", CRASH_PAYLOAD_KEYS)
        payload = self._valid_crash_payload()
        payload["fallback_cleanup_used"] = 1
        with self.assertRaises(BackupRestoreError) as hard:
            validate_crash_payload(payload)
        self.assertEqual(hard.exception.code, "CRASH_PAYLOAD_HARDCODED")
        gate = inspect.getsource(run_crash_machine_gate)
        self.assertIn("apply_post_stop_fallback", gate)
        self.assertIn("reject_fallback_cleanup", gate)
        self.assertIn("observe_stable_zero", gate)
        self.assertLess(
            gate.index("apply_post_stop_fallback"),
            gate.index("reject_fallback_cleanup"),
        )
        self.assertLess(
            gate.index("reject_fallback_cleanup"),
            gate.index("return validate_crash_payload"),
        )
        self.assertNotIn("LOCAL_MATERIAL_RAG_CRASH_RECOVERY_OK", gate)

    def test_tamper_only_exact_label_mismatch_counts_as_rejected(self) -> None:
        from infra.f1.material_rag_backup_restore import (
            CRASH_PAYLOAD_KEYS,
            evaluate_tamper_probe,
            run_crash_machine_gate,
            validate_crash_payload,
        )

        accepted = evaluate_tamper_probe(
            returncode=2,
            stdout=b"",
            stderr=b"RESOURCE_LABEL_MISMATCH\n",
            remaining_abort_ids=5,
        )
        self.assertEqual(accepted["tamper_rejected"], 1)
        self.assertEqual(accepted["tamper_reason_verified"], 1)
        self.assertEqual(accepted["tamper_zero_delete"], 1)
        cases = (
            (0, b"", b"RESOURCE_LABEL_MISMATCH\n", 5),
            (1, b"", b"RESOURCE_LABEL_MISMATCH\n", 5),
            (2, b"{}\n", b"RESOURCE_LABEL_MISMATCH\n", 5),
            (2, b"", b"JOURNAL_PROJECT_MISMATCH\n", 5),
            (2, b"", b"RESOURCE_LABEL_MISMATCH\n", 4),
            (2, b"", b"RESOURCE_LABEL_MISMATCH", 5),
        )
        for returncode, stdout, stderr, remaining in cases:
            with self.subTest(
                returncode=returncode, stdout=stdout, stderr=stderr, remaining=remaining
            ):
                with self.assertRaises(BackupRestoreError) as raised:
                    evaluate_tamper_probe(
                        returncode=returncode,
                        stdout=stdout,
                        stderr=stderr,
                        remaining_abort_ids=remaining,
                    )
                self.assertEqual(raised.exception.code, "CRASH_TAMPER_INVALID")
        self.assertIn("tamper_reason_verified", CRASH_PAYLOAD_KEYS)
        payload = self._valid_crash_payload()
        payload["tamper_reason_verified"] = 0
        with self.assertRaises(BackupRestoreError) as hard:
            validate_crash_payload(payload)
        self.assertEqual(hard.exception.code, "CRASH_PAYLOAD_HARDCODED")
        gate = inspect.getsource(run_crash_machine_gate)
        self.assertIn("evaluate_tamper_probe", gate)
        self.assertNotIn("if tamper.returncode == 0", gate)
        localctl = load_localctl()
        validator = localctl._validate_material_rag_crash_payload
        good = self._valid_crash_payload()
        validator(good)
        missing_reason = dict(good)
        del missing_reason["tamper_reason_verified"]
        with self.assertRaises(localctl.LocalError):
            validator(missing_reason)
        missing_stable = dict(good)
        del missing_stable["stable_zero_observations"]
        with self.assertRaises(localctl.LocalError):
            validator(missing_stable)

    def test_correct_fresh_recovery_deletes_two_volumes_three_containers(self) -> None:
        from infra.f1.material_rag_backup_restore import (
            CRASH_PAYLOAD_KEYS,
            validate_crash_payload,
        )

        required = {
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
        }
        self.assertEqual(set(CRASH_PAYLOAD_KEYS), required)
        payload = self._valid_crash_payload()
        self.assertEqual(payload["new_volume"], 2)
        self.assertEqual(payload["new_container"], 3)
        self.assertEqual(payload["deleted"], 5)
        self.assertEqual(payload["remaining"], 0)
        self.assertEqual(payload["rebuild_started"], 0)
        self.assertEqual(payload["package_reverified"], 1)
        self.assertEqual(payload["journal_recovered"], 1)
        self.assertEqual(payload["fallback_cleanup_used"], 0)
        validate_crash_payload(payload)
        gate = inspect.getsource(
            __import__(
                "infra.f1.material_rag_backup_restore",
                fromlist=["run_crash_machine_gate"],
            ).run_crash_machine_gate
        )
        self.assertIn("new_volume", gate)
        self.assertIn("new_container", gate)
        self.assertIn("recover_from_journal", gate)
        self.assertIn("fallback_cleanup_used", gate)
        probe = (
            ROOT / "infra/f1/material-rag/crash_recovery_probe.py"
        ).read_text(encoding="utf-8")
        self.assertIn("recover_from_journal", probe)
        self.assertNotIn("docker rm", probe)
        self.assertNotIn("volume rm", probe)

    def test_validate_crash_payload_rejects_missing_extra_and_hardcoded(self) -> None:
        from infra.f1.material_rag_backup_restore import validate_crash_payload

        payload = self._valid_crash_payload()
        validate_crash_payload(payload)
        missing = dict(payload)
        del missing["hard_death_signal"]
        with self.assertRaises(BackupRestoreError) as raised:
            validate_crash_payload(missing)
        self.assertEqual(raised.exception.code, "CRASH_PAYLOAD_KEYS_INVALID")
        extra = dict(payload)
        extra["bonus"] = 1
        with self.assertRaises(BackupRestoreError) as extra_raised:
            validate_crash_payload(extra)
        self.assertEqual(extra_raised.exception.code, "CRASH_PAYLOAD_KEYS_INVALID")
        hardcoded = dict(payload)
        hardcoded["fresh_recovery_process"] = 0
        with self.assertRaises(BackupRestoreError) as hard:
            validate_crash_payload(hardcoded)
        self.assertEqual(hard.exception.code, "CRASH_PAYLOAD_HARDCODED")
        signal_only = dict(payload)
        signal_only["hard_death_signal"] = 15
        with self.assertRaises(BackupRestoreError) as sig:
            validate_crash_payload(signal_only)
        self.assertEqual(sig.exception.code, "CRASH_PAYLOAD_HARDCODED")
        deleted = dict(payload)
        deleted["deleted"] = 4
        with self.assertRaises(BackupRestoreError) as count:
            validate_crash_payload(deleted)
        self.assertEqual(count.exception.code, "CRASH_PAYLOAD_HARDCODED")

    def test_localctl_rejects_missing_extra_and_hardcoded_crash_payload(self) -> None:
        localctl = load_localctl()
        validator = localctl._validate_material_rag_crash_payload
        payload = self._valid_crash_payload()
        validator(payload)
        with self.assertRaises(localctl.LocalError):
            validator({k: payload[k] for k in payload if k != "deleted"})
        extra = dict(payload)
        extra["token"] = "x"
        with self.assertRaises(localctl.LocalError):
            validator(extra)
        hardcoded = dict(payload)
        hardcoded["fresh_recovery_process"] = 0
        with self.assertRaises(localctl.LocalError):
            validator(hardcoded)

    def _valid_matrix_payload(self) -> dict:
        from infra.f1.material_rag_backup_restore import (
            MATRIX_PAYLOAD_KEYS,
            MATRIX_SCHEMA,
        )

        return {key: 0 for key in MATRIX_PAYLOAD_KEYS} | {
            "schema": MATRIX_SCHEMA,
            "f1_head": F1_HEAD,
            "stages_passed": 3,
            "hard_death_count": 3,
            "fresh_recovery_count": 3,
            "deleted_total": 15,
            "remaining_total": 0,
            "package_reverified_count": 3,
            "journal_recovered_count": 3,
            "rebuild_started_total": 0,
            "fallback_cleanup_total": 0,
            "stable_zero_observations_total": 6,
            "minio_replayed_identity_ok": 1,
            "shared_match": 1,
            "skipped": 0,
            "dedicated_c": 0,
            "dedicated_v": 0,
            "dedicated_n": 0,
        }

    def test_matrix_live_stages_exclude_prepared_and_require_sigkill(self) -> None:
        from infra.f1.material_rag_backup_restore import (
            LIVE_CRASH_STAGES,
            require_fresh_recovery_pids,
            require_hard_death_sigkill,
            require_live_crash_stage,
        )

        self.assertEqual(
            LIVE_CRASH_STAGES,
            ("VOLUMES_REPLACED", "DB_RESTORED", "MINIO_REPLAYED"),
        )
        self.assertNotIn("PREPARED", LIVE_CRASH_STAGES)
        with self.assertRaises(BackupRestoreError) as prepared:
            require_live_crash_stage("PREPARED")
        self.assertEqual(prepared.exception.code, "CRASH_WAIT_STAGE_INVALID")
        with self.assertRaises(BackupRestoreError) as unknown:
            require_live_crash_stage("RECOVERED")
        self.assertEqual(unknown.exception.code, "CRASH_WAIT_STAGE_INVALID")
        self.assertEqual(require_live_crash_stage("VOLUMES_REPLACED"), "VOLUMES_REPLACED")
        self.assertEqual(require_hard_death_sigkill(-9), 9)
        with self.assertRaises(BackupRestoreError) as injected:
            require_hard_death_sigkill(2)
        self.assertEqual(injected.exception.code, "HARD_DEATH_NOT_SIGKILL")
        with self.assertRaises(BackupRestoreError) as exception_exit:
            require_hard_death_sigkill(1)
        self.assertEqual(exception_exit.exception.code, "HARD_DEATH_NOT_SIGKILL")
        self.assertEqual(require_fresh_recovery_pids(11, 12), 1)
        with self.assertRaises(BackupRestoreError) as same_pid:
            require_fresh_recovery_pids(7, 7)
        self.assertEqual(same_pid.exception.code, "CRASH_RECOVERY_PID_COLLISION")
        probe = (
            ROOT / "infra/f1/material-rag/crash_recovery_probe.py"
        ).read_text(encoding="utf-8")
        self.assertIn("--stage", probe)
        self.assertIn("require_live_crash_stage", probe)

    def test_matrix_wrong_stage_missing_resources_and_keys_are_red(self) -> None:
        from infra.f1.material_rag_backup_restore import (
            MATRIX_PAYLOAD_KEYS,
            require_abort_resource_counts,
            require_journal_stage,
            validate_matrix_payload,
        )

        require_journal_stage("VOLUMES_REPLACED", "VOLUMES_REPLACED")
        with self.assertRaises(BackupRestoreError) as stage:
            require_journal_stage("PREPARED", "VOLUMES_REPLACED")
        self.assertEqual(stage.exception.code, "JOURNAL_STAGE_INVALID")
        require_abort_resource_counts(2, 3)
        with self.assertRaises(BackupRestoreError) as missing:
            require_abort_resource_counts(1, 3)
        self.assertEqual(missing.exception.code, "CRASH_NEW_RESOURCE_COUNT")
        with self.assertRaises(BackupRestoreError) as containers:
            require_abort_resource_counts(2, 2)
        self.assertEqual(containers.exception.code, "CRASH_NEW_RESOURCE_COUNT")
        payload = self._valid_matrix_payload()
        self.assertEqual(set(MATRIX_PAYLOAD_KEYS), set(payload))
        validate_matrix_payload(payload)
        missing_key = dict(payload)
        del missing_key["stages_passed"]
        with self.assertRaises(BackupRestoreError) as keys:
            validate_matrix_payload(missing_key)
        self.assertEqual(keys.exception.code, "MATRIX_PAYLOAD_KEYS_INVALID")
        extra = dict(payload)
        extra["bonus"] = 1
        with self.assertRaises(BackupRestoreError) as extra_raised:
            validate_matrix_payload(extra)
        self.assertEqual(extra_raised.exception.code, "MATRIX_PAYLOAD_KEYS_INVALID")
        hardcoded = dict(payload)
        hardcoded["stages_passed"] = 2
        with self.assertRaises(BackupRestoreError) as hard:
            validate_matrix_payload(hardcoded)
        self.assertEqual(hard.exception.code, "MATRIX_PAYLOAD_HARDCODED")

    def test_matrix_fallback_and_minio_tree_mismatch_are_red(self) -> None:
        from infra.f1.material_rag_backup_restore import (
            reject_fallback_cleanup,
            validate_matrix_payload,
            verify_minio_replayed_identity,
        )

        reject_fallback_cleanup(0)
        with self.assertRaises(BackupRestoreError) as fallback:
            reject_fallback_cleanup(1)
        self.assertEqual(fallback.exception.code, "CRASH_FALLBACK_CLEANUP_USED")
        tree = [
            {
                "body_sha256": "a" * 64,
                "bucket": "b" * 64,
                "key": "c" * 64,
                "size": 4,
            }
        ]
        self.assertEqual(verify_minio_replayed_identity(tree, tree), 1)
        mutated = [
            {
                "body_sha256": "d" * 64,
                "bucket": "b" * 64,
                "key": "c" * 64,
                "size": 4,
            }
        ]
        with self.assertRaises(BackupRestoreError) as body:
            verify_minio_replayed_identity(tree, mutated)
        self.assertEqual(body.exception.code, "MINIO_BODY_SHA_MISMATCH")
        payload = self._valid_matrix_payload()
        payload["fallback_cleanup_total"] = 1
        with self.assertRaises(BackupRestoreError) as hard:
            validate_matrix_payload(payload)
        self.assertEqual(hard.exception.code, "MATRIX_PAYLOAD_HARDCODED")
        payload = self._valid_matrix_payload()
        payload["minio_replayed_identity_ok"] = 0
        with self.assertRaises(BackupRestoreError) as minio:
            validate_matrix_payload(payload)
        self.assertEqual(minio.exception.code, "MATRIX_PAYLOAD_HARDCODED")

    def test_matrix_parser_payload_and_gate_lock_three_live_stages(self) -> None:
        from infra.f1.material_rag_backup_restore import (
            MATRIX_OK_TOKEN,
            run_crash_machine_gate,
            run_crash_matrix_gate,
            validate_matrix_payload,
        )

        localctl = load_localctl()
        parser = localctl._parser()
        commands = self._parser_commands(parser)
        self.assertIn("material-rag-backup-restore-crash-check", commands)
        self.assertIn("material-rag-backup-restore-crash-matrix-check", commands)
        parsed = parser.parse_args(["material-rag-backup-restore-crash-matrix-check"])
        self.assertEqual(parsed.command, "material-rag-backup-restore-crash-matrix-check")
        payload = self._valid_matrix_payload()
        validate_matrix_payload(payload)
        self.assertEqual(payload["deleted_total"], 15)
        self.assertEqual(payload["stable_zero_observations_total"], 6)
        validator = localctl._validate_material_rag_matrix_payload
        validator(payload)
        missing = dict(payload)
        del missing["minio_replayed_identity_ok"]
        with self.assertRaises(localctl.LocalError):
            validator(missing)
        single = inspect.getsource(run_crash_machine_gate)
        matrix = inspect.getsource(run_crash_matrix_gate)
        helper = inspect.getsource(
            __import__(
                "infra.f1.material_rag_backup_restore",
                fromlist=["_run_one_hard_death_stage"],
            )._run_one_hard_death_stage
        )
        self.assertIn('"DB_RESTORED"', single)
        self.assertIn("signal.SIGKILL", single)
        self.assertIn("VOLUMES_REPLACED", matrix)
        self.assertIn("DB_RESTORED", matrix)
        self.assertIn("MINIO_REPLAYED", matrix)
        self.assertNotIn('"PREPARED"', matrix)
        self.assertIn("require_live_crash_stage", matrix)
        self.assertIn("verify_minio_replayed_identity", helper)
        self.assertIn("signal.SIGKILL", helper)
        self.assertEqual(MATRIX_OK_TOKEN, "LOCAL_MATERIAL_RAG_CRASH_MATRIX_OK")
        self.assertNotIn(MATRIX_OK_TOKEN, single)


class _MaintenanceSnapshotConnection:
    def __init__(self) -> None:
        self.jobs = ["job-a", "job-b"]
        self.units = ["unit-a"]
        self.bindings = [
            ("bind-a", "ready", "deadbeef"),
        ]
        self.job_deletes = 0
        self.rolled_back = False
        self.committed = False
        self._frozen: tuple[object, ...] | None = None

    def logical_snapshot(self) -> tuple[object, ...]:
        return (tuple(self.jobs), tuple(self.units), tuple(self.bindings))

    def _freeze(self) -> None:
        if self._frozen is None:
            self._frozen = self.logical_snapshot()

    def execute(self, sql: str, *args: object):
        text = " ".join(sql.split())
        if text.startswith("SHOW session_replication_role"):
            return _Rows([("origin",)])
        if text.startswith("SELECT current_user, session_user"):
            return _Rows([("f0d_bootstrap", "f0d_bootstrap", "origin")])
        if text.startswith(
            "SELECT count(*) FROM f1.material_rag_job WHERE status='running'"
        ):
            return _Rows([(0,)])
        if text.startswith("SELECT count(*) FROM f1.material_rag_job"):
            return _Rows([(len(self.jobs),)])
        if text.startswith("SELECT count(*) FROM f1.material_rag_unit"):
            return _Rows([(len(self.units),)])
        if "FROM f1.material_rag_scope_binding WHERE status='provisioning'" in text:
            return _Rows([(0,)])
        if "FROM f1.material_rag_scope_binding WHERE status='deleted'" in text:
            return _Rows([(0,)])
        if text.startswith("SELECT count(*) FROM f1.material_rag_unit AS unit"):
            return _Rows([(0,)])
        if text.startswith("SELECT id FROM f1.material_rag_job"):
            return _Rows([(item,) for item in self.jobs])
        if text.startswith("SELECT id FROM f1.material_rag_unit"):
            return _Rows([(item,) for item in self.units])
        if text.startswith("SELECT id, status"):
            return _Rows(list(self.bindings))
        if text.startswith("DELETE FROM f1.material_rag_job"):
            self._freeze()
            self.jobs = []
            self.job_deletes += 1
            return _Rows([])
        if text.startswith("DELETE FROM f1.material_rag_unit"):
            self._freeze()
            self.units = []
            return _Rows([])
        if text.startswith("UPDATE f1.material_rag_scope_binding"):
            self._freeze()
            self.bindings = [("bind-a", "deleted", None)]
            return _Rows([])
        raise AssertionError("unexpected maintenance sql")

    def rollback(self) -> None:
        if self._frozen is not None:
            jobs, units, bindings = self._frozen
            self.jobs = list(jobs)
            self.units = list(units)
            self.bindings = list(bindings)
        self.rolled_back = True

    def commit(self) -> None:
        self.committed = True


class _Rows:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class MaterialRagBackupRestoreLiveTests(unittest.TestCase):
    def test_live_restore_rebuild_restart_isolation_and_fail_cleanup(self) -> None:
        payload = run_machine_gate()
        self.assertEqual(payload["schema"], CHECK_SCHEMA)
        self.assertEqual(payload["f1_head"], F1_HEAD)
        self.assertEqual(payload["business_table_count"], 38)
        self.assertEqual(payload["front_door_tamper_failures"], 5)
        self.assertEqual(payload["front_door_repair_ok"], 1)
        self.assertEqual(payload["restore_ok"], 1)
        self.assertEqual(payload["rebuild_ok"], 1)
        self.assertEqual(payload["restart_ok"], 1)
        self.assertEqual(payload["minio_live_tree_match"], 1)
        self.assertEqual(payload["post_restart_fresh_process"], 1)
        self.assertEqual(payload["post_restart_retrieval_ok"], 1)
        self.assertEqual(payload["cross_tenant_visible"], 0)
        self.assertEqual(payload["cross_scope_visible"], 0)
        self.assertEqual(payload["cleanup_label_rejection"], 1)
        self.assertEqual(payload["restore_failure_cleanup"], 1)
        self.assertEqual(payload["maintenance_failure_cleanup"], 1)
        self.assertEqual(payload["rebuild_failure_cleanup"], 1)
        self.assertEqual(payload["restart_failure_cleanup"], 1)
        self.assertEqual(payload["restore_mutation_observed"], 1)
        self.assertEqual(payload["maintenance_mutation_observed"], 1)
        self.assertEqual(payload["rebuild_mutation_observed"], 1)
        self.assertEqual(payload["restart_mutation_observed"], 1)
        self.assertEqual(payload["fail_cleanup_ok"], 1)
        self.assertEqual(payload["skipped"], 0)
        self.assertEqual(payload["dedicated_c"], 0)
        self.assertEqual(payload["dedicated_v"], 0)
        self.assertEqual(payload["dedicated_n"], 0)
        self.assertEqual(payload["shared_fingerprint_match"], 1)
        for key in (
            "unreleased_enqueued",
            "revoked_enqueued",
            "cross_tenant_enqueued",
            "rebuild_old_job_reuse",
            "maintenance_job",
            "maintenance_unit",
            "maintenance_live_lease",
        ):
            self.assertEqual(payload[key], 0, key)


if __name__ == "__main__":
    unittest.main()
