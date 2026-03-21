"""Views Addons module."""
from rest_framework import serializers, viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q
from .models_addons import Addon
from .models import Service, EnvironmentVariable
import logging

logger = logging.getLogger(__name__)


class AddonSerializer(serializers.ModelSerializer):
    server = serializers.ReadOnlyField(source='service.server_id')

    class Meta:
        model = Addon
        fields = [
            'id',
            'service',
            'name',
            'addon_type',
            'status',
            'server',
            'created_at']
        read_only_fields = ['status', 'connection_url', 'created_at']


class BackupSerializer(serializers.ModelSerializer):
    class Meta:
        from .models_addons import Backup
        model = Backup
        fields = ['id', 'addon', 'status', 'size_bytes', 'created_at', 'completed_at', 'error_message']
        read_only_fields = ['status', 'size_bytes', 'created_at', 'completed_at', 'error_message']


class AddonViewSet(viewsets.ModelViewSet):
    queryset = Addon.objects.all()
    serializer_class = AddonSerializer
    permission_classes = [IsAuthenticated]

    # ==========================================================================
    # SECURITY: Zero Trust - Only return addons for user's own services
    # ==========================================================================
    def get_queryset(self):
        """Filter addons to only those belonging to the user's services."""
        return self.queryset.filter(
            Q(service__owner=self.request.user) | Q(service__owner__isnull=True)
        )

    def perform_create(self, serializer):
        # SECURITY: Verify user owns the service before creating addon
        service = serializer.validated_data.get('service')
        if service and service.owner and service.owner != self.request.user:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Access denied to this service.")

        addon = serializer.save()
        # Trigger async provisioning via Celery (uses Docker-native
        # provisioner)
        from .tasks import provision_addon_task
        provision_addon_task.delay(str(addon.id))

    @action(detail=True, methods=['post'])
    def deprovision(self, request, pk=None):
        """Delete addon container and remove from service."""
        addon = self.get_object()
        from .tasks import deprovision_addon_task
        deprovision_addon_task.delay(str(addon.id))
        return Response({'status': 'deprovisioning'},
                        status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['get'])
    def credentials(self, request, pk=None):
        """Return parsed connection credentials for this addon."""
        addon = self.get_object()
        if addon.status != 'ACTIVE':
            return Response(
                {'error': 'Addon not active'},
                status=status.HTTP_400_BAD_REQUEST)
        return Response(addon.parsed_credentials)

    @action(detail=True, methods=['get'])
    def status_check(self, request, pk=None):
        """Check current addon container status."""
        addon = self.get_object()

        container_id = addon.coolify_uuid  # We store container_id here

        if not container_id:
            return Response({
                'status': addon.status,
                'message': 'Not yet provisioned'
            })

        # Check Docker container status
        from services.addon_provisioner import addon_provisioner

        try:
            container_status = addon_provisioner.get_status(container_id)
            return Response({
                'status': addon.status,
                'container_running': container_status.get('running', False),
                'container_status': container_status.get('status', 'unknown'),
            })
        except Exception as e:
            logger.error(f"Failed to check addon status: {e}")
            return Response({
                'status': addon.status,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'])
    def backup(self, request, pk=None):
        """Trigger a backup for this addon."""
        addon = self.get_object()
        from .tasks import backup_addon_task
        task = backup_addon_task.delay(str(addon.id))
        return Response({'status': 'backup_started', 'task_id': task.id}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['post'])
    def restore(self, request, pk=None):
        """Restore a backup to this addon."""
        addon = self.get_object()
        backup_id = request.data.get('backup_id')
        if not backup_id:
            return Response({'error': 'backup_id required'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Verify backup belongs to addon
        from .models_addons import Backup
        if not Backup.objects.filter(id=backup_id, addon=addon).exists():
            return Response({'error': 'Backup not found for this addon'}, status=status.HTTP_404_NOT_FOUND)

        from .tasks import restore_addon_task
        task = restore_addon_task.delay(backup_id)
        return Response({'status': 'restore_started', 'task_id': task.id}, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['get'])
    def backups(self, request, pk=None):
        """List backups for this addon."""
        addon = self.get_object()
        from .models_addons import Backup
        backups = Backup.objects.filter(addon=addon).order_by('-created_at')
        serializer = BackupSerializer(backups, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def download_backup(self, request, pk=None):
        """Download a backup file."""
        addon = self.get_object()
        backup_id = request.query_params.get('backup_id')
        if not backup_id:
            return Response({'error': 'backup_id required'}, status=status.HTTP_400_BAD_REQUEST)
        
        from .models_addons import Backup
        try:
            backup = Backup.objects.get(id=backup_id, addon=addon)
        except Backup.DoesNotExist:
            return Response({'error': 'Backup not found'}, status=status.HTTP_404_NOT_FOUND)
            
        import os
        from django.http import FileResponse
        if not os.path.exists(backup.file_path):
            return Response({'error': 'File not found on disk'}, status=status.HTTP_404_NOT_FOUND)

        # Security: ensure the file path is within the expected backups directory
        from django.conf import settings as django_settings
        backups_root = os.path.realpath(os.path.join(django_settings.BASE_DIR, 'backups'))
        real_path = os.path.realpath(backup.file_path)
        if not real_path.startswith(backups_root):
            logger.warning("Blocked backup download path traversal: %s", backup.file_path)
            return Response({'error': 'Invalid backup path'}, status=status.HTTP_403_FORBIDDEN)

        response = FileResponse(open(backup.file_path, 'rb'), as_attachment=True, filename=os.path.basename(backup.file_path))
        return response
