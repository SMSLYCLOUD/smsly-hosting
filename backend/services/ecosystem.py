"""
Zero-Config AI Ecosystem Deployment Engine.

Scans all of a user's GitHub repos, uses AI to analyze each repo's stack,
builds a cross-repo dependency graph, and produces a deploy plan that
can be executed with zero manual configuration.
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple, Iterable

import requests
import tempfile
import subprocess
import shutil

from apps.intelligence.providers import ask_with_fallback
from apps.intelligence.services.env_intelligence import EnvironmentIntelligenceService

logger = logging.getLogger(__name__)

# SEC-ZT-009: GitHub API rate-limit tracking
# Remaining calls are checked before each paginated fetch.
_GITHUB_API_BASE = "https://api.github.com"
_RATE_LIMIT_WARN_THRESHOLD = 100  # Warn below this many remaining calls


def _safe_set(items: Iterable) -> set:
    """Build a set from an iterable, converting unhashable items to their str representation."""
    result: set = set()
    for item in items:
        try:
            result.add(item)
        except TypeError:
            result.add(str(item))
    return result


def _check_github_rate_limit(headers: dict) -> tuple[int, int]:
    """
    Check GitHub API rate-limit remaining. Returns (remaining, limit).
    Logs warning when remaining is low.
    """
    try:
        resp = requests.get(
            f"{_GITHUB_API_BASE}/rate_limit",
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            core = data.get("resources", {}).get("core", {})
            remaining = core.get("remaining", 0)
            limit = core.get("limit", 5000)
            reset = core.get("reset", 0)
            if remaining < _RATE_LIMIT_WARN_THRESHOLD:
                reset_time = time.strftime(
                    "%H:%M:%S UTC", time.gmtime(reset)
                ) if reset else "unknown"
                logger.warning(
                    "SEC-ZT-009: GitHub API rate-limit low: %d/%d remaining "
                    "(resets at %s). Consider reducing scan_window_days.",
                    remaining, limit, reset_time,
                )
            return remaining, limit
    except requests.RequestException as e:
        logger.warning("SEC-ZT-009: Could not check GitHub rate-limit: %s", e)
    return 0, 0


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
    headers = _github_headers(token)
    # SEC-ZT-009: Check rate-limit before starting
    remaining, limit = _check_github_rate_limit(headers)
    if remaining < 10:
        logger.error(
            "SEC-ZT-009: GitHub API rate-limit exhausted (%d/%d). "
            "Cannot scan repos.", remaining, limit,
        )
        return []

    repos: List[dict] = []
    page = 1
    while True:
        # SEC-ZT-009: Re-check rate-limit every 10 pages
        if page % 10 == 0:
            remaining, _ = _check_github_rate_limit(headers)
            if remaining < 10:
                logger.warning(
                    "SEC-ZT-009: Stopping pagination early due to low rate-limit "
                    "(%d remaining at page %d)", remaining, page,
                )
                break

        resp = requests.get(
            f"{_GITHUB_API_BASE}/user/repos",
            headers=headers,
            params={"per_page": 100, "page": page, "sort": "updated"},
            timeout=15,
        )

        # If rate-limited, GitHub returns 403 with a rate-limit message
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            logger.error("SEC-ZT-009: GitHub rate-limit hit during repo fetch")
            break

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
    headers = _github_headers(token)
    # Try the default branch first, fall back to master
    for ref in [branch, "main", "master"]:
        resp = requests.get(
            f"{_GITHUB_API_BASE}/repos/{full_name}/git/trees/{ref}",
            headers=headers,
            params={"recursive": "1"},
            timeout=15,
        )
        if resp.status_code == 403 and "rate limit" in resp.text.lower():
            logger.warning("SEC-ZT-009: Rate-limited fetching tree for %s", full_name)
            return []
        if resp.status_code == 200:
            tree = resp.json().get("tree", [])
            return [item["path"] for item in tree if item["type"] == "blob"]
    return []


def fetch_file_content(token: str, full_name: str, path: str) -> Optional[str]:
    """Download a single file's text content (for env var detection, etc.)."""
    headers = _github_headers(token)
    resp = requests.get(
        f"{_GITHUB_API_BASE}/repos/{full_name}/contents/{path}",
        headers=headers,
        params={"ref": "main"},
        timeout=15,
    )
    if resp.status_code == 403 and "rate limit" in resp.text.lower():
        logger.warning("SEC-ZT-009: Rate-limited fetching file %s from %s", path, full_name)
        return None
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
        "psycopg2": "POSTGRES", "asyncpg": "POSTGRES", "sqlalchemy": "POSTGRES",
        "django.db": "POSTGRES", "databases": "POSTGRES",
        "redis": "REDIS", "aioredis": "REDIS", "celery": "REDIS",
        "rq": "REDIS", "django_redis": "REDIS",
        "pymongo": "MONGODB", "motor": "MONGODB", "mongoengine": "MONGODB",
        "elasticsearch": "ELASTICSEARCH", "opensearchpy": "ELASTICSEARCH",
        "pika": "RABBITMQ", "aio_pika": "RABBITMQ", "kombu": "RABBITMQ",
        "qdrant_client": "QDRANT", "qdrant": "QDRANT",
        "minio": "MINIO", "boto3": "MINIO",
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
                with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read(50_000)  # Cap at 50KB per file
            except OSError:
                continue

            # Check imports against addon map
            for import_name, addon in IMPORT_ADDON_MAP.items():
                if import_name in content:
                    addons.add(addon)

            # Detect external API calls
            import re
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


