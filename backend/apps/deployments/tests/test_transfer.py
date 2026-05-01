# pylint: disable=invalid-name
from django.test import TestCase, override_settings
from unittest.mock import MagicMock, patch
import os
from datetime import timedelta
from django.utils import timezone
from apps.deployments.models import Service, PlatformConfig
from apps.deployments.models_transfer import ServerTransfer
from apps.deployments.services.transfer_service import ServerTransferService
from apps.deployments.models_backup import ServiceBackup, ServerBackup
from apps.deployments.models_core import ManagedServer

class ServerTransferServiceTest(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        self.user = User.objects.create(username="testuser", email="test@test.com")
        self.service = Service.objects.create(name="test-service", deploy_type='DOCKER', owner=self.user)
        self.transfer = ServerTransfer.objects.create(
            service=self.service,
            source_server_ip="1.2.3.4",
            target_server_ip="5.6.7.8",
            target_ssh_key="mock-key",
            transfer_type='SERVICE'
        )

    @patch('apps.deployments.services.transfer_service.SSHClient')
    @patch('apps.deployments.services.transfer_service.BackupService')
    def test_execute_service_transfer_success(self, MockBackupService, MockSSHClient):
        # Param 1: BackupService (Closest/Inner decorator)
        # Param 2: SSHClient (Farthest/Outer decorator)
        MockBackup, MockSSH = MockBackupService, MockSSHClient

        # Setup Mocks
        mock_ssh = MockSSH.return_value
        mock_ssh.check_docker.return_value = True

        def _exec_side_effect(cmd, *args, **kwargs):
            if "docker inspect -f '{{.State.Running}}'" in cmd:
                return "true"
            if "TRANSFER_TCP_OK" in cmd or "echo TRANSFER_TCP_OK" in cmd:
                return "TRANSFER_TCP_OK"
            return "mock_output"

        mock_ssh.exec_command.side_effect = _exec_side_effect

        mock_backup_svc = MockBackup.return_value
        mock_backup = ServiceBackup.objects.create(
            service=self.service,
            file_path='/tmp/backup.tar.gz',
            metadata={'volumes': []},
            status='COMPLETED'
        )
        mock_backup_svc.backup_service.return_value = mock_backup

        # Execute
        svc = ServerTransferService(self.transfer)
        svc.execute()

        # Verify
        self.transfer.refresh_from_db()
        self.assertEqual(self.transfer.status, 'COMPLETED')
        self.assertEqual(self.transfer.target_ssh_key, '')

        mock_ssh.connect.assert_called()
        mock_backup_svc.backup_service.assert_called_with(self.service.id)
        mock_ssh.upload_file.assert_called()

    @patch('apps.deployments.services.transfer_service.SSHClient')
    @patch('apps.deployments.services.transfer_service.BackupService')
    def test_execute_full_transfer_success(self, MockBackupService, MockSSHClient):
        MockBackup, MockSSH = MockBackupService, MockSSHClient

        self.transfer.transfer_type = 'FULL'
        self.transfer.service = None
        self.transfer.save()

        mock_ssh = MockSSH.return_value
        mock_ssh.check_docker.return_value = True
        def _exec_side_effect_full(cmd, *args, **kwargs):
            if "docker inspect -f '{{.State.Running}}'" in cmd:
                return "true"
            if "TRANSFER_TCP_OK" in cmd or "echo TRANSFER_TCP_OK" in cmd:
                return "TRANSFER_TCP_OK"
            return "mock_output"
        mock_ssh.exec_command.side_effect = _exec_side_effect_full

        import os
        os.environ['SMSLY_INSTALL_SCRIPT_SHA256'] = 'dummy-sha256'

        mock_backup_svc = MockBackup.return_value
        mock_backup = ServerBackup.objects.create(
            file_path='/tmp/server_backup.tar.gz',
            status='COMPLETED'
        )
        mock_backup_svc.backup_server.return_value = mock_backup

        svc = ServerTransferService(self.transfer)
        svc.execute()

        self.transfer.refresh_from_db()
        self.assertEqual(self.transfer.status, 'COMPLETED')

        mock_ssh.exec_command.assert_any_call("yes | /tmp/install.sh")
        mock_backup_svc.backup_server.assert_called()

    @patch('apps.deployments.services.transfer_service.SSHClient')
    def test_ssh_failure_handling(self, MockSSHClient):
        mock_ssh = MockSSHClient.return_value
        mock_ssh.connect.side_effect = Exception("Connection refused")

        svc = ServerTransferService(self.transfer)
        svc.execute()

        self.transfer.refresh_from_db()
        self.assertEqual(self.transfer.status, 'FAILED')
        self.assertIn("Connection refused", self.transfer.error_message)

    @patch('apps.deployments.services.transfer_service.BackupService.decrypt_backup')
    @patch('apps.deployments.services.transfer_service.os.path.exists', return_value=False)
    def test_restore_uses_uploaded_filename_when_backup_is_encrypted(
        self, _exists_mock, decrypt_mock
    ):
        """Encrypted backups upload a decrypted tarball; restore must use that uploaded path."""
        self.transfer.source_backup = ServiceBackup.objects.create(
            service=self.service,
            file_path='/tmp/source-backup.tar.gz.enc',
            metadata={},
            status='COMPLETED',
        )
        self.transfer.save(update_fields=['source_backup'])

        decrypt_mock.return_value = '/tmp/source-backup.tar.gz'
        os.environ['BACKUP_ENCRYPTION_KEY'] = 'test-key'

        svc = ServerTransferService(self.transfer)
        svc.ssh = MagicMock()
        svc.transfer.transfer_type = 'SERVICE'

        with patch.object(svc, '_restore_single_service') as restore_single:
            svc._upload()
            svc._restore()

        restore_single.assert_called_once_with('/tmp/source-backup.tar.gz')

    @patch('apps.deployments.services.transfer_service.socket.create_connection')
    def test_verify_between_servers_passes_when_remote_tcp_check_succeeds(self, create_connection_mock):
        create_connection_mock.return_value.__enter__.return_value = None
        svc = ServerTransferService(self.transfer)
        svc.ssh = MagicMock()
        svc.ssh.exec_command.return_value = 'TRANSFER_TCP_OK'

        svc._verify_between_servers()

        svc.ssh.exec_command.assert_called_once()

    @patch('apps.deployments.services.transfer_service.socket.create_connection')
    @override_settings(TRANSFER_REQUIRE_BIDIRECTIONAL_SSH=True)
    def test_verify_between_servers_fails_when_remote_tcp_check_fails(self, create_connection_mock):
        create_connection_mock.side_effect = OSError("blocked")
        svc = ServerTransferService(self.transfer)
        svc.ssh = MagicMock()
        svc.ssh.exec_command.return_value = 'TRANSFER_TCP_FAIL'

        with self.assertRaises(RuntimeError):
            svc._verify_between_servers()

    @patch('apps.deployments.services.transfer_service.socket.create_connection')
    def test_verify_between_servers_warns_by_default_when_remote_tcp_check_fails(self, create_connection_mock):
        create_connection_mock.side_effect = OSError("blocked")
        svc = ServerTransferService(self.transfer)
        svc.ssh = MagicMock()
        svc.ssh.exec_command.return_value = 'TRANSFER_TCP_FAIL'

        svc._verify_between_servers()

    def test_rollback_resets_service_server_to_source(self):
        source_server = ManagedServer.objects.create(
            name='source',
            host='1.2.3.4',
            owner=self.user,
        )
        target_server = ManagedServer.objects.create(
            name='target',
            host='5.6.7.8',
            owner=self.user,
        )
        self.service.server = target_server
        self.service.save(update_fields=['server'])

        self.transfer.status = 'COMPLETED'
        self.transfer.rollback_deadline = timezone.now() + timedelta(hours=1)
        self.transfer.save(update_fields=['status', 'rollback_deadline'])

        svc = ServerTransferService(self.transfer)
        with patch.object(svc, '_update_cloudflare_dns'):
            svc.rollback()

        self.service.refresh_from_db()
        self.transfer.refresh_from_db()
        self.assertEqual(self.service.server_id, source_server.id)
        self.assertEqual(self.transfer.status, 'ROLLED_BACK')
        self.assertFalse(self.transfer.can_rollback)

    def test_rollback_rejects_non_completed_transfer(self):
        self.transfer.status = 'FAILED'
        self.transfer.save(update_fields=['status'])
        svc = ServerTransferService(self.transfer)

        with self.assertRaises(ValueError):
            svc.rollback()
