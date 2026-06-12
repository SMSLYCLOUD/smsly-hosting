# pylint: disable=invalid-name
"""
Tests for ``_validate_registry_url`` — the SSRF guard added to
``config.settings``.

The function is intentionally module-level and is invoked at import time
of ``settings.py``. We exercise it directly here so we don't have to
reload the settings module under multiple environment overrides.
"""
import os

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config.settings import _validate_registry_url


class ValidateRegistryUrlTests(SimpleTestCase):
    def setUp(self):
        self._original = os.environ.get('CONTAINER_REGISTRY_URL')
        self._had = 'CONTAINER_REGISTRY_URL' in os.environ
        os.environ.pop('CONTAINER_REGISTRY_URL', None)

    def tearDown(self):
        if self._had:
            os.environ['CONTAINER_REGISTRY_URL'] = self._original or ''
        else:
            os.environ.pop('CONTAINER_REGISTRY_URL', None)

    def test_empty_url_is_allowed(self):
        os.environ.pop('CONTAINER_REGISTRY_URL', None)
        self.assertIsNone(_validate_registry_url())

    def test_attacker_external_url_is_rejected(self):
        os.environ['CONTAINER_REGISTRY_URL'] = 'http://attacker.example/'
        with self.assertRaises(ImproperlyConfigured):
            _validate_registry_url()

    def test_https_registry_smsly_cloud_is_ok(self):
        os.environ['CONTAINER_REGISTRY_URL'] = 'https://registry.smsly.cloud/'
        self.assertIsNone(_validate_registry_url())

    def test_localhost_http_is_ok(self):
        os.environ['CONTAINER_REGISTRY_URL'] = 'http://localhost:5000/'
        self.assertIsNone(_validate_registry_url())

    def test_private_10_dot_ip_is_ok(self):
        os.environ['CONTAINER_REGISTRY_URL'] = 'http://10.0.0.5:5000/'
        self.assertIsNone(_validate_registry_url())

    def test_private_192_168_is_ok(self):
        os.environ['CONTAINER_REGISTRY_URL'] = 'http://192.168.1.10:5000/'
        self.assertIsNone(_validate_registry_url())

    def test_ftp_scheme_is_rejected(self):
        os.environ['CONTAINER_REGISTRY_URL'] = 'ftp://registry.example/'
        with self.assertRaises(ImproperlyConfigured):
            _validate_registry_url()

    def test_gopher_scheme_is_rejected(self):
        os.environ['CONTAINER_REGISTRY_URL'] = 'gopher://evil.example/'
        with self.assertRaises(ImproperlyConfigured):
            _validate_registry_url()

    def test_external_https_is_rejected(self):
        os.environ['CONTAINER_REGISTRY_URL'] = 'https://attacker.example/'
        with self.assertRaises(ImproperlyConfigured):
            _validate_registry_url()
