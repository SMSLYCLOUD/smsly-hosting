from rest_framework import serializers


class EmptySerializer(serializers.Serializer):
    pass


def _normalize_request_domain(raw_domain: str):
    try:
        from apps.domains.utils import normalize_domain
        return normalize_domain(raw_domain), None
    except ValueError as exc:
        return None, str(exc)


def _rewrite_public_domain(current_domain: str, old_base_domain: str, new_base_domain: str) -> str | None:
    current = str(current_domain or "").strip().lower().rstrip(".")
    old_base = str(old_base_domain or "").strip().lower().rstrip(".")
    new_base = str(new_base_domain or "").strip().lower().rstrip(".")
    if not current or not old_base or not new_base or old_base == new_base:
        return None
    if current == old_base:
        return new_base
    suffix = f".{old_base}"
    if not current.endswith(suffix):
        return None
    prefix = current[:-len(suffix)].rstrip(".")
    if not prefix:
        return new_base
    return f"{prefix}.{new_base}"


def _parse_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "on")
