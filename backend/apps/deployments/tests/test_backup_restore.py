import contextlib
import json
import os
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from apps.deployments.models import EnvironmentVariable, Project, Service
from apps.deployments.models.backup import BackupSchedule, ServerBackup, ServiceBackup
from apps.deployments.services.backup_service import BackupService

User = get_user_model()

class BackupRestoreTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="test", password="pwd")
        self.project = Project.objects.create(name="Test Proj", owner=self.user)
        self.service = Service.objects.create(name="test-service", owner=self.user, project=self.project)
        self.service_backup = ServiceBackup.objects.create(service=self.service, status="COMPLETED")
        self.server_backup = ServerBackup.objects.create(status="COMPLETED")

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch('apps.deployments.tasks.restore_service_backup_task.delay')
    def test_restore_service_requires_confirmation(self, mock_task):
        url = reverse('backup-restore', args=[self.service_backup.id])

        # Request without confirm=true
        response = self.client.post(url, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn("Explicit confirmation required", str(response.data))
        mock_task.assert_not_called()

        # Request with confirm=true — the view also attempts a synchronous
        # pre-restore safety snapshot; with no Docker available in tests it
        # fails and the view returns 422 unless force=true. Test both paths.
        response = self.client.post(url, {"confirm": True}, format='json')
        self.assertEqual(response.status_code, 422)
        self.assertIn("safety snapshot", str(response.data).lower())
        mock_task.assert_not_called()

        # force=true proceeds without the safety snapshot
        response = self.client.post(url, {"confirm": True, "force": True}, format='json')
        self.assertEqual(response.status_code, 200)
        mock_task.assert_called_once()

    @patch('apps.deployments.tasks.restore_server_backup_task.delay')
    def test_restore_server_requires_confirmation(self, mock_task):
        url = reverse('server-backup-restore', args=[self.server_backup.id])

        # Admin access required for server backups
        admin_user = User.objects.create_superuser("admin", "admin@test.com", "pwd")
        self.client.force_authenticate(user=admin_user)

        # Request without confirm=true
        response = self.client.post(url, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn("Explicit confirmation required", str(response.data))
        mock_task.assert_not_called()

        # Request with confirm=true
        response = self.client.post(url, {"confirm": True}, format='json')
        self.assertEqual(response.status_code, 200)
        mock_task.assert_called_once()

    def test_server_backup_download_supports_byte_ranges(self):
        admin_user = User.objects.create_superuser("download-admin", "download@test.com", "pwd")
        Token.objects.create(user=admin_user)
        payload = b"0123456789" * 1024
        backup_file = tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz")
        backup_file.write(payload)
        backup_file.close()
        try:
            backup = ServerBackup.objects.create(
                status="COMPLETED",
                file_path=backup_file.name,
                size_bytes=len(payload),
            )
            from django.core import signing
            signed = signing.TimestampSigner().sign_object({'pk': str(backup.id), 'ts': 0})
            url = reverse('server-backup-download', args=[backup.id]) + f"?signed={signed}"
            response = self.client.get(url, HTTP_RANGE="bytes=10-19")

            self.assertEqual(response.status_code, 206)
            self.assertEqual(response["Content-Range"], f"bytes 10-19/{len(payload)}")
            self.assertEqual(response["Accept-Ranges"], "bytes")
            self.assertEqual(b"".join(response.streaming_content), payload[10:20])
        finally:
            if os.path.exists(backup_file.name):
                os.remove(backup_file.name)

    @patch('apps.deployments.tasks.create_service_backup_task.delay')
    def test_create_service_backup_rejects_foreign_service(self, mock_task):
        other = User.objects.create_user(username="other", password="pwd")
        foreign_service = Service.objects.create(name="foreign-service", owner=other)

        response = self.client.post(
            "/api/v1/backups/",
            {"service": str(foreign_service.id)},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        mock_task.assert_not_called()

    def test_nested_service_backups_are_scoped_to_service(self):
        other_service = Service.objects.create(name="other-owned-service", owner=self.user)
        ServiceBackup.objects.create(service=other_service, status="COMPLETED")

        response = self.client.get(f"/api/v1/services/{self.service.id}/backups/")

        self.assertEqual(response.status_code, 200)
        data = response.data if isinstance(response.data, list) else response.data.get("results", [])
        self.assertEqual([item["id"] for item in data], [str(self.service_backup.id)])

    def test_backup_schedule_rejects_foreign_service(self):
        other = User.objects.create_user(username="schedule-other", password="pwd")
        foreign_service = Service.objects.create(name="foreign-schedule-service", owner=other)

        response = self.client.post(
            "/api/v1/backup-schedules/",
            {
                "service": str(foreign_service.id),
                "cron_expression": "0 3 * * *",
                "retention_days": 7,
                "enabled": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(BackupSchedule.objects.filter(service=foreign_service).exists())

    def test_encrypted_service_backup_restore_decrypts_archive(self):
        key = Fernet.generate_key().decode()
        backup_dir = tempfile.mkdtemp()
        archive_path = os.path.join(backup_dir, "service.tar.gz")
        encrypted_path = None
        try:
            metadata = {
                "service_name": self.service.name,
                "env_vars": [
                    {"key": "RESTORED_VALUE", "value": "from-encrypted-backup", "is_secret": False},
                ],
                "volumes": [],
            }
            metadata_path = os.path.join(backup_dir, "metadata.json")
            with open(metadata_path, "w", encoding="utf-8") as fh:
                json.dump(metadata, fh)
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(metadata_path, arcname="metadata.json")

            with patch.dict(os.environ, {"BACKUP_ENCRYPTION_KEY": key}):
                encrypted_path = BackupService()._maybe_encrypt(archive_path)
                backup = ServiceBackup.objects.create(
                    service=self.service,
                    status="COMPLETED",
                    file_path=encrypted_path,
                )
                BackupService().restore_service(backup.id, requesting_user_id=self.user.id)

            env = EnvironmentVariable.objects.get(service=self.service, key="RESTORED_VALUE")
            self.assertEqual(env.value, "from-encrypted-backup")
        finally:
            for path in [archive_path, encrypted_path]:
                if path and os.path.exists(path):
                    os.remove(path)
            if os.path.exists(backup_dir):
                with contextlib.suppress(OSError):
                    os.remove(os.path.join(backup_dir, "metadata.json"))
                os.rmdir(backup_dir)
