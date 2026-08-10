from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath


_MANIFEST_LINE = re.compile(r"^([0-9a-fA-F]{64}) ([ *])(.+)$")
_READ_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class FixtureIdentity:
    fixture_set_id: str
    fixture_version: str
    core_manifest_sha256: str
    negative_manifest_sha256: str
    core_files: int
    negative_files: int
    core_bytes: int
    negative_bytes: int


ENVIRONMENT_DEMO_V01 = FixtureIdentity(
    fixture_set_id="environment-demo-seed",
    fixture_version="v0.1",
    core_manifest_sha256="e9425d59207461b9ff87c601016932653b8cd3522b7d70ede877ebb5ce6316ae",
    negative_manifest_sha256="2238a2e084e5ff0c37756ed341244bb04cdf8361e45709dfb7939eba7be20e04",
    core_files=24,
    negative_files=2,
    core_bytes=41_500_435,
    negative_bytes=377_765,
)


@dataclass(frozen=True)
class ManifestEntry:
    group: str
    line: int
    expected_sha256: str
    relative_path: str
    path_sha256: str


class ValidationFailure(Exception):
    """A validation error whose public fields contain no source path."""

    def __init__(
        self,
        code: str,
        *,
        group: str | None = None,
        line: int | None = None,
        path_sha256: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.group = group
        self.line = line
        self.path_sha256 = path_sha256

    def public_record(self) -> dict[str, object]:
        record: dict[str, object] = {"code": self.code}
        if self.group is not None:
            record["group"] = self.group
        if self.line is not None:
            record["line"] = self.line
        if self.path_sha256 is not None:
            record["path_sha256"] = self.path_sha256
        return record


class AuditWriteFailure(Exception):
    """An audit write error that never exposes a filesystem path."""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_handle(handle: object) -> str:
    digest = hashlib.sha256()
    while chunk := handle.read(_READ_CHUNK_BYTES):
        digest.update(chunk)
    return digest.hexdigest()


def _open_path_without_symlinks(
    path: Path,
    *,
    final_flags: int,
    unavailable_code: str,
    symlink_code: str,
    group: str | None = None,
) -> int:
    if ".." in path.parts:
        raise ValidationFailure(unavailable_code, group=group)
    absolute = path if path.is_absolute() else Path.cwd() / path
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    try:
        current_descriptor = os.open(absolute.anchor, directory_flags)
    except OSError as error:
        raise ValidationFailure(unavailable_code, group=group) from error
    try:
        for index, part in enumerate(absolute.parts[1:]):
            is_final = index == len(absolute.parts[1:]) - 1
            flags = final_flags if is_final else directory_flags
            flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                next_descriptor = os.open(part, flags, dir_fd=current_descriptor)
            except OSError as error:
                try:
                    component_stat = os.stat(
                        part, dir_fd=current_descriptor, follow_symlinks=False
                    )
                except OSError:
                    component_stat = None
                code = (
                    symlink_code
                    if component_stat is not None
                    and stat.S_ISLNK(component_stat.st_mode)
                    else unavailable_code
                )
                raise ValidationFailure(code, group=group) from error
            os.close(current_descriptor)
            current_descriptor = next_descriptor
        return current_descriptor
    except Exception:
        os.close(current_descriptor)
        raise


def _read_manifest_bytes(path: Path, group: str) -> bytes:
    descriptor = _open_path_without_symlinks(
        path,
        final_flags=os.O_RDONLY | getattr(os, "O_NONBLOCK", 0),
        unavailable_code="MANIFEST_UNREADABLE",
        symlink_code="MANIFEST_UNREADABLE",
        group=group,
    )
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValidationFailure("MANIFEST_UNREADABLE", group=group)
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, _READ_CHUNK_BYTES):
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError as error:
        raise ValidationFailure("MANIFEST_UNREADABLE", group=group) from error
    finally:
        os.close(descriptor)


