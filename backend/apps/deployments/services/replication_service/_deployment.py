import base64
import ipaddress
import logging
import shlex
from urllib.parse import urlparse

from ._utils import _bounded_error

logger = logging.getLogger(__name__)


class DeploymentMixin:

    @classmethod
    def validate_mesh_for_replication(cls, mesh):
        from apps.deployments.models.core import ManagedServer

        peers = list(mesh.peers.filter(is_active=True).select_related("server"))
        if len(peers) < 2:
            raise ValueError("Need at least 2 active peers for replication")
        if not any(peer.is_local for peer in peers):
            raise ValueError("Mesh must include a local peer before replication can be enabled.")

        addresses = set()
        for peer in peers:
            try:
                ipaddress.ip_address(peer.wg_address)
            except ValueError as exc:
                raise ValueError(f"Peer {peer} has an invalid WireGuard address.") from exc
            if peer.wg_address in addresses:
                raise ValueError(f"Duplicate WireGuard address detected: {peer.wg_address}")
            addresses.add(peer.wg_address)

            if peer.is_local:
                continue
            if not peer.server:
                raise ValueError(f"Remote peer {peer.wg_address} is not linked to a server.")
            if peer.server.status != ManagedServer.Status.ONLINE:
                raise ValueError(
                    f"Server '{peer.server.name}' is {peer.server.status}; replication requires ONLINE nodes."
                )
            if not (peer.server.ssh_key or peer.server.ssh_password):
                raise ValueError(f"Server '{peer.server.name}' has no SSH credentials for replication.")

        return peers

    @classmethod
    def deploy_replication(cls, mesh, db_password, admin_password,
                            replication_password="repl_pass"):
        cls.validate_mesh_for_replication(mesh)

        is_fresh = mesh.replication_status == "DISABLED"

        configs = cls.generate_patroni_compose(
            mesh, db_password, admin_password, replication_password,
            is_fresh=is_fresh,
        )
        haproxy_compose, haproxy_cfg = cls.generate_haproxy_compose(mesh)

        results = {"patroni": [], "haproxy": None}

        for peer in mesh.peers.filter(is_active=True):
            wg_ip = peer.wg_address
            compose_content = configs.get(wg_ip)
            if not compose_content:
                continue

            try:
                if peer.is_local:
                    cls._deploy_patroni_local(compose_content)
                elif peer.server:
                    cls._deploy_patroni_remote(peer.server, compose_content)

                results["patroni"].append({
                    "peer": str(peer), "wg_address": wg_ip, "status": "OK",
                })
            except Exception as e:
                logger.error(f"Failed to deploy Patroni to {wg_ip}: {e}")
                results["patroni"].append({
                    "peer": str(peer), "wg_address": wg_ip,
                    "status": f"FAILED: {_bounded_error(e)}",
                })

        try:
            cls._deploy_haproxy_local(haproxy_compose, haproxy_cfg)
            results["haproxy"] = "OK"
        except Exception as e:
            logger.error(f"Failed to deploy HAProxy: {e}")
            results["haproxy"] = f"FAILED: {_bounded_error(e)}"

        return results

    @classmethod
    def wait_for_cluster_ready(cls, mesh, timeout_seconds=180, poll_seconds=5):
        import time

        deadline = time.monotonic() + timeout_seconds
        last_health = None
        while time.monotonic() < deadline:
            last_health = cls.check_replication_health(mesh)
            nodes = last_health.get("nodes", [])
            if nodes:
                unreachable = [
                    node for node in nodes
                    if "UNREACHABLE" in str(node.get("status", ""))
                ]
                if not unreachable and last_health.get("primary"):
                    return {"status": "READY", "health": last_health}
            time.sleep(poll_seconds)
        return {"status": "TIMEOUT", "health": last_health or {}}

    @classmethod
    def _helper_network_for_docker_host(cls, client):
        import os

        explicit = str(os.environ.get("DOCKER_HELPER_NETWORK", "")).strip()
        if explicit:
            return explicit

        docker_host = os.environ.get("DOCKER_HOST", "")
        parsed = urlparse(docker_host)
        host = parsed.hostname or ""
        preferred = os.environ.get("DOCKER_NETWORK", "").strip()
        if host and host not in {"127.0.0.1", "localhost"}:
            try:
                matches = client.containers.list(all=True, filters={"name": host})
                for container in matches:
                    networks = (
                        container.attrs
                        .get("NetworkSettings", {})
                        .get("Networks", {})
                    )
                    if preferred and preferred in networks:
                        return preferred
                    if networks:
                        return next(iter(networks))
            except Exception as exc:
                logger.warning("Could not inspect Docker host helper network: %s", exc)
        return preferred or None

    @classmethod
    def _deploy_patroni_local(cls, compose_content: str):
        import docker

        client = docker.from_env()
        import os
        docker_host = os.environ.get("DOCKER_HOST", "tcp://socket-proxy:2375")
        compose_b64 = base64.b64encode(compose_content.encode()).decode()

        commands = [
            "mkdir -p /tmp/smsly-patroni",
            f"printf %s {shlex.quote(compose_b64)} | base64 -d > /tmp/smsly-patroni/docker-compose.yml",
            "cd /tmp/smsly-patroni && docker compose -p smsly-patroni up -d --pull always",
        ]
        run_kwargs = {
            "image": "docker:cli",
            "command": ["sh", "-c", " && ".join(commands)],
            "remove": True,
            "environment": {"DOCKER_HOST": docker_host},
        }
        helper_network = cls._helper_network_for_docker_host(client)
        if helper_network:
            run_kwargs["network"] = helper_network
        elif urlparse(docker_host).hostname in {"127.0.0.1", "localhost"}:
            run_kwargs["network_mode"] = "host"

        client.containers.run(
            **run_kwargs,
        )

    @classmethod
    def _deploy_patroni_remote(cls, server, compose_content: str):
        from apps.deployments.services.wireguard_service import WireGuardService

        compose_b64 = base64.b64encode(compose_content.encode()).decode()
        commands = [
            "mkdir -p /opt/smsly/patroni",
            f"printf %s {shlex.quote(compose_b64)} | base64 -d > /opt/smsly/patroni/docker-compose.yml",
            "cd /opt/smsly/patroni && docker compose -p smsly-patroni up -d --pull always",
        ]
        WireGuardService._ssh_run(server, " && ".join(commands), timeout=120)

    @classmethod
    def _deploy_haproxy_local(cls, compose_content: str, haproxy_cfg: str):
        import os

        import docker

        client = docker.from_env()
        docker_host = os.environ.get("DOCKER_HOST", "tcp://socket-proxy:2375")
        compose_b64 = base64.b64encode(compose_content.encode()).decode()

        commands = [
            "mkdir -p /tmp/smsly-haproxy",
            f"printf %s {shlex.quote(compose_b64)} | base64 -d > /tmp/smsly-haproxy/docker-compose.yml",
            "cd /tmp/smsly-haproxy && docker compose -p smsly-haproxy up -d --pull always",
        ]
        run_kwargs = {
            "image": "docker:cli",
            "command": ["sh", "-c", " && ".join(commands)],
            "remove": True,
            "environment": {"DOCKER_HOST": docker_host},
        }
        helper_network = cls._helper_network_for_docker_host(client)
        if helper_network:
            run_kwargs["network"] = helper_network
        elif urlparse(docker_host).hostname in {"127.0.0.1", "localhost"}:
            run_kwargs["network_mode"] = "host"

        client.containers.run(**run_kwargs)
