import logging

logger = logging.getLogger(__name__)


def resolve_active_execution_target(service) -> dict:
    from apps.deployments.models.core import ManagedServer

    if not service.active_target_type:
        raise ValueError(f"Cannot resolve target for service {service.id}: Missing active runtime metadata.")

    target = {
        "target_type": service.active_target_type,
        "host_ip": service.active_host_ip,
        "runtime_id": service.active_runtime_id,
        "server_obj": None
    }

    if target["target_type"] in ("remote", "lite_agent"):
        if not target["host_ip"]:
             raise ValueError(f"Cannot resolve remote target for service {service.id}: Missing active_host_ip.")

        server = ManagedServer.objects.filter(host=target["host_ip"]).first()
        if not server:
            server = ManagedServer.objects.filter(private_ip=target["host_ip"]).first()
        if not server:
            server = ManagedServer.objects.filter(wg_address=target["host_ip"]).first()
        target["server_obj"] = server

    return target


def resolve_remote_server(service, latest_deploy):
    from django.db.models import Q
    from apps.deployments.models.core import ManagedServer

    if latest_deploy and latest_deploy.target_server_id:
        target = latest_deploy.target_server
        if not target.is_primary:
            return target
    server = getattr(service, 'server', None)
    if server and not server.is_primary:
        return server
    provider = getattr(service, 'provider', None)
    if provider and provider.provider_type in ('REMOTE', 'LITE_AGENT'):
        host = provider.host or getattr(provider, 'api_url', None)
        if host:
            return ManagedServer.objects.filter(
                Q(host=host) | Q(private_ip=host)
            ).first()
    return None
