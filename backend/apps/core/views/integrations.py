"""User integrations (e.g., GitHub account linking for private repo access)."""

from __future__ import annotations

import logging
import re
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
def integrations_overview(request):
    """
    Return overview of all integration statuses for the settings page.

    Shows which integrations are configured (admin credentials) and
    which are connected (user OAuth tokens).
    """
    from allauth.socialaccount.models import SocialAccount, SocialToken

    # GitHub App (server-to-server)
    github_app_configured = bool(
        getattr(settings, "GITHUB_APP_ID", "")
        and getattr(settings, "GITHUB_APP_PRIVATE_KEY", "")
    )

    # GitHub OAuth (user login)
    github_oauth_app = _get_github_app()
    github_oauth_configured = bool(github_oauth_app)

    # User's GitHub connection
    github_account = SocialAccount.objects.filter(
        user=request.user, provider="github"
    ).first()
    github_connected = bool(github_account)

    # GitHub App installations
    github_installations = []
    try:
        from apps.cloud.models.github_app import GitHubAppInstallation
        for inst in GitHubAppInstallation.objects.filter(
            user=request.user,
            status=GitHubAppInstallation.Status.ACTIVE,
        ):
            github_installations.append({
                "installation_id": inst.installation_id,
                "account_login": inst.account_login,
                "account_type": inst.account_type,
                "repo_count": len(inst.repositories or []),
            })
    except Exception:
        pass

    # GitLab connection
    gitlab_app = _get_gitlab_app()
    gitlab_account = SocialAccount.objects.filter(
        user=request.user, provider="gitlab"
    ).first()
    gitlab_connected = bool(gitlab_account)

    # Bitbucket connection
    bitbucket_app = _get_bitbucket_app()
    bitbucket_account = SocialAccount.objects.filter(
        user=request.user, provider="bitbucket_oauth2"
    ).first()
    bitbucket_connected = bool(bitbucket_account)

    # Webhook secret
    webhook_secret_set = False
    try:
        from apps.deployments.models.core import PlatformConfig
        pc = PlatformConfig.load()
        webhook_secret_set = bool(pc.get_webhook_secret("github"))
    except Exception:
        webhook_secret_set = bool(getattr(settings, "GITHUB_WEBHOOK_SECRET", ""))

    return Response({
        "github_app": {
            "configured": github_app_configured,
            "app_id": getattr(settings, "GITHUB_APP_ID", "") or None,
        },
        "github_oauth": {
            "configured": github_oauth_configured,
            "client_id": github_oauth_app.client_id if github_oauth_app else None,
        },
        "github_connected": github_connected,
        "github_account": {
            "login": github_account.extra_data.get("login") if github_account else None,
            "avatar_url": github_account.extra_data.get("avatar_url") if github_account else None,
        } if github_account else None,
        "github_installations": github_installations,
        "gitlab": {
            "configured": bool(gitlab_app),
            "connected": gitlab_connected,
            "account": {
                "login": gitlab_account.extra_data.get("username") if gitlab_account else None,
                "avatar_url": gitlab_account.extra_data.get("avatar_url") if gitlab_account else None,
            } if gitlab_account else None,
        },
        "bitbucket": {
            "configured": bool(bitbucket_app),
            "connected": bitbucket_connected,
            "account": {
                "login": bitbucket_account.extra_data.get("username") if bitbucket_account else None,
                "avatar_url": (bitbucket_account.extra_data.get("links") or {}).get("avatar", {}).get("href") if bitbucket_account else None,
            } if bitbucket_account else None,
        },
        "webhook_secret_set": webhook_secret_set,
        "webhook_url": f"{request.build_absolute_uri('/').rstrip('/')}/api/v1/webhooks/github/",
    })


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


