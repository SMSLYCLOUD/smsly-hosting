"""
Metrics adapter — queries real ServiceMetric data from the database.

Provides time-series data for CPU, memory, network, and disk I/O.
Falls back to simulated data if no real metrics exist.
"""
import random
import logging
from datetime import datetime, timedelta
from django.utils import timezone
from .models_metrics import ServiceMetric

logger = logging.getLogger(__name__)

DURATION_MAP = {
    '5m': 5,
    '15m': 15,
    '30m': 30,
    '1h': 60,
    '6h': 360,
    '12h': 720,
    '24h': 1440,
    '7d': 10080,
}


class MetricsAdapter:
    """
    Adapter for querying service metrics from the database.
    Falls back to simulated data if no real metrics are available.
    """

    def _query_metrics(self, service_id: str, duration_str: str):
        """Query ServiceMetric records for a given duration."""
        minutes = DURATION_MAP.get(duration_str, 60)
        since = timezone.now() - timedelta(minutes=minutes)
        return ServiceMetric.objects.filter(
            service_id=service_id,
            timestamp__gte=since,
        ).order_by('timestamp')

    def _generate_simulated(self, duration_str: str, min_val: float, max_val: float):
        """Generate simulated time-series when no real data exists."""
        minutes = DURATION_MAP.get(duration_str, 60)
        step = max(1, minutes // 60)
        points = []
        now = datetime.utcnow()
        for i in range(0, minutes, step):
            ts = now - timedelta(minutes=minutes - i)
            points.append({
                'timestamp': ts.isoformat() + 'Z',
                'value': round(random.uniform(min_val, max_val), 2),
            })
        return points

    def get_cpu_history(self, service_id: str, duration: str = '1h') -> list:
        """CPU utilization percentage history."""
        metrics = self._query_metrics(service_id, duration)
        if not metrics.exists():
            return self._generate_simulated(duration, 5.0, 85.0)
        return [
            {
                'timestamp': m.timestamp.isoformat() + 'Z',
                'value': round(m.cpu_percent, 2),
            }
            for m in metrics
        ]

    def get_memory_history(self, service_id: str, duration: str = '1h') -> list:
        """Memory usage in MB history."""
        metrics = self._query_metrics(service_id, duration)
        if not metrics.exists():
            return self._generate_simulated(duration, 100.0, 450.0)
        return [
            {
                'timestamp': m.timestamp.isoformat() + 'Z',
                'value': m.memory_usage,
            }
            for m in metrics
        ]

    def get_network_history(self, service_id: str, duration: str = '1h') -> list:
        """Network I/O in KB/s history."""
        metrics = self._query_metrics(service_id, duration)
        if not metrics.exists():
            return self._generate_simulated(duration, 0.5, 50.0)
        # Convert bytes to KB
        return [
            {
                'timestamp': m.timestamp.isoformat() + 'Z',
                'value': round((m.network_rx_bytes + m.network_tx_bytes) / 1024, 2),
            }
            for m in metrics
        ]

    def get_disk_history(self, service_id: str, duration: str = '1h') -> list:
        """Disk I/O in KB/s history."""
        metrics = self._query_metrics(service_id, duration)
        if not metrics.exists():
            return self._generate_simulated(duration, 0.1, 20.0)
        return [
            {
                'timestamp': m.timestamp.isoformat() + 'Z',
                'value': round((m.disk_read_bytes + m.disk_write_bytes) / 1024, 2),
            }
            for m in metrics
        ]

    def get_current(self, service_id: str) -> dict:
        """Get latest metric snapshot for a service."""
        latest = ServiceMetric.objects.filter(
            service_id=service_id,
        ).order_by('-timestamp').first()
        if not latest:
            return {
                'cpu_percent': 0,
                'memory_usage': 0,
                'memory_limit': 512,
                'memory_percent': 0,
                'network_rx_kb': 0,
                'network_tx_kb': 0,
            }
        return {
            'cpu_percent': round(latest.cpu_percent, 2),
            'memory_usage': latest.memory_usage,
            'memory_limit': latest.memory_limit,
            'memory_percent': round(latest.memory_percent, 2),
            'network_rx_kb': round(latest.network_rx_bytes / 1024, 2),
            'network_tx_kb': round(latest.network_tx_bytes / 1024, 2),
        }


# Singleton instance used by views
metrics_adapter = MetricsAdapter()
