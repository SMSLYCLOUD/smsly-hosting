"""Views Metrics module."""
import logging
from datetime import timedelta

from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.response import Response

from ..metrics import (
    metrics_adapter,  # type: ignore[attr-defined]    # metrics/__init__.py is a hub; metrics_adapter lives in metrics/adapter.py.
)
from apps.deployments.models import Service  # type: ignore[attr-defined]
from ..models.metrics import ServiceMetric

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


def _series_has_activity(series):
    for point in series or []:
        try:
            if float((point or {}).get('value', 0) or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _current_has_activity(current):
    if not isinstance(current, dict):
        return False
    keys = (
        'cpu_percent',
        'memory_usage',
        'memory_limit',
        'memory_percent',
        'network_rx_kb',
        'network_tx_kb',
    )
    for key in keys:
        try:
            if float(current.get(key, 0) or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _live_metrics_fallback(service: Service):
    try:
        from apps.deployments.utils.target import resolve_active_execution_target
        target = resolve_active_execution_target(service)
        if target["target_type"] != "local":
            return None # remote metrics should be routed or come from prometheus/db
        container_id = target["runtime_id"]
    except Exception:
        latest = service.deployments.filter(status='ACTIVE').order_by('-created_at').first()
        container_id = latest.container_id if latest else None

    if not container_id:
        return None

    from apps.core.tasks.metrics import (
        _collect_container_stats,  # local import to avoid eager deps
    )

    stats = _collect_container_stats(str(container_id))
    if not stats:
        return None

    cpu_limit = float(stats.get('cpu_limit') or 0.0)
    cpu_usage = float(stats.get('cpu_usage') or 0.0)
    cpu_percent = (cpu_usage / cpu_limit * 100.0) if cpu_limit > 0 else 0.0

    mem_usage = float(stats.get('memory_usage') or 0.0)
    mem_limit = float(stats.get('memory_limit') or 0.0)
    mem_percent = (mem_usage / mem_limit * 100.0) if mem_limit > 0 else 0.0

    rx_kb = float(stats.get('network_rx_bytes') or 0.0) / 1024
    tx_kb = float(stats.get('network_tx_bytes') or 0.0) / 1024
    disk_kb = (
        float(stats.get('disk_read_bytes') or 0.0)
        + float(stats.get('disk_write_bytes') or 0.0)
    ) / 1024

    now = timezone.now().isoformat()
    return {
        'cpu': [{'timestamp': now, 'value': round(cpu_percent, 2)}],
        'memory': [{'timestamp': now, 'value': round(mem_usage, 2)}],
        'network': [{'timestamp': now, 'value': round(rx_kb + tx_kb, 2)}],
        'disk': [{'timestamp': now, 'value': round(disk_kb, 2)}],
        'current': {
            'cpu_percent': round(cpu_percent, 2),
            'memory_usage': round(mem_usage, 2),
            'memory_limit': round(mem_limit, 2),
            'memory_percent': round(mem_percent, 2),
            'network_rx_kb': round(rx_kb, 2),
            'network_tx_kb': round(tx_kb, 2),
        },
        'source': 'docker_live',
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
            # Check permission: superuser, owner, or team member.
            if not request.user.is_superuser:
                from apps.teams.permissions import get_team_q_filter
                allowed = Service.objects.filter(
                    get_team_q_filter(request.user, request=request), id=service.id
                ).exists()
                if not allowed:
                    return Response(status=status.HTTP_403_FORBIDDEN)

            duration = request.query_params.get('duration', '1h')

            cpu = metrics_adapter.get_cpu_history(service, duration)
            memory = metrics_adapter.get_memory_history(service, duration)
            network = metrics_adapter.get_network_history(service, duration)
            disk = metrics_adapter.get_disk_history(service, duration)
            current = metrics_adapter.get_current(service)

            has_activity = any(
                _series_has_activity(series)
                for series in (cpu, memory, network, disk)
            ) or _current_has_activity(current)

            if not has_activity:
                fallback = _db_metrics_fallback(service, duration)
                if fallback:
                    return Response(fallback)
                live = _live_metrics_fallback(service)
                if live:
                    return Response(live)

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
