import json
import logging
import os
import secrets
from collections import defaultdict
from typing import Any

from .ecosystem_heuristics import _env_plan_map

logger = logging.getLogger(__name__)


def _is_core_service(service: dict) -> bool:
    """Return True when service looks like a core/platform API."""
    name = str(service.get("name") or "").lower()
    repo = str(service.get("repo") or "").lower()
    indicators = {"core", "platform", "api", "backend", "main", "server"}
    return any(ind in name or ind in repo for ind in indicators)


def _is_auth_service(service: dict) -> bool:
    """Return True when service looks like an identity/auth provider."""
    name = str(service.get("name") or "").lower()
    indicators = {"auth", "identity", "sso", "login", "keycloak"}
    return any(ind in name for ind in indicators)


def _is_intelligence_service(service: dict) -> bool:
    """Return True when service looks like an AI/Intelligence service."""
    name = str(service.get("name") or "").lower()
    repo = str(service.get("repo") or "").lower()
    indicators = {"intelligence", "ai", "brain", "senate", "neuron", "llm", "agent"}
    return any(ind in name or ind in repo for ind in indicators)


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


def _normalize_service_plan_fields(service: dict) -> None:
    """Normalize untrusted AI service fields before planning logic consumes them."""
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


def _safe_order(value: Any, default: int = 99) -> int:
    """Best-effort int parser for deploy_order values."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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


def _normalize_addon_token(token: str) -> str:
    normalized = str(token or "").strip().upper().replace("-", "_").replace(" ", "_")
    normalized = _ADDON_ALIASES.get(normalized, normalized)
    return normalized if normalized else ""


def _coerce_addons(raw_addons: Any) -> list[str]:
    """Normalize addon declarations to a deduped list of addon type strings."""
    tokens: list[str] = []
    try:
        _append_tokens(tokens, raw_addons, ("type", "addon", "name", "service", "value"))
    except TypeError as exc:
        logger.warning("_coerce_addons failed for value %r: %s", raw_addons, exc)
        return []
    return _dedupe_preserving_order(
        [addon for addon in (_normalize_addon_token(token) for token in tokens) if addon]
    )


def _build_deploy_sequence(services: list[dict]) -> list[str]:
    """Build deploy sequence from dependency-aware topological sort.

    Respects both ``deploy_order`` and ``depends_on`` so the sequence
    shown in the plan UI matches the actual build order.  Services with
    no dependencies deploy first; cyclic dependencies are appended last.
    """
    try:
        name_map: dict[str, dict] = {}
        for svc in services:
            if isinstance(svc, dict) and not svc.get("skip"):
                name = str(svc.get("name") or _repo_short_name(svc))
                name_map[name] = svc

        # Build adjacency + in-degree
        deps: dict[str, set[str]] = {}
        for name, svc in name_map.items():
            raw = _coerce_depends_on(svc.get("depends_on", []) or [])
            resolved = set()
            for d in raw:
                if d in name_map:
                    resolved.add(d)
            deps[name] = resolved

        indegree: dict[str, int] = {n: len(deps[n]) for n in name_map}
        dependents: dict[str, list[str]] = defaultdict(list)
        for name, dep_set in deps.items():
            for d in dep_set:
                dependents[d].append(name)

        ready = sorted(
            [n for n, deg in indegree.items() if deg == 0],
            key=lambda n: (_safe_order(name_map[n].get("deploy_order"), 99), n),
        )
        ordered: list[str] = []
        processed: set[str] = set()

        while ready:
            node = ready.pop(0)
            ordered.append(node)
            processed.add(node)
            for dependent in dependents.get(node, []):
                if dependent in processed:
                    continue
                indegree[dependent] = max(0, indegree[dependent] - 1)
                if indegree[dependent] == 0:
                    ready.append(dependent)
            ready.sort(key=lambda n: (_safe_order(name_map[n].get("deploy_order"), 99), n))

        unresolved = [n for n in name_map if n not in processed]
        ordered.extend(unresolved)

        return ["addons", *ordered]

    except Exception as e:
        logger.warning("Deploy sequence build failed: %s", e)
        try:
            return ["addons"] + [
                str(svc.get("name") or _repo_short_name(svc))
                for svc in services
                if isinstance(svc, dict) and not svc.get("skip")
            ]
        except Exception:
            return ["addons"]


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
                # Ensure both addon_type and service_name are strings
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


def _unify_cross_service_secrets(services: list[dict]) -> None:
    """Map well-known cross-service secret patterns to shared secret keys.

    Handles two patterns:
    1. Suffix patterns: ``*_TO_AUDIT_SECRET`` → ``audit_service_secret``.
       Any env var ending with ``_TO_AUDIT_SECRET`` on any service is
       unified to ``{{SHARED_SECRET:audit_service_secret}}``.
    2. Full-name patterns: ``PLATFORM_API_SECRET`` → ``platform_api_secret``.
       Any service with this exact var name gets the same shared key.

    Also handles compound prefixes: ``RATE_LIMIT_RATELIMIT_TO_AUDIT_SECRET``
    strips the leading ``RATE_LIMIT_`` prefix (detected via fallback) before
    matching, so it resolves to ``audit_service_secret``.

    This runs BEFORE step 3 (which uses broad substring matching) so the
    more specific cross-service patterns win over overly broad matches
    like ``GATEWAY_SECRET`` matching ``GATEWAY_TO_AUDIT_SECRET``.
    """
    _SUFFIX_TO_KEY: dict[str, str] = {
        "_TO_AUDIT_SECRET": "audit_service_secret",
        "_TO_BACKEND_SECRET": "backend_secret",
        "_TO_IDENTITY_SECRET": "identity_secret",
        "_TO_PLATFORM_SECRET": "platform_secret",
        "_TO_POLICY_SECRET": "policy_secret",
        "_TO_RATE_LIMIT_SECRET": "ratelimit_secret",
        "_TO_GATEWAY_SECRET": "gateway_secret",
        "_TO_SECURITY_GATEWAY_SECRET": "gateway_secret",
        "_TO_RATELIMIT_SECRET": "ratelimit_secret",
    }
    _FULLNAME_TO_KEY: dict[str, str] = {
        "BACKEND_SECRET": "backend_secret",
        "PLATFORM_API_SECRET": "platform_api_secret",
        "RATELIMIT_SECRET": "ratelimit_secret",
        "IDENTITY_SECRET": "identity_secret",
        "IDENTITY_SERVICE_SECRET": "service_secret",
        "GATEWAY_SECRET": "gateway_secret",
    }

    def _infer_prefixes_for_svc(env_map: dict) -> list[str]:
        keys_upper = [k.upper() for k in env_map]
        counts: dict[str, int] = {}
        for k in keys_upper:
            parts = k.split("_")
            for i in range(1, len(parts)):
                p = "_".join(parts[:i]) + "_"
                counts[p] = counts.get(p, 0) + 1
        return [p for p, c in counts.items() if c >= 3]

    for svc in services:
        env_map = svc.get("env_vars", {})
        if not isinstance(env_map, dict):
            continue
        svc_prefixes = svc.get("_env_prefixes", [])
        if not svc_prefixes:
            svc_prefixes = _infer_prefixes_for_svc(env_map)
        for key in list(env_map.keys()):
            val = str(env_map.get(key, ""))
            if val.startswith("{{SHARED_SECRET:"):
                continue
            key_u = key.upper()
            matched_key = None
            for suffix, shared_key in _SUFFIX_TO_KEY.items():
                if key_u.endswith(suffix):
                    matched_key = shared_key
                    break
            if not matched_key:
                for full_name, shared_key in _FULLNAME_TO_KEY.items():
                    if key_u == full_name:
                        matched_key = shared_key
                        break
            if not matched_key:
                # Try stripping known prefix then matching suffix
                for prefix in svc_prefixes:
                    pu = prefix.upper()
                    if key_u.startswith(pu) and len(key_u) > len(pu):
                        stripped = key_u[len(pu):]
                        for suffix, shared_key in _SUFFIX_TO_KEY.items():
                            if stripped.endswith(suffix):
                                matched_key = shared_key
                                break
                        if matched_key:
                            break
            if matched_key:
                env_map[key] = f"{{{{SHARED_SECRET:{matched_key}}}}}"
                logger.info(
                    "Step 3d: Cross-service %s/%s → {{SHARED_SECRET:%s}}",
                    svc.get("name", "?"), key, matched_key,
                )


def _unify_same_name_secrets(services: list[dict]) -> None:
    """Unify same-named secrets across services to a single shared key.

    When the same env var name appears on multiple services with different
    non-shared-secret values (or one already uses ``{{SHARED_SECRET:...}}``),
    all instances are unified to one shared key.  The key is chosen from
    the most commonly used existing key; otherwise the lowercase var name
    is used.
    """
    key_vals: dict[str, dict[str, str]] = {}
    for svc in services:
        env_map = svc.get("env_vars", {})
        if not isinstance(env_map, dict):
            continue
        for k, v in env_map.items():
            key_vals.setdefault(k, {})[svc.get("name", "?")] = str(v or "")

    for k, svc_vals in key_vals.items():
        if len(svc_vals) < 2:
            continue
        has_secret = any(
            any(w in k.upper() for w in ["SECRET", "KEY", "TOKEN", "PASSWORD"])
            for _ in svc_vals
        )
        if not has_secret:
            continue
        shared_keys = set()
        real_vals = set()
        for v in svc_vals.values():
            if v.startswith("{{SHARED_SECRET:"):
                sk = v.split("{{SHARED_SECRET:")[-1].rstrip("}}").rstrip("}")
                shared_keys.add(sk)
            elif v.startswith("{{") or v.startswith("REPLACE_") or v == "":
                continue
            else:
                real_vals.add(v)
        if not shared_keys and len(real_vals) <= 1:
            continue
        target_key = (
            shared_keys.pop()
            if len(shared_keys) == 1
            else k.lower()
        )
        for svc_name, v in svc_vals.items():
            if v != f"{{{{SHARED_SECRET:{target_key}}}}}":
                for svc in services:
                    if svc.get("name") == svc_name:
                        svc["env_vars"][k] = f"{{{{SHARED_SECRET:{target_key}}}}}"
                        break


def _ai_env_crosscheck(services: list[dict], ai_provider: str | None) -> None:
    """
    AI cross-check pass: analyzes the generated env vars and identifies
    cross-service secret mismatches or missing vars that the initial
    AI pass missed.  Runs after the main plan + heuristic corrections.
    """
    from apps.intelligence.providers import _cached_ask

    if not ai_provider:
        return

    # Collect env_prefix info to help AI understand prefixed relations
    _prefix_map: dict[str, list[str]] = {}
    for svc in services:
        prefixes = svc.get("_env_prefixes", [])
        if prefixes:
            _prefix_map[_repo_short_name(svc)] = prefixes

    lines = []
    if _prefix_map:
        lines.append("ENV PREFIX DETECTED (services using pydantic env_prefix):")
        for svc_name, prefixes in _prefix_map.items():
            lines.append(f"  {svc_name} prefixes: {', '.join(prefixes)}")
        lines.append("")

    for svc in services:
        name = _repo_short_name(svc)
        env = svc.get("env_vars", {})
        secrets = {k: v for k, v in env.items() if any(w in k.upper() for w in ["SECRET", "KEY", "TOKEN", "PASSWORD", "SALT"])}
        urls = {k: v for k, v in env.items() if any(w in k.upper() for w in ["_URL", "_ENDPOINT", "_HOST", "_API"])}
        lines.append(f"SERVICE: {name}")
        for k, v in sorted(secrets.items()):
            lines.append(f"  SECRET  {k}: {v}")
        for k, v in sorted(urls.items()):
            lines.append(f"  URL     {k}: {v}")
        lines.append("")

    prompt = f"""You are auditing a microservice deployment plan. Review the env vars below.

