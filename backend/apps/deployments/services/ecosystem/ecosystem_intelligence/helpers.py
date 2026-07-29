import logging
from typing import Any

logger = logging.getLogger(__name__)


def _safe_order(value: Any, default: int = 99) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _repo_short_name(service: dict) -> str:
    repo = str(service.get("repo") or "").strip().rstrip("/")
    if repo:
        short_name = repo.split("/")[-1]
        if short_name.endswith(".git"):
            short_name = short_name[:-4]
        if short_name:
            return short_name
    return "service"


def _append_tokens(tokens: list[str], raw: Any, preferred_keys: tuple[str, ...]) -> None:
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


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen = set()
    deduped: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _coerce_depends_on(raw_depends: Any) -> list[str]:
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


def _normalize_service_plan_fields(service: dict) -> None:
    from ..ecosystem_heuristics import _env_plan_map
    from .addons import _coerce_addons

    service["env_vars"] = _env_plan_map(service.get("env_vars", {}))
    service["addons"] = _coerce_addons(service.get("addons", []))
    service["depends_on"] = _coerce_depends_on(service.get("depends_on", []))
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
    build_val = str(service.get("build") or "").strip().lower()
    if not build_val or build_val in ("none", "null"):
        build_val = "dockerfile"
    service["build"] = build_val
