from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.cloud.models import CloudProvider
from apps.cloud.services.compute import ComputeService
from apps.deployments.models import Deployment
from apps.deployments.utils import (
    append_log,
    broadcast_status,
)

from .state import _mark_deployment_active, _post_deploy_success

logger = logging.getLogger(__name__)

def _get_config_hours(field: str, default: int) -> int:
    """Read deploy timing from PlatformConfig, falling back to default."""
    try:
        from apps.deployments.models import PlatformConfig
        return getattr(PlatformConfig.load(), field, default) or default
    except Exception:
        return default


def _do_promote(deployment: Deployment, provider: CloudProvider) -> None:
    service = deployment.service
    green_id = deployment.green_container_id
    if not green_id:
        raise RuntimeError("No green container ID on deployment — cannot promote")

    compute = ComputeService(provider)
    adapter = compute.adapter

    if not hasattr(adapter, 'promote_container'):
        target_type = "remote" if provider.provider_type == CloudProvider.ProviderType.REMOTE else "lite_agent"
        host_ip = "unknown"
        if getattr(provider, 'server', None):
            host_ip = provider.server.private_ip or provider.server.host

        _mark_deployment_active(deployment, target_type, host_ip, green_id)

        deployment.container_id = green_id
        deployment.status = Deployment.Status.ACTIVE
        deployment.finished_at = timezone.now()
        deployment.save()

        service.active_target_type = target_type
        service.active_host_ip = host_ip
        service.active_runtime_id = green_id
        service.save(update_fields=['active_target_type', 'active_host_ip', 'active_runtime_id'])

        broadcast_status(deployment)

        _post_deploy_success(deployment, service)
        return

    promoted_id = adapter.promote_container(service.name, green_id)

    _mark_deployment_active(deployment, "local", "127.0.0.1", promoted_id)

    deployment.container_id = promoted_id
    deployment.status = Deployment.Status.ACTIVE
    deployment.finished_at = timezone.now()
    deployment.save()

    service.active_target_type = "local"
    service.active_host_ip = "127.0.0.1"
    service.active_runtime_id = promoted_id
    service.save(update_fields=['active_target_type', 'active_host_ip', 'active_runtime_id'])

    broadcast_status(deployment)

    _post_deploy_success(deployment, service)
    append_log(
        deployment,
        f"[OK] Deployment promoted to ACTIVE. Container: {promoted_id}\n"
    )

    # After promote, the staged container is destroyed. Reset staging
    # verification status so the UI reflects there is no active staged
    # deployment.  Custom staging domains keep their DNS verification
    # (the CNAME is still valid), but auto-generated ones lose meaning.
    if service.staging_domain_verified:
        service.staging_domain_verified = False
        service.save(update_fields=['staging_domain_verified'])


    if provider.provider_type == CloudProvider.ProviderType.LOCAL:
        from .health import _local_route_timeout_seconds, _wait_for_local_route_ready
        route_timeout = _local_route_timeout_seconds(service)
        _wait_for_local_route_ready(
            deployment, service, timeout_seconds=route_timeout,
        )


@shared_task(
    name="apps.deployments.tasks.auto_promote_staged_deployments",
    soft_time_limit=300,
    time_limit=330,
)
def auto_promote_staged_deployments():
    """Auto-promote deployments in STAGED status for longer than configured hours."""
    hours = _get_config_hours('auto_promote_hours', 12)
    if hours <= 0:
        return {'promoted': 0, 'skipped': 'disabled'}

    threshold = timezone.now() - timedelta(hours=hours)
    staged = Deployment.objects.filter(
        status=Deployment.Status.STAGED,
        staged_at__lte=threshold,
    ).select_related('service')

    promoted = 0
    for deployment in staged:
        try:
            from .providers import _resolve_provider_for_service
            provider = _resolve_provider_for_service(deployment.service, prefer_local=True)
            if not provider:
                logger.warning("Auto-promote: no provider for %s, skipping", deployment.service.name)
                continue
            _do_promote(deployment, provider)
            append_log(
                deployment,
                f"[AUTO-PROMOTE] Deployment auto-promoted after {hours} hours.\n"
            )
            promoted += 1
        except Exception as exc:
            logger.exception("Auto-promote failed for deployment %s: %s", deployment.id, exc)

    return {'promoted': promoted}


@shared_task(
    name="apps.deployments.tasks.auto_review_deployments",
    soft_time_limit=300,
    time_limit=330,
)
def auto_review_deployments():
    """Auto-approve deployments stuck in REVIEW status for longer than configured hours."""
    hours = _get_config_hours('auto_review_hours', 2)
    if hours <= 0:
        return {'approved': 0, 'skipped': 'disabled'}

    threshold = timezone.now() - timedelta(hours=hours)
    reviews = Deployment.objects.filter(
        status=Deployment.Status.REVIEW,
        created_at__lte=threshold,
    ).select_related('service')

    approved = 0
    for deployment in reviews:
        try:
            from ..views._helpers import _resolve_provider_for_target
            provider = _resolve_provider_for_target(
                deployment.service,
                target_is_local=bool(getattr(deployment, 'target_is_local', False)),
            )
            if not provider:
                logger.warning("Auto-review: no provider for %s, skipping", deployment.service.name)
                continue

            deployment.status = Deployment.Status.BUILDING
            deployment.started_at = timezone.now()
            deployment.save(update_fields=['status', 'started_at'])

            from .build import resume_deploy_task
            resume_deploy_task.delay(
                deployment_id=str(deployment.id),
                provider_id=str(provider.id),
            )

            append_log(
                deployment,
                f"[AUTO-REVIEW] Deployment auto-approved after {hours} hours. Build starting.\n"
            )
            approved += 1
        except Exception as exc:
            logger.exception("Auto-review failed for deployment %s: %s", deployment.id, exc)

    return {'approved': approved}
