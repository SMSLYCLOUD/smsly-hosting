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


def _best_node_endpoint(server, scheme: str, port: str) -> list[str]:
    """Return ordered upstream URL(s) for a single ManagedServer."""
    urls: list[str] = []
    mesh_ip = _remote_server_mesh_ip(server)
    if mesh_ip:
        urls.append(f"{scheme}://{mesh_ip}:{port}")
    host = str(getattr(server, "host", "") or "").strip()
    if host:
        url = f"{scheme}://{host}:{port}"
        if url not in urls:
            urls.append(url)
    return urls


def _remote_upstream_url_for_service(service) -> str:
    from apps.deployments.services.caddy_manager.config_generation import _resolve_effective_server
    server = _resolve_effective_server(service)
    if not server or getattr(server, "is_primary", False):
        return ""

    # HTTPS over the mesh with per-host SNI (callers emit
    # tls_server_name): plain-http upstreams let https-forcing apps
    # 301-loop forever (browser on https, proxy chain on http).
    # SMSLY_REMOTE_UPSTREAM_SCHEME=http reverts to legacy plaintext.
    scheme = os.environ.get("SMSLY_REMOTE_UPSTREAM_SCHEME", "https").strip().lower()
    if scheme != "https":
        scheme = "http"
    port = "443" if scheme == "https" else "80"

    # Collect primary server + any distinct online remote replica nodes so
    # Master Caddy load-balances across all nodes hosting active replicas.
    replica_nodes = []
    try:
        from apps.autoscaler.models.replica import ServiceReplica

        seen_ids = {str(server.id)}
        for rep in (
            ServiceReplica.objects.filter(
                service=service,
                status="RUNNING",
                node__isnull=False,
            )
            .select_related("node")
        ):
            rnode = getattr(rep, "node", None)
            if (
                rnode is not None
                and str(rnode.id) not in seen_ids
                and getattr(rnode, "status", "ONLINE") != "OFFLINE"
                and not getattr(rnode, "is_primary", False)
            ):
                seen_ids.add(str(rnode.id))
                replica_nodes.append(rnode)
    except Exception:
        pass

    upstreams: list[str] = []
    if not replica_nodes:
        upstreams.extend(_best_node_endpoint(server, scheme, port))
    else:
        for node_obj in [server, *replica_nodes]:
            candidates = _best_node_endpoint(node_obj, scheme, port)
            if candidates and candidates[0] not in upstreams:
                upstreams.append(candidates[0])

    return " ".join(upstreams)


def _service_proxy_upstream() -> str:
    return SERVICE_PROXY_UPSTREAM or "traefik:80"
