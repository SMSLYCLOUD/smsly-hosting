"""Stage the media-node code repo onto a target node without leaking credentials.

The media installer (install-media-node.sh) clones a code repo at install
time. For private repos the node would need a GitHub credential — instead,
the master mints a short-lived GitHub App installation token, downloads the
repo tarball itself, and copies the files to the node over the existing SSH
channel. The node then seeds a local git repo (no remotes) and the installer
clones it via ``file://`` — no GitHub credential ever touches the node.

Flow:
    master (App token) -> GitHub tarball -> /opt/smsly-media-src (node)
    installer --repo-url=file:///opt/smsly-media-src -> /opt/smsly-media-mgmt
"""
import logging
import os
import shutil
import tarfile
import tempfile

import requests

from apps.deployments.models.servers import ManagedServer

from .logging import _append_log

logger = logging.getLogger(__name__)

DEFAULT_MEDIA_REPO_URL = "https://github.com/SMSLYCLOUD/smsly-media-mgmt"
MEDIA_STAGING_DIR = "/opt/smsly-media-src"
GITHUB_API_TIMEOUT_SECONDS = 30
TARBALL_DOWNLOAD_TIMEOUT_SECONDS = 180
SSH_COMMAND_TIMEOUT_SECONDS = 120


def _parse_repo_full_name(repo_url: str) -> str:
    """Extract ``OWNER/REPO`` from an https or scp-style git URL.

    Returns "" when the URL is not a github.com repo URL.
    """
    value = (repo_url or "").strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    lower = value.lower()
    if lower.startswith("https://github.com/"):
        path = value[len("https://github.com/"):]
    elif lower.startswith("git@github.com:"):
        path = value[len("git@github.com:"):]
    else:
        return ""
    parts = [p for p in path.split("/") if p]
    if len(parts) != 2:
        return ""
    return f"{parts[0]}/{parts[1]}"


def resolve_media_repo_url(server: ManagedServer) -> str:
    """Return the canonical media code repo URL for a server.

    Precedence: MediaNodeProfile.script_repo_url -> PlatformConfig
    media_repo_url -> built-in default.
    """
    try:
        profile = getattr(server, "media_profile", None)
        if profile and str(getattr(profile, "script_repo_url", "") or "").strip():
            return str(profile.script_repo_url).strip()
    except Exception:
        pass
    try:
        from apps.deployments.models.core import PlatformConfig
        config = PlatformConfig.load()
        if str(getattr(config, "media_repo_url", "") or "").strip():
            return str(config.media_repo_url).strip()
    except Exception:
        pass
    return DEFAULT_MEDIA_REPO_URL


def _mint_app_token(repo_full_name: str) -> str:
    try:
        from apps.deployments.services.github_app import (
            get_installation_token_for_repo,
        )
        return str(get_installation_token_for_repo(repo_full_name) or "").strip()
    except Exception as exc:
        logger.warning("GitHub App token mint failed for %s: %s", repo_full_name, exc)
        return ""


