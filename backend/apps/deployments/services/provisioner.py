"""
Server auto-provisioning service.

SSHes into a fresh VPS, uploads and runs install.sh,
then auto-registers the server with the API credentials.
"""

import contextlib
import hashlib
import hmac as hmac_mod
import io
import ipaddress
import json
import logging
import os
import re
import secrets
import shlex
import subprocess
import time
from datetime import timedelta
from urllib.parse import urlparse

import paramiko
import requests
from asgiref.sync import async_to_sync
from celery import shared_task
from celery.exceptions import SoftTimeLimitExceeded
from channels.layers import get_channel_layer
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.deployments.models_servers import ManagedServer
from apps.deployments.utils import (
    build_local_source_bundle as utils_build_bundle,
)
from apps.deployments.utils import (
    get_source_root_dir as utils_get_source_root,
)

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


PROVISION_TIMEOUT_SECONDS = _env_int(
    "SMSLY_PROVISION_TIMEOUT_SECONDS",
    1800,
    minimum=60,
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _installer_logs_confirm_success(logs: str) -> bool:
    """Return True only when installer output has a success marker and no failure marker."""
    text = logs or ""
    if "INSTALLATION FAILED" in text:
        return False
    return bool(
        "INSTALLATION SUCCESSFUL!" in text
        or re.search(r"All\s+\d+/\d+\s+verification checks passed", text)
    )


class _ProvisioningResources:
    """Tracks resources created during provisioning for rollback on failure.

    Usage:
        res = _ProvisioningResources(server)
        res.track_db_user(username)
        res.track_firewall_rule(ip, port)
        ...
        # On failure:
        res.rollback()

    Each resource type has a dedicated cleanup function that is idempotent
    (safe to call even if the resource was already partially cleaned up).
    """

    def __init__(self, server: ManagedServer):
        self.server = server
        self._db_users: list[str] = []
        self._firewall_rules: list[tuple[str, str]] = []  # (ip, port)
        self._ssh_key_added = False
        self._wg_peer_id: str | None = None

    def track_db_user(self, username: str):
        self._db_users.append(username)

    def track_firewall_rule(self, node_ip: str, port: str):
        self._firewall_rules.append((node_ip, port))

    def track_ssh_key_added(self):
        self._ssh_key_added = True

    def track_wg_peer(self, peer_id: str):
        self._wg_peer_id = peer_id

    def rollback(self):
        """Best-effort cleanup of all tracked resources. Never raises."""
        for username in self._db_users:
            self._drop_db_user(username)
        for node_ip, port in self._firewall_rules:
            self._remove_firewall_rule(node_ip, port)
        if self._ssh_key_added:
            self._remove_ssh_key()
        if self._wg_peer_id:
            self._remove_wg_peer()

    def _drop_db_user(self, username: str):
        master_db_url = os.environ.get("DATABASE_URL")
        if not master_db_url:
            return
        try:
            import psycopg2
            from psycopg2 import sql
            from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
            conn = psycopg2.connect(master_db_url)
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            with conn.cursor() as cur:
                cur.execute(sql.SQL("DROP OWNED BY {} CASCADE").format(sql.Identifier(username)))
                cur.execute(sql.SQL("DROP USER IF EXISTS {}").format(sql.Identifier(username)))
            conn.close()
            _append_log(self.server, f"🧹 Rolled back DB user: {username}")
        except Exception as exc:
            logger.warning("Rollback: failed to drop DB user %s: %s", username, exc)

    def _remove_firewall_rule(self, node_ip: str, port: str):
        try:
            subprocess.run(
                ["ufw", "delete", "allow", "from", node_ip,
                 "to", "any", "port", port, "proto", "tcp"],
                capture_output=True, timeout=5,
            )
            subprocess.run(
                ["iptables", "-D", "DOCKER-USER",
                 "-s", node_ip, "-p", "tcp", "--dport", "5000",
                 "-j", "ACCEPT"],
                capture_output=True, timeout=5,
            )
            _append_log(self.server, f"🧹 Rolled back firewall rule: {node_ip}:{port}")
        except Exception as exc:
            logger.warning("Rollback: failed to remove firewall rule %s:%s: %s", node_ip, port, exc)

    def _remove_ssh_key(self):
        if not self.server.ssh_key:
            return
        try:
            import io
            key_file = io.StringIO(self.server.ssh_key)
            try:
                pkey = paramiko.Ed25519Key.from_private_key(key_file)
            except Exception:
                key_file.seek(0)
                pkey = paramiko.RSAKey.from_private_key(key_file)
            # Connect with the generated key and remove it from authorized_keys
            client = paramiko.SSHClient()
            from apps.deployments.services.ssh_client import _get_tofu_policy
            client.set_missing_host_key_policy(_get_tofu_policy(self.server.host, self.server.ssh_port))
            connect_kwargs = {
                "hostname": self.server.host,
                "port": self.server.ssh_port,
                "username": self.server.ssh_user,
                "pkey": pkey,
                "timeout": 10,
            }
            if self.server.ssh_password:
                # Try key first, fall back to password
                try:
                    client.connect(**connect_kwargs)
                except paramiko.AuthenticationException:
                    connect_kwargs.pop("pkey")
                    connect_kwargs["password"] = self.server.ssh_password
                    client.connect(**connect_kwargs)
            else:
                client.connect(**connect_kwargs)
            client.exec_command('sed -i "/smsly-self-heal/d" ~/.ssh/authorized_keys')
            client.close()
            _append_log(self.server, "🧹 Rolled back SSH key from remote node")
        except Exception as exc:
            logger.warning("Rollback: failed to remove SSH key: %s", exc)

    def _remove_wg_peer(self):
        try:
            from apps.deployments.services.wireguard_service import WireGuardService
            WireGuardService.remove_peer_from_mesh(self._wg_peer_id)
            _append_log(self.server, f"🧹 Rolled back WireGuard peer: {self._wg_peer_id}")
        except Exception as exc:
            logger.warning("Rollback: failed to remove WG peer %s: %s", self._wg_peer_id, exc)


def _shell_env_assignments(values: dict[str, object]) -> str:
    """Render shell-safe KEY=value assignments for remote installer commands."""
    parts = []
    for key, value in values.items():
        if value is None:
            continue
        parts.append(f"{key}={shlex.quote(str(value))}")
    return " ".join(parts)


def _url_password(raw_url: str | None) -> str:
    if not raw_url:
        return ""
    try:
        return urlparse(raw_url).password or ""
    except Exception:
        return ""


def _url_username(raw_url: str | None) -> str:
    if not raw_url:
        return ""
    try:
        return urlparse(raw_url).username or ""
    except Exception:
        return ""


def _node_queue_name(server: ManagedServer) -> str:
    """Return the stable Celery queue consumed only by this lite node."""
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(server.id)).strip("-")
    return f"smsly-node-{slug or 'agent'}"


def server_install_mode(server: ManagedServer) -> str:
    """Return the installer topology for a managed server."""
    if getattr(server, "is_lite_agent", False):
        return "agent-lite"
    if not bool(getattr(server, "is_primary", False)):
        return "node"
    return "master"


def server_connection_mode(server: ManagedServer) -> str:
    """Return the provider metadata connection mode for a managed server."""
    install_mode = server_install_mode(server)
    if install_mode == "agent-lite":
        return "agent-lite"
    if install_mode == "node":
        return "full-stack-node"
    return "full-install"


def _master_gateway_secret() -> str:
    """Return the master's own GATEWAY_SECRET (used only as last resort fallback)."""
    return str(
        os.environ.get("GATEWAY_SECRET")
        or getattr(settings, "GATEWAY_SECRET", "")
        or ""
    ).strip()


def _get_master_mesh_ip() -> str:
    """Return the WireGuard mesh IP of the primary/master server.

    Lite agents must use the mesh IP for database, RabbitMQ, and Redis
    connections because the public IP is typically firewalled.
    """
    env_mesh = os.environ.get("MASTER_MESH_IP")
    if env_mesh:
        return env_mesh.strip()

    from apps.deployments.models_core import ManagedServer
    primary = ManagedServer.get_primary()
    if not primary:
        return "10.100.0.1"

    try:
        peer = primary.wg_peers.filter(mesh__name="default", is_active=True).first()
        if peer and peer.wg_address:
            return str(peer.wg_address)
    except Exception:
        pass

    wg = str(getattr(primary, "wg_address", "") or "").strip()
    if wg:
        return wg
    try:
        peer = primary.wg_peers.filter(is_active=True).order_by("-updated_at").first()
        if peer and peer.wg_address:
            return str(peer.wg_address)
    except Exception:
        pass

    if primary.is_primary:
        return "10.100.0.1"
    return "10.100.0.1"


