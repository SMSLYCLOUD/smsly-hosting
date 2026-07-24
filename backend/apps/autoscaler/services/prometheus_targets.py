"""
Prometheus target file management.
Writes file_sd_configs JSON files for remote docker-labels exporters.
"""
import contextlib
import json
import logging
import os
import shlex
import tempfile

logger = logging.getLogger(__name__)

_CONFIGURED_TARGETS_DIR = os.environ.get(
    "PROMETHEUS_TARGETS_DIR",
    "/opt/smsly-hosting/prometheus-targets",
)
_FALLBACK_TARGETS_DIR = os.path.join(tempfile.gettempdir(), "smsly-prometheus-targets")

TARGETS_DIR = _CONFIGURED_TARGETS_DIR
_FALLBACK_WARNED = False

DOCKER_LABELS_PORT = int(os.environ.get("DOCKER_LABELS_PORT", "9234"))


def _ensure_target_dir_writable() -> bool:
    """Try to make TARGETS_DIR writable. Falls back to a temp directory."""
    global TARGETS_DIR, _FALLBACK_WARNED

    with contextlib.suppress(OSError):
        os.makedirs(TARGETS_DIR, exist_ok=True)

    if os.access(TARGETS_DIR, os.W_OK):
        return True

    # Attempt self-healing: chmod the directory if the kernel allows it
    # (may succeed with CAP_FOWNER or if running as root).
    try:
        os.chmod(TARGETS_DIR, 0o2777)
        if os.access(TARGETS_DIR, os.W_OK):
            return True
    except OSError:
        pass

    # Attempt to remove stale files owned by root that block writing
    try:
        for f in os.listdir(TARGETS_DIR):
            fpath = os.path.join(TARGETS_DIR, f)
            if os.path.isfile(fpath):
                os.chmod(fpath, 0o666)
                os.remove(fpath)
    except OSError:
        pass

    # Retry after cleanup
    if os.access(TARGETS_DIR, os.W_OK):
        return True

    # Fall back to a writable temp directory
    TARGETS_DIR = _FALLBACK_TARGETS_DIR
    with contextlib.suppress(OSError):
        os.makedirs(TARGETS_DIR, exist_ok=True)

    if os.access(TARGETS_DIR, os.W_OK):
        if not _FALLBACK_WARNED:
            logger.warning(
                "Prometheus targets directory not writable, using fallback: %s",
                TARGETS_DIR,
            )
            _FALLBACK_WARNED = True
        return True

    return False


def write_docker_labels_targets():
    """Write Prometheus file_sd target files for all remote docker-labels exporters."""
    try:
        from apps.deployments.models.core import ManagedServer
    except ImportError:
        logger.warning("ManagedServer model not available — skipping target generation")
        return

    if not _ensure_target_dir_writable():
        logger.warning(
            "Prometheus targets directory %s is not writable. "
            "Run the following on the host to fix: "
            "  chown -R 1000:1000 %s && chmod 2777 %s",
            TARGETS_DIR, TARGETS_DIR, TARGETS_DIR,
        )
        return

    # Local node target
    local_targets = [
        {
            "targets": [f"smsly-docker-labels:{DOCKER_LABELS_PORT}"],
            "labels": {"node": "local", "job": "docker-labels"},
        }
    ]
    _write_target_file("docker-labels-local.json", local_targets)

    # Remote node targets
    remote_servers = ManagedServer.objects.filter(
        is_primary=False,
        status=ManagedServer.Status.ONLINE,
    )

    remote_targets = []
    for server in remote_servers:
        reachable_ip = server.wg_address or server.private_ip or server.host
        if not reachable_ip:
            logger.warning("Server %s has no reachable IP — skipping", server.name)
            continue
        remote_targets.append(
            {
                "targets": [f"{reachable_ip}:{DOCKER_LABELS_PORT}"],
                "labels": {
                    "node": server.name,
                    "host": server.host,
                    "job": "docker-labels",
                },
            }
        )

    if remote_targets:
        _write_target_file("docker-labels-remote.json", remote_targets)

    # Also generate file_sd targets for remote cAdvisor and Node Exporter
    cadvisor_targets = []
    node_exporter_targets = []
    for server in remote_servers:
        reachable_ip = server.wg_address or server.private_ip or server.host
        if not reachable_ip:
            continue
        cadvisor_targets.append({
            "targets": [f"{reachable_ip}:{CADVISOR_PORT}"],
            "labels": {"node": server.name, "host": server.host, "job": "cadvisor"},
        })
        node_exporter_targets.append({
            "targets": [f"{reachable_ip}:{NODE_EXPORTER_PORT}"],
            "labels": {"node": server.name, "host": server.host, "job": "node-exporter"},
        })

    if cadvisor_targets:
        _write_target_file("cadvisor-remote.json", cadvisor_targets)
    if node_exporter_targets:
        _write_target_file("node-exporter-remote.json", node_exporter_targets)

    logger.info(
        "Wrote %d local + %d remote docker-labels, %d cadvisor, %d node-exporter targets",
        len(local_targets),
        len(remote_targets),
        len(cadvisor_targets),
        len(node_exporter_targets),
    )


