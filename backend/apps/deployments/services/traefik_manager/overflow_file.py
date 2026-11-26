"""Traefik file-provider writer for cross-node overflow load balancing.

When a Master-hosted service overflows horizontal replicas onto remote
``ManagedServer`` nodes, Master Caddy still proxies traffic to ``traefik:80``.
This module maintains ``/traefik-dynamic/overflow-<service_id>.yml`` so Master
Traefik balances requests across BOTH the local ``<live_svc>@docker`` pool
(primary + local replicas) and the active remote replica nodes over the
WireGuard mesh.

Priority sits at ``live_priority + 20``, keeping Canary WRR splits
(``live_priority + 100``) highest priority while still overriding the
single-node Docker router when remote replicas are active.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re

import yaml

from apps.deployments.services.traefik_manager.canary_file import (
    TRAEFIK_DYNAMIC_DIR,
    _container_labels,
    _has_child_healthcheck,
    _label,
    _router_service_from_labels,
    qualify_docker_middlewares,
    sanitize_router_name,
)

logger = logging.getLogger(__name__)

OVERFLOW_FILE_PREFIX = "overflow-"
OVERFLOW_PRIORITY_BUMP = 20


def overflow_file_path(service_or_id) -> str | None:
    if isinstance(service_or_id, str):
        raw_id = service_or_id.strip()
    else:
        raw_id = str(getattr(service_or_id, "id", "") or "").strip()
    if not raw_id:
        return None
    safe = re.sub(r"[^A-Za-z0-9-]", "-", raw_id) or "unknown"
    return os.path.join(TRAEFIK_DYNAMIC_DIR, f"{OVERFLOW_FILE_PREFIX}{safe}.yml")


def _is_mesh_or_private_ip(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(str(host or "").strip())
        return addr.is_private or addr in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        return False


def _node_endpoint(node) -> tuple[str, bool]:
    """Return ``(url, is_mesh_http)`` for a remote node."""
    mesh_ip = ""
    try:
        from apps.deployments.services.caddy_manager.upstream import _remote_server_mesh_ip
        mesh_ip = _remote_server_mesh_ip(node)
    except Exception:
        mesh_ip = str(getattr(node, "wg_address", "") or "").strip()

    if mesh_ip and _is_mesh_or_private_ip(mesh_ip):
        return f"http://{mesh_ip}:80", True
    host = str(getattr(node, "host", "") or "").strip()
    if host:
        return f"https://{host}:443", False
    if mesh_ip:
        return f"https://{mesh_ip}:443", False
    return "", False


def remove_overflow_file(service_or_id) -> bool:
    """Remove overflow dynamic config if present. Never raises."""
    try:
        path = overflow_file_path(service_or_id)
        if path and os.path.exists(path):
            os.remove(path)
            logger.info("Overflow Traefik file removed: %s", path)
            return True
    except Exception as exc:
        logger.debug("Overflow file removal skipped: %s", exc)
    return False


def build_overflow_config(
    topology: dict,
    local_weight: int,
    remote_targets: list[dict],
) -> dict:
    """Build Traefik WRR dynamic config balancing local Docker pool + remote nodes."""
    base = sanitize_router_name(topology.get("live_router", "svc"))
    router_name = f"{base}-overflow"
    wrr_name = f"{base}-overflow-wrr"
    mw_name = f"{base}-overflow-proto"

    middlewares = qualify_docker_middlewares(list(topology.get("middlewares") or []))
    if "secure-headers-strict@file" not in middlewares:
        middlewares.insert(0, "secure-headers-strict@file")

    weighted_services = [
        {"name": f"{topology['live_svc']}@docker", "weight": max(1, int(local_weight))},
    ]
    services_def: dict = {}
    middlewares_def: dict = {}
    has_mesh_http = any(t.get("mesh_http") for t in remote_targets)
    if has_mesh_http:
        middlewares_def[mw_name] = {
            "headers": {
                "customRequestHeaders": {
                    "X-Forwarded-Proto": "https",
                }
            }
        }
        if mw_name not in middlewares:
            middlewares.append(mw_name)

    for idx, target in enumerate(remote_targets):
        remote_svc_name = f"{base}-overflow-node-{idx}"
        lb: dict = {
            "servers": [{"url": target["url"]}],
            "passHostHeader": True,
        }
        services_def[remote_svc_name] = {"loadBalancer": lb}
        weighted_services.append({
            "name": f"{remote_svc_name}@file",
            "weight": max(1, int(target.get("weight", 1))),
        })

    weighted: dict = {"services": weighted_services}
    if topology.get("healthcheck_both") and len(remote_targets) == 0:
        weighted["healthCheck"] = {}

    services_def[wrr_name] = {"weighted": weighted}

    router_def: dict = {
        "rule": str(topology.get("live_rule", "") or "").strip(),
        "priority": int(topology.get("live_priority", 100)) + OVERFLOW_PRIORITY_BUMP,
        "entryPoints": list(topology.get("entrypoints") or ["web"]),
        "middlewares": middlewares,
        "service": wrr_name,
    }
    if topology.get("tls_resolver"):
        router_def["tls"] = {"certResolver": topology["tls_resolver"]}

    http_block: dict = {
        "routers": {router_name: router_def},
        "services": services_def,
    }
    if middlewares_def:
        http_block["middlewares"] = middlewares_def
    return {"http": http_block}


def sync_overflow_file(service) -> bool:
    """Create, update, or remove Master Traefik overflow WRR config for *service*."""
    svc_id = str(getattr(service, "id", "") or "").strip()
    svc_name = str(getattr(service, "name", "") or "").strip()
    if not svc_id or not svc_name:
        return False

    try:
        from apps.autoscaler.models.replica import ServiceReplica
        from apps.deployments.services.caddy_manager.config_generation import (
            _resolve_effective_server,
        )

        primary_svr = _resolve_effective_server(service)
        if primary_svr is not None and not getattr(primary_svr, "is_primary", True):
            remove_overflow_file(service)
            return False

        remote_replicas = list(
            ServiceReplica.objects.filter(
                service=service,
                status="RUNNING",
                node__isnull=False,
            ).select_related("node")
        )
        local_count = ServiceReplica.objects.filter(
            service=service,
            status="RUNNING",
            node__isnull=True,
        ).count()
    except Exception as exc:
        logger.debug("Overflow sync skipped (DB unavailable): %s", exc)
        return False

    if not remote_replicas:
        remove_overflow_file(service)
        return False

    node_buckets: dict[str, dict] = {}
    for rep in remote_replicas:
        node = getattr(rep, "node", None)
        if node is None or getattr(node, "status", "ONLINE") == "OFFLINE":
            continue
        url, is_mesh_http = _node_endpoint(node)
        if not url:
            continue
        bucket = node_buckets.setdefault(
            str(node.id),
            {"url": url, "mesh_http": is_mesh_http, "weight": 0},
        )
        bucket["weight"] += 1

    remote_targets = list(node_buckets.values())
    if not remote_targets:
        remove_overflow_file(service)
        return False

    try:
        import docker
        client = docker.from_env()
        live_container = client.containers.get(svc_name)
    except Exception as exc:
        logger.debug("Overflow sync skipped for %s (live container unavailable): %s", svc_name, exc)
        return False

    from apps.deployments.services.deletion_orchestrator import DeletionOrchestrator
    if DeletionOrchestrator._is_foreign_or_platform_resource(
        service, _container_labels(live_container), getattr(live_container, "name", svc_name),
    ):
        logger.warning("Overflow sync refused foreign/platform container %s for service %s", svc_name, svc_id)
        return False

    live_labels = _container_labels(live_container)
    public_domain = str(getattr(service, "public_domain", "") or "").strip().lower()
    found = _router_service_from_labels(live_labels, public_domain) or _router_service_from_labels(
        live_labels, "Host("
    )
    if not found:
        return False
    live_router, live_svc, _ = found
    try:
        live_priority = int(_label(live_labels, live_router, "priority", "100") or 100)
    except ValueError:
        live_priority = 100
    entrypoints = [
        e.strip() for e in _label(live_labels, live_router, "entrypoints", "web").split(",")
        if e.strip()
    ] or ["web"]
    middlewares = qualify_docker_middlewares([
        m.strip() for m in _label(live_labels, live_router, "middlewares", "").split(",")
        if m.strip()
    ])
    topology = {
        "live_router": live_router,
        "live_rule": _label(live_labels, live_router, "rule", ""),
        "live_priority": live_priority,
        "entrypoints": entrypoints,
        "middlewares": middlewares,
        "tls_resolver": _label(live_labels, live_router, "tls.certresolver", "").strip(),
        "live_svc": live_svc,
        "healthcheck_both": _has_child_healthcheck(live_labels, live_svc),
    }
    if not topology["live_rule"]:
        return False

    config = build_overflow_config(topology, 1 + local_count, remote_targets)
    path = overflow_file_path(service)
    if not path:
        return False
    try:
        from apps.deployments.services.traefik_manager.edge_shared_file import (
            ensure_edge_shared_file,
        )
        ensure_edge_shared_file()
    except Exception:
        pass
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(config, fh, default_flow_style=False, sort_keys=False)
    os.replace(tmp_path, path)
    logger.info(
        "Overflow Traefik file written for %s (local_w=%d, remote_nodes=%d)",
        svc_name, 1 + local_count, len(remote_targets),
    )
    return True
