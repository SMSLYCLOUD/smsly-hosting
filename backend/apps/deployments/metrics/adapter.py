"""Adapter module."""
import time
import random
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


class MetricsAdapter:
    """
    Adapter to fetch metrics from Prometheus or Mock for local dev.
    """

    def get_cpu_history(self, service_id: str,
                        duration: str = '1h') -> List[Dict[str, Any]]:
        # TODO: Connect to real Prometheus at http://prometheus:9090
        # For now, we simulate realistic looking data for the UI
        return self._generate_mock_data(service_id, 'cpu', duration)

    def get_memory_history(self, service_id: str,
                           duration: str = '1h') -> List[Dict[str, Any]]:
        return self._generate_mock_data(service_id, 'memory', duration)

    def get_network_history(self, service_id: str,
                            duration: str = '1h') -> List[Dict[str, Any]]:
        return self._generate_mock_data(service_id, 'network', duration)

    def _generate_mock_data(self, service_id: str,
                            metric_type: str, duration: str) -> List[Dict]:
        """
        Generates time-series data.
        """
        data = []
        now = int(time.time())
        points = 60  # 1 hour = 60 points (1 per min)

        base_value = 0
        if metric_type == 'cpu':
            base_value = 20  # 20%
        elif metric_type == 'memory':
            base_value = 256  # 256MB
        elif metric_type == 'network':
            base_value = 1024  # 1KB/s

        for i in range(points):
            timestamp = now - ((points - i) * 60)

            # Add some jitter
            jitter = random.uniform(-0.2, 0.2) * base_value
            value = base_value + jitter

            # Spike occasionally
            if random.random() > 0.95:
                value *= 1.5

            data.append({
                'timestamp': timestamp,
                'value': max(0, round(value, 2))
            })

        return data


metrics_adapter = MetricsAdapter()
