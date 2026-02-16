"""
Health monitor service — monitors container health and auto-restarts unhealthy services.

Checks each service's health_check_path via HTTP request to the container.
Updates health_status and triggers restart if auto_restart is enabled.
"""
import logging
import requests
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

# Track consecutive failures per service (in-memory, resets on worker restart)
_failure_counts = {}


@shared_task
def monitor_health_task():
    """
    Check health for all active services.

    For each service with a health_check_path, sends an HTTP request
    to the container and updates health_status accordingly.
    """
    from apps.deployments.models import Service, Deployment

    services = Service.objects.exclude(health_check_path='')
    checked = 0

    for service in services:
        try:
            _check_service_health(service, Deployment)
            checked += 1
        except Exception as e:
            logger.error("Health check failed for %s: %s", service.name, e)

    logger.debug("Health checked %d services", checked)


def _check_service_health(service, Deployment):
    """Check a single service's health via HTTP."""
    # Find active deployment
    latest = Deployment.objects.filter(
        service=service, status=Deployment.Status.ACTIVE
    ).order_by('-created_at').first()

    if not latest:
        if service.health_status != 'unknown':
            service.health_status = 'unknown'
            service.save(update_fields=['health_status', 'updated_at'])
        return

    # Build health check URL
    # Try container_id first (for direct Docker), then domain
    container_id = latest.container_id
    port = service.internal_port or 8000
    path = service.health_check_path

    # Try health check via public domain first, then container
    health_url = None
    if service.public_domain:
        health_url = f"https://{service.public_domain}{path}"
    elif container_id:
        # Direct container check via Docker bridge network
        health_url = f"http://{container_id[:12]}:{port}{path}"

    if not health_url:
        return

    service_key = str(service.id)
    timeout = service.health_check_timeout or 5

    try:
        resp = requests.get(health_url, timeout=timeout, verify=False)
        is_healthy = 200 <= resp.status_code < 400

        if is_healthy:
            _failure_counts[service_key] = 0
            if service.health_status != 'healthy':
                service.health_status = 'healthy'
                service.save(update_fields=['health_status', 'updated_at'])
                logger.info("✓ %s is healthy", service.name)
        else:
            _handle_failure(service, service_key, f"HTTP {resp.status_code}")
    except requests.Timeout:
        _handle_failure(service, service_key, "Timeout")
    except requests.ConnectionError:
        _handle_failure(service, service_key, "Connection refused")
    except Exception as e:
        _handle_failure(service, service_key, str(e))


def _handle_failure(service, service_key, reason):
    """Handle a health check failure. Auto-restart if threshold exceeded."""
    _failure_counts[service_key] = _failure_counts.get(service_key, 0) + 1
    count = _failure_counts[service_key]
    retries = service.health_check_retries or 3

    logger.warning(
        "✗ %s health check failed (%d/%d): %s",
        service.name, count, retries, reason,
    )

    if count >= retries:
        service.health_status = 'unhealthy'
        service.save(update_fields=['health_status', 'updated_at'])

        if service.auto_restart:
            logger.info("🔄 Auto-restarting unhealthy service: %s", service.name)
            _trigger_restart(service)
            _failure_counts[service_key] = 0


def _trigger_restart(service):
    """Trigger a redeployment of an unhealthy service."""
    try:
        from apps.deployments.tasks import smart_deploy_task
        from apps.deployments.models import Deployment

        latest = Deployment.objects.filter(
            service=service, status=Deployment.Status.ACTIVE
        ).order_by('-created_at').first()

        if latest:
            # Create a new deployment that rebuilds from the same commit
            new_deployment = Deployment.objects.create(
                service=service,
                commit_hash=latest.commit_hash or 'HEAD',
                status=Deployment.Status.QUEUED,
            )
            service.health_status = 'starting'
            service.save(update_fields=['health_status', 'updated_at'])

            smart_deploy_task.delay(
                str(service.id),
                str(new_deployment.id),
            )
            logger.info("Auto-restart triggered for %s", service.name)
    except Exception as e:
        logger.error("Auto-restart failed for %s: %s", service.name, e)
