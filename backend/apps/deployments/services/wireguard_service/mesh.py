import logging
import shlex

from django.utils import timezone

from apps.deployments.utils import log_event

from ._utils import _bounded_error

logger = logging.getLogger(__name__)


class MeshMixin:

    @classmethod
    def deploy_full_mesh(cls, mesh):
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

    @classmethod
    def get_wg_status(cls, iface: str = "wg0") -> dict:
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
            output = container.decode() if isinstance(container, bytes) else str(container)
            return {"status": "UP", "output": output}
        except docker.errors.ContainerError as e:
            output = e.stderr.decode() if hasattr(e, 'stderr') and e.stderr else str(e)
            return {"status": "DOWN", "output": output}
        except Exception as e:
            return {"status": "ERROR", "output": str(e)}
