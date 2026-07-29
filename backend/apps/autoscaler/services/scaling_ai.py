"""Auto-scaling analysis — deterministic rules with optional AI enhancement.

Deterministic rules (always active):
  1. CPU > 85% for 5min → scale up
  2. OOM detected in logs → scale up immediately
  3. Memory growing > 50MB/min → scale up
  4. All instances < 30% CPU for 10min → scale down
  5. Max replicas cap → refuse to spawn beyond limit
  6. Cooldown → don't spawn again within 5min of last spawn

AI enhancement (optional, if any AI provider is configured):
  7. Predicts load spikes from historical patterns
  8. Recommends optimal replica count
  9. Suggests ideal node based on workload affinity

Data sources (in priority order):
  1. Prometheus (primary — container + node metrics)
  2. Loki (logs — OOM, crash loops, error rates)
  3. Direct Docker API (fallback if Prometheus is unreachable)
"""
import json
import logging
import os
import socket
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

PROMETHEUS_URL = getattr(settings, 'PROMETHEUS_INTERNAL_URL', 'http://smsly-prometheus:9090')
LOKI_URL = getattr(settings, 'LOKI_INTERNAL_URL', 'http://smsly-loki:3100')
TIMEOUT = 12

# ── Deterministic thresholds ──────────────────────────────────────────────
CPU_HIGH = float(os.environ.get("SCALE_CPU_HIGH", "70"))          # % — trigger early, before saturation
CPU_CRITICAL = float(os.environ.get("SCALE_CPU_CRITICAL", "90"))  # % — aggressive action
CPU_LOW = float(os.environ.get("SCALE_CPU_LOW", "25"))            # % — below this, consider scale down
SCALE_DOWN_CPU = CPU_LOW                                            # alias for the scale-down decision
MEM_GROWTH_MB_MIN = float(os.environ.get("SCALE_MEM_TREND_MB", "25"))  # MB/min — catch slow leaks
MAX_REPLICAS = int(os.environ.get("SCALE_MAX_REPLICAS", "5"))
COOLDOWN_MINUTES = int(os.environ.get("SCALE_COOLDOWN_MIN", "3"))       # shorter cooldown for faster response
COOLDOWN_DOWN_MINUTES = int(os.environ.get("SCALE_COOLDOWN_DOWN_MIN", "10"))


def _is_ai_configured():
    """Check if any AI provider has API keys set."""
    providers = [
        'OPENAI_API_KEY', 'GROK_API_KEY', 'GEMINI_API_KEY',
        'CLAUDE_API_KEY', 'JULES_API_KEY', 'FREEMODEL_API_KEY',
        'OPENCODE_API_KEY', 'MISTRAL_API_KEY',
    ]
    return any(os.environ.get(p, '').strip() for p in providers)


