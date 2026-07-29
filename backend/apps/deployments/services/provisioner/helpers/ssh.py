import io
import logging
import os
import shlex

import paramiko

from apps.deployments.models.servers import ManagedServer

from .logging import _append_log
from .server_config import _get_master_mesh_ip

logger = logging.getLogger(__name__)


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
