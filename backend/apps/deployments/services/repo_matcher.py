"""Normalize repository URLs for accurate webhook-to-service matching."""


def normalize_repo_url(url: str) -> str | None:
    """
    Normalize a repository URL to ``{host}/{owner}/{repo}`` format for matching.

    Includes the host so ``github.com/owner/repo`` and ``gitlab.com/owner/repo``
    are distinct — a webhook from one provider will never match a service
    configured for another.

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

    parts = url.split('/')
    if len(parts) >= 3:
        return '/'.join(parts[-3:])  # host/owner/repo
    if len(parts) == 2:
        return url  # owner/repo (no host to disambiguate)
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
