"""
Tests for ``encryption_key`` parameter on backup tasks.

``create_service_backup_task`` and ``create_server_backup_task`` now
accept an optional ``encryption_key`` that temporarily overrides
``BACKUP_ENCRYPTION_KEY`` in the environment for the duration of the
backup, then restores the original value.

This lets operators set a per-backup key (e.g. from a schedule's
``encryption_key`` field) without interfering with other concurrent
backups.
"""
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Project, Service
from apps.deployments.services.backup_service import BackupService
from apps.deployments.tasks.data.tasks_backup import (
    create_server_backup_task,
    create_service_backup_task,
)

User = get_user_model()


class TaskEncryptionKeyParamTest(TestCase):
    """encryption_key env override in create_service_backup_task."""

    def setUp(self):
        self.user = User.objects.create_user(username="keytask", password="x")
        self.project = Project.objects.create(name="Key Task", owner=self.user)
        self.service = Service.objects.create(
            name="key-task-svc",
            owner=self.user,
            project=self.project,
        )

    @patch('apps.cloud.docker_client.get_docker_client')
    @patch.object(BackupService, 'backup_service')
    def test_task_sets_encryption_key_in_env(self, mock_backup, mock_get_docker):
        """When encryption_key is provided, BACKUP_ENCRYPTION_KEY is set
        in the environment during backup_service() and restored after."""
        from cryptography.fernet import Fernet
        original_key = Fernet.generate_key().decode()
        per_backup_key = Fernet.generate_key().decode()

        with patch.dict(os.environ, {"BACKUP_ENCRYPTION_KEY": original_key}, clear=False):
            create_service_backup_task(
                self.service.id,
                backup_type="MANUAL",
                encryption_key=per_backup_key,
            )
            # After the task, the env should be restored
            self.assertEqual(os.environ.get("BACKUP_ENCRYPTION_KEY"), original_key)
            # The backup_service should have been called with the per-backup key active
            mock_backup.assert_called_once()

    @patch('apps.cloud.docker_client.get_docker_client')
    @patch.object(BackupService, 'backup_service')
    def test_task_restores_empty_env(self, mock_backup, mock_get_docker):
        """When BACKUP_ENCRYPTION_KEY was not set before, it's removed
        from the env after the task completes."""
        from cryptography.fernet import Fernet
        per_backup_key = Fernet.generate_key().decode()

        # Ensure no BACKUP_ENCRYPTION_KEY in env
        with patch.dict(os.environ, {}, clear=True):
            create_service_backup_task(
                self.service.id,
                backup_type="MANUAL",
                encryption_key=per_backup_key,
            )
            # Should have been removed after task
            self.assertNotIn("BACKUP_ENCRYPTION_KEY", os.environ)

    @patch('apps.cloud.docker_client.get_docker_client')
    @patch.object(BackupService, 'backup_server')
    def test_server_task_sets_encryption_key_in_env(self, mock_backup, mock_get_docker):
        """Same env override/restore for create_server_backup_task."""
        from cryptography.fernet import Fernet
        per_backup_key = Fernet.generate_key().decode()

        from apps.deployments.models.backup import ServerBackup
        sb = ServerBackup.objects.create(status="PENDING")

        original_key = os.environ.get("BACKUP_ENCRYPTION_KEY", "")
        create_server_backup_task(backup_id=sb.id, encryption_key=per_backup_key)
        self.assertEqual(
            os.environ.get("BACKUP_ENCRYPTION_KEY", ""),
            original_key,
        )

    @patch('apps.cloud.docker_client.get_docker_client')
    @patch.object(BackupService, 'backup_service')
    def test_task_without_key_leaves_env_unchanged(self, mock_backup, mock_get_docker):
        """When no encryption_key is provided, env is unchanged."""
        with patch.dict(os.environ, {"BACKUP_ENCRYPTION_KEY": "should-stay"}, clear=False):
            create_service_backup_task(self.service.id, backup_type="MANUAL")
            self.assertEqual(os.environ.get("BACKUP_ENCRYPTION_KEY"), "should-stay")
