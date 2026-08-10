"""Minimal OpenSearch HTTP probe client (stdlib only).

Probe-specific surface that talks to a loopback-only OpenSearch over its
self-signed TLS endpoint.  Uses only ``urllib.request`` + ``ssl`` (no
production dependency).  Never sends credentials in cleartext; the admin
password is read from the probe secrets file at call time and is never
logged.
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class OpenSearchProbeError(RuntimeError):
    pass


class OpenSearchClient:
    def __init__(
        self,
        base_url: str = "https://127.0.0.1:9200",
        password_file: str = "/private/tmp/anhuan-f0j0-secrets/os_admin",
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._password_file = Path(password_file)
        self._timeout = timeout
        # Self-signed cert is expected for the probe; -k equivalent allowed
        # and recorded in the receipt (任务书红线记录该放宽).
        self._ssl_context = ssl._create_unverified_context()

    def _auth_header(self) -> str:
        password = self._password_file.read_text(encoding="ascii").strip()
        return "Basic YWRtaW46" + _b64(password)

    def _request(
        self,
        method: str,
        path: str,
        payload: Any = None,
    ) -> tuple[int, Any]:
        url = self.base_url + path
        body = None
        headers = {"Authorization": self._auth_header()}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, method=method)
        for key, value in headers.items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(
                request, timeout=self._timeout, context=self._ssl_context
            ) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
            raw = error.read()
        try:
            parsed: Any = json.loads(raw) if raw else {}
        except Exception:
            parsed = {"raw_bytes": len(raw)}
        return status, parsed

    def health(self) -> dict[str, Any]:
        status, data = self._request("GET", "/")
        return {"http_status": status, "data": data}

    def version(self) -> str | None:
        _, data = self._request("GET", "/")
        version = data.get("version", {}) if isinstance(data, dict) else {}
        return version.get("number")

    def create_index(self, index: str, body: dict[str, Any]) -> int:
        status, _ = self._request("PUT", f"/{index}", payload=body)
        return status

    def delete_index(self, index: str) -> int:
        status, _ = self._request("DELETE", f"/{index}")
        return status

    def index_exists(self, index: str) -> bool:
        status, _ = self._request("HEAD", f"/{index}")
        return status == 200

    def count(self, index: str) -> int:
        # Refresh first so the count reflects indexed (not translog) state.
        self._request("POST", f"/{index}/_refresh")
        status, data = self._request("POST", f"/{index}/_count", payload={})
        if status != 200 or not isinstance(data, dict):
            raise OpenSearchProbeError(f"COUNT_FAILED status={status}")
        return int(data["count"])

    def bulk(
        self, index: str, documents: list[dict[str, Any]]
    ) -> tuple[int, int, int]:
        """Stream documents into a single bulk request.

        ``documents`` is a list of {"_id": str, "fields": {...}}.
        Returns (status, successful_items, failed_items).
        """
        lines: list[str] = []
        for doc in documents:
            action = json.dumps(
                {"index": {"_index": index, "_id": doc["_id"]}},
                ensure_ascii=True,
            )
            source = json.dumps(doc["fields"], ensure_ascii=True)
            lines.append(action)
            lines.append(source)
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        url = self.base_url + "/_bulk"
        headers = {
            "Authorization": self._auth_header(),
            "Content-Type": "application/x-ndjson",
        }
        request = urllib.request.Request(url, data=payload, method="POST")
        for key, value in headers.items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(
                request, timeout=self._timeout, context=self._ssl_context
            ) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
            raw = error.read()
        data = json.loads(raw) if raw else {}
        items = data.get("items", []) if isinstance(data, dict) else []
        successful = 0
        errors = 0
        for item in items:
            result = item.get("index", {})
            if result.get("status") in (200, 201):
                successful += 1
            else:
                errors += 1
        return status, successful, errors

    def search(
        self,
        index: str,
        query: str,
        size: int = 5,
        filters: list[dict[str, Any]] | None = None,
        _source: bool = True,
    ) -> tuple[int, dict[str, Any]]:
        payload: dict[str, Any] = {
            "query": {"bool": {"must": [{"match": {"body": query}}]}},
            "size": size,
        }
        if filters:
            payload["query"]["bool"]["filter"] = filters
        if not _source:
            payload["_source"] = False
        self._request("POST", f"/{index}/_refresh")
        status, data = self._request("POST", f"/{index}/_search", payload=payload)
        return status, data

    def search_hits(self, index: str, query: str, size: int = 5) -> list[dict[str, Any]]:
        status, data = self.search(index, query, size=size, _source=False)
        if status != 200:
            raise OpenSearchProbeError(f"SEARCH_FAILED status={status}")
        hits = data.get("hits", {}).get("hits", [])
        return [{"id": h["_id"], "score": h.get("_score")} for h in hits]

    def filter_search(
        self,
        index: str,
        filters: list[dict[str, Any]],
        size: int = 1000,
    ) -> list[str]:
        payload: dict[str, Any] = {
            "query": {"bool": {"filter": filters}},
            "size": size,
            "_source": False,
        }
        self._request("POST", f"/{index}/_refresh")
        status, data = self._request("POST", f"/{index}/_search", payload=payload)
        if status != 200:
            raise OpenSearchProbeError(f"FILTER_FAILED status={status}")
        hits = data.get("hits", {}).get("hits", [])
        return [h["_id"] for h in hits]

    def delete_by_query(self, index: str, query: dict[str, Any]) -> int:
        payload = {"query": query}
        status, data = self._request(
            "POST", f"/{index}/_delete_by_query", payload=payload
        )
        if status != 200:
            raise OpenSearchProbeError(f"DELETE_BY_QUERY_FAILED status={status}")
        return int(data.get("deleted", 0))


def _b64(value: str) -> str:
    import base64

    return base64.b64encode(value.encode("ascii")).decode("ascii")


__all__ = ("OpenSearchClient", "OpenSearchProbeError")
