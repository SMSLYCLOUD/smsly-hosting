from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models_addons import Addon
from apps.deployments.models_core import ManagedServer, Service
from apps.deployments.services.deletion_orchestrator import DeletionOrchestrator
from apps.deployments.tasks import delete_service_task

User = get_user_model()

class TestDeletionOrchestrator(TestCase):
    @patch('apps.deployments.services.deletion_orchestrator.get_docker_client')
    def setUp(self, mock_docker):
        self.mock_client = MagicMock()
        mock_docker.return_value = self.mock_client
        self.orchestrator = DeletionOrchestrator()

        self.user = User.objects.create_user(username="deletion", password="password")
        self.service = Service.objects.create(name="test-service", owner=self.user)

        self.addon = Addon.objects.create(
            service=self.service,
            name="test-addon",
            addon_type="POSTGRES",
            status="ACTIVE"
        )

    def test_delete_service_resources_success(self):
        # Mock containers and volumes
        mock_container = MagicMock()
        mock_container.labels = {'smsly.service_id': str(self.service.id)}
        mock_container.name = "test-service-green-123"

        self.mock_client.containers.list.return_value = [mock_container]

        mock_volume = MagicMock()
        mock_volume.attrs = {'Labels': {'smsly.service_id': str(self.service.id)}}
        mock_volume.name = "test-service-vol"

        self.mock_client.volumes.list.return_value = [mock_volume]

        success = self.orchestrator.delete_service_resources(self.service)

        self.assertTrue(success)
        mock_container.stop.assert_called_once_with(timeout=10)
        mock_container.remove.assert_called_once_with(force=True)
        mock_volume.remove.assert_called_once_with(force=True)

    def test_delete_service_resources_uses_active_runtime_id(self):
        self.service.active_runtime_id = "runtime-container-id"
        self.service.save(update_fields=["active_runtime_id"])

        runtime_container = MagicMock()
        runtime_container.labels = {}
        runtime_container.name = "runtime-container"
        self.mock_client.containers.get.return_value = runtime_container
        self.mock_client.containers.list.return_value = []
        self.mock_client.volumes.list.return_value = []

        success = self.orchestrator.delete_service_resources(self.service)

        self.assertTrue(success)
        self.mock_client.containers.get.assert_called_once_with("runtime-container-id")
        runtime_container.stop.assert_called_once_with(timeout=10)
        runtime_container.remove.assert_called_once_with(force=True)

    def test_delete_addon_resources_success(self):
        # Mock containers and volumes
        mock_container = MagicMock()
        mock_container.labels = {'smsly.addon_id': str(self.addon.id)}
        mock_container.name = f"smsly-addon-postgres-{self.addon.id}"

        self.mock_client.containers.list.return_value = [mock_container]

        mock_volume = MagicMock()
        mock_volume.name = f"smsly-addon-postgres-{self.addon.id}-data"
        mock_volume.attrs = {}

        self.mock_client.volumes.list.return_value = [mock_volume]

        success = self.orchestrator.delete_addon_resources(self.addon)

        self.assertTrue(success)
        mock_container.stop.assert_called_once_with(timeout=10)
        mock_container.remove.assert_called_once_with(force=True)
        mock_volume.remove.assert_called_once_with(force=True)

    @patch('apps.deployments.services.remote_orchestrator.RemoteOrchestrator')
    def test_delete_service_task_uses_remote_identity_cleanup(self, mock_remote_cls):
        server = ManagedServer.objects.create(
            name="remote-node",
            host="10.0.0.10",
            owner=self.user,
            is_primary=False,
        )
        self.service.server = server
        self.service.active_target_type = "remote"
        self.service.active_host_ip = server.host
        self.service.save(update_fields=["server", "active_target_type", "active_host_ip"])
        mock_remote_cls.return_value.delete_service_for_local.return_value = True

        delete_service_task.run(str(self.service.id), force=False)

        mock_remote_cls.assert_called_once()
        mock_remote_cls.return_value.delete_service_for_local.assert_called_once()
        self.assertFalse(Service.objects.filter(id=self.service.id).exists())
