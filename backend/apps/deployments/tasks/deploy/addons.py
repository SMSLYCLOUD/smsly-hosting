from __future__ import annotations

import logging
import secrets
import shlex
import subprocess
from urllib.parse import unquote

import docker
import requests

from apps.deployments.models import Deployment, Service

logger = logging.getLogger(__name__)


def _ensure_addons_ready(service: Service, deployment: Deployment) -> None:
    from apps.addons.services.addon_provisioner import addon_provisioner
    from apps.deployments.models.addons import Addon

    addons = Addon.objects.filter(service=service, status='ACTIVE')
    for addon in addons:
        if not addon.connection_url:
            raise RuntimeError(
                f"Addon {addon.addon_type} ({addon.name}) is ACTIVE but has no "
                f"connection URL. Provisioning may have failed silently."
            )
        container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"
        cid, running = addon_provisioner._container_status(container_name)
        if not cid or not running:
            raise RuntimeError(
                f"Addon {addon.addon_type} ({addon.name}) container "
                f"{container_name} is not running (cid={cid}, running={running}). "
                f"The service cannot start without its addon."
            )
        probe_id = secrets.token_hex(4)
        try:
            from urllib.parse import urlparse as _urlparse
            parsed = _urlparse(addon.connection_url)
            hostname = parsed.hostname or container_name
            inspect_cmd = [
                'docker', 'inspect', '-f',
                '{{range .NetworkSettings.Networks}}{{range .Aliases}}{{.}} {{end}}{{end}}',
                container_name,
            ]
            logger.debug("[probe:%s] Running: %s", probe_id, shlex.join(inspect_cmd))
            result = subprocess.run(
                inspect_cmd,
                capture_output=True, text=True, timeout=5,
            )
            aliases = (result.stdout or '').split()
            if hostname not in aliases:
                repair_cmd = [
                    'docker', 'network', 'connect', '--alias', hostname,
                    addon_provisioner.network_name, container_name,
                ]
                logger.debug("[probe:%s] Running: %s", probe_id, shlex.join(repair_cmd))
                subprocess.run(
                    repair_cmd,
                    capture_output=True, check=False, timeout=5,
                )
                logger.warning(
                    "[probe:%s] Repaired missing network alias %s for addon %s",
                    probe_id, hostname, container_name,
                )
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning(
                "[probe:%s] Addon network alias check failed for %s: %s",
                probe_id, container_name, exc,
            )


def _probe_addon_connectivity(service, container_id: str) -> list[str]:
    from apps.deployments.models.addons import Addon
    from urllib.parse import urlparse as _urlparse

    errors = []
    addons = Addon.objects.filter(service=service, status='ACTIVE')
    if not addons.exists():
        return errors

    try:
        client = docker.from_env()
    except docker.errors.DockerException:
        logger.debug("Addon connectivity probe skipped: Docker client unavailable")
        return errors

    for addon in addons:
        if not addon.connection_url:
            continue

        parsed = _urlparse(addon.connection_url)
        hostname = unquote(parsed.hostname or '')
        port = parsed.port
        if not hostname or not port:
            continue

        try:
            test_cmd = (
                f"python3 -c \""
                f"import socket; s=socket.socket(); s.settimeout(5); "
                f"s.connect(('{hostname}', {port})); s.close(); print('OK')"
                f"\" 2>/dev/null || "
                f"python -c \""
                f"import socket; s=socket.socket(); s.settimeout(5); "
                f"s.connect(('{hostname}', {port})); s.close(); print('OK')"
                f"\" 2>/dev/null || "
                f"bash -c 'echo > /dev/tcp/{hostname}/{port}' 2>/dev/null && echo OK"
            )
            result = client.containers.get(container_id).exec_run(
                ["bash", "-c", test_cmd],
            )
            output = (result.output or b"").decode("utf-8", errors="replace").strip()
            if result.exit_code != 0 or "OK" not in output:
                try:
                    http_url = f"http://{hostname}:{port}/"
                    resp = requests.get(http_url, timeout=5, verify=False)
                    if resp.status_code < 500:
                        continue
                except requests.RequestException:
                    pass
                errors.append(
                    f"Addon {addon.addon_type} ({addon.name}): "
                    f"service container cannot reach {hostname}:{port} "
                    f"(exit={result.exit_code}, output={output[:200]})"
                )
        except (docker.errors.DockerException, requests.RequestException) as exc:
            errors.append(
                f"Addon {addon.addon_type} ({addon.name}): "
                f"connectivity probe failed: {exc}"
            )

    return errors
