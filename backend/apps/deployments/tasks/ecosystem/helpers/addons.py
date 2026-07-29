import logging

from apps.deployments.tasks.ecosystem.constants import (
    _ADDON_ENV_ALIASES,
)

from .repo import (
    _canonical_repo_ref,
    _repo_short_name,
    _looks_like_smsly_core_name,
    _repo_slug_from_url,
    _slugify_name,
)

logger = logging.getLogger(__name__)


def _coerce_addon_type(raw) -> str:
    """Normalize addon entries from service or top-level plan payloads."""
    if isinstance(raw, dict):
        for key in ("type", "addon", "name", "service", "value"):
            value = raw.get(key)
            if value:
                return str(value).strip().upper()
        return ""
    return str(raw or "").strip().upper()


def _addon_env_key_map() -> dict[str, str]:
    """Return addon type to primary connection env key mapping."""
    try:
        from apps.addons.services.addon_provisioner import AddonProvisioner
        return dict(AddonProvisioner.ENV_KEY_MAP)
    except Exception:
        return {
            "POSTGRES": "DATABASE_URL",
            "REDIS": "REDIS_URL",
            "MYSQL": "MYSQL_URL",
            "MONGODB": "MONGODB_URI",
            "QDRANT": "QDRANT_URL",
            "ELASTICSEARCH": "ELASTICSEARCH_URL",
            "RABBITMQ": "RABBITMQ_URL",
            "MINIO": "MINIO_URL",
        }


def _addon_env_keys(addon_type: str) -> tuple[str, ...]:
    """Return accepted env keys for an addon type."""
    addon_type = str(addon_type or "").strip().upper()
    primary = _addon_env_key_map().get(addon_type, f"{addon_type}_URL")
    aliases = _ADDON_ENV_ALIASES.get(addon_type, ())
    keys = [primary, *aliases]
    return tuple(dict.fromkeys(k for k in keys if k))


def _addon_type_from_placeholder(token: str) -> str:
    """Map {{FOO_URL}} style placeholders back to addon types."""
    token = str(token or "").strip().upper()
    if token in {"DATABASE_URL", "POSTGRES_URL"}:
        return "POSTGRES"
    if token in {"CACHE_URL", "REDIS_URL"}:
        return "REDIS"

    addon_map = _addon_env_key_map()
    for addon_type, env_key in addon_map.items():
        if token == str(env_key or "").upper():
            return addon_type

    candidate = ""
    if token.endswith("_URL") or token.endswith("_URI"):
        candidate = token[:-4]

    if candidate in addon_map:
        return candidate

    return ""


def _plan_addon_types(plan_addons) -> set[str]:
    """Collect addon types declared at the top level."""
    if not isinstance(plan_addons, list):
        return set()
    return {addon for addon in (_coerce_addon_type(item) for item in plan_addons) if addon}


def _inject_addon_env_defaults(
    resolved_env: dict[str, str],
    addon_types: set[str],
    provisioned_addon_urls: dict[str, str],
) -> None:
    """Populate standard addon URL env vars when a service requests an addon."""
    for addon_type in sorted(addon_types):
        url = provisioned_addon_urls.get(addon_type)
        if not url:
            continue
        for env_key in _addon_env_keys(addon_type):
            resolved_env.setdefault(env_key, url)


def _select_shared_addon_anchor(services: list):
    """
    Choose the best service to host shared ecosystem addons.

    Prefer SMSLY core when present; otherwise use the first created service.
    """
    if not services:
        return None

    for service in services:
        name = getattr(service, "name", "")
        repo_url = getattr(service, "repository_url", "")
        if _looks_like_smsly_core_name(name) or _looks_like_smsly_core_name(_repo_slug_from_url(repo_url)):
            return service

    return services[0]
