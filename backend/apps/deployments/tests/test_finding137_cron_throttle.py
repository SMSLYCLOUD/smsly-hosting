# pylint: disable=invalid-name
"""Tests for the ``CronJobViewSet`` create throttle (Issue 137).

The viewset previously had no throttle — a user could spam cron
job creation. The fix attaches a per-user
``CronJobCreateRateThrottle`` backed by
``settings.DEFAULT_THROTTLE_RATES['cron_jobs_create']``.
"""
import inspect

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service
from apps.deployments.models.cron import CronJob
from apps.core.rate_limiting import CronJobCreateRateThrottle
from apps.deployments.views.cron import CronJobViewSet

User = get_user_model()


TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "cron-throttle-test",
    }
}


FAST_REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "cron_jobs_create": "2/minute",
    },
}


@override_settings(CACHES=TEST_CACHES)
class CronJobCreateThrottleWiringTests(TestCase):
    def test_cron_create_throttle_class_declared(self):
        source = inspect.getsource(CronJobViewSet.get_throttles)
        self.assertIn("CronJobCreateRateThrottle", source)

    def test_cron_create_throttle_scope_correct(self):
        self.assertEqual(CronJobCreateRateThrottle.scope, "cron_jobs_create")

    def test_cron_create_rate_configured_in_settings(self):
        from django.conf import settings as dj_settings
        rates = dj_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        self.assertIn("cron_jobs_create", rates)
        self.assertEqual(rates["cron_jobs_create"], "10/hour")

    def test_get_throttles_returns_throttle_for_create_action(self):
        source = inspect.getsource(CronJobViewSet.get_throttles)
        self.assertIn("self.action == 'create'", source)
        self.assertIn("CronJobCreateRateThrottle()", source)


@override_settings(
    CACHES=TEST_CACHES,
    REST_FRAMEWORK=FAST_REST_FRAMEWORK,
)
class CronJobCreateThrottleBehaviorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="cron-throttle-behav", password="x",
        )
        self.provider = CloudProvider.objects.create(
            name="cron-throttle-behav-prov",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="cron-throttle-behav-svc",
            owner=self.user,
            provider=self.provider,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = f"/api/v1/services/{self.service.id}/cron/"

    def test_first_request_succeeds_under_throttle(self):
        from rest_framework import status
        resp = self.client.post(
            self.url,
            data={"name": "j1", "schedule": "*/5 * * * *", "command": "echo"},
            format="json",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(CronJob.objects.count(), 1)

    def test_throttle_class_attached_to_viewset(self):
        from rest_framework.test import APIRequestFactory
        factory = APIRequestFactory()
        request = factory.post(
            f"/api/v1/services/{self.service.id}/cron/",
            {"name": "j1", "schedule": "*/5 * * * *", "command": "echo"},
            format="json",
        )
        request.user = self.user
        from rest_framework.request import Request
        drf_request = Request(request)
        view = CronJobViewSet()
        view.action = "create"
        view.request = drf_request
        view.kwargs = {"service_pk": self.service.id}
        throttles = view.get_throttles()
        self.assertEqual(len(throttles), 1)
        self.assertIsInstance(throttles[0], CronJobCreateRateThrottle)
        self.assertEqual(throttles[0].scope, "cron_jobs_create")
