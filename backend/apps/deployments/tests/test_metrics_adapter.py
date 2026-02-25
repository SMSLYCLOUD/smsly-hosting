# pylint: disable=invalid-name
"""Unit tests for deployments metrics adapter."""

from unittest.mock import patch

from django.test import SimpleTestCase

from apps.deployments.metrics.adapter import MetricsAdapter


class MetricsAdapterTests(SimpleTestCase):
    def setUp(self):
        self.adapter = MetricsAdapter()

    @patch.object(MetricsAdapter, '_query_prometheus', return_value=None)
    def test_disk_history_falls_back_to_mock(self, _mock_query):
        data = self.adapter.get_disk_history('svc-1', '1h')
        self.assertTrue(data)
        self.assertIn('timestamp', data[0])
        self.assertIn('value', data[0])

    @patch.object(MetricsAdapter, '_query_prometheus', return_value=None)
    def test_get_current_returns_expected_shape(self, _mock_query):
        snapshot = self.adapter.get_current('svc-1')
        expected_keys = {
            'cpu_percent',
            'memory_usage',
            'memory_limit',
            'memory_percent',
            'network_rx_kb',
            'network_tx_kb',
        }
        self.assertEqual(set(snapshot.keys()), expected_keys)

