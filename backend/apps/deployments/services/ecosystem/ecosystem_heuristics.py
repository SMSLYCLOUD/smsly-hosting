import logging
import os
import re
import secrets
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)


def _safe_set(items: Iterable) -> set:
    """Build a set from an iterable, converting unhashable items to their str representation."""
    result: set = set()
    for item in items:
        try:
            result.add(item)
        except TypeError:
            result.add(str(item))
    return result


STACK_SIGNALS = {
    "package.json":    ("node",    3000),
    "next.config.js":  ("nextjs",  3000),
    "next.config.ts":  ("nextjs",  3000),
    "next.config.mjs": ("nextjs",  3000),
    "nuxt.config.ts":  ("nuxt",    3000),
    "requirements.txt":("python",  8000),
    "manage.py":       ("django",  8000),
    "Pipfile":         ("python",  8000),
    "pyproject.toml":  ("python",  8000),
    "Cargo.toml":      ("rust",    8080),
    "go.mod":          ("go",      8080),
    "pom.xml":         ("java",    8080),
    "build.gradle":    ("java",    8080),
    "Gemfile":         ("ruby",    3000),
    "mix.exs":         ("elixir",  4000),
    "composer.json":   ("php",     8080),
}

DB_SIGNALS = {
    "DATABASE_URL": "POSTGRES",
    "POSTGRES":     "POSTGRES",
    "REDIS_URL":    "REDIS",
    "MONGO":        "MONGODB",
    "MYSQL":        "MYSQL",
}

BUILD_STRATEGY = {
    "Dockerfile":        "dockerfile",
    "docker-compose.yml":"docker-compose",
    "docker-compose.yaml":"docker-compose",
    "Procfile":          "nixpacks",
    "nixpacks.toml":     "nixpacks",
}


def _detect_addons_from_imports(clone_dir: str) -> dict:
    """
    Scan source files in a cloned repo for import patterns that indicate
    required addons (databases, caches, queues, search engines, storage).

    Returns: {
        "addons": set of addon names,
        "api_calls": list of detected external API URLs,
        "frameworks": list of detected framework patterns,
    }
    """
    if not clone_dir or not os.path.isdir(clone_dir):
        return {"addons": set(), "api_calls": [], "frameworks": []}

    # Import → addon mapping
    IMPORT_ADDON_MAP = {
        # Python imports
        "psycopg2": "POSTGRES", "asyncpg": "POSTGRES",
        "django.db": "POSTGRES", "databases": "POSTGRES",
        "redis": "REDIS", "aioredis": "REDIS", "celery": "REDIS",
        "rq": "REDIS", "django_redis": "REDIS",
        "pymongo": "MONGODB", "motor": "MONGODB", "mongoengine": "MONGODB",
        "elasticsearch": "ELASTICSEARCH", "opensearchpy": "ELASTICSEARCH",
        "pika": "RABBITMQ", "aio_pika": "RABBITMQ", "kombu": "RABBITMQ",
        "qdrant_client": "QDRANT", "qdrant": "QDRANT",
        "minio": "MINIO",
        "mysql": "MYSQL", "pymysql": "MYSQL", "aiomysql": "MYSQL",
        # JS/TS imports
        "pg": "POSTGRES", "sequelize": "POSTGRES", "prisma": "POSTGRES",
        "typeorm": "POSTGRES", "knex": "POSTGRES",
        "ioredis": "REDIS", "bull": "REDIS", "bullmq": "REDIS",
        "mongoose": "MONGODB", "mongodb": "MONGODB",
        "@elastic/elasticsearch": "ELASTICSEARCH",
        "amqplib": "RABBITMQ",
        "@aws-sdk/client-s3": "MINIO",
    }

    addons = set()
    api_calls = []
    frameworks = []

    skip_dirs = {'node_modules', '.git', '__pycache__', 'venv', '.venv',
                 '.next', 'dist', 'build', '.cache', 'coverage'}

    for root, dirs, filenames in os.walk(clone_dir, topdown=True):
        dirs[:] = [d for d in dirs if d not in skip_dirs]

        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in ('.py', '.js', '.ts', '.tsx', '.jsx'):
                continue

            full_path = os.path.join(root, fname)
            try:
                with open(full_path, encoding='utf-8', errors='ignore') as f:
                    content = f.read(50_000)  # Cap at 50KB per file
            except OSError:
                continue

            # Check imports against addon map
            for import_name, addon in IMPORT_ADDON_MAP.items():
                if import_name in content:
                    addons.add(addon)

            # Detect external API calls
            for m in re.finditer(r'https?://[^\s\'"`,)]+', content):
                url = m.group(0)
                if not any(skip in url for skip in ['localhost', '127.0.0.1', 'example.com']):
                    api_calls.append(url[:120])

            # Detect framework patterns
            if 'FastAPI' in content or 'fastapi' in content:
                frameworks.append('fastapi')
            if 'express' in content:
                frameworks.append('express')
            if 'NestFactory' in content:
                frameworks.append('nestjs')

    return {
        "addons": addons,
        "api_calls": list(_safe_set(api_calls))[:20],  # Dedupe + cap
        "frameworks": list(_safe_set(frameworks)),
    }


