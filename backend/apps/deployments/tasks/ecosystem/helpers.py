import logging

logger = logging.getLogger(__name__)
import os
import re
import secrets
import string
from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse, urlunparse

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.deployments.models import (
    Deployment,
)

from .constants import (
    _ACTIVE_BUILDS_CACHE_KEY,
    _ADDON_ENV_ALIASES,
    _BUILD_DEFER_SECONDS,
    _DEFAULT_WAVE_SIZE,
    _EXTERNAL_SECRETS,
    _MAX_CONCURRENT_BUILDS,
    _MIN_FREE_MEMORY_MB,
    _PLAN_REQUIRED_KEYS,
    _SERVICE_REQUIRED_KEYS,
    _SERVICE_VALID_BUILDS,
    _SMSLY_CORE_HINTS,
    _STACK_DEFAULT_PORTS,
    _VALID_PORT_RANGE,
    _WAVE_RECHECK_SECONDS,
)


def _canonical_repo_ref(raw: Any) -> str:
    text = str(raw or "").strip().rstrip("/")
    if not text:
        return ""
    if text.startswith("git@github.com:"):
        text = text.split(":", 1)[1]
    else:
        parsed = urlparse(text)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            text = parsed.path.strip("/")
    if text.endswith(".git"):
        text = text[:-4]
    return text.strip("/")


