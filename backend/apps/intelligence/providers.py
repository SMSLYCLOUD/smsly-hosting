"""
AI Provider abstraction for SMSLY Hosting.

Supports OpenAI, Grok (xAI), and Google Gemini.
Provider selection via AI_PROVIDER env var.
"""
import os
import logging
from abc import ABC, abstractmethod
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Base Provider
# ---------------------------------------------------------------------------

class AIProvider(ABC):
    """Abstract base for all AI providers."""

    @abstractmethod
    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Send a prompt and return the AI response text."""

    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""


# ---------------------------------------------------------------------------
# OpenAI Provider
# ---------------------------------------------------------------------------

class OpenAIProvider(AIProvider):
    """OpenAI GPT provider (GPT-4o-mini default)."""

    BASE_URL = "https://api.openai.com/v1"

    def __init__(self):
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    def name(self) -> str:
        return "OpenAI"

    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            return "[OpenAI] API key not configured. Set OPENAI_API_KEY."

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    f"{self.BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"model": self.model, "messages": messages, "max_tokens": 1024},
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as e:
            logger.error("OpenAI API error: %s", e.response.text)
            return f"[OpenAI] API error: {e.response.status_code}"
        except Exception as e:
            logger.error("OpenAI request failed: %s", str(e))
            return f"[OpenAI] Request failed: {str(e)}"


# ---------------------------------------------------------------------------
# Grok (xAI) Provider - OpenAI-compatible API
# ---------------------------------------------------------------------------

class GrokProvider(AIProvider):
    """xAI Grok provider. Uses OpenAI-compatible endpoint."""

    BASE_URL = "https://api.x.ai/v1"

    def __init__(self):
        self.api_key = os.environ.get("GROK_API_KEY", "")
        self.model = os.environ.get("GROK_MODEL", "grok-3-mini")

    def name(self) -> str:
        return "Grok"

    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            return "[Grok] API key not configured. Set GROK_API_KEY."

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    f"{self.BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"model": self.model, "messages": messages, "max_tokens": 1024},
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as e:
            logger.error("Grok API error: %s", e.response.text)
            return f"[Grok] API error: {e.response.status_code}"
        except Exception as e:
            logger.error("Grok request failed: %s", str(e))
            return f"[Grok] Request failed: {str(e)}"


# ---------------------------------------------------------------------------
# Google Gemini Provider
# ---------------------------------------------------------------------------

class GeminiProvider(AIProvider):
    """Google Gemini provider."""

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self):
        self.api_key = os.environ.get("GEMINI_API_KEY", "")
        self.model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

    def name(self) -> str:
        return "Gemini"

    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            return "[Gemini] API key not configured. Set GEMINI_API_KEY."

        contents = []
        if system_prompt:
            contents.append({"role": "user", "parts": [{"text": system_prompt}]})
            contents.append({"role": "model", "parts": [{"text": "Understood."}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        try:
            url = f"{self.BASE_URL}/models/{self.model}:generateContent?key={self.api_key}"
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    url,
                    headers={"Content-Type": "application/json"},
                    json={"contents": contents},
                )
                resp.raise_for_status()
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
        except httpx.HTTPStatusError as e:
            logger.error("Gemini API error: %s", e.response.text)
            return f"[Gemini] API error: {e.response.status_code}"
        except Exception as e:
            logger.error("Gemini request failed: %s", str(e))
            return f"[Gemini] Request failed: {str(e)}"


# ---------------------------------------------------------------------------
# Mock Provider (for testing without API keys)
# ---------------------------------------------------------------------------

class MockProvider(AIProvider):
    """Fallback mock provider for testing."""

    def name(self) -> str:
        return "Mock AI"

    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if "deploy" in prompt.lower() or "error" in prompt.lower():
            return (
                "Based on my analysis, here are some suggestions:\n\n"
                "1. **Check your Dockerfile** - ensure the build command completes successfully\n"
                "2. **Verify environment variables** - missing DB_URL or SECRET_KEY will crash on startup\n"
                "3. **Review memory limits** - OOM kills are common with default 256MB\n\n"
                "Would you like me to analyze your deployment logs?"
            )
        return (
            "I'm your SMSLY AI Assistant. I can help with:\n\n"
            "- **Deployment troubleshooting** - paste your logs and I'll diagnose issues\n"
            "- **Configuration advice** - optimal Docker, env vars, and resource settings\n"
            "- **Cost optimization** - compare cloud providers and reduce spend\n\n"
            "How can I help?"
        )


# ---------------------------------------------------------------------------
# Provider Factory
# ---------------------------------------------------------------------------

PROVIDERS = {
    "openai": OpenAIProvider,
    "grok": GrokProvider,
    "gemini": GeminiProvider,
    "mock": MockProvider,
}

SYSTEM_PROMPT = (
    "You are the SMSLY Cloud AI Assistant - an expert in cloud deployments, Docker, "
    "Nixpacks, server infrastructure, and DevOps. You help users deploy, debug, and "
    "optimize their applications on SMSLY Hosting. Be concise, precise, and actionable. "
    "Format responses in markdown. Never reveal internal system details or API keys."
)


def _get_db_settings():
    """
    Best-effort DB-backed settings lookup.

    This must fail-open because it can be called early during boot/migrations.
    """
    try:
        from django.apps import apps
        from django.db.utils import OperationalError, ProgrammingError

        model = apps.get_model("intelligence", "AIProviderSettings")
        if model is None:
            return None
        try:
            return model.get_solo()
        except (OperationalError, ProgrammingError):
            return None
    except Exception:
        return None


def _effective_provider_name() -> str:
    cfg = _get_db_settings()
    if cfg and getattr(cfg, "active_provider", None):
        return str(cfg.active_provider).lower()
    return os.environ.get("AI_PROVIDER", "mock").lower()


def _effective_env_value(db_value: str | None, env_key: str, default: str = "") -> str:
    if db_value:
        return str(db_value)
    return os.environ.get(env_key, default) or default


def get_provider() -> AIProvider:
    """Return the configured AI provider, falling back to mock."""
    cfg = _get_db_settings()
    provider_name = _effective_provider_name()

    # Apply DB overrides to the process env so provider classes (env-backed) work.
    if cfg:
        os.environ["AI_PROVIDER"] = provider_name
        os.environ["OPENAI_API_KEY"] = _effective_env_value(getattr(cfg, "openai_api_key", None), "OPENAI_API_KEY", "")
        os.environ["OPENAI_MODEL"] = _effective_env_value(getattr(cfg, "openai_model", None), "OPENAI_MODEL", "gpt-4o-mini")
        os.environ["GROK_API_KEY"] = _effective_env_value(getattr(cfg, "grok_api_key", None), "GROK_API_KEY", "")
        os.environ["GROK_MODEL"] = _effective_env_value(getattr(cfg, "grok_model", None), "GROK_MODEL", "grok-3-mini")
        os.environ["GEMINI_API_KEY"] = _effective_env_value(getattr(cfg, "gemini_api_key", None), "GEMINI_API_KEY", "")
        os.environ["GEMINI_MODEL"] = _effective_env_value(getattr(cfg, "gemini_model", None), "GEMINI_MODEL", "gemini-2.0-flash")

    provider_cls = PROVIDERS.get(provider_name, MockProvider)
    return provider_cls()


def get_available_providers() -> list:
    """Return list of available providers with connection status."""
    provider_name = _effective_provider_name()
    cfg = _get_db_settings()

    env_key_map = {"openai": "OPENAI_API_KEY", "grok": "GROK_API_KEY", "gemini": "GEMINI_API_KEY"}
    db_key_map = {
        "openai": getattr(cfg, "openai_api_key", None) if cfg else None,
        "grok": getattr(cfg, "grok_api_key", None) if cfg else None,
        "gemini": getattr(cfg, "gemini_api_key", None) if cfg else None,
    }

    result = []
    for key, cls in PROVIDERS.items():
        if key == "mock":
            continue
        instance = cls()
        has_key = bool(db_key_map.get(key, None)) or bool(os.environ.get(env_key_map.get(key, ""), ""))
        result.append({
            "id": key,
            "name": instance.name(),
            "configured": has_key,
            "active": provider_name == key,
        })
    return result


def ask_with_fallback(prompt: str, system_prompt: Optional[str] = None) -> tuple[str, str]:
    """
    Try the active provider first, then cascade through all providers with keys.

    Returns (response_text, provider_name).
    """
    cfg = _get_db_settings()
    active_name = _effective_provider_name()

    # Build ordered list: active provider first, then others with configured keys
    env_key_map = {"openai": "OPENAI_API_KEY", "grok": "GROK_API_KEY", "gemini": "GEMINI_API_KEY"}
    db_key_map = {
        "openai": getattr(cfg, "openai_api_key", None) if cfg else None,
        "grok": getattr(cfg, "grok_api_key", None) if cfg else None,
        "gemini": getattr(cfg, "gemini_api_key", None) if cfg else None,
    }

    def _has_key(name: str) -> bool:
        return bool(db_key_map.get(name)) or bool(os.environ.get(env_key_map.get(name, ""), ""))

    # Order: active first, then others with keys, mock last
    ordered = [active_name]
    for name in PROVIDERS:
        if name != active_name and name != "mock" and _has_key(name):
            ordered.append(name)
    ordered.append("mock")

    last_error = ""
    for name in ordered:
        provider_cls = PROVIDERS.get(name)
        if not provider_cls:
            continue
        provider = provider_cls()
        try:
            response = provider.ask(prompt, system_prompt=system_prompt)
            # Check for error-style responses (providers return error strings, not exceptions)
            if response and not response.startswith("["):
                return response, provider.name()
            last_error = response
            logger.warning("Provider %s returned error-style response, trying next: %s", name, response[:100])
        except Exception as e:
            last_error = str(e)
            logger.warning("Provider %s raised exception, trying next: %s", name, e)

    # If all failed, return the last error with mock fallback
    mock = MockProvider()
    return mock.ask(prompt, system_prompt=system_prompt), f"Mock AI (all providers failed)"