def heuristic_analysis(files: list[str], clone_dir: str | None = None) -> dict:
    """Fast local analysis without AI calls. Optionally scans cloned files for env vars."""
    languages = []
    port = 3000
    addons = set()
    build = "nixpacks"
    seen_stacks = set()

    # Detect ALL stacks/languages present
    for filename, (s, p) in STACK_SIGNALS.items():
        if any(f.endswith(filename) or f == filename for f in files):
            if s not in seen_stacks:
                languages.append(s)
                seen_stacks.add(s)
                port = p  # Use port of last detected stack

    # Detect build strategy (check subdirectories too)
    for filename, strategy in BUILD_STRATEGY.items():
        if filename in files or any(f.endswith(filename) for f in files):
            build = strategy
            break

    # If a repo has a subdirectory Dockerfile (e.g., backend/Dockerfile),
    # detect it for the build strategy
    if build != "dockerfile":
        for f in files:
            if f.endswith("/Dockerfile") or f == "Dockerfile":
                build = "dockerfile"
                break

    # Detect database requirements from file names
    for f in files:
        f_lower = f.lower()
        if "docker-compose" in f_lower:
            addons.add("POSTGRES")  # common
        if "redis" in f_lower:
            addons.add("REDIS")
        if "mongo" in f_lower:
            addons.add("MONGODB")

    # ── Deep Import Scan (Code Analysis Integration) ─────────────────
    # If we have a cloned directory, scan actual imports for precise addons.
    # Initialize with an empty result so the post-block lookups below stay
    # safe when clone_dir is falsy (UnboundLocalError otherwise).
    import_scan: dict[str, Any] = {"addons": set(), "api_calls": [], "frameworks": []}
    if clone_dir:
        import_scan = _detect_addons_from_imports(clone_dir)
        addons |= set(import_scan["addons"])

    # Primary stack is the first detected, but expose all languages
    stack = languages[0] if languages else "unknown"

    # ── Env Var Detection ──
    env_vars = _detect_env_vars(files, stack, port, clone_dir)

    result = {
        "stack": stack,
        "languages": languages,
        "port": port,
        "build": build,
        "addons": list(addons),
        "env_vars": env_vars,
    }

    # Attach deep analysis metadata if available
    if import_scan["frameworks"]:
        result["detected_frameworks"] = import_scan["frameworks"]
    if import_scan["api_calls"]:
        result["external_apis"] = import_scan["api_calls"]

    return result


