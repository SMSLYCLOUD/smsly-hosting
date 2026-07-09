"""
Unified decision engine for the autoscaler.

Merges the two previous engines:

  * ``apps.deployments.services.autoscaler._evaluate_scaling`` —
    per-service CPU target, ``last_scale_at`` cooldown, 1m up / 5m down.

  * ``apps.deployments.services.scaling_ai.ScalingAnalyzer._decide`` —
    env-configured CPU_HIGH / CPU_LOW / CPU_CRITICAL thresholds,
    memory-trend scaling, OOM/crash-loop urgent scaling, formula-based
    replica count, replica-record cooldowns.

Output shape is intentionally compatible with both: the
``recommendation`` dict returned by ``decide()`` carries the same
``action`` (``scale_up`` / ``scale_down`` / ``none``) and
``scale_up_by`` (int) keys the legacy callers used.
"""
import logging
import os
from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from .metrics import MetricsSnapshot

logger = logging.getLogger(__name__)


# ── Threshold defaults (env-overridable) ─────────────────────────────────────
DEFAULT_CPU_HIGH = float(os.environ.get("SCALE_CPU_HIGH", "70"))
DEFAULT_CPU_CRITICAL = float(os.environ.get("SCALE_CPU_CRITICAL", "90"))
DEFAULT_CPU_LOW = float(os.environ.get("SCALE_CPU_LOW", "25"))
DEFAULT_MEM_GROWTH_MB_MIN = float(os.environ.get("SCALE_MEM_TREND_MB", "25"))
DEFAULT_MAX_REPLICAS = int(os.environ.get("SCALE_MAX_REPLICAS", "5"))
DEFAULT_COOLDOWN_UP_MIN = int(os.environ.get("SCALE_COOLDOWN_MIN", "3"))
DEFAULT_COOLDOWN_DOWN_MIN = int(os.environ.get("SCALE_COOLDOWN_DOWN_MIN", "10"))

# Per-instance target. The decision formula aims for ~50% per instance
# (mirrors the previous scaling_ai behaviour) but degrades gracefully if
# the per-service autoscale_cpu_target is set instead.
TARGET_CPU_PER_INSTANCE = 50


@dataclass
class Recommendation:
    action: str = 'none'        # scale_up | scale_down | none
    reason: str = 'Metrics within normal range.'
    scale_up_by: int = 0
    scale_down_by: int = 0
    urgency: str = 'low'        # low | medium | high | critical
    cooldown_active: bool = False
    at_capacity: bool = False
    spawning_in_progress: bool = False

    def to_dict(self) -> dict:
        return {
            'action': self.action,
            'reason': self.reason,
            'scale_up_by': self.scale_up_by,
            'scale_down_by': self.scale_down_by,
            'urgency': self.urgency,
            'cooldown_active': self.cooldown_active,
            'at_capacity': self.at_capacity,
            'spawning_in_progress': self.spawning_in_progress,
        }


