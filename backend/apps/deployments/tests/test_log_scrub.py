"""Secret masking for user-facing log payloads."""
from django.test import SimpleTestCase

from apps.deployments.utils.log_scrub import mask_secrets_in_text


class MaskSecretsTests(SimpleTestCase):
    def test_url_userinfo_password_masked(self):
        line = "connecting to postgresql://app:s3cr3t-pw@db:5432/main"
        out = mask_secrets_in_text(line)
        self.assertNotIn("s3cr3t-pw", out)
        self.assertIn("postgresql://app:***@db:5432/main", out)

    def test_url_query_token_masked(self):
        line = "GET https://api.example.com/hooks?token=abc123XYZ&other=1"
        out = mask_secrets_in_text(line)
        self.assertNotIn("abc123XYZ", out)
        self.assertIn("token=***", out)
        self.assertIn("other=1", out)

    def test_kv_assignment_masked(self):
        out = mask_secrets_in_text("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9")
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", out)
        self.assertIn("***", out)

    def test_url_userinfo_empty_user_masked(self):
        out = mask_secrets_in_text("READY redis://:node-secret-pw@10.0.0.9:6379/0 up")
        self.assertNotIn("node-secret-pw", out)
        self.assertIn("redis://:***@10.0.0.9:6379/0", out)

    def test_plain_logs_untouched(self):
        line = "2026-10-09T10:00:00Z INFO server listening on :8000 (build 42)"
        self.assertEqual(mask_secrets_in_text(line), line)

    def test_port_only_url_untouched(self):
        line = "listening on http://example.com:8080/health ok"
        self.assertEqual(mask_secrets_in_text(line), line)

    def test_empty_and_none_passthrough(self):
        self.assertEqual(mask_secrets_in_text(""), "")
        self.assertIsNone(mask_secrets_in_text(None))

    def test_never_raises(self):
        self.assertEqual(mask_secrets_in_text(12345), 12345)
