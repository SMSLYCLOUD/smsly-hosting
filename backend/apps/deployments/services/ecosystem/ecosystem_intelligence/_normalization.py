import logging
from typing import Any

from ._utils import _dedupe_preserving_order

logger = logging.getLogger(__name__)


def _append_tokens(tokens: list[str], raw: Any, preferred_keys: tuple[str, ...]) -> None:
    """Extract string tokens from flexible AI-generated scalar/list/dict shapes."""
    if raw is None:
        return

    if isinstance(raw, dict):
        for key in preferred_keys:
            if key in raw:
                before = len(tokens)
                _append_tokens(tokens, raw.get(key), preferred_keys)
                if len(tokens) > before:
                    return

        for key, value in raw.items():
            if isinstance(value, bool):
                if value:
                    _append_tokens(tokens, key, preferred_keys)
            elif isinstance(value, (str, int, float, list, tuple, set, dict)):
                _append_tokens(tokens, value, preferred_keys)
        return

    if isinstance(raw, (list, tuple, set)):
        for item in raw:
            _append_tokens(tokens, item, preferred_keys)
        return

    text = str(raw).strip()
    if not text or text.lower() in {"none", "null", "false"}:
        return
    if "," in text:
        for part in text.split(","):
            _append_tokens(tokens, part, preferred_keys)
        return
    tokens.append(text)


def _coerce_depends_on(raw_depends: Any) -> list[str]:
    """Normalize depends_on payload to a flat list."""
    tokens: list[str] = []
    try:
        _append_tokens(
            tokens,
            raw_depends,
            ("repo", "service", "service_name", "name", "target", "id", "value"),
        )
    except TypeError as exc:
        logger.warning("_coerce_depends_on failed for value %r: %s", raw_depends, exc)
        return []
    return _dedupe_preserving_order(tokens)


def _coerce_addons(raw_addons: Any) -> list[str]:
    """Normalize addon declarations to a deduped list of addon type strings."""
    from ._addons import _normalize_addon_token

    tokens: list[str] = []
    try:
        _append_tokens(tokens, raw_addons, ("type", "addon", "name", "service", "value"))
    except TypeError as exc:
        logger.warning("_coerce_addons failed for value %r: %s", raw_addons, exc)
        return []
    return _dedupe_preserving_order(
        [addon for addon in (_normalize_addon_token(token) for token in tokens) if addon]
    )


def _normalize_service_plan_fields(service: dict) -> None:
    """Normalize untrusted AI service fields before planning logic consumes them."""
    from ..ecosystem_heuristics import _env_plan_map

    service["env_vars"] = _env_plan_map(service.get("env_vars", {}))
    service["addons"] = _coerce_addons(service.get("addons", []))
    service["depends_on"] = _coerce_depends_on(service.get("depends_on", []))
    # Normalize port — AI sometimes sends null or omits it
    try:
        port_val = service.get("port")
        if port_val is None:
            port_val = 3000
        else:
            port_val = int(port_val)
        port_val = max(1, min(65535, port_val))
    except (TypeError, ValueError):
        port_val = 3000
    service["port"] = port_val
    # Normalize build — null/empty defaults to dockerfile strategy
    build_val = str(service.get("build") or "").strip().lower()
    if not build_val or build_val in ("none", "null"):
        build_val = "dockerfile"
    service["build"] = build_val
