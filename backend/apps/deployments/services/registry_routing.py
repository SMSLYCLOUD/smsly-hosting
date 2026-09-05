"""Master-registry URL routing for multi-node deployments.

The platform registry is reachable on SEVERAL addresses:

  registry:5000        — Docker DNS, from containers on smsly-net (master)
  127.0.0.1:5000       — host loopback (master host tools)
  10.100.0.1:5000      — WireGuard mesh (remote nodes; preferred)
  <PUBLIC_IP>:5000      — public IP (remote nodes without WG; must be
                          firewalled to node IPs only)

An image tag qualified with one address cannot be pulled from another
(the registry host:port is part of the reference). This module maps an
image reference between the INTERNAL form (used for build/sign/push on
the master) and the NODE-ROUTABLE form (used when a remote node pulls).

Priority for the node-routable URL:
  1. PlatformConfig.master_registry_node_url (operator override)
  2. WIREGUARD_MASTER_MESH_IP / MASTER_MESH_IP env (mesh, e.g. 10.100.0.1:5000)
  3. MASTER_REGISTRY_PUBLIC_URL env / detected public IP (fallback)
  4. The URL unchanged (single-host install — nothing to rewrite)
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_INTERNAL_HOSTS = ("registry:5000", "127.0.0.1:5000", "localhost:5000")


def _env(name: str) -> str:
    import os
    return (os.environ.get(name) or "").strip()


def internal_registry_hosts() -> list[str]:
    """Hosts that only resolve ON the master (containers/loopback)."""
    return list(_INTERNAL_HOSTS)


def master_registry_node_url() -> str:
    """The registry address a REMOTE NODE should use, host:port form.

    Empty string when unknown — callers then leave the reference alone.
    """
    # 1. Operator override via PlatformConfig
    try:
        from apps.deployments.models.core import PlatformConfig
        override = (PlatformConfig.get_config_value("master_registry_node_url") or "").strip()
        if override:
            return override.split("://")[-1].rstrip("/")
    except Exception:
        pass

    # 2. WireGuard mesh (preferred — encrypted, firewalled to peers)
    mesh_ip = _env("WIREGUARD_MASTER_MESH_IP") or _env("MASTER_MESH_IP")
    if mesh_ip:
        return f"{mesh_ip}:5000"

    # 3. Public IP fallback
    public = _env("MASTER_REGISTRY_PUBLIC_URL")
    if public:
        return public.split("://")[-1].rstrip("/")
    public_ip = _env("MASTER_PUBLIC_IP")
    if public_ip:
        return f"{public_ip}:5000"

    return ""


def _split_ref(image_ref: str) -> tuple[str, str]:
    """Split an image reference into (registry_host_port, rest).

    'registry:5000/ns/name:tag' -> ('registry:5000', 'ns/name:tag')
    'ns/name:tag'               -> ('', 'ns/name:tag')
    """
    rest = image_ref
    host = ""
    parts = image_ref.split("/", 1)
    if len(parts) == 2 and (":" in parts[0] or "." in parts[0]):
        host, rest = parts
    return host, rest


def image_ref_for_node(image_ref: str) -> str:
    """Rewrite an INTERNAL master-registry reference for a remote node.

    Non-registry references (external registries, unqualified names)
    pass through unchanged.
    """
    host, rest = _split_ref(image_ref)
    if not host or host not in _INTERNAL_HOSTS:
        return image_ref
    node_url = master_registry_node_url()
    if not node_url:
        return image_ref
    return f"{node_url}/{rest}"


def image_ref_for_internal(image_ref: str) -> str:
    """Rewrite a NODE-routable master-registry reference back to the
    internal form (for master-side operations: cosign verify, retag, prune).

    Only rewrites when the host matches the current node-routable URL.
    """
    host, rest = _split_ref(image_ref)
    if not host:
        return image_ref
    node_url = master_registry_node_url()
    if node_url and host == node_url:
        return f"registry:5000/{rest}"
    return image_ref


def is_master_registry_ref(image_ref: str) -> bool:
    """True if the reference points at the platform's own registry
    (any of its addresses)."""
    host, _ = _split_ref(image_ref)
    return host in _INTERNAL_HOSTS or (bool(master_registry_node_url()) and host == master_registry_node_url())
