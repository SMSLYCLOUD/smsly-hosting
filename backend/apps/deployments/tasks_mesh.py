"""
WireGuard mesh Celery tasks.

Periodic health checks and mesh management tasks.
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    name="apps.deployments.tasks_mesh.check_mesh_health_task",
    soft_time_limit=300,
    time_limit=360,
)
def check_mesh_health_task():
    """
    Periodic task that pings all WireGuard peers in all active meshes
    and updates latency/status information.
    """
    from apps.deployments.models_mesh import MeshNetwork
    from apps.deployments.services.wireguard_service import WireGuardService
    from django.utils import timezone

    meshes = MeshNetwork.objects.filter(is_active=True)
    for mesh in meshes:
        if mesh.peers.filter(is_active=True).count() < 2:
            continue  # No point checking a single-peer mesh
        try:
            results = WireGuardService.check_mesh_health(mesh)
            has_unreachable = any(
                peer_result.get("status") != "OK"
                for peer_result in results.get("peers", [])
            )
            mesh.mesh_last_result = results
            mesh.mesh_last_error = "" if not has_unreachable else "One or more mesh peers are unreachable."
            mesh.mesh_status = "FAILED" if has_unreachable else "ACTIVE"
            mesh.mesh_last_deployed_at = mesh.mesh_last_deployed_at or timezone.now()
            mesh.save(update_fields=[
                "mesh_status",
                "mesh_last_error",
                "mesh_last_result",
                "mesh_last_deployed_at",
                "updated_at",
            ])
            for peer_result in results.get("peers", []):
                if peer_result["status"] != "OK":
                    logger.warning(
                        f"Mesh {mesh.name}: peer {peer_result['wg_address']} "
                        f"is {peer_result['status']}"
                    )
        except Exception as e:
            mesh.mesh_status = "FAILED"
            mesh.mesh_last_error = str(e)
            mesh.mesh_last_result = {"error": str(e)}
            mesh.save(update_fields=[
                "mesh_status",
                "mesh_last_error",
                "mesh_last_result",
                "updated_at",
            ])
            logger.error(f"Mesh health check failed for {mesh.name}: {e}")


@shared_task(
    bind=True,
    name="apps.deployments.tasks_mesh.deploy_mesh_task",
    max_retries=0,
    soft_time_limit=900,
    time_limit=960,
)
def deploy_mesh_task(self, mesh_id: str):
    """Deploy WireGuard configs to all peers in a mesh (async)."""
    from apps.deployments.models_mesh import MeshNetwork
    from apps.deployments.services.wireguard_service import WireGuardService
    from django.utils import timezone

    try:
        mesh = MeshNetwork.objects.get(id=mesh_id)
        mesh.mesh_status = "DEPLOYING"
        mesh.mesh_last_error = ""
        mesh.mesh_last_result = {}
        mesh.save(update_fields=[
            "mesh_status",
            "mesh_last_error",
            "mesh_last_result",
            "updated_at",
        ])
        results = WireGuardService.deploy_full_mesh(mesh)
        failed = results.get("failed") or []
        mesh.mesh_status = "FAILED" if failed else "ACTIVE"
        mesh.mesh_last_error = "; ".join(
            str(item.get("error", item)) for item in failed
        ) if failed else ""
        mesh.mesh_last_result = results
        mesh.mesh_last_deployed_at = timezone.now()
        mesh.save(update_fields=[
            "mesh_status",
            "mesh_last_error",
            "mesh_last_result",
            "mesh_last_deployed_at",
            "updated_at",
        ])
        logger.info("Mesh deploy completed for %s: success=%s failed=%s", mesh.name, len(results.get("success", [])), len(failed))
        return results
    except MeshNetwork.DoesNotExist:
        logger.error(f"Mesh {mesh_id} not found")
        return {"error": "Mesh not found"}
    except Exception as e:
        try:
            mesh.mesh_status = "FAILED"
            mesh.mesh_last_error = str(e)
            mesh.mesh_last_result = {"error": str(e)}
            mesh.save(update_fields=[
                "mesh_status",
                "mesh_last_error",
                "mesh_last_result",
                "updated_at",
            ])
        except Exception:
            pass
        logger.error(f"Mesh deploy failed: {e}")
        return {"error": str(e)}