_STACK_ENV_DEFAULTS = {
    'nextjs': ['NEXT_PUBLIC_API_URL', 'NEXTAUTH_SECRET', 'DATABASE_URL'],
    'django': ['SECRET_KEY', 'DEBUG', 'DATABASE_URL', 'ALLOWED_HOSTS'],
    'python': ['SECRET_KEY', 'DATABASE_URL'],
    'node':   ['PORT', 'DATABASE_URL'],
    'rust':   ['PORT', 'DATABASE_URL', 'RUST_LOG'],
    'go':     ['PORT', 'DATABASE_URL'],
    'ruby':   ['RAILS_ENV', 'SECRET_KEY_BASE', 'DATABASE_URL'],
    'php':    ['APP_KEY', 'DB_CONNECTION', 'DB_HOST'],
}

_ENV_HINTS: dict[str, dict] = {
    'SECRET_KEY':          {'hint': 'Random 50+ char string', 'is_secret': True,  'required': True,  'generate': True},
    'NEXTAUTH_SECRET':     {'hint': 'Random encryption key',  'is_secret': True,  'required': True,  'generate': True},
    'JWT_SECRET':          {'hint': 'JWT signing secret',     'is_secret': True,  'required': True,  'generate': True},
    'SECRET_KEY_BASE':     {'hint': 'Rails secret key',       'is_secret': True,  'required': True,  'generate': True},
    'APP_KEY':             {'hint': 'Laravel app key',        'is_secret': True,  'required': True,  'generate': True},
    'DATABASE_URL':        {'hint': 'postgres://user:pass@host:5432/db', 'is_secret': False, 'required': True},
    'REDIS_URL':           {'hint': 'redis://localhost:6379/0', 'required': False},
    'API_KEY':             {'hint': 'Your API key',           'is_secret': True,  'required': True,  'user_required': True},
    'OPENAI_API_KEY':      {'hint': 'sk-... from platform.openai.com', 'is_secret': True, 'required': True, 'user_required': True},
    'GEMINI_API_KEY':      {'hint': 'From aistudio.google.com',        'is_secret': True, 'required': True, 'user_required': True},
    'ANTHROPIC_API_KEY':   {'hint': 'sk-ant-... from console.anthropic.com', 'is_secret': True, 'required': True, 'user_required': True},
    'STRIPE_SECRET_KEY':   {'hint': 'sk_live_... from Stripe', 'is_secret': True, 'required': True, 'user_required': True},
    'STRIPE_PUBLISHABLE_KEY': {'hint': 'pk_live_... from Stripe', 'required': True, 'user_required': True},
    'NEXT_PUBLIC_API_URL': {'hint': 'https://api.example.com', 'required': False},
    'DEBUG':               {'hint': 'False for production',   'default': 'False', 'required': False},
    'FLASK_ENV':           {'hint': 'production',             'default': 'production', 'required': False},
    'RAILS_ENV':           {'hint': 'production',             'default': 'production', 'required': False},
    'RUST_LOG':            {'hint': 'info, debug, or warn',   'default': 'info',  'required': False},
    'PORT':                {'hint': 'Listening port',         'required': False},
    'ALLOWED_HOSTS':       {'hint': 'Comma-separated or *',  'default': '*',     'required': False},
    'AI_PROVIDER':         {'hint': 'openai | grok | gemini | claude | mistral | nvidia | cloudflare | freemodel | opencode | auto', 'required': True, 'user_required': True},
    'QDRANT_PORT':         {'hint': 'Default: 6333',         'default': '6333',  'required': True},
    'QDRANT_HOST':         {'hint': 'Qdrant hostname',       'required': True,   'user_required': True},
    'SENTRY_DSN':          {'hint': 'https://...@sentry.io/...', 'is_secret': True, 'required': False, 'user_required': True},
}


