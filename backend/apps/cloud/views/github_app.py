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
def github_app_install_url(request):
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


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def github_app_callback(request):
    """Process a GitHub App installation callback.

    Expects {"installation_id": <int>} in the request body.
    Fetches installation details from GitHub and creates/updates the local record.
    """
    from apps.cloud.models.github_app import GitHubAppInstallation
    from apps.deployments.services.github_app import get_github_app_service

    installation_id = request.data.get("installation_id")
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
    if existing and existing.user and existing.user != request.user:
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
            "user": request.user,
            "suspended_at": None,
            "deleted_at": None,
        },
    )

    return Response(
        {
            "id": str(installation.id),
            "installation_id": installation_id,
            "account_login": installation.account_login,
            "account_type": installation.account_type,
            "repositories": installation.repositories,
            "created": created,
        },
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def github_app_installations(request):
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
def github_app_installation_repos(request, installation_id: int):
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
def github_app_installation_delete(request, installation_id: int):
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
