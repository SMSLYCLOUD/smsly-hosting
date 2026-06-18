"""Normalize repository URLs for accurate webhook-to-service matching."""
from typing import Optional


def normalize_repo_url(url: str) -> Optional[str]:
    """
    Normalize a repository URL to ``{owner}/{repo}`` format for matching.

    Handles:
    - https://github.com/owner/repo
    - https://github.com/owner/repo.git
    - git@github.com:owner/repo.git
    - ssh://git@github.com/owner/repo.git
    - http://gitlab.com/owner/repo
    - https://bitbucket.org/owner/repo
    - ``owner/repo`` (already normalized)

    Returns ``None`` if the URL cannot be parsed.
    """
    url = url.strip().rstrip('/').replace('.git', '')

    # SSH format: git@github.com:owner/repo
    for prefix in ['git@', 'ssh://git@']:
        if url.startswith(prefix):
            url = url[len(prefix):]
            if ':' in url:
                url = url.replace(':', '/', 1)
            break

    # Strip protocol prefixes
    for prefix in ['https://', 'http://', 'ssh://']:
        if url.startswith(prefix):
            url = url[len(prefix):]
            break

    # Now should be host/owner/repo
    parts = url.split('/')
    if len(parts) >= 3:
        # host/owner/repo → owner/repo
        return '/'.join(parts[-2:])
    if len(parts) == 2:
        # Already owner/repo
        return url
    return None


def match_service_repo(stored_url: str, webhook_repo_url: str) -> bool:
    """
    Check if a stored service repository URL matches a webhook payload URL.
    Uses normalized owner/repo comparison to avoid ``__icontains`` false matches.
    """
    stored = normalize_repo_url(stored_url)
    incoming = normalize_repo_url(webhook_repo_url)
    if not stored or not incoming:
        return False
    return stored.lower() == incoming.lower()
