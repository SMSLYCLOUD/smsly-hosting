"""Metrics responses carry the service CPU allocation (cpu_limit).

The dashboard renders the allocation next to CPU% and flags throttling;
every response shape (prometheus path, db fallback, docker-live fallback,
error shape) must include it.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.autoscaler.models.metrics import ServiceMetric
from apps.autoscaler.views.metrics import _db_metrics_fallback, _live_metrics_fallback
from apps.deployments.models import Service

User = get_user_model()


class MetricsCpuLimitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="metricslimit", password="x")
        self.service = Service.objects.create(
            name="metricssvc", owner=self.user, cpu_cores=3,
        )

    def test_db_fallback_includes_cpu_limit(self):
        ServiceMetric.objects.create(
            service=self.service,
            cpu_usage=1.5, cpu_limit=3,
            memory_usage=256, memory_limit=512,
            network_rx_bytes=1024, network_tx_bytes=2048,
            disk_read_bytes=0, disk_write_bytes=0,
            timestamp=timezone.now(),
        )
        payload = _db_metrics_fallback(self.service, '1h')
        self.assertIsNotNone(payload)
        self.assertEqual(payload['current']['cpu_limit'], 3.0)

    def test_live_fallback_includes_cpu_limit(self):
        fake_stats = {
            'cpu_limit': 3.0, 'cpu_usage': 1.5,
            'memory_usage': 256.0, 'memory_limit': 512.0,
            'network_rx_bytes': 1024.0, 'network_tx_bytes': 2048.0,
            'disk_read_bytes': 0.0, 'disk_write_bytes': 0.0,
        }
        with mock.patch(
            'apps.deployments.utils.target.resolve_active_execution_target',
            return_value={'target_type': 'local', 'runtime_id': 'abc123'},
        ), mock.patch(
            'apps.core.tasks.metrics._collect_container_stats',
            return_value=fake_stats,
        ):
            payload = _live_metrics_fallback(self.service)
        self.assertIsNotNone(payload)
        self.assertEqual(payload['current']['cpu_limit'], 3.0)
        self.assertEqual(payload['current']['cpu_percent'], 50.0)
