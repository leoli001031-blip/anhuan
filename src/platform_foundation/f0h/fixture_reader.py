"""Read only the registered F0-C identity and source descriptors for F0-H."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
from typing import Iterator

from fixture_gate import ENVIRONMENT_DEMO_V01
from fixture_gate.validator import (
    ManifestEntry,
    ValidationFailure,
    _open_entry,
    _open_path_without_symlinks,
    _parse_manifest,
)
from fixture_page_planner.planner import (
    PlannerFailure,
    REGISTERED_FULL_ROUTE_PLAN,
    REGISTERED_SMOKE_ROUTE_PLAN,
    _json_bytes,
    _selected_entries,
    build_page_plan,
)
from fixture_router.router import (
    REGISTERED_CORE_MANIFEST,
    REGISTERED_NEGATIVE_MANIFEST,
    REGISTERED_SOURCE_ROOT,
)

from .contracts import F0HError


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_REGISTERED_PAGE_PLAN = {
    "smoke": _PROJECT_ROOT / "artifacts/fixture-native-plan/v0.1/smoke-plan.json",
    "full": _PROJECT_ROOT / "artifacts/fixture-native-plan/v0.1/full-plan.json",
}
_READ_CHUNK = 1024 * 1024


@dataclass(frozen=True, slots=True)
class RegisteredPlan:
    profile: str
    payload: dict[str, object]
    manifests: tuple[ManifestEntry, ...]
    page_plan_sha256: str


class RegisteredSource:
    __slots__ = ("_descriptor", "_identity", "sha256", "size")

    def __init__(self, descriptor: int, sha256: str, size: int) -> None:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size != size
        ):
            raise F0HError("SOURCE_OBJECT_INVALID")
        self._descriptor = descriptor
        self._identity = _identity(before)
        self.sha256 = sha256
        self.size = size

    def fileno(self) -> int:
        if self._descriptor < 0:
            raise F0HError("SOURCE_OBJECT_INVALID")
        return self._descriptor

    def read_verified(self) -> bytearray:
        output = bytearray()
        digest = hashlib.sha256()
        offset = 0
        try:
            self.reverify()
            while offset < self.size:
                wanted = min(_READ_CHUNK, self.size - offset)
                chunk = os.pread(self.fileno(), wanted, offset)
                if len(chunk) != wanted:
                    raise F0HError("SOURCE_OBJECT_CHANGED")
                output.extend(chunk)
                digest.update(chunk)
                offset += len(chunk)
            if (
                os.pread(self.fileno(), 1, self.size)
                or digest.hexdigest() != self.sha256
            ):
                raise F0HError("SOURCE_OBJECT_CHANGED")
            self.reverify()
            return output
        except F0HError:
            output[:] = b"\0" * len(output)
            output.clear()
            raise
        except OSError:
            output[:] = b"\0" * len(output)
            output.clear()
            raise F0HError("SOURCE_OBJECT_CHANGED") from None

    def reverify(self) -> None:
        try:
            metadata = os.fstat(self.fileno())
            if (
                _identity(metadata) != self._identity
                or metadata.st_nlink != 1
                or metadata.st_size != self.size
            ):
                raise F0HError("SOURCE_OBJECT_CHANGED")
        except F0HError:
            raise
        except OSError:
            raise F0HError("SOURCE_OBJECT_CHANGED") from None

    def close(self) -> None:
        descriptor = self._descriptor
        self._descriptor = -1
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                raise F0HError("SOURCE_OBJECT_CHANGED") from None


def load_registered_plan(profile: str) -> RegisteredPlan:
    if profile not in {"smoke", "full"}:
        raise F0HError("REPLAY_MISMATCH")
    route_plan = (
        REGISTERED_SMOKE_ROUTE_PLAN if profile == "smoke" else REGISTERED_FULL_ROUTE_PLAN
    )
    try:
        payload = build_page_plan(
            source_root=REGISTERED_SOURCE_ROOT,
            core_manifest=REGISTERED_CORE_MANIFEST,
            negative_manifest=REGISTERED_NEGATIVE_MANIFEST,
            route_plan_path=route_plan,
            profile=profile,
            expected_identity=ENVIRONMENT_DEMO_V01,
        )
        registered_bytes = _read_fixed(_REGISTERED_PAGE_PLAN[profile], 8 * 1024 * 1024)
        if _json_bytes(payload) != registered_bytes:
            raise F0HError("REPLAY_MISMATCH")
        seen: set[str] = set()
        core, core_sha = _parse_manifest(REGISTERED_CORE_MANIFEST, "core", seen)
        negative, negative_sha = _parse_manifest(
            REGISTERED_NEGATIVE_MANIFEST, "negative", seen
        )
        if (
            core_sha != ENVIRONMENT_DEMO_V01.core_manifest_sha256
            or negative_sha != ENVIRONMENT_DEMO_V01.negative_manifest_sha256
        ):
            raise F0HError("REPLAY_MISMATCH")
        manifests = tuple(_selected_entries(core + negative, profile))
        entries = payload.get("entries")
        if not isinstance(entries, list) or len(entries) != len(manifests):
            raise F0HError("REPLAY_MISMATCH")
        for manifest, entry in zip(manifests, entries, strict=True):
            if (
                not isinstance(entry, dict)
                or (entry.get("group"), entry.get("line"))
                != (manifest.group, manifest.line)
                or (entry.get("group") == "negative")
                and any(
                    entry.get(key) is not False
                    for key in (
                        "enterprise_fact_allowed",
                        "current_regulation_allowed",
                        "search_publish_allowed",
                    )
                )
            ):
                raise F0HError("REPLAY_MISMATCH")
        return RegisteredPlan(
            profile=profile,
            payload=payload,
            manifests=manifests,
            page_plan_sha256=hashlib.sha256(registered_bytes).hexdigest(),
        )
    except F0HError:
        raise
    except (PlannerFailure, ValidationFailure, OSError, TypeError, ValueError):
        raise F0HError("REPLAY_MISMATCH") from None


@contextmanager
def open_registered_source(
    registered: RegisteredPlan, index: int
) -> Iterator[RegisteredSource]:
    root_descriptor = -1
    descriptor = -1
    source: RegisteredSource | None = None
    try:
        if (
            not isinstance(registered, RegisteredPlan)
            or isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < len(registered.manifests)
        ):
            raise F0HError("SOURCE_OBJECT_INVALID")
        entry = registered.manifests[index]
        root_descriptor = _open_path_without_symlinks(
            REGISTERED_SOURCE_ROOT,
            final_flags=os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            unavailable_code="SOURCE_ROOT_UNAVAILABLE",
            symlink_code="SOURCE_ROOT_SYMLINK",
        )
        descriptor = _open_entry(root_descriptor, entry)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 8 <= before.st_size <= 64 * 1024 * 1024
        ):
            raise F0HError("SOURCE_OBJECT_INVALID")
        digest = _hash_fd(descriptor, before.st_size)
        after = os.fstat(descriptor)
        if _identity(before) != _identity(after) or digest != entry.expected_sha256:
            raise F0HError("SOURCE_OBJECT_CHANGED")
        source = RegisteredSource(descriptor, entry.expected_sha256, before.st_size)
        descriptor = -1
        yield source
        source.reverify()
    except F0HError:
        raise
    except (ValidationFailure, OSError, TypeError, ValueError):
        raise F0HError("SOURCE_OBJECT_INVALID") from None
    finally:
        if source is not None:
            source.close()
        if descriptor >= 0:
            os.close(descriptor)
        if root_descriptor >= 0:
            os.close(root_descriptor)


def _hash_fd(descriptor: int, size: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while offset < size:
        chunk = os.pread(descriptor, min(_READ_CHUNK, size - offset), offset)
        if not chunk:
            raise F0HError("SOURCE_OBJECT_CHANGED")
        digest.update(chunk)
        offset += len(chunk)
    if os.pread(descriptor, 1, size):
        raise F0HError("SOURCE_OBJECT_CHANGED")
    return digest.hexdigest()


def _read_fixed(path: Path, limit: int) -> bytes:
    descriptor = -1
    output = bytearray()
    try:
        descriptor = _open_path_without_symlinks(
            path,
            final_flags=os.O_RDONLY | getattr(os, "O_NONBLOCK", 0),
            unavailable_code="REGISTERED_INPUT_UNAVAILABLE",
            symlink_code="REGISTERED_INPUT_UNAVAILABLE",
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 1 <= before.st_size <= limit
        ):
            raise F0HError("REPLAY_MISMATCH")
        while len(output) < before.st_size:
            chunk = os.read(descriptor, before.st_size - len(output))
            if not chunk:
                break
            output.extend(chunk)
        after = os.fstat(descriptor)
        if len(output) != before.st_size or _identity(before) != _identity(after):
            raise F0HError("REPLAY_MISMATCH")
        return bytes(output)
    finally:
        output[:] = b"\0" * len(output)
        output.clear()
        if descriptor >= 0:
            os.close(descriptor)


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


__all__ = (
    "RegisteredPlan",
    "RegisteredSource",
    "load_registered_plan",
    "open_registered_source",
)