def _validate_relative_path(
    relative_path: str,
    *,
    group: str,
    line: int,
    path_sha256: str,
) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in relative_path):
        raise ValidationFailure(
            "INVALID_PATH", group=group, line=line, path_sha256=path_sha256
        )
    if "\\" in relative_path:
        raise ValidationFailure(
            "INVALID_PATH", group=group, line=line, path_sha256=path_sha256
        )
    if PurePosixPath(relative_path).is_absolute() or PureWindowsPath(relative_path).is_absolute():
        raise ValidationFailure(
            "ABSOLUTE_PATH", group=group, line=line, path_sha256=path_sha256
        )

    raw_parts = relative_path.split("/")
    if not raw_parts or any(part in {"", ".", ".."} for part in raw_parts):
        code = "PATH_TRAVERSAL" if ".." in raw_parts else "INVALID_PATH"
        raise ValidationFailure(code, group=group, line=line, path_sha256=path_sha256)

    normalized = unicodedata.normalize("NFC", PurePosixPath(relative_path).as_posix())
    if normalized in {"", "."}:
        raise ValidationFailure(
            "INVALID_PATH", group=group, line=line, path_sha256=path_sha256
        )
    return normalized


def _parse_manifest(
    manifest_path: Path,
    group: str,
    seen_paths: set[str],
) -> tuple[list[ManifestEntry], str]:
    manifest_bytes = _read_manifest_bytes(manifest_path, group)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    try:
        lines = manifest_bytes.decode("utf-8").splitlines()
    except UnicodeError as error:
        raise ValidationFailure("MANIFEST_UNREADABLE", group=group) from error

    if not lines:
        raise ValidationFailure("EMPTY_MANIFEST", group=group)

    entries: list[ManifestEntry] = []
    for line_number, raw_line in enumerate(lines, start=1):
        match = _MANIFEST_LINE.fullmatch(raw_line)
        if match is None:
            raise ValidationFailure("MALFORMED_MANIFEST", group=group, line=line_number)

        expected_sha256, _mode, relative_path = match.groups()
        path_sha256 = _sha256_text(relative_path)
        normalized_path = _validate_relative_path(
            relative_path,
            group=group,
            line=line_number,
            path_sha256=path_sha256,
        )
        if normalized_path in seen_paths:
            raise ValidationFailure(
                "DUPLICATE_PATH",
                group=group,
                line=line_number,
                path_sha256=path_sha256,
            )
        seen_paths.add(normalized_path)
        entries.append(
            ManifestEntry(
                group=group,
                line=line_number,
                expected_sha256=expected_sha256.lower(),
                relative_path=relative_path,
                path_sha256=path_sha256,
            )
        )
    return entries, manifest_sha256


def _entry_failure(entry: ManifestEntry, code: str, error: OSError) -> ValidationFailure:
    return ValidationFailure(
        code,
        group=entry.group,
        line=entry.line,
        path_sha256=entry.path_sha256,
    )


def _open_entry(root_descriptor: int, entry: ManifestEntry) -> int:
    current_descriptor = os.dup(root_descriptor)
    parts = PurePosixPath(entry.relative_path).parts
    try:
        for index, part in enumerate(parts):
            final_component = index == len(parts) - 1
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            if final_component:
                flags |= getattr(os, "O_NONBLOCK", 0)
            else:
                flags |= getattr(os, "O_DIRECTORY", 0)
            try:
                next_descriptor = os.open(part, flags, dir_fd=current_descriptor)
            except FileNotFoundError as error:
                raise _entry_failure(entry, "FILE_MISSING", error) from error
            except OSError as error:
                try:
                    component_stat = os.stat(
                        part, dir_fd=current_descriptor, follow_symlinks=False
                    )
                except OSError:
                    component_stat = None
                if component_stat is not None and stat.S_ISLNK(component_stat.st_mode):
                    code = "SYMLINK_REJECTED"
                elif error.errno == errno.ELOOP:
                    code = "SYMLINK_REJECTED"
                elif not final_component and component_stat is not None:
                    code = "NOT_REGULAR_FILE"
                else:
                    code = "FILE_UNREADABLE"
                raise _entry_failure(entry, code, error) from error
            os.close(current_descriptor)
            current_descriptor = next_descriptor
        return current_descriptor
    except Exception:
        os.close(current_descriptor)
        raise


