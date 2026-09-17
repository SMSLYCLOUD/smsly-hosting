"""cpu_limit must be the service allocation, not host cores.

Regression (2026-09-18): _collect_container_stats stored the daemon's
host-wide CPU count (8) as the row's cpu_limit while the service was
allocated 3 cores. Every consumer divides usage by that limit, so CPU%
topped out at 37.5% and CPU-based autoscaling could never fire.
"""
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.core.tasks.metrics import _collect_container_stats

GiB = 1024 * 1024 * 1024


def _docker_stats():
    return {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 3_000_000_000},
            "system_cpu_usage": 8_000_000_000,
            "online_cpus": 8,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": 2_000_000_000},
            "system_cpu_usage": 7_000_000_000,
        },
        "memory_stats": {"usage": 111 * 1024 * 1024, "limit": 5 * GiB},
        "networks": {},
        "blkio_stats": {"io_service_bytes_recursive": []},
    }


def _client():
    client = MagicMock()
    client.containers.get.return_value.stats.return_value = _docker_stats()
    return client


class CpuLimitOverrideTests(SimpleTestCase):
    @patch("apps.core.tasks.metrics._get_docker_client")
    def test_service_allocation_used_as_limit(self, mock_client_fn):
        mock_client_fn.return_value = _client()
        stats = _collect_container_stats("abc123", 3.0)
        self.assertEqual(stats["cpu_limit"], 3.0)
        # usage = (3e9-2e9)/(8e9-7e9) * 8 = 8.0 cores
        self.assertAlmostEqual(stats["cpu_usage"], 8.0)
        self.assertEqual(stats["memory_usage"], 111)
        self.assertEqual(stats["memory_limit"], 5120)

    @patch("apps.core.tasks.metrics._get_docker_client")
    def test_host_count_fallback_without_override(self, mock_client_fn):
        mock_client_fn.return_value = _client()
        stats = _collect_container_stats("abc123")
        self.assertEqual(stats["cpu_limit"], 8.0)

    @patch("apps.core.tasks.metrics._get_docker_client")
    def test_garbage_override_falls_back_to_host(self, mock_client_fn):
        mock_client_fn.return_value = _client()
        stats = _collect_container_stats("abc123", "not-a-number")
        self.assertEqual(stats["cpu_limit"], 8.0)
