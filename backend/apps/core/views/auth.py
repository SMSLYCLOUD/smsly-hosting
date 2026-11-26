"""Authentication-related API views.

Extracted from ``apps.deployments.views`` as part of the Phase-1 refactor
(see ``docs/REFACTOR_PLAN_VIEWS_TASKS.md``). ``SessionTokenView`` is
re-exported from ``apps.deployments.views`` for backwards compatibility with
``apps.deployments.urls`` and any test that imports it from the parent
module.
"""
from rest_framework import permissions, status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from ._helpers import EmptySerializer


class SessionTokenView(GenericAPIView):
    """
    Exchange an authenticated Django session for a DRF token.
    Used by the frontend callback page to avoid token-in-URL leakage.

    SECURITY: switched from GET to POST. GET responses for tokens are
    cacheable, get recorded in browser history, and any CORS
    misconfiguration leaks the token to a third-party origin. POST
    bodies are not cached, not recorded in history, and only readable
    by a correctly-configured Same-Origin request. The token is
    get_or_create (NOT rotated): rotation on every exchange would log
    out concurrent sessions (second tab, CLI, websocket terminal) that
    share the account's single DRF token.
    """
    serializer_class = EmptySerializer
    # CsrfExemptSessionAuthentication is required here: allauth's SSO login
    # flow establishes a Django session via Set-Cookie, then redirects to
    # /auth/callback which POSTs here with credentials:include. The session
    # cookie authenticates the user, but the SPA doesn't have a CSRF token
    # at this point (it was set during the allauth redirect, but the SPA
    # callback page doesn't read it). This endpoint is safe without CSRF
    # because it only exchanges a valid session for a DRF token.
    from apps.core.auth import CookieAwareTokenAuthentication, CsrfExemptSessionAuthentication
    authentication_classes = [
        CsrfExemptSessionAuthentication,
        CookieAwareTokenAuthentication,
    ]
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ['post', 'options', 'head']

    def get_throttles(self):
        from rest_framework.throttling import UserRateThrottle

        class _TokenExchangeThrottle(UserRateThrottle):
            scope = 'token_exchange'
            rate = '120/hour'

        return [_TokenExchangeThrottle()]

    def post(self, request):
        from urllib.parse import urlparse
        import hashlib
        import secrets
        from django.conf import settings
        from django.core.cache import cache

        meta = request.META
        sec_fetch_site = str(meta.get("HTTP_SEC_FETCH_SITE") or "").strip().lower()
        if sec_fetch_site and sec_fetch_site not in ("same-origin", "none"):
            return Response(
                {"error": "Cross-site request rejected"},
                status=status.HTTP_403_FORBIDDEN,
            )

        host = (request.get_host() or "").lower()
        host_hostname = (urlparse(f"//{host}").hostname or "").lower() if host else ""
        origin = str(meta.get("HTTP_ORIGIN") or "").strip()
        referer = str(meta.get("HTTP_REFERER") or "").strip()
        if origin:
            if origin.lower() == "null":
                return Response(
                    {"error": "Cross-origin request rejected"},
                    status=status.HTTP_403_FORBIDDEN,
                )
            parsed_origin = urlparse(origin)
            origin_host = (parsed_origin.hostname or "").lower()
            if (
                (parsed_origin.scheme or "").lower() not in ("http", "https")
                or not origin_host
                or not host_hostname
                or origin_host != host_hostname
            ):
                return Response(
                    {"error": "Cross-origin request rejected"},
                    status=status.HTTP_403_FORBIDDEN,
                )
        elif referer:
            parsed_ref = urlparse(referer)
            ref_host = (parsed_ref.hostname or "").lower()
            if (
                (parsed_ref.scheme or "").lower() not in ("http", "https")
                or not ref_host
                or not host_hostname
                or ref_host != host_hostname
            ):
                return Response(
                    {"error": "Cross-origin referer rejected"},
                    status=status.HTTP_403_FORBIDDEN,
                )

        user = request.user
        if not user or not user.is_authenticated:
            return Response(
                {"error": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        # 2FA ENFORCEMENT: an OAuth/SAML login establishes a Django
        # session, but the DRF token must still wait for TOTP when the
        # user enrolled a device. Log out the unverified session first so
        # it cannot access /admin/ or WebSockets before TOTP completes.
        from django.contrib.auth import logout as django_logout
        from apps.core.auth_2fa import stash_pending_2fa, user_requires_2fa
        if user_requires_2fa(user) and not request.session.get('2fa_verified'):
            django_logout(request)
            stash_pending_2fa(request, user)
            return Response({'requires_2fa': True})

        from rest_framework.authtoken.models import Token
        from apps.core.auth_cookies import set_auth_cookie
        token, _created = Token.objects.get_or_create(user=user)
        if getattr(settings, 'SMSLY_TOKEN_IN_BODY', False):
            body_token = token.key
        else:
            body_token = f"wst_{secrets.token_hex(24)}"
            ticket_digest = hashlib.sha256(body_token.encode()).hexdigest()
            cache.set(f"ws_ticket:{ticket_digest}", user.id, timeout=120)
        resp = Response({'token': body_token})
        try:
            set_auth_cookie(resp, token.key)
        except Exception:
            pass
        return resp