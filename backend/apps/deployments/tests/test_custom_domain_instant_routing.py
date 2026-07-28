# pylint: disable=invalid-name
"""Tests for instant custom-domain routing without redeploy."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from apps.deployments.services.caddy_manager import (
    apply_caddyfile,
    generate_caddyfile,
    validate_service_routes_do_not_hit_control_plane,
)

from apps.billing.models import PricingPlan, UserSubscription
from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, ManagedServer, Service
from apps.licensing.models import PlatformLicense, PlatformTier


class CaddyCustomDomainRoutingTests(TestCase):
    """Ensure custom domains route immediately through Caddy host rewrite."""

    def setUp(self):
        import apps.deployments.services.caddy_manager as caddy_mod
        caddy_mod._last_caddy_reload_ts = 0.0  # reset debounce between tests
        caddy_mod._last_caddy_content_hash = ""  # reset content-hash debounce

        self.user = User.objects.create_user(
            username='caddy-owner',
            email='caddy-owner@example.com',
            password='password123',
        )
        self.provider = CloudProvider.objects.create(
            name='caddy-provider',
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )

    def test_custom_domain_block_rewrites_host_to_public_domain(self):
        svc = Service.objects.create(
            name='buyforfront-caddy',
            owner=self.user,
            provider=self.provider,
            public_domain='buyforfront-0398be.cloud.smsly.cloud',
            custom_domains=['intelliphoton.com'],
        )
        from apps.domains.models import Domain, DomainStatus
        Domain.objects.create(
            domain_name='intelliphoton.com',
            service=svc,
            status=DomainStatus.DNS_VERIFIED,
            verified=True,
        )
        config = SimpleNamespace(
            domain='cloud.smsly.cloud',
            use_ssl=True,
            wildcard_subdomains=False,
            cloudflare_api_token='',
        )

        caddyfile = generate_caddyfile(config)

        self.assertIn('intelliphoton.com {', caddyfile)
        self.assertIn('tls {\n        on_demand\n    }', caddyfile)
        self.assertNotIn('dns cloudflare', caddyfile)
        self.assertIn('reverse_proxy traefik:80 {', caddyfile)
        self.assertIn(
            'header_up Host buyforfront-0398be.cloud.smsly.cloud',
            caddyfile,
        )
        self.assertNotIn(
            'intelliphoton.com {\n    tls {\n        on_demand\n    }\n    reverse_proxy backend:8000',
            caddyfile,
        )

    def test_public_service_domain_routes_to_traefik_not_platform_nginx(self):
        Service.objects.create(
            name='service-domain-caddy',
            owner=self.user,
            provider=self.provider,
            public_domain='service-domain.cloud.smsly.cloud',
        )
        config = SimpleNamespace(
            domain='cloud.smsly.cloud',
            use_ssl=True,
            wildcard_subdomains=False,
            cloudflare_api_token='',
        )

        caddyfile = generate_caddyfile(config)

        self.assertIn(
            'service-domain.cloud.smsly.cloud {\n    reverse_proxy traefik:80',
            caddyfile,
        )
        self.assertNotIn(
            'service-domain.cloud.smsly.cloud {\n    reverse_proxy backend:8000',
            caddyfile,
        )

    def test_platform_domain_stays_independent_from_wildcard_dns_challenge(self):
        config = SimpleNamespace(
            domain='cloud.smsly.cloud',
            use_ssl=True,
            wildcard_subdomains=True,
            cloudflare_api_token='token-123',
        )

        caddyfile = generate_caddyfile(config)

        self.assertIn('cloud.smsly.cloud {', caddyfile)
        self.assertIn('handle /api/* {\n        reverse_proxy backend:8000', caddyfile)
        self.assertIn('*.cloud.smsly.cloud {', caddyfile)
        self.assertEqual(
            caddyfile.count('dns cloudflare token-123'),
            1,
        )
        self.assertIn('reverse_proxy backend:8000', caddyfile)

    def test_standard_ssl_routes_unmatched_http_hosts_to_notice(self):
        config = SimpleNamespace(
            domain='cloud.smsly.cloud',
            use_ssl=True,
            wildcard_subdomains=False,
            cloudflare_api_token='',
        )

        caddyfile = generate_caddyfile(config)

        self.assertIn('cloud.smsly.cloud {', caddyfile)
        self.assertIn('handle /api/* {\n        reverse_proxy backend:8000', caddyfile)
        self.assertIn('handle {\n        reverse_proxy frontend:3000', caddyfile)
        self.assertIn('handle {\n        reverse_proxy backend:8000\n    }', caddyfile)

    def test_ip_mode_keeps_http_catch_all_proxy(self):
        config = SimpleNamespace(
            domain='163.245.214.62',
            use_ssl=False,
            wildcard_subdomains=False,
            cloudflare_api_token='',
        )

        caddyfile = generate_caddyfile(config)

        self.assertIn('http://163.245.214.62 {', caddyfile)
        self.assertIn('reverse_proxy frontend:3000', caddyfile)

    def test_wildcard_routes_known_hosts_and_sends_unknown_to_notice(self):
        Service.objects.create(
            name='known-wildcard-service',
            owner=self.user,
            provider=self.provider,
            public_domain='known.cloud.smsly.cloud',
        )
        config = SimpleNamespace(
            domain='cloud.smsly.cloud',
            use_ssl=True,
            wildcard_subdomains=True,
            cloudflare_api_token='token-123',
        )

        caddyfile = generate_caddyfile(config)

        self.assertIn('@known_hosts host known.cloud.smsly.cloud', caddyfile)
        self.assertIn('handle @known_hosts {\n        reverse_proxy traefik:80', caddyfile)
        self.assertIn('respond "Service Not Found" 404', caddyfile)

    def test_remote_service_routes_through_wireguard_mesh(self):
        server = ManagedServer.objects.create(
            owner=self.user,
            name='remote-worker',
            host='203.0.113.50',
            wg_address='10.150.0.2',
        )
        Service.objects.create(
            name='remote-wildcard-service',
            owner=self.user,
            provider=self.provider,
            server=server,
            public_domain='remote-api.cloud.smsly.cloud',
        )
        config = SimpleNamespace(
            domain='cloud.smsly.cloud',
            use_ssl=True,
            wildcard_subdomains=True,
            cloudflare_api_token='token-123',
        )

        caddyfile = generate_caddyfile(config)

        self.assertIn('@remote_hosts_0 host remote-api.cloud.smsly.cloud', caddyfile)
        self.assertIn('reverse_proxy http://10.150.0.2 http://203.0.113.50 {', caddyfile)
        self.assertIn('lb_try_duration 5s', caddyfile)
        self.assertIn('header_up Host {host}', caddyfile)

    def test_apply_caddyfile_rejects_service_domain_to_control_plane(self):
        Service.objects.create(
            name='guarded-service',
            owner=self.user,
            provider=self.provider,
            public_domain='guarded.cloud.smsly.cloud',
        )
        unsafe = """guarded.cloud.smsly.cloud {
    reverse_proxy backend:8000
}
"""

        errors = validate_service_routes_do_not_hit_control_plane(unsafe)
        result = apply_caddyfile(unsafe)

        self.assertTrue(errors)
        self.assertFalse(result['ok'])
        self.assertIn('Refusing to apply Caddyfile', result['message'])


class InstantCustomDomainApiTests(APITestCase):
    """Domain add/remove should sync routing instantly with no redeploy."""

    def setUp(self):
        from django.conf import settings as dj_settings

        license_obj = PlatformLicense.load()
        license_obj.tier = PlatformTier.PRO
        license_obj.is_valid = True
        license_obj.max_services = 100
        license_obj.max_team_members = 100
        license_obj.save(update_fields=['tier', 'is_valid', 'max_services', 'max_team_members'])

        self._secret_backup = dj_settings.CADDY_ASK_SECRET
        dj_settings.CADDY_ASK_SECRET = "instant-routing-test-secret"

        self.user = User.objects.create_user(
            username='domain-owner',
            email='domain-owner@example.com',
            password='password123',
        )
        self.provider = CloudProvider.objects.create(
            name='domain-provider',
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name='domain-api-service',
            owner=self.user,
            provider=self.provider,
            public_domain='domain-api-service-aaaaaa.cloud.smsly.cloud',
        )
        Deployment.objects.create(
            service=self.service,
            commit_hash='a' * 40,
            commit_message='active deployment',
            status=Deployment.Status.ACTIVE,
        )
        self.client.force_authenticate(user=self.user)

    def tearDown(self):
        from django.conf import settings as dj_settings
        dj_settings.CADDY_ASK_SECRET = self._secret_backup
        from django.core.cache import cache
        cache.clear()

    @patch('apps.deployments.views.service.deploy.smart_deploy_task.delay')
    @patch('apps.deployments.views.ServiceViewSet._sync_caddy', return_value={'ok': True, 'message': 'ok'})
    @patch('apps.domains.tasks.verify_dns_and_provision_ssl_task.delay')
    def test_add_domain_does_not_queue_redeploy(self, verify_mock, _sync_mock, delay_mock):
        response = self.client.post(
            f'/api/v1/services/{self.service.id}/add-domain/',
            {'domain': 'instant.example.com'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data.get('routing_sync_deployment_id'), None)
        self.assertFalse(response.data.get('requires_redeploy', True))
        self.assertIn('No redeploy required', response.data.get('message', ''))
        delay_mock.assert_not_called()

        self.service.refresh_from_db()
        self.assertIn('instant.example.com', self.service.custom_domains)
        self.assertEqual(self.service.deployments.count(), 1)

    @patch('apps.domains.services.dns.ensure_dns_records')
    @patch('apps.deployments.views.ServiceViewSet._sync_caddy', return_value={'ok': True, 'message': 'ok'})
    @patch('apps.domains.tasks.verify_dns_and_provision_ssl_task.delay')
    def test_add_domain_does_not_use_platform_cloudflare_for_custom_dns(
        self,
        verify_mock,
        _sync_mock,
        ensure_dns_mock,
    ):
        from apps.deployments.models import PlatformConfig

        cfg = PlatformConfig.load()
        cfg.cloudflare_api_token = 'platform-token'
        cfg.server_ip = '203.0.113.10'
        cfg.save(update_fields=['cloudflare_api_token', 'server_ip'])

        response = self.client.post(
            f'/api/v1/services/{self.service.id}/add-domain/',
            {'domain': 'customer-owned.example.com'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(response.data.get('dns_synced'))
        self.assertIn('SSL will be issued directly', response.data.get('message', ''))
        ensure_dns_mock.assert_not_called()
        verify_mock.assert_called_once()

    @patch('apps.deployments.views.service.deploy.smart_deploy_task.delay')
    @patch('apps.deployments.views.ServiceViewSet._sync_caddy', return_value={'ok': True, 'message': 'ok'})
    @patch('apps.domains.tasks.verify_dns_and_provision_ssl_task.delay')
    def test_delete_domain_does_not_queue_redeploy(self, verify_mock, _sync_mock, delay_mock):
        self.service.custom_domains = ['instant.example.com']
        self.service.save(update_fields=['custom_domains'])

        response = self.client.post(
            f'/api/v1/services/{self.service.id}/delete-domain/',
            {'domain': 'instant.example.com'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get('routing_sync_deployment_id'), None)
        self.assertFalse(response.data.get('requires_redeploy', True))
        self.assertIn('No redeploy required', response.data.get('message', ''))
        delay_mock.assert_not_called()

        self.service.refresh_from_db()
        self.assertNotIn('instant.example.com', self.service.custom_domains)
        self.assertEqual(self.service.deployments.count(), 1)

    @patch('apps.deployments.views.ServiceViewSet._sync_caddy', return_value={'ok': False, 'message': 'sync failed'})
    @patch('apps.domains.tasks.verify_dns_and_provision_ssl_task.delay')
    def test_add_domain_keeps_domain_when_caddy_sync_fails(self, verify_mock, _sync_mock):
        response = self.client.post(
            f'/api/v1/services/{self.service.id}/add-domain/',
            {'domain': 'rollback.example.com'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertFalse(response.data.get('caddy_synced'))
        self.service.refresh_from_db()
        self.assertIn('rollback.example.com', self.service.custom_domains)

    @patch('apps.deployments.views.ServiceViewSet._sync_caddy', return_value={'ok': False, 'message': 'sync failed'})
    @patch('apps.domains.tasks.verify_dns_and_provision_ssl_task.delay')
    def test_delete_domain_keeps_change_when_caddy_sync_fails(self, verify_mock, _sync_mock):
        self.service.custom_domains = ['rollback.example.com']
        self.service.save(update_fields=['custom_domains'])

        response = self.client.post(
            f'/api/v1/services/{self.service.id}/delete-domain/',
            {'domain': 'rollback.example.com'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertFalse(response.data.get('caddy_synced'))
        self.service.refresh_from_db()
        self.assertNotIn('rollback.example.com', self.service.custom_domains)

    @patch('apps.domains.tasks.verify_dns_and_provision_ssl_task.delay')
    def test_add_domain_rejects_global_conflict(self, verify_mock):
        other_service = Service.objects.create(
            name='domain-conflict-service',
            owner=self.user,
            provider=self.provider,
            public_domain='domain-conflict-service-bbbbbb.cloud.smsly.cloud',
            custom_domains=['taken.example.com'],
        )
        from apps.domains.models import Domain
        Domain.objects.create(domain_name='taken.example.com', service=other_service)

        response = self.client.post(
            f'/api/v1/services/{self.service.id}/add-domain/',
            {'domain': 'taken.example.com'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('already assigned', response.data.get('error', ''))

    @patch('apps.domains.tasks.verify_dns_and_provision_ssl_task.delay')
    def test_add_domain_enforces_plan_quota(self, verify_mock):
        plan = PricingPlan.objects.create(
            name='Starter',
            slug='starter',
            price_monthly_usd='9.00',
            price_yearly_usd='90.00',
            max_custom_domains=1,
        )
        now = timezone.now()
        UserSubscription.objects.create(
            user=self.user,
            plan=plan,
            status='ACTIVE',
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        self.service.custom_domains = ['one.example.com']
        self.service.save(update_fields=['custom_domains'])

        response = self.client.post(
            f'/api/v1/services/{self.service.id}/add-domain/',
            {'domain': 'two.example.com'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn('limit reached', response.data.get('error', ''))

    def test_caddy_ask_rejects_pending_custom_domain(self):
        from apps.domains.models import Domain, DomainStatus

        self.service.custom_domains = ['pending.example.com']
        self.service.save(update_fields=['custom_domains'])
        Domain.objects.create(
            domain_name='pending.example.com',
            service=self.service,
            status=DomainStatus.PENDING,
            verified=False,
        )

        response = self.client.get(
            '/api/v1/services/check-domain/',
            {'domain': 'pending.example.com'},
            HTTP_X_CADDY_SECRET='instant-routing-test-secret',
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_caddy_ask_accepts_dns_verified_custom_domain(self):
        from apps.domains.models import Domain, DomainStatus

        Domain.objects.create(
            domain_name='verified.example.com',
            service=self.service,
            status=DomainStatus.DNS_VERIFIED,
            verified=True,
        )

        response = self.client.get(
            '/api/v1/services/check-domain/',
            {'domain': 'verified.example.com'},
            HTTP_X_CADDY_SECRET='instant-routing-test-secret',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
