"""Mesh reachability for user-service addons.

Autoscaler replicas spawned on remote nodes ("vertical" spread) receive the
service's env verbatim — including DATABASE_URL / REDIS_URL hostnames that
only resolve inside the MASTER's docker network. On the remote node those
names are dead DNS and the replica crash-loops.

Fix: expose each addon through the WireGuard mesh via a per-addon socat
forwarder container on the master, bound STRICTLY to the master's mesh IP
(never 0.0.0.0), then rewrite the replica's env URLs to
``<master_mesh_ip>:<forward_port>``.

Forwarders are shared across all replicas of an addon (one per addon, keyed
in ``Addon.provider_metadata['mesh_forward_port']``) and are intentionally
left running on replica destroy — they cost ~nothing and are removed when
the addon itself is deprovisioned.
"""
import logging
import subprocess
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)

FORWARDER_IMAGE = "alpine/socat:latest"
PORT_RANGE_START = 21000
PORT_RANGE_END = 25000


def _sh(args, timeout=90):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _get_master_mesh_ip() -> str:
    try:
        from apps.deployments.services.provisioner.helpers import _get_master_mesh_ip as _g
        ip = _g()
        if ip:
            return ip
    except Exception:
        pass
    return "10.100.0.1"


def _addon_container_name(addon) -> str:
    return f"smsly-addon-{addon.addon_type.lower()}-{addon.id}"


def _forwarder_name(addon) -> str:
    return f"smsly-mesh-fwd-{str(addon.id)[:8]}"


def _alloc_port(addon) -> int:
    """Deterministic free-ish port in the mesh-forward range."""
    base = PORT_RANGE_START + (hash(str(addon.id)) % (PORT_RANGE_END - PORT_RANGE_START))
    return base


def ensure_addon_mesh_forward(addon) -> tuple[str, int] | None:
    """Ensure a mesh-bound forwarder exists for this addon.

    Returns (master_mesh_ip, forward_port) or None when the addon has no
    usable connection_url (nothing to expose).
    """
    url = str(getattr(addon, 'connection_url', '') or '')
    parsed = urlparse(url)
    if not parsed.hostname or not parsed.port:
        return None

    meta = getattr(addon, 'provider_metadata', None) or {}
    fwd_port = meta.get('mesh_forward_port')
    fwd_name = _forwarder_name(addon)

    # Already running?
    chk = _sh(['docker', 'inspect', '-f', '{{.State.Running}}', fwd_name])
    if chk.returncode == 0 and chk.stdout.strip() == 'true' and fwd_port:
        return _get_master_mesh_ip(), int(fwd_port)

    # (Re)create forwarder: listen on mesh IP only, dial addon by its
    # network alias inside smsly-net.
    target_host = parsed.hostname
    target_port = parsed.port
    if not fwd_port:
        fwd_port = _alloc_port(addon)

    _sh(['docker', 'rm', '-f', fwd_name])
    res = _sh([
        'docker', 'run', '-d',
        '--name', fwd_name,
        '--restart', 'unless-stopped',
        '--network', 'smsly-net',
        '-p', f"{_get_master_mesh_ip()}:{fwd_port}:{target_port}",
        FORWARDER_IMAGE,
        f"tcp-listen:{target_port},fork,reuseaddr",
        f"tcp-connect:{target_host}:{target_port}",
    ])
    if res.returncode != 0:
        logger.warning("mesh forwarder create failed for %s: %s",
                       fwd_name, res.stderr.strip()[:200])
        return None

    # Persist the port so restarts reuse it deterministically.
    try:
        meta['mesh_forward_port'] = fwd_port
        addon.provider_metadata = meta
        addon.save(update_fields=['provider_metadata', 'updated_at'])
    except Exception as exc:
        logger.debug("could not persist mesh_forward_port: %s", exc)

    return _get_master_mesh_ip(), int(fwd_port)


def rewrite_env_for_mesh(service, master_ip: str | None = None) -> dict[str, str]:
    """Return env overrides mapping addon-hosted URL vars to mesh endpoints.

    Only rewrites values whose hostname matches one of the service's ACTIVE
    addon aliases. Everything else passes through untouched.
    """
    overrides: dict[str, str] = {}
    addons = list(service.addons.exclude(status='DELETED')) if hasattr(service, 'addons') else []
    alias_map = {}
    for a in addons:
        h = urlparse(str(a.connection_url or '')).hostname
        if h:
            alias_map[h] = a
    if not alias_map:
        return overrides

    ip = master_ip or _get_master_mesh_ip()
    for ev in service.env_vars.all():
        val = ev.value or ''
        try:
            parsed = urlparse(val)
            host = parsed.hostname
        except Exception:
            continue
        if not host or host not in alias_map:
            continue
        fwd = ensure_addon_mesh_forward(alias_map[host])
        if not fwd:
            continue
        mip, port = fwd
        netloc = f"{mip}:{port}"
        if parsed.username:
            cred = parsed.username
            if parsed.password:
                cred += f":{parsed.password}"
            netloc = f"{cred}@{netloc}"
        overrides[ev.key] = urlunparse(parsed._replace(netloc=netloc))
    return overrides


def cleanup_addon_mesh_forward(addon) -> None:
    """Remove the forwarder when the addon goes away."""
    _sh(['docker', 'rm', '-f', _forwarder_name(addon)])
