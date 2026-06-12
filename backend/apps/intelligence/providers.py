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
import hashlib
import threading
import concurrent.futures
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional, List, Tuple, Generator
import time
import httpx
from django.core.cache import cache
from functools import wraps

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection pool — reuse TCP+TLS connections across calls
# ---------------------------------------------------------------------------
_client_pool: dict[str, httpx.Client] = {}
_client_pool_lock = threading.Lock()

def _get_client(provider_key: str, timeout: int = 60) -> httpx.Client:
    """Get or create a pooled httpx.Client for the given provider."""
    with _client_pool_lock:
        if provider_key not in _client_pool:
            _client_pool[provider_key] = httpx.Client(
                timeout=timeout,
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=5,
                    keepalive_expiry=30,
                ),
            )
        return _client_pool[provider_key]

# ---------------------------------------------------------------------------
# Circuit breaker — skip providers that are failing repeatedly
# ---------------------------------------------------------------------------
_provider_failures: dict[str, list[datetime]] = defaultdict(list)
_provider_circuit_open_until: dict[str, datetime] = {}
_circuit_lock = threading.Lock()

CIRCUIT_FAILURE_THRESHOLD = 5
CIRCUIT_OPEN_DURATION = timedelta(minutes=5)
CIRCUIT_WINDOW = timedelta(minutes=5)

def _record_provider_failure(provider_id: str):
    """Record a provider failure for circuit breaker."""
    with _circuit_lock:
        now = datetime.now()
        _provider_failures[provider_id].append(now)
        cutoff = now - CIRCUIT_WINDOW
        _provider_failures[provider_id] = [f for f in _provider_failures[provider_id] if f > cutoff]
        if len(_provider_failures[provider_id]) >= CIRCUIT_FAILURE_THRESHOLD:
            _provider_circuit_open_until[provider_id] = now + CIRCUIT_OPEN_DURATION

def _is_circuit_open(provider_id: str) -> bool:
    """Check if circuit breaker is open for a provider."""
    with _circuit_lock:
        until = _provider_circuit_open_until.get(provider_id)
        if until and datetime.now() < until:
            return True
        if provider_id in _provider_circuit_open_until:
            del _provider_circuit_open_until[provider_id]
        return False

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
    id: str = ""

    @abstractmethod
    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Send a prompt and return the AI response text."""

    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""

    def is_configured(self) -> bool:
        """Check if this provider has a valid API key."""
        return bool(getattr(self, 'api_key', ''))

    def ask_stream(self, prompt: str, system_prompt: Optional[str] = None) -> Generator[str, None, None]:
        """Yield response chunks as they arrive. Override in subclasses that support streaming."""
        response = self.ask(prompt, system_prompt)
        yield response

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

    def ask_stream(self, prompt: str, system_prompt: Optional[str] = None) -> Generator[str, None, None]:
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

    def ask_stream(self, prompt: str, system_prompt: Optional[str] = None) -> Generator[str, None, None]:
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

    def ask_stream(self, prompt: str, system_prompt: Optional[str] = None) -> Generator[str, None, None]:
        """Fallback: yield full response as single chunk."""
        response = self.ask(prompt, system_prompt)
        yield response

    def get_balance(self) -> dict:
        """Check Gemini API quota status. Gemini uses quota-based billing, not credits."""
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        try:
            # Validate key by listing available models
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
            # Claude returns content as a list of blocks
            return "".join(
                block.get("text", "") for block in data.get("content", [])
                if block.get("type") == "text"
            )
        except Exception as e:
            logger.error("Claude ask failed: %s", e)
            raise

    def ask_stream(self, prompt: str, system_prompt: Optional[str] = None) -> Generator[str, None, None]:
        """Fallback: yield full response as single chunk."""
        response = self.ask(prompt, system_prompt)
        yield response

    def get_balance(self) -> dict:
        """Check Claude/Anthropic balance. No official balance API yet."""
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        try:
            # Validate key by hitting a lightweight endpoint
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
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Jules ask failed: %s", exc)
            raise

    def ask_stream(self, prompt: str, system_prompt: Optional[str] = None) -> Generator[str, None, None]:
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
            client = _get_client("openrouter", timeout=60)
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

    def ask_stream(self, prompt: str, system_prompt: Optional[str] = None) -> Generator[str, None, None]:
        """Yield response chunks as they arrive (SSE streaming)."""
        if not self.api_key:
            raise ValueError("[OpenRouter] API key not configured.")

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

        client = _get_client("openrouter", timeout=120)
        with client.stream(
            "POST",
            f"{self.BASE_URL}/chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://smsly.hosting",
                "X-Title": "SMSLY Intelligence Senate",
            },
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
        """Fetch OpenRouter balance."""
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        try:
            client = _get_client("openrouter", timeout=15)
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
            client = _get_client("groq", timeout=45)
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

    def ask_stream(self, prompt: str, system_prompt: Optional[str] = None) -> Generator[str, None, None]:
        """Yield response chunks as they arrive (SSE streaming)."""
        if not self.api_key:
            raise ValueError("[Groq] API key not configured.")

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

        client = _get_client("groq", timeout=120)
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
            client = _get_client("alibaba", timeout=60)
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

    def ask_stream(self, prompt: str, system_prompt: Optional[str] = None) -> Generator[str, None, None]:
        """Yield response chunks as they arrive (SSE streaming)."""
        if not self.api_key:
            raise ValueError("[Alibaba] API key not configured.")

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

        client = _get_client("alibaba", timeout=120)
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
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("DeepSeek ask failed: %s", exc)
            raise

    def ask_stream(self, prompt: str, system_prompt: Optional[str] = None) -> Generator[str, None, None]:
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
            client = _get_client("locallm", timeout=120)
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

    def ask_stream(self, prompt: str, system_prompt: Optional[str] = None) -> Generator[str, None, None]:
        """Yield response chunks as they arrive (SSE streaming)."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 2048,
            "stream": True,
        }

        client = _get_client("locallm", timeout=120)
        with client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=headers,
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
            client = _get_client("smslycloud", timeout=60)
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

    def ask_stream(self, prompt: str, system_prompt: Optional[str] = None) -> Generator[str, None, None]:
        """Yield response chunks as they arrive (SSE streaming)."""
        if not self.api_key:
            raise ValueError("[SMSLY Cloud AI] API key not configured.")

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

        client = _get_client("smslycloud", timeout=120)
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
        return {"balance": "SmslyCloud (Standard)", "currency": "N/A", "raw": {}}


