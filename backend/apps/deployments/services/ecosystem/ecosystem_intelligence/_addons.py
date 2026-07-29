import logging
from typing import Any

from ._normalization import _coerce_addons, _coerce_depends_on
from ._utils import _repo_short_name

logger = logging.getLogger(__name__)

_ADDON_ALIASES = {
    "POSTGRESQL": "POSTGRES",
    "POSTGRES_DB": "POSTGRES",
    "POSTGRES_DATABASE": "POSTGRES",
    "DATABASE": "POSTGRES",
    "DB": "POSTGRES",
    "CACHE": "REDIS",
    "REDIS_CACHE": "REDIS",
    "MONGO": "MONGODB",
    "RABBIT": "RABBITMQ",
    "AMQP": "RABBITMQ",
    "VECTOR": "QDRANT",
    "VECTOR_DB": "QDRANT",
    "S3": "MINIO",
    "OBJECT_STORAGE": "MINIO",
}


def _normalize_addon_token(token: str) -> str:
    normalized = str(token or "").strip().upper().replace("-", "_").replace(" ", "_")
    normalized = _ADDON_ALIASES.get(normalized, normalized)
    return normalized if normalized else ""


def _rebuild_addons_manifest(services: list[dict], existing_addons: Any) -> list[dict]:
    """Rebuild addon shared_by map from service-level addon declarations."""
    addon_map: dict[str, set] = {}

    if isinstance(existing_addons, list):
        for addon in existing_addons:
            if not isinstance(addon, dict):
                continue
            addon_types = _coerce_addons(addon)
            addon_type = addon_types[0] if addon_types else ""
            if not addon_type:
                continue
            addon_map.setdefault(addon_type, set())
            for svc_name in _coerce_depends_on(addon.get("shared_by", []) or []):
                svc_text = str(svc_name or "").strip()
                if svc_text:
                    try:
                        addon_map[addon_type].add(svc_text)
                    except TypeError:
                        logger.warning("Unhashable svc_text for addon %r: %r", addon_type, svc_text)

    for service in services:
        if not isinstance(service, dict) or service.get("skip"):
            continue
        service_name = str(service.get("name") or _repo_short_name(service)).strip()
        if not service_name:
            continue
        normalized_addons = _coerce_addons(service.get("addons", []) or [])
        service["addons"] = normalized_addons
        for addon_type in normalized_addons:
            if not addon_type:
                continue
            try:
                str_addon_type = str(addon_type)
                str_service_name = str(service_name)
                addon_map.setdefault(str_addon_type, set()).add(str_service_name)
            except TypeError as e:
                logger.warning("Unhashable addon_type or service_name: %r / %r - %s", addon_type, service_name, e)
            except Exception as e:
                logger.warning("Unexpected error processing addon {0} for service {1}: {2}", addon_type, service_name, e)

    try:
        return [
            {"type": addon_type, "shared_by": sorted(shared_by)}
            for addon_type, shared_by in sorted(addon_map.items())
        ]
    except TypeError as exc:
        logger.warning("Unhashable key in addon_map: %s", exc)
        return []
