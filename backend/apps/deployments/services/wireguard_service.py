"""
WireGuard VPN Mesh service.

Handles key generation, config rendering, and SSH deployment
of WireGuard configurations across the server fleet.
"""

import ipaddress
import logging
import re
import shlex
import subprocess
import textwrap

from django.db import transaction
from django.utils import timezone

from apps.deployments.utils import log_event

logger = logging.getLogger(__name__)


def _command_text(result) -> str:
    if isinstance(result, tuple):
        stdout = result[0] if len(result) > 0 else ""
        stderr = result[1] if len(result) > 1 else ""
        return (stdout or "") + (("\n" + stderr) if stderr else "")
    return "" if result is None else str(result)


def _bounded_error(exc, limit=2000) -> str:
    return str(exc).replace("\x00", "")[:limit]


class WireGuardService:
    """Manage WireGuard mesh network across Grid servers."""

    INTERFACE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")

    @classmethod
    def validate_interface_name(cls, iface: str) -> str:
        """Reject interface names that WireGuard or the shell should not receive."""
        value = str(iface or "").strip()
        if not cls.INTERFACE_RE.fullmatch(value):
            raise ValueError("Invalid WireGuard interface name.")
        return value

    @staticmethod
    def validate_endpoint(endpoint: str) -> str:
        """Validate optional WireGuard endpoint host:port strings."""
        value = str(endpoint or "").strip()
        if not value:
            return ""
        if value.count(":") < 1:
            raise ValueError("WireGuard endpoint must include a port.")
        host, port_raw = value.rsplit(":", 1)
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise ValueError("WireGuard endpoint port must be numeric.") from exc
        if port < 1 or port > 65535:
            raise ValueError("WireGuard endpoint port is out of range.")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host):
                raise ValueError("WireGuard endpoint host is invalid.")
        return f"{host}:{port}"

    @staticmethod
    def validate_wg_config(config: str) -> None:
        """Validate the minimum WireGuard config shape before applying it."""
        if not isinstance(config, str) or "\x00" in config:
            raise ValueError("Invalid WireGuard config.")
        required_patterns = [
            r"(?m)^\s*\[Interface\]\s*$",
            r"(?m)^\s*PrivateKey\s*=\s*\S+",
            r"(?m)^\s*Address\s*=\s*\S+",
        ]
        for pattern in required_patterns:
            if not re.search(pattern, config):
                raise ValueError("WireGuard config is missing required interface fields.")

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

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

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

    @classmethod
    def _fetch_server_wg_public_key(cls, server) -> str | None:
        """SSH into a server and read its existing WireGuard public key.

        Returns the base64 public key string, or None if the key could not
        be fetched (no SSH credentials, SSH auth failed, or file missing).
        """
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
            # Valid WireGuard base64 public key is 44 chars ending with =
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
        """SSH into a server and add/update a WireGuard peer on its wg0 interface.

        This adds the peer both live (``wg set``) and persistently
        (appends to ``/etc/wireguard/wg0.conf``).

        Returns True if the peer was added successfully, False otherwise.
        """
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

            # 1. Add live peer (idempotent — wg set replaces existing peer with same key)
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

            # 2. Persist to config file (skip if peer section already exists)
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

    # ── Config Rendering ─────────────────────────────────────────────────

    @classmethod
    def build_wg_config(cls, peer) -> str:
        """
        Build a complete wg0.conf for a specific peer.

        The config includes the [Interface] section for this peer
        and [Peer] sections for all OTHER peers in the same mesh.

        If the peer has a server-managed key (private_key is empty), the
        [Interface] section omits PrivateKey so the node keeps its own key.
        """
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

        # Interface section
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

        # PostUp/PostDown for firewall rules and rp_filter fix.
        # Strict rp_filter (=1) drops WireGuard handshake packets because the
        # kernel cannot reverse-path-validate the decapsulated tunnel source IP
        # before the interface is fully established.  Loose mode (=2) fixes this.
        config += textwrap.dedent(f"""\
            PostUp = sysctl -w net.ipv4.conf.%i.rp_filter=2 net.ipv4.conf.all.rp_filter=2; iptables -A INPUT -p udp --dport {mesh.listen_port} -j ACCEPT
            PostDown = iptables -D INPUT -p udp --dport {mesh.listen_port} -j ACCEPT; sysctl -w net.ipv4.conf.all.rp_filter=1
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
                peer_section += f"    Endpoint = {cls.validate_endpoint(other.endpoint)}\n"
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
        from apps.deployments.models.core import ManagedServer
        from apps.deployments.models.mesh import WireGuardPeer

        # Check if peer already exists
        existing = WireGuardPeer.objects.filter(
            mesh=mesh, server=server, is_local=is_local,
        ).first()
        if existing:
            # For remote peers, verify the public key is still current.
            # Re-provisioning generates new WG keys on disk — if we don't
            # refresh, the master's peer config holds a stale key and the
            # handshake will fail silently (0 bytes received, 0 handshakes).
            if not is_local and server:
                current_key = cls._fetch_server_wg_public_key(server)
                if current_key and current_key != existing.public_key:
                    logger.warning(
                        "WG public key changed for %s: %s → %s (re-provisioned?)",
                        server.host, existing.public_key[:16], current_key[:16],
                    )
                    existing.public_key = current_key
                    existing.private_key = ""  # server-managed
                    existing.save(update_fields=["public_key", "private_key", "updated_at"])
            logger.info(f"Peer already exists for server {server or 'local'}")
            return existing

        if not is_local:
            if not server:
                raise ValueError("Remote mesh peers require a ManagedServer.")
            if server.status != ManagedServer.Status.ONLINE and server.provision_status not in (
                ManagedServer.ProvisionStatus.PROVISIONING,
                ManagedServer.ProvisionStatus.DONE,
            ):
                raise ValueError(f"Server '{server.name}' is {server.status} ({server.provision_status}); only ONLINE or PROVISIONING/DONE servers can join a mesh.")
            if not (server.ssh_key or server.ssh_password):
                raise ValueError(f"Server '{server.name}' has no SSH credentials for mesh deployment.")

        # Generate keys and assign IP
        private_key, public_key = cls.generate_keypair()

        # For remote peers, try to use the node's existing WireGuard keypair
        # instead of generating a new one.  When the node was installed via
        # backend/install.sh or agent-lite bootstrap, it already generated its
        # own keys on disk.  Using the existing key prevents a mismatch between
        # the master's peer config and the node's actual wg0 interface.
        fetched_key = None
        if not is_local and server:
            fetched_key = cls._fetch_server_wg_public_key(server)
        if fetched_key:
            public_key = fetched_key
            private_key = ""  # server-managed, not stored on master
            logger.info(
                "Using existing WG public key from node %s: %s",
                server.host, fetched_key[:16],
            )

        # Build endpoint. Public host is the safe default for arbitrary VPS
        # fleets; private IP only works when the operator explicitly marks the
        # node as sharing a routable private network.
        endpoint = ""
        if server:
            metadata = getattr(server, "provider_metadata", {}) or {}
            prefer_private = str(
                metadata.get("mesh_endpoint")
                or metadata.get("wireguard_endpoint")
                or ""
            ).lower() == "private" or bool(metadata.get("prefer_private_mesh"))
            if server.private_ip and prefer_private:
                endpoint = f"{server.private_ip}:{mesh.listen_port}"
                log_event(
                    action="MESH_ENDPOINT_SELECTION",
                    target=f"Server: {server.name}",
                    metadata={"selected": "private", "ip": server.private_ip, "reason": "Explicit private mesh preference"}
                )
            else:
                endpoint = f"{server.host}:{mesh.listen_port}"
                log_event(
                    action="MESH_ENDPOINT_SELECTION",
                    target=f"Server: {server.name}",
                    metadata={"selected": "public", "ip": server.host, "reason": "Default routable endpoint"}
                )
        elif is_local:
            # For local server, try to detect public IP
            endpoint = cls._detect_local_endpoint(mesh.listen_port)

        # Critical section: lock the mesh row so concurrent provision calls
        # cannot race on next_available_ip() and get the same address.
        # SSH operations are kept outside the atomic block to avoid holding
        # a DB row lock during network I/O.
        from apps.deployments.models.mesh import MeshNetwork
        with transaction.atomic():
            mesh = MeshNetwork.objects.select_for_update().get(id=mesh.id)
            wg_address = mesh.next_available_ip()
            peer = WireGuardPeer.objects.create(
                mesh=mesh,
                server=server,
                private_key=private_key,
                public_key=public_key,
                wg_address=wg_address,
                endpoint=cls.validate_endpoint(endpoint),
                allowed_ips=f"{wg_address}/32",
                is_active=True,
                is_local=is_local,
            )

        logger.info(
            f"Added peer {peer.wg_address} to mesh {mesh.name} "
            f"(server: {server or 'local'})"
        )

        # When we fetched the node's existing key via SSH, also add the master
        # as a peer on the node immediately.  This makes the tunnel bidirectional
        # without waiting for the async deploy_mesh_task, which may fail if the
        # node has already regenerated keys or SSH credentials are stale.
        if fetched_key:
            local_peer = WireGuardPeer.objects.filter(
                mesh=mesh, is_local=True,
            ).first()
            if local_peer and local_peer.public_key and local_peer.wg_address:
                cls._ensure_peer_on_node_via_ssh(
                    server,
                    peer_pubkey=local_peer.public_key,
                    peer_wg_ip=local_peer.wg_address,
                    peer_endpoint=local_peer.endpoint,
                )

        return peer

    @classmethod
    def ensure_server_in_default_mesh(cls, server, *, deploy_async: bool = True) -> dict:
        """
        Ensure the local controller and a connected server are in the default mesh.

        This is idempotent and intended for server provisioning/connect flows.
        """
        from apps.deployments.models.core import ManagedServer
        from apps.deployments.models.mesh import MeshNetwork

        if not server:
            raise ValueError("server is required")
        if server.status not in (ManagedServer.Status.ONLINE, ManagedServer.Status.UNKNOWN) \
           and server.provision_status != ManagedServer.ProvisionStatus.PROVISIONING:
            raise ValueError(f"Server '{server.name}' is {server.status}; only ONLINE/PROVISIONING servers can join a mesh.")
        if server.is_primary:
            return {"mesh": None, "peer": None, "queued": False, "reason": "primary server is local peer"}
        if not (server.ssh_key or server.ssh_password):
            raise ValueError(f"Server '{server.name}' has no SSH credentials for automatic VPN mesh setup.")

        project = getattr(server, "project", None)
        mesh = MeshNetwork.objects.filter(
            project=project,
            name="default",
        ).order_by("created_at").first()
        if not mesh:
            mesh = MeshNetwork.objects.create(
                project=project,
                name="default",
                subnet="10.100.0.0/24",
                listen_port=51820,
                interface_name="wg0",
                is_active=True,
            )
        elif not mesh.is_active:
            mesh.is_active = True
            mesh.save(update_fields=["is_active", "updated_at"])

        from apps.deployments.models.mesh import WireGuardPeer
        local_existed = WireGuardPeer.objects.filter(
            mesh=mesh,
            server=None,
            is_local=True,
        ).exists()
        peer_existed = WireGuardPeer.objects.filter(
            mesh=mesh,
            server=server,
            is_local=False,
        ).exists()

        local_peer = cls.add_peer_to_mesh(mesh, server=None, is_local=True)
        peer = cls.add_peer_to_mesh(mesh, server=server, is_local=False)

        update_fields = []
        if getattr(server, "wg_address", None) != peer.wg_address:
            server.wg_address = peer.wg_address
            update_fields.append("wg_address")
        if update_fields:
            server.save(update_fields=[*update_fields, "updated_at"])

        primary = ManagedServer.get_primary()
        if primary and getattr(primary, "wg_address", None) != local_peer.wg_address:
            primary.wg_address = local_peer.wg_address
            primary.save(update_fields=["wg_address", "updated_at"])

        # Deploy the local (master) config synchronously so the master's wg0
        # interface picks up the new peer immediately.  The remote peer is
        # either already configured (via SSH in add_peer_to_mesh above) or
        # will be deployed by the async task below.
        try:
            cls.deploy_config(local_peer)
        except Exception as deploy_exc:
            logger.warning(
                "Local WG config deploy failed (will retry async): %s", deploy_exc,
            )

        queued = False
        should_deploy = (
            not local_existed
            or not peer_existed
            or mesh.mesh_status != "ACTIVE"
        )
        if deploy_async and should_deploy and WireGuardPeer.objects.filter(mesh=mesh, is_active=True).count() >= 2:
            if mesh.mesh_status != "DEPLOYING":
                mesh.mesh_status = "DEPLOYING"
                mesh.mesh_last_error = ""
                mesh.save(update_fields=["mesh_status", "mesh_last_error", "updated_at"])
                try:
                    from apps.deployments.tasks.infra.tasks_mesh import deploy_mesh_task
                    deploy_mesh_task.delay(str(mesh.id))
                    queued = True
                except Exception as exc:
                    mesh.mesh_status = "FAILED"
                    mesh.mesh_last_error = _bounded_error(exc)
                    mesh.save(update_fields=["mesh_status", "mesh_last_error", "updated_at"])
                    raise

        return {
            "mesh": str(mesh.id),
            "peer": str(peer.id),
            "wg_address": str(peer.wg_address),
            "queued": queued,
        }

    @classmethod
    def remove_peer_from_mesh(cls, peer):
        """
        Remove a peer from the mesh.

        1. SSH into the target and tear down WireGuard
        2. Update configs on all remaining peers
        3. Delete the WireGuardPeer record

        Configs are updated BEFORE deleting the peer record so that if any
        deploy fails, the peer can still be retried.
        """
        mesh = peer.mesh
        server = peer.server

        # Tear down WireGuard on the target server
        if server and not peer.is_local:
            try:
                cls._ssh_run(server, f"wg-quick down {shlex.quote(mesh.interface_name)} || true")
            except Exception as e:
                logger.warning(f"Failed to tear down WG on {server}: {e}")

        # Update configs on all remaining peers BEFORE deleting the record
        from apps.deployments.models.mesh import WireGuardPeer
        remaining_peers = WireGuardPeer.objects.filter(mesh=mesh, is_active=True).exclude(id=peer.id)
        for p in remaining_peers:
            try:
                cls.deploy_config(p)
            except Exception as e:
                logger.warning(f"Failed to update config on {p}: {e}")

        # Now safe to delete
        peer.delete()

    @classmethod
    def deploy_config(cls, peer):
        """
        Deploy WireGuard configuration to a peer's server via SSH.

        1. Generate the config
        2. SSH into the server
        3. Write /etc/wireguard/wg0.conf
        4. Restart WireGuard interface

        Peers whose private key is empty (server-managed) are skipped —
        their key was fetched from the existing server install and the config
        is managed locally on that server.
        """
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
        Since the API runs unprivileged, we use the Docker socket proxy to spin up
        an ephemeral privileged container to apply the config to the host network.
        """
        import os

        import docker
        iface = cls.validate_interface_name(iface)
        cls.validate_wg_config(config)
        client = docker.from_env()
        docker_host = os.environ.get("DOCKER_HOST", "tcp://socket-proxy:2375")

        # Write config to a temporary file via a container
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

        # Restart WireGuard interface
        safe_iface = shlex.quote(iface)
        commands = [
            "apk add wireguard-tools iptables >/dev/null 2>&1 || true",
            # Check if kernel module is loaded on the host.
            # Use /proc/modules directly — Alpine images do not ship
            # kmod/lsmod, and installing it wastes time on every restart.
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
        """Deploy WireGuard config on a remote server via SSH."""
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

    # ── Mesh Operations ──────────────────────────────────────────────────

    @classmethod
    def deploy_full_mesh(cls, mesh):
        """
        Deploy WireGuard configs to ALL peers in a mesh.

        Call this after adding/removing a peer to update everyone's config.
        """
        from apps.deployments.models.mesh import MeshNetwork

        iface = cls.validate_interface_name(mesh.interface_name)
        if mesh.name != "default":
            conflicting_mesh = MeshNetwork.objects.filter(
                is_active=True,
                interface_name=iface,
            ).exclude(id=mesh.id).first()
            if conflicting_mesh:
                message = (
                    f"Refusing to deploy mesh '{mesh.name}' on interface '{iface}' "
                    f"because active mesh '{conflicting_mesh.name}' already uses it."
                )
                logger.error(message)
                return {"success": [], "failed": [{"peer": "mesh", "error": message}]}

        from apps.deployments.models.mesh import WireGuardPeer
        peers = WireGuardPeer.objects.filter(mesh=mesh, is_active=True)
        results = {"success": [], "failed": []}

        for peer in peers:
            try:
                cls.deploy_config(peer)
                results["success"].append(str(peer))
                log_event(
                    action="MESH_DEPLOY_SUCCESS",
                    target=f"Peer: {peer.wg_address}",
                    metadata={"peer": str(peer), "mesh": mesh.name, "is_local": peer.is_local}
                )
            except Exception as e:
                logger.error(f"Failed to deploy to {peer}: {e}")
                results["failed"].append({"peer": str(peer), "error": _bounded_error(e)})
                log_event(
                    action="MESH_DEPLOY_FAILED",
                    target=f"Peer: {peer.wg_address}",
                    metadata={"peer": str(peer), "error": _bounded_error(e), "mesh": mesh.name}
                )

        return results

    @classmethod
    def check_mesh_health(cls, mesh):
        """
        Check connectivity between all peers in the mesh.

        Pings each peer from the local server and updates latency_ms.
        """
        from apps.deployments.models.mesh import WireGuardPeer
        local_peer = WireGuardPeer.objects.filter(mesh=mesh, is_local=True).first()
        if not local_peer:
            return {"error": "No local peer configured in mesh"}

        results = []
        for peer in WireGuardPeer.objects.filter(mesh=mesh, is_active=True).exclude(is_local=True):
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
                error = _bounded_error(e)
                peer.latency_ms = None
                peer.save(update_fields=["latency_ms"])
                results.append({
                    "peer": str(peer),
                    "wg_address": peer.wg_address,
                    "latency_ms": None,
                    "status": f"UNREACHABLE: {error}",
                })
                # Exhaustive Logging: Log the health failure
                log_event(
                    action="MESH_PEER_UNREACHABLE",
                    target=f"Peer: {peer.wg_address}",
                    metadata={
                        "peer": str(peer),
                        "endpoint": peer.endpoint,
                        "error": error,
                        "mesh": mesh.name
                    }
                )

        return {"peers": results}

    # ── WireGuard Status ─────────────────────────────────────────────────

    @classmethod
    def get_wg_status(cls, iface: str = "wg0") -> dict:
        """Get WireGuard interface status from `wg show` using a docker container."""
        import docker
        try:
            client = docker.from_env()
            container = client.containers.run(
                "alpine",
                command=["sh", "-c", f"apk add wireguard-tools >/dev/null 2>&1 && wg show {shlex.quote(iface)}"],
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
        try:
            from apps.deployments.models.core import PlatformConfig
            config = PlatformConfig.load()
            configured_ip = str(getattr(config, "server_ip", "") or "").strip()
            if configured_ip:
                return f"{configured_ip}:{port}"
        except Exception:
            pass
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
