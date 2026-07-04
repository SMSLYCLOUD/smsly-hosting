# pylint: disable=invalid-name
"""Resilience tests for AI provider status endpoint and balance collection."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.intelligence.providers import (
    AIProvider,
    MockProvider,
    get_available_providers,
)
from apps.licensing.models import PlatformLicense, PlatformTier

User = get_user_model()


class _FailBalanceProvider(AIProvider):
    def __init__(self):
        self.api_key = "configured"
        self.model = "fail-model"

    def ask(self, prompt: str, system_prompt=None) -> str:
        return "ok"

    def name(self) -> str:
        return "FailBalance"

    def get_balance(self) -> dict:
        raise RuntimeError("balance backend unavailable")


class AIProviderStatusResilienceTests(TestCase):
    def setUp(self):
        license_obj = PlatformLicense.load()
        license_obj.tier = PlatformTier.PRO
        license_obj.is_valid = True
        license_obj.max_services = 100
        license_obj.max_team_members = 100
        license_obj.save(update_fields=["tier", "is_valid", "max_services", "max_team_members"])

        self.user = User.objects.create_superuser(
            username="airesilience2",
            email="airesilience2@example.com",
            password="password123",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_get_available_providers_include_balance_handles_provider_errors(self):
        with patch.dict(
            "apps.intelligence.providers.PROVIDERS",
            {"fail": _FailBalanceProvider, "mock": MockProvider},
            clear=True,
        ):
            providers = get_available_providers(include_balance=True)

        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0]["id"], "fail")
        self.assertIn(
            providers[0]["balance"]["balance"],
            {"Error checking", "Timed out"},
        )

    @patch("apps.intelligence.views.get_configured_providers")
    @patch("apps.intelligence.views.get_available_providers")
    def test_ai_providers_status_degrades_instead_of_500(self, mock_available, mock_configured):
        mock_available.side_effect = RuntimeError("provider status query failed")
        mock_configured.side_effect = RuntimeError("configured provider query failed")

        response = self.client.get("/api/v1/ai/providers/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.get("degraded"))
        self.assertEqual(response.data["providers"], [])
        self.assertEqual(response.data["mode"], "unconfigured")
