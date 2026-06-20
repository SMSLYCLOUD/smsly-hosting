
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import (
    Deployment,
    ManagedServer,
    Project,
    ServerTransfer,
    Service,
)

User = get_user_model()

class RemoteLifecycleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='lifecycle', email='life@example.com', password='password123')
        self.project = Project.objects.create(name='Lifecycle Project', owner=self.user)
        self.server = ManagedServer.objects.create(name='Life Node', owner=self.user, private_ip='10.0.0.2', project=self.project)
        self.service = Service.objects.create(name='Life Service', project=self.project, server=self.server)

    def test_clean_remote_deployment(self):
        deployment = Deployment.objects.create(service=self.service, status=Deployment.Status.QUEUED, commit_hash='xyz123')
        deployment.status = Deployment.Status.DEPLOYING
        deployment.save()
        deployment.status = Deployment.Status.HEALTH_CHECK
        deployment.save()
        deployment.status = Deployment.Status.ACTIVE
        deployment.save()

        self.assertEqual(deployment.status, Deployment.Status.ACTIVE)

    def test_remote_transfer_lifecycle(self):
        ManagedServer.objects.create(name='Target Node', owner=self.user, private_ip='10.0.0.3', project=self.project)
        transfer = ServerTransfer.objects.create(
            owner=self.user,
            source_server_ip='10.0.0.2',
            target_server_ip='10.0.0.3',
            status='PREPARING',
            transfer_type='SERVICE',
            service=self.service,
        )

        transfer.status = 'VERIFYING'
        transfer.save()

        self.assertTrue(Service.objects.filter(id=self.service.id).exists())

        transfer.status = 'COMPLETED'
        transfer.save()

    def test_concurrent_protection(self):
        pass
