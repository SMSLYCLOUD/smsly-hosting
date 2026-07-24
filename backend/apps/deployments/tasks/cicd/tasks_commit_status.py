"""Provider-aware commit status dispatcher.

Detects the Git provider from the repository URL and posts deployment
status (pending/success/failure) as commit statuses to GitHub, GitLab,
or Bitbucket.  Replaces direct calls to update_github_commit_status.
"""
from __future__ import annotations

import logging
from urllib.parse import quote, urlparse

import requests
from celery import shared_task

logger = logging.getLogger(__name__)

# GitLab API state mapping
_GITLAB_STATE_MAP = {
    "pending": "pending",
    "success": "success",
    "failure": "failed",
    "error": "failed",
    "cancelled": "canceled",
}

# Bitbucket API state mapping
_BITBUCKET_STATE_MAP = {
    "pending": "INPROGRESS",
    "success": "SUCCESS",
    "failure": "FAILED",
    "error": "FAILED",
    "cancelled": "STOPPED",
}


def _detect_provider(repository_url: str) -> str:
    """Detect the Git provider from a repository URL.

    Returns 'github', 'gitlab', or 'bitbucket'. Falls back to 'github'
    if the host is not recognized (self-hosted GitLab instances configured
    via settings.GITLAB_URL should also match 'gitlab').
    """
    if not repository_url:
        return "github"

    try:
        parsed = urlparse(repository_url)
        host = (parsed.hostname or "").lower()
    except Exception:
        return "github"

    if "github" in host:
        return "github"
    if "gitlab" in host:
        return "gitlab"
    if "bitbucket" in host:
        return "bitbucket"

    # Self-hosted GitLab: check against configured GITLAB_URL
    try:
        from django.conf import settings
        configured = getattr(settings, "GITLAB_URL", "") or ""
        if configured:
            configured_host = urlparse(configured).hostname or ""
            if host == configured_host.lower():
                return "gitlab"
    except Exception:
        pass

    return "github"


def _extract_repo_path(repository_url: str) -> str | None:
    """Extract 'owner/repo' (or 'group/project') from a repository URL."""
    try:
        parsed = urlparse(repository_url)
        path_parts = parsed.path.strip("/").replace(".git", "").split("/")
        if len(path_parts) >= 2:
            return f"{path_parts[0]}/{path_parts[1]}"
    except Exception:
        pass
    return None


# ── GitHub ────────────────────────────────────────────────────────────────────

def _post_github(deployment, state: str, description: str, target_url: str) -> bool:
    """Post a commit status to GitHub via the GitHub App installation."""
    from apps.deployments.services.github_app import (
        get_github_app_service,
        get_installation_for_repo,
    )

    repo_full_name = _extract_repo_path(deployment.service.repository_url)
    if not repo_full_name:
        return False

    installation = get_installation_for_repo(repo_full_name)
    if not installation:
        logger.debug("No GitHub App installation covers %s", repo_full_name)
        return False

    svc = get_github_app_service()
    if not svc:
        return False

    return svc.create_commit_status(
        installation_id=installation.installation_id,
        repo_full_name=repo_full_name,
        sha=deployment.commit_hash,
        state=state,
        description=description[:140],
        context="smsly/deploy",
        target_url=target_url,
    )


# ── GitLab ────────────────────────────────────────────────────────────────────

def _get_gitlab_token(user) -> str | None:
    """Retrieve the stored GitLab OAuth token for *user*, refreshing if needed."""
    try:
        from allauth.socialaccount.models import SocialAccount, SocialToken

        account = SocialAccount.objects.filter(user=user, provider="gitlab").order_by("-id").first()
        if not account:
            return None
        token_obj = SocialToken.objects.filter(account=account).order_by("-id").first()
        if not token_obj:
            return None

        if token_obj.expires_at:
            from django.utils import timezone
            if token_obj.expires_at <= timezone.now():
                _refresh_gitlab_token(token_obj)

        return token_obj.token
    except Exception:
        return None


