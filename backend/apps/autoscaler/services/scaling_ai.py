"""Auto-scaling analysis — deterministic rules with optional AI enhancement.

The deterministic decision logic now lives in a single place:
``apps.autoscaler.engine.decision.DecisionEngine``. This module keeps the
``ScalingAnalyzer`` facade (used by the Jules auto-fix path for monitoring
enrichment) and delegates to the unified engine + metrics collector so
the two code paths cannot diverge.

Deterministic rules (always active, via DecisionEngine):
  1. CPU > target for the cooldown window → scale up
  2. OOM/crash detected → urgent scale up
  3. Memory growing > threshold → scale up
  4. CPU below low threshold + above min_replicas → scale down
  5. max_replicas cap → refuse to spawn beyond limit
  6. Cooldown → don't spawn again within the cooldown window

AI enhancement (optional, if any AI provider is configured):
  7. Predicts load spikes from historical patterns
  8. Recommends optimal replica count
  9. Suggests ideal node based on workload affinity

Data sources (via the unified MetricsCollector):
  1. Prometheus (primary — container + node metrics)
  2. Loki (logs — OOM, crash loops, error rates)
  3. Direct Docker API (fallback if Prometheus is unreachable)
"""
import json
import logging
import os

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# ── Deterministic thresholds (re-exported from the unified engine) ──────────
# Kept here for backward compatibility with callers/tests that import them.
from apps.autoscaler.engine.decision import (  # noqa: F401
    DEFAULT_CPU_HIGH as CPU_HIGH,
    DEFAULT_CPU_CRITICAL as CPU_CRITICAL,
    DEFAULT_CPU_LOW as CPU_LOW,
    DEFAULT_MAX_REPLICAS as MAX_REPLICAS,
    DEFAULT_COOLDOWN_MIN as COOLDOWN_MINUTES,
    DEFAULT_COOLDOWN_DOWN_MIN as COOLDOWN_DOWN_MINUTES,
    DEFAULT_MEM_GROWTH_MB_MIN as MEM_GROWTH_MB_MIN,
)
SCALE_DOWN_CPU = CPU_LOW  # alias for the scale-down decision

PROMETHEUS_URL = getattr(settings, 'PROMETHEUS_INTERNAL_URL', 'http://smsly-prometheus:9090')
LOKI_URL = getattr(settings, 'LOKI_INTERNAL_URL', 'http://smsly-loki:3100')
TIMEOUT = 12


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

    Deterministic rules always run via the unified ``DecisionEngine``.
    AI enhancement is layered on top when providers are configured.
    """

    def __init__(self, service):
        self.service = service
        self.service_name = service.compose_main_service or service.name
        self.ai_enabled = _is_ai_configured()
        self.engine = 'ai_enhanced' if self.ai_enabled else 'deterministic'

    def analyze(self):
        """Return full analysis with recommendation and metadata."""
        from apps.autoscaler.engine.metrics import MetricsCollector
        from apps.autoscaler.models.replica import ServiceReplica

        # Unified metrics path: Prometheus (with Loki errors) → DB → Docker.
        snap = MetricsCollector(self.service, prefer='prometheus').collect()
        metrics = {
            'cpu_percent': snap.cpu_percent,
            'memory_mb': snap.memory_mb,
            'memory_trend': snap.memory_trend_mb_per_min,
        }
        errors = {
            'error_count_1h': snap.error_count_1h,
            'oom_detected': snap.oom_detected,
            'crash_loop': snap.crash_loop,
            'has_errors': snap.has_errors,
        }

        running = ServiceReplica.objects.filter(
            service=self.service, status='RUNNING'
        ).count()
        spawning = ServiceReplica.objects.filter(
            service=self.service, status__in=('SPAWNING', 'DRAINING')
        ).exists()
        max_r = self.service.max_replicas or MAX_REPLICAS
        min_r = self.service.min_replicas or 0
        guardrails = {
            'running_replicas': running,
            'max_replicas': max_r,
            'at_capacity': running >= max_r,
            'spawning_in_progress': spawning,
            'cooldown_active': False,
            'cooldown_down_active': False,
            'can_scale_up': (not spawning and running < max_r),
            'can_scale_down': running > min_r,
        }

        # ── Deterministic decision (always runs, single source of truth) ──
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
            'docker_fallback_used': snap.source == 'docker',
            'error_analysis': errors,
            'guardrails': guardrails,
            'recommendation': recommendation,
            'ai_insight': ai_insight,
        }

    # ── Decision engine (delegates to the unified DecisionEngine) ──────────
    def _decide(self, metrics, errors, guardrails):
        """Delegate to the unified DecisionEngine.

        Retained for backward compatibility with callers/tests that pass
        the legacy (metrics, errors, guardrails) dict shape. The decision
        logic lives in one place: ``apps.autoscaler.engine.decision``.
        """
        from apps.autoscaler.engine.decision import DecisionEngine
        from apps.autoscaler.engine.metrics import MetricsSnapshot

        snap = MetricsSnapshot(
            cpu_percent=metrics.get('cpu_percent'),
            memory_mb=metrics.get('memory_mb'),
            memory_trend_mb_per_min=metrics.get('memory_trend'),
            error_count_1h=errors.get('error_count_1h', 0),
            oom_detected=errors.get('oom_detected', False),
            crash_loop=errors.get('crash_loop', False),
            has_errors=errors.get('has_errors', False),
            source='legacy',
        )
        engine = DecisionEngine(
            snap,
            running_replicas=guardrails.get('running_replicas', 0),
            max_replicas=self.service.max_replicas or MAX_REPLICAS,
            min_replicas=self.service.min_replicas or 0,
            cpu_target=self.service.autoscale_cpu_target or 0,
            spawning_in_progress=guardrails.get('spawning_in_progress', False),
        )
        rec = engine.decide()
        return {
            'action': rec.action,
            'reason': rec.reason,
            'scale_up_by': rec.scale_up_by,
            'urgency': rec.urgency,
        }

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
