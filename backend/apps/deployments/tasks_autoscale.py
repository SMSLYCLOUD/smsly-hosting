"""Auto-scaling API endpoints and Celery tasks."""
import logging
from celery import shared_task
from django.db import models
from rest_framework import viewsets, permissions, status, serializers
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.deployments.models_core import Service, ManagedServer
from apps.deployments.models_replica import ServiceReplica
from apps.deployments.services.spawning_service import SpawningService
from apps.deployments.services.node_scorer import NodeScorer
from apps.deployments.services.scaling_ai import ScalingAnalyzer

logger = logging.getLogger(__name__)


@shared_task(
    name='apps.deployments.tasks_autoscale.analyze_all_services_task',
    bind=True,
    ignore_result=True,
)
def analyze_all_services_task(self):
    """Periodic task: analyze active services and auto-scale as needed."""
    active = ServiceReplica.objects.filter(status='RUNNING').values_list('service_id', flat=True)
    services = Service.objects.filter(status='RUNNING').distinct()
    services = services.filter(
        models.Q(id__in=active) | models.Q(compose_file='', deploy_mode='SINGLE')
    )
    analyzed = 0
    for svc in services[:20]:  # batch limit per tick
        try:
            analyze_and_scale_service(str(svc.id))
            analyzed += 1
        except Exception as exc:
            logger.warning("Auto-scale failed for %s: %s", svc.name, exc)
    return {'analyzed': analyzed}


def analyze_and_scale_service(service_id: str):
    """Celery task: analyze a service and auto-scale if needed."""
    try:
        service = Service.objects.get(id=service_id)
    except Service.DoesNotExist:
        logger.warning("Auto-scale task: service %s not found", service_id)
        return

    # 1. AI analysis
    analyzer = ScalingAnalyzer(service)
    result = analyzer.analyze()
    rec = result['recommendation']

    if rec['action'] == 'none':
        return

    # 2. Find best node
    candidates = ManagedServer.objects.filter(
        status=ManagedServer.Status.ONLINE,
        allow_user_workloads=True,
    ).exclude(is_primary=True)

    if not candidates.exists():
        logger.warning("No candidate nodes for scaling %s", service.name)
        return

    scorer = NodeScorer()
    scores = scorer.score(candidates)

    # 3. Execute
    spawner = SpawningService()
    try:
        if rec['action'] == 'scale_up':
            count = min(rec['scale_up_by'], len(scores))
            spawned = 0
            for node, score, resources in scores:
                if spawned >= count:
                    break
                if score < 20:  # node too loaded
                    continue
                replica = ServiceReplica.objects.create(
                    service=service, node=node,
                    spawn_reason=rec['reason'],
                    status='SPAWNING',
                )
                try:
                    spawner.spawn(service, node, replica)
                    spawned += 1
                    logger.info(
                        "Auto-scaled %s: spawned replica on %s (score=%.1f)",
                        service.name, node.name, score,
                    )
                except Exception as exc:
                    logger.error("Failed to spawn replica for %s on %s: %s", service.name, node.name, exc)
                    replica.status = 'DESTROYED'
                    replica.save(update_fields=['status'])

        elif rec['action'] == 'scale_down':
            # Destroy idle replicas
            replicas = ServiceReplica.objects.filter(
                service=service, status='RUNNING'
            ).order_by('created_at')
            for replica in replicas:
                spawner.destroy(replica)
                logger.info("Auto-scaled down %s: destroyed replica %s", service.name, replica.container_name)

    finally:
        spawner.cleanup()

    return result
