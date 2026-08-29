import base64
import logging
import subprocess
import time

import requests

logger = logging.getLogger(__name__)

_GITHUB_API_BASE = "https://api.github.com"
_RATE_LIMIT_WARN_THRESHOLD = 100  # Warn below this many remaining calls


def _check_github_rate_limit(headers: dict) -> tuple[int, int]:
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


def _github_headers(token: str) -> dict:
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }


def fetch_all_repos(token: str) -> list[dict]:
    headers = _github_headers(token)
    remaining, limit = _check_github_rate_limit(headers)
    if remaining < 10:
        logger.error(
            "SEC-ZT-009: GitHub API rate-limit exhausted (%d/%d). "
            "Cannot scan repos.", remaining, limit,
        )
        return []

    repos: list[dict] = []
    page = 1
    while True:
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
            params={  # type: ignore[arg-type]
                "per_page": 100, "page": page, "sort": "updated"
            },
            timeout=15,
        )

        # If /user/repos fails with 403, the token is likely a GitHub App
        # installation token — fall back to /installation/repositories
        if resp.status_code == 403 and "rate limit" not in resp.text.lower():
            logger.info("GitHub App token detected (403 on /user/repos), "
                        "falling back to /installation/repositories")
            return _fetch_installation_repos(token)

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


def _fetch_installation_repos(token: str) -> list[dict]:
    """Fetch repos via GitHub App installation token (fallback for /user/repos)."""
    headers = _github_headers(token)
    repos: list[dict] = []
    page = 1
    while True:
        resp = requests.get(
            f"{_GITHUB_API_BASE}/installation/repositories",
            headers=headers,
            params={"per_page": 100, "page": page},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.error("Failed to fetch installation repos: %s %s",
                         resp.status_code, resp.text[:200])
            break
        data = resp.json()
        batch = data.get("repositories", [])
        if not batch:
            break
        repos.extend(batch)
        page += 1
        if len(batch) < 100:
            break
    return repos


def fetch_repo_tree(token: str, full_name: str, branch: str = "main") -> list[str]:
    headers = _github_headers(token)
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


def fetch_file_content(token: str, full_name: str, path: str) -> str | None:
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
    data = resp.json()
    if data.get("encoding") == "base64":
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    return data.get("content", "")


def _sanitize_git_output(text: str, token: str | None = None) -> str:
    sanitized = text or ""
    if token:
        sanitized = sanitized.replace(token, "***")
    sanitized = sanitized.replace("https://x-access-token:***@", "https://")
    return sanitized[:1200]


def _clone_repo(repo_full: str, target_dir: str, token: str | None = None) -> bool:
    try:
        provider = "github.com"
        if repo_full.startswith(("github.com/", "gitlab.com/", "bitbucket.org/")):
            provider, repo_full = repo_full.split("/", 1)

        clone_url = f"https://{provider}/{repo_full}.git"

        if token and provider == "github.com":
            clone_url = f"https://x-access-token:{token}@{provider}/{repo_full}.git"

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
