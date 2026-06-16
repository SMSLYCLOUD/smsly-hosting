"""Authentication-related API views.

Extracted from ``apps.deployments.views`` as part of the Phase-1 refactor
(see ``docs/REFACTOR_PLAN_VIEWS_TASKS.md``). ``SessionTokenView`` is
re-exported from ``apps.deployments.views`` for backwards compatibility with
``apps.deployments.urls`` and any test that imports it from the parent
module.
"""
from rest_framework import permissions, serializers
from rest_framework.authtoken.models import Token
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework import status


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
        Token.objects.filter(user=user).delete()
        new_token = Token.objects.create(user=user)
        return Response({'token': new_token.key})