def _get_github_oauth_callback_url(request) -> str:
    """
    Resolve the GitHub OAuth callback URL pointing to the frontend SPA.
    Priority:
    1. GITHUB_OAUTH_CALLBACK_URL env var/setting
    2. Dynamic PlatformConfig domain & SSL configuration from database
    3. settings.SITE_URL
    4. request-based fallback (origin)
    """
    callback_url = getattr(settings, 'GITHUB_OAUTH_CALLBACK_URL', None)
    if callback_url:
        return callback_url

    site_url = ""
    try:
        from apps.deployments.models.core import PlatformConfig
        platform_cfg = PlatformConfig.objects.first()
        if platform_cfg and platform_cfg.domain:
            db_domain = platform_cfg.domain.strip().lower().rstrip('.')
            # Only use domain from database if it's a real custom domain (not localhost/loopback)
            if db_domain and db_domain not in ('localhost', '127.0.0.1', '::1'):
                db_is_ip = bool(re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}', db_domain))
                # Force HTTPS in production (except IP or localhost or settings.DEBUG)
                use_ssl = platform_cfg.use_ssl
                scheme = 'https' if (use_ssl and not db_is_ip and not settings.DEBUG) else 'http'
                site_url = f"{scheme}://{db_domain}"
    except Exception as e:
        logger.warning("Failed to load PlatformConfig for dynamic site_url: %s", e)

    if not site_url:
        site_url = getattr(settings, 'SITE_URL', '').rstrip('/')
        if not settings.DEBUG and site_url and not _is_ip_or_localhost(site_url):
            site_url = site_url.replace('http://', 'https://')

    if not site_url:
        # Fallback only if SITE_URL is not configured
        scheme = "https" if request.is_secure() or request.headers.get('X-Forwarded-Proto') == 'https' else "http"
        origin = f"{scheme}://{request.get_host()}"
        site_url = origin

    return f"{site_url}/auth/github/callback"


def _is_ip_or_localhost(url_str: str) -> bool:
    """Return True if the URL contains a raw IP address or localhost."""
    import ipaddress
    from urllib.parse import urlparse
    try:
        hostname = urlparse(url_str).hostname
        if not hostname:
            return False
        if hostname.lower() in ("localhost", "127.0.0.1", "::1"):
            return True
        # Strip brackets for IPv6 if present
        host_clean = hostname.strip("[]")
        ipaddress.ip_address(host_clean)
        return True
    except ValueError:
        return False


