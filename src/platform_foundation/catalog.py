"""Closed registry for the already approved Environment Demo fixtures.

Only opaque identities leave this module.  Source paths exist briefly in
memory because the frozen manifests require them; they are never included in
exceptions, database records, logs, or artifacts.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from fixture_gate import ENVIRONMENT_DEMO_V01, ValidationFailure
from fixture_gate.validator import (
    ManifestEntry,
    _open_entry,
    _open_path_without_symlinks,
    _parse_manifest,
)
from fixture_router import build_route_plan
from fixture_router.router import (
    REGISTERED_CORE_MANIFEST,
    REGISTERED_NEGATIVE_MANIFEST,
    REGISTERED_SOURCE_ROOT,
    _selected_entries,
    _stat_identity,
)


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ROUTE_PLAN = _PROJECT_ROOT / "artifacts/fixture-routing/v0.1/route-plan.json"
_NATIVE_PLAN = _PROJECT_ROOT / "artifacts/fixture-native-plan/v0.1/full-plan.json"
_ROUTE_PLAN_SHA256 = (
    "2937047ed5d2c6db7f73ba7d8ba597acd24ec376cde73b5b48e529ac6cf5004c"
)
_NATIVE_PLAN_SHA256 = (
    "08c8a3e972950cd2be88f86a4f79d9727c39f81e12a321c50cb3a46581534436"
)
_SOURCE_NAMESPACE = uuid.UUID("5a4940b4-cb56-5e76-8a62-70cd9e30084f")
_MAX_PLAN_BYTES = 4 * 1024 * 1024


class CatalogError(RuntimeError):
    """A stable path-free catalog failure."""

    _ALLOWED = frozenset(
        {
            "CATALOG_IDENTITY_MISMATCH",
            "CATALOG_PLAN_INVALID",
            "CATALOG_SOURCE_UNAVAILABLE",
            "CATALOG_SOURCE_CHANGED",
            "CATALOG_SOURCE_NOT_REGISTERED",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._ALLOWED:
            code = "CATALOG_PLAN_INVALID"
        self.code = code
        super().__init__(code)

    def to_dict(self) -> dict[str, str]:
        return {"error": "CATALOG_ERROR", "reason_code": self.code}


@dataclass(frozen=True)
class CatalogEntry:
    source_id: uuid.UUID
    document_id: str
    fixture_set_id: str
    fixture_version: str
    group: str
    line: int
    expected_sha256: str
    expected_size: int
    document_type: str
    corpus_role: str
    enterprise_fact_allowed: bool
    current_regulation_allowed: bool
    search_publish_allowed: bool
    plan: dict[str, object] = field(repr=False, compare=False)
    _manifest_entry: ManifestEntry = field(repr=False, compare=False)

    def public_record(self) -> dict[str, object]:
        return {
            "source_id": str(self.source_id),
            "document_id": self.document_id,
            "fixture_set_id": self.fixture_set_id,
            "fixture_version": self.fixture_version,
            "group": self.group,
            "line": self.line,
            "expected_sha256": self.expected_sha256,
            "expected_size": self.expected_size,
            "document_type": self.document_type,
            "corpus_role": self.corpus_role,
            "enterprise_fact_allowed": self.enterprise_fact_allowed,
            "current_regulation_allowed": self.current_regulation_allowed,
            "search_publish_allowed": self.search_publish_allowed,
        }


def _read_fixed_json(path: Path, expected_sha256: str) -> dict[str, object]:
    try:
        descriptor = _open_path_without_symlinks(
            path,
            final_flags=os.O_RDONLY | getattr(os, "O_NONBLOCK", 0),
            unavailable_code="REGISTERED_INPUT_UNAVAILABLE",
            symlink_code="REGISTERED_INPUT_UNAVAILABLE",
        )
    except ValidationFailure:
        raise CatalogError("CATALOG_IDENTITY_MISMATCH") from None
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > _MAX_PLAN_BYTES
        ):
            raise CatalogError("CATALOG_IDENTITY_MISMATCH")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            total += len(chunk)
            if total > _MAX_PLAN_BYTES:
                raise CatalogError("CATALOG_IDENTITY_MISMATCH")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        payload = b"".join(chunks)
        if (
            _stat_identity(before) != _stat_identity(after)
            or hashlib.sha256(payload).hexdigest() != expected_sha256
        ):
            raise CatalogError("CATALOG_IDENTITY_MISMATCH")
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise CatalogError("CATALOG_PLAN_INVALID")
        return value
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise CatalogError("CATALOG_PLAN_INVALID") from None
    finally:
        os.close(descriptor)


def _manifest_entries() -> list[ManifestEntry]:
    try:
        seen: set[str] = set()
        core, core_sha = _parse_manifest(REGISTERED_CORE_MANIFEST, "core", seen)
        negative, negative_sha = _parse_manifest(
            REGISTERED_NEGATIVE_MANIFEST, "negative", seen
        )
    except ValidationFailure:
        raise CatalogError("CATALOG_IDENTITY_MISMATCH") from None
    if (
        core_sha != ENVIRONMENT_DEMO_V01.core_manifest_sha256
        or negative_sha != ENVIRONMENT_DEMO_V01.negative_manifest_sha256
    ):
        raise CatalogError("CATALOG_IDENTITY_MISMATCH")
    return core + negative


def _source_size(entry: ManifestEntry) -> int:
    try:
        root = _open_path_without_symlinks(
            REGISTERED_SOURCE_ROOT,
            final_flags=os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            unavailable_code="SOURCE_ROOT_UNAVAILABLE",
            symlink_code="SOURCE_ROOT_SYMLINK",
        )
        try:
            descriptor = _open_entry(root, entry)
        finally:
            os.close(root)
    except (OSError, ValidationFailure):
        raise CatalogError("CATALOG_SOURCE_UNAVAILABLE") from None
    try:
        source_stat = os.fstat(descriptor)
        if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_nlink != 1:
            raise CatalogError("CATALOG_SOURCE_UNAVAILABLE")
        return int(source_stat.st_size)
    except OSError:
        raise CatalogError("CATALOG_SOURCE_UNAVAILABLE") from None
    finally:
        os.close(descriptor)


def load_catalog(profile: str = "full") -> tuple[CatalogEntry, ...]:
    """Rebuild the frozen route evidence and return the closed registry."""

    if profile not in {"smoke", "full"}:
        raise CatalogError("CATALOG_PLAN_INVALID")
    route_plan = _read_fixed_json(_ROUTE_PLAN, _ROUTE_PLAN_SHA256)
    native_plan = _read_fixed_json(_NATIVE_PLAN, _NATIVE_PLAN_SHA256)
    try:
        live_route = build_route_plan(
            source_root=REGISTERED_SOURCE_ROOT,
            core_manifest=REGISTERED_CORE_MANIFEST,
            negative_manifest=REGISTERED_NEGATIVE_MANIFEST,
            profile="full",
            expected_identity=ENVIRONMENT_DEMO_V01,
        )
    except Exception:
        raise CatalogError("CATALOG_SOURCE_CHANGED") from None
    if live_route != route_plan:
        raise CatalogError("CATALOG_SOURCE_CHANGED")
    if (
        native_plan.get("fixture_set_id") != ENVIRONMENT_DEMO_V01.fixture_set_id
        or native_plan.get("fixture_version") != ENVIRONMENT_DEMO_V01.fixture_version
        or native_plan.get("input_route_plan_sha256") != _ROUTE_PLAN_SHA256
        or native_plan.get("raw_text_persisted") is not False
        or native_plan.get("ocr_executed") is not False
        or native_plan.get("benchmark_tier") != "NONE"
        or native_plan.get("external_processing") != "DENY"
    ):
        raise CatalogError("CATALOG_PLAN_INVALID")
    planned = native_plan.get("entries")
    if not isinstance(planned, list):
        raise CatalogError("CATALOG_PLAN_INVALID")
    plan_by_key = {
        (entry.get("group"), entry.get("line")): entry
        for entry in planned
        if isinstance(entry, dict)
    }
    all_entries = _manifest_entries()
    selected = _selected_entries(all_entries, profile)
    result: list[CatalogEntry] = []
    for manifest_entry in selected:
        plan_entry = plan_by_key.get((manifest_entry.group, manifest_entry.line))
        if not isinstance(plan_entry, dict):
            raise CatalogError("CATALOG_PLAN_INVALID")
        document_id = plan_entry.get("document_id")
        if not isinstance(document_id, str) or len(document_id) != 64:
            raise CatalogError("CATALOG_PLAN_INVALID")
        source_id = uuid.uuid5(
            _SOURCE_NAMESPACE,
            "\0".join(
                (
                    ENVIRONMENT_DEMO_V01.fixture_set_id,
                    ENVIRONMENT_DEMO_V01.fixture_version,
                    manifest_entry.group,
                    str(manifest_entry.line),
                    document_id,
                )
            ),
        )
        result.append(
            CatalogEntry(
                source_id=source_id,
                document_id=document_id,
                fixture_set_id=ENVIRONMENT_DEMO_V01.fixture_set_id,
                fixture_version=ENVIRONMENT_DEMO_V01.fixture_version,
                group=manifest_entry.group,
                line=manifest_entry.line,
                expected_sha256=manifest_entry.expected_sha256,
                expected_size=_source_size(manifest_entry),
                document_type=str(plan_entry.get("type")),
                corpus_role=str(plan_entry.get("corpus_role")),
                enterprise_fact_allowed=(
                    plan_entry.get("enterprise_fact_allowed") is True
                ),
                current_regulation_allowed=(
                    plan_entry.get("current_regulation_allowed") is True
                ),
                search_publish_allowed=(
                    plan_entry.get("search_publish_allowed") is True
                ),
                plan=plan_entry,
                _manifest_entry=manifest_entry,
            )
        )
    return tuple(result)


@contextmanager
def open_catalog_source(entry: CatalogEntry) -> Iterator[int]:
    """Open one registered source through restricted descriptors only."""

    try:
        root = _open_path_without_symlinks(
            REGISTERED_SOURCE_ROOT,
            final_flags=os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            unavailable_code="SOURCE_ROOT_UNAVAILABLE",
            symlink_code="SOURCE_ROOT_SYMLINK",
        )
        try:
            descriptor = _open_entry(root, entry._manifest_entry)
        finally:
            os.close(root)
    except (OSError, ValidationFailure):
        raise CatalogError("CATALOG_SOURCE_UNAVAILABLE") from None
    before: os.stat_result | None = None
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size != entry.expected_size
        ):
            raise CatalogError("CATALOG_SOURCE_CHANGED")
        yield descriptor
        after = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(after):
            raise CatalogError("CATALOG_SOURCE_CHANGED")
    except OSError:
        raise CatalogError("CATALOG_SOURCE_UNAVAILABLE") from None
    finally:
        os.close(descriptor)


def catalog_entry_by_id(source_id: uuid.UUID, profile: str = "full") -> CatalogEntry:
    for entry in load_catalog(profile):
        if entry.source_id == source_id:
            return entry
    raise CatalogError("CATALOG_SOURCE_NOT_REGISTERED")


__all__ = (
    "CatalogEntry",
    "CatalogError",
    "catalog_entry_by_id",
    "load_catalog",
    "open_catalog_source",
)
