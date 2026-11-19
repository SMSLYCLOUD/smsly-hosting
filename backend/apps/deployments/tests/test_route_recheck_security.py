"""Security regressions for the public route health status endpoint."""

from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITestCase

from apps.deployments.views.route import RouteRecheckView


class PublicRouteHealthStatusSecurityTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.factory = APIRequestFactory()
        self.view = RouteRecheckView.as_view()
        self.service = SimpleNamespace(id="service-123", health_status="unhealthy")

    @patch("apps.deployments.views.route._service_for_domain")
    @patch("apps.core.services.health_monitor._check_service_health")
    @patch("apps.core.services.health_monitor.reset_restart_state")
    def test_anonymous_get_does_not_reset_restart_protection(
        self, reset_restart_state, check_health, service_for_domain
    ):
        service_for_domain.return_value = self.service
        request = self.factory.get("/api/v1/system/route-recheck/?host=known.example.com")

        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["health_status"], "unhealthy")
        reset_restart_state.assert_not_called()
        check_health.assert_not_called()

    @patch("apps.deployments.views.route._service_for_domain")
    @patch("apps.core.services.health_monitor._check_service_health")
    @patch("apps.core.services.health_monitor.reset_restart_state")
    def test_anonymous_post_does_not_reset_restart_protection(
        self, reset_restart_state, check_health, service_for_domain
    ):
        service_for_domain.return_value = self.service
        request = self.factory.post(
            "/api/v1/system/route-recheck/",
            {"host": "known.example.com"},
            format="json",
        )

        response = self.view(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["health_status"], "unhealthy")
        reset_restart_state.assert_not_called()
        check_health.assert_not_called()

    @patch("apps.deployments.views.route._service_for_domain")
    def test_spoofed_xff_does_not_bypass_rate_limit(self, service_for_domain):
        service_for_domain.return_value = self.service
        req1 = self.factory.get(
            "/api/v1/system/route-recheck/?host=known.example.com",
            REMOTE_ADDR="198.51.100.10",
            HTTP_X_FORWARDED_FOR="203.0.113.1",
        )
        req2 = self.factory.get(
            "/api/v1/system/route-recheck/?host=known.example.com",
            REMOTE_ADDR="198.51.100.10",
            HTTP_X_FORWARDED_FOR="203.0.113.2",
        )

        resp1 = self.view(req1)
        resp2 = self.view(req2)

        self.assertEqual(resp1.status_code, status.HTTP_200_OK)
        self.assertEqual(resp2.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
