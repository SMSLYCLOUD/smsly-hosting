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

from django.utils import timezone

from .decision import DecisionEngine, Recommendation
from .metrics import MetricsCollector
from .reconciler import Reconciler, ScaleResult

logger = logging.getLogger(__name__)


def analyze_and_apply(service, *, now=None) -> ScaleResult:
    """One-shot: collect metrics → decide → reconcile. Returns ScaleResult.

    Accepts either a ``Service`` instance or its UUID string. The
    Celery task path passes strings to avoid carrying ORM instances
    across the broker boundary.
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

    # 1. Metrics
    metrics = MetricsCollector(service).collect()

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

    # 3. Decide
    engine = DecisionEngine(
        metrics,
        running_replicas=running,
        max_replicas=service.max_replicas,
        cpu_target=service.autoscale_cpu_target,
        last_scale_at=last_event,
        spawning_in_progress=spawning,
        now=now,
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

    engine = DecisionEngine(
        metrics,
        running_replicas=running,
        max_replicas=service.max_replicas,
        cpu_target=service.autoscale_cpu_target,
        last_scale_at=last_spawned.created_at if last_spawned else service.last_scale_at,
        spawning_in_progress=spawning,
        now=now,
    )
    rec = engine.decide()
    return {
        'service': str(service.id),
        'service_name': service.compose_main_service or service.name,
        'metrics': metrics.to_dict(),
        'recommendation': rec.to_dict(),
        'timestamp': now.isoformat(),
    }
