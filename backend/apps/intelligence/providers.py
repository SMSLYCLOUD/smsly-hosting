"""
AI Provider abstraction for SMSLY Hosting.

Supports OpenAI, Grok (xAI), Google Gemini, Claude (Anthropic), and Jules.
All providers work together when multiple keys are configured:
- 1 provider: uses it solo
- 2+ providers: collaborative consensus mode
- 0 providers: mock fallback
"""
import os
import logging
import concurrent.futures
from abc import ABC, abstractmethod
from typing import Optional, List, Tuple
import time
import httpx
from django.core.cache import cache
from functools import wraps

logger = logging.getLogger(__name__)

def retry_429(max_retries=3, base_delay=2.0):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except httpx.HTTPStatusError as exc:
                    last_exc = exc
                    if exc.response.status_code == 429 and attempt < max_retries:
                        delay = base_delay * (2 ** attempt)
                        logger.warning("429 Rate Limit in %s. Retrying in %ss...", func.__name__, delay)
                        time.sleep(delay)
                        continue
                    raise
                except Exception as exc:
                    # Some providers raise their last exception manually at the end
                    if hasattr(exc, 'response') and getattr(exc.response, 'status_code', None) == 429 and attempt < max_retries:
                        delay = base_delay * (2 ** attempt)
                        logger.warning("429 Rate Limit Exception in %s. Retrying in %ss...", func.__name__, delay)
                        time.sleep(delay)
                        continue
                    raise
            raise last_exc
        return wrapper
    return decorator

# Common placeholder values copied from forms/UX that should never be treated
# as real API keys.
_KEY_PLACEHOLDERS = {
    "configured key (hidden)",
    "enter api key",
    "not configured",
    "your_api_key_here",
}


def _sanitize_api_key(raw: Optional[str]) -> str:
    """Normalize user-entered key values and strip placeholder text."""
    if raw is None:
        return ""
    value = str(raw).strip().strip('"').strip("'")
    lower = value.lower()
    if lower.startswith("bearer "):
        value = value[7:].strip()
        lower = value.lower()
    if not value:
        return ""
    if lower in _KEY_PLACEHOLDERS:
        return ""
    if lower.startswith("configured key") and "hidden" in lower:
        return ""
    if set(value) == {"*"}:
        return ""
    return value


def _normalize_model(raw: Optional[str], default: str) -> str:
    value = str(raw or "").strip()
    return value or default


