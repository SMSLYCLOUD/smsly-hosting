"""
Replication Celery tasks.

Periodic replication health checks and auto-failover.
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="apps.deployments.tasks_replication.check_replication_health_task")
def check_replication_health_task():
    """
    Periodic task (every 30s): check WAL replication lag across all meshes.

    Logs warnings if lag exceeds thresholds:
    - > 1MB: warning
    - > 10MB: critical alert
    - Node unreachable: error
    """
    from apps.deployments.models_mesh import MeshNetwork
    from apps.deployments.services.replication_service import ReplicationService

    meshes = MeshNetwork.objects.filter(is_active=True)
    for mesh in meshes:
        if mesh.peers.filter(is_active=True).count() < 2:
            continue  # No replication possible with 1 peer

        try:
            health = ReplicationService.check_replication_health(mesh)

            for node in health.get("nodes", []):
                if "UNREACHABLE" in node.get("status", ""):
                    logger.error(
                        f"Replication node {node['name']} ({node['wg_address']}) "
                        f"is UNREACHABLE"
                    )

            for replica in health.get("replicas", []):
                lag = replica.get("lag_bytes")
                if lag is not None:
                    if lag > 10 * 1024 * 1024:  # 10MB
                        logger.critical(
                            f"CRITICAL replication lag on {replica['name']}: "
                            f"{lag / 1024 / 1024:.1f}MB"
                        )
                    elif lag > 1024 * 1024:  # 1MB
                        logger.warning(
                            f"Replication lag on {replica['name']}: "
                            f"{lag / 1024:.0f}KB"
                        )

        except Exception as e:
            logger.error(f"Replication health check failed for mesh {mesh.name}: {e}")


@shared_task(name="apps.deployments.tasks_replication.deploy_replication_task")
def deploy_replication_task(mesh_id: str, db_password: str,
                             admin_password: str,
                             replication_password: str = "repl_pass"):
    """Deploy Patroni replication cluster to a mesh (async)."""
    from apps.deployments.models_mesh import MeshNetwork
    from apps.deployments.services.replication_service import ReplicationService

    try:
        mesh = MeshNetwork.objects.get(id=mesh_id)
        results = ReplicationService.deploy_replication(
            mesh, db_password, admin_password, replication_password,
        )
        logger.info(f"Replication deployment completed: {results}")
        return results
    except MeshNetwork.DoesNotExist:
        logger.error(f"Mesh {mesh_id} not found")
    except Exception as e:
        logger.error(f"Replication deployment failed: {e}")


@shared_task(name="apps.deployments.tasks_replication.manual_failover_task")
def manual_failover_task(mesh_id: str, target_wg_address: str):
    """Trigger manual Patroni failover to a target replica (async)."""
    from apps.deployments.models_mesh import MeshNetwork
    from apps.deployments.services.replication_service import ReplicationService

    try:
        mesh = MeshNetwork.objects.get(id=mesh_id)
        result = ReplicationService.manual_failover(mesh, target_wg_address)
        logger.info(f"Failover result: {result}")
        return result
    except MeshNetwork.DoesNotExist:
        logger.error(f"Mesh {mesh_id} not found")
    except Exception as e:
        logger.error(f"Failover failed: {e}")
        return {"error": str(e)}
