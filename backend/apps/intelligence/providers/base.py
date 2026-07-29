"""
AI Provider abstraction for SMSLY Hosting.

Supports OpenAI, Grok (xAI), Google Gemini, Claude (Anthropic), and Jules.
All providers work together when multiple keys are configured:
- 1 provider: uses it solo
- 2+ providers: collaborative consensus mode
- 0 providers: mock fallback
"""
import logging
import threading
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Generator
from datetime import datetime, timedelta
from functools import wraps

import httpx

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
        _provider_circuit_open_until.pop(provider_id, None)
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


def _sanitize_api_key(raw: str | None) -> str:
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


def _normalize_model(raw: str | None, default: str) -> str:
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
    except Exception:
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
    def ask(self, prompt: str, system_prompt: str | None = None) -> str:
        """Send a prompt and return the AI response text."""

    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name."""

    def is_configured(self) -> bool:
        """Check if this provider has a valid API key."""
        return bool(getattr(self, 'api_key', ''))

    def ask_stream(self, prompt: str, system_prompt: str | None = None) -> Generator[str, None, None]:
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
