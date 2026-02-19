"""Metrics adapter — connects to real Prometheus, falls back to mock data."""
import time
import random
import logging
from typing import List, Dict, Any
from datetime import datetime, timezone

import requests
from decouple import config

logger = logging.getLogger(__name__)

PROMETHEUS_URL = config('PROMETHEUS_URL', default='http://prometheus:9090')
PROMETHEUS_TIMEOUT = 5  # seconds


class MetricsAdapter:
    """
    Fetches metrics from Prometheus. Falls back to mock data when
    Prometheus is unreachable (e.g. local dev, no Prometheus deployed).
    """

    def __init__(self):
        self._prometheus_ok = None  # None = untested, True/False = cached

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_cpu_history(self, service_id: str,
                        duration: str = '1h') -> List[Dict[str, Any]]:
        data = self._query_prometheus(
            f'rate(container_cpu_usage_seconds_total'
            f'{{container_label_com_docker_compose_service="{service_id}"}}[5m]) * 100',
            duration,
        )
        return data if data else self._generate_mock_data('cpu', duration)

    def get_memory_history(self, service_id: str,
                           duration: str = '1h') -> List[Dict[str, Any]]:
        data = self._query_prometheus(
            f'container_memory_usage_bytes'
            f'{{container_label_com_docker_compose_service="{service_id}"}} / 1024 / 1024',
            duration,
        )
        return data if data else self._generate_mock_data('memory', duration)

    def get_network_history(self, service_id: str,
                            duration: str = '1h') -> List[Dict[str, Any]]:
        data = self._query_prometheus(
            f'rate(container_network_receive_bytes_total'
            f'{{container_label_com_docker_compose_service="{service_id}"}}[5m])',
            duration,
        )
        return data if data else self._generate_mock_data('network', duration)

    def get_disk_history(self, service_id: str,
                         duration: str = '1h') -> List[Dict[str, Any]]:
        data = self._query_prometheus(
            f'(rate(container_fs_reads_bytes_total'
            f'{{container_label_com_docker_compose_service="{service_id}"}}[5m])'
            f' + rate(container_fs_writes_bytes_total'
            f'{{container_label_com_docker_compose_service="{service_id}"}}[5m])) / 1024',
            duration,
        )
        return data if data else self._generate_mock_data('disk', duration)

    def get_current(self, service_id: str) -> Dict[str, Any]:
        """
        Return a current snapshot used by the dashboard cards.
        Falls back to derived values from mock time-series data.
        """
        cpu = self.get_cpu_history(service_id, '1h')
        memory = self.get_memory_history(service_id, '1h')
        network = self.get_network_history(service_id, '1h')

        cpu_percent = self._latest_value(cpu)
        memory_usage = self._latest_value(memory)
        memory_limit = max(512.0, memory_usage * 1.6)
        memory_percent = round(
            (memory_usage / memory_limit) * 100 if memory_limit > 0 else 0.0, 2
        )
        network_total = self._latest_value(network)

        return {
            'cpu_percent': round(cpu_percent, 2),
            'memory_usage': round(memory_usage, 2),
            'memory_limit': round(memory_limit, 2),
            'memory_percent': memory_percent,
            'network_rx_kb': round(network_total * 0.6, 2),
            'network_tx_kb': round(network_total * 0.4, 2),
        }

    # ------------------------------------------------------------------
    # Prometheus Query
    # ------------------------------------------------------------------

    def _query_prometheus(self, query: str, duration: str) -> List[Dict] | None:
        """Query Prometheus range API. Returns list of {timestamp, value} or None."""
        if self._prometheus_ok is False:
            return None  # Skip if we already know it's down

        duration_map = {'1h': 3600, '6h': 21600, '24h': 86400, '7d': 604800}
        range_seconds = duration_map.get(duration, 3600)
        step = max(range_seconds // 60, 15)  # ~60 points

        end = int(time.time())
        start = end - range_seconds

        try:
            resp = requests.get(
                f'{PROMETHEUS_URL}/api/v1/query_range',
                params={
                    'query': query,
                    'start': start,
                    'end': end,
                    'step': step,
                },
                timeout=PROMETHEUS_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()

            if data.get('status') != 'success':
                return None

            results = data.get('data', {}).get('result', [])
            if not results:
                return None

            # Flatten first result's values
            values = results[0].get('values', [])
            self._prometheus_ok = True
            return [
                {
                    'timestamp': datetime.fromtimestamp(
                        int(float(v[0])),
                        tz=timezone.utc,
                    ).isoformat(),
                    'value': round(float(v[1]), 2),
                }
                for v in values
            ]

        except requests.RequestException as e:
            if self._prometheus_ok is None:
                logger.info("Prometheus not available at %s, using mock data: %s",
                            PROMETHEUS_URL, e)
            self._prometheus_ok = False
            return None

    # ------------------------------------------------------------------
    # Mock Data Fallback
    # ------------------------------------------------------------------

    def _generate_mock_data(self, metric_type: str,
                            duration: str) -> List[Dict]:
        """Generate realistic looking time-series data for the UI."""
        data = []
        now = int(time.time())
        points = 60

        base = {'cpu': 20, 'memory': 256, 'network': 1024, 'disk': 80}.get(metric_type, 20)

        for i in range(points):
            timestamp = now - ((points - i) * 60)
            jitter = random.uniform(-0.2, 0.2) * base
            value = base + jitter
            if random.random() > 0.95:
                value *= 1.5
            data.append({
                'timestamp': datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(),
                'value': max(0, round(value, 2)),
            })

        return data

    @staticmethod
    def _latest_value(series: List[Dict[str, Any]]) -> float:
        if not series:
            return 0.0
        latest = series[-1] or {}
        try:
            return float(latest.get('value', 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0


metrics_adapter = MetricsAdapter()
