import logging

logger = logging.getLogger(__name__)
import contextlib
import logging
import random

from celery import shared_task


def _get_local_role() -> str:
    """Read local server role from file."""
    try:
        with open("/tmp/.smsly_cluster_role") as f:
            return f.read().strip()
    except Exception:
        return "FOLLOWER"


@shared_task(name="apps.deployments.tasks_election.heartbeat_task")
def heartbeat_task():
    """
    Periodic task (every 5s):
    - If LEADER: send heartbeats to all followers
    - If FOLLOWER: check if leader heartbeat has timed out
    - If CANDIDATE: do nothing (election in progress)
    """
    from apps.deployments.models_mesh import MeshNetwork
    from apps.deployments.services.election_service import ElectionService

    # Find active meshes with cluster state
    meshes = MeshNetwork.objects.filter(is_active=True)
    for mesh in meshes:
        try:
            cluster = ElectionService.get_or_create_cluster(mesh=mesh)
        except Exception as e:
            logger.error(f"Failed to get cluster for mesh {mesh.name}: {e}")
            continue

        role = _get_local_role()

        if role == "LEADER":
            try:
                ElectionService.send_heartbeat(cluster)
            except Exception as e:
                logger.error(f"Heartbeat send failed: {e}")

        elif role == "FOLLOWER":
            try:
                elected = ElectionService.check_leader_timeout(cluster)
                if elected:
                    logger.info("Election triggered — promoted to leader!")
            except Exception as e:
                logger.error(f"Leader timeout check failed: {e}")

        # Periodic cleanup of old heartbeat logs (every ~100 runs ≈ 8 min)
        if random.randint(1, 100) == 1:
            with contextlib.suppress(Exception):
                ElectionService.cleanup_old_heartbeats(cluster)


@shared_task(name="apps.deployments.tasks_election.force_election_task")
def force_election_task(mesh_id: str | None = None):
    """
    Force a new election (admin action).

    Used when the current leader is misbehaving but hasn't technically
    timed out yet.
    """
    from apps.deployments.models_mesh import MeshNetwork
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
