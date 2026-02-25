"""
Zero-Config AI Ecosystem Deployment Engine.

Scans all of a user's GitHub repos, uses AI to analyze each repo's stack,
builds a cross-repo dependency graph, and produces a deploy plan that
can be executed with zero manual configuration.
"""

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import requests

from apps.intelligence.providers import ask_with_fallback

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# GitHub helpers
# ──────────────────────────────────────────────────────────────────────────────

def _github_headers(token: str) -> dict:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }


def fetch_all_repos(token: str) -> List[dict]:
    """Fetch ALL repos visible to *token* (paginated)."""
    repos: List[dict] = []
    page = 1
    while True:
        resp = requests.get(
            "https://api.github.com/user/repos",
            headers=_github_headers(token),
            params={"per_page": 100, "page": page, "sort": "updated"},
            timeout=15,
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        repos.extend(batch)
        page += 1
        if len(batch) < 100:
            break
    return repos


def fetch_repo_tree(token: str, full_name: str, branch: str = "main") -> List[str]:
    """Fetch the top-level file tree for a repo (plus key nested files)."""
    # Try the default branch first, fall back to master
    for ref in [branch, "main", "master"]:
        resp = requests.get(
            f"https://api.github.com/repos/{full_name}/git/trees/{ref}",
            headers=_github_headers(token),
            params={"recursive": "1"},
            timeout=15,
        )
        if resp.status_code == 200:
            tree = resp.json().get("tree", [])
            return [item["path"] for item in tree if item["type"] == "blob"]
    return []


def fetch_file_content(token: str, full_name: str, path: str) -> Optional[str]:
    """Download a single file's text content (for env var detection, etc.)."""
    resp = requests.get(
        f"https://api.github.com/repos/{full_name}/contents/{path}",
        headers=_github_headers(token),
        params={"ref": "main"},
        timeout=15,
    )
    if resp.status_code != 200:
        return None
    import base64
    data = resp.json()
    if data.get("encoding") == "base64":
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    return data.get("content", "")


# ──────────────────────────────────────────────────────────────────────────────
# Local (heuristic) stack detection — fast, no AI needed
# ──────────────────────────────────────────────────────────────────────────────

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


def heuristic_analysis(files: List[str], clone_dir: str = None,
                       scan_depth: int = 30) -> dict:
    """
    Local analysis without AI calls.

    scan_depth controls how deep we go:
      10 = Quick scan: filenames only (heuristics + env var detection)
      20 = Deep scan: + docker-compose parser, DB identity, cross-service refs
      30 = Full scan: same as 20 (AI is triggered separately in scan_and_analyze)
    """
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

    # Primary stack is the first detected, but expose all languages
    stack = languages[0] if languages else "unknown"

    # ── Env Var Detection ──
    env_vars = _detect_env_vars(files, stack, port, clone_dir)

    # ── Deep Code Scanning (Layer 1: "inside the code") ──
    # Only runs at scan_depth >= 20 (skipped for Tier 10 quick scans)
    if scan_depth >= 20 and clone_dir:
        compose_info = _scan_docker_compose(files, clone_dir)
        db_identity = _detect_database_identity(files, clone_dir)
        service_refs = _detect_cross_service_refs(files, clone_dir)
    else:
        compose_info = {}
        db_identity = {}
        service_refs = {}

    # Merge addons discovered from docker-compose
    for addon_type in compose_info.get('addons', []):
        addons.add(addon_type)

    # Override port if docker-compose specifies one
    if compose_info.get('port'):
        port = compose_info['port']

    return {
        "stack": stack,
        "languages": languages,
        "port": port,
        "build": build,
        "addons": list(addons),
        "env_vars": env_vars,
        # Deep scan results (used by ecosystem linker at deploy time)
        "database_name": db_identity.get('db_name', ''),
        "database_user": db_identity.get('db_user', ''),
        "database_driver": db_identity.get('db_driver', 'postgresql'),
        "env_format": db_identity.get('env_format', 'url'),
        "service_refs": service_refs,
        "compose_services": compose_info.get('services', {}),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Env Var Intelligence
# ──────────────────────────────────────────────────────────────────────────────

# Per-stack default env vars
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

# Rich hints for common env vars
_ENV_HINTS: Dict[str, dict] = {
    'SECRET_KEY':          {'hint': 'Random 50+ char string', 'is_secret': True,  'required': True,  'generate': True},
    'NEXTAUTH_SECRET':     {'hint': 'Random encryption key',  'is_secret': True,  'required': True,  'generate': True},
    'JWT_SECRET':          {'hint': 'JWT signing secret',     'is_secret': True,  'required': True,  'generate': True},
    'SECRET_KEY_BASE':     {'hint': 'Rails secret key',       'is_secret': True,  'required': True,  'generate': True},
    'APP_KEY':             {'hint': 'Laravel app key',        'is_secret': True,  'required': True,  'generate': True},
    'DATABASE_URL':        {'hint': 'postgres://user:pass@host:5432/db', 'is_secret': True, 'required': True},
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
    'AI_PROVIDER':         {'hint': 'openai | gemini | anthropic | auto', 'required': True, 'user_required': True},
    'QDRANT_PORT':         {'hint': 'Default: 6333',         'default': '6333',  'required': True},
    'QDRANT_HOST':         {'hint': 'Qdrant hostname',       'required': True,   'user_required': True},
    'SENTRY_DSN':          {'hint': 'https://...@sentry.io/...', 'is_secret': True, 'required': False, 'user_required': True},
}


def _detect_env_vars(files: List[str], stack: str, port: int,
                     clone_dir: str = None) -> list:
    """Detect and enrich env vars from stack defaults + .env.example + config patterns."""
    import re as _re
    import secrets

    # 1. Start with stack defaults
    var_keys: List[str] = list(_STACK_ENV_DEFAULTS.get(stack, []))

    # 2. Scan .env.example / .env.sample / .env.template from cloned files
    if clone_dir:
        env_example_files = [f for f in files
                             if os.path.basename(f) in ('.env.example', '.env.sample', '.env.template')]
        for ef in env_example_files:
            try:
                full_path = os.path.join(clone_dir, ef)
                with open(full_path, 'r', errors='replace') as fh:
                    for line in fh:
                        line = line.strip()
                        if not line or line.startswith('#'):
                            continue
                        if '=' in line:
                            key = line.split('=', 1)[0].strip()
                            if key and _re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', key):
                                var_keys.append(key)
            except Exception:
                pass

        # 3. Scan config.py / settings.py for os.environ / os.getenv patterns
        config_candidates = [f for f in files
                             if os.path.basename(f) in ('config.py', 'settings.py', '.env')]
        for cf in config_candidates:
            try:
                full_path = os.path.join(clone_dir, cf)
                with open(full_path, 'r', errors='replace') as fh:
                    content = fh.read()
                patterns = _re.findall(
                    r"os\.(?:environ\[?['\"]|environ\.get\(['\"]|getenv\(['\"])([A-Z_][A-Z0-9_]*)",
                    content
                )
                var_keys.extend(patterns)
            except Exception:
                pass

    # 4. Deduplicate while preserving order
    seen: set = set()
    unique_keys: List[str] = []
    for k in var_keys:
        ku = k.upper()
        if ku not in seen:
            seen.add(ku)
            unique_keys.append(k)

    # 5. Enrich with hints
    result = []
    for key in unique_keys:
        hints = _ENV_HINTS.get(key, {})
        obj: Dict[str, Any] = {
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


# ──────────────────────────────────────────────────────────────────────────────
# Deep Code Scanners — "inside the code everywhere and everytime"
# ──────────────────────────────────────────────────────────────────────────────

def _scan_docker_compose(files: List[str], clone_dir: str) -> dict:
    """
    Parse docker-compose.yml to extract service topology.

    Reads the actual YAML to understand:
    - Service definitions (names, ports, depends_on)
    - Environment variables (inline and from x-common-env anchors)
    - Cross-service internal URLs (http://container:port patterns)
    - Database and Redis connection strings
    - Shared infrastructure signals (postgres, redis, rabbitmq images)

    Returns:
        {
            'services': {
                'backend': {'port': 8001, 'depends': ['db', 'redis'], 'env': {...}},
            },
            'addons': ['POSTGRES', 'REDIS'],
            'port': 8001,
            'shared_env': {'DATABASE_URL': '...'},
            'service_urls': {'BACKEND_URL': 'backend:8001'},
        }
    """
    import re as _re

    result: Dict[str, Any] = {
        'services': {},
        'addons': [],
        'port': None,
        'shared_env': {},
        'service_urls': {},
    }

    # Find docker-compose files
    compose_files = [f for f in files if 'docker-compose' in f.lower()
                     and (f.endswith('.yml') or f.endswith('.yaml'))]
    if not compose_files:
        return result

    try:
        import yaml
    except ImportError:
        logger.debug("PyYAML not available — skipping docker-compose scan")
        return result

    addons = set()

    for cf in compose_files:
        try:
            full_path = os.path.join(clone_dir, cf)
            with open(full_path, 'r', errors='replace') as fh:
                content = fh.read()

            # Parse YAML (use safe_load to avoid code execution)
            data = yaml.safe_load(content)
            if not isinstance(data, dict):
                continue

            # Extract x-common-env (YAML anchor for shared env)
            for key, val in data.items():
                if key.startswith('x-') and isinstance(val, dict):
                    result['shared_env'].update(val)

            services_block = data.get('services', {})
            if not isinstance(services_block, dict):
                continue

            for svc_name, svc_def in services_block.items():
                if not isinstance(svc_def, dict):
                    continue

                svc_info: Dict[str, Any] = {
                    'port': None,
                    'depends': [],
                    'env': {},
                    'image': '',
                }

                # Image → detect infra
                image = svc_def.get('image', '')
                if isinstance(image, str):
                    svc_info['image'] = image
                    img_lower = image.lower()
                    if 'postgres' in img_lower:
                        addons.add('POSTGRES')
                    if 'redis' in img_lower:
                        addons.add('REDIS')
                    if 'rabbitmq' in img_lower or 'rabbit' in img_lower:
                        addons.add('RABBITMQ')
                    if 'mongo' in img_lower:
                        addons.add('MONGODB')
                    if 'mysql' in img_lower or 'mariadb' in img_lower:
                        addons.add('MYSQL')

                # Ports
                ports = svc_def.get('ports', [])
                if isinstance(ports, list) and ports:
                    for p in ports:
                        port_str = str(p)
                        # Parse "8001:8001" or "8001"
                        parts = port_str.split(':')
                        try:
                            container_port = int(parts[-1].split('/')[0])
                            svc_info['port'] = container_port
                            # Use last non-infra service port as main port
                            if not any(infra in svc_name.lower()
                                       for infra in ('postgres', 'redis', 'rabbit', 'mongo')):
                                result['port'] = container_port
                        except (ValueError, IndexError):
                            pass

                # depends_on
                depends = svc_def.get('depends_on', [])
                if isinstance(depends, list):
                    svc_info['depends'] = depends
                elif isinstance(depends, dict):
                    svc_info['depends'] = list(depends.keys())

                # Environment → extract env vars
                env_block = svc_def.get('environment', {})
                if isinstance(env_block, dict):
                    svc_info['env'] = {
                        str(k): str(v) for k, v in env_block.items()
                        if k and v is not None
                    }
                elif isinstance(env_block, list):
                    for item in env_block:
                        if '=' in str(item):
                            k, v = str(item).split('=', 1)
                            svc_info['env'][k.strip()] = v.strip()

                # Detect internal service URLs in env values
                for env_key, env_val in svc_info['env'].items():
                    if isinstance(env_val, str):
                        # Match http://service-name:port patterns
                        url_match = _re.search(
                            r'https?://([a-z0-9_-]+):(\d+)',
                            env_val, _re.IGNORECASE,
                        )
                        if url_match and env_key.upper().endswith(('_URL', '_HOST', '_SERVICE')):
                            result['service_urls'][env_key] = env_val

                result['services'][svc_name] = svc_info

        except Exception as exc:
            logger.debug("Failed to parse %s: %s", cf, exc)
            continue

    result['addons'] = list(addons)
    return result


def _detect_database_identity(files: List[str], clone_dir: str) -> dict:
    """
    Determine which specific database this service uses.

    Reads settings.py, docker-compose.yml, and .env files to find:
    - The database NAME (e.g., 'marketer', 'lina', 'smsly_backend')
    - The database USER
    - The database DRIVER (postgresql, postgresql+asyncpg, mysql)
    - Whether the app expects DATABASE_URL or individual POSTGRES_* vars

    Returns:
        {
            'db_name': 'marketer',
            'db_user': 'marketer',
            'db_driver': 'postgresql',
            'env_format': 'url',  # 'url' or 'individual'
        }
    """
    import re as _re

    result: Dict[str, Any] = {
        'db_name': '',
        'db_user': '',
        'db_driver': 'postgresql',
        'env_format': 'url',
    }

    # Strategy 1: Parse DATABASE_URL from docker-compose or .env files
    db_url_pattern = _re.compile(
        r'DATABASE_URL\s*[:=]\s*["\']?'
        r'(postgres(?:ql)?(?:\+asyncpg)?://([^:]+):([^@]*)@[^/]+/([^"\'\s]+))',
        _re.IGNORECASE,
    )

    # Strategy 2: Check for individual POSTGRES_* vars (env_format = 'individual')
    individual_pattern = _re.compile(
        r'POSTGRES_DB\s*[:=]\s*["\']?([a-zA-Z0-9_]+)',
        _re.IGNORECASE,
    )
    individual_user_pattern = _re.compile(
        r'POSTGRES_USER\s*[:=]\s*["\']?([a-zA-Z0-9_]+)',
        _re.IGNORECASE,
    )

    # Strategy 3: Check settings.py for env format detection
    uses_dj_database_url = False
    uses_individual_vars = False

    # Scan config files in priority order
    config_files = []
    for f in files:
        basename = os.path.basename(f).lower()
        if basename in ('settings.py', 'config.py'):
            config_files.insert(0, f)  # highest priority
        elif 'docker-compose' in basename and (f.endswith('.yml') or f.endswith('.yaml')):
            config_files.append(f)
        elif basename in ('.env', '.env.example', '.env.production', '.env.sample'):
            config_files.append(f)

    for cf in config_files:
        try:
            full_path = os.path.join(clone_dir, cf)
            with open(full_path, 'r', errors='replace') as fh:
                content = fh.read()

            # Check for dj_database_url usage → env_format = 'url'
            if 'dj_database_url' in content or 'DATABASE_URL' in content:
                uses_dj_database_url = True

            # Check for individual POSTGRES_* usage → env_format = 'individual'
            if 'POSTGRES_HOST' in content or 'POSTGRES_DB' in content:
                if 'os.environ' in content or 'os.getenv' in content:
                    uses_individual_vars = True

            # Extract DATABASE_URL
            match = db_url_pattern.search(content)
            if match:
                full_url, user, _, db_name = match.groups()
                if not result['db_name']:
                    result['db_name'] = db_name.strip("'\"")
                if not result['db_user'] and user:
                    result['db_user'] = user.strip("'\"")
                # Detect async driver
                if '+asyncpg' in full_url:
                    result['db_driver'] = 'postgresql+asyncpg'

            # Extract individual POSTGRES_DB
            match = individual_pattern.search(content)
            if match and not result['db_name']:
                result['db_name'] = match.group(1)

            match = individual_user_pattern.search(content)
            if match and not result['db_user']:
                result['db_user'] = match.group(1)

        except Exception:
            continue

    # Determine env_format
    if uses_individual_vars and not uses_dj_database_url:
        result['env_format'] = 'individual'
    elif uses_dj_database_url:
        result['env_format'] = 'url'

    return result


def _detect_cross_service_refs(files: List[str], clone_dir: str) -> dict:
    """
    Find environment variables that reference other services.

    Scans settings.py, .env files, and docker-compose for patterns like:
    - SMSLY_BACKEND_URL = os.environ.get('SMSLY_BACKEND_URL', 'http://localhost:8000')
    - BACKEND_URL: http://smsly-backend:8001
    - NEXT_PUBLIC_API_URL=http://api.example.com

    Returns:
        {
            'SMSLY_BACKEND_URL': {
                'default': 'http://localhost:8000',
                'references_service': 'smsly-backend',
            },
            ...
        }
    """
    import re as _re

    refs: Dict[str, dict] = {}

    # Patterns for URL env vars
    url_var_pattern = _re.compile(
        r"""
        (?:                                          # Match context
            os\.(?:environ\.get|getenv)\s*\(\s*      #   os.environ.get( or os.getenv(
            ['"]([A-Z_]+_URL|[A-Z_]+_HOST)['"]       #   'VAR_NAME'
            (?:\s*,\s*['"]?(https?://[^'")\s]+))?    #   , 'default_value' (optional)
        |                                            # OR
            ([A-Z_]+_URL|[A-Z_]+_SERVICE_URL)        #   VAR_NAME
            \s*[:=]\s*                               #   = or :
            ['"]?(https?://[a-z0-9._-]+(?::\d+)?)   #   http://hostname:port
        )
        """,
        _re.VERBOSE | _re.IGNORECASE,
    )

    # Service name pattern in URLs
    service_name_pattern = _re.compile(r'https?://([a-z][a-z0-9_-]+)(?::\d+)?', _re.IGNORECASE)

    # Scan relevant files
    scan_files = [f for f in files if os.path.basename(f).lower() in (
        'settings.py', 'config.py', '.env', '.env.example',
        '.env.sample', '.env.production', '.env.template',
    ) or 'docker-compose' in f.lower()]

    for sf in scan_files:
        try:
            full_path = os.path.join(clone_dir, sf)
            with open(full_path, 'r', errors='replace') as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue

                    matches = url_var_pattern.findall(line)
                    for match in matches:
                        # match is a tuple from the groups
                        var_name = match[0] or match[2]
                        url_value = match[1] or match[3]

                        if not var_name:
                            continue

                        var_name = var_name.upper()
                        ref_info: Dict[str, Any] = {}

                        if url_value:
                            ref_info['default'] = url_value
                            # Extract service name from URL
                            svc_match = service_name_pattern.match(url_value)
                            if svc_match:
                                hostname = svc_match.group(1)
                                # Filter out localhost/127.0.0.1
                                if hostname not in ('localhost', '127', '0'):
                                    ref_info['references_service'] = hostname

                        if var_name not in refs:
                            refs[var_name] = ref_info
        except Exception:
            continue

    return refs


# ──────────────────────────────────────────────────────────────────────────────
# AI-Powered Ecosystem Analysis
# ──────────────────────────────────────────────────────────────────────────────

ECOSYSTEM_PROMPT = """You are an expert DevOps architect. Analyze these GitHub repositories and create a zero-config deployment plan.

For each repo, determine:
1. The tech stack (django, nextjs, node, rust, go, etc.)
2. The port it listens on
3. Build strategy (dockerfile or nixpacks)
4. Required addons (POSTGRES, REDIS, MONGODB, etc.)
5. Essential environment variables it needs
6. Which OTHER repos it depends on (e.g., a frontend that needs a backend API URL)
7. Parse docker-compose.yml files to understand:
   - Which services share a database server (one Postgres, multiple databases)
   - Internal service URLs (http://container-name:port patterns)
   - Shared environment variable blocks (x-common-env anchors)
   - Redis database number isolation (redis://host:6379/0 vs /1 vs /4)
8. For ecosystems with shared infrastructure:
   - Identify the "infra layer" (postgres, redis, rabbitmq)
   - Map each service to its SPECIFIC database on the shared Postgres
   - Detect init-databases.sql or similar bootstrap scripts
   - Track which services share the same database vs have their own

Then determine the overall deployment order (databases/addons first, then backends, then frontends).

Return ONLY valid JSON matching this exact structure:
{
  "services": [
    {
      "repo": "owner/repo-name",
      "name": "short-name",
      "stack": "django",
      "port": 8000,
      "build": "nixpacks",
      "addons": ["POSTGRES"],
      "env_vars": {"DATABASE_URL": "{{POSTGRES_URL}}", "SECRET_KEY": "{{GENERATE}}"},
      "depends_on": ["other-repo-name"],
      "deploy_order": 1,
      "database_name": "marketer",
      "database_user": "marketer",
      "redis_db": 4,
      "consumes_services": {"SMSLY_BACKEND_URL": "smsly-backend"},
      "exposes_as": {"BACKEND_URL": "http://{self}:8001"}
    }
  ],
  "addons": [
    {"type": "POSTGRES", "shared_by": ["repo-name-1", "repo-name-2"]}
  ],
  "deploy_sequence": ["addons", "repo-name-backend", "repo-name-frontend"]
}

Use {{PLACEHOLDER}} for auto-generated values:
- {{POSTGRES_URL}} → auto-provisioned Postgres connection string
- {{REDIS_URL}} → auto-provisioned Redis connection string
- {{GENERATE}} → auto-generate a random secure string
- {{SERVICE:repo-name}} → internal URL to another deployed service

IMPORTANT:
- Skip repos that are clearly not deployable (docs-only, config-only, forks of big projects)
- Mark forked repos with "skip": true
- Assign deploy_order numbers (lower = deploy first)
"""


def analyze_ecosystem(repos_data: List[dict]) -> dict:
    """
    Use AI to analyze all repos together and produce a deploy plan.

    repos_data: list of {"repo": "owner/name", "files": [...], "description": "...", "heuristic": {...}}
    """
    # Build the input summary for AI
    repo_summaries = []
    for rd in repos_data:
        summary = f"\n### {rd['repo']}\n"
        summary += f"Description: {rd.get('description', 'No description')}\n"
        summary += f"Default branch: {rd.get('default_branch', 'main')}\n"
        summary += f"Heuristic detection: {rd.get('heuristic', {})}\n"
        # Show key files (limit to 50 most relevant)
        files = rd.get("files", [])
        key_files = [f for f in files if any(f.endswith(ext) for ext in (
            ".json", ".toml", ".yml", ".yaml", ".py", ".js", ".ts",
            ".rs", ".go", ".rb", ".php", "Dockerfile", "Procfile",
            ".env.example", ".env.sample"
        ))][:50]
        if not key_files:
            key_files = files[:30]
        summary += f"Key files: {', '.join(key_files)}\n"
        repo_summaries.append(summary)

    full_prompt = "Here are all the repositories to analyze:\n" + "\n".join(repo_summaries)

    response_text, provider = ask_with_fallback(full_prompt, system_prompt=ECOSYSTEM_PROMPT)

    # Parse JSON from response
    import json
    try:
        # Try to extract JSON from markdown code blocks
        if "```json" in response_text:
            json_str = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            json_str = response_text.split("```")[1].split("```")[0].strip()
        else:
            json_str = response_text.strip()
        plan = json.loads(json_str)
        if isinstance(plan, dict) and isinstance(plan.get("services"), list):
            for svc in plan["services"]:
                if isinstance(svc, dict):
                    svc["env_vars"] = _env_plan_map(svc.get("env_vars", {}))
        plan["ai_provider"] = provider
        return plan
    except (json.JSONDecodeError, IndexError) as e:
        logger.error("Failed to parse AI ecosystem response: %s", e)
        logger.error("Raw response: %s", response_text[:1000])
        # Fall back to heuristic-only plan
        return _build_heuristic_plan(repos_data, str(e))


def _env_plan_map(raw_env: Any) -> Dict[str, str]:
    """
    Normalize environment variable payloads to a flat dict.

    Accepts either:
    - {"KEY": "value"}
    - [{"key": "KEY", "default": "value", "is_secret": true, ...}, ...]
    """
    if isinstance(raw_env, dict):
        return {
            str(k).strip().upper(): "" if v is None else str(v)
            for k, v in raw_env.items()
            if str(k).strip()
        }

    env_map: Dict[str, str] = {}
    if not isinstance(raw_env, list):
        return env_map

    for entry in raw_env:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "").strip().upper()
        if not key:
            continue

        default_val = entry.get("default")
        if default_val not in (None, ""):
            env_map[key] = str(default_val)
            continue

        if entry.get("generate") or entry.get("is_secret"):
            env_map[key] = "{{GENERATE}}"
            continue

        env_map[key] = ""

    return env_map


def _build_heuristic_plan(repos_data: List[dict], error: str = "") -> dict:
    """Build a basic deploy plan from heuristics when AI fails."""
    services = []
    all_addons = {}
    order = 1

    for rd in repos_data:
        h = rd.get("heuristic", {})
        if h.get("stack") == "unknown":
            continue

        name = rd["repo"].split("/")[-1]
        svc = {
            "repo": rd["repo"],
            "name": name,
            "stack": h.get("stack", "unknown"),
            "port": h.get("port", 3000),
            "build": h.get("build", "nixpacks"),
            "addons": h.get("addons", []),
            "env_vars": _env_plan_map(h.get("env_vars", {})),
            "depends_on": [],
            "deploy_order": order,
        }

        # Track shared addons
        for addon in svc["addons"]:
            if addon not in all_addons:
                all_addons[addon] = []
            all_addons[addon].append(name)

        services.append(svc)
        order += 1

    # Sort: backends before frontends
    backend_stacks = {"django", "python", "rust", "go", "java", "ruby", "elixir", "php"}
    backends = [s for s in services if s["stack"] in backend_stacks]
    frontends = [s for s in services if s["stack"] not in backend_stacks]

    sorted_services = []
    for i, s in enumerate(backends + frontends, 1):
        s["deploy_order"] = i
        sorted_services.append(s)

    addons_list = [{"type": k, "shared_by": v} for k, v in all_addons.items()]

    deploy_sequence = ["addons"] + [s["name"] for s in sorted_services]

    return {
        "services": sorted_services,
        "addons": addons_list,
        "deploy_sequence": deploy_sequence,
        "ai_provider": f"Heuristic (AI parse failed: {error})" if error else "Heuristic",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Full Scan Pipeline
# ──────────────────────────────────────────────────────────────────────────────

def scan_and_analyze(token: str, scan_depth: int = 30) -> dict:
    """
    Full pipeline: fetch all repos → analyze each → deploy plan.

    scan_depth controls accuracy vs speed:
      10 = Quick scan (~1s): heuristics only, no AI
      20 = Deep scan (~3s): heuristics + code parsing, no AI
      30 = Full AI scan (~15s): everything + AI ecosystem analysis

    Returns the deploy plan dict ready for the frontend.
    """
    logger.info("Starting ecosystem scan (depth=%d)...", scan_depth)

    # 1. Fetch all repos
    all_repos = fetch_all_repos(token)
    logger.info("Found %d repositories", len(all_repos))

    # 2. Analyze each repo
    repos_data = []
    for repo in all_repos:
        full_name = repo["full_name"]
        description = repo.get("description", "") or ""
        default_branch = repo.get("default_branch", "main")
        is_fork = repo.get("fork", False)

        # Skip forks and empty repos
        if is_fork or repo.get("size", 0) == 0:
            continue

        # Fetch file tree
        files = fetch_repo_tree(token, full_name, default_branch)
        if not files:
            continue

        # Heuristic analysis (depth-controlled)
        heuristic = heuristic_analysis(files, scan_depth=scan_depth)

        repos_data.append({
            "repo": full_name,
            "description": description,
            "default_branch": default_branch,
            "files": files,
            "heuristic": heuristic,
            "private": repo.get("private", False),
        })

    if not repos_data:
        return {
            "services": [],
            "addons": [],
            "deploy_sequence": [],
            "ai_provider": "None",
            "message": "No deployable repositories found.",
        }

    # 3. AI ecosystem analysis (only at depth 30)
    if scan_depth >= 30:
        logger.info("Analyzed %d repos, sending to AI for ecosystem plan...", len(repos_data))
        plan = analyze_ecosystem(repos_data)
    else:
        logger.info("Analyzed %d repos (depth=%d, skipping AI)", len(repos_data), scan_depth)
        plan = _build_heuristic_plan(repos_data, error=f"Scan depth {scan_depth}: AI analysis skipped")

    # 4. Enrich with metadata
    plan["total_repos_scanned"] = len(all_repos)
    plan["deployable_repos"] = len(repos_data)

    return plan
