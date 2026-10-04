from __future__ import annotations

from typing import Any

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service


def _resolve_provider_for_service(service: Service, prefer_local: bool = False) -> CloudProvider | None:
    """
    Strict one-to-one provider resolution. No silent fallbacks.
    - If prefer_local, the deployment must run on the local controller:
      return the attached provider only when it is itself an active LOCAL
      provider, else the active LOCAL provider, else fail explicitly.
      A stored REMOTE provider must never silently serve a local deploy.
    - If service has a provider (and local was not requested), it MUST be
      active and we return it.
    - If no provider and local was not requested, prefer REMOTE, then LOCAL.
    - Fail explicitly if intended target unavailable.
    """
    if prefer_local:
        attached = getattr(service, 'provider', None)
        if (
            attached is not None
            and getattr(attached, 'is_active', False)
            and getattr(attached, 'provider_type', None) == CloudProvider.ProviderType.LOCAL
        ):
            return attached
        local = CloudProvider.objects.filter(
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True
        ).first()
        if local:
            return local
        return None

    if service.provider:
        if service.provider.is_active:
            return service.provider
        return None

    remote = CloudProvider.objects.filter(
        provider_type=CloudProvider.ProviderType.REMOTE,
        is_active=True
    ).first()
    if remote:
        return remote

    return CloudProvider.objects.filter(
        provider_type=CloudProvider.ProviderType.LOCAL,
        is_active=True
    ).first()

def _deployment_effective_server(deployment: Deployment) -> Any:
    if bool(getattr(deployment, "target_is_local", False)):
        return None

    server = getattr(deployment, "target_server", None) or getattr(deployment.service, "server", None)
    if server:
        return server

    service = deployment.service
    active_type = getattr(service, "active_target_type", None) or ""
    if active_type.lower() in ("remote", "lite_agent"):
        host_ip = getattr(service, "active_host_ip", None)
        if host_ip:
            from apps.deployments.models.core import ManagedServer
            srv = ManagedServer.objects.filter(host=host_ip).first()
            if srv:
                return srv
            srv = ManagedServer.objects.filter(private_ip=host_ip).first()
            if srv:
                return srv
            srv = ManagedServer.objects.filter(wg_address=host_ip).first()
            if srv:
                return srv

    return None

def _is_local_deployment_server(server, config) -> bool:
    if server is None:
        return True
    return (
        bool(getattr(server, "is_primary", False))
        or str(getattr(server, "host", "") or "") == str(getattr(config, "server_ip", "") or "")
    )
