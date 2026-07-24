# pylint: disable=invalid-name
"""Tests for the ``ecosystem_bulk_env`` throttle (Issue 120).

The action iterates over multiple services and writes one
``EnvironmentVariable`` per (service, key) pair.  Without a
throttle, a user can DOS the platform by triggering thousands
of ORM writes.  The fix attaches a per-user ``UserRateThrottle``
(``EcosystemBulkEnvRateThrottle``) capped at 10/hour.
"""

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

User = get_user_model()


ECOSYSTEM_THROTTLE_RATES = {
    "anon": "10000/hour",
    "user": "1000000/hour",
    "ecosystem_bulk_env": "3/hour",
}


REST_FRAMEWORK_ECOSYSTEM = {
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
    "DEFAULT_THROTTLE_RATES": ECOSYSTEM_THROTTLE_RATES,
}


@override_settings(REST_FRAMEWORK=REST_FRAMEWORK_ECOSYSTEM)
class EcosystemBulkEnvThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        from rest_framework.settings import api_settings
        api_settings.reload()
        from apps.cloud.views import EcosystemBulkEnvRateThrottle
        if hasattr(EcosystemBulkEnvRateThrottle, 'THROTTLE_RATES'):
            EcosystemBulkEnvRateThrottle.THROTTLE_RATES = (
                api_settings.DEFAULT_THROTTLE_RATES
            )
        self.user = User.objects.create_user(
            username="ecosystem-throttle-user", password="123",
        )
        self.other = User.objects.create_user(
            username="ecosystem-throttle-other", password="123",
        )
        self.provider = CloudProvider.objects.create(
            name="ecosystem-throttle-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="ecosystem-throttle-svc",
            owner=self.user,
            provider=self.provider,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def tearDown(self):
        from rest_framework.settings import api_settings
        api_settings.reload()

    def _bulk_env(self):
        return self.client.post(
            "/api/v1/cloud/intelligence/ecosystem_bulk_env/",
            {
                "env_vars": {"FOO": "bar"},
                "service_ids": [str(self.service.id)],
            },
            format="json",
        )

    def test_throttle_attached_to_view(self):
        from apps.cloud.views import IntelligenceViewSet
        method = getattr(IntelligenceViewSet, 'ecosystem_bulk_env', None)
        self.assertIsNotNone(method)
        kwargs = getattr(method, 'kwargs', {}) or {}
        throttle_classes = list(kwargs.get('throttle_classes', []) or [])
        self.assertTrue(
            any(
                cls.__name__ == 'EcosystemBulkEnvRateThrottle'
                for cls in throttle_classes
            ),
            f"Throttle not attached. Got: {throttle_classes}",
        )

    def test_throttle_blocks_after_quota(self):
        for _ in range(3):
            resp = self._bulk_env()
            self.assertEqual(
                resp.status_code, 200,
                f"Expected 200, got {resp.status_code}: {resp.data}",
            )
        resp = self._bulk_env()
        self.assertEqual(resp.status_code, 429)

    def test_throttle_is_per_user(self):
        for _ in range(3):
            self._bulk_env()

        other_client = APIClient()
        other_client.force_authenticate(user=self.other)
        other_service = Service.objects.create(
            name="ecosystem-throttle-other-svc",
            owner=self.other,
            provider=self.provider,
        )
        resp = other_client.post(
            "/api/v1/cloud/intelligence/ecosystem_bulk_env/",
            {
                "env_vars": {"FOO": "bar"},
                "service_ids": [str(other_service.id)],
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
