"""GitHub integration views — repository listing for deployment UI."""

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


def _get_github_token(user, force_refresh=False):
    """Retrieve the stored GitHub OAuth token for *user*, refreshing if expired."""
    try:
        from allauth.socialaccount.models import SocialAccount, SocialToken

        account = (
            SocialAccount.objects.filter(user=user, provider="github")
            .order_by("-id")
            .first()
        )
        if not account:
            return None
        token_obj = (
            SocialToken.objects.filter(account=account)
            .order_by("-id")
            .first()
        )
        if not token_obj:
            return None

        # Check if token is expired (or about to expire) and attempt refresh
        needs_refresh = force_refresh
        if not needs_refresh and token_obj.expires_at:
            from django.utils import timezone
            from datetime import timedelta
            # Add a 5 minute buffer to prevent token expiring mid-request
            if token_obj.expires_at <= timezone.now() + timedelta(minutes=5):
                needs_refresh = True

        if needs_refresh:
            refreshed = _refresh_github_token(token_obj)
            if not refreshed:
                return None

        return token_obj.token
    except Exception:
        logger.exception("Failed to get GitHub token")
        return None


def _refresh_github_token(token_obj):
    """Use the refresh token to obtain a new GitHub access token.

    Returns True if refresh succeeded, False otherwise.
    Updates token_obj in-place and saves to DB.
    """
    # token_secret stores the refresh token in allauth
    refresh_token = getattr(token_obj, "token_secret", None)
    if not refresh_token:
        logger.warning("No refresh token stored — user must reconnect GitHub")
        return False

    try:
        from allauth.socialaccount.models import SocialApp
        app = SocialApp.objects.filter(provider="github").first()
        if not app:
            logger.error("No GitHub SocialApp configured")
            return False

        resp = requests.post(
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

        if "access_token" not in data:
            logger.error("GitHub token refresh failed: %s", data.get("error_description", data))
            return False

        # Update stored token
        from django.utils import timezone
        from datetime import timedelta
        token_obj.token = data["access_token"]
        if data.get("refresh_token"):
            token_obj.token_secret = data["refresh_token"]
        expires_in = data.get("expires_in", 28800)  # default 8h
        token_obj.expires_at = timezone.now() + timedelta(seconds=int(expires_in))
        token_obj.save()
        logger.info("GitHub token refreshed successfully for account %s", token_obj.account)
        return True

    except Exception as exc:
        logger.exception("GitHub token refresh error: %s", exc)
        return False


@extend_schema(responses=OpenApiTypes.OBJECT)
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

    try:
        page = int(request.query_params.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = min(int(request.query_params.get("per_page", 30)), 100)
    except (TypeError, ValueError):
        per_page = 30
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

        # If we hit a 401, the token might have expired before the database knew it.
        # Force a refresh and try one more time.
        if sc == 401:
            refreshed_token = _get_github_token(request.user, force_refresh=True)
            if refreshed_token and refreshed_token != token:
                headers["Authorization"] = f"token {refreshed_token}"
                try:
                    if q:
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
                        for r in items if isinstance(r, dict)
                    ]
                    return Response({"repos": repos, "page": page, "per_page": per_page})
                except Exception:
                    # If the second try fails, fall through to the error response
                    pass

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
