"""GitHub Webhooks setup service."""
import logging
from urllib.parse import urlparse

import requests
from django.conf import settings

from apps.deployments.utils.error_handling import (
    ExternalServiceError,
    ValidationError,
    log_error,
)
from apps.deployments.views.github import _get_github_token

logger = logging.getLogger(__name__)

def setup_github_webhook(user, repo_url: str):
    """
    Sets up a GitHub webhook for the given repository if it doesn't already exist.

    Args:
        user: The user object
        repo_url: The GitHub repository URL

    Returns:
        bool: True if successful, False if failed

    Raises:
        ValidationError: If repo_url is invalid
        ExternalServiceError: If GitHub API fails
    """
    if not repo_url:
        raise ValidationError(
            message="Repository URL is required",
            field="repo_url",
            user_message="Please provide a valid GitHub repository URL"
        )

    # Check if the URL is a GitHub URL
    parsed = urlparse(repo_url)
    if parsed.hostname != "github.com":
        raise ValidationError(
            message="Invalid GitHub URL",
            field="repo_url",
            details={"hostname": parsed.hostname, "url": repo_url},
            user_message="Only GitHub repositories are supported"
        )

    # Extract owner/repo
    path_parts = parsed.path.strip("/").split("/")
    if len(path_parts) < 2:
        raise ValidationError(
            message="Invalid GitHub repository path",
            field="repo_path",
            details={"path": parsed.path},
            user_message="Repository URL should be in format: https://github.com/owner/repo"
        )

    owner = path_parts[0]
    repo = path_parts[1].replace(".git", "")
    full_name = f"{owner}/{repo}"

    token = _get_github_token(user)
    if not token:
        log_error(
            ValidationError(
                message="No GitHub token found",
                details={"user_id": user.id, "username": user.username}
            )
        )
        return False

    # Read webhook secret from PlatformConfig (DB) first, then fall back
    # to settings. This matches the verification logic in github.py so
    # the secret used to create the webhook always matches the secret
    # used to verify incoming webhooks.
    webhook_secret = ""
    try:
        from apps.deployments.models.core import PlatformConfig
        webhook_secret = PlatformConfig.load().get_webhook_secret('github') or ""
    except Exception:
        pass
    if not webhook_secret:
        webhook_secret = getattr(settings, "GITHUB_WEBHOOK_SECRET", "")
    if not webhook_secret or webhook_secret == "replace_me_with_random_string":
        logger.error(
            "GITHUB_WEBHOOK_SECRET is missing/placeholder. Refusing to create webhook until a secure secret is set."
        )
        return False

    base_url = getattr(settings, "SITE_URL", "http://localhost:8000").rstrip("/")
    target_webhook_url = f"{base_url}/api/v1/webhooks/github/"

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        # Check existing webhooks
        resp = requests.get(
            f"https://api.github.com/repos/{full_name}/hooks",
            headers=headers,
            timeout=10
        )

        if resp.status_code == 403:
            error_text = resp.text if resp else ''
            if 'rate limit' in error_text.lower() or 'api rate limit exceeded' in error_text.lower():
                raise ExternalServiceError(
                    message="GitHub API rate limit exceeded",
                    service_name="GitHub",
                    service_error=error_text,
                    status_code=403,
                    user_message="GitHub API rate limit exceeded. Please wait before retrying."
                )
            elif 'bad credentials' in error_text.lower() or 'invalid token' in error_text.lower():
                raise ExternalServiceError(
                    message="Invalid GitHub token",
                    service_name="GitHub",
                    service_error=error_text,
                    status_code=403,
                    user_message="Invalid GitHub token. Please check your token permissions."
                )
            elif 'forbidden' in error_text.lower() or 'permission denied' in error_text.lower():
                raise ExternalServiceError(
                    message="Permission denied",
                    service_name="GitHub",
                    service_error=error_text,
                    status_code=403,
                    user_message="Permission denied. Ensure your GitHub token has repository scope."
                )
            else:
                raise ExternalServiceError(
                    message="GitHub API access denied",
                    service_name="GitHub",
                    service_error=error_text,
                    status_code=403,
                    user_message="Access denied to GitHub repository. Please check permissions."
                )

        resp.raise_for_status()
        hooks = resp.json()

        for hook in hooks:
            config = hook.get("config", {})
            if config.get("url") == target_webhook_url:
                logger.info(f"Webhook already exists for {full_name} pointing to {target_webhook_url}")
                return True

        # Create webhook
        payload = {
            "name": "web",
            "active": True,
            "events": ["push", "pull_request"],
            "config": {
                "url": target_webhook_url,
                "content_type": "json",
                "insecure_ssl": "0" if target_webhook_url.startswith("https") else "1",
                "secret": webhook_secret
            }
        }

        create_resp = requests.post(
            f"https://api.github.com/repos/{full_name}/hooks",
            headers=headers,
            json=payload,
            timeout=10
        )

        # Handle specific 403 errors
        if create_resp.status_code == 403:
            error_text = create_resp.text if create_resp else ''
            if 'rate limit' in error_text.lower() or 'api rate limit exceeded' in error_text.lower():
                raise ExternalServiceError(
                    message="GitHub API rate limit exceeded",
                    service_name="GitHub",
                    service_error=error_text,
                    status_code=403,
                    user_message="GitHub API rate limit exceeded. Please wait before retrying."
                )
            elif 'bad credentials' in error_text.lower() or 'invalid token' in error_text.lower():
                raise ExternalServiceError(
                    message="Invalid GitHub token",
                    service_name="GitHub",
                    service_error=error_text,
                    status_code=403,
                    user_message="Invalid GitHub token. Please check your token permissions."
                )
            elif 'forbidden' in error_text.lower() or 'permission denied' in error_text.lower():
                raise ExternalServiceError(
                    message="Permission denied",
                    service_name="GitHub",
                    service_error=error_text,
                    status_code=403,
                    user_message="Permission denied. Ensure your GitHub token has repository scope."
                )
            else:
                raise ExternalServiceError(
                    message="GitHub API access denied",
                    service_name="GitHub",
                    service_error=error_text,
                    status_code=403,
                    user_message="Access denied to GitHub repository. Please check permissions."
                )

        create_resp.raise_for_status()
        logger.info(f"Successfully created GitHub webhook for {full_name}")
        return True

    except requests.exceptions.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        error_text = exc.response.text if exc.response else ''

        # Handle specific 403 errors
        if status_code == 403:
            if 'rate limit' in error_text.lower() or 'api rate limit exceeded' in error_text.lower():
                raise ExternalServiceError(
                    message="GitHub API rate limit exceeded",
                    service_name="GitHub",
                    service_error=error_text,
                    status_code=status_code,
                    user_message="GitHub API rate limit exceeded. Please wait before retrying."
                )
            elif 'bad credentials' in error_text.lower() or 'invalid token' in error_text.lower():
                raise ExternalServiceError(
                    message="Invalid GitHub token",
                    service_name="GitHub",
                    service_error=error_text,
                    status_code=status_code,
                    user_message="Invalid GitHub token. Please check your token permissions."
                )
            elif 'forbidden' in error_text.lower() or 'permission denied' in error_text.lower():
                raise ExternalServiceError(
                    message="Permission denied",
                    service_name="GitHub",
                    service_error=error_text,
                    status_code=status_code,
                    user_message="Permission denied. Ensure your GitHub token has repository scope."
                )
            else:
                raise ExternalServiceError(
                    message="GitHub API access denied",
                    service_name="GitHub",
                    service_error=error_text,
                    status_code=status_code,
                    user_message="Access denied to GitHub repository. Please check permissions."
                )

        raise ExternalServiceError(
            message="Failed to setup GitHub webhook",
            service_name="GitHub",
            service_error=error_text,
            status_code=status_code,
            user_message="Failed to setup GitHub webhook. Please try again later."
        )
    except requests.exceptions.RequestException as exc:
        raise ExternalServiceError(
            message="Network error setting up GitHub webhook",
            service_name="GitHub",
            service_error=str(exc),
            user_message="Network error occurred while setting up webhook. Please check your connection."
        )
    except Exception as exc:
        log_error(exc, {"user_id": user.id, "repo_url": repo_url})
        raise
