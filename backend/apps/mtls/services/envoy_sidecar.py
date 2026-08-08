"""
Envoy Sidecar Service
======================
Manages Envoy sidecar lifecycle for transparent mTLS on user services.

The Envoy sidecar handles:
- Inbound mTLS termination (validates caller's SPIFFE identity)
- Outbound mTLS origination (presents this service's SPIFFE identity)
- Dynamic certificate rotation via SPIRE SDS
- L7 authorization policy enforcement via RBAC

Usage:
    from apps.mtls.services.envoy_sidecar import EnvoySidecar

    # Generate config for a service
    config = EnvoySidecar.generate_config(service, mtls_config)

    # Inject sidecar into running service
    EnvoySidecar.inject_sidecar(service)

    # Remove sidecar
    EnvoySidecar.remove_sidecar(service)

    # Check sidecar status
    status = EnvoySidecar.get_sidecar_status(service)
"""

import logging
import os
import re
import time

logger = logging.getLogger(__name__)

ENVOY_IMAGE = os.getenv("ENVOY_SIDECAR_IMAGE", "ghcr.io/smsly/envoy-spire-sidecar:latest")
ENVOY_ADMIN_PORT = 9901
ENVOY_INBOUND_PORT = 80
ENVOY_OUTBOUND_PORT = 8080

# Volume mounts for SPIRE agent socket and SVIDs
SPIRE_AGENT_SOCKET_VOLUME = "spire-ecosystem-agent-socket"
SPIRE_SVIDS_VOLUME = "spire-ecosystem-agent-svids"
SPIRE_AGENT_SOCKET_CONTAINER_PATH = "/opt/spire/run"
SPIRE_SVIDS_CONTAINER_PATH = "/opt/spire/svids"


