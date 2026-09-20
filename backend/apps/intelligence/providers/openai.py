import json as _json
import os
from collections.abc import Generator

from .base import AIProvider, _get_client, _looks_like_model_error, _normalize_model, _sanitize_api_key, retry_429, logger


class OpenAIProvider(AIProvider):
    """OpenAI GPT provider — model configurable via OPENAI_MODEL env var."""

    BASE_URL = "https://api.openai.com/v1"

    def __init__(self):
        self.api_key = _sanitize_api_key(os.environ.get("OPENAI_API_KEY", ""))
        self.model = _normalize_model(os.environ.get("OPENAI_MODEL"), "gpt-4o-mini")

    def name(self) -> str:
        return f"OpenAI ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: str | None = None) -> str:
        if not self.api_key:
            raise ValueError("[OpenAI] API key not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        candidate_models: list[str] = []
        for candidate in [self.model, "gpt-4o-mini", "gpt-4o"]:
            if candidate and candidate not in candidate_models:
                candidate_models.append(candidate)

        last_error: Exception | None = None
        client = _get_client("openai", timeout=60)
        for candidate_model in candidate_models:
            try:
                resp = client.post(
                    f"{self.BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": candidate_model,
                        "messages": messages,
                        "max_tokens": 2048
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except Exception as exc:
                last_error = exc
                if _looks_like_model_error(exc):
                    logger.warning(
                        "OpenAI model %s failed, trying fallback model",
                        candidate_model,
                    )
                    continue
                break

        logger.error("OpenAI ask failed: %s", last_error)
        raise last_error or RuntimeError("OpenAI request failed")

    def ask_stream(self, prompt: str, system_prompt: str | None = None) -> Generator[str, None, None]:
        """Yield response chunks as they arrive (SSE streaming)."""
        if not self.api_key:
            raise ValueError("[OpenAI] API key not configured.")

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

        client = _get_client("openai", timeout=120)
        with client.stream(
            "POST",
            f"{self.BASE_URL}/chat/completions",
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
        """Probe OpenAI API availability.

        OpenAI has no key-scoped balance endpoint (the legacy
        ``dashboard/billing/credit_grants`` feed is retired and
        ``organization/costs`` requires an admin key), so a ``/v1/models``
        probe is the honest signal: 200 means the key works.
        """
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        try:
            client = _get_client("openai", timeout=15)
            resp = client.get(
                f"{self.BASE_URL}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            if resp.status_code == 200:
                try:
                    model_count = len(resp.json().get("data", []))
                except Exception:
                    model_count = 0
                return {
                    "balance": f"Active ({model_count} models available)" if model_count else "Active",
                    "currency": "USD",
                    "raw": {},
                }
            if resp.status_code == 401:
                return {"balance": "Invalid API key", "currency": "", "raw": {}}
            if resp.status_code == 429:
                return {"balance": "Rate limited", "currency": "USD", "raw": {}}
            return {
                "balance": "Active (check platform.openai.com)",
                "currency": "USD",
                "raw": {}
            }
        except Exception as e:
            logger.debug("OpenAI balance check failed: %s", e)
            return {"balance": "Error checking", "currency": "", "raw": {}}
