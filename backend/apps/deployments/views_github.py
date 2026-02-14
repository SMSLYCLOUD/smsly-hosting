"""GitHub integration views — repository listing for deployment UI."""

from __future__ import annotations

import logging

import requests
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

logger = logging.getLogger(__name__)


def _get_github_token(user):
    """Retrieve the stored GitHub OAuth token for *user*, or None."""
    try:
        from allauth.socialaccount.models import SocialAccount, SocialToken

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
        return getattr(token, "token", None)
    except Exception:
        return None


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def github_repos(request):
    """
    Return the authenticated user's GitHub repositories.

    Query params
    ------------
    q       — Optional search filter (matched against full_name).
    page    — Page number (default 1).
    per_page — Results per page (default 30, max 100).
    sort    — 'updated' (default), 'created', 'pushed', 'full_name'.
    """
    token = _get_github_token(request.user)
    if not token:
        return Response(
            {
                "error": "GitHub not connected. Please link your GitHub account first.",
                "repos": [],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    page = int(request.query_params.get("page", 1))
    per_page = min(int(request.query_params.get("per_page", 30)), 100)
    sort = request.query_params.get("sort", "updated")
    q = request.query_params.get("q", "").strip()

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        if q:
            # Use the search API for query filtering
            gh = requests.get(
                "https://api.github.com/search/repositories",
                headers=headers,
                params={
                    "q": f"{q} user:@me fork:true",
                    "sort": "updated",
                    "per_page": per_page,
                    "page": page,
                },
                timeout=10,
            )
        else:
            gh = requests.get(
                "https://api.github.com/user/repos",
                headers=headers,
                params={
                    "sort": sort,
                    "direction": "desc",
                    "per_page": per_page,
                    "page": page,
                    "affiliation": "owner,collaborator,organization_member",
                },
                timeout=10,
            )

        gh.raise_for_status()
        raw = gh.json()

        # search API nests inside "items"
        items = raw.get("items", raw) if q else raw

        repos = [
            {
                "full_name": r["full_name"],
                "name": r["name"],
                "private": r["private"],
                "default_branch": r.get("default_branch", "main"),
                "html_url": r["html_url"],
                "clone_url": r["clone_url"],
                "description": r.get("description") or "",
                "language": r.get("language") or "",
                "updated_at": r.get("updated_at"),
                "stargazers_count": r.get("stargazers_count", 0),
            }
            for r in items
            if isinstance(r, dict)
        ]

        return Response({"repos": repos, "page": page, "per_page": per_page})

    except requests.exceptions.HTTPError as exc:
        sc = exc.response.status_code if exc.response is not None else 502
        detail = "GitHub API error"
        if sc == 401:
            detail = "GitHub token expired or revoked. Please reconnect your account."
        logger.warning("GitHub API error %s: %s", sc, exc)
        return Response({"error": detail, "repos": []}, status=sc)
    except Exception as exc:
        logger.exception("Failed to fetch GitHub repos")
        return Response(
            {"error": str(exc), "repos": []},
            status=status.HTTP_502_BAD_GATEWAY,
        )
