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
import tempfile
import subprocess
import shutil

from apps.intelligence.providers import ask_with_fallback
from apps.intelligence.services.env_intelligence import EnvironmentIntelligenceService

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
        "api_calls": list(set(api_calls))[:20],  # Dedupe + cap
        "frameworks": list(set(frameworks)),
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
    'JULES_API_KEY':       {'hint': 'From your Jules console/provider', 'is_secret': True, 'required': True, 'user_required': True},
    'STRIPE_SECRET_KEY':   {'hint': 'sk_live_... from Stripe', 'is_secret': True, 'required': True, 'user_required': True},
    'STRIPE_PUBLISHABLE_KEY': {'hint': 'pk_live_... from Stripe', 'required': True, 'user_required': True},
    'NEXT_PUBLIC_API_URL': {'hint': 'https://api.example.com', 'required': False},
    'DEBUG':               {'hint': 'False for production',   'default': 'False', 'required': False},
    'FLASK_ENV':           {'hint': 'production',             'default': 'production', 'required': False},
    'RAILS_ENV':           {'hint': 'production',             'default': 'production', 'required': False},
    'RUST_LOG':            {'hint': 'info, debug, or warn',   'default': 'info',  'required': False},
    'PORT':                {'hint': 'Listening port',         'required': False},
    'ALLOWED_HOSTS':       {'hint': 'Comma-separated or *',  'default': '*',     'required': False},
    'AI_PROVIDER':         {'hint': 'openai | grok | gemini | claude | jules | auto', 'required': True, 'user_required': True},
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
    
    Return ONLY valid JSON matching this structure:
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


def analyze_ecosystem(repos_data: List[dict], github_token: str = None) -> dict:
    """
    Use AI Senate to analyze all repos together in a temporary workspace.
    Clones repos, scans for cross-repo dependencies, and produces a plan.
    """
    import json
    import re as _re

    # 1. Create a temporary workspace for the analysis
    with tempfile.TemporaryDirectory(prefix="smsly-ecosystem-") as workspace_dir:
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
                rd['configs_summary'] = {k: v[:500] for k, v in scan.get('configs', {}).items()}
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
            current_vars = set(rd.get('env_vars_context', {}).keys())
            for other_rd in repos_data:
                if other_rd['repo'] == rd['repo']: continue
                other_vars = set(other_rd.get('env_vars_context', {}).keys())
                common = current_vars.intersection(other_vars)
                if common:
                    cross_links.append(f"SHARED STATE: {rd['repo']} and {other_rd['repo']} share env keys: {list(common)}")

            # Grep for other repo names in this repo's configs/env (Service Discovery)
            for other in repo_names:
                if other == rd.get('repo_name_short'): continue
                for path, content in rd.get('configs_summary', {}).items():
                    if other in content.lower():
                        cross_links.append(f"DEPENDENCY HINT: {rd['repo']} mentions {other} in {path} (Potential URL target)")

        brief_header = "ECOSYSTEM DISCOVERY HINTS:\n" + "\n".join(set(cross_links)) if cross_links else ""
        full_prompt = f"### ECOSYSTEM ARCHITECTURAL BRIEF\n{brief_header}\n\n"
        full_prompt += "### REPOSITORY DETAILS\n" + "\n".join(repo_summaries)

        # 6. Call AI Senate
        response_text, provider = ask_with_fallback(full_prompt, system_prompt=ECOSYSTEM_PROMPT)

    # 7. Parse and structure the plan (Workspace is now deleted)
    try:
        json_match = _re.search(r'\{.*\}', response_text, _re.DOTALL)
        if not json_match:
            raise ValueError("No JSON found in Senate response")
        
        plan = json.loads(json_match.group(0))
        if isinstance(plan, dict) and isinstance(plan.get("services"), list):
            # 5. Apply the Senate's environment resolutions
            for svc in plan["services"]:
                if isinstance(svc, dict):
                    svc["env_vars"] = _env_plan_map(svc.get("env_vars", {}))
            
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


_SMSLY_CORE_ALIASES = {
    "smsly-core",
    "smsly-platform-api",
    "platform-api",
    "smsly-core-api",
}


def _normalize_token(value: Any) -> str:
    """Normalize names/repo refs for fuzzy matching."""
    token = str(value or "").strip().lower()
    token = token.replace("_", "-").replace(" ", "-")
    return token.strip("-")


def _repo_short_name(service: dict) -> str:
    """Extract short repo name from owner/repo ref."""
    repo_ref = str(service.get("repo") or "").strip().lower()
    if not repo_ref:
        return ""
    return repo_ref.split("/")[-1]


def _is_smsly_service(service: dict) -> bool:
    """Return True when service looks like part of the SMSLY family."""
    repo_name = _repo_short_name(service)
    svc_name = _normalize_token(service.get("name"))
    return "smsly" in repo_name or "smsly" in svc_name


