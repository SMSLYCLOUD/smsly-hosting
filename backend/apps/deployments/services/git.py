"""Git module."""
import os
import shutil
import git
import logging
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)


class GitManager:
    """
    Manages Git operations: Cloning and Checkout.
    """

    @staticmethod
    def clone_repo(repo_url: str, branch: str = 'main',
                   destination: str = '/tmp/builds', token: str = None) -> str:
        """
        Clones a repository to a destination.
        Returns the path to the cloned directory.
        """

        def _sanitize_url(url: str) -> str:
            try:
                parsed = urlparse(url)
                if not parsed.scheme or not parsed.netloc:
                    return url
                host = parsed.hostname or ""
                if parsed.port:
                    host = f"{host}:{parsed.port}"
                return urlunparse(parsed._replace(netloc=host))
            except Exception:
                return url

        def _is_github_https(url: str) -> bool:
            try:
                parsed = urlparse(url)
                return (
                    parsed.scheme in ("http", "https")
                    and (parsed.hostname or "").lower().endswith("github.com")
                )
            except Exception:
                return False

        # Prepare Auth URL if token provided (do not log this URL)
        authed_url = repo_url
        if token:
            parsed = urlparse(repo_url)
            if parsed.scheme in ("http", "https") and parsed.netloc:
                # GitHub expects the token as the "password" part of basic auth.
                # The username can be any non-empty string; `x-access-token` is the common convention.
                user = "x-access-token" if _is_github_https(repo_url) else "oauth2"
                authed_url = parsed._replace(netloc=f"{user}:{token}@{parsed.netloc}").geturl()

        # Clean destination
        repo_dir = os.path.join(
            destination, repo_url.split('/')[-1].replace('.git', ''))
        if os.path.exists(repo_dir):
            shutil.rmtree(repo_dir)

        os.makedirs(repo_dir, exist_ok=True)

        logger.info("Cloning %s (branch: %s) to %s", _sanitize_url(repo_url), branch, repo_dir)

        try:
            git.Repo.clone_from(authed_url, repo_dir, branch=branch, depth=1)
            return repo_dir
        except git.GitCommandError as e:
            msg = str(e)
            if token:
                msg = msg.replace(token, "<redacted>")
            logger.error("Git Clone Failed: %s", msg)
            raise RuntimeError(f"Failed to clone repository: {msg}") from e
