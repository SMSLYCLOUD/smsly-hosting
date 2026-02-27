from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.licensing.decorators import require_tier
from apps.deployments.models_addons import Addon
from .services.db_proxy import DatabaseProxy
from .services.maintenance import AddonMaintenanceService

class AddonMaintenanceViewSet(viewsets.ReadOnlyModelViewSet):
    """API for Addon Maintenance & Exploration."""
    permission_classes = [permissions.IsAuthenticated]

    @require_tier('pro', 'enterprise')
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @require_tier('pro', 'enterprise')
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    def get_queryset(self):
        return Addon.objects.filter(service__owner=self.request.user)

    @action(detail=True, methods=['get'])
    @require_tier('pro', 'enterprise')
    def tables(self, request, pk=None):
        addon = self.get_object()
        proxy = DatabaseProxy(addon)
        return Response(proxy.list_tables())

    @action(detail=True, methods=['post'])
    @require_tier('pro', 'enterprise')
    def query(self, request, pk=None):
        addon = self.get_object()
        sql = request.data.get('sql')
        if not sql:
            return Response({'error': 'SQL required'}, status=status.HTTP_400_BAD_REQUEST)

        proxy = DatabaseProxy(addon)
        result = proxy.query(sql)
        if 'error' in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)

    @action(detail=True, methods=['get'])
    @require_tier('pro', 'enterprise')
    def stats(self, request, pk=None):
        addon = self.get_object()
        proxy = DatabaseProxy(addon)
        return Response(proxy.get_stats())

    @action(detail=True, methods=['post'])
    @require_tier('pro', 'enterprise')
    def vacuum(self, request, pk=None):
        addon = self.get_object()
        service = AddonMaintenanceService(addon)
        service.vacuum_analyze()
        return Response({'status': 'started'})

    @action(detail=True, methods=['post'], url_path='rotate-credentials')
    @require_tier('pro', 'enterprise')
    def rotate_credentials(self, request, pk=None):
        addon = self.get_object()
        service = AddonMaintenanceService(addon)
        return Response(service.rotate_credentials())