def deploy_docker_labels_exporter_on_node(server, force: bool = False):
    """SSH into a remote ManagedServer and deploy the docker-labels exporter container."""
    from apps.deployments.services.ssh_client import SSHClient

    client = SSHClient(
        ip=server.host,
        key_content=server.ssh_key,
        user=server.ssh_user,
        port=server.ssh_port,
        password=server.ssh_password,
        wg_address=server.wg_address,
    )

    try:
        client.connect()
    except Exception as exc:
        logger.error("SSH connection failed for %s: %s", server.name, exc)
        return False

    tmp = None
    try:
        # 0. Check if exporter is already running — skip if not forced
        if not force:
            out, _err, _code = client.exec_command(
                "docker inspect smsly-docker-labels --format='{{.State.Status}}' 2>/dev/null",
                raise_on_error=False,
            )
            existing_status = out.strip()
            if existing_status == "running":
                logger.debug("docker-labels exporter already running on %s", server.name)
                return True

        # 1. Write exporter script to temp file and upload
        exporter_script = _get_exporter_script_content()
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
        tmp.write(exporter_script)
        tmp.close()

        remote_path = "/opt/smsly-hosting/docker-labels-exporter.py"
        client.exec_command("mkdir -p /opt/smsly-hosting")
        client.upload_file(tmp.name, remote_path)

        # 2. Remove any stale container and pull image
        client.exec_command("docker rm -f smsly-docker-labels 2>/dev/null")
        client.exec_command("docker pull python:3.12-alpine 2>/dev/null", raise_on_error=False)

        # 4. Run the exporter container.
        # Bind to 0.0.0.0 — the WireGuard mesh provides encryption and Docker
        # bypasses UFW. Binding to a specific WG IP fails when the interface
        # isn't fully up yet (docker run rejects the bind).
        cmd = (
            f"docker run -d --name smsly-docker-labels --restart unless-stopped "
            f"--security-opt no-new-privileges:true --security-opt apparmor=docker-default "
            f"-v /var/run/docker.sock:/var/run/docker.sock:ro "
            f"-p {DOCKER_LABELS_PORT}:{DOCKER_LABELS_PORT} "
            f"-e NODE_NAME={shlex.quote(server.name)} "
            f"-v {remote_path}:/app/exporter.py:ro "
            f"python:3.12-alpine python3 -u /app/exporter.py"
        )
        _out, err, exit_code = client.exec_command(cmd, raise_on_error=False)
        if exit_code != 0:
            error = err.strip()
            logger.error("Failed to start docker-labels on %s: %s", server.name, error)
            return False

        logger.info("Deployed docker-labels exporter on %s", server.name)
        return True

    except Exception as exc:
        logger.error("Deploy failed for %s: %s", server.name, exc)
        return False
    finally:
        if tmp:
            with contextlib.suppress(Exception):
                os.unlink(tmp.name)
        with contextlib.suppress(Exception):
            client.close()


CADVISOR_PORT = 8080
NODE_EXPORTER_PORT = 9100


def _node_bind_ip(server) -> str:
    """Return the WireGuard mesh IP if available, else 0.0.0.0."""
    wg = getattr(server, "wg_address", None)
    return wg if wg else "0.0.0.0"