def _get_site_url(request) -> str:
    """Resolve the site URL from PlatformConfig, settings, or request."""
    site_url = ""
    try:
        from apps.deployments.models.core import PlatformConfig
        platform_cfg = PlatformConfig.objects.first()
        if platform_cfg and platform_cfg.domain:
            db_domain = platform_cfg.domain.strip().lower().rstrip('.')
            if db_domain and db_domain not in ('localhost', '127.0.0.1', '::1'):
                db_is_ip = bool(re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}', db_domain))
                use_ssl = platform_cfg.use_ssl
                scheme = 'https' if (use_ssl and not db_is_ip and not settings.DEBUG) else 'http'
                site_url = f"{scheme}://{db_domain}"
    except Exception as e:
        logger.warning("Failed to load PlatformConfig for site_url: %s", e)
    if not site_url:
        site_url = getattr(settings, 'SITE_URL', '').rstrip('/')
        if not settings.DEBUG and site_url and not _is_ip_or_localhost(site_url):
            site_url = site_url.replace('http://', 'https://')
    if not site_url:
        scheme = "https" if request.is_secure() or request.headers.get('X-Forwarded-Proto') == 'https' else "http"
        site_url = f"{scheme}://{request.get_host()}"
    return site_url


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
    callback_url = _get_github_oauth_callback_url(request)

    logger.info("GitHub OAuth authorize URL - callback_url=%s, DEBUG=%s", callback_url, settings.DEBUG)

    scopes = settings.SOCIALACCOUNT_PROVIDERS.get("github", {}).get(
        "SCOPE", ["user", "repo", "read:org"]
    )

    # Generate a random state param for CSRF protection
    state = secrets.token_urlsafe(32)
    # Store state in Django cache (valid for 10 minutes)
    from django.core.cache import cache
    cache.set(f"github_oauth_state:{state}", str(request.user.id), timeout=600)

    params = {
        "client_id": app.client_id.strip(),
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


def _verify_oauth_state(request, provider: str) -> Response | None:
    """
    Verify the ``state`` parameter on an OAuth callback.

    The corresponding ``*_oauth_url`` view stores
    ``{provider}_oauth_state:{state} → user_id`` in the cache before
    redirecting the user to the provider. The callback must echo the
    same ``state`` so we can prove the user who started the flow is
    the one completing it. Without this check, an attacker can trick
    a victim into linking the attacker's GitHub/GitLab/Bitbucket
    account to the victim's SMSLY account.

    Returns ``None`` on success, or a ``Response`` describing the
    failure (the caller should return it directly).
    """
    state = request.data.get("state")
    if not state:
        return Response(
            {"error": "Missing 'state' parameter."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    from django.core.cache import cache
    cache_key = f"{provider}_oauth_state:{state}"
    expected_user_id = cache.get(cache_key)
    if not expected_user_id:
        return Response(
            {"error": "Invalid or expired state."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    # Single-use: delete the cache entry immediately so the same
    # state cannot be replayed by an attacker.
    cache.delete(cache_key)
    if str(expected_user_id) != str(request.user.id):
        return Response(
            {"error": "State does not match the current user."},
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


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
    state_err = _verify_oauth_state(request, "github")
    if state_err is not None:
        return state_err

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
    callback_url = _get_github_oauth_callback_url(request)

    logger.info("GitHub OAuth callback exchange - callback_url=%s, DEBUG=%s", callback_url, settings.DEBUG)

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

        # SECURITY: refuse to silently re-assign an existing SocialAccount
        # to a different user. Without this guard, a GitHub user can
        # take over another tenant's SMSLY account by completing the
        # OAuth callback while signed in as the attacker — the existing
        # SocialAccount (uid=github_uid) gets reassigned to the
        # attacker's SMSLY user, and any repo/integration tied to it
        # follows.
        existing = SocialAccount.objects.filter(
            provider="github", uid=github_uid,
        ).first()
        if existing and existing.user_id != request.user.id:
            return Response(
                {
                    "error": (
                        "This OAuth account is already linked to another "
                        "user. Please contact the original owner to "
                        "release the link."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        account, created = SocialAccount.objects.update_or_create(
            provider="github",
            uid=github_uid,
            defaults={
                "user": request.user,
                "extra_data": profile,
            },
        )

        # Upsert the token — include expires_at if GitHub provides it
        from datetime import timedelta

        from django.utils import timezone

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


# ══════════════════════════════════════════════════════════════════════════════
# GitLab Integration
# ══════════════════════════════════════════════════════════════════════════════

def _get_gitlab_app():
    try:
        from allauth.socialaccount.models import SocialApp
        return SocialApp.objects.filter(provider="gitlab").first()
    except Exception:
        return None


def _get_gitlab_oauth_callback_url(request) -> str:
    override = getattr(settings, 'GITLAB_OAUTH_CALLBACK_URL', None)
    if override:
        return override
    site_url = _get_site_url(request)
    return f"{site_url}/auth/gitlab/callback"


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def gitlab_connection(request):
    try:
        from allauth.socialaccount.models import SocialAccount, SocialToken
    except Exception:
        return Response({
            "connected": False,
            "has_token": False,
            "account": None,
            "warning": "GitLab integration not available on this server.",
        })

    account = SocialAccount.objects.filter(user=request.user, provider="gitlab").order_by("-id").first()
    extra = account.extra_data if account and isinstance(account.extra_data, dict) else {}

    return Response({
        "connected": bool(account),
        "has_token": bool(SocialToken.objects.filter(account=account).exists() if account else False),
        "account": {
            "uid": account.uid,
            "login": extra.get("username"),
            "avatar_url": extra.get("avatar_url"),
        } if account else None,
    })


GITLAB_DEFAULT_URL = "https://gitlab.com"


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def gitlab_oauth_url(request):
    app = _get_gitlab_app()
    if not app:
        return Response(
            {"error": "GitLab OAuth not configured. Add a SocialApp in admin."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    callback_url = _get_gitlab_oauth_callback_url(request)
    state = secrets.token_urlsafe(32)
    from django.core.cache import cache
    cache.set(f"gitlab_oauth_state:{state}", str(request.user.id), timeout=600)

    gitlab_url = getattr(settings, "GITLAB_URL", GITLAB_DEFAULT_URL)
    params = {
        "client_id": app.client_id.strip(),
        "redirect_uri": callback_url,
        "response_type": "code",
        "state": state,
        "scope": "read_user api",
    }

    return Response({
        "url": f"{gitlab_url}/oauth/authorize?{urlencode(params)}",
        "callback_url": callback_url,
    })


class GitLabCallbackSerializer(serializers.Serializer):
    code = serializers.CharField(required=True)


@extend_schema(request=GitLabCallbackSerializer, responses=OpenApiTypes.OBJECT)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def gitlab_oauth_callback(request):
    state_err = _verify_oauth_state(request, "gitlab")
    if state_err is not None:
        return state_err

    code = request.data.get("code")
    if not code:
        return Response({"error": "Missing 'code' parameter."}, status=status.HTTP_400_BAD_REQUEST)

    app = _get_gitlab_app()
    if not app:
        return Response({"error": "GitLab OAuth not configured."}, status=status.HTTP_400_BAD_REQUEST)

    callback_url = _get_gitlab_oauth_callback_url(request)
    gitlab_url = getattr(settings, "GITLAB_URL", GITLAB_DEFAULT_URL)

    try:
        token_resp = http_requests.post(
            f"{gitlab_url}/oauth/token",
            data={
                "client_id": app.client_id,
                "client_secret": app.secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": callback_url,
            },
            timeout=15,
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()
    except Exception as exc:
        logger.error("GitLab token exchange failed: %s", exc)
        return Response({"error": "Failed to exchange code with GitLab."}, status=status.HTTP_502_BAD_GATEWAY)

    access_token = token_data.get("access_token")
    if not access_token:
        return Response({"error": "GitLab rejected the code."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        profile_resp = http_requests.get(
            f"{gitlab_url}/api/v4/user",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        profile_resp.raise_for_status()
        profile = profile_resp.json()
    except Exception as exc:
        logger.error("GitLab profile fetch failed: %s", exc)
        return Response({"error": "Failed to fetch GitLab profile."}, status=status.HTTP_502_BAD_GATEWAY)

    gitlab_uid = str(profile.get("id", ""))
    if not gitlab_uid:
        return Response({"error": "GitLab profile missing user ID."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        from allauth.socialaccount.models import SocialAccount, SocialToken
        # SECURITY: refuse to silently re-assign an existing SocialAccount
        # to a different user. See github_oauth_callback for the full
        # rationale — without this guard, completing the OAuth flow
        # while signed in as an attacker would take over the tenant
        # that originally owned the GitLab uid.
        existing = SocialAccount.objects.filter(
            provider="gitlab", uid=gitlab_uid,
        ).first()
        if existing and existing.user_id != request.user.id:
            return Response(
                {
                    "error": (
                        "This OAuth account is already linked to another "
                        "user. Please contact the original owner to "
                        "release the link."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )
        account, _created = SocialAccount.objects.update_or_create(
            provider="gitlab",
            uid=gitlab_uid,
            defaults={"user": request.user, "extra_data": profile},
        )

        token_defaults = {"token": access_token, "token_secret": token_data.get("refresh_token", ""), "app": app}
        expires_in = token_data.get("expires_in")
        if expires_in:
            from datetime import timedelta

            from django.utils import timezone
            token_defaults["expires_at"] = timezone.now() + timedelta(seconds=int(expires_in))
        else:
            from datetime import timedelta

            from django.utils import timezone
            token_defaults["expires_at"] = timezone.now() + timedelta(days=365)

        SocialToken.objects.update_or_create(account=account, defaults=token_defaults)
    except Exception as exc:
        logger.error("Failed to save GitLab account: %s", exc)
        return Response({"error": "Failed to save GitLab connection."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return Response({
        "connected": True,
        "account": {
            "uid": gitlab_uid,
            "login": profile.get("username"),
            "avatar_url": profile.get("avatar_url"),
        },
    })


# ══════════════════════════════════════════════════════════════════════════════
# Bitbucket Integration
# ══════════════════════════════════════════════════════════════════════════════

def _get_bitbucket_app():
    try:
        from allauth.socialaccount.models import SocialApp
        return SocialApp.objects.filter(provider="bitbucket_oauth2").first()
    except Exception:
        return None


def _get_bitbucket_oauth_callback_url(request) -> str:
    override = getattr(settings, 'BITBUCKET_OAUTH_CALLBACK_URL', None)
    if override:
        return override
    site_url = _get_site_url(request)
    return f"{site_url}/auth/bitbucket/callback"


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def bitbucket_connection(request):
    try:
        from allauth.socialaccount.models import SocialAccount, SocialToken
    except Exception:
        return Response({
            "connected": False,
            "has_token": False,
            "account": None,
            "warning": "Bitbucket integration not available on this server.",
        })

    account = SocialAccount.objects.filter(user=request.user, provider="bitbucket_oauth2").order_by("-id").first()
    extra = account.extra_data if account and isinstance(account.extra_data, dict) else {}

    return Response({
        "connected": bool(account),
        "has_token": bool(SocialToken.objects.filter(account=account).exists() if account else False),
        "account": {
            "uid": account.uid,
            "login": extra.get("username") or extra.get("display_name"),
            "avatar_url": extra.get("links", {}).get("avatar", {}).get("href"),
        } if account else None,
    })


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def bitbucket_oauth_url(request):
    app = _get_bitbucket_app()
    if not app:
        return Response(
            {"error": "Bitbucket OAuth not configured. Add a SocialApp in admin."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    callback_url = _get_bitbucket_oauth_callback_url(request)
    state = secrets.token_urlsafe(32)
    from django.core.cache import cache
    cache.set(f"bitbucket_oauth_state:{state}", str(request.user.id), timeout=600)

    params = {
        "client_id": app.client_id.strip(),
        "redirect_uri": callback_url,
        "response_type": "code",
        "state": state,
        "scope": "account repository",
    }
    authorize_url = f"https://bitbucket.org/site/oauth2/authorize?{urlencode(params)}"

    return Response({
        "url": authorize_url,
        "callback_url": callback_url,
    })


class BitbucketCallbackSerializer(serializers.Serializer):
    code = serializers.CharField(required=True)


@extend_schema(request=BitbucketCallbackSerializer, responses=OpenApiTypes.OBJECT)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def bitbucket_oauth_callback(request):
    state_err = _verify_oauth_state(request, "bitbucket")
    if state_err is not None:
        return state_err

    code = request.data.get("code")
    if not code:
        return Response({"error": "Missing 'code' parameter."}, status=status.HTTP_400_BAD_REQUEST)

    app = _get_bitbucket_app()
    if not app:
        return Response({"error": "Bitbucket OAuth not configured."}, status=status.HTTP_400_BAD_REQUEST)

    callback_url = _get_bitbucket_oauth_callback_url(request)

    try:
        token_resp = http_requests.post(
            "https://bitbucket.org/site/oauth2/access_token",
            data={
                "client_id": app.client_id,
                "client_secret": app.secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": callback_url,
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )
        token_resp.raise_for_status()
        token_data = token_resp.json()
    except Exception as exc:
        logger.error("Bitbucket token exchange failed: %s", exc)
        return Response({"error": "Failed to exchange code with Bitbucket."}, status=status.HTTP_502_BAD_GATEWAY)

    access_token = token_data.get("access_token")
    if not access_token:
        return Response({"error": "Bitbucket rejected the code."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        profile_resp = http_requests.get(
            "https://api.bitbucket.org/2.0/user",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        profile_resp.raise_for_status()
        profile = profile_resp.json()
    except Exception as exc:
        logger.error("Bitbucket profile fetch failed: %s", exc)
        return Response({"error": "Failed to fetch Bitbucket profile."}, status=status.HTTP_502_BAD_GATEWAY)

    bb_uid = str(profile.get("account_id") or profile.get("uuid", ""))
    if not bb_uid:
        return Response({"error": "Bitbucket profile missing user ID."}, status=status.HTTP_400_BAD_REQUEST)

    try:
        from allauth.socialaccount.models import SocialAccount, SocialToken
        # SECURITY: refuse to silently re-assign an existing SocialAccount
        # to a different user. See github_oauth_callback for the full
        # rationale — without this guard, completing the OAuth flow
        # while signed in as an attacker would take over the tenant
        # that originally owned the Bitbucket uid.
        existing = SocialAccount.objects.filter(
            provider="bitbucket_oauth2", uid=bb_uid,
        ).first()
        if existing and existing.user_id != request.user.id:
            return Response(
                {
                    "error": (
                        "This OAuth account is already linked to another "
                        "user. Please contact the original owner to "
                        "release the link."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )
        account, _created = SocialAccount.objects.update_or_create(
            provider="bitbucket_oauth2",
            uid=bb_uid,
            defaults={"user": request.user, "extra_data": profile},
        )

        from datetime import timedelta
        from django.utils import timezone

        expires_in = int(token_data.get("expires_in", 3600))
        token_defaults = {
            "token": access_token,
            "token_secret": token_data.get("refresh_token", ""),
            "app": app,
            "expires_at": timezone.now() + timedelta(seconds=expires_in),
        }
        SocialToken.objects.update_or_create(account=account, defaults=token_defaults)
    except Exception as exc:
        logger.error("Failed to save Bitbucket account: %s", exc)
        return Response({"error": "Failed to save Bitbucket connection."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return Response({
        "connected": True,
        "account": {
            "uid": bb_uid,
            "login": profile.get("username") or profile.get("display_name"),
            "avatar_url": profile.get("links", {}).get("avatar", {}).get("href"),
        },
    })
