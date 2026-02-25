# pylint: disable=invalid-name
"""Integration API tests (GitHub connect bootstrap flow)."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

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
