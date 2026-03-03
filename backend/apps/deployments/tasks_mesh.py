"""
WireGuard mesh Celery tasks.

Periodic health checks and mesh management tasks.
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="apps.deployments.tasks_mesh.check_mesh_health_task")
def check_mesh_health_task():
    """
    Periodic task that pings all WireGuard peers in all active meshes
    and updates latency/status information.
    """
    from apps.deployments.models_mesh import MeshNetwork
    from apps.deployments.services.wireguard_service import WireGuardService

    meshes = MeshNetwork.objects.filter(is_active=True)
    for mesh in meshes:
        if mesh.peers.filter(is_active=True).count() < 2:
            continue  # No point checking a single-peer mesh
        try:
            results = WireGuardService.check_mesh_health(mesh)
            for peer_result in results.get("peers", []):
                if peer_result["status"] != "OK":
                    logger.warning(
                        f"Mesh {mesh.name}: peer {peer_result['wg_address']} "
                        f"is {peer_result['status']}"
                    )
        except Exception as e:
            logger.error(f"Mesh health check failed for {mesh.name}: {e}")


@shared_task(name="apps.deployments.tasks_mesh.deploy_mesh_task")
def deploy_mesh_task(mesh_id: str):
    """Deploy WireGuard configs to all peers in a mesh (async)."""
    from apps.deployments.models_mesh import MeshNetwork
    from apps.deployments.services.wireguard_service import WireGuardService

    try:
        mesh = MeshNetwork.objects.get(id=mesh_id)
        results = WireGuardService.deploy_full_mesh(mesh)
        logger.info(f"Mesh deploy completed: {results}")
        return results
    except MeshNetwork.DoesNotExist:
        logger.error(f"Mesh {mesh_id} not found")
    except Exception as e:
        logger.error(f"Mesh deploy failed: {e}")