def _verify_entry(
    root_descriptor: int,
    entry: ManifestEntry,
    seen_files: set[tuple[int, int]],
) -> dict[str, object]:
    descriptor = _open_entry(root_descriptor, entry)
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValidationFailure(
                "NOT_REGULAR_FILE",
                group=entry.group,
                line=entry.line,
                path_sha256=entry.path_sha256,
            )

        file_identity = (file_stat.st_dev, file_stat.st_ino)
        if file_identity in seen_files:
            raise ValidationFailure(
                "DUPLICATE_PATH",
                group=entry.group,
                line=entry.line,
                path_sha256=entry.path_sha256,
            )
        seen_files.add(file_identity)

        try:
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                actual_sha256 = _sha256_handle(handle)
        except OSError as error:
            raise ValidationFailure(
                "FILE_UNREADABLE",
                group=entry.group,
                line=entry.line,
                path_sha256=entry.path_sha256,
            ) from error
        final_stat = os.fstat(descriptor)
        before = (
            file_stat.st_dev,
            file_stat.st_ino,
            file_stat.st_size,
            file_stat.st_mtime_ns,
            file_stat.st_ctime_ns,
        )
        after = (
            final_stat.st_dev,
            final_stat.st_ino,
            final_stat.st_size,
            final_stat.st_mtime_ns,
            final_stat.st_ctime_ns,
        )
        if before != after:
            raise ValidationFailure(
                "FILE_CHANGED_DURING_READ",
                group=entry.group,
                line=entry.line,
                path_sha256=entry.path_sha256,
            )
        if actual_sha256 != entry.expected_sha256:
            raise ValidationFailure(
                "HASH_MISMATCH",
                group=entry.group,
                line=entry.line,
                path_sha256=entry.path_sha256,
            )

        return {
            "group": entry.group,
            "line": entry.line,
            "path_sha256": entry.path_sha256,
            "size_bytes": file_stat.st_size,
        }
    finally:
        os.close(descriptor)


def verify_fixture_set(
    *,
    source_root: Path,
    core_manifest: Path,
    negative_manifest: Path,
    expected_identity: FixtureIdentity | None = None,
) -> dict[str, object]:
    seen_paths: set[str] = set()
    core_entries, core_manifest_sha256 = _parse_manifest(
        core_manifest, "core", seen_paths
    )
    negative_entries, negative_manifest_sha256 = _parse_manifest(
        negative_manifest, "negative", seen_paths
    )

    if expected_identity is not None:
        identity_matches = (
            core_manifest_sha256 == expected_identity.core_manifest_sha256
            and negative_manifest_sha256 == expected_identity.negative_manifest_sha256
            and len(core_entries) == expected_identity.core_files
            and len(negative_entries) == expected_identity.negative_files
        )
        if not identity_matches:
            raise ValidationFailure("MANIFEST_IDENTITY_MISMATCH")

    entries = core_entries + negative_entries
    root_descriptor = _open_path_without_symlinks(
        source_root,
        final_flags=os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        unavailable_code="SOURCE_ROOT_UNAVAILABLE",
        symlink_code="SOURCE_ROOT_SYMLINK",
    )

    seen_files: set[tuple[int, int]] = set()
    try:
        verified_entries = [
            _verify_entry(root_descriptor, entry, seen_files) for entry in entries
        ]
    finally:
        os.close(root_descriptor)

    group_summary: dict[str, dict[str, int]] = {
        "core": {"files": 0, "bytes": 0},
        "negative": {"files": 0, "bytes": 0},
    }
    for entry in verified_entries:
        group = str(entry["group"])
        group_summary[group]["files"] += 1
        group_summary[group]["bytes"] += int(entry["size_bytes"])

    if expected_identity is not None:
        bytes_match = (
            group_summary["core"]["bytes"] == expected_identity.core_bytes
            and group_summary["negative"]["bytes"] == expected_identity.negative_bytes
        )
        if not bytes_match:
            raise ValidationFailure("FIXTURE_IDENTITY_MISMATCH")

    public_entries = [
        {
            "group": entry["group"],
            "line": entry["line"],
            "path_sha256": entry["path_sha256"],
        }
        for entry in verified_entries
    ]

    return {
        "schema_version": "fixture-gate-audit/v1",
        "fixture_set_id": (
            expected_identity.fixture_set_id if expected_identity else "unregistered"
        ),
        "fixture_version": (
            expected_identity.fixture_version if expected_identity else "unregistered"
        ),
        "policy": {
            "external_processing": "DENY",
            "model_training": "DENY",
            "production_use": "DENY",
            "public_display": "DENY",
        },
        "manifest_sha256": {
            "core": core_manifest_sha256,
            "negative": negative_manifest_sha256,
        },
        "summary": {
            "core": group_summary["core"],
            "negative": group_summary["negative"],
            "verified": len(verified_entries),
            "failed": 0,
        },
        "entries": public_entries,
    }


