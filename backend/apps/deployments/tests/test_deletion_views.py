from django.test import TestCase
class DummyTest(TestCase):
    pass
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from apps.deployments.models_core import Service
from apps.deployments.models_addons import Addon

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

    @patch('apps.deployments.tasks.delete_addon_task.delay')
    def test_addon_delete_endpoint(self, mock_delay):
        response = self.client.delete(f'/api/v1/addons/{self.addon.id}/')

        self.assertEqual(response.status_code, 202)
        self.addon.refresh_from_db()
        self.assertEqual(self.addon.status, Addon.Status.DELETION_PENDING)
        mock_delay.assert_called_once_with(str(self.addon.id))
