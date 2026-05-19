import os
import unittest
import django
django.setup()

from unittest.mock import patch, MagicMock
from apps.deployments.api_token_auth import RemoteSyncHMACAuthentication
from django.test import RequestFactory
from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed
import hashlib
import hmac
import time

class TestAuthenticationHardening(unittest.TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.auth = RemoteSyncHMACAuthentication()

    @patch("apps.deployments.api_token_auth.settings")
    def test_missing_gateway_secret_fails_closed(self, mock_settings):
        mock_settings.GATEWAY_SECRET = ""
        mock_settings.SECRET_KEY = "should-not-fallback"

        request = self.factory.post("/api/v1/sync/", data={"test": "1"})
        request.headers = {
            "X-SMSLY-Remote-Sync": "1",
            "X-Gateway-Signature-V2": "fake",
            "X-Request-Timestamp": str(int(time.time()))
        }

        with self.assertRaises(AuthenticationFailed) as context:
            self.auth.authenticate(request)

        self.assertIn("not configured", str(context.exception))

    @patch("apps.deployments.api_token_auth.settings")
    def test_invalid_signature_fails(self, mock_settings):
        mock_settings.GATEWAY_SECRET = "secret123"

        request = self.factory.post("/api/v1/sync/", data={"test": "1"})
        request.headers = {
            "X-SMSLY-Remote-Sync": "1",
            "X-Gateway-Signature-V2": "invalid",
            "X-Request-Timestamp": str(int(time.time()))
        }

        with self.assertRaises(AuthenticationFailed) as context:
            self.auth.authenticate(request)

        self.assertIn("Invalid remote sync signature", str(context.exception))
