"""
GitHub token retrieval utilities.
"""
import logging

logger = logging.getLogger(__name__)


def get_github_token_for_repo(user, repo_full_name: str) -> str | None:
    _repo = (repo_full_name or "").strip()
    if not _repo or "/" not in _repo or len(_repo.split("/")) < 2:
        return get_github_oauth_token_for_user(user)

    try:
        from apps.deployments.services.github_app import get_installation_token_for_repo
        app_token = get_installation_token_for_repo(_repo)
        if app_token:
            return app_token
    except Exception as exc:
        logger.warning(
            "GitHub App token fetch failed for %s, falling back to OAuth: %s",
            _repo,
            exc,
        )

    return get_github_oauth_token_for_user(user)


def get_github_token_for_user(user) -> str | None:
    """Get a GitHub token for user-level operations (listing repos, etc.).

    Tries GitHub App installation token first (for any installation linked
    to the user), then falls back to the user's OAuth token.
    """
    if not user:
        return None

    # Try GitHub App first — find any active installation linked to this user
    try:
        from apps.cloud.models.github_app import GitHubAppInstallation
        from apps.deployments.services.github_app import get_github_app_service

        installation = GitHubAppInstallation.objects.filter(
            user=user,
            status=GitHubAppInstallation.Status.ACTIVE,
        ).order_by('-created_at').first()

        if installation:
            svc = get_github_app_service()
            if svc:
                token = svc.get_installation_token_for_id(installation.installation_id)
                if token:
                    return token
    except Exception as exc:
        logger.debug("GitHub App token fetch failed for user %s, falling back to OAuth: %s", user, exc)

    return get_github_oauth_token_for_user(user)


def get_github_oauth_token_for_user(user):
    if not user:
        return None

    try:
        from allauth.socialaccount.models import SocialAccount, SocialToken
    except Exception:
        return None

    account = (
        SocialAccount.objects.filter(user=user, provider="github")
        .order_by("-id")
        .first()
    )
    if not account:
        return None

    token = (
        SocialToken.objects.filter(account=account)
        .order_by("-id")
        .first()
    )
    if not token:
        return None

    access_token = getattr(token, "token", None)
    if not access_token:
        return None

    try:
        from django.utils import timezone
        expires_at = getattr(token, "expires_at", None)
        if expires_at and expires_at <= timezone.now():
            refresh_token = getattr(token, "token_secret", None)
            if refresh_token:
                try:
                    from datetime import timedelta

                    import requests as http_requests
                    from allauth.socialaccount.models import SocialApp

                    app = SocialApp.objects.filter(provider="github").first()
                    if app:
                        resp = http_requests.post(
                            "https://github.com/login/oauth/access_token",
                            headers={"Accept": "application/json"},
                            data={
                                "client_id": app.client_id,
                                "client_secret": app.secret,
                                "grant_type": "refresh_token",
                                "refresh_token": refresh_token,
                            },
                            timeout=10,
                        )
                        data = resp.json()
                        if "access_token" in data:
                            token.token = data["access_token"]
                            if data.get("refresh_token"):
                                token.token_secret = data["refresh_token"]
                            expires_in = data.get("expires_in", 28800)
                            token.expires_at = timezone.now() + timedelta(seconds=int(expires_in))
                            token.save()
                            access_token = token.token
                            logger.info("GitHub OAuth token refreshed for user %s", user)
                        else:
                            logger.warning("GitHub token refresh failed: %s", data.get("error_description", data))
                except Exception as exc:
                    logger.warning("GitHub token refresh error: %s", exc)
            else:
                logger.warning("No refresh token available for user %s - reconnect required", user)
    except Exception as exc:
        logger.warning("Token expiry check failed: %s", exc)

    return access_token or None