def deploy_cadvisor_on_node(server, force: bool = False):
    """SSH into a remote server and deploy cAdvisor for container metrics."""
    from apps.deployments.services.ssh_client import SSHClient
    client = SSHClient(
        ip=server.host, key_content=server.ssh_key, user=server.ssh_user,
        port=server.ssh_port, password=server.ssh_password, wg_address=server.wg_address,
    )
    try:
        client.connect()
        client.exec_command(
            f"docker rm -f smsly-cadvisor 2>/dev/null; "
            f"docker run -d --name smsly-cadvisor --restart unless-stopped "
            f"--security-opt no-new-privileges:true --security-opt apparmor=docker-default "
            f"--privileged "
            f"-v /:/rootfs:ro -v /var/run/docker.sock:/var/run/docker.sock:ro "
            f"-v /sys:/sys:ro -v /var/lib/docker/:/var/lib/docker:ro "
            f"-v /dev/disk/:/dev/disk:ro "
            f"-p {CADVISOR_PORT}:{CADVISOR_PORT} "
            f"gcr.io/cadvisor/cadvisor:v0.49.1 "
            f"--containerd=unix:///var/run/containerd/containerd.sock",
            raise_on_error=False,
        )
        logger.info("Deployed cAdvisor on %s", server.name)
        return True
    except Exception as exc:
        logger.error("cAdvisor deploy failed for %s: %s", server.name, exc)
        return False
    finally:
        with contextlib.suppress(Exception):
            client.close()


def deploy_node_exporter_on_node(server, force: bool = False):
    """SSH into a remote server and deploy Node Exporter for host metrics."""
    from apps.deployments.services.ssh_client import SSHClient
    client = SSHClient(
        ip=server.host, key_content=server.ssh_key, user=server.ssh_user,
        port=server.ssh_port, password=server.ssh_password, wg_address=server.wg_address,
    )
    try:
        client.connect()
        client.exec_command(
            f"docker rm -f smsly-node-exporter 2>/dev/null; "
            f"docker run -d --name smsly-node-exporter --restart unless-stopped "
            f"--security-opt no-new-privileges:true --security-opt apparmor=docker-default "
            f"-v /proc:/host/proc:ro -v /sys:/host/sys:ro -v /:/rootfs:ro "
            f"-p {NODE_EXPORTER_PORT}:{NODE_EXPORTER_PORT} "
            f"prom/node-exporter:v1.6.1 "
            f"--path.procfs=/host/proc --path.rootfs=/rootfs --path.sysfs=/host/sys "
            f"--collector.filesystem.mount-points-exclude=^/(sys|proc|dev|host|etc)($$|/)",
            raise_on_error=False,
        )
        logger.info("Deployed Node Exporter on %s", server.name)
        return True
    except Exception as exc:
        logger.error("Node Exporter deploy failed for %s: %s", server.name, exc)
        return False
    finally:
        with contextlib.suppress(Exception):
            client.close()


PROMTAIL_PORT = 9080


def deploy_promtail_on_node(server, force: bool = False):
    """SSH into a remote ManagedServer and deploy a Promtail log collector container.

    The Promtail pushes container logs to the primary (VPS) Loki instance.
    """
    # Determine the Loki URL for the remote node.
    # Set SMSLY_LOKI_PUBLIC_URL in .env to override (e.g. WireGuard IP or public IP).
    import os as _os

    from apps.deployments.models.core import ManagedServer
    from apps.deployments.services.ssh_client import SSHClient
    loki_ip = (_os.environ.get("SMSLY_LOKI_PUBLIC_URL") or "").strip()
    if not loki_ip:
        primary = ManagedServer.objects.filter(is_primary=True).first()
        if not primary:
            logger.error("No primary server found — cannot deploy remote Promtail")
            return False
        loki_ip = (primary.wg_address or primary.private_ip or primary.host or "").strip()
    if not loki_ip:
        logger.error("Primary server has no reachable IP — cannot deploy remote Promtail")
        return False
    loki_url = f"http://{loki_ip}:3100/loki/api/v1/push"
    logger.info("Remote Promtail Loki URL: %s", loki_url)

    client = SSHClient(
        ip=server.host,
        key_content=server.ssh_key,
        user=server.ssh_user,
        port=server.ssh_port,
        password=server.ssh_password,
        wg_address=server.wg_address,
    )

    try:
        client.connect()
    except Exception as exc:
        logger.error("SSH connection failed for %s: %s", server.name, exc)
        return False

    tmp = None
    try:
        # 0. Generate and upload the latest Promtail config
        config = _generate_remote_promtail_config(loki_url)
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False)
        tmp.write(config)
        tmp.close()

        remote_dir = "/opt/smsly-hosting"
        remote_config = f"{remote_dir}/promtail-config.yml"
        client.exec_command(f"mkdir -p {remote_dir}")
        client.upload_file(tmp.name, remote_config)

        # 1. Check if container exists — if so, reload or recreate
        out, _err, _code = client.exec_command(
            "docker inspect smsly-promtail --format='{{.State.Status}}' 2>/dev/null",
            raise_on_error=False,
        )
        if out.strip() == "running":
            if not force:
                # Hot-reload the updated config without disrupting the container
                client.exec_command(
                    "docker exec smsly-promtail kill -HUP 1 2>/dev/null || "
                    "docker restart smsly-promtail 2>/dev/null",
                    raise_on_error=False,
                )
                logger.info("Promtail config updated + reloaded on %s", server.name)
                return True
            # Force: remove and recreate from scratch
            client.exec_command("docker rm -f smsly-promtail 2>/dev/null")

        # 2. Container doesn't exist — create it
        client.exec_command("docker pull grafana/promtail:2.9.3 2>/dev/null", raise_on_error=False)

        cmd = (
            f"docker run -d --name smsly-promtail --restart unless-stopped "
            f"--security-opt no-new-privileges:true --security-opt apparmor=docker-default "
            f"-v /var/log:/var/log:ro "
            f"-v /var/lib/docker/containers:/var/lib/docker/containers:ro "
            f"-v /var/run/docker.sock:/var/run/docker.sock:ro "
            f"-v {remote_config}:/etc/promtail/config.yml:ro "
            f"grafana/promtail:2.9.3 "
            f"-config.file=/etc/promtail/config.yml"
        )
        _out, err, exit_code = client.exec_command(cmd, raise_on_error=False)
        if exit_code != 0:
            error = err.strip()
            logger.error("Failed to start Promtail on %s: %s", server.name, error)
            return False

        logger.info("Deployed Promtail on %s (Loki: %s)", server.name, loki_url)
        return True

    except Exception as exc:
        logger.error("Promtail deploy failed for %s: %s", server.name, exc)
        return False
    finally:
        if tmp:
            with contextlib.suppress(Exception):
                os.unlink(tmp.name)
        with contextlib.suppress(Exception):
            client.close()


