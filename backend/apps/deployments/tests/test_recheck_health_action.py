# pylint: disable=invalid-name
"""Tests for manual health recheck endpoint."""

from unittest.mock import patch

from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service


class RecheckHealthActionTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username='health-owner',
            email='health-owner@example.com',
            password='password123',
        )
        self.other = User.objects.create_user(
            username='health-other',
            email='health-other@example.com',
            password='password123',
        )
        self.provider = CloudProvider.objects.create(
            name='local-provider',
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name='recheck-health-service',
            owner=self.owner,
            provider=self.provider,
            health_status='unhealthy',
        )

    @patch('apps.core.services.health_monitor._check_service_health')
    @patch('apps.core.services.health_monitor.reset_restart_state')
    def test_owner_can_trigger_recheck(self, reset_state_mock, check_mock):
        self.client.force_authenticate(user=self.owner)
        url = f'/api/v1/services/{self.service.id}/recheck-health/'

        response = self.client.post(url, {'reset_backoff': True}, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(str(response.data.get('service_id')), str(self.service.id))
        self.assertIn('health_status', response.data)
        reset_state_mock.assert_called_once_with(str(self.service.id))
        check_mock.assert_called_once()

    def test_non_owner_cannot_trigger_recheck(self):
        self.client.force_authenticate(user=self.other)
        url = f'/api/v1/services/{self.service.id}/recheck-health/'

        response = self.client.post(url, {'reset_backoff': True}, format='json')

        # Service queryset is owner-scoped, so non-owner should not see it.
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

