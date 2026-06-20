# pylint: disable=invalid-name
"""Tests for multi-tenancy isolation."""
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.cloud.models import CloudProvider
from apps.deployments.models import EnvironmentVariable, Service


class MultiTenancyTests(TestCase):
    """Test that users cannot access each other's resources."""

    def setUp(self):
        """Create two separate users and their resources."""
        # User 1
        self.user1 = User.objects.create_user('user1', 'user1@example.com', 'password123')
        self.client1 = APIClient()
        self.client1.force_authenticate(user=self.user1)

        # User 2
        self.user2 = User.objects.create_user('user2', 'user2@example.com', 'password123')
        self.client2 = APIClient()
        self.client2.force_authenticate(user=self.user2)

        self.provider = CloudProvider.objects.create(
            name='Local',
            provider_type='LOCAL'
        )

        # User 1's service
        self.service1 = Service.objects.create(
            name='user1-service',
            repository_url='https://github.com/user1/repo',
            owner=self.user1,
            provider=self.provider
        )

        # User 2's service
        self.service2 = Service.objects.create(
            name='user2-service',
            repository_url='https://github.com/user2/repo',
            owner=self.user2,
            provider=self.provider
        )

    def test_user_cannot_view_other_users_services(self):
        """User 1 should not see User 2's services."""
        response = self.client1.get('/api/v1/services/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        service_names = [s['name'] for s in response.data['results'] if 'name' in s]

        self.assertIn('user1-service', service_names)
        self.assertNotIn('user2-service', service_names)

    def test_user_cannot_access_other_users_service_detail(self):
        """User 1 should get 404 when accessing User 2's service."""
        response = self.client1.get(f'/api/v1/services/{self.service2.id}/')

        # Should return 404 (not 403) to avoid leaking existence
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_user_cannot_delete_other_users_service(self):
        """User 1 should not be able to delete User 2's service."""
        response = self.client1.delete(f'/api/v1/services/{self.service2.id}/')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        # Verify service still exists
        self.assertTrue(Service.objects.filter(id=self.service2.id).exists())

    def test_user_cannot_view_other_users_env_vars(self):
        """User 1 should not see User 2's environment variables."""
        # Create env var for User 2
        EnvironmentVariable.objects.create(
            service=self.service2,
            key='SECRET_KEY',
            value='user2_secret',
            is_secret=True
        )

        response = self.client1.get(f'/api/v1/services/{self.service2.id}/env_vars/')

        # 404 is preferred, but 405 is also acceptable access denial in some router configs
        self.assertIn(response.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_405_METHOD_NOT_ALLOWED])

    def test_user_cannot_trigger_deployment_for_other_users_service(self):
        """User 1 should not trigger deployments for User 2's service."""
        response = self.client1.post(
            f'/api/v1/services/{self.service2.id}/deploy/',
            data={'ref': 'main'}
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_superuser_can_access_all_services(self):
        """Superuser should see all services."""
        admin = User.objects.create_superuser('admin', 'admin@example.com', 'admin123')
        admin_client = APIClient()
        admin_client.force_authenticate(user=admin)

        response = admin_client.get('/api/v1/services/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Admin should see both services
        self.assertGreaterEqual(len(response.data.get('results', [])), 2)
