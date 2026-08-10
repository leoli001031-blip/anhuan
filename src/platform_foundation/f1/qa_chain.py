"""F1 evidence-QA chain: per-enterprise RAGFlow retrieval + PG citation + LLM.

Each enterprise owns one RAGFlow dataset; QA only retrieves from that
dataset.  Candidate chunk IDs are re-verified against PostgreSQL under the
enterprise's F0-I tenant context (never hardcoded).  The answer is returned
with citation evidence; the raw Q&A is persisted encrypted by qa_service.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from .auth import Tenant
from .qa_service import QaResult

REFUSE_NO_HITS = "NO_HITS"
REFUSE_DOMAIN = "INVALID_CORPUS_DOMAIN"
REFUSE_CHAIN_UNAVAILABLE = "QA_CHAIN_UNAVAILABLE"


async def run(question: str, tenant: Tenant) -> QaResult:
    """Run the chain for a tenant; returns an answer or a reason-coded refusal."""
    try:
        dataset_id = _dataset_for_enterprise(tenant.enterprise_id)
    except ChainUnavailable:
        return QaResult(None, [], REFUSE_CHAIN_UNAVAILABLE, str(tenant.enterprise_id))
    if dataset_id is None:
        # No dataset provisioned for this enterprise yet -> no corpus.
        return QaResult(None, [], REFUSE_DOMAIN, str(tenant.enterprise_id))
    from platform_foundation.f0j1.retrieval import RetrievalService, validate_domain
    from platform_foundation.f0j1.ragflow_client import RagFlowClient

    from .ragflow_provision import RAGFLOW_BASE

    try:
        ragflow = RagFlowClient(base_url=RAGFLOW_BASE)
        token = _ragflow_token()
        retrieval = RetrievalService(ragflow, token)
        candidates = retrieval.search(question, [dataset_id], size=6)
    except ChainUnavailable:
        return QaResult(None, [], REFUSE_CHAIN_UNAVAILABLE, str(tenant.enterprise_id))
    except Exception:  # noqa: BLE001
        return QaResult(None, [], REFUSE_NO_HITS, str(tenant.enterprise_id))
    if not candidates:
        return QaResult(None, [], REFUSE_NO_HITS, str(tenant.enterprise_id))
    try:
        citations = await _verify_candidates(candidates, tenant)
    except ChainUnavailable:
        return QaResult(None, [], REFUSE_CHAIN_UNAVAILABLE, str(tenant.enterprise_id))
    if not citations:
        return QaResult(None, [], "ALL_CANDIDATES_REJECTED", str(tenant.enterprise_id))
    try:
        answer = _answer(question, citations)
    except ChainUnavailable:
        return QaResult(None, [], REFUSE_CHAIN_UNAVAILABLE, str(tenant.enterprise_id))
    actual, rejection = _actual_citations(answer, citations)
    if rejection is not None:
        return QaResult(None, [], rejection, str(tenant.enterprise_id))
    return QaResult(
        answer=answer,
        citations=actual,
        refusal_reason=None,
        request_id=None,
    )


class ChainUnavailable(RuntimeError):
    pass


def _dataset_for_enterprise(enterprise_id: uuid.UUID) -> str | None:
    """Return the enterprise's RAGFlow dataset id (provisioned by name)."""
    import urllib.error

    from platform_foundation.f0j1.ragflow_client import RagFlowProbeError
    from .ragflow_provision import RagflowProvisionError, dataset_for_enterprise

    try:
        return dataset_for_enterprise(enterprise_id)
    except (
        RagflowProvisionError,
        RagFlowProbeError,
        urllib.error.URLError,
        ConnectionError,
        OSError,
    ):
        raise ChainUnavailable("RAGFLOW_NOT_PROVISIONED") from None


def _ragflow_token() -> str:
    from .ragflow_provision import RagflowProvisionError, ragflow_token

    try:
        return ragflow_token()
    except RagflowProvisionError:
        raise ChainUnavailable("RAGFLOW_TOKEN_UNAVAILABLE") from None


