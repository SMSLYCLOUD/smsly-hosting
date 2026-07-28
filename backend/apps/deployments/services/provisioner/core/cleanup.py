import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.deployments.constants import TASK_TIME_LIMIT_QUICK
from apps.deployments.models.servers import ManagedServer

from .helpers import PROVISION_TIMEOUT_SECONDS, _append_log

logger = logging.getLogger(__name__)


@shared_task(name="apps.deployments.services.provisioner.cleanup_stale_server_provisioning", soft_time_limit=TASK_TIME_LIMIT_QUICK[0], time_limit=TASK_TIME_LIMIT_QUICK[1])
def cleanup_stale_server_provisioning():
    stale_after_seconds = max(3600, PROVISION_TIMEOUT_SECONDS * 2)
    cutoff = timezone.now() - timedelta(seconds=stale_after_seconds)
    stale_servers = ManagedServer.objects.filter(
        provision_status=ManagedServer.ProvisionStatus.PROVISIONING,
        updated_at__lt=cutoff,
    )

    cleaned = 0
    for server in stale_servers:
        server.provision_status = ManagedServer.ProvisionStatus.FAILED
        server.save(update_fields=["provision_status", "updated_at"])
        _append_log(
            server,
            (
                "Provisioning was auto-marked as failed because no updates were "
                f"received for over {stale_after_seconds} seconds."
            ),
        )
        cleaned += 1

    pending_cutoff = timezone.now() - timedelta(hours=24)
    stale_pending = ManagedServer.objects.filter(
        provision_status=ManagedServer.ProvisionStatus.PENDING,
        updated_at__lt=pending_cutoff,
    )
    for server in stale_pending:
        server.provision_status = ManagedServer.ProvisionStatus.FAILED
        server.save(update_fields=["provision_status", "updated_at"])
        _append_log(
            server,
            (
                "Provisioning was auto-marked as failed because the server was "
                "never provisioned (stuck in PENDING for over 24 hours)."
            ),
        )
        cleaned += 1

    if cleaned:
        logger.warning("Auto-cleaned %d stale provisioning records", cleaned)
    return cleaned
