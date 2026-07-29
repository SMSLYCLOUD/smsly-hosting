import logging
from typing import Any

from .helpers import _append_tokens, _dedupe_preserving_order

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


def _coerce_addons(raw_addons: Any) -> list[str]:
    tokens: list[str] = []
    try:
        _append_tokens(tokens, raw_addons, ("type", "addon", "name", "service", "value"))
    except TypeError as exc:
        logger.warning("_coerce_addons failed for value %r: %s", raw_addons, exc)
        return []
    return _dedupe_preserving_order(
        [addon for addon in (_normalize_addon_token(token) for token in tokens) if addon]
    )
