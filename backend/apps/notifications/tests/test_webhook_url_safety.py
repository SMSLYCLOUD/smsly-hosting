"""Hermetic tests for the SSRF guards in apps/notifications/webhooks.py.

These tests cover ``_validate_notification_url`` and ``_post_notification``:
- Allowed hosts (hooks.slack.com, discord.com) accepted.
- Disallowed hosts (attacker.example.com, metadata IP, loopback) rejected.
- Non-http(s) schemes rejected.
- IP literals and DNS-resolved internal IPs rejected.
- 64KB body cap enforced.
- 5s timeout + redirect-disable applied.
- Audit log does not leak the full webhook URL.
"""

import socket
import unittest
from unittest.mock import MagicMock, patch

from apps.notifications.webhooks import (
    _validate_notification_url,
    _post_notification,
    _log_notification,
    _ALLOWED_NOTIFICATION_HOSTS,
)


class NotificationURLValidationTests(unittest.TestCase):

    def test_allows_hooks_slack_com(self):
        host = _validate_notification_url('https://hooks.slack.com/services/T/B/X')
        self.assertEqual(host, 'hooks.slack.com')

    def test_allows_discord_com(self):
        host = _validate_notification_url('https://discord.com/api/webhooks/123/abc')
        self.assertEqual(host, 'discord.com')

    def test_allows_discordapp_com(self):
        host = _validate_notification_url('https://discordapp.com/api/webhooks/123/abc')
        self.assertEqual(host, 'discordapp.com')

    def test_allows_hooks_slack_gov_com(self):
        host = _validate_notification_url('https://hooks.slack-gov.com/services/T/B/X')
        self.assertEqual(host, 'hooks.slack-gov.com')

    def test_rejects_unlisted_host(self):
        with self.assertRaises(ValueError) as ctx:
            _validate_notification_url('https://attacker.example.com/hook')
        self.assertIn('not in the allowlist', str(ctx.exception))

    def test_rejects_localhost(self):
        with self.assertRaises(ValueError):
            _validate_notification_url('https://localhost/hook')

    def test_rejects_loopback_ip_literal(self):
        with self.assertRaises(ValueError):
            _validate_notification_url('https://127.0.0.1/hook')

    def test_rejects_ftp_scheme(self):
        with self.assertRaises(ValueError) as ctx:
            _validate_notification_url('ftp://hooks.slack.com/hook')
        self.assertIn('http(s)', str(ctx.exception))

    def test_rejects_file_scheme(self):
        with self.assertRaises(ValueError):
            _validate_notification_url('file:///etc/passwd')

    def test_rejects_empty_url(self):
        with self.assertRaises(ValueError):
            _validate_notification_url('')

    def test_rejects_missing_host(self):
        with self.assertRaises(ValueError) as ctx:
            _validate_notification_url('https:///path')
        self.assertIn('missing a host', str(ctx.exception))

    def test_dns_resolution_rejects_loopback(self):
        fake_info = [(2, 1, 6, '', ('127.0.0.1', 0))]
        with patch('apps.notifications.webhooks.socket.getaddrinfo', return_value=fake_info):
            with self.assertRaises(ValueError) as ctx:
                _validate_notification_url('https://hooks.slack.com/x')
        self.assertIn('disallowed IP', str(ctx.exception))

    def test_dns_resolution_rejects_link_local_metadata(self):
        fake_info = [(2, 1, 6, '', ('169.254.169.254', 0))]
        with patch('apps.notifications.webhooks.socket.getaddrinfo', return_value=fake_info):
            with self.assertRaises(ValueError):
                _validate_notification_url('https://hooks.slack.com/x')

    def test_dns_resolution_rejects_rfc1918(self):
        fake_info = [(2, 1, 6, '', ('10.0.0.5', 0))]
        with patch('apps.notifications.webhooks.socket.getaddrinfo', return_value=fake_info):
            with self.assertRaises(ValueError):
                _validate_notification_url('https://hooks.slack.com/x')

    def test_dns_resolution_failure_rejected(self):
        with patch('apps.notifications.webhooks.socket.getaddrinfo',
                   side_effect=socket.gaierror('no such host')):
            with self.assertRaises(ValueError) as ctx:
                _validate_notification_url('https://hooks.slack.com/x')
        self.assertIn('Cannot resolve', str(ctx.exception))


class PostNotificationSafetyTests(unittest.TestCase):

    @patch('apps.notifications.webhooks.requests.post')
    def test_post_uses_5s_timeout_and_disables_redirects(self, mpost):
        mpost.return_value = MagicMock(status_code=200)
        ok = _post_notification(
            'https://hooks.slack.com/services/T/B/X',
            {'text': 'hello'},
            user=None,
            provider='slack',
        )
        self.assertTrue(ok)
        kwargs = mpost.call_args.kwargs
        self.assertEqual(kwargs['timeout'], 5)
        self.assertFalse(kwargs['allow_redirects'])

    @patch('apps.notifications.webhooks.requests.post')
    def test_post_rejects_oversize_body(self, mpost):
        big_payload = {'text': 'a' * (65 * 1024)}
        ok = _post_notification(
            'https://hooks.slack.com/x',
            big_payload,
            provider='slack',
        )
        self.assertFalse(ok)
        mpost.assert_not_called()

    @patch('apps.notifications.webhooks.requests.post')
    def test_post_returns_false_for_5xx(self, mpost):
        mpost.return_value = MagicMock(status_code=500)
        ok = _post_notification('https://hooks.slack.com/x', {'text': 'h'}, provider='slack')
        self.assertFalse(ok)

    @patch('apps.notifications.webhooks.requests.post')
    def test_post_returns_false_for_disallowed_host(self, mpost):
        ok = _post_notification('https://attacker.example.com/x', {'text': 'h'}, provider='slack')
        self.assertFalse(ok)
        mpost.assert_not_called()

    @patch('apps.notifications.webhooks.requests.post')
    def test_post_returns_false_on_network_error(self, mpost):
        import requests as _req
        mpost.side_effect = _req.ConnectionError('boom')
        ok = _post_notification('https://hooks.slack.com/x', {'text': 'h'}, provider='slack')
        self.assertFalse(ok)


class AuditLogRedactionTests(unittest.TestCase):
    """The audit log must never persist the full webhook URL (which
    contains the secret token in the path)."""

    def test_log_notification_never_logs_full_url(self):
        from apps.notifications.webhooks import _log_notification
        with self.assertLogs('apps.notifications.webhooks', level='INFO') as cm:
            _log_notification(
                provider='slack',
                user=None,
                url='https://hooks.slack.com/services/T0/B0/XXX-secret-webhook-token',
            )
        for line in cm.output:
            self.assertNotIn('XXX-secret-webhook-token', line)
            # The host is allowed.
            self.assertIn('hooks.slack.com', line)
