"""
Metrics adapter module.

Provides an abstraction layer for collecting and querying service metrics.
In production, this could be backed by Prometheus, Datadog, or a similar
monitoring system. Currently provides simulated metrics for development.
"""
import random
import time
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class MetricsAdapter:
    """
    Adapter for querying service metrics.

    Supports pluggable backends. Default: simulated metrics.
    Production: swap with Prometheus, CloudWatch, or Datadog adapter.
    """

    def _generate_time_series(self, duration_str: str, min_val: float,
                              max_val: float):
        """Generate a simulated time-series based on duration string."""
        duration_map = {
            '5m': 5,
            '15m': 15,
            '30m': 30,
            '1h': 60,
            '6h': 360,
            '12h': 720,
            '24h': 1440,
            '7d': 10080,
        }
        minutes = duration_map.get(duration_str, 60)
        # Generate one data point per minute, max 60 points
        step = max(1, minutes // 60)
        points = []
        now = datetime.utcnow()
        for i in range(0, minutes, step):
            timestamp = now - timedelta(minutes=minutes - i)
            value = round(random.uniform(min_val, max_val), 2)
            points.append({
                'timestamp': timestamp.isoformat() + 'Z',
                'value': value,
            })
        return points

    def get_cpu_history(self, service_id: str,
                        duration: str = '1h') -> list:
        """Get CPU utilization history for a service."""
        logger.debug(
            f"Fetching CPU metrics for service {service_id}, duration={duration}")
        return self._generate_time_series(duration, 5.0, 85.0)

    def get_memory_history(self, service_id: str,
                           duration: str = '1h') -> list:
        """Get memory utilization history for a service."""
        logger.debug(
            f"Fetching memory metrics for service {service_id}, duration={duration}")
        return self._generate_time_series(duration, 100.0, 450.0)

    def get_network_history(self, service_id: str,
                            duration: str = '1h') -> list:
        """Get network I/O history for a service."""
        logger.debug(
            f"Fetching network metrics for service {service_id}, duration={duration}")
        return self._generate_time_series(duration, 0.5, 50.0)


# Singleton instance used by views
metrics_adapter = MetricsAdapter()
