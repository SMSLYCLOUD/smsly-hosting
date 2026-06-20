from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.deployments.models_addons import Addon
from apps.deployments.rate_limiting import (
    DBQueryRateThrottle,
    DBRotateCredentialsRateThrottle,
    DBVacuumRateThrottle,
)

from .services.db_proxy import DatabaseProxy
from .services.maintenance import AddonMaintenanceService


class AddonMaintenanceViewSet(viewsets.ReadOnlyModelViewSet):
    """API for Addon Maintenance & Exploration."""
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    def get_queryset(self):
        return Addon.objects.filter(service__owner=self.request.user)

    @action(detail=True, methods=['get'])
    def tables(self, request, pk=None):
        addon = self.get_object()
        proxy = DatabaseProxy(addon)
        return Response(proxy.list_tables())

    @action(detail=True, methods=['post'],
            throttle_classes=[DBQueryRateThrottle])
    def query(self, request, pk=None):
        """Run arbitrary SQL against the addon's database.

        Throttled at ``db_query: 30/minute`` (per user). This is
        the highest-impact uncapped write on the platform —
        arbitrary SQL against a shared DB. The throttle bounds
        the per-user rate at which an attacker can extract data
        or hammer the DB.
        """
        addon = self.get_object()
        sql = request.data.get('sql')
        if not sql:
            return Response({'error': 'SQL required'}, status=status.HTTP_400_BAD_REQUEST)

        proxy = DatabaseProxy(addon)
        result = proxy.query(sql, addon=addon, user=request.user)
        if isinstance(result, dict) and 'error' in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)

    @action(detail=True, methods=['get'])
    def stats(self, request, pk=None):
        addon = self.get_object()
        proxy = DatabaseProxy(addon)
        return Response(proxy.get_stats())

    @action(detail=True, methods=['post'],
            throttle_classes=[DBVacuumRateThrottle])
    def vacuum(self, request, pk=None):
        """Run VACUUM ANALYZE on the addon DB.

        Throttled at ``db_vacuum: 1/hour`` (per user). VACUUM
        locks the addon DB while it runs; the hourly cap is
        enough for routine maintenance and bounds the blast
        radius of a misclick.
        """
        addon = self.get_object()
        service = AddonMaintenanceService(addon)
        service.vacuum_analyze()
        return Response({'status': 'started'})

    @action(detail=True, methods=['post'], url_path='rotate-credentials',
            throttle_classes=[DBRotateCredentialsRateThrottle])
    def rotate_credentials(self, request, pk=None):
        """Rotate the addon's database credentials.

        Throttled at ``db_rotate: 1/hour`` (per user). Rotation
        invalidates the addon's secrets across all services
        that depend on it. The hourly cap bounds the blast
        radius of a misclick.
        """
        addon = self.get_object()
        service = AddonMaintenanceService(addon)
        result = service.rotate_credentials()
        if result.get('status') == 'not_implemented':
            return Response(result, status=status.HTTP_501_NOT_IMPLEMENTED)
        if result.get('status') in {'failed', 'error'}:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)
