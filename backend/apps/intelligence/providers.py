"""
AI Provider abstraction for SMSLY Hosting.

Supports OpenAI, Grok (xAI), Google Gemini, and Claude (Anthropic).
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
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    def name(self) -> str:
        return f"OpenAI ({self.model})"

    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("[OpenAI] API key not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        with httpx.Client(timeout=60) as client:
            resp = client.post(
                f"{self.BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self.model, "messages": messages, "max_tokens": 2048},
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

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
                        "raw": {"total_granted": total, "total_used": used, "remaining": remaining},
                    }
                # Fallback: try organization billing
                resp2 = client.get(
                    "https://api.openai.com/v1/organization/costs",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    params={"limit": 1},
                )
                if resp2.status_code == 200:
                    return {"balance": "Active (usage-based)", "currency": "USD", "raw": resp2.json()}
                return {"balance": "Active (check platform.openai.com)", "currency": "USD", "raw": {}}
        except Exception as e:
            logger.debug("OpenAI balance check failed: %s", e)
            return {"balance": "Error checking", "currency": "", "raw": {}}


# ---------------------------------------------------------------------------
# Grok (xAI) Provider — OpenAI-compatible API
# ---------------------------------------------------------------------------

class GrokProvider(AIProvider):
    """xAI Grok provider — model configurable via GROK_MODEL env var."""

    BASE_URL = "https://api.x.ai/v1"

    def __init__(self):
        self.api_key = os.environ.get("GROK_API_KEY", "")
        self.model = os.environ.get("GROK_MODEL", "grok-3-mini")

    def name(self) -> str:
        return f"Grok ({self.model})"

    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("[Grok] API key not configured.")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        with httpx.Client(timeout=60) as client:
            resp = client.post(
                f"{self.BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": self.model, "messages": messages, "max_tokens": 2048},
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

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
                return {"balance": "Active (check console.x.ai)", "currency": "USD", "raw": {}}
        except Exception as e:
            logger.debug("Grok balance check failed: %s", e)
            return {"balance": "Error checking", "currency": "", "raw": {}}


# ---------------------------------------------------------------------------
# Google Gemini Provider
# ---------------------------------------------------------------------------

class GeminiProvider(AIProvider):
    """Google Gemini provider — model configurable via GEMINI_MODEL env var."""

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self):
        self.api_key = os.environ.get("GEMINI_API_KEY", "")
        self.model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

    def name(self) -> str:
        return f"Gemini ({self.model})"

    def ask(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError("[Gemini] API key not configured.")

        contents = []
        if system_prompt:
            contents.append({"role": "user", "parts": [{"text": system_prompt}]})
            contents.append({"role": "model", "parts": [{"text": "Understood."}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        url = f"{self.BASE_URL}/models/{self.model}:generateContent?key={self.api_key}"
        with httpx.Client(timeout=60) as client:
            resp = client.post(
                url,
                headers={"Content-Type": "application/json"},
                json={"contents": contents},
            )
            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]

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
                return {"balance": "Unknown (check aistudio.google.com)", "currency": "", "raw": {}}
        except Exception as e:
            logger.debug("Gemini balance check failed: %s", e)
            return {"balance": "Error checking", "currency": "", "raw": {}}


# ---------------------------------------------------------------------------
# Claude (Anthropic) Provider
# ---------------------------------------------------------------------------

class ClaudeProvider(AIProvider):
    """Anthropic Claude provider — model configurable via CLAUDE_MODEL env var."""

    BASE_URL = "https://api.anthropic.com/v1"

    def __init__(self):
        self.api_key = os.environ.get("CLAUDE_API_KEY", "")
        self.model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-20250514")

    def name(self) -> str:
        return f"Claude ({self.model})"

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
                    return {"balance": "Active (check console.anthropic.com)", "currency": "USD", "raw": {}}
                if resp.status_code == 401:
                    return {"balance": "❌ Invalid API key", "currency": "", "raw": {}}
                if resp.status_code == 429:
                    return {"balance": "⚠️ Rate limited / credits low", "currency": "USD", "raw": {}}
                return {"balance": f"Status {resp.status_code}", "currency": "", "raw": {}}
        except Exception as e:
            logger.debug("Claude balance check failed: %s", e)
            return {"balance": "Error checking", "currency": "", "raw": {}}


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
# Provider Registry
# ---------------------------------------------------------------------------

PROVIDERS = {
    "openai": OpenAIProvider,
    "grok": GrokProvider,
    "gemini": GeminiProvider,
    "claude": ClaudeProvider,
    "mock": MockProvider,
}

ENV_KEY_MAP = {
    "openai": "OPENAI_API_KEY",
    "grok": "GROK_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "claude": "CLAUDE_API_KEY",
}

SYSTEM_PROMPT = (
    "You are the SMSLY Cloud AI Assistant - an expert in cloud deployments, Docker, "
    "Nixpacks, server infrastructure, and DevOps. You help users deploy, debug, and "
    "optimize their applications on SMSLY Hosting. Be concise, precise, and actionable. "
    "Format responses in markdown. Never reveal internal system details or API keys."
)


# ---------------------------------------------------------------------------
# DB Settings Helper
# ---------------------------------------------------------------------------

def _get_db_settings():
    """
    Best-effort DB-backed settings lookup.
    Fails open because it can be called early during boot/migrations.
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


