"""
Unified metrics sources for the autoscaler.

Replaces the three independent metric paths previously used by:
  * apps.deployments.services.autoscaler (DB-stored ServiceMetric)
  * apps.deployments.services.scaling_ai (Prometheus + Loki + Docker fallback)
  * apps.autoscaler.views (`docker stats` / K8s metrics API)

All callers go through ``MetricsCollector.collect(service)`` and get the
same dict shape regardless of backend.
"""
import http.client
import json
import logging
import os
import socket
from dataclasses import asdict, dataclass

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


PROMETHEUS_URL = getattr(settings, 'PROMETHEUS_INTERNAL_URL', 'http://smsly-prometheus:9090')
LOKI_URL = getattr(settings, 'LOKI_INTERNAL_URL', 'http://smsly-loki:3100')
METRICS_TIMEOUT = 12


@dataclass
class MetricsSnapshot:
    """Canonical metrics view of a service. ``None`` = unknown / not measured."""
    cpu_percent: float | None = None
    memory_mb: float | None = None
    memory_trend_mb_per_min: float | None = None
    error_count_1h: int = 0
    oom_detected: bool = False
    crash_loop: bool = False
    has_errors: bool = False
    source: str = 'unknown'  # prometheus | db | docker | k8s | none

    def to_dict(self) -> dict:
        return asdict(self)


