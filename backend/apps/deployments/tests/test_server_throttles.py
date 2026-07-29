from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.deployments.models.servers import ManagedServer

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "throttle-tests",
    }
}


FAST_THROTTLE_RATES = {
    "anon": "200/hour",
    "user": "5000/hour",
    "deployments": "10/hour",
    "deployment_burst": "3/minute",
    "transfers": "5/min",
    "server_run_command": "2/minute",
    "server_run_command_burst": "2/minute",
}


REST_FRAMEWORK_FAST = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 100,
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.deployments.models.api_token.APITokenAuthentication",
        "apps.deployments.models.api_token.RemoteSyncHMACAuthentication",
        "rest_framework.authentication.TokenAuthentication",
        "apps.core.auth.CsrfExemptSessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": FAST_THROTTLE_RATES,
}


@pytest.mark.django_db(transaction=True)
@override_settings(CACHES=TEST_CACHES)
class ServerRunCommandThrottleTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="throttle_user", password="123"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="throttle-test",
            host="203.0.113.60",
            api_url="https://throttle.example.com",
            api_token="tok",
            ssh_user="root",
            ssh_password="ssh-pass",
        )
        self.url = f"/api/v1/servers/{self.server.id}/run_command/"

    def tearDown(self):
        self.server.delete()
        self.user.delete()

    def _post(self):
        with patch(
            "apps.deployments.services.self_healing_orchestrator.SelfHealingOrchestrator"
        ) as mock_cls:
            mock_orch = MagicMock()
            mock_orch._exec.return_value = ("", "", 0)
            mock_cls.return_value = mock_orch
            return self.client.post(
                self.url, {"command": "docker ps"}, format="json"
            )

    @override_settings(REST_FRAMEWORK=REST_FRAMEWORK_FAST)
    def test_three_calls_third_returns_429(self):
        from django.core.cache import cache
        cache.clear()

        first = self._post()
        second = self._post()
        third = self._post()

        self.assertIn(first.status_code, (200, 400, 403),
                      f"First call returned {first.status_code}")
        self.assertIn(second.status_code, (200, 400, 403),
                      f"Second call returned {second.status_code}")
        self.assertEqual(third.status_code, 429,
                         f"Third call expected 429, got {third.status_code}")

    @override_settings(REST_FRAMEWORK=REST_FRAMEWORK_FAST)
    def test_429_response_includes_retry_after_header(self):
        from django.core.cache import cache
        cache.clear()

        self._post()
        self._post()
        third = self._post()

        self.assertEqual(third.status_code, 429)
        self.assertIn("Retry-After", third.headers)
