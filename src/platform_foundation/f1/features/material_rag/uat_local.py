"""Local-only closed-query material RAG UAT adapter.

Synthetic catalog only.  No PostgreSQL, Ark, RAGFlow, files, or free text.
Enabled exclusively when ``F1_MATERIAL_RAG_UAT_LOCAL=1`` and
``F1_LOCAL_ENGINEERING=1``.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from ...auth import Tenant


ENV_FLAG = "F1_MATERIAL_RAG_UAT_LOCAL"
ENGINEERING_FLAG = "F1_LOCAL_ENGINEERING"

ENTERPRISE_A = uuid.UUID("20000000-0000-4000-8000-00000000000a")
ENTERPRISE_B = uuid.UUID("20000000-0000-4000-8000-00000000000b")
CLIENT_A = uuid.UUID("41000000-0000-4000-8000-0000000000aa")
CLIENT_B = uuid.UUID("41000000-0000-4000-8000-0000000000bb")

PROVIDER_DOCUMENT_RECORD_ID = uuid.UUID("41000000-0000-4000-8000-000000000011")
PROVIDER_DOCUMENT_VERSION_ID = uuid.UUID("41000000-0000-4000-8000-000000000012")
PROVIDER_UNIT_ID = uuid.UUID("41000000-0000-4000-8000-000000000013")
CLIENT_A_DOCUMENT_RECORD_ID = uuid.UUID("41000000-0000-4000-8000-000000000021")
CLIENT_A_DOCUMENT_VERSION_ID = uuid.UUID("41000000-0000-4000-8000-000000000022")
CLIENT_A_UNIT_ID = uuid.UUID("41000000-0000-4000-8000-000000000023")
ENTERPRISE_B_DOCUMENT_RECORD_ID = uuid.UUID("41000000-0000-4000-8000-000000000091")
ENTERPRISE_B_DOCUMENT_VERSION_ID = uuid.UUID("41000000-0000-4000-8000-000000000092")
ENTERPRISE_B_UNIT_ID = uuid.UUID("41000000-0000-4000-8000-000000000093")

CLOSED_QUERY_IDS = frozenset(
    {
        "provider.shared",
        "client.current",
        "combo.provider_client",
        "cross.denied",
        "fail.clear",
        "progress.wait",
    }
)
CLOSED_ENTERPRISES = frozenset({ENTERPRISE_A, ENTERPRISE_B})
CLOSED_CLIENTS = {
    ENTERPRISE_A: frozenset({CLIENT_A, CLIENT_B}),
    ENTERPRISE_B: frozenset(),
}
CLOSED_CLIENT_FIXTURE_NAMES = {
    "UAT-SYNTH-CLIENT-A": CLIENT_A,
    "UAT-SYNTH-CLIENT-B": CLIENT_B,
}
PHYSICAL_DATASET_ID = "ds_must_not_leak"
PHYSICAL_CHUNK_ID = "chunk_must_not_leak"
PHYSICAL_SCOPE_TOKEN = "scope_must_not_leak"

ScopeKind = Literal["service_provider", "client"]


def synthetic_sha(label: str) -> str:
    return hashlib.sha256(f"UAT_SYNTH|{label}".encode("utf-8")).hexdigest()


class LocalUatFault(RuntimeError):
    def __init__(self, code: str, http_status: int) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True, slots=True)
class LocalUatCitation:
    canonical_unit_id: uuid.UUID
    document_record_id: uuid.UUID
    document_version_id: uuid.UUID
    document_name: str
    version_number: int
    source_sha256: str
    page_number: int
    body_sha256: str
    snippet: str
    scope_kind: ScopeKind

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "canonical_unit_id": str(self.canonical_unit_id),
            "document_record_id": str(self.document_record_id),
            "document_version_id": str(self.document_version_id),
            "document_name": self.document_name,
            "version_number": self.version_number,
            "source_sha256": self.source_sha256,
            "page_number": self.page_number,
            "body_sha256": self.body_sha256,
            "snippet": self.snippet,
            "scope_kind": self.scope_kind,
        }


@dataclass(frozen=True, slots=True)
class LocalUatAskResult:
    answer: str | None
    citations: tuple[LocalUatCitation, ...]
    refusal_reason: str | None
    request_id: uuid.UUID
    http_status: int
    scope_label: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "citations": [item.to_public_dict() for item in self.citations],
            "refusal_reason": self.refusal_reason,
            "request_id": str(self.request_id),
            "scope_label": self.scope_label,
        }


@dataclass(frozen=True, slots=True)
class _Unit:
    enterprise_id: uuid.UUID
    client_account_id: uuid.UUID | None
    citation: LocalUatCitation


def _unit(
    *,
    enterprise_id: uuid.UUID,
    client_account_id: uuid.UUID | None,
    canonical_unit_id: uuid.UUID,
    document_record_id: uuid.UUID,
    document_version_id: uuid.UUID,
    document_name: str,
    source_label: str,
    body_label: str,
    snippet: str,
    scope_kind: ScopeKind,
) -> _Unit:
    return _Unit(
        enterprise_id=enterprise_id,
        client_account_id=client_account_id,
        citation=LocalUatCitation(
            canonical_unit_id=canonical_unit_id,
            document_record_id=document_record_id,
            document_version_id=document_version_id,
            document_name=document_name,
            version_number=1,
            source_sha256=synthetic_sha(source_label),
            page_number=1,
            body_sha256=synthetic_sha(body_label),
            snippet=snippet,
            scope_kind=scope_kind,
        ),
    )


_CATALOG: tuple[_Unit, ...] = (
    _unit(
        enterprise_id=ENTERPRISE_A,
        client_account_id=None,
        canonical_unit_id=PROVIDER_UNIT_ID,
        document_record_id=PROVIDER_DOCUMENT_RECORD_ID,
        document_version_id=PROVIDER_DOCUMENT_VERSION_ID,
        document_name="UAT_SYNTH_PROVIDER_DOC",
        source_label="provider-source",
        body_label="provider-body",
        snippet="SYNTH_PROVIDER_HIT",
        scope_kind="service_provider",
    ),
    _unit(
        enterprise_id=ENTERPRISE_A,
        client_account_id=CLIENT_A,
        canonical_unit_id=CLIENT_A_UNIT_ID,
        document_record_id=CLIENT_A_DOCUMENT_RECORD_ID,
        document_version_id=CLIENT_A_DOCUMENT_VERSION_ID,
        document_name="UAT_SYNTH_CLIENT_A_DOC",
        source_label="client-a-source",
        body_label="client-a-body",
        snippet="SYNTH_CLIENT_A_HIT",
        scope_kind="client",
    ),
    _unit(
        enterprise_id=ENTERPRISE_B,
        client_account_id=None,
        canonical_unit_id=ENTERPRISE_B_UNIT_ID,
        document_record_id=ENTERPRISE_B_DOCUMENT_RECORD_ID,
        document_version_id=ENTERPRISE_B_DOCUMENT_VERSION_ID,
        document_name="UAT_SYNTH_ENTERPRISE_B_DOC",
        source_label="enterprise-b-source",
        body_label="enterprise-b-body",
        snippet="SYNTH_ENTERPRISE_B_HIT",
        scope_kind="service_provider",
    ),
)

_asks: dict[tuple[uuid.UUID, uuid.UUID], tuple[str, uuid.UUID | None, LocalUatAskResult]] = {}
_deleted: set[tuple[uuid.UUID, uuid.UUID | None]] = set()
_residuals: dict[tuple[uuid.UUID, uuid.UUID | None], int] = {}
_visible: dict[uuid.UUID, dict[str, Any]] = {}
_mutations = 0
_test_client_binder: Callable[[Tenant, uuid.UUID | None], uuid.UUID | None] | None = None


def local_uat_enabled() -> bool:
    return os.environ.get(ENV_FLAG) == "1" and os.environ.get(ENGINEERING_FLAG) == "1"


def set_test_client_binder(
    binder: Callable[[Tenant, uuid.UUID | None], uuid.UUID | None] | None,
) -> None:
    global _test_client_binder
    _test_client_binder = binder


def catalog_enterprise_for_tenant(tenant: Tenant) -> uuid.UUID:
    enterprise_id = tenant.enterprise_id
    if enterprise_id not in CLOSED_ENTERPRISES:
        raise LocalUatFault("MATERIAL_CONTEXT_NOT_FOUND", 404)
    return enterprise_id


async def bind_client_account(
    tenant: Tenant, client_account_id: uuid.UUID | None
) -> uuid.UUID | None:
    catalog_enterprise_for_tenant(tenant)
    if client_account_id is None:
        return None
    if _test_client_binder is not None:
        return _test_client_binder(tenant, client_account_id)
    from fastapi import HTTPException

    from ..p4 import crm as crm_service

    try:
        account = await crm_service.get_account(tenant, client_account_id)
    except HTTPException:
        raise LocalUatFault("MATERIAL_CONTEXT_NOT_FOUND", 404) from None
    display_name = account.get("display_name") if isinstance(account, dict) else None
    slot = CLOSED_CLIENT_FIXTURE_NAMES.get(display_name)
    allowed = CLOSED_CLIENTS.get(tenant.enterprise_id, frozenset())
    if slot is None or slot not in allowed:
        raise LocalUatFault("MATERIAL_CONTEXT_NOT_FOUND", 404)
    return slot


def reset_store() -> None:
    global _mutations
    _asks.clear()
    _deleted.clear()
    _residuals.clear()
    _visible.clear()
    _mutations = 0


def store_mutation_count() -> int:
    return _mutations


def _require_enabled() -> None:
    if not local_uat_enabled():
        raise LocalUatFault("MATERIAL_RAG_UAT_LOCAL_DISABLED", 404)


def _scope_key(
    enterprise_id: uuid.UUID, client_account_id: uuid.UUID | None
) -> tuple[uuid.UUID, uuid.UUID | None]:
    return (enterprise_id, client_account_id)


def _assert_context(
    enterprise_id: uuid.UUID, client_account_id: uuid.UUID | None
) -> None:
    if enterprise_id not in CLOSED_ENTERPRISES:
        raise LocalUatFault("MATERIAL_CONTEXT_NOT_FOUND", 404)
    if client_account_id is None:
        return
    allowed = CLOSED_CLIENTS.get(enterprise_id, frozenset())
    if client_account_id not in allowed:
        raise LocalUatFault("MATERIAL_CONTEXT_NOT_FOUND", 404)


def _catalog_units(
    enterprise_id: uuid.UUID, client_account_id: uuid.UUID | None
) -> tuple[_Unit, ...]:
    units = tuple(
        unit
        for unit in _CATALOG
        if unit.enterprise_id == enterprise_id
        and unit.client_account_id == client_account_id
        and _scope_key(enterprise_id, client_account_id) not in _deleted
    )
    return units


def _select_units(
    query_id: str,
    enterprise_id: uuid.UUID,
    client_account_id: uuid.UUID | None,
) -> tuple[_Unit, ...]:
    if query_id == "provider.shared":
        return _catalog_units(enterprise_id, None)
    if query_id == "client.current":
        if client_account_id is None:
            raise LocalUatFault("MATERIAL_CONTEXT_NOT_FOUND", 404)
        return _catalog_units(enterprise_id, client_account_id)
    if query_id == "combo.provider_client":
        if client_account_id is None:
            raise LocalUatFault("MATERIAL_CONTEXT_NOT_FOUND", 404)
        return _catalog_units(enterprise_id, None) + _catalog_units(
            enterprise_id, client_account_id
        )
    raise LocalUatFault("QUERY_ID_NOT_AUTHORIZED", 404)


def _scope_label(query_id: str, client_account_id: uuid.UUID | None) -> str:
    if query_id == "provider.shared":
        return "服务商共享域"
    if query_id == "client.current":
        if client_account_id == CLIENT_A:
            return "当前客户域"
        return "当前客户域（空）"
    if query_id == "combo.provider_client":
        if client_account_id == CLIENT_A:
            return "服务商共享域 + 当前客户域"
        return "服务商共享域 + 当前客户域（客户为空，不回退）"
    return "固定场景"


def _result_from_units(
    query_id: str,
    request_id: uuid.UUID,
    client_account_id: uuid.UUID | None,
    units: tuple[_Unit, ...],
    http_status: int = 200,
) -> LocalUatAskResult:
    citations = tuple(unit.citation for unit in units)
    if http_status == 202:
        answer = None
        reason: str | None = "REQUEST_IN_PROGRESS"
    elif not citations:
        answer = None
        reason = "NO_HITS"
    elif query_id == "provider.shared":
        answer = "SYNTH_PROVIDER_ANSWER"
        reason = None
    elif query_id == "client.current":
        answer = "SYNTH_CLIENT_A_ANSWER"
        reason = None
    else:
        answer = "SYNTH_COMBO_ANSWER"
        reason = None
    return LocalUatAskResult(
        answer=answer,
        citations=citations,
        refusal_reason=reason,
        request_id=request_id,
        http_status=http_status,
        scope_label=_scope_label(query_id, client_account_id),
    )


def _set_visible(enterprise_id: uuid.UUID, result: LocalUatAskResult | None) -> None:
    if result is None:
        _visible[enterprise_id] = {"answer": None, "citations": []}
        return
    _visible[enterprise_id] = {
        "answer": result.answer,
        "citations": [item.to_public_dict() for item in result.citations],
    }


def visible_result(enterprise_id: uuid.UUID) -> dict[str, Any]:
    return _visible.get(enterprise_id, {"answer": None, "citations": []})


def ask(
    *,
    query_id: str,
    request_id: uuid.UUID,
    enterprise_id: uuid.UUID,
    client_account_id: uuid.UUID | None,
) -> LocalUatAskResult:
    global _mutations
    _require_enabled()
    if not isinstance(query_id, str) or query_id not in CLOSED_QUERY_IDS:
        raise LocalUatFault("QUERY_ID_NOT_AUTHORIZED", 404)
    if query_id == "cross.denied":
        raise LocalUatFault("MATERIAL_CONTEXT_NOT_FOUND", 404)
    if query_id == "fail.clear":
        _mutations += 1
        _set_visible(enterprise_id, None)
        raise LocalUatFault("MATERIAL_RAG_UNAVAILABLE", 503)
    _assert_context(enterprise_id, client_account_id)
    key = (enterprise_id, request_id)
    existing = _asks.get(key)
    if existing is not None:
        stored_query, stored_client, stored_result = existing
        if stored_query != query_id or stored_client != client_account_id:
            raise LocalUatFault("REQUEST_ID_CONFLICT", 409)
        return stored_result
    if query_id == "progress.wait":
        result = _result_from_units(
            query_id, request_id, client_account_id, (), http_status=202
        )
        _mutations += 1
        _asks[key] = (query_id, client_account_id, result)
        return result
    units = _select_units(query_id, enterprise_id, client_account_id)
    result = _result_from_units(query_id, request_id, client_account_id, units)
    _mutations += 1
    _asks[key] = (query_id, client_account_id, result)
    _set_visible(enterprise_id, result)
    return result


def rebuild(
    *,
    enterprise_id: uuid.UUID,
    client_account_id: uuid.UUID | None,
) -> dict[str, int | str]:
    global _mutations
    _require_enabled()
    _assert_context(enterprise_id, client_account_id)
    key = _scope_key(enterprise_id, client_account_id)
    _deleted.discard(key)
    count = len(_catalog_units(enterprise_id, client_account_id))
    _residuals[key] = count
    _mutations += 1
    return {"status": "rebuilt", "residual_count": count}


def delete_scope(
    *,
    enterprise_id: uuid.UUID,
    client_account_id: uuid.UUID | None,
) -> dict[str, int | str]:
    global _mutations
    _require_enabled()
    _assert_context(enterprise_id, client_account_id)
    key = _scope_key(enterprise_id, client_account_id)
    _deleted.add(key)
    _residuals[key] = 0
    _mutations += 1
    return {"status": "deleted", "residual_count": 0}


def open_citation(
    *,
    enterprise_id: uuid.UUID,
    document_record_id: uuid.UUID,
    document_version_id: uuid.UUID,
) -> dict[str, Any]:
    _require_enabled()
    if enterprise_id not in CLOSED_ENTERPRISES:
        raise LocalUatFault("MATERIAL_CITATION_NOT_FOUND", 404)
    for unit in _CATALOG:
        if (
            unit.enterprise_id == enterprise_id
            and unit.citation.document_record_id == document_record_id
            and unit.citation.document_version_id == document_version_id
        ):
            return {
                "document_record_id": str(unit.citation.document_record_id),
                "document_version_id": str(unit.citation.document_version_id),
                "page_number": unit.citation.page_number,
                "scope_kind": unit.citation.scope_kind,
            }
    raise LocalUatFault("MATERIAL_CITATION_NOT_FOUND", 404)


__all__ = (
    "CLIENT_A",
    "CLIENT_B",
    "CLOSED_CLIENT_FIXTURE_NAMES",
    "CLOSED_QUERY_IDS",
    "ENTERPRISE_A",
    "ENTERPRISE_B",
    "ENGINEERING_FLAG",
    "ENV_FLAG",
    "CLIENT_A_DOCUMENT_RECORD_ID",
    "CLIENT_A_DOCUMENT_VERSION_ID",
    "ENTERPRISE_B_DOCUMENT_RECORD_ID",
    "ENTERPRISE_B_DOCUMENT_VERSION_ID",
    "LocalUatAskResult",
    "LocalUatCitation",
    "LocalUatFault",
    "PHYSICAL_CHUNK_ID",
    "PHYSICAL_DATASET_ID",
    "PHYSICAL_SCOPE_TOKEN",
    "PROVIDER_DOCUMENT_RECORD_ID",
    "PROVIDER_DOCUMENT_VERSION_ID",
    "ask",
    "bind_client_account",
    "catalog_enterprise_for_tenant",
    "delete_scope",
    "local_uat_enabled",
    "open_citation",
    "rebuild",
    "reset_store",
    "set_test_client_binder",
    "store_mutation_count",
    "synthetic_sha",
    "visible_result",
)
