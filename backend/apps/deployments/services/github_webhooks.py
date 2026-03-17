"""GitHub Webhooks setup service."""
import logging
import requests
from django.conf import settings
from urllib.parse import urlparse
from apps.deployments.views_github import _get_github_token

logger = logging.getLogger(__name__)

def setup_github_webhook(user, repo_url: str):
    """
    Sets up a GitHub webhook for the given repository if it doesn't already exist.
    """
    if not repo_url:
        return

    # Check if the URL is a GitHub URL
    parsed = urlparse(repo_url)
    if parsed.hostname != "github.com":
        return

    # Extract owner/repo
    path_parts = parsed.path.strip("/").split("/")
    if len(path_parts) < 2:
        return

    owner = path_parts[0]
    repo = path_parts[1].replace(".git", "")
    full_name = f"{owner}/{repo}"

    token = _get_github_token(user)
    if not token:
        logger.warning(f"No GitHub token found for user {user.username}. Cannot setup webhook for {full_name}.")
        return

    webhook_secret = getattr(settings, "GITHUB_WEBHOOK_SECRET", "")
    if not webhook_secret or webhook_secret == "replace_me_with_random_string":
        logger.error(
            "GITHUB_WEBHOOK_SECRET is missing/placeholder. Refusing to create webhook until a secure secret is set."
        )
        return

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
        resp.raise_for_status()
        hooks = resp.json()

        for hook in hooks:
            config = hook.get("config", {})
            if config.get("url") == target_webhook_url:
                logger.info(f"Webhook already exists for {full_name} pointing to {target_webhook_url}")
                return

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
        create_resp.raise_for_status()
        logger.info(f"Successfully created GitHub webhook for {full_name}")

    except requests.exceptions.HTTPError as exc:
        sc = exc.response.status_code if exc.response is not None else 502

        if sc == 401:
            logger.info(f"GitHub webhook setup for {full_name} failed with 401. Forcing token refresh and retrying.")
            refreshed_token = _get_github_token(user, force_refresh=True)
            if refreshed_token and refreshed_token != token:
                headers["Authorization"] = f"token {refreshed_token}"
                try:
                    resp = requests.get(
                        f"https://api.github.com/repos/{full_name}/hooks",
                        headers=headers,
                        timeout=10
                    )
                    resp.raise_for_status()
                    hooks = resp.json()

                    for hook in hooks:
                        config = hook.get("config", {})
                        if config.get("url") == target_webhook_url:
                            logger.info(f"Webhook already exists for {full_name} pointing to {target_webhook_url}")
                            return

                    create_resp = requests.post(
                        f"https://api.github.com/repos/{full_name}/hooks",
                        headers=headers,
                        json=payload,
                        timeout=10
                    )
                    create_resp.raise_for_status()
                    logger.info(f"Successfully created GitHub webhook for {full_name}")
                    return
                except Exception as retry_exc:
                    logger.error(f"Retry failed to setup GitHub webhook for {full_name}: {retry_exc}")

        logger.error(f"Failed to setup GitHub webhook for {full_name}. Status: {sc}. Response: {exc.response.text if exc.response else ''}")
    except Exception as exc:
        logger.exception(f"Unexpected error setting up GitHub webhook for {full_name}: {exc}")
