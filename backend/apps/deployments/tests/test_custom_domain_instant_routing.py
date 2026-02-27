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

from apps.billing.models import PricingPlan, UserSubscription
from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service
from services.caddy_manager import generate_caddyfile


class CaddyCustomDomainRoutingTests(TestCase):
    """Ensure custom domains route immediately through Caddy host rewrite."""

    def setUp(self):
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
        Service.objects.create(
            name='buyforfront-caddy',
            owner=self.user,
            provider=self.provider,
            public_domain='buyforfront-0398be.cloud.smsly.cloud',
            custom_domains=['intelliphoton.com'],
        )
        config = SimpleNamespace(
            domain='cloud.smsly.cloud',
            use_ssl=True,
            wildcard_subdomains=False,
            cloudflare_api_token='',
        )

        caddyfile = generate_caddyfile(config)

        self.assertIn('intelliphoton.com {', caddyfile)
        self.assertIn('reverse_proxy localhost:8081 {', caddyfile)
        self.assertIn(
            'header_up Host buyforfront-0398be.cloud.smsly.cloud',
            caddyfile,
        )


class InstantCustomDomainApiTests(APITestCase):
    """Domain add/remove should sync routing instantly with no redeploy."""

    def setUp(self):
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

    @patch('apps.deployments.views.smart_deploy_task.delay')
    @patch('apps.deployments.views.ServiceViewSet._sync_caddy', return_value=True)
    def test_add_domain_does_not_queue_redeploy(self, _sync_mock, delay_mock):
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

    @patch('apps.deployments.views.smart_deploy_task.delay')
    @patch('apps.deployments.views.ServiceViewSet._sync_caddy', return_value=True)
    def test_delete_domain_does_not_queue_redeploy(self, _sync_mock, delay_mock):
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

    @patch('apps.deployments.views.ServiceViewSet._sync_caddy', return_value=False)
    def test_add_domain_rolls_back_when_caddy_sync_fails(self, _sync_mock):
        response = self.client.post(
            f'/api/v1/services/{self.service.id}/add-domain/',
            {'domain': 'rollback.example.com'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.service.refresh_from_db()
        self.assertEqual(self.service.custom_domains or [], [])

    @patch('apps.deployments.views.ServiceViewSet._sync_caddy', return_value=False)
    def test_delete_domain_rolls_back_when_caddy_sync_fails(self, _sync_mock):
        self.service.custom_domains = ['rollback.example.com']
        self.service.save(update_fields=['custom_domains'])

        response = self.client.post(
            f'/api/v1/services/{self.service.id}/delete-domain/',
            {'domain': 'rollback.example.com'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.service.refresh_from_db()
        self.assertIn('rollback.example.com', self.service.custom_domains)

    def test_add_domain_rejects_global_conflict(self):
        Service.objects.create(
            name='domain-conflict-service',
            owner=self.user,
            provider=self.provider,
            public_domain='domain-conflict-service-bbbbbb.cloud.smsly.cloud',
            custom_domains=['taken.example.com'],
        )

        response = self.client.post(
            f'/api/v1/services/{self.service.id}/add-domain/',
            {'domain': 'taken.example.com'},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('already assigned', response.data.get('error', ''))

    def test_add_domain_enforces_plan_quota(self):
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
