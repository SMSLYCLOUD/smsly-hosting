"""Slow query API endpoints."""
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.deployments.services.slow_query import fetch_slow_queries, fetch_query_stats, reset_pg_stat_statements


class SlowQueryViewSet(viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        """Get slow queries (default: >100ms mean execution time)."""
        min_ms = float(request.GET.get('min_ms', 100))
        limit = min(int(request.GET.get('limit', 50)), 200)
        queries = fetch_slow_queries(min_duration_ms=min_ms, limit=limit)
        stats = fetch_query_stats()
        return Response({'queries': queries, 'stats': stats})

    @action(detail=False, methods=['post'])
    def reset(self, request):
        """Reset pg_stat_statements counters."""
        ok = reset_pg_stat_statements()
        return Response({'status': 'ok' if ok else 'error'})
