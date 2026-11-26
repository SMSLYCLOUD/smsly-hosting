"""Slow query API endpoints."""
from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.deployments.services.slow_query import (
    check_pg_stat_statements_status,
    fetch_query_stats,
    fetch_slow_queries,
    reset_pg_stat_statements,
)


class SlowQueryViewSet(viewsets.GenericViewSet):
    # SECURITY: pg_stat_statements is platform-wide — restrict to admins
    # to prevent regular users from enumerating other tenants' query
    # patterns or resetting shared observability counters.
    permission_classes = [permissions.IsAdminUser]

    def list(self, request):
        """Get slow queries (default: >100ms mean execution time)."""
        min_ms = float(request.GET.get('min_ms', 100))
        limit = min(int(request.GET.get('limit', 50)), 200)
        ext_ok, warning = check_pg_stat_statements_status()
        queries = fetch_slow_queries(min_duration_ms=min_ms, limit=limit) if ext_ok else []
        stats = fetch_query_stats() if ext_ok else {
            'unique_queries': 0,
            'total_calls': 0,
            'total_time_ms': 0,
            'cache_hit_ratio': 100,
        }
        return Response({
            'queries': queries,
            'stats': stats,
            'extension_available': ext_ok,
            'warning': warning,
        })

    @action(detail=False, methods=['post'])
    def reset(self, request):
        """Reset pg_stat_statements counters."""
        ok = reset_pg_stat_statements()
        return Response({'status': 'ok' if ok else 'error'})
