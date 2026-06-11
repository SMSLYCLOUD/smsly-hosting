from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.contrib.auth import get_user_model
from apps.deployments.models import Service, Project, PlatformConfig, ServerTransfer
from apps.deployments.services.transfer_service import ServerTransferService

User = get_user_model()

class TransferServiceLocalDetectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser', email='test@example.com', password='password123'
        )
        self.project = Project.objects.create(name='Test Project', owner=self.user)
        self.service = Service.objects.create(name='Test Service', project=self.project)
        
        # Configure PlatformConfig with a dummy server IP
        self.local_ip = '198.51.100.1'
        self.config = PlatformConfig.objects.create(
            domain='smsly.cloud',
            server_ip=self.local_ip,
            use_ssl=False
        )

    @patch('apps.deployments.services.transfer_service.SSHClient')
    def test_local_source_ip_bypasses_source_ssh_init(self, mock_ssh_client):
        # Create a transfer where source_server_ip equals PlatformConfig's server_ip
        # and target_server_ip is different (a remote server)
        transfer = ServerTransfer.objects.create(
            owner=self.user,
            source_server_ip=self.local_ip,
            target_server_ip='203.0.113.2',
            transfer_type='SERVICE',
            service=self.service,
            target_ssh_password='target-pass',
        )

        service = ServerTransferService(transfer)
        
        # Mock target connection to avoid outbound socket connection
        service.ssh = MagicMock()
        
        # Calling _init_source_ssh directly or running the init step in execute
        # should NOT raise ValueError because the source is local.
        try:
            service._init_source_ssh()
        except ValueError as exc:
            self.fail(f"_init_source_ssh raised ValueError unexpectedly: {exc}")
            
        self.assertIsNone(service.source_ssh)
