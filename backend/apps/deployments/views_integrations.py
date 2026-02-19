"""User integrations (e.g., GitHub account linking for private repo access)."""

from __future__ import annotations

from urllib.parse import quote

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


def _github_connect_url() -> str:
    # allauth endpoint. `process=connect` links the social account to the
    # already-authenticated user instead of creating/logging in a new user.
    next_path = quote("/auth/callback", safe="/")
    return f"/accounts/github/login/?process=connect&next={next_path}"


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def github_connection(request):
    """
    Return GitHub integration status for the current user.

    Used by the frontend to display a "Connect GitHub" button for enabling
    private repository deploys.
    """
    try:
        from allauth.socialaccount.models import SocialAccount, SocialToken
    except Exception:
        # allauth not installed/migrated. Treat as not connected.
        return Response(
            {
                "connected": False,
                "has_token": False,
                "connect_url": _github_connect_url(),
                "account": None,
                "warning": "GitHub integration not available on this server.",
            },
            status=status.HTTP_200_OK,
        )

    account = (
        SocialAccount.objects.filter(user=request.user, provider="github")
        .order_by("-id")
        .first()
    )
    token = None
    if account:
        token = (
            SocialToken.objects.filter(account=account)
            .order_by("-id")
            .first()
        )

    extra = account.extra_data if account and isinstance(account.extra_data, dict) else {}
    login = extra.get("login") or extra.get("username") or None
    avatar_url = extra.get("avatar_url") or None

    return Response(
        {
            "connected": bool(account),
            "has_token": bool(getattr(token, "token", None)),
            "connect_url": _github_connect_url(),
            "account": (
                {
                    "uid": account.uid,
                    "login": login,
                    "avatar_url": avatar_url,
                }
                if account
                else None
            ),
        },
        status=status.HTTP_200_OK,
    )
