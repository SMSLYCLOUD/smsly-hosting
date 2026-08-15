import concurrent.futures
import os

from django.core.cache import cache

from .base import AIProvider, _is_circuit_open, _record_provider_failure, logger
from .sync import _sync_db_to_env, BALANCE_CACHE_TTL_SECONDS, BALANCE_FETCH_BUDGET_SECONDS
from .openai import OpenAIProvider
from .grok import GrokProvider
from .gemini import GeminiProvider
from .claude import ClaudeProvider
from .openrouter import OpenRouterProvider
from .groq import GroqProvider
from .alibaba import AlibabaProvider
from .deepseek import DeepSeekProvider
from .jules import JulesProvider
from .localllm import LocalLLMProvider
from .smsly_cloud import SMSLYCloudProvider
from .free_model import FreeModelProvider
from .opencode import OpenCodeProvider
from .mistral import MistralProvider
from .nvidia_nim import NvidiaNimProvider
from .cloudflare import CloudflareProvider
from .kimi import KimiProvider
from .orcarouter import OrcaRouterProvider
from .zenmax import ZenMaxProvider
from .agentrouter import AgentRouterProvider
from .mock import MockProvider


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
    "kimi": KimiProvider,
    "orcarouter": OrcaRouterProvider,
    "zenmax": ZenMaxProvider,
    "agentrouter": AgentRouterProvider,
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
    "kimi": "KIMI_API_KEY",
    "orcarouter": "ORCAROUTER_API_KEY",
    "zenmax": "ZENMAX_API_KEY",
    "agentrouter": "AGENTROUTER_API_KEY",
}


def get_available_providers(include_balance: bool = False) -> list[dict]:
    """Return list of all providers with connection status and optional balance."""
    _sync_db_to_env()
    result: list = []
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

        provider_instance: AIProvider | None = provider_instances.get(provider_id)
        if provider_instance is not None:
            pending[provider_id] = provider_instance

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
                    except Exception as exc:
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


def get_configured_providers() -> list[AIProvider]:
    """Return all providers that have valid API keys configured."""
    _sync_db_to_env()
    configured = []
    for key, cls in PROVIDERS.items():
        if key == "mock":
            continue

        if key == "localllm":
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


def _resolve_providers(provider_ids: list[str]) -> list[AIProvider]:
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