# ---------------------------------------------------------------------------
# FreeModel.dev Provider (OpenAI-compatible)
# ---------------------------------------------------------------------------

class FreeModelProvider(AIProvider):
    """
    FreeModel.dev provider — free AI model gateway.

    Uses an OpenAI-compatible `/chat/completions` endpoint.
    """

    def __init__(self, model_override: Optional[str] = None):
        self.api_key = _sanitize_api_key(os.environ.get("FREEMODEL_API_KEY", ""))
        default_model = os.environ.get("FREEMODEL_MODEL", "gpt-4o-mini")
        self.model = model_override or _normalize_model(default_model, "gpt-4o-mini")
        self.base_url = os.environ.get(
            "FREEMODEL_BASE_URL",
            "https://api.freemodel.dev/v1",
        ).rstrip("/")

    def name(self) -> str:
        return f"FreeModel ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("[FreeModel] API key not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            client = _get_client("freemodel", timeout=60)
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
            logger.error("FreeModel ask failed: %s", exc)
            raise

    def ask_stream(self, prompt: str, system_prompt: Optional[str] = None) -> Generator[str, None, None]:
        """Yield response chunks as they arrive (SSE streaming)."""
        if not self.api_key:
            raise ValueError("[FreeModel] API key not configured.")

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

        client = _get_client("freemodel", timeout=120)
        with client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
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
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        return {"balance": "Active (Free)", "currency": "N/A", "raw": {}}


# ---------------------------------------------------------------------------
# OpenCode API Provider (OpenAI-compatible)
# ---------------------------------------------------------------------------