def _get_master_wg_pubkey() -> str:
    """Return the WireGuard public key of the primary/master server.

    This key is injected into node install scripts so the node can add the
    master as a WireGuard peer immediately (before the async deploy_mesh_task
    runs), enabling the handshake to reach 10.100.0.1:5432 for migrations.
    """
    env_pubkey = os.environ.get("MASTER_WG_PUBKEY")
    if env_pubkey:
        return env_pubkey.strip()

    try:
        from apps.deployments.models_core import ManagedServer
        primary = ManagedServer.get_primary()
        if primary:
            peer = primary.wg_peers.filter(mesh__name="default", is_active=True).first()
            if peer and peer.public_key:
                return str(peer.public_key).strip()
    except Exception:
        pass

    # Fallback: read from the host's WireGuard public key file
    try:
        import subprocess
        result = subprocess.run(
            ["cat", "/etc/wireguard/public.key"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            key = result.stdout.strip()
            if key:
                return key
    except Exception:
        pass

    return ""



def build_agent_lite_install_env(
    server: ManagedServer,
    master_ip: str | None = None,
) -> tuple[dict[str, str], list[str]]:
    """
    Build the environment needed to install or update a lite agent.

    Lite agents share the master's database and HMAC secret, but run local
    RabbitMQ and Redis via docker-compose.agent-lite.yml. They also need a
    deterministic node queue so local agent workers consume local deploys.

    IMPORTANT: MASTER_IP is the public IP used for HTTP API calls, but
    MASTER_MESH_IP is the WireGuard IP used for the shared database and
    registry. This separation is required because the public IP is typically
    firewalled for internal ports.
    """
    messages: list[str] = []

    messages.append("Generating dedicated lite-node database credentials on the master.")
    node_user, node_pass = _provision_node_db_credentials(server)
    if node_user and node_pass:
        messages.append(f"Dedicated lite-node DB user ready: {node_user}.")
        master_db_user = node_user
        master_db_pass = node_pass
    else:
        messages.append(
            "Dedicated DB user creation failed; falling back to master DB credentials."
        )
        database_url = os.environ.get("DATABASE_URL", "")
        master_db_user = (
            os.environ.get("POSTGRES_USER")
            or _url_username(database_url)
            or "postgres"
        )
        master_db_pass = (
            os.environ.get("POSTGRES_PASSWORD")
            or _url_password(database_url)
            or ""
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

    # WireGuard mesh IP for internal services (DB, MQ, Redis)
    # Must be set — public IP is firewalled for internal ports.
    master_mesh_ip = _get_master_mesh_ip()
    if not master_mesh_ip:
        raise ValueError(
            "No WireGuard mesh IP found for master. "
            "Cannot provision a lite agent without a mesh VPN IP."
        )
    messages.append(f"Master mesh IP for internal services: {master_mesh_ip}")

    node_id = str(server.id)
    node_queue = _node_queue_name(server)
    # SEC-ZT-003: Generate a unique per-agent GATEWAY_SECRET instead of
    # sharing the master's global secret. This limits blast radius:
    # compromise of one agent does not leak credentials for all agents.
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


def _restrict_ssh_key_to_master_ip(ssh, server: ManagedServer) -> None:
    """
    Generate an ephemeral SSH key pair and add the public key to the node's
    authorized_keys with a ``from="<master-ip>"`` restriction.

    The private key is stored encrypted on the ManagedServer record for
    future self-healing. The ``from="..."`` restriction ensures the key
    can ONLY be used from the master's IP address — even if the private
    key is stolen, it cannot be used from a different network location.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    master_ip = os.environ.get("PUBLIC_IP") or "127.0.0.1"
    mesh_ip = _get_master_mesh_ip()
    allowed_ips = f"{master_ip},{mesh_ip}" if mesh_ip else master_ip

    # Always use Ed25519 to avoid ssh-rsa deprecation rejections on modern OpenSSH (>= 8.8)
    # Generate natively via cryptography to avoid dependency on ssh-keygen
    private_key = ed25519.Ed25519PrivateKey.generate()

    priv_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption()
    ).decode("utf-8")

    pub_key_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH
    ).decode("utf-8")

    # pub_key_bytes is "ssh-ed25519 AAAAC3Nz..."
    pub_key_line = pub_key_bytes.strip()

    # Always replace any existing smsly-self-heal key to guarantee the key on
    # the node matches the fresh private key we are about to store in the DB.
    # The old approach (skip if any smsly-self-heal exists) caused a mismatch
    # after re-provisioning: a stale RSA key with the same comment tag would
    # prevent the new Ed25519 key from being deployed.
    restricted_line = f'from="{allowed_ips}" {pub_key_line} smsly-self-heal\n'
    cmd = (
        f'mkdir -p ~/.ssh && chmod 700 ~/.ssh && '
        f'sed -i "/smsly-self-heal/d" ~/.ssh/authorized_keys 2>/dev/null; '
        f'echo {shlex.quote(restricted_line)} >> ~/.ssh/authorized_keys && '
        f'chmod 600 ~/.ssh/authorized_keys'
    )
    try:
        _stdin, _stdout, _stderr = ssh.exec_command(cmd, timeout=15)
        _exit = _stdout.channel.recv_exit_status()
        if _exit != 0:
            raise RuntimeError(f"SSH command exited with code {_exit}")
        # Store the private key (IP-restricted, encrypted at rest)
        server.ssh_key = priv_key_pem
        server.save(update_fields=['ssh_key', 'updated_at'])
        _append_log(server, f"🔒 IP-restricted SSH key added (from=\"{allowed_ips}\")")
    except Exception as exc:
        _append_log(server, f"⚠ IP-restricted SSH key skipped: {exc}")


def _harden_node_ssh(ssh, server: ManagedServer) -> None:
    """
    Clear the plaintext SSH password from the ManagedServer record after the
    IP-restricted Ed25519 key has been installed and verified.

    Password authentication remains enabled on the remote node (for manual
    operator access), but the platform will only use the generated key.
    """
    if not server.ssh_key:
        _append_log(server, "⚠ SSH cleanup skipped: no key on record")
        return

    try:
        _stdin, _stdout, _stderr = ssh.exec_command(
            "grep -q smsly-self-heal ~/.ssh/authorized_keys", timeout=10
        )
        if _stdout.channel.recv_exit_status() != 0:
            _append_log(server, "⚠ SSH cleanup skipped: key not found in authorized_keys")
            return
    except Exception as exc:
        _append_log(server, f"⚠ SSH cleanup skipped: key verification failed: {exc}")
        return

    # Verify the key actually works by attempting a test connection
    try:
        import io

        import paramiko
        test_ssh = paramiko.SSHClient()
        from apps.deployments.services.ssh_client import _get_tofu_policy
        test_ssh.set_missing_host_key_policy(_get_tofu_policy(server.host, server.ssh_port))
        pkey = paramiko.Ed25519Key.from_private_key(io.StringIO(server.ssh_key))
        test_ssh.connect(
            hostname=server.host,
            port=server.ssh_port,
            username=server.ssh_user,
            pkey=pkey,
            timeout=10,
        )
        test_ssh.close()
    except Exception as exc:
        _append_log(server, f"⚠ SSH cleanup skipped: test connection using restricted key failed: {exc}")
        return

    # Key is confirmed working — clear the stored password.
    if server.ssh_password:
        server.ssh_password = ""
        server.save(update_fields=['ssh_password', 'updated_at'])
        _append_log(server, "🔒 SSH password cleared from record (key-only auth)")


def _schedule_remote_reboot(ssh, server: ManagedServer, reason: str) -> bool:
    """Schedule a best-effort reboot after successful provisioning/update."""
    command = (
        "if [ \"$(id -u)\" -eq 0 ]; then "
        "(nohup sh -c 'sleep 8; /sbin/reboot || reboot' >/dev/null 2>&1 &); "
        "else "
        "(nohup sh -c 'sleep 8; sudo -n /sbin/reboot || sudo -n reboot' >/dev/null 2>&1 &); "
        "fi"
    )
    try:
        ssh.exec_command(command)
        logger.info("Scheduled remote reboot for %s after %s", server.host, reason)
        return True
    except Exception as exc:
        logger.warning("Failed to schedule remote reboot for %s: %s", server.host, exc)
        return False


def _harden_master_firewall(server: ManagedServer) -> None:
    """Ensure the Master's firewall allows traffic from the new node's IP.

    Two layers of firewall rules are applied:
    1. UFW rules for standard ports (Postgres, Redis, RabbitMQ) — lite agents only.
    2. iptables DOCKER-USER chain rules for the registry port (5000) — ALL
       remote nodes.  Docker bypasses UFW entirely, so the DOCKER-USER chain
       is the only way to restrict access to Docker-published ports.

    The backend container has ``NET_ADMIN`` capability and ``iptables``
    installed, so these commands run directly without ``sudo``.
    """
    if not server.host:
        return

    # Validate the IP to prevent shell injection
    try:
        validated_ip = str(ipaddress.ip_address(server.host))
    except ValueError:
        logger.warning(
            "Skipping firewall hardening: invalid IP %s", server.host
        )
        return

    _append_log(server, f"🛡️ Hardening Master firewall for Node IP: {validated_ip}...")

    # ── UFW rules for lite agent service ports ──────────────────────────
    if getattr(server, "is_lite_agent", False):
        for port in ("5432", "6379", "5672"):
            with contextlib.suppress(Exception):
                subprocess.run(
                    ["ufw", "allow", "from", validated_ip,
                     "to", "any", "port", port, "proto", "tcp"],
                    capture_output=True, timeout=5,
                )

    # ── iptables DOCKER-USER rule for registry port 5000 ────────────────
    # Ensure chain exists (idempotent — ignore "already exists")
    subprocess.run(
        ["iptables", "-N", "DOCKER-USER"],
        capture_output=True, timeout=5,
    )

    try:
        # Check if rule already exists (idempotent)
        check = subprocess.run(
            ["iptables", "-C", "DOCKER-USER",
             "-s", validated_ip, "-p", "tcp", "--dport", "5000",
             "-j", "ACCEPT"],
            capture_output=True, timeout=5,
        )
        if check.returncode != 0:
            subprocess.run(
                ["iptables", "-I", "DOCKER-USER",
                 "-s", validated_ip, "-p", "tcp", "--dport", "5000",
                 "-j", "ACCEPT"],
                capture_output=True, timeout=5,
            )
            _append_log(
                server,
                f"✅ iptables: Allowed {validated_ip} -> registry port 5000",
            )
        else:
            _append_log(
                server,
                f"ℹ️ iptables: Rule for {validated_ip}:5000 already exists",
            )
    except Exception as exc:
        logger.warning("Failed to add iptables rule for %s: %s", validated_ip, exc)
        _append_log(
            server,
            f"⚠️ Could not add iptables rule for {validated_ip}:5000 — "
            "ensure the master firewall allows this node manually.",
        )

    # Also allow the node's WireGuard mesh IP if available
    wg_address = getattr(server, "wg_address", None) or ""
    if wg_address:
        try:
            validated_wg = str(ipaddress.ip_address(str(wg_address)))
            check = subprocess.run(
                ["iptables", "-C", "DOCKER-USER",
                 "-s", validated_wg, "-p", "tcp", "--dport", "5000",
                 "-j", "ACCEPT"],
                capture_output=True, timeout=5,
            )
            if check.returncode != 0:
                subprocess.run(
                    ["iptables", "-I", "DOCKER-USER",
                     "-s", validated_wg, "-p", "tcp", "--dport", "5000",
                     "-j", "ACCEPT"],
                    capture_output=True, timeout=5,
                )
                _append_log(
                    server,
                    f"✅ iptables: Allowed mesh IP {validated_wg} -> registry port 5000",
                )
        except (ValueError, Exception) as exc:
            logger.debug("Skipping WireGuard IP iptables rule: %s", exc)

    _append_log(server, "✅ Master firewall rules synchronized for this node.")


def _prepare_remote_install_lock(ssh, server: ManagedServer) -> None:
    """Clear stale installer locks and optionally replace an active retry instance."""
    replace_active = _env_bool("SMSLY_PROVISION_REPLACE_ACTIVE_INSTALLER", default=True)
    command = f"""
set -eu
lock=/tmp/smsly-install.lock
if [ ! -f "$lock" ]; then
  exit 0
fi
pid=$(cat "$lock" 2>/dev/null | tr -dc '0-9' || true)
if [ -z "$pid" ]; then
  echo CLEAR_EMPTY_LOCK
  rm -f "$lock"
  exit 0
fi
if ! kill -0 "$pid" 2>/dev/null; then
  echo CLEAR_STALE_LOCK:$pid
  rm -f "$lock"
  exit 0
fi
args=$(ps -p "$pid" -o args= 2>/dev/null || true)
case "$args" in
  *smsly-install.sh*|*install.sh*) ;;
  *)
    echo REFUSE_NON_INSTALLER_PID:$pid:$args
    exit 42
    ;;
esac
if [ {"1" if replace_active else "0"} -ne 1 ]; then
  echo ACTIVE_INSTALLER:$pid:$args
  exit 41
fi
echo REPLACE_ACTIVE_INSTALLER:$pid:$args
kill "$pid" 2>/dev/null || true
sleep 2
if kill -0 "$pid" 2>/dev/null; then
  kill -9 "$pid" 2>/dev/null || true
fi
rm -f "$lock"
"""
    _stdin, stdout, stderr = ssh.exec_command(command)
    exit_code = stdout.channel.recv_exit_status()
    output = (
        stdout.read().decode("utf-8", errors="replace")
        + stderr.read().decode("utf-8", errors="replace")
    ).strip()

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("CLEAR_STALE_LOCK:"):
            _append_log(server, f"ℹ️ Removed stale installer lock for PID {line.split(':', 1)[1]}.")
        elif line == "CLEAR_EMPTY_LOCK":
            _append_log(server, "ℹ️ Removed empty installer lock file.")
        elif line.startswith("REPLACE_ACTIVE_INSTALLER:"):
            _append_log(
                server,
                "⚠️ Previous installer process was still running; stopped it before retrying.",
            )
        elif line.startswith("ACTIVE_INSTALLER:"):
            _append_log(server, "⚠️ Another installer process is already running on this server.")
        elif line.startswith("REFUSE_NON_INSTALLER_PID:"):
            _append_log(server, "⚠️ Installer lock points at a non-installer process; refusing to remove it automatically.")

    if exit_code != 0:
        raise RuntimeError(
            "Remote installer lock is active. Retry after the current install finishes "
            "or clear /tmp/smsly-install.lock on the server if it is stale."
        )


def _source_root_dir() -> str:
    """Return container path to smsly-hosting source root."""
    return utils_get_source_root()


def _build_local_source_bundle() -> str:
    """
    Build a temporary tar.gz bundle of source code for tokenless provisioning.

    Returns local temporary file path.
    """
    return utils_build_bundle()


def _load_install_script():
    """
    Load the installer script content.

    Priority:
    1) Local file in the backend image/workdir (for bundled installs)
    2) Fallback to GitHub raw URL (for minimal backend images)
    """
    required_sha = os.environ.get("SMSLY_INSTALL_SCRIPT_SHA256", "").strip()

    candidates = [
        # /app/install.sh — the Docker-bundled copy, always current.
        # This is backend/install.sh which is self-contained (no lib/
        # sourcing needed). It is always in sync with the backend image.
        "/app/install.sh",
        # Absolute-path fallback for non-standard container layouts.
        os.path.abspath(
            os.path.join(os.path.dirname(__file__), "../../../install.sh")
        ),
        # Host filesystem copy — only used in local development.
        # In production this can be stale; the bundled copy is preferred.
        os.path.abspath(os.path.join(os.getcwd(), "install.sh")),
    ]

    # Auto-calculate SHA from local candidates if environment variable is missing
    if not required_sha:
        for path in candidates:
            if os.path.isfile(path):
                try:
                    with open(path, "rb") as f:
                        file_content = f.read()
                        if file_content.strip():
                            required_sha = hashlib.sha256(file_content).hexdigest()
                            logger.info("Auto-calculated SMSLY_INSTALL_SCRIPT_SHA256 from %s: %s", path, required_sha)
                            break
                except Exception as e:
                    logger.warning("Failed to auto-calculate SHA from %s: %s", path, e)

    def _verify(content: str, source: str):
        if not required_sha:
            if source.startswith("url:"):
                raise ValueError(
                    "SMSLY_INSTALL_SCRIPT_SHA256 is not set and no local install.sh found. "
                    "Refusing to execute an unverified script from the network. "
                    "Set SMSLY_INSTALL_SCRIPT_SHA256 to the SHA-256 of your install.sh."
                )
            logger.warning(
                "SMSLY_INSTALL_SCRIPT_SHA256 is missing and no local install.sh found. "
                "Skipping checksum verification for %s.", source
            )
            return
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if digest.lower() != required_sha.lower():
            raise ValueError(
                f"install.sh checksum mismatch from {source}: expected {required_sha}, got {digest}"
            )


    for path in candidates:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as install_file:
                content = install_file.read()
                _verify(content, f"local:{path}")
                # Inline the lib/*.sh modules so the script is self-contained
                # when uploaded to a remote VPS (lib/ directory isn't uploaded).
                lib_dir = os.path.join(os.path.dirname(path), "lib")
                if os.path.isdir(lib_dir):
                    inline_lines = []
                    for lib_file in sorted(os.listdir(lib_dir)):
                        if not lib_file.endswith(".sh"):
                            continue
                        if lib_file in ("fresh.sh", "update.sh"):
                            continue
                        lib_path = os.path.join(lib_dir, lib_file)
                        with open(lib_path, encoding="utf-8") as lf:
                            lib_content = lf.read()
                        inline_lines.append(
                            f"# --- lib/{lib_file} ---\n{lib_content}\n"
                            f"# --- end lib/{lib_file} ---"
                        )
                    if inline_lines:
                        inline_block = "\n\n".join(inline_lines)
                        # Replace the lib sourcing block delimited by sentinel
                        # markers. This is robust against encoding / whitespace
                        # variations that would break a fragile str.replace().
                        _start = "--- BEGIN_LIB_SOURCING ---"
                        _end = "--- END_LIB_SOURCING ---"
                        _s = content.find(_start)
                        _e = content.find(_end)
                        if _s != -1 and _e != -1 and _e > _s:
                            # Keep the LIB_DIR line (it is before the marker);
                            # trim from start marker through end marker line.
                            content = (
                                content[:_s]
                                + "\n" + inline_block + "\n"
                                + content[_e + len(_end) + 1:]
                            )
                        else:
                            # Sentinel markers not found — fall back to regex
                            # replacement of the lib sourcing loop.  This handles
                            # older install.sh files on the host filesystem that
                            # pre-date the sentinel-marker approach.
                            content = re.sub(
                                r'for lib in "\$LIB_DIR"/\*\.sh; do\s*\n'
                                r'(?:\s*#.*\n)*'
                                r'\s*case "\$lib" in \*/fresh\.sh\|\*/update\.sh\) continue ;; esac\s*\n'
                                r'\s*\[ -f "\$lib" \] && source "\$lib"\s*\n'
                                r'\s*done\s*\n',
                                "\n" + inline_block + "\n",
                                content,
                            )
                return content, f"local:{path}"

    script_url = (
        os.environ.get(
            "SMSLY_INSTALL_SCRIPT_URL",
            "https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/main/install.sh",
        )
        .strip()
    )
    response = requests.get(script_url, timeout=30)
    response.raise_for_status()
    content = response.text
    _verify(content, f"url:{script_url}")
    if not content.strip():
        raise ValueError("Downloaded installer script is empty")
    return content, f"url:{script_url}"




def _broadcast_provision_log(server: ManagedServer, message: str):
    """Push a provision log line via WebSocket."""
    try:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"provision_{server.id}",
            {
                "type": "provision.log",
                "message": message,
            },
        )
    except Exception:
        pass  # WebSocket is optional — logs are still saved to DB


def _registry_login_commands(server: ManagedServer) -> str:
    """Generate ``docker login`` commands for all registries this
    node has access to.

    Returns a shell snippet that runs ``docker login`` for each
    registry linked via ``server.registry_access``.  For internal
    registries (reachable via mesh VPN), the login runs without
    explicit credentials against the registry's public-key or
    WireGuard-authenticated endpoint.
    """
    commands = []
    registries = server.registry_access.filter(is_active=True).select_related("content_type")
    for reg in registries:
        url = (reg.registry_url or "").strip()
        if not url:
            continue
        user = (reg.username or "").strip()
        pwd = (reg.password or "").strip()
        if user and pwd:
            # Authenticated registry — run docker login
            safe_user = shlex.quote(user)
            safe_pwd = shlex.quote(pwd)
            safe_url = shlex.quote(url)
            commands.append(
                f"printf '%s\\n' {safe_pwd} | docker login --username {safe_user} "
                f"--password-stdin {safe_url} 2>/dev/null || true"
            )
        # else: unauthenticated / internal registry — skip docker login entirely.
        # Running `docker login <url>` without credentials in a non-interactive SSH
        # session will hang waiting for a prompt or silently fail. These registries
        # must be listed in /etc/docker/daemon.json insecure-registries during
        # provisioning instead.
    if commands:
        return " && ".join(commands)
    return "true"


def _append_log(server: ManagedServer, line: str):
    """Append a line to provision_logs and broadcast."""
    import re
    import uuid

    from django.utils import timezone
    timestamp = timezone.now().strftime("%Y-%m-%d %H:%M:%S")
    correlation_id = getattr(server, "_provision_correlation_id", None)
    if not correlation_id:
        correlation_id = str(uuid.uuid4())[:8]
        server._provision_correlation_id = correlation_id
    line = re.sub(r'([A-Za-z0-9+/=]{40,})', r'[REDACTED]', line)
    line = re.sub(r'([0-9a-f]{32,})', r'[REDACTED]', line)
    formatted_line = f"[{timestamp}] [tx:{correlation_id}] {line}"
    server.provision_logs += formatted_line + "\n"
    server.save(update_fields=["provision_logs", "updated_at"])
    _broadcast_provision_log(server, formatted_line)


def _get_ssh_client(server: ManagedServer) -> paramiko.SSHClient:
    """Create and connect an SSH client to the target server."""
    client = paramiko.SSHClient()
    # Do NOT call load_system_host_keys() in Docker containers — stale
    # entries from prior provisioning runs cause key-mismatch rejections
    # after server reboots (AutoAddPolicy only handles *missing* keys, not
    # *changed* ones).  WarningPolicy logs but accepts any host key, which
    # is appropriate for infrastructure automation inside a trusted network.

    strict_mode = str(os.environ.get("SMSLY_STRICT_SSH_HOST_KEY_CHECK", "false")).lower() not in ("false", "0", "no")
    allow_auto_add = str(os.environ.get("ALLOW_SSH_AUTOADD", "false")).lower() in ("true", "1", "yes")

    if strict_mode and not allow_auto_add:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    elif allow_auto_add:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    else:
        # Default: TOFU — trust on first use, reject on key change.
        from apps.deployments.services.ssh_client import _get_tofu_policy
        client.set_missing_host_key_policy(_get_tofu_policy(server.host, server.ssh_port))


    connect_kwargs = {
        "hostname": server.host,
        "port": server.ssh_port,
        "username": server.ssh_user,
        "timeout": 30,
        "banner_timeout": 30,
    }

    if server.ssh_key:
        # Use SSH private key
        key_file = io.StringIO(server.ssh_key)
        pkey: paramiko.PKey | None = None
        try:
            pkey = paramiko.RSAKey.from_private_key(key_file)
        except paramiko.SSHException:
            key_file.seek(0)
            try:
                pkey = paramiko.Ed25519Key.from_private_key(key_file)
            except paramiko.SSHException:
                pkey = None
        if pkey is not None:
            connect_kwargs["pkey"] = pkey
        elif server.ssh_password:
            # Key couldn't be parsed — fall back to password
            connect_kwargs["password"] = server.ssh_password
        else:
            raise ValueError("SSH key is present but could not be parsed, and no password fallback available.")
    elif server.ssh_password:
        connect_kwargs["password"] = server.ssh_password
    else:
        raise ValueError("No SSH credentials provided (need password or key)")

    # Try key auth first; if it fails (e.g. stale key from prior provision
    # on a re-provisioned VPS), fall back to password auth.
    try:
        client.connect(**connect_kwargs)
    except paramiko.AuthenticationException:
        if "pkey" in connect_kwargs and server.ssh_password:
            logger.warning(
                "SSH key auth failed for %s — falling back to password "
                "(key may be stale from prior provisioning).",
                server.host,
            )
            connect_kwargs.pop("pkey")
            connect_kwargs["password"] = server.ssh_password
            client.connect(**connect_kwargs)
        else:
            raise
    return client


def _provision_node_db_credentials(server: ManagedServer):
    """
    Create a dedicated PostgreSQL user on the Master DB for this node.

    Grants ALL PRIVILEGES on existing tables AND sets ALTER DEFAULT PRIVILEGES
    so future tables created by migrations are automatically accessible.
    """
    master_db_url = os.environ.get("DATABASE_URL")
    if not master_db_url:
        return None, None

    # Node-specific username
    node_id_short = str(server.id).split('-')[0]
    username = f"node_agent_{node_id_short}"

    # Preserve existing password if already generated/stored
    metadata = server.provider_metadata or {}
    existing_pass = metadata.get("node_db_password") or server.node_db_password

    password = existing_pass or secrets.token_urlsafe(24)

    try:
        import psycopg2
        from psycopg2 import sql
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

        conn = psycopg2.connect(master_db_url)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)

        is_new_user = False
        with conn.cursor() as cur:
            # Check if user already exists
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (username,))
            user_exists = bool(cur.fetchone())

            if not user_exists:
                cur.execute(sql.SQL("CREATE USER {} WITH PASSWORD %s").format(sql.Identifier(username)), (password,))
                is_new_user = True
            elif not existing_pass:
                # If the user exists in DB but we don't have the password stored in metadata, reset it
                cur.execute(sql.SQL("ALTER USER {} WITH PASSWORD %s").format(sql.Identifier(username)), (password,))
                is_new_user = True

            # Grant database-level access
            parsed = urlparse(master_db_url)
            db_name = parsed.path.lstrip('/')

            cur.execute(sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {}").format(
                sql.Identifier(db_name), sql.Identifier(username)
            ))

            # Grant schema-level permissions (same connection, same DB)
            cur.execute(sql.SQL("GRANT ALL PRIVILEGES ON SCHEMA public TO {}").format(sql.Identifier(username)))
            cur.execute(sql.SQL("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(username)))
            cur.execute(sql.SQL("GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(username)))
            # CRITICAL: Auto-grant permissions on future tables created by migrations
            cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {}").format(sql.Identifier(username)))
            cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO {}").format(sql.Identifier(username)))

        logger.info("Provisioned Master DB credentials for node %s: %s (new_user=%s)", server.name, username, is_new_user)
        if not isinstance(server.provider_metadata, dict):
            server.provider_metadata = {}
        server.provider_metadata["node_db_user"] = username
        # Store password in encrypted field, remove from plaintext metadata
        server.node_db_password = password
        server.provider_metadata.pop("node_db_password", None)
        server.save(update_fields=["provider_metadata", "node_db_password"])

        # Re-render PgCat config (hot-reload) so the new/changed password
        # is picked up. This handles both new users AND password rotations.
        _rerender_pgcat_config()

        return username, password
    except Exception as e:
        logger.error("Failed to create node DB credentials for %s: %s", server.name, e)
        return None, None


def _restart_pgcat():
    """Restart the PgCat pooler so it re-renders its config with new node agents.

    render_pgcat_config.py queries the DB for all lite-agent rows and builds
    PgCat user pools for each.  A restart causes that script to run again,
    picking up any newly-provisioned node agent credentials.

    First attempts a hot-reload (docker exec + render + SIGHUP) to avoid
    connection disruption. Falls back to full restart with retry on failure.
    """
    import time as _time

    def _find_pgcat_container():
        try:
            import docker as docker_lib
            client = docker_lib.from_env()
            for name in ("smsly-hosting-pgcat-1", "smsly-hosting-pgcat"):
                try:
                    return client.containers.get(name), name
                except docker_lib.errors.NotFound:
                    continue
        except Exception as exc:
            logger.warning("PgCat docker client init failed: %s", exc)
        return None, None

    def _wait_healthy(container, name, timeout=30):
        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            try:
                container.reload()
                health = container.attrs.get("State", {}).get("Health", {}).get("Status")
                if health == "healthy":
                    logger.info("PgCat %s is healthy.", name)
                    return True
            except Exception:
                pass
            _time.sleep(2)
        return False

    container, pgcat_name = _find_pgcat_container()
    if not container:
        logger.warning(
            "PgCat container not found — node agent pools will not be "
            "active until the next PgCat restart."
        )
        return

    # Strategy 1: Hot-reload via docker exec + SIGHUP (no connection drop)
    try:
        exit_code, output = container.exec_run(
            ["python3", "/app/render_pgcat_config.py", "/tmp/pgcat.toml"],
            demux=True,
        )
        if exit_code == 0:
            # Copy rendered config to the live path and send SIGHUP
            container.exec_run(
                ["sh", "-c", "cp /tmp/pgcat.toml /etc/pgcat/pgcat.toml && kill -HUP 1"],
            )
            _time.sleep(2)
            logger.info("PgCat hot-reloaded config via docker exec + SIGHUP.")
            return
        logger.warning("PgCat render exited %d: %s", exit_code, (output or b"").decode(errors="replace"))
    except Exception as exc:
        logger.info("PgCat hot-reload failed (%s), falling back to restart.", exc)

    # Strategy 2: Full restart with retry (3 attempts, exponential backoff)
    for attempt in range(3):
        try:
            container.restart(timeout=10)
            logger.info("Restarted PgCat container %s (attempt %d/3).", pgcat_name, attempt + 1)
            if _wait_healthy(container, pgcat_name, timeout=20):
                return
            logger.warning("PgCat %s not healthy after restart attempt %d.", pgcat_name, attempt + 1)
        except Exception as exc:
            logger.warning("PgCat restart attempt %d failed: %s", attempt + 1, exc)
        if attempt < 2:
            _time.sleep(5 * (attempt + 1))

    logger.warning(
        "PgCat %s did not become healthy after 3 restart attempts. "
        "Node agent pools may not be active.",
        pgcat_name,
    )


def _rerender_pgcat_config():
    """Hot-reload PgCat config without restarting the container.

    Runs render_pgcat_config.py inside the container to regenerate
    pgcat.toml, then sends SIGHUP so PgCat re-reads it. This is
    preferred over _restart_pgcat() for config-only changes (new
    users, password rotations, replica changes) because it avoids
    connection disruption.

    Falls back to _restart_pgcat() if docker exec fails.
    """
    import time as _time
    try:
        import docker as docker_lib
        client = docker_lib.from_env()
        container = None
        for name in ("smsly-hosting-pgcat-1", "smsly-hosting-pgcat"):
            try:
                container = client.containers.get(name)
                break
            except docker_lib.errors.NotFound:
                continue
        if not container:
            logger.warning("PgCat container not found for config re-render.")
            return
        exit_code, output = container.exec_run(
            ["python3", "/app/render_pgcat_config.py", "/tmp/pgcat.toml"],
            demux=True,
        )
        if exit_code == 0:
            container.exec_run(
                ["sh", "-c", "cp /tmp/pgcat.toml /etc/pgcat/pgcat.toml && kill -HUP 1"],
            )
            _time.sleep(1)
            logger.info("PgCat config re-rendered and reloaded via SIGHUP.")
        else:
            logger.warning("PgCat render failed (exit %d): %s", exit_code, (output or b"").decode(errors="replace"))
            _restart_pgcat()
    except Exception as exc:
        logger.warning("PgCat re-render failed (%s), falling back to restart.", exc)
        _restart_pgcat()


def _verify_agent_db_connectivity(ssh, server: ManagedServer):
    """Verify that a freshly provisioned agent can reach its DB through PgCat.

    After WireGuard is set up, the agent's backend should be able to connect
    to the master PgCat at MASTER_MESH_IP:5432.  We SSH into the agent and
    query the local health endpoint, which checks database connectivity.
    """
    if server_install_mode(server) != "agent-lite":
        return  # Nodes run their own PostgreSQL; nothing to verify

    _append_log(server, "Verifying agent DB connectivity via health endpoint...")
    deadline = getattr(server, "_start_time", 0) + 120
    import time as _time
    while _time.monotonic() < deadline:
        try:
            _stdin, stdout, _stderr = ssh.exec_command(
                "curl -sf --max-time 5 http://localhost:8000/health/ 2>/dev/null",
                timeout=10,
            )
            body = stdout.read().decode("utf-8", errors="replace").strip()
            if '"status":"healthy"' in body or '"database":"healthy"' in body:
                _append_log(server, "Agent DB connectivity verified (health endpoint reports healthy).")
                return
        except Exception:
            pass
        _time.sleep(5)

    _append_log(
        server,
        "Agent health endpoint did not report healthy within the wait window. "
        "The node will be marked ONLINE but may require a manual restart if "
        "the database connection does not recover on its own.",
    )


@shared_task(bind=True, max_retries=0, soft_time_limit=1860, time_limit=1920)
def provision_server(self, server_id: str, skip_reboot: bool = False):
    """
    Provision Grid on a remote server via SSH.

    Steps:
    1. SSH into the target server
    2. Upload install.sh
    3. Run it in non-interactive mode
    4. Parse output for credentials
    5. Update ManagedServer with api_url + api_token
    """
    try:
        with transaction.atomic():
            server = ManagedServer.objects.select_for_update().get(id=server_id)
            conflict = (
                ManagedServer.objects.select_for_update()
                .filter(
                    host=server.host,
                    provision_status=ManagedServer.ProvisionStatus.PROVISIONING,
                )
                .exclude(id=server.id)
                .exists()
            )
            if conflict:
                server.provision_status = ManagedServer.ProvisionStatus.FAILED
                server.provision_logs = (
                    "Another provisioning task is already running for this host. "
                    "Retry after the active install completes.\n"
                )
                server.save(
                    update_fields=["provision_status", "provision_logs", "updated_at"]
                )
                return
            # Mark provisioning while holding locks to reduce same-host races.
            server.provision_status = ManagedServer.ProvisionStatus.PROVISIONING
            server.provision_logs = ""
            server.save(
                update_fields=["provision_status", "provision_logs", "updated_at"]
            )
    except ManagedServer.DoesNotExist:
        logger.error("Server %s not found", server_id)
        return

    _append_log(server, "🚀 Starting Grid provisioning...")
    _append_log(server, f"📡 Connecting to {server.ssh_user}@{server.host}:{server.ssh_port}")

    ssh = None
    local_bundle_path = None
    resources = _ProvisioningResources(server)
    try:
        # The installer repository is public/open source, so provisioning should not
        # depend on a user's linked GitHub OAuth token. Keep the local bundle path as
        # an explicit operator override only; the normal path uses an unauthenticated
        # public clone and avoids uploading a large tarball over SSH.
        prefer_local_bundle = str(
            os.environ.get("SMSLY_PROVISION_USE_LOCAL_BUNDLE", "false")
        ).strip().lower() not in ("0", "false", "no", "off")
        use_local_bundle = prefer_local_bundle

        # -- Step 0: Harden Master Firewall --
        _harden_master_firewall(server)
        if getattr(server, "is_lite_agent", False):
            for port in ("5432", "6379", "5672"):
                resources.track_firewall_rule(server.host, port)

        # -- Step 1: Connect --
        ssh = _get_ssh_client(server)
        _append_log(server, "✅ SSH connection established")

        # -- Step 1b: Add IP-restricted SSH key to node's authorized_keys --
        # Even if the private key is stolen, it can only be used from the
        # master's IP address. This prevents credential theft from other
        # network locations.
        _restrict_ssh_key_to_master_ip(ssh, server)
        if server.ssh_key:
            resources.track_ssh_key_added()

        # -- Step 1c: Clear stored SSH password (key is now installed) --
        # Password auth stays enabled on the node for manual operator access,
        # but the platform will only use the IP-restricted Ed25519 key.
        _harden_node_ssh(ssh, server)

        # -- Step 2: Upload install script --
        _append_log(server, "📦 Uploading install script...")
        sftp = ssh.open_sftp()

        install_script_content, install_script_source = _load_install_script()
        _append_log(server, f"📥 Installer source: {install_script_source}")
        if use_local_bundle:
            _append_log(
                server,
                "ℹ️ Provisioning in local-bundle mode (no GitHub clone required).",
            )
        else:
            _append_log(
                server,
                "ℹ️ Installer repository is public; using unauthenticated GitHub clone.",
            )
        remote_script = sftp.open("/tmp/smsly-install.sh", "w")
        try:
            remote_script.write(install_script_content)
            remote_script.flush()
        finally:
            remote_script.close()
        sftp.chmod("/tmp/smsly-install.sh", 0o755)

        run_prefix = ""
        sftp.close()
        _append_log(server, "✅ Install script uploaded")

        run_prefix = ""
        if use_local_bundle:
            _append_log(server, "📦 Uploading local source bundle for provisioning fallback...")
            local_bundle_path = _build_local_source_bundle()
            try:
                bundle_size = os.path.getsize(local_bundle_path)
                _append_log(server, f"ℹ️ Local bundle size: {bundle_size / 1024 / 1024:.2f} MB")

                # Re-open SFTP for the bundle put
                sftp_bundle = ssh.open_sftp()
                try:
                    sftp_bundle.put(local_bundle_path, "/tmp/smsly-hosting-src.tar.gz")
                    remote_size = sftp_bundle.stat("/tmp/smsly-hosting-src.tar.gz").st_size
                finally:
                    sftp_bundle.close()
                if remote_size != bundle_size:
                    raise RuntimeError(
                        "Uploaded source bundle size mismatch: "
                        f"local={bundle_size} remote={remote_size}"
                    )

                _append_log(server, "📦 Extracting source bundle on target...")
                extract_cmd = (
                    "rm -rf /tmp/smsly-hosting-src && "
                    "mkdir -p /tmp/smsly-hosting-src && "
                    "tar -xzf /tmp/smsly-hosting-src.tar.gz "
                    "-C /tmp/smsly-hosting-src && "
                    "test -f /tmp/smsly-hosting-src/docker-compose.prod.yml"
                )
                stdin, stdout, stderr = ssh.exec_command(extract_cmd)
                extract_exit = stdout.channel.recv_exit_status()
                extract_err = stderr.read().decode("utf-8", errors="replace").strip()
                if extract_exit != 0:
                    raise RuntimeError(
                        "Failed to prepare local source bundle on target: "
                        f"{extract_err or f'exit {extract_exit}'}"
                    )
                run_prefix = "cd /tmp/smsly-hosting-src && "
            finally:
                if os.path.exists(local_bundle_path):
                    with contextlib.suppress(OSError):
                        os.remove(local_bundle_path)

        # -- Step 3: Run install script --
        _prepare_remote_install_lock(ssh, server)
        _append_log(server, "⚙️ Running Grid installer (this may take 5-15 minutes)...")

        # Build non-interactive environment.
        master_ip = os.environ.get("PUBLIC_IP") or "127.0.0.1"
        install_env = {
            "NON_INTERACTIVE": "1",
            "SKIP_REBOOT": "1",
            "SMSLY_STRICT_VERIFY": "1",
            "MASTER_IP": master_ip,
            "SMSLY_BRANCH": os.environ.get("SMSLY_BRANCH", "main"),
            "USE_SSL": "false",
            "SMSLY_NODE_HOST": server.host,
        }

        install_args: list[str] = []
        install_mode = server_install_mode(server)
        if install_mode == "agent-lite":
            lite_env, lite_messages = build_agent_lite_install_env(
                server,
                master_ip=master_ip,
            )
            for message in lite_messages:
                _append_log(server, message)
            install_env.update(lite_env)
            install_args.append("--mode=agent-lite")
            # Track DB user for rollback
            node_db_user = (server.provider_metadata or {}).get("node_db_user")
            if node_db_user:
                resources.track_db_user(node_db_user)
        elif install_mode == "node":
            install_args.append("--mode=node")

            # --- Node Provisioning & Automated TLS ---
            from apps.deployments.models_core import PlatformConfig
            from apps.deployments.services.dns import ensure_dns_records
            config = PlatformConfig.get_config()
            cf_token = config.cloudflare_api_token
            root_domain = config.root_domain

            if cf_token and root_domain:
                node_slug = str(server.id).split('-')[0]
                node_domain = f"node-{node_slug}.{root_domain}"

                _append_log(server, f"🌐 Automated TLS: Generating DNS record for node ({node_domain})...")
                try:
                    ensure_dns_records([node_domain], server.host, cf_token)
                    _append_log(server, "✅ Automated TLS: DNS sync OK.")

                    install_env["CLOUDFLARE_API_TOKEN"] = cf_token
                    install_env["DOMAIN"] = node_domain
                    install_env["USE_SSL"] = "true"
                except Exception as e:
                    logger.error("Failed to provision DNS/TLS for node %s: %s", server.name, e)
                    _append_log(server, f"⚠️ Automated TLS: Failed to generate DNS record: {e}")
        # ─── Resume Check ──────────────────────────────────────────────────
        stdin, stdout, stderr = ssh.exec_command("test -f /opt/smsly-hosting/.smsly_install_state && echo 'RESUME' || echo 'FRESH'")
        remote_mode = stdout.read().decode().strip()
        if "RESUME" in remote_mode:
            _append_log(server, "ℹ️ Found partial installation state. Resuming from last checkpoint...")
            install_args.append("--resume")

        if use_local_bundle:
            install_env["SMSLY_FORCE_SOURCE_SYNC"] = "1"
            install_env["SMSLY_INSTALL_WORKDIR"] = "/tmp/smsly-hosting-src"

        install_args_str = " ".join(shlex.quote(arg) for arg in install_args)
        cmd = (
            f"{run_prefix}{_shell_env_assignments(install_env)} "
            f"bash /tmp/smsly-install.sh {install_args_str} 2>&1"
        )

        # Execute with a channel for streaming output
        transport = ssh.get_transport()
        if transport is None:
            raise RuntimeError("SSH transport is not active; cannot open session")
        channel = transport.open_session()
        channel.set_combine_stderr(True)
        channel.settimeout(PROVISION_TIMEOUT_SECONDS + 60)
        channel.exec_command(cmd)
        started_at = time.monotonic()

        # Stream output in chunks
        buffer = ""
        credentials_file_content = ""
        while True:
            if channel.recv_ready():
                chunk = channel.recv(4096).decode("utf-8", errors="replace")
                buffer += chunk

                # Process complete lines
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line:
                        _append_log(server, line)

                        # Look for credentials file path
                        if (
                            "credentials saved" in line.lower()
                            or ".credentials" in line
                        ):
                            _append_log(server, "[cred] Credentials detected — extracting...")

            if channel.exit_status_ready():
                # Drain remaining output
                while channel.recv_ready():
                    chunk = channel.recv(4096).decode("utf-8", errors="replace")
                    buffer += chunk
                for line in buffer.strip().split("\n"):
                    if line.strip():
                        _append_log(server, line.strip())
                break

            elapsed = time.monotonic() - started_at
            if elapsed > PROVISION_TIMEOUT_SECONDS:
                with contextlib.suppress(Exception):
                    channel.close()
                raise TimeoutError(
                    f"Install script timed out after {PROVISION_TIMEOUT_SECONDS} seconds"
                )

            time.sleep(0.5)

        exit_code = channel.recv_exit_status()
        _append_log(server, f"\n[installer] Install script exited with code: {exit_code}")

        # Older installer revisions could return a stale non-zero status after
        # printing the final success banner. Treat that as success only when no
        # failure banner was emitted.
        is_success_in_logs = _installer_logs_confirm_success(server.provision_logs)

        if exit_code != 0:
            if is_success_in_logs:
                _append_log(server, "Installer logs confirm success despite a non-zero SSH exit status.")
                server.provision_status = ManagedServer.ProvisionStatus.DONE
                server.save(update_fields=["provision_status"])
            else:
                server.provision_status = ManagedServer.ProvisionStatus.FAILED
                server.save(update_fields=["provision_status"])
                raise RuntimeError(f"Install script failed with exit code {exit_code}")

        # Scrub installer artifacts (may contain injected tokens)
        with contextlib.suppress(Exception):
            ssh.exec_command(
                "shred -u /tmp/smsly-install.sh /tmp/smsly-hosting-src.tar.gz "
                "/tmp/smsly-hosting-src 2>/dev/null || "
                "rm -rf /tmp/smsly-install.sh /tmp/smsly-hosting-src.tar.gz /tmp/smsly-hosting-src"
            )

        # -- Step 4a: Registry Docker Login --
        registry_cmds = _registry_login_commands(server)
        if registry_cmds and registry_cmds != "true":
            _append_log(server, "🔑 Logging into configured registries on node...")
            try:
                stdin, stdout, stderr = ssh.exec_command(registry_cmds, timeout=60)
                reg_exit = stdout.channel.recv_exit_status()
                if reg_exit == 0:
                    _append_log(server, "✅ Docker login succeeded for all configured registries")
                else:
                    reg_err = stderr.read().decode("utf-8", errors="replace").strip()[:500]
                    _append_log(server, f"⚠️ Registry docker-login had non-zero exit ({reg_exit}): {reg_err}")
            except Exception as exc:
                _append_log(server, f"⚠️ Registry docker-login command failed: {exc}")

        # -- Step 4b: Extract credentials --
        _append_log(server, "[cred] Reading credentials from server...")

        stdin, stdout, stderr = ssh.exec_command(
            "cat /root/.credentials 2>/dev/null || "
            "cat /opt/smsly-hosting/.credentials 2>/dev/null || "
            "cat /root/.smsly-credentials 2>/dev/null || "
            "cat /opt/smsly-hosting/.smsly-credentials 2>/dev/null || "
            "echo 'CREDS_NOT_FOUND'"
        )
        credentials_file_content = stdout.read().decode("utf-8", errors="replace")

        api_token = ""
        admin_user = ""
        admin_password = ""

        if "CREDS_NOT_FOUND" in credentials_file_content:
            _append_log(server, "⚠️ Credentials file not found — trying API token from .env")
            # Fallback: extract from .env file
            stdin, stdout, stderr = ssh.exec_command(
                "grep -E '^(ADMIN_TOKEN|API_TOKEN|AUTH_TOKEN|DJANGO_SUPERUSER_PASSWORD)=' "
                "/opt/smsly-hosting/.env 2>/dev/null"
            )
            for token_line in stdout.read().decode("utf-8").strip().splitlines():
                if "=" in token_line:
                    key, value = token_line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip("'\"")
                    if key == "DJANGO_SUPERUSER_PASSWORD":
                        admin_user = "admin"
                        admin_password = value
                    elif key in ("ADMIN_TOKEN", "API_TOKEN", "AUTH_TOKEN"):
                        api_token = value
        else:
            # Parse credentials file
            for line in credentials_file_content.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if line.lower().startswith("username:"):
                    admin_user = line.split(":", 1)[1].strip()
                elif line.lower().startswith("password:"):
                    admin_password = line.split(":", 1)[1].strip()
                elif "token" in line.lower() and ":" in line:
                    api_token = line.split(":", 1)[1].strip().strip("'\"")
                elif line.startswith(("API_TOKEN=", "ADMIN_TOKEN=", "AUTH_TOKEN=")):
                    api_token = line.split("=", 1)[1].strip().strip("'\"")

        # -- Step 4b: Extract TLS Certificate --
        if install_mode in ("agent-lite", "node"):
            _append_log(server, "[cred] Fetching node TLS certificate automatically...")
            # The installer places the self-signed cert in the certs directory
            stdin, stdout, stderr = ssh.exec_command("cat /opt/smsly-hosting/certs/registry.crt 2>/dev/null || cat /opt/smsly-hosting/caddy-config/certs/ip.crt 2>/dev/null")
            tls_cert = stdout.read().decode("utf-8", errors="replace").strip()
            if tls_cert and "-----BEGIN CERTIFICATE-----" in tls_cert:
                import hashlib
                server.tls_cert_sha256 = hashlib.sha256(tls_cert.strip().encode('utf-8')).hexdigest()
                server.save(update_fields=["tls_cert_sha256"])
                _append_log(server, "✅ Node TLS certificate automatically fetched and saved!")
            else:
                _append_log(server, "⚠️ Warning: Could not automatically fetch node TLS certificate.")

        # -- Step 5: Determine API URL --
        # Check if SSL was set up (look for Caddy with domain)
        stdin, stdout, stderr = ssh.exec_command(
            "grep -E '^(DOMAIN|USE_SSL)=' /opt/smsly-hosting/.env 2>/dev/null"
        )
        env_pairs = {}
        for line in stdout.read().decode("utf-8", errors="replace").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            env_pairs[key.strip()] = value.strip().strip("'\"")

        env_domain = (env_pairs.get("DOMAIN") or "").strip().rstrip(".")
        use_ssl = (env_pairs.get("USE_SSL") or "").strip().lower() in (
            "1", "true", "yes", "on"
        )
        is_ip_domain = False
        try:
            ipaddress.ip_address(env_domain)
            is_ip_domain = True
        except ValueError:
            is_ip_domain = False

        candidate_urls: list[str] = []
        if env_domain and env_domain not in ("localhost", "127.0.0.1") and not is_ip_domain:
            scheme = "https" if use_ssl else "http"
            candidate_urls.append(f"{scheme}://{env_domain}")
        # Control-plane reachable endpoints.
        candidate_urls.append(f"http://{server.host}")
        # Legacy compatibility only; some older installs may still expose this.
        candidate_urls.append(f"http://{server.host}:8090")

        # Preserve order while removing duplicates.
        seen_urls: set[str] = set()
        api_urls = []
        for url in candidate_urls:
            normalized = url.rstrip("/")
            if normalized in seen_urls:
                continue
            seen_urls.add(normalized)
            api_urls.append(normalized)

        api_url = api_urls[0] if api_urls else f"http://{server.host}"

        remote_gateway_secret = ""
        try:
            _stdin, stdout, stderr = ssh.exec_command(
                "grep -E '^GATEWAY_SECRET=' /opt/smsly-hosting/.env "
                "2>/dev/null | head -1"
            )
            secret_line = stdout.read().decode("utf-8", errors="replace").strip()
            if "=" in secret_line:
                remote_gateway_secret = secret_line.split("=", 1)[1].strip().strip("'\"")
        except Exception as secret_exc:
            _append_log(server, f"Warning: could not read remote gateway secret: {secret_exc}")

        if not api_token and remote_gateway_secret:
            from .tls_verify import (
                _check_pin_after_handshake,
                resolve_tls_verify_for_url,
            )
            token_errors = []
            for candidate_url in api_urls:
                path = "/api/v1/auth/node-token-exchange-hmac/"
                body = json.dumps(
                    {"node_name": f"Node-{server.host or server.name}"[:100]},
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
                timestamp = str(int(time.time()))
                nonce = secrets.token_urlsafe(16)
                body_hash = hashlib.sha256(body).hexdigest()
                # SECURITY: bind the nonce into the signed payload so
                # a captured request cannot be replayed with a fresh
                # nonce. Matches the format expected by
                # views_node_exchange.node_token_exchange_via_gateway.
                payload = f"POST|{path}|{timestamp}|{nonce}|{body_hash}"
                signature = hmac_mod.new(
                    remote_gateway_secret.encode(),
                    payload.encode(),
                    hashlib.sha256,
                ).hexdigest()
                # SECURITY (Batch G cont): per-server TLS verify
                # + optional SHA-256 pin. For HTTPS URLs the safe
                # default is verify=True; a network-adjacent MITM
                # would otherwise capture the gateway_secret.
                verify, fingerprint = resolve_tls_verify_for_url(
                    candidate_url
                )
                try:
                    response = requests.post(
                        f"{candidate_url}{path}",
                        data=body,
                        headers={
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                            "X-Gateway-Signature-V2": signature,
                            "X-Request-Timestamp": timestamp,
                            "X-Request-Nonce": nonce,
                        },
                        timeout=20,
                        verify=verify,
                        stream=True,
                    )
                    if fingerprint:
                        _check_pin_after_handshake(response, fingerprint)
                    _ = response.content
                    response.close()
                    if not response.ok:
                        token_errors.append(f"{candidate_url}:HTTP {response.status_code}")
                        continue
                    token_value = response.json().get("token", "")
                    if token_value:
                        api_token = token_value
                        api_url = candidate_url
                        _append_log(server, "HMAC token exchange succeeded.")
                        break
                    token_errors.append(f"{candidate_url}:empty token payload")
                except Exception as token_exc:
                    token_errors.append(f"{candidate_url}:{token_exc}")
            if not api_token and token_errors:
                _append_log(
                    server,
                    "Warning: HMAC token exchange failed via all candidates: "
                    + "; ".join(token_errors),
                )

        # If installer did not emit an API token, exchange admin credentials for one.
        if not api_token and admin_user and admin_password:
            from .tls_verify import (
                _check_pin_after_handshake,
                resolve_tls_verify_for_url,
            )
            token_errors = []
            for candidate_url in api_urls:
                login_url = f"{candidate_url}/api/v1/auth/login/"
                # SECURITY (Batch G cont): same per-server TLS verify
                # as the HMAC exchange above. This is the path that
                # ships the SSH/admin password over the wire —
                # without verify=True an MITM would capture it.
                verify, fingerprint = resolve_tls_verify_for_url(
                    candidate_url
                )
                try:
                    response = requests.post(
                        login_url,
                        json={"username": admin_user, "password": admin_password},
                        timeout=20,
                        verify=verify,
                        stream=True,
                    )
                    if fingerprint:
                        _check_pin_after_handshake(response, fingerprint)
                    _ = response.content
                    response.close()
                    if not response.ok:
                        token_errors.append(f"{candidate_url}:HTTP {response.status_code}")
                        continue
                    payload = response.json()
                    token_value = payload.get("key") or payload.get("token", "")
                    if token_value:
                        api_token = token_value
                        api_url = candidate_url
                        break
                    token_errors.append(f"{candidate_url}:empty token payload")
                except Exception as token_exc:
                    token_errors.append(f"{candidate_url}:{token_exc}")

            if not api_token and token_errors:
                _append_log(
                    server,
                    "Warning: API token exchange failed via all candidates: "
                    + "; ".join(token_errors),
                )

        if not api_token and getattr(server, "is_lite_agent", False):
            _append_log(
                server,
                "Lite Agent install does not create a local admin token; "
                "Master will manage the node through the shared agent channel.",
            )
        elif not api_token:
            raise RuntimeError(
                "Provisioning completed but no API token was discovered. "
                "Verify gateway health and credentials, then retry provisioning."
            )

        _append_log(server, f"🌐 API URL: {api_url}")
        if api_token:
            _append_log(server, f"[cred] Token: {'*' * 8}...{api_token[-4:] if len(api_token) > 4 else '****'}")

        # -- Step 6: Update server record --
        server.api_url = api_url
        server.api_token = api_token or ""
        provider_metadata = dict(server.provider_metadata or {})
        provider_metadata["connection_mode"] = server_connection_mode(server)
        update_fields = [
            "api_url", "api_token", "provision_status", "status",
            "provider_metadata", "updated_at",
        ]
        # wg_address may have been set by ensure_server_in_default_mesh or fallback
        if getattr(server, "wg_address", None):
            update_fields.append("wg_address")
        if remote_gateway_secret:
            server.gateway_secret = remote_gateway_secret
            update_fields.append("gateway_secret")
            _append_log(server, "Remote HMAC gateway secret synchronized.")
        if getattr(server, "is_lite_agent", False):
            gateway_secret = str(install_env.get("MASTER_GATEWAY_SECRET") or "").strip()
            node_queue = str(install_env.get("SMSLY_NODE_QUEUE") or _node_queue_name(server))
            provider_metadata["node_id"] = str(server.id)
            provider_metadata["node_queue"] = node_queue
            provider_metadata["node_host"] = str(server.host or "")
            if gateway_secret and not remote_gateway_secret:
                server.gateway_secret = gateway_secret
                update_fields.append("gateway_secret")
                _append_log(
                    server,
                    "Lite Agent HMAC secret synchronized with the master.",
                )
            _append_log(server, f"Lite Agent node queue: {node_queue}")
        server.provider_metadata = provider_metadata

        # -- Step 7: Synchronous WireGuard mesh setup BEFORE marking ONLINE --
        # The wg_address must be populated before the server is marked ONLINE so
        # the orchestrator can use WireGuard mesh URLs for health checks and
        # deployments immediately after provisioning completes.
        wg_assigned = False
        try:
            from apps.deployments.services.wireguard_service import WireGuardService
            mesh_result = WireGuardService.ensure_server_in_default_mesh(
                server,
                deploy_async=True,
            )
            wg_assigned = bool(mesh_result.get("wg_address"))
            wg_peer_id = mesh_result.get("peer_id")
            if wg_peer_id:
                resources.track_wg_peer(wg_peer_id)
            _append_log(
                server,
                f"VPN mesh auto-connect queued: {mesh_result.get('wg_address')}",
            )
        except Exception as mesh_exc:
            _append_log(
                server,
                f"Warning: VPN mesh auto-connect could not complete yet: {mesh_exc}",
            )

        # Fallback: if wg_address is still empty, try to read it directly from
        # the WireGuardPeer record that ensure_server_in_default_mesh may have
        # created before raising.  This prevents the node from being marked
        # ONLINE with no WireGuard address, which would force all API traffic
        # through the public IP (and fail for HTTP-only nodes).
        if not wg_assigned and not getattr(server, "wg_address", None):
            try:
                from apps.deployments.models_mesh import WireGuardPeer
                fallback_peer = WireGuardPeer.objects.filter(
                    server=server,
                    is_local=False,
                    is_active=True,
                ).order_by("-created_at").first()
                if fallback_peer and fallback_peer.wg_address:
                    server.wg_address = fallback_peer.wg_address
                    server.save(update_fields=["wg_address", "updated_at"])
                    wg_assigned = True
                    _append_log(
                        server,
                        f"VPN mesh wg_address recovered from peer record: {fallback_peer.wg_address}",
                    )
            except Exception:
                pass

        # -- Post-provision health verification ----------------------------------
        # After WireGuard is up, the agent should be able to reach the master DB
        # via the mesh IP.  Verify before marking ONLINE so we don't strand an
        # agent that looks healthy but cannot connect to its database.
        _verify_agent_db_connectivity(ssh, server)

        server.provision_status = ManagedServer.ProvisionStatus.DONE
        server.status = ManagedServer.Status.ONLINE
        server.save(update_fields=update_fields)

        _append_log(server, "✅ Grid provisioning complete!")
        _append_log(server, f"🖥️ Server '{server.name}' is now online at {api_url}")

        # Deploy docker-labels exporter on the newly provisioned node
        try:
            from apps.deployments.services.prometheus_targets import (
                deploy_cadvisor_on_node,
                deploy_docker_labels_exporter_on_node,
                deploy_node_exporter_on_node,
                deploy_promtail_on_node,
                write_docker_labels_targets,
            )
            _append_log(server, "Deploying observability agents...")
            if deploy_docker_labels_exporter_on_node(server):
                _append_log(server, "✓ docker-labels exporter deployed")
            if deploy_promtail_on_node(server):
                _append_log(server, "✓ Promtail deployed")
            if deploy_cadvisor_on_node(server):
                _append_log(server, "✓ cAdvisor deployed")
            else:
                _append_log(server, "⚠ cAdvisor deployment failed (non-critical)")
            if deploy_node_exporter_on_node(server):
                _append_log(server, "✓ Node Exporter deployed")
            else:
                _append_log(server, "⚠ Node Exporter deployment failed (non-critical)")
            write_docker_labels_targets()
        except Exception as exc:
            _append_log(server, f"⚠ observability deployment skipped: {exc}")

        # The token from provisioning may be a DRF session token.
        # Try to exchange it for a long-lived smsly_ API token via the
        # node-token-exchange endpoint on the new server.
        if api_token and not api_token.startswith("smsly_"):
            _append_log(server, "🔄 Attempting auto token exchange for long-lived API token...")
            try:
                # Try credential-based exchange using SSH password
                ssh_password = str(server.ssh_password or "").strip()
                if ssh_password:
                    for username in ("admin", "root"):
                        exchange_url = f"{api_url}/api/v1/auth/node-token-exchange/"
                        resp = requests.post(
                            exchange_url,
                            json={
                                "username": username,
                                "password": ssh_password,
                                "node_name": f"Primary-{server.owner.username}",
                            },
                            timeout=15,
                        )
                        if resp.status_code == 200:
                            new_token = resp.json().get("token")
                            if new_token:
                                server.api_token = new_token
                                server.save(update_fields=["api_token", "updated_at"])
                                _append_log(server, f"✅ Auto-exchanged for smsly_ API token: {new_token[:12]}...")
                                break
            except Exception as exc:
                _append_log(server, f"⚠️ Auto token exchange failed (non-critical): {exc}")

        # -- Step 9: SSH credentials are kept for self-healing --
        # The SSH key is stored encrypted at rest using FIELD_ENCRYPTION_KEY.
        # For maximum security, operators can move FIELD_ENCRYPTION_KEY to a
        # separate file (FIELD_ENCRYPTION_KEY_FILE) with chmod 600 so that
        # a compromised .env does not expose the encryption key.

        if not skip_reboot and _env_bool("SMSLY_PROVISION_REBOOT_ON_SUCCESS", default=True):
            _append_log(server, "Scheduling remote reboot after successful provisioning.")
            if _schedule_remote_reboot(ssh, server, "provisioning"):
                server.status = ManagedServer.Status.UNKNOWN
                server.save(update_fields=["status", "updated_at"])
                _append_log(
                    server,
                    "Remote reboot scheduled. Health check will mark the node online after it returns.",
                )

    except SoftTimeLimitExceeded as exc:
        logger.exception("Provisioning soft-timeout for server %s", server_id)
        resources.rollback()
        server.provision_status = ManagedServer.ProvisionStatus.FAILED
        server.save(update_fields=["provision_status", "updated_at"])
        _append_log(
            server,
            f"\nProvisioning timed out before completion: {exc}",
        )
    except Exception as exc:
        logger.exception("Provisioning failed for server %s", server_id)
        resources.rollback()
        server.provision_status = ManagedServer.ProvisionStatus.FAILED
        server.save(update_fields=["provision_status", "updated_at"])
        _append_log(server, f"\n❌ Provisioning failed: {exc}")
    finally:
        if local_bundle_path and os.path.exists(local_bundle_path):
            with contextlib.suppress(OSError):
                os.remove(local_bundle_path)
        try:
            if ssh is not None:
                ssh.close()
        except Exception:
            pass


@shared_task
def cleanup_stale_server_provisioning():
    """
    Auto-heal stale provisioning rows left behind by interrupted workers.

    This prevents ManagedServer entries from staying in PROVISIONING forever.
    """
    stale_after_seconds = max(3600, PROVISION_TIMEOUT_SECONDS * 2)
    cutoff = timezone.now() - timedelta(seconds=stale_after_seconds)
    stale_servers = ManagedServer.objects.filter(
        provision_status=ManagedServer.ProvisionStatus.PROVISIONING,
        updated_at__lt=cutoff,
    )

    cleaned = 0
    for server in stale_servers:
        server.provision_status = ManagedServer.ProvisionStatus.FAILED
        server.save(update_fields=["provision_status", "updated_at"])
        _append_log(
            server,
            (
                "Provisioning was auto-marked as failed because no updates were "
                f"received for over {stale_after_seconds} seconds."
            ),
        )
        cleaned += 1

    if cleaned:
        logger.warning("Auto-cleaned %d stale provisioning records", cleaned)
    return cleaned
