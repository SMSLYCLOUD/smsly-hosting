import logging

logger = logging.getLogger(__name__)
import logging

from celery import shared_task
from django.core.cache import cache
from django.utils import timezone


def _bounded_error(exc, limit=2000):
    return str(exc).replace("\x00", "")[:limit]


@shared_task(
    name="apps.deployments.tasks.infra.tasks_mesh.check_mesh_health_task",
    soft_time_limit=300,
    time_limit=360)
def check_mesh_health_task():
    """
    Periodic task that pings all WireGuard peers in all active meshes
    and updates latency/status information.
    """
    from apps.deployments.models.mesh import MeshNetwork
    from apps.deployments.services.wireguard_service import WireGuardService

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
                    # Self-healing logic for unreachable peers
                    try:
                        peer = mesh.peers.filter(wg_address=peer_result["wg_address"], is_active=True).first()
                        if peer and peer.server:
                            server = peer.server
                            from apps.deployments.models.core import ManagedServer
                            # Only heal if the server itself is ONLINE publicly
                            if server.status == ManagedServer.Status.ONLINE:
                                # Check if the public or private IP endpoint changed
                                metadata = getattr(server, "provider_metadata", {}) or {}
                                prefer_private = str(
                                    metadata.get("mesh_endpoint")
                                    or metadata.get("wireguard_endpoint")
                                    or ""
                                ).lower() == "private" or bool(metadata.get("prefer_private_mesh"))

                                if server.private_ip and prefer_private:
                                    expected_endpoint = f"{server.private_ip}:{mesh.listen_port}"
                                else:
                                    expected_endpoint = f"{server.host}:{mesh.listen_port}"

                                if peer.endpoint != expected_endpoint:
                                    # SECURITY (Batch G cont): run the
                                    # candidate through the same
                                    # validator the rest of the mesh
                                    # service uses, so a malformed
                                    # server.host or a half-updated
                                    # private_ip cannot poison the
                                    # peer record.
                                    from apps.deployments.services.wireguard_service import (
                                        WireGuardService,
                                    )
                                    try:
                                        validated_endpoint = (
                                            WireGuardService.validate_endpoint(
                                                expected_endpoint
                                            )
                                        )
                                    except ValueError as endpoint_exc:
                                        logger.warning(
                                            "Skipping mesh endpoint update for %s: %s",
                                            peer.wg_address, endpoint_exc,
                                        )
                                    else:
                                        logger.info(
                                            f"Mesh {mesh.name}: Peer {peer.wg_address} endpoint changed from "
                                            f"'{peer.endpoint}' to '{validated_endpoint}'. Updating in database."
                                        )
                                        peer.endpoint = validated_endpoint
                                        peer.save(update_fields=["endpoint", "updated_at"])

                                # Trigger recovery task (deploy_mesh_task) with 5 min cooldown rate-limit per peer
                                heal_lock_key = f"mesh-heal-lock:{peer.id}"
                                if cache.add(heal_lock_key, "1", timeout=300):
                                    logger.warning(
                                        f"Mesh {mesh.name}: Peer {peer.wg_address} is unreachable but server is ONLINE. "
                                        f"Triggering auto-healing mesh redeployment."
                                    )
                                    from apps.deployments.tasks.infra.tasks_mesh import (
                                        deploy_mesh_task,
                                    )
                                    deploy_mesh_task.delay(str(mesh.id))
                    except Exception as he:
                        logger.error(f"Failed during mesh VPN self-healing for peer {peer_result.get('wg_address')}: {he}")
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
    name="apps.deployments.tasks.infra.tasks_mesh.deploy_mesh_task",
    max_retries=0,
    soft_time_limit=900,
    time_limit=960)
def deploy_mesh_task(self, mesh_id: str):
    """Deploy WireGuard configs to all peers in a mesh (async)."""
    from apps.deployments.models.mesh import MeshNetwork
    from apps.deployments.services.wireguard_service import WireGuardService

    lock_key = f"mesh-deploy:{mesh_id}"
    if not cache.add(lock_key, "1", timeout=960):
        logger.warning("Mesh deploy skipped for %s because another deploy is running", mesh_id)
        return {"error": "Mesh deployment already running"}

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
            mesh.mesh_last_error = _bounded_error(e)
            mesh.mesh_last_result = {"error": _bounded_error(e)}
            mesh.save(update_fields=[
                "mesh_status",
                "mesh_last_error",
                "mesh_last_result",
                "updated_at",
            ])
        except Exception:
            pass
        logger.error(f"Mesh deploy failed: {e}")
        return {"error": _bounded_error(e)}
    finally:
        cache.delete(lock_key)
