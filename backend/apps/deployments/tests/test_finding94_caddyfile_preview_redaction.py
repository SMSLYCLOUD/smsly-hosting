# pylint: disable=invalid-name
"""Regression tests for Finding #94 (caddyfile_preview redaction).

Before the fix, ``DomainConfigView`` returned the full Caddyfile in
``response['caddyfile_preview']`` including the lines that hold TLS,
``Strict-Transport-Security`` headers, ``internal`` directives,
``basicauth`` users, and any ``${ENV_VAR}`` placeholders that may
encode tokens. The fix redacts any line containing those keywords
or env-var-style placeholders, replacing the whole line with
``***REDACTED***``.

These tests verify:
  * A line containing any of the redact keywords is replaced.
  * A line containing a ``${ENV_VAR}`` placeholder is replaced.
  * Plain Caddyfile directives (e.g. ``root * /var/www``) pass through.
  * Empty input is a no-op.
"""

from django.test import SimpleTestCase

from apps.core.views.system import _redact_caddyfile_preview


class Finding94CaddyfilePreviewRedactionTests(SimpleTestCase):
    def test_redacts_strict_transport_security_line(self):
        text = (
            "example.com {\n"
            "    header Strict-Transport-Security \"max-age=31536000\"\n"
            "}\n"
        )
        out = _redact_caddyfile_preview(text)
        self.assertIn("***REDACTED***", out)
        self.assertNotIn("max-age=31536000", out)

    def test_redacts_tls_internal_basicauth_lines(self):
        text = (
            "example.com {\n"
            "    tls /etc/caddy/cert.pem /etc/caddy/key.pem\n"
            "    internal\n"
            "    basicauth {\n"
            "        admin $2a$14$abcdef\n"
            "    }\n"
            "}\n"
        )
        out = _redact_caddyfile_preview(text)
        self.assertIn("***REDACTED***", out)
        self.assertNotIn("caddy/cert.pem", out)
        self.assertNotIn("basicauth", out.lower())
        self.assertNotIn("$2a$14$abcdef", out)

    def test_redacts_env_var_placeholders(self):
        text = (
            "example.com {\n"
            "    reverse_proxy backend:80 { ${UPSTREAM_HEADER} }\n"
            "}\n"
        )
        out = _redact_caddyfile_preview(text)
        self.assertNotIn("${UPSTREAM_HEADER}", out)
        self.assertIn("***REDACTED***", out)

    def test_passes_through_plain_directives(self):
        text = (
            "example.com {\n"
            "    root * /var/www/html\n"
            "    encode gzip\n"
            "    file_server\n"
            "}"
        )
        out = _redact_caddyfile_preview(text)
        self.assertNotIn("***REDACTED***", out)
        self.assertIn("root * /var/www/html", out)
        self.assertIn("encode gzip", out)
        self.assertIn("file_server", out)

    def test_empty_text_is_unchanged(self):
        self.assertEqual(_redact_caddyfile_preview(""), "")
