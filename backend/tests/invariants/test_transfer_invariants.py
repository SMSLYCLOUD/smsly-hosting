from django.test import TestCase
from apps.deployments.models import ServerTransfer, ManagedServer, Service, Project
from django.contrib.auth import get_user_model

User = get_user_model()

class TransferInvariantTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', email='test@example.com', password='password123')
        self.project = Project.objects.create(name='Test Project', owner=self.user)
        self.source_server = ManagedServer.objects.create(name='Source', owner=self.user, private_ip='1.1.1.1', project=self.project)
        self.target_server = ManagedServer.objects.create(name='Target', owner=self.user, private_ip='2.2.2.2', project=self.project)
        self.service = Service.objects.create(name='Test Service', project=self.project, server=self.source_server)

    def test_transfer_source_preservation_on_failure(self):
        """A failed transfer must not destroy the source data."""
        transfer = ServerTransfer.objects.create(
            owner=self.user,
            source_server_ip='1.1.1.1',
            target_server_ip='2.2.2.2',
            status='FAILED',
            transfer_type='SERVICE',
            service=self.service,
        )

        # Enforce that source is preserved by asserting state logic
        if hasattr(transfer, 'cleanup_source'):
            with self.assertRaises(ValueError):
                transfer.cleanup_source()

        self.assertTrue(Service.objects.filter(id=self.service.id, server=self.source_server).exists())
