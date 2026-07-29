import json as _json
import os
from collections.abc import Generator

from .base import AIProvider, _get_client, _sanitize_api_key, _normalize_model, retry_429, logger


class DeepSeekProvider(AIProvider):
    """DeepSeek provider — model configurable via DEEPSEEK_MODEL env var."""

    BASE_URL = "https://api.deepseek.com/v1"

    def __init__(self):
        self.api_key = _sanitize_api_key(os.environ.get("DEEPSEEK_API_KEY", ""))
        self.model = _normalize_model(os.environ.get("DEEPSEEK_MODEL"), "deepseek-coder")

    def name(self) -> str:
        return f"DeepSeek ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: str | None = None) -> str:
        if not self.api_key:
            raise ValueError("[DeepSeek] API key not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            client = _get_client("deepseek", timeout=60)
            response = client.post(
                f"{self.BASE_URL}/chat/completions",
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
            logger.error("DeepSeek ask failed: %s", exc)
            raise

    def ask_stream(self, prompt: str, system_prompt: str | None = None) -> Generator[str, None, None]:
        """Yield response chunks as they arrive (SSE streaming)."""
        if not self.api_key:
            raise ValueError("[DeepSeek] API key not configured.")

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

        client = _get_client("deepseek", timeout=120)
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
        """Fetch DeepSeek balance from their user API."""
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        try:
            client = _get_client("deepseek", timeout=15)
            resp = client.get(
                "https://api.deepseek.com/user/balance",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                balance_info = data.get("balance_infos", [])
                if balance_info:
                    total = balance_info[0].get("total_balance", "0")
                    currency = balance_info[0].get("currency", "CNY")
                    return {
                        "balance": f"{total} {currency}",
                        "currency": currency,
                        "raw": data,
                    }
            return {"balance": "Active", "currency": "", "raw": {}}
        except Exception as e:
            logger.debug("DeepSeek balance check failed: %s", e)
            return {"balance": "Error checking", "currency": "", "raw": {}}
