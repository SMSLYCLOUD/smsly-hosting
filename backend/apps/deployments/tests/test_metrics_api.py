# pylint: disable=invalid-name
"""API tests for service metrics endpoint."""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service
from apps.deployments.models.metrics import ServiceMetric


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

    @patch('apps.deployments.views.metrics.metrics_adapter.get_current')
    @patch('apps.deployments.views.metrics.metrics_adapter.get_disk_history')
    @patch('apps.deployments.views.metrics.metrics_adapter.get_network_history')
    @patch('apps.deployments.views.metrics.metrics_adapter.get_memory_history')
    @patch('apps.deployments.views.metrics.metrics_adapter.get_cpu_history')
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

    @patch('apps.deployments.views.metrics.metrics_adapter.get_current')
    @patch('apps.deployments.views.metrics.metrics_adapter.get_disk_history')
    @patch('apps.deployments.views.metrics.metrics_adapter.get_network_history')
    @patch('apps.deployments.views.metrics.metrics_adapter.get_memory_history')
    @patch('apps.deployments.views.metrics.metrics_adapter.get_cpu_history')
    def test_metrics_endpoint_uses_db_fallback_when_prometheus_series_are_zero_only(
        self,
        cpu_mock,
        mem_mock,
        net_mock,
        disk_mock,
        current_mock,
    ):
        self.client.force_authenticate(user=self.user)
        zero_series = [{'timestamp': '2026-03-08T00:00:00+00:00', 'value': 0}]
        cpu_mock.return_value = zero_series
        mem_mock.return_value = zero_series
        net_mock.return_value = zero_series
        disk_mock.return_value = zero_series
        current_mock.return_value = {
            'cpu_percent': 0,
            'memory_usage': 0,
            'memory_limit': 0,
            'memory_percent': 0,
            'network_rx_kb': 0,
            'network_tx_kb': 0,
        }

        ServiceMetric.objects.create(
            service=self.service,
            timestamp=timezone.now(),
            cpu_usage=0.2,
            cpu_limit=2.0,
            memory_usage=128,
            memory_limit=512,
            network_rx_bytes=1024,
            network_tx_bytes=2048,
            disk_read_bytes=4096,
            disk_write_bytes=8192,
        )

        response = self.client.get(self.url, {'duration': '1h'})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get('source'), 'db_fallback')
        self.assertGreater(response.data.get('current', {}).get('cpu_percent', 0), 0)
