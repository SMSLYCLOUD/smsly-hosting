from __future__ import annotations

import logging

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


    if provider.provider_type == CloudProvider.ProviderType.LOCAL:
        from .health import _local_route_timeout_seconds, _wait_for_local_route_ready
        route_timeout = _local_route_timeout_seconds(service)
        _wait_for_local_route_ready(
            deployment, service, timeout_seconds=route_timeout,
        )
