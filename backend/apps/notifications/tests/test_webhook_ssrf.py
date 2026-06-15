import unittest
from unittest.mock import MagicMock, patch

from apps.notifications.webhooks import (
    _post_notification,
    _validate_notification_url,
)


class ValidateSsrfImportTests(unittest.TestCase):
    def test_validate_ssrf_is_imported(self):
        from apps.notifications.webhooks import validate_ssrf
        self.assertTrue(callable(validate_ssrf))


class PostNotificationSsrfTests(unittest.TestCase):

    @patch('apps.notifications.webhooks.validate_ssrf')
    @patch('apps.notifications.webhooks._validate_notification_url',
           return_value='hooks.slack.com')
    @patch('apps.notifications.webhooks.requests.post')
    def test_loopback_url_rejected_by_validate_ssrf(
            self, mpost, _vnu, mock_validate_ssrf):
        from django.core.exceptions import ValidationError
        mock_validate_ssrf.side_effect = ValidationError(
            "Loopback IPs (127.0.0.1) are not allowed."
        )
        ok = _post_notification(
            'https://hooks.slack.com/services/T/B/X',
            {'text': 'hello'},
            user=None,
            provider='slack',
        )
        self.assertFalse(ok)
        mock_validate_ssrf.assert_called_once()
        mpost.assert_not_called()

    @patch('apps.notifications.webhooks.validate_ssrf')
    @patch('apps.notifications.webhooks._validate_notification_url',
           return_value='hooks.slack.com')
    @patch('apps.notifications.webhooks.requests.post')
    def test_metadata_ip_rejected_by_validate_ssrf(
            self, mpost, _vnu, mock_validate_ssrf):
        from django.core.exceptions import ValidationError
        mock_validate_ssrf.side_effect = ValidationError(
            "Link-local IPs (169.254.169.254) are not allowed."
        )
        ok = _post_notification(
            'https://hooks.slack.com/services/T/B/X',
            {'text': 'hello'},
            user=None,
            provider='slack',
        )
        self.assertFalse(ok)
        mpost.assert_not_called()

    @patch('apps.notifications.webhooks.validate_ssrf')
    @patch('apps.notifications.webhooks._validate_notification_url',
           return_value='hooks.slack.com')
    @patch('apps.notifications.webhooks.requests.post')
    def test_public_url_passes_validate_ssrf(
            self, mpost, _vnu, mock_validate_ssrf):
        mpost.return_value = MagicMock(status_code=200)
        ok = _post_notification(
            'https://hooks.slack.com/services/T/B/X',
            {'text': 'hello'},
            user=None,
            provider='slack',
        )
        self.assertTrue(ok)
        mock_validate_ssrf.assert_called_once()
        mpost.assert_called_once()


if __name__ == '__main__':
    unittest.main()