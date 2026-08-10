#!/usr/bin/env python3
"""Prepare the sole private, data-only input for F1.1.1 formal acceptance.

The command has no caller-controlled path, Docker target, credential or
question arguments.  It copies the already-authorized provider/key material,
generates new scratch-only credentials, pins the existing read-only F0-I
container through selected inspect fields, and selects only registered
Fixture inputs.  Successful stdout is one fixed token; every failure is
reduced to one body-free reason code.
"""
from __future__ import annotations

import base64
import ctypes
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import uuid
from typing import Any, Callable, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
for _import_root in (ROOT, ROOT / "src"):
    if str(_import_root) not in sys.path:
        sys.path.insert(0, str(_import_root))

from fixture_gate import ENVIRONMENT_DEMO_V01  # noqa: E402
from fixture_router.router import REGISTERED_SOURCE_ROOT  # noqa: E402
from platform_foundation import catalog  # noqa: E402
from tests import f111_clean_rebuild as clean_rebuild  # noqa: E402


OUTPUT_ROOT = Path("/private/tmp/anhuan-f111-formal-inputs")
PROVIDER_SOURCE = Path("/private/tmp/anhuan-f0j1-secrets")
F0I_KEY_SOURCE = Path("/private/tmp/anhuan-f0i-acceptance-v01.key")
F0F_KEY_SOURCE = Path(
    "/private/tmp/anhuan-f111-formal-frozen-inputs/f0f_source_key"
)
F0F_KEY_BUNDLE_NAME = "f0f_source_key"
SOURCE_CONFIG_NAME = "source-config.json"
SECRETS_DIRECTORY_NAME = "secrets"
PROVIDER_DIRECTORY_NAME = "provider"
FIXTURE_DIRECTORY_NAME = "fixtures"
F0I_KEY_NAME = "f0i-key"
CONFIG_SCHEMA = "f1.1.1-formal-source-config-v2"
SOURCE_SCOPE_SCHEMA = "f1.1.1-f0i-source-scope-v1"
F0G_SOURCE_SCOPE_SCHEMA = "f1.1.1-f0g-source-scope-v2"
F0G_SOURCE_SCOPE_NAME = "f0g_source_scope"
MAX_F0G_SOURCE_DUMP_BYTES = 512 * 1024 * 1024
MAX_COPY_BYTES = 4 * 1024 * 1024
MAX_DOCKER_OUTPUT = 128 * 1024
MAX_SOURCE_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_SOURCE_BUNDLE_HEADER_BYTES = 256 * 1024
SOURCE_BUNDLE_NAME = "fixture_source_objects_bundle"
SOURCE_BUNDLE_SCHEMA = "f1.1.1-fixture-source-objects-bundle-v1"
SOURCE_BUNDLE_MAGIC = b"F111FSB1"
RUNTIME_TREE_BUNDLE_SCHEMA = "f1.1.1-frozen-runtime-tree-bundle-v1"
RUNTIME_TREE_BUNDLE_MAGIC = b"F111RTB1"
MAX_RUNTIME_TREE_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_RUNTIME_TREE_HEADER_BYTES = 256 * 1024
RUNTIME_TREE_SOURCE_MODES = frozenset({0o600, 0o644, 0o755})
DOCKER = Path("/usr/local/bin/docker")
DOCKER_DESKTOP_BINARY = Path(
    "/Applications/Docker.app/Contents/Resources/bin/docker"
)
DOCKER_SHA256 = "c9766c884e4f2de2aadf8eba072d4a19f45e7f7535138cd0c8bac143f1c26644"
DOCKER_SOCKET_LINK = Path("/var/run/docker.sock")
DOCKER_EMPTY_CONFIG = Path("/private/var/empty")
GIT = Path("/usr/bin/git")

QUESTION_ALTERNATE = (
    "请仅依据当前已上传的登记资料，指出另一项能够由原文直接支持的信息；"
    "不得使用外部知识，并按系统要求附上原文引用。"
)

SOURCE_SCOPE_KEYS = frozenset(
    {
        "schema",
        "host",
        "published_port",
        "database",
        "access",
        "container_id",
        "container_name",
        "compose_project",
        "compose_service",
        "image_id",
        "image_reference",
    }
)
F0G_SOURCE_SCOPE_KEYS = frozenset(
    {
        "schema",
        "database",
        "role",
        "schemas",
        "access",
        "read_only",
        "container_id",
        "container_name",
        "compose_project",
        "compose_service",
        "image_id",
        "image_reference",
        "published_port",
        "dump_sha256",
        "aggregate_sha256",
    }
)


class PrepError(RuntimeError):
    """One fixed, body-free preparation failure."""

    def __init__(self, code: str) -> None:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,95}", code):
            code = "INTERNAL_FAILURE"
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class FrozenInput:
    bundle_name: str
    relative_path: Path
    sha256: str


@dataclass(frozen=True, slots=True)
class RuntimeTreeContract:
    phase: str
    bundle_name: str
    relative_root: Path
    files: tuple[Path, ...]
    tree_sha256: str


RUNTIME_TREE_CONTRACTS = (
    RuntimeTreeContract(
        phase="f0e",
        bundle_name="f0e_runtime_tree_bundle",
        relative_root=Path("infra/f0e"),
        files=tuple(
            Path(value)
            for value in (
                "Dockerfile",
                "README.md",
                "THIRD_PARTY_NOTICES.md",
                "component-lock.json",
                "compose.yaml",
                "requirements.lock",
                "runner.py",
                "runtime-lock.json",
                "sbom.spdx.json",
                "seccomp.json",
                "synthetic_probe.py",
            )
        ),
        tree_sha256="18108f9d5336b34b7a898b9683b325a251769e4ca080565ef0adda8f2eab7e55",
    ),
    RuntimeTreeContract(
        phase="f0f",
        bundle_name="f0f_runtime_tree_bundle",
        relative_root=Path("infra/f0f"),
        files=tuple(
            Path(value)
            for value in (
                "Dockerfile",
                "README.md",
                "THIRD_PARTY_NOTICES.md",
                "component-lock.json",
                "compose.yaml",
                "runner.py",
                "runtime-lock.json",
                "sbom.spdx.json",
                "seccomp.json",
                "synthetic_probe.py",
            )
        ),
        tree_sha256="229c9078caccdeb6ff0d94ad6da9eab72d167ee89d4236fe73aec3cffe9a7ea6",
    ),
    RuntimeTreeContract(
        phase="f0h",
        bundle_name="f0h_runtime_tree_bundle",
        relative_root=Path("infra/f0h"),
        files=tuple(
            Path(value)
            for value in (
                "Dockerfile",
                "README.md",
                "THIRD_PARTY_NOTICES.md",
                "build-sources/antlr4-python3-runtime-4.9.3.tar.gz",
                "build-sources/packaging-26.3-py3-none-any.whl",
                "build-sources/wheel-0.47.0-py3-none-any.whl",
                "component-lock.json",
                "compose.yaml",
                "model-metadata/config.yaml",
                "model-metadata/default_models.yaml",
                "models/PP-OCRv6_det_small.onnx",
                "models/PP-OCRv6_rec_small.onnx",
                "models/ch_ppocr_mobile_v2.0_cls_mobile.onnx",
                "requirements.lock",
                "runner.py",
                "runtime-lock.json",
                "sbom.spdx.json",
                "seccomp.json",
                "synthetic_probe.py",
                "wheels/antlr4_python3_runtime-4.9.3-py3-none-any.whl",
                "wheels/certifi-2026.7.22-py3-none-any.whl",
                "wheels/charset_normalizer-3.4.9-cp311-cp311-manylinux2014_aarch64.manylinux_2_17_aarch64.manylinux_2_28_aarch64.whl",
                "wheels/colorlog-6.12.0-py3-none-any.whl",
                "wheels/idna-3.18-py3-none-any.whl",
                "wheels/omegaconf-2.3.1-py3-none-any.whl",
                "wheels/rapidocr-3.9.2-py3-none-any.whl",
                "wheels/requests-2.34.2-py3-none-any.whl",
                "wheels/urllib3-2.7.0-py3-none-any.whl",
            )
        ),
        tree_sha256="3b705b9c88f65df44db3afbe5a8b278b2c7e322c8f3f850152f96c93762026d8",
    ),
)


FROZEN_INPUTS = (
    FrozenInput(
        "fixture_route_plan_json",
        Path("artifacts/fixture-routing/v0.1/route-plan.json"),
        "2937047ed5d2c6db7f73ba7d8ba597acd24ec376cde73b5b48e529ac6cf5004c",
    ),
    FrozenInput(
        "fixture_native_plan_json",
        Path("artifacts/fixture-native-plan/v0.1/full-plan.json"),
        "08c8a3e972950cd2be88f86a4f79d9727c39f81e12a321c50cb3a46581534436",
    ),
    # The repair base contains neither frozen manifest.  They remain read-only
    # in the primary worktree and are copied into this private source bundle so
    # a tracked-only checkout can consume the closed catalog without changing
    # the frozen originals.
    FrozenInput(
        "fixture_core_manifest",
        Path("fixtures/environment-demo-seed/v0.1/core-manifest.sha256"),
        ENVIRONMENT_DEMO_V01.core_manifest_sha256,
    ),
    FrozenInput(
        "fixture_negative_manifest",
        Path("fixtures/environment-demo-seed/v0.1/negative-manifest.sha256"),
        ENVIRONMENT_DEMO_V01.negative_manifest_sha256,
    ),
    FrozenInput(
        "f0h_runtime_acceptance_json",
        Path("artifacts/f0h-ppocrv6-runtime/v0.1/acceptance.json"),
        "0d25e1ec9addfa0d24d85523ebd621747835ea40f00d44090c5632dc4676093b",
    ),
)

EVALUATION_SAMPLES_RELATIVE = Path(
    "artifacts/f0j1-retrieval-qa/v0.1/evaluation_samples.json"
)
EVALUATION_SAMPLES_SHA256 = (
    "1bbb124571f548d876c7b106d8533a9ba5cb3b1bac321a6c29f1244150558f61"
)


def _bundle_entry_records(entries: Sequence[Any]) -> tuple[dict[str, Any], ...]:
    expected_count = ENVIRONMENT_DEMO_V01.core_files + ENVIRONMENT_DEMO_V01.negative_files
    if len(entries) != expected_count:
        raise PrepError("SOURCE_BUNDLE_ENTRY_SET_REJECTED")
    records: list[dict[str, Any]] = []
    offset = 0
    seen_ids: set[str] = set()
    seen_positions: set[tuple[str, int]] = set()
    seen_digests: set[str] = set()
    ordered = sorted(
        entries,
        key=lambda entry: (
            str(getattr(entry, "group", "")),
            int(getattr(entry, "line", -1)),
            str(getattr(entry, "source_id", "")),
        ),
    )
    for entry in ordered:
        source_id = str(getattr(entry, "source_id", ""))
        group = str(getattr(entry, "group", ""))
        line = getattr(entry, "line", None)
        digest = str(getattr(entry, "expected_sha256", ""))
        size = getattr(entry, "expected_size", None)
        try:
            parsed_id = uuid.UUID(source_id)
        except ValueError:
            raise PrepError("SOURCE_BUNDLE_ENTRY_SET_REJECTED") from None
        if (
            parsed_id.version != 5
            or group not in {"core", "negative"}
            or type(line) is not int
            or line < 1
            or type(size) is not int
            or size < 1
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or source_id in seen_ids
            or (group, line) in seen_positions
            or digest in seen_digests
        ):
            raise PrepError("SOURCE_BUNDLE_ENTRY_SET_REJECTED")
        records.append(
            {
                "source_id": source_id,
                "group": group,
                "line": line,
                "sha256": digest,
                "size": size,
                "offset": offset,
            }
        )
        offset += size
        if offset > MAX_SOURCE_BUNDLE_BYTES:
            raise PrepError("SOURCE_BUNDLE_OVERSIZE")
        seen_ids.add(source_id)
        seen_positions.add((group, line))
        seen_digests.add(digest)
    return tuple(records)