def _looks_like_model_error(exc: Exception) -> bool:
    """Best-effort detection for provider responses caused by invalid model IDs."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    status = exc.response.status_code
    if status not in (400, 404):
        return False
    try:
        payload = exc.response.json()
    except Exception:  # noqa: BLE001
        payload = {}
    message = str(payload).lower()
    return any(
        token in message
        for token in (
            "model",
            "unknown model",
            "unsupported model",
            "model_not_found",
            "not found",
        )
    )

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

    def is_configured(self) -> bool:
        """Check if this provider has a valid API key."""
        return bool(getattr(self, 'api_key', ''))

    def get_balance(self) -> dict:
        """
        Fetch remaining balance/credits from the provider's billing API.
        Returns: {"balance": str, "currency": str, "raw": dict}
        Override in subclasses that support balance checking.
        """
        return {"balance": "N/A", "currency": "", "raw": {}}


# ---------------------------------------------------------------------------
# OpenAI Provider
# ---------------------------------------------------------------------------

class OpenAIProvider(AIProvider):
    """OpenAI GPT provider — model configurable via OPENAI_MODEL env var."""

    BASE_URL = "https://api.openai.com/v1"

    def __init__(self):
        self.api_key = _sanitize_api_key(os.environ.get("OPENAI_API_KEY", ""))
        self.model = _normalize_model(os.environ.get("OPENAI_MODEL"), "gpt-4o-mini")

    def name(self) -> str:
        return f"OpenAI ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("[OpenAI] API key not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        candidate_models: List[str] = []
        for candidate in [self.model, "gpt-4o-mini", "gpt-4o"]:
            if candidate and candidate not in candidate_models:
                candidate_models.append(candidate)

        last_error: Optional[Exception] = None
        with httpx.Client(timeout=60) as client:
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
                except Exception as exc:  # noqa: BLE001
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

    def get_balance(self) -> dict:
        """Fetch OpenAI credit balance."""
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        try:
            with httpx.Client(timeout=15) as client:
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
                # Fallback: try organization billing
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
        except Exception as e: # pylint: disable=broad-exception-caught
            logger.debug("OpenAI balance check failed: %s", e)
            return {"balance": "Error checking", "currency": "", "raw": {}}


# ---------------------------------------------------------------------------
# Grok (xAI) Provider — OpenAI-compatible API
# ---------------------------------------------------------------------------

class GrokProvider(AIProvider):
    """xAI Grok provider — model configurable via GROK_MODEL env var."""

    BASE_URL = "https://api.x.ai/v1"

    def __init__(self):
        self.api_key = _sanitize_api_key(os.environ.get("GROK_API_KEY", ""))
        self.model = _normalize_model(os.environ.get("GROK_MODEL"), "grok-3-mini")

    def name(self) -> str:
        return f"Grok ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("[Grok] API key not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        candidate_models: List[str] = []
        for candidate in [self.model, "grok-3-mini", "grok-3"]:
            if candidate and candidate not in candidate_models:
                candidate_models.append(candidate)

        last_error: Optional[Exception] = None
        with httpx.Client(timeout=60) as client:
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
                except Exception as exc:  # noqa: BLE001
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

    def get_balance(self) -> dict:
        """Fetch xAI/Grok credit balance."""
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        try:
            with httpx.Client(timeout=15) as client:
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
        except Exception as e: # pylint: disable=broad-exception-caught
            logger.debug("Grok balance check failed: %s", e)
            return {"balance": "Error checking", "currency": "", "raw": {}}


# ---------------------------------------------------------------------------
# Google Gemini Provider
# ---------------------------------------------------------------------------

class GeminiProvider(AIProvider):
    """Google Gemini provider — model configurable via GEMINI_MODEL env var."""

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self):
        self.api_key = _sanitize_api_key(os.environ.get("GEMINI_API_KEY", ""))
        self.model = _normalize_model(os.environ.get("GEMINI_MODEL"), "gemini-2.0-flash")

    def name(self) -> str:
        return f"Gemini ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("[Gemini] API key not configured.")

        contents = []
        if system_prompt:
            contents.append({"role": "user", "parts": [{"text": system_prompt}]})
            contents.append({"role": "model", "parts": [{"text": "Understood."}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        candidate_models: List[str] = []
        for candidate in [self.model, "gemini-2.0-flash", "gemini-1.5-flash"]:
            normalized = str(candidate or "").strip().replace("models/", "")
            if normalized and normalized not in candidate_models:
                candidate_models.append(normalized)

        last_error: Optional[Exception] = None
        with httpx.Client(timeout=60) as client:
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
                except Exception as exc:  # noqa: BLE001
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

    def get_balance(self) -> dict:
        """Check Gemini API quota status. Gemini uses quota-based billing, not credits."""
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        try:
            # Validate key by listing available models
            with httpx.Client(timeout=15) as client:
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
                    return {"balance": "⚠️ Quota exhausted", "currency": "quota-based", "raw": {}}
                return {
                    "balance": "Unknown (check aistudio.google.com)",
                    "currency": "",
                    "raw": {}
                }
        except Exception as e: # pylint: disable=broad-exception-caught
            logger.debug("Gemini balance check failed: %s", e)
            return {"balance": "Error checking", "currency": "", "raw": {}}


# ---------------------------------------------------------------------------
# Claude (Anthropic) Provider
# ---------------------------------------------------------------------------

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
    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
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

        # pylint: disable=broad-exception-caught
        try:
            with httpx.Client(timeout=60) as client:
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
                # Claude returns content as a list of blocks
                return "".join(
                    block.get("text", "") for block in data.get("content", [])
                    if block.get("type") == "text"
                )
        except Exception as e:
            logger.error("Claude ask failed: %s", e)
            raise

    def get_balance(self) -> dict:
        """Check Claude/Anthropic balance. No official balance API yet."""
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        try:
            # Validate key by hitting a lightweight endpoint
            with httpx.Client(timeout=15) as client:
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
                    return {"balance": "❌ Invalid API key", "currency": "", "raw": {}}
                if resp.status_code == 429:
                    return {
                        "balance": "⚠️ Rate limited / credits low",
                        "currency": "USD",
                        "raw": {}
                    }
                return {"balance": f"Status {resp.status_code}", "currency": "", "raw": {}}
        except Exception as e: # pylint: disable=broad-exception-caught
            logger.debug("Claude balance check failed: %s", e)
            return {"balance": "Error checking", "currency": "", "raw": {}}


# ---------------------------------------------------------------------------
# Jules Provider
# ---------------------------------------------------------------------------

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
    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("[Jules] API key not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            with httpx.Client(timeout=60) as client:
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
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Jules ask failed: %s", exc)
            raise

    def get_balance(self) -> dict:
        """Jules balance API is provider-specific; surface configuration state."""
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        return {
            "balance": "Active (check Jules billing console)",
            "currency": "USD",
            "raw": {},
        }


# ---------------------------------------------------------------------------
# OpenRouter Provider (OpenAI-compatible)
# ---------------------------------------------------------------------------

class OpenRouterProvider(AIProvider):
    """OpenRouter provider — gateway to 100+ models."""

    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self):
        self.api_key = _sanitize_api_key(os.environ.get("OPENROUTER_API_KEY", ""))
        self.model = _normalize_model(os.environ.get("OPENROUTER_MODEL"), "openrouter/auto")

    def name(self) -> str:
        return f"OpenRouter ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("[OpenRouter] API key not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            with httpx.Client(timeout=60) as client:
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
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("OpenRouter ask failed: %s", exc)
            raise

    def get_balance(self) -> dict:
        """Fetch OpenRouter balance."""
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        try:
            with httpx.Client(timeout=15) as client:
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
        except Exception: # pylint: disable=broad-exception-caught
            return {"balance": "Error checking", "currency": "", "raw": {}}


# ---------------------------------------------------------------------------
# Groq Provider (OpenAI-compatible)
# ---------------------------------------------------------------------------

class GroqProvider(AIProvider):
    """Groq provider — ultra-low latency Llama/Mixtral."""

    BASE_URL = "https://api.groq.com/openai/v1"

    def __init__(self):
        self.api_key = _sanitize_api_key(os.environ.get("GROQ_API_KEY", ""))
        self.model = _normalize_model(os.environ.get("GROQ_MODEL"), "llama-3.3-70b-versatile")

    def name(self) -> str:
        return f"Groq ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("[Groq] API key not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            with httpx.Client(timeout=45) as client:
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
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Groq ask failed: %s", exc)
            raise


# ---------------------------------------------------------------------------
# Alibaba (DashScope) Provider (OpenAI-compatible)
# ---------------------------------------------------------------------------

class AlibabaProvider(AIProvider):
    """Alibaba DashScope (Qwen) provider."""

    BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def __init__(self):
        self.api_key = _sanitize_api_key(os.environ.get("ALIBABA_API_KEY", ""))
        self.model = _normalize_model(os.environ.get("ALIBABA_MODEL"), "qwen-max")

    def name(self) -> str:
        return f"Alibaba ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("[Alibaba] API key not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            with httpx.Client(timeout=60) as client:
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
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Alibaba ask failed: %s", exc)
            raise


# ---------------------------------------------------------------------------
# DeepSeek Provider (OpenAI-compatible)
# ---------------------------------------------------------------------------

class DeepSeekProvider(AIProvider):
    """DeepSeek provider — model configurable via DEEPSEEK_MODEL env var."""

    BASE_URL = "https://api.deepseek.com/v1"

    def __init__(self):
        self.api_key = _sanitize_api_key(os.environ.get("DEEPSEEK_API_KEY", ""))
        self.model = _normalize_model(os.environ.get("DEEPSEEK_MODEL"), "deepseek-coder")

    def name(self) -> str:
        return f"DeepSeek ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("[DeepSeek] API key not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            with httpx.Client(timeout=60) as client:
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
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("DeepSeek ask failed: %s", exc)
            raise

    def get_balance(self) -> dict:
        """Fetch DeepSeek balance from their user API."""
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        try:
            with httpx.Client(timeout=15) as client:
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
        except Exception as e: # pylint: disable=broad-exception-caught
            logger.debug("DeepSeek balance check failed: %s", e)
            return {"balance": "Error checking", "currency": "", "raw": {}}


# ---------------------------------------------------------------------------
# Local LLM Provider (OpenAI-compatible)
# ---------------------------------------------------------------------------

class LocalLLMProvider(AIProvider):
    """
    Local LLM provider for services like Ollama, LocalAI, vLLM, etc.
    Expects an OpenAI-compatible /v1/chat/completions endpoint.
    """

    def __init__(self, model_override: Optional[str] = None):
        self.api_key = _sanitize_api_key(os.environ.get("LOCALLM_API_KEY", ""))
        default_model = os.environ.get("LOCALLM_MODEL", "local-model")
        self.model = model_override or _normalize_model(default_model, "local-model")
        self.base_url = os.environ.get(
            "LOCALLM_BASE_URL",
            "http://localhost:11434/v1",
        ).rstrip("/")

    def name(self) -> str:
        return f"Local LLM ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        # API key is optional for local LLMs but we support it
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            with httpx.Client(timeout=120) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": self.model,
                        "messages": messages,
                        "max_tokens": 2048,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                return payload["choices"][0]["message"]["content"]
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Local LLM ask failed at %s: %s", self.base_url, exc)
            raise

    def is_configured(self) -> bool:
        # Local LLM is considered configured if base_url is set (has a default)
        return bool(self.base_url)

    def get_balance(self) -> dict:
        return {"balance": "Local (Free)", "currency": "N/A", "raw": {}}


# ---------------------------------------------------------------------------
# SMSLY Cloud AI Provider
# ---------------------------------------------------------------------------

class SMSLYCloudProvider(AIProvider):
    """
    SMSLY Cloud AI provider.
    Hosted expert for the SMSLY Senate.
    """

    BASE_URL = "https://ai.smsly.cloud/v1"

    def __init__(self):
        self.api_key = _sanitize_api_key(os.environ.get("SMSLYCLOUD_API_KEY", ""))
        self.model = _normalize_model(os.environ.get("SMSLYCLOUD_MODEL"), "smsly-latest")

    def name(self) -> str:
        return f"SMSLY Cloud AI ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("[SMSLY Cloud AI] API key not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            with httpx.Client(timeout=60) as client:
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
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("SMSLY Cloud AI ask failed: %s", exc)
            raise

    def get_balance(self) -> dict:
        return {"balance": "SmslyCloud (Standard)", "currency": "N/A", "raw": {}}


# ---------------------------------------------------------------------------
# Mock Provider (fallback when no API keys configured)
# ---------------------------------------------------------------------------

class MockProvider(AIProvider):
    """Fallback mock provider for testing."""

    def name(self) -> str:
        return "Mock AI"

    def is_configured(self) -> bool:
        return True  # Always available as last resort

    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if "deploy" in prompt.lower() or "error" in prompt.lower():
            return (
                "Based on my analysis, here are some suggestions:\n\n"
                "1. **Check your Dockerfile** - ensure the build command completes successfully\n"
                "2. **Verify environment variables** - missing DB_URL or SECRET_KEY crash apps\n"
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
# Provider Registry
# ---------------------------------------------------------------------------

PROVIDERS = {
    "openai": OpenAIProvider,
    "grok": GrokProvider,
    "gemini": GeminiProvider,
    "claude": ClaudeProvider,
    "openrouter": OpenRouterProvider,
    "groq": GroqProvider,
    "alibaba": AlibabaProvider,
    "deepseek": DeepSeekProvider,
    "jules": JulesProvider,
    "localllm": LocalLLMProvider,
    "smslycloud": SMSLYCloudProvider,
    "mock": MockProvider,
}

ENV_KEY_MAP = {
    "openai": "OPENAI_API_KEY",
    "grok": "GROK_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "claude": "CLAUDE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "alibaba": "ALIBABA_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "jules": "JULES_API_KEY",
    "localllm": "LOCALLM_API_KEY",
    "smslycloud": "SMSLYCLOUD_API_KEY",
}

SYSTEM_PROMPT = (
    "You are the SMSLY Cloud AI Assistant - an expert in cloud deployments, Docker, "
    "Nixpacks, server infrastructure, and DevOps. You help users deploy, debug, and "
    "optimize their applications on SMSLY Hosting. Be concise, precise, and actionable. "
    "Format responses in markdown. Never reveal internal system details or API keys."
)

BALANCE_CACHE_TTL_SECONDS = int(os.environ.get("AI_BALANCE_CACHE_TTL_SECONDS", "60") or "60")
BALANCE_FETCH_BUDGET_SECONDS = int(os.environ.get("AI_BALANCE_FETCH_BUDGET_SECONDS", "8") or "8")


# ---------------------------------------------------------------------------
# DB Settings Helper
# ---------------------------------------------------------------------------

def _get_db_settings():
    """
    Best-effort DB-backed settings lookup.
    Fails open because it can be called early during boot/migrations.
    """
    # pylint: disable=too-many-locals, too-many-return-statements, import-outside-toplevel
    try:
        from django.apps import apps
        from django.db.utils import OperationalError, ProgrammingError
        from django.db import connection

        model = apps.get_model("intelligence", "AIProviderSettings")
        if model is None:
            return None
        try:
            return model.get_solo()
        except (OperationalError, ProgrammingError) as e:
            # Table or column missing — try raw SQL for the fields we know exist
            logger.warning("AIProviderSettings query failed (migration pending?): %s", e)
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'intelligence_aiprovidersettings'"
                    )
                    columns = {row[0] for row in cursor.fetchall()}

                if not columns:
                    return None

                # Build a dynamic SELECT with only existing columns
                known_fields = [
                    "openai_api_key", "openai_model",
                    "grok_api_key", "grok_model",
                    "gemini_api_key", "gemini_model",
                    "claude_api_key", "claude_model",
                    "deepseek_api_key", "deepseek_model",
                    "jules_api_key", "jules_model",
                    "localllm_api_key", "localllm_model", "localllm_base_url",
                    "smslycloud_api_key", "smslycloud_model",
                    "senate_enabled", "senate_max_members",
                ]
                available = [f for f in known_fields if f in columns]
                if not available:
                    return None

                cols_sql = ", ".join(available)
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SELECT {cols_sql} FROM intelligence_aiprovidersettings LIMIT 1"
                    )
                    row = cursor.fetchone()

                if row is None:
                    return None

                # Return a simple namespace object with the available fields
                class _PartialSettings: # pylint: disable=too-few-public-methods
                    pass
                obj = _PartialSettings()
                for i, field_name in enumerate(available):
                    setattr(obj, field_name, row[i])
                # Set missing fields to None
                for f in known_fields:
                    if f not in columns:
                        setattr(obj, f, None)
                return obj
            except Exception as inner_e: # pylint: disable=broad-exception-caught
                logger.debug("Raw SQL fallback also failed: %s", inner_e)
                return None
    except Exception as e: # pylint: disable=broad-exception-caught
        logger.debug("_get_db_settings outer exception: %s", e)
        return None


def _effective_env_value(db_value: str | None, env_key: str, default: str = "") -> str:
    if db_value is not None:
        raw = str(db_value)
    else:
        raw = os.environ.get(env_key, default) or default

    if env_key.endswith("_API_KEY"):
        return _sanitize_api_key(raw)
    return _normalize_model(raw, default) if env_key.endswith("_MODEL") else raw


def _sync_db_to_env():
    """Push DB-stored keys/models into process env so provider classes work."""
    cfg = _get_db_settings()
    if not cfg:
        return

    pairs = [
        ("openai_api_key", "OPENAI_API_KEY", ""),
        ("openai_model", "OPENAI_MODEL", "gpt-4o-mini"),
        ("grok_api_key", "GROK_API_KEY", ""),
        ("grok_model", "GROK_MODEL", "grok-3-mini"),
        ("gemini_api_key", "GEMINI_API_KEY", ""),
        ("gemini_model", "GEMINI_MODEL", "gemini-2.0-flash"),
        ("claude_api_key", "CLAUDE_API_KEY", ""),
        ("claude_model", "CLAUDE_MODEL", "claude-sonnet-4-20250514"),
        ("openrouter_api_key", "OPENROUTER_API_KEY", ""),
        ("openrouter_model", "OPENROUTER_MODEL", "openrouter/auto"),
        ("groq_api_key", "GROQ_API_KEY", ""),
        ("groq_model", "GROQ_MODEL", "llama-3.3-70b-versatile"),
        ("alibaba_api_key", "ALIBABA_API_KEY", ""),
        ("alibaba_model", "ALIBABA_MODEL", "qwen-max"),
        ("deepseek_api_key", "DEEPSEEK_API_KEY", ""),
        ("deepseek_model", "DEEPSEEK_MODEL", "deepseek-coder"),
        ("jules_api_key", "JULES_API_KEY", ""),
        ("jules_model", "JULES_MODEL", "jules-latest"),
        ("localllm_api_key", "LOCALLM_API_KEY", ""),
        ("localllm_model", "LOCALLM_MODEL", "local-model"),
        ("localllm_base_url", "LOCALLM_BASE_URL", "http://localhost:11434/v1"),
        ("smslycloud_api_key", "SMSLYCLOUD_API_KEY", ""),
        ("smslycloud_model", "SMSLYCLOUD_MODEL", "smsly-latest"),
        ("senate_enabled", "SENATE_ENABLED", "True"),
        ("senate_max_members", "SENATE_MAX_MEMBERS", "5"),
    ]
    for attr, env_key, default in pairs:
        val = _effective_env_value(getattr(cfg, attr, None), env_key, default)
        if val:
            os.environ[env_key] = val
        else:
            os.environ.pop(env_key, None)


# ---------------------------------------------------------------------------
# Provider Discovery
# ---------------------------------------------------------------------------

def get_available_providers(include_balance: bool = False) -> List[dict]:
    """Return list of all providers with connection status and optional balance."""
    _sync_db_to_env()
    result = []
    provider_instances: dict[str, AIProvider] = {}
    for key, cls in PROVIDERS.items():
        if key == "mock":
            continue
        instance = cls()
        provider_instances[key] = instance
        info = {
            "id": key,
            "name": instance.name(),
            "configured": instance.is_configured(),
            "model": getattr(instance, 'model', ''),
        }
        result.append(info)

    if not include_balance:
        return result

    balances_by_id: dict[str, dict] = {}
    provider_models: dict[str, str] = {
        str(info["id"]): str(info.get("model") or "") for info in result
    }
    pending: dict[str, AIProvider] = {}

    for info in result:
        provider_id = info["id"]
        configured = bool(info.get("configured"))
        if not configured:
            balances_by_id[provider_id] = {"balance": "Not configured", "currency": "", "raw": {}}
            continue

        model = str(info.get("model") or "")
        cache_key = f"ai:provider-balance:{provider_id}:{model}"
        cached = cache.get(cache_key)
        if isinstance(cached, dict):
            balances_by_id[provider_id] = cached
            continue

        instance = provider_instances.get(provider_id)
        if instance:
            pending[provider_id] = instance

    if pending:
        max_workers = min(4, len(pending))
        futures = {}
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        try:
            for provider_id, instance in pending.items():
                futures[pool.submit(instance.get_balance)] = provider_id

            try:
                for future in concurrent.futures.as_completed(
                    futures, timeout=BALANCE_FETCH_BUDGET_SECONDS
                ):
                    provider_id = futures[future]
                    try:
                        balance = future.result()
                        if not isinstance(balance, dict):
                            balance = {"balance": "Unknown", "currency": "", "raw": {}}
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Balance fetch failed for %s: %s", provider_id, exc)
                        balance = {"balance": "Error checking", "currency": "", "raw": {}}
                    balances_by_id[provider_id] = balance
                    cache.set(
                        f"ai:provider-balance:{provider_id}:{provider_models.get(provider_id, '')}",
                        balance,
                        timeout=BALANCE_CACHE_TTL_SECONDS,
                    )
            except concurrent.futures.TimeoutError:
                logger.warning(
                    "AI provider balance fetch exceeded budget (%ss); returning partial balances",
                    BALANCE_FETCH_BUDGET_SECONDS,
                )
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    for info in result:
        provider_id = info["id"]
        if provider_id in balances_by_id:
            info["balance"] = balances_by_id[provider_id]
            continue
        configured = bool(info.get("configured"))
        info["balance"] = (
            {"balance": "Timed out", "currency": "", "raw": {}}
            if configured
            else {"balance": "Not configured", "currency": "", "raw": {}}
        )

    return result


def get_configured_providers() -> List[AIProvider]:
    """Return all providers that have valid API keys configured."""
    _sync_db_to_env()
    configured = []
    for key, cls in PROVIDERS.items():
        if key == "mock":
            continue

        if key == "localllm":
            # Support multiple local models (comma-separated)
            raw_models = os.environ.get("LOCALLM_MODEL", "")
            if "," in raw_models:
                models = [m.strip() for m in raw_models.split(",") if m.strip()]
                for model in models:
                    instance = cls(model_override=model)
                    if instance.is_configured():
                        configured.append(instance)
                continue

        instance = cls()
        if instance.is_configured():
            configured.append(instance)
    
    # Respect Senate Committee size limits if configured
    try:
        max_members = int(os.environ.get("SENATE_MAX_MEMBERS", "5"))
        if len(configured) > max_members:
            logger.info("Capping Senate Committee to %d members (found %d)", max_members, len(configured))
            configured = configured[:max_members]
    except (ValueError, TypeError):
        pass

    return configured


def get_provider() -> AIProvider:
    """Return the first configured AI provider, falling back to mock."""
    configured = get_configured_providers()
    if configured:
        return configured[0]
    return MockProvider()


# ---------------------------------------------------------------------------
# Single Provider Ask
# ---------------------------------------------------------------------------

def _ask_single(
    provider: AIProvider,
    prompt: str,
    system_prompt: Optional[str] = None
) -> Tuple[str, str]:
    """Ask a single provider. Returns (response, provider_name) or raises."""
    response = provider.ask(prompt, system_prompt=system_prompt)
    return response, provider.name()


# ---------------------------------------------------------------------------
# Senate Committee — Multi-Provider Deliberation
# ---------------------------------------------------------------------------

COMMITTEE_SYSTEM_PROMPT = (
    "You are a member of the SMSLY AI Senate Committee — a panel of AI experts "
    "that collaborates on DevOps, deployment, and infrastructure decisions. "
    "You provide honest, technical analysis. When reviewing peers, be constructive "
    "but direct about disagreements."
)


def _parallel_ask(providers: List[AIProvider], prompt: str,
                  system_prompt: Optional[str] = None) -> List[Tuple[str, str]]:
    """Ask multiple providers in parallel. Returns list of (response, name)."""
    results: List[Tuple[str, str]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(providers)) as pool:
        futures = {
            pool.submit(_ask_single, p, prompt, system_prompt): p
            for p in providers
        }
        for future in concurrent.futures.as_completed(futures, timeout=90):
            provider = futures[future]
            try:
                response, name = future.result()
                results.append((response, name))
            except Exception as e: # pylint: disable=broad-exception-caught
                logger.warning("Provider %s failed: %s", provider.name(), e)

    return results


def ask_collaborative(prompt: str, system_prompt: Optional[str] = None) -> Tuple[str, str]:
    # pylint: disable=too-many-locals, too-many-statements, too-many-return-statements
    """
    Senate Committee deliberation when 2+ providers are active.

    Phase 1 — PROPOSE: All providers answer independently (parallel)
    Phase 2 — REVIEW:  Each provider reviews & votes on all proposals (parallel)
    Phase 3 — RESOLVE: Chair synthesizes votes into final committee resolution

    Single provider → direct answer. Zero providers → mock fallback.

    Returns (response_text, attribution_string).
    """
    _sync_db_to_env()
    configured = get_configured_providers()

    if not configured:
        mock = MockProvider()
        return mock.ask(prompt, system_prompt=system_prompt), mock.name()

    if len(configured) == 1:
        provider = configured[0]
        try:
            return _ask_single(provider, prompt, system_prompt)
        except Exception as e: # pylint: disable=broad-exception-caught
            logger.warning("Single provider %s failed: %s", provider.name(), e)
            mock = MockProvider()
            return mock.ask(prompt, system_prompt=system_prompt), \
                f"Mock AI ({provider.name()} failed)"

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PHASE 1 — PROPOSE: Each provider answers independently
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    logger.info("Senate Committee convened with %d members", len(configured))
    proposals = _parallel_ask(configured, prompt, system_prompt or COMMITTEE_SYSTEM_PROMPT)

    if not proposals:
        mock = MockProvider()
        return mock.ask(prompt, system_prompt=system_prompt), \
            f"Mock AI (all {len(configured)} senators failed)"

    if len(proposals) == 1:
        return proposals[0]

    member_names = [name for _, name in proposals]
    logger.info("Phase 1 complete: %d proposals received from %s",
                len(proposals), ", ".join(member_names))

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PHASE 2 — REVIEW & VOTE: Each provider reviews all other proposals
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    proposals_text = "\n\n---\n\n".join(
        f"### Proposal by {name}\n{resp}" for resp, name in proposals
    )

    review_prompt = (
        f"You are reviewing {len(proposals)} proposals from fellow committee members.\n\n"
        f"Original question:\n{prompt}\n\n"
        f"Proposals:\n{proposals_text}\n\n"
        f"For EACH proposal, vote:\n"
        f"- **AGREE** — the proposal is correct and complete\n"
        f"- **AMEND** — partially correct but needs changes (explain what)\n"
        f"- **DISAGREE** — fundamentally wrong (explain why)\n\n"
        f"Then state your FINAL RECOMMENDATION in 2-3 sentences.\n"
        f"Format: one vote per proposal, then your recommendation."
    )

    votes = _parallel_ask(configured, review_prompt, COMMITTEE_SYSTEM_PROMPT)
    logger.info("Phase 2 complete: %d votes received", len(votes))

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PHASE 3 — CHAIR'S RESOLUTION: Synthesize into final answer
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    votes_text = "\n\n---\n\n".join(
        f"### Review by {name}\n{resp}" for resp, name in votes
    ) if votes else "No reviews were submitted."

    chair_prompt = (
        f"You are the CHAIR of the SMSLY AI Senate Committee.\n\n"
        f"Original question:\n{prompt}\n\n"
        f"Proposals submitted:\n{proposals_text}\n\n"
        f"Committee votes and reviews:\n{votes_text}\n\n"
        f"Write the FINAL COMMITTEE RESOLUTION:\n"
        f"1. State the consensus answer (what the majority agreed on)\n"
        f"2. Note any important dissenting points\n"
        f"3. Give the final actionable recommendation\n\n"
        f"Be concise — max 250 words. Write as the committee, not as an individual."
    )

    # Rotate chair: use a different provider than the first proposer
    chair = configured[1] if len(configured) > 1 else configured[0]
    try:
        resolution = chair.ask(chair_prompt, system_prompt=COMMITTEE_SYSTEM_PROMPT)
        attribution = f"Senate Committee ({' + '.join(member_names)})"
        logger.info("Phase 3 complete: Chair %s delivered resolution", chair.name())
        return resolution, attribution
    except Exception as e: # pylint: disable=broad-exception-caught
        logger.warning("Chair %s failed to deliver resolution: %s", chair.name(), e)
        # Fallback: try another provider as chair
        for fallback_chair in configured:
            if fallback_chair is not chair:
                try:
                    resolution = fallback_chair.ask(
                        chair_prompt, system_prompt=COMMITTEE_SYSTEM_PROMPT
                    )
                    attribution = f"Senate Committee ({' + '.join(member_names)})"
                    return resolution, attribution
                except Exception: # pylint: disable=broad-exception-caught
                    continue
        # Last resort: return first proposal
        return proposals[0][0], f"{proposals[0][1]} (committee failed, solo answer)"


# ---------------------------------------------------------------------------
# ask_with_fallback — Primary API (backwards-compatible)
# ---------------------------------------------------------------------------

def ask_with_fallback(prompt: str, system_prompt: Optional[str] = None) -> Tuple[str, str]:
    """
    Smart multi-provider ask:
    - If 2+ providers configured → collaborative consensus mode
    - If 1 provider → single provider mode
    - If 0 providers → mock fallback

    Returns (response_text, provider_name).
    """
    _sync_db_to_env()
    configured = get_configured_providers()

    senate_enabled = os.environ.get("SENATE_ENABLED", "True").lower() == "true"
    if len(configured) >= 2 and senate_enabled:
        response, provider_name = ask_collaborative(prompt, system_prompt)
        if not provider_name.startswith("Mock AI (all"):
            return response, provider_name

        # Committee phase can fail even when at least one provider is
        # recoverable (e.g., transient endpoint/model errors). Try a direct
        # sequential pass before returning full mock mode.
        for provider in configured:
            try:
                return _ask_single(provider, prompt, system_prompt)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Committee rescue with %s failed: %s", provider.name(), exc)
        return response, provider_name

    if len(configured) == 1:
        provider = configured[0]
        try:
            return _ask_single(provider, prompt, system_prompt)
        except Exception as e: # pylint: disable=broad-exception-caught
            logger.warning("Provider %s failed, falling back to mock: %s", provider.name(), e)
            mock = MockProvider()
            return mock.ask(prompt, system_prompt=system_prompt), \
                f"Mock AI ({provider.name()} failed)"

    # No providers configured
    mock = MockProvider()
    return mock.ask(prompt, system_prompt=system_prompt), mock.name()
