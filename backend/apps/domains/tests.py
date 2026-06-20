from unittest.mock import patch

from apps.cloud.models import CloudProvider
from apps.deployments.models import PlatformConfig, Service
from apps.domains.models import Domain, DomainStatus
from django.contrib.auth import get_user_model
from django.test import TestCase

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

    @patch('apps.domains.verification.resolve_cname_chain', return_value=[])
    @patch('apps.domains.verification.resolve_host_ips')
    @patch('apps.domains.tasks._trigger_caddy_reload')
    def test_dns_verification_success(self, reload_mock, resolve_ips_mock, _chain_mock):
        resolve_ips_mock.return_value = {"1.2.3.4"}
        domain = Domain.objects.create(
            domain_name="my-custom-domain.com",
            service=self.service
        )

        from apps.domains.tasks import verify_dns_and_provision_ssl_task
        verify_dns_and_provision_ssl_task(domain.id)

        domain.refresh_from_db()
        self.assertEqual(domain.status, DomainStatus.DNS_VERIFIED)
        self.assertTrue(domain.verified)
        self.assertEqual(domain.dns_actual, "A/AAAA records 1.2.3.4")
        reload_mock.assert_called_once()

    @patch('apps.domains.verification.resolve_cname_chain', return_value=[])
    @patch('apps.domains.verification.resolve_host_ips')
    @patch('apps.domains.tasks._trigger_caddy_reload')
    def test_dns_verification_failure(self, reload_mock, resolve_ips_mock, _chain_mock):
        resolve_ips_mock.return_value = {"5.6.7.8"}
        domain = Domain.objects.create(
            domain_name="my-custom-domain.com",
            service=self.service
        )

        from apps.domains.tasks import verify_dns_and_provision_ssl_task
        verify_dns_and_provision_ssl_task(domain.id)

        domain.refresh_from_db()
        self.assertEqual(domain.status, DomainStatus.DNS_PENDING)
        self.assertFalse(domain.verified)
        self.assertEqual(domain.dns_actual, "A/AAAA records 5.6.7.8")
        self.assertIn("A/AAAA record to 1.2.3.4", domain.last_error)
        reload_mock.assert_not_called()

    @patch('apps.domains.verification.resolve_cname_chain')
    @patch('apps.domains.verification.resolve_host_ips', return_value=set())
    @patch('apps.domains.tasks._trigger_caddy_reload')
    def test_dns_verification_accepts_service_public_domain_cname(
        self,
        reload_mock,
        _resolve_ips_mock,
        resolve_cname_chain_mock,
    ):
        resolve_cname_chain_mock.return_value = ["my-service.cloud.smsly.cloud"]
        domain = Domain.objects.create(
            domain_name="my-custom-domain.com",
            service=self.service,
        )

        from apps.domains.tasks import verify_dns_and_provision_ssl_task
        verify_dns_and_provision_ssl_task(domain.id)

        domain.refresh_from_db()
        self.assertEqual(domain.status, DomainStatus.DNS_VERIFIED)
        self.assertTrue(domain.verified)
        self.assertIn("CNAME chain my-service.cloud.smsly.cloud", domain.dns_actual)
        reload_mock.assert_called_once()
