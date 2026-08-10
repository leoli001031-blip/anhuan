"""F0-J1 DeepSeek LLM client (stdlib only).

OpenAI-compatible chat completions against the DeepSeek official API.
The API key is read from the probe secrets file at call time; it is never
logged or echoed.  The default model is ``deepseek-v4-flash`` per the
taskbook (leader may change before start).
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
KEY_FILE = "/private/tmp/anhuan-f0j1-secrets/deepseek_api_key"


def _ssl_context() -> ssl.SSLContext:
    """SSL context with the certifi CA bundle.

    The host Python's default CA path is missing on this machine, so the
    public DeepSeek endpoint fails TLS verification; certifi ships a usable
    bundle in the project venv.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


class LlmProbeError(RuntimeError):
    pass


class DeepSeekClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        key_file: str = KEY_FILE,
        model: str = DEFAULT_MODEL,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._key_file = Path(key_file)
        self.model = model
        self._timeout = timeout

    def _chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 8192,
    ) -> tuple[int, Any]:
        api_key = self._key_file.read_text(encoding="ascii").strip()
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self._timeout, context=_ssl_context()
            ) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
            raw = error.read()
        try:
            return status, json.loads(raw) if raw else {}
        except Exception:
            return status, {"raw_bytes": len(raw)}

    def complete(self, prompt: str, system: str | None = None, max_tokens: int = 8192) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        # Retry empty/absent content: the model intermittently returns an
        # empty or reasoning-only response on long prompts.
        for attempt in range(4):
            status, data = self._chat(messages, max_tokens=max_tokens)
            if status != 200:
                raise LlmProbeError(f"LLM_FAILED status={status}")
            try:
                content = str(data["choices"][0]["message"]["content"]).strip()
            except Exception:
                raise LlmProbeError("LLM_RESPONSE_INVALID") from None
            if content:
                return content
        raise LlmProbeError("LLM_RESPONSE_EMPTY")


__all__ = ("DeepSeekClient", "LlmProbeError", "DEFAULT_MODEL")
