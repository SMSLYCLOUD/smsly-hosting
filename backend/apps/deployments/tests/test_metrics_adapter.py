# pylint: disable=invalid-name
"""Unit tests for deployments metrics adapter."""

from unittest.mock import patch

from django.test import SimpleTestCase

from apps.deployments.metrics.adapter import MetricsAdapter


class MetricsAdapterTests(SimpleTestCase):
    def setUp(self):
        self.adapter = MetricsAdapter()

    @patch.object(MetricsAdapter, '_query_prometheus', return_value=None)
    def test_disk_history_returns_empty_when_unavailable(self, _mock_query):
        data = self.adapter.get_disk_history('svc-1', '1h')
        self.assertEqual(data, [])

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
        self.assertEqual(snapshot['cpu_percent'], 0.0)
        self.assertEqual(snapshot['memory_usage'], 0.0)
        self.assertEqual(snapshot['memory_limit'], 0.0)
