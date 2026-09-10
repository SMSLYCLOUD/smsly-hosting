"""GitHub App Manifest flow — Railway-style one-click App creation.

The manual flow required the operator to create the GitHub App by hand on
github.com and paste the App ID, client secret, private key and webhook
secret into platform settings — four copy/paste steps that were easy to
get wrong (the webhook secret was never pasted, so push-to-deploy never
triggered).

The manifest flow instead sends the user to
``github.com/settings/apps/new?manifest=<jwt>`` with a manifest GitHub
itself validates and pre-fills. After the user clicks "Create GitHub
App", GitHub redirects to our setup_url with a one-time ``code``; we
exchange it at ``POST /app-manifests/{code}/conversions`` and receive
EVERY credential (app id, client id/secret, PEM private key, webhook
secret) in one response. Nothing is ever pasted by a human.

Endpoints:
  GET  /api/v1/integrations/github/app-manifest/url/
       -> {"url": "https://github.com/settings/apps/new?manifest=..."}
  GET  /api/v1/integrations/github/app-manifest/setup/?code=...
       -> GitHub redirect target; exchanges the code, stores all
          credentials in PlatformConfig, redirects the browser to the
          integrations page (or returns JSON for API clients).
"""
from __future__ import annotations

import logging
import secrets as pysecrets
import time

import jwt as pyjwt
import requests
from django.conf import settings
from django.shortcuts import redirect
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

logger = logging.getLogger(__name__)

_GH_API = "https://api.github.com"
# Manifest codes are single-use and short-lived; the whole flow is meant
# to complete within one browser session.
_MANIFEST_JWT_TTL = 600


def _write_github_env(app_id: str, client_id: str, client_secret: str,
                      private_key: str, webhook_secret: str) -> None:
    """Mirror GitHub App credentials into the platform .env file.

    Values that contain spaces or newlines (the PEM) are double-quoted so
    `source .env` under `set -e` keeps working. Existing keys are replaced
    in place; missing keys are appended.
    """
    import os

    env_path = os.environ.get(
        "SMSLY_ENV_FILE", "/opt/smsly-hosting/.env",
    )
    try:
        with open(env_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        lines = []

    updates = {
        "GITHUB_APP_ID": app_id,
        "GITHUB_CLIENT_ID": client_id,
        "GITHUB_CLIENT_SECRET": client_secret,
        # Escape real newlines to \n sequences AND quote: an unquoted PEM
        # breaks bash sourcing (the shell executes "RSA ..." as a command).
        "GITHUB_APP_PRIVATE_KEY": '"' + private_key.replace("\n", "\\n") + '"',
        "GITHUB_WEBHOOK_SECRET": webhook_secret,
    }
    # Never persist empty values over good ones.
    updates = {k: v for k, v in updates.items() if str(v or "").strip().strip('"')}
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates and key not in seen:
                out.append(f"{key}={updates[key]}\n")
                seen.add(key)
                continue
        out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}\n")
    with open(env_path, "w", encoding="utf-8") as fh:
        fh.writelines(out)


