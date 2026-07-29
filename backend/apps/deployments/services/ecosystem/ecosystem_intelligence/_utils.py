from typing import Any


def _safe_order(value: Any, default: int = 99) -> int:
    """Best-effort int parser for deploy_order values."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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


def _repo_short_name(service: dict) -> str:
    """Return a stable service name fallback from repo metadata."""
    repo = str(service.get("repo") or "").strip().rstrip("/")
    if repo:
        short_name = repo.split("/")[-1]
        if short_name.endswith(".git"):
            short_name = short_name[:-4]
        if short_name:
            return short_name
    return "service"
