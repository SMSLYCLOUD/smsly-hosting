# pylint: disable=invalid-name
"""
Regression tests for Issue 74 (addon destroy has no throttle).

Before the fix, an authenticated user could call
``DELETE /api/v1/addons/{id}/`` in an unbounded loop, each
call enqueuing an async deletion task that hits Docker and
the addon DB. After the fix, the destroy action is capped
at 30/minute per user.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service
from apps.deployments.models.addons import Addon
from apps.core.rate_limiting import AddonDeleteRateThrottle

User = get_user_model()


ADDON_THROTTLE_RATES = {
    "anon": "10000/hour",
    "user": "1000000/hour",
    "addon_delete": "3/minute",
}


REST_FRAMEWORK_ADDON = {
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
    "DEFAULT_THROTTLE_RATES": ADDON_THROTTLE_RATES,
}


@override_settings(REST_FRAMEWORK=REST_FRAMEWORK_ADDON)
class AddonDeleteThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        from rest_framework.settings import api_settings
        api_settings.reload()
        if hasattr(AddonDeleteRateThrottle, 'THROTTLE_RATES'):
            AddonDeleteRateThrottle.THROTTLE_RATES = (
                api_settings.DEFAULT_THROTTLE_RATES
            )
        self.user = User.objects.create_user(
            username='addon-throttle-user', password='123',
        )
        self.provider = CloudProvider.objects.create(
            name='addon-throttle-provider',
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name='addon-throttle-svc',
            owner=self.user,
            provider=self.provider,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def tearDown(self):
        from rest_framework.settings import api_settings
        api_settings.reload()

    def _create_addon(self, name):
        return Addon.objects.create(
            service=self.service,
            name=name,
            addon_type='POSTGRES',
            status='ACTIVE',
        )

    def _delete(self, addon):
        with patch(
            'apps.deployments.tasks.delete_addon_task.delay',
        ):
            resp = self.client.delete(f'/api/v1/addons/{addon.id}/')
        return resp

    def test_destroy_throttled_after_quota(self):
        addons = [self._create_addon(f'a{i}') for i in range(4)]
        for i, addon in enumerate(addons[:3]):
            resp = self._delete(addon)
            self.assertEqual(
                resp.status_code, 202,
                f"Addons {i} returned {resp.status_code}",
            )
        resp = self._delete(addons[3])
        self.assertEqual(resp.status_code, 429)

    def test_destroy_throttle_is_per_user(self):
        for i in range(3):
            self._delete(self._create_addon(f'first-{i}'))

        # Fourth from same user: 429.
        resp = self._delete(self._create_addon('first-extra'))
        self.assertEqual(resp.status_code, 429)

        # Different user is not affected.
        other = User.objects.create_user(
            username='addon-throttle-other', password='123',
        )
        other_service = Service.objects.create(
            name='other-svc',
            owner=other,
            provider=self.provider,
        )
        other_addon = Addon.objects.create(
            service=other_service,
            name='other-addon',
            addon_type='POSTGRES',
            status='ACTIVE',
        )
        other_client = APIClient()
        other_client.force_authenticate(user=other)
        with patch(
            'apps.deployments.tasks.delete_addon_task.delay',
        ):
            resp = other_client.delete(
                f'/api/v1/addons/{other_addon.id}/',
            )
        self.assertEqual(resp.status_code, 202)
