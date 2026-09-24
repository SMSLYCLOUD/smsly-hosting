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
    def _ip_key(item):
        try:
            return tuple(int(part) for part in item[0].split("."))
        except ValueError:
            return (255, 255, 255, 255)

    entries.sort(key=lambda item: (_ip_key(item), item[1]))
    return domain, [{"name": fqdn, "ip": ip} for ip, fqdn in entries]


def build_mesh_hosts() -> tuple[str, int]:
    """Render the hosts-format zone file. Returns (content, record_count).

    Deterministic ordering (sorted by IP) keeps the write idempotent so
    unchanged content is skipped.
    """
    _, records = get_mesh_zone_records()

    lines = [
        "# Generated by apps.deployments.services.mesh_dns — do not edit.",
        "# Regenerated automatically when mesh peers change "
        "(sync_mesh_dns_task).",
    ]
    for record in records:
        lines.append(f"{record['ip']} {record['name']}")

    return "\n".join(lines) + "\n", len(records)


def build_corefile() -> str:
    """Render the CoreDNS Corefile for the mesh zone."""
    domain = mesh_dns_domain()
    return (
        f".:53 {{\n"
        f"    errors\n"
        f"    health :8080\n"
        f"    ready :8181\n"
        f"    hosts {COREDNS_CONTAINER_HOSTS_PATH} {domain} {{\n"
        f"        ttl 30\n"
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


def apply_mesh_dns() -> dict:
    """Regenerate and write the Corefile + mesh hosts zone.

    Idempotent: unchanged content is skipped (no write, no reload —
    the CoreDNS `hosts` plugin re-reads the file on change anyway).
    Never raises; returns {"ok", "message", "records"}.
    """
    try:
        os.makedirs(COREDNS_CONFIG_DIR, exist_ok=True)
        hosts_content, record_count = build_mesh_hosts()
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
