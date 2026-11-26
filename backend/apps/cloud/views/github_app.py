"""GitHub App installation API endpoints.

Handles the GitHub App installation flow:
- Redirecting users to GitHub's installation page
- Processing the installation callback
- Listing and managing installations and their repos
"""
from __future__ import annotations

import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

logger = logging.getLogger(__name__)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def github_app_install_url(request) -> Response:
    """Return the URL to redirect the user to for GitHub App installation."""
    import time
    from urllib.parse import urlencode

    from django.conf import settings
    import jwt as pyjwt

    from apps.deployments.services.github_app import get_github_app_service

    svc = get_github_app_service()
    if svc is None:
        return Response(
            {"error": "GitHub App is not configured."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    slug = svc.get_app_slug()
    if not slug:
        return Response(
            {"error": "Could not determine GitHub App slug."},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    state_payload = {
        "user_id": str(request.user.id),
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
    }
    state_token = pyjwt.encode(
        state_payload,
        settings.SECRET_KEY,
        algorithm="HS256",
    )

    return Response({
        "url": f"https://github.com/apps/{slug}/installations/new?{urlencode({'state': state_token})}"
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def github_app_install_with_oauth(request) -> Response:
    """Start combined GitHub App install + OAuth flow.

    Redirects to GitHub's installation page with a state token encoding
    the user's ID.  After installation, GitHub redirects back with both
    ``installation_id`` and ``code`` (OAuth), which the callback exchanges
    to automatically identify and link the user.
    """
    import time
    from urllib.parse import urlencode

    from django.conf import settings
    import jwt as pyjwt

    from apps.deployments.services.github_app import get_github_app_service

    svc = get_github_app_service()
    if svc is None:
        return Response(
            {"error": "GitHub App is not configured."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    slug = svc.get_app_slug()
    if not slug:
        return Response(
            {"error": "Could not determine GitHub App slug."},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    state_payload = {
        "user_id": str(request.user.id),
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
    }
    state_token = pyjwt.encode(
        state_payload,
        settings.SECRET_KEY,
        algorithm="HS256",
    )

    install_url = (
        f"https://github.com/apps/{slug}/installations/new"
        f"?{urlencode({'state': state_token})}"
    )
    return Response({"url": install_url})


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def github_app_callback(request) -> Response:
    """Process a GitHub App installation callback.

    Supports two modes:
    1. Direct: ``{"installation_id": <int>}`` in request body (existing flow).
    2. Combined OAuth: ``installation_id`` and ``state`` as query params or
       body fields, where *state* is a JWT encoding the user ID (from
       ``github_app_install_with_oauth``).  When *state* is present the
       user is identified from the JWT rather than the session, so the
       endpoint can be called unauthenticated (e.g. from a redirect).

    GET is supported for GitHub's redirect-based installation flow.
    """
    import jwt as pyjwt
    from django.conf import settings as dj_settings

    from apps.cloud.models.github_app import GitHubAppInstallation
    from apps.deployments.services.github_app import get_github_app_service

    # Accept installation_id from body or query params
    installation_id = (
        request.data.get("installation_id")
        or request.query_params.get("installation_id")
    )
    state = (
        request.data.get("state")
        or request.query_params.get("state")
    )

    # If state token is present, ALWAYS verify signature and match user_id
    target_user = request.user
    if state:
        try:
            payload = pyjwt.decode(state, dj_settings.SECRET_KEY, algorithms=["HS256"])
        except Exception:
            return Response(
                {"error": "Invalid or expired OAuth state."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        state_user_id = payload.get("user_id")
        if not state_user_id:
            return Response(
                {"error": "Invalid OAuth state payload."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if target_user.is_authenticated and str(target_user.id) != str(state_user_id):
            return Response(
                {"error": "OAuth state does not match authenticated user."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not target_user.is_authenticated:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            target_user = User.objects.filter(id=state_user_id, is_active=True).first()
            if not target_user:
                return Response(
                    {"error": "Authenticated user not found for state."},
                    status=status.HTTP_403_FORBIDDEN,
                )

    if not target_user or not target_user.is_authenticated:
        return Response(
            {"error": "Authentication or signed OAuth state is required."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    if not installation_id:
        return Response(
            {"error": "installation_id is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        installation_id = int(installation_id)
    except (TypeError, ValueError):
        return Response(
            {"error": "installation_id must be an integer."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if installation_id <= 0:
        return Response(
            {"error": "Invalid installation_id."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    svc = get_github_app_service()
    if svc is None:
        return Response(
            {"error": "GitHub App is not configured."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    # Fetch installation details from GitHub
    inst_data = svc.get_installation(installation_id)
    if inst_data is None:
        return Response(
            {"error": "Could not fetch installation details from GitHub."},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    account = inst_data.get("account", {})

    # Prevent ownership hijack: if installation already linked to a different user, reject
    from apps.cloud.models.github_app import GitHubAppInstallation as GHAI
    existing = GHAI.objects.filter(installation_id=installation_id).first()
    if existing and existing.user and existing.user != target_user:
        return Response(
            {"error": "This installation is already linked to another account."},
            status=status.HTTP_409_CONFLICT,
        )

    # Verify installation ownership via GitHub SocialAccount UID or user/installations API
    import requests as http_requests
    from allauth.socialaccount.models import SocialAccount, SocialToken

    inst_id_int = int(installation_id)
    inst_account_id = account.get("id")
    social_account = (
        SocialAccount.objects.filter(user=target_user, provider="github")
        .order_by("-id")
        .first()
    )
    ownership_verified = False
    if social_account and inst_account_id and str(social_account.uid) == str(inst_account_id):
        ownership_verified = True
    else:
        social_token = None
        if social_account:
            social_token = (
                SocialToken.objects.filter(account=social_account)
                .order_by("-id")
                .first()
            )
        if not social_token:
            social_token = (
                SocialToken.objects.filter(
                    account__user=target_user,
                    account__provider="github",
                )
                .order_by("-id")
                .first()
            )
        token_val = getattr(social_token, "token", None) if social_token else None
        if token_val:
            try:
                page = 1
                while page <= 10:
                    resp = http_requests.get(
                        "https://api.github.com/user/installations",
                        headers={
                            "Authorization": f"token {token_val}",
                            "Accept": "application/vnd.github+json",
                            "X-GitHub-Api-Version": "2022-11-28",
                        },
                        params={"per_page": 100, "page": page},
                        timeout=10,
                    )
                    if resp.status_code != 200:
                        break
                    data = resp.json() or {}
                    installations = data.get("installations") or []
                    if any(int(i.get("id", 0)) == inst_id_int for i in installations):
                        ownership_verified = True
                        break
                    total_count = data.get("total_count")
                    if len(installations) < 100 or (
                        total_count is not None and page * 100 >= total_count
                    ):
                        break
                    page += 1
            except Exception as exc:
                logger.warning(
                    "GitHub installation ownership check failed for user=%s installation_id=%s: %s",
                    target_user.id,
                    inst_id_int,
                    exc,
                )

    if not ownership_verified:
        return Response(
            {"error": "Could not verify ownership of this GitHub App installation."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Fetch accessible repositories
    repos_data = svc.list_installation_repos(installation_id)
    repositories = [{"id": r["id"], "name": r["full_name"]} for r in repos_data]

    # Create or update the installation record
    installation, created = GitHubAppInstallation.objects.update_or_create(
        installation_id=installation_id,
        defaults={
            "account_login": account.get("login", ""),
            "account_id": account.get("id", 0),
            "account_type": account.get("type", "User"),
            "account_avatar_url": account.get("avatar_url", ""),
            "status": GitHubAppInstallation.Status.ACTIVE,
            "repository_selection": inst_data.get("repository_selection", "selected"),
            "repositories": repositories,
            "permissions": inst_data.get("permissions", {}),
            "events": inst_data.get("events", []),
            "user": target_user if target_user.is_authenticated else None,
            "suspended_at": None,
            "deleted_at": None,
        },
    )

    from django.http import HttpResponseRedirect
    from django.conf import settings as dj_settings

    result = {
        "id": str(installation.id),
        "installation_id": installation_id,
        "account_login": installation.account_login,
        "account_type": installation.account_type,
        "repositories": installation.repositories,
        "created": created,
    }

    # For browser redirects (GET from GitHub), redirect to frontend settings
    if request.method == "GET":
        from apps.cloud.views.github_app_manifest import _platform_base_url
        frontend_url = _platform_base_url(request)
        if frontend_url:
            return HttpResponseRedirect(f"{frontend_url.rstrip('/')}/settings?tab=git&github_app=connected")
        return HttpResponseRedirect("/settings?tab=git&github_app=connected")

    return Response(result, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def github_app_installations(request) -> Response:
    """List GitHub App installations linked to the current user."""
    from apps.cloud.models.github_app import GitHubAppInstallation

    installations = GitHubAppInstallation.objects.filter(
        user=request.user,
        status=GitHubAppInstallation.Status.ACTIVE,
    )

    return Response(
        {
            "installations": [
                {
                    "id": str(inst.id),
                    "installation_id": inst.installation_id,
                    "account_login": inst.account_login,
                    "account_type": inst.account_type,
                    "account_avatar_url": inst.account_avatar_url,
                    "repository_selection": inst.repository_selection,
                    "repo_count": len(inst.repositories or []),
                    "created_at": inst.created_at.isoformat(),
                }
                for inst in installations
            ]
        }
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def github_app_installation_repos(request, installation_id: int) -> Response:
    """List repos accessible to a specific installation."""
    from apps.cloud.models.github_app import GitHubAppInstallation
    from apps.deployments.services.github_app import get_github_app_service

    try:
        installation = GitHubAppInstallation.objects.get(
            installation_id=installation_id,
            user=request.user,
            status=GitHubAppInstallation.Status.ACTIVE,
        )
    except GitHubAppInstallation.DoesNotExist:
        return Response(
            {"error": "Installation not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Refresh repo list from GitHub
    svc = get_github_app_service()
    if svc is None:
        # Return cached repos if service unavailable
        return Response({"repositories": installation.repositories or []})

    repos = svc.list_installation_repos(installation_id)
    repo_list = [{"id": r["id"], "full_name": r["full_name"], "private": r.get("private", False)} for r in repos]

    # Update cached repos
    installation.repositories = [{"id": r["id"], "name": r["full_name"]} for r in repos]
    installation.save(update_fields=["repositories", "updated_at"])

    return Response({"repositories": repo_list})


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def github_app_installation_delete(request, installation_id: int) -> Response:
    """Unlink an installation from the platform.

    This does NOT uninstall the app on GitHub — the user must do that separately.
    """
    from apps.cloud.models.github_app import GitHubAppInstallation

    try:
        installation = GitHubAppInstallation.objects.get(
            installation_id=installation_id,
            user=request.user,
        )
    except GitHubAppInstallation.DoesNotExist:
        return Response(
            {"error": "Installation not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    installation.status = GitHubAppInstallation.Status.DELETED
    installation.deleted_at = timezone.now()
    installation.user = None
    installation.save(update_fields=["status", "deleted_at", "user", "updated_at"])

    return Response(status=status.HTTP_204_NO_CONTENT)
