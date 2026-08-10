from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fixture_gate import ENVIRONMENT_DEMO_V01, ValidationFailure, verify_fixture_set
from fixture_gate.validator import AuditWriteFailure, write_audit


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_source(root: Path, relative_path: str, data: bytes) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _write_manifest(path: Path, entries: list[tuple[str, bytes]]) -> None:
    lines = [f"{_digest(data)}  {relative_path}" for relative_path, data in entries]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class FixtureGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name).resolve()
        self.source = self.base / "source"
        self.source.mkdir()
        self.core_manifest = self.base / "core.sha256"
        self.negative_manifest = self.base / "negative.sha256"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _valid_pair(self) -> None:
        core = [("core.txt", b"core")]
        negative = [("negative.txt", b"negative")]
        for relative_path, data in core + negative:
            _write_source(self.source, relative_path, data)
        _write_manifest(self.core_manifest, core)
        _write_manifest(self.negative_manifest, negative)

    def _verify(self) -> dict[str, object]:
        return verify_fixture_set(
            source_root=self.source,
            core_manifest=self.core_manifest,
            negative_manifest=self.negative_manifest,
        )

    def _assert_failure(self, expected_code: str) -> ValidationFailure:
        with self.assertRaises(ValidationFailure) as context:
            self._verify()
        self.assertEqual(context.exception.code, expected_code)
        return context.exception

    def test_valid_manifests_are_aggregated(self) -> None:
        self._valid_pair()
        audit = self._verify()
        self.assertEqual(audit["summary"]["verified"], 2)
        self.assertEqual(audit["summary"]["failed"], 0)
        self.assertEqual(audit["summary"]["core"], {"files": 1, "bytes": 4})
        self.assertEqual(audit["policy"]["external_processing"], "DENY")
        self.assertEqual(audit["fixture_set_id"], "unregistered")
        self.assertEqual(
            set(audit),
            {
                "schema_version",
                "fixture_set_id",
                "fixture_version",
                "policy",
                "manifest_sha256",
                "summary",
                "entries",
            },
        )
        self.assertEqual(set(audit["entries"][0]), {"group", "line", "path_sha256"})

    def test_replacement_manifests_cannot_claim_registered_identity(self) -> None:
        self._valid_pair()
        with self.assertRaises(ValidationFailure) as context:
            verify_fixture_set(
                source_root=self.source,
                core_manifest=self.core_manifest,
                negative_manifest=self.negative_manifest,
                expected_identity=ENVIRONMENT_DEMO_V01,
            )
        self.assertEqual(context.exception.code, "MANIFEST_IDENTITY_MISMATCH")

    def test_swapped_manifests_cannot_claim_registered_identity(self) -> None:
        self._valid_pair()
        with self.assertRaises(ValidationFailure) as context:
            verify_fixture_set(
                source_root=self.source,
                core_manifest=self.negative_manifest,
                negative_manifest=self.core_manifest,
                expected_identity=ENVIRONMENT_DEMO_V01,
            )
        self.assertEqual(context.exception.code, "MANIFEST_IDENTITY_MISMATCH")

    def test_chinese_and_spaces_in_relative_path(self) -> None:
        core = [("资料 甲/检测 报告.pdf", "合规证据".encode())]
        negative = [("历史/旧 规则.txt", b"archived")]
        for relative_path, data in core + negative:
            _write_source(self.source, relative_path, data)
        _write_manifest(self.core_manifest, core)
        _write_manifest(self.negative_manifest, negative)
        audit = self._verify()
        serialized = json.dumps(audit, ensure_ascii=False)
        self.assertEqual(audit["summary"]["verified"], 2)
        self.assertNotIn("检测 报告", serialized)
        self.assertNotIn(str(self.source), serialized)

    def test_changed_byte_is_rejected(self) -> None:
        self._valid_pair()
        (self.source / "core.txt").write_bytes(b"Core")
        self._assert_failure("HASH_MISMATCH")

    def test_missing_file_is_rejected(self) -> None:
        self._valid_pair()
        (self.source / "core.txt").unlink()
        self._assert_failure("FILE_MISSING")

    def test_malformed_manifest_is_rejected(self) -> None:
        self._valid_pair()
        self.core_manifest.write_text("not-a-sha  core.txt\n", encoding="utf-8")
        self._assert_failure("MALFORMED_MANIFEST")

    def test_fifo_manifest_is_rejected_without_blocking(self) -> None:
        self._valid_pair()
        self.core_manifest.unlink()
        os.mkfifo(self.core_manifest)
        self._assert_failure("MANIFEST_UNREADABLE")

    def test_manifest_symlink_ancestor_is_rejected(self) -> None:
        self._valid_pair()
        manifest_directory = self.base / "manifest-directory"
        manifest_directory.mkdir()
        nested_manifest = manifest_directory / "core.sha256"
        nested_manifest.write_bytes(self.core_manifest.read_bytes())
        alias = self.base / "manifest-alias"
        alias.symlink_to(manifest_directory, target_is_directory=True)
        with self.assertRaises(ValidationFailure) as context:
            verify_fixture_set(
                source_root=self.source,
                core_manifest=alias / "core.sha256",
                negative_manifest=self.negative_manifest,
            )
        self.assertEqual(context.exception.code, "MANIFEST_UNREADABLE")

    def test_absolute_path_is_rejected(self) -> None:
        self._valid_pair()
        self.core_manifest.write_text(f"{'0' * 64}  /outside.txt\n", encoding="utf-8")
        self._assert_failure("ABSOLUTE_PATH")

    def test_parent_traversal_is_rejected(self) -> None:
        self._valid_pair()
        self.core_manifest.write_text(f"{'0' * 64}  ../outside.txt\n", encoding="utf-8")
        self._assert_failure("PATH_TRAVERSAL")

    def test_duplicate_path_is_rejected(self) -> None:
        data = b"same"
        _write_source(self.source, "same.txt", data)
        _write_manifest(self.core_manifest, [("same.txt", data), ("same.txt", data)])
        _write_manifest(self.negative_manifest, [("other.txt", b"other")])
        _write_source(self.source, "other.txt", b"other")
        self._assert_failure("DUPLICATE_PATH")

    def test_duplicate_path_across_manifests_is_rejected(self) -> None:
        data = b"same"
        _write_source(self.source, "same.txt", data)
        _write_manifest(self.core_manifest, [("same.txt", data)])
        _write_manifest(self.negative_manifest, [("same.txt", data)])
        self._assert_failure("DUPLICATE_PATH")

    def test_directory_entry_is_rejected(self) -> None:
        directory = self.source / "folder"
        directory.mkdir()
        self.core_manifest.write_text(f"{'0' * 64}  folder\n", encoding="utf-8")
        _write_source(self.source, "negative.txt", b"negative")
        _write_manifest(self.negative_manifest, [("negative.txt", b"negative")])
        self._assert_failure("NOT_REGULAR_FILE")

    def test_fifo_entry_is_rejected_without_blocking(self) -> None:
        fifo = self.source / "pipe"
        os.mkfifo(fifo)
        self.core_manifest.write_text(f"{'0' * 64}  pipe\n", encoding="utf-8")
        _write_source(self.source, "negative.txt", b"negative")
        _write_manifest(self.negative_manifest, [("negative.txt", b"negative")])
        self._assert_failure("NOT_REGULAR_FILE")

    def test_symlink_entry_is_rejected(self) -> None:
        target = _write_source(self.source, "target.txt", b"target")
        link = self.source / "link.txt"
        link.symlink_to(target)
        self.core_manifest.write_text(f"{_digest(b'target')}  link.txt\n", encoding="utf-8")
        _write_source(self.source, "negative.txt", b"negative")
        _write_manifest(self.negative_manifest, [("negative.txt", b"negative")])
        self._assert_failure("SYMLINK_REJECTED")

    def test_symlink_source_root_is_rejected(self) -> None:
        self._valid_pair()
        alias = self.base / "source-alias"
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValidationFailure) as context:
            verify_fixture_set(
                source_root=alias,
                core_manifest=self.core_manifest,
                negative_manifest=self.negative_manifest,
            )
        self.assertEqual(context.exception.code, "SOURCE_ROOT_SYMLINK")

    def test_source_root_symlink_ancestor_is_rejected(self) -> None:
        self._valid_pair()
        alias = self.base / "parent-alias"
        alias.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(ValidationFailure) as context:
            verify_fixture_set(
                source_root=alias / "source",
                core_manifest=self.core_manifest,
                negative_manifest=self.negative_manifest,
            )
        self.assertEqual(context.exception.code, "SOURCE_ROOT_SYMLINK")

    def test_audit_output_outside_allowed_root_is_not_touched(self) -> None:
        victim = self.source / "victim.txt"
        victim.write_bytes(b"original")
        with self.assertRaises(AuditWriteFailure):
            write_audit(
                {"schema_version": "test"},
                victim,
                allowed_root=self.base / "artifacts/fixture-audit/v0.1",
            )
        self.assertEqual(victim.read_bytes(), b"original")

    def test_audit_output_symlink_is_rejected_without_touching_target(self) -> None:
        allowed_root = self.base / "artifacts/fixture-audit/v0.1"
        allowed_root.mkdir(parents=True)
        victim = self.base / "victim.txt"
        victim.write_bytes(b"original")
        (allowed_root / "audit.json").symlink_to(victim)
        with self.assertRaises(AuditWriteFailure):
            write_audit(
                {"schema_version": "test"},
                allowed_root / "audit.json",
                allowed_root=allowed_root,
            )
        self.assertEqual(victim.read_bytes(), b"original")

    def test_audit_output_hardlink_is_rejected_without_touching_target(self) -> None:
        allowed_root = self.base / "artifacts/fixture-audit/v0.1"
        allowed_root.mkdir(parents=True)
        victim = self.base / "victim.txt"
        victim.write_bytes(b"original")
        os.link(victim, allowed_root / "audit.json")
        with self.assertRaises(AuditWriteFailure):
            write_audit(
                {"schema_version": "test"},
                allowed_root / "audit.json",
                allowed_root=allowed_root,
            )
        self.assertEqual(victim.read_bytes(), b"original")

    def test_audit_output_fifo_is_rejected_without_blocking(self) -> None:
        allowed_root = self.base / "artifacts/fixture-audit/v0.1"
        allowed_root.mkdir(parents=True)
        os.mkfifo(allowed_root / "audit.json")
        with self.assertRaises(AuditWriteFailure):
            write_audit(
                {"schema_version": "test"},
                allowed_root / "audit.json",
                allowed_root=allowed_root,
            )

    def test_cli_identity_failure_is_redacted_without_traceback(self) -> None:
        self._valid_pair()
        secret_name = "secret-company.pdf"
        self.core_manifest.write_text(f"{'0' * 64}  {secret_name}\n", encoding="utf-8")
        output = self.base / "artifacts/fixture-audit/v0.1/audit.json"
        environment = os.environ.copy()
        project_root = Path(__file__).resolve().parents[1]
        environment["PYTHONPATH"] = str(project_root / "src")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "fixture_gate",
                "verify",
                "--source-root",
                str(self.source),
                "--core-manifest",
                str(self.core_manifest),
                "--negative-manifest",
                str(self.negative_manifest),
                "--output",
                str(output),
            ],
            cwd=self.base,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("code=MANIFEST_IDENTITY_MISMATCH", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn(secret_name, result.stderr)
        self.assertNotIn(secret_name, output.read_text(encoding="utf-8"))

    def test_cli_rejects_unsafe_output_without_traceback(self) -> None:
        self._valid_pair()
        victim = self.source / "victim.txt"
        victim.write_bytes(b"original")
        environment = os.environ.copy()
        project_root = Path(__file__).resolve().parents[1]
        environment["PYTHONPATH"] = str(project_root / "src")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "fixture_gate",
                "verify",
                "--source-root",
                str(self.source),
                "--core-manifest",
                str(self.core_manifest),
                "--negative-manifest",
                str(self.negative_manifest),
                "--output",
                str(victim),
            ],
            cwd=self.base,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("code=AUDIT_WRITE_FAILED", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn(str(victim), result.stderr)
        self.assertEqual(victim.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