class OpenCodeProvider(AIProvider):
    """
    OpenCode AI provider — configurable gateway.

    Uses an OpenAI-compatible `/chat/completions` endpoint.
    """

    def __init__(self, model_override: Optional[str] = None):
        self.api_key = _sanitize_api_key(os.environ.get("OPENCODE_API_KEY", ""))
        default_model = os.environ.get("OPENCODE_MODEL", "opencode-latest")
        self.model = model_override or _normalize_model(default_model, "opencode-latest")
        self.base_url = os.environ.get(
            "OPENCODE_BASE_URL",
            "https://api.opencode.ai/v1",
        ).rstrip("/")

    def name(self) -> str:
        return f"OpenCode ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("[OpenCode] API key not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            client = _get_client("opencode", timeout=60)
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
            logger.error("OpenCode ask failed: %s", exc)
            raise

    def ask_stream(self, prompt: str, system_prompt: Optional[str] = None) -> Generator[str, None, None]:
        """Yield response chunks as they arrive (SSE streaming)."""
        if not self.api_key:
            raise ValueError("[OpenCode] API key not configured.")

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

        client = _get_client("opencode", timeout=120)
        with client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
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
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        return {"balance": "Active (OpenCode)", "currency": "N/A", "raw": {}}


# ---------------------------------------------------------------------------
# Mistral Provider (OpenAI-compatible)
# ---------------------------------------------------------------------------

class MistralProvider(AIProvider):
    """
    Mistral AI provider — La Plateforme.

    Uses an OpenAI-compatible `/chat/completions` endpoint.
    Free tier requires phone verification and training opt-in.
    """

    def __init__(self, model_override: Optional[str] = None):
        self.api_key = _sanitize_api_key(os.environ.get("MISTRAL_API_KEY", ""))
        default_model = os.environ.get("MISTRAL_MODEL", "mistral-small-latest")
        self.model = model_override or _normalize_model(default_model, "mistral-small-latest")
        self.base_url = os.environ.get(
            "MISTRAL_BASE_URL",
            "https://api.mistral.ai/v1",
        ).rstrip("/")

    def name(self) -> str:
        return f"Mistral ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("[Mistral] API key not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            client = _get_client("mistral", timeout=60)
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
            logger.error("Mistral ask failed: %s", exc)
            raise

    def ask_stream(self, prompt: str, system_prompt: Optional[str] = None) -> Generator[str, None, None]:
        """Yield response chunks as they arrive (SSE streaming)."""
        if not self.api_key:
            raise ValueError("[Mistral] API key not configured.")

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

        client = _get_client("mistral", timeout=120)
        with client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
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
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        return {"balance": "Active (Free Tier)", "currency": "N/A", "raw": {}}


# ---------------------------------------------------------------------------
# NVIDIA NIM Provider (OpenAI-compatible)
# ---------------------------------------------------------------------------