Identify:
1. Secrets with DIFFERENT names across services that must hold the SAME value
   (e.g. POLICY_TO_AUDIT_SECRET on policy-service ↔ AUDIT_SERVICE_SECRET on audit-service)
2. Prefixed secrets (e.g. RATE_LIMIT_GATEWAY_SECRET) that should match unprefixed
   counterparts (GATEWAY_SECRET) on other services
3. Empty required secrets that should use {{GENERATE}} or {{SHARED_SECRET:name}}
4. Empty service URLs that should use {{SERVICE:name}}

{''.join(lines)}

Return ONLY valid JSON:
{{"corrections": [
  {{"service": "service-name", "var": "VAR", "new_value": "{{SHARED_SECRET:name}}"}}
]}}"""

    logger.info("=== AI ENV CROSS-CHECK ===")
    try:
        resp, provider = _cached_ask(
            prompt, system_prompt="You are a DevOps auditor. Return ONLY valid JSON.", provider_id=ai_provider,
        )
        resp = resp or ""
        start = resp.find('{')
        end = resp.rfind('}')
        if start == -1 or end == -1:
            return
        result = json.loads(resp[start:end+1])
        corrections = result.get("corrections") or []
        if not corrections:
            logger.info("AI cross-check: no corrections needed")
            return
        logger.info(f"AI cross-check found {len(corrections)} corrections")
        for corr in corrections:
            svc_name = str(corr.get("service", ""))
            var_name = str(corr.get("var", ""))
            new_val = str(corr.get("new_value", ""))
            if not svc_name or not var_name or not new_val:
                continue
            for svc in services:
                if (_repo_short_name(svc) == svc_name) and var_name in svc.get("env_vars", {}):
                    svc["env_vars"][var_name] = new_val
                    logger.info(f"  Fixed {svc_name}/{var_name} → {new_val}")
                    break
    except Exception as e:
        logger.warning(f"AI env cross-check failed: {e}")


def _apply_generic_ecosystem_intelligence(services: list[dict]):
    """
    Elite Level 5: Zero-Hardcoding Service Discovery.
    Analyzes the 'functional intent' of each service to build a generic mesh.
    """
    deployable = [s for s in services if isinstance(s, dict) and not s.get("skip")]
    # 0. Role Discovery
    core_svc = next((s for s in deployable if _is_core_service(s)), None)
    auth_svc = next((s for s in deployable if _is_auth_service(s)), None)

    # 0.1 Addon auto-detection pre-pass
    # Detect addons from env var names BEFORE the main loop so has_* flags
    # are accurate when computed per-service.
    _ADDON_ENV_VARS: dict[str, list[str]] = {
        "POSTGRES":      ["DATABASE_URL", "POSTGRES_URL", "POSTGRES_HOST", "POSTGRES_DSN", "PGHOST"],
        "REDIS":         ["REDIS_URL", "REDIS_HOST", "CACHE_URL", "CELERY_RESULT_BACKEND"],
        "RABBITMQ":      ["BROKER_URL", "RABBITMQ_URL", "CELERY_BROKER_URL", "AMQP_URL", "RABBITMQ_HOST"],
        "QDRANT":        ["QDRANT_URL", "QDRANT_HOST", "VECTOR_DB_URL"],
        "MYSQL":         ["MYSQL_URL", "MYSQL_HOST", "MARIADB_URL", "MARIADB_HOST"],
        "MONGODB":       ["MONGODB_URL", "MONGO_URL", "MONGO_URI", "MONGODB_HOST"],
        "ELASTICSEARCH": ["ELASTICSEARCH_URL", "ELASTICSEARCH_HOST", "ELASTIC_URL", "ELASTIC_HOST", "OPENSEARCH_URL"],
        "MINIO":         ["MINIO_ENDPOINT", "MINIO_HOST", "S3_ENDPOINT_URL", "S3_HOST"],
        "MEMCACHED":     ["MEMCACHED_URL", "MEMCACHED_HOST", "MEMCACHE_SERVERS"],
    }
    _ADDON_IMPORT_HINTS: dict[str, list[str]] = {
        "POSTGRES":      ["psycopg2", "asyncpg", "django.db", "databases", "pg", "sequelize", "prisma", "typeorm", "knex"],
        "REDIS":         ["redis", "aioredis", "celery", "rq", "django_redis", "ioredis", "bull", "bullmq"],
        "RABBITMQ":      ["pika", "aio_pika", "kombu", "amqplib"],
        "QDRANT":        ["qdrant_client", "qdrant"],
        "MYSQL":         ["mysql", "pymysql", "aiomysql"],
        "MONGODB":       ["pymongo", "motor", "mongoengine", "mongoose", "mongodb"],
        "ELASTICSEARCH": ["elasticsearch", "opensearchpy", "@elastic/elasticsearch"],
        "MINIO":         ["minio"],
    }
    # Frontend services (Next.js, Nuxt, or named "frontend") should NOT
    # have database/Redis/infra addons — they talk to backends via HTTP APIs.
    # Strip addon-specific env vars BEFORE detection so addons aren't assigned.
    _FRONTEND_STACKS = frozenset({"nextjs", "nuxt"})
    _ADDON_ALL_KEYS = {k.upper() for keys in _ADDON_ENV_VARS.values() for k in keys}
    for svc in deployable:
        stack = str(svc.get("stack") or "").lower().strip()
        name = str(svc.get("name") or "").lower()
        if stack in _FRONTEND_STACKS or "frontend" in name:
            env_map = svc.get("env_vars", {})
            if isinstance(env_map, dict):
                for ek in list(env_map.keys()):
                    if ek.upper() in _ADDON_ALL_KEYS:
                        del env_map[ek]

    for svc in deployable:
        env_map = svc.get("env_vars", {})
        if not isinstance(env_map, dict):
            continue
        _addons = svc.get("addons", []) or []
        if not isinstance(_addons, list):
            _addons = []
        _env_upper = {k.upper() for k in env_map}
        for addon_type, env_keys in _ADDON_ENV_VARS.items():
            if addon_type in _addons:
                continue
            if any(ek in _env_upper for ek in env_keys):
                _addons.append(addon_type)
        _imports = set()
        clone_dir = svc.get("clone_dir") or ""
        if clone_dir and os.path.isdir(clone_dir):
            _SKIP_DIRS = {"node_modules", ".git", "__pycache__", "venv", ".venv", "dist", "build", ".tox", ".eggs", "vendor", "target"}
            for root, dirs, files in os.walk(clone_dir):
                dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
                for fname in files:
                    if not fname.endswith((".py", ".js", ".ts", ".go", ".rs")):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", errors="ignore") as f:
                            content = f.read(8192)
                    except Exception:
                        continue
                    for addon_type, import_patterns in _ADDON_IMPORT_HINTS.items():
                        for pattern in import_patterns:
                            if pattern in content:
                                _imports.add(addon_type)
        for addon_type in _imports:
            if addon_type not in _addons:
                _addons.append(addon_type)
        if _addons != (svc.get("addons") or []):
            svc["addons"] = _addons

    # 3. Cross-service secret mapping (before per-service loop)
    _unify_cross_service_secrets(services)

    for svc in deployable:
        env_map = svc.get("env_vars", {})
        if not isinstance(env_map, dict):
            env_map = _env_plan_map(env_map)
        svc_name = str(svc.get("name") or "").lower()
        stack = str(svc.get("stack") or "").lower()
        try:
            addons = set(_coerce_addons(svc.get("addons", []) or []))
        except TypeError as exc:
            logger.warning("Unhashable addons set for svc %r (%s): %s", svc_name, svc.get("repo"), exc)
            addons = set()

        # 1. Stack-based Addon Defaults
        if stack == "django":
            addons.add("POSTGRES")
            addons.add("REDIS")
        elif stack in ["node", "nextjs", "nuxt"]:
            if any("DATABASE_URL" in k.upper() for k in env_map):
                addons.add("POSTGRES")

        svc["addons"] = sorted(addons)

        # 2. Dynamic Cross-Linking (Intelligent Mesh)

        # Link to Core API
        if core_svc and svc != core_svc:
            # If service has vars that look like they need the Core/Platform URL
            for key in list(env_map.keys()):
                key_u = key.upper()
                if any(k in key_u for k in ["API_URL", "CORE_URL", "PLATFORM_URL", "BACKEND_URL"]):
                    core_name = str(core_svc.get("name") or _repo_short_name(core_svc)).strip()
                    if not core_name:
                        continue
                    # If it's a prefixed var (e.g. MYPROJECT_PLATFORM_API_URL), preserve the key
                    # but wire it to the detected core service
                    env_map[key] = f"{{{{SERVICE:{core_name}}}}}"

                    # Also add implicit dependency
                    try:
                        deps = set(_coerce_depends_on(svc.get("depends_on", []) or []))
                    except TypeError as exc:
                        logger.warning("Unhashable depends_on set for svc %r (%s): %s", svc_name, svc.get("repo"), exc)
                        deps = set()
                    deps.add(core_name)
                    svc["depends_on"] = sorted(deps)

        # Link to Auth Provider
        if auth_svc and svc != auth_svc:
            for key in list(env_map.keys()):
                key_u = key.upper()
                if any(k in key_u for k in ["AUTH_URL", "IDENTITY_URL", "OIDC_URL", "SSO_URL"]):
                    auth_name = str(auth_svc.get("name") or _repo_short_name(auth_svc)).strip()
                    if not auth_name:
                        continue
                    env_map[key] = f"{{{{SERVICE:{auth_name}}}}}"

                    try:
                        deps = set(_coerce_depends_on(svc.get("depends_on", []) or []))
                    except TypeError as exc:
                        logger.warning("Unhashable depends_on set for auth svc %r (%s): %s", svc_name, svc.get("repo"), exc)
                        deps = set()
                    deps.add(auth_name)
                    svc["depends_on"] = sorted(deps)

        # 3a. Global Secret Synchronization
        for key in list(env_map.keys()):
            key_u = key.upper()
            if any(k in key_u for k in ["JWT_SECRET", "ENCRYPTION_KEY", "APP_SECRET", "GATEWAY_SECRET", "SERVICE_SECRET"]):
                current = str(env_map.get(key, ""))
                if current.startswith("{{SHARED_SECRET:"):
                    continue
                env_map[key] = f"{{{{SHARED_SECRET:{key.lower()}}}}}"

        # 3b. Env prefix secret matching
        # Strip known env_prefix from secret-like vars and unify with
        # unprefixed counterparts on other services.  Prefixes come from
        # the scanner, or are inferred from the var names themselves
        # (e.g. RATE_LIMIT_ appearing on many vars).
        svc_prefixes = svc.get("_env_prefixes", [])
        if not svc_prefixes:
            # Fallback: infer prefix from var names that share a leading segment
            keys = [k.upper() for k in env_map]
            _counts: dict[str, int] = {}
            for k in keys:
                parts = k.split("_")
                for i in range(1, len(parts)):
                    p = "_".join(parts[:i]) + "_"
                    _counts[p] = _counts.get(p, 0) + 1
            svc_prefixes = [p for p, c in _counts.items() if c >= 3]
        if svc_prefixes:
            for key in list(env_map.keys()):
                key_u = key.upper()
                for prefix in svc_prefixes:
                    pu = prefix.upper()
                    if not key_u.startswith(pu):
                        continue
                    base_name = key_u[len(pu):]
                    if not base_name or not any(w in base_name for w in ["SECRET", "KEY", "TOKEN", "PASSWORD", "SALT"]):
                        continue
                    for other in services:
                        other_env = other.get("env_vars", {})
                        if base_name in other_env:
                            shared_key = f"{{{{SHARED_SECRET:{base_name.lower()}}}}}"
                            env_map[key] = shared_key
                            if not str(other_env[base_name]).startswith("{{SHARED_SECRET:"):
                                other_env[base_name] = shared_key
                            logger.info(
                                "Step 3b: Unified %s (%s → %s) with %s on %s",
                                key, svc.get("name", "?"), base_name,
                                base_name, other.get("name", "?")
                            )
                            break

        # 3c. AI Intelligence Inheritance (independent of other loops)
        for key in list(env_map.keys()):
            key_u = key.upper()
            if any(k in key_u for k in ["AI_PROVIDER", "LLM_PROVIDER"]):
                env_map[key] = "auto"
            if any(k in key_u for k in ["OPENAI_API_KEY", "GEMINI_API_KEY", "CLAUDE_API_KEY", "GROK_API_KEY", "ANTHROPIC_API_KEY"]):
                 env_map[key] = f"{{{{SHARED_SECRET:{key.lower()}}}}}"

        # 4. Standard Addon URL Injection
        # Map env var names to their addon placeholders.  When a service
        # declares an addon, inject the placeholder for every matching var.
        _ADDON_URL_MAP: list[tuple[str, str, list[str]]] = [
            ("{{POSTGRES_URL}}",       "POSTGRES",      ["DATABASE_URL", "POSTGRES_URL", "POSTGRES_DSN", "PGHOST"]),
            ("{{REDIS_URL}}",          "REDIS",         ["REDIS_URL", "REDIS_HOST", "CACHE_URL", "CELERY_RESULT_BACKEND"]),
            ("{{RABBITMQ_URL}}",       "RABBITMQ",      ["RABBITMQ_URL", "BROKER_URL", "CELERY_BROKER_URL", "AMQP_URL", "RABBITMQ_HOST"]),
            ("{{QDRANT_URL}}",         "QDRANT",        ["QDRANT_URL", "QDRANT_HOST", "VECTOR_DB_URL"]),
            ("{{MYSQL_URL}}",          "MYSQL",         ["MYSQL_URL", "MYSQL_HOST", "MARIADB_URL", "MARIADB_HOST"]),
            ("{{MONGODB_URL}}",        "MONGODB",       ["MONGODB_URL", "MONGO_URL", "MONGO_URI", "MONGODB_HOST"]),
            ("{{ELASTICSEARCH_URL}}",  "ELASTICSEARCH", ["ELASTICSEARCH_URL", "ELASTICSEARCH_HOST", "ELASTIC_URL", "ELASTIC_HOST", "OPENSEARCH_URL"]),
            ("{{MINIO_URL}}",          "MINIO",         ["MINIO_ENDPOINT", "MINIO_HOST", "S3_ENDPOINT_URL", "S3_HOST"]),
            ("{{MEMCACHED_URL}}",      "MEMCACHED",     ["MEMCACHED_URL", "MEMCACHED_HOST", "MEMCACHE_SERVERS"]),
        ]
        _svc_addon_upper = {str(a).upper() for a in (svc.get("addons") or [])}
        _ecosystem_addon_upper: set[str] = set()
        for o in deployable:
            _ecosystem_addon_upper.update(str(a).upper() for a in (o.get("addons") or []))

        for placeholder, addon_type, env_keys in _ADDON_URL_MAP:
            addon_upper = addon_type.upper()
            has_addon = addon_upper in _svc_addon_upper
            ecosystem_has = addon_upper in _ecosystem_addon_upper
            for env_key in env_keys:
                if env_key not in env_map:
                    continue
                cur = str(env_map.get(env_key, "")).strip()
                if has_addon or (ecosystem_has and (not cur or cur in ("", "{{GENERATE}}", "{{FILL_ME}}") or cur.startswith("REPLACE_WITH_"))):
                    env_map[env_key] = placeholder

        # 4.5 Intelligence Service Specialization
        if _is_intelligence_service(svc):
            env_map.setdefault("AI_PROVIDER", "auto")

        svc["env_vars"] = env_map

    # 3d. Unify same-name secrets across services (after per-service loop)
    _unify_same_name_secrets(services)

    # 4.52 CORS & CSRF auto-fill
    # If a backend has CORS_ALLOWED_ORIGINS or CSRF_TRUSTED_ORIGINS pointing
    # to a non-frontend service (or is empty), point them to the frontend.
    # Search ALL services (including skipped) for a frontend by name first,
    # then fall back to stack-based detection (only non-skipped).
    _frontends = [s for s in services if "frontend" in str(s.get("name", "")).lower()]
    if not _frontends:
        _frontends = [s for s in services if s.get("stack") in ("nextjs", "nuxt") and not s.get("skip")]
    if _frontends:
        _fe_name = str(_frontends[0].get("name") or _repo_short_name(_frontends[0])).strip()
        _fe_placeholder = f"{{{{SERVICE:{_fe_name}}}}}"
        _fe_names = {str(s.get("name", "")).strip().lower() for s in _frontends}
        for svc in deployable:
            env_map = svc.get("env_vars", {})
            if not isinstance(env_map, dict):
                continue
            for cors_key in ("CORS_ALLOWED_ORIGINS", "CSRF_TRUSTED_ORIGINS"):
                cur = str(env_map.get(cors_key, "")).strip()
                # Replace if empty, placeholder, or pointing to a non-frontend service
                should_replace = (
                    not cur
                    or cur in ("{{GENERATE}}", "{{FILL_ME}}")
                )
                if not should_replace and cur.startswith("{{SERVICE:"):
                    ref_name = cur[len("{{SERVICE:"):-2].strip().lower()
                    if ref_name and ref_name not in _fe_names:
                        should_replace = True
                if should_replace:
                    env_map[cors_key] = _fe_placeholder
    else:
        # No frontend detected — set CORS to wildcard so backends don't
        # deny all cross-origin requests at runtime.
        for svc in deployable:
            env_map = svc.get("env_vars", {})
            if not isinstance(env_map, dict):
                continue
            for cors_key in ("CORS_ALLOWED_ORIGINS", "CSRF_TRUSTED_ORIGINS"):
                cur = str(env_map.get(cors_key, "")).strip()
                if not cur or cur in ("{{GENERATE}}", "{{FILL_ME}}"):
                    env_map[cors_key] = "*"

    # 4.54 Fix STRIPE_SECRET_KEY collision
    # Step 3a may have assigned {{SHARED_SECRET:secret_key}} (generic)
    # instead of a Stripe-specific shared key. Fix it.
    for svc in deployable:
        env_map = svc.get("env_vars", {})
        if not isinstance(env_map, dict):
            continue
        if env_map.get("STRIPE_SECRET_KEY") == "{{SHARED_SECRET:secret_key}}":
            env_map["STRIPE_SECRET_KEY"] = ""
        if env_map.get("STRIPE_WEBHOOK_SECRET") == "{{SHARED_SECRET:webhook_secret}}":
            env_map["STRIPE_WEBHOOK_SECRET"] = ""

    # 4.55 platform_secret orphan fix
    # GATEWAY_TO_PLATFORM_SECRET references {{SHARED_SECRET:platform_secret}}
    # but platform-api has no PLATFORM_SECRET. Add it.
    for svc in deployable:
        if "platform" in str(svc.get("name", "")).lower():
            env_map = svc.get("env_vars", {})
            if isinstance(env_map, dict) and "PLATFORM_SECRET" not in env_map:
                env_map["PLATFORM_SECRET"] = "{{SHARED_SECRET:platform_secret}}"

    # 4.53 Clear hardcoded external API keys (LAST mutation before cross-linking)
    # The scanner picks up real production values from source code for service-
    # specific external API keys.  Replace them with {{GENERATE}} so the final
    # display pass generates mock random values that let services start.
    # Uses suffix/prefix pattern matching instead of maintaining an ever-growing
    # exact-name list.  Shared secrets ({{SHARED_SECRET:...}}) and addon URLs
    # ({{SERVICE:...}}) are protected by the val.startswith("{{") guard.
    _EXTERNAL_KEY_SUFFIXES = (
        "_API_KEY", "_SECRET_KEY", "_WEBHOOK_SECRET", "_AUTH_TOKEN",
        "_ACCESS_TOKEN", "_ACCESS_KEY", "_PUBLISHABLE_KEY",
        "_PRIVATE_KEY", "_TOKEN", "_PASSWORD", "_SECRET", "_KEY",
    )
    _EXTERNAL_PREFIXES = (
        "RESEND_", "SMSMAN_", "FIVESIM_", "COINBASE_", "NOWPAYMENTS_",
        "PAYSTACK_", "FLUTTERWAVE_", "STRIPE_", "TWILIO_", "SMTP_",
        "SENTRY_", "OTEL_", "CAPROVER_", "AWS_", "VAULT_", "IPINFO_",
        "MAXMIND_", "INFOBIP_", "META_", "VERCEL_",
    )
    # Keys that must NEVER be cleared (addon URLs, framework, shared infra)
    _SKIP_CLEAR = {
        "DATABASE_URL", "POSTGRES_URL", "POSTGRES_DSN", "PGHOST",
        "REDIS_URL", "REDIS_HOST", "REDIS_PORT", "CACHE_URL", "CELERY_RESULT_BACKEND",
        "RABBITMQ_URL", "BROKER_URL", "CELERY_BROKER_URL", "AMQP_URL", "RABBITMQ_HOST",
        "QDRANT_URL", "QDRANT_HOST", "VECTOR_DB_URL",
        "MYSQL_URL", "MYSQL_HOST", "MARIADB_URL", "MARIADB_HOST",
        "MONGODB_URL", "MONGO_URL", "MONGO_URI", "MONGODB_HOST",
        "ELASTICSEARCH_URL", "ELASTICSEARCH_HOST", "ELASTIC_URL", "ELASTIC_HOST", "OPENSEARCH_URL",
        "MINIO_ENDPOINT", "MINIO_HOST", "S3_ENDPOINT_URL", "S3_HOST",
        "MEMCACHED_URL", "MEMCACHED_HOST", "MEMCACHE_SERVERS",
        "PORT", "HOST", "HOSTNAME", "NODE_ENV", "DEBUG", "LOG_LEVEL",
        "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE", "ALLOWED_HOSTS",
        "SERVICE_PORT", "WEB_CONCURRENCY", "WORKERS",
        "AI_PROVIDER",
    }
    for svc in deployable:
        env_map = svc.get("env_vars", {})
        if not isinstance(env_map, dict):
            continue
        for key in list(env_map.keys()):
            if key in _SKIP_CLEAR:
                continue
            val = str(env_map.get(key, ""))
            if not val or val.startswith("{{") or val in ("", "{{GENERATE}}"):
                continue
            ku = key.upper()
            if any(ku.endswith(sfx) for sfx in _EXTERNAL_KEY_SUFFIXES):
                env_map[key] = "{{GENERATE}}"
                continue
            if any(ku.startswith(pfx) for pfx in _EXTERNAL_PREFIXES):
                env_map[key] = "{{GENERATE}}"
                continue
            if ku.startswith("PLATFORM_TO_") and ku.endswith("_SECRET"):
                env_map[key] = "{{GENERATE}}"

        # Catch-all: clear remaining long random-looking values (scanner artifacts)
        for key in list(env_map.keys()):
            if key in _SKIP_CLEAR:
                continue
            val = str(env_map.get(key, ""))
            if not val or val.startswith("{{") or val in ("", "{{GENERATE}}"):
                continue
            ku = key.upper()
            # Clear if value is a long random string (>=40 chars, no protocol)
            # These are always scanner artifacts from source code analysis
            if len(val) >= 40 and "://" not in val:
                env_map[key] = "{{GENERATE}}"
                continue
            # Clear if value contains only base64-like chars and is long
            if len(val) >= 30 and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for c in val):
                env_map[key] = "{{GENERATE}}"

    # 4.54 Auto-link service URLs and ports
    # After the AI and external-key clearing, many _URL and _PORT vars are
    # empty or {{GENERATE}}.  This step detects them by name pattern and
    # links them to the correct service automatically.
    _svc_by_name: dict[str, dict] = {}
    for svc in deployable:
        n = str(svc.get("name") or _repo_short_name(svc)).strip().lower()
        if n:
            _svc_by_name[n] = svc
        # Also index by short name parts (e.g. "smsly-backend" → "backend")
        parts = n.split("-")
        for p in parts:
            if p and p not in _svc_by_name:
                _svc_by_name[p] = svc

    _URL_SUFFIXES = ("_URL", "_SERVICE_URL", "_ENDPOINT", "_BACKEND_URL", "_API_URL", "_BASE_URL", "_HEALTH_URL", "_INTERNAL_URL")
    _PORT_SUFFIXES = ("_PORT", "_SERVICE_PORT")

    for svc in deployable:
        env_map = svc.get("env_vars", {})
        if not isinstance(env_map, dict):
            continue
        svc_port = str(svc.get("port") or 3000)

        for key in list(env_map.keys()):
            val = str(env_map.get(key, "") or "").strip()
            ku = key.upper()

            # --- Service URL linking ---
            # Replace if: empty, placeholder, or doesn't look like a URL
            # (no "://" and no "{{" prefix → likely a random string from code scan)
            is_url = any(ku.endswith(sfx) for sfx in _URL_SUFFIXES)
            if is_url and (not val or val == "{{GENERATE}}" or val == "{{FILL_ME}}"
                           or ("://" not in val and not val.startswith("{{"))):
                # Extract the target service name from the key
                stem = ku
                for sfx in sorted(_URL_SUFFIXES, key=len, reverse=True):
                    if stem.endswith(sfx):
                        stem = stem[:-len(sfx)]
                        break
                # Remove common prefixes
                for pfx in ("PLATFORM_TO_", "GATEWAY_TO_", "RATE_LIMIT_", "IDENTITY_TO_", "POLICY_TO_", "NEXT_PUBLIC_"):
                    if stem.startswith(pfx):
                        stem = stem[len(pfx):]
                        break
                stem_lower = stem.lower().replace("_", "-")
                matched = None
                if stem_lower in _svc_by_name:
                    matched = _svc_by_name[stem_lower]
                else:
                    for sname, s in _svc_by_name.items():
                        if stem_lower and set(stem_lower.split("-")).issubset(set(sname.split("-"))):
                            matched = s
                            break
                if matched:
                    target_name = str(matched.get("name") or _repo_short_name(matched)).strip()
                    env_map[key] = f"{{{{SERVICE:{target_name}}}}}"
                    continue

            # --- Port linking ---
            is_port = any(ku.endswith(sfx) for sfx in _PORT_SUFFIXES)
            if is_port and (not val or val == "{{GENERATE}}" or val == "{{FILL_ME}}"
                            or (not val.isdigit() and not val.startswith("{{"))):
                stem = ku
                for sfx in sorted(_PORT_SUFFIXES, key=len, reverse=True):
                    if stem.endswith(sfx):
                        stem = stem[:-len(sfx)]
                        break
                for pfx in ("PLATFORM_TO_", "GATEWAY_TO_", "RATE_LIMIT_", "IDENTITY_TO_", "POLICY_TO_", "NEXT_PUBLIC_"):
                    if stem.startswith(pfx):
                        stem = stem[len(pfx):]
                        break
                stem_lower = stem.lower().replace("_", "-")
                matched = None
                if stem_lower in _svc_by_name:
                    matched = _svc_by_name[stem_lower]
                else:
                    for sname, s in _svc_by_name.items():
                        if stem_lower and set(stem_lower.split("-")).issubset(set(sname.split("-"))):
                            matched = s
                            break
                if matched:
                    env_map[key] = str(matched.get("port") or 3000)
                    continue
                env_map[key] = svc_port

            # --- Cross-service secret linking (PLATFORM_TO, GATEWAY_TO, IDENTITY_TO, POLICY_TO) ---
            for _pfx in ("PLATFORM_TO_", "GATEWAY_TO_", "IDENTITY_TO_", "POLICY_TO_"):
                if ku.startswith(_pfx) and ku.endswith("_SECRET"):
                    if not val or val == "{{GENERATE}}" or val == "{{FILL_ME}}" or "://" not in val:
                        target = ku[len(_pfx):-len("_SECRET")].lower()
                        shared_key = f"{_pfx.lower()}{target}_secret"
                        env_map[key] = f"{{{{SHARED_SECRET:{shared_key}}}}}"
                        break

    # 4.6 Heuristic Fallback Cross-Linking
    # When the AI/heuristic didn't produce SERVICE: links, auto-wire frontends
    # to backends so services are never isolated.
    backend_stacks = {"django", "python", "rust", "go", "java", "ruby", "elixir", "php"}
    frontend_stacks = {"nextjs", "node", "nuxt"}
    backends = [s for s in deployable if s.get("stack") in backend_stacks]
    frontends = [s for s in deployable if s.get("stack") in frontend_stacks]
    if backends and frontends:
        for svc in deployable:
            env_map = svc.get("env_vars", {})
            svc_name = str(svc.get("name") or _repo_short_name(svc)).strip()
            stack = str(svc.get("stack") or "").lower()
            # Check if this service already has any SERVICE: link or API_URL reference
            has_cross_ref = any(
                "{{SERVICE:" in str(v) or "API_URL" in k.upper()
                for k, v in env_map.items()
            )
            if has_cross_ref:
                continue
            # Frontend → backend link
            if stack in frontend_stacks and backends:
                target = backends[0]
                target_name = str(target.get("name") or _repo_short_name(target)).strip()
                if not target_name:
                    continue
                env_map.setdefault(
                    "NEXT_PUBLIC_API_URL",
                    "{{{{SERVICE:{}}}}}".format(target_name),
                )
                try:
                    deps = set(_coerce_depends_on(svc.get("depends_on", []) or []))
                    deps.add(target_name)
                    svc["depends_on"] = sorted(deps)
                except TypeError:
                    pass
            # Backend → frontend link (CORS)
            if stack in backend_stacks and frontends:
                target = frontends[0]
                target_name = str(target.get("name") or _repo_short_name(target)).strip()
                if not target_name:
                    continue
                env_map.setdefault(
                    "CORS_ALLOWED_ORIGINS",
                    "{{{{SERVICE:{}}}}}".format(target_name),
                )
                # Backend doesn't depend on frontend for deploy order,
                # but the var reference makes the runtime link.

    # 5. Intelligent env var completion — fill empty vars with sensible
    #    defaults or {{SERVICE:...}} placeholders inferred from var names.
    _FRAMEWORK_DEFAULTS = {
        "PORT": "8000",
        "ALLOWED_HOSTS": "*",
        "DEBUG": "false",
        "LOG_LEVEL": "info",
        "HOST": "0.0.0.0",
        "NODE_ENV": "production",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    _SERVICE_URL_SUFFIXES = ("_URL", "_ENDPOINT", "_HOST", "_API", "_ADDRESS")

    def _stem_matches_service(stem: str, service_name: str) -> bool:
        stem_parts = set(stem.split("-"))
        name_parts = set(service_name.split("-"))
        return stem_parts.issubset(name_parts)

    for svc in deployable:
        env_map = svc.get("env_vars", {})
        for key in list(env_map.keys()):
            val = str(env_map.get(key, "")).strip() or ""
            if val and val not in ("{{GENERATE}}", "{{FILL_ME}}") and not val.startswith("REPLACE_WITH_"):
                continue
            if key in _FRAMEWORK_DEFAULTS:
                env_map[key] = _FRAMEWORK_DEFAULTS[key]
                continue
            key_u = key.upper().strip()
            for suffix in _SERVICE_URL_SUFFIXES:
                if key_u.endswith(suffix):
                    stem = key_u[:-len(suffix)].lower().replace("_", "-")
                    for other in deployable:
                        other_name = str(other.get("name", "")).lower()
                        if other is svc:
                            continue
                        if _stem_matches_service(stem, other_name):
                            env_map[key] = f"{{{{SERVICE:{other['name']}}}}}"
                            try:
                                deps = set(_coerce_depends_on(svc.get("depends_on", []) or []))
                                deps.add(other['name'])
                                svc["depends_on"] = sorted(deps)
                            except TypeError:
                                pass
                            break
                    break

    # 5b. Align PORT env var with the plan port field.
    # The plan's `port` field is the authoritative runtime port.  Ensure
    # the `PORT` env var (which the app actually reads) matches it.
    for svc in deployable:
        env_map = svc.get("env_vars", {})
        svc_port = str(svc.get("port") or 3000)
        env_map["PORT"] = svc_port

    # 6. Dependency Depth Sorting — only set if AI didn't provide a specific order
    # Auth (10) > Core (20) > Others (50)
    for svc in deployable:
        current_order = svc.get("deploy_order")
        if current_order is not None and current_order != 99:
            continue  # AI set a specific order, respect it
        order = 50
        if _is_auth_service(svc):
            order = 10
        elif _is_core_service(svc):
            order = 20
        svc["deploy_order"] = order

    # 7. Elite 100% Exhaustive Sweep
    _ensure_100_percent_env_coverage(deployable)

    # 8. Final display pass — replace remaining {{GENERATE}} sentinels
    # with real random values so the plan UI shows actual values
    # instead of placeholders.  Vars with the SAME name across services
    # get the SAME value (e.g. PLATFORM_API_SECRET on backend + platform-api).
    # This mirrors what the AI would do via {{SHARED_SECRET:name}}.
    # First, unify: if ANY service has {{GENERATE}} for a key, ALL services
    # get {{GENERATE}} so the pool dedup works correctly.
    _generate_keys: set[str] = set()
    for svc in deployable:
        env_map = svc.get("env_vars", {})
        for key, val in env_map.items():
            if val == "{{GENERATE}}":
                _generate_keys.add(key)
    if _generate_keys:
        for svc in deployable:
            env_map = svc.get("env_vars", {})
            for key in list(env_map.keys()):
                if key in _generate_keys:
                    env_map[key] = "{{GENERATE}}"
    # Also detect conflicting values for the same key across services.
    # If a secret-like key has different real values on different services,
    # unify them to a single generated value.
    _key_values: dict[str, set[str]] = {}
    for svc in deployable:
        env_map = svc.get("env_vars", {})
        for key, val in env_map.items():
            val_str = str(val or "")
            if val_str in ("", "{{GENERATE}}", "{{FILL_ME}}") or val_str.startswith("REPLACE_WITH_") or val_str.startswith("{{SERVICE:") or val_str.startswith("{{SHARED_SECRET:"):
                continue
            _key_values.setdefault(key, set()).add(val_str)
    for svc in deployable:
        env_map = svc.get("env_vars", {})
        for key in list(env_map.keys()):
            vals = _key_values.get(key)
            if vals and len(vals) > 1:
                if any(k in key.upper() for k in ["SECRET", "KEY", "TOKEN", "PASSWORD"]):
                    env_map[key] = "{{GENERATE}}"
                    _generate_keys.add(key)
    _generate_pool: dict[str, str] = {}
    for svc in deployable:
        env_map = svc.get("env_vars", {})
        for key, val in env_map.items():
            if val == "{{GENERATE}}":
                if key not in _generate_pool:
                    _generate_pool[key] = secrets.token_urlsafe(48)
    for svc in deployable:
        env_map = svc.get("env_vars", {})
        for key in list(env_map.keys()):
            if env_map[key] == "{{GENERATE}}":
                env_map[key] = _generate_pool[key]
        svc["env_vars"] = env_map

    # 9. Resource allocation based on detected intensity
    for svc in deployable:
        if svc.get("_is_heavy"):
            svc["cpu_cores"] = 2.0
            svc["memory_mb"] = 4096
        else:
            svc["cpu_cores"] = 1.0
            svc["memory_mb"] = 1024


def _ensure_100_percent_env_coverage(services: list[dict]):
    """
    Ensure every env var has a value. This is the LAST RESORT fallback.
    The AI should have filled everything intelligently from code analysis.
    Only external API keys get {{GENERATE}}.
    """
    _ADDON_URL_KEYS = {
        "DATABASE_URL", "POSTGRES_URL", "POSTGRES_DSN", "PGHOST",
        "REDIS_URL", "REDIS_HOST", "CACHE_URL", "CELERY_RESULT_BACKEND",
        "RABBITMQ_URL", "BROKER_URL", "CELERY_BROKER_URL", "AMQP_URL", "RABBITMQ_HOST",
        "QDRANT_URL", "QDRANT_HOST", "VECTOR_DB_URL",
        "MYSQL_URL", "MYSQL_HOST", "MARIADB_URL", "MARIADB_HOST",
        "MONGODB_URL", "MONGO_URL", "MONGO_URI", "MONGODB_HOST",
        "ELASTICSEARCH_URL", "ELASTICSEARCH_HOST", "ELASTIC_URL", "ELASTIC_HOST", "OPENSEARCH_URL",
        "MINIO_ENDPOINT", "MINIO_HOST", "S3_ENDPOINT_URL", "S3_HOST",
        "MEMCACHED_URL", "MEMCACHED_HOST", "MEMCACHE_SERVERS",
    }
    for svc in services:
        env_map = svc.get("env_vars", {})
        svc_port = str(svc.get("port") or 3000)
        str(svc.get("name") or "service")

        for key in list(env_map.keys()):
            val = env_map.get(key)
            if not val or str(val).strip() in ("", "{{GENERATE}}", "{{FILL_ME}}") or str(val).startswith("REPLACE_WITH_"):
                if key in _ADDON_URL_KEYS:
                    continue

                key_upper = key.upper()
                if any(k in key_upper for k in ["SECRET", "KEY", "TOKEN", "PASSWORD", "AUTH_HASH", "SALT"]):
                    env_map[key] = "{{GENERATE}}"
                elif "CORS" in key_upper or "ORIGIN" in key_upper:
                    env_map[key] = f"http://localhost:{svc_port}"
                else:
                    # Do not generate random strings for non-secrets to prevent crashing
                    # typed configs (like numbers/ints). Leave empty to allow app defaults.
                    env_map[key] = ""

        svc["env_vars"] = env_map


def _apply_plan_repo_defaults(services: list[dict], repos_data: list[dict]):
    """Fill missing service branch values from GitHub repo metadata."""
    by_full_repo: dict[str, str] = {}
    by_repo_name: dict[str, str | None] = {}

    for repo_data in repos_data:
        repo_full = str(repo_data.get("repo") or "").strip().lower()
        if not repo_full:
            continue
        default_branch = str(repo_data.get("default_branch") or "main").strip() or "main"
        by_full_repo[repo_full] = default_branch

        repo_name = repo_full.split("/")[-1]
        if repo_name in by_repo_name and by_repo_name[repo_name] != default_branch:
            by_repo_name[repo_name] = None
        elif repo_name not in by_repo_name:
            by_repo_name[repo_name] = default_branch

    for svc in services:
        if not isinstance(svc, dict):
            continue
        current_branch = str(svc.get("branch") or "").strip()
        if current_branch:
            continue

        repo_ref = str(svc.get("repo") or "").strip().lower()
        fallback_branch = ""
        if repo_ref:
            fallback_branch = by_full_repo.get(repo_ref, "")
            if not fallback_branch and "/" not in repo_ref:
                fallback_branch = by_repo_name.get(repo_ref) or ""
        if not fallback_branch:
            svc_name = str(svc.get("name") or "").strip().lower()
            if svc_name:
                fallback_branch = by_repo_name.get(svc_name) or ""

        svc["branch"] = fallback_branch or "main"