def _source_bundle_header(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    payload_size = sum(int(record["size"]) for record in records)
    return {
        "schema": SOURCE_BUNDLE_SCHEMA,
        "entry_count": len(records),
        "payload_size": payload_size,
        "entries": [dict(record) for record in records],
    }


def _write_all(descriptor: int, raw: bytes, code: str) -> None:
    view = memoryview(raw)
    while view:
        try:
            written = os.write(descriptor, view)
        except OSError:
            raise PrepError(code) from None
        if written <= 0:
            raise PrepError(code)
        view = view[written:]


def _read_exact(descriptor: int, size: int, code: str) -> bytes:
    if type(size) is not int or size < 0:
        raise PrepError(code)
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        try:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
        except OSError:
            raise PrepError(code) from None
        if not chunk:
            raise PrepError(code)
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _source_identity_and_digest(entry: Any) -> tuple[tuple[int, ...], str, int]:
    try:
        with catalog.open_catalog_source(entry) as descriptor:
            before = os.fstat(descriptor)
            digest, size = _digest_descriptor(descriptor, MAX_SOURCE_BUNDLE_BYTES)
            after = os.fstat(descriptor)
    except Exception:
        raise PrepError("SOURCE_BUNDLE_SOURCE_REJECTED") from None
    if (
        _identity(before) != _identity(after)
        or before.st_uid != os.geteuid()
        or before.st_nlink != 1
        or size != getattr(entry, "expected_size", None)
        or digest != getattr(entry, "expected_sha256", None)
    ):
        raise PrepError("SOURCE_BUNDLE_SOURCE_REJECTED")
    return _identity(before), digest, size


def _write_fixture_source_bundle(
    entries: Sequence[Any], destination: Path
) -> tuple[dict[str, Any], ...]:
    records = _bundle_entry_records(entries)
    by_id = {str(getattr(entry, "source_id", "")): entry for entry in entries}
    before = {
        record["source_id"]: _source_identity_and_digest(by_id[record["source_id"]])
        for record in records
    }
    header = _canonical_bytes(_source_bundle_header(records))
    if not 2 <= len(header) <= MAX_SOURCE_BUNDLE_HEADER_BYTES:
        raise PrepError("SOURCE_BUNDLE_HEADER_REJECTED")
    total_size = len(SOURCE_BUNDLE_MAGIC) + 8 + len(header) + sum(
        int(record["size"]) for record in records
    )
    if total_size > MAX_SOURCE_BUNDLE_BYTES:
        raise PrepError("SOURCE_BUNDLE_OVERSIZE")
    if _lexists(destination):
        raise PrepError("SOURCE_BUNDLE_TARGET_OCCUPIED")
    _regular_directory(destination.parent, "SOURCE_BUNDLE_WRITE_REJECTED", private=True)
    descriptor = -1
    published = False
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        _write_all(descriptor, SOURCE_BUNDLE_MAGIC, "SOURCE_BUNDLE_WRITE_REJECTED")
        _write_all(
            descriptor,
            struct.pack(">Q", len(header)),
            "SOURCE_BUNDLE_WRITE_REJECTED",
        )
        _write_all(descriptor, header, "SOURCE_BUNDLE_WRITE_REJECTED")
        for record in records:
            entry = by_id[record["source_id"]]
            digest = hashlib.sha256()
            copied = 0
            try:
                with catalog.open_catalog_source(entry) as source_descriptor:
                    source_before = os.fstat(source_descriptor)
                    if _identity(source_before) != before[record["source_id"]][0]:
                        raise PrepError("SOURCE_BUNDLE_SOURCE_MUTATED")
                    remaining = int(record["size"])
                    while remaining:
                        chunk = os.read(source_descriptor, min(1024 * 1024, remaining))
                        if not chunk:
                            raise PrepError("SOURCE_BUNDLE_SOURCE_MUTATED")
                        _write_all(descriptor, chunk, "SOURCE_BUNDLE_WRITE_REJECTED")
                        digest.update(chunk)
                        copied += len(chunk)
                        remaining -= len(chunk)
                    source_after = os.fstat(source_descriptor)
            except PrepError:
                raise
            except Exception:
                raise PrepError("SOURCE_BUNDLE_SOURCE_REJECTED") from None
            if (
                _identity(source_after) != before[record["source_id"]][0]
                or copied != record["size"]
                or digest.hexdigest() != record["sha256"]
            ):
                raise PrepError("SOURCE_BUNDLE_SOURCE_MUTATED")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        _validate_fixture_source_bundle(destination, records)
        after = {
            record["source_id"]: _source_identity_and_digest(
                by_id[record["source_id"]]
            )
            for record in records
        }
        if after != before:
            raise PrepError("SOURCE_BUNDLE_SOURCE_MUTATED")
        published = True
    except PrepError:
        if not published:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    except OSError:
        if not published:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        raise PrepError("SOURCE_BUNDLE_WRITE_REJECTED") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return records


def _validate_bundle_record(record: Any) -> dict[str, Any]:
    keys = {"source_id", "group", "line", "sha256", "size", "offset"}
    if not isinstance(record, dict) or set(record) != keys:
        raise PrepError("SOURCE_BUNDLE_HEADER_REJECTED")
    try:
        parsed_id = uuid.UUID(str(record["source_id"]))
    except ValueError:
        raise PrepError("SOURCE_BUNDLE_HEADER_REJECTED") from None
    if (
        parsed_id.version != 5
        or record["group"] not in {"core", "negative"}
        or type(record["line"]) is not int
        or record["line"] < 1
        or not re.fullmatch(r"[0-9a-f]{64}", str(record["sha256"]))
        or type(record["size"]) is not int
        or record["size"] < 1
        or type(record["offset"]) is not int
        or record["offset"] < 0
    ):
        raise PrepError("SOURCE_BUNDLE_HEADER_REJECTED")
    return dict(record)


def _validate_fixture_source_bundle(
    path: Path,
    expected_records: Sequence[Mapping[str, Any]] | None = None,
) -> None:
    try:
        info = path.lstat()
    except OSError:
        raise PrepError("SOURCE_BUNDLE_READ_REJECTED") from None
    if info.st_size > MAX_SOURCE_BUNDLE_BYTES:
        raise PrepError("SOURCE_BUNDLE_OVERSIZE")
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or info.st_size < 18
    ):
        raise PrepError("SOURCE_BUNDLE_READ_REJECTED")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        before = os.fstat(descriptor)
        if (
            _identity(info) != _identity(before)
            or not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or not 18 <= before.st_size <= MAX_SOURCE_BUNDLE_BYTES
        ):
            raise PrepError("SOURCE_BUNDLE_READ_REJECTED")
        magic = _read_exact(
            descriptor, len(SOURCE_BUNDLE_MAGIC), "SOURCE_BUNDLE_HEADER_REJECTED"
        )
        length_raw = _read_exact(descriptor, 8, "SOURCE_BUNDLE_HEADER_REJECTED")
        if magic != SOURCE_BUNDLE_MAGIC:
            raise PrepError("SOURCE_BUNDLE_HEADER_REJECTED")
        header_length = struct.unpack(">Q", length_raw)[0]
        if not 2 <= header_length <= MAX_SOURCE_BUNDLE_HEADER_BYTES:
            raise PrepError("SOURCE_BUNDLE_HEADER_REJECTED")
        header_raw = _read_exact(
            descriptor, header_length, "SOURCE_BUNDLE_TRUNCATED"
        )
        header = _load_unique_json(header_raw)
        if (
            not isinstance(header, dict)
            or set(header) != {"schema", "entry_count", "payload_size", "entries"}
            or header.get("schema") != SOURCE_BUNDLE_SCHEMA
            or type(header.get("entry_count")) is not int
            or type(header.get("payload_size")) is not int
            or not isinstance(header.get("entries"), list)
            or header_raw != _canonical_bytes(header)
        ):
            raise PrepError("SOURCE_BUNDLE_HEADER_REJECTED")
        records = tuple(_validate_bundle_record(value) for value in header["entries"])
        expected_count = (
            ENVIRONMENT_DEMO_V01.core_files + ENVIRONMENT_DEMO_V01.negative_files
        )
        if (
            header["entry_count"] != len(records)
            or len(records) != expected_count
            or header["payload_size"] != sum(record["size"] for record in records)
        ):
            raise PrepError("SOURCE_BUNDLE_ENTRY_SET_REJECTED")
        if tuple(records) != tuple(
            sorted(
                records,
                key=lambda value: (
                    value["group"],
                    value["line"],
                    value["source_id"],
                ),
            )
        ):
            raise PrepError("SOURCE_BUNDLE_ORDER_REJECTED")
        if expected_records is not None and tuple(records) != tuple(
            dict(value) for value in expected_records
        ):
            raise PrepError("SOURCE_BUNDLE_ENTRY_SET_REJECTED")
        expected_offset = 0
        seen_ids: set[str] = set()
        seen_positions: set[tuple[str, int]] = set()
        seen_digests: set[str] = set()
        for record in records:
            if (
                record["offset"] != expected_offset
                or record["source_id"] in seen_ids
                or (record["group"], record["line"]) in seen_positions
                or record["sha256"] in seen_digests
            ):
                raise PrepError("SOURCE_BUNDLE_OFFSET_REJECTED")
            digest = hashlib.sha256()
            remaining = record["size"]
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise PrepError("SOURCE_BUNDLE_TRUNCATED")
                digest.update(chunk)
                remaining -= len(chunk)
            if digest.hexdigest() != record["sha256"]:
                raise PrepError("SOURCE_BUNDLE_BODY_REJECTED")
            expected_offset += record["size"]
            seen_ids.add(record["source_id"])
            seen_positions.add((record["group"], record["line"]))
            seen_digests.add(record["sha256"])
        if expected_offset != header["payload_size"] or os.read(descriptor, 1):
            raise PrepError("SOURCE_BUNDLE_SIZE_REJECTED")
        after = os.fstat(descriptor)
        if _identity(info) != _identity(before) or _identity(before) != _identity(after):
            raise PrepError("SOURCE_BUNDLE_MUTATED")
    except PrepError:
        raise
    except OSError:
        raise PrepError("SOURCE_BUNDLE_READ_REJECTED") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validated_runtime_tree_contract(
    contract: RuntimeTreeContract,
) -> tuple[str, ...]:
    if (
        not isinstance(contract, RuntimeTreeContract)
        or not re.fullmatch(r"f0[efh]|test", contract.phase)
        or not re.fullmatch(r"[a-z0-9_]{1,64}", contract.bundle_name)
        or contract.relative_root.is_absolute()
        or not contract.relative_root.parts
        or any(part in {"", ".", ".."} for part in contract.relative_root.parts)
        or not re.fullmatch(r"[0-9a-f]{64}", contract.tree_sha256)
        or not contract.files
    ):
        raise PrepError("RUNTIME_TREE_CONTRACT_REJECTED")
    names: list[str] = []
    for relative in contract.files:
        if (
            not isinstance(relative, Path)
            or relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
            or relative.as_posix() != str(relative)
        ):
            raise PrepError("RUNTIME_TREE_CONTRACT_REJECTED")
        names.append(relative.as_posix())
    if names != sorted(names) or len(names) != len(set(names)):
        raise PrepError("RUNTIME_TREE_CONTRACT_REJECTED")
    return tuple(names)


