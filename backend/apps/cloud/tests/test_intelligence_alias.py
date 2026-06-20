# pylint: disable=invalid-name
"""Tests for backward-compatible cloud intelligence endpoints."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


class CloudIntelligenceAliasTests(TestCase):
    """Ensure legacy frontend endpoints keep working."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="askalias",
            email="askalias@test.com",
            password="testpass123",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch("apps.intelligence.providers.ask_with_fallback")
    def test_ask_alias_routes_to_chat(self, mock_ask_with_fallback):
        mock_ask_with_fallback.return_value = ("Hello from AI", "Mock AI")

        response = self.client.post(
            "/api/v1/cloud/intelligence/ask/",
            {"message": "hello"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["response"], "Hello from AI")
        self.assertEqual(response.data["provider"], "Mock AI")

    @patch("apps.intelligence.providers.ask_with_fallback")
    def test_ask_alias_without_trailing_slash_routes_to_chat(self, mock_ask_with_fallback):
        mock_ask_with_fallback.return_value = ("Hello no slash", "Mock AI")

        response = self.client.post(
            "/api/v1/cloud/intelligence/ask",
            {"message": "hello"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["response"], "Hello no slash")

    @patch("apps.intelligence.providers.ask_with_fallback", side_effect=RuntimeError("provider down"))
    def test_ask_alias_fails_open_when_provider_errors(self, _mock_ask):
        response = self.client.post(
            "/api/v1/cloud/intelligence/ask/",
            {"message": "hello"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.get("degraded"))
        self.assertEqual(response.data.get("provider"), "Mock AI (degraded)")

    @patch("apps.intelligence.providers.get_available_providers", side_effect=RuntimeError("provider status failed"))
    def test_providers_endpoint_fails_open(self, _mock_providers):
        response = self.client.get("/api/v1/cloud/intelligence/providers/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])
