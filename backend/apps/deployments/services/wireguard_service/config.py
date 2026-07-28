import logging
import shlex
import textwrap

logger = logging.getLogger(__name__)


class ConfigMixin:

    @classmethod
    def build_wg_config(cls, peer) -> str:
        from apps.deployments.models.mesh import WireGuardPeer

        if not peer.private_key:
            raise ValueError(
                f"Cannot build config for {peer}: private key is empty "
                "(server-managed key — config is not stored on master)"
            )

        mesh = peer.mesh
        other_peers = WireGuardPeer.objects.filter(
            mesh=mesh, is_active=True,
        ).exclude(id=peer.id)

        if peer.private_key:
            config = textwrap.dedent(f"""\
                [Interface]
                PrivateKey = {peer.private_key}
                Address = {peer.wg_address}/24
                ListenPort = {mesh.listen_port}
                # SaveConfig = false
            """)
        else:
            config = textwrap.dedent(f"""\
                [Interface]
                Address = {peer.wg_address}/24
                ListenPort = {mesh.listen_port}
                # SaveConfig = false
                # PrivateKey is server-managed (not stored on master)
            """)

        config += textwrap.dedent(f"""\
            PostUp = sysctl -w net.ipv4.conf.%i.rp_filter=2 net.ipv4.conf.all.rp_filter=2; iptables -A INPUT -p udp --dport {mesh.listen_port} -j ACCEPT
            PostDown = iptables -D INPUT -p udp --dport {mesh.listen_port} -j ACCEPT; sysctl -w net.ipv4.conf.all.rp_filter=1
        """)

        for other in other_peers:
            peer_section = textwrap.dedent(f"""\

                [Peer]
                # {other.server.name if other.server else 'local'}
                PublicKey = {other.public_key}
                AllowedIPs = {other.wg_address}/32
            """)
            if other.endpoint:
                peer_section += f"    Endpoint = {cls.validate_endpoint(other.endpoint)}\n"
            peer_section += "    PersistentKeepalive = 25\n"
            config += peer_section

        return config.strip() + "\n"

    @classmethod
    def deploy_config(cls, peer):
        if not peer.private_key:
            logger.info(
                "Skipping config deploy to %s (private key is server-managed)",
                peer,
            )
            return

        config = cls.build_wg_config(peer)
        mesh = peer.mesh
        iface = cls.validate_interface_name(mesh.interface_name)
        cls.validate_wg_config(config)

        if peer.is_local:
            cls._deploy_local(config, iface)
        elif peer.server:
            cls._deploy_remote(peer.server, config, iface)
        else:
            raise ValueError("Peer has no server and is not local")

        logger.info(f"Deployed WG config to {peer}")

    @classmethod
    def _deploy_local(cls, config: str, iface: str):
        import os

        import docker
        iface = cls.validate_interface_name(iface)
        cls.validate_wg_config(config)
        client = docker.from_env()
        docker_host = os.environ.get("DOCKER_HOST", "tcp://socket-proxy:2375")

        safe_iface = shlex.quote(iface)
        import base64
        b64_config = base64.b64encode(config.encode()).decode()
        cmd = f"mkdir -p /etc/wireguard && echo '{b64_config}' | base64 -d > /etc/wireguard/{safe_iface}.conf && chmod 600 /etc/wireguard/{safe_iface}.conf"

        try:
            client.containers.run(
                "alpine",
                command=["sh", "-c", cmd],
                remove=True,
                environment={"DOCKER_HOST": docker_host},
                volumes={"/etc/wireguard": {"bind": "/etc/wireguard", "mode": "rw"}},
            )
        except Exception as e:
            logger.error(f"Failed to write host WG config via Docker: {e}")
            raise RuntimeError(f"Local config write failed: {e}. Check if socket-proxy allows volume mounts.")

        safe_iface = shlex.quote(iface)
        commands = [
            "apk add wireguard-tools iptables >/dev/null 2>&1 || true",
            "grep -q wireguard /proc/modules || (echo 'WIREGUARD_MODULE_MISSING' && exit 1)",
            f"wg-quick down {safe_iface} >/dev/null 2>&1 || true",
            f"wg-quick up {safe_iface}"
        ]
        try:
            client.containers.run(
                "alpine",
                command=["sh", "-c", " && ".join(commands)],
                remove=True,
                privileged=True,
                network_mode="host",
                environment={"DOCKER_HOST": docker_host},
                volumes={
                    "/lib/modules": {"bind": "/lib/modules", "mode": "ro"},
                    "/etc/wireguard": {"bind": "/etc/wireguard", "mode": "ro"},
                },
            )
        except Exception as e:
            if "WIREGUARD_MODULE_MISSING" in str(e):
                 raise RuntimeError("WireGuard kernel module is not loaded on the host VPS. Run 'sudo modprobe wireguard' on the host.")
            logger.error(f"Failed to restart host WG via Docker: {e}")
            raise RuntimeError(f"Local wg-quick failed: {e}. Ensure socket-proxy allows privileged:true.")

    @classmethod
    def _deploy_remote(cls, server, config: str, iface: str):
        iface = cls.validate_interface_name(iface)
        cls.validate_wg_config(config)
        safe_iface = shlex.quote(iface)
        import base64
        b64_config = base64.b64encode(config.encode()).decode()
        commands = [
            "set -e",
            "if [ \"$(id -u)\" -eq 0 ]; then SUDO=''; else SUDO='sudo -n'; fi",
            "if command -v apt-get >/dev/null 2>&1; then "
            "$SUDO apt-get update >/dev/null 2>&1 || true; "
            "$SUDO apt-get install -y wireguard iptables >/dev/null 2>&1 || true; "
            "elif command -v yum >/dev/null 2>&1; then "
            "$SUDO yum install -y wireguard-tools iptables >/dev/null 2>&1 || true; "
            "fi",
            "command -v wg-quick >/dev/null 2>&1 || { echo 'wg-quick is not installed' >&2; exit 41; }",
            "$SUDO mkdir -p /etc/wireguard",
            (
                f"tmp=$($SUDO mktemp /etc/wireguard/{safe_iface}.conf.tmp.XXXXXX) && "
                f"printf %s {shlex.quote(b64_config)} | base64 -d | $SUDO tee \"$tmp\" >/dev/null && "
                "$SUDO chmod 600 \"$tmp\" && "
                f"$SUDO mv \"$tmp\" /etc/wireguard/{safe_iface}.conf"
            ),
            "$SUDO modprobe wireguard 2>/dev/null || true",
            f"$SUDO wg-quick down {safe_iface} >/dev/null 2>&1 || true",
            f"$SUDO wg-quick up {safe_iface}",
            f"$SUDO wg show {safe_iface} >/dev/null",
            f"$SUDO systemctl enable wg-quick@{safe_iface} >/dev/null 2>&1 || true",
        ]
        cls._ssh_run(server, " && ".join(commands), timeout=180)
