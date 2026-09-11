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

# Both trust domains are valid. Ecosystem-managed services belong to
# the ecosystem domain; every other service belongs to the platform
# domain (separate SPIRE server/trust bundle).
ALLOWED_TRUST_DOMAINS = {ECOSYSTEM_SPIFFE_TRUST_DOMAIN, PLATFORM_SPIFFE_TRUST_DOMAIN}
# Back-compat alias: historical callers only knew the ecosystem domain.
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
    prefixed = sorted(
        v for v in names
        if v.endswith(short_name) and v.startswith("smsly-hosting_")
    )
    if prefixed:
        return prefixed[0]
    if short_name in names:
        return short_name
    suffix = '_' + short_name
    matches = sorted(v for v in names if v.endswith(suffix))
    if matches:
        return matches[0]
    return short_name


def is_mtls_enabled(service) -> bool:
    """Check if mTLS is enabled for a service."""
    # An explicitly enabled service is authoritative. Do not let a
    # stale platform-wide toggle suppress the labels/mounts needed by SPIRE
    # Docker selectors during its redeploy.
    try:
        enabled = getattr(service.mtls_config, "enabled", False)
        if not isinstance(enabled, bool):
            return False
        if enabled:
            return True
    except Exception:
        pass
    # Check PlatformConfig DB toggles (per trust-domain) first.
    try:
        from apps.deployments.models.platform import PlatformConfig
        pc = PlatformConfig.load()
        trust_domain = ""
        try:
            trust_domain = str(getattr(service.mtls_config, "trust_domain", "") or "")
        except Exception:
            pass
        if trust_domain == PLATFORM_SPIFFE_TRUST_DOMAIN:
            if not pc.mtls_enabled:
                return False
        elif not pc.mtls_ecosystem_enabled:
            return False
    except Exception:
        pass

    # Fall back to env var
    platform_enabled = os.getenv("MTLS_ENABLED", "true").lower() in ("true", "1", "yes")
    if not platform_enabled:
        return False

    try:
        enabled = getattr(service.mtls_config, "enabled", False)
        return enabled if isinstance(enabled, bool) else False
    except Exception:
        pass

    return True


def get_service_trust_domain(service) -> str:
    """Get the trust domain for a specific service.

    Honors the service's configured domain when it is a known platform
    domain. Ecosystem-managed services default to ecosystem.local;
    every other service defaults to platform.local (each backed by its
    own SPIRE server/trust bundle).
    """
    try:
        td = str(service.mtls_config.trust_domain or "").strip()
        if td in ALLOWED_TRUST_DOMAINS:
            return td
        if td:
            logger.error(
                "Service %s has unknown trust_domain=%r, falling back by ownership",
                service.name, td,
            )
    except Exception:
        pass

    try:
        if str(getattr(service, "managed_by", "") or "").upper() == "ECOSYSTEM":
            return ECOSYSTEM_SPIFFE_TRUST_DOMAIN
    except Exception:
        pass
    return PLATFORM_SPIFFE_TRUST_DOMAIN


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


def get_ecosystem_mtls_labels(service) -> dict:
    """Return mandatory ecosystem labels for SPIRE Docker attestation."""
    service_name = _safe_service_name(service.name)
    return {
        "com.paas.service": service_name,
        "com.paas.mtls": "true",
        "com.paas.spiffe_id": f"spiffe://{ECOSYSTEM_SPIFFE_TRUST_DOMAIN}/service/{service_name}",
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


def _spire_volume_names(service) -> tuple[str, str]:
    """Return (socket_volume, svids_volume) for the service's trust domain."""
    if get_service_trust_domain(service) == PLATFORM_SPIFFE_TRUST_DOMAIN:
        return PLATFORM_SPIRE_SOCKET_HOST_PATH, PLATFORM_SPIRE_SVIDS_HOST_PATH
    return ECOSYSTEM_SPIRE_SOCKET_HOST_PATH, ECOSYSTEM_SPIRE_SVIDS_HOST_PATH


def get_mtls_volumes(service=None) -> list:
    """Get volume mounts for SPIRE socket and SVIDs.

    Returns list of (host_volume, container_path, mode) tuples for the
    service's own trust domain (platform or ecosystem agent volumes).
    """
    if service is None:
        return [
            (ECOSYSTEM_SPIRE_SOCKET_HOST_PATH, SPIRE_SOCKET_CONTAINER_PATH, "ro"),
            (ECOSYSTEM_SPIRE_SVIDS_HOST_PATH, SPIRE_SVIDS_CONTAINER_PATH, "ro"),
        ]
    socket_vol, svids_vol = _spire_volume_names(service)
    return [
        (socket_vol, SPIRE_SOCKET_CONTAINER_PATH, "ro"),
        (svids_vol, SPIRE_SVIDS_CONTAINER_PATH, "ro"),
    ]


def get_mtls_docker_run_args(service) -> str:
    """Get Docker CLI args for SPIRE mounts (used in spawn() via SSH)."""
    if not is_mtls_enabled(service):
        return ""

    socket_vol, svids_vol = _spire_volume_names(service)
    args = (
        f"-v {socket_vol}:{SPIRE_SOCKET_CONTAINER_PATH}:ro "
        f"-v {svids_vol}:{SPIRE_SVIDS_CONTAINER_PATH}:ro "
    )
    return args


def get_mtls_docker_run_volumes(service) -> dict:
    """Get Docker SDK volume dict (used in spawn_local())."""
    if not is_mtls_enabled(service):
        return {}

    socket_vol, svids_vol = _spire_volume_names(service)
    return {
        socket_vol: {"bind": SPIRE_SOCKET_CONTAINER_PATH, "mode": "ro"},
        svids_vol: {"bind": SPIRE_SVIDS_CONTAINER_PATH, "mode": "ro"},
    }


def merge_docker_volumes(*volume_maps: dict) -> dict:
    """Merge mounts by destination, preventing Docker duplicate bind errors."""
    merged = {}
    for volume_map in volume_maps:
        for source, config in (volume_map or {}).items():
            destination = str((config or {}).get("bind") or "").strip()
            if not destination:
                continue
            for old_source, old_config in list(merged.items()):
                if old_config.get("bind") == destination:
                    del merged[old_source]
            merged[source] = config
    return merged


def _safe_service_name(name: str) -> str:
    """Sanitize service name for use as Docker label value."""
    return re.sub(r'^[.-]+|[^a-zA-Z0-9_.-]', '', name)[:100]