def _download_repo_tarball(repo_full_name: str, token: str, dest_dir: str) -> str:
    """Download + extract the repo tarball, return the extracted root dir."""
    api_base = f"https://api.github.com/repos/{repo_full_name}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    repo_resp = requests.get(api_base, headers=headers, timeout=GITHUB_API_TIMEOUT_SECONDS)
    if not repo_resp.ok:
        raise RuntimeError(f"GitHub repo lookup failed: HTTP {repo_resp.status_code}")
    default_branch = (repo_resp.json().get("default_branch") or "main").strip() or "main"

    tarball_url = f"{api_base}/tarball/{default_branch}"
    with requests.get(
        tarball_url,
        headers={**headers, "Accept": "application/octet-stream"},
        timeout=TARBALL_DOWNLOAD_TIMEOUT_SECONDS,
        stream=True,
    ) as resp:
        if not resp.ok:
            raise RuntimeError(f"GitHub tarball download failed: HTTP {resp.status_code}")
        archive_path = os.path.join(dest_dir, "repo.tar.gz")
        with open(archive_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if chunk:
                    fh.write(chunk)

    with tarfile.open(archive_path, "r:gz") as tar:
        # Guard against path traversal in archive members.
        for member in tar.getmembers():
            member_path = os.path.normpath(os.path.join(dest_dir, member.name))
            if not member_path.startswith(os.path.normpath(dest_dir) + os.sep):
                raise RuntimeError("Refusing to extract tarball with absolute/parent paths")
        tar.extractall(dest_dir)
    os.remove(archive_path)

    top_dirs = [
        entry for entry in os.listdir(dest_dir)
        if os.path.isdir(os.path.join(dest_dir, entry))
    ]
    if len(top_dirs) != 1:
        raise RuntimeError(f"Unexpected tarball layout: {top_dirs!r}")
    return os.path.join(dest_dir, top_dirs[0])


def _sftp_upload_tree(ssh, local_root: str, remote_root: str) -> int:
    """Copy a local tree to the node over SFTP. Returns file count."""
    stdin, stdout, stderr = ssh.exec_command(
        f"rm -rf {remote_root} && mkdir -p {remote_root}",
        timeout=SSH_COMMAND_TIMEOUT_SECONDS,
    )
    if stdout.channel.recv_exit_status() != 0:
        err = stderr.read().decode("utf-8", errors="replace").strip()[:300]
        raise RuntimeError(f"Could not prepare remote staging dir: {err}")

    sftp = ssh.open_sftp()
    try:
        count = 0
        for dirpath, dirnames, filenames in os.walk(local_root):
            rel = os.path.relpath(dirpath, local_root)
            remote_dir = remote_root if rel == "." else f"{remote_root}/{rel.replace(os.sep, '/')}"
            if rel != ".":
                try:
                    sftp.mkdir(remote_dir)
                except OSError:
                    pass
            for name in filenames:
                sftp.put(
                    os.path.join(dirpath, name),
                    f"{remote_dir}/{name}",
                )
                count += 1
        return count
    finally:
        sftp.close()


def _git_seed_on_node(ssh, remote_dir: str) -> None:
    """Turn the staged tree into a local git repo with NO remotes.

    The installer clones it via file:// — git history works, `git pull`
    degrades gracefully, and no credential is stored anywhere on the node.
    """
    cmd = (
        f"cd {remote_dir} && rm -rf .git && "
        "git init -q && git add -A && "
        "git -c user.name=smsly -c user.email=ops@smsly.cloud "
        "commit -qm seed && git remote -v"
    )
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=SSH_COMMAND_TIMEOUT_SECONDS)
    if stdout.channel.recv_exit_status() != 0:
        err = stderr.read().decode("utf-8", errors="replace").strip()[:300]
        raise RuntimeError(f"Could not seed node-local git repo: {err}")


def stage_media_repo_for_node(ssh, server: ManagedServer) -> tuple[str, str]:
    """Fetch the media code repo on the master, copy it to the node.

    Returns ``(repo_url, repo_token)`` for the installer args. On the
    GitHub App path the URL is ``file:///opt/smsly-media-src`` and the
    token is "" — nothing secret reaches the node. If App staging fails
    but a profile token exists, falls back to the legacy direct-https
    clone so provisioning can still proceed.
    """
    repo_url = resolve_media_repo_url(server)
    repo_full_name = _parse_repo_full_name(repo_url)

    if repo_full_name:
        token = _mint_app_token(repo_full_name)
        if token:
            tmp_dir = tempfile.mkdtemp(prefix="smsly-media-")
            try:
                _append_log(server, "Fetching media code with GitHub App token (master-side)...")
                extracted = _download_repo_tarball(repo_full_name, token, tmp_dir)
                count = _sftp_upload_tree(ssh, extracted, MEDIA_STAGING_DIR)
                _git_seed_on_node(ssh, MEDIA_STAGING_DIR)
                _append_log(
                    server,
                    f"Media code staged on node ({count} files, no credentials placed on node).",
                )
                return f"file://{MEDIA_STAGING_DIR}", ""
            except Exception as exc:
                # Never log the token — _download/_sftp errors don't carry
                # headers, but stay defensive if requests ever echoes a URL.
                safe = str(exc).replace(token, "[REDACTED]")
                logger.warning("GitHub App media staging failed: %s", safe)
                _append_log(
                    server,
                    f"GitHub App media staging failed ({safe}); trying direct clone.",
                )
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

    # Legacy fallback: direct https clone with the profile token (if any).
    legacy_token = ""
    try:
        profile = getattr(server, "media_profile", None)
        legacy_token = str(getattr(profile, "script_repo_token", "") or "").strip()
    except Exception:
        legacy_token = ""
    if legacy_token:
        _append_log(server, "Using MediaNodeProfile token for direct repo clone.")
    else:
        _append_log(
            server,
            "No GitHub App token and no profile token — installer will "
            "attempt an unauthenticated clone (works for public repos only).",
        )
    return repo_url, legacy_token
