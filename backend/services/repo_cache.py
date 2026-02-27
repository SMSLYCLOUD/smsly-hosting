"""
Git repository cache for faster repeat deployments.

Strategy:
  - First deploy: full `git clone --bare` into cache dir
  - Subsequent deploys: `git fetch` (seconds vs minutes)
  - LRU eviction: repos not used in 7 days get cleaned up
  - Thread-safe: uses file locks to prevent concurrent clone/fetch races

Cache location: /opt/smsly-cache/repos/<host>/<owner>/<repo>/
"""
import os
import time
import shutil
import subprocess
import logging
import hashlib
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from filelock import FileLock

logger = logging.getLogger(__name__)

CACHE_DIR = os.environ.get('REPO_CACHE_DIR', '/opt/smsly-cache/repos')
CACHE_MAX_AGE_DAYS = int(os.environ.get('REPO_CACHE_MAX_AGE_DAYS', '7'))


def _is_github_https(repo_url: str) -> bool:
    """Return True for HTTPS GitHub URLs."""
    try:
        parsed = urlparse(repo_url)
    except Exception:
        return False
    return parsed.scheme in ("http", "https") and (parsed.hostname or "").lower() == "github.com"


def _build_remote_url(repo_url: str, token: str | None) -> str:
    """
    Build a remote URL without embedding secrets.

    For GitHub HTTPS + token, keep a username-only URL so git can use askpass
    for the password/token prompt.
    """
    if not token or not _is_github_https(repo_url):
        return repo_url

    parsed = urlparse(repo_url)
    host = parsed.hostname or "github.com"
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=f"x-access-token@{host}"))


def _git_env(cache: Path, token: str | None) -> tuple[dict, Path | None]:
    """Build git env vars and optional askpass helper path."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if not token:
        return env, None

    askpass_path = cache / f".askpass-{os.getpid()}-{int(time.time() * 1000)}.sh"
    askpass_path.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  *Username*) printf \"%s\" \"x-access-token\" ;;\n"
        "  *) printf \"%s\" \"$SMSLY_GIT_PASSWORD\" ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    os.chmod(askpass_path, 0o700)
    env["GIT_ASKPASS"] = str(askpass_path)
    env["SMSLY_GIT_PASSWORD"] = token
    return env, askpass_path


def _cache_path(repo_url: str) -> Path:
    """Deterministic cache path from repo URL."""
    # Normalize: strip .git suffix, lowercase
    url = repo_url.rstrip('/').lower()
    if url.endswith('.git'):
        url = url[:-4]
    # Extract host/owner/repo from URL
    # Handles: https://github.com/owner/repo, git@github.com:owner/repo
    if '://' in url:
        parts = url.split('://')[1].split('/')
    elif ':' in url:
        host_part, path_part = url.split(':', 1)
        host = host_part.split('@')[-1]
        parts = [host] + path_part.split('/')
    else:
        parts = [hashlib.sha256(url.encode()).hexdigest()[:12]]

    return Path(CACHE_DIR) / '/'.join(parts[-3:]) if len(parts) >= 3 else Path(CACHE_DIR) / '/'.join(parts)


def get_or_clone(repo_url: str, branch: str = 'main', token: str = None) -> str:
    """
    Get cached repo or clone fresh. Returns path to worktree checkout.

    Args:
        repo_url: Git repository URL
        branch: Branch to check out
        token: Optional GitHub token for private repos

    Returns:
        Absolute path to a directory with the checked-out code
    """
    cache = _cache_path(repo_url)
    bare_dir = cache / 'bare.git'
    lock_file = cache / '.lock'

    cache.mkdir(parents=True, exist_ok=True)

    remote_url = _build_remote_url(repo_url, token)

    with FileLock(str(lock_file), timeout=300):
        if (bare_dir / 'HEAD').exists():
            # Cached — just fetch
            logger.info(f"Cache HIT: fetching {repo_url}")
            env, askpass_path = _git_env(cache, token)
            try:
                subprocess.run(
                    ['git', 'remote', 'set-url', 'origin', remote_url],
                    cwd=str(bare_dir),
                    check=True,
                    capture_output=True,
                    timeout=60,
                    env=env,
                )
                subprocess.run(
                    ['git', 'fetch', '--all', '--prune'],
                    cwd=str(bare_dir),
                    check=True,
                    capture_output=True,
                    timeout=120,
                    env=env,
                )
            finally:
                if askpass_path:
                    askpass_path.unlink(missing_ok=True)
        else:
            # Cache MISS — full bare clone
            logger.info(f"Cache MISS: cloning {repo_url}")
            env, askpass_path = _git_env(cache, token)
            try:
                subprocess.run(
                    ['git', 'clone', '--bare', remote_url, str(bare_dir)],
                    check=True,
                    capture_output=True,
                    timeout=300,
                    env=env,
                )
            finally:
                if askpass_path:
                    askpass_path.unlink(missing_ok=True)

    # Touch last_used timestamp for LRU
    (cache / '.last_used').write_text(str(time.time()))

    # Create a fresh worktree checkout for this build
    worktree_dir = cache / f'worktree-{branch}-{int(time.time())}'
    if worktree_dir.exists():
        shutil.rmtree(str(worktree_dir))

    subprocess.run(
        ['git', 'clone', '--local', '--branch', branch,
         '--single-branch', '--depth', '1',
         str(bare_dir), str(worktree_dir)],
        check=True,
        capture_output=True,
        timeout=60,
    )

    return str(worktree_dir)


def cleanup_old_caches():
    """Remove repos not used in CACHE_MAX_AGE_DAYS days."""
    if not os.path.exists(CACHE_DIR):
        return

    cutoff = time.time() - (CACHE_MAX_AGE_DAYS * 86400)
    cleaned = 0

    for root, dirs, files in os.walk(CACHE_DIR, topdown=False):
        last_used_file = os.path.join(root, '.last_used')
        if os.path.exists(last_used_file):
            try:
                ts = float(open(last_used_file).read().strip())
                if ts < cutoff:
                    shutil.rmtree(root)
                    cleaned += 1
                    logger.info(f"Evicted cache: {root}")
            except (ValueError, OSError):
                pass

    logger.info(f"Cache cleanup complete: evicted {cleaned} repos")


def cleanup_worktrees(repo_url: str, keep_latest: int = 2):
    """Clean up old worktree checkouts, keeping the N most recent."""
    cache = _cache_path(repo_url)
    if not cache.exists():
        return
    worktrees = sorted(
        [d for d in cache.iterdir() if d.name.startswith('worktree-')],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    for old in worktrees[keep_latest:]:
        shutil.rmtree(str(old), ignore_errors=True)
