# pylint: disable=invalid-name
from django.test import TestCase
from unittest.mock import MagicMock, patch
import os
from django.utils import timezone
from apps.deployments.models import Service, PlatformConfig
from apps.deployments.models_transfer import ServerTransfer
from apps.deployments.services.transfer_service import ServerTransferService
from apps.deployments.models_backup import ServiceBackup, ServerBackup

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
        mock_ssh.exec_command.return_value = "mock_output"

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
