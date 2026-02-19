"""API tests for service metrics endpoint."""

from unittest.mock import patch

from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service


class MetricsApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='metrics-user',
            email='metrics-user@example.com',
            password='password123',
        )
        self.provider = CloudProvider.objects.create(
            name='metrics-provider',
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name='metrics-svc',
            owner=self.user,
            provider=self.provider,
        )
        self.url = f'/api/v1/services/{self.service.id}/metrics/'

    @patch('apps.deployments.views_metrics.metrics_adapter.get_current')
    @patch('apps.deployments.views_metrics.metrics_adapter.get_disk_history')
    @patch('apps.deployments.views_metrics.metrics_adapter.get_network_history')
    @patch('apps.deployments.views_metrics.metrics_adapter.get_memory_history')
    @patch('apps.deployments.views_metrics.metrics_adapter.get_cpu_history')
    def test_metrics_endpoint_returns_expected_shape(
        self,
        cpu_mock,
        mem_mock,
        net_mock,
        disk_mock,
        current_mock,
    ):
        self.client.force_authenticate(user=self.user)
        sample_series = [{'timestamp': '2026-02-19T00:00:00+00:00', 'value': 10.5}]
        cpu_mock.return_value = sample_series
        mem_mock.return_value = sample_series
        net_mock.return_value = sample_series
        disk_mock.return_value = sample_series
        current_mock.return_value = {
            'cpu_percent': 10.5,
            'memory_usage': 128.0,
            'memory_limit': 512.0,
            'memory_percent': 25.0,
            'network_rx_kb': 2.0,
            'network_tx_kb': 1.5,
        }

        response = self.client.get(self.url, {'duration': '1h'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for key in ('cpu', 'memory', 'network', 'disk', 'current'):
            self.assertIn(key, response.data)

