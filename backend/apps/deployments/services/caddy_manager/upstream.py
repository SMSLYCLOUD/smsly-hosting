import logging
import os

from .utils import _normalize_upstream_ip

logger = logging.getLogger(__name__)

SERVICE_PROXY_UPSTREAM = os.environ.get("SMSLY_SERVICE_PROXY_UPSTREAM", "traefik:80")


def _remote_server_mesh_ip(server) -> str:
    if not server or getattr(server, "is_primary", False):
        return ""

    try:
        peer = server.wg_peers.filter(mesh__name="default", is_active=True).first()
        if peer and peer.wg_address:
            return _normalize_upstream_ip(peer.wg_address)
    except Exception as exc:
        logger.debug("Failed to resolve WireGuard mesh IP for remote server: %s", exc)

    address = str(getattr(server, "wg_address", "") or "").strip()
    if address:
        return _normalize_upstream_ip(address)

    try:
        peer = server.wg_peers.filter(is_active=True).order_by("-updated_at").first()
        if peer and peer.wg_address:
            return _normalize_upstream_ip(peer.wg_address)
    except Exception:
        return ""

    return ""


def _remote_upstream_url_for_service(service) -> str:
    from apps.deployments.services.caddy_manager.config_generation import _resolve_effective_server
    server = _resolve_effective_server(service)
    if not server or getattr(server, "is_primary", False):
        return ""

    upstreams: list[str] = []

    def append(url: str):
        if url and url not in upstreams:
            upstreams.append(url)

    mesh_ip = _remote_server_mesh_ip(server)
    if mesh_ip:
        append(f"http://{mesh_ip}")

    host = str(server.host or "").strip()
    if host:
        append(f"http://{host}")

    return " ".join(upstreams)


def _service_proxy_upstream() -> str:
    return SERVICE_PROXY_UPSTREAM or "traefik:80"


def _local_upstream_for_service(service) -> str:
    """Direct container upstream for local/primary-server services.

    Returns ``{container_name}:{internal_port}`` so Caddy proxies directly
    to the service container on the shared Docker network, bypassing Traefik.
    Traefik's socket proxy doesn't forward container events, so user service
    containers are invisible to Traefik and routing through it returns 503.
    """
    name = str(getattr(service, "name", "") or "").strip()
    port = getattr(service, "internal_port", None) or 8000
    if name:
        return f"{name}:{port}"
    return _service_proxy_upstream()
