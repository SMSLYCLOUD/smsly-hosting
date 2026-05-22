import logging
from apps.deployments.models_core import Service, Deployment

logger = logging.getLogger(__name__)

def resolve_active_execution_target(service: Service) -> dict:
    """
    Resolve the true execution target of a service based on verified post-deployment metadata.
    This replaces frontend intent or historical guesses.

    Returns a dict:
    {
        "target_type": str (e.g., 'local', 'remote', 'lite_agent'),
        "host_ip": str,
        "runtime_id": str,
        "server_obj": ManagedServer or None
    }

    Raises ValueError if authoritative metadata is missing.
    """
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

        # Try to resolve to a ManagedServer object if possible
        from apps.deployments.models_core import ManagedServer
        server = ManagedServer.objects.filter(host=target["host_ip"]).first()
        if not server:
            server = ManagedServer.objects.filter(private_ip=target["host_ip"]).first()
        target["server_obj"] = server

    return target
