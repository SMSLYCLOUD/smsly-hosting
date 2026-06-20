from unittest.mock import MagicMock

from apps.deployments.models import PlatformConfig, Project, ServerTransfer, Service
from apps.deployments.services.transfer_service import ServerTransferService
from django.contrib.auth import get_user_model
from django.test import TestCase

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

    def test_local_source_ip_bypasses_source_ssh_init(self):
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

        # Verify _target_is_local returns False (target is remote) but
        # _node_api_url returns a URL based on target_server_ip.
        # This is the modern replacement for the old _init_source_ssh check.
        self.assertFalse(service._target_is_local())
        self.assertIn('203.0.113.2', service._node_api_url())
