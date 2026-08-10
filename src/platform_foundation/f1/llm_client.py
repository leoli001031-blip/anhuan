"""F1 DeepSeek client using only the F1 provider-secret boundary."""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request

from .secret_files import SecretFileError, read_provider_secret_text

BASE_URL = "https://api.deepseek.com/v1"
MODEL = "deepseek-v4-pro"


class F1LlmError(RuntimeError):
    pass


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - platform CA fallback contains no secret
        return ssl.create_default_context()


class F1DeepSeekClient:
    def __init__(self, *, timeout: float = 120.0) -> None:
        self._timeout = timeout

    def _api_key(self) -> str:
        try:
            return read_provider_secret_text(
                "deepseek_api_key", file_env="F1_DEEPSEEK_API_KEY_FILE"
            )
        except SecretFileError:
            raise F1LlmError("LLM_KEY_UNAVAILABLE") from None

    def _chat(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int,
    ) -> tuple[int, object]:
        request = urllib.request.Request(
            f"{BASE_URL}/chat/completions",
            data=json.dumps(
                {
                    "model": MODEL,
                    "messages": messages,
                    "temperature": 0.0,
                    "max_tokens": max_tokens,
                },
                ensure_ascii=True,
            ).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key()}",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self._timeout, context=_ssl_context()
            ) as response:
                status = response.status
                raw = response.read()
        except urllib.error.HTTPError as error:
            status = error.code
            raw = error.read()
        except (OSError, urllib.error.URLError):
            raise F1LlmError("LLM_UNAVAILABLE") from None
        try:
            return status, json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            return status, {}

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 8192,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        for _attempt in range(4):
            status, payload = self._chat(messages, max_tokens=max_tokens)
            if status != 200 or not isinstance(payload, dict):
                raise F1LlmError("LLM_UNAVAILABLE")
            try:
                value = payload["choices"][0]["message"]["content"]
                content = str(value).strip()
            except (KeyError, IndexError, TypeError):
                raise F1LlmError("LLM_RESPONSE_INVALID") from None
            if content:
                return content
        raise F1LlmError("LLM_RESPONSE_EMPTY")


__all__ = ("F1DeepSeekClient", "F1LlmError")
