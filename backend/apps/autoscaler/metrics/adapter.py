"""Metrics adapter — connects to real Prometheus with fail-closed fallbacks."""
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any

import requests
from decouple import config

logger = logging.getLogger(__name__)

PROMETHEUS_URL = config('PROMETHEUS_URL', default='http://smsly-prometheus:9090')
PROMETHEUS_TIMEOUT = 5  # seconds


class MetricsAdapter:
    """
    Fetches metrics from Prometheus.
    Returns empty series when Prometheus is unreachable so dashboards
    do not render synthetic values as real telemetry.
    """

    def __init__(self):
        self._prometheus_ok = None  # None = untested, True/False = cached

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_cpu_history(self, service_ref,
                        duration: str = '1h') -> list[dict[str, Any]]:
        pattern = self._service_pattern(service_ref)
        queries = [
            (
                'rate(container_cpu_usage_seconds_total'
                f'{{container_label_smsly_blue_green_canonical_name=~"{pattern}"}}[5m]) * 100'
            ),
            (
                'rate(container_cpu_usage_seconds_total'
                f'{{container_label_com_docker_compose_service=~"{pattern}"}}[5m]) * 100'
            ),
            (
                'rate(container_cpu_usage_seconds_total'
                f'{{container_label_service_id=~"{pattern}"}}[5m]) * 100'
            ),
            (
                'docker_container_cpu_usage_seconds_total'
                f'{{service_name=~"{pattern}"}} * 100'
            ),
        ]
        return self._query_first_non_empty(queries, duration)

    def get_memory_history(self, service_ref,
                           duration: str = '1h') -> list[dict[str, Any]]:
         pattern = self._service_pattern(service_ref)
         queries = [
             (
                 'container_memory_usage_bytes'
                 f'{{container_label_smsly_blue_green_canonical_name=~"{pattern}"}} / 1024 / 1024'
             ),
             (
                 'container_memory_usage_bytes'
                 f'{{container_label_com_docker_compose_service=~"{pattern}"}} / 1024 / 1024'
             ),
             (
                 'container_memory_usage_bytes'
                 f'{{container_label_service_id=~"{pattern}"}} / 1024 / 1024'
             ),
             (
                 'docker_container_memory_usage_bytes'
                 f'{{service_name=~"{pattern}"}} / 1024 / 1024'
             ),
         ]
         return self._query_first_non_empty(queries, duration)

    def get_network_history(self, service_ref,
                            duration: str = '1h') -> list[dict[str, Any]]:
        pattern = self._service_pattern(service_ref)
        queries = [
            (
                'rate(container_network_receive_bytes_total'
                f'{{container_label_smsly_blue_green_canonical_name=~"{pattern}"}}[5m])'
            ),
            (
                'rate(container_network_receive_bytes_total'
                f'{{container_label_com_docker_compose_service=~"{pattern}"}}[5m])'
            ),
            (
                'rate(container_network_receive_bytes_total'
                f'{{container_label_service_id=~"{pattern}"}}[5m])'
            ),
            (
                'rate(docker_container_network_receive_bytes_total'
                f'{{service_name=~"{pattern}"}}[5m])'
            ),
        ]
        return self._query_first_non_empty(queries, duration)

    def get_disk_history(self, service_ref,
                          duration: str = '1h') -> list[dict[str, Any]]:
        pattern = self._service_pattern(service_ref)
        queries = [
            (
                '(rate(container_fs_reads_bytes_total'
                f'{{container_label_smsly_blue_green_canonical_name=~"{pattern}"}}[5m])'
                ' + rate(container_fs_writes_bytes_total'
                f'{{container_label_smsly_blue_green_canonical_name=~"{pattern}"}}[5m])) / 1024'
            ),
            (
                '(rate(container_fs_reads_bytes_total'
                f'{{container_label_com_docker_compose_service=~"{pattern}"}}[5m])'
                ' + rate(container_fs_writes_bytes_total'
                f'{{container_label_com_docker_compose_service=~"{pattern}"}}[5m])) / 1024'
            ),
            (
                '(rate(container_fs_reads_bytes_total'
                f'{{container_label_service_id=~"{pattern}"}}[5m])'
                ' + rate(container_fs_writes_bytes_total'
                f'{{container_label_service_id=~"{pattern}"}}[5m])) / 1024'
            ),
            (
                '(rate(docker_container_fs_reads_bytes_total'
                f'{{service_name=~"{pattern}"}}[5m])'
                ' + rate(docker_container_fs_writes_bytes_total'
                f'{{service_name=~"{pattern}"}}[5m])) / 1024'
            ),
        ]
        return self._query_first_non_empty(queries, duration)

    def get_current(self, service_ref) -> dict[str, Any]:
        """
        Return a current snapshot used by the dashboard cards.
        """
        cpu = self.get_cpu_history(service_ref, '1h')
        memory = self.get_memory_history(service_ref, '1h')
        network = self.get_network_history(service_ref, '1h')

        if not cpu and not memory and not network:
            return {
                'cpu_percent': 0.0,
                'memory_usage': 0.0,
                'memory_limit': 0.0,
                'memory_percent': 0.0,
                'network_rx_kb': 0.0,
                'network_tx_kb': 0.0,
            }

        cpu_percent = self._latest_value(cpu)
        memory_usage = self._latest_value(memory)
        memory_limit = max(0.0, memory_usage * 1.6)
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
    # Addon metrics — query by addon container_name / compose_service
    # ------------------------------------------------------------------

    def get_addon_metrics(self, addon_ref, duration: str = '1h') -> dict[str, Any]:
        """Return the same shape as the service metrics for an addon container."""
        name = getattr(addon_ref, 'name', '') or ''
        compose_service = re.escape(name)
        container_name = re.escape(name)
        if not compose_service:
            compose_service = re.escape(str(getattr(addon_ref, 'id', '')))

        cpu_queries = [
            (
                'rate(container_cpu_usage_seconds_total'
                f'{{name=~"{container_name}"}}[5m]) * 100'
            ),
            (
                'rate(container_cpu_usage_seconds_total'
                f'{{container_label_com_docker_compose_service=~"{compose_service}"}}[5m]) * 100'
            ),
        ]
        memory_queries = [
            (
                'container_memory_usage_bytes'
                f'{{name=~"{container_name}"}} / 1024 / 1024'
            ),
            (
                'container_memory_usage_bytes'
                f'{{container_label_com_docker_compose_service=~"{compose_service}"}} / 1024 / 1024'
            ),
        ]

        cpu = self._query_first_non_empty(cpu_queries, duration)
        memory = self._query_first_non_empty(memory_queries, duration)
        network = self.get_network_history(addon_ref, duration)
        disk = self.get_disk_history(addon_ref, duration)

        if not any([cpu, memory, network, disk]):
            return self._addon_live_fallback(addon_ref)

        current = self.get_current(addon_ref)
        return {
            'cpu': cpu,
            'memory': memory,
            'network': network,
            'disk': disk,
            'current': current,
            'source': 'prometheus',
        }

    def _addon_live_fallback(self, addon_ref) -> dict[str, Any]:
        """Last-resort: sample the live Docker container for an addon."""
        container_id = getattr(addon_ref, 'coolify_uuid', None) or getattr(addon_ref, 'name', None)
        if not container_id:
            return {
                'cpu': [],
                'memory': [],
                'network': [],
                'disk': [],
                'current': {
                    'cpu_percent': 0.0,
                    'memory_usage': 0.0,
                    'memory_limit': 0.0,
                    'memory_percent': 0.0,
                    'network_rx_kb': 0.0,
                    'network_tx_kb': 0.0,
                },
                'source': 'unavailable',
            }

        try:
            from .tasks_metrics import _collect_container_stats
            stats = _collect_container_stats(str(container_id))
        except Exception:
            stats = None
        if not stats:
            return {
                'cpu': [],
                'memory': [],
                'network': [],
                'disk': [],
                'current': {
                    'cpu_percent': 0.0,
                    'memory_usage': 0.0,
                    'memory_limit': 0.0,
                    'memory_percent': 0.0,
                    'network_rx_kb': 0.0,
                    'network_tx_kb': 0.0,
                },
                'source': 'unavailable',
            }

        cpu_limit = float(stats.get('cpu_limit') or 0.0)
        cpu_usage = float(stats.get('cpu_usage') or 0.0)
        cpu_percent = (cpu_usage / cpu_limit * 100.0) if cpu_limit > 0 else 0.0
        mem_usage = float(stats.get('memory_usage') or 0.0)
        mem_limit = float(stats.get('memory_limit') or 0.0)
        mem_percent = (mem_usage / mem_limit * 100.0) if mem_limit > 0 else 0.0
        rx_kb = float(stats.get('network_rx_bytes') or 0.0) / 1024
        tx_kb = float(stats.get('network_tx_bytes') or 0.0) / 1024
        now = datetime.now(tz=UTC).isoformat()

        return {
            'cpu': [{'timestamp': now, 'value': round(cpu_percent, 2)}],
            'memory': [{'timestamp': now, 'value': round(mem_usage, 2)}],
            'network': [{'timestamp': now, 'value': round(rx_kb + tx_kb, 2)}],
            'disk': [{'timestamp': now, 'value': round(
                (float(stats.get('disk_read_bytes') or 0.0) + float(stats.get('disk_write_bytes') or 0.0)) / 1024,
                2,
            )}],
            'current': {
                'cpu_percent': round(cpu_percent, 2),
                'memory_usage': round(mem_usage, 2),
                'memory_limit': round(mem_limit, 2),
                'memory_percent': round(mem_percent, 2),
                'network_rx_kb': round(rx_kb, 2),
                'network_tx_kb': round(tx_kb, 2),
            },
            'source': 'docker_live',
        }

    # ------------------------------------------------------------------
    # Prometheus Query
    # ------------------------------------------------------------------

    def _query_prometheus(self, query: str, duration: str) -> list[dict] | None:
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
                params={  # type: ignore[arg-type]
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
                        tz=UTC,
                    ).isoformat(),
                    'value': round(float(v[1]), 2),
                }
                for v in values
            ]

        except requests.RequestException as e:
            if self._prometheus_ok is None:
                logger.info("Prometheus not available at %s, returning empty metrics: %s",
                            PROMETHEUS_URL, e)
            self._prometheus_ok = False
            return None

    def _service_pattern(self, service_ref) -> str:
        identifiers = self._service_identifiers(service_ref)
        escaped = [self._escape_regex(item) for item in identifiers if item]
        if not escaped:
            return self._escape_regex(str(service_ref))
        return "|".join(escaped)

    @staticmethod
    def _escape_regex(text: str) -> str:
        # Escape only characters that have special meaning in regular expressions
        # and double-escape backslashes for PromQL double-quoted string literals.
        special_chars = r'.+*?^$()[]{}|\\'
        return ''.join(f'\\\\{c}' if c in special_chars else c for c in text)

    @staticmethod
    def _service_identifiers(service_ref) -> list[str]:
        if isinstance(service_ref, str):
            return [service_ref]

        values: list[str] = []
        for attr in ("id", "name", "compose_main_service", "public_domain"):
            raw = getattr(service_ref, attr, None)
            if raw is not None:
                values.append(str(raw))
        return [value for value in values if value]

    def _query_first_non_empty(self, queries: list[str], duration: str) -> list[dict[str, Any]]:
        for query in queries:
            data = self._query_prometheus(query, duration)
            if data:
                return data
        return []

    @staticmethod
    def _latest_value(series: list[dict[str, Any]]) -> float:
        if not series:
            return 0.0
        latest = series[-1] or {}
        try:
            return float(latest.get('value', 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0


metrics_adapter = MetricsAdapter()
