"""CoreDNS mesh-zone management for the WireGuard mesh (mesh DNS).

The WireGuard mesh is addressed by raw mesh IPs (10.100.0.x) today —
every consumer (registry pulls, DATABASE_URL, promtail) hardcodes the
master's mesh IP or the peer IPs directly. This module gives the mesh
DNS names instead, served by CoreDNS on the master:

  master.mesh.internal     — the master peer (conventionally 10.100.0.1)
  registry.mesh.internal  — platform registry on the master (:5000)
  postgres.mesh.internal  — master Postgres (:5432)
  redis.mesh.internal     — master Redis (:6379)
  rabbitmq.mesh.internal  — master RabbitMQ (:5672)
  grid2.mesh.internal     — node #2's mesh address (node_number based)
  my-node.mesh.internal   — same node, by ManagedServer.name

The zone is a plain hosts-format file consumed by CoreDNS's `hosts`
plugin (auto-reloaded on change). Files are written into the
`coredns_config` named volume — the same single-writer volume pattern
the Caddyfile uses — which is mounted read-only into the coredns
container at /etc/coredns and writable at /coredns-config in the
backend/celery-deploy containers.

Failure behaviour: apply_mesh_dns() is idempotent and never raises on
zone changes; write errors are returned in the result dict so the beat
task can log them without crashing.

Multi-master prep: failover only needs to repoint these records at the
new master's mesh IP; consumers that switch to the names follow
automatically. Nothing existing is rewired in this change — the DNS
zone is purely additive.
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

# Writable mount of the `coredns_config` named volume (backend/celery-deploy).
COREDNS_CONFIG_DIR = os.environ.get("COREDNS_CONFIG_DIR", "/coredns-config")
# Paths INSIDE the coredns container (same volume, mounted read-only).
COREDNS_CONTAINER_HOSTS_PATH = "/etc/coredns/mesh.hosts"
COREDNS_CONTAINER_COREFILE_PATH = "/etc/coredns/Corefile"

DEFAULT_MESH_DNS_DOMAIN = "mesh.internal"

# Stable service aliases pointing at the master's mesh IP. DNS carries no
# port — callers append the fixed internal port (registry :5000,
# postgres :5432, redis :6379, rabbitmq :5672).
MASTER_SERVICE_ALIASES = ("registry", "postgres", "redis", "rabbitmq")

_LABEL_SANITIZE_RE = re.compile(r"[^a-z0-9-]+")
_EPHEMERAL_CONTAINER_RE = re.compile(
    r"(?:^(?:smsly-)?envoy-|-(?:green|promoting|old|promote-conflict)(?:-|$)|-(?:prev|envoy)$)",
    re.IGNORECASE,
)
_DNS_HOSTNAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
_HEX_SHORT_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_SERVICE_BRIDGE_PREFIXES = ("smsly-net", "paas-svc-")
_INFRA_PROXY_SERVICES = frozenset(
    {"traefik", "caddy", "coredns", "route-fallback", "socket-proxy", "cloudflared", "tunnel"}
)
_INFRA_COMPOSE_PROJECTS = frozenset({"smsly-hosting"})
_EPHEMERAL_ROLES = frozenset({"sidecar", "envoy-sidecar", "green", "promoting", "backup"})


def _ip_sort_key(ip: str) -> tuple[int, ...]:
    """Numeric sort key for IPv4 addresses."""
    try:
        return tuple(int(part) for part in str(ip).split("."))
    except ValueError:
        return (255, 255, 255, 255)


def _normalize_dns_host(raw: str) -> str:
    """Normalize a hostname candidate or return '' if invalid for hosts."""
    host = (raw or "").strip().rstrip(".").lower()
    if not host or len(host) > 253 or not _DNS_HOSTNAME_RE.match(host):
        return ""
    return host


def _is_service_bridge(net_name: str) -> bool:
    """True for project/service bridge networks served via CoreDNS."""
    return isinstance(net_name, str) and (
        net_name == "smsly-net" or net_name.startswith(_SERVICE_BRIDGE_PREFIXES)
    )


def mesh_dns_domain() -> str:
    """The zone served for mesh names.

    Priority: MESH_DNS_DOMAIN env → PlatformConfig override → default.
    """
    env_domain = (os.environ.get("MESH_DNS_DOMAIN") or "").strip().lower()
    if env_domain:
        return env_domain
    try:
        from apps.deployments.models.core import PlatformConfig

        override = (PlatformConfig.get_config_value("mesh_dns_domain") or "").strip().lower()
        if override:
            return override
    except Exception:
        pass
    return DEFAULT_MESH_DNS_DOMAIN


def _sanitize_label(value: str) -> str:
    """Make a string safe as a single DNS label (lowercase [a-z0-9-])."""
    label = _LABEL_SANITIZE_RE.sub("-", (value or "").strip().lower()).strip("-")
    return label or "node"


def _default_mesh():
    """The primary mesh — the platform's infra mesh (name='default')."""
    from apps.deployments.models.mesh import MeshNetwork

    mesh = MeshNetwork.objects.filter(name="default", is_active=True).first()
    if mesh is None:
        mesh = MeshNetwork.objects.filter(is_active=True).order_by("created_at").first()
    return mesh


