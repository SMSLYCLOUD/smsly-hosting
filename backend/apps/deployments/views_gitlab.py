"""GitLab repo views — repository, branch, and commit listing."""
from __future__ import annotations

import logging
import requests
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

logger = logging.getLogger(__name__)

GITLAB_DEFAULT_URL = "https://gitlab.com"


def _get_gitlab_token(user):
    try:
        from allauth.socialaccount.models import SocialAccount, SocialToken
        account = SocialAccount.objects.filter(user=user, provider="gitlab").order_by("-id").first()
        if not account:
            return None
        token_obj = SocialToken.objects.filter(account=account).order_by("-id").first()
        if not token_obj:
            return None
        return token_obj.token
    except Exception:
        return None


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def gitlab_repos(request):
    token = _get_gitlab_token(request.user)
    if not token:
        return Response({"error": "GitLab not connected.", "repos": []}, status=status.HTTP_400_BAD_REQUEST)

    try:
        resp = requests.get(
            f"{GITLAB_DEFAULT_URL}/api/v4/projects",
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
        resp = requests.get(
            f"{GITLAB_DEFAULT_URL}/api/v4/projects/{project_path}/repository/branches",
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
        resp = requests.get(
            f"{GITLAB_DEFAULT_URL}/api/v4/projects/{project_path}/repository/commits",
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
