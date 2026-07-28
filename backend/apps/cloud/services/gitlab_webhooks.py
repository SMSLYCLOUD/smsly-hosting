"""GitLab Webhooks setup service."""
import logging
from urllib.parse import urlparse

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def setup_gitlab_webhook(user, repo_url: str) -> bool:
    """
    Sets up a GitLab webhook for the given repository.
    Creates a Push Event hook pointing to the platform's GitLab webhook endpoint.
    """
    if not repo_url:
        return False

    parsed = urlparse(repo_url)
    gitlab_url = getattr(settings, 'GITLAB_URL', 'https://gitlab.com').rstrip('/')
    hostname = parsed.hostname or ''

    # Normalize gitlab.com vs self-hosted GitLab
    # Allow any hostname that matches the configured GITLAB_URL, not
    # just hosts containing the literal string 'gitlab' (self-hosted).
    if hostname not in gitlab_url and 'gitlab' not in hostname:
        return False

    # Extract owner/repo from URL (supports gitlab.com/owner/repo and self-hosted)
    path_parts = [p for p in parsed.path.strip('/').split('/') if p]
    if len(path_parts) < 2:
        return False
    repo = path_parts[-1].replace('.git', '')
    # GitLab API uses URL-encoded project path: namespace/project
    project_path = '/'.join(path_parts[:-1]) + '/' + repo

    token = _get_gitlab_token(user)
    if not token:
        return False

    secret = getattr(settings, 'GITLAB_WEBHOOK_SECRET', '')
    if not secret:
        logger.error("GITLAB_WEBHOOK_SECRET not configured")
        return False

    base_url = getattr(settings, 'SITE_URL', 'http://localhost:8000').rstrip('/')
    webhook_url = f"{base_url}/api/v1/webhooks/gitlab/"

    headers = {
        'PRIVATE-TOKEN': token,
        'Content-Type': 'application/json',
    }

    try:
        project_id = requests.get(
            f"{gitlab_url}/api/v4/projects/{requests.utils.quote(project_path, safe='')}",
            headers=headers, timeout=10,
        ).json().get('id')

        if not project_id:
            return False

        existing = requests.get(
            f"{gitlab_url}/api/v4/projects/{project_id}/hooks",
            headers=headers, timeout=10,
        )
        if existing.ok:
            for hook in existing.json():
                if hook.get('url', '').rstrip('/') == webhook_url.rstrip('/'):
                    logger.info("GitLab webhook already exists for %s", project_path)
                    return True

        resp = requests.post(
            f"{gitlab_url}/api/v4/projects/{project_id}/hooks",
            headers=headers,
            json={
                'url': webhook_url,
                'push_events': True,
                'merge_request_events': True,
                'token': secret,
                'enable_ssl_verification': base_url.startswith('https'),
            },
            timeout=10,
        )
        if resp.ok:
            logger.info("GitLab webhook created for %s", project_path)
            return True
        logger.warning("GitLab webhook creation failed: %s", resp.text[:200])
        return False

    except requests.RequestException as e:
        logger.warning("GitLab webhook setup error: %s", e)
        return False


def _get_gitlab_token(user):
    """Retrieve the stored GitLab OAuth token for *user*, refreshing if expired."""
    from apps.deployments.views.gitlab import _get_gitlab_token as _get_token
    return _get_token(user)
