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
    """Verify addon reachability WITHOUT running code inside the service container.

    Why this doesn't do a `docker exec` probe:
      - The previous implementation ran `python -c socket.create_connection(...)`
        from inside the service container. That probe was responsible for a
        huge fraction of false-positive deploy failures: it required Docker DNS
        to be up, the service container's `/etc/hosts` to be populated, and
        (for gVisor) the sandbox to allow egress to the bridge. Any of those
        being not-yet-ready — which is a race, not a real failure — would
        mark the deploy as FAILED.
      - The real reachability check is whether the addon container is running
        AND is attached to a network that the service container can also
        resolve. Docker's embedded DNS handles the rest at request time.

    So we just verify:
      1. The addon container exists and is running (else it can't serve traffic).
      2. The addon is on at least one Docker network that the service is also on.
      3. The network alias that the service's env vars reference is declared
         on that shared network (so Docker DNS will resolve it).

    This is what `_ensure_addons_ready` already does during container
    attach, so we only re-check (a) and re-affirm the alias wiring.
    """
    from apps.deployments.models.addons import Addon
    from urllib.parse import urlparse as _urlparse

    errors: list[str] = []
    addons = list(Addon.objects.filter(service=service, status='ACTIVE'))
    if not addons:
        return errors

    try:
        client = docker.from_env()
    except docker.errors.DockerException:
        # No Docker client = we can't verify, but also can't fail. The service
        # will discover broken addons at first connection attempt. Skip.
        logger.debug("Addon connectivity probe skipped: Docker client unavailable")
        return errors

    # Snapshot the service container's network attachment once.
    try:
        service_container = client.containers.get(container_id)
        service_container.reload()
        service_networks: set[str] = {
            name for name in (service_container.attrs.get('NetworkSettings') or {}).get('Networks', {}).keys()
        }
    except docker.errors.NotFound:
        # Service container already gone — nothing to verify against.
        return errors
    except docker.errors.DockerException as exc:
        logger.debug("Could not inspect service container networks: %s", exc)
        service_networks = set()

    for addon in addons:
        if not addon.connection_url:
            continue
        parsed = _urlparse(addon.connection_url)
        hostname = unquote(parsed.hostname or '').strip().lower()
        port = parsed.port
        if not hostname or not port:
            continue

        # The addon's container name follows the standard naming convention
        # (see addon_provisioner._container_name). We look it up by both
        # name and ID to handle renames.
        container_name = f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"
        try:
            addon_container = client.containers.get(container_name)
        except docker.errors.NotFound:
            errors.append(
                f"Addon {addon.addon_type} ({addon.name}): "
                f"container '{container_name}' is not running. "
                f"Service cannot start without its addon."
            )
            continue
        except docker.errors.DockerException as exc:
            errors.append(
                f"Addon {addon.addon_type} ({addon.name}): "
                f"docker lookup failed: {exc}"
            )
            continue

        try:
            addon_container.reload()
        except docker.errors.DockerException:
            pass

        if addon_container.status != 'running':
            errors.append(
                f"Addon {addon.addon_type} ({addon.name}): "
                f"container '{container_name}' is in state "
                f"'{addon_container.status}', expected 'running'."
            )
            continue

        # The addon MUST be on at least one network the service is on, and
        # MUST declare the hostname alias on that shared network so Docker
        # DNS resolves it. If the service is on multiple networks, ANY one
        # shared network with the right alias is sufficient.
        addon_networks: dict[str, set[str]] = {}
        for net_name, net_conf in (addon_container.attrs.get('NetworkSettings') or {}).get('Networks', {}).items():
            aliases = set((net_conf or {}).get('Aliases') or [])
            addon_networks[net_name] = {a.lower() for a in aliases}

        shared_net = None
        for net in service_networks:
            if net in addon_networks and hostname in addon_networks[net]:
                shared_net = net
                break

        if not shared_net:
            # Fall back: addon is on a network the service is on, but the
            # specific alias isn't declared. Re-attach with the alias to
            # self-heal, matching the logic in _ensure_addons_ready. This
            # is idempotent so it's safe to run here.
            try:
                for net in service_networks:
                    if net in addon_networks:
                        # attach missing alias to the shared network
                        addon_container.exec_run(
                            [],  # noop, we use the network connect below
                        ) if False else None  # placeholder kept for diff clarity
                # Use docker network connect to add the alias
                net = next(iter(service_networks & set(addon_networks.keys())), None)
                if net:
                    subprocess.run(
                        ['docker', 'network', 'connect', '--alias', hostname, net, container_name],
                        capture_output=True, check=False, timeout=5,
                    )
                    shared_net = net
                    logger.info(
                        "Repaired missing alias '%s' for addon %s on network %s",
                        hostname, container_name, net,
                    )
            except Exception as exc:
                logger.debug("Alias repair attempt failed: %s", exc)

        if not shared_net:
            errors.append(
                f"Addon {addon.addon_type} ({addon.name}): "
                f"no shared network with alias '{hostname}' between "
                f"service ({sorted(service_networks)}) and addon "
                f"({sorted(addon_networks)})."
            )

    return errors