class DecisionEngine:
    """Pure function: service + metrics + guardrails → Recommendation.

    No I/O, no Django ORM lookups inside the decision itself — callers
    supply ``running_replicas``, ``last_scale_at`` and ``spawning``.
    This keeps the engine unit-testable and prevents the dual-source
    confusion that plagued the previous three implementations.
    """

    def __init__(
        self,
        metrics: MetricsSnapshot,
        *,
        running_replicas: int,
        max_replicas: int,
        cpu_target: int,
        last_scale_at: object | None = None,
        spawning_in_progress: bool = False,
        now=None,
        cpu_high: float = DEFAULT_CPU_HIGH,
        cpu_critical: float = DEFAULT_CPU_CRITICAL,
        cpu_low: float = DEFAULT_CPU_LOW,
        mem_growth_mb_min: float = DEFAULT_MEM_GROWTH_MB_MIN,
        cooldown_up_min: int = DEFAULT_COOLDOWN_UP_MIN,
        cooldown_down_min: int = DEFAULT_COOLDOWN_DOWN_MIN,
    ):
        self.metrics = metrics
        self.running_replicas = running_replicas
        self.max_replicas = max(max_replicas, 1)
        self.cpu_target = cpu_target
        self.last_scale_at = last_scale_at
        self.spawning_in_progress = spawning_in_progress
        self.now = now or timezone.now()
        self.cpu_high = cpu_high
        self.cpu_critical = cpu_critical
        self.cpu_low = cpu_low
        self.mem_growth_mb_min = mem_growth_mb_min
        self.cooldown_up_min = cooldown_up_min
        self.cooldown_down_min = cooldown_down_min

    # ── Cooldown logic (uses the dedicated last_scale_at field) ─────────────
    def _cooldown_active(self) -> bool:
        if not self.last_scale_at:
            return False
        return (self.now - self.last_scale_at) < timedelta(minutes=self.cooldown_up_min)

    def _scale_down_cooldown_active(self) -> bool:
        if not self.last_scale_at:
            return False
        return (self.now - self.last_scale_at) < timedelta(minutes=self.cooldown_down_min)

    # ── Main decision ───────────────────────────────────────────────────────
    def decide(self) -> Recommendation:
        at_capacity = self.running_replicas >= self.max_replicas
        cooldown_active = self._cooldown_active()
        scale_down_cooldown = self._scale_down_cooldown_active()

        r = Recommendation(
            cooldown_active=cooldown_active,
            at_capacity=at_capacity,
            spawning_in_progress=self.spawning_in_progress,
        )

        # Emergency: OOM or crash → always urgent scale up
        if self.metrics.oom_detected or self.metrics.crash_loop:
            if at_capacity:
                r.reason = 'OOM/crash detected but at capacity.'
                return r
            r.action = 'scale_up'
            r.urgency = 'critical'
            r.reason = 'OOM/crash detected — immediate scaling.'
            r.scale_up_by = min(
                max(2, self.running_replicas + 2),
                max(1, self.max_replicas - self.running_replicas),
            )
            return r

        # Spawn in flight — defer
        if self.spawning_in_progress:
            r.reason = 'Replica spawn in progress — waiting.'
            return r

        if at_capacity:
            r.reason = 'At max_replicas — no further scaling.'
            return r

        if cooldown_active:
            r.reason = 'Cooldown active — waiting before next scale.'
            return r

        # ── Scale up ────────────────────────────────────────────────────────
        cpu = self.metrics.cpu_percent or 0.0
        mem_trend = self.metrics.memory_trend_mb_per_min or 0.0

        cpu_target_eff = float(self.cpu_target or TARGET_CPU_PER_INSTANCE)
        cpu_high_eff = max(self.cpu_high, cpu_target_eff)
        cpu_critical_eff = max(self.cpu_critical, cpu_high_eff + 10)

        # Use the per-service target for the simple, stable rule (mirrors the
        # legacy services.autoscaler behaviour). Use cpu_high_eff for the
        # formula-based multiplier rule (mirrors scaling_ai).
        if cpu >= cpu_target_eff or cpu >= cpu_high_eff or mem_trend > self.mem_growth_mb_min:
            # Formula: aim for ~50% per instance. If per-service target
            # is meaningfully higher than the global default, use it.
            per_instance_target = min(TARGET_CPU_PER_INSTANCE, cpu_target_eff)
            total_instances = self.running_replicas + 1  # home + replicas
            if cpu > 0:
                needed = max(0, int(cpu / per_instance_target) - total_instances + 1)
            else:
                needed = 0
            if needed <= 0 and (cpu >= cpu_target_eff or mem_trend > self.mem_growth_mb_min):
                needed = 1  # at least one if above threshold
            headroom = self.max_replicas - self.running_replicas
            needed = min(needed, headroom)
            if needed > 0:
                r.action = 'scale_up'
                if cpu >= cpu_critical_eff:
                    r.urgency = 'critical'
                elif cpu >= 80:
                    r.urgency = 'high'
                else:
                    r.urgency = 'medium'
                r.scale_up_by = needed
                r.reason = (
                    f'CPU at {cpu:.0f}% — {total_instances} instances '
                    f'→ need {needed} more (target ≤{per_instance_target:.0f}% per instance)'
                )
                if mem_trend > self.mem_growth_mb_min:
                    r.reason = (
                        f'Memory growing at {mem_trend:.1f} MB/min — {r.reason}'
                    )
                return r

        # ── Scale down ──────────────────────────────────────────────────────
        if (
            self.running_replicas > 0
            and cpu <= self.cpu_low
            and not scale_down_cooldown
        ):
            r.action = 'scale_down'
            r.urgency = 'low'
            r.scale_down_by = 1
            r.reason = (
                f'CPU at {cpu:.0f}% with {self.running_replicas} extra '
                f'replicas — removing {r.scale_down_by}.'
            )
            return r

        if scale_down_cooldown and self.running_replicas > 0 and cpu <= self.cpu_low:
            r.reason = 'Scale-down cooldown active.'
            return r

        r.reason = 'Metrics within normal range.'
        return r
