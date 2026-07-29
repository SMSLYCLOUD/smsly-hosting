# pylint: disable=invalid-name
"""
Regression tests for Issue 71 (create_token has no throttle).

Before the fix, an authenticated user could call
``POST /api/v1/tokens/create/`` in an unbounded loop. After
the fix, the endpoint is capped at 10/hour per user.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.core.rate_limiting import TokenCreateRateThrottle

User = get_user_model()


TOKENS_THROTTLE_RATES = {
    "anon": "10000/hour",
    "user": "1000000/hour",
    "token_create": "3/minute",
}


REST_FRAMEWORK_TOKEN = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.deployments.models.api_token.APITokenAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": TOKENS_THROTTLE_RATES,
}


@override_settings(REST_FRAMEWORK=REST_FRAMEWORK_TOKEN)
class TokenCreateThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        from rest_framework.settings import api_settings
        api_settings.reload()
        # Throttle classes cache the rates dict on first
        # instantiation. Reset it so the override above wins.
        if hasattr(TokenCreateRateThrottle, 'THROTTLE_RATES'):
            TokenCreateRateThrottle.THROTTLE_RATES = (
                api_settings.DEFAULT_THROTTLE_RATES
            )
        self.user = User.objects.create_user(
            username='token-throttle-user', password='123',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = '/api/v1/tokens/create/'

    def tearDown(self):
        from rest_framework.settings import api_settings
        api_settings.reload()

    def _post(self):
        return self.client.post(
            self.url, {'name': 'cli'}, format='json',
        )

    def test_throttled_after_quota(self):
        for _ in range(3):
            resp = self._post()
            self.assertEqual(resp.status_code, 201, resp.data)
        resp = self._post()
        self.assertEqual(resp.status_code, 429)

    def test_throttle_is_per_user(self):
        for _ in range(3):
            self._post()
        self.assertEqual(self._post().status_code, 429)

        other = User.objects.create_user(
            username='token-throttle-other', password='123',
        )
        other_client = APIClient()
        other_client.force_authenticate(user=other)
        for _ in range(3):
            resp = other_client.post(
                self.url, {'name': 'cli'}, format='json',
            )
            self.assertEqual(resp.status_code, 201, resp.data)
