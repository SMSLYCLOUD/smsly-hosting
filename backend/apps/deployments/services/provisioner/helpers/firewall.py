import contextlib
import ipaddress
import logging
import subprocess

from apps.deployments.models.servers import ManagedServer

from .env import _env_bool
from .logging import _append_log

logger = logging.getLogger(__name__)


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
