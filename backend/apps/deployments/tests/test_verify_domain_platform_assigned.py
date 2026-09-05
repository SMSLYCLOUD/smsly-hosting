# pylint: disable=invalid-name
"""Tests for platform-assigned domain short-circuit in verify-domain.

Grid-issued hostnames (service public_domain / staging_domain) are owned
by the platform itself, so DNS quorum verification is skipped for them.
This prevents ecosystem deploys from flapping on DNS propagation lag or
Cloudflare-proxied records, and from burning the shared apex cert cap.
"""
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient


TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "verify-platform-assigned-tests",
    }
}

FAST_THROTTLE_RATES = {
    "anon": "1000/hour",
    "user": "10000/hour",
    "caddy_ask": "1000/min",
}

REST_FRAMEWORK_LOOSE = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 100,
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.deployments.models.api_token.APITokenAuthentication",
        "apps.deployments.models.api_token.RemoteSyncHMACAuthentication",
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
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


@override_settings(CACHES=TEST_CACHES)
@override_settings(REST_FRAMEWORK=REST_FRAMEWORK_LOOSE)
class VerifyPlatformAssignedTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model

        from apps.deployments.models import Project, Service

        User = get_user_model()
        self.user = User.objects.create_user(
            username="verify-owner", password="x"
        )
        self.project = Project.objects.create(
            name="Verify Proj", owner=self.user,
        )
        self.service = Service.objects.create(
            name="verify-svc",
            owner=self.user,
            project=self.project,
            public_domain="verify-svc-abc123.grid.smsly.cloud",
            staging_domain="staging-verify-svc.grid.smsly.cloud",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _verify_url(self):
        return f"/api/v1/services/{self.service.id}/verify-domain/"

    def test_own_public_domain_skips_dns_quorum(self):
        with patch(
            "apps.domains.verification.verify_custom_domain_dns"
        ) as mock_verify:
            response = self.client.post(
                self._verify_url(),
                {"domain": "verify-svc-abc123.grid.smsly.cloud"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["verified"])
        mock_verify.assert_not_called()
        self.service.refresh_from_db()
        self.assertTrue(self.service.domain_verified)

    def test_own_staging_domain_sets_staging_flag(self):
        with patch(
            "apps.domains.verification.verify_custom_domain_dns"
        ) as mock_verify:
            response = self.client.post(
                self._verify_url(),
                {"domain": "staging-verify-svc.grid.smsly.cloud"},
                format="json",
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["verified"])
        mock_verify.assert_not_called()
        self.service.refresh_from_db()
        self.assertTrue(self.service.staging_domain_verified)
        self.assertFalse(self.service.domain_verified)

    def test_custom_domain_still_uses_dns_quorum(self):
        with patch(
            "apps.domains.verification.verify_custom_domain_dns"
        ) as mock_verify:
            mock_verify.return_value = MagicMock(
                verified=False,
                expected="verify-svc-abc123.grid.smsly.cloud",
                actual="No CNAME, A, or AAAA records found",
                error="DNS not configured",
            )
            response = self.client.post(
                self._verify_url(),
                {"domain": "custom.example.com"},
                format="json",
            )
        mock_verify.assert_called_once()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["verified"])

    def test_per_fqdn_cap_does_not_block_siblings(self):
        from django.core.cache import cache
        cache.clear()
        with patch(
            "apps.domains.verification.verify_custom_domain_dns"
        ) as mock_verify:
            mock_verify.return_value = MagicMock(
                verified=False,
                expected="x",
                actual="y",
                error="DNS not configured",
            )
            for _ in range(20):
                resp = self.client.post(
                    self._verify_url(),
                    {"domain": "busy.example.com"},
                    format="json",
                )
                self.assertEqual(resp.status_code, status.HTTP_200_OK)
            resp = self.client.post(
                self._verify_url(),
                {"domain": "busy.example.com"},
                format="json",
            )
            self.assertEqual(
                resp.status_code, status.HTTP_429_TOO_MANY_REQUESTS
            )
            # A different FQDN under another apex is unaffected.
            resp = self.client.post(
                self._verify_url(),
                {"domain": "fresh.other-apex.example"},
                format="json",
            )
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
