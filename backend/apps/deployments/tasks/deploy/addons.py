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
    from apps.deployments.models.network_scope import ScopedNetwork as _Net

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

            # Also ensure addon is on the service's scoped network so the
            # service container (which may be on an isolated bridge) can
            # resolve addon hostnames via Docker DNS.
            project = getattr(service, 'project', None)
            if project:
                scoped_network = _Net.resolve_network_name(project)
                if scoped_network and scoped_network != addon_provisioner.network_name:
                    # Check the addon is connected to the scoped network
                    scoped_inspect = subprocess.run(
                        ['docker', 'inspect', '-f',
                         '{{range .NetworkSettings.Networks}}{{.NetworkID}} {{end}}',
                         container_name],
                        capture_output=True, text=True, timeout=5,
                    )
                    scoped_net_id = subprocess.run(
                        ['docker', 'network', 'inspect', '-f', '{{.Id}}', scoped_network],
                        capture_output=True, text=True, timeout=5,
                    )
                    if (scoped_net_id.returncode == 0 and
                            scoped_net_id.stdout.strip() not in scoped_inspect.stdout):
                        scoped_alias = addon.name or f"{addon.addon_type.lower()}-{service.name}"
                        scoped_connect = [
                            'docker', 'network', 'connect', '--alias', scoped_alias,
                            scoped_network, container_name,
                        ]
                        logger.debug("[probe:%s] Running: %s", probe_id, shlex.join(scoped_connect))
                        subprocess.run(
                            scoped_connect,
                            capture_output=True, check=False, timeout=5,
                        )
                        logger.info(
                            "[probe:%s] Connected addon %s to scoped network %s (alias: %s)",
                            probe_id, container_name, scoped_network, scoped_alias,
                        )

        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning(
                "[probe:%s] Addon network alias check failed for %s: %s",
                probe_id, container_name, exc,
            )


def _resolve_addon_ip(addon, client) -> str:
    """Resolve an addon container's IP address on its primary Docker network.

    Returns the IP string or empty string if resolution fails.
    This allows probes to work under gVisor where Docker DNS is unavailable.
    """
    container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"
    try:
        container = client.containers.get(container_name)
        container.reload()
        networks = (container.attrs.get('NetworkSettings') or {}).get('Networks') or {}
        for _net_data in networks.values():
            ip = _net_data.get('IPAddress', '')
            if ip:
                return ip
    except Exception:
        pass
    return ''


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

        # Resolve addon IP on the host side — needed for gVisor containers
        # where Docker DNS (127.0.0.11) is unreachable from inside the sandbox.
        addon_ip = _resolve_addon_ip(addon, client)
        # Prefer IP-based probe (works with both runc and gVisor)
        probe_host = addon_ip if addon_ip else hostname

        try:
            test_cmd = (
                "import socket,time; "
                f"host={probe_host!r}; port={port}; error=None; "
                "\nfor attempt in range(3):"
                "\n try:"
                "\n  s=socket.create_connection((host,port),5); s.close(); print('OK'); raise SystemExit(0)"
                "\n except OSError as exc:"
                "\n  error=exc; time.sleep(1)"
                "\nraise SystemExit(f'{type(error).__name__}: {error}')"
            )
            result = client.containers.get(container_id).exec_run(
                ["python", "-c", test_cmd],
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
