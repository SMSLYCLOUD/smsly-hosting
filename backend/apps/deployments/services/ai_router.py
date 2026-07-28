"""Helpers for SMSLY-managed LiteLLM / AI Router services."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass

import yaml

from apps.deployments.models import EnvironmentVariable, Service  # type: ignore[attr-defined]  # models re-exports from submodules

AI_ROUTER_IMAGE = "ghcr.io/berriai/litellm"
OLLAMA_IMAGE = "ollama/ollama"
DEFAULT_AI_ROUTER_API_BASE = "/api/v1"
DEFAULT_AI_ROUTER_UI_BASE = "/"
DEFAULT_BRAID_ALIAS = "braid-llm"


@dataclass
class OllamaTarget:
    """Normalized Ollama deployment candidate for an AI router."""

    service_id: str
    service_name: str
    public_domain: str
    model: str
    alias: str
    api_base: str
    mode: str
    selected: bool
    min_ram_gb: int | None = None
    has_enough_ram: bool = True


def _required_ram_gb_for_model(model_name: str) -> int | None:
    """
    Best-effort RAM floor for common Ollama models.

    This protects the AI router from auto-selecting backends that are known to
    OOM under typical quant defaults, which manifests as 5xx from LiteLLM.
    """

    key = str(model_name or "").strip().lower()
    if not key:
        return None

    # Keep this list small + conservative; templates can still override by
    # explicitly selecting targets via AI_ROUTER_SELECTED_SERVICE_IDS.
    known: dict[str, int] = {
        "phi3": 4,
        "nomic-embed-text": 2,
        "qwen2.5": 8,
        "llama3.1": 8,
        "deepseek-r1": 8,
        "mixtral": 24,
    }
    return known.get(key)


def _env_map(service: Service) -> dict[str, str]:
    return {
        str(env.key or "").upper(): str(env.value or "")
        for env in service.env_vars.all()
    }


def _latest_status(service: Service) -> str:
    latest = service.deployments.order_by("-created_at").only("status").first()
    return str(getattr(latest, "status", "") or "").upper()


def is_ai_router_service(service: Service) -> bool:
    image = str(service.docker_image or "").lower()
    return AI_ROUTER_IMAGE in image or service.name.startswith("ai-router")


def is_ollama_service(service: Service) -> bool:
    image = str(service.docker_image or "").lower()
    return image.startswith(OLLAMA_IMAGE) or service.name.startswith("ollama-") or service.name.startswith("llama-")


def is_embedding_model(model_name: str) -> bool:
    return "embed" in str(model_name or "").lower()


def get_ollama_model_name(service: Service) -> str:
    env = _env_map(service)
    return str(env.get("OLLAMA_MODEL", "")).strip()


def _selected_service_ids_from_env(service: Service) -> set[str]:
    env = _env_map(service)
    raw = str(env.get("AI_ROUTER_SELECTED_SERVICE_IDS", "")).strip()
    if not raw:
        return set()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return {str(item).strip() for item in parsed if str(item).strip()}
    except json.JSONDecodeError:
        pass
    return {part.strip() for part in raw.split(",") if part.strip()}


def _target_from_service(service: Service, selected_ids: set[str]) -> OllamaTarget | None:
    model = get_ollama_model_name(service)
    if not model:
        return None

    port = int(service.internal_port or 11434)
    alias = f"ollama/{model}"
    required_ram_gb = _required_ram_gb_for_model(model)
    try:
        svc_mem_mb = int(service.memory_mb or 0)
    except (TypeError, ValueError):
        svc_mem_mb = 0
    has_enough_ram = True
    if required_ram_gb is not None:
        has_enough_ram = svc_mem_mb >= required_ram_gb * 1024

    # If the user has never explicitly chosen models for this router,
    # auto-disable targets that are likely to OOM.
    auto_selected = (str(service.id) in selected_ids) if selected_ids else True
    selected = auto_selected if selected_ids else auto_selected and has_enough_ram

    return OllamaTarget(
        service_id=str(service.id),
        service_name=service.name,
        public_domain=service.public_domain or "",
        model=model,
        alias=alias,
        api_base=f"http://{service.name}:{port}",
        mode="embedding" if is_embedding_model(model) else "chat",
        selected=selected,
        min_ram_gb=required_ram_gb,
        has_enough_ram=has_enough_ram,
    )


def discover_ai_router_targets(service: Service) -> list[OllamaTarget]:
    """
    Detect Ollama services that should be available to this router.

    Preference order:
    - same owner
    - same project when available
    - ACTIVE deployments only
    """

    query = (
        Service.objects
        .filter(owner=service.owner, is_preview=False)
        .exclude(id=service.id)
        .prefetch_related("env_vars", "deployments")
        .order_by("name")
    )
    if service.project_id:
        query = query.filter(project_id=service.project_id)

    selected_ids = _selected_service_ids_from_env(service)
    targets: list[OllamaTarget] = []

    for candidate in query:
        if not is_ollama_service(candidate):
            continue

        # Auto-mapping: if it's explicitly selected via env, always include it
        # (LiteLLM will auto-retry connecting when it boots).
        # Otherwise, only include it if it's currently ACTIVE.
        if _latest_status(candidate) != "ACTIVE" and str(candidate.id) not in selected_ids:
            continue

        target = _target_from_service(candidate, selected_ids)
        if target is not None:
            targets.append(target)

    return targets


def generate_ai_router_proxy_config(service: Service) -> str:
    """Generate a LiteLLM proxy YAML with discovered Ollama backends."""

    env = _env_map(service)
    braid_enabled = str(env.get("AI_ROUTER_BRAID_ENABLED", "true")).strip().lower() in {"1", "true", "yes", "on"}
    braid_alias = str(env.get("AI_ROUTER_BRAID_ALIAS", DEFAULT_BRAID_ALIAS)).strip() or DEFAULT_BRAID_ALIAS

    targets = [target for target in discover_ai_router_targets(service) if target.selected]

    model_list: list[dict] = []
    braid_targets: list[OllamaTarget] = []

    for target in targets:
        item: dict[str, object] = {
            "model_name": target.alias,
            "litellm_params": {
                "model": target.alias,
                "api_base": target.api_base,
            },
        }
        if target.mode == "embedding":
            item["model_info"] = {
                "mode": "embedding",
                "base_model": target.model,
            }
        else:
            braid_targets.append(target)
        model_list.append(item)

    if braid_enabled:
        for target in braid_targets:
            model_list.append({
                "model_name": braid_alias,
                "litellm_params": {
                    "model": target.alias,
                    "api_base": target.api_base,
                },
            })

    payload = {
        "model_list": model_list,
        "litellm_settings": {
            "drop_params": True,
            "telemetry": False,
            "num_retries": 2,
            "request_timeout": 300,
        },
        "router_settings": {
            "routing_strategy": "latency-based-routing",
            "routing_strategy_args": {
                "ttl": 300
            }
        },
    }
    return yaml.safe_dump(payload, sort_keys=False)


def serialize_ai_router_config(service: Service) -> dict:
    env = _env_map(service)
    targets = discover_ai_router_targets(service)
    selected_targets = [asdict(target) for target in targets if target.selected]
    return {
        "service_id": str(service.id),
        "api_base": str(env.get("AI_ROUTER_API_BASE", DEFAULT_AI_ROUTER_API_BASE)).strip() or DEFAULT_AI_ROUTER_API_BASE,
        "ui_base": str(env.get("AI_ROUTER_UI_BASE", DEFAULT_AI_ROUTER_UI_BASE)).strip() or DEFAULT_AI_ROUTER_UI_BASE,
        "braid_alias": str(env.get("AI_ROUTER_BRAID_ALIAS", DEFAULT_BRAID_ALIAS)).strip() or DEFAULT_BRAID_ALIAS,
        "braid_enabled": str(env.get("AI_ROUTER_BRAID_ENABLED", "true")).strip().lower() in {"1", "true", "yes", "on"},
        "selected_service_ids": [target["service_id"] for target in selected_targets],
        "detected_models": [asdict(target) for target in targets],
        "config_preview": generate_ai_router_proxy_config(service),
    }


def persist_ai_router_config(
    service: Service,
    *,
    selected_service_ids: Iterable[str],
    api_base: str = DEFAULT_AI_ROUTER_API_BASE,
    ui_base: str = DEFAULT_AI_ROUTER_UI_BASE,
    braid_alias: str = DEFAULT_BRAID_ALIAS,
    braid_enabled: bool = True,
) -> None:
    """Persist router settings as service env vars for future deployments."""

    values = {
        "AI_ROUTER_API_BASE": api_base or DEFAULT_AI_ROUTER_API_BASE,
        "AI_ROUTER_UI_BASE": ui_base or DEFAULT_AI_ROUTER_UI_BASE,
        "AI_ROUTER_SELECTED_SERVICE_IDS": json.dumps(
            [str(item).strip() for item in selected_service_ids if str(item).strip()]
        ),
        "AI_ROUTER_BRAID_ALIAS": braid_alias or DEFAULT_BRAID_ALIAS,
        "AI_ROUTER_BRAID_ENABLED": "true" if braid_enabled else "false",
        "AI_ROUTER_AUTO_DISCOVER_MODELS": "true",
    }
    for key, value in values.items():
        EnvironmentVariable.objects.update_or_create(
            service=service,
            key=key,
            defaults={
                "value": value,
                "is_secret": False,
                "source": "USER",
            },
        )