async def _verify_candidates(candidates: list[str], tenant: Tenant) -> list[Any]:
    from .citation import verify_candidates as f1_verify
    from .secret_files import SecretFileError

    chunk_ids = [uuid.UUID(c) for c in candidates if _is_uuid(c)]
    if not chunk_ids:
        return []
    try:
        return await f1_verify(chunk_ids, tenant)
    except SecretFileError:
        raise ChainUnavailable("F0I_KEY_UNAVAILABLE") from None


def _answer(question: str, citations: list[Any]) -> str:
    from .llm_client import F1DeepSeekClient, F1LlmError

    context_chunks = "\n".join(
        f"[chunk_id={c.chunk_id} pages={list(c.pages)}]\n"
        + c.body.decode("utf-8", errors="replace")
        for c in citations[:6]
    )
    prompt = (
        "请基于以下资料回答用户问题。\n"
        "每句事实必须以 [chunk_id=<完整chunk_id>, pages=[页码]] 内联标注来源；"
        "无法确认的必须写「根据已有资料无法确认」。\n\n"
        f"资料：\n{context_chunks}\n\n问题：{question}"
    )
    client = F1DeepSeekClient()
    try:
        return client.complete(prompt, system=_SYSTEM_PROMPT)
    except F1LlmError:
        raise ChainUnavailable("LLM_UNAVAILABLE") from None


_SYSTEM_PROMPT = (
    "你是证据化问答助手，回答必须严格遵守以下规则：\n"
    "1) 每一句陈述事实的句子，必须在句末以 [chunk_id=<完整chunk_id>, pages=[页码]] "
    "的形式内联标注其来源；\n"
    "2) 无法从资料引用的内容，必须明确写「根据已有资料无法确认」，不得编造；\n"
    "3) 禁止输出资料中不存在的具体事实；\n"
    "4) 回答末尾单独列出「引用 chunk_id：」清单。"
)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def _extract_cited_ids(answer: str) -> set[str]:
    """Extract every ``chunk_id=<uuid>`` the LLM cited in its answer."""
    import re

    ids: set[str] = set()
    for value in re.findall(r"chunk_id=([0-9a-fA-F-]{36})", answer):
        try:
            ids.add(str(uuid.UUID(value)))
        except ValueError:
            continue
    return ids


def _extract_citation_refs(answer: str) -> dict[str, set[int]]:
    """Extract syntactically complete ``chunk_id + pages`` references.

    Bare chunk ids are intentionally not accepted: page provenance is part of
    the citation identity and must be checked against PostgreSQL evidence.
    """
    import re

    pattern = re.compile(
        r"\[chunk_id=([0-9a-fA-F-]{36}),\s*pages=\[([0-9,\s]+)\]\]"
    )
    references: dict[str, set[int]] = {}
    for match in pattern.finditer(answer):
        try:
            chunk_id = str(uuid.UUID(match.group(1)))
            pages = {int(value.strip()) for value in match.group(2).split(",")}
        except (TypeError, ValueError):
            continue
        if not pages or any(page <= 0 for page in pages):
            continue
        references.setdefault(chunk_id, set()).update(pages)
    return references


def _actual_citations(
    answer: str, citations: list[Any]
) -> tuple[list[dict], str | None]:
    """Return only LLM-observed references intersected with PG evidence."""
    references = _extract_citation_refs(answer)
    if not references:
        return [], "MISSING_CITATION"
    if _extract_cited_ids(answer) != set(references):
        return [], "INVALID_CITATION_FORMAT"
    verified = {str(item.chunk_id): item for item in citations}
    if not set(references).issubset(verified):
        return [], "FABRICATED_CITATION"

    actual: list[dict] = []
    for chunk_id, cited_pages in references.items():
        evidence = verified[chunk_id]
        observed_pages = set(evidence.pages)
        if not cited_pages.issubset(observed_pages):
            return [], "INVALID_CITATION_PAGE"
        actual.append(
            {
                "chunk_id": chunk_id,
                "document_id": str(evidence.document_id),
                "tenant_id": str(evidence.tenant_id),
                "pages": sorted(cited_pages),
                "body_sha256": evidence.body_sha256,
            }
        )
    return actual, None


__all__ = ("run", "QaResult", "ChainUnavailable")
