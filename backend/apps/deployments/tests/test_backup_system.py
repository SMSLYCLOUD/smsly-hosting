import json
import os
import shutil
import tarfile
import tempfile
import unittest
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

import docker
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.deployments.models import EnvironmentVariable, Project, Service
from apps.deployments.models.backup import BackupSchedule, ServiceBackup
from apps.deployments.services.backup_service import BackupService
from apps.deployments.tasks.data.tasks_backup import cleanup_old_backups_task

User = get_user_model()


def _docker_daemon_reachable():
    if not shutil.which('docker'):
        return False
    if os.environ.get('DOCKER_HOST'):
        return True
    try:
        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False

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
        BackupSchedule.objects.create(service=self.service, enabled=True, retention_days=7)

        # Create an old valid backup (10 days old, beyond the 7-day retention)
        old_valid_backup = ServiceBackup.objects.create(
            service=self.service,
            status='COMPLETED',
        )
        ServiceBackup.objects.filter(id=old_valid_backup.id).update(created_at=timezone.now() - timedelta(days=10))

        # Run cleanup — the old backup is older than retention and gets deleted
        cleanup_old_backups_task()
        self.assertFalse(ServiceBackup.objects.filter(id=old_valid_backup.id).exists())

        # Create a new valid backup (within retention window)
        new_valid_backup = ServiceBackup.objects.create(
            service=self.service,
            status='COMPLETED',
        )

        # Run cleanup again — the new backup is within retention and survives
        cleanup_old_backups_task()
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

    @unittest.skipUnless(os.environ.get('DOCKER_HOST') or _docker_daemon_reachable(), 'Requires Docker daemon')
    @patch('apps.cloud.docker_client.get_docker_client')
    def test_service_backup_masks_secrets_unless_transfer_backup(self, mock_get_docker_client):
        EnvironmentVariable.objects.create(
            service=self.service,
            key='PUBLIC_VALUE',
            value='visible',
            is_secret=False,
        )
        EnvironmentVariable.objects.create(
            service=self.service,
            key='SECRET_VALUE',
            value='real-secret-value',
            is_secret=True,
        )

        docker_client = MagicMock()
        docker_client.containers.get.side_effect = docker.errors.NotFound('missing')
        mock_get_docker_client.return_value = docker_client

        backup_paths = []
        try:
            manual = BackupService().backup_service(self.service.id)
            transfer = BackupService().backup_service(
                self.service.id,
                backup_type='TRANSFER',
            )
            backup_paths.extend([manual.file_path, transfer.file_path])

            def metadata_for(backup):
                with tarfile.open(backup.file_path, 'r:gz') as tar:
                    return json.load(tar.extractfile('metadata.json'))

            manual_meta = metadata_for(manual)
            transfer_meta = metadata_for(transfer)

            manual_env = {item['key']: item['value'] for item in manual_meta['env_vars']}
            transfer_env = {item['key']: item['value'] for item in transfer_meta['env_vars']}

            self.assertEqual(manual_env['PUBLIC_VALUE'], 'visible')
            self.assertEqual(manual_env['SECRET_VALUE'], '********')
            self.assertFalse(manual_meta['secrets_included'])
            self.assertEqual(transfer_env['SECRET_VALUE'], 'real-secret-value')
            self.assertTrue(transfer_meta['secrets_included'])
        finally:
            for path in backup_paths:
                if path and os.path.exists(path):
                    os.remove(path)

    @patch('apps.deployments.services.ssh_client.SSHClient')
    def test_remote_backup_and_restore(self, mock_ssh_class):
        from apps.deployments.models.core import ManagedServer
        from apps.deployments.models.storage import Volume

        # Create a ManagedServer
        server = ManagedServer.objects.create(
            name="Remote Node",
            host="1.2.3.4",
            ssh_user="root",
            ssh_key="-----BEGIN PRIVATE KEY-----\n...",
            owner=self.user
        )

        # Set active execution target variables on service
        self.service.active_target_type = "remote"
        self.service.active_host_ip = "1.2.3.4"
        self.service.save()

        # Add a volume
        Volume.objects.create(
            service=self.service,
            name="vol-1",
            mount_path="/data"
        )

        # Mock SSH Client instance
        mock_ssh = MagicMock()
        mock_ssh.check_docker.return_value = True
        mock_ssh.exec_command.return_value = ("stdout", "stderr", 0)
        mock_ssh_class.return_value = mock_ssh

        # Mock download_file to write a valid tar.gz archive
        def mock_download(remote, local):
            temp_dir = tempfile.mkdtemp()
            metadata = {
                'service_name': self.service.name,
                'service_id': str(self.service.id),
                'deploy_type': 'DOCKER',
                'env_vars': [],
                'volumes': [{'name': 'vol-1', 'mount_path': '/data', 'filename': 'volume_vol-1.tar.gz'}]
            }
            with open(os.path.join(temp_dir, 'metadata.json'), 'w') as f:
                json.dump(metadata, f)
            with open(os.path.join(temp_dir, 'image.tar'), 'w') as f:
                f.write('dummy image')
            with open(os.path.join(temp_dir, 'volume_vol-1.tar.gz'), 'w') as f:
                f.write('dummy volume')
            with tarfile.open(local, "w:gz") as tar:
                tar.add(temp_dir, arcname="")
            shutil.rmtree(temp_dir)

        mock_ssh.download_file.side_effect = mock_download

        # Trigger remote backup
        backup = BackupService().backup_service(self.service.id)

        self.assertEqual(backup.status, 'COMPLETED')
        self.assertTrue(os.path.exists(backup.file_path))
        mock_ssh.connect.assert_called_once()

        # Trigger remote restore
        with patch('apps.deployments.tasks.deploy.helpers._resolve_provider_for_service') as mock_resolve_provider:
            provider = MagicMock()
            provider.id = uuid.uuid4()
            mock_resolve_provider.return_value = provider

            # Set server on service to trigger remote restore target resolution
            self.service.server = server
            self.service.save()

            success = BackupService().restore_service(backup.id)
            self.assertTrue(success)
            mock_ssh.upload_file.assert_called()

        if os.path.exists(backup.file_path):
            os.remove(backup.file_path)