def heuristic_analysis(files: List[str], clone_dir: str = None) -> dict:
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
    # If we have a cloned directory, scan actual imports for precise addons
    import_scan = _detect_addons_from_imports(clone_dir)
    addons |= import_scan["addons"]

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
    'AI_PROVIDER':         {'hint': 'openai | grok | gemini | claude | auto', 'required': True, 'user_required': True},
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
# AI-Powered Ecosystem Analysis
# ──────────────────────────────────────────────────────────────────────────────

ECOSYSTEM_PROMPT = """You are the Supreme DevOps Architect of the CloudNeuron AI Senate. Your mission is to architect a 100% stable, zero-config, high-performance ecosystem of microservices from multiple repositories.
    
    ### ADVANCED CONNECTIVITY REASONING:
    1. CIRCULAR RESOLUTION: If Service A needs Service B and vice-versa, use internal Docker DNS names (e.g., http://service-b:8000) for internal traffic and public placeholders for client-side traffic.
    2. SHARED SECRET VAULT: Identify variables like JWT_SECRET, AUTH_KEY, or ENCRYPTION_TOKEN. If multiple services use them, assign the SAME {{SHARED_SECRET:name}} placeholder so they can communicate.
    3. CORS & OAUTH: Automatically detect if a backend needs a frontend's URL for `CORS_ALLOWED_ORIGINS` or `OAUTH_CALLBACK_URL`. Use {{SERVICE:frontend-repo}} to link them.
    4. DATABASE CONSOLIDATION: If multiple services need POSTGRES, prefer a single shared instance with unique database names ({{POSTGRES_URL}}/service_name) unless they are strictly isolated.
    
    ### CRITICAL RULES:
    1. EXHAUSTIVE RESOLUTION: Never leave an environment variable empty. 
    2. DETERMINISTIC LINKING: Use {{SERVICE:repo-name}} for service URLs, {{POSTGRES_URL}} for databases, and {{GENERATE}} for unique secrets.
    3. DEPLOY ORDER: Rank services by dependency depth. Infrastructure -> Core APIs -> Background Workers -> Frontends.
    4. STRICT TYPE CONSTRAINTS — ALL array fields must contain ONLY strings, NEVER objects/dicts. Violating this will crash the deployment system.
    
    ### STRICT TYPE RULES — VIOLATIONS WILL CRASH THE SYSTEM:
    - "depends_on" MUST be an array of strings ONLY. NEVER objects. WRONG: [{"name": "svc-a"}] RIGHT: ["svc-a"]
    - "shared_by" MUST be an array of strings ONLY. NEVER objects. WRONG: [{"service": "svc-a"}] RIGHT: ["svc-a"]
    - Service-level "addons" (inside each service object) MUST be an array of strings ONLY. WRONG: [{"type": "POSTGRES"}] RIGHT: ["POSTGRES"]
    - "deploy_sequence" MUST be an array of strings ONLY. NEVER objects.
    - "env_vars" values MUST be strings ONLY. NEVER objects, arrays, or numbers. WRONG: {"KEY": {"value": "v"}} RIGHT: {"KEY": "{{PLACEHOLDER}}"}
    - Each top-level addon entry in the "addons" array must have "type" as a string and "shared_by" as an array of strings.
    - NEVER nest objects inside arrays. Every element of every array must be a primitive (string, number, boolean) or the specific object shape shown below.
    
    Return ONLY valid JSON matching this EXACT structure — every field and type must be followed precisely:
    {
      "ecosystem_name": "string",
      "services": [
        {
          "repo": "owner/repo-name",
          "name": "short-name",
          "stack": "django|nextjs|node|python|etc",
          "port": 8000,
          "env_vars": {
            "DATABASE_URL": "{{POSTGRES_URL}}",
            "API_URL": "{{SERVICE:backend-repo}}",
            "FRONTEND_URL": "{{SERVICE:frontend-repo}}",
            "JWT_SECRET": "{{SHARED_SECRET:auth_token}}"
          },
          "depends_on": ["other-repo-name"],
          "deploy_order": 1
        }
      ],
      "addons": [
        {"type": "POSTGRES", "shared_by": ["repo-a", "repo-b"]}
      ],
      "deploy_sequence": ["addons", "service-a", "service-b"]
    }
    """


