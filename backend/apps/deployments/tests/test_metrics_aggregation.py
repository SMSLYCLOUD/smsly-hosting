# pylint: disable=invalid-name
"""Regression tests for Issue 42 (DB-side aggregation in _db_metrics_fallback).

The fallback used to load up to 1200 full ORM objects per service and
build the time series + current snapshot in Python. With ~200 services
that's up to 240k rows of DecimalField/BigIntegerField kept in memory
just to compute summary stats.

The fix is a single ``.aggregate()`` call that pulls avg/max/count
from the database in one round trip.
"""

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service
from apps.deployments.models.metrics import ServiceMetric
from apps.deployments.views.metrics import _db_metrics_fallback


class DbMetricsFallbackAggregationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='metric-agg',
            email='metric-agg@example.com',
            password='password123',
        )
        self.provider = CloudProvider.objects.create(
            name='metric-agg-provider',
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name='metric-agg-svc',
            owner=self.user,
            provider=self.provider,
        )
        self.now = timezone.now()
        for i, (cpu_use, cpu_lim, mem_use, mem_lim) in enumerate(
            [(0.2, 2.0, 128, 512), (0.6, 2.0, 384, 512)]
        ):
            ServiceMetric.objects.create(
                service=self.service,
                timestamp=self.now - timezone.timedelta(seconds=10 - i),
                cpu_usage=cpu_use,
                cpu_limit=cpu_lim,
                memory_usage=mem_use,
                memory_limit=mem_lim,
                network_rx_bytes=1024 * (i + 1),
                network_tx_bytes=2048 * (i + 1),
                disk_read_bytes=4096 * (i + 1),
                disk_write_bytes=8192 * (i + 1),
            )

    def test_returns_none_when_no_rows(self):
        ServiceMetric.objects.filter(service=self.service).delete()
        result = _db_metrics_fallback(self.service, '1h')
        self.assertIsNone(result)

    def test_returns_summary_shape(self):
        result = _db_metrics_fallback(self.service, '1h')
        self.assertIsNotNone(result)
        self.assertEqual(result['source'], 'db_fallback')
        for key in (
            'sample_count',
            'cpu_avg',
            'cpu_max',
            'memory_avg_mb',
            'memory_max_mb',
            'memory_percent_avg',
            'memory_percent_max',
            'network_avg_kb',
            'network_max_kb',
            'disk_avg_kb',
            'disk_max_kb',
            'current',
        ):
            self.assertIn(key, result)
        self.assertEqual(result['sample_count'], 2)
        # cpu_usage_max = 0.6, cpu_limit_max = 2.0 → 30.0%
        self.assertEqual(result['cpu_max'], 30.0)
        self.assertEqual(result['current']['cpu_percent'], 30.0)
        self.assertEqual(result['current']['memory_usage'], 384)
        self.assertEqual(result['current']['memory_limit'], 512)
        # 384 / 512 * 100 = 75.0
        self.assertEqual(result['current']['memory_percent'], 75.0)

    def test_issues_only_one_aggregate_query(self):
        """The fallback must do its work in a single DB round trip."""
        with self.assertNumQueries(1):
            result = _db_metrics_fallback(self.service, '1h')
        self.assertIsNotNone(result)
        self.assertEqual(result['sample_count'], 2)

    def test_aggregate_is_mockable(self):
        """The function exposes ``aggregate`` as the integration point
        that test code can patch for hermetic coverage of callers."""
        with patch(
            'apps.deployments.views.metrics.ServiceMetric.objects'
        ) as mock_manager:
            mock_qs = mock_manager.filter.return_value
            mock_qs.aggregate.return_value = {
                'cpu_usage_avg': 0.4, 'cpu_usage_max': 0.6,
                'cpu_limit_avg': 2.0, 'cpu_limit_max': 2.0,
                'memory_usage_avg': 256, 'memory_usage_max': 384,
                'memory_limit_avg': 512, 'memory_limit_max': 512,
                'network_rx_avg': 1536, 'network_rx_max': 2048,
                'network_tx_avg': 3072, 'network_tx_max': 4096,
                'disk_read_avg': 6144, 'disk_read_max': 8192,
                'disk_write_avg': 12288, 'disk_write_max': 16384,
                'sample_count': 2,
            }
            result = _db_metrics_fallback(self.service, '1h')

        self.assertEqual(result['sample_count'], 2)
        self.assertEqual(result['current']['cpu_percent'], 30.0)
        mock_qs.aggregate.assert_called_once()