def _master_mesh_ip(mesh) -> str:
    """The master's mesh IP: the local peer's wg_address, else env fallback."""
    local_peer = None
    if mesh is not None:
        local_peer = (
            mesh.peers.filter(is_active=True, is_local=True)
            .order_by("created_at")
            .first()
        )
    if local_peer is not None and local_peer.wg_address:
        return str(local_peer.wg_address)
    return (os.environ.get("MASTER_MESH_IP") or "").strip()


def _peer_labels(peer, domain: str) -> list[str]:
    """DNS labels (excluding the zone) for one peer, most-specific first."""
    labels: list[str] = []
    if peer.is_local:
        return ["master"]
    server = getattr(peer, "server", None)
    node_number = getattr(server, "node_number", None) if server else None
    if node_number:
        label = f"grid{node_number}"
        if label not in labels:
            labels.append(label)
    name = getattr(server, "name", "") if server else ""
    if name:
        label = _sanitize_label(name)
        if label not in labels:
            labels.append(label)
    if not labels:
        # Peer without a linked server row — fall back to a sanitized IP.
        label = _sanitize_label(str(peer.wg_address).replace(".", "-"))
        labels.append(label)
    return labels


def get_mesh_zone_records() -> tuple[str, list[dict]]:
    """Render the zone as structured records for API consumers.

    Returns (domain, [{"name": fqdn, "ip": address}, ...]) sorted
    deterministically (by IP, then name). Read-only: never writes files.
    """
    domain = mesh_dns_domain()
    mesh = _default_mesh()

    entries: list[tuple[str, str]] = []  # (ip, fqdn)

    master_ip = _master_mesh_ip(mesh)
    if master_ip:
        entries.append((master_ip, f"master.{domain}"))
        for alias in MASTER_SERVICE_ALIASES:
            entries.append((master_ip, f"{alias}.{domain}"))

    if mesh is not None:
        peers = mesh.peers.filter(is_active=True).select_related("server").order_by("created_at")
        for peer in peers:
            ip = str(peer.wg_address)
            if not ip:
                continue
            if not peer.is_local and ip == master_ip:
                continue  # duplicate of master IP without local flag
            for label in _peer_labels(peer, domain):
                fqdn = f"{label}.{domain}"
                if (ip, fqdn) not in entries:
                    entries.append((ip, fqdn))

    # Deterministic order: by IP octets, then name.
    entries.sort(key=lambda item: (_ip_sort_key(item[0]), item[1]))
    return domain, [{"name": fqdn, "ip": ip} for ip, fqdn in entries]


