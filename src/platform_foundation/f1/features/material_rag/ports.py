"""Internal repository and transport ports for material retrieval.

These types are not a public API.  They exist so the production service can
keep fail-closed logic in one place while PostgreSQL and the remote adapter
stay behind a typed I/O seam.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from ...auth import Tenant
from .contracts import RetrievalContext, ScopeKind


@dataclass(frozen=True, slots=True)
class ScopeBinding:
    knowledge_scope_id: uuid.UUID
    dataset_ref: str


@dataclass(frozen=True, slots=True)
class ReleasedUnitRecord:
    canonical_unit_id: uuid.UUID
    knowledge_scope_id: uuid.UUID
    document_record_id: uuid.UUID
    document_version_id: uuid.UUID
    source_sha256: str
    page_number: int
    body_sha256: str
    body: str
    scope_kind: ScopeKind
    document_name: str
    version_number: int


class MaterialRagRepository(Protocol):
    async def load_provider_scope_id(self, tenant: Tenant) -> uuid.UUID | None:
        """Return the actor-visible provider scope, or None."""

    async def load_client_scope_id(
        self, tenant: Tenant, client_account_id: uuid.UUID
    ) -> uuid.UUID | None:
        """Return the named client scope, or None.  Must not fall back."""

    async def load_ready_bindings(
        self, tenant: Tenant, context: RetrievalContext
    ) -> tuple[ScopeBinding, ...]:
        """Return ready dataset bindings for the resolved context scopes."""

    async def load_released_units(
        self,
        tenant: Tenant,
        context: RetrievalContext,
        unit_ids: tuple[uuid.UUID, ...],
    ) -> tuple[ReleasedUnitRecord, ...]:
        """Return currently released, in-scope units for candidate ids."""


class MaterialRagTransport(Protocol):
    async def retrieve_candidates(
        self,
        query: str,
        datasets: tuple[ScopeBinding, ...],
        limit: int,
    ) -> tuple[object, ...]:
        """Return untrusted remote candidates for already-authorized text."""


__all__ = (
    "MaterialRagRepository",
    "MaterialRagTransport",
    "ReleasedUnitRecord",
    "ScopeBinding",
)
