"""Server backup restore response must warn that the DB dump was not restored."""
import logging
import os
import tarfile
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models.backup import ServerBackup
from apps.deployments.services.backup_service import BackupService

User = get_user_model()


def _build_tarball_with_db_dump() -> str:
    """Create a real .tar.gz with a db_dump.sql entry so the warning fires."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz")
    tmp.close()
    with tarfile.open(tmp.name, "w:gz") as tar:
        import io
        info = tarfile.TarInfo(name="db_dump.sql")
        data = b"-- fake pg_dump output for testing\n"
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return tmp.name


class ServerBackupResponseWarningTest(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="srv-admin", password="x",
        )
        self.archive_path = _build_tarball_with_db_dump()
        self.addCleanup(self._cleanup_archive)
        self.backup = ServerBackup.objects.create(
            status="COMPLETED",
            file_path=self.archive_path,
        )

    def _cleanup_archive(self):
        if os.path.exists(self.archive_path):
            os.remove(self.archive_path)

    def test_restore_server_logs_db_dump_warning(self):
        logger = logging.getLogger("apps.deployments.services.backup_service")
        with self.assertLogs(logger, level="WARNING") as captured:
            with patch.object(BackupService, "_restore_service_from_file"):
                BackupService().restore_server(backup_id=str(self.backup.id))
        joined = "\n".join(captured.output)
        self.assertIn("db_dump.sql was NOT restored", joined)
        self.assertIn("DISASTER_RECOVERY", joined)

    def test_server_backup_viewset_restore_includes_warning(self):
        from django.urls import reverse
        from rest_framework.test import APIClient

        client = APIClient()
        client.force_authenticate(user=self.admin)

        with patch(
            "apps.deployments.tasks.restore_server_backup_task.delay",
        ):
            response = client.post(
                reverse("server-backup-restore", args=[self.backup.id]),
                {"confirm": "true"},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("warning", response.data)
        self.assertIn("Manual psql restore required", response.data["warning"])
        self.assertIn("DISASTER_RECOVERY", response.data["warning"])
