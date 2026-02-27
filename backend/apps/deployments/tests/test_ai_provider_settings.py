# pylint: disable=invalid-name
"""Tests for AI provider update/clear behavior."""

import os

from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase

from apps.intelligence.models import AIProviderSettings
from apps.intelligence.providers import _sync_db_to_env
from apps.licensing.models import PlatformLicense, PlatformTier


class AIProviderSettingsTests(APITestCase):
    def setUp(self):
        license_obj = PlatformLicense.load()
        license_obj.tier = PlatformTier.PRO
        license_obj.is_valid = True
        license_obj.max_services = 100
        license_obj.max_team_members = 100
        license_obj.save(update_fields=["tier", "is_valid", "max_services", "max_team_members"])

        self.admin = User.objects.create_superuser(
            username="ai-admin",
            email="ai-admin@example.com",
            password="password123",
        )
        self.client.force_authenticate(user=self.admin)
        self.url = "/api/v1/ai/providers/update/"

    def test_admin_can_clear_provider_key(self):
        settings = AIProviderSettings.get_solo()
        settings.openai_api_key = "sk-test-1234"
        settings.save(update_fields=["openai_api_key"])

        response = self.client.post(self.url, {"openai_api_key": ""}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        settings.refresh_from_db()
        self.assertFalse(settings.openai_api_key)

    def test_sync_db_to_env_removes_cleared_key(self):
        settings = AIProviderSettings.get_solo()
        settings.openai_api_key = ""
        settings.save(update_fields=["openai_api_key"])
        os.environ["OPENAI_API_KEY"] = "stale-value"

        _sync_db_to_env()

        self.assertNotIn("OPENAI_API_KEY", os.environ)
