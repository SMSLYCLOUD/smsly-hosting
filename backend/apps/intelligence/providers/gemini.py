import os
from collections.abc import Generator

from .base import AIProvider, _get_client, _looks_like_model_error, _normalize_model, _sanitize_api_key, retry_429, logger


class GeminiProvider(AIProvider):
    """Google Gemini provider — model configurable via GEMINI_MODEL env var."""

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self):
        self.api_key = _sanitize_api_key(os.environ.get("GEMINI_API_KEY", ""))
        self.model = _normalize_model(os.environ.get("GEMINI_MODEL"), "gemini-2.0-flash")

    def name(self) -> str:
        return f"Gemini ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: str | None = None) -> str:
        if not self.api_key:
            raise ValueError("[Gemini] API key not configured.")

        contents = []
        if system_prompt:
            contents.append({"role": "user", "parts": [{"text": system_prompt}]})
            contents.append({"role": "model", "parts": [{"text": "Understood."}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        candidate_models: list[str] = []
        for candidate in [self.model, "gemini-2.0-flash", "gemini-1.5-flash"]:
            normalized = str(candidate or "").strip().replace("models/", "")
            if normalized and normalized not in candidate_models:
                candidate_models.append(normalized)

        last_error: Exception | None = None
        client = _get_client("gemini", timeout=60)
        for candidate_model in candidate_models:
            try:
                url = f"{self.BASE_URL}/models/{candidate_model}:generateContent?key={self.api_key}"
                resp = client.post(
                    url,
                    headers={"Content-Type": "application/json"},
                    json={"contents": contents},
                )
                resp.raise_for_status()
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except Exception as exc:
                last_error = exc
                if _looks_like_model_error(exc):
                    logger.warning(
                        "Gemini model %s failed, trying fallback model",
                        candidate_model,
                    )
                    continue
                break

        logger.error("Gemini ask failed: %s", last_error)
        raise last_error or RuntimeError("Gemini request failed")

    def ask_stream(self, prompt: str, system_prompt: str | None = None) -> Generator[str, None, None]:
        """Fallback: yield full response as single chunk."""
        response = self.ask(prompt, system_prompt)
        yield response

    def get_balance(self) -> dict:
        """Check Gemini API quota status. Gemini uses quota-based billing, not credits."""
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        try:
            client = _get_client("gemini", timeout=15)
            resp = client.get(
                f"{self.BASE_URL}/models?key={self.api_key}",
            )
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                model_count = len(models)
                return {
                    "balance": f"Active ({model_count} models available)",
                    "currency": "quota-based",
                    "raw": {"available_models": model_count, "tier": "free/pay-as-you-go"},
                }
            if resp.status_code == 429:
                return {"balance": "\u26a0\ufe0f Quota exhausted", "currency": "quota-based", "raw": {}}
            return {
                "balance": "Unknown (check aistudio.google.com)",
                "currency": "",
                "raw": {}
            }
        except Exception as e:
            logger.debug("Gemini balance check failed: %s", e)
            return {"balance": "Error checking", "currency": "", "raw": {}}
