# pylint: disable=invalid-name
"""Regression tests for Finding #180 (TRANSFER_LOG_LIMIT configurable).

The transfer service previously hard-coded a 300KB log cap as a
module-level constant. The fix makes it operator-tunable via
``settings.TRANSFER_LOG_LIMIT`` with a default of 100KB
(``100 * 1024``), so deployments with chatty pipelines can dial
the cap down (to keep DB rows small) or up (forensics).
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.deployments.models.transfer import ServerTransfer
from apps.deployments.services.transfer_service import (
    TRANSFER_LOG_LIMIT,
    ServerTransferService,
    get_transfer_log_limit,
)

User = get_user_model()


def _make_transfer():
    user = User.objects.create_user(
        username="f180-user", password="x", email="u@e.com",
    )
    return ServerTransfer.objects.create(
        owner=user,
        transfer_type="SERVICE",
        source_server_ip="10.0.0.1",
        target_server_ip="10.0.0.2",
    )


class TransferLogLimitSettingTests(TestCase):
    """The log cap is read from ``settings.TRANSFER_LOG_LIMIT``."""

    def test_default_constant_is_100kb(self):
        self.assertEqual(TRANSFER_LOG_LIMIT, 100 * 1024)

    def test_default_lookup_is_100kb_when_setting_unset(self):
        with override_settings():
            from django.conf import settings
            if "TRANSFER_LOG_LIMIT" in settings.__dict__:
                del settings.TRANSFER_LOG_LIMIT
        self.assertEqual(get_transfer_log_limit(), 100 * 1024)

    def test_setting_overrides_default(self):
        with override_settings(TRANSFER_LOG_LIMIT=4096):
            self.assertEqual(get_transfer_log_limit(), 4096)

    def test_setting_can_be_increased(self):
        with override_settings(TRANSFER_LOG_LIMIT=2_000_000):
            self.assertEqual(get_transfer_log_limit(), 2_000_000)


class TransferLogTruncationRespectsSettingTests(TestCase):
    """``_log`` truncates the combined log text using the configured cap."""

    def test_log_under_cap_is_not_truncated(self):
        transfer = _make_transfer()
        svc = ServerTransferService(transfer)
        with override_settings(TRANSFER_LOG_LIMIT=10000):
            svc._log("hello world")
        transfer.refresh_from_db()
        self.assertIn("hello world", transfer.logs)
        self.assertNotIn("truncated", transfer.logs.lower())

    def test_log_over_cap_truncates_marker_present(self):
        transfer = _make_transfer()
        svc = ServerTransferService(transfer)
        with override_settings(TRANSFER_LOG_LIMIT=120):
            svc._log("a" * 400)
        transfer.refresh_from_db()
        self.assertIn("truncated", transfer.logs.lower())
