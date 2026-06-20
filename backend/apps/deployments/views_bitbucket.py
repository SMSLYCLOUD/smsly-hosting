"""Bitbucket repo views — repository, branch, and commit listing."""
from __future__ import annotations

import contextlib
import logging
from datetime import timedelta

import requests
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

logger = logging.getLogger(__name__)


def _get_bitbucket_token(user):
    """Retrieve the stored Bitbucket OAuth token for *user*, refreshing if expired."""
    try:
        from allauth.socialaccount.models import SocialAccount, SocialToken
        from django.utils import timezone

        account = SocialAccount.objects.filter(user=user, provider="bitbucket_oauth2").order_by("-id").first()
        if not account:
            return None
        token_obj = SocialToken.objects.filter(account=account).order_by("-id").first()
        if not token_obj:
            return None

        if token_obj.expires_at and token_obj.expires_at <= timezone.now():
            refreshed = _refresh_bitbucket_token(token_obj)
            if not refreshed:
                return None

        return token_obj.token
    except Exception:
        return None


def _refresh_bitbucket_token(token_obj):
    """Use the refresh token to obtain a new Bitbucket access token."""
    from django.utils import timezone
    refresh_token = getattr(token_obj, "token_secret", None)
    if not refresh_token:
        logger.warning("No refresh token stored for Bitbucket — user must reconnect")
        return False

    try:
        from allauth.socialaccount.models import SocialApp

        app = SocialApp.objects.filter(provider="bitbucket_oauth2").first()
        if not app:
            logger.error("No Bitbucket SocialApp configured")
            return False

        resp = requests.post(
            "https://bitbucket.org/site/oauth2/access_token",
            auth=(app.client_id, app.secret),
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )
        if not resp.ok:
            logger.warning("Bitbucket token refresh failed: %s", resp.text[:200])
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
        logger.info("Bitbucket token refreshed successfully")
        return True
    except Exception as exc:
        logger.error("Bitbucket token refresh failed: %s", exc)
        return False


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def bitbucket_repos(request):
    token = _get_bitbucket_token(request.user)
    if not token:
        return Response({"error": "Bitbucket not connected.", "repos": []}, status=status.HTTP_400_BAD_REQUEST)

    try:
        resp = requests.get(
            "https://api.bitbucket.org/2.0/repositories",
            headers={"Authorization": f"Bearer {token}"},
            params={"role": "member", "pagelen": 100},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error("Bitbucket repos fetch failed: %s", exc)
        return Response({"error": str(exc), "repos": []}, status=status.HTTP_502_BAD_GATEWAY)

    repos = []
    for r in data.get("values", []):
        repos.append({
            "full_name": r.get("full_name"),
            "name": r.get("name"),
            "private": r.get("is_private", False),
            "default_branch": r.get("mainbranch", {}).get("name", "main"),
            "html_url": r.get("links", {}).get("html", {}).get("href"),
            "clone_url": next((ln["href"] for ln in r.get("links", {}).get("clone", []) if ln.get("name") == "https"), ""),
            "description": r.get("description") or "",
            "language": r.get("language") or "",
            "updated_at": r.get("updated_on"),
        })
    return Response({"repos": repos, "page": 1, "per_page": 100})


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def bitbucket_branches(request):
    token = _get_bitbucket_token(request.user)
    if not token:
        return Response({"error": "Bitbucket not connected."}, status=status.HTTP_400_BAD_REQUEST)

    repo = request.query_params.get("repo", "")
    if not repo:
        return Response({"error": "repo parameter required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        resp = requests.get(
            f"https://api.bitbucket.org/2.0/repositories/{repo}/refs/branches",
            headers={"Authorization": f"Bearer {token}"},
            params={"pagelen": 50},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error("Bitbucket branches fetch failed: %s", exc)
        return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

    return Response([{
        "name": b.get("name"),
        "commit": b.get("target", {}).get("hash", "")[:7],
    } for b in data.get("values", [])])


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def bitbucket_commits(request):
    token = _get_bitbucket_token(request.user)
    if not token:
        return Response({"error": "Bitbucket not connected."}, status=status.HTTP_400_BAD_REQUEST)

    repo = request.query_params.get("repo", "")
    branch = request.query_params.get("branch", "main")
    if not repo:
        return Response({"error": "repo parameter required"}, status=status.HTTP_400_BAD_REQUEST)

    try:
        resp = requests.get(
            f"https://api.bitbucket.org/2.0/repositories/{repo}/commits/{branch}",
            headers={"Authorization": f"Bearer {token}"},
            params={"pagelen": 30},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.error("Bitbucket commits fetch failed: %s", exc)
        return Response({"error": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

    return Response([{
        "sha": c.get("hash"),
        "message": c.get("message", "").split("\n")[0],
        "author": c.get("author", {}).get("user", {}).get("display_name") or c.get("author", {}).get("raw"),
        "date": c.get("date"),
    } for c in data.get("values", [])])
