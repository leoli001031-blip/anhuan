"""Minimal RAGFlow v0.26.4 HTTP probe client (stdlib only).

Talks to the loopback RAGFlow service over its v1 HTTP API.  Uses only
``urllib.request`` — no SDK, no external dependencies.  Emits only counts,
booleans and reason codes; never credentials or content.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class RagFlowProbeError(RuntimeError):
    pass


class RagFlowClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:9380",
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_url = f"{self.base_url}/api/v1"
        self._timeout = timeout

    def _post(self, path: str, payload: dict[str, Any]) -> tuple[int, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.api_url + path,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
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

    def _get(self, path: str, token: str) -> tuple[int, Any]:
        request = urllib.request.Request(
            self.api_url + path,
            method="GET",
            headers={"Authorization": f"Bearer {token}"},
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

    def register_user(self, nickname: str, email: str, password: str) -> tuple[int, Any]:
        return self._post(
            "/users", {"nickname": nickname, "email": email, "password": password}
        )

    def login(self, email: str, password: str) -> tuple[int, Any]:
        return self._post("/auth/login", {"email": email, "password": password})

    def create_dataset(
        self, token: str, name: str, chunk_method: str = "naive"
    ) -> tuple[int, Any]:
        payload = {"name": name, "chunk_method": chunk_method}
        return self._post("/datasets", payload) if False else self._post_authed(
            "/datasets", token, payload
        )

    def _post_authed(
        self, path: str, token: str, payload: dict[str, Any]
    ) -> tuple[int, Any]:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.api_url + path,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
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


__all__ = ("RagFlowClient", "RagFlowProbeError")
