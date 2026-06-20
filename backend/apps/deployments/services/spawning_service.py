"""Auto-scaling replica spawner: create/destroy container replicas on remote nodes."""
import contextlib
import logging
import shlex

from django.utils import timezone

from .ssh_client import SSHClient

logger = logging.getLogger(__name__)


class SpawningService:
    """Creates and destroys service replicas on remote managed servers."""

    def __init__(self):
        self._ssh_clients = {}

    def _get_ssh(self, node):
        if node.id not in self._ssh_clients:
            client = SSHClient(
                ip=node.host,
                key_content=node.ssh_key,
                user=node.ssh_user,
                port=node.ssh_port,
                password=node.ssh_password,
                wg_address=node.wg_address,
            )
            client.connect()
            self._ssh_clients[node.id] = client
        return self._ssh_clients[node.id]

    def spawn(self, service, node, replica):
        """SSH into node, pull image, run container with Traefik labels.

        The replica gets identical Traefik labels so Traefik on the
        target node auto-discovers it and adds it to the load-balancer.
        """
        from apps.deployments.models import PlatformConfig
        config = PlatformConfig.load()
        ssh = self._get_ssh(node)

        # Pre-flight: check node has enough free resources
        if not self._check_node_capacity(ssh, node, service):
            raise RuntimeError(
                f"Node {node.name} has insufficient free resources "
                f"to spawn a replica for {service.name}"
            )

        name = self._safe_name(f"{service.name}-replica-{replica.id.hex[:8]}")
        image = service.docker_image or ''
        if not image:
            raise ValueError(f"Service {service.name} has no docker_image set")

        port = str(service.internal_port or 8000)
        domain = service.public_domain or f"{name}.localhost"
        net = "smsly-net"
        router = name.replace('.', '-').replace('_', '-')

        # Traefik labels — identical routing, Traefik round-robins
        labels = [
            "traefik.enable=true",
            f"traefik.docker.network={net}",
            f"traefik.http.routers.{router}.rule=Host(`{domain}`)",
            f"traefik.http.routers.{router}.entrypoints=web",
            f"traefik.http.services.{router}.loadbalancer.server.port={port}",
            "managed_by=smsly-hosting",
            f"smsly.blue_green.canonical_name={shlex.quote(service.name)}",
            "smsly.replica=true",
        ]

        if config.use_ssl:
            labels.append(f"traefik.http.routers.{router}.entrypoints=websecure")
            labels.append("traefik.http.routers.{router}.tls=true")

        label_args = " ".join(f"-l {shlex.quote(label)}" for label in labels)

        # Env vars from the service
        env_args = ""
        for ev in service.env_vars.all():
            env_args += f" -e {shlex.quote(ev.key)}={shlex.quote(ev.value)}"

        cmd = (
            f"docker pull {shlex.quote(image)} 2>/dev/null; "
            f"docker rm -f {shlex.quote(name)} 2>/dev/null; "
            f"docker run -d --name {shlex.quote(name)} "
            f"--restart unless-stopped --network {shlex.quote(net)} "
            f"{label_args} {env_args} "
            f"{shlex.quote(image)}"
        )

        _out, err, exit_code = ssh.exec_command(cmd, raise_on_error=False)
        if exit_code != 0:
            raise RuntimeError(f"Failed to spawn replica on {node.name}: {err}")

        # Get container ID
        cid_out, _, _ = ssh.exec_command(
            f"docker inspect --format='{{{{.Id}}}}' {shlex.quote(name)}",
            raise_on_error=False,
        )
        replica.container_id = cid_out.strip()[:64]
        replica.container_name = name
        replica.status = 'RUNNING'
        replica.save(update_fields=['container_id', 'container_name', 'status'])

        logger.info("Spawned replica %s on %s (%s)", name, node.name, node.host)
        return replica

    def destroy(self, replica):
        """SSH into node, stop and remove the replica container."""
        if not replica.node:
            replica.status = 'DESTROYED'
            replica.destroyed_at = timezone.now()
            replica.save(update_fields=['status', 'destroyed_at'])
            return

        replica.status = 'DESTROYING'
        replica.save(update_fields=['status'])

        try:
            ssh = self._get_ssh(replica.node)
            name = shlex.quote(replica.container_name)
            ssh.exec_command(
                f"docker stop {name} 2>/dev/null; docker rm -f {name} 2>/dev/null",
                raise_on_error=False,
            )
        except Exception as exc:
            logger.warning("Failed to destroy replica %s: %s", replica.container_name, exc)

        replica.status = 'DESTROYED'
        replica.destroyed_at = timezone.now()
        replica.save(update_fields=['status', 'destroyed_at'])
        logger.info("Destroyed replica %s on %s", replica.container_name, replica.node.name)

    def cleanup(self):
        """Close all SSH connections."""
        for client in self._ssh_clients.values():
            with contextlib.suppress(Exception):
                client.close()
        self._ssh_clients.clear()

    def spawn_local(self, service, replica):
        """Create a replica on the local Docker daemon — no SSH needed."""
        import docker as docker_lib

        from apps.deployments.models import PlatformConfig

        config = PlatformConfig.load()
        client = docker_lib.from_env()
        name = self._safe_name(f"{service.name}-replica-{replica.id.hex[:8]}")
        image = service.docker_image or ''
        if not image:
            raise ValueError(f"Service {service.name} has no docker_image set")

        # Check local capacity
        self._check_local_capacity(service)

        port = str(service.internal_port or 8000)
        domain = service.public_domain or f"{name}.localhost"
        net = "smsly-net"
        router = name.replace('.', '-').replace('_', '-')

        labels = {
            "traefik.enable": "true",
            "traefik.docker.network": net,
            f"traefik.http.routers.{router}.rule": f"Host(`{domain}`)",
            f"traefik.http.routers.{router}.entrypoints": "web",
            f"traefik.http.services.{router}.loadbalancer.server.port": port,
            "managed_by": "smsly-hosting",
            "smsly.blue_green.canonical_name": service.name,
            "smsly.replica": "true",
        }
        if config.use_ssl:
            labels[f"traefik.http.routers.{router}.entrypoints"] = "websecure,web"
            labels[f"traefik.http.routers.{router}.tls"] = "true"

        env_vars = {ev.key: ev.value for ev in service.env_vars.all()}
        container = client.containers.run(
            image=image,
            name=name,
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            network=net,
            labels=labels,
            environment=env_vars,
        )
        replica.container_id = container.id[:64]
        replica.container_name = name
        replica.status = 'RUNNING'
        replica.save(update_fields=['container_id', 'container_name', 'status'])
        logger.info("Spawned replica %s locally", name)
        return replica

    def _check_local_capacity(self, service):
        """Raise if local host lacks free RAM for the replica."""
        min_ram_mb = getattr(service, 'memory_mb', None) or 128
        try:
            with open('/proc/meminfo') as f:
                mem = {}
                for line in f:
                    if ':' in line:
                        k, v = line.split(':', 1)
                        mem[k.strip()] = int(v.strip().split()[0])
                available = (mem.get('MemAvailable', 0) + mem.get('Cached', 0)) // 1024
                total = mem.get('MemTotal', 0) // 1024
                if available < min_ram_mb:
                    raise RuntimeError(f"Local host: {available} MB free, need {min_ram_mb}")
                if total > 0 and (available / total * 100) < 20:
                    raise RuntimeError(f"Local host: only {available/total*100:.0f}% RAM free")
        except FileNotFoundError:
            pass  # not Linux — skip check

    def _check_node_capacity(self, ssh, node, service):
        """Verify node has enough free RAM to run another replica."""
        min_ram_mb = getattr(service, 'memory_mb', None) or 128
        try:
            out, _, _ = ssh.exec_command(
                "free -m | awk '/^Mem:/{print ($4+$7) \" \" $2}'",
                raise_on_error=False,
            )
            parts = out.strip().split()
            if len(parts) >= 2:
                available_mb, total_mb = int(parts[0]), int(parts[1])
                free_pct = (available_mb / total_mb * 100) if total_mb > 0 else 0
                if available_mb < min_ram_mb:
                    logger.warning("Node %s: %d MB free (need %d)", node.name, available_mb, min_ram_mb)
                    return False
                if free_pct < 20:
                    logger.warning("Node %s: only %.0f%% RAM free", node.name, free_pct)
                    return False
                logger.info("Node %s OK: %d MB free (%.0f%%), %s needs %d MB",
                            node.name, available_mb, free_pct, service.name, min_ram_mb)
                return True
        except Exception as exc:
            logger.warning("Capacity check failed for %s: %s", node.name, exc)
        return False  # safer to refuse if we can't check

    @staticmethod
    def _safe_name(name: str) -> str:
        import re
        return re.sub(r'[^a-zA-Z0-9_.-]', '', name)[:100]
