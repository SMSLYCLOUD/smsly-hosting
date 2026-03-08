"""Views Metrics module."""
import logging
from datetime import timedelta

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from django.utils import timezone
from rest_framework import serializers, viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Service
from .models_metrics import ServiceMetric
from .metrics import metrics_adapter

logger = logging.getLogger(__name__)


def _duration_to_delta(duration: str) -> timedelta:
    duration_map = {
        '1h': timedelta(hours=1),
        '6h': timedelta(hours=6),
        '24h': timedelta(hours=24),
        '7d': timedelta(days=7),
    }
    return duration_map.get(duration, timedelta(hours=1))


def _db_metrics_fallback(service: Service, duration: str):
    since = timezone.now() - _duration_to_delta(duration)
    rows = list(
        ServiceMetric.objects.filter(service=service, timestamp__gte=since)
        .order_by('timestamp')[:1200]
    )
    if not rows:
        return None

    cpu = [
        {'timestamp': row.timestamp.isoformat(), 'value': round(float(row.cpu_percent), 2)}
        for row in rows
    ]
    memory = [
        {'timestamp': row.timestamp.isoformat(), 'value': round(float(row.memory_usage), 2)}
        for row in rows
    ]
    network = [
        {
            'timestamp': row.timestamp.isoformat(),
            'value': round(float(row.network_rx_bytes + row.network_tx_bytes) / 1024, 2),
        }
        for row in rows
    ]
    disk = [
        {
            'timestamp': row.timestamp.isoformat(),
            'value': round(float(row.disk_read_bytes + row.disk_write_bytes) / 1024, 2),
        }
        for row in rows
    ]

    latest = rows[-1]
    return {
        'cpu': cpu,
        'memory': memory,
        'network': network,
        'disk': disk,
        'current': {
            'cpu_percent': round(float(latest.cpu_percent), 2),
            'memory_usage': round(float(latest.memory_usage), 2),
            'memory_limit': round(float(latest.memory_limit), 2),
            'memory_percent': round(float(latest.memory_percent), 2),
            'network_rx_kb': round(float(latest.network_rx_bytes) / 1024, 2),
            'network_tx_kb': round(float(latest.network_tx_bytes) / 1024, 2),
        },
        'source': 'db_fallback',
    }


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

            cpu = metrics_adapter.get_cpu_history(service, duration)
            memory = metrics_adapter.get_memory_history(service, duration)
            network = metrics_adapter.get_network_history(service, duration)
            disk = metrics_adapter.get_disk_history(service, duration)
            current = metrics_adapter.get_current(service)

            if not cpu and not memory and not network and not disk:
                fallback = _db_metrics_fallback(service, duration)
                if fallback:
                    return Response(fallback)

            return Response({
                'cpu': cpu,
                'memory': memory,
                'network': network,
                'disk': disk,
                'current': current,
            })
        except Service.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error(
                "Metrics API failed for service=%s: %s",
                service_pk,
                exc,
                exc_info=True,
            )
            return Response(
                {
                    'cpu': [],
                    'memory': [],
                    'network': [],
                    'disk': [],
                    'current': {
                        'cpu_percent': 0,
                        'memory_usage': 0,
                        'memory_limit': 0,
                        'memory_percent': 0,
                        'network_rx_kb': 0,
                        'network_tx_kb': 0,
                    },
                    'error': 'Metrics temporarily unavailable',
                },
                status=status.HTTP_200_OK,
            )
