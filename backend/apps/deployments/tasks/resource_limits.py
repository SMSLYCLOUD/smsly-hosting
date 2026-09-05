"""One-off + event-driven application of Service resource limits."""
import logging

from celery import shared_task

from apps.deployments.constants import (
    RETRY_DELAY_FAST,
    TASK_TIME_LIMIT_QUICK,
)

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=RETRY_DELAY_FAST, soft_time_limit=TASK_TIME_LIMIT_QUICK[0], time_limit=TASK_TIME_LIMIT_QUICK[1], name="apps.deployments.tasks.apply_service_resource_limits")
def apply_service_resource_limits_task(self, service_id: str) -> dict:
    """Apply a service's stored CPU/RAM limits to its running containers.

    Dispatched by the Service post_save signal so limit changes take
    effect without a redeploy. Never raises on the final attempt —
    returns the helper result (or the error) instead.
    """
    from apps.deployments.models import Service
    from apps.deployments.services.resource_limits import apply_service_resource_limits

    try:
        service = Service.objects.get(id=service_id)
    except Service.DoesNotExist:
        return {"updated": [], "skipped_containers": [], "skipped": "service not found", "errors": []}
    try:
        return apply_service_resource_limits(service)
    except Exception as exc:
        logger.warning("Live limit apply failed for service %s: %s", service_id, exc)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=RETRY_DELAY_FAST)
        return {"updated": [], "skipped_containers": [], "skipped": None, "errors": [str(exc)]}
