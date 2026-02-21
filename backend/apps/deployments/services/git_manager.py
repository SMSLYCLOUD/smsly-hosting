"""Git module."""
import os
import shutil
import logging
import uuid
import stat
import git
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

        # Clean destination
        cleaned_url = repo_url.rstrip('/')
        repo_name = cleaned_url.split('/')[-1].replace('.git', '')
        if not repo_name:
            # Fallback for root domains or empty names
            repo_name = f"repo-{uuid.uuid4().hex[:8]}"

        repo_dir = os.path.join(destination, repo_name)
        if os.path.exists(repo_dir):
            shutil.rmtree(repo_dir)

        os.makedirs(repo_dir, exist_ok=True)

        logger.info("Cloning %s (branch: %s) to %s", _sanitize_url(repo_url), branch, repo_dir)

        env = os.environ.copy()
        askpass_path = None
        clone_url = repo_url

        try:
            if token:
                # Security: Use GIT_ASKPASS to avoid leaking token in process list
                parsed = urlparse(repo_url)
                if parsed.scheme in ("http", "https") and parsed.netloc:
                    # Inject username so git asks for password
                    user = "x-access-token" if _is_github_https(repo_url) else "oauth2"
                    # Only add username, not password
                    clone_url = urlunparse(parsed._replace(netloc=f"{user}@{parsed.hostname or parsed.netloc}"))

                askpass_filename = f"askpass-{uuid.uuid4().hex}.sh"
                askpass_path = os.path.join(os.path.dirname(destination), askpass_filename)

                # Create the askpass script
                with open(askpass_path, 'w', encoding='utf-8') as f:
                    f.write('#!/bin/sh\nprintf "%s" "$SMSLY_GIT_PASSWORD"\n')

                os.chmod(askpass_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR) # 0o700

                env["GIT_ASKPASS"] = askpass_path
                env["SMSLY_GIT_PASSWORD"] = token
                env["GIT_TERMINAL_PROMPT"] = "0"

            git.Repo.clone_from(clone_url, repo_dir, branch=branch, depth=1, env=env)
            return repo_dir

        except git.GitCommandError as e:
            msg = str(e)
            if token:
                msg = msg.replace(token, "<redacted>")
            auth_markers = (
                "Authentication failed",
                "Invalid username or token",
                "could not read Username",
                "403",
            )
            if token and any(marker in msg for marker in auth_markers):
                logger.warning(
                    "Git clone with linked token failed; retrying anonymous clone for %s",
                    _sanitize_url(repo_url),
                )
                try:
                    if os.path.exists(repo_dir):
                        shutil.rmtree(repo_dir)
                    os.makedirs(repo_dir, exist_ok=True)
                    fallback_env = os.environ.copy()
                    fallback_env["GIT_TERMINAL_PROMPT"] = "0"
                    fallback_env.pop("GIT_ASKPASS", None)
                    fallback_env.pop("SMSLY_GIT_PASSWORD", None)
                    git.Repo.clone_from(
                        repo_url,
                        repo_dir,
                        branch=branch,
                        depth=1,
                        env=fallback_env,
                    )
                    logger.info(
                        "Anonymous clone fallback succeeded for %s",
                        _sanitize_url(repo_url),
                    )
                    return repo_dir
                except git.GitCommandError as fallback_error:
                    fallback_msg = str(fallback_error)
                    logger.error("Anonymous clone fallback failed: %s", fallback_msg)
                    raise RuntimeError(
                        f"Failed to clone repository with token and anonymous fallback: {msg} | {fallback_msg}"
                    ) from fallback_error
            logger.error("Git Clone Failed: %s", msg)
            raise RuntimeError(f"Failed to clone repository: {msg}") from e

        finally:
            # Cleanup askpass script
            if askpass_path and os.path.exists(askpass_path):
                try:
                    os.remove(askpass_path)
                except Exception:
                    pass
