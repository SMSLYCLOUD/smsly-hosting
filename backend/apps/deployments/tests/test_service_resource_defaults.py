"""Unit tests for host-scaled service resource defaults (no DB)."""
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.deployments.models.service import (
    default_service_cpu_cores,
    default_service_memory_mb,
    default_service_resources,
)


def _mem(total_gb):
    m = MagicMock()
    m.total = total_gb * (1024 ** 3)
    return m


class TestDefaultServiceResources(TestCase):
    def _tiers(self, cores, mem_gb):
        with patch("os.cpu_count", return_value=cores), \
             patch("psutil.virtual_memory", return_value=_mem(mem_gb)):
            return default_service_resources()

    def test_big_host_gets_3_cores(self):
        self.assertEqual(self._tiers(8, 32), (3.0, 6144))

    def test_medium_host_gets_2_cores(self):
        self.assertEqual(self._tiers(4, 8), (2.0, 4096))

    def test_small_host_floor_is_2_cores(self):
        cpu, mem = self._tiers(2, 4)
        self.assertGreaterEqual(cpu, 2.0)
        self.assertGreaterEqual(mem, 2048)

    def test_probe_failure_falls_back_to_floor(self):
        with patch("os.cpu_count", side_effect=OSError("nope")), \
             patch("psutil.virtual_memory", side_effect=OSError("nope")):
            self.assertEqual(default_service_resources(), (2.0, 2048))

    def test_model_default_callables_meet_floor(self):
        from decimal import Decimal
        self.assertGreaterEqual(float(default_service_cpu_cores()), 2.0)
        self.assertIsInstance(default_service_cpu_cores(), Decimal)
        self.assertGreaterEqual(int(default_service_memory_mb()), 2048)

    def test_model_fields_use_the_callables(self):
        from apps.deployments.models import Service
        cpu_field = Service._meta.get_field('cpu_cores')
        mem_field = Service._meta.get_field('memory_mb')
        self.assertEqual(cpu_field.default, default_service_cpu_cores)
        self.assertEqual(mem_field.default, default_service_memory_mb)
