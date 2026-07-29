"""Bitbucket Webhooks setup service."""
import logging
from urllib.parse import urlparse

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def setup_bitbucket_webhook(user, repo_url: str) -> bool:
    """
    Sets up a Bitbucket webhook for the given repository.
    Creates a repo:push hook pointing to the platform's Bitbucket webhook endpoint.
    """
    if not repo_url:
        return False

    parsed = urlparse(repo_url)
    if 'bitbucket' not in (parsed.hostname or ''):
        return False

    # Extract workspace/repo from URL
    path_parts = [p for p in parsed.path.strip('/').split('/') if p]
    if len(path_parts) < 2:
        return False
    workspace = path_parts[0]
    repo_slug = path_parts[1].replace('.git', '')

    token = _get_bitbucket_token(user)
    if not token:
        return False

    secret = getattr(settings, 'BITBUCKET_WEBHOOK_SECRET', '')
    if not secret:
        logger.error("BITBUCKET_WEBHOOK_SECRET not configured")
        return False

    base_url = getattr(settings, 'SITE_URL', 'http://localhost:8000').rstrip('/')
    webhook_url = f"{base_url}/api/v1/webhooks/bitbucket/"
    api_base = 'https://api.bitbucket.org/2.0'

    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }

    try:
        existing = requests.get(
            f"{api_base}/repositories/{workspace}/{repo_slug}/hooks",
            headers=headers, timeout=10,
        )
        if existing.ok:
            for hook in existing.json().get('values', []):
                if hook.get('url', '').rstrip('/') == webhook_url.rstrip('/'):
                    logger.info("Bitbucket webhook already exists for %s/%s", workspace, repo_slug)
                    return True

        resp = requests.post(
            f"{api_base}/repositories/{workspace}/{repo_slug}/hooks",
            headers=headers,
            json={
                'description': 'SMSLY Auto-Deploy',
                'url': webhook_url,
                'active': True,
                'events': [
                    'repo:push',
                    'pullrequest:created',
                    'pullrequest:updated',
                    'pullrequest:approved',
                    'pullrequest:fulfilled',
                    'pullrequest:rejected',
                ],
            },
            timeout=10,
        )
        if resp.ok:
            logger.info("Bitbucket webhook created for %s/%s", workspace, repo_slug)
            return True
        logger.warning("Bitbucket webhook creation failed: %s", resp.text[:200])
        return False

    except requests.RequestException as e:
        logger.warning("Bitbucket webhook setup error: %s", e)
        return False


def _get_bitbucket_token(user):
    """Retrieve the stored Bitbucket OAuth token for *user*, refreshing if expired."""
    from apps.deployments.views.bitbucket import _get_bitbucket_token as _get_token
    return _get_token(user)
