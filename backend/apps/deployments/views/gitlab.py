"""GitLab repo views — repository, branch, and commit listing."""
from __future__ import annotations

import contextlib
import logging
from datetime import timedelta

import requests
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

logger = logging.getLogger(__name__)

GITLAB_DEFAULT_URL = "https://gitlab.com"


def _get_gitlab_token(user):
    """Retrieve the stored GitLab OAuth token for *user*, refreshing if expired."""
    try:
        from allauth.socialaccount.models import SocialAccount, SocialToken
        from django.utils import timezone

        account = SocialAccount.objects.filter(user=user, provider="gitlab").order_by("-id").first()
        if not account:
            return None
        token_obj = SocialToken.objects.filter(account=account).order_by("-id").first()
        if not token_obj:
            return None

        if token_obj.expires_at and token_obj.expires_at <= timezone.now():
            refreshed = _refresh_gitlab_token(token_obj)
            if not refreshed:
                return None

        return token_obj.token
    except Exception:
        return None


def _refresh_gitlab_token(token_obj):
    """Use the refresh token to obtain a new GitLab access token."""
    refresh_token = getattr(token_obj, "token_secret", None)
    if not refresh_token:
        logger.warning("No refresh token stored for GitLab — user must reconnect")
        return False

    try:
        from allauth.socialaccount.models import SocialApp
        from django.conf import settings

        app = SocialApp.objects.filter(provider="gitlab").first()
        if not app:
            logger.error("No GitLab SocialApp configured")
            return False

        gitlab_url = getattr(settings, "GITLAB_URL", "https://gitlab.com").rstrip("/")
        resp = requests.post(
            f"{gitlab_url}/oauth/token",
            data={
                "client_id": app.client_id,
                "client_secret": app.secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )
        if not resp.ok:
            logger.warning("GitLab token refresh failed: %s", resp.text[:200])
            return False

        data = resp.json()
        new_token = data.get("access_token", "")
        if not new_token:
            return False

        token_obj.token = new_token
        token_obj.token_secret = data.get("refresh_token", refresh_token)
        if "expires_in" in data:
            with contextlib.suppress(ValueError, TypeError):
                token_obj.expires_at = timezone.now() + timedelta(seconds=int(data["expires_in"]))
        token_obj.save()
        logger.info("GitLab token refreshed successfully")
        return True
    except Exception as exc:
        logger.error("GitLab token refresh failed: %s", exc)
        return False


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def gitlab_repos(request):
    token = _get_gitlab_token(request.user)
    if not token:
        return Response({"error": "GitLab not connected.", "repos": []}, status=status.HTTP_400_BAD_REQUEST)

    try:
        from django.conf import settings as django_settings
        gitlab_url = getattr(django_settings, 'GITLAB_URL', GITLAB_DEFAULT_URL) or GITLAB_DEFAULT_URL
        resp = requests.get(
            f"{gitlab_url.rstrip('/')}/api/v4/projects",
            headers={"Authorization": f"Bearer {token}"},
            params={"membership": True, "per_page": 100, "order_by": "updated_at"},
            timeout=15,
        )
        resp.raise_for_status()
        projects = resp.json()
    except Exception as exc:
        logger.error("GitLab repos fetch failed: %s", exc)
        return Response({"error": str(exc), "repos": []}, status=status.HTTP_502_BAD_GATEWAY)

    repos = []
    for p in projects:
        repos.append({
            "full_name": p.get("path_with_namespace"),
            "name": p.get("name"),
            "private": p.get("visibility") != "public",
            "default_branch": p.get("default_branch", "main"),
            "html_url": p.get("web_url"),
            "clone_url": p.get("http_url_to_repo") or p.get("ssh_url_to_repo"),
            "description": p.get("description") or "",
            "language": "",
            "updated_at": p.get("last_activity_at"),
        })
    return Response({"repos": repos, "page": 1, "per_page": 100})


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def gitlab_branches(request):
    token = _get_gitlab_token(request.user)
    if not token:
        return Response({"error": "GitLab not connected."}, status=status.HTTP_400_BAD_REQUEST)

    repo = request.query_params.get("repo", "")
    if not repo:
        return Response({"error": "repo parameter required"}, status=status.HTTP_400_BAD_REQUEST)

    project_path = repo.replace("/", "%2F")
    try:
        from django.conf import settings as django_settings
        gitlab_url = getattr(django_settings, 'GITLAB_URL', GITLAB_DEFAULT_URL) or GITLAB_DEFAULT_URL
        resp = requests.get(
            f"{gitlab_url.rstrip('/')}/api/v4/projects/{project_path}/repository/branches",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 50},
            timeout=15,
        )
        resp.raise_for_status()
        branches = resp.json()
    except Exception as exc:
        logger.error("GitLab branches fetch failed: %s", exc)
        return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

    return Response([{"name": b.get("name"), "commit": b.get("commit", {}).get("short_id")} for b in branches])


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def gitlab_commits(request):
    token = _get_gitlab_token(request.user)
    if not token:
        return Response({"error": "GitLab not connected."}, status=status.HTTP_400_BAD_REQUEST)

    repo = request.query_params.get("repo", "")
    branch = request.query_params.get("branch", "main")
    if not repo:
        return Response({"error": "repo parameter required"}, status=status.HTTP_400_BAD_REQUEST)

    project_path = repo.replace("/", "%2F")
    try:
        from django.conf import settings as django_settings
        gitlab_url = getattr(django_settings, 'GITLAB_URL', GITLAB_DEFAULT_URL) or GITLAB_DEFAULT_URL
        resp = requests.get(
            f"{gitlab_url.rstrip('/')}/api/v4/projects/{project_path}/repository/commits",
            headers={"Authorization": f"Bearer {token}"},
            params={"ref_name": branch, "per_page": 30},
            timeout=15,
        )
        resp.raise_for_status()
        commits = resp.json()
    except Exception as exc:
        logger.error("GitLab commits fetch failed: %s", exc)
        return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

    return Response([{
        "sha": c.get("id"),
        "message": c.get("title"),
        "author": c.get("author_name"),
        "date": c.get("created_at"),
    } for c in commits])
