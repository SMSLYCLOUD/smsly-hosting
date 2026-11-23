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
    from django.db.models import Avg, Count, Max

    since = timezone.now() - _duration_to_delta(duration)
    agg = ServiceMetric.objects.filter(service=service, timestamp__gte=since).aggregate(
        cpu_usage_avg=Avg('cpu_usage'),
        cpu_usage_max=Max('cpu_usage'),
        cpu_limit_avg=Avg('cpu_limit'),
        cpu_limit_max=Max('cpu_limit'),
        memory_usage_avg=Avg('memory_usage'),
        memory_usage_max=Max('memory_usage'),
        memory_limit_avg=Avg('memory_limit'),
        memory_limit_max=Max('memory_limit'),
        network_rx_avg=Avg('network_rx_bytes'),
        network_rx_max=Max('network_rx_bytes'),
        network_tx_avg=Avg('network_tx_bytes'),
        network_tx_max=Max('network_tx_bytes'),
        disk_read_avg=Avg('disk_read_bytes'),
        disk_read_max=Max('disk_read_bytes'),
        disk_write_avg=Avg('disk_write_bytes'),
        disk_write_max=Max('disk_write_bytes'),
        sample_count=Count('id'),
    )
    sample_count = int((agg or {}).get('sample_count') or 0)
    if sample_count <= 0:
        return None

    cpu_usage_avg = float(agg.get('cpu_usage_avg') or 0.0)
    cpu_usage_max = float(agg.get('cpu_usage_max') or 0.0)
    cpu_limit_avg = float(agg.get('cpu_limit_avg') or 0.0)
    cpu_limit_max = float(agg.get('cpu_limit_max') or 0.0)
    cpu_avg = round((cpu_usage_avg / cpu_limit_avg * 100.0) if cpu_limit_avg > 0 else 0.0, 2)
    cpu_max = round((cpu_usage_max / cpu_limit_max * 100.0) if cpu_limit_max > 0 else 0.0, 2)

    mem_avg = round(float(agg.get('memory_usage_avg') or 0.0), 2)
    mem_max = round(float(agg.get('memory_usage_max') or 0.0), 2)
    mem_lim_avg = float(agg.get('memory_limit_avg') or 0.0)
    mem_lim_max = round(float(agg.get('memory_limit_max') or 0.0), 2)
    mem_pct_avg = round((mem_avg / mem_lim_avg * 100.0) if mem_lim_avg > 0 else 0.0, 2)
    mem_pct_max = round((mem_max / mem_lim_max * 100.0) if mem_lim_max > 0 else 0.0, 2)

    rx_avg_kb = float(agg.get('network_rx_avg') or 0.0) / 1024.0
    rx_max_kb = round(float(agg.get('network_rx_max') or 0.0) / 1024.0, 2)
    tx_avg_kb = float(agg.get('network_tx_avg') or 0.0) / 1024.0
    tx_max_kb = round(float(agg.get('network_tx_max') or 0.0) / 1024.0, 2)
    net_avg_kb = round(rx_avg_kb + tx_avg_kb, 2)
    net_max_kb = round(rx_max_kb + tx_max_kb, 2)

    disk_avg_kb = round(
        (float(agg.get('disk_read_avg') or 0.0) + float(agg.get('disk_write_avg') or 0.0)) / 1024.0,
        2,
    )
    disk_max_kb = round(
        (float(agg.get('disk_read_max') or 0.0) + float(agg.get('disk_write_max') or 0.0)) / 1024.0,
        2,
    )
    now_iso = timezone.now().isoformat()

    return {
        'sample_count': sample_count,
        'cpu_avg': cpu_avg,
        'cpu_max': cpu_max,
        'memory_avg_mb': mem_avg,
        'memory_max_mb': mem_max,
        'memory_percent_avg': mem_pct_avg,
        'memory_percent_max': mem_pct_max,
        'network_avg_kb': net_avg_kb,
        'network_max_kb': net_max_kb,
        'disk_avg_kb': disk_avg_kb,
        'disk_max_kb': disk_max_kb,
        'cpu': [{'timestamp': now_iso, 'value': cpu_max}],
        'memory': [{'timestamp': now_iso, 'value': mem_max}],
        'network': [{'timestamp': now_iso, 'value': net_max_kb}],
        'disk': [{'timestamp': now_iso, 'value': disk_max_kb}],
        'current': {
            'cpu_percent': cpu_max,
            'cpu_limit': round(float(service.cpu_cores or 0), 2),
            'memory_usage': mem_max,
            'memory_limit': mem_lim_max,
            'memory_percent': mem_pct_max,
            'network_rx_kb': rx_max_kb,
            'network_tx_kb': tx_max_kb,
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

    stats = _collect_container_stats(str(container_id), service.cpu_cores)
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
            'cpu_limit': round(float(service.cpu_cores or 0), 2),
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
            if isinstance(current, dict):
                current['cpu_limit'] = round(float(service.cpu_cores or 0), 2)

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
                        'cpu_limit': round(float(service.cpu_cores or 0), 2),
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