def _is_smsly_core_service(service: dict) -> bool:
    """Return True when service is the ecosystem core/platform API."""
    repo_name = _normalize_token(_repo_short_name(service))
    svc_name = _normalize_token(service.get("name"))
    candidates = {repo_name, svc_name}
    if any(item in _SMSLY_CORE_ALIASES for item in candidates):
        return True
    return any(
        item.startswith("smsly") and ("core" in item or "platform-api" in item)
        for item in candidates
        if item
    )


def _coerce_depends_on(raw_depends: Any) -> List[str]:
    """Normalize depends_on payload to a flat list."""
    if isinstance(raw_depends, list):
        return [str(item).strip() for item in raw_depends if str(item).strip()]
    if isinstance(raw_depends, str):
        text = raw_depends.strip()
        if not text:
            return []
        if "," in text:
            return [token.strip() for token in text.split(",") if token.strip()]
        return [text]
    return []


def _safe_order(value: Any, default: int = 99) -> int:
    """Best-effort int parser for deploy_order values."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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
            addon_type = str(addon.get("type") or "").strip().upper()
            if not addon_type:
                continue
            addon_map.setdefault(addon_type, set())
            for svc_name in addon.get("shared_by", []) or []:
                svc_text = str(svc_name or "").strip()
                if svc_text:
                    addon_map[addon_type].add(svc_text)

    for service in services:
        if not isinstance(service, dict) or service.get("skip"):
            continue
        service_name = str(service.get("name") or _repo_short_name(service)).strip()
        if not service_name:
            continue
        for addon in service.get("addons", []) or []:
            addon_type = str(addon or "").strip().upper()
            if not addon_type:
                continue
            addon_map.setdefault(addon_type, set()).add(service_name)

    return [
        {"type": addon_type, "shared_by": sorted(shared_by)}
        for addon_type, shared_by in sorted(addon_map.items())
    ]


def _apply_generic_ecosystem_intelligence(services: List[dict]):
    """
    Elite Level 5: Zero-Hardcoding Service Discovery.
    Analyzes the 'functional intent' of each service to build a generic mesh.
    """
    deployable = [s for s in services if isinstance(s, dict) and not s.get("skip")]
    if not deployable:
        return

    # 1. Functional Mapping
    apis = [s for s in deployable if str(s.get("stack")).lower() in ["django", "fastapi", "express", "go", "rust", "backend"]]
    frontends = [s for s in deployable if str(s.get("stack")).lower() in ["nextjs", "nuxt", "react", "vue", "frontend"]]
    auth_providers = [s for s in deployable if any(k in s.get("name", "").lower() for k in ["auth", "identity", "keycloak", "login"])]

    # 2. Dynamic Cross-Linking
    for svc in deployable:
        env_map = svc.get("env_vars", {})
    
        # Link Frontends to APIs
        if svc in frontends and apis:
            target_api = apis[0].get("name")
            if target_api:
                for key in list(env_map.keys()):
                    if any(k in key.upper() for k in ["API_URL", "BACKEND_URL", "SERVER_URL"]):
                        env_map[key] = f"{{{{SERVICE:{target_api}}}}}"

        # Link everything to Auth Provider if detected
        if auth_providers and svc not in auth_providers:
            auth_name = auth_providers[0].get("name")
            for key in list(env_map.keys()):
                if any(k in key.upper() for k in ["AUTH_URL", "IDENTITY_URL", "OIDC_URL", "JWT_ISSUER"]):
                    env_map[key] = f"{{{{SERVICE:{auth_name}}}}}"

        # 3. Global Secret Synchronization
        for key in list(env_map.keys()):
            if any(k in key.upper() for k in ["JWT_SECRET", "ENCRYPTION_KEY", "APP_SECRET", "GATEWAY_SECRET"]):
                # Assign a shared secret placeholder so they all get the same value across the cluster
                env_map[key] = f"{{{{SHARED_SECRET:{key.lower()}}}}}"

        # 4. Standard Database Injection
        if any(k in str(svc.get("addons", [])).upper() for k in ["POSTGRES", "DATABASE"]):
            env_map.setdefault("DATABASE_URL", "{{POSTGRES_URL}}")
        if "REDIS" in str(svc.get("addons", [])).upper():
            env_map.setdefault("REDIS_URL", "{{REDIS_URL}}")

        svc["env_vars"] = env_map

    # 5. Dependency Depth Sorting
    # Infrastructure/Auth (10) > APIs (20) > Workers (30) > Frontends (40)
    for svc in deployable:
        order = 50
        if svc in auth_providers: order = 10
        elif svc in apis: order = 20
        elif svc in frontends: order = 40
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
        deployable,
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

    _apply_smsly_core_intelligence(sorted_services)
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

def scan_and_analyze(token: str) -> dict:
    """
    Full pipeline: fetch all repos → analyze each → AI ecosystem plan.

    Returns the deploy plan dict ready for the frontend.
    """
    logger.info("Starting ecosystem scan...")

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

    # 3. AI ecosystem analysis
    plan = analyze_ecosystem(repos_data)
    plan["total_repos_scanned"] = len(all_repos)
    plan["deployable_repos"] = len(repos_data)

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
        else:
            logger.error(f"Git clone failed for {repo_full}: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Error cloning {repo_full}: {e}")
        return False