def _repository_url(repo_ref: Any) -> str:
    raw = str(repo_ref or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return raw.rstrip("/")
    canonical = _canonical_repo_ref(raw)
    return f"https://github.com/{canonical}" if canonical else ""


def _repo_short_name(repo_ref: Any) -> str:
    canonical = _canonical_repo_ref(repo_ref)
    return canonical.split("/")[-1] if canonical else ""


def _slugify_name(raw: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9-]+", "-", str(raw or "").strip())
    name = re.sub(r"-{2,}", "-", name).strip("-").lower()
    return name or "service"


def _coerce_addon_type(raw: Any) -> str:
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


def _placeholder_addon_types(raw_env: Any) -> set[str]:
    """Extract addon types referenced by env placeholders (including embedded)."""
    addon_types: set[str] = set()
    for value in _normalize_env_vars(raw_env).values():
        value_text = str(value or "").strip()
        # Find all {{...}} tokens in the value, not just full-string ones
        for match in re.finditer(r"\{\{(.+?)\}\}", value_text):
            token = match.group(1).strip()
            if token.upper().startswith("SHARED_SECRET:") or token.upper() == "GENERATE":
                continue
            if token.upper().startswith("SERVICE:"):
                continue
            addon_type = _addon_type_from_placeholder(token)
            if addon_type:
                addon_types.add(addon_type)
    return addon_types


def _service_placeholder_refs(raw_env: Any) -> list[str]:
    """Extract service references from {{SERVICE:name}} env placeholders."""
    refs: list[str] = []
    for value in _normalize_env_vars(raw_env).values():
        value_text = str(value or "")
        for match in re.finditer(r"\{\{\s*SERVICE\s*:\s*(.+?)\s*\}\}", value_text, re.IGNORECASE):
            ref = match.group(1).strip()
            if ref:
                refs.append(ref)
    return refs


def _plan_addon_types(plan_addons: Any) -> set[str]:
    """Collect addon types declared at the top level."""
    if not isinstance(plan_addons, list):
        return set()
    return {addon for addon in (_coerce_addon_type(item) for item in plan_addons) if addon}


def _service_plan_addon_types(svc_plan: dict[str, Any], plan_addons: Any = None) -> set[str]:
    """Return all addon types intended for a service."""
    addon_types: set[str] = set()
    raw_service_addons = svc_plan.get("addons", [])
    if isinstance(raw_service_addons, list):
        addon_types.update(
            addon for addon in (_coerce_addon_type(item) for item in raw_service_addons) if addon
        )

    addon_types.update(_placeholder_addon_types(svc_plan.get("env_vars", {})))

    if isinstance(plan_addons, list):
        aliases = {
            str(svc_plan.get("name") or "").strip().lower(),
            _slugify_name(svc_plan.get("name") or ""),
            _canonical_repo_ref(svc_plan.get("repo")).lower(),
            _repo_short_name(svc_plan.get("repo")).lower(),
        }
        aliases.discard("")
        for addon in plan_addons:
            addon_type = _coerce_addon_type(addon)
            if not addon_type:
                continue
            if not isinstance(addon, dict):
                continue
            shared_by = addon.get("shared_by") or addon.get("services") or addon.get("used_by") or []
            if isinstance(shared_by, str):
                shared_tokens = [shared_by]
            elif isinstance(shared_by, list):
                shared_tokens = shared_by
            else:
                shared_tokens = []
            normalized_shared = set()
            for token in shared_tokens:
                token_text = str(token or "").strip()
                if not token_text:
                    continue
                normalized_shared.update({
                    token_text.lower(),
                    _slugify_name(token_text),
                    _canonical_repo_ref(token_text).lower(),
                    _repo_short_name(token_text).lower(),
                })
            normalized_shared.discard("")
            if aliases & normalized_shared:
                addon_types.add(addon_type)

    return addon_types


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


def _deployment_target_for_server(server, provider) -> tuple[Any, bool]:
    """Translate a selected server into Deployment target fields."""
    if server is None:
        return None, str(getattr(provider, "provider_type", "")).upper() == "LOCAL"
    if bool(getattr(server, "is_primary", False)):
        return None, True
    return server, False


def _validate_plan_structure(plan: dict) -> list[str]:
    """
    Validate ecosystem plan structure.
    Returns a list of validation errors (empty = valid).
    """
    errors: list[str] = []

    if not isinstance(plan, dict):
        return ["Plan must be a dict"]

    # Check required keys
    for key in _PLAN_REQUIRED_KEYS:
        if key not in plan:
            errors.append(f"Missing required plan key: {key}")

    services = plan.get("services", [])
    if not isinstance(services, list):
        errors.append("'services' must be a list")
        return errors

    for i, svc in enumerate(services):
        if not isinstance(svc, dict):
            errors.append(f"services[{i}] must be a dict, got {type(svc).__name__}")
            continue

        # Check required service keys
        for key in _SERVICE_REQUIRED_KEYS:
            if key not in svc:
                errors.append(f"services[{i}] missing required key: {key}")

        skip = svc.get("skip", False)
        if skip:
            continue

        # Validate build type
        build = str(svc.get("build", "") or "").strip().lower()
        if build and build not in _SERVICE_VALID_BUILDS:
            errors.append(f"services[{i}] invalid build '{build}'. Allowed: {', '.join(sorted(_SERVICE_VALID_BUILDS))}")

        # Validate port range
        port = svc.get("port")
        if port is not None:
            try:
                p = int(port)
                if p < _VALID_PORT_RANGE[0] or p > _VALID_PORT_RANGE[1]:
                    errors.append(f"services[{i}] port {p} out of range ({_VALID_PORT_RANGE[0]}-{_VALID_PORT_RANGE[1]})")
            except (TypeError, ValueError):
                errors.append(f"services[{i}] port must be an integer")

        # Validate depends_on format
        deps = svc.get("depends_on")
        if deps is not None and not isinstance(deps, (str, list)):
            errors.append(f"services[{i}] depends_on must be a string or list")

        # Validate addons format
        addons = svc.get("addons")
        if addons is not None:
            if isinstance(addons, list):
                for j, a in enumerate(addons):
                    if not isinstance(a, str):
                        errors.append(f"services[{i}] addons[{j}] must be a string")
            else:
                errors.append(f"services[{i}] addons must be a list")

    return errors


def _alias_ambiguity_report(dependencies: dict[str, set[str]], entries_by_key: dict) -> list[str]:
    """Report ambiguous dependency aliases for user visibility."""
    warnings_list: list[str] = []
    alias_owner: dict[str, str | None] = {}

    for key, entry in entries_by_key.items():
        repo = str(entry["repo"]).strip().lower()
        repo_name = repo.split("/")[-1]
        aliases = {
            repo, repo_name,
            str(entry.get("name") or "").strip().lower(),
            str(entry.get("requested_name") or "").strip().lower(),
        }
        for alias in aliases:
            if not alias:
                continue
            if alias in alias_owner and alias_owner[alias] != key:
                alias_owner[alias] = None
            else:
                alias_owner[alias] = key

    # Collect ambiguous aliases
    ambiguous = {alias for alias, owner in alias_owner.items() if owner is None}
    if ambiguous:
        warnings_list.append(f"Ambiguous dependency aliases (resolved to None): {', '.join(sorted(ambiguous))}")

    return warnings_list


def _get_available_memory_mb() -> int:
    """Return available RAM in MB, or a large default if psutil is unavailable."""
    try:
        import psutil
        return int(psutil.virtual_memory().available / (1024 * 1024))
    except ImportError:
        logger.warning("psutil unavailable — using conservative 512 MB memory estimate")
        return 512


def _has_enough_memory(min_free_mb: int = _MIN_FREE_MEMORY_MB) -> bool:
    """Check if system has at least min_free_mb of available memory."""
    free = _get_available_memory_mb()
    if free >= min_free_mb:
        return True
    logger.warning("Low memory: %d MB available, need %d MB. Deferring wave.", free, min_free_mb)
    return False


def _env_int(name: str, default: int, minimum: int = 1, maximum: int = 500) -> int:
    """Read bounded int from env."""
    try:
        parsed = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _count_active_ecosystem_builds() -> int:
    """Count ecosystem deployments currently being built (from cache counter)."""
    try:
        return int(cache.get(_ACTIVE_BUILDS_CACHE_KEY, 0))
    except Exception:
        return 0


def _increment_active_ecosystem_builds() -> None:
    """Increment the active build counter (1-hour TTL safety net)."""
    try:
        try:
            cache.incr(_ACTIVE_BUILDS_CACHE_KEY)
        except (ValueError, ConnectionError):
            cache.add(_ACTIVE_BUILDS_CACHE_KEY, 1, timeout=3600)
    except Exception:
        pass


def _decrement_active_ecosystem_builds() -> None:
    """Decrement the active build counter."""
    try:
        current = int(cache.get(_ACTIVE_BUILDS_CACHE_KEY, 0))
        if current > 0:
            cache.set(_ACTIVE_BUILDS_CACHE_KEY, current - 1, timeout=3600)
    except Exception:
        pass


def _rebuild_ecosystem_build_counter() -> None:
    """Recalculate the build counter from actual deployment statuses.
    Called periodically to prevent drift from stale cache entries."""
    try:
        active_statuses = {
            Deployment.Status.QUEUED,
            Deployment.Status.BUILDING,
            Deployment.Status.DEPLOYING,
            Deployment.Status.HEALTH_CHECK,
        }
        count = Deployment.objects.filter(
            commit_hash="ecosystem-deploy",
            status__in=active_statuses,
        ).count()
        cache.set(_ACTIVE_BUILDS_CACHE_KEY, count, timeout=3600)
    except Exception:
        pass


def _get_ecosystem_build_config() -> dict:
    """Read ecosystem build settings from PlatformConfig with env var fallback."""
    try:
        from apps.deployments.models.core import PlatformConfig
        cfg = PlatformConfig.load()
        max_concurrent = cfg.ecosystem_max_concurrent_builds or _MAX_CONCURRENT_BUILDS
        stagger = cfg.ecosystem_build_stagger_seconds or 30
        wave_size = cfg.ecosystem_default_wave_size or _DEFAULT_WAVE_SIZE
        recheck = cfg.ecosystem_wave_recheck_seconds or _WAVE_RECHECK_SECONDS
    except Exception:
        max_concurrent = _MAX_CONCURRENT_BUILDS
        stagger = 30
        wave_size = _DEFAULT_WAVE_SIZE
        recheck = _WAVE_RECHECK_SECONDS
    return {
        "max_concurrent_builds": max_concurrent,
        "build_stagger_seconds": stagger,
        "wave_size": wave_size,
        "wave_recheck_seconds": recheck,
    }


def _wave_recheck_countdown() -> int:
    """Return wave recheck countdown in seconds from PlatformConfig."""
    return _get_ecosystem_build_config()["wave_recheck_seconds"]


def _generate_secret(length: int = 50) -> str:
    """Generate a secure random string for env vars."""
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _next_available_service_name(ServiceModel, base_name: str) -> str:
    """Find a globally unique service name."""
    if not ServiceModel.objects.filter(name=base_name).exists():
        return base_name

    for _ in range(20):
        candidate = f"{base_name}-{secrets.token_hex(2)}"
        if not ServiceModel.objects.filter(name=candidate).exists():
            return candidate

    return f"{base_name}-{secrets.token_hex(4)}"


def _looks_like_smsly_core_name(raw: str) -> bool:
    """Detect SMSLY core/platform API style service names."""
    token = _slugify_name(raw)
    if token in _SMSLY_CORE_HINTS:
        return True
    return token.startswith("smsly") and ("core" in token or "platform-api" in token)


def _repo_slug_from_url(url: str) -> str:
    """Extract repo slug from repository URL when available."""
    text = str(url or "").strip().rstrip("/")
    if not text:
        return ""
    tail = text.split("/")[-1]
    if tail.endswith(".git"):
        tail = tail[:-4]
    return _slugify_name(tail)


def _select_shared_addon_anchor(services: list[Any]):
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


def _normalize_env_vars(raw_env: Any) -> dict[str, str]:
    """
    Accept flexible env var formats from AI/heuristics and normalize to dict.

    Supported:
    - {"KEY": "value"}
    - [{"key": "KEY", "default": "value", "is_secret": true, ...}, ...]
    """
    normalized: dict[str, str] = {}

    if isinstance(raw_env, dict):
        for key, value in raw_env.items():
            key_text = str(key or "").strip().upper()
            if not key_text:
                continue
            normalized[key_text] = "" if value is None else str(value)
        return normalized

    if isinstance(raw_env, list):
        for entry in raw_env:
            if not isinstance(entry, dict):
                continue
            key_text = str(entry.get("key") or "").strip().upper()
            if not key_text:
                continue

            default_val = entry.get("default")
            if default_val not in (None, ""):
                normalized[key_text] = str(default_val)
                continue

            if key_text in _EXTERNAL_SECRETS or key_text.endswith("_API_KEY"):
                normalized[key_text] = ""
                continue

            if entry.get("generate") or entry.get("is_secret"):
                normalized[key_text] = "{{GENERATE}}"
                continue

            normalized[key_text] = ""

    return normalized


def _detect_service_port(svc_plan: dict, stack: str = "") -> int:
    """Detect the internal port for a service from multiple sources.

    Priority:
      1. Explicit port in svc_plan (user-provided)
      2. Dockerfile EXPOSE directive (cloned repo)
      3. docker-compose.yml ports mapping
      4. .env / .env.example PORT variable
      5. Stack-aware default (e.g. Django=8000, Next.js=3000)
      6. Global fallback (3000)
    """
    # 1. Explicit port in plan
    explicit = svc_plan.get("port")
    if explicit is not None:
        try:
            p = int(explicit)
            if _VALID_PORT_RANGE[0] <= p <= _VALID_PORT_RANGE[1]:
                return p
        except (TypeError, ValueError):
            pass

    _clone_dir = svc_plan.get("_clone_dir", "")
    if not _clone_dir:
        # No cloned source — skip file-based detection
        stack_l = str(stack or "").strip().lower()
        return _STACK_DEFAULT_PORTS.get(stack_l, 3000)

    import os

    # 2. Dockerfile EXPOSE
    dockerfile_path = svc_plan.get("dockerfile", "")
    try:
        df_path = os.path.join(_clone_dir, dockerfile_path or "Dockerfile")
        if os.path.isfile(df_path):
            with open(df_path, "r", errors="ignore") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.upper().startswith("EXPOSE"):
                        parts = stripped.split()
                        if len(parts) >= 2:
                            for part in parts[1:]:
                                port_str = part.split("/")[0]
                                try:
                                    p = int(port_str)
                                    if _VALID_PORT_RANGE[0] <= p <= _VALID_PORT_RANGE[1]:
                                        return p
                                except (TypeError, ValueError):
                                    continue
    except Exception:
        pass

    # 3. docker-compose.yml ports mapping (first "host:container" → container port)
    for dc_name in ("docker-compose.yml", "docker-compose.yaml"):
        try:
            dc_path = os.path.join(_clone_dir, dc_name)
            if os.path.isfile(dc_path):
                with open(dc_path, "r", errors="ignore") as f:
                    in_ports = False
                    for line in f:
                        stripped = line.strip()
                        if stripped.startswith("ports:"):
                            in_ports = True
                            continue
                        if in_ports:
                            if stripped.startswith("- "):
                                # e.g. "- 8080:3000" or "- "3000:3000""
                                port_part = stripped.lstrip("- ").strip().strip('"').strip("'")
                                container_port = port_part.split(":")[-1].split("/")[0]
                                try:
                                    p = int(container_port)
                                    if _VALID_PORT_RANGE[0] <= p <= _VALID_PORT_RANGE[1]:
                                        return p
                                except (TypeError, ValueError):
                                    continue
                            else:
                                in_ports = False
        except Exception:
            pass

    # 4. .env / .env.example PORT variable
    for env_name in (".env.example", ".env", ".env.local"):
        try:
            env_path = os.path.join(_clone_dir, env_name)
            if os.path.isfile(env_path):
                with open(env_path, "r", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if line.upper().startswith("PORT="):
                            val = line.split("=", 1)[1].strip().strip('"').strip("'")
                            try:
                                p = int(val)
                                if _VALID_PORT_RANGE[0] <= p <= _VALID_PORT_RANGE[1]:
                                    return p
                            except (TypeError, ValueError):
                                continue
        except Exception:
            pass

    # 5. Stack-aware default
    stack_l = str(stack or "").strip().lower()
    return _STACK_DEFAULT_PORTS.get(stack_l, 3000)


def _stack_runtime_defaults(stack: str, port: int) -> dict[str, str]:
    """Inject safe runtime defaults per stack."""
    stack_l = str(stack or "").strip().lower()
    defaults: dict[str, str] = {"PORT": str(max(1, int(port or 3000)))}

    if stack_l in {"node", "nextjs", "nuxt"}:
        defaults["NODE_ENV"] = "production"
    if stack_l in {"python", "django"}:
        defaults["PYTHONUNBUFFERED"] = "1"
        defaults["PYTHONDONTWRITEBYTECODE"] = "1"

    return defaults


def _service_placeholder_target(
    ref_name: str,
    created_services: dict[str, Any],
) -> tuple[str, int]:
    """Return the internal host and port for a service placeholder."""
    ref_service = (
        created_services.get(ref_name)
        or created_services.get(ref_name.lower())
    )
    if not ref_service:
        try:
            from apps.deployments.models import Service
            ref_service = Service.objects.filter(name__iexact=ref_name).first()
        except Exception as e:
            logger.warning(f"Failed to lookup service {ref_name} in database: {e}")

    if ref_service:
        host = ref_service.name
        port = ref_service.internal_port or 3000
        return host, port

    return _slugify_name(ref_name), 3000



def _service_placeholder_url(
    ref_name: str,
    created_services: dict[str, Any],
    *,
    as_authority: bool = False,
) -> str:
    """Resolve a service reference to an internal URL or URL authority."""
    host, port = _service_placeholder_target(ref_name, created_services)
    authority = f"{host}:{port}"
    if as_authority:
        return authority
    return f"http://{authority}"


def _rewrite_url_path(base_url: str, suffix: str) -> str:
    """Rewrite a URL path with a suffix like /identity."""
    suffix_text = str(suffix or "").strip()
    if not suffix_text:
        return str(base_url or "")

    parsed_suffix = urlparse(f"/{suffix_text.lstrip('/')}")
    parsed_base = urlparse(str(base_url or ""))
    if not parsed_base.scheme or not parsed_base.netloc:
        return f"{str(base_url or '').rstrip('/')}/{suffix_text.lstrip('/')}"

    return urlunparse(
        parsed_base._replace(
            path=parsed_suffix.path,
            query=parsed_suffix.query or parsed_base.query,
            fragment=parsed_suffix.fragment or parsed_base.fragment,
        )
    )


def _resolve_single_placeholder(
    token: str,
    key: str,
    created_services: dict[str, Any],
    shared_addons: dict[str, str],
    shared_secrets: dict[str, str],
) -> str | None:
    """Resolve a single {{...}} token to a concrete value.

    Returns the resolved value, or None if the token is not recognised
    (caller should leave the original placeholder text in place).
    """
    # {{GENERATE}} is handled by the caller (full-string only)
    if token == "GENERATE":
        return None

    # Default/ignore platform-specific or legacy placeholders
    token_upper = token.upper()
    if token_upper.startswith("CAPROVER_"):
        if token_upper.endswith("_URL") or token_upper.endswith("_URI"):
            return "http://localhost"
        return ""

    # {{SHARED_SECRET:name}}
    if token.upper().startswith("SHARED_SECRET:"):
        secret_name = token[14:].strip().lower()
        if not secret_name:
            secret_name = str(key or "shared").strip().lower()
        return shared_secrets.setdefault(secret_name, _generate_secret())

    # {{SERVICE:ref}}
    if token.upper().startswith("SERVICE:"):
        ref_name = token[8:].strip()
        return _service_placeholder_url(ref_name, created_services)

    # Addon URL placeholders (POSTGRES_URL, REDIS_URL, DATABASE_URL, etc.)
    addon_type = _addon_type_from_placeholder(token)
    if addon_type and addon_type in shared_addons:
        return shared_addons[addon_type]

    # Environment variable fallback
    env_fallback = os.environ.get(token)
    if env_fallback:
        return env_fallback

    # Addon placeholder that couldn't be resolved -> hard error
    if addon_type:
        raise ValueError(
            f"Addon placeholder {{{{{token}}}}} for {key} could not be resolved. "
            f"Provision addon {addon_type} or set {token}."
        )

    return None


def _resolve_from_manifest_or_fallback(
    repo: str,
    service_name: str,
    entry: dict[str, Any],
    svc_plan: dict[str, Any],
    created_services: dict[str, Any],
    shared_addons: dict[str, str],
    shared_secrets: dict[str, str],
    stack: str = "",
) -> dict[str, str]:
    """Resolve env vars from actual source files when available.

    Priority:
      1. ManifestEnvResolver (reads .env.example + SECRETS-MANIFEST.yaml)
      2. _resolve_env_placeholders + AI Senate fallback (existing behavior)
    """
    # Try to find cloned source for this repo
    source_dir = _find_cloned_source_for_repo(repo, service_name)
    resolved_env = None

    if source_dir:
        try:
            from apps.deployments.services.manifest_env_resolver import ManifestEnvResolver

            resolver = ManifestEnvResolver(
                source_dir=source_dir,
                service_name=service_name,
            )
            resolved = resolver.resolve_all()

            # Resolve addon placeholders in manifest-resolved values
            manifest_resolved: dict[str, str] = {}
            _ADDON_PLACEHOLDERS = (
                "{{POSTGRES_URL}}", "{{REDIS_URL}}", "{{RABBITMQ_URL}}",
                "{{QDRANT_URL}}", "{{MYSQL_URL}}", "{{MONGODB_URL}}",
                "{{ELASTICSEARCH_URL}}", "{{MINIO_URL}}", "{{MEMCACHED_URL}}",
            )
            for key, value in resolved.items():
                if any(placeholder in str(value) for placeholder in _ADDON_PLACEHOLDERS):
                    manifest_resolved[key] = _resolve_env_placeholders(
                        {key: value}, created_services, shared_addons, shared_secrets,
                    ).get(key, value)
                else:
                    manifest_resolved[key] = value

            for unres_k in getattr(resolver, "unresolved_vars", []):
                if unres_k not in manifest_resolved:
                    manifest_resolved[unres_k] = ""

            logger.info(
                "Manifest resolver filled %d vars for %s (stack=%s)",
                len(manifest_resolved), service_name, resolver.stack,
            )
            resolved_env = manifest_resolved

        except Exception as exc:
            logger.warning(
                "Manifest resolver failed for %s: %s; falling back to placeholder + AI Senate",
                service_name, exc,
            )
            resolved_env = None

    if resolved_env is None:
        # Fallback: original placeholder resolution
        env_vars = _normalize_env_vars(svc_plan.get("env_vars", {}))
        resolved_env = _resolve_env_placeholders(
            env_vars, created_services,
            shared_addons=shared_addons,
            shared_secrets=shared_secrets,
        )

    _is_frontend = stack in {"nextjs", "nuxt"} or "frontend" in service_name.lower()
    if getattr(settings, "SENATE_ENABLED", True) and not _is_frontend:
        try:
            from apps.intelligence.services.env_intelligence import (
                EnvironmentIntelligenceService,
            )
            # Collect vars that need real production values:
            # empty/placeholder, heuristic/mock, and unresolved.
            _PLACEHOLDER_VALS = ("", "{{GENERATE}}", "{{FILL_ME}}", "CHANGEME", "TODO")
            _MOCK_PATTERNS = ("localhost", "127.0.0.1", "mock", "test_", "fake_")
            _needs_senate = {}
            for k, v in resolved_env.items():
                v_str = str(v or "").strip()
                if v_str in _PLACEHOLDER_VALS or v_str.startswith("REPLACE_WITH_") or any(p in v_str.lower() for p in _MOCK_PATTERNS):
                    _needs_senate[k] = v
            # Also include unresolved and heuristic vars from manifest resolver
            if source_dir:
                for k in getattr(resolver, 'unresolved_vars', []):
                    if k not in _needs_senate:
                        _needs_senate[k] = ""
                for k in getattr(resolver, 'heuristic_vars', []):
                    if k not in _needs_senate:
                        _needs_senate[k] = resolved_env.get(k, "")

            if _needs_senate:
                senate_suggestions = EnvironmentIntelligenceService.resolve_environment(
                    _needs_senate, stack, service_name,
                )
                for k, v in senate_suggestions.items():
                    if k in resolved_env and (not resolved_env[k] or resolved_env[k] in ("", "{{GENERATE}}", "{{FILL_ME}}")):
                        resolved_env[k] = v
        except Exception as exc:
            logger.warning("AI Senate enrichment failed for %s: %s", service_name, exc)

    return resolved_env


def _find_cloned_source_for_repo(repo: str, service_name: str) -> str | None:
    """Find the local cloned source directory for a repo.

    Checks multiple common paths where ecosystem scans or builds
    would clone repos to. Works for any repo, not just SMSLYCLOUD.
    """
    import os as _os

    repo_short = repo.lower().split("/")[-1].replace(".git", "")
    candidates = []

    # Priority 1: SMSLY_BUILDS_DIR (where the platform clones repos for building)
    from apps.deployments.services.pipeline import _get_builds_root
    builds_root = _get_builds_root()
    for build_candidate in [
        _os.path.join(builds_root, f"svc_{service_name}"),
        _os.path.join(builds_root, f"svc_{repo_short}"),
        _os.path.join(builds_root, f"build_{repo_short}"),
    ]:
        if _os.path.isdir(build_candidate) and _os.path.isdir(_os.path.join(build_candidate, ".git")):
            candidates.append(build_candidate)

    # Priority 2: Scan builds directory for any matching subdir with .git
    if _os.path.isdir(builds_root):
        try:
            for name in _os.listdir(builds_root):
                candidate = _os.path.join(builds_root, name)
                if not _os.path.isdir(candidate) or name.startswith("."):
                    continue
                if name == service_name or repo_short in name:
                    if _os.path.isdir(_os.path.join(candidate, ".git")):
                        candidates.append(candidate)
        except OSError:
            pass

    # Priority 3: SMSLYCLOUD monorepo checkout (development convenience)
    smslycloud = _os.path.join(_os.getcwd(), "SMSLYCLOUD")
    if _os.path.isdir(smslycloud):
        try:
            for name in _os.listdir(smslycloud):
                candidate = _os.path.join(smslycloud, name)
                if not _os.path.isdir(candidate) or name.startswith("."):
                    continue
                if repo_short == name.lower() or repo_short in name.lower() or service_name in name:
                    candidates.append(candidate)
        except OSError:
            pass

    # Priority 4: /tmp clone directory (ecosystem scan clones)
    tmp_root = _os.path.join("/tmp", "smsly-ecosystem")
    if _os.path.isdir(tmp_root):
        try:
            for name in _os.listdir(tmp_root):
                candidate = _os.path.join(tmp_root, name)
                if not _os.path.isdir(candidate) or name.startswith("."):
                    continue
                if repo_short in name or service_name in name:
                    if _os.path.isdir(_os.path.join(candidate, ".git")):
                        candidates.append(candidate)
        except OSError:
            pass

    if candidates:
        return candidates[0]
    return None


def _resolve_env_placeholders(
    env_vars: dict[str, str],
    created_services: dict[str, Any],
    shared_addons: dict[str, str] | None = None,
    shared_secrets: dict[str, str] | None = None,
) -> dict[str, str]:
    """Resolve known placeholders into concrete values.

    Handles both full-string placeholders (e.g. "{{POSTGRES_URL}}") and
    embedded placeholders (e.g. "{{POSTGRES_URL}}/identity" or
    "wss://{{SERVICE:smsly-security-gateway}}").  Multiple placeholders in a
    single value are all resolved.
    """
    resolved: dict[str, str] = {}
    shared_addons = shared_addons or {}
    shared_secrets = shared_secrets if shared_secrets is not None else {}

    for key, value in env_vars.items():
        value_text = str(value or "")

        # Fast-path: full-string special tokens
        if value_text == "{{GENERATE}}":
            resolved[key] = _generate_secret()
            continue

        if value_text.startswith("{{SHARED_SECRET:") and value_text.endswith("}}"):
            secret_name = value_text[16:-2].strip().lower()
            if not secret_name:
                secret_name = str(key or "shared").strip().lower()
            resolved[key] = shared_secrets.setdefault(secret_name, _generate_secret())
            continue

        addon_suffix = re.match(r"^\s*\{\{\s*([^{}]+?)\s*\}\}/(.+)$", value_text)
        if addon_suffix:
            token = addon_suffix.group(1).strip()
            suffix = addon_suffix.group(2).strip()
            addon_type = _addon_type_from_placeholder(token)
            if (
                addon_type in {"POSTGRES", "MYSQL", "MONGODB"}
                and addon_type in shared_addons
                and "{{" not in suffix
            ):
                resolved[key] = _rewrite_url_path(shared_addons[addon_type], suffix)
                continue

        # General case: resolve every {{...}} token inside the string.
        # This handles embedded placeholders like "{{POSTGRES_URL}}/identity".
        # Bind `key` and `value_text` into the closure defaults so that if
        # _replacer is ever retained beyond this iteration (async, queued,
        # cached), it still sees the value it was created for instead of the
        # last iteration's late-bound values.
        if "{{" in value_text:
            def _replacer(match: re.Match, _key=key, _value_text=value_text) -> str:
                token = match.group(1).strip()
                if token.upper().startswith("SERVICE:"):
                    prefix = _value_text[:match.start()]
                    if re.search(r"[a-z][a-z0-9+.-]*://$", prefix, re.IGNORECASE):
                        ref_name = token[8:].strip()
                        return _service_placeholder_url(
                            ref_name,
                            created_services,
                            as_authority=True,
                        )
                resolved_val = _resolve_single_placeholder(
                    token, _key, created_services, shared_addons, shared_secrets,
                )
                return resolved_val if resolved_val is not None else match.group(0)

            resolved[key] = re.sub(r"\{\{(.+?)\}\}", _replacer, value_text)
        else:
            resolved[key] = value_text

    return resolved


def _validate_resolved_env(resolved_env: dict[str, str]) -> None:
    """Ensure no unresolved placeholders remain in resolved env vars."""
    unresolved_keys = []
    for key, value in resolved_env.items():
        if isinstance(value, str) and re.search(r"\{\{.*?\}\}", value):
            unresolved_keys.append(f"{key}={value}")
    if unresolved_keys:
        raise ValueError(
            "Unresolved placeholders in env vars: "
            + "; ".join(unresolved_keys)
        )


def _validate_required_env(resolved_env: dict[str, str], addon_types: set[str] | None = None) -> None:
    """Validate env required by requested addons without forcing every stack to use every addon."""
    missing = []
    for addon_type in sorted(addon_types or set()):
        keys = _addon_env_keys(addon_type)
        if keys and not any(resolved_env.get(key) for key in keys):
            missing.append(f"{addon_type} ({'/'.join(keys)})")
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")


def _runtime_watch_defaults(user) -> dict[str, str]:
    """Default zero-click runtime monitoring configuration."""
    defaults = {
        "JULES_RUNTIME_WATCH": "true",
        "JULES_NOTIFY_IN_APP": "true",
        "JULES_NOTIFY_SMS": "true",
        "JULES_NOTIFY_EMAIL": "true",
        "JULES_NOTIFY_TELEGRAM": "false",
        "JULES_NOTIFY_WHATSAPP": "false",
    }
    email = str(getattr(user, "email", "") or "").strip()
    if email:
        defaults["ALERT_EMAIL"] = email
    return defaults


def _ecosystem_project_name(raw_name: str) -> str:
    """Generate a beautified, unique project name for ecosystem deployments.

    Produces names like: ``my-app — Jul 8, 2026 · 14:32``
    The timestamp ensures uniqueness across repeated deploys of the same ecosystem.
    """
    import calendar

    from django.utils import timezone as _tz

    base = _slugify_name(raw_name).replace("-", " ").strip()
    if not base:
        base = "Ecosystem"

    # Title-case the base name for readability
    base = " ".join(word.capitalize() for word in base.split())

    now = _tz.now()
    month_abbr = calendar.month_abbr[now.month]   # e.g. "Jul"
    day = now.day
    year = now.year
    hour = now.hour
    minute = now.minute
    return f"{base} — {month_abbr} {day}, {year} · {hour:02d}:{minute:02d}"


def _order_key(item: Any) -> int:
    """Sort helper for deploy order."""
    if not isinstance(item, dict):
        return 99
    try:
        return int(item.get("deploy_order", 99))
    except (TypeError, ValueError):
        return 99


def _normalize_buildpack(raw: Any) -> str:
    """Map ecosystem plan build strategy to Service.buildpack choices.

    Default is DOCKER for ecosystem services.  Only falls back to NIXPACKS
    when the plan explicitly requests it or the build type is static.
    """
    build = str(raw or "").strip().lower()
    if build in {"docker", "dockerfile", "docker-file"}:
        return "DOCKER"
    if build in {"static", "static-site", "static_site"}:
        return "STATIC"
    if build in {"nixpacks"}:
        return "NIXPACKS"
    # Default: Docker build for ecosystem services
    return "DOCKER"


def _normalize_deploy_mode(svc_plan: dict[str, Any]) -> tuple[str, str, str]:
    """
    Resolve deploy mode and compose hints from plan.

    Returns: (deploy_mode, compose_file, compose_main_service)
    """
    mode_raw = str(svc_plan.get("deploy_mode") or "").strip().upper()
    build_raw = str(svc_plan.get("build") or "").strip().lower()
    compose_file = str(
        svc_plan.get("compose_file")
        or svc_plan.get("docker_compose_file")
        or ""
    ).strip()
    compose_main = str(
        svc_plan.get("compose_main_service")
        or svc_plan.get("main_service")
        or ""
    ).strip()

    if mode_raw == "COMPOSE" or build_raw in {"docker-compose", "compose"} or compose_file:
        return "COMPOSE", (compose_file or "docker-compose.yml"), compose_main
    return "SINGLE", "", ""


def _extract_dependencies(raw_depends: Any) -> list[str]:
    """Normalize depends_on values to a flat list of tokens."""
    if isinstance(raw_depends, str):
        text = raw_depends.strip()
        if not text:
            return []
        if "," in text:
            return [token.strip() for token in text.split(",") if token.strip()]
        return [text]

    if isinstance(raw_depends, list):
        values = []
        for item in raw_depends:
            token = str(item or "").strip()
            if token:
                values.append(token)
        return values

    return []


def _chunked(items: list[str], size: int) -> Iterable[list[str]]:
    """Yield fixed-size chunks."""
    for idx in range(0, len(items), size):
        yield items[idx:idx + size]


def _build_dependency_waves(
    entries_by_key: dict[str, dict[str, Any]],
    dependencies: dict[str, set[str]],
    wave_size: int,
) -> tuple[list[list[str]], list[str]]:
    """
    Build deployment waves from dependency graph.

    Returns:
    - waves: list of canonical repo keys grouped for parallel deploy
    - cyclic_or_unresolved: keys that could not be topologically sorted
    """
    dependents: dict[str, set[str]] = defaultdict(set)
    indegree: dict[str, int] = {}

    for key in entries_by_key:
        try:
            raw_deps = dependencies.get(key, set())
            # Ensure all dependencies are hashable (strings)
            safe_deps = []
            for dep in raw_deps:
                if dep in entries_by_key:
                    try:
                        # Ensure dependency is a string
                        str_dep = str(dep)
                        safe_deps.append(str_dep)
                    except Exception:
                        logger.warning(f"Cannot convert dependency {dep} to string for service {key}")
                        continue

            deps = set(safe_deps)
            dependencies[key] = deps
            indegree[key] = len(deps)
            for dep in deps:
                dependents[dep].add(key)
        except Exception as e:
            logger.error(f"Error processing dependencies for {key}: {e}")
            # Skip this entry to prevent the entire scan from failing
            dependencies[key] = set()
            indegree[key] = 0

    def _entry_order(repo_key: str) -> int:
        return int(entries_by_key[repo_key].get("deploy_order", 99))

    ready = sorted(
        [key for key, degree in indegree.items() if degree == 0],
        key=_entry_order,
    )
    processed: set[str] = set()
    waves: list[list[str]] = []

    while ready:
        layer = ready
        ready = []

        for chunk in _chunked(layer, wave_size):
            waves.append(chunk)

        for node in layer:
            processed.add(node)
            for dependent in dependents.get(node, set()):
                if dependent in processed:
                    continue
                indegree[dependent] = max(0, indegree[dependent] - 1)
                if indegree[dependent] == 0:
                    ready.append(dependent)

        ready.sort(key=_entry_order)

    unresolved = [
        key for key in sorted(entries_by_key.keys(), key=_entry_order)
        if key not in processed
    ]

    if unresolved:
        for chunk in _chunked(unresolved, wave_size):
            waves.append(chunk)

    return waves, unresolved


def _resolve_dependency_map(
    entries_by_key: dict[str, dict[str, Any]],
) -> dict[str, set[str]]:
    """Resolve depends_on aliases to canonical repo keys."""
    alias_owner: dict[str, str | None] = {}

    for key, entry in entries_by_key.items():
        repo = str(entry["repo"]).strip().lower()
        repo_name = repo.split("/")[-1]
        aliases = {
            repo,
            repo_name,
            str(entry.get("name") or "").strip().lower(),
            str(entry.get("requested_name") or "").strip().lower(),
            _slugify_name(entry.get("name") or "").lower(),
        }
        for alias in aliases:
            if not alias:
                continue
            if alias in alias_owner and alias_owner[alias] != key:
                alias_owner[alias] = None
            else:
                alias_owner[alias] = key

    alias_to_key = {
        alias: owner for alias, owner in alias_owner.items()
        if owner is not None
    }

    resolved: dict[str, set[str]] = {}
    for key, entry in entries_by_key.items():
        deps: set[str] = set()
        raw_tokens = [
            *_extract_dependencies(entry.get("depends_on", [])),
            *_service_placeholder_refs(entry.get("plan", {}).get("env_vars", {})),
        ]
        for token in raw_tokens:
            token_text = token.strip().lower()
            dep = (
                alias_to_key.get(token_text)
                or alias_to_key.get(_canonical_repo_ref(token_text).lower())
                or alias_to_key.get(_slugify_name(token_text).lower())
            )
            if dep and dep != key:
                deps.add(dep)
        resolved[key] = deps
    return resolved


def _queue_wave(app, deployment_ids: list[str], provider_id: str, wave_index: int) -> int:
    """Queue QUEUED deployments in this wave with concurrency control."""

    queued = 0
    build_cfg = _get_ecosystem_build_config()
    max_concurrent = build_cfg["max_concurrent_builds"]
    stagger = build_cfg["build_stagger_seconds"]

    for i, deployment_id in enumerate(deployment_ids):
        deployment = Deployment.objects.filter(id=deployment_id).first()
        if not deployment:
            continue
        if deployment.status != Deployment.Status.QUEUED:
            continue

        # Check concurrency limit
        active = _count_active_ecosystem_builds()
        if active >= max_concurrent:
            deployment.build_logs = (
                f"{deployment.build_logs or ''}"
                f"\n[Ecosystem] Build concurrency limit reached — deferred in wave {wave_index + 1}.\n"
            )
            deployment.save(update_fields=["build_logs"])
            app.send_task(
                "apps.deployments.tasks_ecosystem.ecosystem_deferred_build_task",
                args=[str(deployment.id), str(provider_id), wave_index],
                countdown=_BUILD_DEFER_SECONDS,
            )
            continue

        _increment_active_ecosystem_builds()

        countdown = i * stagger
        deployment.build_logs = (
            f"{deployment.build_logs or ''}"
            f"\n[Ecosystem] Queued in wave {wave_index + 1} (stagger +{countdown}s).\n"
        )
        deployment.save(update_fields=["build_logs"])

        app.send_task(
            "apps.deployments.tasks.smart_deploy_task",
            args=[str(deployment.id), str(provider_id)],
            kwargs={"skip_review": True},
            countdown=countdown,
        )
        queued += 1

    return queued


def _cancel_dependent_deployments(
    waves: list[list[str]],
    from_wave_index: int,
    failed_deployment_ids: list[str],
    dependencies: dict[str, set[str]],
    deployment_by_repo_key: dict[str, str],
    reason: str,
) -> int:
    """Cancel queued deployments in unreleased waves that depend on failed deployments."""

    # Reverse the mapping to find the repo_key from deployment_id
    repo_key_by_deployment = {v: k for k, v in deployment_by_repo_key.items()}

    # Identify which repo_keys failed
    failed_keys = {
        repo_key_by_deployment[dep_id]
        for dep_id in failed_deployment_ids
        if dep_id in repo_key_by_deployment
    }

    if not failed_keys:
        return 0

    # Build dependents map: parent -> set of children
    dependents: dict[str, set[str]] = defaultdict(set)
    for key, deps in dependencies.items():
        for dep in deps:
            dependents[dep].add(key)

    # Transitively find all nodes that depend on a failed node
    to_cancel_keys: set[str] = set()
    stack = list(failed_keys)
    while stack:
        node = stack.pop()
        for child in dependents.get(node, set()):
            if child not in to_cancel_keys and child not in failed_keys:
                to_cancel_keys.add(child)
                stack.append(child)

    if not to_cancel_keys:
        return 0

    to_cancel_ids = [
        deployment_by_repo_key[key]
        for key in to_cancel_keys
        if key in deployment_by_repo_key
    ]

    cancelled = 0
    for deployment in Deployment.objects.filter(id__in=to_cancel_ids):
        if deployment.status != Deployment.Status.QUEUED:
            continue
        deployment.status = Deployment.Status.CANCELLED
        deployment.finished_at = timezone.now()
        deployment.build_logs = (
            f"{deployment.build_logs or ''}"
            f"\n[Ecosystem] Cancelled before execution: {reason}\n"
        )
        deployment.save(update_fields=["status", "finished_at", "build_logs"])
        cancelled += 1

    return cancelled


def _cancel_all_remaining_deployments(
    waves: list[list[str]],
    from_wave_index: int,
    failed_deployment_ids: list[str],
    deployment_by_repo_key: dict[str, str],
    reason: str,
) -> int:
    """Cancel ALL queued deployments from *from_wave_index* onwards.

    Unlike ``_cancel_dependent_deployments`` which only cancels nodes
    that transitively depend on the failed node, this function cancels
    every remaining queued deployment in the ecosystem.  Used when
    ``cancel_others_on_failure`` is enabled.
    """
    failed_set = set(failed_deployment_ids)

    to_cancel_ids: list[str] = []
    for wave in waves[from_wave_index:]:
        for dep_id in wave:
            if dep_id in failed_set:
                continue
            to_cancel_ids.append(dep_id)

    if not to_cancel_ids:
        return 0

    cancelled = 0
    for deployment in Deployment.objects.filter(id__in=to_cancel_ids):
        if deployment.status != Deployment.Status.QUEUED:
            continue
        deployment.status = Deployment.Status.CANCELLED
        deployment.finished_at = timezone.now()
        deployment.build_logs = (
            f"{deployment.build_logs or ''}"
            f"\n[Ecosystem] Cancelled (fail-fast mode): {reason}\n"
        )
        deployment.save(update_fields=["status", "finished_at", "build_logs"])
        cancelled += 1

    return cancelled


def _cancel_unreleased_deployments(waves: list[list[str]], from_wave_index: int, reason: str) -> int:
    """Cancel remaining queued deployments when wave orchestration aborts (e.g. timeout)."""
    to_cancel_ids: list[str] = []
    for wave in waves[from_wave_index:]:
        to_cancel_ids.extend(wave)
    if not to_cancel_ids:
        return 0
    cancelled = 0
    for deployment in Deployment.objects.filter(id__in=to_cancel_ids):
        if deployment.status != Deployment.Status.QUEUED:
            continue
        deployment.status = Deployment.Status.CANCELLED
        deployment.finished_at = timezone.now()
        deployment.build_logs = (
            f"{deployment.build_logs or ''}"
            f"\n[Ecosystem] Cancelled: {reason}\n"
        )
        deployment.save(update_fields=["status", "finished_at", "build_logs"])
        cancelled += 1
    return cancelled


def _finalize_ecosystem_plan(plan_id: str | None, waves: list[list[str]]):
    """Sync final EcosystemPlan status when all waves have completed or aborted."""
    if not plan_id:
        return
    from apps.deployments.models.ecosystem import EcosystemPlan
    try:
        plan_rec = EcosystemPlan.objects.filter(id=plan_id).first()
        if not plan_rec:
            return
        if not waves:
            plan_rec.status = EcosystemPlan.Status.COMPLETED
            plan_rec.completed_at = timezone.now()
            plan_rec.error_message = ""
            plan_rec.save(update_fields=["status", "completed_at", "error_message", "updated_at"])
            return
        all_ids = [str(dep_id) for wave in waves for dep_id in wave]
        deployments = list(Deployment.objects.filter(id__in=all_ids).values("status"))
        statuses = [d["status"] for d in deployments]
        failed_states = {
            Deployment.Status.FAILED,
            Deployment.Status.BUILD_FAILED,
            Deployment.Status.BACKUP_FAILED,
            Deployment.Status.MIGRATION_FAILED,
            Deployment.Status.CANCELLED,
        }
        in_progress_states = {
            Deployment.Status.QUEUED,
            Deployment.Status.REVIEW,
            Deployment.Status.BUILDING,
            Deployment.Status.AWAITING_APPROVAL,
            Deployment.Status.BACKUP_RUNNING,
            Deployment.Status.MIGRATION_PLANNING,
            Deployment.Status.MIGRATION_RUNNING,
            Deployment.Status.DEPLOYING,
            Deployment.Status.HEALTH_CHECK,
            "STARTING",
        }
        if any(st in in_progress_states for st in statuses):
            return  # Still running
        failed_count = sum(1 for st in statuses if st in failed_states)
        if failed_count == 0 and statuses:
            plan_rec.status = EcosystemPlan.Status.COMPLETED
            plan_rec.completed_at = timezone.now()
            plan_rec.error_message = ""
        else:
            plan_rec.status = EcosystemPlan.Status.FAILED
            plan_rec.error_message = f"Ecosystem deploy finished with {failed_count}/{len(statuses)} service failures or cancellations."
        _rebuild_ecosystem_build_counter()
        plan_rec.save(update_fields=["status", "completed_at", "error_message", "updated_at"])
    except Exception as exc:
        logger.warning("Failed to finalize ecosystem plan %s: %s", plan_id, exc)


def _apply_service_profile(service, svc_plan: dict[str, Any], provider, port: int, server=None):
    """Apply ecosystem plan profile to a service with production defaults.

    Important: user-customisable fields (health_check_path, internal_port,
    cpu_cores, memory_mb) are only set from the plan when they still hold
    their model defaults.  This prevents the ecosystem deploy from silently
    overriding values the user changed through the web UI.
    """
    buildpack = _normalize_buildpack(svc_plan.get("build"))
    deploy_mode, compose_file, compose_main = _normalize_deploy_mode(svc_plan)
    root_directory = str(svc_plan.get("root_directory") or service.root_directory or "/").strip()
    if not root_directory.startswith("/"):
        root_directory = f"/{root_directory.lstrip('/')}"
    root_directory = root_directory or "/"

    service.repository_url = _repository_url(svc_plan["repo"])
    resolved_branch = str(
        svc_plan.get("branch")
        or svc_plan.get("default_branch")
        or service.branch
        or "main"
    ).strip() or "main"
    service.branch = resolved_branch

    # Only set port from plan if still at model default (8000) or unset/invalid.
    if not service.internal_port or service.internal_port == 8000:
        service.internal_port = int(port)

    service.buildpack = buildpack
    service.deploy_mode = deploy_mode
    service.compose_file = compose_file if deploy_mode == "COMPOSE" else ""
    service.compose_main_service = compose_main if deploy_mode == "COMPOSE" else ""
    service.root_directory = root_directory
    if not service.provider:
        service.provider = provider
    if server is not None or svc_plan.get("force_local_target"):
        service.server = server

    # Only set health_check_path from plan if user hasn't customised it
    # (still at model default "/health" or empty).
    health_path = str(svc_plan.get("health_check_path") or "").strip()
    if health_path:
        current_path = (service.health_check_path or "").strip()
        if not current_path or current_path == "/health":
            service.health_check_path = health_path if health_path.startswith("/") else f"/{health_path}"

    server_id = svc_plan.get("server_id")
    if server_id:
        from apps.deployments.models import ManagedServer
        try:
            if str(server_id).lower() in ("local", "primary"):
                server = ManagedServer.get_primary()
            else:
                server = ManagedServer.objects.filter(id=server_id, owner=service.owner).first()

            if server:
                service.server = server
        except Exception:
            pass

    # Only set cpu_cores/memory_mb from plan if still at model defaults.
    cpu_cores = getattr(service, "cpu_cores", None)
    if not cpu_cores or float(cpu_cores) == 1.0:
        cpu = svc_plan.get("cpu_cores")
        if cpu:
            try:
                service.cpu_cores = Decimal(str(cpu))
            except Exception:
                pass
    memory_mb = getattr(service, "memory_mb", None)
    if not memory_mb or memory_mb == 2048:
        mem = svc_plan.get("memory_mb")
        if mem:
            try:
                service.memory_mb = int(mem)
            except Exception:
                pass

    service.save()


def _update_plan_progress(plan_id: str, msg: str) -> None:
    """Persist scan progress so the frontend can show it on resume after page navigation."""
    from apps.deployments.models.ecosystem import EcosystemPlan
    try:
        EcosystemPlan.objects.filter(id=plan_id).update(
            scan_progress=msg,
            updated_at=timezone.now(),
        )
    except Exception:
        pass
