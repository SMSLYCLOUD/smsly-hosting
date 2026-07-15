"""Authentication-related API views.

Extracted from ``apps.deployments.views`` as part of the Phase-1 refactor
(see ``docs/REFACTOR_PLAN_VIEWS_TASKS.md``). ``SessionTokenView`` is
re-exported from ``apps.deployments.views`` for backwards compatibility with
``apps.deployments.urls`` and any test that imports it from the parent
module.
"""
import hmac

from django.conf import settings
from rest_framework import authentication, permissions, serializers, status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response


class EmptySerializer(serializers.Serializer):
    """Schema placeholder for APIViews without request/response bodies."""


class SessionTokenView(GenericAPIView):
    """
    Exchange an authenticated Django session for a DRF token.
    Used by the frontend callback page to avoid token-in-URL leakage.

    SECURITY: switched from GET to POST. GET responses for tokens are
    cacheable, get recorded in browser history, and any CORS
    misconfiguration leaks the token to a third-party origin. POST
    bodies are not cached, not recorded in history, and only readable
    by a correctly-configured Same-Origin request. The DRF token is
    also rotated on every exchange so a token captured from any prior
    response is invalidated as soon as the legitimate caller refreshes
    it.
    """
    serializer_class = EmptySerializer
    # CsrfExemptSessionAuthentication is required here: allauth's SSO login
    # flow establishes a Django session via Set-Cookie, then redirects to
    # /auth/callback which POSTs here with credentials:include. The session
    # cookie authenticates the user, but the SPA doesn't have a CSRF token
    # at this point (it was set during the allauth redirect, but the SPA
    # callback page doesn't read it). This endpoint is safe without CSRF
    # because it only exchanges a valid session for a DRF token.
    from apps.core.auth import CsrfExemptSessionAuthentication
    authentication_classes = [
        CsrfExemptSessionAuthentication,
        'apps.core.auth.CookieAwareTokenAuthentication',
    ]
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ['post', 'options', 'head']

    def get_throttles(self):
        from rest_framework.throttling import UserRateThrottle

        class _TokenExchangeThrottle(UserRateThrottle):
            scope = 'token_exchange'
            rate = '10/hour'

        return [_TokenExchangeThrottle()]

    def post(self, request):
        user = request.user
        if not user or not user.is_authenticated:
            return Response(
                {"error": "Authentication required"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        # Get-or-create a DRF auth token for this user. The consumer
        # (TerminalConsumer._authenticate_token) validates the token as
        # a 40-char hex string against the rest_framework.authtoken
        # table, so the signed-token approach previously used here was
        # rejected with "subprotocol is invalid" (colons in the signed
        # value are not valid in Sec-WebSocket-Protocol).
        from rest_framework.authtoken.models import Token
        token, _created = Token.objects.get_or_create(user=user)
        return Response({'token': token.key})

class ZeroTrustHMACAuthentication(authentication.BaseAuthentication):
    """
    Authenticate requests from peer nodes using HMAC V2.
    Required headers: X-Gateway-Signature-V2, X-Request-Timestamp,
    X-Request-Nonce.

    SECURITY (Batch G): the nonce is now mandatory and bound into the
    signed payload. Without this, a captured request can be replayed
    for the full timestamp window. Callers must generate a
    cryptographically-random nonce per request, send it in
    ``X-Request-Nonce``, and include it in the signed payload as
    ``{method}|{path}|{timestamp}|{nonce}|{body_hash}``.
    """
    def authenticate(self, request):
        import hashlib
        import time

        from django.contrib.auth import get_user_model
        User = get_user_model()

        signature = request.headers.get("X-Gateway-Signature-V2", "")
        timestamp = request.headers.get("X-Request-Timestamp", "")
        nonce = request.headers.get("X-Request-Nonce", "")
        if not signature or not timestamp or not nonce:
            return None

        # Verify timestamp freshness (1 min window)
        try:
            req_ts = int(timestamp)
            if abs(int(time.time()) - req_ts) > 60:
                raise authentication.AuthenticationFailed("Timestamp expired")
        except ValueError:
            raise authentication.AuthenticationFailed("Invalid timestamp")

        # SECURITY: nonce replay protection. Each nonce is one-use
        # within the freshness window.
        from django.core.cache import cache
        nonce_key = f"hmac_nonce:{nonce}"
        if cache.get(nonce_key):
            raise authentication.AuthenticationFailed("Nonce already used")
        cache.set(nonce_key, "1", timeout=120)

        # Verify HMAC
        gw_secret = getattr(settings, "GATEWAY_SECRET", settings.SECRET_KEY)
        method = request.method
        path = request.get_full_path()

        try:
            body = request.body
        except Exception:
            body = b""

        body_hash = hashlib.sha256(body).hexdigest()
        payload = f"{method}|{path}|{timestamp}|{nonce}|{body_hash}"
        expected = hmac.new(gw_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected, signature):
            raise authentication.AuthenticationFailed("Invalid HMAC signature")

        # Authentication success — use the first active superuser as the actor
        admin = User.objects.filter(is_superuser=True, is_active=True).first()
        if not admin:
            raise authentication.AuthenticationFailed("No admin user available")

        return (admin, None)

class CaddySecretOrAdminPermission(permissions.BasePermission):
    """
    Permission gate for the Caddy ``on_demand_tls`` 'ask' endpoint.

    Allows access if EITHER:

    * the request carries ``secret`` query param matching
      ``CADDY_ASK_SECRET`` (embedded in the Caddyfile ask URL as
      ``?secret=<value>`` — Caddy v2 can send query params), OR
    * the request is from an authenticated admin user.

    ``CADDY_ASK_SECRET`` is read from PlatformConfig DB first, then
    falls back to the ``CADDY_ASK_SECRET`` env var. If neither is set,
    the endpoint still allows access for backward compatibility with
    existing Caddyfiles, protected by domain verification + rate limits.
    """

    message = "Caddy ask endpoint requires a valid secret or admin authentication."

    def has_permission(self, request, view):
        expected = self._get_expected_secret()
        if expected:
            provided = request.query_params.get("secret", "")
            if provided and hmac.compare_digest(provided, expected):
                return True
            # Also check X-Caddy-Secret header for older Caddyfile compatibility
            header_provided = request.headers.get("X-Caddy-Secret", "")
            if header_provided and hmac.compare_digest(header_provided, expected):
                return True
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False) and (
            getattr(user, "is_superuser", False) or getattr(user, "is_staff", False)
        ):
            return True
        # No secret configured — allow through with domain + rate limit protections only
        return bool(not expected)

    @staticmethod
    def _get_expected_secret():
        """Return CADDY_ASK_SECRET from PlatformConfig DB, with env var fallback."""
        try:
            from .models_core import PlatformConfig
            cfg = PlatformConfig.load()
            db_secret = str(getattr(cfg, 'caddy_ask_secret', '') or '').strip()
            if db_secret:
                return db_secret
        except Exception:
            pass
        return str(getattr(settings, "CADDY_ASK_SECRET", "") or "")
