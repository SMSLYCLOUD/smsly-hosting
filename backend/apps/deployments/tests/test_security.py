"""
Security-focused tests for SMSLY Hosting API.
Ensures authentication and authorization are properly enforced.
"""
from django.urls import reverse
from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase
from apps.deployments.models import Service


class AuthenticationTests(APITestCase):
    """Test that authentication is required for all endpoints."""

    def test_unauthenticated_user_cannot_list_services(self):
        """Unauthenticated requests should return 401/403."""
        url = reverse('service-list')
        response = self.client.get(url)
        self.assertIn(
            response.status_code, [
                status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_unauthenticated_user_cannot_create_service(self):
        """Unauthenticated users cannot create services."""
        url = reverse('service-list')
        data = {
            'name': 'my-app',
            'repository_url': 'https://github.com/example/app',
        }
        response = self.client.post(url, data, format='json')
        self.assertIn(
            response.status_code, [
                status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_unauthenticated_user_cannot_list_deployments(self):
        """Unauthenticated requests to deployments should return 401/403."""
        url = reverse('deployment-list')
        response = self.client.get(url)
        self.assertIn(
            response.status_code, [
                status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_authenticated_user_can_list_services(self):
        """Authenticated users should be able to list services."""
        user = User.objects.create_user(
            'testuser', 'test@example.com', 'testpass123')
        self.client.force_authenticate(user=user)
        url = reverse('service-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_authenticated_user_can_create_service(self):
        """Authenticated users should be able to create services."""
        user = User.objects.create_user(
            'testuser2', 'test2@example.com', 'testpass123')
        self.client.force_authenticate(user=user)
        url = reverse('service-list')
        data = {
            'name': 'my-authenticated-app',
            'repository_url': 'https://github.com/example/app',
            'branch': 'main'
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Service.objects.count(), 1)


class EnvVarSecurityTests(APITestCase):
    """Test that environment variable security is maintained."""

    def setUp(self):
        self.user = User.objects.create_user(
            'secuser', 'sec@example.com', 'secpass123')
        self.client.force_authenticate(user=self.user)

        # Create a service
        self.service = Service.objects.create(
            name='test-service',
            repository_url='https://github.com/test/app'
        )

    def test_auto_injected_keys_are_placeholders(self):
        """Auto-injected API keys should be placeholders, not real keys."""
        url = reverse('service-list')
        data = {
            'name': 'new-service',
            'repository_url': 'https://github.com/example/app',
            'branch': 'main'
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Check that env vars are placeholders
        service = Service.objects.get(name='new-service')
        api_key = service.env_vars.get(key='SMSLY_API_KEY')
        self.assertEqual(api_key.value, 'PLACEHOLDER_CONFIGURE_IN_DASHBOARD')
        self.assertNotIn('sk_live', api_key.value)
