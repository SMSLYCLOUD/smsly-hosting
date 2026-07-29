# pylint: disable=invalid-name
"""Regression tests for Finding #193
(``ServerTransferService.execute`` state-machine atomicity).

The transfer pipeline performs several status transitions
(``PREPARING`` → ``UPLOADING`` → ``RESTORING`` → ``DNS_CUTOVER`` →
``VERIFYING`` → ``COMPLETED``) plus terminal ``FAILED`` /
``ROLLED_BACK`` writes. Before the fix each transition was a bare
``self.transfer.save(update_fields=['status'])`` — a concurrent
controller (e.g. an admin POSTing ``/cancel/``) could clobber the
status mid-run, leaving the row in an inconsistent state.

The fix:

  * introduces a ``_set_status()`` helper that wraps the
    read-modify-write in ``transaction.atomic`` with
    ``select_for_update``;
  * makes ``_complete()`` and ``_handle_failure()`` also atomic
    so the final state-transition + credential clearing + error
    message write are committed together;
  * keeps the long-running SSH / Caddy / Docker side effects
    outside the DB lock so the lock is held only for the duration
    of the single update.
"""

import inspect
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Service
from apps.deployments.models.transfer import ServerTransfer
from apps.deployments.services import transfer_service

User = get_user_model()


class Finding193StateMachineAtomicityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="fix193", email="fix193@example.com", password="x",
        )
        self.service = Service.objects.create(
            owner=self.user, name="fix193-svc",
        )
        self.transfer = ServerTransfer.objects.create(
            owner=self.user,
            transfer_type="SERVICE",
            service=self.service,
            source_server_ip="10.0.0.10",
            target_server_ip="10.0.0.20",
            source_ssh_key="fake",
        )

    def test_set_status_uses_select_for_update(self):
        from django.db.models import QuerySet

        original = QuerySet.select_for_update
        lock_mock = MagicMock()

        def _fake(self, *args, **kwargs):
            lock_mock(self, *args, **kwargs)
            return original(self, *args, **kwargs)

        svc = transfer_service.ServerTransferService(self.transfer)
        with patch("django.db.models.QuerySet.select_for_update", new=_fake):
            svc._set_status("UPLOADING")

        self.assertGreaterEqual(lock_mock.call_count, 1)
        self.transfer.refresh_from_db()
        self.assertEqual(self.transfer.status, "UPLOADING")

    def test_handle_failure_atomic_clears_creds_and_status(self):
        svc = transfer_service.ServerTransferService(self.transfer)
        svc._cleanup_uploaded_remote_backup = MagicMock()
        svc._log = MagicMock()
        svc.ssh = MagicMock()
        svc._target_is_local = lambda: False

        svc._handle_failure(RuntimeError("boom"))

        self.transfer.refresh_from_db()
        self.assertEqual(self.transfer.status, "FAILED")
        self.assertEqual(self.transfer.target_ssh_key, "")
        self.assertEqual(self.transfer.target_ssh_password, "")
        self.assertEqual(self.transfer.source_ssh_key, "")
        self.assertEqual(self.transfer.source_ssh_password, "")
        self.assertIn("boom", self.transfer.error_message)

    def test_complete_atomic_sets_completed_status(self):
        svc = transfer_service.ServerTransferService(self.transfer)
        svc._cleanup_uploaded_remote_backup = MagicMock()
        svc._log = MagicMock()
        svc._update = MagicMock()
        svc._regenerate_master_caddyfile = MagicMock()
        svc._remap_service_domain_for_target = MagicMock(return_value=[])

        svc._complete()

        self.transfer.refresh_from_db()
        self.assertEqual(self.transfer.status, "COMPLETED")
        self.assertIsNotNone(self.transfer.completed_at)

    def test_execute_uses_set_status_helper(self):
        """Static check: the execute() body should use the
        ``_set_status`` helper rather than writing ``status`` directly."""
        src = inspect.getsource(transfer_service.ServerTransferService.execute)
        self.assertIn("_set_status(", src)
        self.assertNotIn("self.transfer.save(update_fields=['status'])", src)
