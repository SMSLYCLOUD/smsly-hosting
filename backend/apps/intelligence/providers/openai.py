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
        """Fetch OpenAI credit balance."""
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        try:
            client = _get_client("openai", timeout=15)
            resp = client.get(
                "https://api.openai.com/dashboard/billing/credit_grants",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                total = data.get("total_granted", 0)
                used = data.get("total_used", 0)
                remaining = data.get("total_available", total - used)
                return {
                    "balance": f"${remaining:.2f}",
                    "currency": "USD",
                    "raw": {
                        "total_granted": total,
                        "total_used": used,
                        "remaining": remaining
                    },
                }
            resp2 = client.get(
                "https://api.openai.com/v1/organization/costs",
                headers={"Authorization": f"Bearer {self.api_key}"},
                params={"limit": 1},
            )
            if resp2.status_code == 200:
                return {
                    "balance": "Active (usage-based)",
                    "currency": "USD",
                    "raw": resp2.json()
                }
            return {
                "balance": "Active (check platform.openai.com)",
                "currency": "USD",
                "raw": {}
            }
        except Exception as e:
            logger.debug("OpenAI balance check failed: %s", e)
            return {"balance": "Error checking", "currency": "", "raw": {}}
