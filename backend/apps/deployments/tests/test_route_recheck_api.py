# pylint: disable=invalid-name
"""Tests for public route recheck endpoint."""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service


class RouteRecheckApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="recheck-user",
            email="recheck-user@example.com",
            password="password123",
        )
        self.provider = CloudProvider.objects.create(
            name="route-recheck-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="route-recheck-service",
            owner=self.user,
            provider=self.provider,
            public_domain="route-recheck-service-aaaaaa.cloud.smsly.cloud",
            custom_domains=["recheck.example.com"],
        )
        self.url = "/api/v1/system/route-recheck/"

    @patch("apps.deployments.views.RouteRecheckView._trigger_recheck", return_value=(True, "healthy"))
    def test_public_domain_recheck(self, trigger_mock):
        response = self.client.get(
            self.url,
            {"host": self.service.public_domain},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("health_status"), "healthy")
        trigger_mock.assert_called_once()

    @patch("apps.deployments.views.RouteRecheckView._trigger_recheck", return_value=(True, "starting"))
    def test_custom_domain_recheck(self, trigger_mock):
        response = self.client.post(
            self.url,
            {"host": "recheck.example.com"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get("health_status"), "starting")
        trigger_mock.assert_called_once()

    def test_unknown_domain_returns_404(self):
        response = self.client.get(
            self.url,
            {"host": "missing.example.com"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch("apps.deployments.views.RouteRecheckView._trigger_recheck", return_value=(True, "healthy"))
    def test_recheck_is_rate_limited_per_client(self, _trigger_mock):
        first = self.client.get(
            self.url,
            {"host": self.service.public_domain},
            format="json",
        )
        second = self.client.get(
            self.url,
            {"host": self.service.public_domain},
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
