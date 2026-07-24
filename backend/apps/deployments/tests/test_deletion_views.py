from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models.addons import Addon
from apps.deployments.models.core import Service

User = get_user_model()

class TestDeletionViews(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", email="test@example.com", password="password")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.service = Service.objects.create(name="test-service", owner=self.user)
        self.addon = Addon.objects.create(service=self.service, name="test-addon", addon_type="REDIS")

    @patch('apps.deployments.tasks.delete_service_task.delay')
    def test_service_delete_endpoint(self, mock_delay):
        response = self.client.delete(f'/api/v1/services/{self.service.id}/')

        self.assertEqual(response.status_code, 202)
        self.service.refresh_from_db()
        self.assertEqual(self.service.status, Service.Status.DELETION_PENDING)
        mock_delay.assert_called_once_with(str(self.service.id), force=False)

    @patch('apps.deployments.views.ServiceViewSet._is_remote_sync_request', return_value=True)
    @patch('apps.deployments.views.ServiceViewSet._sync_caddy', return_value={'ok': True})
    @patch('apps.deployments.services.deletion_orchestrator.DeletionOrchestrator')
    def test_remote_sync_service_delete_runs_cleanup_synchronously(
        self,
        mock_orchestrator_cls,
        _sync_caddy,
        _remote_sync,
    ):
        mock_orchestrator_cls.return_value.delete_service_resources.return_value = True

        response = self.client.delete(f'/api/v1/services/{self.service.id}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "deleted")
        mock_orchestrator_cls.return_value.delete_service_resources.assert_called_once()
        self.assertFalse(Service.objects.filter(id=self.service.id).exists())

    @patch('apps.deployments.tasks.delete_addon_task.delay')
    def test_addon_delete_endpoint(self, mock_delay):
        response = self.client.delete(f'/api/v1/addons/{self.addon.id}/')

        self.assertEqual(response.status_code, 202)
        self.addon.refresh_from_db()
        self.assertEqual(self.addon.status, Addon.Status.DELETION_PENDING)
        mock_delay.assert_called_once_with(str(self.addon.id))
