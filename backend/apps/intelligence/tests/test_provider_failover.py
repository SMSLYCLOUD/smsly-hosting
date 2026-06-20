# pylint: disable=invalid-name
"""Failover and key-sanitization tests for AI providers."""

import os
from unittest import TestCase
from unittest.mock import patch

from apps.intelligence.providers import (
    AIProvider,
    ask_with_fallback,
    get_configured_providers,
)


class _WorkingProvider(AIProvider):
    def __init__(self, provider_name: str):
        self.api_key = "configured"
        self._provider_name = provider_name

    def ask(self, prompt: str, system_prompt=None) -> str:
        return f"live:{prompt}"

    def name(self) -> str:
        return self._provider_name


class ProviderFailoverTests(TestCase):
    @patch("apps.intelligence.providers.ask_collaborative")
    @patch("apps.intelligence.providers.get_configured_providers")
    def test_committee_total_failure_rescues_with_direct_provider(
        self,
        mock_configured,
        mock_collab,
    ):
        mock_configured.return_value = [
            _WorkingProvider("OpenAI (gpt-4o-mini)"),
            _WorkingProvider("Grok (grok-3-mini)"),
        ]
        mock_collab.return_value = ("mock-response", "Mock AI (all 2 senators failed)")

        response, provider = ask_with_fallback("hello")

        self.assertEqual(response, "live:hello")
        self.assertEqual(provider, "OpenAI (gpt-4o-mini)")

    @patch("apps.intelligence.providers._get_db_settings", return_value=None)
    def test_placeholder_key_is_not_treated_as_configured(self, _mock_db_settings):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "Configured key (hidden)"}, clear=True):
            configured = [p for p in get_configured_providers() if p.__class__.__name__ not in ['LocalLLMProvider']]
        self.assertEqual(configured, [])

    @patch("apps.intelligence.providers._get_db_settings", return_value=None)
    def test_bearer_prefix_key_still_counts_as_configured(self, _mock_db_settings):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "Bearer sk-live"}, clear=True):
            configured = get_configured_providers()
        self.assertTrue(any("OpenAI" in provider.name() for provider in configured))