class ScalingAnalyzer:
    """Analyze a service and recommend scaling actions.

    Deterministic rules always run. AI enhancement is layered on top
    when providers are configured.
    """

    def __init__(self, service):
        self.service = service
        self.service_name = service.compose_main_service or service.name
        self.ai_enabled = _is_ai_configured()
        self.engine = 'ai_enhanced' if self.ai_enabled else 'deterministic'

    def analyze(self):
        """Return full analysis with recommendation and metadata."""
        metrics = self._fetch_prometheus_metrics()
        docker_fallback = {}
        if not any(metrics.values()):
            docker_fallback = self._fetch_docker_fallback()
            metrics.update(docker_fallback)

        errors = self._fetch_loki_errors()
        guardrails = self._check_guardrails()

        # ── Deterministic decision (always runs) ─────────────────────────
        recommendation = self._decide(metrics, errors, guardrails)

        # ── AI enhancement (optional) ────────────────────────────────────
        ai_insight = {}
        if self.ai_enabled and recommendation['action'] in ('scale_up', 'none'):
            ai_insight = self._ai_enhance(metrics, errors, recommendation)

        return {
            'service': str(self.service.id),
            'service_name': self.service_name,
            'timestamp': timezone.now().isoformat(),
            'engine': self.engine,
            'ai_configured': self.ai_enabled,
            'metrics': metrics,
            'docker_fallback_used': bool(docker_fallback),
            'error_analysis': errors,
            'guardrails': guardrails,
            'recommendation': recommendation,
            'ai_insight': ai_insight,
        }

    # ── Data sources ─────────────────────────────────────────────────────

    def _fetch_prometheus_metrics(self):
        label = f'service_name="{self.service_name}"'
        queries = {
            'cpu_percent': f'avg(rate(docker_container_cpu_usage_seconds_total{{{label}}}[5m])) * 100',
            'memory_mb': f'docker_container_memory_usage_bytes{{{label}}} / 1024 / 1024',
            'memory_trend': f'deriv(docker_container_memory_usage_bytes{{{label}}}[15m]) / 1024 / 1024',
        }
        results = {}
        for key, query in queries.items():
            results[key] = self._promql(query)
        return results

    def _fetch_docker_fallback(self):
        """Direct Docker API stats — used when Prometheus is unreachable."""
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect('/var/run/docker.sock')
            sock.sendall(b"GET /containers/json?all=true HTTP/1.0\r\nHost: localhost\r\n\r\n")
            data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
            sock.close()
            containers = json.loads(data.split(b"\r\n\r\n", 1)[1])

            for c in containers:
                labels = c.get('Labels', {}) or {}
                cid = c.get('Id', '')
                canonical = labels.get('smsly.blue_green.canonical_name', '')
                compose_svc = labels.get('com.docker.compose.service', '')
                if self.service_name not in (canonical, compose_svc):
                    continue

                # Get stats for this container
                sock2 = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock2.settimeout(10)
                sock2.connect('/var/run/docker.sock')
                sock2.sendall(f"GET /containers/{cid}/stats?stream=false HTTP/1.0\r\nHost: localhost\r\n\r\n".encode())
                sdata = b""
                while True:
                    chunk = sock2.recv(4096)
                    if not chunk:
                        break
                    sdata += chunk
                sock2.close()
                stats = json.loads(sdata.split(b"\r\n\r\n", 1)[1])

                cpu = stats.get('cpu_stats', {})
                precpu = stats.get('precpu_stats', {})
                cpu_d = cpu.get('cpu_usage', {}).get('total_usage', 0) - \
                        precpu.get('cpu_usage', {}).get('total_usage', 0)
                sys_d = cpu.get('system_cpu_usage', 0) - \
                        precpu.get('system_cpu_usage', 0)
                cpu_pct = (cpu_d / sys_d * cpu.get('online_cpus', 1)) * 100 if sys_d > 0 else 0

                mem = stats.get('memory_stats', {})
                mem_bytes = mem.get('usage', 0) - mem.get('stats', {}).get('inactive_file', 0)

                return {
                    'cpu_percent': round(cpu_pct, 2),
                    'memory_mb': round(mem_bytes / 1024 / 1024, 2),
                    'memory_trend': None,  # can't compute trend from one sample
                }
        except Exception as exc:
            logger.debug("Docker fallback failed for %s: %s", self.service_name, exc)
        return {}

    def _fetch_loki_errors(self):
        query = f'{{compose_service=~"{self.service_name}.*"}} |= "error"'
        try:
            resp = requests.get(
                f"{LOKI_URL}/loki/api/v1/query_range",
                params={'query': query, 'start': _ns_ago(3600),
                        'end': _ns_ago(0), 'limit': 50},
                timeout=TIMEOUT,
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

    # ── Guardrails ────────────────────────────────────────────────────────

    def _check_guardrails(self):
        from apps.autoscaler.models.replica import ServiceReplica
        running = ServiceReplica.objects.filter(
            service=self.service, status='RUNNING'
        ).count()
        spawning = ServiceReplica.objects.filter(
            service=self.service, status__in=('SPAWNING', 'DRAINING')
        ).exists()
        last_spawn = ServiceReplica.objects.filter(
            service=self.service, status='RUNNING'
        ).order_by('-created_at').first()

        cooldown_ok = True
        cooldown_down_ok = True
        if last_spawn:
            since = (timezone.now() - last_spawn.created_at).total_seconds()
            cooldown_ok = since >= COOLDOWN_MINUTES * 60
        if running == 0:
            cooldown_down_ok = True
        else:
            last_destroy = ServiceReplica.objects.filter(
                service=self.service, status='DESTROYED'
            ).order_by('-destroyed_at').first()
            if last_destroy and last_destroy.destroyed_at:
                since = (timezone.now() - last_destroy.destroyed_at).total_seconds()
                cooldown_down_ok = since >= COOLDOWN_DOWN_MINUTES * 60

        return {
            'running_replicas': running,
            'max_replicas': MAX_REPLICAS,
            'at_capacity': running >= MAX_REPLICAS,
            'spawning_in_progress': spawning,
            'cooldown_active': not cooldown_ok,
            'cooldown_down_active': not cooldown_down_ok,
            'can_scale_up': (not spawning and cooldown_ok and running < MAX_REPLICAS),
            'can_scale_down': (running > 0 and cooldown_down_ok),
        }

    # ── Decision engine ───────────────────────────────────────────────────

    def _decide(self, metrics, errors, guardrails):
        cpu = metrics.get('cpu_percent', 0) or 0
        mem_trend = metrics.get('memory_trend', 0) or 0
        oom = errors.get('oom_detected', False)
        crash = errors.get('crash_loop', False)
        running = guardrails['running_replicas']

        r = {'action': 'none', 'reason': 'Metrics within normal range.',
             'scale_up_by': 0, 'urgency': 'low'}

        # ── Emergency: OOM or crash ──────────────────────────────────
        if oom or crash:
            if guardrails['can_scale_up']:
                r.update(action='scale_up', urgency='critical',
                         reason='OOM/crash detected — immediate scaling.',
                         scale_up_by=max(2, running + 2))
            else:
                r['reason'] = 'OOM/crash detected but guardrails prevent scaling.'
            return r

        if guardrails['spawning_in_progress']:
            r['reason'] = 'Replica spawn in progress — waiting.'
            return r

        if not guardrails['can_scale_up']:
            r['reason'] = 'Guardrails prevent scaling (at capacity or in cooldown).'
            return r

        # ── Horizontal scaling: CPU-based ───────────────────────────
        # Target: each instance at ~50% CPU. Home service counts as 1 instance.
        # Formula: needed = ceil(cpu / target) - (running + 1 home)
        TARGET_CPU = 50
        total_instances = running + 1  # home service + replicas
        if cpu > CPU_HIGH or mem_trend > MEM_GROWTH_MB_MIN:
            needed = max(0, int(cpu / TARGET_CPU) - total_instances + 1)
            if needed <= 0 and cpu > CPU_HIGH:
                needed = 1  # at least one if above threshold
            if needed > 0:
                reason = (f'CPU at {cpu:.0f}% — '
                          f'{total_instances} instances → need {needed} more '
                          f'(target ≤{TARGET_CPU}% per instance)')
                if mem_trend > MEM_GROWTH_MB_MIN:
                    reason = f'Memory growing at {mem_trend:.1f} MB/min — {reason}'
                r.update(action='scale_up', urgency='high' if cpu > 80 else 'medium',
                         reason=reason, scale_up_by=needed)
                return r

        # ── Scale down: idle check ──────────────────────────────────
        if cpu <= SCALE_DOWN_CPU and running > 0 and guardrails['can_scale_down']:
            r.update(action='scale_down', urgency='low',
                     reason=f'CPU at {cpu:.0f}% with {running} extra replicas — removing 1.')

        return r

    # ── AI enhancement (best-effort, non-blocking) ────────────────────────

    def _ai_enhance(self, metrics, errors, recommendation):
        """Ask AI to refine the deterministic recommendation."""
        try:
            # Prefer the primary AI provider
            from apps.deployments.services.ai_router import DEFAULT_AI_ROUTER_API_BASE
            api_key = os.environ.get('OPENAI_API_KEY') or os.environ.get('GROK_API_KEY') or \
                      os.environ.get('GEMINI_API_KEY') or os.environ.get('OPENCODE_API_KEY')
            if not api_key:
                return {'skipped': True, 'reason': 'No API key found'}

            prompt = (
                f"Service: {self.service_name}\n"
                f"CPU: {metrics.get('cpu_percent', 'N/A')}%\n"
                f"Memory: {metrics.get('memory_mb', 'N/A')}MB\n"
                f"Memory trend: {metrics.get('memory_trend', 'N/A')} MB/min\n"
                f"Errors (1h): {errors.get('error_count_1h', 0)}\n"
                f"OOM: {errors.get('oom_detected', False)}\n"
                f"Current recommendation: {recommendation['action']} "
                f"(reason: {recommendation['reason']})\n\n"
                f"Return a JSON object with: "
                f"'confidence' (0-1), 'optimal_replicas' (int), "
                f"'prediction' (str: 'stable', 'growing', 'spiking', 'declining'), "
                f"'note' (str: brief explanation). "
                f"Only return valid JSON, no other text."
            )

            resp = requests.post(
                f"{DEFAULT_AI_ROUTER_API_BASE}/chat/completions",
                json={
                    'model': os.environ.get('OPENAI_MODEL', 'gpt-4o-mini'),
                    'messages': [{'role': 'user', 'content': prompt}],
                    'max_tokens': 200,
                    'temperature': 0.1,
                },
                headers={'Authorization': f'Bearer {api_key}'},
                timeout=15,
            )
            if resp.ok:
                content = resp.json()['choices'][0]['message']['content']
                return json.loads(content)
        except Exception as exc:
            logger.debug("AI enhancement skipped: %s", exc)

        return {'skipped': True, 'reason': 'AI enhancement failed — deterministic rules applied'}

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
        except Exception:
            return None


def _ns_ago(seconds: int) -> str:
    ts = timezone.now() - timedelta(seconds=seconds)
    return str(int(ts.timestamp() * 1_000_000_000))
