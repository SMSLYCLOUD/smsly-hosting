# pylint: disable=invalid-name
"""Tests for SEC (Issue 73): consumers.py audit log redacts and truncates commands."""
from django.test import TestCase

from apps.deployments.consumers import TerminalConsumer


class CommandRedactionTests(TestCase):
    def test_truncate_short_command_is_unchanged(self):
        cmd = "ls -la"
        self.assertEqual(
            TerminalConsumer._truncate_command(cmd, max_len=200),
            cmd,
        )

    def test_truncate_long_command_is_clipped(self):
        cmd = "x" * 500
        out = TerminalConsumer._truncate_command(cmd, max_len=200)
        self.assertTrue(out.endswith("...[truncated]"))
        self.assertLessEqual(len(out), 200 + len("...[truncated]"))

    def test_redact_kv_token(self):
        cmd = "curl -H 'Authorization: Bearer eyJabc.def.ghi' https://api"
        out = TerminalConsumer._redact_command(cmd)
        self.assertIn("[REDACTED]", out)
        self.assertNotIn("eyJabc.def.ghi", out)

    def test_redact_kv_password(self):
        cmd = "mysql -u root --password=supersecret"
        out = TerminalConsumer._redact_command(cmd)
        self.assertIn("[REDACTED]", out)
        self.assertNotIn("supersecret", out)

    def test_redact_long_hex_blob(self):
        cmd = "echo 0123456789abcdef0123456789abcdef0123456789abcdef"
        out = TerminalConsumer._redact_command(cmd)
        self.assertIn("[REDACTED]", out)

    def test_redact_preserves_safe_command(self):
        cmd = "ls -la /tmp"
        out = TerminalConsumer._redact_command(cmd)
        self.assertEqual(out, cmd)

    def test_redact_empty_is_empty(self):
        self.assertEqual(TerminalConsumer._redact_command(""), "")