def analyze_ecosystem(repos_data: List[dict], github_token: str = None, ai_provider: str = None) -> dict:
    """
    Use AI Senate to analyze all repos together in a temporary workspace.
    Clones repos, scans for cross-repo dependencies, and produces a plan.
    """
    import json
    import re as _re

    # 1. Create a temporary workspace for the analysis
    with tempfile.TemporaryDirectory(prefix="cloud-ecosystem-") as workspace_dir:
        logger.info(f"Created ecosystem workspace: {workspace_dir}")
        
        # 2. Clone all repos into the workspace
        for rd in repos_data:
            repo_full = rd.get('repo')
            if not repo_full:
                continue
            
            repo_name = repo_full.split('/')[-1]
            target_dir = os.path.join(workspace_dir, repo_name)
            
            success = _clone_repo(repo_full, target_dir, github_token)
            if success:
                rd['clone_dir'] = target_dir
                rd['repo_name_short'] = repo_name
            else:
                logger.warning(f"Failed to clone {repo_full} for analysis")

        # 3. Aggressive Multi-Repo Scanning
        for rd in repos_data:
            clone_dir = rd.get('clone_dir')
            if clone_dir:
                from apps.intelligence.scanner import RepoScanner
                scanner = RepoScanner(clone_dir)
                scan = scanner.scan()
                rd['env_vars_context'] = scan.get('env_vars_context', {})
                rd['stack'] = scan.get('stack', rd.get('stack', 'unknown'))
                
                # Intelligent Config Extraction (Context Size Optimization)
                configs_summary = {}
                priority_files = ['docker-compose.yml', 'docker-compose.yaml', 'Dockerfile', 'package.json', 'requirements.txt', 'pyproject.toml', 'Cargo.toml', 'go.mod']
                
                raw_configs = scan.get('configs', {})
                critical_configs = [(k, v) for k, v in raw_configs.items() if any(p in os.path.basename(k) for p in priority_files)]
                
                # Sort by priority, then limit to top 4 files to prevent token bloat
                def _sort_key(item):
                    bname = os.path.basename(item[0])
                    for i, pf in enumerate(priority_files):
                        if pf in bname: return i
                    return 99
                
                critical_configs.sort(key=_sort_key)
                
                for k, v in critical_configs[:4]:
                    bname = os.path.basename(k)
                    if 'package.json' in bname:
                        import json as _json
                        try:
                            parsed = _json.loads(v)
                            slim = {
                                "scripts": parsed.get("scripts", {}),
                                "dependencies": parsed.get("dependencies", {}),
                            }
                            configs_summary[k] = _json.dumps(slim, indent=2)
                        except Exception:
                            configs_summary[k] = v[:300] + "\n...[truncated]"
                    elif 'Dockerfile' in bname or 'docker-compose' in bname:
                        # Keep full logic but strip comments and blanks
                        lines = [line for line in v.split('\n') if line.strip() and not line.strip().startswith('#')]
                        configs_summary[k] = '\n'.join(lines)[:800]
                    else:
                        configs_summary[k] = v[:300] + "\n...[truncated]"
                        
                rd['configs_summary'] = configs_summary
                rd['structure'] = scan.get('structure', '')

        # 4. Build the Cross-Repo Intelligence Brief
        repo_summaries = []
        for rd in repos_data:
            summary = f"\n### REPO: {rd['repo']} (Name: {rd.get('repo_name_short', 'unknown')})\n"
            summary += f"Description: {rd.get('description', 'No description')}\n"
            summary += f"Stack: {rd.get('stack', 'unknown')}\n"
            
            # Detect resource intensity
            is_heavy = False
            for file_path, content in rd.get('configs_summary', {}).items():
                if any(lib in content.lower() for lib in ['torch', 'tensorflow', 'nvidia', 'java', 'spring', 'elasticsearch']):
                    is_heavy = True
                    break
            rd['is_heavy'] = is_heavy
            summary += f"Resource Intensity: {'HEAVY (Requires 2GB+ RAM)' if is_heavy else 'STANDARD'}\n"

            if rd.get('env_vars_context'):
                summary += "Expected Env Vars (with Logic Hints):\n"
                for var, ctxs in rd['env_vars_context'].items():
                    ctx = ctxs[0] if ctxs else "No context"
                    summary += f"- {var}: {ctx}\n"
            
            if rd.get('configs_summary'):
                summary += "Critical Config Analysis:\n"
                for path, snippet in rd['configs_summary'].items():
                    if any(p in path for p in ['Dockerfile', 'compose', 'package', 'requirements', 'settings', 'config', 'urls']):
                        summary += f"#### FILE: {path}\n```\n{snippet}\n```\n"

            repo_summaries.append(summary)

        # 5. Global Linkage & Discovery Analysis
        cross_links = []
        repo_names = [rd.get('repo_name_short') for rd in repos_data if rd.get('repo_name_short')]
        for rd in repos_data:
            cd = rd.get('clone_dir')
            if not cd: continue
            
            # Look for environment variable overlaps
            current_vars = _safe_set(rd.get('env_vars_context', {}).keys())
            for other_rd in repos_data:
                if other_rd['repo'] == rd['repo']: continue
                other_vars = _safe_set(other_rd.get('env_vars_context', {}).keys())
                common = current_vars.intersection(other_vars)
                if common:
                    cross_links.append(f"SHARED STATE: {rd['repo']} and {other_rd['repo']} share env keys: {list(common)}")

            # Grep for other repo names in this repo's configs/env (Service Discovery)
            for other in repo_names:
                if other == rd.get('repo_name_short'): continue
                for path, content in rd.get('configs_summary', {}).items():
                    if other in content.lower():
                        cross_links.append(f"DEPENDENCY HINT: {rd['repo']} mentions {other} in {path} (Potential URL target)")

        try:
            cross_links_deduped = _safe_set(cross_links)
            brief_header = "ECOSYSTEM DISCOVERY HINTS:\n" + "\n".join(cross_links_deduped) if cross_links_deduped else ""
        except TypeError as exc:
            logger.warning("Unhashable cross_links entry: %s", exc)
            cross_links_safe = [str(x) for x in cross_links]
            cross_links_deduped = _safe_set(cross_links_safe)
            brief_header = "ECOSYSTEM DISCOVERY HINTS:\n" + "\n".join(cross_links_deduped) if cross_links_deduped else ""
        full_prompt = f"### ECOSYSTEM ARCHITECTURAL BRIEF\n{brief_header}\n\n"
        full_prompt += "### REPOSITORY DETAILS\n" + "\n".join(repo_summaries)

        # 6. Call AI Senate
        response_text, provider = ask_with_fallback(full_prompt, system_prompt=ECOSYSTEM_PROMPT, provider_id=ai_provider)

    # 7. Parse and structure the plan (Workspace is now deleted)
    try:
        # Intelligently extract JSON block by finding the outermost braces
        start_idx = response_text.find('{')
        end_idx = response_text.rfind('}')
        
        if start_idx == -1 or end_idx == -1 or start_idx > end_idx:
            raise ValueError("No JSON found in Senate response")
            
        json_str = response_text[start_idx:end_idx+1]
        plan = json.loads(json_str)
        if isinstance(plan, dict) and isinstance(plan.get("services"), list):
            # Ensure each service is a dict with sanitized list fields
            sanitized_services = []
            for svc in plan["services"]:
                if not isinstance(svc, dict):
                    continue
                _normalize_service_plan_fields(svc)
                sanitized_services.append(svc)
            plan["services"] = sanitized_services
            
            _apply_plan_repo_defaults(plan["services"], repos_data)
            _apply_generic_ecosystem_intelligence(plan["services"])
            plan["addons"] = _rebuild_addons_manifest(plan["services"], plan.get("addons", []))
            plan["deploy_sequence"] = _build_deploy_sequence(plan["services"])
        
        plan["ai_provider"] = provider
        return plan
        
    except Exception as e:
        logger.error("Failed to parse AI ecosystem response: %s", e)
        # Fall back to heuristic-only plan
        return _build_heuristic_plan(repos_data, str(e))


