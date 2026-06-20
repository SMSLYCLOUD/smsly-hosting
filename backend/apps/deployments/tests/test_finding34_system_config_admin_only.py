from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


class Finding34SystemConfigMaintenanceAdminOnlyTests(TestCase):
    def setUp(self):
        self.url = '/api/v1/system/config/'
        cache.clear()

    def _login(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    @patch('apps.deployments.tasks.run_maintenance_task.apply_async')
    def test_non_admin_clear_returns_403(self, mock_apply):
        user = User.objects.create_user(
            username='f34q-user', password='p', email='u@e.com',
        )
        client = self._login(user)
        resp = client.post(self.url, {'action': 'clear'}, format='json')
        self.assertEqual(resp.status_code, 403)
        mock_apply.assert_not_called()

    @patch('apps.deployments.tasks.run_maintenance_task.apply_async')
    def test_non_admin_refresh_returns_403(self, mock_apply):
        user = User.objects.create_user(
            username='f34q-user2', password='p', email='u2@e.com',
        )
        client = self._login(user)
        resp = client.post(self.url, {'action': 'refresh'}, format='json')
        self.assertEqual(resp.status_code, 403)
        mock_apply.assert_not_called()

    @patch('apps.deployments.tasks.run_maintenance_task.apply_async')
    def test_non_admin_update_returns_403(self, mock_apply):
        user = User.objects.create_user(
            username='f34q-user3', password='p', email='u3@e.com',
        )
        client = self._login(user)
        resp = client.post(self.url, {'action': 'update'}, format='json')
        self.assertEqual(resp.status_code, 403)
        mock_apply.assert_not_called()

