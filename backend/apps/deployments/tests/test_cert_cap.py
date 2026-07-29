# pylint: disable=invalid-name
"""
Tests for the per-apex daily cert-issuance cap on the Caddy ask endpoint.

Verifies:
  * The first 20 successful lookups for a given apex are allowed.
  * The 21st lookup for the same apex is rejected with HTTP 429.
  * The cap is keyed per-apex: a different apex is independent.
"""

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

FAST_THROTTLE_RATES = {
    "anon": "200/hour",
    "user": "5000/hour",
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


TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "cert-cap-tests",
    }
}


@override_settings(CACHES=TEST_CACHES)
@override_settings(REST_FRAMEWORK=REST_FRAMEWORK_LOOSE)
class PerApexCertCapTests(TestCase):
    """Per-apex daily issuance cap (CADDY_DAILY_CERT_CAP, default 20)."""

    def setUp(self):
        from django.conf import settings as dj_settings
        from django.contrib.auth import get_user_model

        from apps.deployments.models import Project, Service
        from apps.domains.models import Domain, DomainStatus

        User = get_user_model()
        self.owner = User.objects.create_user(
            username="cert-cap-owner", password="x"
        )
        self.project = Project.objects.create(
            name="Cert Cap Test", owner=self.owner,
        )
        self.service = Service.objects.create(
            name="cert-cap-svc",
            owner=self.owner,
            project=self.project,
            public_domain="certcap.cloud.smsly.cloud",
        )
        for i in range(25):
            Domain.objects.create(
                domain_name=f"host{i}.example.com",
                service=self.service,
                status=DomainStatus.DNS_VERIFIED,
                verified=True,
            )
        # Authorize one alternate apex.
        Domain.objects.create(
            domain_name="authorized.other-apex.com",
            service=self.service,
            status=DomainStatus.DNS_VERIFIED,
            verified=True,
        )

        self._secret_backup = dj_settings.CADDY_ASK_SECRET
        dj_settings.CADDY_ASK_SECRET = "cap-test-secret"

        self._cap_backup = getattr(dj_settings, "CADDY_DAILY_CERT_CAP", None)
        dj_settings.CADDY_DAILY_CERT_CAP = 20

        self.client = APIClient()

    def tearDown(self):
        from django.conf import settings as dj_settings
        dj_settings.CADDY_ASK_SECRET = self._secret_backup
        if self._cap_backup is not None:
            dj_settings.CADDY_DAILY_CERT_CAP = self._cap_backup

    def _get(self, domain):
        return self.client.get(
            "/api/v1/services/check-domain/",
            {"domain": domain},
            HTTP_X_CADDY_SECRET="cap-test-secret",
        )

    def test_20_succeed_21st_returns_429_for_same_apex(self):
        from django.core.cache import cache
        cache.clear()

        for i in range(20):
            resp = self._get(f"host{i}.example.com")
            self.assertEqual(
                resp.status_code,
                status.HTTP_200_OK,
                f"Request {i+1} returned {resp.status_code} (expected 200)",
            )

        # 21st host under the same apex must be capped.
        resp = self._get("host20.example.com")
        self.assertEqual(resp.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn("cap", (resp.data.get("error") or "").lower())

    def test_different_apexes_are_independent(self):
        from django.core.cache import cache
        cache.clear()

        # Burn through the cap on the first apex.
        for i in range(20):
            resp = self._get(f"host{i}.example.com")
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
        resp = self._get("host20.example.com")
        self.assertEqual(resp.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

        # A different apex still works because the counter is per-apex.
        resp = self._get("authorized.other-apex.com")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
