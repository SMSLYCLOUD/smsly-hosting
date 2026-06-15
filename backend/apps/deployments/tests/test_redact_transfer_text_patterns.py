import re
import unittest

from apps.deployments.services.transfer_service import (
    _PATTERNS,
    _redact_transfer_text,
)


class PatternsListShapeTests(unittest.TestCase):
    def test_patterns_is_module_level_list(self):
        self.assertIsInstance(_PATTERNS, list)
        self.assertGreaterEqual(len(_PATTERNS), 6)
        for p in _PATTERNS:
            self.assertIsInstance(p, re.Pattern)

    def test_bearer_token_pattern_present(self):
        pat = _PATTERNS[2]
        self.assertIsInstance(pat, re.Pattern)
        self.assertEqual(
            pat.pattern, r"Bearer\s+[A-Za-z0-9._-]+",
        )

    def test_postgres_dsn_pattern_present(self):
        pat = _PATTERNS[3]
        self.assertIsInstance(pat, re.Pattern)
        self.assertEqual(
            pat.pattern, r"postgres(ql)?://[^\s]+:[^\s]+@",
        )


class RedactTransferTextTests(unittest.TestCase):
    def test_redacts_pem_private_key(self):
        text = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAK...lots of bytes...==\n"
            "-----END RSA PRIVATE KEY-----\n"
        )
        out = _redact_transfer_text(text)
        self.assertIn("***", out)
        self.assertNotIn("MIIEowIBAAK", out)

    def test_redacts_bearer_token(self):
        out = _redact_transfer_text("Authorization: Bearer abcDEF.123-xyz_456")
        self.assertNotIn("abcDEF.123-xyz_456", out)
        self.assertIn("***", out)

    def test_redacts_bearer_token_in_standalone(self):
        out = _redact_transfer_text("logged in with Bearer some-token-value")
        self.assertNotIn("some-token-value", out)
        self.assertIn("***", out)

    def test_redacts_postgres_url_with_password(self):
        out = _redact_transfer_text(
            "DATABASE_URL=postgres://user:supersecret@db.example.com:5432/mydb"
        )
        self.assertNotIn("supersecret", out)
        self.assertIn("***", out)

    def test_redacts_postgresql_url_with_password(self):
        out = _redact_transfer_text(
            "DSN=postgresql://admin:p@ssw0rd@localhost/app"
        )
        self.assertNotIn("p@ssw0rd", out)
        self.assertIn("***", out)

    def test_redacts_https_basic_auth_in_url(self):
        out = _redact_transfer_text(
            "Cloning https://alice:s3cret@github.com/foo/bar.git"
        )
        self.assertNotIn("s3cret", out)
        self.assertIn("***@", out)

    def test_redacts_env_style_secret(self):
        out = _redact_transfer_text("API_KEY=sk-test-12345")
        self.assertNotIn("sk-test-12345", out)
        self.assertIn("API_KEY=***", out)

    def test_redacts_authorization_header(self):
        out = _redact_transfer_text("Authorization: Bearer abc")
        self.assertNotIn("abc", out)
        self.assertIn("***", out)

    def test_passes_through_safe_text(self):
        safe = "this is just a normal log message"
        self.assertEqual(_redact_transfer_text(safe), safe)

    def test_handles_empty_and_none(self):
        self.assertEqual(_redact_transfer_text(""), "")
        self.assertEqual(_redact_transfer_text(None), "")


if __name__ == "__main__":
    unittest.main()
