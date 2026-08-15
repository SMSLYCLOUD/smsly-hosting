import json as _json
import os
from collections.abc import Generator

from .base import (
    AIProvider,
    _get_client,
    _normalize_model,
    _sanitize_api_key,
    logger,
    retry_429,
)


class OpenAICompatibleProvider(AIProvider):
    """Base for OpenAI-compatible ``/chat/completions`` providers.

    Subclasses set ``ENV_PREFIX``, ``DEFAULT_MODEL``, ``DEFAULT_BASE_URL``,
    and ``LABEL``. Environment variables follow ``<PREFIX>_API_KEY``,
    ``<PREFIX>_MODEL``, and ``<PREFIX>_BASE_URL``.
    """

    ENV_PREFIX = ""
    DEFAULT_MODEL = ""
    DEFAULT_BASE_URL = ""
    LABEL = ""

    def __init__(self, model_override: str | None = None):
        self.api_key = _sanitize_api_key(
            os.environ.get(f"{self.ENV_PREFIX}_API_KEY", "")
        )
        self.model = model_override or _normalize_model(
            os.environ.get(f"{self.ENV_PREFIX}_MODEL", ""),
            self.DEFAULT_MODEL,
        )
        self.base_url = (
            os.environ.get(f"{self.ENV_PREFIX}_BASE_URL", "") or self.DEFAULT_BASE_URL
        ).rstrip("/")

    def _client_key(self) -> str:
        return self.ENV_PREFIX.lower()

    def name(self) -> str:
        return f"{self.LABEL} ({self.model})"

    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url)

    @retry_429()
    def ask(self, prompt: str, system_prompt: str | None = None) -> str:
        if not self.api_key:
            raise ValueError(f"[{self.LABEL}] API key not configured.")
        if not self.base_url:
            raise ValueError(f"[{self.LABEL}] base URL not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            client = _get_client(self._client_key(), timeout=60)
            response = client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": 2048,
                },
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            logger.error("%s ask failed: %s", self.LABEL, exc)
            raise

    def ask_stream(self, prompt: str, system_prompt: str | None = None) -> Generator[str, None, None]:
        """Yield response chunks as they arrive (SSE streaming)."""
        if not self.api_key:
            raise ValueError(f"[{self.LABEL}] API key not configured.")
        if not self.base_url:
            raise ValueError(f"[{self.LABEL}] base URL not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 2048,
            "stream": True,
        }

        client = _get_client(self._client_key(), timeout=120)
        with client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = _json.loads(data_str)
                        delta = data.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except _json.JSONDecodeError:
                        continue
