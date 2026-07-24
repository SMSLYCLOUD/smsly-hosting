# pylint: disable=invalid-name
"""
Tests for the hardened Caddy ``on_demand_tls`` 'ask' endpoint.

Verifies:
  * Missing or wrong ``X-Caddy-Secret`` returns 401.
  * Correct ``X-Caddy-Secret`` returns 200 for an authorized domain.
  * Per-IP throttling caps requests at 60/minute (61st returns 429).
"""

from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

FAST_THROTTLE_RATES = {
    "anon": "200/hour",
    "user": "5000/hour",
    "caddy_ask": "60/min",
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


TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "caddy-ask-security",
    }
}


@override_settings(CACHES=TEST_CACHES)
class CaddyAskSecurityTests(TestCase):
    """Endpoint hardening: secret auth + per-IP rate limit + per-apex cert cap."""

    def setUp(self):
        from django.conf import settings as dj_settings
        from django.contrib.auth import get_user_model

        from apps.deployments.models import Project, Service
        from apps.domains.models import Domain, DomainStatus

        User = get_user_model()
        self.owner = User.objects.create_user(
            username="caddy-ask-owner",
            password="x",
        )
        self.project = Project.objects.create(
            name="Caddy Ask Test",
            owner=self.owner,
        )
        self.service = Service.objects.create(
            name="caddy-ask-svc",
            owner=self.owner,
            project=self.project,
            public_domain="caddy-ask.cloud.smsly.cloud",
        )
        Domain.objects.create(
            domain_name="authorized.example.com",
            service=self.service,
            status=DomainStatus.DNS_VERIFIED,
            verified=True,
        )
        Domain.objects.create(
            domain_name="other-authorized.example.com",
            service=self.service,
            status=DomainStatus.DNS_VERIFIED,
            verified=True,
        )
        # Stash a known secret for the test session.
        self._secret_backup = dj_settings.CADDY_ASK_SECRET
        dj_settings.CADDY_ASK_SECRET = "test-caddy-secret-1234"
        self.secret = dj_settings.CADDY_ASK_SECRET

        self.client = APIClient()

    def tearDown(self):
        from django.conf import settings as dj_settings
        dj_settings.CADDY_ASK_SECRET = self._secret_backup

    def _get(self, domain, secret=None):
        headers = {}
        if secret is not None:
            headers["HTTP_X_CADDY_SECRET"] = secret
        return self.client.get(
            "/api/v1/services/check-domain/",
            {"domain": domain},
            **headers,
        )

    def test_missing_secret_returns_401(self):
        from django.core.cache import cache
        cache.clear()

        resp = self._get("authorized.example.com")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_wrong_secret_returns_401(self):
        from django.core.cache import cache
        cache.clear()

        resp = self._get("authorized.example.com", secret="not-the-right-secret")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_correct_secret_returns_200_for_authorized_domain(self):
        from django.core.cache import cache
        cache.clear()

        resp = self._get("authorized.example.com", secret=self.secret)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_correct_secret_returns_404_for_unauthorized_domain(self):
        from django.core.cache import cache
        cache.clear()

        resp = self._get("evil.example.org", secret=self.secret)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    @override_settings(REST_FRAMEWORK=REST_FRAMEWORK_FAST)
    @override_settings(CADDY_DAILY_CERT_CAP=1000)
    def test_60_requests_succeed_61st_returns_429(self):
        from django.core.cache import cache
        cache.clear()

        for i in range(60):
            resp = self._get("authorized.example.com", secret=self.secret)
            self.assertEqual(
                resp.status_code,
                status.HTTP_200_OK,
                f"Request {i+1} returned {resp.status_code} (expected 200)",
            )
        # 61st request from the same IP should be throttled.
        resp = self._get("authorized.example.com", secret=self.secret)
        self.assertEqual(resp.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn("Retry-After", resp.headers)


@override_settings(CACHES=TEST_CACHES)
class CaddyAskAdminAccessTests(TestCase):
    """Human admin access via authenticated session is also permitted."""

    def setUp(self):
        from django.conf import settings as dj_settings
        from django.contrib.auth import get_user_model

        from apps.deployments.models import Project, Service
        from apps.domains.models import Domain, DomainStatus

        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="caddy-ask-admin",
            email="admin@example.com",
            password="x",
        )
        self.project = Project.objects.create(
            name="Caddy Ask Admin Test",
            owner=self.admin,
        )
        self.service = Service.objects.create(
            name="caddy-ask-svc-admin",
            owner=self.admin,
            project=self.project,
            public_domain="admin-ask.cloud.smsly.cloud",
        )
        Domain.objects.create(
            domain_name="admin-authorized.example.com",
            service=self.service,
            status=DomainStatus.DNS_VERIFIED,
            verified=True,
        )

        # Wipe the secret so only admin auth can reach the endpoint.
        self._secret_backup = dj_settings.CADDY_ASK_SECRET
        dj_settings.CADDY_ASK_SECRET = ""

        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def tearDown(self):
        from django.conf import settings as dj_settings
        dj_settings.CADDY_ASK_SECRET = self._secret_backup

    def test_admin_user_can_access_without_secret(self):
        from django.core.cache import cache
        cache.clear()

        resp = self.client.get(
            "/api/v1/services/check-domain/",
            {"domain": "admin-authorized.example.com"},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
