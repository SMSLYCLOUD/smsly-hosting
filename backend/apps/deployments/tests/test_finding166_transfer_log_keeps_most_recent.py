"""Regression test for Finding #166 (TRANSFER_LOG_LIMIT keeps most recent).

The truncation in ``ServerTransferService._log`` uses
``combined[-TRANSFER_LOG_LIMIT:]`` to slice the most recent
characters off the end of the accumulated log text. This test
asserts that the *most recent* log content is preserved (not the
oldest) when the cap is exceeded.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from apps.deployments.models.transfer import ServerTransfer
from apps.deployments.services.transfer_service import (
    TRANSFER_LOG_LIMIT,
    ServerTransferService,
)

User = get_user_model()


def _make_transfer():
    user = User.objects.create_user(
        username="f166-user", password="x", email="u@e.com",
    )
    return ServerTransfer.objects.create(
        owner=user,
        transfer_type="SERVICE",
        source_server_ip="10.0.0.1",
        target_server_ip="10.0.0.2",
    )


class TransferLogKeepsMostRecentTests(TestCase):
    """Finding #166: the slice must retain the most recent entries."""

    def test_truncation_keeps_most_recent_line(self):
        transfer = _make_transfer()
        svc = ServerTransferService(transfer)
        small_cap = 400
        with override_settings(TRANSFER_LOG_LIMIT=small_cap):
            svc._log("OLDEST_MARKER_AAA " + "A" * 200)
            svc._log("MIDDLE_MARKER_BBB " + "B" * 200)
            svc._log("NEWEST_MARKER_CCC " + "C" * 200)
        transfer.refresh_from_db()
        self.assertIn("NEWEST_MARKER_CCC", transfer.logs)
        self.assertNotIn("OLDEST_MARKER_AAA", transfer.logs)

    def test_truncation_marker_present_above_cap(self):
        transfer = _make_transfer()
        svc = ServerTransferService(transfer)
        with override_settings(TRANSFER_LOG_LIMIT=200):
            svc._log("X" * 600)
        transfer.refresh_from_db()
        self.assertIn("truncated", transfer.logs.lower())

    def test_default_log_limit_is_100kb(self):
        self.assertEqual(TRANSFER_LOG_LIMIT, 100 * 1024)
