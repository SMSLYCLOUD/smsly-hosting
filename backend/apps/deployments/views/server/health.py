"""
Health check mixins for ManagedServerViewSet.
"""

from rest_framework import status
from rest_framework.decorators import action, throttle_classes
from rest_framework.response import Response

from apps.core.rate_limiting import ServerHealthCheckRateThrottle
from ...models.servers import ManagedServer
from .serializers import (
    ManagedServerSerializer,
    ServerCheckAllThrottle,
)


class HealthMixin:

    @action(detail=True, methods=["post"],
            throttle_classes=[ServerHealthCheckRateThrottle])
    def health_check(self, request, pk=None):
        from .helpers import _refresh_managed_server_health
        server = self.get_object()
        server = _refresh_managed_server_health(server)
        return Response(ManagedServerSerializer(server).data)

    @action(detail=False, methods=["post"])
    @throttle_classes([ServerCheckAllThrottle])
    def check_all(self, request):
        from apps.deployments.tasks.infra.tasks_health import refresh_managed_server_health
        servers = list(self.get_queryset())
        for server in servers:
            refresh_managed_server_health.delay(str(server.id))
        return Response(
            {"status": "scheduled", "count": len(servers)},
            status=status.HTTP_202_ACCEPTED,
        )
