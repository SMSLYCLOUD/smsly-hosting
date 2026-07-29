# pylint: disable=invalid-name
"""Regression tests for Finding #147
(``ServerTransferService._uploaded_remote_backup_path`` is never
cleared on interrupt).

The pipeline uploads a (possibly decrypted) source backup to the
target at ``/tmp/<name>`` and stashes the resulting path on
``self._uploaded_remote_backup_path`` so the restore step can find
it. Before the fix, if the upload step raised mid-way (or the worker
process died), the file on the target was never deleted — a fresh
backup could be uploaded on the next run, while the orphaned
/decrypted backup lingered on the target's ``/tmp/``.

The fix wraps the body of ``_upload()`` in a ``try/except`` that
calls ``_cleanup_uploaded_remote_backup()`` on any exception. The
cleanup helper:

  * removes the local file when the target is local;
  * runs ``rm -f <path>`` on the target when the target is remote;
  * resets ``self._uploaded_remote_backup_path`` to ``None`` so
    ``_restore()`` does not point at a file that no longer exists.
"""

import os
import tempfile
from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Service
from apps.deployments.models.transfer import ServerTransfer
from apps.deployments.services import transfer_service

User = get_user_model()


def _make_transfer(user, local_path):
    return ServerTransfer.objects.create(
        owner=user,
        transfer_type="SERVICE",
        source_server_ip="10.0.0.10",
        target_server_ip="10.0.0.20",
        source_ssh_key="fake",
    )


class Finding147RemoteBackupCleanupTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="fix147", email="fix147@example.com", password="x",
        )
        self.service = Service.objects.create(
            owner=self.user, name="fix147-svc",
        )
        self.transfer = _make_transfer(self.user, "/tmp/whatever")

    def test_helper_clears_attribute(self):
        svc = transfer_service.ServerTransferService(self.transfer)
        svc._uploaded_remote_backup_path = "/tmp/leak.bin"
        svc.ssh = MagicMock()
        svc._target_is_local = lambda: False

        svc._cleanup_uploaded_remote_backup()

        self.assertIsNone(svc._uploaded_remote_backup_path)
        svc.ssh.exec_command.assert_called_once()
        joined = " ".join(str(c) for c in svc.ssh.exec_command.call_args_list)
        self.assertIn("rm -f", joined)
        self.assertIn("/tmp/leak.bin", joined)

    def test_helper_removes_local_file_when_target_is_local(self):
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz")
        tmp.write(b"data")
        tmp.close()
        try:
            svc = transfer_service.ServerTransferService(self.transfer)
            svc._uploaded_remote_backup_path = tmp.name
            svc.ssh = MagicMock()
            svc._target_is_local = lambda: True

            svc._cleanup_uploaded_remote_backup()

            self.assertFalse(os.path.exists(tmp.name))
            self.assertIsNone(svc._uploaded_remote_backup_path)
        finally:
            if os.path.exists(tmp.name):
                os.remove(tmp.name)

    def test_upload_failure_triggers_cleanup(self):
        """If ``ssh.upload_file`` raises, the helper must be called
        and ``_uploaded_remote_backup_path`` must end up cleared."""
        from apps.deployments.models.backup import ServiceBackup

        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".tar.gz",
        ) as tmp:
            tmp.write(b"data")
            local_path = tmp.name

        try:
            backup = ServiceBackup.objects.create(
                service=self.service,
                file_path=local_path,
                backup_type="MANUAL",
            )
            self.transfer.source_backup = backup
            self.transfer.save()

            svc = transfer_service.ServerTransferService(self.transfer)
            svc.ssh = MagicMock()
            svc.ssh.upload_file = MagicMock(side_effect=RuntimeError("boom"))
            svc._target_is_local = lambda: False
            svc._update = MagicMock()
            svc._log = MagicMock()

            with self.assertRaises(RuntimeError):
                svc._upload()

            self.assertIsNone(svc._uploaded_remote_backup_path)
        finally:
            if os.path.exists(local_path):
                os.remove(local_path)

    def test_cleanup_is_idempotent_when_no_path(self):
        svc = transfer_service.ServerTransferService(self.transfer)
        svc.ssh = MagicMock()
        svc._uploaded_remote_backup_path = None
        svc._cleanup_uploaded_remote_backup()
        svc.ssh.exec_command.assert_not_called()
        self.assertIsNone(svc._uploaded_remote_backup_path)