def get_live_container_dns_records(client=None) -> list[dict]:
    """Enumerate running Docker bridge containers for CoreDNS discovery.

    Maps each active service container name and service alias on ``smsly-net*``
    or ``paas-svc-*`` bridges (both ``<name>`` and ``<name>.default.internal``)
    to its live primary project-bridge IP. Skips ephemeral deployment
    candidates (``*-green-*``, ``*-promoting-*``, ``*-old-*``, sidecars) and
    suppresses un-scoped names that collide across separate tenant bridges.

    Best-effort and non-fatal: returns [] when Docker is unavailable.
    """
    if client is None:
        try:
            import docker

            try:
                client = docker.from_env(timeout=10)
            except TypeError:
                client = docker.from_env()
        except Exception as exc:
            logger.debug("mesh_dns: Docker unavailable for container DNS discovery: %s", exc)
            return []

    try:
        containers = client.containers.list()
    except Exception as exc:
        logger.debug("mesh_dns: container enumeration failed: %s", exc)
        return []

    # Map normalized hostname -> {(primary_net, primary_ip), ...} across containers.
    records_by_host: dict[str, set[tuple[str, str]]] = {}

    for container in containers:
        try:
            attrs = getattr(container, "attrs", None) or {}
            state = str(
                ((attrs.get("State") or {}).get("Status"))
                or getattr(container, "status", "")
                or ""
            ).strip().lower()
            if state and state != "running":
                continue

            cname = str(getattr(container, "name", None) or attrs.get("Name") or "").lstrip("/").strip()
            if not cname or _EPHEMERAL_CONTAINER_RE.search(cname):
                continue

            labels = ((attrs.get("Config") or {}).get("Labels")) or {}
            compose_proj = str(labels.get("com.docker.compose.project") or "").strip().lower()
            compose_svc = str(labels.get("com.docker.compose.service") or "").strip().lower()
            if (
                compose_proj in _INFRA_COMPOSE_PROJECTS
                or str(labels.get("smsly.sidecar") or "").strip().lower() == "true"
                or str(labels.get("smsly.role") or "").strip().lower() in _EPHEMERAL_ROLES
                or str(labels.get("smsly.ephemeral") or "").strip().lower() == "true"
                or compose_svc in _INFRA_PROXY_SERVICES
            ):
                continue

            networks = ((attrs.get("NetworkSettings") or {}).get("Networks")) or {}
            qualifying: list[tuple[str, dict, str]] = []
            for net_name, net_cfg in networks.items():
                if not _is_service_bridge(net_name):
                    continue
                ip = str((net_cfg or {}).get("IPAddress") or "").strip()
                if ip and not any(ch.isspace() for ch in ip):
                    qualifying.append((net_name, net_cfg or {}, ip))

            if not qualifying:
                continue

            # Prefer scoped project/service bridges over shared legacy `smsly-net`.
            # When a container attaches to multiple distinct scoped tenant bridges
            # (e.g. shared Postgres across projects), record each scoped bridge so
            # cross-bridge IP collisions are filtered out rather than emitting one
            # bridge's unreachable IP globally.
            scoped_qualifying = [item for item in qualifying if item[0] != "smsly-net"]
            active_attachments = scoped_qualifying if scoped_qualifying else qualifying

            full_id = str(attrs.get("Id") or getattr(container, "id", "") or "").lower()
            short_id = str(getattr(container, "short_id", None) or full_id[:12]).lower()
            norm_cname = _normalize_dns_host(cname)
            svc_label = _normalize_dns_host(str(labels.get("smsly.service_name") or ""))

            for net_name, net_cfg, bridge_ip in active_attachments:
                base_names: set[str] = set()
                if norm_cname:
                    base_names.add(norm_cname)
                if svc_label:
                    base_names.add(svc_label)
                if compose_svc and net_name != "smsly-net":
                    norm_compose = _normalize_dns_host(compose_svc)
                    if norm_compose:
                        base_names.add(norm_compose)

                for raw_alias in (net_cfg.get("Aliases") or []):
                    alias = _normalize_dns_host(str(raw_alias))
                    if not alias:
                        continue
                    if alias == short_id or (_HEX_SHORT_ID_RE.match(alias) and full_id.startswith(alias)):
                        continue
                    base_names.add(alias)

                for host in base_names:
                    records_by_host.setdefault(host, set()).add((net_name, bridge_ip))
                    if "." not in host:
                        records_by_host.setdefault(f"{host}.default.internal", set()).add(
                            (net_name, bridge_ip)
                        )
        except Exception as exc:  # noqa: BLE001 — skip malformed container attrs
            logger.debug("mesh_dns: skipping container during DNS discovery: %s", exc)
            continue

    entries: list[tuple[str, str]] = []
    for hostname, targets in records_by_host.items():
        distinct_nets = {net for net, _ in targets}
        distinct_ips = {ip for _, ip in targets}
        if len(distinct_nets) > 1 and len(distinct_ips) > 1:
            logger.debug(
                "mesh_dns: excluding ambiguous un-scoped record %r across bridges %s",
                hostname,
                sorted(distinct_nets),
            )
            continue
        for ip in sorted(distinct_ips, key=_ip_sort_key):
            entries.append((ip, hostname))

    entries.sort(key=lambda item: (_ip_sort_key(item[0]), item[1]))
    return [{"name": host, "ip": ip} for ip, host in entries]


def build_mesh_hosts(
    include_containers: bool = False,
    client=None,
) -> tuple[str, int]:
    """Render the hosts-format zone file. Returns (content, record_count).

    Keeps WireGuard mesh peers pure by default (``include_containers=False``)
    and optionally appends live Docker container discovery records.
    Deterministic ordering (sorted by IP, then name) keeps writes idempotent.
    """
    _, mesh_records = get_mesh_zone_records()
    combined: list[dict] = list(mesh_records)
    if include_containers:
        combined.extend(get_live_container_dns_records(client=client))

    deduped = sorted(
        {(str(r["ip"]), str(r["name"])) for r in combined if r.get("ip") and r.get("name")},
        key=lambda item: (_ip_sort_key(item[0]), item[1]),
    )

    lines = [
        "# Generated by apps.deployments.services.mesh_dns — do not edit.",
        "# Regenerated automatically when mesh peers or containers change "
        "(sync_mesh_dns_task / sync_dns_now).",
    ]
    for ip, name in deduped:
        lines.append(f"{ip} {name}")

    return "\n".join(lines) + "\n", len(deduped)


