from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from infra.f1 import local_backup


PROJECT_ID = "anhuan-engineering-1234"
DATABASE = "anhuan_engineering_1234"
BACKUP_ID = "20260811T120000Z-123456abcdef"
BUSINESS_SNAPSHOT = {
    "table_count": 34,
    "total_row_count": 2,
    "nonempty_table_count": 2,
    "count_sha256": "a" * 64,
}


def load_localctl():
    path = Path(__file__).resolve().parents[1] / "scripts/localctl"
    loader = importlib.machinery.SourceFileLoader(
        "engineering_closeout_localctl", str(path)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise AssertionError("localctl import unavailable")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


localctl = load_localctl()


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
    private_file(root / "database.dump", b"postgres-dump\x00v1")
    minio = private_directory(root / "minio-data")
    nested = private_directory(minio / "tenant-a")
    private_file(nested / "opaque-object-one", b"first-object")
    private_file(minio / "opaque-object-two", b"second-object")
    return root


def rewrite_canonical_manifest(root: Path, document: dict[str, object]) -> None:
    path = root / "manifest.json"
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


class EngineeringCloseoutBackupTests(unittest.TestCase):
    def test_create_and_verify_aggregate_only_canonical_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = backup_stage(Path(raw))
            manifest = local_backup.create_manifest(
                root, PROJECT_ID, DATABASE, BUSINESS_SNAPSHOT
            )

            self.assertEqual(set(manifest), local_backup._MANIFEST_FIELDS)
            self.assertEqual(manifest["schema"], local_backup.SCHEMA)
            self.assertEqual(manifest["minio_file_count"], 2)
            self.assertEqual(
                manifest["minio_total_size"], len(b"first-objectsecond-object")
            )
            manifest_path = root / "manifest.json"
            self.assertEqual(stat.S_IMODE(manifest_path.stat().st_mode), 0o600)
            raw_manifest = manifest_path.read_text(encoding="utf-8")
            self.assertEqual(
                raw_manifest,
                json.dumps(
                    manifest,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
            )
            for forbidden in (
                "database.dump",
                "minio-data",
                "tenant-a",
                "opaque-object-one",
                "opaque-object-two",
            ):
                self.assertNotIn(forbidden, raw_manifest)
            self.assertEqual(
                local_backup.verify_backup(root, PROJECT_ID, DATABASE), manifest
            )

    def test_tree_digest_commits_to_paths_and_empty_directories(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            first = backup_stage(parent / "first")
            second = backup_stage(parent / "second")
            (second / "minio-data" / "tenant-a" / "opaque-object-one").rename(
                second / "minio-data" / "tenant-a" / "renamed-object"
            )
            empty = private_directory(first / "minio-data" / "empty-prefix")
            first_manifest = local_backup.create_manifest(
                first, PROJECT_ID, DATABASE, BUSINESS_SNAPSHOT
            )
            second_manifest = local_backup.create_manifest(
                second, PROJECT_ID, DATABASE, BUSINESS_SNAPSHOT
            )
            self.assertNotEqual(
                first_manifest["minio_tree_sha256"],
                second_manifest["minio_tree_sha256"],
            )
            empty.rmdir()
            with self.assertRaisesRegex(
                local_backup.BackupContractError, "MINIO_TREE_MISMATCH"
            ):
                local_backup.verify_backup(first, PROJECT_ID, DATABASE)

    def test_verify_rejects_content_sha_and_manifest_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = backup_stage(Path(raw))
            manifest = local_backup.create_manifest(
                root, PROJECT_ID, DATABASE, BUSINESS_SNAPSHOT
            )
            private_file(root / "database.dump", b"tampered-dump")
            with self.assertRaisesRegex(
                local_backup.BackupContractError, "DATABASE_DUMP_MISMATCH"
            ):
                local_backup.verify_backup(root, PROJECT_ID, DATABASE)

            private_file(root / "database.dump", b"postgres-dump\x00v1")
            manifest["minio_tree_sha256"] = "0" * 64
            rewrite_canonical_manifest(root, manifest)
            with self.assertRaisesRegex(
                local_backup.BackupContractError, "MINIO_TREE_MISMATCH"
            ):
                local_backup.verify_backup(root, PROJECT_ID, DATABASE)

            manifest["unexpected"] = True
            rewrite_canonical_manifest(root, manifest)
            with self.assertRaisesRegex(
                local_backup.BackupContractError, "MANIFEST_SCHEMA_INVALID"
            ):
                local_backup.verify_backup(root, PROJECT_ID, DATABASE)

    def test_verify_rejects_noncanonical_and_duplicate_manifest_fields(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = backup_stage(Path(raw))
            manifest = local_backup.create_manifest(
                root, PROJECT_ID, DATABASE, BUSINESS_SNAPSHOT
            )
            path = root / "manifest.json"
            path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            path.chmod(0o600)
            with self.assertRaisesRegex(
                local_backup.BackupContractError, "MANIFEST_NOT_CANONICAL"
            ):
                local_backup.verify_backup(root, PROJECT_ID, DATABASE)

            canonical = json.dumps(
                manifest, sort_keys=True, separators=(",", ":")
            )
            duplicate = canonical.replace(
                '"schema":', '"schema":"duplicate","schema":', 1
            )
            path.write_text(duplicate + "\n", encoding="utf-8")
            path.chmod(0o600)
            with self.assertRaisesRegex(
                local_backup.BackupContractError, "MANIFEST_DUPLICATE_FIELD"
            ):
                local_backup.verify_backup(root, PROJECT_ID, DATABASE)

    def test_rejects_missing_and_extra_root_entries(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            missing = backup_stage(parent / "missing")
            (missing / "database.dump").unlink()
            with self.assertRaisesRegex(
                local_backup.BackupContractError, "BACKUP_ENTRY_MISSING"
            ):
                local_backup.create_manifest(
                    missing, PROJECT_ID, DATABASE, BUSINESS_SNAPSHOT
                )

            extra = backup_stage(parent / "extra")
            private_file(extra / "extra", b"not-allowed")
            with self.assertRaisesRegex(
                local_backup.BackupContractError, "BACKUP_ENTRY_EXTRA"
            ):
                local_backup.create_manifest(
                    extra, PROJECT_ID, DATABASE, BUSINESS_SNAPSHOT
                )

    def test_rejects_symlinks_hardlinks_and_nonregular_entries(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            symlink_root = backup_stage(parent / "symlink")
            target = symlink_root / "minio-data" / "opaque-object-two"
            target.unlink()
            target.symlink_to(symlink_root / "database.dump")
            with self.assertRaisesRegex(
                local_backup.BackupContractError, "SYMLINK_REJECTED"
            ):
                local_backup.create_manifest(
                    symlink_root, PROJECT_ID, DATABASE, BUSINESS_SNAPSHOT
                )

            hardlink_root = backup_stage(parent / "hardlink")
            hardlink = hardlink_root / "minio-data" / "hardlink"
            os.link(hardlink_root / "database.dump", hardlink)
            with self.assertRaisesRegex(
                local_backup.BackupContractError, "HARDLINK_REJECTED"
            ):
                local_backup.create_manifest(
                    hardlink_root, PROJECT_ID, DATABASE, BUSINESS_SNAPSHOT
                )

            fifo_root = backup_stage(parent / "fifo")
            fifo = fifo_root / "minio-data" / "named-pipe"
            os.mkfifo(fifo, mode=0o600)
            with self.assertRaisesRegex(
                local_backup.BackupContractError, "MINIO_ENTRY_TYPE_INVALID"
            ):
                local_backup.create_manifest(
                    fifo_root, PROJECT_ID, DATABASE, BUSINESS_SNAPSHOT
                )

    def test_rejects_owner_and_mode_violations(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            root_mode = backup_stage(parent / "root-mode")
            root_mode.chmod(0o750)
            with self.assertRaisesRegex(
                local_backup.BackupContractError, "BACKUP_ROOT_MODE_INVALID"
            ):
                local_backup.create_manifest(
                    root_mode, PROJECT_ID, DATABASE, BUSINESS_SNAPSHOT
                )

            file_mode = backup_stage(parent / "file-mode")
            (file_mode / "database.dump").chmod(0o640)
            with self.assertRaisesRegex(
                local_backup.BackupContractError, "DATABASE_DUMP_MODE_INVALID"
            ):
                local_backup.create_manifest(
                    file_mode, PROJECT_ID, DATABASE, BUSINESS_SNAPSHOT
                )

            owner = backup_stage(parent / "owner")
            with mock.patch.object(
                local_backup.os, "geteuid", return_value=os.geteuid() + 1
            ):
                with self.assertRaisesRegex(
                    local_backup.BackupContractError, "BACKUP_ROOT_OWNER_INVALID"
                ):
                    local_backup.create_manifest(
                        owner, PROJECT_ID, DATABASE, BUSINESS_SNAPSHOT
                    )

    def test_verify_rejects_path_traversal_and_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = backup_stage(Path(raw))
            local_backup.create_manifest(
                root, PROJECT_ID, DATABASE, BUSINESS_SNAPSHOT
            )
            traversal = root / "minio-data" / ".." / ".." / "backup"
            with self.assertRaisesRegex(
                local_backup.BackupContractError, "BACKUP_PATH_TRAVERSAL"
            ):
                local_backup.verify_backup(traversal, PROJECT_ID, DATABASE)
            with self.assertRaisesRegex(
                local_backup.BackupContractError, "EXPECTED_PROJECT_ID_INVALID"
            ):
                local_backup.verify_backup(root, "../other-project", DATABASE)
            with self.assertRaisesRegex(
                local_backup.BackupContractError, "MANIFEST_DATABASE_MISMATCH"
            ):
                local_backup.verify_backup(root, PROJECT_ID, "another_database")

    def test_restored_minio_tree_must_match_backup(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            backup = backup_stage(parent / "backup")
            local_backup.create_manifest(
                backup, PROJECT_ID, DATABASE, BUSINESS_SNAPSHOT
            )
            restored = private_directory(parent / "restored")
            private_file(restored / "different-object", b"different")
            with self.assertRaisesRegex(
                local_backup.BackupContractError,
                "RESTORED_MINIO_TREE_MISMATCH",
            ):
                local_backup.verify_restored_minio(backup, restored)

    def test_manifest_mode_and_hardlink_are_verified(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            mode_root = backup_stage(parent / "mode")
            local_backup.create_manifest(
                mode_root, PROJECT_ID, DATABASE, BUSINESS_SNAPSHOT
            )
            (mode_root / "manifest.json").chmod(0o644)
            with self.assertRaisesRegex(
                local_backup.BackupContractError, "MANIFEST_MODE_INVALID"
            ):
                local_backup.verify_backup(mode_root, PROJECT_ID, DATABASE)

            link_root = backup_stage(parent / "link")
            local_backup.create_manifest(
                link_root, PROJECT_ID, DATABASE, BUSINESS_SNAPSHOT
            )
            outside = parent / "manifest-copy"
            os.link(link_root / "manifest.json", outside)
            with self.assertRaisesRegex(
                local_backup.BackupContractError, "MANIFEST_HARDLINK_REJECTED"
            ):
                local_backup.verify_backup(link_root, PROJECT_ID, DATABASE)


class LocalctlBackupRestoreContractTests(unittest.TestCase):
    STATE = {
        "project_id": "d30c42bc-d5a3-48db-a40b-ab88748055f8",
        "compose_project": "anhuan-closeout-123456abcdef",
        "database": "anhuan_closeout_123456abcdef123456abcdef",
    }

    def test_business_snapshot_requires_all_current_exact_tables(self) -> None:
        counts = {
            name: (1 if index == 0 else 0)
            for index, name in enumerate(localctl.P2_P7_TABLES)
        }
        output = "\n".join(
            f"{name}|{counts[name]}" for name in sorted(counts)
        )
        with mock.patch.object(
            localctl,
            "_compose",
            return_value=mock.Mock(returncode=0, stdout=output + "\n", stderr=""),
        ):
            snapshot = localctl._business_snapshot(self.STATE)
        self.assertEqual(snapshot["table_count"], 34)
        self.assertEqual(snapshot["total_row_count"], 1)
        self.assertEqual(snapshot["nonempty_table_count"], 1)
        self.assertRegex(str(snapshot["count_sha256"]), r"\A[0-9a-f]{64}\Z")

        incomplete = "\n".join(output.splitlines()[:-1]) + "\n"
        with (
            mock.patch.object(
                localctl,
                "_compose",
                return_value=mock.Mock(
                    returncode=0, stdout=incomplete, stderr=""
                ),
            ),
            self.assertRaisesRegex(
                localctl.LocalError, "LOCAL_BUSINESS_SNAPSHOT_INVALID"
            ),
        ):
            localctl._business_snapshot(self.STATE)

    def test_backup_collects_db_and_minio_before_manifest_finalization(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            backups = private_directory(Path(raw) / "backups")
            calls: list[tuple[str, ...]] = []

            def compose(_state, *arguments, **_kwargs):
                calls.append(tuple(arguments))
                if "--volume" not in arguments:
                    return mock.Mock(returncode=0, stdout="", stderr="")
                mount = arguments[arguments.index("--volume") + 1]
                stage = Path(mount.removesuffix(":/backup"))
                if arguments[-1] == "backup-db":
                    private_file(stage / "database.dump", b"real-pg-dump")
                elif arguments[-1] == "backup-minio":
                    minio = private_directory(stage / "minio-data")
                    private_file(minio / "object", b"real-minio-object")
                return mock.Mock(returncode=0, stdout="", stderr="")

            output = io.StringIO()

            def business_snapshot(_state):
                calls.append(("business-snapshot",))
                return BUSINESS_SNAPSHOT

            with (
                mock.patch.object(localctl, "BACKUPS_DIR", backups),
                mock.patch.object(localctl, "_new_backup_id", return_value=BACKUP_ID),
                mock.patch.object(
                    localctl, "_core_containers_ready", return_value=True
                ),
                mock.patch.object(
                    localctl,
                    "_business_snapshot",
                    side_effect=business_snapshot,
                ),
                mock.patch.object(localctl, "_assert_resource_labels"),
                mock.patch.object(localctl, "_compose", side_effect=compose),
                contextlib.redirect_stdout(output),
            ):
                localctl._backup(self.STATE)

            final = backups / BACKUP_ID
            self.assertTrue(final.is_dir())
            local_backup.verify_backup(
                final,
                str(self.STATE["project_id"]),
                str(self.STATE["database"]),
            )
            self.assertFalse((backups / f".pending-{BACKUP_ID}").exists())
            self.assertEqual(output.getvalue(), f"LOCAL_BACKUP_OK {BACKUP_ID}\n")
            stop_index = next(
                index for index, call in enumerate(calls) if call[:1] == ("stop",)
            )
            db_index = next(
                index for index, call in enumerate(calls) if call[-1:] == ("backup-db",)
            )
            snapshot_index = calls.index(("business-snapshot",))
            minio_index = next(
                index
                for index, call in enumerate(calls)
                if call[-1:] == ("backup-minio",)
            )
            resume_index = next(
                index for index, call in enumerate(calls) if call[:2] == ("up", "-d")
            )
            self.assertLess(stop_index, db_index)
            self.assertLess(stop_index, snapshot_index)
            self.assertLess(snapshot_index, db_index)
            self.assertLess(db_index, minio_index)
            self.assertLess(minio_index, resume_index)

    def test_restore_requires_confirmation_before_backup_lookup(self) -> None:
        with mock.patch.object(localctl, "_resolve_backup") as resolve:
            with self.assertRaisesRegex(
                localctl.LocalError, "LOCAL_RESTORE_CONFIRMATION_REQUIRED"
            ):
                localctl._restore(
                    self.STATE, backup_id=None, confirmed=False
                )
        resolve.assert_not_called()

    def test_restore_defaults_to_latest_or_uses_strict_explicit_id(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            backups = private_directory(Path(raw) / "backups")
            older = "20260810T120000Z-000000000001"
            newer = "20260811T120000Z-000000000002"
            private_directory(backups / older)
            private_directory(backups / newer)
            with mock.patch.object(localctl, "BACKUPS_DIR", backups):
                self.assertEqual(localctl._resolve_backup(None)[0], newer)
                self.assertEqual(localctl._resolve_backup(older)[0], older)
                with self.assertRaisesRegex(
                    localctl.LocalError, "LOCAL_BACKUP_ID_INVALID"
                ):
                    localctl._resolve_backup("../../shared")

    def test_log_gate_scans_all_private_backups_including_legacy_schema(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            backups = private_directory(Path(raw) / "backups")
            legacy = private_directory(
                backups / "20260810T120000Z-000000000001"
            )
            current = private_directory(backups / BACKUP_ID)
            private_file(legacy / "manifest.json", b'{"schema":"legacy"}\n')
            private_file(legacy / "database.dump", b"legacy-dump")
            legacy_minio = private_directory(legacy / "minio-data")
            private_file(legacy_minio / "legacy-object", b"legacy")
            private_file(current / "manifest.json", b'{"schema":"current"}\n')
            private_file(current / "database.dump", b"current-dump")
            current_minio = private_directory(current / "minio-data")
            private_file(current_minio / "current-object", b"current")

            with mock.patch.object(localctl, "BACKUPS_DIR", backups):
                observed = localctl._backup_files_for_log_check(self.STATE)

            self.assertEqual(len(observed), 6)
            self.assertTrue(any(path.name == "legacy-object" for path in observed))
            self.assertTrue(any(path.name == "current-object" for path in observed))

    def test_restore_verifies_before_exact_data_volume_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            backups = private_directory(Path(raw) / "backups")
            restore_tmp = private_directory(Path(raw) / "restore-tmp")
            backup = backup_stage(backups, BACKUP_ID)
            local_backup.create_manifest(
                backup,
                str(self.STATE["project_id"]),
                str(self.STATE["database"]),
                BUSINESS_SNAPSHOT,
            )
            events: list[str] = []
            original_verify = local_backup.verify_backup

            def verify(*arguments, **kwargs):
                events.append("verify")
                return original_verify(*arguments, **kwargs)

            def compose(_state, *arguments, **_kwargs):
                events.append("compose:" + ":".join(arguments))
                if arguments[-1] == "backup-minio":
                    mount = arguments[arguments.index("--volume") + 1]
                    stage = Path(mount.removesuffix(":/backup"))
                    minio = private_directory(stage / "minio-data")
                    tenant = private_directory(minio / "tenant-a")
                    private_file(tenant / "opaque-object-one", b"first-object")
                    private_file(minio / "opaque-object-two", b"second-object")
                return mock.Mock(returncode=0, stdout="", stderr="")

            removed: list[list[str]] = []
            output = io.StringIO()
            original_restored_verify = local_backup.verify_restored_minio

            def run(arguments, **_kwargs):
                removed.append(arguments)
                events.append("remove")
                return mock.Mock(returncode=0, stdout="", stderr="")

            with (
                mock.patch.object(localctl, "BACKUPS_DIR", backups),
                mock.patch.object(localctl, "TMP_DIR", restore_tmp),
                mock.patch.object(
                    localctl,
                    "_business_snapshot",
                    return_value=BUSINESS_SNAPSHOT,
                ),
                mock.patch.object(
                    localctl.local_backup, "verify_backup", side_effect=verify
                ),
                mock.patch.object(
                    localctl.local_backup,
                    "verify_restored_minio",
                    wraps=original_restored_verify,
                ) as restored_verify,
                mock.patch.object(localctl, "_assert_resource_labels"),
                mock.patch.object(
                    localctl,
                    "_project_data_volumes",
                    side_effect=[
                        {
                            "postgres_data": "volume-postgres",
                            "minio_data": "volume-minio",
                        },
                        {},
                        {
                            "postgres_data": "restored-postgres",
                            "minio_data": "restored-minio",
                        },
                    ],
                ),
                mock.patch.object(localctl, "_docker", return_value="/trusted/docker"),
                mock.patch.object(localctl, "_run", side_effect=run),
                mock.patch.object(localctl, "_compose", side_effect=compose),
                mock.patch.object(
                    localctl,
                    "_sync_secrets",
                    side_effect=lambda *_args, **_kwargs: events.append("secrets"),
                ),
                mock.patch.object(
                    localctl,
                    "_start",
                    side_effect=lambda *_args, **_kwargs: events.append("start"),
                ) as start,
                contextlib.redirect_stdout(output),
            ):
                localctl._restore(
                    self.STATE, backup_id=None, confirmed=True
                )

            self.assertEqual(events[0], "verify")
            self.assertEqual(
                removed,
                [
                    ["/trusted/docker", "volume", "rm", "volume-minio"],
                    ["/trusted/docker", "volume", "rm", "volume-postgres"],
                ],
            )
            self.assertIn("compose:run:--rm:migrator", events)
            self.assertTrue(
                any(event.endswith(":restore-db") for event in events)
            )
            self.assertTrue(
                any(event.endswith(":restore-minio") for event in events)
            )
            self.assertEqual(events[-1], "start")
            start.assert_called_once_with(
                self.STATE, capture=True, announce=False
            )
            restored_verify.assert_called_once()
            self.assertEqual(
                output.getvalue(), f"LOCAL_RESTORE_OK {BACKUP_ID}\n"
            )

    def test_restore_data_failure_cleans_partial_state_without_starting(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            backups = private_directory(Path(raw) / "backups")
            backup = backup_stage(backups, BACKUP_ID)
            local_backup.create_manifest(
                backup,
                str(self.STATE["project_id"]),
                str(self.STATE["database"]),
                BUSINESS_SNAPSHOT,
            )

            def compose(_state, *arguments, **_kwargs):
                if arguments[-1] == "restore-minio":
                    raise localctl.LocalError("LOCAL_COMMAND_FAILED")
                return mock.Mock(returncode=0, stdout="", stderr="")

            with (
                mock.patch.object(localctl, "BACKUPS_DIR", backups),
                mock.patch.object(localctl, "_assert_resource_labels"),
                mock.patch.object(
                    localctl,
                    "_project_data_volumes",
                    side_effect=[
                        {
                            "postgres_data": "old-postgres",
                            "minio_data": "old-minio",
                        },
                        {},
                    ],
                ),
                mock.patch.object(localctl, "_docker", return_value="/trusted/docker"),
                mock.patch.object(
                    localctl,
                    "_run",
                    return_value=mock.Mock(returncode=0, stdout="", stderr=""),
                ),
                mock.patch.object(localctl, "_compose", side_effect=compose),
                mock.patch.object(localctl, "_sync_secrets"),
                mock.patch.object(localctl, "_cleanup_failed_restore") as cleanup,
                mock.patch.object(localctl, "_start") as start,
            ):
                with self.assertRaisesRegex(
                    localctl.LocalError, "LOCAL_COMMAND_FAILED"
                ):
                    localctl._restore(
                        self.STATE, backup_id=BACKUP_ID, confirmed=True
                    )
            cleanup.assert_called_once_with(self.STATE)
            start.assert_not_called()

    def test_restore_business_identity_mismatch_cleans_partial_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw)
            backups = private_directory(parent / "backups")
            backup = backup_stage(backups, BACKUP_ID)
            manifest = local_backup.create_manifest(
                backup,
                str(self.STATE["project_id"]),
                str(self.STATE["database"]),
                BUSINESS_SNAPSHOT,
            )
            observed = dict(BUSINESS_SNAPSHOT)
            observed["total_row_count"] = 3
            expected_minio = {
                "minio_tree_sha256": manifest["minio_tree_sha256"],
                "minio_file_count": manifest["minio_file_count"],
                "minio_total_size": manifest["minio_total_size"],
            }
            restore_check = private_directory(parent / "restore-check")

            with (
                mock.patch.object(localctl, "BACKUPS_DIR", backups),
                mock.patch.object(localctl, "_assert_resource_labels"),
                mock.patch.object(
                    localctl,
                    "_project_data_volumes",
                    side_effect=[
                        {
                            "postgres_data": "old-postgres",
                            "minio_data": "old-minio",
                        },
                        {},
                        {
                            "postgres_data": "new-postgres",
                            "minio_data": "new-minio",
                        },
                    ],
                ),
                mock.patch.object(localctl, "_docker", return_value="/trusted/docker"),
                mock.patch.object(
                    localctl,
                    "_run",
                    return_value=mock.Mock(returncode=0, stdout="", stderr=""),
                ),
                mock.patch.object(localctl, "_compose"),
                mock.patch.object(localctl, "_sync_secrets"),
                mock.patch.object(
                    localctl,
                    "_new_restore_check_directory",
                    return_value=restore_check,
                ),
                mock.patch.object(localctl, "_remove_restore_check_directory"),
                mock.patch.object(
                    localctl.local_backup,
                    "verify_restored_minio",
                    return_value=expected_minio,
                ),
                mock.patch.object(localctl, "_start"),
                mock.patch.object(
                    localctl, "_business_snapshot", return_value=observed
                ),
                mock.patch.object(localctl, "_cleanup_failed_restore") as cleanup,
                self.assertRaisesRegex(
                    localctl.LocalError,
                    "LOCAL_RESTORE_BUSINESS_IDENTITY_MISMATCH",
                ),
            ):
                localctl._restore(
                    self.STATE, backup_id=BACKUP_ID, confirmed=True
                )

            cleanup.assert_called_once_with(self.STATE)

    def test_failed_restore_cleanup_is_project_scoped(self) -> None:
        resource_calls = {"ps": 0}

        def resources(kind, _project):
            if kind != "ps":
                raise AssertionError("unexpected resource kind")
            resource_calls["ps"] += 1
            return ["current-project-container"] if resource_calls["ps"] == 1 else []

        commands: list[list[str]] = []

        def run(arguments, **_kwargs):
            commands.append(arguments)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with (
            mock.patch.object(localctl, "_compose"),
            mock.patch.object(localctl, "_assert_resource_labels"),
            mock.patch.object(localctl, "_resource_ids", side_effect=resources),
            mock.patch.object(localctl, "_docker", return_value="/trusted/docker"),
            mock.patch.object(localctl, "_run", side_effect=run),
            mock.patch.object(
                localctl,
                "_project_data_volumes",
                side_effect=[
                    {
                        "postgres_data": "new-postgres",
                        "minio_data": "new-minio",
                    },
                    {},
                ],
            ),
        ):
            localctl._cleanup_failed_restore(self.STATE)

        self.assertEqual(
            commands,
            [
                ["/trusted/docker", "rm", "-f", "current-project-container"],
                ["/trusted/docker", "volume", "rm", "new-postgres"],
                ["/trusted/docker", "volume", "rm", "new-minio"],
            ],
        )

    def test_compose_backup_helpers_have_separate_secret_boundary(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "infra/f1/docker-compose.local.yml"
        ).read_text(encoding="utf-8")

        def service(name: str, following: str) -> str:
            return source.split(f"  {name}:\n", 1)[1].split(
                f"  {following}:\n", 1
            )[0]

        backup_db = service("backup-db", "backup-minio")
        backup_minio = service("backup-minio", "restore-db")
        restore_db = service("restore-db", "restore-minio")
        restore_minio = service("restore-minio", "api")
        for block in (backup_db, restore_db):
            self.assertIn("backup_secrets:/run/secrets/f1:ro", block)
            self.assertNotIn("api_secrets", block)
            self.assertNotIn("worker_secrets", block)
        for block in (backup_minio, restore_minio):
            for forbidden in (
                "api_secrets",
                "worker_secrets",
                "backup_secrets",
            ):
                self.assertNotIn(forbidden, block)

    def test_invalid_manifest_prevents_all_restore_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            backups = private_directory(Path(raw) / "backups")
            backup = backup_stage(backups, BACKUP_ID)
            local_backup.create_manifest(
                backup,
                str(self.STATE["project_id"]),
                str(self.STATE["database"]),
                BUSINESS_SNAPSHOT,
            )
            private_file(backup / "database.dump", b"tampered")
            with (
                mock.patch.object(localctl, "BACKUPS_DIR", backups),
                mock.patch.object(localctl, "_project_data_volumes") as volumes,
                mock.patch.object(localctl, "_compose") as compose,
            ):
                with self.assertRaisesRegex(
                    localctl.LocalError, "LOCAL_BACKUP_MANIFEST_INVALID"
                ):
                    localctl._restore(
                        self.STATE,
                        backup_id=BACKUP_ID,
                        confirmed=True,
                    )
            volumes.assert_not_called()
            compose.assert_not_called()


if __name__ == "__main__":
    unittest.main()
