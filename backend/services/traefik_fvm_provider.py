import json
import logging
import os
import yaml
from django.db import models

logger = logging.getLogger(__name__)

def generate_fvm_traefik_config() -> None:
    """
    Queries active services running on FVM and generates Traefik dynamic config YAML
    for the file provider.
    """
    from apps.deployments.models_core import Service
    from apps.deployments.models_fvm import FVMIPAllocation
    from services.traefik_labels import generate_traefik_labels

    config_dir = "/opt/smsly-hosting/traefik-dynamic"
    os.makedirs(config_dir, exist_ok=True)
    config_path = os.path.join(config_dir, "fvm-routes.yml")

    # We only care about services that are deployed and have the runtime set to 'firecracker'.
    # For Phase 1, we assume all services with allocations need routes.

    http_routers = {}
    http_services = {}
    http_middlewares = {}
    tcp_routers = {}
    tcp_services = {}
    udp_routers = {}
    udp_services = {}

    allocations = FVMIPAllocation.objects.filter(service__isnull=False)

    for alloc in allocations:
        service = alloc.service
        vm_ip = alloc.ip_address

        # We reuse the existing labels generator to keep logic consistent.
        # But we need to translate those flat labels into nested YAML config.
        # It's actually easier to just manually build the Traefik objects for FVM
        # since we know the IP and the ports.

        # Simplified example for HTTP routing:
        app_port = 80 # default

        # We'd parse the actual config from service.env_vars or service config
        service_id = f"fvm-{service.id}"

        # HTTP Service
        http_services[service_id] = {
            "loadBalancer": {
                "servers": [
                    {"url": f"http://{vm_ip}:{app_port}"}
                ]
            }
        }

        # HTTP Router
        # Using service.domains if available, otherwise just instance ID
        primary_domain = f"{service.name}.smsly.app"

        http_routers[service_id] = {
            "rule": f"Host(`{primary_domain}`)",
            "service": service_id,
            "entryPoints": ["websecure"],
            "tls": {}
        }

    config = {
        "http": {
            "routers": http_routers,
            "services": http_services,
            "middlewares": http_middlewares
        },
        "tcp": {
            "routers": tcp_routers,
            "services": tcp_services
        },
        "udp": {
            "routers": udp_routers,
            "services": udp_services
        }
    }

    with open(config_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False)

    logger.info(f"Generated FVM Traefik config at {config_path}")

if __name__ == "__main__":
    import django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    django.setup()
    generate_fvm_traefik_config()