def _detect_env_vars(files: list[str], stack: str, port: int,
                     clone_dir: str | None = None) -> list:
    """Detect and enrich env vars from stack defaults + .env.example + config patterns."""

    # 1. Start with stack defaults
    var_keys: list[str] = list(_STACK_ENV_DEFAULTS.get(stack, []))

    # ── File-name-based detection (works even without clone_dir) ─────────
    # Recognise key config files by name and inject their well-known env vars.
    basenames = {os.path.basename(f) for f in files}

    if '.env.example' in basenames or '.env.sample' in basenames or '.env.template' in basenames or '.env.production' in basenames:
        var_keys.extend([
            'DATABASE_URL', 'SECRET_KEY', 'REDIS_URL', 'API_KEY',
            'OPENAI_API_KEY', 'GEMINI_API_KEY',
        ])

    if 'docker-compose.yml' in basenames or 'docker-compose.yaml' in basenames:
        var_keys.extend(['COMPOSE_PROJECT_NAME', 'EXTERNAL_PORT'])

    if 'settings.py' in basenames or 'config.py' in basenames:
        var_keys.extend(['ALLOWED_HOSTS', 'CORS_ALLOWED_ORIGINS', 'CSRF_TRUSTED_ORIGINS'])

    if 'next.config.js' in basenames or 'next.config.ts' in basenames or 'next.config.mjs' in basenames:
        var_keys.extend(['NEXT_PUBLIC_API_URL', 'NEXTAUTH_URL', 'NEXTAUTH_SECRET'])

    # 2. Scan .env.example / .env.sample / .env.template from cloned files
    if clone_dir:
        env_example_files = [f for f in files
                             if os.path.basename(f) in (
                                 '.env.example', '.env.sample', '.env.template', '.env',
                                 '.env.production', '.env.local', '.env.development',
                                 '.env.staging', '.env.test',
                             )]
        for ef in env_example_files:
            try:
                full_path = os.path.join(clone_dir, ef)
                with open(full_path, errors='replace') as fh:
                    for line in fh:
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        if '=' in line:
                            key = line.split('=', 1)[0].strip()
                            if key and re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', key):
                                var_keys.append(key)
            except Exception:
                pass

        # NEW: Scan docker-compose files for environment variables
        compose_files = [f for f in files if os.path.basename(f) in ('docker-compose.yml', 'docker-compose.yaml', 'compose.yml', 'compose.yaml')]
        for cf in compose_files:
            try:
                import yaml
                full_path = os.path.join(clone_dir, cf)
                with open(full_path, errors='replace') as fh:
                    compose_data = yaml.safe_load(fh)
                if compose_data and isinstance(compose_data, dict):
                    services = compose_data.get('services', {})
                    if isinstance(services, dict):
                        for svc_name, svc_def in services.items():
                            if isinstance(svc_def, dict):
                                env = svc_def.get('environment')
                                if isinstance(env, dict):
                                    for k in env:
                                        if k and re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', str(k)):
                                            var_keys.append(str(k))
                                elif isinstance(env, list):
                                    for item in env:
                                        if isinstance(item, str) and '=' in item:
                                            k = item.split('=', 1)[0].strip()
                                            if k and re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', k):
                                                var_keys.append(k)
                                        elif isinstance(item, str): # if it's just VAR meaning pass-through
                                            if item and re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', item):
                                                var_keys.append(item)
            except Exception:
                pass

        # 3. Scan config.py / settings.py for os.environ / os.getenv patterns
        config_candidates = [f for f in files
                             if os.path.basename(f) in ('config.py', 'settings.py')]
        for cf in config_candidates:
            try:
                full_path = os.path.join(clone_dir, cf)
                with open(full_path, errors='replace') as fh:
                    content = fh.read()
                patterns = re.findall(
                    r"os\.(?:environ\[?['\"]|environ\.get\(['\"]|getenv\(['\"])([A-Z_][A-Z0-9_]*)",
                    content
                )
                var_keys.extend(patterns)
            except Exception:
                pass

    # 4. Deduplicate while preserving order
    seen: set = set()
    unique_keys: list[str] = []
    for k in var_keys:
        ku = k.upper()
        if ku not in seen:
            seen.add(ku)
            unique_keys.append(k)

    # 5. Enrich with hints
    result = []
    for key in unique_keys:
        hints = _ENV_HINTS.get(key, {})
        obj: dict[str, Any] = {
            'key': key,
            'hint': hints.get('hint', ''),
            'required': hints.get('required', True),
            'is_secret': hints.get('is_secret',
                                   any(w in key.lower() for w in ('key', 'secret', 'password', 'token'))),
            'user_required': hints.get('user_required', False),
        }

        # Auto-generate secrets
        if hints.get('generate'):
            obj['default'] = secrets.token_urlsafe(48)
            obj['user_required'] = False
        elif 'default' in hints:
            obj['default'] = hints['default']

        # PORT gets detected port
        if key == 'PORT':
            obj['default'] = str(port)

        result.append(obj)

    return result