def analyze_ecosystem_chunked(repos_data: List[dict], github_token: str = None, ai_provider: str = None, chunk_size: int = 4) -> dict:
    """
    Analyzes repos in batches of `chunk_size` to prevent token limits.
    After accumulating the partial plans, it runs a final AI synthesis pass
    to fix cross-repo links and consolidate addons.
    """
    import json
    
    global_services = []
    global_addons_map = {}
    
    # Process in chunks
    chunks = [repos_data[i:i + chunk_size] for i in range(0, len(repos_data), chunk_size)]
    
    for i, chunk in enumerate(chunks):
        logger.info(f"Processing ecosystem chunk {i+1}/{len(chunks)}")
        try:
            from celery import current_task
            if current_task:
                current_task.update_state(
                    state='PROGRESS', 
                    meta={'state': f'Processing batch {i+1} of {len(chunks)}...'}
                )
        except Exception:
            pass
        
        # Note: We reuse analyze_ecosystem here, which will create a temporary workspace
        # and do the AI scan for just these 3-4 repos.
        plan = analyze_ecosystem(chunk, github_token, ai_provider)
        
        services = plan.get("services", [])
        if not isinstance(services, list):
            services = []
        # Defensive: skip non-dict entries from AI responses
        for svc in services:
            if isinstance(svc, dict):
                global_services.append(svc)
        
        for addon in plan.get("addons", []):
            if isinstance(addon, dict):
                addon_types = _coerce_addons(addon)
                if addon_types:
                    atype = addon_types[0]
                    try:
                        shared = _coerce_depends_on(addon.get("shared_by", []))
                    except TypeError as exc:
                        logger.warning("Unhashable shared_by for addon %r: %s", addon, exc)
                        shared = []
                    global_addons_map.setdefault(atype, set()).update(shared)
                    
    # Rebuild preliminary addons
    global_addons = [{"type": k, "shared_by": list(v)} for k, v in global_addons_map.items()]
    
    # Final AI Synthesis Pass if there was more than one chunk
    if len(chunks) > 1:
        synthesis_prompt = f"""
        You are the Senate Architect performing a FINAL SYNTHESIS pass.
        We have processed a massive ecosystem in batches. Here is the combined JSON plan of all services and addons.
        
        YOUR JOB:
        1. Resolve any cross-repo dependencies. If Service A needs the URL of Service B, ensure Service A's env vars use {{{{SERVICE:service-b}}}}.
        2. Consolidate addons (e.g. ensure only one POSTGRES if they should share).
        3. Ensure 100% env var coverage.
        4. FULL DEPLOY ORDER AUTHORITY: You have complete power to restructure the "deploy_order" and "deploy_sequence" from scratch to ensure a successful deployment (e.g., Auth/Identity -> Core API -> Gateways -> Frontends).
        
        CURRENT COMBINED PLAN:
        ```json
        {json.dumps({{"services": global_services, "addons": global_addons}}, indent=2)}
        ```
        
        CRITICAL TYPE RULES — violation will crash the system:
        - ALL array fields ("depends_on", "shared_by", service-level "addons", "deploy_sequence") must contain ONLY strings, NEVER objects.
        - "env_vars" values must be strings ONLY, never objects or arrays.
        - Every service in "services" must be a flat object; no arrays within arrays.
        
        Return ONLY valid JSON matching this exact structure:
        {{
          "ecosystem_name": "Synthesized Ecosystem",
          "services": [...],
          "addons": [...]
        }}
        """
        try:
            from apps.intelligence.providers import ask_with_fallback
            response_text, provider = ask_with_fallback(synthesis_prompt, system_prompt=ECOSYSTEM_PROMPT, provider_id=ai_provider)
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}')
            if start_idx != -1 and end_idx != -1 and start_idx <= end_idx:
                json_str = response_text[start_idx:end_idx+1]
                synth_plan = json.loads(json_str)
                raw_svcs = synth_plan.get("services")
                if isinstance(raw_svcs, list):
                    global_services = [s for s in raw_svcs if isinstance(s, dict)]
                raw_addons = synth_plan.get("addons")
                if isinstance(raw_addons, list):
                    global_addons = [a for a in raw_addons if isinstance(a, dict)]
        except Exception as e:
            logger.warning(f"Synthesis pass failed, using raw merged plan: {e}")

    # Strictly sanitize services: strip non-dicts, normalize each, build a clean list
    sanitized_services = []
    for svc in global_services:
        if not isinstance(svc, dict):
            continue
        try:
            _normalize_service_plan_fields(svc)
            sanitized_services.append(svc)
        except Exception as exc:
            logger.warning("Skipping unprocessable service %r: %s", svc.get("repo", "?"), exc)
    global_services = sanitized_services
    
    try:
        _apply_plan_repo_defaults(global_services, repos_data)
        _apply_generic_ecosystem_intelligence(global_services)
    except TypeError as exc:
        logger.warning("TypeError during ecosystem intelligence processing: %s", exc)
    except Exception as exc:
        logger.warning("Unexpected error during ecosystem intelligence processing: %s", exc)
    
    try:
        final_addons = _rebuild_addons_manifest(global_services, global_addons)
    except Exception as exc:
        logger.warning("Addon manifest rebuild failed: %s", exc)
        final_addons = []
    
    try:
        deploy_sequence = _build_deploy_sequence(global_services)
    except Exception as exc:
        logger.warning("Deploy sequence build failed: %s", exc)
        deploy_sequence = ["addons"]
    
    return {
        "ecosystem_name": "SMSLY Auto-Generated Ecosystem",
        "services": global_services,
        "addons": final_addons,
        "deploy_sequence": deploy_sequence,
        "ai_provider": ai_provider or "auto"
    }


