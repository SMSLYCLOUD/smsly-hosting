from django.conf import settings
from apps.deployments.models import ManagedServer
import logging

logger = logging.getLogger(__name__)

def select_eligible_node(user) -> ManagedServer:
    """Select the first eligible ``ManagedServer`` for a user's deployment.

    Eligibility criteria:
    * Server must be ``ONLINE``.
    * Primary servers are only eligible if they explicitly allow user workloads
      (``allow_user_workloads``) or the global setting
      ``CLOUDNEURON_ALLOW_CONTROL_PLANE_WORKLOADS`` is ``True``.

    The function logs a warning when no eligible server is found, helping
    operators diagnose deployment stalls.
    """
    servers = ManagedServer.objects.filter(owner=user)
    allow_control_plane = getattr(settings, 'CLOUDNEURON_ALLOW_CONTROL_PLANE_WORKLOADS', False)

    eligible = []
    for s in servers:
        if s.status != "ONLINE":
            continue

        if getattr(s, 'is_primary', False):
            if getattr(s, 'allow_user_workloads', False) or allow_control_plane:
                eligible.append(s)
        else:
            eligible.append(s)

    if not eligible:
        logger.warning("No eligible ManagedServer found for user %s", getattr(user, 'id', user))
        return None
    return eligible[0]
