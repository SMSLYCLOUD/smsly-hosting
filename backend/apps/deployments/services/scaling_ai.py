"""AI-driven scaling analysis — queries Prometheus + Loki for service health and scaling decisions."""
import logging
import requests
from datetime import timedelta
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

PROMETHEUS_URL = getattr(settings, 'PROMETHEUS_INTERNAL_URL', 'http://prometheus:9090')
LOKI_URL = getattr(settings, 'LOKI_INTERNAL_URL', 'http://loki:3100')
TIMEOUT = 12

# Thresholds
CPU_HIGH = 80          # % — above this, consider scaling
CPU_CRITICAL = 95      # % — above this, scale NOW
CPU_LOW = 30           # % — below this, consider scaling down
MEM_GROWTH_MB_MIN = 50 # MB/minute — memory leak detection
ERROR_SPIKE_RATIO = 5  # x baseline — error rate spike


class ScalingAnalyzer:
    """Analyze a service's metrics and logs to recommend scaling actions."""

    def __init__(self, service):
        self.service = service
        self.service_name = service.compose_main_service or service.name

    def analyze(self):
        """Return a dict with recommendation and supporting data."""
        metrics = self._fetch_prometheus_metrics()
        errors = self._fetch_loki_errors()
        recommendation = self._decide(metrics, errors)

        return {
            'service': str(self.service.id),
            'service_name': self.service_name,
            'timestamp': timezone.now().isoformat(),
            'metrics': metrics,
            'error_analysis': errors,
            'recommendation': recommendation,
        }

    def _fetch_prometheus_metrics(self):
        """Query Prometheus for CPU, memory, network of this service."""
        label = f'service_name="{self.service_name}"'
        queries = {
            'cpu_percent': f'avg(rate(docker_container_cpu_usage_seconds_total{{{label}}}[5m])) * 100',
            'memory_mb': f'docker_container_memory_usage_bytes{{{label}}} / 1024 / 1024',
            'memory_trend': f'deriv(docker_container_memory_usage_bytes{{{label}}}[15m]) / 1024 / 1024',
            'network_rx': f'rate(docker_container_network_receive_bytes_total{{{label}}}[5m])',
            'network_tx': f'rate(docker_container_network_transmit_bytes_total{{{label}}}[5m])',
        }
        results = {}
        for key, query in queries.items():
            results[key] = self._promql(query)
        return results

    def _fetch_loki_errors(self):
        """Query Loki for error patterns in recent logs."""
        query = f'{{compose_service=~"{self.service_name}.*"}} |= "error"'
        try:
            resp = requests.get(
                f"{LOKI_URL}/loki/api/v1/query_range",
                params={
                    'query': query,
                    'start': _ns_ago(3600),  # 1 hour
                    'end': _ns_ago(0),
                    'limit': 50,
                },
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            streams = resp.json().get('data', {}).get('result', [])
            error_count = sum(len(s.get('values', [])) for s in streams)
            oom_detected = any(
                'out of memory' in str(s.get('values', [])).lower()
                for s in streams
            )
            return {
                'error_count_1h': error_count,
                'oom_detected': oom_detected,
                'has_errors': error_count > 0,
            }
        except Exception as exc:
            logger.debug("Loki error query failed: %s", exc)
            return {'error_count_1h': 0, 'oom_detected': False, 'has_errors': False}

    def _decide(self, metrics, errors):
        """Apply thresholds and return a scaling recommendation."""
        cpu = metrics.get('cpu_percent', 0) or 0
        mem = metrics.get('memory_mb', 0) or 0
        mem_trend = metrics.get('memory_trend', 0) or 0
        error_count = errors.get('error_count_1h', 0)
        oom = errors.get('oom_detected', False)

        recommendation = {
            'action': 'none',
            'reason': 'Metrics within normal range.',
            'scale_up_by': 0,
            'urgency': 'low',
        }

        if oom:
            recommendation['action'] = 'scale_up'
            recommendation['reason'] = 'OOM detected in logs — immediate scaling required.'
            recommendation['scale_up_by'] = 2
            recommendation['urgency'] = 'critical'
        elif cpu >= CPU_CRITICAL:
            recommendation['action'] = 'scale_up'
            recommendation['reason'] = f'CPU at {cpu:.0f}% — critical threshold exceeded.'
            recommendation['scale_up_by'] = max(1, int(cpu / 30))
            recommendation['urgency'] = 'high'
        elif cpu >= CPU_HIGH:
            recommendation['action'] = 'scale_up'
            recommendation['reason'] = f'CPU at {cpu:.0f}% — sustained high load.'
            recommendation['scale_up_by'] = 1
            recommendation['urgency'] = 'medium'
        elif mem_trend > MEM_GROWTH_MB_MIN:
            recommendation['action'] = 'scale_up'
            recommendation['reason'] = f'Memory growing at {mem_trend:.1f} MB/min — possible leak.'
            recommendation['scale_up_by'] = 1
            recommendation['urgency'] = 'medium'
        elif cpu <= CPU_LOW and mem < 100:
            # Check if there are existing replicas to scale down
            from apps.deployments.models_replica import ServiceReplica
            running = ServiceReplica.objects.filter(
                service=self.service, status='RUNNING'
            ).count()
            if running > 0:
                recommendation['action'] = 'scale_down'
                recommendation['reason'] = f'All instances below {CPU_LOW}% CPU — idle replicas can be removed.'
                recommendation['scale_up_by'] = 0
                recommendation['urgency'] = 'low'

        return recommendation

    def _promql(self, query):
        try:
            resp = requests.get(
                f"{PROMETHEUS_URL}/api/v1/query",
                params={'query': query},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            results = resp.json().get('data', {}).get('result', [])
            if not results:
                return None
            values = [float(r['value'][1]) for r in results if r.get('value')]
            return sum(values) / len(values) if values else None
        except Exception as exc:
            logger.debug("PromQL query failed for %s: %s", self.service_name, exc)
            return None


def _ns_ago(seconds: int) -> str:
    ts = timezone.now() - timedelta(seconds=seconds)
    return str(int(ts.timestamp() * 1_000_000_000))
