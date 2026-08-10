"""F0-J1 evaluation sample generator.

Generates a JSON file containing QA pairs with full citation bodies so that
an LLM judge (or human reviewer) can assess citation faithfulness without
needing environmental-domain expertise.

Run only when the F0-J1 RAGFlow probe stack is up.  The output file is placed
under ``artifacts/f0j1-retrieval-qa/v0.1/`` with 0700/0600 permissions and is
excluded from git.

Leak rules: output contains chunk bodies (necessary for judging) but never
source filenames, keys, DSNs, or absolute paths.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any

from platform_foundation.f0j1.citation import verify_citations
from platform_foundation.f0j1.llm_client import DeepSeekClient
from platform_foundation.f0j1.qa_service import QaResult, QaService
from platform_foundation.f0j1.ragflow_client import RagFlowClient
from platform_foundation.f0j1.retrieval import RetrievalService

PROJECT = "anhuan-f0j1-ragflow"
BASE_URL = "http://127.0.0.1:80"
DATASET_NAME = "f0j1-canonical"
TOKEN_FILE = "/private/tmp/anhuan-f0j1-secrets/ragflow_api_key"
OUTPUT_DIR = Path("artifacts/f0j1-retrieval-qa/v0.1")
OUTPUT_FILE = OUTPUT_DIR / "evaluation_samples.json"

# Evaluation corpus: mix of answerable, refusal, and stress queries.
# The answerable ones are chosen to be generic enough for environmental
# compliance documents (EIA reports, waste management plans, etc.).
EVAL_QUERIES: list[dict[str, Any]] = [
    # --- answerable ---
    {"id": "q001", "query": "废气治理采用什么方案？", "expected": "answerable"},
    {"id": "q002", "query": "企业的危废代码有哪些？", "expected": "answerable"},
    {"id": "q003", "query": "环评报告的有效期到什么时候？", "expected": "answerable"},
    {"id": "q004", "query": "污水处理采用什么工艺？", "expected": "answerable"},
    {"id": "q005", "query": "厂区有哪些环保设施？", "expected": "answerable"},
    {"id": "q006", "query": "整改措施包括哪些内容？", "expected": "answerable"},
    {"id": "q007", "query": "这家企业需要执行哪些排放标准？", "expected": "answerable"},
    {"id": "q008", "query": "环保设施的运行要求有哪些？", "expected": "answerable"},
    # --- refusal / no hits ---
    {"id": "q009", "query": "zzzzzz不存在的内容关键字qwertyuiop", "expected": "refusal"},
    {"id": "q010", "query": "这家企业的法定代表人手机号是多少？", "expected": "refusal"},
    {"id": "q011", "query": "该项目的银行贷款金额是多少？", "expected": "refusal"},
    # --- stress / hallucination-prone ---
    {"id": "q012", "query": "请详细说明该企业的生产工艺流程和原辅材料清单。", "expected": "answerable_or_refusal"},
    {"id": "q013", "query": "根据资料，该企业在 2024 年受到了哪些环保行政处罚？", "expected": "answerable_or_refusal"},
    {"id": "q014", "query": "请列出所有涉及有毒有害物质的名录和存储量。", "expected": "answerable_or_refusal"},
]


def _load_token() -> str:
    path = Path(TOKEN_FILE)
    if not path.is_file():
        print(f"F0J1_EVAL_ERROR: token file missing: {TOKEN_FILE}", file=sys.stderr)
        sys.exit(2)
    return path.read_text(encoding="ascii").strip()


def _find_dataset(client: RagFlowClient, token: str) -> dict[str, Any]:
    for dataset in client.list_datasets(token):
        if dataset.get("name") == DATASET_NAME:
            return dataset
    raise RuntimeError(f"dataset {DATASET_NAME!r} not found")


def _run_sample(qa: QaService, item: dict[str, Any]) -> dict[str, Any]:
    result: QaResult = qa.ask(item["query"])
    record: dict[str, Any] = {
        "sample_id": item["id"],
        "query": item["query"],
        "expected_behavior": item["expected"],
        "refusal_reason": result.refusal_reason,
    }
    if result.answer:
        record["answer"] = result.answer
    else:
        record["answer"] = None

    # Replace QaService's truncated snippets with full verified bodies so the
    # judge can actually verify citation faithfulness.
    full_bodies: dict[str, str] = {}
    if result.citations:
        chunk_uuids = [uuid.UUID(c["chunk_id"]) for c in result.citations]
        verified = verify_citations(chunk_uuids).verified
        full_bodies = {str(c.chunk_id): c.body.decode("utf-8", errors="replace") for c in verified}

    record["citations"] = []
    for citation in result.citations:
        record["citations"].append(
            {
                "chunk_id": citation["chunk_id"],
                "document_id": citation["document_id"],
                "pages": citation["pages"],
                "body": full_bodies.get(citation["chunk_id"], citation["snippet"]),
            }
        )
    return record


def main() -> int:
    client = RagFlowClient(base_url=BASE_URL)
    token = _load_token()
    dataset = _find_dataset(client, token)
    dataset_id = str(dataset["id"])
    try:
        uuid.UUID(dataset_id)
    except ValueError:
        print("F0J1_EVAL_ERROR: dataset id is not a UUID", file=sys.stderr)
        return 2

    retrieval = RetrievalService(client, token)
    llm = DeepSeekClient()
    qa = QaService(retrieval, llm, [dataset_id])

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.chmod(0o700)

    samples: list[dict[str, Any]] = []
    for item in EVAL_QUERIES:
        try:
            sample = _run_sample(qa, item)
        except Exception as error:
            sample = {
                "sample_id": item["id"],
                "query": item["query"],
                "expected_behavior": item["expected"],
                "error": f"{type(error).__name__}: {error}",
            }
        samples.append(sample)

    payload = {
        "schema": "f0j1-evaluation-samples-v1",
        "dataset_id": dataset_id,
        "dataset_name": DATASET_NAME,
        "model": llm.model,
        "sample_count": len(samples),
        "samples": samples,
        "notes": (
            "Evaluation focuses on citation faithfulness, not environmental "
            "domain accuracy. Each sample contains the LLM answer and the "
            "cited chunk bodies so a judge can verify whether the answer is "
            "grounded in the retrieved text."
        ),
    }

    OUTPUT_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    OUTPUT_FILE.chmod(0o600)

    # Print only a count summary to stdout; never print answers or bodies.
    total = len(samples)
    answered = sum(1 for s in samples if s.get("answer"))
    refused = sum(1 for s in samples if s.get("refusal_reason"))
    errors = sum(1 for s in samples if "error" in s)
    print(
        json.dumps(
            {
                "status": "OK",
                "output": str(OUTPUT_FILE),
                "total": total,
                "answered": answered,
                "refused": refused,
                "errors": errors,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
