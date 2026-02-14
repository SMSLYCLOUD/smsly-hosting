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


def heuristic_analysis(files: List[str]) -> dict:
    """Fast local analysis without AI calls."""
    stack = "unknown"
    port = 3000
    addons = set()
    build = "nixpacks"

    # Detect stack
    for filename, (s, p) in STACK_SIGNALS.items():
        if any(f.endswith(filename) or f == filename for f in files):
            stack = s
            port = p

    # Detect build strategy
    for filename, strategy in BUILD_STRATEGY.items():
        if filename in files or any(f.endswith(filename) for f in files):
            build = strategy
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

    return {
        "stack": stack,
        "port": port,
        "build": build,
        "addons": list(addons),
        "env_vars": {},
    }


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
        plan["ai_provider"] = provider
        return plan
    except (json.JSONDecodeError, IndexError) as e:
        logger.error("Failed to parse AI ecosystem response: %s", e)
        logger.error("Raw response: %s", response_text[:1000])
        # Fall back to heuristic-only plan
        return _build_heuristic_plan(repos_data, str(e))


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
            "env_vars": h.get("env_vars", {}),
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
