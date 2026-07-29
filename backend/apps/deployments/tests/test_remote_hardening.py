from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, PlatformConfig, Service
from apps.deployments.models.core import ManagedServer
from apps.deployments.services.pipeline import PipelineManager
from apps.deployments.services.ssh_client import SSHClient
from apps.deployments.utils import is_deployment_local


class RemoteHardeningTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='remotetest',
            email='remote@test.com',
            password='testpass123'
        )
        self.local_provider = CloudProvider.objects.create(
            name='local-prov',
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True
        )
        self.remote_provider = CloudProvider.objects.create(
            name='remote-prov',
            provider_type=CloudProvider.ProviderType.REMOTE,
            is_active=True
        )

        # PlatformConfig setup
        self.config = PlatformConfig.objects.create(
            domain="localhost",
            server_ip="1.2.3.4"
        )

        # Local Server setup
        self.local_server = ManagedServer.objects.create(
            owner=self.user,
            name="Primary Local Server",
            host="1.2.3.4",
            is_primary=True
        )

        # Remote Server setup
        self.remote_server = ManagedServer.objects.create(
            owner=self.user,
            name="Remote Follower Server",
            host="5.6.7.8",
            is_primary=False
        )

    def test_is_deployment_local_explicit_target_is_local(self):
        service = Service.objects.create(
            name='svc-1',
            owner=self.user,
            provider=self.remote_provider,
            server=self.remote_server
        )
        deployment = Deployment.objects.create(
            service=service,
            commit_hash='abc1234',
            target_is_local=True
        )
        self.assertTrue(is_deployment_local(deployment))

    def test_is_deployment_local_primary_server(self):
        service = Service.objects.create(
            name='svc-2',
            owner=self.user,
            provider=self.local_provider,
            server=self.local_server
        )
        deployment = Deployment.objects.create(
            service=service,
            commit_hash='abc1234'
        )
        self.assertTrue(is_deployment_local(deployment))

    def test_is_deployment_local_remote_server(self):
        service = Service.objects.create(
            name='svc-3',
            owner=self.user,
            provider=self.remote_provider,
            server=self.remote_server
        )
        deployment = Deployment.objects.create(
            service=service,
            commit_hash='abc1234'
        )
        self.assertFalse(is_deployment_local(deployment))

    def test_is_deployment_local_active_target_fallback(self):
        service = Service.objects.create(
            name='svc-4',
            owner=self.user,
            provider=self.remote_provider,
            active_target_type="remote",
            active_host_ip="5.6.7.8"
        )
        deployment = Deployment.objects.create(
            service=service,
            commit_hash='abc1234'
        )
        self.assertFalse(is_deployment_local(deployment))

        service.active_host_ip = "1.2.3.4"
        service.save()
        self.assertTrue(is_deployment_local(deployment))

    @patch('apps.deployments.services.pipeline.NixpacksBuilder.push_image')
    def test_pipeline_push_image_success(self, mock_push):
        mock_push.return_value = "registry.smsly.cloud/smsly/app:tag"

        service = Service.objects.create(
            name='svc-git',
            owner=self.user,
            provider=self.remote_provider,
            server=self.remote_server,
            deploy_type='GIT'
        )
        deployment = Deployment.objects.create(
            service=service,
            commit_hash='abc1234'
        )

        with patch.object(settings, 'CONTAINER_REGISTRY_URL', 'registry.smsly.cloud'):
            manager = PipelineManager(deployment)
            manager.image_name = "smsly/app:tag"
            manager._push_image()
            self.assertEqual(manager.image_name, "registry.smsly.cloud/smsly/app:tag")

    @patch('apps.deployments.services.pipeline.NixpacksBuilder.push_image')
    def test_pipeline_push_image_fail_local_allows_fallback(self, mock_push):
        # Push fails, returns the local tag
        mock_push.return_value = "smsly/app:tag"

        service = Service.objects.create(
            name='svc-git-local',
            owner=self.user,
            provider=self.local_provider,
            server=self.local_server,
            deploy_type='GIT'
        )
        deployment = Deployment.objects.create(
            service=service,
            commit_hash='abc1234'
        )

        with patch.object(settings, 'CONTAINER_REGISTRY_URL', 'registry.smsly.cloud'):
            manager = PipelineManager(deployment)
            manager.image_name = "smsly/app:tag"
            manager._push_image()
            # Falls back successfully to local tag
            self.assertEqual(manager.image_name, "smsly/app:tag")

    @patch('apps.deployments.services.pipeline.NixpacksBuilder.push_image')
    def test_pipeline_push_image_fail_remote_raises_error(self, mock_push):
        # Push fails, returns the local tag
        mock_push.return_value = "smsly/app:tag"

        service = Service.objects.create(
            name='svc-git-remote',
            owner=self.user,
            provider=self.remote_provider,
            server=self.remote_server,
            deploy_type='GIT'
        )
        deployment = Deployment.objects.create(
            service=service,
            commit_hash='abc1234'
        )

        with patch.object(settings, 'CONTAINER_REGISTRY_URL', 'registry.smsly.cloud'):
            manager = PipelineManager(deployment)
            manager.image_name = "smsly/app:tag"
            with self.assertRaises(SystemError) as context:
                manager._push_image()
            self.assertIn("Local fallback is not allowed for remote deployments", str(context.exception))

    @patch('apps.deployments.services.ssh_client.SSHClient.connect')
    @patch('apps.deployments.services.ssh_client.paramiko.SSHClient')
    def test_ssh_client_reconnects_if_transport_inactive(self, mock_ssh_class, mock_connect):
        mock_client = MagicMock()
        mock_ssh_class.return_value = mock_client

        # Setup transport
        mock_transport = MagicMock()
        mock_client.get_transport.return_value = mock_transport

        # First call: transport is active
        mock_transport.is_active.return_value = True

        client = SSHClient(ip="1.2.3.4", key_content="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----")
        client.client = mock_client

        # Mock connect implementation to simulate real client setup
        def mock_connect_side_effect():
            client.client = mock_client
        mock_connect.side_effect = mock_connect_side_effect

        # Mock exec_command behavior
        mock_chan = MagicMock()
        mock_chan.recv_ready.return_value = False
        mock_chan.recv_stderr_ready.return_value = False
        mock_chan.exit_status_ready.return_value = True
        mock_chan.recv_exit_status.return_value = 0
        mock_client.exec_command.return_value = (MagicMock(), MagicMock(channel=mock_chan), MagicMock())

        client.exec_command("echo hello")
        # should not call connect since it was already active
        mock_connect.assert_not_called()

        # Second call: transport becomes inactive
        mock_transport.is_active.return_value = False

        client.exec_command("echo hello")
        # should close client and call connect
        mock_client.close.assert_called_once()
        mock_connect.assert_called_once()