_KNOWN_ENV_SUFFIXES = frozenset({
    "_URL", "_HOST", "_PORT", "_KEY", "_SECRET", "_TOKEN", "_PASSWORD",
    "_PASS", "_USER", "_USERNAME", "_DB", "_DATABASE", "_SCHEMA",
    "_ENDPOINT", "_API_KEY", "_API_SECRET", "_CLIENT_ID", "_CLIENT_SECRET",
    "_BUCKET", "_REGION", "_ZONE", "_DOMAIN", "_EMAIL", "_DSN",
    "_PATH", "_DIR", "_FILE", "_DIRECTORY", "_ID", "_ARN",
})


def _is_well_known_env_var(name: str) -> bool:
    upper = name.upper().strip()
    if not upper:
        return False
    if upper in _ENV_HINTS:
        return True
    for suffix in _KNOWN_ENV_SUFFIXES:
        if upper.endswith(suffix):
            return True
    if any(upper.startswith(p) for p in ("NEXT_PUBLIC_", "REACT_APP_", "VITE_", "NUXT_", "GATSBY_")):
        return True
    return False


def _merge_deep_env(env_map: dict[str, str], deep_env: dict[str, list[str]]) -> dict[str, str]:
    """Merge deep-scanned env vars into env_map, filtering to only well-known vars."""
    added = 0
    for var_name in deep_env:
        upper_key = var_name.upper()
        if upper_key in env_map:
            continue
        if not _is_well_known_env_var(upper_key):
            continue
        fill = "{{GENERATE}}" if any(
            w in upper_key for w in ("SECRET", "KEY", "TOKEN", "PASSWORD")
        ) else ""
        env_map[upper_key] = fill
        added += 1
    if added:
        logger.debug("Merged %d deep-scanned env vars into heuristic env", added)
    return env_map


def _env_plan_map(raw_env: Any) -> dict[str, str]:
    """
    Normalize environment variable payloads to a flat dict.

    Accepts either:
    - {"KEY": "value"}
    - [{"key": "KEY", "default": "value", "is_secret": true, ...}, ...]
    """
    if isinstance(raw_env, dict):
        env_map: dict[str, str] = {}
        for key, value in raw_env.items():
            key_text = str(key).strip().upper()
            if not key_text:
                continue
            if isinstance(value, dict):
                entry = value
                raw_val = entry.get("value")
                if raw_val in (None, "", "{{FILL_ME}}") or str(raw_val).startswith("REPLACE_WITH_"):
                    value = entry.get("default")
                else:
                    value = raw_val
                if str(value or "").strip() in ("{{FILL_ME}}") or str(value or "").startswith("REPLACE_WITH_"):
                    value = ""
                if not value and (entry.get("generate") or entry.get("is_secret")):
                    value = "{{GENERATE}}"
            else:
                # Plain string value — preserve {{GENERATE}} sentinel
                if str(value).strip() in ("{{FILL_ME}}") or str(value).startswith("REPLACE_WITH_"):
                    value = ""
            env_map[key_text] = "" if value is None else str(value)
        return env_map

    env_map = {}
    if not isinstance(raw_env, list):
        return env_map

    for entry in raw_env:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "").strip().upper()
        if not key:
            continue

        default_val = entry.get("default")
        if default_val not in (None, "", "{{GENERATE}}", "{{FILL_ME}}") and not str(default_val or "").startswith("REPLACE_WITH_"):
            env_map[key] = str(default_val)
            continue

        if entry.get("generate") or entry.get("is_secret"):
            env_map[key] = "{{GENERATE}}"
            continue

        env_map[key] = ""

    return env_map