def _env_plan_map(raw_env: Any) -> Dict[str, str]:
    """
    Normalize environment variable payloads to a flat dict.

    Accepts either:
    - {"KEY": "value"}
    - [{"key": "KEY", "default": "value", "is_secret": true, ...}, ...]
    """
    if isinstance(raw_env, dict):
        env_map: Dict[str, str] = {}
        for key, value in raw_env.items():
            key_text = str(key).strip().upper()
            if not key_text:
                continue
            if isinstance(value, dict):
                entry = value
                value = (
                    entry.get("value")
                    if entry.get("value") not in (None, "")
                    else entry.get("default")
                )
                if value in (None, "") and (entry.get("generate") or entry.get("is_secret")):
                    value = "{{GENERATE}}"
            env_map[key_text] = "" if value is None else str(value)
        return env_map

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


# ──────────────────────────────────────────────────────────────────────────────
# Generic Ecosystem Intelligence Helpers
# ──────────────────────────────────────────────────────────────────────────────

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


def _coerce_depends_on(raw_depends: Any) -> List[str]:
    """Normalize depends_on payload to a flat list."""
    tokens: List[str] = []
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


def _append_tokens(tokens: List[str], raw: Any, preferred_keys: Tuple[str, ...]) -> None:
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
            elif isinstance(value, (str, int, float)):
                _append_tokens(tokens, value, preferred_keys)
            elif isinstance(value, (list, tuple, set)):
                _append_tokens(tokens, value, preferred_keys)
            elif isinstance(value, dict):
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


