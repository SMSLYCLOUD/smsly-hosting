# pylint: disable=invalid-name
"""Regression tests for provider endpoint corrections.

- localllm must NOT self-activate from factory defaults (autostart bug:
  a dead localhost endpoint joined the Senate and stalled probed chains).
- jules must NOT activate against the dead api.jules.google.com default.
- cloudflare must NOT activate while the YOUR_ACCOUNT_ID placeholder is set.
- opencode / deepseek / kimi factory defaults must point at live endpoints.
"""

import os
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from apps.intelligence.providers import get_configured_providers
from apps.intelligence.providers.cloudflare import CloudflareProvider
from apps.intelligence.providers.deepseek import DeepSeekProvider
from apps.intelligence.providers.jules import JulesProvider
from apps.intelligence.providers.kimi import KimiProvider
from apps.intelligence.providers.localllm import LocalLLMProvider
from apps.intelligence.providers.opencode import OpenCodeProvider


def _provider_ids(configured):
    return {getattr(p, "id", p.__class__.__name__) for p in configured}


class LocalLLMAutostartTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch("apps.intelligence.providers._get_db_settings", return_value=None)
    def test_empty_env_yields_no_localllm(self, _mock_db_settings):
        with patch.dict(os.environ, {}, clear=True):
            configured = get_configured_providers()
        self.assertNotIn("localllm", _provider_ids(configured))

    @patch("apps.intelligence.providers._get_db_settings", return_value=None)
    def test_stock_localhost_default_is_not_an_opt_in(self, _mock_db_settings):
        with patch.dict(
            os.environ,
            {"LOCALLM_BASE_URL": "http://localhost:11434/v1"},
            clear=True,
        ):
            self.assertFalse(LocalLLMProvider().is_configured())
            configured = get_configured_providers()
        self.assertNotIn("localllm", _provider_ids(configured))

    @override_settings(LOCALLM_ALLOWED_HOSTS=("localhost",))
    def test_explicit_allowlisted_localhost_still_works(self):
        with patch.dict(
            os.environ,
            {"LOCALLM_BASE_URL": "http://localhost:11434/v1"},
            clear=True,
        ):
            self.assertTrue(LocalLLMProvider().is_configured())

    def test_explicit_custom_host_is_configured(self):
        with patch.dict(
            os.environ,
            {"LOCALLM_BASE_URL": "https://ollama.internal.example/v1"},
            clear=True,
        ):
            self.assertTrue(LocalLLMProvider().is_configured())


class JulesOptInTests(TestCase):
    def test_dead_default_host_is_never_configured(self):
        with patch.dict(
            os.environ,
            {
                "JULES_API_KEY": "live-key",
                "JULES_BASE_URL": "https://api.jules.google.com/v1",
            },
            clear=True,
        ):
            self.assertFalse(JulesProvider().is_configured())

    def test_empty_base_url_is_not_configured(self):
        with patch.dict(
            os.environ,
            {"JULES_API_KEY": "live-key", "JULES_BASE_URL": ""},
            clear=True,
        ):
            self.assertFalse(JulesProvider().is_configured())

    def test_custom_gateway_with_key_is_configured(self):
        with patch.dict(
            os.environ,
            {
                "JULES_API_KEY": "live-key",
                "JULES_BASE_URL": "https://jules.internal.example/v1",
            },
            clear=True,
        ):
            self.assertTrue(JulesProvider().is_configured())


class CloudflarePlaceholderTests(TestCase):
    def test_placeholder_account_id_is_not_configured(self):
        with patch.dict(
            os.environ,
            {
                "CLOUDFLARE_API_KEY": "live-key",
                "CLOUDFLARE_BASE_URL": (
                    "https://gateway.ai.cloudflare.com/v1/"
                    "YOUR_ACCOUNT_ID/default/compat"
                ),
            },
            clear=True,
        ):
            self.assertFalse(CloudflareProvider().is_configured())

    def test_factory_default_uses_compat_path(self):
        with patch.dict(os.environ, {}, clear=True):
            provider = CloudflareProvider()
        self.assertTrue(provider.base_url.endswith("/default/compat"))


class LiveDefaultTests(TestCase):
    def test_opencode_default_is_zen_endpoint(self):
        with patch.dict(os.environ, {}, clear=True):
            provider = OpenCodeProvider()
        self.assertEqual(provider.base_url, "https://opencode.ai/zen/v1")
        self.assertEqual(provider.model, "big-pickle")

    def test_deepseek_default_model_is_live(self):
        with patch.dict(os.environ, {}, clear=True):
            provider = DeepSeekProvider()
        self.assertEqual(provider.model, "deepseek-chat")

    def test_kimi_default_model_is_live(self):
        with patch.dict(os.environ, {}, clear=True):
            provider = KimiProvider()
        self.assertEqual(provider.model, "kimi-k2.6")
