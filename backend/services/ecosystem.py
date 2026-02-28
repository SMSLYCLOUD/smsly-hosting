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

    # Primary stack is the first detected, but expose all languages
    stack = languages[0] if languages else "unknown"

    # ── Env Var Detection ──
    env_vars = _detect_env_vars(files, stack, port, clone_dir)

    return {
        "stack": stack,
        "languages": languages,
        "port": port,
        "build": build,
        "addons": list(addons),
        "env_vars": env_vars,
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

ECOSYSTEM_PROMPT = """You are an expert DevOps architect. Analyze these GitHub repositories and create a zero-config deployment plan.

For each repo, determine:
1. The tech stack (django, nextjs, node, rust, go, etc.)
2. The port it listens on
3. Build strategy (dockerfile or nixpacks)
4. Required addons (POSTGRES, REDIS, MONGODB, etc.)
5. Essential environment variables it needs
6. Which OTHER repos it depends on (e.g., a frontend that needs a backend API URL)

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
      "deploy_order": 1
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
            _apply_plan_repo_defaults(plan["services"], repos_data)
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
            "branch": str(rd.get("default_branch") or "main"),
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

    # 4. Enrich with metadata
    plan["total_repos_scanned"] = len(all_repos)
    plan["deployable_repos"] = len(repos_data)

    return plan