def _dedupe_preserving_order(values: List[str]) -> List[str]:
    seen = set()
    deduped: List[str] = []
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


def _coerce_addons(raw_addons: Any) -> List[str]:
    """Normalize addon declarations to a deduped list of addon type strings."""
    tokens: List[str] = []
    try:
        _append_tokens(tokens, raw_addons, ("type", "addon", "name", "service", "value"))
    except TypeError as exc:
        logger.warning("_coerce_addons failed for value %r: %s", raw_addons, exc)
        return []
    return _dedupe_preserving_order(
        [addon for addon in (_normalize_addon_token(token) for token in tokens) if addon]
    )


def _build_deploy_sequence(services: List[dict]) -> List[str]:
    """Build deploy sequence names from ordered, non-skipped services."""
    ordered = sorted(
        [svc for svc in services if isinstance(svc, dict) and not svc.get("skip")],
        key=lambda svc: (_safe_order(svc.get("deploy_order"), 99), str(svc.get("name") or _repo_short_name(svc))),
    )
    return ["addons"] + [str(svc.get("name") or _repo_short_name(svc)) for svc in ordered]


def _rebuild_addons_manifest(services: List[dict], existing_addons: Any) -> List[dict]:
    """Rebuild addon shared_by map from service-level addon declarations."""
    addon_map: Dict[str, set] = {}

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
                addon_map.setdefault(addon_type, set()).add(service_name)
            except TypeError:
                logger.warning("Unhashable addon_type or service_name: %r / %r", addon_type, service_name)

    try:
        return [
            {"type": addon_type, "shared_by": sorted(shared_by)}
            for addon_type, shared_by in sorted(addon_map.items())
        ]
    except TypeError as exc:
        logger.warning("Unhashable key in addon_map: %s", exc)
        return []


def _apply_generic_ecosystem_intelligence(services: List[dict]):
    """
    Elite Level 5: Zero-Hardcoding Service Discovery.
    Analyzes the 'functional intent' of each service to build a generic mesh.
    """
    deployable = [s for s in services if isinstance(s, dict) and not s.get("skip")]
    # 0. Role Discovery
    core_svc = next((s for s in deployable if _is_core_service(s)), None)
    auth_svc = next((s for s in deployable if _is_auth_service(s)), None)

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
            if any("DATABASE_URL" in k.upper() for k in env_map.keys()):
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

        # 3. Global Secret Synchronization
        for key in list(env_map.keys()):
            key_u = key.upper()
            if any(k in key_u for k in ["JWT_SECRET", "ENCRYPTION_KEY", "APP_SECRET", "GATEWAY_SECRET"]):
                # Assign a shared secret placeholder so they all get the same value across the cluster
                env_map[key] = f"{{{{SHARED_SECRET:{key.lower()}}}}}"
            
            # AI Intelligence Inheritance
            if any(k in key_u for k in ["AI_PROVIDER", "LLM_PROVIDER"]):
                env_map[key] = "auto"
            
            if any(k in key_u for k in ["OPENAI_API_KEY", "GEMINI_API_KEY", "CLAUDE_API_KEY", "GROK_API_KEY", "ANTHROPIC_API_KEY"]):
                 env_map[key] = f"{{{{SHARED_SECRET:{key.lower()}}}}}"

        # 4. Standard Database Injection
        if any(k in str(svc.get("addons", [])).upper() for k in ["POSTGRES", "DATABASE"]):
            env_map.setdefault("DATABASE_URL", "{{POSTGRES_URL}}")
        if "REDIS" in str(svc.get("addons", [])).upper():
            env_map.setdefault("REDIS_URL", "{{REDIS_URL}}")

        # 4.5 Intelligence Service Specialization
        if _is_intelligence_service(svc):
            env_map.setdefault("AI_PROVIDER", "auto")
            # If it's an intelligence service, it almost certainly needs a vector DB or similar
            if "QDRANT" in str(svc.get("addons", [])).upper() or "VECTOR" in str(svc.get("addons", [])).upper():
                 env_map.setdefault("QDRANT_URL", "{{QDRANT_URL}}")

        svc["env_vars"] = env_map

    # 5. Dependency Depth Sorting
    # Auth (10) > Core (20) > Others (50)
    for svc in deployable:
        order = 50
        if _is_auth_service(svc):
            order = 10
        elif _is_core_service(svc):
            order = 20
        svc["deploy_order"] = order

    # 6. Elite 100% Exhaustive Sweep
    _ensure_100_percent_env_coverage(deployable)


