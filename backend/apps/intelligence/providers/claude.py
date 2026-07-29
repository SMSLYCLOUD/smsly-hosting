import os
from collections.abc import Generator

from .base import AIProvider, _get_client, _sanitize_api_key, _normalize_model, retry_429, logger


class ClaudeProvider(AIProvider):
    """Anthropic Claude provider — model configurable via CLAUDE_MODEL env var."""

    BASE_URL = "https://api.anthropic.com/v1"

    def __init__(self):
        self.api_key = _sanitize_api_key(os.environ.get("CLAUDE_API_KEY", ""))
        self.model = _normalize_model(
            os.environ.get("CLAUDE_MODEL"),
            "claude-sonnet-4-20250514",
        )

    def name(self) -> str:
        return f"Claude ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: str | None = None) -> str:
        if not self.api_key:
            raise ValueError("[Claude] API key not configured.")

        messages = [{"role": "user", "content": prompt}]

        payload = {
            "model": self.model,
            "max_tokens": 2048,
            "messages": messages,
        }
        if system_prompt:
            payload["system"] = system_prompt

        try:
            client = _get_client("claude", timeout=60)
            resp = client.post(
                f"{self.BASE_URL}/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return "".join(
                block.get("text", "") for block in data.get("content", [])
                if block.get("type") == "text"
            )
        except Exception as e:
            logger.error("Claude ask failed: %s", e)
            raise

    def ask_stream(self, prompt: str, system_prompt: str | None = None) -> Generator[str, None, None]:
        """Fallback: yield full response as single chunk."""
        response = self.ask(prompt, system_prompt)
        yield response

    def get_balance(self) -> dict:
        """Check Claude/Anthropic balance. No official balance API yet."""
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        try:
            client = _get_client("claude", timeout=15)
            resp = client.post(
                f"{self.BASE_URL}/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            if resp.status_code == 200:
                return {
                    "balance": "Active (check console.anthropic.com)",
                    "currency": "USD",
                    "raw": {}
                }
            if resp.status_code == 401:
                return {"balance": "\u274c Invalid API key", "currency": "", "raw": {}}
            if resp.status_code == 429:
                return {
                    "balance": "\u26a0\ufe0f Rate limited / credits low",
                    "currency": "USD",
                    "raw": {}
                }
            return {"balance": f"Status {resp.status_code}", "currency": "", "raw": {}}
        except Exception as e:
            logger.debug("Claude balance check failed: %s", e)
            return {"balance": "Error checking", "currency": "", "raw": {}}
