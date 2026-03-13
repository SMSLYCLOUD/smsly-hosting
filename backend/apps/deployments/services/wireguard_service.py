"""
WireGuard VPN Mesh service.

Handles key generation, config rendering, and SSH deployment
of WireGuard configurations across the server fleet.
"""

import logging
import subprocess
import textwrap

from django.utils import timezone

logger = logging.getLogger(__name__)


class WireGuardService:
    """Manage WireGuard mesh network across CloudNeuron servers."""

    # ── Key Generation ───────────────────────────────────────────────────

    @staticmethod
    def generate_keypair() -> tuple[str, str]:
        """
        Generate a WireGuard private/public key pair (pure Python).
        Returns (private_key, public_key).
        """
        return WireGuardService._generate_keypair_python()

    @staticmethod
    def _generate_keypair_python() -> tuple[str, str]:
        """Generate WireGuard keys using Python cryptography (X25519)."""
        import base64
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        from cryptography.hazmat.primitives import serialization

        private_key = X25519PrivateKey.generate()
        private_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return (
            base64.b64encode(private_bytes).decode(),
            base64.b64encode(public_bytes).decode(),
        )

    # ── Config Rendering ─────────────────────────────────────────────────

    @staticmethod
    def build_wg_config(peer) -> str:
        """
        Build a complete wg0.conf for a specific peer.

        The config includes the [Interface] section for this peer
        and [Peer] sections for all OTHER peers in the same mesh.
        """
        from apps.deployments.models_mesh import WireGuardPeer

        mesh = peer.mesh
        other_peers = WireGuardPeer.objects.filter(
            mesh=mesh, is_active=True,
        ).exclude(id=peer.id)

        # Interface section
        config = textwrap.dedent(f"""\
            [Interface]
            PrivateKey = {peer.private_key}
            Address = {peer.wg_address}/24
            ListenPort = {mesh.listen_port}
            # SaveConfig = false
        """)

        # PostUp/PostDown for firewall rules
        config += textwrap.dedent(f"""\
            PostUp = iptables -A INPUT -p udp --dport {mesh.listen_port} -j ACCEPT
            PostDown = iptables -D INPUT -p udp --dport {mesh.listen_port} -j ACCEPT
        """)

        # Peer sections
        for other in other_peers:
            peer_section = textwrap.dedent(f"""\

                [Peer]
                # {other.server.name if other.server else 'local'}
                PublicKey = {other.public_key}
                AllowedIPs = {other.wg_address}/32
            """)
            if other.endpoint:
                peer_section += f"    Endpoint = {other.endpoint}\n"
            # Keep-alive to maintain NAT mappings
            peer_section += "    PersistentKeepalive = 25\n"
            config += peer_section

        return config.strip() + "\n"

    # ── Peer Management ──────────────────────────────────────────────────

    @classmethod
    def add_peer_to_mesh(cls, mesh, server=None, is_local=False):
        """
        Add a new peer to the mesh network.

        1. Generate keypair
        2. Assign next available IP
        3. Create WireGuardPeer record
        4. Update configs on all existing peers (SSH deploy)

        Args:
            mesh: MeshNetwork instance
            server: ManagedServer instance (None for local server)
            is_local: True if this is the local (this) server

        Returns:
            WireGuardPeer instance
        """
        from apps.deployments.models_mesh import WireGuardPeer

        # Check if peer already exists
        existing = WireGuardPeer.objects.filter(
            mesh=mesh, server=server, is_local=is_local,
        ).first()
        if existing:
            logger.info(f"Peer already exists for server {server or 'local'}")
            return existing

        # Generate keys and assign IP
        private_key, public_key = cls.generate_keypair()
        wg_address = mesh.next_available_ip()

        # Build endpoint
        endpoint = ""
        if server:
            endpoint = f"{server.host}:{mesh.listen_port}"
        elif is_local:
            # For local server, try to detect public IP
            endpoint = cls._detect_local_endpoint(mesh.listen_port)

        peer = WireGuardPeer.objects.create(
            mesh=mesh,
            server=server,
            private_key=private_key,
            public_key=public_key,
            wg_address=wg_address,
            endpoint=endpoint,
            allowed_ips=f"{wg_address}/32",
            is_active=True,
            is_local=is_local,
        )

        logger.info(
            f"Added peer {peer.wg_address} to mesh {mesh.name} "
            f"(server: {server or 'local'})"
        )
        return peer

    @classmethod
    def remove_peer_from_mesh(cls, peer):
        """
        Remove a peer from the mesh.

        1. SSH into the target and tear down WireGuard
        2. Remove peer from all other configs
        3. Delete the WireGuardPeer record
        """
        mesh = peer.mesh
        server = peer.server

        # Tear down WireGuard on the target server
        if server and not peer.is_local:
            try:
                cls._ssh_run(server, f"wg-quick down {mesh.interface_name} || true")
            except Exception as e:
                logger.warning(f"Failed to tear down WG on {server}: {e}")

        # Delete the peer record
        peer.delete()

        # Update configs on all remaining peers
        remaining_peers = mesh.peers.filter(is_active=True)
        for p in remaining_peers:
            try:
                cls.deploy_config(p)
            except Exception as e:
                logger.warning(f"Failed to update config on {p}: {e}")

    # ── SSH Deployment ───────────────────────────────────────────────────

    @classmethod
    def deploy_config(cls, peer):
        """
        Deploy WireGuard configuration to a peer's server via SSH.

        1. Generate the config
        2. SSH into the server
        3. Write /etc/wireguard/wg0.conf
        4. Restart WireGuard interface
        """
        config = cls.build_wg_config(peer)
        mesh = peer.mesh
        iface = mesh.interface_name

        if peer.is_local:
            # Local deployment — write directly
            cls._deploy_local(config, iface)
        elif peer.server:
            # Remote deployment via SSH
            cls._deploy_remote(peer.server, config, iface)
        else:
            raise ValueError("Peer has no server and is not local")

        logger.info(f"Deployed WG config to {peer}")

    @classmethod
    def _deploy_local(cls, config: str, iface: str):
        """
        Deploy WireGuard config on the local server.
        Since the API runs unprivileged, we use the Docker socket to spin up
        an ephemeral privileged container to apply the config to the host network.
        """
        import docker
        client = docker.from_env()

        # Write config to a temporary file via a container
        # Since /etc/wireguard might not be mounted in the backend, we run an alpine 
        # container mounting /etc/wireguard from the host.
        cmd = f"mkdir -p /etc/wireguard && cat > /etc/wireguard/{iface}.conf << 'WG_CONF_EOF'\n{config}\nWG_CONF_EOF\nchmod 600 /etc/wireguard/{iface}.conf"
        
        try:
            client.containers.run(
                "alpine",
                command=["sh", "-c", cmd],
                remove=True,
                volumes={"/etc/wireguard": {"bind": "/etc/wireguard", "mode": "rw"}},
            )
        except Exception as e:
            logger.error(f"Failed to write host WG config via Docker: {e}")
            raise RuntimeError(f"Local config write failed: {e}")

        # Restart WireGuard interface
        commands = [
            # Ensure it's installed (if Debian/Ubuntu host system underneath)
            "apk add wireguard-tools iptables >/dev/null 2>&1 || true",
            f"wg-quick down {iface} >/dev/null 2>&1 || true",
            f"wg-quick up {iface}"
        ]
        try:
            client.containers.run(
                "alpine",
                command=["sh", "-c", " && ".join(commands)],
                remove=True,
                privileged=True,
                network_mode="host",
                volumes={
                    "/lib/modules": {"bind": "/lib/modules", "mode": "ro"},
                    "/etc/wireguard": {"bind": "/etc/wireguard", "mode": "ro"},
                },
            )
        except Exception as e:
            logger.error(f"Failed to restart host WG via Docker: {e}")
            raise RuntimeError(f"Local wg-quick failed: {e}")

    @classmethod
    def _deploy_remote(cls, server, config: str, iface: str):
        """Deploy WireGuard config on a remote server via SSH."""
        commands = [
            # Ensure WireGuard is installed
            "apt-get install -y wireguard > /dev/null 2>&1 || true",
            "mkdir -p /etc/wireguard",
            # Write config (using heredoc)
            f"cat > /etc/wireguard/{iface}.conf << 'WG_CONF_EOF'\n{config}\nWG_CONF_EOF",
            f"chmod 600 /etc/wireguard/{iface}.conf",
            # Restart interface
            f"wg-quick down {iface} 2>/dev/null || true",
            f"wg-quick up {iface}",
            # Enable on boot
            f"systemctl enable wg-quick@{iface} 2>/dev/null || true",
        ]
        cls._ssh_run(server, " && ".join(commands))

    # ── Mesh Operations ──────────────────────────────────────────────────

    @classmethod
    def deploy_full_mesh(cls, mesh):
        """
        Deploy WireGuard configs to ALL peers in a mesh.

        Call this after adding/removing a peer to update everyone's config.
        """
        peers = mesh.peers.filter(is_active=True)
        results = {"success": [], "failed": []}

        for peer in peers:
            try:
                cls.deploy_config(peer)
                results["success"].append(str(peer))
            except Exception as e:
                logger.error(f"Failed to deploy to {peer}: {e}")
                results["failed"].append({"peer": str(peer), "error": str(e)})

        return results

    @classmethod
    def check_mesh_health(cls, mesh):
        """
        Check connectivity between all peers in the mesh.

        Pings each peer from the local server and updates latency_ms.
        """
        from apps.deployments.models_mesh import WireGuardPeer

        local_peer = mesh.peers.filter(is_local=True).first()
        if not local_peer:
            return {"error": "No local peer configured in mesh"}

        results = []
        for peer in mesh.peers.filter(is_active=True).exclude(is_local=True):
            try:
                latency = cls._ping(peer.wg_address)
                peer.latency_ms = latency
                peer.last_handshake = timezone.now()
                peer.save(update_fields=["latency_ms", "last_handshake"])
                results.append({
                    "peer": str(peer),
                    "wg_address": peer.wg_address,
                    "latency_ms": latency,
                    "status": "OK",
                })
            except Exception as e:
                peer.latency_ms = None
                peer.save(update_fields=["latency_ms"])
                results.append({
                    "peer": str(peer),
                    "wg_address": peer.wg_address,
                    "latency_ms": None,
                    "status": f"UNREACHABLE: {e}",
                })

        return {"peers": results}

    # ── WireGuard Status ─────────────────────────────────────────────────

    @classmethod
    def get_wg_status(cls, iface: str = "wg0") -> dict:
        """Get WireGuard interface status from `wg show` using a docker container."""
        import docker
        import shlex
        safe_iface = shlex.quote(iface)
        try:
            client = docker.from_env()
            container = client.containers.run(
                "alpine",
                command=["sh", "-c", f"apk add wireguard-tools >/dev/null 2>&1 && wg show {safe_iface}"],
                privileged=True,
                network_mode="host",
                volumes={"/lib/modules": {"bind": "/lib/modules", "mode": "ro"}},
                remove=True,
                stderr=True,
                stdout=True
            )
            # The docker run command returns bytes
            output = container.decode() if isinstance(container, bytes) else str(container)
            return {"status": "UP", "output": output}
        except docker.errors.ContainerError as e:
            # wg show exits with 1 if interface doesn't exist or is down
            output = e.stderr.decode() if hasattr(e, 'stderr') and e.stderr else str(e)
            return {"status": "DOWN", "output": output}
        except Exception as e:
            return {"status": "ERROR", "output": str(e)}

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _ssh_run(server, command: str, timeout: int = 60) -> str:
        """Run a command on a remote server via SSH."""
        from apps.deployments.services.provisioner import _get_ssh_client

        client = _get_ssh_client(server)
        try:
            stdin, stdout, stderr = client.exec_command(
                command, timeout=timeout,
            )
            exit_code = stdout.channel.recv_exit_status()
            output = stdout.read().decode()
            errors = stderr.read().decode()

            if exit_code != 0:
                raise RuntimeError(
                    f"SSH command failed (exit {exit_code}): {errors}"
                )
            return output
        finally:
            client.close()

    @staticmethod
    def _ping(ip: str, count: int = 3) -> float:
        """Ping an IP and return average latency in ms."""
        result = subprocess.run(
            ["ping", "-c", str(count), "-W", "2", ip],
            capture_output=True, text=True, check=True,
        )
        # Parse average from: rtt min/avg/max/mdev = 0.5/1.0/1.5/0.3 ms
        import re
        match = re.search(r"rtt .+ = [\d.]+/([\d.]+)/", result.stdout)
        if match:
            return float(match.group(1))
        raise ValueError(f"Could not parse ping output for {ip}")

    @staticmethod
    def _detect_local_endpoint(port: int) -> str:
        """Detect this server's public IP for the WireGuard endpoint."""
        import requests as req
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
