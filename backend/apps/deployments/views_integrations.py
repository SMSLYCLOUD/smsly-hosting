"""User integrations (e.g., GitHub account linking for private repo access)."""

from __future__ import annotations

from urllib.parse import quote

from django.conf import settings
from django.contrib.auth import login
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


def _login_backend_path(user) -> str:
    """
    Resolve an auth backend path for django.contrib.auth.login().
    Token-authenticated users often don't have user.backend populated.
    """
    backend = getattr(user, "backend", None)
    if backend:
        return backend
    backends = list(getattr(settings, "AUTHENTICATION_BACKENDS", []) or [])
    if backends:
        return backends[0]
    return "django.contrib.auth.backends.ModelBackend"


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


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def github_connect(request):
    """
    Bootstrap a Django session for the currently authenticated API user,
    then return the allauth GitHub connect URL.

    Why this exists:
    - The frontend commonly authenticates with DRF token auth.
    - allauth "process=connect" requires a Django session user.
    """
    login(request, request.user, backend=_login_backend_path(request.user))
    request.session.modified = True
    request.session.save()

    return Response(
        {
            "connect_url": _github_connect_url(),
            "session_bootstrapped": True,
        },
        status=status.HTTP_200_OK,
    )
