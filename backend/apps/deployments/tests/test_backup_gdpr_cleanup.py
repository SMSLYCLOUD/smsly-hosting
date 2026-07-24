"""GDPR right-to-erasure: backup files and rows are removed when a user is deleted."""
import os
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Project, Service
from apps.deployments.models.backup import (
    BackupSchedule,
    ServerBackup,
    ServiceBackup,
)
from apps.deployments.services.backup_service import purge_user_backups
from apps.deployments.tasks.data.tasks_backup import purge_user_backups_task

User = get_user_model()


class BackupGDPRCleanupTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="gdpr-owner", password="x",
        )
        self.project = Project.objects.create(
            name="GDPR Proj", owner=self.user,
        )
        self.service = Service.objects.create(
            name="gdpr-service",
            owner=self.user,
            project=self.project,
        )

    def _make_tarball(self) -> str:
        fd, path = tempfile.mkstemp(prefix="gdpr_backup_", suffix=".tar.gz")
        os.close(fd)
        with open(path, "wb") as fh:
            fh.write(b"tarball-bytes-for-test")
        return path

    def test_purge_user_backups_removes_files_and_rows(self):
        path_a = self._make_tarball()
        path_b = self._make_tarball()
        try:
            sb_a = ServiceBackup.objects.create(
                service=self.service, status="COMPLETED", file_path=path_a,
            )
            sb_b = ServiceBackup.objects.create(
                service=self.service, status="COMPLETED", file_path=path_b,
            )

            counters = purge_user_backups(self.user.id)

            self.assertFalse(os.path.exists(path_a))
            self.assertFalse(os.path.exists(path_b))
            self.assertFalse(
                ServiceBackup.objects.filter(id__in=[sb_a.id, sb_b.id]).exists()
            )
            self.assertEqual(counters["service_backup_files_deleted"], 2)
            self.assertEqual(counters["service_backups_deleted"], 2)
        finally:
            for p in (path_a, path_b):
                if os.path.exists(p):
                    os.remove(p)

    def test_purge_user_backups_removes_server_backups_for_owned_services(self):
        service_path = self._make_tarball()
        server_path = self._make_tarball()
        try:
            ServiceBackup.objects.create(
                service=self.service,
                status="COMPLETED",
                file_path=service_path,
            )
            ServerBackup.objects.create(
                status="COMPLETED",
                file_path=server_path,
                services_included=[str(self.service.id)],
            )

            counters = purge_user_backups(self.user.id)

            self.assertFalse(os.path.exists(service_path))
            self.assertFalse(os.path.exists(server_path))
            self.assertGreaterEqual(
                counters.get("server_backup_files_deleted", 0), 1,
            )
            self.assertGreaterEqual(
                counters.get("server_backups_deleted", 0), 1,
            )
        finally:
            for p in (service_path, server_path):
                if os.path.exists(p):
                    os.remove(p)

    def test_purge_user_backups_deletes_cloud_objects(self):
        path = self._make_tarball()
        try:
            BackupSchedule.objects.create(
                service=self.service,
                storage_backend="s3",
                s3_bucket="test-bucket",
                s3_region="us-east-1",
                s3_access_key="AKIA",
                s3_secret_key="secret",
            )
            ServiceBackup.objects.create(
                service=self.service, status="COMPLETED", file_path=path,
            )

            with patch(
                "apps.deployments.services.backup_service.delete_cloud_backup_object",
                return_value=True,
            ) as mock_delete:
                counters = purge_user_backups(self.user.id)

            self.assertEqual(counters["cloud_objects_deleted"], 1)
            self.assertEqual(mock_delete.call_count, 1)
            args, _kwargs = mock_delete.call_args
            self.assertEqual(args[0], "test-bucket")
            self.assertTrue(args[1].startswith("smsly-backups/"))
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_purge_user_backups_task_emits_audit_log(self):
        from apps.deployments.models.audit import AuditLog

        path = self._make_tarball()
        try:
            ServiceBackup.objects.create(
                service=self.service, status="COMPLETED", file_path=path,
            )
            counters = purge_user_backups_task.run(str(self.user.id), actor="gdpr-test")
            self.assertFalse(os.path.exists(path))
            self.assertGreater(
                counters.get("service_backup_files_deleted", 0), 0,
            )
            audit = AuditLog.objects.filter(action="USER_BACKUPS_PURGED").first()
            self.assertIsNotNone(audit)
            self.assertEqual(audit.metadata["user_id"], str(self.user.id))
            self.assertEqual(audit.actor, "gdpr-test")
        finally:
            if os.path.exists(path):
                os.remove(path)
