import os

from django.core.cache import cache

from .base import _sanitize_api_key, _normalize_model, logger

SYSTEM_PROMPT = (
    "You are the SMSLY Cloud AI Assistant - an expert in cloud deployments, Docker, "
    "Nixpacks, server infrastructure, and DevOps. You help users deploy, debug, and "
    "optimize their applications on SMSLY Hosting. Be concise, precise, and actionable. "
    "Format responses in markdown. Never reveal internal system details or API keys."
)

BALANCE_CACHE_TTL_SECONDS = int(os.environ.get("AI_BALANCE_CACHE_TTL_SECONDS", "60") or "60")
BALANCE_FETCH_BUDGET_SECONDS = int(os.environ.get("AI_BALANCE_FETCH_BUDGET_SECONDS", "8") or "8")


def _get_db_settings():
    """
    Best-effort DB-backed settings lookup.
    Fails open because it can be called early during boot/migrations.
    """
    try:
        from django.apps import apps
        from django.db import connection
        from django.db.utils import OperationalError, ProgrammingError

        model = apps.get_model("intelligence", "AIProviderSettings")
        if model is None:
            return None
        try:
            return model.get_solo()
        except (OperationalError, ProgrammingError) as e:
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

                known_fields = [
                    "openai_api_key", "openai_model",
                    "grok_api_key", "grok_model",
                    "gemini_api_key", "gemini_model",
                    "claude_api_key", "claude_model",
                    "openrouter_api_key", "openrouter_model",
                    "groq_api_key", "groq_model",
                    "alibaba_api_key", "alibaba_model",
                    "deepseek_api_key", "deepseek_model",
                    "jules_api_key", "jules_model",
                    "localllm_api_key", "localllm_model", "localllm_base_url",
                    "smslycloud_api_key", "smslycloud_model",
                    "freemodel_api_key", "freemodel_model", "freemodel_base_url",
                    "opencode_api_key", "opencode_model", "opencode_base_url",
                    "mistral_api_key", "mistral_model", "mistral_base_url",
                    "nvidia_api_key", "nvidia_model", "nvidia_base_url",
                    "cloudflare_api_key", "cloudflare_model", "cloudflare_base_url",
                    "kimi_api_key", "kimi_model", "kimi_base_url",
                    "orcarouter_api_key", "orcarouter_model", "orcarouter_base_url",
                    "zenmax_api_key", "zenmax_model", "zenmax_base_url",
                    "agentrouter_api_key", "agentrouter_model", "agentrouter_base_url",
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

                class _PartialSettings:
                    pass
                obj = _PartialSettings()
                for i, field_name in enumerate(available):
                    setattr(obj, field_name, row[i])
                for f in known_fields:
                    if f not in columns:
                        setattr(obj, f, None)
                return obj
            except Exception as inner_e:
                logger.debug("Raw SQL fallback also failed: %s", inner_e)
                return None
    except Exception as e:
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
        ("kimi_api_key", "KIMI_API_KEY", ""),
        ("kimi_model", "KIMI_MODEL", "kimi-latest"),
        ("kimi_base_url", "KIMI_BASE_URL", "https://api.moonshot.ai/v1"),
        ("orcarouter_api_key", "ORCAROUTER_API_KEY", ""),
        ("orcarouter_model", "ORCAROUTER_MODEL", "orcarouter/auto"),
        ("orcarouter_base_url", "ORCAROUTER_BASE_URL", ""),
        ("zenmax_api_key", "ZENMAX_API_KEY", ""),
        ("zenmax_model", "ZENMAX_MODEL", "zenmax/auto"),
        ("zenmax_base_url", "ZENMAX_BASE_URL", ""),
        ("agentrouter_api_key", "AGENTROUTER_API_KEY", ""),
        ("agentrouter_model", "AGENTROUTER_MODEL", "agentrouter/auto"),
        ("agentrouter_base_url", "AGENTROUTER_BASE_URL", ""),
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
