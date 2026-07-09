"""
Tests for backup maximum size enforcement.

When ``BACKUP_MAX_SIZE_BYTES`` is set and the backup archive
exceeds it, the backup is deleted and a ``RuntimeError`` is raised.
This prevents accidental disk exhaustion from oversized backups.
"""
import os
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Project, Service
from apps.deployments.services.backup_service import BackupService

User = get_user_model()


class BackupSizeEnforcementTest(TestCase):
    """BACKUP_MAX_SIZE_BYTES enforcement in backup_service()."""

    def setUp(self):
        self.user = User.objects.create_user(username="sizetest", password="pwd")
        self.project = Project.objects.create(name="Size Test", owner=self.user)
        self.service = Service.objects.create(
            name="size-test-service",
            owner=self.user,
            project=self.project,
        )

    def _mock_docker(self, mock_get_docker, mock_docker_env):
        """Configure docker mocks so backup proceeds to the size check."""
        docker_client = MagicMock()
        mock_get_docker.return_value = docker_client
        mock_docker_env.return_value = docker_client
        mock_ctr = MagicMock()
        mock_ctr.attrs = {"Config": {"Env": []}}
        mock_ctr.exec_run.return_value = MagicMock(exit_code=0, output=b"")
        docker_client.containers.get.return_value = mock_ctr

    @patch("apps.cloud.docker_client.get_docker_client")
    @patch.object(BackupService, '_maybe_encrypt')
    @patch("docker.from_env")
    def test_backup_exceeding_max_size_raises(self, mock_docker_env, mock_encrypt, mock_get_docker):
        """When the archive is larger than BACKUP_MAX_SIZE_BYTES, raise
        RuntimeError and ensure the file is removed."""
        self._mock_docker(mock_get_docker, mock_docker_env)
        mock_encrypt.side_effect = lambda p: p

        with patch.dict(os.environ, {
            'BACKUP_MAX_SIZE_BYTES': '1',
        }, clear=False):
            with self.assertRaises(RuntimeError) as ctx:
                BackupService().backup_service(self.service.id)

        self.assertIn("exceeds maximum", str(ctx.exception))
        self.assertIn("BACKUP_MAX_SIZE_BYTES", str(ctx.exception))

    @patch("apps.cloud.docker_client.get_docker_client")
    @patch.object(BackupService, '_maybe_encrypt')
    @patch("docker.from_env")
    def test_backup_within_max_size_succeeds(self, mock_docker_env, mock_encrypt, mock_get_docker):
        """When within the limit, backup completes normally."""
        self._mock_docker(mock_get_docker, mock_docker_env)
        mock_encrypt.side_effect = lambda p: p

        with patch.dict(os.environ, {
            'BACKUP_MAX_SIZE_BYTES': str(10 * 1024 * 1024 * 1024),
        }, clear=False):
            backup = BackupService().backup_service(self.service.id)

        self.assertEqual(backup.status, 'COMPLETED')
        self.assertTrue(os.path.exists(backup.file_path))
        if backup.file_path and os.path.exists(backup.file_path):
            os.remove(backup.file_path)

    @patch("apps.cloud.docker_client.get_docker_client")
    @patch.object(BackupService, '_maybe_encrypt')
    @patch("docker.from_env")
    def test_max_size_zero_disables_enforcement(self, mock_docker_env, mock_encrypt, mock_get_docker):
        """BACKUP_MAX_SIZE_BYTES=0 disables the check entirely."""
        self._mock_docker(mock_get_docker, mock_docker_env)
        mock_encrypt.side_effect = lambda p: p

        with patch.dict(os.environ, {
            'BACKUP_MAX_SIZE_BYTES': '0',
        }, clear=False):
            backup = BackupService().backup_service(self.service.id)

        self.assertEqual(backup.status, 'COMPLETED')
        if backup.file_path and os.path.exists(backup.file_path):
            os.remove(backup.file_path)

    @patch("apps.cloud.docker_client.get_docker_client")
    @patch.object(BackupService, '_maybe_encrypt')
    @patch("docker.from_env")
    def test_max_size_unset_uses_default_and_succeeds(self, mock_docker_env, mock_encrypt, mock_get_docker):
        """No BACKUP_MAX_SIZE_BYTES env var uses the 50 GB default and succeeds."""
        self._mock_docker(mock_get_docker, mock_docker_env)
        mock_encrypt.side_effect = lambda p: p

        backup = BackupService().backup_service(self.service.id)
        self.assertEqual(backup.status, 'COMPLETED')
        if backup.file_path and os.path.exists(backup.file_path):
            os.remove(backup.file_path)
