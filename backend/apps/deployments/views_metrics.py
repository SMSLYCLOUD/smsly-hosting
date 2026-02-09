"""Views Metrics module."""
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Service
from .metrics import metrics_adapter


class MetricsViewSet(viewsets.ViewSet):
    """
    ReadOnly ViewSet for Service Metrics.
    """
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, service_pk=None):
        if not service_pk:
            return Response({'error': 'Service ID required'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            service = Service.objects.get(pk=service_pk)
            # Check permission
            if service.owner != request.user:
                return Response(status=status.HTTP_403_FORBIDDEN)

            duration = request.query_params.get('duration', '1h')

            cpu = metrics_adapter.get_cpu_history(str(service.id), duration)
            memory = metrics_adapter.get_memory_history(
                str(service.id), duration)
            network = metrics_adapter.get_network_history(
                str(service.id), duration)

            return Response({
                'cpu': cpu,
                'memory': memory,
                'network': network
            })
        except Service.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
