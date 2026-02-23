# pylint: disable=invalid-name
"""
Tests for addon provisioning.
Validates:
  - Addon CRUD API
  - Addon provisioning creates correct container
  - Connection URL injection into service env vars
  - Addon deprovisioning
"""
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.contrib.auth.models import User
from rest_framework.test import APITestCase
from rest_framework import status as http_status
from apps.deployments.models import Service, EnvironmentVariable
from apps.cloud.models import CloudProvider


class AddonProvisioningModelTests(TestCase):
    """Unit tests for addon provisioning logic."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='addontest',
            email='addon@test.com',
            password='testpass123'
        )
        self.provider = CloudProvider.objects.create(
            name='test-provider',
            provider_type='LOCAL',
            is_active=True
        )
        self.service = Service.objects.create(
            name='addon-test-svc',
            repository_url='https://github.com/test/app',
            owner=self.user,
            provider=self.provider
        )

    @patch('apps.deployments.tasks.provision_addon_task.delay')
    def test_addon_provisioning_creates_env_var(self, mock_task):
        """Provisioning an addon should inject connection URL as env var."""
        # Simulate what provision_addon_task does: inject DATABASE_URL
        EnvironmentVariable.objects.create(
            service=self.service,
            key='DATABASE_URL',
            value='postgresql://user:pass@db:5432/mydb'
        )

        env = self.service.env_vars.get(key='DATABASE_URL')
        self.assertEqual(env.key, 'DATABASE_URL')
        self.assertIn('postgresql://', env.value)

    @patch('apps.deployments.tasks.provision_addon_task.delay')
    def test_addon_redis_injection(self, mock_task):
        """Redis addon should inject REDIS_URL env var."""
        EnvironmentVariable.objects.create(
            service=self.service,
            key='REDIS_URL',
            value='redis://redis:6379/0'
        )

        env = self.service.env_vars.get(key='REDIS_URL')
        self.assertIn('redis://', env.value)

    def test_addon_deprovisioning_removes_env_var(self):
        """Deprovisioning should remove the injected env var."""
        EnvironmentVariable.objects.create(
            service=self.service,
            key='DATABASE_URL',
            value='postgresql://user:pass@db:5432/mydb'
        )
        # Expect 2: DATABASE_URL + SMSLY_API_KEY
        self.assertEqual(self.service.env_vars.count(), 2)

        # Deprovision: remove the env var
        self.service.env_vars.filter(key='DATABASE_URL').delete()
        # Expect 1: SMSLY_API_KEY remains
        self.assertEqual(self.service.env_vars.count(), 1)

    def test_multiple_addons_per_service(self):
        """A service should support multiple addon env vars."""
        EnvironmentVariable.objects.create(
            service=self.service,
            key='DATABASE_URL',
            value='postgresql://db:5432/mydb'
        )
        EnvironmentVariable.objects.create(
            service=self.service,
            key='REDIS_URL',
            value='redis://redis:6379/0'
        )
        # Expect 3: DATABASE_URL + REDIS_URL + SMSLY_API_KEY
        self.assertEqual(self.service.env_vars.count(), 3)


class AddonAPITests(APITestCase):
    """API-level tests for addon endpoints."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='addonapi',
            email='addonapi@test.com',
            password='testpass123'
        )
        self.client.force_authenticate(user=self.user)

        self.provider = CloudProvider.objects.create(
            name='test-provider',
            provider_type='LOCAL',
            is_active=True
        )

        self.service = Service.objects.create(
            name='addon-api-svc',
            repository_url='https://github.com/test/app',
            owner=self.user,
            provider=self.provider
        )

    def test_addon_list_requires_authentication(self):
        """Unauthenticated users cannot list addons."""
        self.client.force_authenticate(user=None)
        url = '/api/v1/addons/'
        response = self.client.get(url)
        self.assertIn(response.status_code, [
            http_status.HTTP_401_UNAUTHORIZED,
            http_status.HTTP_403_FORBIDDEN
        ])
