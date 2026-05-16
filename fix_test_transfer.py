with open('backend/apps/deployments/tests/test_transfer.py', 'r') as f:
    content = f.read()

content = content.replace("def test_execute_full_transfer_success(self, MockBackupService, MockSSHClient):", "@patch('apps.deployments.services.transfer_service.SSHClient')\n    @patch('apps.deployments.services.transfer_service.BackupService')\n    def test_execute_full_transfer_success(self, MockBackupService, MockSSHClient):")
content = content.replace("def test_ssh_failure_handling(self, MockBackupService, MockSSHClient):", "@patch('apps.deployments.services.transfer_service.SSHClient')\n    @patch('apps.deployments.services.transfer_service.BackupService')\n    def test_ssh_failure_handling(self, MockBackupService, MockSSHClient):")

with open('backend/apps/deployments/tests/test_transfer.py', 'w') as f:
    f.write(content)
