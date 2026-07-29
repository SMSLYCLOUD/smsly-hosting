import logging
import re
import shlex
import subprocess

from ._utils import _command_text

logger = logging.getLogger(__name__)


class HelpersMixin:

    @classmethod
    def _fetch_server_wg_public_key(cls, server) -> str | None:
        if not (getattr(server, "ssh_key", "") or getattr(server, "ssh_password", "")):
            return None
        try:
            from apps.deployments.services.ssh_client import SSHClient

            ssh = SSHClient(
                host=server.host,
                port=server.ssh_port,
                username=server.ssh_user,
                password=server.ssh_password,
                private_key=server.ssh_key,
            )
            ssh.connect()
            out, _err, code = ssh.exec_command(
                "cat /etc/wireguard/public.key 2>/dev/null || true",
                timeout=15,
                raise_on_error=False,
            )
            ssh.close()
            key = (out or "").strip()
            if key and len(key) == 44 and key.endswith("="):
                return key
        except Exception as exc:
            logger.debug("Could not fetch WG public key from %s: %s", server.host, exc)
        return None

    @classmethod
    def _ensure_peer_on_node_via_ssh(
        cls,
        server,
        peer_pubkey: str,
        peer_wg_ip: str,
        peer_endpoint: str,
    ) -> bool:
        if not (getattr(server, "ssh_key", "") or getattr(server, "ssh_password", "")):
            return False
        try:
            from apps.deployments.services.ssh_client import SSHClient

            ssh = SSHClient(
                host=server.host,
                port=server.ssh_port,
                username=server.ssh_user,
                password=server.ssh_password,
                private_key=server.ssh_key,
            )
            ssh.connect()

            _pk = shlex.quote(peer_pubkey)
            _ep = shlex.quote(peer_endpoint)
            _ip = shlex.quote(peer_wg_ip)
            add_live = (
                f"wg set wg0 peer {_pk} "
                f"endpoint {_ep} "
                f"allowed-ips {_ip}/32 "
                "persistent-keepalive 25"
            )
            ssh.exec_command(add_live, timeout=15, raise_on_error=False)

            persist = (
                f"grep -q '^PublicKey = {_pk}$' /etc/wireguard/wg0.conf 2>/dev/null "
                f"|| printf '\\n[Peer]\\nPublicKey = {_pk}\\n"
                f"AllowedIPs = {_ip}/32\\n"
                f"Endpoint = {_ep}\\n"
                f"PersistentKeepalive = 25\\n' >> /etc/wireguard/wg0.conf"
            )
            ssh.exec_command(persist, timeout=15, raise_on_error=False)

            ssh.close()
            logger.info(
                "Added peer %s (%s) to node %s via SSH",
                peer_pubkey[:16], peer_wg_ip, server.host,
            )
            return True
        except Exception as exc:
            logger.warning(
                "Could not add peer to node %s via SSH: %s", server.host, exc,
            )
            return False

    @staticmethod
    def _ssh_run(server, command: str, timeout: int = 60) -> str:
        from apps.deployments.services.ssh_client import SSHClient

        if not (getattr(server, "ssh_key", "") or getattr(server, "ssh_password", "")):
            raise ValueError(f"Server '{server.name}' has no SSH credentials configured.")

        ssh = SSHClient(
            host=server.host,
            port=server.ssh_port,
            username=server.ssh_user,
            password=server.ssh_password,
            private_key=server.ssh_key,
            wg_address=getattr(server, "wg_address", None),
        )
        try:
            ssh.connect()
            output = ssh.exec_command(command, timeout=timeout)
            return _command_text(output)
        finally:
            ssh.close()

    @staticmethod
    def _ping(ip: str, count: int = 3) -> float:
        result = subprocess.run(
            ["ping", "-c", str(count), "-W", "2", ip],
            capture_output=True, text=True, check=True,
        )
        match = re.search(r"rtt .+ = [\d.]+/([\d.]+)/", result.stdout)
        if match:
            return float(match.group(1))
        raise ValueError(f"Could not parse ping output for {ip}")

    @staticmethod
    def _detect_local_endpoint(port: int) -> str:
        import requests as req
        try:
            from apps.deployments.models.core import PlatformConfig
            config = PlatformConfig.load()
            configured_ip = str(getattr(config, "server_ip", "") or "").strip()
            if configured_ip:
                return f"{configured_ip}:{port}"
        except Exception as exc:
            logger.debug("Failed to load PlatformConfig for WireGuard endpoint: %s", exc)
        import os
        env_ip = str(os.environ.get("PUBLIC_IP", "") or "").strip()
        if env_ip:
            return f"{env_ip}:{port}"
        for url in [
            "https://api.ipify.org",
            "https://ifconfig.me/ip",
            "https://ipv4.icanhazip.com",
        ]:
            try:
                resp = req.get(url, timeout=5)
                ip = resp.text.strip()
                if ip:
                    return f"{ip}:{port}"
            except Exception:
                continue
        return ""
