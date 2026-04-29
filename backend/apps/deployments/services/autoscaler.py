# pylint: disable=invalid-name
"""
Autoscaler service — scales container replicas based on CPU utilization.

Checks CPU usage against the service's autoscale_cpu_target and adjusts
replica count between min_replicas and max_replicas.
"""
import logging
from celery import shared_task
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger(__name__)


@shared_task
def check_autoscale_task():
    """
    Check CPU usage for all services and scale replicas if needed.

    Logic:
    - If avg CPU > target for 2+ minutes: scale up
    - If avg CPU < target * 0.5 for 5+ minutes: scale down
    - Never go below min_replicas or above max_replicas
    """
    from apps.deployments.models import Service
    from apps.deployments.models_metrics import ServiceMetric

    services = Service.objects.filter(
        max_replicas__gt=1,  # Only check services with autoscaling enabled
    )

    for service in services:
        try:
            _evaluate_scaling(service, ServiceMetric)
        except Exception as e:
            logger.error("Autoscale check failed for %s: %s", service.name, e)


def _evaluate_scaling(service, ServiceMetric):
    """Evaluate whether a service needs scaling."""
    now = timezone.now()

    # Cooldown enforcement
    last_scaled = service.updated_at
    if last_scaled:
        time_since_scale = now - last_scaled
        if time_since_scale < timedelta(minutes=1):
            return  # Global 1-minute cooldown for any scaling to prevent rapid flapping

    target_cpu = service.autoscale_cpu_target
    current_replicas = service.min_replicas

    # Get avg CPU over last 2 minutes
    recent_metrics = ServiceMetric.objects.filter(
        service=service,
        timestamp__gte=now - timedelta(minutes=2),
    ).order_by('-timestamp')

    if not recent_metrics.exists():
        return  # No metrics, can't decide

    avg_cpu = sum(m.cpu_percent for m in recent_metrics) / recent_metrics.count()

    # Scale UP: CPU > target for 2+ minutes
    if avg_cpu > target_cpu and current_replicas < service.max_replicas:
        new_replicas = min(current_replicas + 1, service.max_replicas)
        logger.info(
            "⬆ Scaling UP %s: %d → %d replicas (CPU: %.1f%% > %d%%)",
            service.name, current_replicas, new_replicas, avg_cpu, target_cpu,
        )
        service.min_replicas = new_replicas
        service.save(update_fields=['min_replicas', 'updated_at'])
        return

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
                return  # 5-minute cooldown for scale down operations

            new_replicas = max(current_replicas - 1, 1)
            logger.info(
                "⬇ Scaling DOWN %s: %d → %d replicas (CPU: %.1f%% < %.0f%%)",
                service.name, current_replicas, new_replicas,
                avg_cpu_5m, scale_down_threshold,
            )
            service.min_replicas = new_replicas
            service.save(update_fields=['min_replicas', 'updated_at'])
