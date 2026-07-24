"""Service backup viewset - composed from domain-specific mixins."""
import logging

from apps.teams.permissions import get_team_q_filter

logger = logging.getLogger(__name__)

from rest_framework import permissions, viewsets
from rest_framework.exceptions import PermissionDenied
from ...models.backup import ServiceBackup
from ...serializers import ServiceBackupSerializer
from ...tasks import create_service_backup_task
from .._helpers import (
    is_remote_sync_request,
)
from .keys import KeyManagementMixin
from .download import DownloadActionsMixin
from .restore import RestoreActionsMixin
from .history import HistoryActionsMixin


class ServiceBackupViewSet(KeyManagementMixin, DownloadActionsMixin, RestoreActionsMixin, HistoryActionsMixin, viewsets.ModelViewSet):
    """Service backup viewset composed from domain-specific mixins."""
    queryset = ServiceBackup.objects.all().order_by('-created_at')
    serializer_class = ServiceBackupSerializer
    permission_classes = [permissions.IsAuthenticated]



    @staticmethod
    def _user_can_access_service(user, service):
        if not user or not user.is_authenticated or not service:
            return False
        if user.is_superuser or service.owner_id == user.id:
            return True
        return service.project_id and service.project.team_id and service.project.team.members.filter(user=user).exists()



    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)



    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)



    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)



    def get_queryset(self):
        qs = self.queryset

        # `header` and `download_key` are intentionally public actions (see
        # their docstrings) — the data they return (V2 key_id + fingerprint +
        # service name + creation timestamp) is non-secret and meant to be
        # shareable. Those actions override the viewset permission to
        # `permission_classes=[permissions.AllowAny]` and clear
        # `authentication_classes`, so `self.request.user` arrives as
        # `AnonymousUser`. The auth-scoped Q-filter below would then raise
        # `TypeError: Cannot cast AnonymousUser to int` when Django tries
        # to coerce it for the FK lookup. Return the unscoped queryset for
        # those actions instead.
        if getattr(self, 'action', None) in ('header', 'download_key'):
            pass
        elif not self.request.user.is_authenticated:
            # Defensive: any other action reached without auth (shouldn't
            # happen given the viewset's IsAuthenticated default, but cheap
            # to guard against future regressions).
            return qs.none()
        elif self.request.user.is_superuser or is_remote_sync_request(self.request):
            pass
        else:
            qs = qs.filter(
                get_team_q_filter(self.request.user, prefix='service__', request=self.request)
            ).distinct()

        qs = qs.order_by('-created_at')
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
        backup = serializer.save(created_by=self.request.user, status='PENDING')
        create_service_backup_task.delay(service_id=str(backup.service.id), backup_type='MANUAL', backup_id=str(backup.id))
