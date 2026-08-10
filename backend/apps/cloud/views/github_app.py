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
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

logger = logging.getLogger(__name__)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def github_app_install_url(request) -> Response:
    """Return the URL to redirect the user to for GitHub App installation."""
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

    return Response({"url": f"https://github.com/apps/{slug}/installations/new"})


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
@permission_classes([IsAuthenticated])
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

    # If state token is present, resolve user from JWT
    target_user = request.user
    if state and not target_user.is_authenticated:
        try:
            payload = pyjwt.decode(state, dj_settings.SECRET_KEY, algorithms=["HS256"])
            user_id = payload.get("user_id")
            if user_id:
                from django.contrib.auth import get_user_model
                User = get_user_model()
                target_user = User.objects.filter(id=user_id).first() or target_user
        except Exception:
            pass

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

    # Fetch accessible repositories
    repos_data = svc.list_installation_repos(installation_id)
    repositories = [{"id": r["id"], "name": r["full_name"]} for r in repos_data]

    # Prevent ownership hijack: if installation already linked to a different user, reject
    from apps.cloud.models.github_app import GitHubAppInstallation as GHAI
    existing = GHAI.objects.filter(installation_id=installation_id).first()
    if existing and existing.user and existing.user != target_user:
        return Response(
            {"error": "This installation is already linked to another account."},
            status=status.HTTP_409_CONFLICT,
        )

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
        frontend_url = getattr(dj_settings, "FRONTEND_URL", "https://grid.smsly.cloud")
        return HttpResponseRedirect(f"{frontend_url.rstrip('/')}/settings/integrations?github_app=connected")

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