def _effective_env_value(db_value: str | None, env_key: str, default: str = "") -> str:
    if db_value:
        return str(db_value)
    return os.environ.get(env_key, default) or default


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
    ]
    for attr, env_key, default in pairs:
        val = _effective_env_value(getattr(cfg, attr, None), env_key, default)
        if val:
            os.environ[env_key] = val


# ---------------------------------------------------------------------------
# Provider Discovery
# ---------------------------------------------------------------------------

def get_available_providers(include_balance: bool = False) -> List[dict]:
    """Return list of all providers with connection status and optional balance."""
    _sync_db_to_env()
    result = []
    for key, cls in PROVIDERS.items():
        if key == "mock":
            continue
        instance = cls()
        info = {
            "id": key,
            "name": instance.name(),
            "configured": instance.is_configured(),
            "model": getattr(instance, 'model', ''),
        }
        if include_balance and instance.is_configured():
            info["balance"] = instance.get_balance()
        elif include_balance:
            info["balance"] = {"balance": "Not configured", "currency": "", "raw": {}}
        result.append(info)
    return result


def get_configured_providers() -> List[AIProvider]:
    """Return all providers that have valid API keys configured."""
    _sync_db_to_env()
    configured = []
    for key, cls in PROVIDERS.items():
        if key == "mock":
            continue
        instance = cls()
        if instance.is_configured():
            configured.append(instance)
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

def _ask_single(provider: AIProvider, prompt: str, system_prompt: Optional[str] = None) -> Tuple[str, str]:
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
            except Exception as e:
                logger.warning("Provider %s failed: %s", provider.name(), e)

    return results


def ask_collaborative(prompt: str, system_prompt: Optional[str] = None) -> Tuple[str, str]:
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
        except Exception as e:
            logger.warning("Single provider %s failed: %s", provider.name(), e)
            mock = MockProvider()
            return mock.ask(prompt, system_prompt=system_prompt), f"Mock AI ({provider.name()} failed)"

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PHASE 1 — PROPOSE: Each provider answers independently
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    logger.info("Senate Committee convened with %d members", len(configured))
    proposals = _parallel_ask(configured, prompt, system_prompt or COMMITTEE_SYSTEM_PROMPT)

    if not proposals:
        mock = MockProvider()
        return mock.ask(prompt, system_prompt=system_prompt), f"Mock AI (all {len(configured)} senators failed)"

    if len(proposals) == 1:
        return proposals[0]

    member_names = [name for _, name in proposals]
    logger.info("Phase 1 complete: %d proposals received from %s", len(proposals), ", ".join(member_names))

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
    except Exception as e:
        logger.warning("Chair %s failed to deliver resolution: %s", chair.name(), e)
        # Fallback: try another provider as chair
        for fallback_chair in configured:
            if fallback_chair is not chair:
                try:
                    resolution = fallback_chair.ask(chair_prompt, system_prompt=COMMITTEE_SYSTEM_PROMPT)
                    attribution = f"Senate Committee ({' + '.join(member_names)})"
                    return resolution, attribution
                except Exception:
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

    if len(configured) >= 2:
        return ask_collaborative(prompt, system_prompt)

    if len(configured) == 1:
        provider = configured[0]
        try:
            return _ask_single(provider, prompt, system_prompt)
        except Exception as e:
            logger.warning("Provider %s failed, falling back to mock: %s", provider.name(), e)
            mock = MockProvider()
            return mock.ask(prompt, system_prompt=system_prompt), f"Mock AI ({provider.name()} failed)"

    # No providers configured
    mock = MockProvider()
    return mock.ask(prompt, system_prompt=system_prompt), mock.name()


