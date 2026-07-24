"""snapshot views."""
import logging

logger = logging.getLogger(__name__)



import contextlib
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from apps.teams.permissions import get_team_q_filter
from ..models import Service
from ..models.audit import AuditLog
from ..models.backup import ServiceSnapshot
from ..serializers import ServiceSnapshotDiffSerializer, ServiceSnapshotRestoreSerializer, ServiceSnapshotSerializer
from ._helpers import is_remote_sync_request
class ServiceSnapshotViewSet(viewsets.ModelViewSet):
    queryset = ServiceSnapshot.objects.all().order_by('-created_at')
    serializer_class = ServiceSnapshotSerializer
    permission_classes = [permissions.IsAuthenticated]

    @staticmethod
    def _user_can_access_service(user, service):
        if not user or not user.is_authenticated or not service:
            return False
        if user.is_superuser or service.owner_id == user.id:
            return True
        return service.project_id and service.project.team_id and service.project.team.members.filter(user=user).exists()

    def get_queryset(self):
        qs = self.queryset
        if not self.request.user.is_authenticated:
            return qs.none()

        if not (self.request.user.is_superuser or is_remote_sync_request(self.request)):
            qs = qs.filter(
                get_team_q_filter(self.request.user, prefix='service__', request=self.request)
            ).distinct()

        project_id = self.request.query_params.get('project_id')
        if project_id:
            qs = qs.filter(service__project_id=project_id)
        service_pk = self.kwargs.get('service_pk')
        if service_pk:
            qs = qs.filter(service_id=service_pk)
        return qs

    def perform_create(self, serializer):
        service = serializer.validated_data.get('service')
        if not self._user_can_access_service(self.request.user, service):
            raise PermissionDenied("You do not have access to this service.")

        from ..services.snapshot_service import SnapshotService
        try:
            snapshot = SnapshotService.capture_snapshot(
                service_id=str(service.id),
                trigger=serializer.validated_data.get('trigger', 'MANUAL'),
                label=serializer.validated_data.get('label', ''),
                created_by=self.request.user,
            )
            serializer.instance = snapshot
        except Exception as exc:
            raise serializers.ValidationError({"detail": str(exc)})

    @action(detail=True, methods=['post'])
    def restore(self, request, pk=None, *args, **kwargs):
        snapshot = self.get_object()

        confirm = request.data.get('confirm')
        if str(confirm).lower() != 'true':
            return Response(
                {'error': 'Explicit confirmation required. Send "confirm": true.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = ServiceSnapshotRestoreSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        target_service_id = serializer.validated_data.get('target_service_id')
        redeploy = serializer.validated_data.get('redeploy', False)

        if target_service_id:
            target_service = Service.objects.filter(
                id=target_service_id,
            ).select_related('project__team').first()
            if not self._user_can_access_service(request.user, target_service):
                return Response(
                    {'error': 'Target service not found or permission denied'},
                    status=status.HTTP_404_NOT_FOUND,
                )
        else:
            target_service = snapshot.service

        if not self._user_can_access_service(request.user, target_service):
            return Response(
                {'error': 'Permission denied for target service'},
                status=status.HTTP_403_FORBIDDEN,
            )

        from ..services.snapshot_service import SnapshotService
        try:
            result = SnapshotService.restore_snapshot(
                snapshot_id=str(snapshot.id),
                target_service_id=str(target_service.id) if target_service_id else None,
                redeploy=redeploy,
                requesting_user=request.user,
            )
            with contextlib.suppress(Exception):
                AuditLog(
                    actor=request.user.get_username(),
                    action='SNAPSHOT_RESTORED',
                    target=f'snapshot={snapshot.id}',
                    metadata={
                        'service_id': str(target_service.id),
                        'redeploy': redeploy,
                        'changes_count': result.get('config_changes', 0),
                    },
                ).save()
            return Response(result, status=status.HTTP_200_OK)
        except Exception as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='diff')
    def diff(self, request, pk=None, *args, **kwargs):
        snapshot_a = self.get_object()
        serializer = ServiceSnapshotDiffSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        compare_with_id = serializer.validated_data['compare_with_id']
        try:
            snapshot_b = ServiceSnapshot.objects.get(id=compare_with_id)
        except ServiceSnapshot.DoesNotExist:
            return Response({'error': 'Comparison snapshot not found'}, status=status.HTTP_404_NOT_FOUND)

        if not self._user_can_access_service(request.user, snapshot_a.service) or \
           not self._user_can_access_service(request.user, snapshot_b.service):
            return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)

        from ..services.snapshot_service import SnapshotService
        try:
            result = SnapshotService.diff_snapshots(
                snapshot_a_id=str(snapshot_a.id),
                snapshot_b_id=str(snapshot_b.id),
            )
            return Response(result, status=status.HTTP_200_OK)
        except Exception as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


