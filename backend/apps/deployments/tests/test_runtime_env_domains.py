# pylint: disable=invalid-name
"""Regression tests for deployment runtime domain env assembly."""

from django.contrib.auth.models import User
from django.test import TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import EnvironmentVariable, Service
from apps.deployments.tasks.deploy.build_compose import _build_runtime_env


class RuntimeEnvDomainAssemblyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='env-owner',
            email='env-owner@example.com',
            password='password123',
        )
        self.provider = CloudProvider.objects.create(
            name='local-provider-env',
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name='env-domain-service',
            owner=self.user,
            provider=self.provider,
            internal_port=8000,
        )

    def test_service_domains_override_stale_env_values(self):
        EnvironmentVariable.objects.create(
            service=self.service,
            key='PUBLIC_DOMAIN',
            value='old.example.com',
            is_secret=False,
        )
        EnvironmentVariable.objects.create(
            service=self.service,
            key='CUSTOM_DOMAINS',
            value='buyforfront.com',
            is_secret=False,
        )

        self.service.public_domain = 'buyforfront-0398be.cloud.smsly.cloud'
        self.service.custom_domains = ['intelliphoton.com', 'api.intelliphoton.com']
        self.service.save(update_fields=['public_domain', 'custom_domains'])

        env = _build_runtime_env(self.service)

        self.assertEqual(env['PUBLIC_DOMAIN'], 'buyforfront-0398be.cloud.smsly.cloud')
        self.assertEqual(env['CUSTOM_DOMAINS'], 'intelliphoton.com,api.intelliphoton.com')

    def test_stale_custom_domains_env_removed_when_service_has_none(self):
        EnvironmentVariable.objects.create(
            service=self.service,
            key='CUSTOM_DOMAINS',
            value='legacy.example.com',
            is_secret=False,
        )

        self.service.custom_domains = []
        self.service.save(update_fields=['custom_domains'])

        env = _build_runtime_env(self.service)

        self.assertNotIn('CUSTOM_DOMAINS', env)
        self.assertEqual(env['PORT'], '8000')

    def test_explicit_port_env_persists_to_service(self):
        EnvironmentVariable.objects.create(
            service=self.service,
            key='PORT',
            value='3000',
            is_secret=False,
        )
        self.assertEqual(self.service.internal_port, 8000)
        env = _build_runtime_env(self.service)
        self.service.refresh_from_db()
        self.assertEqual(env['PORT'], '3000')
        self.assertEqual(self.service.internal_port, 3000)

    def test_detected_port_persists_to_service(self):
        from unittest.mock import patch
        self.assertEqual(self.service.internal_port, 8000)
        with patch('apps.deployments.tasks.deploy.build_compose._detect_exposed_port', return_value=4000):
            env = _build_runtime_env(self.service)
        self.service.refresh_from_db()
        self.assertEqual(env['PORT'], '4000')
        self.assertEqual(self.service.internal_port, 4000)
