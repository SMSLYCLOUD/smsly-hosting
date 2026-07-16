# pylint: disable=invalid-name
"""
Autoscaler service — thin entry point that delegates to the unified
``apps.autoscaler.engine`` pipeline.

The legacy ``_evaluate_scaling`` helper is retained for backward
compatibility with the existing test suite and the original simple
"scale min_replicas by 1 if avg CPU over the last 2m exceeds the
service's autoscale_cpu_target" semantics.
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def check_autoscale_task():
    """Check CPU usage for all services and scale replicas if needed.

    Delegates to the unified engine so the two Celery beat tasks
    (``check_autoscale_task`` and ``tasks_autoscale.analyze_all_services_task``)
    cannot double-spawn replicas for the same service — the engine
    uses a per-service lock inside ``Reconciler``.
    """
    from apps.autoscaler.engine.pipeline import analyze_and_apply
    from apps.deployments.models import Service

    services = Service.objects.filter(
        autoscale_enabled=True, max_replicas__gt=1,
    )

    for service in services:
        try:
            # 120 s dedup window: the 3-min sweep always wins.  This
            # prevents the 30 s quick-check from duplicating work that
            # the 3-min batch task is already doing.
            analyze_and_apply(service, min_interval_seconds=120)
        except Exception as e:
            logger.error("Autoscale check failed for %s: %s", service.name, e)


def _evaluate_scaling(service, ServiceMetric):
    """Evaluate whether a service needs scaling — LEGACY.

    This function is retained for backward compatibility with existing
    tests. It is NO LONGER CALLED by the production autoscale path;
    that uses the unified ``DecisionEngine`` + ``Reconciler`` in
    ``apps.autoscaler.engine``.

    IMPORTANT: This function previously mutated ``service.min_replicas``
    to track the computed replica count, which is semantically wrong.
    ``min_replicas`` is a user-facing configuration hint, not a runtime
    replica counter. The unified engine uses actual ``ServiceReplica``
    rows for replica tracking instead. This legacy function now ONLY
    computes the recommendation — it no longer modifies the service
    record.
    """
    import warnings
    warnings.warn(
        "_evaluate_scaling is deprecated. Use apps.autoscaler.engine.pipeline.analyze_and_apply().",
        DeprecationWarning,
        stacklevel=2,
    )

    from apps.deployments.services.server_guard import ServerGuard

    if ServerGuard.is_control_plane(getattr(service, "server", None)):
        logger.warning(
            "Autoscale skipped for %s: control-plane server is not a workload target",
            service.name,
        )
        return None

    now = timezone.now()

    # Cooldown enforcement (uses the dedicated last_scale_at field so that
    # unrelated writes to `updated_at` do not reset the cooldown).
    last_scaled = service.last_scale_at
    if last_scaled:
        time_since_scale = now - last_scaled
        if time_since_scale < timedelta(minutes=1):
            return None  # Global 1-minute cooldown for any scaling to prevent rapid flapping

    target_cpu = service.autoscale_cpu_target
    # Count actual running replicas
    from apps.deployments.models_replica import ServiceReplica
    running_replicas = ServiceReplica.objects.filter(
        service=service, status='RUNNING'
    ).count()
    current_replicas = 1 + running_replicas  # home instance + spawned

    # Get avg CPU over last 2 minutes
    recent_metrics = ServiceMetric.objects.filter(
        service=service,
        timestamp__gte=now - timedelta(minutes=2),
    ).order_by('-timestamp')

    if not recent_metrics.exists():
        return None  # No metrics, can't decide

    avg_cpu = sum(m.cpu_percent for m in recent_metrics) / recent_metrics.count()

    # Scale UP: CPU > target for 2+ minutes
    if avg_cpu > target_cpu and current_replicas < service.max_replicas:
        new_replicas = min(current_replicas + 1, service.max_replicas)
        logger.info(
            "⬆ Scaling UP %s: %d → %d replicas (CPU: %.1f%% > %d%%)",
            service.name, current_replicas, new_replicas, avg_cpu, target_cpu,
        )
        service.last_scale_at = now
        service.save(update_fields=['last_scale_at'])
        return {'action': 'scale_up', 'replicas': new_replicas}

    # Scale DOWN: CPU < 50% of target for 5+ minutes
    five_min_metrics = ServiceMetric.objects.filter(
        service=service,
        timestamp__gte=now - timedelta(minutes=5),
    )
    if five_min_metrics.exists():
        avg_cpu_5m = sum(m.cpu_percent for m in five_min_metrics) / five_min_metrics.count()
        scale_down_threshold = target_cpu * 0.5

        if avg_cpu_5m < scale_down_threshold and current_replicas > 1:
            if last_scaled and (now - last_scaled) < timedelta(minutes=5):
                return None  # 5-minute cooldown for scale down operations

            new_replicas = max(current_replicas - 1, 1)
            logger.info(
                "⬇ Scaling DOWN %s: %d → %d replicas (CPU: %.1f%% < %.0f%%)",
                service.name, current_replicas, new_replicas,
                avg_cpu_5m, scale_down_threshold,
            )
            service.last_scale_at = now
            service.save(update_fields=['last_scale_at'])
            return {'action': 'scale_down', 'replicas': new_replicas}

    return None
