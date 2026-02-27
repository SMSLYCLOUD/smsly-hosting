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


def _safe_stderr(exc: subprocess.CalledProcessError) -> str:
    """Extract stderr for diagnostics without raising."""
    return ((exc.stderr or '') or (exc.stdout or '')).strip()


def _is_auth_error(stderr: str) -> bool:
    text = (stderr or '').lower()
    return any(
        marker in text for marker in (
            "authentication failed",
            "invalid username or token",
            "could not read username",
            "repository not found",
            "permission denied",
            "http basic: access denied",
            "fatal: could not read from remote repository",
        )
    )


def _bare_clone(bare_dir: Path, remote_url: str, env: dict):
    """Clone bare repo into cache dir."""
    subprocess.run(
        ['git', 'clone', '--bare', remote_url, str(bare_dir)],
        check=True,
        capture_output=True,
        timeout=300,
        env=env,
    )


def _fetch_bare(bare_dir: Path, remote_url: str, env: dict):
    """Update bare repo from origin."""
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
                try:
                    _fetch_bare(bare_dir, remote_url, env)
                except subprocess.CalledProcessError as fetch_error:
                    stderr = _safe_stderr(fetch_error)
                    logger.warning(
                        "Cache fetch failed for %s (rebuilding cache): %s",
                        repo_url,
                        stderr[:500],
                    )
                    shutil.rmtree(bare_dir, ignore_errors=True)
                    try:
                        _bare_clone(bare_dir, remote_url, env)
                    except subprocess.CalledProcessError as clone_error:
                        clone_stderr = _safe_stderr(clone_error)
                        if token and _is_auth_error(clone_stderr):
                            fallback_env, fallback_askpass = _git_env(cache, None)
                            try:
                                _bare_clone(bare_dir, repo_url, fallback_env)
                                logger.info("Anonymous cache clone fallback succeeded for %s", repo_url)
                            except subprocess.CalledProcessError as fallback_error:
                                fallback_stderr = _safe_stderr(fallback_error)
                                raise RuntimeError(
                                    f"git cache rebuild failed: {clone_stderr or stderr}; fallback failed: {fallback_stderr}"
                                ) from fallback_error
                            finally:
                                if fallback_askpass:
                                    fallback_askpass.unlink(missing_ok=True)
                        else:
                            raise RuntimeError(
                                f"git cache rebuild failed: {clone_stderr or stderr}"
                            ) from clone_error
            finally:
                if askpass_path:
                    askpass_path.unlink(missing_ok=True)
        else:
            # Cache MISS — full bare clone
            logger.info(f"Cache MISS: cloning {repo_url}")
            env, askpass_path = _git_env(cache, token)
            try:
                try:
                    _bare_clone(bare_dir, remote_url, env)
                except subprocess.CalledProcessError as clone_error:
                    stderr = _safe_stderr(clone_error)
                    if token and _is_auth_error(stderr):
                        fallback_env, fallback_askpass = _git_env(cache, None)
                        try:
                            _bare_clone(bare_dir, repo_url, fallback_env)
                            logger.info("Anonymous cache clone fallback succeeded for %s", repo_url)
                        except subprocess.CalledProcessError as fallback_error:
                            fallback_stderr = _safe_stderr(fallback_error)
                            raise RuntimeError(
                                f"git cache clone failed: {stderr}; fallback failed: {fallback_stderr}"
                            ) from fallback_error
                        finally:
                            if fallback_askpass:
                                fallback_askpass.unlink(missing_ok=True)
                    else:
                        raise RuntimeError(f"git cache clone failed: {stderr}") from clone_error
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
