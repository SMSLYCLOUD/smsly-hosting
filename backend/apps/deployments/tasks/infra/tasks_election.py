import logging

from celery import shared_task

from apps.deployments.constants import TASK_TIME_LIMIT_TRIVIAL

logger = logging.getLogger(__name__)


@shared_task(name="apps.deployments.tasks_election.heartbeat_task", soft_time_limit=TASK_TIME_LIMIT_TRIVIAL[0], time_limit=TASK_TIME_LIMIT_TRIVIAL[1])
def heartbeat_task():
    """
    Periodic task (every 5s):
    - If LEADER: send heartbeats to all followers
    - If FOLLOWER: check if leader heartbeat has timed out
    - If CANDIDATE: retry only after the campaign timeout (worker recovery)
    """
    from django.core.cache import cache
    from apps.deployments.models.mesh import MeshNetwork
    from apps.deployments.services.election_service import ElectionService

    has_mesh = cache.get("election:has_active_mesh")
    if has_mesh is False:
        return
    # Find active meshes with cluster state
    meshes = list(MeshNetwork.objects.filter(is_active=True))
    cache.set("election:has_active_mesh", bool(meshes), timeout=60)
    for mesh in meshes:
        try:
            cluster = ElectionService.get_or_create_cluster(mesh=mesh)
        except Exception as e:
            logger.error(f"Failed to get cluster for mesh {mesh.name}: {e}")
            continue

        role = cluster.local_role

        if role == "LEADER":
            try:
                ElectionService.send_heartbeat(cluster)
            except Exception as e:
                logger.error(f"Heartbeat send failed: {e}")

        elif role in ("FOLLOWER", "CANDIDATE"):
            try:
                elected = ElectionService.check_leader_timeout(cluster)
                if elected:
                    logger.info("Election triggered — promoted to leader!")
            except Exception as e:
                logger.error(f"Leader timeout check failed: {e}")


@shared_task(name="apps.deployments.tasks_election.cleanup_heartbeat_logs_task", soft_time_limit=TASK_TIME_LIMIT_TRIVIAL[0], time_limit=TASK_TIME_LIMIT_TRIVIAL[1])
def cleanup_heartbeat_logs_task():
    """
    Scheduled task (every 10 minutes): Clean up old heartbeat logs.

    Previously this was a probabilistic random call inside heartbeat_task.
    Running it on a deterministic schedule ensures cleanup always happens.
    """
    from apps.deployments.models.mesh import MeshNetwork
    from apps.deployments.services.election_service import ElectionService

    meshes = MeshNetwork.objects.filter(is_active=True)
    for mesh in meshes:
        try:
            cluster = ElectionService.get_or_create_cluster(mesh=mesh)
            ElectionService.cleanup_old_heartbeats(cluster)
        except Exception as e:
            logger.error(f"Heartbeat cleanup failed for mesh {mesh.name}: {e}")


@shared_task(name="apps.deployments.tasks_election.force_election_task", soft_time_limit=TASK_TIME_LIMIT_TRIVIAL[0], time_limit=TASK_TIME_LIMIT_TRIVIAL[1])
def force_election_task(mesh_id: str | None = None):
    """
    Force a new election (admin action).

    Used when the current leader is misbehaving but hasn't technically
    timed out yet.
    """
    from apps.deployments.models.mesh import MeshNetwork
    from apps.deployments.services.election_service import ElectionService

    if mesh_id:
        try:
            mesh = MeshNetwork.objects.get(id=mesh_id)
            cluster = ElectionService.get_or_create_cluster(mesh=mesh)
            result = ElectionService.start_election(cluster)
            logger.info(f"Forced election for mesh {mesh.name}: {'won' if result else 'lost'}")
            return result
        except MeshNetwork.DoesNotExist:
            logger.error(f"Mesh {mesh_id} not found")
            return False
    else:
        # Election for all active meshes
        meshes = MeshNetwork.objects.filter(is_active=True)
        results = {}
        for mesh in meshes:
            cluster = ElectionService.get_or_create_cluster(mesh=mesh)
            results[mesh.name] = ElectionService.start_election(cluster)
        return results
