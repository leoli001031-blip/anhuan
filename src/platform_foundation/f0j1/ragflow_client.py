"""F0-J1 RAGFlow HTTP client (stdlib only).

Extends the F0-J0 probe client with dataset/document/chunk/retrieval
operations used by the retrieval + evidence-QA chain.  No SDK dependency.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class RagFlowProbeError(RuntimeError):
    """Body-free RAGFlow failure safe for logs and wrapper exceptions."""

    def __init__(
        self,
        reason: str,
        *,
        status: int | None = None,
        already_exists: bool = False,
    ) -> None:
        self.reason = reason
        self.status = status
        self.already_exists = already_exists
        suffix = f" status={status}" if status is not None else ""
        super().__init__(f"{reason}{suffix}")


def _response_indicates_already_exists(data: Any) -> bool:
    """Classify idempotent conflicts without exposing the remote message."""
    if not isinstance(data, dict):
        return False
    message = data.get("message")
    return isinstance(message, str) and "already" in message.lower()


class RagFlowClient:
    def __init__(self, base_url: str = "http://127.0.0.1:80", timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_url = f"{self.base_url}/api/v1"
        self._timeout = timeout

    def _request(
        self, method: str, path: str, token: str | None, payload: Any = None
    ) -> tuple[int, Any]:
        body = None
        headers: dict[str, str] = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            self.api_url + path, data=body, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
            raw = error.read()
        try:
            return status, json.loads(raw) if raw else {}
        except Exception:
            return status, {"raw_bytes": len(raw)}

    # --- datasets ---
    def list_datasets(self, token: str) -> list[dict[str, Any]]:
        status, data = self._request("GET", "/datasets", token)
        if status != 200 or data.get("code") != 0:
            raise RagFlowProbeError("DATASET_LIST_FAILED", status=status)
        return data.get("data", [])

    def list_all_datasets(self, token: str) -> list[dict[str, Any]]:
        """List every dataset for identity reconciliation, not one UI page."""
        result: list[dict[str, Any]] = []
        page = 1
        while page <= 10_000:
            status, data = self._request(
                "GET", f"/datasets?page={page}&page_size=100", token
            )
            if status != 200 or data.get("code") != 0:
                raise RagFlowProbeError("DATASET_LIST_FAILED", status=status)
            raw = data.get("data", [])
            datasets = (
                raw.get("datasets", raw.get("docs", []))
                if isinstance(raw, dict)
                else raw
            )
            if not isinstance(datasets, list):
                raise RagFlowProbeError("DATASET_LIST_FAILED invalid_data")
            result.extend(item for item in datasets if isinstance(item, dict))
            total = int(raw.get("total", 0)) if isinstance(raw, dict) else 0
            if (
                not datasets
                or (total and len(result) >= total)
                or (not total and len(datasets) < 100)
            ):
                return result
            page += 1
        raise RagFlowProbeError("DATASET_LIST_FAILED pagination_limit")

    def create_dataset(self, token: str, name: str, embedding_model: str) -> dict[str, Any]:
        status, data = self._request(
            "POST",
            "/datasets",
            token,
            {"name": name, "chunk_method": "naive", "embedding_model": embedding_model},
        )
        if status != 200 or data.get("code") != 0:
            raise RagFlowProbeError("DATASET_CREATE_FAILED", status=status)
        return data["data"]

    def delete_datasets(self, token: str, ids: list[str]) -> int:
        status, data = self._request("DELETE", "/datasets", token, {"ids": ids})
        if status != 200 or data.get("code") != 0:
            raise RagFlowProbeError("DATASET_DELETE_FAILED", status=status)
        return int(data.get("data", {}).get("success_count", 0))

    # --- documents ---
    def create_empty_document(self, token: str, dataset_id: str, name: str) -> dict[str, Any]:
        status, data = self._request(
            "POST",
            f"/datasets/{dataset_id}/documents?type=empty",
            token,
            {"name": name, "parser_method": "naive"},
        )
        if status != 200 or data.get("code") != 0:
            raise RagFlowProbeError("DOC_CREATE_FAILED", status=status)
        return data["data"]

    def list_documents(self, token: str, dataset_id: str) -> list[dict[str, Any]]:
        status, data = self._request("GET", f"/datasets/{dataset_id}/documents", token)
        if status != 200 or data.get("code") != 0:
            raise RagFlowProbeError("DOC_LIST_FAILED", status=status)
        docs = data.get("data", {})
        if isinstance(docs, dict):
            return docs.get("docs", [])
        return docs

    def list_all_documents(
        self, token: str, dataset_id: str
    ) -> list[dict[str, Any]]:
        """List every document so duplicate/version checks are authoritative."""
        result: list[dict[str, Any]] = []
        page = 1
        while page <= 10_000:
            status, data = self._request(
                "GET",
                f"/datasets/{dataset_id}/documents?page={page}&page_size=100",
                token,
            )
            if status != 200 or data.get("code") != 0:
                raise RagFlowProbeError("DOC_LIST_FAILED", status=status)
            raw = data.get("data", {})
            documents = raw.get("docs", []) if isinstance(raw, dict) else raw
            if not isinstance(documents, list):
                raise RagFlowProbeError("DOC_LIST_FAILED invalid_data")
            result.extend(item for item in documents if isinstance(item, dict))
            total = int(raw.get("total", 0)) if isinstance(raw, dict) else 0
            if (
                not documents
                or (total and len(result) >= total)
                or (not total and len(documents) < 100)
            ):
                return result
            page += 1
        raise RagFlowProbeError("DOC_LIST_FAILED pagination_limit")

    def delete_documents(self, token: str, dataset_id: str, ids: list[str]) -> int:
        status, data = self._request(
            "DELETE", f"/datasets/{dataset_id}/documents", token, {"ids": ids}
        )
        if status != 200:
            raise RagFlowProbeError("DOC_DELETE_FAILED", status=status)
        return int(data.get("data", {}).get("success_count", 0))

    # --- chunks ---
    def add_chunk(
        self,
        token: str,
        dataset_id: str,
        document_id: str,
        content: str,
        important_keywords: list[str] | None = None,
        questions: list[str] | None = None,
        tag_kwd: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "content": content,
            "important_keywords": important_keywords or [],
            "questions": questions or [],
        }
        if tag_kwd:
            payload["tag_kwd"] = tag_kwd
        status, data = self._request(
            "POST",
            f"/datasets/{dataset_id}/documents/{document_id}/chunks",
            token,
            payload,
        )
        if status != 200 or data.get("code") != 0:
            raise RagFlowProbeError("CHUNK_ADD_FAILED", status=status)
        return data["data"]

    def list_chunks(
        self, token: str, dataset_id: str, document_id: str
    ) -> list[dict[str, Any]]:
        """List all chunks of a document (paginated, page_size <= 100)."""
        all_chunks: list[dict[str, Any]] = []
        page = 1
        while True:
            status, data = self._request(
                "GET",
                f"/datasets/{dataset_id}/documents/{document_id}/chunks"
                f"?page={page}&page_size=100",
                token,
            )
            if status != 200 or data.get("code") != 0:
                raise RagFlowProbeError("CHUNK_LIST_FAILED", status=status)
            chunks = data.get("data", {}).get("chunks", [])
            if not isinstance(chunks, list):
                raise RagFlowProbeError("CHUNK_LIST_FAILED invalid_data")
            all_chunks.extend(item for item in chunks if isinstance(item, dict))
            total = int(data.get("data", {}).get("total", 0))
            if (
                not chunks
                or (total and len(all_chunks) >= total)
                or (not total and len(chunks) < 100)
            ):
                break
            page += 1
        return all_chunks

    def get_chunk(
        self, token: str, dataset_id: str, document_id: str, chunk_id: str
    ) -> dict[str, Any]:
        """Return one chunk including its adapter metadata tags."""
        status, data = self._request(
            "GET",
            f"/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id}",
            token,
        )
        if status != 200 or data.get("code") != 0:
            raise RagFlowProbeError("CHUNK_GET_FAILED", status=status)
        chunk = data.get("data") or {}
        if not isinstance(chunk, dict):
            raise RagFlowProbeError("CHUNK_GET_FAILED invalid_data")
        return chunk

    def real_dataset_chunk_count(self, token: str, dataset_id: str) -> int:
        """Authoritative chunk count: sum of per-document list_chunks.

        The dataset-level ``chunk_count`` field is a local counter that can
        drift from the index; this walks the actual chunk list instead.
        A short retry absorbs transient 502s right after a restart.
        """
        import time

        last_error: Exception | None = None
        for attempt in range(4):
            try:
                total = 0
                for doc in self.list_documents(token, dataset_id):
                    total += len(self.list_chunks(token, dataset_id, doc["id"]))
                return total
            except RagFlowProbeError as error:
                last_error = error
                time.sleep(3 * (attempt + 1))
        raise last_error  # type: ignore[misc]

    def delete_chunks(
        self, token: str, dataset_id: str, document_id: str, ids: list[str] | None = None
    ) -> bool:
        """Delete chunks; returns True when the API accepted the request.

        The RAGFlow DELETE handler returns ``{"code": 0}`` without reporting
        the deleted count, so success is judged by the response code; callers
        verify the effect via ``real_dataset_chunk_count``.
        """
        payload: dict[str, Any] = {"delete_all": ids is None}
        if ids:
            payload["chunk_ids"] = ids
        status, data = self._request(
            "DELETE",
            f"/datasets/{dataset_id}/documents/{document_id}/chunks",
            token,
            payload,
        )
        if status != 200:
            raise RagFlowProbeError("CHUNK_DELETE_FAILED", status=status)
        if data.get("code") != 0:
            raise RagFlowProbeError("CHUNK_DELETE_FAILED", status=status)
        return True

    # --- retrieval ---
    def retrieval(
        self, token: str, dataset_ids: list[str], question: str, page_size: int = 5
    ) -> list[dict[str, Any]]:
        status, data = self._request(
            "POST",
            "/retrieval",
            token,
            {"question": question, "dataset_ids": dataset_ids, "page_size": page_size},
        )
        if status != 200 or data.get("code") != 0:
            raise RagFlowProbeError("RETRIEVAL_FAILED", status=status)
        return data.get("data", {}).get("chunks", [])

    # --- providers ---
    def add_provider(self, token: str, provider_name: str) -> None:
        status, data = self._request(
            "PUT", "/providers", token, {"provider_name": provider_name}
        )
        if status != 200 or data.get("code") != 0:
            raise RagFlowProbeError("PROVIDER_ADD_FAILED", status=status)

    def create_provider_instance(
        self,
        token: str,
        provider_name: str,
        instance_name: str,
        api_key: str,
        base_url: str,
        model_info: list[dict[str, Any]],
    ) -> None:
        status, data = self._request(
            "POST",
            f"/providers/{provider_name}/instances",
            token,
            {
                "instance_name": instance_name,
                "api_key": api_key,
                "base_url": base_url,
                "model_info": model_info,
            },
        )
        if status != 200 or data.get("code") != 0:
            raise RagFlowProbeError(
                "INSTANCE_CREATE_FAILED",
                status=status,
                already_exists=(
                    status == 409 or _response_indicates_already_exists(data)
                ),
            )


__all__ = ("RagFlowClient", "RagFlowProbeError")
