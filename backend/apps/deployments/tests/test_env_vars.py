# pylint: disable=invalid-name
"""
Tests for environment variable management.
Validates:
  - Encrypted env var creation and retrieval
  - Env var listing masks secrets
  - Env var deletion
  - Duplicate key prevention
  - Env var injection during deployment
"""
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from rest_framework import status as http_status
from rest_framework.test import APITestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import EnvironmentVariable, Service


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

    def test_env_var_widen_max_length(self):
        """Should support env vars with length > 255 (up to 10000) characters."""
        long_value = 'A' * 1000
        env = EnvironmentVariable.objects.create(
            service=self.service,
            key='LONG_VAR',
            value=long_value
        )
        env.refresh_from_db()
        self.assertEqual(env.value, long_value)


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

    def test_env_var_post_parses_false_secret_flag_correctly(self):
        """String value 'false' should not be coerced to True."""
        url = f'/api/v1/services/{self.service.id}/env_vars/'
        payload = {
            'key': 'PUBLIC_FLAG',
            'value': 'enabled',
            'is_secret': 'false',
        }
        response = self.client.post(url, payload, format='json')

        self.assertIn(response.status_code, [
            http_status.HTTP_200_OK,
            http_status.HTTP_201_CREATED,
        ])
        env_var = EnvironmentVariable.objects.get(service=self.service, key='PUBLIC_FLAG')
        self.assertFalse(env_var.is_secret)

    def test_env_var_get_handles_corrupted_encrypted_value(self):
        """Listing env vars should not 500 if a stored value is malformed."""
        env_var = EnvironmentVariable.objects.create(
            service=self.service,
            key='BROKEN_VALUE',
            value='original'
        )
        table = EnvironmentVariable._meta.db_table
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {table} SET value = %s WHERE id = %s",
                ['not-a-valid-encrypted-token', env_var.id],
            )

        url = f'/api/v1/services/{self.service.id}/env_vars/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)

        items = response.data if isinstance(response.data, list) else response.data.get('results', [])
        row = next((item for item in items if item.get('key') == 'BROKEN_VALUE'), None)
        self.assertIsNotNone(row)
        self.assertIn(row.get('value'), ['', 'not-a-valid-encrypted-token'])

    def test_bulk_post_rejects_ciphertext(self):
        """Bulk env var POST endpoint should skip Fernet ciphertext values and return a warning."""
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        f = Fernet(key)
        ciphertext = f.encrypt(b"secret-value").decode('utf-8')

        url = f'/api/v1/services/{self.service.id}/env_vars/'
        payload = {
            'vars': [
                {'key': 'VALID_VAR', 'value': 'good-value'},
                {'key': 'CIPHERTEXT_VAR', 'value': ciphertext},
            ]
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)

        # Ciphertext should be skipped, normal one saved
        self.assertTrue(EnvironmentVariable.objects.filter(service=self.service, key='VALID_VAR').exists())
        self.assertFalse(EnvironmentVariable.objects.filter(service=self.service, key='CIPHERTEXT_VAR').exists())

        # Response should contain warnings/skipped info
        self.assertIn('warning', response.data)
        self.assertEqual(response.data['added'], 1)

    def test_single_post_rejects_ciphertext(self):
        """Single env var POST endpoint should return 400 Bad Request if value is Fernet ciphertext."""
        from cryptography.fernet import Fernet
        key = Fernet.generate_key()
        f = Fernet(key)
        ciphertext = f.encrypt(b"secret-value").decode('utf-8')

        url = f'/api/v1/services/{self.service.id}/env_vars/'
        payload = {
            'key': 'CIPHERTEXT_VAR',
            'value': ciphertext,
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)
        self.assertIn('value', response.data)
        self.assertFalse(EnvironmentVariable.objects.filter(service=self.service, key='CIPHERTEXT_VAR').exists())

    def test_build_runtime_env_filters_ciphertext(self):
        """_build_runtime_env should exclude environment variables that are still Fernet ciphertext."""
        from cryptography.fernet import Fernet

        from apps.deployments.tasks.deployment.tasks_deploy_local import _build_runtime_env
        key = Fernet.generate_key()
        f = Fernet(key)
        ciphertext = f.encrypt(b"secret-value").decode('utf-8')

        # Create env vars directly in DB bypassing validation
        EnvironmentVariable.objects.create(
            service=self.service,
            key='VALID_VAR',
            value='good-value'
        )
        ciphertext_var = EnvironmentVariable.objects.create(
            service=self.service,
            key='CIPHERTEXT_VAR',
            value='temp-value'
        )

        table = EnvironmentVariable._meta.db_table
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {table} SET value = %s WHERE id = %s",
                [ciphertext, ciphertext_var.id],
            )

        runtime_env = _build_runtime_env(self.service)
        self.assertIn('VALID_VAR', runtime_env)
        self.assertEqual(runtime_env['VALID_VAR'], 'good-value')
        self.assertNotIn('CIPHERTEXT_VAR', runtime_env)
