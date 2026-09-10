"""Tests for the GitHub App manifest one-click flow.

The manifest exchange must leave the platform fully usable with zero
manual steps: PlatformConfig credentials, a quoted .env mirror, AND the
allauth SocialApp that powers user OAuth. Missing the SocialApp was the
live gap — `github_oauth_url` kept answering "GitHub OAuth not
configured" after a successful manifest run.
"""

import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.cloud.views import github_app_manifest as manifest_views
from apps.deployments.models.core import PlatformConfig

User = get_user_model()

_CONVERSION = {
    "id": 123456,
    "slug": "smsly-cloud-test",
    "name": "SMSLY Cloud",
    "client_id": "Iv1.testclientid",
    "client_secret": "testclientsecret",
    "webhook_secret": "testwebhooksecret",
    "pem": "-----BEGIN RSA PRIVATE KEY-----\nMIIBTEST\n-----END RSA PRIVATE KEY-----\n",
}


def _admin_client():
    admin = User.objects.create_superuser("ghadmin", "ghadmin@example.com", "pw")
    client = APIClient()
    client.force_authenticate(admin)
    return client


class ManifestSetupTests(TestCase):
    def _post_conversion(self, code="testcode"):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = dict(_CONVERSION)
        with patch.object(manifest_views.requests, "post", return_value=mock_resp):
            return self.client.post(
                "/api/v1/integrations/github/app-manifest/setup/?format=json",
                {"code": code},
                format="json",
            )

    def setUp(self):
        self.client = _admin_client()

    def test_setup_requires_code(self):
        resp = self.client.post(
            "/api/v1/integrations/github/app-manifest/setup/?format=json",
            {},
            format="json",
        )
        # Missing code -> 400 (serializer or explicit check).
        self.assertIn(resp.status_code, (400, 404))

    def test_setup_forbids_non_admin(self):
        user = User.objects.create_user("pleb", "pleb@example.com", "pw")
        client = APIClient()
        client.force_authenticate(user)
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = dict(_CONVERSION)
        with patch.object(manifest_views.requests, "post", return_value=mock_resp):
            resp = client.post(
                "/api/v1/integrations/github/app-manifest/setup/?format=json",
                {"code": "x"},
                format="json",
            )
        self.assertEqual(resp.status_code, 403)

    def test_setup_creates_social_app(self):
        from allauth.socialaccount.models import SocialApp

        self.assertFalse(SocialApp.objects.filter(provider="github").exists())
        with patch.object(
            manifest_views, "_write_github_env", return_value=None
        ):
            resp = self._post_conversion()
        self.assertEqual(resp.status_code, 200)
        app = SocialApp.objects.filter(provider="github").first()
        self.assertIsNotNone(app)
        self.assertEqual(app.client_id, "Iv1.testclientid")
        self.assertEqual(app.secret, "testclientsecret")

    def test_setup_stores_platform_config(self):
        with patch.object(
            manifest_views, "_write_github_env", return_value=None
        ):
            resp = self._post_conversion()
        self.assertEqual(resp.status_code, 200)
        cfg = PlatformConfig.objects.first()
        self.assertEqual(str(cfg.github_app_id), "123456")
        self.assertEqual(cfg.github_client_id, "Iv1.testclientid")
        self.assertIn("BEGIN RSA PRIVATE KEY", cfg.github_app_private_key)

    def test_write_github_env_quotes_pem(self):
        import os
        import tempfile

        fd, path = tempfile.mkstemp()
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("FOO=bar\n")
            with patch.dict(os.environ, {"SMSLY_ENV_FILE": path}):
                manifest_views._write_github_env(
                    "123", "cid", "csec",
                    "-----BEGIN RSA PRIVATE KEY-----\nABC\n-----END RSA PRIVATE KEY-----\n",
                    "whsec",
                )
            with open(path, encoding="utf-8") as fh:
                content = fh.read()
            line = next(
                l for l in content.splitlines()
                if l.startswith("GITHUB_APP_PRIVATE_KEY=")
            )
            # Quoted AND newline-escaped: safe for `source .env`.
            self.assertTrue(line.startswith('GITHUB_APP_PRIVATE_KEY="-----BEGIN'))
            self.assertIn("\\n", line)
            self.assertNotIn("RSA PRIVATE KEY----- ", line)
        finally:
            os.unlink(path)
