"""
mTLS Integration for Spawning Service
======================================
Adds SPIRE socket mounts, Docker labels, and SPIFFE env vars to
containers spawned by the platform. Generic — works with any tenant app.

Usage:
    from .mtls_integration import get_mtls_labels, get_mtls_env_vars, get_mtls_volumes

    # In spawn() or spawn_local():
    labels.update(get_mtls_labels(service))
    env_vars.update(get_mtls_env_vars(service))
    # Add volume mounts for SPIRE socket and SVIDs
"""

import os
import logging

logger = logging.getLogger(__name__)

# SPIRE socket and SVID paths (defaults match docker-compose.spire.yml)
SPIRE_SOCKET_HOST_PATH = os.getenv("SPIRE_SOCKET_HOST_PATH", "spire-agent-socket")
SPIRE_SVIDS_HOST_PATH = os.getenv("SPIRE_SVIDS_HOST_PATH", "spire-agent-svids")
SPIRE_SOCKET_CONTAINER_PATH = "/opt/spire/run"
SPIRE_SVIDS_CONTAINER_PATH = "/opt/spire/svids"
SPIRE_TRUST_DOMAIN = os.getenv("SPIFFE_TRUST_DOMAIN", "platform.local")


def is_mtls_enabled(service) -> bool:
    """Check if mTLS is enabled for a service.

    Checks:
    1. Platform-wide MTLS_ENABLED env var (default: true)
    2. Per-service mtls_enabled attribute (if model has it)
    """
    platform_enabled = os.getenv("MTLS_ENABLED", "true").lower() in ("true", "1", "yes")
    if not platform_enabled:
        return False

    # Check per-service toggle (if the model has mtls_config)
    try:
        if hasattr(service, 'mtls_config'):
            return service.mtls_config.enabled
    except Exception:
        pass

    return True


def get_mtls_labels(service) -> dict:
    """Get Docker labels for SPIRE workload attestation.

    The label `com.paas.service=<name>` tells the SPIRE agent which
    SPIFFE ID to issue to this container.
    """
    if not is_mtls_enabled(service):
        return {}

    service_name = _safe_service_name(service.name)
    return {
        "com.paas.service": service_name,
        "com.paas.mtls": "true",
        "com.paas.spiffe_id": f"spiffe://{SPIRE_TRUST_DOMAIN}/service/{service_name}",
    }


def get_mtls_env_vars(service) -> dict:
    """Get SPIFFE environment variables for a service."""
    if not is_mtls_enabled(service):
        return {}

    return {
        "SPIFFE_ENDPOINT_SOCKET": f"unix://{SPIRE_SOCKET_CONTAINER_PATH}/agent.sock",
        "SPIFFE_TRUST_DOMAIN": SPIRE_TRUST_DOMAIN,
        "SPIFFE_SVID_CERT_PATH": f"{SPIRE_SVIDS_CONTAINER_PATH}/cert.pem",
        "SPIFFE_SVID_KEY_PATH": f"{SPIRE_SVIDS_CONTAINER_PATH}/key.pem",
        "SPIFFE_BUNDLE_PATH": f"{SPIRE_SVIDS_CONTAINER_PATH}/bundle.pem",
        "MTLS_ENABLED": "true",
    }


def get_mtls_volumes() -> list:
    """Get volume mounts for SPIRE socket and SVIDs.

    Returns list of (host_volume, container_path, mode) tuples.
    """
    return [
        (SPIRE_SOCKET_HOST_PATH, SPIRE_SOCKET_CONTAINER_PATH, "ro"),
        (SPIRE_SVIDS_HOST_PATH, SPIRE_SVIDS_CONTAINER_PATH, "ro"),
    ]


def get_mtls_docker_run_args(service) -> str:
    """Get Docker CLI args for SPIRE mounts (used in spawn() via SSH)."""
    if not is_mtls_enabled(service):
        return ""

    # SPIRE socket is a Unix Domain Socket mounted as a volume.
    # No network attachment needed — the socket is accessible from any Docker network.
    args = (
        f"-v {SPIRE_SOCKET_HOST_PATH}:{SPIRE_SOCKET_CONTAINER_PATH}:ro "
        f"-v {SPIRE_SVIDS_HOST_PATH}:{SPIRE_SVIDS_CONTAINER_PATH}:ro "
    )
    return args


def get_mtls_docker_run_volumes(service) -> dict:
    """Get Docker SDK volume dict (used in spawn_local())."""
    if not is_mtls_enabled(service):
        return {}

    return {
        SPIRE_SOCKET_HOST_PATH: {"bind": SPIRE_SOCKET_CONTAINER_PATH, "mode": "ro"},
        SPIRE_SVIDS_HOST_PATH: {"bind": SPIRE_SVIDS_CONTAINER_PATH, "mode": "ro"},
    }


def _safe_service_name(name: str) -> str:
    """Sanitize service name for use as Docker label value."""
    import re
    return re.sub(r'[^a-zA-Z0-9_.-]', '', name)[:100]