def _runtime_tree_snapshot(
    source_root: Path,
    contract: RuntimeTreeContract,
) -> tuple[tuple[dict[str, Any], ...], dict[str, tuple[int, ...]]]:
    expected_files = _validated_runtime_tree_contract(contract)
    _regular_directory(source_root, "RUNTIME_TREE_SOURCE_REJECTED", private=False)
    expected_directories = {
        parent.as_posix()
        for relative in contract.files
        for parent in relative.parents
        if parent != Path(".")
    }
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    identities: dict[str, tuple[int, ...]] = {
        "D:": _identity(source_root.lstat())
    }
    try:
        for current_raw, directory_names, file_names in os.walk(
            source_root, topdown=True, followlinks=False
        ):
            current = Path(current_raw)
            for name in directory_names:
                path = current / name
                info = path.lstat()
                relative = path.relative_to(source_root).as_posix()
                if (
                    stat.S_ISLNK(info.st_mode)
                    or not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) not in {0o700, 0o755}
                    or relative not in expected_directories
                ):
                    raise PrepError("RUNTIME_TREE_SOURCE_REJECTED")
                actual_directories.add(relative)
                identities["D:" + relative] = _identity(info)
            for name in file_names:
                path = current / name
                relative = path.relative_to(source_root).as_posix()
                if relative not in expected_files:
                    raise PrepError("RUNTIME_TREE_SOURCE_REJECTED")
                actual_files.add(relative)
    except PrepError:
        raise
    except (OSError, ValueError):
        raise PrepError("RUNTIME_TREE_SOURCE_REJECTED") from None
    if actual_files != set(expected_files) or actual_directories != expected_directories:
        raise PrepError("RUNTIME_TREE_SOURCE_REJECTED")

    records: list[dict[str, Any]] = []
    for relative in expected_files:
        path = source_root / Path(relative)
        descriptor = -1
        try:
            info = path.lstat()
            descriptor = os.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            before = os.fstat(descriptor)
            digest, size = _digest_descriptor(
                descriptor, MAX_RUNTIME_TREE_BUNDLE_BYTES
            )
            after = os.fstat(descriptor)
        except PrepError:
            raise PrepError("RUNTIME_TREE_SOURCE_REJECTED") from None
        except OSError:
            raise PrepError("RUNTIME_TREE_SOURCE_REJECTED") from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        mode = stat.S_IMODE(info.st_mode)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or mode not in RUNTIME_TREE_SOURCE_MODES
            or size < 1
            or _identity(info) != _identity(before)
            or _identity(before) != _identity(after)
        ):
            raise PrepError("RUNTIME_TREE_SOURCE_REJECTED")
        records.append(
            {
                "relative_path": relative,
                "mode": mode,
                "sha256": digest,
                "size": size,
            }
        )
        identities["F:" + relative] = _identity(info)
    if (
        sum(int(record["size"]) for record in records)
        > MAX_RUNTIME_TREE_BUNDLE_BYTES
        or _sha256(_canonical_bytes(records)) != contract.tree_sha256
    ):
        raise PrepError("RUNTIME_TREE_CONTRACT_REJECTED")
    return tuple(records), identities


def _runtime_tree_header(
    contract: RuntimeTreeContract, records: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    offset = 0
    entries: list[dict[str, Any]] = []
    for record in records:
        entry = dict(record)
        entry["offset"] = offset
        entries.append(entry)
        offset += int(record["size"])
    return {
        "schema": RUNTIME_TREE_BUNDLE_SCHEMA,
        "phase": contract.phase,
        "entry_count": len(entries),
        "payload_size": offset,
        "tree_sha256": contract.tree_sha256,
        "entries": entries,
    }


def _write_runtime_tree_bundle(
    source_root: Path,
    contract: RuntimeTreeContract,
    destination: Path,
) -> None:
    records, before_identities = _runtime_tree_snapshot(source_root, contract)
    header = _canonical_bytes(_runtime_tree_header(contract, records))
    total_size = (
        len(RUNTIME_TREE_BUNDLE_MAGIC)
        + 8
        + len(header)
        + sum(int(record["size"]) for record in records)
    )
    if (
        not 2 <= len(header) <= MAX_RUNTIME_TREE_HEADER_BYTES
        or total_size > MAX_RUNTIME_TREE_BUNDLE_BYTES
        or _lexists(destination)
    ):
        raise PrepError(
            "RUNTIME_TREE_TARGET_OCCUPIED"
            if _lexists(destination)
            else "RUNTIME_TREE_OVERSIZE"
        )
    _regular_directory(destination.parent, "RUNTIME_TREE_WRITE_REJECTED", private=True)
    descriptor = -1
    complete = False
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        _write_all(descriptor, RUNTIME_TREE_BUNDLE_MAGIC, "RUNTIME_TREE_WRITE_REJECTED")
        _write_all(
            descriptor,
            struct.pack(">Q", len(header)),
            "RUNTIME_TREE_WRITE_REJECTED",
        )
        _write_all(descriptor, header, "RUNTIME_TREE_WRITE_REJECTED")
        for record in records:
            relative = str(record["relative_path"])
            source_descriptor = -1
            try:
                source_descriptor = os.open(
                    source_root / Path(relative),
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                )
                source_before = os.fstat(source_descriptor)
                if _identity(source_before) != before_identities["F:" + relative]:
                    raise PrepError("RUNTIME_TREE_SOURCE_MUTATED")
                digest = hashlib.sha256()
                remaining = int(record["size"])
                while remaining:
                    chunk = os.read(source_descriptor, min(1024 * 1024, remaining))
                    if not chunk:
                        raise PrepError("RUNTIME_TREE_SOURCE_MUTATED")
                    _write_all(descriptor, chunk, "RUNTIME_TREE_WRITE_REJECTED")
                    digest.update(chunk)
                    remaining -= len(chunk)
                source_after = os.fstat(source_descriptor)
            except PrepError:
                raise
            except OSError:
                raise PrepError("RUNTIME_TREE_SOURCE_REJECTED") from None
            finally:
                if source_descriptor >= 0:
                    os.close(source_descriptor)
            if (
                _identity(source_after) != before_identities["F:" + relative]
                or digest.hexdigest() != record["sha256"]
            ):
                raise PrepError("RUNTIME_TREE_SOURCE_MUTATED")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        _validate_runtime_tree_bundle(destination, contract)
        after_records, after_identities = _runtime_tree_snapshot(source_root, contract)
        if after_records != records or after_identities != before_identities:
            raise PrepError("RUNTIME_TREE_SOURCE_MUTATED")
        complete = True
    except PrepError:
        raise
    except OSError:
        raise PrepError("RUNTIME_TREE_WRITE_REJECTED") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not complete:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass


def _validated_runtime_tree_entry(value: Any) -> dict[str, Any]:
    keys = {"relative_path", "mode", "sha256", "size", "offset"}
    if not isinstance(value, dict) or set(value) != keys:
        raise PrepError("RUNTIME_TREE_HEADER_REJECTED")
    relative = Path(str(value["relative_path"]))
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.as_posix() != value["relative_path"]
        or type(value["mode"]) is not int
        or value["mode"] not in RUNTIME_TREE_SOURCE_MODES
        or not re.fullmatch(r"[0-9a-f]{64}", str(value["sha256"]))
        or type(value["size"]) is not int
        or value["size"] < 1
        or type(value["offset"]) is not int
        or value["offset"] < 0
    ):
        raise PrepError("RUNTIME_TREE_HEADER_REJECTED")
    return dict(value)


def _validate_runtime_tree_bundle(
    path: Path, contract: RuntimeTreeContract
) -> None:
    expected_files = _validated_runtime_tree_contract(contract)
    descriptor = -1
    try:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or not 18 <= info.st_size <= MAX_RUNTIME_TREE_BUNDLE_BYTES
        ):
            raise PrepError("RUNTIME_TREE_READ_REJECTED")
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        before = os.fstat(descriptor)
        if _identity(info) != _identity(before):
            raise PrepError("RUNTIME_TREE_READ_REJECTED")
        magic = _read_exact(
            descriptor, len(RUNTIME_TREE_BUNDLE_MAGIC), "RUNTIME_TREE_HEADER_REJECTED"
        )
        header_size = struct.unpack(
            ">Q", _read_exact(descriptor, 8, "RUNTIME_TREE_HEADER_REJECTED")
        )[0]
        if magic != RUNTIME_TREE_BUNDLE_MAGIC or not 2 <= header_size <= MAX_RUNTIME_TREE_HEADER_BYTES:
            raise PrepError("RUNTIME_TREE_HEADER_REJECTED")
        header_raw = _read_exact(descriptor, header_size, "RUNTIME_TREE_TRUNCATED")
        header = _load_unique_json(header_raw)
        if (
            not isinstance(header, dict)
            or set(header)
            != {
                "schema",
                "phase",
                "entry_count",
                "payload_size",
                "tree_sha256",
                "entries",
            }
            or header.get("schema") != RUNTIME_TREE_BUNDLE_SCHEMA
            or header.get("phase") != contract.phase
            or header.get("tree_sha256") != contract.tree_sha256
            or type(header.get("entry_count")) is not int
            or type(header.get("payload_size")) is not int
            or not isinstance(header.get("entries"), list)
            or header_raw != _canonical_bytes(header)
        ):
            raise PrepError("RUNTIME_TREE_HEADER_REJECTED")
        entries = tuple(
            _validated_runtime_tree_entry(value) for value in header["entries"]
        )
        if (
            header["entry_count"] != len(entries)
            or tuple(value["relative_path"] for value in entries) != expected_files
            or tuple(entries)
            != tuple(sorted(entries, key=lambda value: value["relative_path"]))
        ):
            raise PrepError("RUNTIME_TREE_ENTRY_SET_REJECTED")
        tree_records = [
            {
                "relative_path": value["relative_path"],
                "mode": value["mode"],
                "sha256": value["sha256"],
                "size": value["size"],
            }
            for value in entries
        ]
        if _sha256(_canonical_bytes(tree_records)) != contract.tree_sha256:
            raise PrepError("RUNTIME_TREE_ENTRY_SET_REJECTED")
        expected_offset = 0
        for value in entries:
            if value["offset"] != expected_offset:
                raise PrepError("RUNTIME_TREE_OFFSET_REJECTED")
            digest = hashlib.sha256()
            remaining = int(value["size"])
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise PrepError("RUNTIME_TREE_TRUNCATED")
                digest.update(chunk)
                remaining -= len(chunk)
            if digest.hexdigest() != value["sha256"]:
                raise PrepError("RUNTIME_TREE_BODY_REJECTED")
            expected_offset += int(value["size"])
        if (
            header["payload_size"] != expected_offset
            or os.read(descriptor, 1)
            or info.st_size != len(RUNTIME_TREE_BUNDLE_MAGIC) + 8 + header_size + expected_offset
        ):
            raise PrepError("RUNTIME_TREE_SIZE_REJECTED")
        after = os.fstat(descriptor)
        if _identity(before) != _identity(after):
            raise PrepError("RUNTIME_TREE_MUTATED")
    except PrepError:
        raise
    except OSError:
        raise PrepError("RUNTIME_TREE_READ_REJECTED") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("duplicate json key")
    return value


