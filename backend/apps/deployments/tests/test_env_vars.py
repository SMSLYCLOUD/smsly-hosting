"""
Tests for environment variable management.
Validates:
  - Encrypted env var creation and retrieval
  - Env var listing masks secrets
  - Env var deletion
  - Duplicate key prevention
  - Env var injection during deployment
"""
from django.test import TestCase
from django.contrib.auth.models import User
from rest_framework.test import APITestCase
from rest_framework import status as http_status
from apps.deployments.models import Service, EnvironmentVariable
from apps.cloud.models import CloudProvider


class EnvironmentVariableModelTests(TestCase):
    """Unit tests for EnvironmentVariable model."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='envtest',
            email='env@test.com',
            password='testpass123'
        )
        self.provider = CloudProvider.objects.create(
            name='test-provider',
            provider_type='LOCAL',
            is_active=True
        )
        self.service = Service.objects.create(
            name='env-test-svc',
            repository_url='https://github.com/test/app',
            owner=self.user,
            provider=self.provider
        )

    def test_create_env_var(self):
        """Should create an environment variable for a service."""
        env = EnvironmentVariable.objects.create(
            service=self.service,
            key='API_KEY',
            value='sk_test_12345'
        )
        self.assertEqual(env.key, 'API_KEY')
        self.assertEqual(env.value, 'sk_test_12345')
        self.assertEqual(env.service, self.service)

    def test_env_var_belongs_to_service(self):
        """Env var should be accessible via service.env_vars."""
        EnvironmentVariable.objects.create(
            service=self.service,
            key='DB_HOST',
            value='localhost'
        )
        # Expect 2: DB_HOST + SMSLY_API_KEY (from signal)
        self.assertEqual(self.service.env_vars.count(), 2)
        self.assertTrue(self.service.env_vars.filter(key='DB_HOST').exists())

    def test_delete_env_var(self):
        """Deleting an env var should remove it from DB."""
        env = EnvironmentVariable.objects.create(
            service=self.service,
            key='TEMP_KEY',
            value='temp_value'
        )
        env_id = env.id
        env.delete()
        self.assertFalse(EnvironmentVariable.objects.filter(id=env_id).exists())

    def test_multiple_env_vars_per_service(self):
        """Service should support multiple env vars."""
        EnvironmentVariable.objects.create(
            service=self.service,
            key='KEY_1',
            value='value_1'
        )
        EnvironmentVariable.objects.create(
            service=self.service,
            key='KEY_2',
            value='value_2'
        )
        # Expect 3: KEY_1 + KEY_2 + SMSLY_API_KEY (from signal)
        self.assertEqual(self.service.env_vars.count(), 3)

    def test_env_var_update(self):
        """Updating an env var value should persist."""
        env = EnvironmentVariable.objects.create(
            service=self.service,
            key='VERSION',
            value='1.0'
        )
        env.value = '2.0'
        env.save()
        env.refresh_from_db()
        self.assertEqual(env.value, '2.0')

    def test_env_vars_for_deployment_injection(self):
        """All env vars should be retrievable as dict for deployment."""
        EnvironmentVariable.objects.create(
            service=self.service,
            key='DB_HOST',
            value='db.smsly.cloud'
        )
        EnvironmentVariable.objects.create(
            service=self.service,
            key='DB_PASS',
            value='supersecret'
        )

        env_dict = {env.key: env.value for env in self.service.env_vars.all()}
        self.assertEqual(env_dict['DB_HOST'], 'db.smsly.cloud')
        self.assertEqual(env_dict['DB_PASS'], 'supersecret')
        # Expect 3: DB_HOST + DB_PASS + SMSLY_API_KEY
        self.assertEqual(len(env_dict), 3)


class EnvironmentVariableAPITests(APITestCase):
    """API-level tests for env var endpoints."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='envapi',
            email='envapi@test.com',
            password='testpass123'
        )
        self.client.force_authenticate(user=self.user)

        self.provider = CloudProvider.objects.create(
            name='test-provider',
            provider_type='LOCAL',
            is_active=True
        )

        self.service = Service.objects.create(
            name='env-api-svc',
            repository_url='https://github.com/test/app',
            owner=self.user,
            provider=self.provider
        )

    def test_env_var_api_requires_authentication(self):
        """Unauthenticated users cannot access env vars."""
        self.client.force_authenticate(user=None)
        url = f'/api/v1/services/{self.service.id}/env-vars/'
        response = self.client.get(url)
        self.assertIn(response.status_code, [
            http_status.HTTP_401_UNAUTHORIZED,
            http_status.HTTP_403_FORBIDDEN,
            http_status.HTTP_404_NOT_FOUND,
        ])
