# pylint: disable=invalid-name
"""
Regression tests for Issue 20 (cloud_storage.templates throttle).

The ``templates`` action on the cloud-storage viewset must be
throttled per user at ``cloud_templates: 30/minute`` so a script
cannot probe the static TEMPLATES list indefinitely.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

User = get_user_model()


TEMPLATES_URL = "/api/v1/cloud-storage/templates/"


@override_settings(
    REST_FRAMEWORK={
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
        "DEFAULT_THROTTLE_RATES": {
            "cloud_templates": "3/minute",
        },
    }
)
class CloudStorageTemplatesThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        # DRF's api_settings caches values on first read. Reset
        # the cache so the @override_settings REST_FRAMEWORK dict
        # is honoured by the throttle class instantiations below.
        from rest_framework.settings import api_settings
        api_settings.reload()
        # The throttle class also caches ``THROTTLE_RATES`` as a
        # class attribute at import time. Patch it on the class
        # for the duration of the test so the override_settings
        # value is read.
        from apps.cloud.views_cloud_storage import (
            CloudStorageTemplatesRateThrottle,
        )
        CloudStorageTemplatesRateThrottle.THROTTLE_RATES = (
            api_settings.DEFAULT_THROTTLE_RATES
        )
        self.user = User.objects.create_user(
            username="cs-throttle", password="123",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def tearDown(self):
        from apps.cloud.views_cloud_storage import (
            CloudStorageTemplatesRateThrottle,
        )
        # Delete the class-level THROTTLE_RATES attr we set in setUp so that
        # CloudStorageTemplatesRateThrottle falls back through the MRO to
        # SimpleRateThrottle.THROTTLE_RATES (the production dict set at import
        # time).  Restoring self._saved_throttle_rates instead would leave a
        # class-level attr that shadows the base class and can cause
        # ImproperlyConfigured errors in tests that run after this class when
        # the random seed changes test ordering.
        try:
            del CloudStorageTemplatesRateThrottle.THROTTLE_RATES
        except AttributeError:
            pass
        from rest_framework.settings import api_settings
        api_settings.reload()

    def test_throttled_after_quota(self):
        for _ in range(3):
            resp = self.client.get(TEMPLATES_URL)
            self.assertEqual(resp.status_code, 200, resp.data)
        resp = self.client.get(TEMPLATES_URL)
        self.assertEqual(resp.status_code, 429)

    def test_throttle_is_per_user(self):
        other = User.objects.create_user(
            username="cs-throttle-2", password="123",
        )
        other_client = APIClient()
        other_client.force_authenticate(user=other)

        for _ in range(3):
            self.client.get(TEMPLATES_URL)
        self.assertEqual(self.client.get(TEMPLATES_URL).status_code, 429)
        self.assertEqual(other_client.get(TEMPLATES_URL).status_code, 200)
