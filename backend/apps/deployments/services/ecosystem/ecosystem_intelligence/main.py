import logging
import os
import secrets
from typing import Any

from ..ecosystem_heuristics import _env_plan_map
from .addons import _ADDON_ALIASES, _coerce_addons
from .classification import _is_auth_service, _is_core_service, _is_intelligence_service
from .deploy_sequence import _build_deploy_sequence
from .helpers import _coerce_depends_on, _repo_short_name
from .secret_unification import _unify_cross_service_secrets, _unify_same_name_secrets

logger = logging.getLogger(__name__)


def _ensure_100_percent_env_coverage(services: list[dict]):
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
                from apps.cloud.services.build_constants import is_secret_env_var
                if is_secret_env_var(key_upper):
                    env_map[key] = "{{GENERATE}}"
                elif "CORS" in key_upper or "ORIGIN" in key_upper:
                    env_map[key] = f"http://localhost:{svc_port}"
                else:
                    env_map[key] = ""

        svc["env_vars"] = env_map


def _apply_plan_repo_defaults(services: list[dict], repos_data: list[dict]):
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


def _apply_generic_ecosystem_intelligence(services: list[dict]):
    deployable = [s for s in services if isinstance(s, dict) and not s.get("skip")]
    core_svc = next((s for s in deployable if _is_core_service(s)), None)
    auth_svc = next((s for s in deployable if _is_auth_service(s)), None)

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

        if stack == "django":
            addons.add("POSTGRES")
            addons.add("REDIS")
        elif stack in ["node", "nextjs", "nuxt"]:
            if any("DATABASE_URL" in k.upper() for k in env_map):
                addons.add("POSTGRES")

        svc["addons"] = sorted(addons)

        if core_svc and svc != core_svc:
            for key in list(env_map.keys()):
                key_u = key.upper()
                if any(k in key_u for k in ["API_URL", "CORE_URL", "PLATFORM_URL", "BACKEND_URL"]):
                    core_name = str(core_svc.get("name") or _repo_short_name(core_svc)).strip()
                    if not core_name:
                        continue
                    env_map[key] = f"{{{{SERVICE:{core_name}}}}}"

                    try:
                        deps = set(_coerce_depends_on(svc.get("depends_on", []) or []))
                    except TypeError as exc:
                        logger.warning("Unhashable depends_on set for svc %r (%s): %s", svc_name, svc.get("repo"), exc)
                        deps = set()
                    deps.add(core_name)
                    svc["depends_on"] = sorted(deps)

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

        for key in list(env_map.keys()):
            key_u = key.upper()
            if any(k in key_u for k in ["JWT_SECRET", "ENCRYPTION_KEY", "APP_SECRET", "GATEWAY_SECRET", "SERVICE_SECRET"]):
                current = str(env_map.get(key, ""))
                if current.startswith("{{SHARED_SECRET:"):
                    continue
                env_map[key] = f"{{{{SHARED_SECRET:{key.lower()}}}}}"

        svc_prefixes = svc.get("_env_prefixes", [])
        if not svc_prefixes:
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

        for key in list(env_map.keys()):
            key_u = key.upper()
            if any(k in key_u for k in ["AI_PROVIDER", "LLM_PROVIDER"]):
                env_map[key] = "auto"
            if any(k in key_u for k in ["OPENAI_API_KEY", "GEMINI_API_KEY", "CLAUDE_API_KEY", "GROK_API_KEY", "ANTHROPIC_API_KEY"]):
                 env_map[key] = f"{{{{SHARED_SECRET:{key.lower()}}}}}"

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

        if _is_intelligence_service(svc):
            env_map.setdefault("AI_PROVIDER", "auto")

        svc["env_vars"] = env_map

    _unify_same_name_secrets(services)

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
        for svc in deployable:
            env_map = svc.get("env_vars", {})
            if not isinstance(env_map, dict):
                continue
            for cors_key in ("CORS_ALLOWED_ORIGINS", "CSRF_TRUSTED_ORIGINS"):
                cur = str(env_map.get(cors_key, "")).strip()
                if not cur or cur in ("{{GENERATE}}", "{{FILL_ME}}"):
                    env_map[cors_key] = "*"

    for svc in deployable:
        env_map = svc.get("env_vars", {})
        if not isinstance(env_map, dict):
            continue
        if env_map.get("STRIPE_SECRET_KEY") == "{{SHARED_SECRET:secret_key}}":
            env_map["STRIPE_SECRET_KEY"] = ""
        if env_map.get("STRIPE_WEBHOOK_SECRET") == "{{SHARED_SECRET:webhook_secret}}":
            env_map["STRIPE_WEBHOOK_SECRET"] = ""

    for svc in deployable:
        if "platform" in str(svc.get("name", "")).lower():
            env_map = svc.get("env_vars", {})
            if isinstance(env_map, dict) and "PLATFORM_SECRET" not in env_map:
                env_map["PLATFORM_SECRET"] = "{{SHARED_SECRET:platform_secret}}"

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

        for key in list(env_map.keys()):
            if key in _SKIP_CLEAR:
                continue
            val = str(env_map.get(key, ""))
            if not val or val.startswith("{{") or val in ("", "{{GENERATE}}"):
                continue
            ku = key.upper()
            if len(val) >= 40 and "://" not in val:
                env_map[key] = "{{GENERATE}}"
                continue
            if len(val) >= 30 and all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for c in val):
                env_map[key] = "{{GENERATE}}"

    _svc_by_name: dict[str, dict] = {}
    for svc in deployable:
        n = str(svc.get("name") or _repo_short_name(svc)).strip().lower()
        if n:
            _svc_by_name[n] = svc
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

            is_url = any(ku.endswith(sfx) for sfx in _URL_SUFFIXES)
            if is_url and (not val or val == "{{GENERATE}}" or val == "{{FILL_ME}}"
                           or ("://" not in val and not val.startswith("{{"))):
                stem = ku
                for sfx in sorted(_URL_SUFFIXES, key=len, reverse=True):
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
                    target_name = str(matched.get("name") or _repo_short_name(matched)).strip()
                    env_map[key] = f"{{{{SERVICE:{target_name}}}}}"
                    continue

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

            for _pfx in ("PLATFORM_TO_", "GATEWAY_TO_", "IDENTITY_TO_", "POLICY_TO_"):
                if ku.startswith(_pfx) and ku.endswith("_SECRET"):
                    if not val or val == "{{GENERATE}}" or val == "{{FILL_ME}}" or "://" not in val:
                        target = ku[len(_pfx):-len("_SECRET")].lower()
                        shared_key = f"{_pfx.lower()}{target}_secret"
                        env_map[key] = f"{{{{SHARED_SECRET:{shared_key}}}}}"
                        break

    backend_stacks = {"django", "python", "rust", "go", "java", "ruby", "elixir", "php"}
    frontend_stacks = {"nextjs", "node", "nuxt"}
    backends = [s for s in deployable if s.get("stack") in backend_stacks]
    frontends = [s for s in deployable if s.get("stack") in frontend_stacks]
    if backends and frontends:
        for svc in deployable:
            env_map = svc.get("env_vars", {})
            svc_name = str(svc.get("name") or _repo_short_name(svc)).strip()
            stack = str(svc.get("stack") or "").lower()
            has_cross_ref = any(
                "{{SERVICE:" in str(v) or "API_URL" in k.upper()
                for k, v in env_map.items()
            )
            if has_cross_ref:
                continue
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
            if stack in backend_stacks and frontends:
                target = frontends[0]
                target_name = str(target.get("name") or _repo_short_name(target)).strip()
                if not target_name:
                    continue
                env_map.setdefault(
                    "CORS_ALLOWED_ORIGINS",
                    "{{{{SERVICE:{}}}}}".format(target_name),
                )

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

    for svc in deployable:
        env_map = svc.get("env_vars", {})
        svc_port = str(svc.get("port") or 3000)
        env_map["PORT"] = svc_port

    for svc in deployable:
        current_order = svc.get("deploy_order")
        if current_order is not None and current_order != 99:
            continue
        order = 50
        if _is_auth_service(svc):
            order = 10
        elif _is_core_service(svc):
            order = 20
        svc["deploy_order"] = order

    _ensure_100_percent_env_coverage(deployable)

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
                from apps.cloud.services.build_constants import is_secret_env_var
                if is_secret_env_var(key):
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

    # Host-scaled floor for non-heavy services (never 1 core).
    from apps.deployments.models.service import default_service_resources
    _base_cpu, _base_mem = default_service_resources()
    for svc in deployable:
        if svc.get("_is_heavy"):
            svc["cpu_cores"] = 3.0
            svc["memory_mb"] = 6144
        else:
            svc["cpu_cores"] = _base_cpu
            svc["memory_mb"] = _base_mem
