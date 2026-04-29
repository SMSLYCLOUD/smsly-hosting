import os
import uuid
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from unittest.mock import patch, MagicMock
from apps.deployments.models import Service, Project
from apps.deployments.models_backup import ServiceBackup, BackupSchedule
from apps.deployments.tasks import cleanup_old_backups_task
from apps.deployments.services.backup_service import BackupService
from django.contrib.auth import get_user_model

User = get_user_model()

class BackupSystemTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="test", password="pwd")
        self.project = Project.objects.create(name="Test Proj", owner=self.user)
        self.service = Service.objects.create(
            name="test-service",
            owner=self.user,
            project=self.project
        )

    def test_cleanup_old_backups_task_keeps_latest_valid_backup(self):
        schedule = BackupSchedule.objects.create(service=self.service, enabled=True, retention_days=7)

        # Create an old valid backup
        old_valid_backup = ServiceBackup.objects.create(
            service=self.service,
            status='COMPLETED',
            created_at=timezone.now() - timedelta(days=10)
        )
        # Update the created_at directly since auto_now_add is set
        ServiceBackup.objects.filter(id=old_valid_backup.id).update(created_at=timezone.now() - timedelta(days=10))

        # Run cleanup
        cleanup_old_backups_task()

        # Should NOT be deleted because it is the ONLY valid backup
        self.assertTrue(ServiceBackup.objects.filter(id=old_valid_backup.id).exists())

        # Create a new valid backup
        new_valid_backup = ServiceBackup.objects.create(
            service=self.service,
            status='COMPLETED'
        )

        # Run cleanup again
        cleanup_old_backups_task()

        # NOW the old backup should be deleted, because there is a newer valid backup
        self.assertFalse(ServiceBackup.objects.filter(id=old_valid_backup.id).exists())
        self.assertTrue(ServiceBackup.objects.filter(id=new_valid_backup.id).exists())

    @patch('os.remove')
    @patch('os.path.exists', return_value=True)
    def test_prune_old_backups_keeps_latest_valid_backup(self, mock_exists, mock_remove):
        # We test BackupService._prune_old_backups
        # Set retain to 1 for this test
        with patch.dict('os.environ', {'BACKUP_RETENTION_COUNT': '1'}):
            # Create two backups
            backup1 = ServiceBackup.objects.create(service=self.service, status='FAILED')
            ServiceBackup.objects.filter(id=backup1.id).update(created_at=timezone.now() - timedelta(days=2))

            backup2 = ServiceBackup.objects.create(service=self.service, status='COMPLETED')
            ServiceBackup.objects.filter(id=backup2.id).update(created_at=timezone.now() - timedelta(days=1))

            # Trigger prune
            BackupService._prune_old_backups(ServiceBackup, self.service.id)

            # The failed one should be deleted since retain=1 and backup2 is the most recent
            self.assertFalse(ServiceBackup.objects.filter(id=backup1.id).exists())
            self.assertTrue(ServiceBackup.objects.filter(id=backup2.id).exists())
