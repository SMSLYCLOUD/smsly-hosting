# pylint: disable=invalid-name
from django.test import TestCase
from unittest.mock import MagicMock, patch
from django.utils import timezone
from apps.deployments.models import Service, PlatformConfig
from apps.deployments.models_transfer import ServerTransfer
from apps.deployments.services.transfer_service import ServerTransferService
from apps.deployments.models_backup import ServiceBackup, ServerBackup

class ServerTransferServiceTest(TestCase):
    def setUp(self):
        self.service = Service.objects.create(name="test-service", deploy_type='DOCKER')
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
        mock_ssh.exec_command.assert_any_call(f"tar -xzf /tmp/backup.tar.gz -C /tmp/restore_{self.transfer.id}")

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