class EnvoySidecar:
    """Manages Envoy sidecar lifecycle for a service."""

    @staticmethod
    def generate_config(service, mtls_config):
        """
        Generate Envoy YAML config for a service.

        Args:
            service: The Service model instance
            mtls_config: The MtlsConfig model instance

        Returns:
            str: Complete Envoy YAML configuration
        """
        template_path = os.path.join(
            os.path.dirname(__file__),
            "..", "..", "..", "..",
            "infrastructure", "envoy", "envoy.yaml.template",
        )

        try:
            with open(template_path, "r") as f:
                template = f.read()
        except FileNotFoundError:
            raise RuntimeError(f"Envoy template not found at {template_path}")

        # Get service port
        app_port = service.internal_port or 8000

        # Get SPIRE agent socket path
        spire_socket = os.getenv(
            "SPIFFE_ENDPOINT_SOCKET",
            "unix:///opt/spire/run/agent.sock",
        )
        # Strip unix:// prefix for Envoy UDS config
        socket_path = spire_socket.replace("unix://", "")

        # Generate SPIFFE ID
        spiffe_id = mtls_config.spiffe_id or f"spiffe://ecosystem.local/service/{service.name}"
        trust_domain = mtls_config.trust_domain or "ecosystem.local"

        # Replace placeholders
        config = template
        config = config.replace("{{APP_PORT}}", str(app_port))
        config = config.replace("{{TRUST_DOMAIN}}", trust_domain)
        config = config.replace("{{SERVICE_NAME}}", service.name)
        config = config.replace("{{SPIRE_AGENT_SOCKET}}", socket_path)

        return config

    @staticmethod
    def get_sidecar_name(service):
        """Get the container name for a service's Envoy sidecar."""
        safe_name = re.sub(r"[^a-zA-Z0-9_.-]", "", service.name)[:80]
        return f"envoy-{safe_name}"

    @staticmethod
    def inject_sidecar(service):
        """
        Inject Envoy sidecar container alongside a running service.

        The sidecar uses Docker network_mode: service:{main_container}
        so it shares the network namespace with the application container.

        Args:
            service: The Service model instance

        Returns:
            dict: Sidecar container info
        """
        from apps.cloud.docker_client import get_docker_client
        from apps.mtls.models import MtlsConfig

        client = get_docker_client()
        mtls_config = service.mtls_config

        sidecar_name = EnvoySidecar.get_sidecar_name(service)

        # Check if sidecar already exists
        try:
            existing = client.containers.get(sidecar_name)
            if existing.status == "running":
                logger.info("Envoy sidecar already running for %s", service.name)
                return {"status": "already_running", "name": sidecar_name}
            else:
                # Remove stopped sidecar
                existing.remove(force=True)
        except Exception:
            pass

        # Find the main service container
        main_container = EnvoySidecar._find_main_container(client, service)
        if not main_container:
            raise RuntimeError(f"No running container found for service {service.name}")

        # Generate Envoy config
        config = EnvoySidecar.generate_config(service, mtls_config)

        # Write config to a temp file and mount it
        import tempfile
        config_dir = tempfile.mkdtemp(prefix="envoy-config-")
        config_path = os.path.join(config_dir, "envoy.yaml")
        with open(config_path, "w") as f:
            f.write(config)

        try:
            container = client.containers.run(
                image=ENVOY_IMAGE,
                name=sidecar_name,
                detach=True,
                restart_policy={"Name": "unless-stopped"},
                # Share network namespace with main container
                network_mode=f"service:{main_container.name}",
                labels={
                    "managed_by": "smsly-hosting",
                    "envoy_sidecar": "true",
                    "smsly.blue_green.canonical_name": service.name,
                    "com.paas.service": service.name,
                },
                environment={
                    "SPIFFE_TRUST_DOMAIN": mtls_config.trust_domain or "ecosystem.local",
                    "SPIFFE_ENDPOINT_SOCKET": "unix:///opt/spire/run/agent.sock",
                    "SERVICE_NAME": service.name,
                    "APP_PORT": str(service.internal_port or 8000),
                },
                volumes={
                    config_path: {"bind": "/etc/envoy/envoy.yaml", "mode": "ro"},
                    SPIRE_AGENT_SOCKET_VOLUME: {
                        "bind": SPIRE_AGENT_SOCKET_CONTAINER_PATH,
                        "mode": "ro",
                    },
                    SPIRE_SVIDS_VOLUME: {
                        "bind": SPIRE_SVIDS_CONTAINER_PATH,
                        "mode": "ro",
                    },
                },
                security_opt=["no-new-privileges:true"],
                cap_drop=["ALL"],
                mem_limit="64m",
                nano_cpus=int(0.25e9),  # 0.25 CPU
                pids_limit=64,
            )

            logger.info(
                "Injected Envoy sidecar %s for service %s",
                sidecar_name,
                service.name,
            )
            return {
                "status": "injected",
                "name": sidecar_name,
                "container_id": container.id[:12],
            }

        finally:
            # Cleanup temp config
            try:
                os.unlink(config_path)
                os.rmdir(config_dir)
            except Exception:
                pass

    @staticmethod
    def remove_sidecar(service):
        """
        Remove Envoy sidecar container for a service.

        Args:
            service: The Service model instance

        Returns:
            dict: Removal status
        """
        from apps.cloud.docker_client import get_docker_client

        client = get_docker_client()
        sidecar_name = EnvoySidecar.get_sidecar_name(service)

        try:
            container = client.containers.get(sidecar_name)
            container.stop(timeout=5)
            container.remove(force=True)
            logger.info("Removed Envoy sidecar %s for service %s", sidecar_name, service.name)
            return {"status": "removed", "name": sidecar_name}
        except Exception as exc:
            logger.warning("Could not remove sidecar %s: %s", sidecar_name, exc)
            return {"status": "not_found", "name": sidecar_name}

    @staticmethod
    def get_sidecar_status(service):
        """
        Check if the Envoy sidecar is running and healthy.

        Args:
            service: The Service model instance

        Returns:
            dict: Sidecar status information
        """
        from apps.cloud.docker_client import get_docker_client

        client = get_docker_client()
        sidecar_name = EnvoySidecar.get_sidecar_name(service)

        try:
            container = client.containers.get(sidecar_name)

            # Check health via Envoy admin API
            healthy = False
            if container.status == "running":
                try:
                    # Use docker exec to check health
                    result = container.exec_run(
                        ["curl", "-sf", "http://127.0.0.1:9901/ready"],
                        timeout=5,
                    )
                    healthy = result.exit_code == 0
                except Exception:
                    pass

            return {
                "name": sidecar_name,
                "status": container.status,
                "healthy": healthy,
                "container_id": container.id[:12] if container else None,
                "image": container.image.tags[0] if container.image else None,
                "started_at": container.attrs.get("State", {}).get("StartedAt"),
            }
        except Exception:
            return {
                "name": sidecar_name,
                "status": "not_found",
                "healthy": False,
                "container_id": None,
                "image": None,
                "started_at": None,
            }

    @staticmethod
    def _find_main_container(client, service):
        """Find the main container for a service."""
        containers = client.containers.list(
            filters={"label": "managed_by=smsly-hosting"},
        )
        for ctr in containers:
            labels = ctr.labels or {}
            if labels.get("smsly.blue_green.canonical_name") == service.name:
                if not labels.get("envoy_sidecar"):
                    return ctr
        return None

    @staticmethod
    def inject_sidecar_compose(service, compose_data):
        """
        Inject Envoy sidecar into Docker Compose data.

        Used by the deployment pipeline to add sidecar to compose files.

        Args:
            service: The Service model instance
            compose_data: dict - parsed compose YAML

        Returns:
            dict: Modified compose data with sidecar service added
        """
        from apps.mtls.models import MtlsConfig

        try:
            mtls_config = service.mtls_config
            if not mtls_config.enabled:
                return compose_data
        except MtlsConfig.DoesNotExist:
            return compose_data

        sidecar_name = EnvoySidecar.get_sidecar_name(service)
        app_port = service.internal_port or 8000

        # Add sidecar service
        if "services" not in compose_data:
            compose_data["services"] = {}

        compose_data["services"][sidecar_name] = {
            "image": ENVOY_IMAGE,
            "restart": "unless-stopped",
            "network_mode": f"service:{service.name}",
            "environment": {
                "SPIFFE_TRUST_DOMAIN": mtls_config.trust_domain or "ecosystem.local",
                "SPIFFE_ENDPOINT_SOCKET": "unix:///opt/spire/run/agent.sock",
                "SERVICE_NAME": service.name,
                "APP_PORT": str(app_port),
            },
            "volumes": [
                f"{SPIRE_AGENT_SOCKET_VOLUME}:{SPIRE_AGENT_SOCKET_CONTAINER_PATH}:ro",
                f"{SPIRE_SVIDS_VOLUME}:{SPIRE_SVIDS_CONTAINER_PATH}:ro",
            ],
            "security_opt": ["no-new-privileges:true"],
            "cap_drop": ["ALL"],
            "mem_limit": "64m",
            "cpus": 0.25,
            "pids_limit": 64,
            "labels": {
                "managed_by": "smsly-hosting",
                "envoy_sidecar": "true",
                "smsly.blue_green.canonical_name": service.name,
            },
            "depends_on": {
                service.name: {
                    "condition": "service_started",
                },
            },
        }

        # Ensure SPIRE volumes are declared
        if "volumes" not in compose_data:
            compose_data["volumes"] = {}

        compose_data["volumes"][SPIRE_AGENT_SOCKET_VOLUME] = {
            "external": True,
            "name": SPIRE_AGENT_SOCKET_VOLUME,
        }
        compose_data["volumes"][SPIRE_SVIDS_VOLUME] = {
            "external": True,
            "name": SPIRE_SVIDS_VOLUME,
        }

        return compose_data