def _refresh_gitlab_token(token_obj) -> bool:
    """Attempt to refresh a GitLab OAuth token in-place."""
    refresh_token = getattr(token_obj, "token_secret", None)
    if not refresh_token:
        return False
    try:
        from allauth.socialaccount.models import SocialApp
        from django.conf import settings
        from django.utils import timezone

        app = SocialApp.objects.filter(provider="gitlab").first()
        if not app:
            return False

        gitlab_url = getattr(settings, "GITLAB_URL", "https://gitlab.com") or "https://gitlab.com"
        resp = requests.post(
            f"{gitlab_url}/oauth/token",
            data={
                "client_id": app.client_id,
                "client_secret": app.secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=10,
        )
        data = resp.json()
        if "access_token" not in data:
            return False

        token_obj.token = data["access_token"]
        if data.get("refresh_token"):
            token_obj.token_secret = data["refresh_token"]
        from datetime import timedelta
        token_obj.expires_at = timezone.now() + timedelta(seconds=int(data.get("expires_in", 7200)))
        token_obj.save()
        return True
    except Exception:
        return False


def _post_gitlab(deployment, state: str, description: str, target_url: str) -> bool:
    """Post a commit status to GitLab via the user's OAuth token."""
    from django.conf import settings

    user = deployment.service.owner
    if not user:
        return False

    token = _get_gitlab_token(user)
    if not token:
        logger.debug("No GitLab token for user %s; skipping commit status", user)
        return False

    repo_url = deployment.service.repository_url
    gitlab_url = getattr(settings, "GITLAB_URL", "https://gitlab.com") or "https://gitlab.com"
    api_base = f"{gitlab_url.rstrip('/')}/api/v4"

    # Resolve project ID from URL
    try:
        parsed = urlparse(repo_url)
        project_path = parsed.path.strip("/").replace(".git", "")
        encoded_path = quote(project_path, safe="")

        resp = requests.get(
            f"{api_base}/projects/{encoded_path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning("GitLab project lookup failed (%s): %s", resp.status_code, resp.text[:200])
            return False
        project_id = resp.json().get("id")
    except Exception:
        logger.exception("Failed to resolve GitLab project for %s", repo_url)
        return False

    if not project_id or not deployment.commit_hash:
        return False

    gitlab_state = _GITLAB_STATE_MAP.get(state, "pending")

    try:
        resp = requests.post(
            f"{api_base}/projects/{project_id}/statuses/{deployment.commit_hash}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "state": gitlab_state,
                "description": description[:140],
                "context": "smsly/deploy",
                **({"target_url": target_url} if target_url else {}),
            },
            timeout=10,
        )
        if resp.status_code in (200, 201):
            return True
        logger.warning("GitLab commit status failed (%s): %s", resp.status_code, resp.text[:200])
    except Exception:
        logger.exception("Failed to post GitLab commit status")
    return False


# ── Bitbucket ─────────────────────────────────────────────────────────────────

def _get_bitbucket_token(user) -> str | None:
    """Retrieve the stored Bitbucket OAuth token for *user*, refreshing if needed."""
    try:
        from allauth.socialaccount.models import SocialAccount, SocialToken

        account = SocialAccount.objects.filter(user=user, provider="bitbucket_oauth2").order_by("-id").first()
        if not account:
            return None
        token_obj = SocialToken.objects.filter(account=account).order_by("-id").first()
        if not token_obj:
            return None

        if token_obj.expires_at:
            from django.utils import timezone
            if token_obj.expires_at <= timezone.now():
                _refresh_bitbucket_token(token_obj)

        return token_obj.token
    except Exception:
        return None


def _refresh_bitbucket_token(token_obj) -> bool:
    """Attempt to refresh a Bitbucket OAuth token in-place."""
    refresh_token = getattr(token_obj, "token_secret", None)
    if not refresh_token:
        return False
    try:
        from allauth.socialaccount.models import SocialApp
        from django.utils import timezone

        app = SocialApp.objects.filter(provider="bitbucket_oauth2").first()
        if not app:
            return False

        resp = requests.post(
            "https://bitbucket.org/site/oauth2/access_token",
            auth=(app.client_id, app.secret),
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=10,
        )
        data = resp.json()
        if "access_token" not in data:
            return False

        token_obj.token = data["access_token"]
        if data.get("refresh_token"):
            token_obj.token_secret = data["refresh_token"]
        from datetime import timedelta
        token_obj.expires_at = timezone.now() + timedelta(seconds=int(data.get("expires_in", 7200)))
        token_obj.save()
        return True
    except Exception:
        return False


def _post_bitbucket(deployment, state: str, description: str, target_url: str) -> bool:
    """Post a build status to Bitbucket via the user's OAuth token."""
    user = deployment.service.owner
    if not user:
        return False

    token = _get_bitbucket_token(user)
    if not token:
        logger.debug("No Bitbucket token for user %s; skipping commit status", user)
        return False

    repo_path = _extract_repo_path(deployment.service.repository_url)
    if not repo_path or not deployment.commit_hash:
        return False

    bb_state = _BITBUCKET_STATE_MAP.get(state, "INPROGRESS")

    try:
        resp = requests.post(
            f"https://api.bitbucket.org/2.0/repositories/{repo_path}/commit/{deployment.commit_hash}/statuses/build",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "state": bb_state,
                "key": "smsly-deploy",
                "name": "SMSLY Deploy",
                "description": description[:140],
                **({"url": target_url} if target_url else {}),
            },
            timeout=10,
        )
        if resp.ok and resp.status_code in (200, 201):
            return True
        logger.warning("Bitbucket commit status failed (%s): %s", resp.status_code, resp.text[:200])
    except Exception:
        logger.exception("Failed to post Bitbucket commit status")
    return False


# ── Dispatcher Task ───────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=2, soft_time_limit=30, time_limit=60)
def update_commit_status(
    self,
    deployment_id: str,
    state: str,
    description: str,
    target_url: str = "",
):
    """Post a commit status to the correct provider for a deployment.

    Detects the provider from the service's repository_url and dispatches
    to the appropriate API (GitHub App, GitLab OAuth, or Bitbucket OAuth).

    Args:
        deployment_id: UUID string of the Deployment.
        state: One of 'pending', 'success', 'failure', 'error'.
        description: Short description (max 140 chars).
        target_url: Optional URL to link from the status.
    """
    try:
        from apps.deployments.models import Deployment

        deployment = Deployment.objects.select_related("service", "service__owner").get(id=deployment_id)
        service = deployment.service

        if not service or not service.repository_url:
            return

        if not deployment.commit_hash or len(deployment.commit_hash) < 7:
            return

        provider = _detect_provider(service.repository_url)
        desc = description[:140]

        if provider == "gitlab":
            _post_gitlab(deployment, state, desc, target_url)
        elif provider == "bitbucket":
            _post_bitbucket(deployment, state, desc, target_url)
        else:
            _post_github(deployment, state, desc, target_url)

    except Deployment.DoesNotExist:
        logger.warning("Deployment %s not found for commit status update", deployment_id)
    except Exception as exc:
        logger.exception("Error updating commit status: %s", exc)
        raise self.retry(exc=exc)
