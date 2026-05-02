import unittest
from django.test import TestCase
from apps.domains.models import Domain, DomainStatus
from apps.deployments.models import Service, PlatformConfig
from apps.cloud.models import CloudProvider
from django.contrib.auth import get_user_model
from unittest.mock import patch

User = get_user_model()

class DnsVerificationTests(TestCase):
    def setUp(self):
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

        cfg = PlatformConfig.load()
        cfg.server_ip = "1.2.3.4"
        cfg.save()

    @patch('socket.gethostbyname')
    @patch('apps.domains.tasks._trigger_caddy_reload')
    def test_dns_verification_success(self, reload_mock, gethostbyname_mock):
        gethostbyname_mock.return_value = "1.2.3.4"
        domain = Domain.objects.create(
            domain_name="my-custom-domain.com",
            service=self.service
        )

        from apps.domains.tasks import verify_dns_and_provision_ssl_task
        verify_dns_and_provision_ssl_task(domain.id)

        domain.refresh_from_db()
        self.assertEqual(domain.status, DomainStatus.DNS_VERIFIED)
        self.assertTrue(domain.verified)
        self.assertEqual(domain.dns_actual, "A record to 1.2.3.4")
        reload_mock.assert_called_once()

    @patch('socket.gethostbyname')
    @patch('apps.domains.tasks._trigger_caddy_reload')
    def test_dns_verification_failure(self, reload_mock, gethostbyname_mock):
        gethostbyname_mock.return_value = "5.6.7.8"
        domain = Domain.objects.create(
            domain_name="my-custom-domain.com",
            service=self.service
        )

        from apps.domains.tasks import verify_dns_and_provision_ssl_task
        verify_dns_and_provision_ssl_task(domain.id)

        domain.refresh_from_db()
        self.assertEqual(domain.status, DomainStatus.DNS_PENDING)
        self.assertFalse(domain.verified)
        self.assertEqual(domain.dns_actual, "A record to 5.6.7.8")
        self.assertIn("Expected A record to 1.2.3.4 but got A record to 5.6.7.8.", domain.last_error)
        reload_mock.assert_not_called()
