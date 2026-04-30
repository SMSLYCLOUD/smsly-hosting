from unittest.mock import patch, MagicMock
from django.test import TestCase
from apps.deployments.services.deletion_orchestrator import DeletionOrchestrator
from apps.deployments.models_core import Service
from apps.deployments.models_addons import Addon
import uuid

class TestDeletionOrchestrator(TestCase):
    @patch('apps.deployments.services.deletion_orchestrator.get_docker_client')
    def setUp(self, mock_docker):
        self.mock_client = MagicMock()
        mock_docker.return_value = self.mock_client
        self.orchestrator = DeletionOrchestrator()

        self.service = Service.objects.create(name="test-service")

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
