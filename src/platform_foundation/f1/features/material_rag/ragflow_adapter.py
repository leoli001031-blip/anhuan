"""RAGFlow adapter for scope datasets and canonical material units.

All functions in this module are internal.  They accept physical dataset
references only after the product context has been resolved server-side.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Callable, Iterable

from platform_foundation.f0j1.ragflow_client import RagFlowClient

from ...ragflow_provision import RAGFLOW_BASE, ragflow_lock, ragflow_token
from .contracts import CanonicalUnit, MaterialRagIntegrityError, MaterialRagLeaseLost
from .security import (
    AUTHORIZED_MATERIAL_RAG_SOURCE_SHA256,
    assert_external_text_safe,
    remote_document_name,
)


LeaseGuard = Callable[[], bool] | None
MutationFence = Callable[[], AbstractContextManager[None]] | None
_DATASET_ID_RE = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True, slots=True)
class RemoteCandidate:
    canonical_unit_id: uuid.UUID
    knowledge_scope_id: uuid.UUID
    document_record_id: uuid.UUID
    document_version_id: uuid.UUID
    source_sha256: str
    page_number: int
    body_sha256: str


def _guard(lease_guard: LeaseGuard) -> None:
    if lease_guard is not None and not bool(lease_guard()):
        raise MaterialRagLeaseLost("MATERIAL_RAG_LEASE_LOST")


def _authorize_mutation(
    mutation_fence: MutationFence,
) -> AbstractContextManager[None]:
    if mutation_fence is None:
        raise MaterialRagIntegrityError("MATERIAL_RAG_RELEASE_FENCE_REQUIRED")
    return mutation_fence()


def _client() -> tuple[RagFlowClient, str]:
    return RagFlowClient(base_url=RAGFLOW_BASE), ragflow_token()


def _tags(value: object) -> dict[str, str]:
    raw_tags = value if isinstance(value, list) else [value] if value else []
    result: dict[str, str] = {}
    for raw in raw_tags:
        key, separator, item = str(raw).partition("=")
        if separator and key not in result:
            result[key] = item
    return result


def _hydrate_chunks(
    client: RagFlowClient,
    token: str,
    dataset_id: str,
    document_id: str,
    *,
    lease_guard: LeaseGuard = None,
) -> list[dict]:
    _guard(lease_guard)
    chunks = client.list_chunks(token, dataset_id, document_id)
    hydrated: list[dict] = []
    for chunk in chunks:
        chunk_id = chunk.get("id") or chunk.get("chunk_id")
        if not chunk_id:
            raise MaterialRagIntegrityError("MATERIAL_RAG_REMOTE_CHUNK_INVALID")
        _guard(lease_guard)
        detail = client.get_chunk(token, dataset_id, document_id, str(chunk_id))
        merged = dict(chunk)
        merged.update(detail)
        hydrated.append(merged)
    return hydrated


def _remote_unit_map(
    chunks: Iterable[dict],
    *,
    expected: dict[uuid.UUID, CanonicalUnit] | None = None,
    knowledge_scope_id: uuid.UUID | None = None,
    document_version_id: uuid.UUID | None = None,
    source_sha256: str | None = None,
) -> dict[uuid.UUID, str]:
    units: dict[uuid.UUID, str] = {}
    for chunk in chunks:
        tags = _tags(chunk.get("tag_kwd"))
        try:
            unit_id = uuid.UUID(tags["canonical_unit_id"])
            remote_scope_id = uuid.UUID(tags["knowledge_scope_id"])
            remote_record_id = uuid.UUID(tags["document_record_id"])
            remote_version_id = uuid.UUID(tags["document_version_id"])
            remote_source_sha = tags["source_sha256"]
            remote_page = int(tags["page_number"])
        except (KeyError, TypeError, ValueError):
            raise MaterialRagIntegrityError("MATERIAL_RAG_REMOTE_IDENTITY_INVALID") from None
        body_sha = tags.get("body_sha256")
        content = chunk.get("content")
        actual_sha = (
            hashlib.sha256(content.encode("utf-8")).hexdigest()
            if isinstance(content, str)
            else None
        )
        if (
            body_sha is None
            or actual_sha is None
            or body_sha != actual_sha
            or len(body_sha) != 64
            or unit_id in units
        ):
            raise MaterialRagIntegrityError("MATERIAL_RAG_REMOTE_IDENTITY_INVALID")
        if (
            (knowledge_scope_id is not None and remote_scope_id != knowledge_scope_id)
            or (
                document_version_id is not None
                and remote_version_id != document_version_id
            )
            or (source_sha256 is not None and remote_source_sha != source_sha256)
        ):
            raise MaterialRagIntegrityError("MATERIAL_RAG_REMOTE_IDENTITY_INVALID")
        if expected is not None:
            canonical = expected.get(unit_id)
            if canonical is None or (
                remote_scope_id != canonical.knowledge_scope_id
                or remote_record_id != canonical.document_record_id
                or remote_version_id != canonical.document_version_id
                or remote_source_sha != canonical.source_sha256
                or remote_page != canonical.page_number
                or body_sha != canonical.body_sha256
            ):
                raise MaterialRagIntegrityError(
                    "MATERIAL_RAG_REMOTE_IDENTITY_INVALID"
                )
        units[unit_id] = body_sha
    return units


def reconcile_version(
    *,
    dataset_id: str,
    knowledge_scope_id: uuid.UUID,
    document_version_id: uuid.UUID,
    units: tuple[CanonicalUnit, ...],
    rebuild: bool = False,
    lease_guard: LeaseGuard = None,
    mutation_fence: MutationFence = None,
) -> int:
    """Reconcile one version document and verify every remote chunk.

    Exact retries add zero chunks.  An existing canonical identity with a
    different body SHA fails closed rather than overwriting evidence.
    """
    if not _DATASET_ID_RE.fullmatch(dataset_id):
        raise MaterialRagIntegrityError("MATERIAL_RAG_DATASET_BINDING_INVALID")
    if any(
        unit.knowledge_scope_id != knowledge_scope_id
        or unit.document_version_id != document_version_id
        or unit.source_sha256 not in AUTHORIZED_MATERIAL_RAG_SOURCE_SHA256
        for unit in units
    ):
        raise MaterialRagIntegrityError("MATERIAL_RAG_UNIT_SCOPE_MISMATCH")
    expected = {unit.id: unit for unit in units}
    if len(expected) != len(units):
        raise MaterialRagIntegrityError("MATERIAL_RAG_UNIT_DUPLICATE")
    client, token = _client()
    source_sha256_values = {unit.source_sha256 for unit in units}
    if len(source_sha256_values) != 1:
        raise MaterialRagIntegrityError("MATERIAL_RAG_UNIT_SCOPE_MISMATCH")
    name = remote_document_name(source_sha256_values.pop())
    with ragflow_lock(f"material-scope-{knowledge_scope_id.hex}"):
        _guard(lease_guard)
        matches = [
            document
            for document in client.list_all_documents(token, dataset_id)
            if document.get("name") == name
        ]
        if len(matches) > 1:
            raise MaterialRagIntegrityError("MATERIAL_RAG_REMOTE_DOCUMENT_AMBIGUOUS")
        if rebuild and matches:
            with _authorize_mutation(mutation_fence):
                client.delete_documents(token, dataset_id, [str(matches[0]["id"])])
            matches = []
        if matches:
            document_id = str(matches[0].get("id") or "")
        else:
            with _authorize_mutation(mutation_fence):
                document_id = str(
                    client.create_empty_document(token, dataset_id, name).get("id")
                    or ""
                )
        if not document_id:
            raise MaterialRagIntegrityError("MATERIAL_RAG_REMOTE_DOCUMENT_INVALID")

        existing = _remote_unit_map(
            _hydrate_chunks(
                client,
                token,
                dataset_id,
                document_id,
                lease_guard=lease_guard,
            ),
            expected=expected,
            knowledge_scope_id=knowledge_scope_id,
            document_version_id=document_version_id,
        )
        if not set(existing).issubset(expected):
            raise MaterialRagIntegrityError("MATERIAL_RAG_REMOTE_EXTRA_UNIT")
        written = 0
        for unit_id, unit in expected.items():
            prior_sha = existing.get(unit_id)
            if prior_sha is not None:
                if prior_sha != unit.body_sha256:
                    raise MaterialRagIntegrityError("MATERIAL_RAG_REMOTE_BODY_MISMATCH")
                continue
            body = unit.body.reveal()
            assert_external_text_safe(body)
            with _authorize_mutation(mutation_fence):
                client.add_chunk(
                    token,
                    dataset_id,
                    document_id,
                    body,
                    tag_kwd=[
                        f"canonical_unit_id={unit.id}",
                        f"knowledge_scope_id={unit.knowledge_scope_id}",
                        f"document_record_id={unit.document_record_id}",
                        f"document_version_id={unit.document_version_id}",
                        f"source_sha256={unit.source_sha256}",
                        f"page_number={unit.page_number}",
                        f"body_sha256={unit.body_sha256}",
                    ],
                )
            written += 1

        final = _remote_unit_map(
            _hydrate_chunks(
                client,
                token,
                dataset_id,
                document_id,
                lease_guard=lease_guard,
            ),
            expected=expected,
            knowledge_scope_id=knowledge_scope_id,
            document_version_id=document_version_id,
        )
        if set(final) != set(expected):
            raise MaterialRagIntegrityError("MATERIAL_RAG_REMOTE_COUNT_MISMATCH")
        if any(final[unit_id] != unit.body_sha256 for unit_id, unit in expected.items()):
            raise MaterialRagIntegrityError("MATERIAL_RAG_REMOTE_BODY_MISMATCH")
        return written


def delete_version(
    *,
    dataset_id: str,
    knowledge_scope_id: uuid.UUID,
    document_version_id: uuid.UUID,
    source_sha256: str,
    lease_guard: LeaseGuard = None,
) -> int:
    """Delete only the version document from one already-resolved scope."""
    if not _DATASET_ID_RE.fullmatch(dataset_id):
        raise MaterialRagIntegrityError("MATERIAL_RAG_DATASET_BINDING_INVALID")
    if source_sha256 not in AUTHORIZED_MATERIAL_RAG_SOURCE_SHA256:
        raise MaterialRagIntegrityError("MATERIAL_RAG_SOURCE_NOT_AUTHORIZED")
    client, token = _client()
    name = remote_document_name(source_sha256)
    with ragflow_lock(f"material-scope-{knowledge_scope_id.hex}"):
        _guard(lease_guard)
        expected_dataset_name = f"f1-material-{knowledge_scope_id.hex}"
        datasets = client.list_all_datasets(token)
        same_id = [item for item in datasets if item.get("id") == dataset_id]
        same_name = [
            item for item in datasets if item.get("name") == expected_dataset_name
        ]
        # A crash after remote dataset deletion but before the local binding
        # finalize is a normal retry point.  Absence of both exact identities
        # is idempotent; any partial or crossed identity still fails closed.
        if not same_id and not same_name:
            return 0
        if (
            len(same_id) != 1
            or len(same_name) != 1
            or same_id[0].get("name") != expected_dataset_name
            or same_name[0].get("id") != dataset_id
        ):
            raise MaterialRagIntegrityError(
                "MATERIAL_RAG_REMOTE_DATASET_IDENTITY_INVALID"
            )
        matches = [
            document
            for document in client.list_all_documents(token, dataset_id)
            if document.get("name") == name
        ]
        if len(matches) > 1:
            raise MaterialRagIntegrityError("MATERIAL_RAG_REMOTE_DOCUMENT_AMBIGUOUS")
        if not matches:
            return 0
        document_id = str(matches[0].get("id") or "")
        if not document_id:
            raise MaterialRagIntegrityError("MATERIAL_RAG_REMOTE_DOCUMENT_INVALID")
        _remote_unit_map(
            _hydrate_chunks(
                client,
                token,
                dataset_id,
                document_id,
                lease_guard=lease_guard,
            ),
            knowledge_scope_id=knowledge_scope_id,
            document_version_id=document_version_id,
            source_sha256=source_sha256,
        )
        # Cleanup remains authorized after a source is withdrawn: a live
        # delete-job lease plus exact scope/version/source reconciliation is
        # required, but current release state must not strand remote text.
        _guard(lease_guard)
        client.delete_documents(token, dataset_id, [document_id])
        _guard(lease_guard)
        remaining = [
            document
            for document in client.list_all_documents(token, dataset_id)
            if document.get("name") == name
        ]
        if remaining:
            raise MaterialRagIntegrityError("MATERIAL_RAG_REMOTE_DELETE_MISMATCH")
        return 1


def delete_empty_scope_dataset(
    *,
    dataset_id: str,
    knowledge_scope_id: uuid.UUID,
    lease_guard: LeaseGuard = None,
) -> int:
    """Delete exactly one empty scope dataset and prove it no longer exists.

    A retry is idempotent only when both the encrypted binding id and the
    deterministic scope name are absent.  A different dataset under the same
    name, or any remaining document, fails closed.
    """
    if not _DATASET_ID_RE.fullmatch(dataset_id):
        raise MaterialRagIntegrityError("MATERIAL_RAG_DATASET_BINDING_INVALID")
    expected_name = f"f1-material-{knowledge_scope_id.hex}"
    client, token = _client()
    with ragflow_lock(f"material-scope-{knowledge_scope_id.hex}"):
        _guard(lease_guard)
        datasets = client.list_all_datasets(token)
        same_id = [item for item in datasets if item.get("id") == dataset_id]
        same_name = [item for item in datasets if item.get("name") == expected_name]
        if not same_id:
            if same_name:
                raise MaterialRagIntegrityError(
                    "MATERIAL_RAG_REMOTE_DATASET_IDENTITY_INVALID"
                )
            return 0
        if (
            len(same_id) != 1
            or len(same_name) != 1
            or same_id[0].get("name") != expected_name
            or same_name[0].get("id") != dataset_id
        ):
            raise MaterialRagIntegrityError(
                "MATERIAL_RAG_REMOTE_DATASET_IDENTITY_INVALID"
            )
        documents = client.list_all_documents(token, dataset_id)
        if documents:
            raise MaterialRagIntegrityError(
                "MATERIAL_RAG_REMOTE_DATASET_NOT_EMPTY"
            )
        _guard(lease_guard)
        if client.delete_datasets(token, [dataset_id]) != 1:
            raise MaterialRagIntegrityError(
                "MATERIAL_RAG_REMOTE_DATASET_DELETE_MISMATCH"
            )
        _guard(lease_guard)
        remaining = client.list_all_datasets(token)
        if any(
            item.get("id") == dataset_id or item.get("name") == expected_name
            for item in remaining
        ):
            raise MaterialRagIntegrityError(
                "MATERIAL_RAG_REMOTE_DATASET_DELETE_MISMATCH"
            )
        return 1


__all__ = (
    "RemoteCandidate",
    "delete_empty_scope_dataset",
    "delete_version",
    "reconcile_version",
)
