"""schedule views."""
import logging

logger = logging.getLogger(__name__)



from rest_framework import permissions, viewsets
from rest_framework.exceptions import PermissionDenied
from apps.teams.permissions import get_team_q_filter
from ..models.backup import BackupSchedule, SnapshotSchedule
from ..serializers import BackupScheduleSerializer, SnapshotScheduleSerializer
from ._helpers import is_remote_sync_request
from .backup import ServiceBackupViewSet
class SnapshotScheduleViewSet(viewsets.ModelViewSet):
    queryset = SnapshotSchedule.objects.all().order_by('id')
    serializer_class = SnapshotScheduleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        if instance.is_server_wide and not self.request.user.is_superuser:
            raise PermissionDenied("Only admins can delete server-wide snapshot schedules.")
        if instance.service:
            from .backup import ServiceBackupViewSet
            if not ServiceBackupViewSet._user_can_access_service(self.request.user, instance.service):
                raise PermissionDenied("You do not have access to this service.")
        super().perform_destroy(instance)

    def get_queryset(self):
        qs = self.queryset
        if not (self.request.user.is_superuser or is_remote_sync_request(self.request)):
            qs = qs.filter(
                get_team_q_filter(self.request.user, prefix='service__', request=self.request)
            ).distinct()
        service_id = self.request.query_params.get('service')
        if service_id:
            qs = qs.filter(service_id=service_id)
        return qs

    def _validate_schedule_access(self, serializer):
        service = serializer.validated_data.get(
            'service',
            getattr(serializer.instance, 'service', None),
        )
        is_server_wide = serializer.validated_data.get(
            'is_server_wide',
            getattr(serializer.instance, 'is_server_wide', False),
        )
        if is_server_wide and not self.request.user.is_superuser:
            raise PermissionDenied("Only admins can manage server-wide snapshot schedules.")
        if not service and not is_server_wide:
            raise PermissionDenied("A service is required for non-server-wide snapshot schedules.")
        if service and not ServiceBackupViewSet._user_can_access_service(self.request.user, service):
            raise PermissionDenied("You do not have access to this service.")

    def perform_create(self, serializer):
        self._validate_schedule_access(serializer)
        serializer.save()

    def perform_update(self, serializer):
        self._validate_schedule_access(serializer)
        serializer.save()


class BackupScheduleViewSet(viewsets.ModelViewSet):
    queryset = BackupSchedule.objects.all().order_by('id')
    serializer_class = BackupScheduleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        # Mirror _validate_schedule_access for the delete path.
        if instance.is_server_wide and not self.request.user.is_superuser:
            raise PermissionDenied("Only admins can delete server-wide backup schedules.")
        if instance.service:
            from .backup import ServiceBackupViewSet
            if not ServiceBackupViewSet._user_can_access_service(self.request.user, instance.service):
                raise PermissionDenied("You do not have access to this service.")
        super().perform_destroy(instance)

    def get_queryset(self):
        qs = self.queryset
        if not (self.request.user.is_superuser or is_remote_sync_request(self.request)):
            qs = qs.filter(
                get_team_q_filter(self.request.user, prefix='service__', request=self.request)
            ).distinct()
        service_id = self.request.query_params.get('service')
        if service_id:
            qs = qs.filter(service_id=service_id)
        return qs

    def _validate_schedule_access(self, serializer):
        service = serializer.validated_data.get(
            'service',
            getattr(serializer.instance, 'service', None),
        )
        is_server_wide = serializer.validated_data.get(
            'is_server_wide',
            getattr(serializer.instance, 'is_server_wide', False),
        )
        if is_server_wide and not self.request.user.is_superuser:
            raise PermissionDenied("Only admins can manage server-wide backup schedules.")
        if not service and not is_server_wide:
            raise PermissionDenied("A service is required for non-server-wide backup schedules.")
        if service and not ServiceBackupViewSet._user_can_access_service(self.request.user, service):
            raise PermissionDenied("You do not have access to this service.")

    def perform_create(self, serializer):
        self._validate_schedule_access(serializer)
        serializer.save()

    def perform_update(self, serializer):
        self._validate_schedule_access(serializer)
        serializer.save()