class MetricsCollector:
    """Single metrics entry point with fallback chain.

    Tries the preferred source first and falls back to the others. The
    order matters for both correctness and latency:

      * ``db`` (default) — read recent ``ServiceMetric`` rows written by
        ``tasks_metrics.collect_metrics_task``. No network call, safe
        to run on the 30s Celery beat.
      * ``prometheus`` — direct PromQL. Slower, but more current.
        Used by the on-demand REST ``/analyze`` endpoint and the
        Jules auto-fix path.
      * ``docker`` — direct Docker socket read. Fallback if both
        DB and Prometheus are unavailable.

    Output shape is the same ``MetricsSnapshot`` regardless of which
    backend actually produced the numbers.
    """

    def __init__(self, service, prefer: str = 'db'):
        self.service = service
        self.service_name = service.compose_main_service or service.name
        self.prefer = prefer

    def collect(self) -> MetricsSnapshot:
        order = self._source_order()
        for source in order:
            if source == 'db':
                snap = self._from_service_metrics()
            elif source == 'prometheus':
                snap = self._from_prometheus()
            elif source == 'docker':
                snap = self._from_docker_socket()
            else:
                continue
            if snap.cpu_percent is not None:
                return snap
        # Return last attempt (with whatever it found) so the decision
        # engine can see "no metrics available" via the source field.
        return snap

    def _source_order(self) -> list:
        if self.prefer == 'prometheus':
            return ['prometheus', 'db', 'docker']
        if self.prefer == 'docker':
            return ['docker', 'db', 'prometheus']
        return ['db', 'prometheus', 'docker']

    # ── Prometheus ──────────────────────────────────────────────────────────
    def _from_prometheus(self) -> MetricsSnapshot:
        label = f'service_name="{self.service_name}"'
        queries = {
            'cpu_percent': f'max(rate(docker_container_cpu_usage_seconds_total{{{label}}}[5m]) * 100)',
            'memory_mb': f'max(docker_container_memory_usage_bytes{{{label}}} / 1024 / 1024)',
            'memory_trend': f'max(deriv(docker_container_memory_usage_bytes{{{label}}}[15m]) / 1024 / 1024)',
        }
        out: dict = {}
        for key, query in queries.items():
            out[key] = self._promql(query)
        if not any(out.values()):
            errors = self._loki_errors()
            return MetricsSnapshot(
                cpu_percent=out.get('cpu_percent'),
                memory_mb=out.get('memory_mb'),
                memory_trend_mb_per_min=out.get('memory_trend'),
                error_count_1h=errors.get('error_count_1h', 0),
                oom_detected=errors.get('oom_detected', False),
                crash_loop=errors.get('crash_loop', False),
                has_errors=errors.get('has_errors', False),
                source='prometheus',
            )
        return MetricsSnapshot(
            cpu_percent=out.get('cpu_percent'),
            memory_mb=out.get('memory_mb'),
            memory_trend_mb_per_min=out.get('memory_trend'),
            source='prometheus',
        )

    # ── DB-stored ServiceMetric (used by collect_metrics_task) ──────────────
    def _from_service_metrics(self) -> MetricsSnapshot:
        from datetime import timedelta

        from apps.deployments.models_metrics import ServiceMetric
        now = timezone.now()
        recent = ServiceMetric.objects.filter(
            service=self.service,
            timestamp__gte=now - timedelta(minutes=2),
        )
        if not recent.exists():
            return MetricsSnapshot(source='db')
        avg_cpu = sum(m.cpu_percent for m in recent) / recent.count()
        avg_mem = sum(m.memory_usage for m in recent) / recent.count()
        return MetricsSnapshot(
            cpu_percent=avg_cpu,
            memory_mb=avg_mem,
            memory_trend_mb_per_min=None,
            source='db',
        )

    # ── Direct Docker socket fallback (used by scaling_ai legacy) ───────────
    def _from_docker_socket(self) -> MetricsSnapshot:
        if not os.path.exists('/var/run/docker.sock'):
            return MetricsSnapshot(source='docker')
        try:
            conn = http.client.HTTPConnection('localhost', timeout=10)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect('/var/run/docker.sock')
            conn.sock = sock
            conn.request('GET', '/containers/json?all=true')
            resp = conn.getresponse()
            containers = json.loads(resp.read())
            conn.close()

            for c in containers:
                labels = c.get('Labels', {}) or {}
                cid = c.get('Id', '')
                canonical = labels.get('smsly.blue_green.canonical_name', '')
                compose_svc = labels.get('com.docker.compose.service', '')
                if self.service_name not in (canonical, compose_svc):
                    continue

                conn2 = http.client.HTTPConnection('localhost', timeout=10)
                sock2 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock2.settimeout(10)
                sock2.connect('/var/run/docker.sock')
                conn2.sock = sock2
                conn2.request('GET', f'/containers/{cid}/stats?stream=false')
                resp2 = conn2.getresponse()
                stats = json.loads(resp2.read())
                conn2.close()

                cpu = stats.get('cpu_stats', {})
                precpu = stats.get('precpu_stats', {})
                cpu_d = cpu.get('cpu_usage', {}).get('total_usage', 0) - \
                        precpu.get('cpu_usage', {}).get('total_usage', 0)
                sys_d = cpu.get('system_cpu_usage', 0) - \
                        precpu.get('system_cpu_usage', 0)
                cpu_pct = (cpu_d / sys_d * cpu.get('online_cpus', 1)) * 100 if sys_d > 0 else 0

                mem = stats.get('memory_stats', {})
                mem_bytes = mem.get('usage', 0) - mem.get('stats', {}).get('inactive_file', 0)
                return MetricsSnapshot(
                    cpu_percent=round(cpu_pct, 2),
                    memory_mb=round(mem_bytes / 1024 / 1024, 2),
                    memory_trend_mb_per_min=None,
                    source='docker',
                )
        except Exception as exc:
            logger.debug("Docker fallback failed for %s: %s", self.service_name, exc)
        return MetricsSnapshot(source='docker')

    # ── Loki errors (only used in combination with prometheus path) ─────────
    def _loki_errors(self) -> dict:
        from datetime import timedelta
        query = f'{{compose_service=~"{self.service_name}.*"}} |= "error"'
        ts_ns = int((timezone.now() - timedelta(seconds=3600)).timestamp() * 1_000_000_000)
        end_ns = int(timezone.now().timestamp() * 1_000_000_000)
        try:
            resp = requests.get(
                f"{LOKI_URL}/loki/api/v1/query_range",
                params={  # type: ignore[arg-type]
                    'query': query, 'start': str(ts_ns),
                    'end': str(end_ns), 'limit': 50
                },
                timeout=METRICS_TIMEOUT,
            )
            resp.raise_for_status()
            streams = resp.json().get('data', {}).get('result', [])
            error_count = sum(len(s.get('values', [])) for s in streams)
            oom = any('out of memory' in str(s.get('values', [])).lower() for s in streams)
            crash_loop = any(
                'restarting' in str(s.get('values', [])).lower() or
                'crashloop' in str(s.get('values', [])).lower()
                for s in streams
            )
            return {
                'error_count_1h': error_count,
                'oom_detected': oom,
                'crash_loop': crash_loop,
                'has_errors': error_count > 0,
            }
        except Exception:
            return {'error_count_1h': 0, 'oom_detected': False, 'crash_loop': False, 'has_errors': False}

    @staticmethod
    def _promql(query: str):
        try:
            resp = requests.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={'query': query},
                timeout=METRICS_TIMEOUT,
            )
            resp.raise_for_status()
            results = resp.json().get('data', {}).get('result', [])
            if not results:
                return None
            values = [float(r['value'][1]) for r in results if r.get('value')]
            return sum(values) / len(values) if values else None
        except Exception:
            return None