def _ensure_100_percent_env_coverage(services: List[dict]):
    """
    Guarantees that NO environment variable is left empty.
    Forces production-ready values or clear placeholders for every key.
    """
    for svc in services:
        env_map = svc.get("env_vars", {})
        
        # Scan for any null, empty, or missing values
        for key in list(env_map.keys()):
            val = env_map.get(key)
            if not val or str(val).strip() == "":
                # Fallback Logic:
                if any(k in key.upper() for k in ["SECRET", "KEY", "TOKEN", "PASSWORD", "AUTH_HASH"]):
                    env_map[key] = "{{GENERATE}}"
                elif any(k in key.upper() for k in ["URL", "HOST", "ENDPOINT"]):
                    env_map[key] = "http://localhost" # Safe fallback placeholder
                else:
                    env_map[key] = f"REPLACE_WITH_PRODUCTION_{key.upper()}"
        
        svc["env_vars"] = env_map

    # Final sorting for deploy sequence
    ordered = sorted(
        services,
        key=lambda s: (
            _safe_order(s.get("deploy_order"), 99),
            str(s.get("name") or _repo_short_name(s)),
        ),
    )
    for index, service in enumerate(ordered, 1):
        service["deploy_order"] = index


def _apply_plan_repo_defaults(services: List[dict], repos_data: List[dict]):
    """Fill missing service branch values from GitHub repo metadata."""
    by_full_repo: Dict[str, str] = {}
    by_repo_name: Dict[str, str | None] = {}

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


def _build_heuristic_plan(repos_data: List[dict], error: str = "") -> dict:
    """Build a basic deploy plan from heuristics when AI fails."""
    services = []
    order = 1

    for rd in repos_data:
        h = rd.get("heuristic", {})
        if h.get("stack") == "unknown":
            continue

        name = rd["repo"].split("/")[-1]
        svc = {
            "repo": rd["repo"],
            "name": name,
            "branch": str(rd.get("default_branch") or "main"),
            "stack": h.get("stack", "unknown"),
            "port": h.get("port", 3000),
            "build": h.get("build", "nixpacks"),
            "addons": h.get("addons", []),
            "env_vars": _env_plan_map(h.get("env_vars", {})),
            "depends_on": [],
            "deploy_order": order,
        }

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

    _apply_generic_ecosystem_intelligence(sorted_services)
    addons_list = _rebuild_addons_manifest(sorted_services, [])
    deploy_sequence = _build_deploy_sequence(sorted_services)

    return {
        "services": sorted_services,
        "addons": addons_list,
        "deploy_sequence": deploy_sequence,
        "ai_provider": f"Heuristic (AI parse failed: {error})" if error else "Heuristic",
    }


# ──────────────────────────────────────────────────────────────────────────────
# Full Scan Pipeline
# ──────────────────────────────────────────────────────────────────────────────

def scan_and_analyze(token: str, ai_provider: str = None, selected_repos: list = None) -> dict:
    """
    Full pipeline: fetch all repos → analyze each → AI ecosystem plan.
    If selected_repos is provided, only processes those specific repositories.

    Returns the deploy plan dict ready for the frontend.
    """
    logger.info("Starting ecosystem scan...")
    try:
        return _scan_and_analyze_impl(token, ai_provider=ai_provider, selected_repos=selected_repos)
    except TypeError as exc:
        logger.exception("Ecosystem scan failed with unhashable type error: %s", exc)
        return {
            "error": f"Scan failed: {str(exc)}. This is usually caused by unexpected AI response data.",
            "services": [],
            "addons": [],
            "deploy_sequence": [],
            "ai_provider": "None",
        }
    except Exception as exc:
        logger.exception("Ecosystem scan failed unexpectedly: %s", exc)
        return {
            "error": f"Scan failed: {str(exc)}",
            "services": [],
            "addons": [],
            "deploy_sequence": [],
            "ai_provider": "None",
        }


def _scan_and_analyze_impl(token: str, ai_provider: str = None, selected_repos: list = None) -> dict:
    """Internal implementation of scan_and_analyze."""
    # 1. Fetch all repos
    all_repos = fetch_all_repos(token)
    logger.info("Found %d repositories", len(all_repos))
    
    # Filter by user selection if provided
    if selected_repos is not None:
        if isinstance(selected_repos, list):
            all_repos = [r for r in all_repos if r.get("full_name") in selected_repos]
        elif isinstance(selected_repos, str):
            all_repos = [r for r in all_repos if r.get("full_name") == selected_repos]
        else:
            logger.warning("selected_repos is unexpected type %s, skipping filter", type(selected_repos).__name__)
        logger.info("Filtered down to %d selected repositories", len(all_repos))

    # 2. Analyze each repo
    repos_data = []
    scan_warnings = []
    for repo in all_repos:
        full_name = repo["full_name"]
        description = repo.get("description", "") or ""
        default_branch = repo.get("default_branch", "main")
        is_fork = repo.get("fork", False)

        # Skip forks and empty repos
        if is_fork or repo.get("size", 0) == 0:
            continue

        # Fetch file tree. A single inaccessible repo should not fail the
        # whole ecosystem scan.
        try:
            files = fetch_repo_tree(token, full_name, default_branch)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Skipping %s during ecosystem scan: %s", full_name, exc)
            scan_warnings.append(f"{full_name}: {exc}")
            continue
        if not files:
            continue

        # Quick heuristic analysis
        heuristic = heuristic_analysis(files)

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

    logger.info("Analyzed %d repos, sending to AI for ecosystem plan...", len(repos_data))

    # 3. AI ecosystem analysis (CHUNKED)
    plan = analyze_ecosystem_chunked(repos_data, github_token=token, ai_provider=ai_provider)
    
    # Propagate AI preference to individual services
    actual_provider = plan.get("ai_provider")
    services = plan.get("services", [])
    for svc in services:
        if isinstance(svc, dict):
            # Ensure runtime environment has the provider selection
            env = svc.get("env_vars", {})
            if "AI_PROVIDER" not in env:
                # If user selected a specific provider, force it. Otherwise use the Senate's choice.
                env["AI_PROVIDER"] = ai_provider if (ai_provider and ai_provider != "auto") else actual_provider
            svc["env_vars"] = env

    plan["total_repos_scanned"] = len(all_repos)
    plan["deployable_repos"] = len(repos_data)
    plan["scan_warning_count"] = len(scan_warnings)
    if scan_warnings:
        plan["scan_warnings"] = scan_warnings[:20]

    return plan


