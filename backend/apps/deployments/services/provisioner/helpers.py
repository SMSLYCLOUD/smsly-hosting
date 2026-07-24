"""Standalone helper functions for server provisioning."""

import contextlib
import hashlib
import io
import ipaddress
import logging
import os
import re
import secrets
import shlex
import subprocess
import uuid
from urllib.parse import urlparse

import paramiko
import requests
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.utils import timezone

from apps.deployments.models.servers import ManagedServer
from apps.deployments.utils import build_local_source_bundle as utils_build_bundle
from apps.deployments.utils import get_source_root_dir as utils_get_source_root

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
    text = logs or ""
    if "INSTALLATION FAILED" in text:
        return False
    return bool(
        "INSTALLATION SUCCESSFUL!" in text
        or re.search(r"All\s+\d+/\d+\s+verification checks passed", text)
    )


def _shell_env_assignments(values: dict[str, object]) -> str:
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
    env_pubkey = os.environ.get("MASTER_WG_PUBKEY")
    if env_pubkey:
        return env_pubkey.strip()
    try:
        pub_key_path = "/etc/wireguard/public.key"
        if os.path.exists(pub_key_path):
            with open(pub_key_path) as f:
                return f.read().strip()
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["cat", "/etc/wireguard/public.key"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def build_agent_lite_install_env(
    server: ManagedServer,
    master_ip: str | None = None,
) -> tuple[dict[str, str], list[str]]:
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


def _restrict_ssh_key_to_master_ip(ssh, server: ManagedServer) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    master_ip = os.environ.get("PUBLIC_IP") or "127.0.0.1"
    mesh_ip = _get_master_mesh_ip()
    allowed_ips = f"{master_ip},{mesh_ip}" if mesh_ip else master_ip

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

    pub_key_line = pub_key_bytes.strip()

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
        server.ssh_key = priv_key_pem
        server.save(update_fields=['ssh_key', 'updated_at'])
        _append_log(server, f"🔒 IP-restricted SSH key added (from=\"{allowed_ips}\")")
    except Exception as exc:
        _append_log(server, f"⚠ IP-restricted SSH key skipped: {exc}")


def _harden_node_ssh(ssh, server: ManagedServer) -> None:
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

    try:
        import paramiko as _paramiko
        test_ssh = _paramiko.SSHClient()
        from apps.deployments.services.ssh_client import _get_tofu_policy
        test_ssh.set_missing_host_key_policy(_get_tofu_policy(server.host, server.ssh_port))
        pkey = _paramiko.Ed25519Key.from_private_key(io.StringIO(server.ssh_key))
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

    if server.ssh_password:
        server.ssh_password = ""
        server.save(update_fields=['ssh_password', 'updated_at'])
        _append_log(server, "🔒 SSH password cleared from record (key-only auth)")


def _schedule_remote_reboot(ssh, server: ManagedServer, reason: str) -> bool:
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
    if not server.host:
        return

    try:
        validated_ip = str(ipaddress.ip_address(server.host))
    except ValueError:
        logger.warning(
            "Skipping firewall hardening: invalid IP %s", server.host
        )
        return

    _append_log(server, f"🛡️ Hardening Master firewall for Node IP: {validated_ip}...")

    if getattr(server, "is_lite_agent", False):
        # Agent-lite uses the master's PostgreSQL (via WireGuard mesh) but runs
        # local Redis and RabbitMQ — only open the DB port.
        for port in ("5432",):
            with contextlib.suppress(Exception):
                subprocess.run(
                    ["ufw", "allow", "from", validated_ip,
                     "to", "any", "port", port, "proto", "tcp"],
                    capture_output=True, timeout=5,
                )

    subprocess.run(
        ["iptables", "-N", "DOCKER-USER"],
        capture_output=True, timeout=5,
    )

    try:
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
    return utils_get_source_root()


def _build_local_source_bundle() -> str:
    return utils_build_bundle()


def _load_install_script():
    required_sha = os.environ.get("SMSLY_INSTALL_SCRIPT_SHA256", "").strip()

    candidates = [
        "/app/install.sh",
        os.path.abspath(
            os.path.join(os.path.dirname(__file__), "../../../../install.sh")
        ),
        os.path.abspath(os.path.join(os.getcwd(), "install.sh")),
    ]

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
                        _start = "--- BEGIN_LIB_SOURCING ---"
                        _end = "--- END_LIB_SOURCING ---"
                        _s = content.find(_start)
                        _e = content.find(_end)
                        if _s != -1 and _e != -1 and _e > _s:
                            content = (
                                content[:_s]
                                + "\n" + inline_block + "\n"
                                + content[_e + len(_end) + 1:]
                            )
                        else:
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
        pass


def _registry_login_commands(server: ManagedServer) -> str:
    commands = []
    registries = server.registry_access.filter(is_active=True).select_related("content_type")
    for reg in registries:
        url = (reg.registry_url or "").strip()
        if not url:
            continue
        user = (reg.username or "").strip()
        pwd = (reg.password or "").strip()
        if user and pwd:
            safe_user = shlex.quote(user)
            safe_pwd = shlex.quote(pwd)
            safe_url = shlex.quote(url)
            commands.append(
                f"printf '%s\\n' {safe_pwd} | docker login --username {safe_user} "
                f"--password-stdin {safe_url} 2>/dev/null || true"
            )
    if commands:
        return " && ".join(commands)
    return "true"


def _append_log(server: ManagedServer, line: str):
    timestamp = timezone.now().strftime("%Y-%m-%d %H:%M:%S")
    correlation_id = getattr(server, "_provision_correlation_id", None)
    if not correlation_id:
        correlation_id = str(uuid.uuid4())[:8]
        server._provision_correlation_id = correlation_id
    line = re.sub(r'(?i)(password|passwd|secret|token|key)\s*[=:]\s*\S+', r'\1=[REDACTED]', line)
    line = re.sub(r'([A-Za-z0-9+/=]{40,})', r'[REDACTED]', line)
    line = re.sub(r'([0-9a-f]{32,})', r'[REDACTED]', line)
    formatted_line = f"[{timestamp}] [tx:{correlation_id}] {line}"
    server.provision_logs += formatted_line + "\n"
    server.save(update_fields=["provision_logs", "updated_at"])
    _broadcast_provision_log(server, formatted_line)


def _get_ssh_client(server: ManagedServer) -> paramiko.SSHClient:
    client = paramiko.SSHClient()

    strict_mode = str(os.environ.get("SMSLY_STRICT_SSH_HOST_KEY_CHECK", "false")).lower() not in ("false", "0", "no")
    allow_auto_add = str(os.environ.get("ALLOW_SSH_AUTOADD", "false")).lower() in ("true", "1", "yes")

    if strict_mode and not allow_auto_add:
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    elif allow_auto_add:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    else:
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
            connect_kwargs["password"] = server.ssh_password
        else:
            raise ValueError("SSH key is present but could not be parsed, and no password fallback available.")
    elif server.ssh_password:
        connect_kwargs["password"] = server.ssh_password
    else:
        raise ValueError("No SSH credentials provided (need password or key)")

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
    master_db_url = os.environ.get("DATABASE_URL")
    if not master_db_url:
        return None, None

    node_id_short = str(server.id).split('-')[0]
    username = f"node_agent_{node_id_short}"

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
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (username,))
            user_exists = bool(cur.fetchone())

            if not user_exists:
                cur.execute(sql.SQL("CREATE USER {} WITH PASSWORD %s").format(sql.Identifier(username)), (password,))
                is_new_user = True
            elif not existing_pass:
                cur.execute(sql.SQL("ALTER USER {} WITH PASSWORD %s").format(sql.Identifier(username)), (password,))
                is_new_user = True

            parsed = urlparse(master_db_url)
            db_name = parsed.path.lstrip('/')

            cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(db_name), sql.Identifier(username)
            ))
            cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(username)))
            cur.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(sql.Identifier(username)))
            cur.execute(sql.SQL("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(username)))
            cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}").format(sql.Identifier(username)))
            cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE ON SEQUENCES TO {}").format(sql.Identifier(username)))

        logger.info("Provisioned Master DB credentials for node %s: %s (new_user=%s)", server.name, username, is_new_user)
        if not isinstance(server.provider_metadata, dict):
            server.provider_metadata = {}
        server.provider_metadata["node_db_user"] = username
        server.node_db_password = password
        server.provider_metadata.pop("node_db_password", None)
        server.save(update_fields=["provider_metadata", "node_db_password"])

        _rerender_pgcat_config()

        return username, password
    except Exception as e:
        logger.error("Failed to create node DB credentials for %s: %s", server.name, e)
        return None, None


def _restart_pgcat():
    import time as _time_mod

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
        deadline = _time_mod.monotonic() + timeout
        while _time_mod.monotonic() < deadline:
            try:
                container.reload()
                health = container.attrs.get("State", {}).get("Health", {}).get("Status")
                if health == "healthy":
                    logger.info("PgCat %s is healthy.", name)
                    return True
            except Exception:
                pass
            _time_mod.sleep(2)
        return False

    container, pgcat_name = _find_pgcat_container()
    if not container:
        logger.warning(
            "PgCat container not found — node agent pools will not be "
            "active until the next PgCat restart."
        )
        return

    try:
        exit_code, output = container.exec_run(
            ["python3", "/app/render_pgcat_config.py", "/tmp/pgcat.toml"],
            demux=True,
        )
        if exit_code == 0:
            container.exec_run(
                ["sh", "-c", "cp /tmp/pgcat.toml /etc/pgcat/pgcat.toml && kill -HUP 1"],
            )
            _time_mod.sleep(2)
            logger.info("PgCat hot-reloaded config via docker exec + SIGHUP.")
            return
        logger.warning("PgCat render exited %d: %s", exit_code, (output or b"").decode(errors="replace"))
    except Exception as exc:
        logger.info("PgCat hot-reload failed (%s), falling back to restart.", exc)

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
            _time_mod.sleep(5 * (attempt + 1))

    logger.warning(
        "PgCat %s did not become healthy after 3 restart attempts. "
        "Node agent pools may not be active.",
        pgcat_name,
    )


def _rerender_pgcat_config():
    import time as _time_mod
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
            _time_mod.sleep(1)
            logger.info("PgCat config re-rendered and reloaded via SIGHUP.")
        else:
            logger.warning("PgCat render failed (exit %d): %s", exit_code, (output or b"").decode(errors="replace"))
            _restart_pgcat()
    except Exception as exc:
        logger.warning("PgCat re-render failed (%s), falling back to restart.", exc)
        _restart_pgcat()


def _verify_agent_db_connectivity(ssh, server: ManagedServer, start_time: float):
    if server_install_mode(server) != "agent-lite":
        return

    _append_log(server, "Verifying agent DB connectivity via health endpoint...")
    deadline = start_time + 120
    import time as _time_mod
    while _time_mod.monotonic() < deadline:
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
        _time_mod.sleep(5)

    _append_log(
        server,
        "Agent health endpoint did not report healthy within the wait window. "
        "The node will be marked ONLINE but may require a manual restart if "
        "the database connection does not recover on its own.",
    )
