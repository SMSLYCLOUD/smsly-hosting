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
import re
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

# Only the ecosystem trust domain is allowed for user services.
# platform.local belongs to platform-internal services (separate SPIRE
# server/trust bundle) — letting a user service claim it binds the
# wrong certificate chain (see get_service_trust_domain).
ALLOWED_ECOSYSTEM_TRUST_DOMAINS = {ECOSYSTEM_SPIFFE_TRUST_DOMAIN}


def resolve_spire_volume_name(short_name: str) -> str:
    """Resolve a SPIRE named volume to the real Docker volume name.

    Compose stacks prefix volumes with the project name (e.g.
    ``smsly-hosting_spire-ecosystem-agent-socket``), so the bare short
    name usually does not exist — mounting it would make Docker create
    an EMPTY volume that shadows the real socket directory. Prefer the
    exact name, else the unique ``*_<short>`` match, else the short
    name unchanged (caller decides whether to mount or skip).
    """
    try:
        from apps.cloud.docker_client import get_docker_client
        names = [v.name for v in get_docker_client().volumes.list()]
    except Exception:
        return short_name
    if short_name in names:
        return short_name
    suffix = '_' + short_name
    matches = sorted(v for v in names if v.endswith(suffix))
    if matches:
        return matches[0]
    return short_name


def is_mtls_enabled(service) -> bool:
    """Check if mTLS is enabled for a service."""
    # Check PlatformConfig DB toggle first
    try:
        from apps.deployments.models.platform import PlatformConfig
        pc = PlatformConfig.load()
        if not pc.mtls_ecosystem_enabled:
            return False
    except Exception:
        pass

    # Fall back to env var
    platform_enabled = os.getenv("MTLS_ENABLED", "true").lower() in ("true", "1", "yes")
    if not platform_enabled:
        return False

    try:
        return service.mtls_config.enabled
    except Exception:
        pass

    return True


def get_service_trust_domain(service) -> str:
    """Get the trust domain for a specific service.

    User-deployed services always get ecosystem.local.
    Rejects any attempt to use platform.local or other trust domains.
    """
    try:
        td = service.mtls_config.trust_domain
        if td not in ALLOWED_ECOSYSTEM_TRUST_DOMAINS:
            logger.error(
                "Service %s has disallowed trust_domain=%r, forcing ecosystem.local",
                service.name, td,
            )
            return ECOSYSTEM_SPIFFE_TRUST_DOMAIN
        return td
    except Exception:
        pass

    return ECOSYSTEM_SPIFFE_TRUST_DOMAIN


def get_mtls_labels(service) -> dict:
    """Get Docker labels for SPIRE workload attestation."""
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
    Always uses ecosystem volumes (user services only).
    """
    return [
        (ECOSYSTEM_SPIRE_SOCKET_HOST_PATH, SPIRE_SOCKET_CONTAINER_PATH, "ro"),
        (ECOSYSTEM_SPIRE_SVIDS_HOST_PATH, SPIRE_SVIDS_CONTAINER_PATH, "ro"),
    ]


def get_mtls_docker_run_args(service) -> str:
    """Get Docker CLI args for SPIRE mounts (used in spawn() via SSH)."""
    if not is_mtls_enabled(service):
        return ""

    args = (
        f"-v {ECOSYSTEM_SPIRE_SOCKET_HOST_PATH}:{SPIRE_SOCKET_CONTAINER_PATH}:ro "
        f"-v {ECOSYSTEM_SPIRE_SVIDS_HOST_PATH}:{SPIRE_SVIDS_CONTAINER_PATH}:ro "
    )
    return args


def get_mtls_docker_run_volumes(service) -> dict:
    """Get Docker SDK volume dict (used in spawn_local())."""
    if not is_mtls_enabled(service):
        return {}

    return {
        ECOSYSTEM_SPIRE_SOCKET_HOST_PATH: {"bind": SPIRE_SOCKET_CONTAINER_PATH, "mode": "ro"},
        ECOSYSTEM_SPIRE_SVIDS_HOST_PATH: {"bind": SPIRE_SVIDS_CONTAINER_PATH, "mode": "ro"},
    }


def _safe_service_name(name: str) -> str:
    """Sanitize service name for use as Docker label value."""
    return re.sub(r'^[.-]+|[^a-zA-Z0-9_.-]', '', name)[:100]