def sync_ecosystem_envs(project_id: str) -> dict:
    """
    Exhaustive sync of all environment variables for a project ecosystem.
    Uses AI Senate to re-analyze every service in the project and push fresh linking/secrets.
    """
    from apps.deployments.models import Service, Project, EnvironmentVariable
    from django.db import transaction
    import secrets

    try:
        project = Project.objects.get(id=project_id)
        services = Service.objects.filter(project=project, status='ACTIVE')
        
        if not services.exists():
            return {"status": "error", "message": "No active services found in this project to sync."}

        # 1. Prepare data for AI analysis
        repos_data = []
        for s in services:
            repos_data.append({
                'repo': s.repository_url.split('github.com/')[-1] if s.repository_url else s.name,
                'name': s.name,
                'clone_dir': getattr(s, 'local_path', None), # Assume local path if available
                'stack': getattr(s, 'stack', 'unknown'),
                'description': getattr(s, 'description', '')
            })

        # 2. Trigger AI Ecosystem Analysis
        logger.info("Triggering AI Ecosystem Analysis for project %s (%d services)", project.name, len(services))
        plan = analyze_ecosystem(repos_data)
        
        if not plan or "services" not in plan:
            return {"status": "error", "message": "AI Senate failed to produce a valid ecosystem plan."}

        # 3. Persist the plan (Sync All)
        with transaction.atomic():
            for svc_plan in plan["services"]:
                svc_name = svc_plan.get("name")
                service = next((s for s in services if s.name == svc_name), None)
                if not service: continue

                plan_envs = svc_plan.get("env_vars", {})
                for key, val in plan_envs.items():
                    # Placeholder resolution
                    final_val = val
                    if val == "{{GENERATE}}":
                        final_val = secrets.token_hex(32)
                    elif str(val).startswith("{{SERVICE:"):
                        # Keep placeholder for runtime resolution or resolve now if possible
                        target_repo = val.replace("{{SERVICE:", "").replace("}}", "")
                        target_svc = next((s for s in services if s.name == target_repo or s.repository_url and target_repo in s.repository_url), None)
                        if target_svc:
                            final_val = f"http://{target_svc.name}:{target_svc.internal_port}"

                    # Update or create
                    EnvironmentVariable.objects.update_or_create(
                        service=service,
                        key=key,
                        defaults={
                            "value": final_val,
                            "is_secret": val == "{{GENERATE}}" or any(k in key.upper() for k in ["SECRET", "KEY", "TOKEN", "PASSWORD"]),
                            "source": "SYSTEM"
                        }
                    )

        return {
            "status": "success", 
            "message": f"Ecosystem sync complete for {len(services)} services. AI Provider: {plan.get('ai_provider', 'unknown')}",
            "plan": plan
        }

    except Exception as e:
        logger.exception("Failed to sync ecosystem envs: %s", e)
        return {"status": "error", "message": str(e)}


def _sanitize_git_output(text: str, token: str = None) -> str:
    """Remove credentials from git output before logging."""
    sanitized = text or ""
    if token:
        sanitized = sanitized.replace(token, "***")
    sanitized = sanitized.replace("https://x-access-token:***@", "https://")
    return sanitized[:1200]


def _clone_repo(repo_full: str, target_dir: str, token: str = None) -> bool:
    """Clone a repository into a target directory using Git."""
    try:
        # Construct clone URL with token if provided
        clone_url = f"https://github.com/{repo_full}.git"
        if token:
            clone_url = f"https://x-access-token:{token}@github.com/{repo_full}.git"
        
        # Run git clone --depth 1 for speed
        result = subprocess.run(
            ["git", "clone", "--depth", "1", clone_url, target_dir],
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0:
            return True

        logger.error("Git clone failed for %s: %s", repo_full, _sanitize_git_output(result.stderr, token))
        return False
    except Exception as e:
        logger.error("Error cloning %s: %s", repo_full, _sanitize_git_output(str(e), token))
        return False
