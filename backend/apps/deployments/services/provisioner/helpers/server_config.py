import logging
import os
import re
import secrets
import subprocess

from django.conf import settings

from apps.deployments.models.servers import ManagedServer

from .env import _url_password

logger = logging.getLogger(__name__)


def _node_queue_name(server: ManagedServer) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(server.id)).strip("-")
    return f"smsly-node-{slug or 'agent'}"


def server_install_mode(server: ManagedServer) -> str:
    if getattr(server, "is_lite_agent", False):
        return "agent-lite"
    if getattr(server, "node_type", None) == "media":
        return "media"
    if not bool(getattr(server, "is_primary", False)):
        return "node"
    return "master"


def server_connection_mode(server: ManagedServer) -> str:
    install_mode = server_install_mode(server)
    if install_mode == "agent-lite":
        return "agent-lite"
    if install_mode == "node":
        return "full-stack-node"
    return "full-install"


def _master_gateway_secret() -> str:
    return str(
        os.environ.get("GATEWAY_SECRET")
        or getattr(settings, "GATEWAY_SECRET", "")
        or ""
    ).strip()


def _get_master_mesh_ip() -> str:
    env_mesh = os.environ.get("MASTER_MESH_IP")
    if env_mesh:
        return env_mesh.strip()

    from apps.deployments.models.core import ManagedServer as ManagedServerCore
    primary = ManagedServerCore.get_primary()
    if not primary:
        return "10.100.0.1"

    try:
        peer = primary.wg_peers.filter(mesh__name="default", is_active=True).first()
        if peer and peer.wg_address:
            return str(peer.wg_address)
    except Exception as exc:
        logger.debug("Failed to resolve WireGuard address from default mesh: %s", exc)

    wg = str(getattr(primary, "wg_address", "") or "").strip()
    if wg:
        return wg
    try:
        peer = primary.wg_peers.filter(is_active=True).order_by("-updated_at").first()
        if peer and peer.wg_address:
            return str(peer.wg_address)
    except Exception as exc:
        logger.debug("Failed to resolve WireGuard address from peers: %s", exc)

    if primary.is_primary:
        return "10.100.0.1"
    return "10.100.0.1"


def _get_master_wg_pubkey() -> str:
    env_pubkey = os.environ.get("MASTER_WG_PUBKEY")
    if env_pubkey:
        return env_pubkey.strip()
    try:
        pub_key_path = "/etc/wireguard/public.key"
        if os.path.exists(pub_key_path):
            with open(pub_key_path) as f:
                return f.read().strip()
    except Exception as exc:
        logger.debug("Failed to read WireGuard public key from file: %s", exc)
    try:
        result = subprocess.run(
            ["cat", "/etc/wireguard/public.key"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception as exc:
        logger.debug("Failed to read WireGuard public key via subprocess: %s", exc)
    return ""


def build_agent_lite_install_env(
    server: ManagedServer,
    master_ip: str | None = None,
) -> tuple[dict[str, str], list[str]]:
    from .database import _provision_node_db_credentials

    messages: list[str] = []

    messages.append("Generating dedicated lite-node database credentials on the master.")
    node_user, node_pass = _provision_node_db_credentials(server)
    if node_user and node_pass:
        messages.append(f"Dedicated lite-node DB user ready: {node_user}.")
        master_db_user = node_user
        master_db_pass = node_pass
    else:
        raise RuntimeError(
            "Failed to create dedicated DB credentials for lite agent node. "
            "Cannot fall back to master DB credentials — this would grant the "
            "remote node unrestricted access to the master database. "
            "Check PostgreSQL connectivity and retry."
        )

    master_mq_pass = (
        os.environ.get("RABBITMQ_PASSWORD")
        or _url_password(os.environ.get("CELERY_BROKER_URL", ""))
        or ""
    )
    master_redis_pass = (
        os.environ.get("REDIS_PASSWORD")
        or _url_password(os.environ.get("REDIS_URL", ""))
        or ""
    )
    resolved_master_ip = (
        str(master_ip or "").strip()
        or os.environ.get("PUBLIC_IP", "").strip()
        or "127.0.0.1"
    )

    master_mesh_ip = _get_master_mesh_ip()
    if not master_mesh_ip:
        raise ValueError(
            "No WireGuard mesh IP found for master. "
            "Cannot provision a lite agent without a mesh VPN IP."
        )
    messages.append(f"Master mesh IP for internal services: {master_mesh_ip}")

    node_id = str(server.id)
    node_queue = _node_queue_name(server)
    agent_gateway_secret = server.gateway_secret or ""
    if not agent_gateway_secret:
        agent_gateway_secret = secrets.token_hex(32)
        server.gateway_secret = agent_gateway_secret
        server.save(update_fields=["gateway_secret"])
        logger.info("Generated unique GATEWAY_SECRET for agent %s", server.id)

    master_wg_pubkey = _get_master_wg_pubkey()

    return (
        {
            "MASTER_IP": resolved_master_ip,
            "MASTER_MESH_IP": master_mesh_ip,
            "MASTER_WG_PUBKEY": master_wg_pubkey,
            "MASTER_DB_USER": master_db_user,
            "MASTER_DB_PASSWORD": master_db_pass,
            "MASTER_MQ_PASSWORD": master_mq_pass,
            "MASTER_REDIS_PASSWORD": master_redis_pass,
            "MASTER_GATEWAY_SECRET": agent_gateway_secret,
            "MASTER_FIELD_ENCRYPTION_KEY": getattr(settings, "FIELD_ENCRYPTION_KEY", ""),
            "SMSLY_NODE_HOST": str(server.host or "").strip(),
            "SMSLY_NODE_ID": node_id,
            "SMSLY_NODE_QUEUE": node_queue,
        },
        messages,
    )
