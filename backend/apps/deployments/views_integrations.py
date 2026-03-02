"""User integrations (e.g., GitHub account linking for private repo access)."""

from __future__ import annotations

import logging
import secrets
from urllib.parse import quote, urlencode

import requests as http_requests
from django.conf import settings
from django.contrib.auth import login
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _github_connect_url() -> str:
    # Legacy allauth endpoint (kept for backward compat / admin panel).
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


def _get_github_app():
    """Return the allauth SocialApp for GitHub, or None."""
    try:
        from allauth.socialaccount.models import SocialApp
        return SocialApp.objects.filter(provider="github").first()
    except Exception:
        return None


# ── Views ────────────────────────────────────────────────────────────────────


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
    login_name = extra.get("login") or extra.get("username") or None
    avatar_url = extra.get("avatar_url") or None

    return Response(
        {
            "connected": bool(account),
            "has_token": bool(getattr(token, "token", None)),
            "connect_url": _github_connect_url(),
            "account": (
                {
                    "uid": account.uid,
                    "login": login_name,
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


# ── NEW: API-based GitHub OAuth (bypasses session cookies) ───────────────────


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def github_oauth_url(request):
    """
    Return the GitHub OAuth authorization URL.

    The frontend navigates the browser to this URL. After user authorization,
    GitHub redirects back to the frontend callback page with a `code` param.
    The frontend then POSTs that code to `github_oauth_callback`.

    This keeps the client_id server-side and constructs the URL properly.
    """
    app = _get_github_app()
    if not app:
        return Response(
            {"error": "GitHub OAuth not configured. Add a SocialApp in admin."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Build callback URL pointing to the FRONTEND callback page
    origin = settings.SITE_URL.rstrip("/")
    callback_url = f"{origin}/auth/github/callback"

    scopes = settings.SOCIALACCOUNT_PROVIDERS.get("github", {}).get(
        "SCOPE", ["user", "repo", "read:org"]
    )

    # Generate a random state param for CSRF protection
    state = secrets.token_urlsafe(32)
    # Store state in Django cache (valid for 10 minutes)
    from django.core.cache import cache
    cache.set(f"github_oauth_state:{state}", str(request.user.id), timeout=600)

    params = {
        "client_id": app.client_id,
        "redirect_uri": callback_url,
        "scope": " ".join(scopes),
        "response_type": "code",
        "state": state,
    }

    authorize_url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"

    return Response(
        {
            "url": authorize_url,
            "callback_url": callback_url,
        },
        status=status.HTTP_200_OK,
    )


class GitHubCallbackSerializer(serializers.Serializer):
    code = serializers.CharField(required=True, help_text="GitHub authorization code")


@extend_schema(request=GitHubCallbackSerializer, responses=OpenApiTypes.OBJECT)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def github_oauth_callback(request):
    """
    Exchange a GitHub authorization code for an access token, then link
    the GitHub account to the currently authenticated user.

    This is the SPA-compatible replacement for allauth's browser-based
    callback. No server-side sessions or cookies needed.
    """
    code = request.data.get("code")
    if not code:
        return Response(
            {"error": "Missing 'code' parameter."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    app = _get_github_app()
    if not app:
        return Response(
            {"error": "GitHub OAuth not configured."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Build the same callback URL the frontend used
    origin = settings.SITE_URL.rstrip("/")
    callback_url = f"{origin}/auth/github/callback"

    # ── Step 1: Exchange code for access token ──────────────────────────
    try:
        token_resp = http_requests.post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": app.client_id,
                "client_secret": app.secret,
                "code": code,
                "redirect_uri": callback_url,
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()
    except Exception as exc:
        logger.error("GitHub token exchange failed: %s", exc)
        return Response(
            {"error": "Failed to exchange code with GitHub."},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    access_token = token_data.get("access_token")
    if not access_token:
        error_desc = token_data.get("error_description", token_data.get("error", "unknown"))
        logger.warning("GitHub token exchange returned error: %s", error_desc)
        return Response(
            {"error": f"GitHub rejected the code: {error_desc}"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # ── Step 2: Fetch GitHub user profile ───────────────────────────────
    try:
        profile_resp = http_requests.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"token {access_token}",
                "Accept": "application/json",
            },
            timeout=10,
        )
        profile_resp.raise_for_status()
        profile = profile_resp.json()
    except Exception as exc:
        logger.error("GitHub profile fetch failed: %s", exc)
        return Response(
            {"error": "Failed to fetch GitHub profile."},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    github_uid = str(profile.get("id", ""))
    github_login = profile.get("login", "")

    if not github_uid:
        return Response(
            {"error": "GitHub profile missing user ID."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # ── Step 3: Create/update SocialAccount + SocialToken ───────────────
    try:
        from allauth.socialaccount.models import SocialAccount, SocialToken

        account, created = SocialAccount.objects.update_or_create(
            provider="github",
            uid=github_uid,
            defaults={
                "user": request.user,
                "extra_data": profile,
            },
        )

        # If account exists but belongs to another user, re-link it
        if not created and account.user_id != request.user.id:
            account.user = request.user
            account.extra_data = profile
            account.save()

        # Upsert the token — include expires_at if GitHub provides it
        from django.utils import timezone
        from datetime import timedelta

        token_defaults = {
            "token": access_token,
            "token_secret": token_data.get("refresh_token", ""),
            "app": app,
        }

        # GitHub Apps with token expiration return expires_in (default 8h = 28800s)
        expires_in = token_data.get("expires_in")
        if expires_in:
            token_defaults["expires_at"] = timezone.now() + timedelta(seconds=int(expires_in))
        else:
            # Standard OAuth tokens don't expire — set far future
            # but still set a value so refresh is attempted periodically
            token_defaults["expires_at"] = timezone.now() + timedelta(days=365)

        SocialToken.objects.update_or_create(
            account=account,
            defaults=token_defaults,
        )

        logger.info(
            "GitHub account linked: user=%s github=%s (created=%s)",
            request.user.username,
            github_login,
            created,
        )
    except Exception as exc:
        logger.error("Failed to save GitHub account: %s", exc)
        return Response(
            {"error": "Failed to save GitHub connection."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return Response(
        {
            "connected": True,
            "account": {
                "uid": github_uid,
                "login": github_login,
                "avatar_url": profile.get("avatar_url"),
            },
        },
        status=status.HTTP_200_OK,
    )
