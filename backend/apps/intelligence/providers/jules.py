import os
from collections.abc import Generator

from .base import AIProvider, _get_client, _sanitize_api_key, _normalize_model, retry_429, logger


class JulesProvider(AIProvider):
    """
    Jules provider.

    Uses an OpenAI-compatible `/chat/completions` API endpoint so it can run
    against managed Jules-compatible gateways without custom SDK coupling.
    """

    def __init__(self):
        self.api_key = _sanitize_api_key(os.environ.get("JULES_API_KEY", ""))
        self.model = _normalize_model(os.environ.get("JULES_MODEL"), "jules-latest")
        self.base_url = os.environ.get(
            "JULES_BASE_URL",
            "https://api.jules.google.com/v1",
        ).rstrip("/")

    def name(self) -> str:
        return f"Jules ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: str | None = None) -> str:
        if not self.api_key:
            raise ValueError("[Jules] API key not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            client = _get_client("jules", timeout=60)
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
            logger.error("Jules ask failed: %s", exc)
            raise

    def ask_stream(self, prompt: str, system_prompt: str | None = None) -> Generator[str, None, None]:
        """Fallback: yield full response as single chunk."""
        response = self.ask(prompt, system_prompt)
        yield response

    def get_balance(self) -> dict:
        """Jules balance API is provider-specific; surface configuration state."""
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        return {
            "balance": "Active (check Jules billing console)",
            "currency": "USD",
            "raw": {},
        }
