import logging
import shlex

from django.db import transaction

from apps.deployments.utils import log_event

from ._utils import _bounded_error

logger = logging.getLogger(__name__)


class PeersMixin:

    @classmethod
    def add_peer_to_mesh(cls, mesh, server=None, is_local=False):
        from apps.deployments.models.core import ManagedServer
        from apps.deployments.models.mesh import WireGuardPeer

        existing = WireGuardPeer.objects.filter(
            mesh=mesh, server=server, is_local=is_local,
        ).first()
        if existing:
            if not is_local and server:
                current_key = cls._fetch_server_wg_public_key(server)
                if current_key and current_key != existing.public_key:
                    logger.warning(
                        "WG public key changed for %s: %s → %s (re-provisioned?)",
                        server.host, existing.public_key[:16], current_key[:16],
                    )
                    existing.public_key = current_key
                    existing.private_key = ""
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

        private_key, public_key = cls.generate_keypair()

        fetched_key = None
        if not is_local and server:
            fetched_key = cls._fetch_server_wg_public_key(server)
        if fetched_key:
            public_key = fetched_key
            private_key = ""
            logger.info(
                "Using existing WG public key from node %s: %s",
                server.host, fetched_key[:16],
            )

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
            endpoint = cls._detect_local_endpoint(mesh.listen_port)

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
        mesh = peer.mesh
        server = peer.server

        if server and not peer.is_local:
            try:
                cls._ssh_run(server, f"wg-quick down {shlex.quote(mesh.interface_name)} || true")
            except Exception as e:
                logger.warning(f"Failed to tear down WG on {server}: {e}")

        from apps.deployments.models.mesh import WireGuardPeer
        remaining_peers = WireGuardPeer.objects.filter(mesh=mesh, is_active=True).exclude(id=peer.id)
        for p in remaining_peers:
            try:
                cls.deploy_config(p)
            except Exception as e:
                logger.warning(f"Failed to update config on {p}: {e}")

        peer.delete()
