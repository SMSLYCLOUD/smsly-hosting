"""Tests for Finding #158 (configurable notification webhook timeout).

The notification webhook helper previously hard-coded a 5s
``requests.post`` timeout. The fix routes the timeout through
``settings.NOTIFICATION_WEBHOOK_TIMEOUT`` (default 5.0) so operators
can tune slow downstream targets without a code change.
"""
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from apps.notifications.webhooks import _get_request_timeout, _post_notification


class NotificationWebhookTimeoutConfigTests(SimpleTestCase):

    def test_default_timeout_is_5_seconds(self):
        with override_settings():
            from django.conf import settings
            if hasattr(settings, 'NOTIFICATION_WEBHOOK_TIMEOUT'):
                del settings.NOTIFICATION_WEBHOOK_TIMEOUT
            self.assertEqual(_get_request_timeout(), 5.0)

    @override_settings(NOTIFICATION_WEBHOOK_TIMEOUT=12.5)
    def test_custom_setting_is_honored(self):
        self.assertEqual(_get_request_timeout(), 12.5)

    @override_settings(NOTIFICATION_WEBHOOK_TIMEOUT=0)
    def test_zero_setting_falls_back_to_default(self):
        self.assertEqual(_get_request_timeout(), 5.0)

    @override_settings(NOTIFICATION_WEBHOOK_TIMEOUT=-1)
    def test_negative_setting_falls_back_to_default(self):
        self.assertEqual(_get_request_timeout(), 5.0)

    @override_settings(NOTIFICATION_WEBHOOK_TIMEOUT="not-a-float")
    def test_non_numeric_setting_falls_back_to_default(self):
        self.assertEqual(_get_request_timeout(), 5.0)

    @patch('apps.notifications.webhooks.requests.post')
    def test_post_uses_configured_timeout(self, mpost):
        mpost.return_value = MagicMock(status_code=200)
        with override_settings(NOTIFICATION_WEBHOOK_TIMEOUT=17.0):
            ok = _post_notification(
                'https://hooks.slack.com/services/T/B/X',
                {'text': 'hello'},
                user=None,
                provider='slack',
            )
        self.assertTrue(ok)
        self.assertEqual(mpost.call_args.kwargs['timeout'], 17.0)