def _load_unique_json(raw: bytes) -> Any:
    try:
        return json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise PrepError("JSON_DUPLICATE_KEY") from None


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _lexists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_mode),
        int(info.st_uid),
        int(info.st_nlink),
        int(info.st_size),
        int(info.st_mtime_ns),
        int(info.st_ctime_ns),
    )


def _regular_directory(path: Path, code: str, *, private: bool) -> Path:
    if not path.is_absolute():
        raise PrepError(code)
    try:
        info = path.lstat()
        canonical = path.resolve(strict=True)
    except OSError:
        raise PrepError(code) from None
    if (
        canonical != path
        or not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or (private and stat.S_IMODE(info.st_mode) != 0o700)
    ):
        raise PrepError(code)
    return path


def _write_private(path: Path, raw: bytes, code: str = "PRIVATE_WRITE_REJECTED") -> None:
    if not raw or _lexists(path):
        raise PrepError(code)
    _regular_directory(path.parent, code, private=True)
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or info.st_size != len(raw)
        ):
            raise PrepError(code)
    except PrepError:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    except OSError:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise PrepError(code) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _digest_descriptor(descriptor: int, maximum: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > maximum:
            raise PrepError("SOURCE_FILE_REJECTED")
        digest.update(chunk)
    return digest.hexdigest(), total


def _copy_stable_file(
    source: Path,
    destination: Path,
    *,
    code: str,
    allowed_modes: frozenset[int],
    expected_sha256: str | None = None,
    maximum: int = MAX_COPY_BYTES,
) -> None:
    """Copy one owner file while proving source identity and digest stability."""

    if (
        not source.is_absolute()
        or not allowed_modes
        or expected_sha256 is not None
        and not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
    ):
        raise PrepError(code)
    try:
        if source.resolve(strict=True) != source:
            raise PrepError(code)
    except OSError:
        raise PrepError(code) from None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    source_descriptor = -1
    target_descriptor = -1
    published = False
    first_identity: tuple[int, ...] | None = None
    first_digest = ""
    try:
        source_descriptor = os.open(source, flags)
        before = os.fstat(source_descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) not in allowed_modes
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or not 0 < before.st_size <= maximum
        ):
            raise PrepError(code)
        first_identity = _identity(before)
        if _lexists(destination):
            raise PrepError(code)
        _regular_directory(destination.parent, code, private=True)
        target_descriptor = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(source_descriptor, min(1024 * 1024, maximum + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise PrepError(code)
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(target_descriptor, view)
                if written <= 0:
                    raise PrepError(code)
                view = view[written:]
        os.fsync(target_descriptor)
        after = os.fstat(source_descriptor)
        if total != before.st_size or _identity(after) != first_identity:
            raise PrepError(code)
        first_digest = digest.hexdigest()
        if expected_sha256 is not None and first_digest != expected_sha256:
            raise PrepError(code)
        os.close(target_descriptor)
        target_descriptor = -1
        os.close(source_descriptor)
        source_descriptor = -1

        source_descriptor = os.open(source, flags)
        second_before = os.fstat(source_descriptor)
        second_digest, second_size = _digest_descriptor(source_descriptor, maximum)
        second_after = os.fstat(source_descriptor)
        if (
            _identity(second_before) != first_identity
            or _identity(second_after) != first_identity
            or second_size != before.st_size
            or second_digest != first_digest
        ):
            raise PrepError(code)
        target = destination.lstat()
        if (
            not stat.S_ISREG(target.st_mode)
            or stat.S_ISLNK(target.st_mode)
            or stat.S_IMODE(target.st_mode) != 0o600
            or target.st_uid != os.geteuid()
            or target.st_nlink != 1
            or target.st_size != before.st_size
        ):
            raise PrepError(code)
        published = True
    except PrepError:
        if not published:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    except OSError:
        if not published:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        raise PrepError(code) from None
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if target_descriptor >= 0:
            os.close(target_descriptor)


def _read_stable_file(
    source: Path,
    *,
    code: str,
    expected_sha256: str,
    maximum: int = MAX_COPY_BYTES,
) -> bytes:
    """Read a fixed private input twice without exposing its body."""

    try:
        if not source.is_absolute() or source.resolve(strict=True) != source:
            raise PrepError(code)
    except OSError:
        raise PrepError(code) from None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    first = b""
    identity: tuple[int, ...] | None = None
    for attempt in range(2):
        descriptor = -1
        try:
            descriptor = os.open(source, flags)
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_uid != os.geteuid()
                or before.st_nlink != 1
                or not 1 <= before.st_size <= maximum
            ):
                raise PrepError(code)
            chunks: list[bytes] = []
            remaining = before.st_size
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise PrepError(code)
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
            raw = b"".join(chunks)
            observed_identity = _identity(before)
            if _identity(after) != observed_identity or _sha256(raw) != expected_sha256:
                raise PrepError(code)
            if attempt == 0:
                first = raw
                identity = observed_identity
            elif raw != first or observed_identity != identity:
                raise PrepError(code)
        except OSError:
            raise PrepError(code) from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    return first


def _fixed_process(
    executable: Path,
    arguments: Sequence[str],
    *,
    cwd: Path,
    code: str,
    timeout: int = 30,
) -> bytes:
    try:
        executable_info = executable.lstat()
    except OSError:
        raise PrepError(code) from None
    if (
        not stat.S_ISREG(executable_info.st_mode)
        or stat.S_ISLNK(executable_info.st_mode)
        or not os.access(executable, os.X_OK)
        or any(not isinstance(value, str) or "\x00" in value for value in arguments)
    ):
        raise PrepError(code)
    environment = {
        "HOME": "/private/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "TMPDIR": "/private/tmp",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }
    try:
        completed = subprocess.run(
            (str(executable), *arguments),
            cwd=str(cwd),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PrepError(code) from None
    output = bytes(completed.stdout)
    if completed.returncode != 0 or len(output) > MAX_DOCKER_OUTPUT:
        raise PrepError(code)
    return output


def _docker_binary_identity(path: Path, code: str) -> tuple[int, ...]:
    descriptor = -1
    try:
        if not path.is_absolute() or path.resolve(strict=True) != path:
            raise PrepError(code)
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {0, os.geteuid()}
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or not 1 <= before.st_size <= 128 * 1024 * 1024
            or not os.access(path, os.X_OK)
        ):
            raise PrepError(code)
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise PrepError(code)
            digest.update(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        identity = _identity(before)
        if (
            _identity(after) != identity
            or digest.hexdigest() != DOCKER_SHA256
        ):
            raise PrepError(code)
        return identity
    except PrepError:
        raise
    except OSError:
        raise PrepError(code) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _trusted_docker_executable(code: str) -> tuple[Path, tuple[int, ...]]:
    try:
        launcher = DOCKER.lstat()
        link = Path(os.readlink(DOCKER))
        resolved = DOCKER.resolve(strict=True)
    except OSError:
        raise PrepError(code) from None
    if (
        not stat.S_ISLNK(launcher.st_mode)
        or launcher.st_uid not in {0, os.geteuid()}
        or not link.is_absolute()
        or link != DOCKER_DESKTOP_BINARY
        or resolved != DOCKER_DESKTOP_BINARY
    ):
        raise PrepError(code)
    return resolved, _docker_binary_identity(resolved, code)


def _docker_environment(code: str) -> dict[str, str]:
    try:
        empty_info = DOCKER_EMPTY_CONFIG.lstat()
        socket_link_info = DOCKER_SOCKET_LINK.lstat()
        socket_target = DOCKER_SOCKET_LINK.resolve(strict=True)
        socket_info = socket_target.stat()
        empty = tuple(DOCKER_EMPTY_CONFIG.iterdir())
    except OSError:
        raise PrepError(code) from None
    if (
        not stat.S_ISDIR(empty_info.st_mode)
        or stat.S_ISLNK(empty_info.st_mode)
        or empty_info.st_uid not in {0, os.geteuid()}
        or stat.S_IMODE(empty_info.st_mode) & 0o022
        or empty
        or not stat.S_ISLNK(socket_link_info.st_mode)
        or socket_link_info.st_uid not in {0, os.geteuid()}
        or not socket_target.is_absolute()
        or not stat.S_ISSOCK(socket_info.st_mode)
        or socket_info.st_uid != os.geteuid()
        or stat.S_IMODE(socket_info.st_mode) & 0o022
    ):
        raise PrepError(code)
    return {
        "HOME": str(DOCKER_EMPTY_CONFIG),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "TMPDIR": "/private/tmp",
        "DOCKER_CONFIG": str(DOCKER_EMPTY_CONFIG),
        "DOCKER_HOST": "unix:///var/run/docker.sock",
    }


def _docker_command_contract(
    arguments: Sequence[str], code: str
) -> tuple[Path, tuple[int, ...], dict[str, str]]:
    if any(not isinstance(value, str) or "\x00" in value for value in arguments):
        raise PrepError(code)
    executable, identity = _trusted_docker_executable(code)
    return executable, identity, _docker_environment(code)


def _verify_docker_command_contract(
    executable: Path, identity: tuple[int, ...], code: str
) -> None:
    observed, observed_identity = _trusted_docker_executable(code)
    _docker_environment(code)
    if observed != executable or observed_identity != identity:
        raise PrepError(code)


def _docker_output(arguments: tuple[str, ...]) -> bytes:
    code = "DOCKER_READ_REJECTED"
    executable, identity, environment = _docker_command_contract(arguments, code)
    try:
        completed = subprocess.run(
            (str(executable), *arguments),
            cwd=str(ROOT),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        _verify_docker_command_contract(executable, identity, code)
        raise PrepError(code) from None
    _verify_docker_command_contract(executable, identity, code)
    output = bytes(completed.stdout)
    if completed.returncode != 0 or len(output) > MAX_DOCKER_OUTPUT:
        raise PrepError(code)
    return output


def _docker_output_to_private_file(
    arguments: tuple[str, ...], destination: Path
) -> None:
    if _lexists(destination):
        raise PrepError("F0G_SOURCE_DUMP_REJECTED")
    _regular_directory(
        destination.parent, "F0G_SOURCE_DUMP_REJECTED", private=True
    )
    code = "F0G_SOURCE_DUMP_REJECTED"
    executable, identity, environment = _docker_command_contract(arguments, code)
    descriptor = -1
    complete = False
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            try:
                completed = subprocess.run(
                    (str(executable), *arguments),
                    cwd=str(ROOT),
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=900,
                )
            except (OSError, subprocess.TimeoutExpired):
                _verify_docker_command_contract(executable, identity, code)
                raise PrepError(code) from None
            _verify_docker_command_contract(executable, identity, code)
            stream.flush()
            os.fsync(stream.fileno())
        info = destination.lstat()
        if (
            completed.returncode != 0
            or not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or not 0 < info.st_size <= MAX_F0G_SOURCE_DUMP_BYTES
        ):
            raise PrepError(code)
        complete = True
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not complete:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass


def _derive_primary_root() -> Path:
    raw = _fixed_process(
        GIT,
        ("-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"),
        cwd=ROOT,
        code="PRIMARY_ROOT_REJECTED",
    )
    try:
        text = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise PrepError("PRIMARY_ROOT_REJECTED") from None
    if not text or "\n" in text:
        raise PrepError("PRIMARY_ROOT_REJECTED")
    common = Path(text)
    primary = common.parent
    if common.name != ".git":
        raise PrepError("PRIMARY_ROOT_REJECTED")
    return _regular_directory(primary, "PRIMARY_ROOT_REJECTED", private=False)


def _selected_inspect_identity(raw: bytes, expected_id: str) -> clean_rebuild.SourceContainerIdentity:
    lines = raw.splitlines()
    if len(lines) != 9:
        raise PrepError("SOURCE_IDENTITY_REJECTED")
    try:
        values = [json.loads(line, object_pairs_hook=_unique_object) for line in lines]
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise PrepError("SOURCE_IDENTITY_REJECTED") from None
    (
        container_id,
        container_name,
        image_id,
        status,
        health,
        compose_project,
        compose_service,
        image_reference,
        ports,
    ) = values
    if (
        container_id != expected_id
        or not isinstance(container_name, str)
        or not container_name.startswith("/")
        or status != "running"
        or health != "healthy"
        or compose_project != clean_rebuild.SOURCE_COMPOSE_PROJECT
        or compose_service != clean_rebuild.SOURCE_COMPOSE_SERVICE
        or image_reference != clean_rebuild.PG_IMAGE
        or not isinstance(image_id, str)
        or not isinstance(ports, list)
        or len(ports) != 1
        or not isinstance(ports[0], dict)
        or set(ports[0]) != {"HostIp", "HostPort"}
        or ports[0].get("HostIp") != "127.0.0.1"
    ):
        raise PrepError("SOURCE_IDENTITY_REJECTED")
    try:
        published_port = int(ports[0]["HostPort"])
    except (KeyError, TypeError, ValueError):
        raise PrepError("SOURCE_IDENTITY_REJECTED") from None
    document = {
        "container_id": container_id,
        "container_name": container_name.removeprefix("/"),
        "compose_project": compose_project,
        "compose_service": compose_service,
        "image_id": image_id,
        "image_reference": image_reference,
        "published_port": published_port,
    }
    canonical = _canonical_bytes(document)
    try:
        return clean_rebuild.parse_source_container_identity(
            canonical, expected_port=published_port
        )
    except Exception:
        raise PrepError("SOURCE_IDENTITY_REJECTED") from None


def _source_identity_bytes() -> tuple[bytes, int]:
    discovery = _docker_output(
        (
            "container",
            "ls",
            "--no-trunc",
            "--filter",
            "label=com.docker.compose.project=" + clean_rebuild.SOURCE_COMPOSE_PROJECT,
            "--filter",
            "label=com.docker.compose.service=" + clean_rebuild.SOURCE_COMPOSE_SERVICE,
            "--format",
            "{{.ID}}",
        )
    )
    identifiers = [value.decode("ascii") for value in discovery.splitlines() if value]
    if len(identifiers) != 1 or not re.fullmatch(r"[0-9a-f]{64}", identifiers[0]):
        raise PrepError("SOURCE_CONTAINER_AMBIGUOUS")
    identifier = identifiers[0]
    command = (
        "container",
        "inspect",
        "--format",
        clean_rebuild._SOURCE_INSPECT_FORMAT,
        identifier,
    )
    first_raw = _docker_output(command)
    first = _selected_inspect_identity(first_raw, identifier)
    second_raw = _docker_output(command)
    second = _selected_inspect_identity(second_raw, identifier)
    if first != second:
        raise PrepError("SOURCE_IDENTITY_DRIFT")
    return _canonical_bytes(
        {
            "container_id": first.container_id,
            "container_name": first.container_name,
            "compose_project": first.compose_project,
            "compose_service": first.compose_service,
            "image_id": first.image_id,
            "image_reference": first.image_reference,
            "published_port": first.published_port,
        }
    ), first.published_port


def _source_scope_bytes(identity_raw: bytes) -> bytes:
    try:
        identity_document = _load_unique_json(identity_raw)
        if not isinstance(identity_document, dict):
            raise PrepError("SOURCE_SCOPE_REJECTED")
        port = identity_document.get("published_port")
        if type(port) is not int:
            raise PrepError("SOURCE_SCOPE_REJECTED")
        identity = clean_rebuild.parse_source_container_identity(
            identity_raw, expected_port=port
        )
        builder = getattr(clean_rebuild, "source_scope_document")
        parser = getattr(clean_rebuild, "parse_source_scope")
        document = builder(identity)
        raw = _canonical_bytes(document)
        decoded = _load_unique_json(raw)
        if (
            not isinstance(decoded, dict)
            or set(decoded) != SOURCE_SCOPE_KEYS
            or decoded.get("schema") != SOURCE_SCOPE_SCHEMA
            or decoded.get("host") != "127.0.0.1"
            or decoded.get("database") != clean_rebuild.SOURCE_DATABASE_NAME
            or decoded.get("access") != "LOCAL_DOCKER_EXEC_READ_ONLY"
            or decoded.get("published_port") != identity.published_port
        ):
            raise PrepError("SOURCE_SCOPE_REJECTED")
        parser(raw)
        return raw
    except PrepError:
        raise
    except (AttributeError, TypeError, ValueError):
        raise PrepError("SOURCE_SCOPE_CONTRACT_PENDING") from None
    except Exception:
        raise PrepError("SOURCE_SCOPE_REJECTED") from None


def _f0g_source_aggregate(
    identity: clean_rebuild.SourceContainerIdentity,
) -> str:
    try:
        statement_raw = clean_rebuild.f0g_source_aggregate_statement()
        if not isinstance(statement_raw, bytes):
            raise PrepError("F0G_SOURCE_AGGREGATE_REJECTED")
        statement = statement_raw.decode("ascii")
        raw = _docker_output(
            (
                "exec",
                "--user",
                "postgres",
                identity.container_id,
                "psql",
                "--username=" + clean_rebuild.F0G_SOURCE_ROLE,
                "--dbname=" + clean_rebuild.F0G_SOURCE_DATABASE_NAME,
                "--no-password",
                "--no-psqlrc",
                "--tuples-only",
                "--no-align",
                "--field-separator=|",
                "--set=ON_ERROR_STOP=1",
                "--command",
                statement,
            )
        )
        digest = clean_rebuild.parse_f0g_source_aggregate(raw)
    except PrepError as error:
        if error.code == "DOCKER_READ_REJECTED":
            raise PrepError("F0G_SOURCE_AGGREGATE_REJECTED") from None
        raise
    except (AttributeError, UnicodeDecodeError):
        raise PrepError("F0G_SOURCE_CONTRACT_PENDING") from None
    except Exception:
        raise PrepError("F0G_SOURCE_AGGREGATE_REJECTED") from None
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise PrepError("F0G_SOURCE_AGGREGATE_REJECTED")
    return digest


def _f0g_source_dump_digest(
    identity: clean_rebuild.SourceContainerIdentity,
    destination: Path,
) -> str:
    try:
        _docker_output_to_private_file(
            (
                "exec",
                "--user",
                "postgres",
                identity.container_id,
                "pg_dump",
                "--username=" + clean_rebuild.F0G_SOURCE_ROLE,
                "--dbname=" + clean_rebuild.F0G_SOURCE_DATABASE_NAME,
                "--no-password",
                "--format=plain",
                "--data-only",
                "--no-owner",
                "--no-privileges",
                "--schema=f0d",
                "--schema=f0e",
                "--schema=f0f",
                "--exclude-table-data=f0d.alembic_version",
            ),
            destination,
        )
        digest = clean_rebuild.normalized_f0g_data_dump_digest(destination)
    except PrepError:
        raise
    except AttributeError:
        raise PrepError("F0G_SOURCE_CONTRACT_PENDING") from None
    except Exception:
        raise PrepError("F0G_SOURCE_DUMP_REJECTED") from None
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise PrepError("F0G_SOURCE_DUMP_REJECTED")
    return digest


def _validate_f0g_source_scope(raw: bytes) -> None:
    try:
        document = _load_unique_json(raw)
        if (
            not isinstance(document, dict)
            or set(document) != F0G_SOURCE_SCOPE_KEYS
            or raw != _canonical_bytes(document)
            or document.get("schema") != F0G_SOURCE_SCOPE_SCHEMA
            or document.get("database") != clean_rebuild.F0G_SOURCE_DATABASE_NAME
            or document.get("role") != clean_rebuild.F0G_SOURCE_ROLE
            or document.get("schemas") != list(clean_rebuild.F0G_SOURCE_SCHEMAS)
            or document.get("access") != "LOCAL_DOCKER_EXEC_READ_ONLY"
            or document.get("read_only") is not True
        ):
            raise PrepError("F0G_SOURCE_SCOPE_REJECTED")
        clean_rebuild.parse_f0g_source_scope(raw)
    except PrepError:
        raise
    except AttributeError:
        raise PrepError("F0G_SOURCE_CONTRACT_PENDING") from None
    except Exception:
        raise PrepError("F0G_SOURCE_SCOPE_REJECTED") from None


def _f0g_source_scope_bytes(identity_raw: bytes, scratch_directory: Path) -> bytes:
    _regular_directory(
        scratch_directory, "F0G_SOURCE_SCRATCH_REJECTED", private=True
    )
    try:
        identity_document = _load_unique_json(identity_raw)
        if not isinstance(identity_document, dict):
            raise PrepError("F0G_SOURCE_SCOPE_REJECTED")
        port = identity_document.get("published_port")
        if type(port) is not int:
            raise PrepError("F0G_SOURCE_SCOPE_REJECTED")
        identity = clean_rebuild.parse_source_container_identity(
            identity_raw, expected_port=port
        )
    except PrepError:
        raise
    except Exception:
        raise PrepError("F0G_SOURCE_SCOPE_REJECTED") from None

    first_dump_path = scratch_directory / ".f0g-source-dump-first"
    second_dump_path = scratch_directory / ".f0g-source-dump-second"
    if _lexists(first_dump_path) or _lexists(second_dump_path):
        raise PrepError("F0G_SOURCE_SCRATCH_REJECTED")
    try:
        _validate_live_source(identity)
        first_aggregate = _f0g_source_aggregate(identity)
        first_dump = _f0g_source_dump_digest(identity, first_dump_path)
        _validate_live_source(identity)
        second_aggregate = _f0g_source_aggregate(identity)
        second_dump = _f0g_source_dump_digest(identity, second_dump_path)
        _validate_live_source(identity)
        third_aggregate = _f0g_source_aggregate(identity)
        if (
            first_aggregate != second_aggregate
            or second_aggregate != third_aggregate
            or first_dump != second_dump
        ):
            raise PrepError("F0G_SOURCE_DRIFT")
        document = {
            "schema": F0G_SOURCE_SCOPE_SCHEMA,
            "database": clean_rebuild.F0G_SOURCE_DATABASE_NAME,
            "role": clean_rebuild.F0G_SOURCE_ROLE,
            "schemas": list(clean_rebuild.F0G_SOURCE_SCHEMAS),
            "access": "LOCAL_DOCKER_EXEC_READ_ONLY",
            "read_only": True,
            "container_id": identity.container_id,
            "container_name": identity.container_name,
            "compose_project": identity.compose_project,
            "compose_service": identity.compose_service,
            "image_id": identity.image_id,
            "image_reference": identity.image_reference,
            "published_port": identity.published_port,
            "dump_sha256": first_dump,
            "aggregate_sha256": first_aggregate,
        }
        raw = _canonical_bytes(document)
        _validate_f0g_source_scope(raw)
        return raw
    finally:
        cleanup_failed = False
        for path in (first_dump_path, second_dump_path):
            try:
                path.unlink(missing_ok=True)
                if _lexists(path):
                    cleanup_failed = True
            except OSError:
                cleanup_failed = True
        if cleanup_failed:
            raise PrepError("F0G_SOURCE_SCRATCH_REJECTED")


def _select_fixture_entries(entries: Iterable[Any]) -> tuple[Any, ...]:
    values = tuple(entries)
    pdf = sorted(
        (entry for entry in values if getattr(entry, "document_type", None) == "PDF"),
        key=lambda entry: str(getattr(entry, "source_id", "")),
    )
    jpeg = sorted(
        (entry for entry in values if getattr(entry, "document_type", None) == "JPEG"),
        key=lambda entry: str(getattr(entry, "source_id", "")),
    )
    if len(pdf) < 3 or not jpeg:
        raise PrepError("FIXTURE_SET_INCOMPLETE")
    selected = (pdf[0], pdf[1], pdf[2], jpeg[0])
    digests = [str(getattr(entry, "expected_sha256", "")) for entry in selected]
    if len(set(digests)) != len(digests) or any(
        not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in digests
    ):
        raise PrepError("FIXTURE_SET_INCOMPLETE")
    return selected


def _registered_fixture_record(
    entry: Any,
    *,
    registered_ids: frozenset[Any],
    source_root: Path,
    staging_directory: Path,
    published_directory: Path,
) -> dict[str, str]:
    if getattr(entry, "source_id", None) not in registered_ids:
        raise PrepError("FIXTURE_NOT_REGISTERED")
    manifest_entry = getattr(entry, "_manifest_entry", None)
    relative_text = getattr(manifest_entry, "relative_path", None)
    if not isinstance(relative_text, str):
        raise PrepError("FIXTURE_NOT_REGISTERED")
    relative = Path(relative_text)
    path = source_root / relative
    try:
        canonical = path.resolve(strict=True)
    except OSError:
        raise PrepError("FIXTURE_SOURCE_REJECTED") from None
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or canonical != path
        or not path.is_absolute()
    ):
        raise PrepError("FIXTURE_SOURCE_REJECTED")
    expected = str(getattr(entry, "expected_sha256", ""))
    expected_size = getattr(entry, "expected_size", None)
    try:
        source_id = uuid.UUID(str(getattr(entry, "source_id", "")))
    except ValueError:
        raise PrepError("FIXTURE_SOURCE_REJECTED") from None
    media = {
        "PDF": "application/pdf",
        "JPEG": "image/jpeg",
    }.get(str(getattr(entry, "document_type", "")))
    if (
        source_id.version != 5
        or media is None
        or not re.fullmatch(r"[0-9a-f]{64}", expected)
        or type(expected_size) is not int
        or not 0 < expected_size <= 100 * 1024 * 1024
        or not staging_directory.is_absolute()
        or not published_directory.is_absolute()
    ):
        raise PrepError("FIXTURE_SOURCE_REJECTED")
    _regular_directory(
        staging_directory, "FIXTURE_TARGET_REJECTED", private=True
    )
    name = source_id.hex
    destination = staging_directory / name
    published = published_directory / name
    if destination.name != name or published.name != name or _lexists(destination):
        raise PrepError("FIXTURE_TARGET_REJECTED")
    target_descriptor = -1
    target_complete = False
    try:
        try:
            baseline_identity, baseline_digest, baseline_size = (
                _source_identity_and_digest(entry)
            )
        except PrepError:
            raise PrepError("FIXTURE_SOURCE_REJECTED") from None
        path_info = path.lstat()
        if (
            baseline_digest != expected
            or baseline_size != expected_size
            or baseline_identity[:2]
            != (int(path_info.st_dev), int(path_info.st_ino))
            or path_info.st_uid != os.geteuid()
            or path_info.st_nlink != 1
        ):
            raise PrepError("FIXTURE_SOURCE_REJECTED")
        target_descriptor = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        digest = hashlib.sha256()
        with catalog.open_catalog_source(entry) as source_descriptor:
            source_before = os.fstat(source_descriptor)
            if _identity(source_before) != baseline_identity:
                raise PrepError("FIXTURE_SOURCE_MUTATED")
            remaining = expected_size
            while remaining:
                chunk = os.read(source_descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise PrepError("FIXTURE_SOURCE_MUTATED")
                _write_all(target_descriptor, chunk, "FIXTURE_TARGET_REJECTED")
                digest.update(chunk)
                remaining -= len(chunk)
            if os.read(source_descriptor, 1):
                raise PrepError("FIXTURE_SOURCE_MUTATED")
            source_after = os.fstat(source_descriptor)
            if (
                _identity(source_after) != baseline_identity
                or digest.hexdigest() != expected
            ):
                raise PrepError("FIXTURE_SOURCE_MUTATED")
        os.fsync(target_descriptor)
        os.close(target_descriptor)
        target_descriptor = -1
        try:
            final_identity, final_digest, final_size = (
                _source_identity_and_digest(entry)
            )
        except PrepError:
            raise PrepError("FIXTURE_SOURCE_MUTATED") from None
        target_info = destination.lstat()
        target_descriptor = os.open(
            destination,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        target_digest, target_size = _digest_descriptor(
            target_descriptor, 100 * 1024 * 1024
        )
        target_after = os.fstat(target_descriptor)
        if (
            final_identity != baseline_identity
            or final_digest != expected
            or final_size != expected_size
            or not stat.S_ISREG(target_info.st_mode)
            or stat.S_ISLNK(target_info.st_mode)
            or stat.S_IMODE(target_info.st_mode) != 0o600
            or target_info.st_uid != os.geteuid()
            or target_info.st_nlink != 1
            or target_size != expected_size
            or target_digest != expected
            or _identity(target_info) != _identity(target_after)
        ):
            raise PrepError("FIXTURE_TARGET_REJECTED")
        target_complete = True
    except PrepError:
        raise
    except Exception:
        raise PrepError("FIXTURE_SOURCE_REJECTED") from None
    finally:
        if target_descriptor >= 0:
            os.close(target_descriptor)
        if not target_complete:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
    return {"path": str(published), "sha256": expected, "content_type": media}


def _catalog_entries(private_directory: Path) -> tuple[Any, ...]:
    by_name = {item.bundle_name: private_directory / item.bundle_name for item in FROZEN_INPUTS}
    required = {
        "fixture_route_plan_json",
        "fixture_native_plan_json",
        "fixture_core_manifest",
        "fixture_negative_manifest",
    }
    if not required.issubset(by_name):
        raise PrepError("CATALOG_BUNDLE_INCOMPLETE")
    fields = (
        "_ROUTE_PLAN",
        "_NATIVE_PLAN",
        "REGISTERED_CORE_MANIFEST",
        "REGISTERED_NEGATIVE_MANIFEST",
    )
    previous = {name: getattr(catalog, name) for name in fields}
    try:
        catalog._ROUTE_PLAN = by_name["fixture_route_plan_json"]
        catalog._NATIVE_PLAN = by_name["fixture_native_plan_json"]
        catalog.REGISTERED_CORE_MANIFEST = by_name["fixture_core_manifest"]
        catalog.REGISTERED_NEGATIVE_MANIFEST = by_name["fixture_negative_manifest"]
        return tuple(catalog.load_catalog("full"))
    except Exception:
        raise PrepError("REGISTERED_CATALOG_REJECTED") from None
    finally:
        for name, value in previous.items():
            setattr(catalog, name, value)


def _validate_question(raw: bytes) -> bytes:
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise PrepError("QUESTION_REJECTED") from None
    if (
        not raw
        or len(raw) > 4096
        or "\x00" in value
        or "\n" in value
        or "\r" in value
        or any(ord(character) < 0x20 for character in value)
    ):
        raise PrepError("QUESTION_REJECTED")
    return raw


def _evaluation_candidates(
    document: Any,
) -> tuple[tuple[str, bytes, tuple[uuid.UUID, ...]], ...]:
    if (
        not isinstance(document, dict)
        or document.get("schema") != "f0j1-evaluation-samples-v1"
        or not isinstance(document.get("samples"), list)
    ):
        raise PrepError("EVALUATION_SAMPLE_REJECTED")
    eligible: list[tuple[str, bytes, tuple[uuid.UUID, ...]]] = []
    for sample in document["samples"]:
        if (
            not isinstance(sample, dict)
            or sample.get("expected_behavior") != "answerable"
            or not isinstance(sample.get("answer"), str)
            or not sample.get("answer")
            or sample.get("refusal_reason") is not None
            or not isinstance(sample.get("citations"), list)
            or not sample.get("citations")
            or not isinstance(sample.get("query"), str)
            or not isinstance(sample.get("sample_id"), str)
        ):
            continue
        document_ids: list[uuid.UUID] = []
        valid = True
        for citation in sample["citations"]:
            if not isinstance(citation, dict):
                valid = False
                break
            try:
                document_ids.append(uuid.UUID(str(citation.get("document_id", ""))))
            except ValueError:
                valid = False
                break
        if not valid or not document_ids:
            continue
        query = _validate_question(sample["query"].encode("utf-8"))
        eligible.append(
            (
                sample["sample_id"],
                query,
                tuple(dict.fromkeys(document_ids)),
            )
        )
    if not eligible:
        raise PrepError("QUESTION_GROUNDING_NOT_PROVEN")
    return tuple(sorted(eligible, key=lambda item: (len(item[2]), item[0])))


def _evaluation_question_candidates(
    primary_root: Path,
) -> tuple[tuple[str, bytes, tuple[uuid.UUID, ...]], ...]:
    raw = _read_stable_file(
        primary_root / EVALUATION_SAMPLES_RELATIVE,
        code="EVALUATION_SAMPLE_REJECTED",
        expected_sha256=EVALUATION_SAMPLES_SHA256,
    )
    return _evaluation_candidates(_load_unique_json(raw))


def _validate_live_source(identity: clean_rebuild.SourceContainerIdentity) -> None:
    raw = _docker_output(
        (
            "container",
            "inspect",
            "--format",
            clean_rebuild._SOURCE_INSPECT_FORMAT,
            identity.container_id,
        )
    )
    if _selected_inspect_identity(raw, identity.container_id) != identity:
        raise PrepError("SOURCE_IDENTITY_DRIFT")


def _document_fixture_sha256(
    identity: clean_rebuild.SourceContainerIdentity,
    document_ids: Sequence[uuid.UUID],
) -> str:
    """Resolve one prior cited document to the closed fixture registry.

    Only UUIDs from the fixed-SHA evaluation file enter the statement.  The
    selected output is limited to opaque UUID, SHA-256 and type; no body,
    question, path or credential is queried.
    """

    unique = tuple(dict.fromkeys(document_ids))
    if not unique or len(unique) > 16 or any(not isinstance(value, uuid.UUID) for value in unique):
        raise PrepError("QUESTION_GROUNDING_NOT_PROVEN")
    array = "ARRAY[" + ",".join(f"'{value}'::uuid" for value in unique) + "]"
    statement = (
        "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY, DEFERRABLE;"
        "WITH requested(document_id) AS (SELECT DISTINCT unnest(" + array + ")) "
        "SELECT q.document_id::text,trim(d.source_object_sha256) "
        "FROM requested AS q JOIN f0i.document_scope AS d ON d.id=q.document_id "
        "JOIN f0d.fixture_source_registry AS r "
        "ON r.enterprise_id=d.enterprise_id "
        "AND r.expected_sha256=d.source_object_sha256 "
        "AND r.expected_size_bytes=d.source_object_size_bytes "
        "AND r.document_type=d.document_type "
        "WHERE d.terminal_status='CANONICAL_SCOPE_INCLUDED' "
        "AND r.corpus_role='CORE_FIXTURE' AND r.enterprise_fact_allowed "
        "AND EXISTS (SELECT 1 FROM f0i.chunk AS c "
        "WHERE c.enterprise_id=d.enterprise_id AND c.document_scope_id=d.id "
        "AND c.chunk_level='CHILD') "
        "GROUP BY q.document_id,d.source_object_sha256 "
        "HAVING count(DISTINCT r.id)=1 ORDER BY q.document_id;COMMIT;"
    )
    _validate_live_source(identity)
    raw = _docker_output(
        (
            "exec",
            "--user",
            "postgres",
            identity.container_id,
            "psql",
            "--username=" + clean_rebuild.SOURCE_DATABASE_SUPERUSER,
            "--dbname=" + clean_rebuild.SOURCE_DATABASE_NAME,
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--field-separator=|",
            "--set=ON_ERROR_STOP=1",
            "--command",
            statement,
        )
    )
    _validate_live_source(identity)
    lines = [line for line in raw.splitlines() if line not in {b"", b"BEGIN", b"COMMIT"}]
    result: dict[uuid.UUID, str] = {}
    for line in lines:
        try:
            fields = line.decode("ascii").split("|")
            document_id = uuid.UUID(fields[0])
        except (UnicodeDecodeError, ValueError, IndexError):
            raise PrepError("QUESTION_GROUNDING_NOT_PROVEN") from None
        if (
            len(fields) != 2
            or document_id not in unique
            or document_id in result
            or not re.fullmatch(r"[0-9a-f]{64}", fields[1])
        ):
            raise PrepError("QUESTION_GROUNDING_NOT_PROVEN")
        result[document_id] = fields[1]
    if set(result) != set(unique) or len(set(result.values())) != 1:
        raise PrepError("QUESTION_GROUNDING_NOT_PROVEN")
    return next(iter(result.values()))


def _grounded_inputs(
    primary_root: Path,
    identity_raw: bytes,
    entries: Sequence[Any],
    staging_fixture_directory: Path,
    published_fixture_directory: Path,
) -> tuple[bytes, bytes]:
    candidates = _evaluation_question_candidates(primary_root)
    identity_document = _load_unique_json(identity_raw)
    port = identity_document.get("published_port") if isinstance(identity_document, dict) else None
    if type(port) is not int:
        raise PrepError("SOURCE_IDENTITY_REJECTED")
    try:
        identity = clean_rebuild.parse_source_container_identity(
            identity_raw, expected_port=port
        )
    except Exception:
        raise PrepError("SOURCE_IDENTITY_REJECTED") from None
    registered_ids = frozenset(getattr(entry, "source_id", None) for entry in entries)
    catalog_pdf_by_sha: dict[str, Any] = {}
    for entry in entries:
        if (
            getattr(entry, "document_type", None) != "PDF"
            or getattr(entry, "corpus_role", None) != "CORE_FIXTURE"
            or getattr(entry, "enterprise_fact_allowed", None) is not True
        ):
            continue
        digest = str(getattr(entry, "expected_sha256", ""))
        if digest in catalog_pdf_by_sha:
            raise PrepError("REGISTERED_CATALOG_REJECTED")
        catalog_pdf_by_sha[digest] = entry
    preferred_entries: tuple[Any, ...] = ()
    primary_question: bytes | None = None
    for _sample_id, query, document_ids in candidates:
        mapped: list[Any] = []
        candidate_proven = True
        for document_id in document_ids:
            try:
                digest = _document_fixture_sha256(identity, (document_id,))
            except PrepError as error:
                if error.code in {"SOURCE_IDENTITY_DRIFT", "DOCKER_READ_REJECTED"}:
                    raise
                candidate_proven = False
                break
            entry = catalog_pdf_by_sha.get(digest)
            if entry is None:
                candidate_proven = False
                break
            if entry not in mapped:
                mapped.append(entry)
        if candidate_proven and 1 <= len(mapped) <= 3:
            preferred_entries = tuple(mapped)
            primary_question = query
            break
    if not preferred_entries or primary_question is None:
        raise PrepError("QUESTION_GROUNDING_NOT_PROVEN")
    remaining_pdf = sorted(
        (
            entry
            for entry in entries
            if getattr(entry, "document_type", None) == "PDF"
            and getattr(entry, "corpus_role", None) == "CORE_FIXTURE"
            and getattr(entry, "enterprise_fact_allowed", None) is True
            and entry not in preferred_entries
        ),
        key=lambda entry: str(getattr(entry, "source_id", "")),
    )
    jpeg = sorted(
        (
            entry
            for entry in entries
            if getattr(entry, "document_type", None) == "JPEG"
            and getattr(entry, "corpus_role", None) == "CORE_FIXTURE"
            and getattr(entry, "enterprise_fact_allowed", None) is True
        ),
        key=lambda entry: str(getattr(entry, "source_id", "")),
    )
    needed = 3 - len(preferred_entries)
    if needed < 0 or len(remaining_pdf) < needed or not jpeg:
        raise PrepError("FIXTURE_SET_INCOMPLETE")
    selected = (*preferred_entries, *remaining_pdf[:needed], jpeg[0])
    records = [
        _registered_fixture_record(
            entry,
            registered_ids=registered_ids,
            source_root=REGISTERED_SOURCE_ROOT,
            staging_directory=staging_fixture_directory,
            published_directory=published_fixture_directory,
        )
        for entry in selected
    ]
    raw = _canonical_bytes(records)
    if (
        len(records) != clean_rebuild.REQUIRED_FIXTURES
        or len({record["path"] for record in records}) != len(records)
        or len({record["sha256"] for record in records}) != len(records)
        or {
            record["content_type"] for record in records
        } != {"application/pdf", "image/jpeg"}
        or any(
            Path(record["path"]).parent != published_fixture_directory
            or not re.fullmatch(r"[0-9a-f]{32}", Path(record["path"]).name)
            or not (staging_fixture_directory / Path(record["path"]).name).is_file()
            for record in records
        )
    ):
        raise PrepError("FIXTURE_MANIFEST_REJECTED")
    return raw, _validate_question(primary_question)


def _random_text() -> bytes:
    return (secrets.token_urlsafe(36) + "\n").encode("ascii")


def _credential_files() -> dict[str, bytes]:
    passwords = {
        "auditor": _random_text(),
        "invitee": _random_text(),
        "tenant_a": _random_text(),
        "tenant_b": _random_text(),
    }
    values: dict[str, bytes] = {
        "f1_api_password": _random_text(),
        "f1_worker_password": _random_text(),
        "f1_qa_key": base64.urlsafe_b64encode(secrets.token_bytes(32)) + b"\n",
        "grafana_admin_password": _random_text(),
        "invite_signing_key": _random_text(),
        "keycloak_admin_password": _random_text(),
        "minio_root_password": _random_text(),
        "minio_root_user": ("f111" + secrets.token_hex(12) + "\n").encode("ascii"),
        "oidc_admin_anhuan_local": _random_text(),
        "oidc_auditor": passwords["auditor"],
        "oidc_invitee": passwords["invitee"],
        "oidc_tenant_a": passwords["tenant_a"],
        "oidc_tenant_b": passwords["tenant_b"],
        "oidc_tester": _random_text(),
        "auditor_password": passwords["auditor"],
        "auditor_username": b"auditor\n",
        "invitee_password": passwords["invitee"],
        "invitee_username": b"invitee@fixture.invalid\n",
        "tenant_a_password": passwords["tenant_a"],
        "tenant_a_username": b"tenant-a\n",
        "tenant_b_password": passwords["tenant_b"],
        "tenant_b_username": b"tenant-b\n",
    }
    if set(values) != set(clean_rebuild.COPY_SECRET_FILES):
        raise PrepError("SECRET_SET_REJECTED")
    return values


def _publish_exclusive(stage: Path, target: Path) -> None:
    if _lexists(target):
        raise PrepError("TARGET_OCCUPIED")
    try:
        library = ctypes.CDLL(None, use_errno=True)
        rename = library.renamex_np
        rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        rename.restype = ctypes.c_int
        result = rename(os.fsencode(stage), os.fsencode(target), 0x00000004)
    except (AttributeError, OSError):
        raise PrepError("ATOMIC_PUBLISH_UNAVAILABLE") from None
    if result != 0:
        observed = ctypes.get_errno()
        if observed in {errno.EEXIST, errno.ENOTEMPTY}:
            raise PrepError("TARGET_OCCUPIED")
        raise PrepError("ATOMIC_PUBLISH_REJECTED")


def _verify_fixture_manifest_bundle(
    raw: bytes,
    *,
    actual_directory: Path,
    published_directory: Path,
) -> None:
    document = _load_unique_json(raw)
    if not isinstance(document, list) or len(document) != clean_rebuild.REQUIRED_FIXTURES:
        raise PrepError("FIXTURE_MANIFEST_REJECTED")
    names: set[str] = set()
    digests: set[str] = set()
    media_count = {"application/pdf": 0, "image/jpeg": 0}
    for item in document:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "content_type"}:
            raise PrepError("FIXTURE_MANIFEST_REJECTED")
        path = Path(str(item["path"]))
        name = path.name
        digest = str(item["sha256"])
        media = str(item["content_type"])
        try:
            source_id = uuid.UUID(hex=name)
        except ValueError:
            raise PrepError("FIXTURE_MANIFEST_REJECTED") from None
        if (
            not path.is_absolute()
            or path.parent != published_directory
            or source_id.version != 5
            or source_id.hex != name
            or name in names
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or digest in digests
            or media not in media_count
        ):
            raise PrepError("FIXTURE_MANIFEST_REJECTED")
        target = actual_directory / name
        descriptor = -1
        try:
            info = target.lstat()
            descriptor = os.open(
                target,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            before = os.fstat(descriptor)
            observed, size = _digest_descriptor(descriptor, 100 * 1024 * 1024)
            after = os.fstat(descriptor)
        except PrepError:
            raise PrepError("FIXTURE_TARGET_REJECTED") from None
        except OSError:
            raise PrepError("FIXTURE_TARGET_REJECTED") from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or not 0 < size <= 100 * 1024 * 1024
            or _identity(info) != _identity(before)
            or _identity(before) != _identity(after)
            or observed != digest
        ):
            raise PrepError("FIXTURE_TARGET_REJECTED")
        names.add(name)
        digests.add(digest)
        media_count[media] += 1
    try:
        actual_names = {item.name for item in actual_directory.iterdir()}
    except OSError:
        raise PrepError("FIXTURE_TARGET_REJECTED") from None
    if actual_names != names or media_count != {"application/pdf": 3, "image/jpeg": 1}:
        raise PrepError("FIXTURE_MANIFEST_REJECTED")
    if actual_directory == published_directory:
        try:
            parsed = clean_rebuild.parse_fixture_manifest(raw)
        except Exception:
            raise PrepError("FIXTURE_MANIFEST_REJECTED") from None
        if len(parsed) != clean_rebuild.REQUIRED_FIXTURES:
            raise PrepError("FIXTURE_MANIFEST_REJECTED")


def _verify_bundle(target: Path, *, published_root: Path | None = None) -> None:
    _regular_directory(target, "OUTPUT_VERIFY_REJECTED", private=True)
    logical_root = target if published_root is None else published_root
    if not logical_root.is_absolute():
        raise PrepError("OUTPUT_VERIFY_REJECTED")
    expected_root = {
        SECRETS_DIRECTORY_NAME,
        PROVIDER_DIRECTORY_NAME,
        FIXTURE_DIRECTORY_NAME,
        F0I_KEY_NAME,
        SOURCE_CONFIG_NAME,
    }
    try:
        if {item.name for item in target.iterdir()} != expected_root:
            raise PrepError("OUTPUT_VERIFY_REJECTED")
    except OSError:
        raise PrepError("OUTPUT_VERIFY_REJECTED") from None
    secrets_directory = _regular_directory(
        target / SECRETS_DIRECTORY_NAME, "OUTPUT_VERIFY_REJECTED", private=True
    )
    provider_directory = _regular_directory(
        target / PROVIDER_DIRECTORY_NAME, "OUTPUT_VERIFY_REJECTED", private=True
    )
    fixture_directory = _regular_directory(
        target / FIXTURE_DIRECTORY_NAME, "OUTPUT_VERIFY_REJECTED", private=True
    )
    expected_secrets = set(clean_rebuild.COPY_SECRET_FILES) | {
        "f0i_source_scope",
        F0G_SOURCE_SCOPE_NAME,
        "fixture_manifest",
        "question_primary",
        "question_alternate",
        F0F_KEY_BUNDLE_NAME,
        SOURCE_BUNDLE_NAME,
        *(item.bundle_name for item in FROZEN_INPUTS),
        *(item.bundle_name for item in RUNTIME_TREE_CONTRACTS),
    }
    if {item.name for item in secrets_directory.iterdir()} != expected_secrets:
        raise PrepError("OUTPUT_VERIFY_REJECTED")
    if {item.name for item in provider_directory.iterdir()} != set(
        clean_rebuild.PROVIDER_SECRET_FILES
    ):
        raise PrepError("OUTPUT_VERIFY_REJECTED")
    files = (
        *secrets_directory.iterdir(),
        *provider_directory.iterdir(),
        target / F0I_KEY_NAME,
        target / SOURCE_CONFIG_NAME,
    )
    for path in files:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or info.st_size < 1
        ):
            raise PrepError("OUTPUT_VERIFY_REJECTED")
    config_raw = (target / SOURCE_CONFIG_NAME).read_bytes()
    config = _load_unique_json(config_raw)
    expected_config = {
        "schema": CONFIG_SCHEMA,
        "secrets_directory": str(logical_root / SECRETS_DIRECTORY_NAME),
        "provider_secrets_directory": str(logical_root / PROVIDER_DIRECTORY_NAME),
        "f0i_key_file": str(logical_root / F0I_KEY_NAME),
        "f0g_source_scope_file": str(
            logical_root / SECRETS_DIRECTORY_NAME / F0G_SOURCE_SCOPE_NAME
        ),
    }
    if config != expected_config or config_raw != _canonical_bytes(expected_config):
        raise PrepError("OUTPUT_VERIFY_REJECTED")
    identity_keys = {
        "container_id",
        "container_name",
        "compose_project",
        "compose_service",
        "image_id",
        "image_reference",
        "published_port",
    }
    scope_raw = (secrets_directory / "f0i_source_scope").read_bytes()
    scope_document = _load_unique_json(scope_raw)
    if not isinstance(scope_document, dict):
        raise PrepError("OUTPUT_VERIFY_REJECTED")
    identity_document = {
        key: value for key, value in scope_document.items() if key in identity_keys
    }
    if scope_raw != _source_scope_bytes(_canonical_bytes(identity_document)):
        raise PrepError("OUTPUT_VERIFY_REJECTED")
    f0g_scope_raw = (secrets_directory / F0G_SOURCE_SCOPE_NAME).read_bytes()
    _validate_f0g_source_scope(f0g_scope_raw)
    f0g_scope_document = _load_unique_json(f0g_scope_raw)
    if (
        not isinstance(f0g_scope_document, dict)
        or {key: f0g_scope_document.get(key) for key in identity_keys}
        != identity_document
    ):
        raise PrepError("OUTPUT_VERIFY_REJECTED")
    _validate_fixture_source_bundle(secrets_directory / SOURCE_BUNDLE_NAME)
    if (secrets_directory / F0F_KEY_BUNDLE_NAME).lstat().st_size != 32:
        raise PrepError("OUTPUT_VERIFY_REJECTED")
    for contract in RUNTIME_TREE_CONTRACTS:
        _validate_runtime_tree_bundle(
            secrets_directory / contract.bundle_name, contract
        )
    fixture_manifest = (secrets_directory / "fixture_manifest").read_bytes()
    _verify_fixture_manifest_bundle(
        fixture_manifest,
        actual_directory=fixture_directory,
        published_directory=logical_root / FIXTURE_DIRECTORY_NAME,
    )


def _cleanup_stage(stage: Path) -> None:
    if not _lexists(stage):
        return
    if (
        not stage.is_absolute()
        or stage.parent != OUTPUT_ROOT.parent
        or not stage.name.startswith("." + OUTPUT_ROOT.name + "-staging-")
    ):
        raise PrepError("STAGING_CLEANUP_REJECTED")
    try:
        shutil.rmtree(stage)
    except OSError:
        raise PrepError("STAGING_CLEANUP_REJECTED") from None


def _cleanup_published_target(
    target: Path, expected_identity: tuple[int, int]
) -> None:
    if target != OUTPUT_ROOT or not target.is_absolute() or target.parent != OUTPUT_ROOT.parent:
        raise PrepError("OUTPUT_CLEANUP_REJECTED")
    try:
        info = target.lstat()
    except OSError:
        raise PrepError("OUTPUT_CLEANUP_REJECTED") from None
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.geteuid()
        or (int(info.st_dev), int(info.st_ino)) != expected_identity
    ):
        raise PrepError("OUTPUT_CLEANUP_REJECTED")
    try:
        shutil.rmtree(target)
    except OSError:
        raise PrepError("OUTPUT_CLEANUP_REJECTED") from None


def prepare_inputs() -> None:
    target = OUTPUT_ROOT
    if not target.is_absolute() or target.name in {"", ".", ".."}:
        raise PrepError("TARGET_REJECTED")
    if _lexists(target):
        raise PrepError("TARGET_OCCUPIED")
    primary_root = _derive_primary_root()
    provider_source = _regular_directory(
        PROVIDER_SOURCE, "PROVIDER_SOURCE_REJECTED", private=True
    )
    stage = Path(
        tempfile.mkdtemp(
            prefix="." + target.name + "-staging-", dir=str(target.parent)
        )
    )
    stage.chmod(0o700)
    published = False
    try:
        secrets_directory = stage / SECRETS_DIRECTORY_NAME
        provider_directory = stage / PROVIDER_DIRECTORY_NAME
        fixture_directory = stage / FIXTURE_DIRECTORY_NAME
        secrets_directory.mkdir(mode=0o700)
        provider_directory.mkdir(mode=0o700)
        fixture_directory.mkdir(mode=0o700)

        for item in FROZEN_INPUTS:
            if (
                not re.fullmatch(r"[a-z0-9_]{1,64}", item.bundle_name)
                or item.relative_path.is_absolute()
                or ".." in item.relative_path.parts
            ):
                raise PrepError("FROZEN_INPUT_REJECTED")
            _copy_stable_file(
                primary_root / item.relative_path,
                secrets_directory / item.bundle_name,
                code="FROZEN_INPUT_REJECTED",
                allowed_modes=frozenset({0o600, 0o644}),
                expected_sha256=item.sha256,
            )
        for contract in RUNTIME_TREE_CONTRACTS:
            _write_runtime_tree_bundle(
                primary_root / contract.relative_root,
                contract,
                secrets_directory / contract.bundle_name,
            )
        entries = _catalog_entries(secrets_directory)

        for name in clean_rebuild.PROVIDER_SECRET_FILES:
            _copy_stable_file(
                provider_source / name,
                provider_directory / name,
                code="PROVIDER_FILE_REJECTED",
                allowed_modes=frozenset({0o600}),
            )
        _copy_stable_file(
            F0I_KEY_SOURCE,
            stage / F0I_KEY_NAME,
            code="F0I_KEY_REJECTED",
            allowed_modes=frozenset({0o600}),
        )
        _copy_stable_file(
            F0F_KEY_SOURCE,
            secrets_directory / F0F_KEY_BUNDLE_NAME,
            code="F0F_KEY_REJECTED",
            allowed_modes=frozenset({0o600}),
            maximum=32,
        )
        if (secrets_directory / F0F_KEY_BUNDLE_NAME).lstat().st_size != 32:
            raise PrepError("F0F_KEY_REJECTED")

        identity_raw, _published_port = _source_identity_bytes()
        source_scope = _source_scope_bytes(identity_raw)
        f0g_source_scope = _f0g_source_scope_bytes(identity_raw, stage)
        fixture_manifest, primary_question = _grounded_inputs(
            primary_root,
            identity_raw,
            entries,
            fixture_directory,
            target / FIXTURE_DIRECTORY_NAME,
        )
        alternate = _validate_question(QUESTION_ALTERNATE.encode("utf-8"))
        if primary_question == alternate:
            raise PrepError("QUESTION_REJECTED")

        for name, raw in _credential_files().items():
            _write_private(secrets_directory / name, raw)
        _write_private(secrets_directory / "f0i_source_scope", source_scope)
        _write_private(
            secrets_directory / F0G_SOURCE_SCOPE_NAME, f0g_source_scope
        )
        _write_private(secrets_directory / "fixture_manifest", fixture_manifest)
        _write_private(secrets_directory / "question_primary", primary_question + b"\n")
        _write_private(secrets_directory / "question_alternate", alternate + b"\n")
        _write_fixture_source_bundle(
            entries, secrets_directory / SOURCE_BUNDLE_NAME
        )

        final_secrets = target / SECRETS_DIRECTORY_NAME
        final_provider = target / PROVIDER_DIRECTORY_NAME
        config = {
            "schema": CONFIG_SCHEMA,
            "secrets_directory": str(final_secrets),
            "provider_secrets_directory": str(final_provider),
            "f0i_key_file": str(target / F0I_KEY_NAME),
            "f0g_source_scope_file": str(
                final_secrets / F0G_SOURCE_SCOPE_NAME
            ),
        }
        _write_private(stage / SOURCE_CONFIG_NAME, _canonical_bytes(config))
        _verify_bundle(stage, published_root=target)
        stage_info = stage.lstat()
        stage_identity = (int(stage_info.st_dev), int(stage_info.st_ino))
        _publish_exclusive(stage, target)
        published = True
        try:
            _verify_bundle(target)
        except Exception:
            _cleanup_published_target(target, stage_identity)
            published = False
            raise
    finally:
        if not published:
            _cleanup_stage(stage)


def main(argv: Sequence[str] | None = None) -> int:
    values = list(sys.argv if argv is None else argv)
    try:
        if len(values) != 1:
            raise PrepError("ARGUMENT_REJECTED")
        prepare_inputs()
        sys.stdout.write("INPUT_PREP_READY\n")
        return 0
    except PrepError as error:
        sys.stderr.write("error=" + error.code + "\n")
        return 2
    except Exception:
        sys.stderr.write("error=INTERNAL_FAILURE\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
