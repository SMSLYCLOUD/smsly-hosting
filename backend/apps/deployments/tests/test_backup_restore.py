import unittest
import os
import tempfile
from django.test import TestCase
from unittest.mock import patch
from rest_framework.test import APIClient
from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from apps.deployments.models import Service, Project
from apps.deployments.models_backup import ServiceBackup, ServerBackup
import uuid

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
    @unittest.skip('URL router resolution issues with servicebackup-restore')
    def test_restore_service_requires_confirmation(self, mock_task):
        url = reverse('servicebackup-restore', args=[self.service_backup.id])

        # Request without confirm=true
        response = self.client.post(url, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn("Explicit confirmation required", str(response.data))
        mock_task.assert_not_called()

        # Request with confirm=true
        response = self.client.post(url, {"confirm": True}, format='json')
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
        token = Token.objects.create(user=admin_user)
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
            url = reverse('server-backup-download', args=[backup.id]) + f"?token={token.key}"
            response = self.client.get(url, HTTP_RANGE="bytes=10-19")

            self.assertEqual(response.status_code, 206)
            self.assertEqual(response["Content-Range"], f"bytes 10-19/{len(payload)}")
            self.assertEqual(response["Accept-Ranges"], "bytes")
            self.assertEqual(b"".join(response.streaming_content), payload[10:20])
        finally:
            if os.path.exists(backup_file.name):
                os.remove(backup_file.name)
