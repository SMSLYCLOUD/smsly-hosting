import os
import sys
import django

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.deployments.models import Service
from apps.deployments.models_transfer import ServerTransfer
from apps.deployments.services.transfer_service import ServerTransferService
from unittest.mock import patch, MagicMock

User = get_user_model()

def run_simulation():
    # 1. Setup mock data
    user, _ = User.objects.get_or_create(username='transfer_test_user', email='test@example.com')
    service, _ = Service.objects.get_or_create(
        name='test-simulate-transfer-service',
        owner=user,
        deploy_type='DOCKER'
    )

    transfer = ServerTransfer.objects.create(
        service=service,
        source_server_ip='1.1.1.1',
        target_server_ip='2.2.2.2',
        target_ssh_key='mock_ssh_key',
        transfer_type='SERVICE'
    )

    print(f"Created transfer {transfer.id} for service {service.name}")

    # 2. Patch external dependencies (SSH and Backup)
    with patch('apps.deployments.services.transfer_service.SSHClient') as MockSSHClient, \
         patch('apps.deployments.services.transfer_service.BackupService') as MockBackupService:

        mock_ssh = MockSSHClient.return_value
        mock_ssh.check_docker.return_value = True

        def _exec_side_effect(cmd, *args, **kwargs):
            print(f"[SSH MOCK] Executing: {cmd}")
            if "docker inspect" in cmd:
                return "true"
            if "docker ps -q" in cmd:
                return "mock_container_id\n"
            return "mock_output"

        mock_ssh.exec_command.side_effect = _exec_side_effect

        mock_backup_svc = MockBackupService.return_value

        # We need a fake ServiceBackup instance
        from apps.deployments.models_backup import ServiceBackup
        mock_backup = ServiceBackup.objects.create(
            service=service,
            file_path='/tmp/mock_backup.tar.gz',
            status='COMPLETED'
        )
        mock_backup_svc.backup_service.return_value = mock_backup

        print("Starting transfer execution...")
        engine = ServerTransferService(transfer)

        try:
            engine.execute()
        except Exception as e:
            print(f"Error during transfer: {e}")
            import traceback
            traceback.print_exc()

        transfer.refresh_from_db()
        print(f"Transfer status: {transfer.status}")
        print(f"Error message: {transfer.error_message}")
        print(f"Progress: {transfer.progress_percent}% - {transfer.current_step}")

        if transfer.status == 'COMPLETED':
            print("SUCCESS! Transfer simulation completed smoothly.")
        else:
            print("FAILURE! Transfer simulation did not complete as expected.")

if __name__ == '__main__':
    run_simulation()
