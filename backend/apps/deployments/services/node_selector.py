from django.conf import settings
from apps.deployments.models import ManagedServer
import logging

logger = logging.getLogger(__name__)

def select_eligible_node(user) -> ManagedServer:
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
        return None
    return eligible[0]
