import logging
import os
import re
import secrets
import string
from typing import Any
from urllib.parse import urlparse, urlunparse

from django.conf import settings

from apps.deployments.tasks.ecosystem.constants import (
    _EXTERNAL_SECRETS,
)

from apps.deployments.services.ecosystem.ecosystem_heuristics import (
    _is_external_api_key,
)

from .addons import (
    _addon_env_keys,
    _addon_type_from_placeholder,
    _coerce_addon_type,
)
from .repo import (
    _canonical_repo_ref,
    _repo_short_name,
    _slugify_name,
)

logger = logging.getLogger(__name__)

# Sentinel values that mean "no real value provided" — never persist them.
_PLACEHOLDER_SENTINELS = ("{{FILL_ME}}", "CHANGEME", "TODO")


def _is_sentinel_value(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    if text in _PLACEHOLDER_SENTINELS or text == "{{GENERATE}}":
        return True
    return text.startswith("REPLACE_WITH_")


def _generate_secret(length: int = 50) -> str:
    """Generate a secure random string for env vars."""
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))


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
            if default_val not in (None, "") and not _is_sentinel_value(default_val):
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
            # SEC: scope the fallback lookup to the deploy's own services —
            # never leak another user's service name/port.
            owner_ids = {
                getattr(s, "owner_id", None)
                for s in created_services.values()
                if getattr(s, "owner_id", None)
            }
            qs = Service.objects.filter(name__iexact=ref_name)
            if owner_ids:
                qs = qs.filter(owner_id__in=owner_ids)
            ref_service = qs.first()
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

            # Skip unresolved vars entirely — don't write empty strings
            # that will override the service's own defaults and crash
            # pydantic-typed env parsing. CORS_ORIGINS= is the classic
            # example: the manifest can't infer it, an empty value
            # makes pydantic json.loads("") fail at boot, and the
            # service's own default ("http://localhost:3000") would
            # have worked fine if we just left the key unset.
            unresolved = getattr(resolver, "unresolved_vars", [])
            skipped_unresolved = [k for k in unresolved if k not in manifest_resolved]
            manifest_resolved = {k: v for k, v in manifest_resolved.items() if str(v or "").strip()}
            if skipped_unresolved:
                logger.info(
                    "Manifest resolver leaving %d unresolved vars unset for %s "
                    "(service defaults will apply): %s",
                    len(skipped_unresolved), service_name,
                    ", ".join(sorted(skipped_unresolved)[:10]),
                )
            resolved_env = manifest_resolved

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
            _needs_senate = set()
            for k, v in resolved_env.items():
                v_str = str(v or "").strip()
                if v_str in _PLACEHOLDER_VALS or v_str.startswith("REPLACE_WITH_") or any(p in v_str.lower() for p in _MOCK_PATTERNS):
                    _needs_senate.add(k)
            # Also include unresolved and heuristic vars from manifest resolver
            if source_dir:
                for k in getattr(resolver, 'unresolved_vars', []):
                    _needs_senate.add(k)
                for k in getattr(resolver, 'heuristic_vars', []):
                    if k not in resolved_env:
                        _needs_senate.add(k)

            if _needs_senate:
                # Send ALL env vars as context so AI can make informed decisions,
                # but redact values of secret-named vars — they must never leave
                # the platform boundary.
                try:
                    from apps.cloud.services.build_constants import is_secret_env_var
                    _senate_context = {
                        k: ("[REDACTED]" if is_secret_env_var(k) and str(v or "").strip() else v)
                        for k, v in resolved_env.items()
                    }
                except Exception:
                    _senate_context = dict(resolved_env)
                senate_suggestions = EnvironmentIntelligenceService.resolve_environment(
                    _senate_context, stack, service_name, fill_keys=_needs_senate,
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
            if _is_external_api_key(key):
                # Third-party provider keys can never be randomly generated —
                # leave empty so the user supplies the real credential.
                resolved[key] = ""
            else:
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
    """Ensure no unresolved placeholders remain in resolved env vars.

    SECURITY: error text must NEVER include the env VALUE — a
    partially-resolved string can embed real secret fragments
    (e.g. postgres://user:realpass@{{HOST}}). This message flows into
    per-repo results, plan.services_created (served by the API), and
    logs. Report KEY NAMES ONLY.
    """
    unresolved_keys = []
    for key, value in resolved_env.items():
        if isinstance(value, str) and re.search(r"\{\{.*?\}\}", value):
            unresolved_keys.append(str(key))
    if unresolved_keys:
        raise ValueError(
            "Unresolved placeholders in env vars: "
            + ", ".join(unresolved_keys)
            + " — set concrete values (or lock them) before deploying"
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