def build_corefile() -> str:
    """Render the CoreDNS Corefile for mesh peers and local container DNS."""
    return (
        f".:53 {{\n"
        f"    errors\n"
        f"    health :8080\n"
        f"    ready :8181\n"
        f"    hosts {COREDNS_CONTAINER_HOSTS_PATH} . {{\n"
        f"        ttl 5\n"
        f"        reload 5s\n"
        f"        fallthrough\n"
        f"    }}\n"
        f"    forward . /etc/resolv.conf\n"
        f"    cache 30\n"
        f"    loop\n"
        f"    reload\n"
        f"}}\n"
    )


def _write_atomic(path: str, content: str) -> None:
    """Write content to path atomically (tmp file + os.replace)."""
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, path)


def apply_mesh_dns(client=None) -> dict:
    """Regenerate and write the Corefile + mesh/container hosts zone.

    Idempotent: unchanged content is skipped (no write, no reload —
    the CoreDNS `hosts` plugin re-reads the file on change anyway).
    Never raises; returns {"ok", "message", "records"}.
    """
    try:
        os.makedirs(COREDNS_CONFIG_DIR, exist_ok=True)
        hosts_content, record_count = build_mesh_hosts(
            include_containers=True,
            client=client,
        )
        corefile_content = build_corefile()

        written: list[str] = []
        hosts_path = os.path.join(COREDNS_CONFIG_DIR, "mesh.hosts")
        corefile_path = os.path.join(COREDNS_CONFIG_DIR, "Corefile")

        def _current(path: str) -> str:
            try:
                with open(path, encoding="utf-8") as handle:
                    return handle.read()
            except OSError:
                return ""

        if _current(hosts_path) != hosts_content:
            _write_atomic(hosts_path, hosts_content)
            written.append("mesh.hosts")
        if _current(corefile_path) != corefile_content:
            _write_atomic(corefile_path, corefile_content)
            written.append("Corefile")

        if not written:
            return {
                "ok": True,
                "message": f"mesh zone unchanged ({record_count} records)",
                "records": record_count,
            }
        return {
            "ok": True,
            "message": f"wrote {' + '.join(written)} ({record_count} records)",
            "records": record_count,
        }
    except Exception as exc:  # noqa: BLE001 — never crash callers
        logger.warning("mesh_dns: failed to apply mesh DNS zone: %s", exc)
        return {"ok": False, "message": str(exc), "records": 0}


def _coredns_dir_writable(config_dir: str) -> bool:
    """True when COREDNS_CONFIG_DIR exists or can be created and written."""
    if os.name == "nt" and config_dir == "/coredns-config" and not os.path.exists(config_dir):
        return False
    try:
        os.makedirs(config_dir, exist_ok=True)
        return os.access(config_dir, os.W_OK)
    except OSError:
        return False


def sync_dns_now(client=None) -> dict:
    """Immediately sync CoreDNS hosts, coalescing concurrent callers.

    Serialized via cache lock with a trailing dirty marker so concurrent
    deploys or swaps never drop the newest container IP. Falls back to
    ``queue_mesh_dns_sync()`` when ``COREDNS_CONFIG_DIR`` is not writable or
    writing fails. Never raises.
    """
    from apps.deployments.tasks.infra.tasks_mesh_dns import (
        MESH_DNS_LOCK_KEY,
        queue_mesh_dns_sync,
    )

    if not _coredns_dir_writable(COREDNS_CONFIG_DIR):
        queue_mesh_dns_sync()
        return {"ok": True, "queued": True, "message": "queued mesh DNS sync"}

    dirty_key = "mesh-dns-sync-dirty"
    try:
        from django.core.cache import cache

        try:
            cache.set(dirty_key, "1", timeout=60)
        except Exception:
            pass
        if not cache.add(MESH_DNS_LOCK_KEY, "1", timeout=30):
            return {"ok": True, "coalesced": True, "message": "coalesced into active sync"}
        try:
            result = {"ok": False, "message": "not run", "records": 0}
            for _ in range(3):
                try:
                    cache.delete(dirty_key)
                except Exception:
                    pass
                result = apply_mesh_dns(client=client)
                if not result.get("ok"):
                    queue_mesh_dns_sync()
                    return result
                try:
                    if not cache.get(dirty_key):
                        break
                except Exception:
                    break
            return result
        finally:
            try:
                cache.delete(MESH_DNS_LOCK_KEY)
            except Exception:
                pass
    except Exception:
        result = apply_mesh_dns(client=client)
        if not result.get("ok"):
            queue_mesh_dns_sync()
        return result