def _platform_base_url(request=None) -> str:
    """Resolve the platform's public base URL (scheme://host[:port])."""
    override = getattr(settings, "SITE_URL", None)
    if override:
        return override.rstrip("/")

    try:
        from apps.deployments.models.core import PlatformConfig
        cfg = PlatformConfig.objects.first()
        if cfg and getattr(cfg, "domain", ""):
            domain = cfg.domain.strip().lower().rstrip(".")
            if domain and domain not in ("localhost", "127.0.0.1", "::1"):
                import re as _re
                is_ip = bool(_re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", domain))
                scheme = "https" if (getattr(cfg, "use_ssl", False) and not is_ip) else "http"
                return f"{scheme}://{domain}"
    except Exception as exc:
        logger.debug("PlatformConfig domain resolution failed: %s", exc)

    if request is not None:
        scheme = "https" if request.is_secure() or request.headers.get("X-Forwarded-Proto") == "https" else "http"
        return f"{scheme}://{request.get_host()}"
    return ""


def _build_manifest(base_url: str, webhook_secret: str) -> dict:
    """The GitHub App manifest. Mirrors the permissions the manual setup
    required (Contents read for clones, webhook for push events, metadata
    mandatory)."""
    return {
        "name": "SMSLY Cloud",
        "url": base_url,
        "hook_attributes": {
            # The REAL routed webhook receiver (URL conf: deployments.urls
            # mounts 'webhooks/github/' under /api/v1/). An earlier
            # revision pointed at /api/v1/services/webhook/github/ which
            # does not exist — GitHub delivered every push to a 404.
            "url": f"{base_url}/api/v1/webhooks/github/",
            # GitHub generates its own secret when empty; we instead
            # SUPPLY one so the webhook receiver's HMAC verification keys
            # already match — no paste, no drift.
            "active": True,
        },
        # After the operator clicks "Create", GitHub redirects the browser
        # here with ?code=. This is a frontend page that POSTs the code
        # back to app-manifest/setup/ with the operator's API token.
        "redirect_url": f"{base_url}/auth/github/setup-callback",
        "callback_urls": [f"{base_url}/auth/github/callback"],
        "setup_url": f"{base_url}/auth/github/setup-callback",
        "description": "Deploy pushes to SMSLY Cloud with zero configuration. Every branch gets an environment.",
        "public": False,
        # Webhook events that trigger deploys.
        "default_events": ["push", "pull_request", "installation"],
        "default_permissions": {
            "contents": "read",        # clone private repos
            "metadata": "read",        # mandatory for every App
            "pull_requests": "write",  # PR comments + commit statuses
            "deployments": "write",    # GitHub Deployments API
            "statuses": "write",      # commit status reporting
        },
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def github_app_manifest_url(request) -> Response:
    """GET -> {"manifest": {...}, "post_url": "https://github.com/settings/apps/new"}.

    The frontend auto-submits the manifest as a form POST to GitHub
    (the documented manifest flow — GitHub validates the JSON schema
    itself). After the user clicks "Create GitHub App", GitHub redirects
    to our ``redirect_url`` with a one-time ``code``. Admin-only:
    creating the platform's GitHub App is an operator action.
    """
    if not request.user.is_superuser:
        return Response({"error": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)

    base_url = _platform_base_url(request)
    if not base_url:
        return Response({"error": "Platform domain is not configured."},
                        status=status.HTTP_400_BAD_REQUEST)

    # Persist the webhook secret we want GitHub to register for us. The
    # manifest can carry it directly — GitHub honors "secret" in
    # hook_attributes — so the receiver and GitHub agree from second one.
    try:
        from apps.deployments.models.core import PlatformConfig
        cfg = PlatformConfig.objects.first()
        if cfg and not (getattr(cfg, "github_webhook_secret", "") or "").strip():
            cfg.github_webhook_secret = pysecrets.token_hex(32)
            cfg.save(update_fields=["github_webhook_secret"])
    except Exception as exc:
        logger.warning("Could not pre-generate webhook secret: %s", exc)

    try:
        webhook_secret = (
            PlatformConfig.objects.values_list("github_webhook_secret", flat=True).first() or ""
        )
    except Exception:
        webhook_secret = ""

    manifest = _build_manifest(base_url, webhook_secret)
    if webhook_secret:
        manifest["hook_attributes"]["secret"] = webhook_secret

    return Response({
        "manifest": manifest,
        "post_url": "https://github.com/settings/apps/new",
        "expires_in": _MANIFEST_JWT_TTL,
    })


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def github_app_manifest_setup(request):
    """Exchange a manifest ``code`` for the full credential set.

    GitHub redirects the operator's browser to ``redirect_url`` with
    ``?code=`` after App creation; the frontend setup-callback page POSTs
    that code here with the API token. Direct GET with ``?code=`` also
    works (browser flow). Stores everything in PlatformConfig, mirrors
    .env, and ensures the allauth SocialApp — completing the flow with
    zero manual steps.

    Accepts both browser redirects (returns a redirect to the frontend
    integrations page or the install step) and API calls (returns JSON).
    """
    code = (
        request.query_params.get("code")
        or (request.data.get("code") if isinstance(request.data, dict) else "")
        or ""
    ).strip()
    if not code:
        return Response({"error": "Missing manifest code from GitHub."},
                        status=status.HTTP_400_BAD_REQUEST)

    if not request.user.is_superuser:
        return Response({"error": "Admin access required."},
                        status=status.HTTP_403_FORBIDDEN)

    try:
        resp = requests.post(
            f"{_GH_API}/app-manifests/{code}/conversions",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15,
        )
    except requests.Timeout:
        return Response({"error": "GitHub timed out exchanging the manifest code."},
                        status=status.HTTP_504_GATEWAY_TIMEOUT)
    except Exception as exc:
        logger.exception("Manifest code exchange failed: %s", exc)
        return Response({"error": "Failed to exchange manifest code."},
                        status=status.HTTP_502_BAD_GATEWAY)

    if resp.status_code != 201:
        logger.error("GitHub manifest conversion returned %s: %s",
                     resp.status_code, resp.text[:300])
        return Response({"error": f"GitHub rejected the manifest code (HTTP {resp.status_code})."},
                        status=status.HTTP_502_BAD_GATEWAY)

    data = resp.json()
    app_id = str(data.get("id") or "")
    client_id = str(data.get("client_id") or "")
    client_secret = str(data.get("client_secret") or "")
    private_key = str(data.get("pem") or "")
    webhook_secret = str(data.get("webhook_secret") or "")
    app_slug = str(data.get("slug") or "")
    app_name = str(data.get("name") or "SMSLY Cloud")

    if not (app_id and client_id and private_key):
        return Response({"error": "GitHub response was missing required credentials."},
                        status=status.HTTP_502_BAD_GATEWAY)

    from apps.deployments.models.core import PlatformConfig
    cfg = PlatformConfig.objects.first()
    if cfg is None:
        cfg = PlatformConfig.objects.create()

    cfg.github_app_id = app_id
    if client_secret:
        cfg.github_client_id = client_id
        cfg.github_client_secret = client_secret
    cfg.github_app_private_key = private_key
    # GitHub generated this secret FOR our webhook URL and told us what
    # it is — store it so the receiver's HMAC check matches immediately.
    if webhook_secret:
        cfg.github_webhook_secret = webhook_secret
    cfg.save(update_fields=[
        "github_app_id", "github_client_id",
        "github_client_secret", "github_app_private_key", "github_webhook_secret",
        "updated_at",
    ])

    logger.info(
        "GitHub App '%s' (id=%s, slug=%s) created via manifest flow — all "
        "credentials stored automatically.",
        app_name, app_id, app_slug,
    )

    # ── Close the loop: user OAuth needs an allauth SocialApp ──────────
    # Without this, `github_oauth_url` keeps answering "GitHub OAuth not
    # configured. Add a SocialApp in admin." even though the App exists
    # and PlatformConfig holds every credential. Create/update it from
    # the conversion response so "Connect GitHub Account" works
    # immediately with zero manual steps.
    try:
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site

        social_app, _ = SocialApp.objects.update_or_create(
            provider="github",
            defaults={
                "name": f"{app_name} (auto)",
                "client_id": client_id,
                "secret": client_secret,
            },
        )
        try:
            site = Site.objects.get_current()
        except Exception:
            site = Site.objects.first()
        if site is not None:
            social_app.sites.add(site)
        logger.info("SocialApp for GitHub ensured (app id=%s)", app_id)
    except Exception as exc:
        logger.warning("Could not ensure GitHub SocialApp: %s", exc)

    # ── Mirror credentials into .env (quoted) ──────────────────────────
    # Workers and fresh processes read env first; PlatformConfig is the
    # DB fallback. The private key MUST be \n-escaped and quoted or bash
    # sourcing .env executes the PEM as commands (2026-09-10 incident).
    try:
        _write_github_env(app_id, client_id, client_secret, private_key, webhook_secret)
    except Exception as exc:
        logger.warning("Could not mirror GitHub credentials to .env: %s", exc)

    wants_json = (
        request.method == "POST"
        or "application/json" in (request.headers.get("Accept") or "")
        or request.query_params.get("format") == "json"
    )
    if wants_json:
        return Response({
            "status": "created",
            "app_id": app_id,
            "app_slug": app_slug,
            "webhook_configured": bool(webhook_secret),
            "next_step": "install",
            "install_url": f"https://github.com/apps/{app_slug}/installations/new"
            if app_slug else None,
        })

    # Browser flow: send the user straight into the INSTALL step (the
    # App exists now; installing it on their org is the only thing left).
    frontend = "/dashboard/settings?tab=integrations&github_app=created"
    if app_slug:
        import urllib.parse
        state = pyjwt.encode(
            {"user_id": str(request.user.id),
             "iat": int(time.time()),
             "exp": int(time.time()) + 600},
            settings.SECRET_KEY, algorithm="HS256",
        )
        frontend = (
            f"https://github.com/apps/{app_slug}/installations/new"
            f"?{urllib.parse.urlencode({'state': state})}"
        )
    return redirect(frontend)
