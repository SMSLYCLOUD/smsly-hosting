# pylint: disable=invalid-name
"""SSRF guard tests for AIProviderSettings.localllm_base_url.

The ``localllm_base_url`` admin setting is used as the OpenAI-compatible
endpoint for the local LLM provider.  The provider call is prefixed with
the provider's API key, so an admin who pointed the URL at an internal
service (cloud metadata, link-local RFC1918, or the platform's own mesh)
would exfiltrate the key to themselves.  These tests pin the validation
contract: internal IP ranges are always rejected, and any hostname must
be explicitly listed in ``settings.LOCALLM_ALLOWED_HOSTS`` (default
empty tuple = reject everything).
"""

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from apps.intelligence.models import AIProviderSettings


class LocalLLMBaseURLSSRFGuardTests(TestCase):
    def setUp(self):
        AIProviderSettings.objects.all().delete()

    def _settings(self):
        return AIProviderSettings.get_solo()

    def test_localhost_rejected(self):
        s = self._settings()
        s.localllm_base_url = 'http://localhost:1234'
        with self.assertRaises(ValidationError) as ctx:
            s.full_clean()
        self.assertIn('localllm_base_url', ctx.exception.message_dict)

    def test_loopback_ipv4_rejected(self):
        s = self._settings()
        s.localllm_base_url = 'http://127.0.0.1:8080'
        with self.assertRaises(ValidationError) as ctx:
            s.full_clean()
        self.assertIn('localllm_base_url', ctx.exception.message_dict)

    def test_cloud_metadata_ipv4_rejected(self):
        s = self._settings()
        s.localllm_base_url = (
            'http://169.254.169.254/latest/meta-data/iam/security-credentials/'
        )
        with self.assertRaises(ValidationError) as ctx:
            s.full_clean()
        self.assertIn('localllm_base_url', ctx.exception.message_dict)

    def test_rfc1918_10_rejected(self):
        s = self._settings()
        s.localllm_base_url = 'http://10.0.0.1/v1'
        with self.assertRaises(ValidationError) as ctx:
            s.full_clean()
        self.assertIn('localllm_base_url', ctx.exception.message_dict)

    def test_rfc1918_192_rejected(self):
        s = self._settings()
        s.localllm_base_url = 'http://192.168.1.1/'
        with self.assertRaises(ValidationError) as ctx:
            s.full_clean()
        self.assertIn('localllm_base_url', ctx.exception.message_dict)

    def test_rfc1918_172_rejected(self):
        s = self._settings()
        s.localllm_base_url = 'http://172.16.0.5:1234/v1'
        with self.assertRaises(ValidationError) as ctx:
            s.full_clean()
        self.assertIn('localllm_base_url', ctx.exception.message_dict)

    def test_empty_url_is_allowed(self):
        s = self._settings()
        s.localllm_base_url = ''
        s.full_clean()

    def test_hostname_rejected_when_allowlist_empty(self):
        s = self._settings()
        with override_settings(LOCALLM_ALLOWED_HOSTS=()):
            s.localllm_base_url = 'http://my-llm.local:11434'
            with self.assertRaises(ValidationError) as ctx:
                s.full_clean()
        self.assertIn('localllm_base_url', ctx.exception.message_dict)

    @override_settings(LOCALLM_ALLOWED_HOSTS=('my-llm.local',))
    def test_hostname_accepted_when_allowlisted(self):
        s = self._settings()
        s.localllm_base_url = 'http://my-llm.local:11434'
        s.full_clean()

    def test_public_ip_literal_still_rejected_via_network_ranges(self):
        # 8.8.8.8 is a public IP — not in any disallowed range — so the
        # IP check should NOT trip.  The hostname-allowlist check should
        # then trip because ``8.8.8.8`` is not a hostname and is not
        # in the (empty) allowlist.  We assert the request is rejected.
        s = self._settings()
        s.localllm_base_url = 'http://8.8.8.8:80/v1'
        with self.assertRaises(ValidationError) as ctx:
            s.full_clean()
        self.assertIn('localllm_base_url', ctx.exception.message_dict)
