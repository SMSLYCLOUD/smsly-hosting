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
from filelock import FileLock

logger = logging.getLogger(__name__)

CACHE_DIR = os.environ.get('REPO_CACHE_DIR', '/opt/smsly-cache/repos')
CACHE_MAX_AGE_DAYS = int(os.environ.get('REPO_CACHE_MAX_AGE_DAYS', '7'))


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

    # Inject token into URL for private repos
    auth_url = repo_url
    if token and '://' in repo_url:
        auth_url = repo_url.replace('https://', f'https://x-access-token:{token}@')

    with FileLock(str(lock_file), timeout=300):
        if (bare_dir / 'HEAD').exists():
            # Cached — just fetch
            logger.info(f"Cache HIT: fetching {repo_url}")
            subprocess.run(
                ['git', 'fetch', '--all', '--prune'],
                cwd=str(bare_dir),
                check=True,
                capture_output=True,
                timeout=120,
                env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'},
            )
        else:
            # Cache MISS — full bare clone
            logger.info(f"Cache MISS: cloning {repo_url}")
            subprocess.run(
                ['git', 'clone', '--bare', auth_url, str(bare_dir)],
                check=True,
                capture_output=True,
                timeout=300,
                env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'},
            )

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
