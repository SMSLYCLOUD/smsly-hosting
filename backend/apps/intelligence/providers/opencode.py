import json as _json
import os
from collections.abc import Generator

from .base import AIProvider, _get_client, _sanitize_api_key, _normalize_model, retry_429, logger


class OpenCodeProvider(AIProvider):
    """
    OpenCode AI provider — configurable gateway.

    Uses an OpenAI-compatible `/chat/completions` endpoint.
    """

    def __init__(self, model_override: str | None = None):
        self.api_key = _sanitize_api_key(os.environ.get("OPENCODE_API_KEY", ""))
        default_model = os.environ.get("OPENCODE_MODEL", "opencode-latest")
        self.model = model_override or _normalize_model(default_model, "opencode-latest")
        self.base_url = os.environ.get(
            "OPENCODE_BASE_URL",
            "https://api.opencode.ai/v1",
        ).rstrip("/")

    def name(self) -> str:
        return f"OpenCode ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: str | None = None) -> str:
        if not self.api_key:
            raise ValueError("[OpenCode] API key not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            client = _get_client("opencode", timeout=60)
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
            payload = response.json()
            return payload["choices"][0]["message"]["content"]
        except Exception as exc:
            logger.error("OpenCode ask failed: %s", exc)
            raise

    def ask_stream(self, prompt: str, system_prompt: str | None = None) -> Generator[str, None, None]:
        """Yield response chunks as they arrive (SSE streaming)."""
        if not self.api_key:
            raise ValueError("[OpenCode] API key not configured.")

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

        client = _get_client("opencode", timeout=120)
        with client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
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

    def get_balance(self) -> dict:
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        return {"balance": "Active (OpenCode)", "currency": "N/A", "raw": {}}
