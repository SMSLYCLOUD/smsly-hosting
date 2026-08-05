from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.deployments.models.addons import Addon
from apps.teams.permissions import assert_can_write, get_team_q_filter
from apps.core.rate_limiting import (
    DBQueryRateThrottle,
    DBRotateCredentialsRateThrottle,
    DBVacuumRateThrottle,
)

from ..services.db_proxy import DatabaseProxy
from ..services.maintenance import AddonMaintenanceService


class AddonMaintenanceViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    def get_queryset(self):
        # Team-scoped (owner OR team member), matching every other viewset in
        # the platform. Mutating actions below additionally require
        # assert_can_write.
        return Addon.objects.filter(get_team_q_filter(self.request.user, prefix='service__'))

    @action(detail=True, methods=['get'])
    def tables(self, request, pk=None):
        addon = self.get_object()
        proxy = DatabaseProxy(addon)
        return Response(proxy.list_tables())

    @action(detail=True, methods=['post'],
            throttle_classes=[DBQueryRateThrottle])
    def query(self, request, pk=None):
        addon = self.get_object()
        sql = request.data.get('sql')
        if not sql:
            return Response({'error': 'SQL required'}, status=status.HTTP_400_BAD_REQUEST)
        assert_can_write(self.request.user, addon.service, action='query addon')
        proxy = DatabaseProxy(addon)
        try:
            result = proxy.query(sql, addon=addon, user=request.user)
        except PermissionError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except Exception:
            return Response({'error': 'Query failed'}, status=status.HTTP_400_BAD_REQUEST)
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
        addon = self.get_object()
        assert_can_write(self.request.user, addon.service, action='vacuum addon')
        service = AddonMaintenanceService(addon)
        try:
            service.vacuum_analyze()
        except Exception:
            return Response({'error': 'Vacuum failed'}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'status': 'started'})

    @action(detail=True, methods=['post'], url_path='rotate-credentials',
            throttle_classes=[DBRotateCredentialsRateThrottle])
    def rotate_credentials(self, request, pk=None):
        addon = self.get_object()
        assert_can_write(self.request.user, addon.service, action='rotate addon credentials')
        from ..tasks import rotate_addon_credentials_task
        try:
            rotate_addon_credentials_task.delay(addon_id=str(addon.id))
        except Exception:
            return Response(
                {'error': 'Rotation queue unavailable'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {'status': 'started', 'message': 'Credential rotation queued.'},
            status=status.HTTP_202_ACCEPTED,
        )
