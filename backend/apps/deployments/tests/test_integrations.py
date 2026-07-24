# pylint: disable=invalid-name
"""Integration API tests (GitHub connect bootstrap flow and OAuth url generation)."""

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

try:
    from allauth.socialaccount.models import SocialApp
except ImportError:
    SocialApp = None

User = get_user_model()


class GitHubConnectBootstrapTests(TestCase):
    """Ensure GitHub connect flow works for token-authenticated users."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="gh-user",
            email="gh-user@example.com",
            password="pass12345",
        )
        self.client = APIClient()

    def test_connect_endpoint_bootstraps_session(self):
        self.client.force_authenticate(user=self.user)

        resp = self.client.get("/api/v1/integrations/github/connect/")

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data.get("session_bootstrapped"))
        self.assertIn("/accounts/github/login/", resp.data.get("connect_url", ""))
        self.assertIn("process=connect", resp.data.get("connect_url", ""))
        self.assertIn("next=/auth/callback", resp.data.get("connect_url", ""))
        self.assertEqual(self.client.session.get("_auth_user_id"), str(self.user.pk))

    def test_connect_endpoint_requires_auth(self):
        client = APIClient()
        resp = client.get("/api/v1/integrations/github/connect/")
        self.assertIn(resp.status_code, [401, 403])


class GitHubOAuthUrlTests(TestCase):
    """Test generating GitHub OAuth authorization URL."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="oauth-user",
            email="oauth-user@example.com",
            password="pass12345",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        # Create a GitHub SocialApp if allauth is available
        if SocialApp is not None:
            site = Site.objects.get_current()
            self.app, _ = SocialApp.objects.get_or_create(
                provider="github",
                defaults={
                    "name": "GitHub",
                    "client_id": "test_client_id",
                    "secret": "test_secret",
                }
            )
            self.app.sites.add(site)

    @override_settings(DEBUG=False, SITE_URL="http://209.159.152.123")
    def test_github_oauth_url_ip_keeps_http(self):
        if SocialApp is None:
            self.skipTest("allauth not installed/available")
        resp = self.client.get("/api/v1/integrations/github/oauth-url/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("redirect_uri=http%3A%2F%2F209.159.152.123%2Fauth%2Fgithub%2Fcallback", resp.data.get("url", ""))

    @override_settings(DEBUG=False, SITE_URL="http://grid.smsly.cloud")
    def test_github_oauth_url_domain_forces_https(self):
        if SocialApp is None:
            self.skipTest("allauth not installed/available")
        resp = self.client.get("/api/v1/integrations/github/oauth-url/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("redirect_uri=https%3A%2F%2Fgrid.smsly.cloud%2Fauth%2Fgithub%2Fcallback", resp.data.get("url", ""))

    @override_settings(DEBUG=False, SITE_URL="http://localhost:3000")
    def test_github_oauth_url_localhost_keeps_http(self):
        if SocialApp is None:
            self.skipTest("allauth not installed/available")
        resp = self.client.get("/api/v1/integrations/github/oauth-url/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("redirect_uri=http%3A%2F%2Flocalhost%3A3000%2Fauth%2Fgithub%2Fcallback", resp.data.get("url", ""))

    def test_github_oauth_url_uses_platform_config_dynamically(self):
        if SocialApp is None:
            self.skipTest("allauth not installed/available")

        # Create a PlatformConfig in the database with a custom domain
        from apps.deployments.models.core import PlatformConfig
        PlatformConfig.objects.create(
            pk=1,
            domain="my-custom-domain.com",
            use_ssl=True
        )

        # Even if SITE_URL is set to an IP address, it should prioritize the DB domain and force HTTPS
        with override_settings(DEBUG=False, SITE_URL="http://209.159.152.123"):
            resp = self.client.get("/api/v1/integrations/github/oauth-url/")
            self.assertEqual(resp.status_code, 200)
            self.assertIn("redirect_uri=https%3A%2F%2Fmy-custom-domain.com%2Fauth%2Fgithub%2Fcallback", resp.data.get("url", ""))


