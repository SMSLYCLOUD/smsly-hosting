"""Tests for mTLS models and views."""
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.deployments.models import Service
from apps.mtls.models import MtlsConfig

User = get_user_model()


class MtlsConfigModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='mtls-tester', email='mtls@test.com', password='pass123'
        )
        self.service = Service.objects.create(
            name='test-svc', owner=self.user, deploy_type='DOCKER'
        )

    def test_spiffe_id_auto_generated(self):
        config = MtlsConfig.objects.create(service=self.service)
        self.assertEqual(config.spiffe_id, 'spiffe://platform.local/service/test-svc')

    def test_custom_trust_domain(self):
        config = MtlsConfig.objects.create(
            service=self.service, trust_domain='custom.example.com'
        )
        self.assertEqual(config.spiffe_id, 'spiffe://custom.example.com/service/test-svc')

    def test_is_svid_expired_false_when_no_expiry(self):
        config = MtlsConfig.objects.create(service=self.service)
        self.assertFalse(config.is_svid_expired)

    def test_is_svid_expired_true_when_past(self):
        config = MtlsConfig.objects.create(
            service=self.service, svid_expiry=timezone.now() - timezone.timedelta(hours=1)
        )
        self.assertTrue(config.is_svid_expired)

    def test_is_svid_expired_false_when_future(self):
        config = MtlsConfig.objects.create(
            service=self.service, svid_expiry=timezone.now() + timezone.timedelta(hours=1)
        )
        self.assertFalse(config.is_svid_expired)

    def test_svid_ttl_remaining_zero_when_no_expiry(self):
        config = MtlsConfig.objects.create(service=self.service)
        self.assertEqual(config.svid_ttl_remaining, 0)

    def test_svid_ttl_remaining_positive_when_future(self):
        config = MtlsConfig.objects.create(
            service=self.service, svid_expiry=timezone.now() + timezone.timedelta(hours=2)
        )
        self.assertGreater(config.svid_ttl_remaining, 0)

    def test_str_representation(self):
        config = MtlsConfig.objects.create(service=self.service, enabled=True)
        self.assertIn('enabled', str(config))
        config.enabled = False
        config.save()
        self.assertIn('disabled', str(config))


class MtlsStatusApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='mtls-api', email='mtls-api@test.com', password='pass123'
        )
        self.service = Service.objects.create(
            name='api-svc', owner=self.user, deploy_type='DOCKER'
        )

    def test_mtls_status_returns_401_unauthenticated(self):
        resp = self.client.get(f'/api/v1/services/{self.service.id}/mtls/status/')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_mtls_status_returns_config(self):
        self.client.force_authenticate(self.user)
        MtlsConfig.objects.create(service=self.service, enabled=True)
        resp = self.client.get(f'/api/v1/services/{self.service.id}/mtls/status/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data.get('enabled'))

    def test_mtls_status_returns_404_for_missing_service(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get('/api/v1/services/00000000-0000-0000-0000-000000000000/mtls/status/')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
