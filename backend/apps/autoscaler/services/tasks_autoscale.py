from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from celery import shared_task
from django.db import models as db_models

from apps.deployments.constants import TASK_TIME_LIMIT_MEDIUM
from apps.deployments.models import Service
from apps.autoscaler.models.replica import ServiceReplica

# AUTOSCALE_BATCH_SIZE: maximum services to process per cursor page.
# The periodic task walks all eligible services in batches to avoid
# a single long-running query holding locks or OOMing.
AUTOSCALE_BATCH_SIZE = 20

@shared_task(
    name='apps.autoscaler.services.tasks_autoscale.analyze_all_services_task',
    bind=True,
    ignore_result=True,
    soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0],
    time_limit=TASK_TIME_LIMIT_MEDIUM[1],
)
def analyze_all_services_task(self) -> dict[str, int]:
    """Periodic task: analyze active services and auto-scale as needed.

    Uses an ``id__gt`` cursor so the batch of 20 never silently drops
    services when more than 20 are candidates. Delegates each
    per-service decision to ``analyze_and_scale_service`` so the
    test suite (which patches that name) and the ``ScalingViewSet``
    REST endpoint share the same code path.
    """
    analyzed = 0
    last_id = None
    while True:
        base = ServiceReplica.objects.filter(status='RUNNING').values_list(
            'service_id', flat=True
        )
        qs = Service.objects.filter(
            status='ACTIVE',
        ).filter(
            db_models.Q(autoscale_enabled=True) | db_models.Q(autoscale_enabled__isnull=True),
        ).distinct()
        qs = qs.filter(
            db_models.Q(id__in=base) | db_models.Q(compose_file='', deploy_mode='SINGLE')
        )
        if last_id is not None:
            qs = qs.filter(id__gt=last_id)
        batch = list(qs.order_by('id')[:AUTOSCALE_BATCH_SIZE])
        if not batch:
            break
        for svc in batch:
            try:
                analyze_and_scale_service(str(svc.id))
                analyzed += 1
            except Exception as exc:
                logger.warning("Auto-scale failed for %s: %s", svc.name, exc)
        last_id = batch[-1].id
    return {'analyzed': analyzed}


def analyze_and_scale_service(service_id) -> dict[str, object] | None:
    """Public entry point used by the Celery task, REST endpoint, and tests.

    Accepts a ``Service`` UUID string (from the Celery task / test mocks)
    or a ``Service`` instance (from the REST view). Delegates to the
    unified engine pipeline.
    """
    from apps.autoscaler.engine.pipeline import analyze_and_apply

    if isinstance(service_id, Service):
        service = service_id
    else:
        try:
            service = Service.objects.get(id=service_id)
        except (Service.DoesNotExist, ValueError, TypeError):
            logger.warning("Auto-scale task: service %s not found", service_id)
            return None
    # Pass dedup window so the 3-min sweep and the 30s quick-check
    # share the same cache key and never race on the same service.
    return analyze_and_apply(service, min_interval_seconds=120)


_STUCK_SPAWN_THRESHOLD_SECONDS = 300


@shared_task(
    name='apps.autoscaler.services.tasks_autoscale.cleanup_stuck_spawning',
    bind=True,
    ignore_result=True,
    soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0],
    time_limit=TASK_TIME_LIMIT_MEDIUM[1],
)
def cleanup_stuck_spawning(self) -> dict[str, int]:
    """Mark replicas stuck in SPAWNING for > 5 minutes as DESTROYED.

    Without this, a failed spawn leaves the replica in SPAWNING forever,
    which blocks all future scaling for that service (spawning_in_progress
    check in the pipeline always returns True).
    """
    from datetime import timedelta
    from django.utils import timezone

    threshold = timezone.now() - timedelta(seconds=_STUCK_SPAWN_THRESHOLD_SECONDS)
    stuck = ServiceReplica.objects.filter(
        status='SPAWNING',
        created_at__lt=threshold,
    )
    count = stuck.count()
    if count > 0:
        logger.warning("Cleaning up %d stuck SPAWNING replicas (older than %ds)", count, _STUCK_SPAWN_THRESHOLD_SECONDS)
        stuck.update(status='DESTROYED', destroyed_at=timezone.now())
    return {'cleaned': count}
