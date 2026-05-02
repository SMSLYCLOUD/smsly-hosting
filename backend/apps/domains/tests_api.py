import unittest
from django.test import TestCase, Client, override_settings
from apps.deployments.models import Service, PlatformConfig
from apps.domains.models import Domain, DomainStatus
from apps.cloud.models import CloudProvider
from django.contrib.auth import get_user_model
from unittest.mock import patch

User = get_user_model()

@override_settings(SMSLY_DISABLE_SIGNATURE_CHECK=True)
class DnsApiTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='caddy-owner3',
            email='caddy-owner3@example.com',
            password='password123',
        )
        self.provider = CloudProvider.objects.create(
            name='caddy-provider3',
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name='my-service',
            owner=self.user,
            provider=self.provider,
            public_domain='my-service.cloud.smsly.cloud',
            custom_domains=['my-custom-domain.com'],
        )
        self.domain = Domain.objects.create(
            domain_name="my-custom-domain.com",
            service=self.service,
            status=DomainStatus.SSL_FAILED
        )
        self.client.force_login(self.user)

    @patch('apps.domains.tasks.verify_dns_and_provision_ssl_task.delay')
    def test_retry_domain(self, delay_mock):
        response = self.client.post(f'/api/v1/services/{self.service.id}/retry-domain/', {'domain': 'my-custom-domain.com'})
        self.assertEqual(response.status_code, 200)
        delay_mock.assert_called_once_with(self.domain.id)
