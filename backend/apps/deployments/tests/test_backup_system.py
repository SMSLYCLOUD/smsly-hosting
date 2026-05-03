import os
import uuid
import tempfile
from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from unittest.mock import patch, MagicMock
from cryptography.fernet import Fernet
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

    def test_chunked_backup_encryption_round_trip(self):
        key = Fernet.generate_key().decode()
        payload = (b"0123456789abcdef" * 128 * 1024) + b"tail"
        source = tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz")
        source_path = source.name
        source.write(payload)
        source.close()
        encrypted_path = None
        decrypted_path = None
        try:
            with patch.dict('os.environ', {
                'BACKUP_ENCRYPTION_KEY': key,
                'BACKUP_CRYPTO_CHUNK_SIZE': str(128 * 1024),
            }):
                encrypted_path = BackupService()._maybe_encrypt(source_path)
            self.assertTrue(encrypted_path.endswith(".enc"))
            self.assertTrue(os.path.exists(encrypted_path))

            decrypted_path = BackupService.decrypt_backup(encrypted_path, key)
            with open(decrypted_path, "rb") as f:
                self.assertEqual(f.read(), payload)
        finally:
            for path in [source_path, encrypted_path, decrypted_path]:
                if path and os.path.exists(path):
                    os.remove(path)

    def test_legacy_fernet_backup_decrypt_round_trip(self):
        key = Fernet.generate_key()
        payload = (b"legacy-backup" * 256 * 1024) + b"end"
        encrypted = Fernet(key).encrypt(payload)
        source = tempfile.NamedTemporaryFile(delete=False, suffix=".tar.gz.enc")
        source_path = source.name
        source.write(encrypted)
        source.close()
        decrypted_path = None
        try:
            decrypted_path = BackupService.decrypt_backup(source_path, key.decode())
            with open(decrypted_path, "rb") as f:
                self.assertEqual(f.read(), payload)
        finally:
            for path in [source_path, decrypted_path]:
                if path and os.path.exists(path):
                    os.remove(path)
