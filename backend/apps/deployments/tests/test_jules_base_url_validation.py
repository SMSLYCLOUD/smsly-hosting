# pylint: disable=invalid-name
"""Tests for JULES_BASE_URL host allowlist validation."""

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from apps.intelligence.models import AIProviderSettings


class JULESBaseURLValidationTests(TestCase):
    def setUp(self):
        AIProviderSettings.objects.all().delete()

    def _settings(self):
        return AIProviderSettings.get_solo()

    def test_http_scheme_rejected(self):
        s = self._settings()
        s.jules_base_url = 'http://attacker.com/'
        with self.assertRaises(ValidationError) as ctx:
            s.full_clean()
        self.assertIn('jules_base_url', ctx.exception.message_dict)

    def test_ftp_scheme_rejected(self):
        s = self._settings()
        s.jules_base_url = 'ftp://files.attacker.com/'
        with self.assertRaises(ValidationError) as ctx:
            s.full_clean()
        self.assertIn('jules_base_url', ctx.exception.message_dict)

    def test_official_jules_host_accepted(self):
        s = self._settings()
        s.jules_base_url = 'https://api.jules.google.com/v1'
        s.full_clean()

    @override_settings(JULES_ALLOWED_HOSTS=['api.jules.google.com', 'internal.jules-proxy.example'])
    def test_extra_allowed_host_accepted(self):
        s = self._settings()
        s.jules_base_url = 'https://internal.jules-proxy.example/'
        s.full_clean()

    @override_settings(JULES_ALLOWED_HOSTS=['internal.jules-proxy.example'])
    def test_unknown_host_rejected_even_with_extra_allowlist(self):
        s = self._settings()
        s.jules_base_url = 'https://api.jules.google.com/v1'
        with self.assertRaises(ValidationError) as ctx:
            s.full_clean()
        self.assertIn('jules_base_url', ctx.exception.message_dict)

    def test_empty_url_is_allowed(self):
        s = self._settings()
        s.jules_base_url = ''
        s.full_clean()
