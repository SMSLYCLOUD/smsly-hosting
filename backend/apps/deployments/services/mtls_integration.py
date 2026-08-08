"""
mTLS Integration for Spawning Service
======================================
Adds SPIRE socket mounts, Docker labels, and SPIFFE env vars to
containers spawned by the platform. Generic — works with any tenant app.

Uses the ECOSYSTEM SPIRE server (separate trust domain) so user-deployed
services get their own certificate chain, isolated from platform services.

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

# --- Ecosystem SPIRE (for user-deployed services) ---
ECOSYSTEM_SPIRE_SOCKET_HOST_PATH = os.getenv(
    "ECOSYSTEM_SPIRE_SOCKET_HOST_PATH", "spire-ecosystem-agent-socket"
)
ECOSYSTEM_SPIRE_SVIDS_HOST_PATH = os.getenv(
    "ECOSYSTEM_SPIRE_SVIDS_HOST_PATH", "spire-ecosystem-agent-svids"
)
ECOSYSTEM_SPIFFE_TRUST_DOMAIN = os.getenv("ECOSYSTEM_TRUST_DOMAIN", "ecosystem.local")

# --- Platform SPIRE (for platform-internal services) ---
PLATFORM_SPIRE_SOCKET_HOST_PATH = os.getenv(
    "SPIRE_SOCKET_HOST_PATH", "spire-agent-socket"
)
PLATFORM_SPIRE_SVIDS_HOST_PATH = os.getenv(
    "SPIRE_SVIDS_HOST_PATH", "spire-agent-svids"
)
PLATFORM_SPIFFE_TRUST_DOMAIN = os.getenv("SPIFFE_TRUST_DOMAIN", "platform.local")

# Container paths are the same regardless of which SPIRE instance
SPIRE_SOCKET_CONTAINER_PATH = "/opt/spire/run"
SPIRE_SVIDS_CONTAINER_PATH = "/opt/spire/svids"


def is_mtls_enabled(service) -> bool:
    """Check if mTLS is enabled for a service.

    Checks:
    1. Platform-wide MTLS_ENABLED env var (default: true)
    2. Per-service mtls_enabled attribute (if model has it)
    """
    platform_enabled = os.getenv("MTLS_ENABLED", "true").lower() in ("true", "1", "yes")
    if not platform_enabled:
        return False

    try:
        if hasattr(service, 'mtls_config'):
            return service.mtls_config.enabled
    except Exception:
        pass

    return True


def get_service_trust_domain(service) -> str:
    """Get the trust domain for a specific service.

    User-deployed (ecosystem) services get ecosystem.local.
    Platform services get platform.local.
    """
    try:
        if hasattr(service, 'mtls_config') and service.mtls_config.trust_domain:
            return service.mtls_config.trust_domain
    except Exception:
        pass

    return ECOSYSTEM_SPIFFE_TRUST_DOMAIN


def is_ecosystem_service(service) -> bool:
    """Check if a service belongs to the ecosystem (user-deployed)."""
    return get_service_trust_domain(service) == ECOSYSTEM_SPIFFE_TRUST_DOMAIN


def get_mtls_labels(service) -> dict:
    """Get Docker labels for SPIRE workload attestation.

    The label `com.paas.service=<name>` tells the SPIRE agent which
    SPIFFE ID to issue to this container.
    """
    if not is_mtls_enabled(service):
        return {}

    trust_domain = get_service_trust_domain(service)
    service_name = _safe_service_name(service.name)
    return {
        "com.paas.service": service_name,
        "com.paas.mtls": "true",
        "com.paas.spiffe_id": f"spiffe://{trust_domain}/service/{service_name}",
    }


def get_mtls_env_vars(service) -> dict:
    """Get SPIFFE environment variables for a service."""
    if not is_mtls_enabled(service):
        return {}

    trust_domain = get_service_trust_domain(service)
    return {
        "SPIFFE_ENDPOINT_SOCKET": f"unix://{SPIRE_SOCKET_CONTAINER_PATH}/agent.sock",
        "SPIFFE_TRUST_DOMAIN": trust_domain,
        "SPIFFE_SVID_CERT_PATH": f"{SPIRE_SVIDS_CONTAINER_PATH}/cert.pem",
        "SPIFFE_SVID_KEY_PATH": f"{SPIRE_SVIDS_CONTAINER_PATH}/key.pem",
        "SPIFFE_BUNDLE_PATH": f"{SPIRE_SVIDS_CONTAINER_PATH}/bundle.pem",
        "MTLS_ENABLED": "true",
    }


def get_mtls_volumes(service=None) -> list:
    """Get volume mounts for SPIRE socket and SVIDs.

    Returns list of (host_volume, container_path, mode) tuples.
    Uses ecosystem volumes for user services, platform volumes for platform services.
    """
    if service and not is_ecosystem_service(service):
        socket_host = PLATFORM_SPIRE_SOCKET_HOST_PATH
        svids_host = PLATFORM_SPIRE_SVIDS_HOST_PATH
    else:
        socket_host = ECOSYSTEM_SPIRE_SOCKET_HOST_PATH
        svids_host = ECOSYSTEM_SPIRE_SVIDS_HOST_PATH

    return [
        (socket_host, SPIRE_SOCKET_CONTAINER_PATH, "ro"),
        (svids_host, SPIRE_SVIDS_CONTAINER_PATH, "ro"),
    ]


def get_mtls_docker_run_args(service) -> str:
    """Get Docker CLI args for SPIRE mounts (used in spawn() via SSH)."""
    if not is_mtls_enabled(service):
        return ""

    if is_ecosystem_service(service):
        socket_host = ECOSYSTEM_SPIRE_SOCKET_HOST_PATH
        svids_host = ECOSYSTEM_SPIRE_SVIDS_HOST_PATH
    else:
        socket_host = PLATFORM_SPIRE_SOCKET_HOST_PATH
        svids_host = PLATFORM_SPIRE_SVIDS_HOST_PATH

    args = (
        f"-v {socket_host}:{SPIRE_SOCKET_CONTAINER_PATH}:ro "
        f"-v {svids_host}:{SPIRE_SVIDS_CONTAINER_PATH}:ro "
    )
    return args


def get_mtls_docker_run_volumes(service) -> dict:
    """Get Docker SDK volume dict (used in spawn_local())."""
    if not is_mtls_enabled(service):
        return {}

    if is_ecosystem_service(service):
        socket_host = ECOSYSTEM_SPIRE_SOCKET_HOST_PATH
        svids_host = ECOSYSTEM_SPIRE_SVIDS_HOST_PATH
    else:
        socket_host = PLATFORM_SPIRE_SOCKET_HOST_PATH
        svids_host = PLATFORM_SPIRE_SVIDS_HOST_PATH

    return {
        socket_host: {"bind": SPIRE_SOCKET_CONTAINER_PATH, "mode": "ro"},
        svids_host: {"bind": SPIRE_SVIDS_CONTAINER_PATH, "mode": "ro"},
    }


def _safe_service_name(name: str) -> str:
    """Sanitize service name for use as Docker label value."""
    import re
    return re.sub(r'[^a-zA-Z0-9_.-]', '', name)[:100]
