"""F0-J1 evidence-grounded QA service.

Chain: business-domain query -> RAGFlow recall -> PostgreSQL RLS recheck ->
authorized body + page/bbox citations -> DeepSeek LLM answer with mandatory
per-sentence chunk citations.  Refusals use explicit reason codes.

Returned payload is limited to ``answer`` and ``citations``; no source
filenames, no keys, no unauthorized bodies.  Full answer text is never
written to chat/PROGRESS/BLOCKED (leak redline 1/6).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from .citation import Citation, CitationResult, verify_citations
from .llm_client import DeepSeekClient, LlmProbeError
from .retrieval import RetrievalService, validate_domain

REFUSE_REASON_CODES = (
    "NO_HITS",
    "ALL_CANDIDATES_REJECTED",
    "BODY_UNRECONSTRUCTABLE",
    "INVALID_CORPUS_DOMAIN",
    "LLM_REFUSED_CITATION",
    "LLM_UNABLE_TO_CONFIRM",
)

SYSTEM_PROMPT = (
    "你是证据化问答助手，回答必须严格遵守以下规则：\n"
    "1) 每一句陈述事实的句子，必须在句末以 [chunk_id=<完整chunk_id>, pages=[页码]] "
    "的形式内联标注其来源；\n"
    "2) 无法从资料引用的内容，必须明确写「根据已有资料无法确认」，不得编造；\n"
    "3) 禁止输出资料中不存在的具体事实（工艺名、数字、日期等）；\n"
    "4) 回答末尾单独列出「引用 chunk_id：」清单。\n"
    "示例：根据资料[chunk_id=7e32ca5a-b99c-457d-b0d4-fc38b7da7b7f, pages=[2]]，"
    "该厂采用污水池加盖收集措施。"
)


@dataclass(slots=True)
class QaResult:
    answer: str | None
    citations: list[dict[str, Any]]
    refusal_reason: str | None = None


class QaService:
    def __init__(
        self,
        retrieval: RetrievalService,
        llm: DeepSeekClient,
        dataset_ids: list[str],
        max_context: int = 6,
    ) -> None:
        self.retrieval = retrieval
        self.llm = llm
        self.dataset_ids = dataset_ids
        self.max_context = max_context

    def ask(self, query: str) -> QaResult:
        # 1) Corpus-domain validation.
        try:
            validate_domain(self.dataset_ids)
        except ValueError:
            return QaResult(None, [], REFUSE_REASON_CODES[3])

        # 2) Recall.
        try:
            candidates = self.retrieval.search(
                query, self.dataset_ids, size=self.max_context
            )
        except ValueError:
            return QaResult(None, [], REFUSE_REASON_CODES[3])
        if not candidates:
            return QaResult(None, [], REFUSE_REASON_CODES[0])

        # 3) PostgreSQL RLS recheck + body + citations.
        result: CitationResult = verify_citations(
            [uuid.UUID(c) for c in candidates if _is_uuid(c)]
        )
        if not result.verified:
            return QaResult(None, [], REFUSE_REASON_CODES[1])
        for citation in result.verified:
            if not citation.body:
                return QaResult(None, [], REFUSE_REASON_CODES[2])

        # 4) Build the LLM context (bodies only, no filenames/keys).
        context_chunks: list[str] = []
        for citation in result.verified[: self.max_context]:
            context_chunks.append(
                f"[chunk_id={citation.chunk_id} pages={list(citation.pages)}]\n"
                + citation.body.decode("utf-8", errors="replace")
            )
        prompt = (
            "请基于以下资料回答用户问题。\n"
            "每句事实必须以 [chunk_id=<完整chunk_id>, pages=[页码]] 内联标注来源；"
            "无法确认的必须写「根据已有资料无法确认」。\n\n"
            "资料：\n" + "\n\n".join(context_chunks) + "\n\n用户问题：" + query
        )

        # 5) LLM (up to three attempts: the model sometimes omits inline
        # citations; retries with the rule re-emphasised absorb that).
        answer: str | None = None
        for attempt in range(3):
            try:
                answer = self.llm.complete(prompt, system=SYSTEM_PROMPT)
            except LlmProbeError:
                return QaResult(None, [], REFUSE_REASON_CODES[4])
            if answer and _answer_contains_citation(answer, result.verified):
                break
            if answer and _is_unable_to_confirm(answer):
                # The model honestly states it cannot confirm anything from
                # the provided material; that is a compliant refusal.
                return QaResult(None, [], REFUSE_REASON_CODES[5])
            if attempt < 2:
                prompt = (
                    "上一轮回答没有按要求内联引用。请重新回答，"
                    "每一句事实必须以 [chunk_id=<完整chunk_id>, pages=[页码]] 内联标注；"
                    "无法确认的写「根据已有资料无法确认」。\n\n资料：\n"
                    + "\n\n".join(context_chunks) + "\n\n用户问题：" + query
                )
        if not answer or not _answer_contains_citation(answer, result.verified):
            return QaResult(None, [], REFUSE_REASON_CODES[4])

        citations = [
            {
                "chunk_id": str(c.chunk_id),
                "document_id": str(c.document_id),
                "pages": list(c.pages),
                "bbox": [b["bbox"] for b in c.bbox],
                "snippet": c.body.decode("utf-8", errors="replace")[:200],
            }
            for c in result.verified[: self.max_context]
        ]
        return QaResult(answer, citations)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def _answer_contains_citation(answer: str, verified: list[Citation]) -> bool:
    return any(str(c.chunk_id) in answer for c in verified)


def _is_unable_to_confirm(answer: str) -> bool:
    return "无法确认" in answer or "无法提供" in answer


__all__ = ("QaService", "QaResult", "REFUSE_REASON_CODES")
