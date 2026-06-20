# pylint: disable=invalid-name
"""Tests for SEC (Issue 63): ecosystem_bulk_env secret detection is case-insensitive."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.cloud.models import CloudProvider
from apps.deployments.models import EnvironmentVariable, Service

User = get_user_model()


class EcosystemBulkEnvSecretTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="bulk-env", password="pw", email="u@e.com"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.provider = CloudProvider.objects.create(
            name="bulk-prov", provider_type="LOCAL", is_active=True
        )
        self.service = Service.objects.create(
            name="bulk-svc",
            repository_url="https://github.com/x/y",
            owner=self.user,
            provider=self.provider,
        )
        self.url = "/api/v1/cloud/ecosystem/bulk-env/"

    def _post(self, env_vars):
        return self.client.post(
            self.url,
            {"service_ids": [str(self.service.id)], "env_vars": env_vars},
            format="json",
        )

    def test_lowercase_token_is_secret(self):
        """Lowercase 'token' is treated as a secret key (case-insensitive)."""
        response = self._post({"token": "mysecretvalue123"})
        self.assertEqual(response.status_code, 200)
        env = EnvironmentVariable.objects.get(service=self.service, key="TOKEN")
        self.assertTrue(env.is_secret)

    def test_mixed_case_password_is_secret(self):
        response = self._post({"Password": "hunter2hunter2"})
        self.assertEqual(response.status_code, 200)
        env = EnvironmentVariable.objects.get(service=self.service, key="PASSWORD")
        self.assertTrue(env.is_secret)

    def test_lowercase_api_key_is_secret(self):
        response = self._post({"api_key": "abc123"})
        self.assertEqual(response.status_code, 200)
        env = EnvironmentVariable.objects.get(service=self.service, key="API_KEY")
        self.assertTrue(env.is_secret)

    def test_plain_non_secret_key_is_not_secret(self):
        response = self._post({"APP_NAME": "billing"})
        self.assertEqual(response.status_code, 200)
        env = EnvironmentVariable.objects.get(service=self.service, key="APP_NAME")
        self.assertFalse(env.is_secret)

    def test_url_suffixed_key_is_secret(self):
        """'URL' in the new pattern triggers secret flag for DB-style vars."""
        response = self._post({"database_url": "postgres://u:p@h/d"})
        self.assertEqual(response.status_code, 200)
        env = EnvironmentVariable.objects.get(service=self.service, key="DATABASE_URL")
        self.assertTrue(env.is_secret)