def failure_audit(error: ValidationFailure) -> dict[str, object]:
    return {
        "schema_version": "fixture-gate-audit/v1",
        "fixture_set_id": "environment-demo-seed",
        "fixture_version": "v0.1",
        "policy": {
            "external_processing": "DENY",
            "model_training": "DENY",
            "production_use": "DENY",
            "public_display": "DENY",
        },
        "summary": {
            "core": {"files": 0, "bytes": 0},
            "negative": {"files": 0, "bytes": 0},
            "verified": 0,
            "failed": 1,
        },
        "failures": [error.public_record()],
    }


def _ensure_directory_without_symlinks(directory: Path) -> Path:
    absolute = directory if directory.is_absolute() else Path.cwd() / directory
    current = Path(absolute.anchor)
    try:
        for part in absolute.parts[1:]:
            current /= part
            try:
                component_stat = current.lstat()
            except FileNotFoundError:
                current.mkdir(mode=0o700)
                component_stat = current.lstat()
            if stat.S_ISLNK(component_stat.st_mode) or not stat.S_ISDIR(
                component_stat.st_mode
            ):
                raise AuditWriteFailure("AUDIT_WRITE_FAILED")
    except OSError as error:
        raise AuditWriteFailure("AUDIT_WRITE_FAILED") from error
    return absolute


def write_audit(
    audit: dict[str, object], output_path: Path, *, allowed_root: Path
) -> None:
    output_absolute = (
        output_path if output_path.is_absolute() else Path.cwd() / output_path
    )
    allowed_absolute = (
        allowed_root if allowed_root.is_absolute() else Path.cwd() / allowed_root
    )
    if output_absolute != allowed_absolute / "audit.json":
        raise AuditWriteFailure("AUDIT_WRITE_FAILED")

    allowed_absolute = _ensure_directory_without_symlinks(allowed_absolute)
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    descriptor: int | None = None
    directory_descriptor: int | None = None
    try:
        directory_descriptor = os.open(allowed_absolute, directory_flags)
        descriptor = os.open(
            "audit.json",
            os.O_WRONLY
            | os.O_CREAT
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            0o600,
            dir_fd=directory_descriptor,
        )
        output_stat = os.fstat(descriptor)
        if not stat.S_ISREG(output_stat.st_mode) or output_stat.st_nlink != 1:
            raise AuditWriteFailure("AUDIT_WRITE_FAILED")
        os.ftruncate(descriptor, 0)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n", closefd=False) as handle:
            json.dump(audit, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(descriptor)
    except AuditWriteFailure:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise AuditWriteFailure("AUDIT_WRITE_FAILED") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory_descriptor is not None:
            os.close(directory_descriptor)
