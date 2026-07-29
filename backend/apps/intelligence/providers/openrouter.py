import json as _json
import os
from collections.abc import Generator

from .base import AIProvider, _get_client, _sanitize_api_key, _normalize_model, retry_429, logger


class OpenRouterProvider(AIProvider):
    """OpenRouter provider — gateway to 100+ models."""

    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self):
        self.api_key = _sanitize_api_key(os.environ.get("OPENROUTER_API_KEY", ""))
        self.model = _normalize_model(os.environ.get("OPENROUTER_MODEL"), "openrouter/auto")

    def name(self) -> str:
        return f"OpenRouter ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: str | None = None) -> str:
        if not self.api_key:
            raise ValueError("[OpenRouter] API key not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            client = _get_client("openrouter", timeout=60)
            response = client.post(
                f"{self.BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://smsly.hosting",
                    "X-Title": "SMSLY Intelligence Senate",
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
            logger.error("OpenRouter ask failed: %s", exc)
            raise

    def ask_stream(self, prompt: str, system_prompt: str | None = None) -> Generator[str, None, None]:
        """Yield response chunks as they arrive (SSE streaming)."""
        if not self.api_key:
            raise ValueError("[OpenRouter] API key not configured.")

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

        client = _get_client("openrouter", timeout=120)
        with client.stream(
            "POST",
            f"{self.BASE_URL}/chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://smsly.hosting",
                "X-Title": "SMSLY Intelligence Senate",
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

    def get_balance(self) -> dict:
        """Fetch OpenRouter balance."""
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        try:
            client = _get_client("openrouter", timeout=15)
            resp = client.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                limit = data.get("limit", 0)
                usage = data.get("usage", 0)
                remaining = limit - usage if limit else "Unlimited"
                return {
                    "balance": f"${remaining}" if isinstance(remaining, (int, float)) else remaining,
                    "currency": "USD",
                    "raw": data,
                }
            return {"balance": "Active", "currency": "USD", "raw": {}}
        except Exception:
            return {"balance": "Error checking", "currency": "", "raw": {}}
