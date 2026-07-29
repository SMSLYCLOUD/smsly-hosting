import os
from collections.abc import Generator

from .base import AIProvider, _get_client, _sanitize_api_key, _normalize_model, retry_429, logger


class CloudflareProvider(AIProvider):
    """
    Cloudflare Workers AI provider — accessed via AI Gateway.

    Uses an OpenAI-compatible `/chat/completions` endpoint through
    Cloudflare AI Gateway. Requires a Cloudflare account and AI Gateway setup.
    Base URL format: https://gateway.ai.cloudflare.com/v1/{account_id}/{gateway}/workers-ai
    """

    def __init__(self, model_override: str | None = None):
        self.api_key = _sanitize_api_key(os.environ.get("CLOUDFLARE_API_KEY", ""))
        default_model = os.environ.get("CLOUDFLARE_MODEL", "@cf/meta/llama-3.1-8b-instruct")
        self.model = model_override or _normalize_model(default_model, "@cf/meta/llama-3.1-8b-instruct")
        self.base_url = os.environ.get(
            "CLOUDFLARE_BASE_URL",
            "https://gateway.ai.cloudflare.com/v1/YOUR_ACCOUNT_ID/default/workers-ai",
        ).rstrip("/")

    def name(self) -> str:
        return f"Cloudflare AI ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: str | None = None) -> str:
        if not self.api_key:
            raise ValueError("[Cloudflare] API key not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            client = _get_client("cloudflare", timeout=60)
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
            logger.error("Cloudflare ask failed: %s", exc)
            raise

    def ask_stream(self, prompt: str, system_prompt: str | None = None) -> Generator[str, None, None]:
        """Fallback: yield full response as single chunk."""
        response = self.ask(prompt, system_prompt)
        yield response

    def get_balance(self) -> dict:
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        return {"balance": "Active (Free Tier)", "currency": "N/A", "raw": {}}
