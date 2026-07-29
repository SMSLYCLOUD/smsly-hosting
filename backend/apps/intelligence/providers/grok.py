import json as _json
import os
from collections.abc import Generator

from .base import AIProvider, _get_client, _looks_like_model_error, _normalize_model, _sanitize_api_key, retry_429, logger


class GrokProvider(AIProvider):
    """xAI Grok provider — model configurable via GROK_MODEL env var."""

    BASE_URL = "https://api.x.ai/v1"

    def __init__(self):
        self.api_key = _sanitize_api_key(os.environ.get("GROK_API_KEY", ""))
        self.model = _normalize_model(os.environ.get("GROK_MODEL"), "grok-3-mini")

    def name(self) -> str:
        return f"Grok ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: str | None = None) -> str:
        if not self.api_key:
            raise ValueError("[Grok] API key not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        candidate_models: list[str] = []
        for candidate in [self.model, "grok-3-mini", "grok-3"]:
            if candidate and candidate not in candidate_models:
                candidate_models.append(candidate)

        last_error: Exception | None = None
        client = _get_client("grok", timeout=60)
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
                        "Grok model %s failed, trying fallback model",
                        candidate_model,
                    )
                    continue
                break

        logger.error("Grok ask failed: %s", last_error)
        raise last_error or RuntimeError("Grok request failed")

    def ask_stream(self, prompt: str, system_prompt: str | None = None) -> Generator[str, None, None]:
        """Yield response chunks as they arrive (SSE streaming)."""
        if not self.api_key:
            raise ValueError("[Grok] API key not configured.")

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

        client = _get_client("grok", timeout=120)
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
        """Fetch xAI/Grok credit balance."""
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        try:
            client = _get_client("grok", timeout=15)
            resp = client.get(
                f"{self.BASE_URL}/api-key",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                remaining = data.get("remaining_balance", None)
                if remaining is not None:
                    return {
                        "balance": f"${remaining:.2f}",
                        "currency": "USD",
                        "raw": data,
                    }
                return {"balance": "Active", "currency": "USD", "raw": data}
            return {
                "balance": "Active (check console.x.ai)",
                "currency": "USD",
                "raw": {}
            }
        except Exception as e:
            logger.debug("Grok balance check failed: %s", e)
            return {"balance": "Error checking", "currency": "", "raw": {}}
