"""Views Metrics module."""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Service
from .metrics import metrics_adapter


class MetricsResponseSerializer(serializers.Serializer):
    cpu = serializers.JSONField()
    memory = serializers.JSONField()
    network = serializers.JSONField()
    disk = serializers.JSONField()
    current = serializers.JSONField()


class MetricsViewSet(viewsets.GenericViewSet):
    """
    ReadOnly ViewSet for Service Metrics.
    """
    queryset = Service.objects.all()
    serializer_class = MetricsResponseSerializer
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name='service_pk',
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
            )
        ],
        responses=MetricsResponseSerializer,
    )
    def list(self, request, service_pk=None):
        if not service_pk:
            return Response({'error': 'Service ID required'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            service = self.queryset.get(pk=service_pk)
            # Check permission
            if (not request.user.is_superuser) and service.owner != request.user:
                return Response(status=status.HTTP_403_FORBIDDEN)

            duration = request.query_params.get('duration', '1h')

            cpu = metrics_adapter.get_cpu_history(str(service.id), duration)
            memory = metrics_adapter.get_memory_history(
                str(service.id), duration)
            network = metrics_adapter.get_network_history(
                str(service.id), duration)
            disk = metrics_adapter.get_disk_history(
                str(service.id), duration)
            current = metrics_adapter.get_current(str(service.id))

            return Response({
                'cpu': cpu,
                'memory': memory,
                'network': network,
                'disk': disk,
                'current': current,
            })
        except Service.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
