import contextlib
import io
import logging
import os
import shlex
import time

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
        "auth_timeout": 30,
        # Never consult the local agent or ~/.ssh keys: auth must come
        # only from the server record, otherwise provisioning
        # nondeterministically succeeds with the operator's key.
        "look_for_keys": False,
        "allow_agent": False,
    }

    if server.ssh_key:
        key_file = io.StringIO(server.ssh_key)
        passphrase = getattr(server, "ssh_key_passphrase", "") or None
        pkey: paramiko.PKey | None = None
        for _loader in (
            paramiko.RSAKey.from_private_key,
            paramiko.Ed25519Key.from_private_key,
            paramiko.ECDSAKey.from_private_key,
        ):
            try:
                key_file.seek(0)
                pkey = _loader(key_file, password=passphrase)
                break
            except paramiko.SSHException:
                continue
            except (ValueError, Exception):
                # binascii/malformed-key errors are not SSHException;
                # try the next format instead of aborting the chain.
                continue
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

    last_exc: Exception | None = None
    for _attempt in range(3):
        try:
            client.connect(**connect_kwargs)
            break
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
                break
            raise
        except (OSError, EOFError, paramiko.SSHException) as exc:
            # Transient network/banner failures: retry with backoff.
            last_exc = exc
            logger.warning(
                "SSH connect to %s failed (attempt %d/3): %s",
                server.host, _attempt + 1, exc,
            )
            time.sleep(2 * (_attempt + 1))
    else:
        raise last_exc  # type: ignore[misc]
    try:
        _transport = client.get_transport()
        if _transport is not None and _transport.is_active():
            _transport.set_keepalive(30)
    except Exception:
        pass
    return client


def _generate_ed25519_keypair() -> tuple[str, str]:
    """Generate a fresh Ed25519 keypair.

    Returns ``(private_key_pem, public_key_line)`` where the private key is
    OpenSSH-format PEM and the public key is a single ``ssh-ed25519 ...`` line
    (no trailing newline) suitable for ``authorized_keys``.
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    private_key = ed25519.Ed25519PrivateKey.generate()

    priv_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption()
    ).decode("utf-8")

    pub_key_line = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH
    ).decode("utf-8").strip()

    return priv_key_pem, pub_key_line


def _restrict_ssh_key_to_master_ip(ssh, server: ManagedServer) -> None:
    master_ip = os.environ.get("PUBLIC_IP") or "127.0.0.1"
    mesh_ip = _get_master_mesh_ip()
    allowed_ips = f"{master_ip},{mesh_ip}" if mesh_ip else master_ip

    priv_key_pem, pub_key_line = _generate_ed25519_keypair()

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
        # Stash the operator-supplied key BEFORE overwriting: rollback
        # restores this backup instead of blanking ssh_key (blanking
        # strands key-only hosts with no way to retry).
        try:
            _meta = dict(getattr(server, "provider_metadata", None) or {})
            if server.ssh_key and not _meta.get("ssh_key_backup"):
                _meta["ssh_key_backup"] = server.ssh_key
                server.provider_metadata = _meta
        except Exception:
            pass
        server.ssh_key = priv_key_pem
        server.save(update_fields=['ssh_key', 'provider_metadata', 'updated_at'])
        _append_log(server, f"🔒 IP-restricted SSH key added (from=\"{allowed_ips}\")")
    except Exception as exc:
        _append_log(server, f"⚠ IP-restricted SSH key skipped: {exc}")


def _harden_node_ssh(ssh, server: ManagedServer) -> None:
    """Verify the IP-restricted key works, but do NOT clear the password yet.

    Password clearing is deferred to provisioning success to avoid locking
    ourselves out if any subsequent step needs password-based sudo.
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

    try:
        import paramiko as _paramiko
        test_ssh = _paramiko.SSHClient()
        from apps.deployments.services.ssh_client import _get_tofu_policy
        test_ssh.set_missing_host_key_policy(_get_tofu_policy(server.host, server.ssh_port))
        pkey = None
        _key_file = io.StringIO(server.ssh_key)
        _passphrase = getattr(server, "ssh_key_passphrase", "") or None
        for _loader in (
            _paramiko.Ed25519Key.from_private_key,
            _paramiko.RSAKey.from_private_key,
            _paramiko.ECDSAKey.from_private_key,
        ):
            try:
                _key_file.seek(0)
                pkey = _loader(_key_file, password=_passphrase)
                break
            except Exception:
                continue
        if pkey is None:
            _append_log(server, "⚠ SSH cleanup skipped: restricted key has unknown format")
            return
        test_ssh.connect(
            hostname=server.host,
            port=server.ssh_port,
            username=server.ssh_user,
            pkey=pkey,
            timeout=10,
        )
        test_ssh.close()
        _append_log(server, "🔒 IP-restricted SSH key verified working")
    except Exception as exc:
        _append_log(server, f"⚠ SSH cleanup skipped: test connection using restricted key failed: {exc}")
        return


def _clear_ssh_password_after_success(server: ManagedServer) -> None:
    """Clear the SSH password from DB after provisioning succeeds.

    Called only on the success path — not during setup — so that
    password-based auth remains available if any step needs it.
    """
    if server.ssh_password:
        server.ssh_password = ""
        server.save(update_fields=['ssh_password', 'updated_at'])
        _append_log(server, "🔒 SSH password cleared from record (key-only auth)")


def _clear_ssh_key_backup_after_success(server: ManagedServer) -> None:
    """Delete the stashed operator key backup after a successful run.

    The backup exists so rollback can restore the operator's original
    key after a FAILURE. Once provisioning succeeds the record holds
    the generated restricted key and the backup is stale key material
    that must not accumulate in provider_metadata.
    """
    try:
        _meta = dict(getattr(server, "provider_metadata", None) or {})
        if _meta.pop("ssh_key_backup", None) is not None:
            server.provider_metadata = _meta
            server.save(update_fields=["provider_metadata", "updated_at"])
            _append_log(server, "🔒 Stale operator key backup cleared from record")
    except Exception as exc:
        logger.debug("Failed to clear ssh_key_backup: %s", exc)


def _schedule_remote_reboot(ssh, server: ManagedServer, reason: str) -> bool:
    command = (
        "if [ \"$(id -u)\" -eq 0 ]; then "
        "(nohup sh -c 'sleep 8; /sbin/reboot || reboot' >/dev/null 2>&1 &); "
        "else "
        "(nohup sh -c 'sleep 8; sudo -n /sbin/reboot || sudo -n reboot' >/dev/null 2>&1 &); "
        "fi"
    )
    try:
        stdin, stdout, stderr = ssh.exec_command(command)
        # Close channel immediately — the reboot runs in the background via nohup.
        # Leaving channels open risks hanging if the connection drops mid-reboot.
        stdin.close()
        with contextlib.suppress(Exception):
            stdout.channel.close()
            stderr.channel.close()
        logger.info("Scheduled remote reboot for %s after %s", server.host, reason)
        return True
    except Exception as exc:
        logger.warning("Failed to schedule remote reboot for %s: %s", server.host, exc)
        return False
