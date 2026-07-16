"""Auto-scaling replica spawner: create/destroy container replicas on remote nodes."""
import contextlib
import json
import logging
import shlex

from django.utils import timezone

from .container_runtime import get_runtime_for_container
from .network_scope import apply_egress_restrictions, ensure_scoped_network
from .ssh_client import SSHClient

logger = logging.getLogger(__name__)


def _scoped_network_for(service) -> str:
    """Get or create an isolated network for a user service.

    Checks if the service's project has a ScopedNetwork override configured.
    If so, uses that network and egress policy. Otherwise defaults to per-service
    bridge isolation.
    """
    project = getattr(service, "project", None)
    if project:
        from apps.deployments.models_network_scope import ScopedNetwork
        scoped = ScopedNetwork.get_for_object(project)
        if scoped:
            cfg = ScopedNetwork.resolve_network_config(project)
            ensure_scoped_network(cfg)
            apply_egress_restrictions(cfg["name"], cfg.get("allowed_egress_networks", ["0.0.0.0/0"]))
            return cfg["name"]

    short_id = str(service.id).replace("-", "")[:12]
    net_name = f"smsly-svc-{short_id}"
    ensure_scoped_network({
        "name": net_name,
        "driver": "bridge",
        "internal": False,
        "enable_ipv6": False,
    })
    # Restrict egress — only allow DNS + internet, block other user services
    apply_egress_restrictions(net_name, ["0.0.0.0/0"])
    return net_name



def _detect_remote_runtime(ssh) -> str | None:
    """Detect sandboxed container runtime on a remote node via SSH.

    Returns ``--runtime runsc``, ``--runtime kata-runtime``, or empty string.
    """
    try:
        out, _, _ = ssh.exec_command(
            "docker info --format '{{json .Runtimes}}'",
            raise_on_error=False,
            timeout=300,
        )
        runtimes = json.loads(out.strip() or "{}")
        if "kata-runtime" in runtimes:
            return "--runtime kata-runtime"
        if "runsc" in runtimes:
            return "--runtime runsc"
    except Exception:
        logger.debug("Remote runtime detection failed, falling back to runc")
    return ""


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
        scoped_net = _scoped_network_for(service)
        net = scoped_net  # primary network — isolate from other services
        router = name.replace('.', '-').replace('_', '-')

        # Traefik labels — point Traefik at the scoped bridge for routing
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

        login_cmd = ""
        if getattr(service, 'registry_credential_id', None) and service.registry_credential.is_active:
            # Use per-service RegistryCredential (third-party registry pull)
            raw_user = service.registry_credential.username
            raw_pwd = service.registry_credential.password
            raw_url = (
                service.registry_credential.registry_url
                .replace("https://", "").replace("http://", "")
                .split("/")[0]
            )
            # SECURITY: Use printf '%s' instead of echo to avoid escape-sequence
            # interpretation and to prevent the password appearing in shell history.
            login_cmd = (
                f"printf '%s\\n' {shlex.quote(raw_pwd)} "
                f"| docker login --username {shlex.quote(raw_user)} "
                f"--password-stdin {shlex.quote(raw_url)}; "
            )
        elif not login_cmd:
            # Fall back to ScopedRegistry chain (Project → Team → Organization → PlatformConfig)
            try:
                from apps.deployments.models_registry_scope import ScopedRegistry
                scope_obj = getattr(service, 'project', None)
                registry_info = ScopedRegistry.resolve_registry_credentials(scope_obj)
                raw_url = (registry_info.get("url") or "").split("://")[-1].rstrip("/")
                raw_user = registry_info.get("username") or ""
                raw_pwd = registry_info.get("password") or ""
                if raw_url and raw_user and raw_pwd:
                    login_cmd = (
                        f"printf '%s\\n' {shlex.quote(raw_pwd)} "
                        f"| docker login --username {shlex.quote(raw_user)} "
                        f"--password-stdin {shlex.quote(raw_url)}; "
                    )
            except Exception:
                logger.debug("Could not resolve scoped registry credentials for spawn; proceeding without auth")


        # Detect sandboxed runtime on the remote node
        runtime_flag = _detect_remote_runtime(ssh)

        mem_mb = getattr(service, 'memory_mb', 2048) or 2048
        cpus = getattr(service, 'cpu_cores', 1.0) or 1.0
        sec_flags = (
            "--security-opt no-new-privileges:true --security-opt apparmor=docker-default "
            "--cap-drop=ALL --cap-add=NET_BIND_SERVICE --cap-add=CHOWN --cap-add=SETUID --cap-add=SETGID "
            f"--memory={mem_mb}m --cpus={cpus} --pids-limit=1024 "
        )

        cmd = (
            f"{login_cmd}"
            f"docker pull {shlex.quote(image)} 2>/dev/null; "
            f"docker rm -f {shlex.quote(name)} 2>/dev/null; "
            f"docker run -d --name {shlex.quote(name)} "
            f"{sec_flags}"
            f"{runtime_flag} "
            f"--restart unless-stopped --network {shlex.quote(net)} "
            f"{label_args} {env_args} "
            f"{shlex.quote(image)}; "
        )

        _out, err, exit_code = ssh.exec_command(cmd, raise_on_error=False, timeout=300)
        if exit_code != 0:
            raise RuntimeError(f"Failed to spawn replica on {node.name}: {err}")

        # Get container ID
        cid_out, _, _ = ssh.exec_command(
            f"docker inspect --format='{{{{.Id}}}}' {shlex.quote(name)}",
            raise_on_error=False,
            timeout=300,
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
                timeout=300,
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
        scoped_net = _scoped_network_for(service)
        net = scoped_net
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

        if getattr(service, 'registry_credential_id', None) and service.registry_credential.is_active:
            try:
                client.login(
                    username=service.registry_credential.username,
                    password=service.registry_credential.password,
                    registry=service.registry_credential.registry_url.replace("https://", "").replace("http://", "").split("/")[0],
                )
            except Exception as e:
                logger.warning("Local docker login failed: %s", e)

        env_vars = {ev.key: ev.value for ev in service.env_vars.all()}

        mem_mb = getattr(service, 'memory_mb', 2048) or 2048
        cpus = getattr(service, 'cpu_cores', 1.0) or 1.0
        runtime = get_runtime_for_container(service_name=service.name)

        container = client.containers.run(
            image=image,
            name=name,
            detach=True,
            restart_policy={"Name": "unless-stopped"},
            network=net,
            labels=labels,
            environment=env_vars,
            security_opt=["no-new-privileges:true", "apparmor:docker-default"],
            cap_drop=["ALL"],
            cap_add=["NET_BIND_SERVICE", "CHOWN", "SETUID", "SETGID"],
            mem_limit=f"{mem_mb}m",
            nano_cpus=int(float(cpus) * 1e9),
            pids_limit=1024,
            runtime=runtime,
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
                timeout=300,
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
