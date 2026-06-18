"""Auto-scaling API endpoints and Celery tasks — thin wrappers around the
unified ``apps.autoscaler.engine`` pipeline.

The two periodic Celery tasks (``check-autoscale-every-30s`` and
``auto-scaling-analyze-every-3m``) used to run *two different* engines
with overlapping logic and no coordination. They now both call
``apps.autoscaler.engine.pipeline.analyze_and_apply`` and the engine
holds a per-service lock, so they cannot double-spawn replicas even
when their intervals overlap.

The ``analyze_and_scale_service`` function is preserved (signature and
return value) because it is the function mocked by
``tests/test_autoscale_pagination.py`` and exposed via the views at
``apps.deployments.views_autoscale.ScalingViewSet.analyze``.
"""
import logging
from celery import shared_task
from django.db import models

from apps.deployments.models_core import Service
from apps.deployments.models_replica import ServiceReplica

logger = logging.getLogger(__name__)


AUTOSCALE_BATCH_SIZE = 20


@shared_task(
    name='apps.deployments.tasks_autoscale.analyze_all_services_task',
    bind=True,
    ignore_result=True,
)
def analyze_all_services_task(self):
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
        qs = Service.objects.filter(status='RUNNING').distinct()
        qs = qs.filter(
            models.Q(id__in=base) | models.Q(compose_file='', deploy_mode='SINGLE')
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


def analyze_and_scale_service(service_id):
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
    return analyze_and_apply(service)

