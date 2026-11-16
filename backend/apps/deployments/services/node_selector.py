import logging

from django.conf import settings

from apps.deployments.models import ManagedServer

logger = logging.getLogger(__name__)

def select_eligible_node(user) -> ManagedServer:
    """Select the best eligible ``ManagedServer`` for a user's deployment.

    Eligibility criteria:
    * Server must be ``ONLINE`` with ``provision_status=DONE``.
    * ``MEDIA`` nodes never take user workloads (voice/video baremetal only).
    * Primary servers are only eligible if they explicitly allow user workloads
      (``allow_user_workloads``) or the global setting
      ``GRID_ALLOW_CONTROL_PLANE_WORKLOADS`` is ``True``.
    * Remote nodes rank via ``NodeScorer.best()`` (free mem/CPU/disk);
      falls back to least-loaded ``services_count`` when Prometheus has
      no data for the fleet.

    The function logs a warning when no eligible server is found, helping
    operators diagnose deployment stalls.
    """
    servers = ManagedServer.objects.filter(owner=user)
    allow_control_plane = getattr(
        settings, 'GRID_ALLOW_CONTROL_PLANE_WORKLOADS',
        getattr(settings, 'CLOUDNEURON_ALLOW_CONTROL_PLANE_WORKLOADS', False),
    )

    remote_nodes = []
    master_node = None

    for s in servers:
        if s.status != "ONLINE":
            continue
        if getattr(s, 'provision_status', 'DONE') != ManagedServer.ProvisionStatus.DONE:
            continue
        if getattr(s, 'node_type', '') == ManagedServer.NodeType.MEDIA:
            continue

        if getattr(s, 'is_primary', False):
            if getattr(s, 'allow_user_workloads', False) or allow_control_plane:
                master_node = s
        else:
            remote_nodes.append(s)

    # Prioritize remote nodes (Lite Agents) to keep the Master lean
    if remote_nodes:
        try:
            from apps.deployments.services.node_scorer import NodeScorer
            best = NodeScorer().best(remote_nodes)
            if best is not None:
                return best
        except Exception as exc:
            logger.debug("NodeScorer unavailable, falling back to services_count: %s", exc)
        # Fallback when Prometheus has no data: least-loaded wins
        remote_nodes.sort(key=lambda x: getattr(x, 'services_count', 0))
        return remote_nodes[0]

    if master_node:
        return master_node

    logger.warning("No eligible ManagedServer found for user %s", getattr(user, 'id', user))
    return None