class NvidiaNimProvider(AIProvider):
    """
    NVIDIA NIM provider — free tier with phone verification.

    Uses an OpenAI-compatible `/chat/completions` endpoint.
    """

    def __init__(self, model_override: Optional[str] = None):
        self.api_key = _sanitize_api_key(os.environ.get("NVIDIA_API_KEY", ""))
        default_model = os.environ.get("NVIDIA_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct")
        self.model = model_override or _normalize_model(default_model, "nvidia/llama-3.1-nemotron-70b-instruct")
        self.base_url = os.environ.get(
            "NVIDIA_BASE_URL",
            "https://integrate.api.nvidia.com/v1",
        ).rstrip("/")

    def name(self) -> str:
        return f"NVIDIA NIM ({self.model})"

    @retry_429()
    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("[NVIDIA NIM] API key not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            client = _get_client("nvidia", timeout=60)
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
            logger.error("NVIDIA NIM ask failed: %s", exc)
            raise

    def ask_stream(self, prompt: str, system_prompt: Optional[str] = None) -> Generator[str, None, None]:
        """Yield response chunks as they arrive (SSE streaming)."""
        if not self.api_key:
            raise ValueError("[NVIDIA NIM] API key not configured.")

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

        client = _get_client("nvidia", timeout=120)
        with client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
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
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        return {"balance": "Active (Free Tier)", "currency": "N/A", "raw": {}}


# ---------------------------------------------------------------------------
# Cloudflare Workers AI Provider (OpenAI-compatible via AI Gateway)
# ---------------------------------------------------------------------------

class CloudflareProvider(AIProvider):
    """
    Cloudflare Workers AI provider — accessed via AI Gateway.

    Uses an OpenAI-compatible `/chat/completions` endpoint through
    Cloudflare AI Gateway. Requires a Cloudflare account and AI Gateway setup.
    Base URL format: https://gateway.ai.cloudflare.com/v1/{account_id}/{gateway}/workers-ai
    """

    def __init__(self, model_override: Optional[str] = None):
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
    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
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
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Cloudflare ask failed: %s", exc)
            raise

    def ask_stream(self, prompt: str, system_prompt: Optional[str] = None) -> Generator[str, None, None]:
        """Fallback: yield full response as single chunk."""
        response = self.ask(prompt, system_prompt)
        yield response

    def get_balance(self) -> dict:
        if not self.api_key:
            return {"balance": "Not configured", "currency": "", "raw": {}}
        return {"balance": "Active (Free Tier)", "currency": "N/A", "raw": {}}


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
        raise NotImplementedError("No AI provider is configured. Add an API key in Settings > AI to use this feature.")


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
    "freemodel": FreeModelProvider,
    "opencode": OpenCodeProvider,
    "mistral": MistralProvider,
    "nvidia": NvidiaNimProvider,
    "cloudflare": CloudflareProvider,
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
    "freemodel": "FREEMODEL_API_KEY",
    "opencode": "OPENCODE_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "cloudflare": "CLOUDFLARE_API_KEY",
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
                    "freemodel_api_key", "freemodel_model", "freemodel_base_url",
                    "opencode_api_key", "opencode_model", "opencode_base_url",
                    "mistral_api_key", "mistral_model", "mistral_base_url",
                    "nvidia_api_key", "nvidia_model", "nvidia_base_url",
                    "cloudflare_api_key", "cloudflare_model", "cloudflare_base_url",
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
    """Push DB-stored keys/models into process env so provider classes work (cached 30s)."""
    cache_key = "ai:db_settings_sync"
    cached = cache.get(cache_key)
    if cached is not None:
        os.environ.update(cached)
        return

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
        ("jules_base_url", "JULES_BASE_URL", "https://api.jules.google.com/v1"),
        ("localllm_api_key", "LOCALLM_API_KEY", ""),
        ("localllm_model", "LOCALLM_MODEL", "local-model"),
        ("localllm_base_url", "LOCALLM_BASE_URL", "http://localhost:11434/v1"),
        ("smslycloud_api_key", "SMSLYCLOUD_API_KEY", ""),
        ("smslycloud_model", "SMSLYCLOUD_MODEL", "smsly-latest"),
        ("freemodel_api_key", "FREEMODEL_API_KEY", ""),
        ("freemodel_model", "FREEMODEL_MODEL", "gpt-4o-mini"),
        ("freemodel_base_url", "FREEMODEL_BASE_URL", "https://api.freemodel.dev/v1"),
        ("opencode_api_key", "OPENCODE_API_KEY", ""),
        ("opencode_model", "OPENCODE_MODEL", "opencode-latest"),
        ("opencode_base_url", "OPENCODE_BASE_URL", "https://api.opencode.ai/v1"),
        ("mistral_api_key", "MISTRAL_API_KEY", ""),
        ("mistral_model", "MISTRAL_MODEL", "mistral-small-latest"),
        ("mistral_base_url", "MISTRAL_BASE_URL", "https://api.mistral.ai/v1"),
        ("nvidia_api_key", "NVIDIA_API_KEY", ""),
        ("nvidia_model", "NVIDIA_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct"),
        ("nvidia_base_url", "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        ("cloudflare_api_key", "CLOUDFLARE_API_KEY", ""),
        ("cloudflare_model", "CLOUDFLARE_MODEL", "@cf/meta/llama-3.1-8b-instruct"),
        ("cloudflare_base_url", "CLOUDFLARE_BASE_URL", "https://gateway.ai.cloudflare.com/v1/YOUR_ACCOUNT_ID/default/workers-ai"),
        ("senate_enabled", "SENATE_ENABLED", "True"),
        ("senate_max_members", "SENATE_MAX_MEMBERS", "5"),
    ]
    env_snapshot = {}
    for attr, env_key, default in pairs:
        val = _effective_env_value(getattr(cfg, attr, None), env_key, default)
        if val:
            os.environ[env_key] = val
            env_snapshot[env_key] = val
        else:
            os.environ.pop(env_key, None)

    cache.set(cache_key, env_snapshot, timeout=30)


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
        instance.id = key
        provider_instances[key] = instance
        info = {
            "id": key,
            "name": instance.name(),
            "configured": instance.is_configured(),
            "model": getattr(instance, 'model', ''),
            "base_url": getattr(instance, 'base_url', ''),
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
                    instance.id = key
                    if instance.is_configured():
                        configured.append(instance)
                continue

        instance = cls()
        if instance.is_configured():
            instance.id = key
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
    """Return the first configured AI provider, or raise."""
    configured = get_configured_providers()
    if configured:
        return configured[0]
    raise RuntimeError("No AI providers configured.")


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

CODE_REVIEW_SYSTEM_PROMPT = (
    "You are an expert code reviewer. Analyze the provided code thoroughly.\n"
    "Focus on:\n"
    "1. Bugs, errors, and potential issues\n"
    "2. Security vulnerabilities\n"
    "3. Performance problems\n"
    "4. Code quality and best practices\n"
    "5. Missing error handling\n\n"
    "Be specific with line references. Provide actionable feedback."
)

SENATE_COMMITTEE_COST_MULTIPLIER = 3


def _parallel_ask(providers: List[AIProvider], prompt: str,
                  system_prompt: Optional[str] = None) -> List[Tuple[str, str]]:
    """Ask multiple providers in parallel. Returns list of (response, name)."""
    results: List[Tuple[str, str]] = []

    timeout = int(os.environ.get("SENATE_TIMEOUT_SECONDS", "180"))
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(providers))
    futures = {
        pool.submit(_ask_single, p, prompt, system_prompt): p
        for p in providers
    }
    try:
        for future in concurrent.futures.as_completed(futures, timeout=timeout):
            provider = futures[future]
            try:
                response, name = future.result()
                results.append((response, name))
            except Exception as e: # pylint: disable=broad-exception-caught
                _record_provider_failure(getattr(provider, "id", ""))
                logger.warning("Provider %s failed: %s", provider.name(), e)
    except concurrent.futures.TimeoutError:
        logger.warning("Parallel ask timed out, proceeding with %d results", len(results))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

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
    configured = [p for p in get_configured_providers() if not _is_circuit_open(getattr(p, "id", ""))]

    if not configured:
        raise RuntimeError("No AI providers configured. Add an API key in Settings > AI.")

    if len(configured) == 1:
        provider = configured[0]
        try:
            return _ask_single(provider, prompt, system_prompt)
        except Exception as e: # pylint: disable=broad-exception-caught
            _record_provider_failure(getattr(provider, "id", ""))
            logger.warning("Single provider %s failed: %s", provider.name(), e)
            raise

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PHASE 1 — PROPOSE: Each provider answers independently
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    logger.info("Senate Committee convened with %d members", len(configured))
    proposals = _parallel_ask(configured, prompt, system_prompt or COMMITTEE_SYSTEM_PROMPT)

    if not proposals:
        raise RuntimeError(f"All {len(configured)} AI providers failed to respond.")

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
        _record_provider_failure(getattr(chair, "id", ""))
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
                    _record_provider_failure(getattr(fallback_chair, "id", ""))
                    continue
        # Last resort: return first proposal
        return proposals[0][0], f"{proposals[0][1]} (committee failed, solo answer)"


# ---------------------------------------------------------------------------
# Provider Resolution Helper
# ---------------------------------------------------------------------------

def _resolve_providers(provider_ids: List[str]) -> List[AIProvider]:
    """Resolve a list of provider IDs to instantiated provider objects.
    Only returns providers that are configured and available."""
    _sync_db_to_env()
    resolved = []
    for pid in provider_ids:
        cls = PROVIDERS.get(pid)
        if not cls:
            continue
        instance = cls()
        instance.id = pid
        if instance.is_configured():
            resolved.append(instance)
    return resolved


# ---------------------------------------------------------------------------
# 2-Agent Code Review
# ---------------------------------------------------------------------------

def ask_code_review(
    prompt: str,
    system_prompt: Optional[str] = None,
    provider_ids: Optional[List[str]] = None,
) -> Tuple[str, str]:
    """2-Agent code review: two providers cross-check each other.

    Flow:
    1. Agent A and Agent B both analyze the code (parallel)
    2. Agent A reviews Agent B's analysis, Agent B reviews Agent A's (parallel)
    3. Return combined insights

    Args:
        prompt: The code/task to review
        system_prompt: Optional system prompt
        provider_ids: List of exactly 2 provider IDs to use

    Returns:
        (combined_response, provider_info) tuple
    """
    _sync_db_to_env()

    if not provider_ids or len(provider_ids) < 2:
        return ask_with_fallback(prompt, system_prompt)

    available = _resolve_providers(provider_ids[:2])
    if len(available) < 2:
        return ask_with_fallback(prompt, system_prompt, provider_ids[0] if provider_ids else None)

    agent_a, agent_b = available[0], available[1]
    effective_system = system_prompt or CODE_REVIEW_SYSTEM_PROMPT

    # Phase 1: Both agents analyze (parallel)
    phase1_prompt = (
        f"You are performing a thorough code review.\n"
        f"Analyze the following and provide your assessment:\n\n{prompt}"
    )

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    try:
        future_a = pool.submit(agent_a.ask, phase1_prompt, effective_system)
        future_b = pool.submit(agent_b.ask, phase1_prompt, effective_system)

        review_a = future_a.result(timeout=60)
        review_b = future_b.result(timeout=60)
    except Exception as exc:
        pool.shutdown(wait=False, cancel_futures=True)
        logger.warning("Code review phase 1 failed: %s", exc)
        return ask_with_fallback(prompt, system_prompt)

    # Phase 2: Cross-review (parallel)
    cross_prompt_a = (
        f"You previously reviewed code and provided:\n---\n{review_a}\n---\n\n"
        f"Another agent reviewed the same code and provided:\n---\n{review_b}\n---\n\n"
        f"Now provide your FINAL assessment. Consider both perspectives.\n"
        f"Identify any issues the other agent caught that you missed.\n"
        f"Resolve any disagreements with reasoning.\n\n"
        f"Original code/task:\n{prompt}"
    )

    cross_prompt_b = (
        f"You previously reviewed code and provided:\n---\n{review_b}\n---\n\n"
        f"Another agent reviewed the same code and provided:\n---\n{review_a}\n---\n\n"
        f"Now provide your FINAL assessment. Consider both perspectives.\n"
        f"Identify any issues the other agent caught that you missed.\n"
        f"Resolve any disagreements with reasoning.\n\n"
        f"Original code/task:\n{prompt}"
    )

    try:
        future_cross_a = pool.submit(agent_a.ask, cross_prompt_a, effective_system)
        future_cross_b = pool.submit(agent_b.ask, cross_prompt_b, effective_system)

        final_a = future_cross_a.result(timeout=60)
        final_b = future_cross_b.result(timeout=60)
    except Exception as exc:
        pool.shutdown(wait=False, cancel_futures=True)
        logger.warning("Code review phase 2 failed: %s", exc)
        combined = f"## Agent A Review:\n{review_a}\n\n## Agent B Review:\n{review_b}"
        return combined, f"code-review({agent_a.id},{agent_b.id})"
    finally:
        pool.shutdown(wait=False)

    combined = (
        f"## Code Review: {agent_a.id} + {agent_b.id}\n\n"
        f"### Agent A ({agent_a.id}) Final Assessment:\n{final_a}\n\n"
        f"### Agent B ({agent_b.id}) Final Assessment:\n{final_b}\n"
    )

    return combined, f"code-review({agent_a.id},{agent_b.id})"


# ---------------------------------------------------------------------------
# ask_with_fallback — Primary API (backwards-compatible)
# ---------------------------------------------------------------------------

def ask_with_fallback(
    prompt: str,
    system_prompt: Optional[str] = None,
    provider_id: str = None,
    mode: str = "auto",
    return_usage: bool = False,
) -> Tuple[str, str]:
    """
    Smart multi-provider ask:
    - mode="code_review": 2-agent cross-review (4 API calls)
    - mode="senate": full N-provider Senate Committee
    - mode="auto" (default): code_review for 2 providers, senate for 3+
    - If provider_id specified -> try that one first, fallback to others
    - If 2+ providers configured -> collaborative consensus mode
    - If 1 provider -> single provider mode
    - If 0 providers -> error

    Returns (response_text, provider_name[, usage_dict]) tuple.
    If ``return_usage`` is True, returns a 3-tuple with a ``usage`` dict that
    follows the OpenAI shape (``prompt_tokens``, ``completion_tokens``,
    ``total_tokens``). When the underlying provider does not report token
    counts the dict is empty.
    """
    _sync_db_to_env()
    configured = [p for p in get_configured_providers() if not _is_circuit_open(getattr(p, "id", ""))]

    def _wrap(resp: str, name: str):
        if return_usage:
            return resp, name, {}
        return resp, name

    # Route to code review mode
    if mode == "code_review" and len(configured) >= 2:
        result = ask_code_review(
            prompt, system_prompt,
            [p.id for p in configured[:2]],
        )
        return _wrap(*result)

    # Route to code review in auto mode when exactly 2 providers (cheaper than full senate)
    if mode == "auto" and len(configured) == 2:
        result = ask_code_review(
            prompt, system_prompt,
            [p.id for p in configured[:2]],
        )
        return _wrap(*result)

    # Priority 1: User-specified provider
    if provider_id and provider_id != "auto":
        target = next((p for p in configured if getattr(p, "id", "") == provider_id or p.__class__.__name__.lower().startswith(provider_id.lower())), None)
        if not target:
            # Try to instantiate even if not in 'configured' (maybe it just needs env check)
            cls = PROVIDERS.get(provider_id)
            if cls:
                instance = cls()
                if instance.is_configured():
                    target = instance

        if target:
            try:
                return _wrap(*_ask_single(target, prompt, system_prompt))
            except Exception as e:
                _record_provider_failure(provider_id)
                logger.warning("Target provider %s failed, falling back: %s", provider_id, e)

    senate_enabled = os.environ.get("SENATE_ENABLED", "True").lower() == "true"
    if len(configured) >= 2 and senate_enabled:
        try:
            return _wrap(*ask_collaborative(prompt, system_prompt))
        except Exception as exc:
            logger.warning("ask_collaborative failed: %s", exc)

        # Committee phase failed across all members. Try a direct
        # sequential pass before giving up.
        for provider in configured:
            try:
                return _wrap(*_ask_single(provider, prompt, system_prompt))
            except Exception as exc:  # noqa: BLE001
                _record_provider_failure(getattr(provider, "id", ""))
                logger.warning("Committee rescue with %s failed: %s", provider.name(), exc)
        raise RuntimeError("All configured AI providers failed to respond.")

    if len(configured) == 1:
        provider = configured[0]
        try:
            return _wrap(*_ask_single(provider, prompt, system_prompt))
        except Exception as e: # pylint: disable=broad-exception-caught
            _record_provider_failure(getattr(provider, "id", ""))
            raise RuntimeError(f"AI provider {provider.name()} failed: {e}")

    raise RuntimeError("No AI providers configured. Add an API key in Settings > AI.")


def _cached_ask(
    prompt: str,
    system_prompt: str = None,
    provider_id: str = None,
    ttl: int = 600,  # 10 minutes
    cache_bypass: bool = False,
    mode: str = "auto",
    return_usage: bool = False,
) -> tuple:
    """Wrapper around ask_with_fallback that caches responses in Redis.

    Args:
        prompt: The user prompt
        system_prompt: Optional system prompt
        provider_id: Optional provider to use
        ttl: Cache TTL in seconds (default 600 = 10 minutes)
        cache_bypass: If True, skip cache (for chat/conversation endpoints)
        mode: "auto", "code_review", or "senate"
        return_usage: If True, return (response, provider, usage) instead of (response, provider)

    Returns:
        (response_text, provider_name[, usage_dict]) tuple
    """
    if cache_bypass:
        return ask_with_fallback(
            prompt, system_prompt, provider_id, mode=mode, return_usage=return_usage,
        )

    # Build cache key from prompt content
    cache_input = f"{system_prompt or ''}:{prompt}"
    cache_hash = hashlib.sha256(cache_input.encode()).hexdigest()[:20]
    cache_key = f"ai:response:{cache_hash}"

    # Check cache
    cached = cache.get(cache_key)
    if cached is not None:
        if return_usage and len(cached) == 2:
            return cached[0], cached[1], {}
        return cached

    # Cache miss — call provider
    result = ask_with_fallback(
        prompt, system_prompt, provider_id, mode=mode, return_usage=return_usage,
    )

    # Cache the result (cache the 2-tuple to preserve existing on-disk shape)
    if return_usage and len(result) == 3:
        cache.set(cache_key, (result[0], result[1]), timeout=ttl)
    else:
        cache.set(cache_key, result, timeout=ttl)

    return result
