"""
Prometheus target file management.
Writes file_sd_configs JSON files for remote docker-labels exporters.
"""
import json
import logging
import os
import shlex
import tempfile

logger = logging.getLogger(__name__)

TARGETS_DIR = os.environ.get(
    "PROMETHEUS_TARGETS_DIR",
    "/opt/smsly-hosting/prometheus-targets",
)

DOCKER_LABELS_PORT = int(os.environ.get("DOCKER_LABELS_PORT", "9234"))


def write_docker_labels_targets():
    """Write Prometheus file_sd target files for all remote docker-labels exporters."""
    try:
        from apps.deployments.models_core import ManagedServer
    except ImportError:
        logger.warning("ManagedServer model not available — skipping target generation")
        return

    os.makedirs(TARGETS_DIR, exist_ok=True)

    # Local node target
    local_targets = [
        {
            "targets": [f"docker-labels:{DOCKER_LABELS_PORT}"],
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

    logger.info(
        "Wrote %d local + %d remote docker-labels targets",
        len(local_targets),
        len(remote_targets),
    )


def deploy_docker_labels_exporter_on_node(server):
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
        # 0. Check if exporter is already running
        stdin, stdout, stderr = client.exec_command(
            "docker inspect smsly-docker-labels --format='{{.State.Status}}' 2>/dev/null",
            raise_on_error=False,
        )
        existing_status = stdout.read().decode().strip()
        if existing_status == "running":
            logger.debug("docker-labels exporter already running on %s", server.name)
            return True

        # 1. Write exporter script to temp file and upload
        exporter_script = _get_exporter_script_content()
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False)
        tmp.write(exporter_script)
        tmp.close()

        remote_path = "/opt/smsly-hosting/docker-labels-exporter.py"
        client.exec_command(f"mkdir -p /opt/smsly-hosting")
        client.upload_file(tmp.name, remote_path)

        # 2. Remove any stale container and pull image
        client.exec_command("docker rm -f smsly-docker-labels 2>/dev/null")
        client.exec_command("docker pull python:3.12-alpine 2>/dev/null", raise_on_error=False)

        # 3. Run the exporter container
        cmd = (
            f"docker run -d --name smsly-docker-labels --restart unless-stopped "
            f"-v /var/run/docker.sock:/var/run/docker.sock:ro "
            f"-p {DOCKER_LABELS_PORT}:{DOCKER_LABELS_PORT} "
            f"-e NODE_NAME={shlex.quote(server.name)} "
            f"-v {remote_path}:/app/exporter.py:ro "
            f"python:3.12-alpine python3 -u /app/exporter.py"
        )
        stdin, stdout, stderr = client.exec_command(cmd, raise_on_error=False)
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            error = stderr.read().decode().strip()
            logger.error("Failed to start docker-labels on %s: %s", server.name, error)
            return False

        logger.info("Deployed docker-labels exporter on %s", server.name)
        return True

    except Exception as exc:
        logger.error("Deploy failed for %s: %s", server.name, exc)
        return False
    finally:
        if tmp:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
        try:
            client.close()
        except Exception:
            pass


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
            with open(resolved, "r") as f:
                return f.read()
    raise FileNotFoundError("Could not find docker-labels-exporter.py")