def _generate_remote_promtail_config(loki_url: str) -> str:
    """Generate a Promtail config for a remote node that pushes to the central Loki."""
    return f"""server:
  http_listen_port: {PROMTAIL_PORT}
  grpc_listen_port: 0

positions:
  filename: /tmp/positions.yaml

clients:
  - url: "{loki_url}"

scrape_configs:
  - job_name: docker
    docker_sd_configs:
      - host: unix:///var/run/docker.sock
        refresh_interval: 5s
    relabel_configs:
      - source_labels: ['__meta_docker_container_label_managed_by']
        regex: 'smsly-hosting'
        action: keep
      - source_labels: ['__meta_docker_container_label_com_docker_compose_service']
        target_label: 'compose_service'
      - source_labels: ['__meta_docker_container_label_smsly_blue_green_canonical_name']
        target_label: 'compose_service'
        regex: '(.+)'
      - source_labels: ['__meta_docker_container_label_com_docker_compose_project']
        target_label: 'compose_project'
      - source_labels: ['__meta_docker_container_name']
        regex: '/(.*)'
        target_label: 'container_name'
      - source_labels: ['__meta_docker_container_id']
        target_label: 'container'
    pipeline_stages:
      - json:
          expressions:
            output: log
            stream: stream
            timestamp: time
      - labels:
          stream:
      - timestamp:
          source: timestamp
          format: RFC3339Nano
      - output:
          source: output
"""


def _write_target_file(filename, targets):
    """Write a JSON target file to TARGETS_DIR."""
    filepath = os.path.join(TARGETS_DIR, filename)
    try:
        with open(filepath, "w") as f:
            json.dump(targets, f, indent=2)
        logger.debug("Wrote %d targets to %s", len(targets), filepath)
    except OSError as exc:
        logger.warning("Failed to write %s: %s", filepath, exc)


def _get_exporter_script_content():
    """Read the docker-labels-exporter.py script content."""
    candidates = [
        os.path.join(os.path.dirname(__file__), "../../../infrastructure/monitoring/docker-labels-exporter.py"),
        "/app/infrastructure/monitoring/docker-labels-exporter.py",
        "/platform-src/infrastructure/monitoring/docker-labels-exporter.py",
        "/opt/smsly-hosting/infrastructure/monitoring/docker-labels-exporter.py",
    ]
    for path in candidates:
        resolved = os.path.realpath(path)
        if os.path.exists(resolved):
            with open(resolved) as f:
                return f.read()
    raise FileNotFoundError("Could not find docker-labels-exporter.py")
