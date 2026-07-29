import json as _json
import os
from collections.abc import Generator

from .base import AIProvider, _get_client, _sanitize_api_key, _normalize_model, retry_429, logger


class LocalLLMProvider(AIProvider):
    """
    Local LLM provider for services like Ollama, LocalAI, vLLM, etc.
    Expects an OpenAI-compatible /v1/chat/completions endpoint.
    """

    def __init__(self, model_override: str | None = None):
        self.api_key = _sanitize_api_key(os.environ.get("LOCALLM_API_KEY", ""))
        default_model = os.environ.get("LOCALLM_MODEL", "local-model")
        self.model = model_override or _normalize_model(default_model, "local-model")
        self.base_url = os.environ.get(
            "LOCALLM_BASE_URL",
            "http://localhost:11434/v1",
        ).rstrip("/")

    def name(self) -> str:
        return f"Local LLM ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: str | None = None) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            client = _get_client("locallm", timeout=120)
            response = client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
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
            logger.error("Local LLM ask failed at %s: %s", self.base_url, exc)
            raise

    def ask_stream(self, prompt: str, system_prompt: str | None = None) -> Generator[str, None, None]:
        """Yield response chunks as they arrive (SSE streaming)."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 2048,
            "stream": True,
        }

        client = _get_client("locallm", timeout=120)
        with client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=headers,
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

    def is_configured(self) -> bool:
        return bool(self.base_url)

    def get_balance(self) -> dict:
        return {"balance": "Local (Free)", "currency": "N/A", "raw": {}}
