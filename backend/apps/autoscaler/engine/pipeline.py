"""
Top-level autoscaler pipeline.

The public function ``analyze_and_apply(service)`` is the single entry
point that all three previous Celery tasks and the legacy REST views
delegate to. It:

  1. Collects metrics via ``MetricsCollector`` (Prometheus → DB → Docker
     socket).
  2. Computes the running replica count and last-spawn time for the
     service.
  3. Feeds those into ``DecisionEngine`` to get a Recommendation.
  4. Hands the Recommendation to ``Reconciler`` to spawn or destroy
     replicas (with per-service locking to prevent double-spawn races).

It is the only function any of the three legacy entry points need to
call. All three are kept as thin wrappers for backward compatibility.
"""
import logging
import os

from django.core.cache import cache as django_cache
from django.utils import timezone

from .decision import DecisionEngine, Recommendation
from .metrics import MetricsCollector
from .reconciler import Reconciler, ScaleResult

logger = logging.getLogger(__name__)

# Circuit breaker: when all metric sources fail consecutively this many
# times, the pipeline switches to "metrics starvation" mode and refuses
# to scale down until at least one successful metric read.
_METRICS_STARVATION_LIMIT = int(os.environ.get("SCALE_METRICS_STARVATION_LIMIT", "3"))


def _starvation_key(service_id: str) -> str:
    return f"autoscale_metrics_starved:{service_id}"


def _check_starvation(service_id: str) -> bool:
    """Return True if the service is in metrics-starvation mode."""
    count = django_cache.get(_starvation_key(service_id)) or 0
    return count >= _METRICS_STARVATION_LIMIT


def _record_metrics_outage(service_id: str):
    """Increment the consecutive-outage counter."""
    key = _starvation_key(service_id)
    count = django_cache.get(key) or 0
    django_cache.set(key, count + 1, timeout=300)  # 5 min TTL


def _clear_starvation(service_id: str):
    """Reset the counter after a successful metric read."""
    django_cache.delete(_starvation_key(service_id))


def analyze_and_apply(service, *, now=None, min_interval_seconds: int = 0) -> ScaleResult:
    """One-shot: collect metrics → decide → reconcile. Returns ScaleResult.

    Accepts either a ``Service`` instance or its UUID string. The
    Celery task path passes strings to avoid carrying ORM instances
    across the broker boundary.

    If *min_interval_seconds* > 0, the function skips services that
    were analysed within that window (dedup cache).  The 30 s task
    passes 120 so the slower 3-min sweep always wins for services
    that qualify for both.
    """
    from apps.deployments.models_core import Service

    now = now or timezone.now()

    if not isinstance(service, Service):
        try:
            service = Service.objects.get(id=service)
        except (Service.DoesNotExist, ValueError):
            logger.warning("analyze_and_apply: service %s not found", service)
            return ScaleResult(
                recommendation=Recommendation(),
                applied=False, error='service not found',
            )

    # Dedup: skip if another task analysed this service recently.
    if min_interval_seconds > 0:
        cache_key = f'autoscale_last:{service.id}'
        if django_cache.get(cache_key):
            return ScaleResult(
                recommendation=Recommendation(reason='dedup: recently analysed'),
                applied=False,
            )
        django_cache.set(cache_key, '1', timeout=min_interval_seconds)

    # 1. Metrics
    metrics = MetricsCollector(service).collect()

    # Circuit breaker: if all metric sources fail, track consecutive
    # outages. Once the limit is reached, refuse to scale down (the
    # DecisionEngine will see cpu_percent=None and return 'none').
    if metrics.cpu_percent is None:
        _record_metrics_outage(str(service.id))
        if _check_starvation(str(service.id)):
            logger.warning(
                "Metrics starvation for %s (%d+ consecutive outages) — "
                "scaling decisions deferred.",
                service.name, _METRICS_STARVATION_LIMIT,
            )
    else:
        _clear_starvation(str(service.id))

    # 2. Running replicas + last spawn for cooldown
    from apps.deployments.models_replica import ServiceReplica
    running = ServiceReplica.objects.filter(
        service=service, status='RUNNING'
    ).count()
    spawning = ServiceReplica.objects.filter(
        service=service, status__in=('SPAWNING', 'DRAINING')
    ).exists()
    last_destroyed = ServiceReplica.objects.filter(
        service=service, status='DESTROYED',
    ).order_by('-destroyed_at').first()
    last_spawned = ServiceReplica.objects.filter(
        service=service, status='RUNNING',
    ).order_by('-created_at').first()

    # Use the most recent of last_scale_at, last spawned, last destroyed
    candidates = [service.last_scale_at, last_spawned.created_at if last_spawned else None,
                  last_destroyed.destroyed_at if last_destroyed and last_destroyed.destroyed_at else None]
    last_event = max((c for c in candidates if c is not None), default=None)

    # 3. Per-service cooldown overrides (from alert_config JSON).
    # If set, they take precedence over the global SCALE_COOLDOWN_*
    # environment variables.
    alert_cfg = dict(service.alert_config or {})  # copy to avoid mutating the DB field
    cooldown_up = alert_cfg.get('cooldown_up_min')
    cooldown_down = alert_cfg.get('cooldown_down_min')

    # 4. Decide
    engine = DecisionEngine(
        metrics,
        running_replicas=running,
        max_replicas=service.max_replicas,
        cpu_target=service.autoscale_cpu_target,
        last_scale_at=last_event,
        spawning_in_progress=spawning,
        now=now,
        **({'cooldown_up_min': int(cooldown_up)} if cooldown_up is not None else {}),
        **({'cooldown_down_min': int(cooldown_down)} if cooldown_down is not None else {}),
    )
    rec: Recommendation = engine.decide()

    # 4. Apply
    result = Reconciler(service, now=now).apply(rec)
    return result


def analyze_only(service, *, now=None) -> dict:
    """Collect + decide without applying. Used by the /analyze REST endpoint
    and the AI enhancement path in the legacy views_autoscale."""
    from apps.deployments.models_replica import ServiceReplica
    now = now or timezone.now()

    metrics = MetricsCollector(service).collect()
    running = ServiceReplica.objects.filter(service=service, status='RUNNING').count()
    spawning = ServiceReplica.objects.filter(
        service=service, status__in=('SPAWNING', 'DRAINING')
    ).exists()
    last_spawned = ServiceReplica.objects.filter(
        service=service, status='RUNNING',
    ).order_by('-created_at').first()

    alert_cfg = dict(service.alert_config or {})  # copy to avoid mutating the DB field
    cooldown_up = alert_cfg.get('cooldown_up_min')
    cooldown_down = alert_cfg.get('cooldown_down_min')

    engine = DecisionEngine(
        metrics,
        running_replicas=running,
        max_replicas=service.max_replicas,
        cpu_target=service.autoscale_cpu_target,
        last_scale_at=last_spawned.created_at if last_spawned else service.last_scale_at,
        spawning_in_progress=spawning,
        now=now,
        **({'cooldown_up_min': int(cooldown_up)} if cooldown_up is not None else {}),
        **({'cooldown_down_min': int(cooldown_down)} if cooldown_down is not None else {}),
    )
    rec = engine.decide()
    return {
        'service': str(service.id),
        'service_name': service.compose_main_service or service.name,
        'metrics': metrics.to_dict(),
        'recommendation': rec.to_dict(),
        'timestamp': now.isoformat(),
    }
