"""Git module."""
import contextlib
import logging
import os
import re
import shutil
import stat
import uuid
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)


def _get_git():
    """Import git lazily while preserving module-level patchability."""
    mod = globals().get("git")
    if mod is None:
        import git as _git
        globals()["git"] = _git
        return _git
    return mod


def __getattr__(name: str):
    if name == "git":
        return _get_git()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _sanitize_repo_symlinks(root_dir: str) -> None:
    """Remove any symlink in *root_dir* whose resolved target escapes *root_dir*."""
    root_real = os.path.realpath(root_dir)
    for dirpath, dirnames, filenames in os.walk(root_real, followlinks=False):
        for name in list(dirnames) + list(filenames):
            full_path = os.path.join(dirpath, name)
            if not os.path.islink(full_path):
                continue
            target_real = os.path.realpath(full_path)
            if target_real != root_real and not target_real.startswith(root_real + os.sep):
                logger.warning(
                    "Removing escaping symlink from cloned repo: %s -> %s",
                    full_path,
                    target_real,
                )
                os.unlink(full_path)
                if name in dirnames:
                    dirnames.remove(name)


class GitManager:
    """
    Manages Git operations: Cloning and Checkout.
    """

    @staticmethod
    def clone_repo(repo_url: str, branch: str = 'main',
                   destination: str = '/tmp/builds', token: str | None = None, commit_hash: str | None = None) -> str:
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
                    and (parsed.hostname or "").lower() in ("github.com", "www.github.com")
                )
            except Exception:
                return False

        # Reject flag injection, local paths, or ext/fd transport helpers
        repo_url_str = (repo_url or '').strip()
        if (
            not repo_url_str
            or repo_url_str.startswith(('-', '/', '\\', '.'))
            or repo_url_str.lower().startswith(('ext::', 'fd::', 'file://'))
        ):
            raise ValueError("Invalid or unsafe git repository URL.")
        if not branch or branch.startswith('-') or any(c in branch for c in ('\x00', '\r', '\n')):
            raise ValueError("Invalid git branch name.")
        if commit_hash and (commit_hash.startswith('-') or any(c in commit_hash for c in ('\x00', '\r', '\n'))):
            raise ValueError("Invalid git commit hash.")

        # Clean destination
        clean_url = repo_url_str.rstrip('/')
        from apps.core.validators import validate_ssrf
        parsed_ssrf = urlparse(clean_url)
        scp_match = re.match(r'^([a-zA-Z0-9._-]+)@([a-zA-Z0-9.-]+):([^\s]+)$', clean_url)
        if parsed_ssrf.scheme.lower() in ('http', 'https'):
            validate_ssrf(clean_url, fail_closed_dns=True)
        elif parsed_ssrf.scheme.lower() == 'ssh' and parsed_ssrf.hostname:
            validate_ssrf(f"https://{parsed_ssrf.hostname}", fail_closed_dns=True)
        elif scp_match:
            validate_ssrf(f"https://{scp_match.group(2)}", fail_closed_dns=True)
        else:
            validate_ssrf(clean_url, fail_closed_dns=True)

        repo_name = clean_url.split('/')[-1].replace('.git', '')
        # Prevent path traversal: reject names containing .. or /
        if not repo_name or '..' in repo_name or '/' in repo_name or '\\' in repo_name:
            repo_name = f"repo-{uuid.uuid4().hex[:8]}"

        repo_dir = os.path.join(destination, repo_name)
        if os.path.exists(repo_dir):
            shutil.rmtree(repo_dir)

        os.makedirs(repo_dir, exist_ok=True)

        logger.info("Cloning %s (branch: %s) to %s", _sanitize_url(repo_url), branch, repo_dir)

        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ALLOW_PROTOCOL"] = "https:ssh"
        askpass_path = None
        clone_url = repo_url
        _git = _get_git()

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

            clone_opts = [
                "-c", "http.followRedirects=false",
                "-c", "protocol.file.allow=never",
                "-c", "protocol.allow=never",
                "-c", "protocol.https.allow=always",
                "-c", "protocol.ssh.allow=always",
            ]
            if commit_hash:
                _git.Repo.clone_from(clone_url, repo_dir, branch=branch, env=env, multi_options=clone_opts)
                repo = _git.Repo(repo_dir)
                repo.git.checkout(commit_hash)
            else:
                _git.Repo.clone_from(clone_url, repo_dir, branch=branch, depth=1, env=env, multi_options=clone_opts)
            _sanitize_repo_symlinks(repo_dir)
            return repo_dir

        except _git.GitCommandError as e:
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
                    fallback_env["GIT_ALLOW_PROTOCOL"] = "https:ssh"
                    fallback_env.pop("GIT_ASKPASS", None)
                    fallback_env.pop("SMSLY_GIT_PASSWORD", None)
                    clone_opts = [
                        "-c", "http.followRedirects=false",
                        "-c", "protocol.file.allow=never",
                        "-c", "protocol.allow=never",
                        "-c", "protocol.https.allow=always",
                        "-c", "protocol.ssh.allow=always",
                    ]
                    if commit_hash:
                        _git.Repo.clone_from(
                            repo_url,
                            repo_dir,
                            branch=branch,
                            env=fallback_env,
                            multi_options=clone_opts,
                        )
                        repo = _git.Repo(repo_dir)
                        repo.git.checkout(commit_hash)
                    else:
                        _git.Repo.clone_from(
                            repo_url,
                            repo_dir,
                            branch=branch,
                            depth=1,
                            env=fallback_env,
                            multi_options=clone_opts,
                        )
                    _sanitize_repo_symlinks(repo_dir)
                    logger.info(
                        "Anonymous clone fallback succeeded for %s",
                        _sanitize_url(repo_url),
                    )
                    return repo_dir
                except _git.GitCommandError as fallback_error:
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
                with contextlib.suppress(Exception):
                    os.remove(askpass_path)
