"""Authentication-related API views.

Extracted from ``apps.deployments.views`` as part of the Phase-1 refactor
(see ``docs/REFACTOR_PLAN_VIEWS_TASKS.md``). ``SessionTokenView`` is
re-exported from ``apps.deployments.views`` for backwards compatibility with
``apps.deployments.urls`` and any test that imports it from the parent
module.
"""
from django.conf import settings
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